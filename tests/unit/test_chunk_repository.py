"""ChunkRepository 단위 테스트."""

from __future__ import annotations

from sqlalchemy.orm import attributes

from app.models.openapi import EMBEDDING_DIM, ApiChunk, ApiDocument
from app.repositories.chunk_repository import ChunkRepository


def _seed_document_with_chunk(session, chunk_type: str = "endpoint") -> None:
    """endpoint 청크 한 건을 embedding 값과 함께 저장한다."""
    document = ApiDocument(
        id="doc-1",
        project="default",
        source_url=None,
        title="샘플 문서",
        content_hash="hash",
        raw_text="{}",
    )
    session.add(document)
    chunk = ApiChunk(
        id="chunk-1",
        document_id="doc-1",
        chunk_type=chunk_type,
        ref_id="ref-1",
        text="hello world",
        embedding=[0.1] * EMBEDDING_DIM,
    )
    session.add(chunk)
    session.commit()
    session.expunge_all()


def test_list_endpoint_chunks_defers_embedding_column(db_session) -> None:
    """쓰이지 않는 embedding 컬럼은 지연 로딩되어 조회 시 함께 전송되지 않는다."""
    _seed_document_with_chunk(db_session)
    repo = ChunkRepository(db_session)

    chunks = repo.list_endpoint_chunks()

    assert len(chunks) == 1
    state = attributes.instance_state(chunks[0])
    assert "embedding" in state.unloaded


def test_list_endpoint_chunks_still_returns_expected_fields(db_session) -> None:
    """embedding 을 지연 로딩해도 나머지 필드는 정상 값을 유지한다."""
    _seed_document_with_chunk(db_session)
    repo = ChunkRepository(db_session)

    chunks = repo.list_endpoint_chunks()

    assert chunks[0].id == "chunk-1"
    assert chunks[0].text == "hello world"
    assert chunks[0].ref_id == "ref-1"
    assert chunks[0].chunk_type == "endpoint"


def test_list_endpoint_chunks_excludes_non_endpoint_types(db_session) -> None:
    """chunk_type 이 endpoint 가 아니면 결과에서 제외된다(기존 동작 유지)."""
    _seed_document_with_chunk(db_session, chunk_type="schema")
    repo = ChunkRepository(db_session)

    chunks = repo.list_endpoint_chunks()

    assert chunks == []
