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

#: `docs/architect-review/23` §5: 임베딩 경고 임계값과 동일 상수 재사용 의도.
#: 실제 값은 호출자(IndexerService)가 `embedding_provider.TOKEN_WARNING_THRESHOLD`
#: 를 명시적으로 넘긴다 — chunk_builder 는 모델/임베딩 모듈에 의존하지 않는다.
DEFAULT_SECTION_TOKEN_LIMIT = 480


@dataclass
class BuiltChunk:
    """청크 텍스트 빌드 결과(타입/참조ID/텍스트)."""

    chunk_type: str  # "endpoint" | "schema" | "section"
    ref_id: str  # endpoint_id, schema_name 또는 section_id
    text: str


def build_endpoint_chunk_text(endpoint: ParsedEndpoint) -> str:
    """엔드포인트 단위 청크 텍스트.

    포맷:
        [METHOD] PATH — SUMMARY
        DESCRIPTION
        Tags: t1, t2
        Params: name(in,required), ...
        Responses: 200, 404
    """

    summary = endpoint.summary or ""
    header = f"[{endpoint.method}] {endpoint.path} — {summary}".rstrip(" —")
    description = endpoint.description or ""
    tags = ", ".join(endpoint.tags) if endpoint.tags else ""
    params_desc = ", ".join(
        f"{p.name}({p.location},{'required' if p.required else 'optional'})"
        for p in endpoint.parameters
    )
    responses_desc = ", ".join(r.status_code for r in endpoint.responses)
    lines = [header]
    if description:
        lines.append(description)
    if tags:
        lines.append(f"Tags: {tags}")
    if params_desc:
        lines.append(f"Params: {params_desc}")
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
    count_tokens: CountTokens | None = None,
    token_limit: int = DEFAULT_SECTION_TOKEN_LIMIT,
) -> list[BuiltChunk]:
    """문서 내 모든 엔드포인트/스키마/섹션에 대해 청크를 생성한다.

    `endpoint_ids` 는 (method, path) → endpoint_id 매핑.
    `section_ids` 는 섹션 순서 인덱스 → section_id 매핑.
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
    for schema in document.schemas:
        chunks.append(
            BuiltChunk(
                chunk_type="schema",
                ref_id=schema.name,
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
