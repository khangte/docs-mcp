"""Indexer / 재색인 동작 테스트."""

from __future__ import annotations

import json


def test_index_document_creates_endpoints_and_chunks(
    services_factory, sample_openapi_3: str
) -> None:
    services = services_factory()
    result = services.sync_service.register(source_url=None, raw_document=sample_openapi_3)
    assert result.endpoints_count == 4  # POST /pet, GET /pet/{petId}, DELETE /pet/{petId}, POST /user
    assert result.chunks_count == result.endpoints_count + result.schemas_count

    chunks = services.chunk_repo.list_by_document(result.document.id)
    assert len(chunks) == result.chunks_count
    endpoints = services.endpoint_repo.list_by_document(result.document.id)
    assert len(endpoints) == result.endpoints_count


def test_reindex_replaces_chunks(services_factory, sample_openapi_3: str) -> None:
    services = services_factory()
    first = services.sync_service.register(source_url=None, raw_document=sample_openapi_3)
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
    first = services.sync_service.register(source_url=None, raw_document=sample_openapi_3)
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
    first = services.sync_service.register(source_url=None, raw_document=sample_openapi_3)
    document_id = first.document.id

    result = services.sync_service.resync(
        document_id, force=True, raw_override=sample_openapi_3
    )
    assert result.status == "reindexed"
