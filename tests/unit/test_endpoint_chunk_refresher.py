"""docs/architect-review/56 §4.4: 엔드포인트 청크 1건 재조립·재임베딩."""

from __future__ import annotations

from app.models import EMBEDDING_DIM, Chunk, Document, EndpointBusinessMetadata
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.services.indexer.embedding_provider import HashEmbeddingProvider
from app.services.indexer.endpoint_chunk_refresher import refresh_endpoint_chunk
from app.services.indexer.indexer_service import IndexerService
from app.services.parser.document_router import parse_document


def _seed(db_session, sample_openapi_3: str) -> tuple[Document, object]:
    """샘플 OpenAPI 문서를 색인해 (document, 첫 엔드포인트) 를 돌려준다."""
    document = Document(
        id="doc-refresh",
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
    indexer = IndexerService(
        endpoint_repo=endpoint_repo,
        chunk_repo=chunk_repo,
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
    )
    indexer.index_document(document=document, parsed=parse_document(sample_openapi_3, "openapi"))
    db_session.flush()
    endpoint = endpoint_repo.list_by_document("doc-refresh")[0]
    return document, endpoint


def test_injected_metadata_appears_in_chunk_text(db_session, sample_openapi_3) -> None:
    """메타데이터를 주입하면 텍스트가 청크에 반영된다."""
    document, endpoint = _seed(db_session, sample_openapi_3)
    chunk_repo = ChunkRepository(db_session)
    metadata = EndpointBusinessMetadata(
        document_id=document.id,
        method=endpoint.method,
        path=endpoint.path,
        business_description="비즈니스 설명",
        source_hash="h",
    )
    metadata.keywords = ["order", "주문"]
    metadata.user_phrases = ["주문 취소", "cancel order"]

    updated = refresh_endpoint_chunk(
        document=document,
        endpoint=endpoint,
        metadata=metadata,
        chunk_repo=chunk_repo,
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
    )

    assert updated is True
    chunk = (
        db_session.query(Chunk)
        .filter(Chunk.document_id == document.id, Chunk.ref_id == endpoint.id)
        .one()
    )
    assert "Keywords: order, 주문" in chunk.text
    assert "Phrases: 주문 취소; cancel order" in chunk.text
    assert "BusinessDesc: 비즈니스 설명" in chunk.text


def test_produces_same_text_as_indexer_path(db_session, sample_openapi_3) -> None:
    """metadata=None 이면 색인이 만든 원래 청크 텍스트와 글자 단위로 같아야 한다."""
    document, endpoint = _seed(db_session, sample_openapi_3)
    chunk_repo = ChunkRepository(db_session)
    before = (
        db_session.query(Chunk)
        .filter(Chunk.document_id == document.id, Chunk.ref_id == endpoint.id)
        .one()
        .text
    )

    refresh_endpoint_chunk(
        document=document,
        endpoint=endpoint,
        metadata=None,
        chunk_repo=chunk_repo,
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
    )

    after = (
        db_session.query(Chunk)
        .filter(Chunk.document_id == document.id, Chunk.ref_id == endpoint.id)
        .one()
        .text
    )
    assert after == before


def test_returns_false_when_endpoint_missing_from_spec(db_session, sample_openapi_3) -> None:
    """스펙에 없는 엔드포인트면 False 를 반환한다."""
    document, endpoint = _seed(db_session, sample_openapi_3)
    endpoint.path = "/사라진경로"
    updated = refresh_endpoint_chunk(
        document=document,
        endpoint=endpoint,
        metadata=None,
        chunk_repo=ChunkRepository(db_session),
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
    )
    assert updated is False
