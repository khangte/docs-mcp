"""검색용 청크 텍스트 빌더."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger
from app.services.indexer.section_splitter import CountTokens, build_section_chunks
from app.services.parser.openapi_parser import (
    ParsedDocument,
    ParsedEndpoint,
    ParsedSchema,
    ParsedSection,
)

_LOG = get_logger("docs_mcp.indexer.chunk_builder")


@dataclass
class BuiltChunk:
    """청크 텍스트 빌드 결과(타입/참조ID/텍스트)."""

    chunk_type: str  # "endpoint" | "schema" | "section"
    ref_id: str  # endpoint_id, schema_id 또는 section_id
    text: str


def build_endpoint_chunk_text(endpoint: ParsedEndpoint) -> str:
    """엔드포인트 단위 청크 텍스트.

    포맷:
        [METHOD] PATH — SUMMARY
        OperationId: operationId
        Params: name(in,required), ...
        Body: field1, field2, ...
        Tags: t1, t2
        DESCRIPTION
        Responses: 200, 404

    docs/architect-review/30 §9.2: request body는 필드명만 나열한다(설명 텍스트
    미포함 — 대형 body가 480/512 토큰 상한을 넘기지 않게). $ref만 있고 인라인
    `properties`가 없는 body(스키마 참조만 있는 경우)는 이름을 뽑을 수 없어
    `Body:` 줄 자체를 생략한다 — 이 케이스의 $ref 해소는 범위 밖(YAGNI).

    docs/architect-review/30 §11.2: SentenceTransformer는 입력 꼬리를 자른다.
    구조 필드(Params·Body)를 저신호 free-text(description)보다 앞에 둬 overflow
    시 고신호 필드명이 먼저 잘리지 않게 한다(header는 선두 고정).
    """

    summary = endpoint.summary or ""
    header = f"[{endpoint.method}] {endpoint.path} — {summary}".rstrip(" —")
    description = endpoint.description or ""
    operation_id = endpoint.operation_id or ""
    tags = ", ".join(endpoint.tags) if endpoint.tags else ""
    params_desc = ", ".join(
        f"{p.name}({p.location},{'required' if p.required else 'optional'})"
        for p in endpoint.parameters
    )
    body_desc = ""
    if endpoint.request_body is not None:
        properties = endpoint.request_body.schema.get("properties")
        if isinstance(properties, dict) and properties:
            body_desc = ", ".join(sorted(str(k) for k in properties.keys()))
    responses_desc = ", ".join(r.status_code for r in endpoint.responses)
    lines = [header]
    if operation_id:
        lines.append(f"OperationId: {operation_id}")
    if params_desc:
        lines.append(f"Params: {params_desc}")
    if body_desc:
        lines.append(f"Body: {body_desc}")
    if tags:
        lines.append(f"Tags: {tags}")
    if description:
        lines.append(description)
    if responses_desc:
        lines.append(f"Responses: {responses_desc}")
    return "\n".join(lines)


def build_schema_chunk_text(schema: ParsedSchema) -> str:
    """스키마 단위 청크 텍스트."""
    lines = [f"Schema: {schema.name}"]
    if schema.description:
        lines.append(schema.description)
    properties = schema.json_schema.get("properties") if isinstance(schema.json_schema, dict) else None
    if isinstance(properties, dict) and properties:
        names = ", ".join(sorted(str(k) for k in properties.keys()))
        lines.append(f"Properties: {names}")
    return "\n".join(lines)


def build_section_chunk_text(section: ParsedSection) -> str:
    """섹션 단위 청크 텍스트.

    포맷:
        # TITLE
        CONTENT
    """
    return f"# {section.title}\n{section.content}" if section.title else section.content


def build_chunks(
    document: ParsedDocument,
    endpoint_ids: dict[tuple[str, str], str],
    section_ids: dict[int, str] | None = None,
    schema_ids: dict[int, str] | None = None,
    count_tokens: CountTokens | None = None,
    token_limit: int = 480,
) -> list[BuiltChunk]:
    """문서 내 모든 엔드포인트/스키마/섹션에 대해 청크를 생성한다.

    `endpoint_ids` 는 (method, path) → endpoint_id 매핑.
    `section_ids` 는 섹션 순서 인덱스 → section_id 매핑.
    `schema_ids` 는 스키마 순서 인덱스 → schema_id 매핑(`ApiSchema.id`와 동일값).
    `count_tokens` 가 주어지면 상한(`token_limit`) 초과 섹션을 `section_splitter`
    로 sub-chunk N개로 분할한다(docs/23). `None`이면 섹션당 청크 1개(기존 동작).
    """

    chunks: list[BuiltChunk] = []
    for endpoint in document.endpoints:
        eid = endpoint_ids.get((endpoint.method, endpoint.path))
        if not eid:
            continue
        chunks.append(
            BuiltChunk(
                chunk_type="endpoint",
                ref_id=eid,
                text=build_endpoint_chunk_text(endpoint),
            )
        )
    for idx, schema in enumerate(document.schemas):
        sid = (schema_ids or {}).get(idx)
        if not sid:
            continue
        chunks.append(
            BuiltChunk(
                chunk_type="schema",
                ref_id=sid,
                text=build_schema_chunk_text(schema),
            )
        )
    for idx, section in enumerate(document.sections):
        sid = (section_ids or {}).get(idx)
        if not sid:
            continue
        if count_tokens is not None:
            texts = build_section_chunks(section, count_tokens, token_limit)
        else:
            texts = [build_section_chunk_text(section)]
        if len(texts) > 1:
            _LOG.info("섹션 %s를 %d개 sub-chunk로 분할함", sid, len(texts))
        for text in texts:
            chunks.append(BuiltChunk(chunk_type="section", ref_id=sid, text=text))
    return chunks
