"""검색용 청크 텍스트 빌더."""

from __future__ import annotations

from dataclasses import dataclass

from src.services.parser.openapi_parser import ParsedDocument, ParsedEndpoint, ParsedSchema


@dataclass
class BuiltChunk:
    """청크 텍스트 빌드 결과(타입/참조ID/텍스트)."""

    chunk_type: str  # "endpoint" | "schema"
    ref_id: str  # endpoint_id 또는 schema_name
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


def build_chunks(
    document: ParsedDocument, endpoint_ids: dict[tuple[str, str], str]
) -> list[BuiltChunk]:
    """문서 내 모든 엔드포인트/스키마에 대해 청크를 생성한다.

    `endpoint_ids` 는 (method, path) → endpoint_id 매핑.
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
    return chunks
