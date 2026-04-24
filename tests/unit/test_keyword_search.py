"""키워드 검색 테스트."""

from __future__ import annotations

from src.models.openapi import ApiChunk
from src.services.search.keyword_search import KeywordSearch, tokenize


class _FakeChunkRepo:
    """KeywordSearch 가 사용하는 `list_all` 만 제공하는 페이크 리포지토리."""

    def __init__(self, chunks: list[ApiChunk]) -> None:
        self._chunks = chunks

    def list_all(self) -> list[ApiChunk]:
        return list(self._chunks)


def _make_chunk(chunk_id: str, text: str, document_id: str = "doc1") -> ApiChunk:
    chunk = ApiChunk(
        id=chunk_id,
        document_id=document_id,
        chunk_type="endpoint",
        ref_id=f"ep-{chunk_id}",
        text=text,
    )
    chunk.embedding = [0.0]
    return chunk


def test_tokenize_lowercases_and_splits() -> None:
    assert tokenize("Find Pet by ID") == ["find", "pet", "by", "id"]
    assert tokenize("") == []


def test_keyword_search_ranks_by_overlap() -> None:
    chunks = [
        _make_chunk("c1", "find pet by id"),
        _make_chunk("c2", "create user account"),
        _make_chunk("c3", "find user"),
    ]
    search = KeywordSearch(_FakeChunkRepo(chunks))
    hits = search.search("find pet", top_k=10)
    # c1 은 find+pet 둘 다 매칭, c3 는 find 만, c2 는 0건
    assert [h.chunk_id for h in hits] == ["c1", "c3"]
    assert hits[0].score > hits[1].score


def test_keyword_search_respects_top_k() -> None:
    chunks = [
        _make_chunk(f"c{i}", "find pet by id token") for i in range(5)
    ]
    search = KeywordSearch(_FakeChunkRepo(chunks))
    hits = search.search("find pet", top_k=2)
    assert len(hits) == 2


def test_keyword_search_empty_when_no_matches() -> None:
    chunks = [_make_chunk("c1", "unrelated content")]
    search = KeywordSearch(_FakeChunkRepo(chunks))
    assert search.search("nothing relevant here zzz", top_k=5) == []


def test_keyword_search_empty_query_returns_empty() -> None:
    chunks = [_make_chunk("c1", "anything goes")]
    search = KeywordSearch(_FakeChunkRepo(chunks))
    assert search.search("    ", top_k=5) == []


def test_keyword_search_respects_candidate_set() -> None:
    chunks = [
        _make_chunk("c1", "find pet by id"),
        _make_chunk("c2", "find pet by id"),
    ]
    search = KeywordSearch(_FakeChunkRepo(chunks))
    hits = search.search("find pet", top_k=5, candidates={"c2"})
    assert [h.chunk_id for h in hits] == ["c2"]
