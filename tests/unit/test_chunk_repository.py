"""ChunkRepository 단위 테스트."""

from __future__ import annotations

from sqlalchemy.orm import attributes

from app.models.openapi import EMBEDDING_DIM, ApiChunk, ApiDocument
from app.repositories.chunk_repository import ChunkRepository


def _seed_document(session, doc_id: str, project: str = "default") -> None:
    """`ApiDocument` 한 건을 저장한다(이미 있으면 건드리지 않는다)."""
    if session.get(ApiDocument, doc_id) is not None:
        return
    session.add(
        ApiDocument(
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
    chunk_type: str = "endpoint",
    project: str = "default",
) -> None:
    """청크 한 건을 저장한다(`text_tsv` 는 DB 가 자동 채운다)."""
    _seed_document(session, document_id, project=project)
    session.add(
        ApiChunk(
            id=chunk_id,
            document_id=document_id,
            chunk_type=chunk_type,
            ref_id=ref_id or f"ref-{chunk_id}",
            text=text,
        )
    )


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


def test_list_all_defers_text_tsv_column(db_session) -> None:
    """text_tsv 는 필터 전용 컬럼이므로 일반 조회 시 지연 로딩되어야 한다.

    `mapped_column(..., deferred=True)` 없이는 `select(ApiChunk)` 로 전체
    컬럼을 적재하는 `list_all()`/`list_endpoint_chunks()` 같은 경로에서
    매번 text_tsv(청크 전체 텍스트의 lexeme 표현)까지 전송된다.
    """
    _seed_document_with_chunk(db_session)
    repo = ChunkRepository(db_session)

    chunks = repo.list_all()

    assert len(chunks) == 1
    state = attributes.instance_state(chunks[0])
    assert "text_tsv" in state.unloaded


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


# --- P1: search_endpoint_by_text (Postgres FTS) --------------------------------


def test_search_endpoint_by_text_matches_any_term(db_session) -> None:
    """term 을 OR 매칭한다 — 하나라도 겹치면 후보다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep1")
    _seed_chunk(db_session, "c2", "doc1", "create user account", ref_id="ep2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["pet", "nomatch"], top_k=10)

    assert [h.chunk_id for h in hits] == ["c1"]
    assert hits[0].ref_id == "ep1"


def test_search_endpoint_by_text_ranks_more_overlap_higher(db_session) -> None:
    """겹치는 term 이 많은 청크가 더 높은 순위(score)를 받는다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep1")
    _seed_chunk(db_session, "c2", "doc1", "find user", ref_id="ep2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["find", "pet"], top_k=10)

    assert [h.chunk_id for h in hits] == ["c1", "c2"]
    assert hits[0].score > hits[1].score


def test_search_endpoint_by_text_respects_top_k(db_session) -> None:
    """top_k 를 초과하는 결과를 반환하지 않는다."""
    for i in range(5):
        _seed_chunk(db_session, f"c{i}", "doc1", "find pet by id", ref_id=f"ep{i}")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["pet"], top_k=2)

    assert len(hits) == 2


def test_search_endpoint_by_text_excludes_non_endpoint_chunks(db_session) -> None:
    """chunk_type 이 endpoint 가 아니면 검색 대상에서 제외된다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", chunk_type="schema")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.search_endpoint_by_text(["pet"], top_k=10) == []


def test_search_endpoint_by_text_document_id_scopes_results(db_session) -> None:
    """document_id 를 지정하면 다른 문서의 청크는 후보에서 빠진다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep1")
    _seed_chunk(db_session, "c2", "doc2", "find pet again", ref_id="ep2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["pet"], top_k=10, document_id="doc2")

    assert [h.chunk_id for h in hits] == ["c2"]


def test_search_endpoint_by_text_project_scopes_results(db_session) -> None:
    """project 를 지정하면 ApiDocument 조인으로 다른 project 청크가 빠진다."""
    _seed_chunk(db_session, "c1", "doc-a", "find pet by id", ref_id="ep1", project="A")
    _seed_chunk(db_session, "c2", "doc-b", "find pet again", ref_id="ep2", project="B")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["pet"], top_k=10, project="A")

    assert [h.chunk_id for h in hits] == ["c1"]


def test_search_endpoint_by_text_no_match_returns_empty(db_session) -> None:
    """겹치는 term 이 하나도 없으면 빈 리스트다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.search_endpoint_by_text(["zzzznomatch"], top_k=10) == []


def test_search_endpoint_by_text_empty_terms_returns_empty(db_session) -> None:
    """term 이 비어 있으면 빈 리스트다(쿼리 자체를 던지지 않는다)."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.search_endpoint_by_text([], top_k=10) == []


def test_search_endpoint_by_text_matches_word_embedded_in_raw_path(db_session) -> None:
    """경로 세그먼트에만 등장하는 단어도 bare word 로 매칭된다(회귀 고정).

    raw path 를 정규화 없이 그대로 `to_tsvector('simple', text)` 에 넣으면
    '/orders/{orderId}' 가 슬래시·중괄호 때문에 '/orders' 같은 컴파운드
    lexeme 으로 뭉쳐 bare 'orders' 로는 안 잡힌다(architect 판단으로 발견
    → regexp_replace 로 구분자를 공백화해 해소). summary/description 에
    'orders' 를 반복하지 않는 실사용 시나리오를 재현한다.
    """
    _seed_chunk(
        db_session,
        "c1",
        "doc1",
        "[GET] /orders/{orderId} — Retrieve a single record by identifier",
        ref_id="ep1",
    )
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["orders"], top_k=10)

    assert [h.chunk_id for h in hits] == ["c1"]


def test_search_endpoint_by_text_matches_ascii_term_in_mixed_script_compound(db_session) -> None:
    """ASCII+한글이 공백 없이 붙은 복합어('GET요청')에서도 순수 ASCII/한글
    term 이 각각 bare lexeme 으로 매칭된다(회귀 고정).

    스크립트 경계 공백삽입 없이 구두점만 공백화하면 'GET요청' 전체가 단일
    lexeme 'get요청'으로 뭉쳐 'get'/'요청' 어느 쪽으로도 안 잡힌다
    (architect 판단으로 발견 → 생성컬럼식에 경계 삽입 정규화 추가로 해소).
    """
    _seed_chunk(db_session, "c1", "doc1", "GET요청 상태 확인", ref_id="ep1")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits_ascii = repo.search_endpoint_by_text(["get"], top_k=10)
    hits_korean = repo.search_endpoint_by_text(["요청"], top_k=10)

    assert [h.chunk_id for h in hits_ascii] == ["c1"]
    assert [h.chunk_id for h in hits_korean] == ["c1"]


def test_search_endpoint_by_text_matches_korean_terms(db_session) -> None:
    """한글 term 도 매칭된다(A안: 한글 포함 FTS)."""
    _seed_chunk(db_session, "c1", "doc1", "주문 목록을 조회한다", ref_id="ep1")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["주문"], top_k=10)

    assert [h.chunk_id for h in hits] == ["c1"]


def test_search_endpoint_by_text_tie_break_by_id_ascending(db_session) -> None:
    """동점(score 동일)이면 chunk id 오름차순으로 결정성을 보장한다."""
    _seed_chunk(db_session, "c2", "doc1", "find pet", ref_id="ep2")
    _seed_chunk(db_session, "c1", "doc1", "find pet", ref_id="ep1")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["find", "pet"], top_k=10)

    assert [h.chunk_id for h in hits] == ["c1", "c2"]


def test_search_endpoint_by_text_term_with_quote_is_escaped_safely(db_session) -> None:
    """term 에 작은따옴표가 섞여도 tsquery 파싱 오류 없이 안전하게 처리된다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.search_endpoint_by_text(["o'brien"], top_k=10) == []


# --- P1: has_endpoint_chunks -----------------------------------------------


def test_has_endpoint_chunks_true_when_present(db_session) -> None:
    """endpoint 청크가 하나라도 있으면 True."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.has_endpoint_chunks() is True


def test_has_endpoint_chunks_false_when_none(db_session) -> None:
    """청크가 전혀 없으면 False."""
    repo = ChunkRepository(db_session)

    assert repo.has_endpoint_chunks() is False


def test_has_endpoint_chunks_false_when_only_non_endpoint(db_session) -> None:
    """schema 청크만 있으면 False(endpoint 타입만 센다)."""
    _seed_chunk(db_session, "c1", "doc1", "schema text", chunk_type="schema")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.has_endpoint_chunks() is False


def test_has_endpoint_chunks_respects_document_id_and_project_scope(db_session) -> None:
    """document_id/project 스코프를 벗어난 청크는 세지 않는다."""
    _seed_chunk(db_session, "c1", "doc-a", "find pet by id", project="A")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.has_endpoint_chunks(document_id="doc-b") is False
    assert repo.has_endpoint_chunks(project="B") is False
    assert repo.has_endpoint_chunks(document_id="doc-a") is True
    assert repo.has_endpoint_chunks(project="A") is True
