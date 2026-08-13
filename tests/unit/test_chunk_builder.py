"""청크 빌더 테스트."""

from __future__ import annotations

from app.services.indexer.chunk_builder import build_chunks
from app.services.parser.openapi_parser import ParsedDocument, ParsedSection, parse_document


def _count_words(text: str) -> int:
    return len(text.split())


def test_endpoint_chunk_text_contains_essentials(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    ids = {(e.method, e.path): f"id-{i}" for i, e in enumerate(parsed.endpoints)}
    chunks = build_chunks(parsed, ids)
    endpoint_chunks = [c for c in chunks if c.chunk_type == "endpoint"]
    assert len(endpoint_chunks) == len(parsed.endpoints)
    get_pet_chunk = next(
        c for c in endpoint_chunks if ids[("GET", "/pet/{petId}")] == c.ref_id
    )
    text = get_pet_chunk.text
    assert "[GET]" in text
    assert "/pet/{petId}" in text
    assert "find pet by id" in text.lower()
    assert "petId" in text


def test_schema_chunk_created(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    chunks = build_chunks(parsed, endpoint_ids={})
    schema_chunks = [c for c in chunks if c.chunk_type == "schema"]
    assert {c.ref_id for c in schema_chunks} == {"Pet", "User"}


def test_empty_endpoint_ids_skips_endpoint_chunks(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    chunks = build_chunks(parsed, endpoint_ids={})
    endpoint_chunks = [c for c in chunks if c.chunk_type == "endpoint"]
    assert endpoint_chunks == []


def _long_section_document() -> ParsedDocument:
    content = "\n\n".join(f"para {i} " + "word " * 5 for i in range(10))
    return ParsedDocument(
        title="doc",
        version="unknown",
        sections=[ParsedSection(title="개요", content=content)],
    )


def test_section_without_count_tokens_stays_single_chunk() -> None:
    """count_tokens 미지정(기본 하위호환)이면 섹션당 청크 1개 그대로."""
    parsed = _long_section_document()
    chunks = build_chunks(parsed, endpoint_ids={}, section_ids={0: "doc:section:0"})
    section_chunks = [c for c in chunks if c.chunk_type == "section"]
    assert len(section_chunks) == 1


def test_section_over_token_limit_splits_into_multiple_chunks_same_ref_id() -> None:
    """docs/23: count_tokens 주어지고 상한 초과 시 section_splitter 로 분할, ref_id는 동일 유지."""
    parsed = _long_section_document()
    chunks = build_chunks(
        parsed,
        endpoint_ids={},
        section_ids={0: "doc:section:0"},
        count_tokens=_count_words,
        token_limit=10,
    )
    section_chunks = [c for c in chunks if c.chunk_type == "section"]
    assert len(section_chunks) > 1
    assert {c.ref_id for c in section_chunks} == {"doc:section:0"}
