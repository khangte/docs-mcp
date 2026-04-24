"""청크 빌더 테스트."""

from __future__ import annotations

from src.services.indexer.chunk_builder import build_chunks
from src.services.parser.openapi_parser import parse_document


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
