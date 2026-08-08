"""벡터 검색(pgvector 코사인 거리) 테스트."""

from __future__ import annotations

from app.models.openapi import EMBEDDING_DIM, ApiChunk, ApiDocument
from app.repositories.chunk_repository import ChunkRepository
from app.services.indexer.embedding_provider import HashEmbeddingProvider
from app.services.search.vector_search import VectorSearch


def _unit_vec(components: list[float]) -> list[float]:
    import math

    norm = math.sqrt(sum(c * c for c in components))
    return [c / norm for c in components] if norm > 0 else components


def _add_chunk(session, doc_id: str, chunk_id: str, vector: list[float]) -> None:
    session.add(
        ApiChunk(
            id=chunk_id,
            document_id=doc_id,
            chunk_type="endpoint",
            ref_id=chunk_id,
            text=chunk_id,
            embedding=vector,
        )
    )


def test_vector_search_empty_query_returns_empty(db_session) -> None:
    provider = HashEmbeddingProvider(dim=EMBEDDING_DIM)
    chunk_repo = ChunkRepository(db_session)
    vs = VectorSearch(provider, chunk_repo)
    assert vs.search("   ", top_k=5) == []


def test_vector_search_returns_sorted_by_score(db_session) -> None:
    provider = HashEmbeddingProvider(dim=EMBEDDING_DIM)
    db_session.add(
        ApiDocument(
            id="doc1", project="default", title="t", version="v", content_hash="h", raw_text="{}"
        )
    )
    db_session.flush()
    a, b = provider.embed_documents(["find pet by id", "create user account"])
    _add_chunk(db_session, "doc1", "a", a)
    _add_chunk(db_session, "doc1", "b", b)
    db_session.commit()

    chunk_repo = ChunkRepository(db_session)
    vs = VectorSearch(provider, chunk_repo)
    hits = vs.search("find pet by id", top_k=2)
    assert hits[0].chunk_id == "a"
    assert hits[0].score >= hits[1].score


def test_vector_search_restricts_to_candidates(db_session) -> None:
    provider = HashEmbeddingProvider(dim=EMBEDDING_DIM)
    db_session.add(
        ApiDocument(
            id="doc1", project="default", title="t", version="v", content_hash="h", raw_text="{}"
        )
    )
    db_session.flush()
    a, b = provider.embed_documents(["find pet by id", "create user account"])
    _add_chunk(db_session, "doc1", "a", a)
    _add_chunk(db_session, "doc1", "b", b)
    db_session.commit()

    chunk_repo = ChunkRepository(db_session)
    vs = VectorSearch(provider, chunk_repo)
    hits = vs.search("find pet by id", top_k=5, candidates={"b"})
    assert {h.chunk_id for h in hits} == {"b"}
