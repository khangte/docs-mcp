"""Indexer / 재색인 동작 테스트."""

from __future__ import annotations

import json

from app.models import EndpointBusinessMetadata


def test_index_document_creates_endpoints_and_chunks(
    services_factory, sample_openapi_3: str
) -> None:
    services = services_factory()
    result = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    # POST /pet, GET /pet/{petId}, DELETE /pet/{petId}, POST /user
    assert result.endpoints_count == 4
    assert result.chunks_count == result.endpoints_count + result.schemas_count

    chunks = services.chunk_repo.list_by_document(result.document.id)
    assert len(chunks) == result.chunks_count
    endpoints = services.endpoint_repo.list_by_document(result.document.id)
    assert len(endpoints) == result.endpoints_count


def test_index_document_with_schema_name_over_64_chars_does_not_crash(
    services_factory, sample_openapi_3: str
) -> None:
    """docs/architect-review/29: schema 컴포넌트명이 chunk.ref_id 컬럼(64자)을 넘어도
    register()가 크래시하지 않고, 청크 ref_id는 schema.name이 아닌 바운드 schema id를 쓴다."""
    services = services_factory()
    doc = json.loads(sample_openapi_3)
    long_name = "x" * 100
    doc["components"]["schemas"][long_name] = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
    }
    raw = json.dumps(doc)

    result = services.sync_service.register(project="default", source_url=None, raw_document=raw)

    schema = services.endpoint_repo.get_schema_by_name(result.document.id, long_name)
    assert schema is not None
    assert len(schema.id) <= 64

    chunks = services.chunk_repo.list_by_document(result.document.id)
    schema_chunk = next(c for c in chunks if c.chunk_type == "schema" and c.ref_id == schema.id)
    assert schema_chunk.ref_id == schema.id
    assert long_name in schema_chunk.text


def test_reindex_replaces_chunks(services_factory, sample_openapi_3: str) -> None:
    services = services_factory()
    first = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    document_id = first.document.id
    first_chunk_ids = {c.id for c in services.chunk_repo.list_by_document(document_id)}
    assert first_chunk_ids

    # 원문 변경 → 재색인
    modified = json.loads(sample_openapi_3)
    modified["info"]["title"] = "Petstore API V2"
    modified["info"]["version"] = "2.0.0"
    modified_raw = json.dumps(modified)

    result = services.sync_service.resync(document_id, raw_override=modified_raw)
    assert result.status == "reindexed"

    new_chunk_ids = {c.id for c in services.chunk_repo.list_by_document(document_id)}
    assert new_chunk_ids  # 새 청크 존재
    # 기존 청크 행이 전부 갈렸는지 확인: 하나도 재사용되지 않는 것이 정상이거나,
    # 최소한 이전 세트가 그대로 남아있지 않아야 한다 (교체 원자성)
    assert len(new_chunk_ids) == result.chunks_count


def test_resync_same_hash_is_skipped(services_factory, sample_openapi_3: str) -> None:
    services = services_factory()
    first = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    document_id = first.document.id
    before = {c.id for c in services.chunk_repo.list_by_document(document_id)}

    result = services.sync_service.resync(document_id, raw_override=sample_openapi_3)
    assert result.status == "skipped"

    after = {c.id for c in services.chunk_repo.list_by_document(document_id)}
    # 해시가 동일하면 청크 집합 변화 없음
    assert before == after


def test_resync_force_reindexes_even_with_same_hash(
    services_factory, sample_openapi_3: str
) -> None:
    services = services_factory()
    first = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    document_id = first.document.id

    result = services.sync_service.resync(
        document_id, force=True, raw_override=sample_openapi_3
    )
    assert result.status == "reindexed"


def test_index_document_with_no_business_metadata_has_no_keywords_lines(
    services_factory, sample_openapi_3: str
) -> None:
    """docs/architect-review/52 §(2) 회귀 확인: metadata 테이블이 비어 있으면
    (아직 3단계 CLI가 없어 항상 이 상태다) 기존 청크 텍스트와 동일해야 한다."""
    services = services_factory()
    result = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    chunks = services.chunk_repo.list_by_document(result.document.id)
    endpoint_chunks = [c for c in chunks if c.chunk_type == "endpoint"]
    assert endpoint_chunks
    assert all("Keywords:" not in c.text and "Phrases:" not in c.text for c in endpoint_chunks)


def test_reindex_applies_business_metadata_by_method_path(
    services_factory, sample_openapi_3: str
) -> None:
    """docs/architect-review/52 §(2): IndexerService가 (document_id, method, path)
    로 metadata를 조회해 build_chunks에 넘긴다. api_endpoint.id 를 거치지 않으므로
    재색인(endpoint 행 전부 교체) 후에도 값이 청크에 반영된다."""
    services = services_factory()
    first = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    document_id = first.document.id

    metadata = EndpointBusinessMetadata(document_id=document_id, method="GET", path="/pet/{petId}")
    metadata.keywords = ["adopt"]
    services.session.add(metadata)
    services.session.commit()

    modified = json.loads(sample_openapi_3)
    modified["info"]["title"] = "Petstore API V2"
    result = services.sync_service.resync(document_id, raw_override=json.dumps(modified))
    assert result.status == "reindexed"

    chunks = services.chunk_repo.list_by_document(document_id)
    endpoint_chunk = next(
        c
        for c in chunks
        if c.chunk_type == "endpoint" and "/pet/{petId}" in c.text and "[GET]" in c.text
    )
    assert "Keywords: adopt" in endpoint_chunk.text


def test_indexed_endpoint_chunk_persists_structure_fields(
    services_factory, sample_openapi_3: str
) -> None:
    """색인 경로가 구조 신호 3필드를 DB 행에 그대로 넣는다(78번 §6)."""
    services = services_factory()
    result = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )

    chunks = [
        c
        for c in services.chunk_repo.list_by_document(result.document.id)
        if c.chunk_type == "endpoint"
    ]
    assert chunks
    assert all(c.leaf_text for c in chunks)
    assert any("get" in c.intent_text for c in chunks)
    schema_chunks = [
        c
        for c in services.chunk_repo.list_by_document(result.document.id)
        if c.chunk_type == "schema"
    ]
    assert all(c.leaf_text == "" and c.intent_text == "" for c in schema_chunks)
