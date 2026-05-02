"""벡터 검색 / 인메모리 벡터 인덱스 테스트."""

from __future__ import annotations

from app.services.indexer.embedding_provider import HashEmbeddingProvider
from app.services.indexer.vector_index import InMemoryVectorIndex
from app.services.search.vector_search import VectorSearch


def _unit_vec(components: list[float]) -> list[float]:
    import math

    norm = math.sqrt(sum(c * c for c in components))
    return [c / norm for c in components] if norm > 0 else components


def test_vector_index_topk_by_cosine() -> None:
    index = InMemoryVectorIndex()
    index.upsert("a", _unit_vec([1.0, 0.0, 0.0]))
    index.upsert("b", _unit_vec([0.9, 0.1, 0.0]))
    index.upsert("c", _unit_vec([0.0, 1.0, 0.0]))
    hits = index.search(_unit_vec([1.0, 0.05, 0.0]), top_k=2)
    ids = [h.chunk_id for h in hits]
    assert ids[0] == "a" or ids[0] == "b"
    # c 는 top-2 에 포함되면 안 된다
    assert "c" not in ids


def test_vector_index_tie_stable_with_candidates() -> None:
    index = InMemoryVectorIndex()
    same = _unit_vec([1.0, 0.0])
    index.upsert("x1", same)
    index.upsert("x2", list(same))
    hits = index.search(same, top_k=2, candidates={"x1", "x2"})
    assert {h.chunk_id for h in hits} == {"x1", "x2"}
    # 점수 동률
    assert abs(hits[0].score - hits[1].score) < 1e-9


def test_vector_index_empty_when_no_vectors() -> None:
    index = InMemoryVectorIndex()
    assert index.search([1.0, 0.0], top_k=5) == []


def test_vector_index_delete_removes_from_results() -> None:
    index = InMemoryVectorIndex()
    index.upsert("a", _unit_vec([1.0, 0.0]))
    index.upsert("b", _unit_vec([0.0, 1.0]))
    index.delete("a")
    hits = index.search(_unit_vec([1.0, 0.0]), top_k=5)
    assert {h.chunk_id for h in hits} == {"b"}


def test_vector_search_empty_query_returns_empty() -> None:
    provider = HashEmbeddingProvider(dim=16)
    index = InMemoryVectorIndex()
    index.upsert("c1", _unit_vec([1.0] + [0.0] * 15))
    vs = VectorSearch(provider, index)
    assert vs.search("   ", top_k=5) == []


def test_vector_search_returns_sorted_by_score() -> None:
    provider = HashEmbeddingProvider(dim=32)
    index = InMemoryVectorIndex()
    # 실 임베딩으로 결정적 벡터 생성
    a, b = provider.embed(["find pet by id", "create user account"])
    index.upsert("a", a)
    index.upsert("b", b)
    vs = VectorSearch(provider, index)
    hits = vs.search("find pet by id", top_k=2)
    assert hits[0].chunk_id == "a"
    assert hits[0].score >= hits[1].score
