"""키워드 검색(Postgres FTS to_tsquery OR 매칭) 테스트."""

from __future__ import annotations

from app.models import Chunk, Document
from app.repositories.chunk_repository import ChunkRepository
from app.services.search.keyword_search import KeywordSearch, tokenize_terms


def _seed_document(session, doc_id: str, project: str = "default") -> None:
    """`Document` 한 건을 저장한다(이미 있으면 건드리지 않는다)."""
    if session.get(Document, doc_id) is not None:
        return
    session.add(
        Document(
            id=doc_id,
            project=project,
            source_url=None,
            title=f"문서 {doc_id}",
            content_hash="hash",
            raw_text="{}",
        )
    )
    session.flush()


def _seed_chunk(
    session,
    chunk_id: str,
    document_id: str,
    text: str,
    ref_id: str | None = None,
    project: str = "default",
) -> None:
    """endpoint 타입 청크 한 건을 저장한다."""
    _seed_document(session, document_id, project=project)
    session.add(
        Chunk(
            id=chunk_id,
            document_id=document_id,
            chunk_type="endpoint",
            ref_id=ref_id or f"ep-{chunk_id}",
            text=text,
        )
    )


def test_tokenize_terms_lowercases_and_includes_korean() -> None:
    """ASCII/언더스코어 및 한글 덩어리를 소문자 term 으로 자른다."""
    assert tokenize_terms("Find Pet by ID") == ["find", "pet", "by", "id"]
    assert tokenize_terms("주문 목록 조회") == ["주문", "목록", "조회"]
    assert tokenize_terms("") == []


def test_keyword_search_ranks_by_overlap(db_session) -> None:
    """겹치는 term 이 많을수록 상위에 랭크된다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep-c1")
    _seed_chunk(db_session, "c2", "doc1", "create user account", ref_id="ep-c2")
    _seed_chunk(db_session, "c3", "doc1", "find user", ref_id="ep-c3")
    db_session.commit()
    search = KeywordSearch(ChunkRepository(db_session))

    hits = search.search("find pet", top_k=10)

    # c1 은 find+pet 둘 다 매칭, c3 는 find 만, c2 는 0건
    assert [h.chunk_id for h in hits] == ["c1", "c3"]
    assert hits[0].score > hits[1].score
    assert hits[0].ref_id == "ep-c1"


def test_keyword_search_respects_top_k(db_session) -> None:
    """top_k 를 초과하는 결과를 반환하지 않는다."""
    for i in range(5):
        _seed_chunk(db_session, f"c{i}", "doc1", "find pet by id token", ref_id=f"ep-c{i}")
    db_session.commit()
    search = KeywordSearch(ChunkRepository(db_session))

    hits = search.search("find pet", top_k=2)

    assert len(hits) == 2


def test_keyword_search_empty_when_no_matches(db_session) -> None:
    """겹치는 term 이 없으면 빈 리스트다."""
    _seed_chunk(db_session, "c1", "doc1", "unrelated content")
    db_session.commit()
    search = KeywordSearch(ChunkRepository(db_session))

    assert search.search("nothing relevant here zzz", top_k=5) == []


def test_keyword_search_empty_query_returns_empty(db_session) -> None:
    """공백만 있는 질의는 빈 리스트다(FTS 쿼리 자체를 던지지 않는다)."""
    _seed_chunk(db_session, "c1", "doc1", "anything goes")
    db_session.commit()
    search = KeywordSearch(ChunkRepository(db_session))

    assert search.search("    ", top_k=5) == []


def test_keyword_search_document_id_scopes_results(db_session) -> None:
    """document_id 를 지정하면 다른 문서의 청크는 후보에서 빠진다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep1")
    _seed_chunk(db_session, "c2", "doc2", "find pet again", ref_id="ep2")
    db_session.commit()
    search = KeywordSearch(ChunkRepository(db_session))

    hits = search.search("find pet", top_k=5, document_id="doc2")

    assert [h.chunk_id for h in hits] == ["c2"]


def test_keyword_search_project_scopes_results(db_session) -> None:
    """project 를 지정하면 다른 project 의 청크는 후보에서 빠진다."""
    _seed_chunk(db_session, "c1", "doc-a", "find pet by id", ref_id="ep1", project="A")
    _seed_chunk(db_session, "c2", "doc-b", "find pet again", ref_id="ep2", project="B")
    db_session.commit()
    search = KeywordSearch(ChunkRepository(db_session))

    hits = search.search("find pet", top_k=5, project="A")

    assert [h.chunk_id for h in hits] == ["c1"]


def test_keyword_search_query_variants_widen_filter_but_not_score(db_session) -> None:
    """query_variants 는 FTS OR 후보 필터만 넓히고, 점수(ts_rank)는 원본 질의 토큰만 쓴다.

    문서 검색(`DocumentSearchService`)과 동일 규약(`docs/search_flow.md` §3.1) —
    variant 는 후보를 놓치지 않게 넓히는 용도일 뿐, 순위는 항상 원본 질의로 정한다.
    """
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep-c1")
    _seed_chunk(db_session, "c2", "doc1", "동물 조회 엔드포인트", ref_id="ep-c2")
    db_session.commit()
    search = KeywordSearch(ChunkRepository(db_session))

    without_variants = search.search("find pet", top_k=10)
    assert [h.chunk_id for h in without_variants] == ["c1"]

    with_variants = search.search("find pet", top_k=10, query_variants=["동물 조회"])

    assert [h.chunk_id for h in with_variants] == ["c1", "c2"]
    assert with_variants[0].score > 0
    assert with_variants[1].score == 0.0


def test_keyword_search_matches_korean_query(db_session) -> None:
    """한글 질의도 매칭된다(A안: 한글 포함 FTS)."""
    _seed_chunk(db_session, "c1", "doc1", "주문 목록을 조회하는 API", ref_id="ep1")
    db_session.commit()
    search = KeywordSearch(ChunkRepository(db_session))

    hits = search.search("주문 목록", top_k=5)

    assert [h.chunk_id for h in hits] == ["c1"]
