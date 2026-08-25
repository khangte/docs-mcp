"""docs/architect-review/56 §3,§4: 힌트 판정·덮어쓰기 4분기·킬스위치."""

from __future__ import annotations

import pytest

from app.core.errors import WritebackDisabledError
from app.models import EMBEDDING_DIM, Document, EndpointBusinessMetadata
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.services.indexer.embedding_provider import HashEmbeddingProvider
from app.services.indexer.indexer_service import IndexerService
from app.services.metadata.spec_payload import (
    build_endpoint_input,
    build_payload_json,
    compute_source_hash,
)
from app.services.metadata.writeback_service import (
    CLIENT_WRITEBACK_MODEL,
    MetadataWritebackService,
)
from app.services.parser.document_router import parse_document


@pytest.fixture()
def seeded(db_session, sample_openapi_3):
    """샘플 문서를 색인하고 서비스/엔드포인트를 함께 돌려준다."""
    document = Document(
        id="doc-wb",
        project="default",
        source_url=None,
        title="t",
        version="1",
        doc_type="openapi",
        content_hash="h",
        raw_text=sample_openapi_3,
    )
    db_session.add(document)
    db_session.flush()
    endpoint_repo = EndpointRepository(db_session)
    chunk_repo = ChunkRepository(db_session)
    provider = HashEmbeddingProvider(dim=EMBEDDING_DIM)
    IndexerService(
        endpoint_repo=endpoint_repo,
        chunk_repo=chunk_repo,
        embedding_provider=provider,
    ).index_document(document=document, parsed=parse_document(sample_openapi_3, "openapi"))
    db_session.commit()
    service = MetadataWritebackService(
        session=db_session,
        endpoint_repo=endpoint_repo,
        document_repo=DocumentRepository(db_session),
        chunk_repo=chunk_repo,
        embedding_provider=provider,
        enabled=True,
    )
    endpoint = endpoint_repo.list_by_document("doc-wb")[0]
    return service, endpoint, db_session


def test_hint_is_missing_when_no_row(seeded) -> None:
    """행이 없으면 missing 힌트를 준다."""
    service, endpoint, _ = seeded
    hint = service.build_request_hint(endpoint.id)
    assert hint is not None
    assert hint.reason == "missing"
    assert "submit_endpoint_metadata" in hint.instruction


def test_hint_disappears_after_submit(seeded) -> None:
    """저장하면 힌트가 사라진다."""
    service, endpoint, _ = seeded
    result = service.submit(endpoint.id, "주문을 생성한다", ["order"], ["주문 생성"])
    assert result.status == "stored"
    assert result.reindexed is True
    assert service.build_request_hint(endpoint.id) is None


def test_submit_fills_provenance_columns(seeded) -> None:
    """저장값이 provenance 컬럼을 채운다."""
    service, endpoint, session = seeded
    service.submit(endpoint.id, "주문을 생성한다", ["order"], ["주문 생성"])
    row = EndpointRepository(session).get_business_metadata(
        endpoint.document_id, endpoint.method, endpoint.path
    )
    expected_hash = compute_source_hash(build_payload_json(build_endpoint_input(endpoint)))
    assert row.model == CLIENT_WRITEBACK_MODEL
    assert row.source_hash == expected_hash
    assert row.generated_at is not None


def test_does_not_overwrite_when_source_hash_unchanged(seeded) -> None:
    """해시가 같으면 덮어쓰지 않는다."""
    service, endpoint, session = seeded
    service.submit(endpoint.id, "첫 번째 설명", ["order"], [])
    result = service.submit(endpoint.id, "두 번째 설명", ["order"], [])
    assert result.status == "already_current"
    row = EndpointRepository(session).get_business_metadata(
        endpoint.document_id, endpoint.method, endpoint.path
    )
    assert row.business_description == "첫 번째 설명"


def test_overwrites_when_source_hash_changed(seeded) -> None:
    """해시가 다르면 덮어쓴다."""
    service, endpoint, session = seeded
    session.add(
        EndpointBusinessMetadata(
            document_id=endpoint.document_id,
            method=endpoint.method,
            path=endpoint.path,
            business_description="낡은 설명",
            source_hash="스펙이-바뀌기-전-해시",
            model=CLIENT_WRITEBACK_MODEL,
        )
    )
    session.commit()
    assert service.build_request_hint(endpoint.id).reason == "stale"
    result = service.submit(endpoint.id, "새 설명", ["order"], [])
    assert result.status == "stored"
    row = EndpointRepository(session).get_business_metadata(
        endpoint.document_id, endpoint.method, endpoint.path
    )
    assert row.business_description == "새 설명"


def test_rejects_when_empty_after_sanitize(seeded) -> None:
    """정규화 후 전부 비면 거부한다."""
    service, endpoint, session = seeded
    result = service.submit(endpoint.id, "  ", [], [])
    assert result.status == "rejected"
    assert result.reason == "empty_after_sanitize"
    assert (
        EndpointRepository(session).get_business_metadata(
            endpoint.document_id, endpoint.method, endpoint.path
        )
        is None
    )


def test_reports_truncated_when_clipped(seeded) -> None:
    """절단되면 truncated=True."""
    service, endpoint, _ = seeded
    result = service.submit(endpoint.id, "가" * 200, [], [])
    assert result.status == "stored"
    assert result.truncated is True


def test_disabled_service_rejects_submit_and_gives_no_hint(seeded) -> None:
    """킬스위치가 꺼지면 submit 은 거부하고 힌트도 안 준다."""
    service, endpoint, session = seeded
    disabled = MetadataWritebackService(
        session=session,
        endpoint_repo=EndpointRepository(session),
        document_repo=DocumentRepository(session),
        chunk_repo=ChunkRepository(session),
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
        enabled=False,
    )
    assert disabled.build_request_hint(endpoint.id) is None
    with pytest.raises(WritebackDisabledError):
        disabled.submit(endpoint.id, "설명", [], [])
