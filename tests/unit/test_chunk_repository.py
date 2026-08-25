"""ChunkRepository 단위 테스트."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import attributes

from app.models import EMBEDDING_DIM, Chunk, Document
from app.models.document_meta import SOURCE_DRIVE, DocumentMeta
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_filters import DocumentMetaFilter


def _seed_document(
    session, doc_id: str, project: str = "default", doc_type: str = "openapi"
) -> None:
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
            doc_type=doc_type,
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
    doc_type: str = "openapi",
) -> None:
    """청크 한 건을 저장한다(`text_tsv` 는 DB 가 자동 채운다)."""
    _seed_document(session, document_id, project=project, doc_type=doc_type)
    session.add(
        Chunk(
            id=chunk_id,
            document_id=document_id,
            chunk_type=chunk_type,
            ref_id=ref_id or f"ref-{chunk_id}",
            text=text,
        )
    )


def _seed_document_with_chunk(session) -> None:
    """endpoint 청크 한 건을 embedding 값과 함께 저장한다."""
    document = Document(
        id="doc-1",
        project="default",
        source_url=None,
        title="샘플 문서",
        content_hash="hash",
        raw_text="{}",
    )
    session.add(document)
    chunk = Chunk(
        id="chunk-1",
        document_id="doc-1",
        chunk_type="endpoint",
        ref_id="ref-1",
        text="hello world",
        embedding=[0.1] * EMBEDDING_DIM,
    )
    session.add(chunk)
    session.commit()
    session.expunge_all()


def test_list_all_defers_text_tsv_column(db_session) -> None:
    """text_tsv 는 필터 전용 컬럼이므로 일반 조회 시 지연 로딩되어야 한다.

    `mapped_column(..., deferred=True)` 없이는 `select(Chunk)` 로 전체
    컬럼을 적재하는 `list_all()` 같은 경로에서 매번 text_tsv(청크 전체
    텍스트의 lexeme 표현)까지 전송된다.
    """
    _seed_document_with_chunk(db_session)
    repo = ChunkRepository(db_session)

    chunks = repo.list_all()

    assert len(chunks) == 1
    state = attributes.instance_state(chunks[0])
    assert "text_tsv" in state.unloaded


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


def test_search_endpoint_by_text_score_terms_scores_independently_of_filter_terms(
    db_session,
) -> None:
    """`score_terms` 를 주면 필터(`terms`)와 별개로 그 term 만으로 ts_rank 를 계산한다.

    query_variants 배선: 필터는 원본+variant term 을 OR 로 넓히되, 점수는
    원본 term 만으로 계산해야 한다(엔드포인트 검색도 문서 검색과 동일 규약).
    """
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep1")
    _seed_chunk(db_session, "c2", "doc1", "동물 조회 엔드포인트", ref_id="ep2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    # 필터는 원본("find","pet") + variant("동물","조회")로 넓어져 c2 도 후보에 들어오지만,
    # score_terms 가 원본만이라 c2 의 ts_rank 는 0 이어야 한다.
    hits = repo.search_endpoint_by_text(
        ["find", "pet", "동물", "조회"], top_k=10, score_terms=["find", "pet"]
    )

    assert [h.chunk_id for h in hits] == ["c1", "c2"]
    assert hits[0].score > 0
    assert hits[1].score == 0.0


def test_search_endpoint_by_text_score_terms_defaults_to_terms(db_session) -> None:
    """`score_terms` 를 생략하면 기존처럼 `terms` 로 점수를 계산한다(하위 호환)."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep1")
    _seed_chunk(db_session, "c2", "doc1", "find user", ref_id="ep2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["find", "pet"], top_k=10)

    assert [h.chunk_id for h in hits] == ["c1", "c2"]
    assert hits[0].score > hits[1].score


def test_search_endpoint_by_text_document_id_scopes_results(db_session) -> None:
    """document_id 를 지정하면 다른 문서의 청크는 후보에서 빠진다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep1")
    _seed_chunk(db_session, "c2", "doc2", "find pet again", ref_id="ep2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["pet"], top_k=10, document_id="doc2")

    assert [h.chunk_id for h in hits] == ["c2"]


def test_search_endpoint_by_text_project_scopes_results(db_session) -> None:
    """project 를 지정하면 Document 조인으로 다른 project 청크가 빠진다."""
    _seed_chunk(db_session, "c1", "doc-a", "find pet by id", ref_id="ep1", project="A")
    _seed_chunk(db_session, "c2", "doc-b", "find pet again", ref_id="ep2", project="B")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["pet"], top_k=10, project="A")

    assert [h.chunk_id for h in hits] == ["c1"]


def test_search_endpoint_by_text_doc_types_excludes_other_doc_types(db_session) -> None:
    """doc_types 를 지정하면 그 doc_type 이 아닌 문서의 청크는 후보에서 빠진다.

    등록형 문서(markdown/csv 등)와 협업 문서(drive/notion)가 같은
    chunk_type="section" 을 공유하므로, 문서 검색 arm 이 이 필터로
    등록형 문서를 걸러낸다(45번 리뷰 §3.2).
    """
    _seed_chunk(db_session, "c1", "doc-drive", "find pet by id", ref_id="ep1", doc_type="drive")
    _seed_chunk(db_session, "c2", "doc-markdown", "find pet again", ref_id="ep2", doc_type="markdown")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["pet"], top_k=10, doc_types=["drive", "notion"])

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


# --- 58번 §4.3: phrase_terms(keyword arm 복합어 대칭) -----------------------


def test_search_endpoint_by_text_phrase_terms_omitted_matches_explicit_none(db_session) -> None:
    """phrase_terms 를 생략한 결과와 명시적으로 None 을 넘긴 결과가 동일하다(회귀).

    두 파라미터가 없던 기존 tsquery 조립과 동일해야 한다는 계약을, 생략과
    명시적 None 이 같은 결과를 내는지로 확인한다.
    """
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep1")
    _seed_chunk(db_session, "c2", "doc1", "find user", ref_id="ep2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    omitted = repo.search_endpoint_by_text(["find", "pet"], top_k=10)
    explicit_none = repo.search_endpoint_by_text(
        ["find", "pet"], top_k=10, phrase_terms=None, score_phrase_terms=None
    )

    assert [(h.chunk_id, h.score) for h in omitted] == [
        (h.chunk_id, h.score) for h in explicit_none
    ]


def test_search_endpoint_by_text_phrase_terms_matches_adjacent_lexemes(db_session) -> None:
    """phrase_terms 그룹은 `<->` 로 묶여, 본문에 두 단어가 인접해야만 매치한다."""
    _seed_chunk(db_session, "c1", "doc1", "결제 장애 대응 절차", chunk_type="section", ref_id="ep1")
    _seed_chunk(
        db_session, "c2", "doc1", "결제 이력과 장애 목록", chunk_type="section", ref_id="ep2"
    )
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(
        [], top_k=10, chunk_type="section", phrase_terms=[["결제", "장애"]]
    )

    assert [h.chunk_id for h in hits] == ["c1"]


def test_search_endpoint_by_text_phrase_terms_or_combined_with_terms(db_session) -> None:
    """phrase_terms 는 terms 와 `|`(OR) 로 결합되어 둘 중 하나만 맞아도 후보가 된다."""
    _seed_chunk(db_session, "c1", "doc1", "결제 장애 대응 절차", chunk_type="section", ref_id="ep1")
    _seed_chunk(db_session, "c2", "doc1", "환불 정책 안내", chunk_type="section", ref_id="ep2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(
        ["환불"], top_k=10, chunk_type="section", phrase_terms=[["결제", "장애"]]
    )

    assert {h.chunk_id for h in hits} == {"c1", "c2"}


def test_search_endpoint_by_text_phrase_terms_group_with_empty_part_is_dropped(
    db_session,
) -> None:
    """빈 문자열 원소가 든 phrase 그룹은 통째로 버려진다(에러 없이 무시)."""
    _seed_chunk(db_session, "c1", "doc1", "결제 장애 대응 절차", chunk_type="section", ref_id="ep1")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(
        [], top_k=10, chunk_type="section", phrase_terms=[["결제", ""]]
    )

    assert hits == []


def test_search_endpoint_by_text_phrase_term_with_quote_is_escaped_safely(db_session) -> None:
    """phrase 그룹 원소에 작은따옴표가 섞여도 tsquery 파싱 오류 없이 처리된다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep1")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(
        [], top_k=10, phrase_terms=[["o'brien", "pet"]]
    )

    assert hits == []


def test_search_endpoint_by_text_score_phrase_terms_scores_independently(db_session) -> None:
    """score_phrase_terms 를 주면 필터(phrase_terms)와 별개로 그것만으로 ts_rank 를 계산한다.

    필터는 terms(`'결제장애'`) + phrase_terms(`'결제' <-> '장애'`)로 c1(구문
    인접)·c2(단일 lexeme) 둘 다 후보에 들이지만, score 는 score_terms만
    쓰도록 score_phrase_terms 를 빈 리스트로 눌러 phrase 매치인 c1 의
    점수를 0 으로 만든다.
    """
    _seed_chunk(db_session, "c1", "doc1", "결제 장애 대응 절차", chunk_type="section", ref_id="ep1")
    _seed_chunk(
        db_session, "c2", "doc1", "결제장애 대응 매뉴얼 개요", chunk_type="section", ref_id="ep2"
    )
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(
        ["결제장애"],
        top_k=10,
        chunk_type="section",
        score_terms=["결제장애"],
        phrase_terms=[["결제", "장애"]],
        score_phrase_terms=[],
    )

    assert {h.chunk_id for h in hits} == {"c1", "c2"}
    scores = {h.chunk_id: h.score for h in hits}
    assert scores["c1"] == 0.0
    assert scores["c2"] > 0.0


def test_search_endpoint_by_text_score_phrase_terms_defaults_to_phrase_terms(db_session) -> None:
    """score_phrase_terms 를 생략하면 phrase_terms 로 점수를 계산한다(score_terms 와 동일 패턴)."""
    _seed_chunk(db_session, "c1", "doc1", "결제 장애 대응 절차", chunk_type="section", ref_id="ep1")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(
        [], top_k=10, chunk_type="section", phrase_terms=[["결제", "장애"]]
    )

    assert len(hits) == 1
    assert hits[0].score > 0.0


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


# --- RRF: search_by_vector ref_id 프로젝션(P2 완성) --------------------------


def test_search_by_vector_returns_ref_id(db_session) -> None:
    """벡터 검색 결과에 ref_id 가 함께 담겨 chunk_id→ref_id 역매핑 없이 쓸 수 있다."""
    document = Document(
        id="doc-1",
        project="default",
        source_url=None,
        title="샘플 문서",
        content_hash="hash",
        raw_text="{}",
    )
    db_session.add(document)
    db_session.add(
        Chunk(
            id="chunk-1",
            document_id="doc-1",
            chunk_type="endpoint",
            ref_id="ep-1",
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_by_vector([0.1] * EMBEDDING_DIM, top_k=5)

    assert len(hits) == 1
    assert hits[0].chunk_id == "chunk-1"
    assert hits[0].ref_id == "ep-1"


def test_search_by_vector_ties_break_by_id_ascending(db_session) -> None:
    """거리가 동일한 청크들은 id 오름차순으로 결정적으로 정렬된다."""
    document = Document(
        id="doc-1",
        project="default",
        source_url=None,
        title="샘플 문서",
        content_hash="hash",
        raw_text="{}",
    )
    db_session.add(document)
    for chunk_id in ["chunk-c", "chunk-a", "chunk-b"]:
        db_session.add(
            Chunk(
                id=chunk_id,
                document_id="doc-1",
                chunk_type="endpoint",
                ref_id=f"ep-{chunk_id}",
                text="hello world",
                embedding=[0.1] * EMBEDDING_DIM,
            )
        )
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_by_vector([0.1] * EMBEDDING_DIM, top_k=5)

    assert [hit.chunk_id for hit in hits] == ["chunk-a", "chunk-b", "chunk-c"]


# --- Q2: search_by_vector 의 chunk_type='endpoint' SQL 필터 ------------------


def test_search_by_vector_excludes_non_endpoint_chunks_without_candidate_ids(
    db_session,
) -> None:
    """candidate_ids 없이도 schema 청크는 벡터 검색 결과에서 제외된다.

    이전에는 `candidate_ids`(endpoint 청크 ID 집합)가 "endpoint 만 남기는
    필터"를 겸했다. 전역 스코프에서는 candidate_ids 를 아예 넘기지 않게
    되므로(Q2), SQL 자체에 chunk_type 조건이 없으면 schema 청크가 섞여
    들어온다.
    """
    document = Document(
        id="doc-1",
        project="default",
        source_url=None,
        title="샘플 문서",
        content_hash="hash",
        raw_text="{}",
    )
    db_session.add(document)
    db_session.add(
        Chunk(
            id="chunk-endpoint",
            document_id="doc-1",
            chunk_type="endpoint",
            ref_id="ep-1",
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )
    db_session.add(
        Chunk(
            id="chunk-schema",
            document_id="doc-1",
            chunk_type="schema",
            ref_id="schema-1",
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_by_vector([0.1] * EMBEDDING_DIM, top_k=5)

    assert [h.chunk_id for h in hits] == ["chunk-endpoint"]


def test_search_by_vector_doc_types_excludes_other_doc_types(db_session) -> None:
    """doc_types 를 지정하면 그 doc_type 이 아닌 문서의 청크는 벡터 검색에서도 빠진다."""
    _seed_document(db_session, "doc-drive", doc_type="drive")
    _seed_document(db_session, "doc-markdown", doc_type="markdown")
    db_session.add(
        Chunk(
            id="chunk-drive",
            document_id="doc-drive",
            chunk_type="section",
            ref_id="ref-drive",
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )
    db_session.add(
        Chunk(
            id="chunk-markdown",
            document_id="doc-markdown",
            chunk_type="section",
            ref_id="ref-markdown",
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_by_vector(
        [0.1] * EMBEDDING_DIM, top_k=5, chunk_type="section", doc_types=["drive", "notion"]
    )

    assert [h.chunk_id for h in hits] == ["chunk-drive"]


# --- P6: search_by_vector 의 hnsw.ef_search 세션 GUC 설정 ---------------------


def test_search_by_vector_sets_hnsw_ef_search_floor(db_session) -> None:
    """벡터 검색 시 hnsw.ef_search 를 모듈 상수(100) 이상으로 설정한다."""
    repo = ChunkRepository(db_session)

    repo.search_by_vector([0.1] * EMBEDDING_DIM, top_k=5)

    value = db_session.execute(text("SHOW hnsw.ef_search")).scalar()
    assert int(value) == 100


def test_search_by_vector_ef_search_at_least_top_k(db_session) -> None:
    """top_k 가 기본 하한(100)보다 크면 ef_search 도 top_k 이상으로 맞춘다."""
    repo = ChunkRepository(db_session)

    repo.search_by_vector([0.1] * EMBEDDING_DIM, top_k=150)

    value = db_session.execute(text("SHOW hnsw.ef_search")).scalar()
    assert int(value) == 150


# --- RRF: list_endpoint_chunk_ids(전체 로우 미적재 스코프 조회) ----------------


def test_list_endpoint_chunk_ids_returns_ids_only(db_session) -> None:
    """endpoint 청크 ID 만 가볍게 반환한다(embedding 등 다른 컬럼 적재 없음)."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id")
    _seed_chunk(db_session, "c2", "doc1", "schema text", chunk_type="schema")
    db_session.commit()
    repo = ChunkRepository(db_session)

    ids = repo.list_endpoint_chunk_ids()

    assert ids == {"c1"}


def test_list_endpoint_chunk_ids_scopes_by_document_id(db_session) -> None:
    """document_id 를 지정하면 다른 문서의 청크 ID 는 제외된다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id")
    _seed_chunk(db_session, "c2", "doc2", "find pet again")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.list_endpoint_chunk_ids(document_id="doc2") == {"c2"}


def test_list_endpoint_chunk_ids_scopes_by_project(db_session) -> None:
    """project 를 지정하면 다른 project 의 청크 ID 는 제외된다."""
    _seed_chunk(db_session, "c1", "doc-a", "find pet by id", project="A")
    _seed_chunk(db_session, "c2", "doc-b", "find pet again", project="B")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.list_endpoint_chunk_ids(project="A") == {"c1"}


def test_has_endpoint_chunks_respects_document_id_and_project_scope(db_session) -> None:
    """document_id/project 스코프를 벗어난 청크는 세지 않는다."""
    _seed_chunk(db_session, "c1", "doc-a", "find pet by id", project="A")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.has_endpoint_chunks(document_id="doc-b") is False
    assert repo.has_endpoint_chunks(project="B") is False
    assert repo.has_endpoint_chunks(document_id="doc-a") is True
    assert repo.has_endpoint_chunks(project="A") is True


# --- doc36 Phase3 #11: chunk_type 인자 승격(공유 메서드, section 조회 겸용) ------


def test_list_endpoint_chunk_ids_chunk_type_param_selects_section(db_session) -> None:
    """chunk_type='section' 을 넘기면 section 청크 ID 만 반환한다(기본값은 endpoint 유지)."""
    _seed_chunk(db_session, "c1", "doc1", "endpoint text", chunk_type="endpoint")
    _seed_chunk(db_session, "c2", "doc1", "section text", chunk_type="section")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.list_endpoint_chunk_ids() == {"c1"}
    assert repo.list_endpoint_chunk_ids(chunk_type="section") == {"c2"}


def test_has_endpoint_chunks_chunk_type_param_selects_section(db_session) -> None:
    """chunk_type='section' 을 넘기면 section 청크 존재 여부만 본다."""
    _seed_chunk(db_session, "c1", "doc1", "endpoint text", chunk_type="endpoint")
    db_session.commit()
    repo = ChunkRepository(db_session)

    assert repo.has_endpoint_chunks(chunk_type="section") is False
    assert repo.has_endpoint_chunks(chunk_type="endpoint") is True


def test_search_endpoint_by_text_chunk_type_param_selects_section(db_session) -> None:
    """chunk_type='section' 을 넘기면 section 청크만 FTS 검색 대상이 된다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", chunk_type="endpoint")
    _seed_chunk(db_session, "c2", "doc1", "find pet again", ref_id="sec-1", chunk_type="section")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["pet"], top_k=10, chunk_type="section")

    assert [h.chunk_id for h in hits] == ["c2"]


def test_search_endpoint_by_text_returns_document_id(db_session) -> None:
    """FTS 히트에 document_id 가 함께 담겨 문서 단위 RRF 융합 키로 쓸 수 있다."""
    _seed_chunk(db_session, "c1", "doc1", "find pet by id", ref_id="ep1")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["pet"], top_k=10)

    assert hits[0].document_id == "doc1"


def test_search_by_vector_returns_document_id(db_session) -> None:
    """벡터 검색 히트에 document_id 가 함께 담긴다."""
    document = Document(
        id="doc-1",
        project="default",
        source_url=None,
        title="샘플 문서",
        content_hash="hash",
        raw_text="{}",
    )
    db_session.add(document)
    db_session.add(
        Chunk(
            id="chunk-1",
            document_id="doc-1",
            chunk_type="endpoint",
            ref_id="ep-1",
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_by_vector([0.1] * EMBEDDING_DIM, top_k=5)

    assert hits[0].document_id == "doc-1"


def _seed_chunk_with_embedding(
    session, chunk_id: str, document_id: str, ref_id: str, project: str = "default"
) -> None:
    """embedding 값을 가진 청크 한 건을 저장한다."""
    _seed_document(session, document_id, project=project)
    session.add(
        Chunk(
            id=chunk_id,
            document_id=document_id,
            chunk_type="endpoint",
            ref_id=ref_id,
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )


def test_search_by_vector_project_scopes_results(db_session) -> None:
    """project 를 지정하면 다른 project 의 청크는 벡터 검색에서 제외된다."""
    _seed_chunk_with_embedding(db_session, "c1", "doc-a", ref_id="ep1", project="A")
    _seed_chunk_with_embedding(db_session, "c2", "doc-b", ref_id="ep2", project="B")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_by_vector([0.1] * EMBEDDING_DIM, top_k=5, project="A")

    assert [h.chunk_id for h in hits] == ["c1"]


def test_search_by_vector_document_id_scopes_results(db_session) -> None:
    """document_id 를 지정하면 다른 문서의 청크는 벡터 검색에서 제외된다."""
    _seed_chunk_with_embedding(db_session, "c1", "doc1", ref_id="ep1")
    _seed_chunk_with_embedding(db_session, "c2", "doc2", ref_id="ep2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_by_vector([0.1] * EMBEDDING_DIM, top_k=5, document_id="doc2")

    assert [h.chunk_id for h in hits] == ["c2"]


def test_get_texts_by_ids_returns_text_map(db_session) -> None:
    """chunk_id 집합의 text 를 배치 조회한다(문서당 반복 조회 방지)."""
    _seed_chunk(db_session, "c1", "doc1", "first text", ref_id="ep1")
    _seed_chunk(db_session, "c2", "doc1", "second text", ref_id="ep2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    result = repo.get_texts_by_ids(["c1", "c2", "missing"])

    assert result == {"c1": "first text", "c2": "second text"}


def test_get_texts_by_ids_empty_input_returns_empty_dict(db_session) -> None:
    """빈 입력이면 쿼리 없이 빈 dict 를 반환한다."""
    repo = ChunkRepository(db_session)

    assert repo.get_texts_by_ids([]) == {}


def test_search_by_vector_chunk_type_param_selects_section(db_session) -> None:
    """chunk_type='section' 을 넘기면 section 청크만 벡터 검색 대상이 된다."""
    document = Document(
        id="doc-1",
        project="default",
        source_url=None,
        title="샘플 문서",
        content_hash="hash",
        raw_text="{}",
    )
    db_session.add(document)
    db_session.add(
        Chunk(
            id="chunk-endpoint",
            document_id="doc-1",
            chunk_type="endpoint",
            ref_id="ep-1",
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )
    db_session.add(
        Chunk(
            id="chunk-section",
            document_id="doc-1",
            chunk_type="section",
            ref_id="sec-1",
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_by_vector([0.1] * EMBEDDING_DIM, top_k=5, chunk_type="section")

    assert [h.chunk_id for h in hits] == ["chunk-section"]


def test_update_endpoint_chunk_updates_text_and_embedding(db_session) -> None:
    """docs/architect-review/56 §4.4: 청크 1건만 갱신(text_tsv 는 generated 라 자동)."""
    db_session.add(
        Document(
            id="doc-cu",
            project="default",
            source_url=None,
            title="t",
            version="1",
            doc_type="openapi",
            content_hash="h",
            raw_text="{}",
        )
    )
    db_session.flush()
    db_session.add(
        Chunk(
            id="doc-cu:chunk:0",
            document_id="doc-cu",
            chunk_type="endpoint",
            ref_id="ep-1",
            text="old text",
            embedding=[0.0] * EMBEDDING_DIM,
        )
    )
    db_session.flush()

    repo = ChunkRepository(db_session)
    updated = repo.update_endpoint_chunk(
        document_id="doc-cu",
        ref_id="ep-1",
        text="new text",
        embedding=[1.0] * EMBEDDING_DIM,
    )
    assert updated is True
    assert db_session.get(Chunk, "doc-cu:chunk:0").text == "new text"

    missing = repo.update_endpoint_chunk(
        document_id="doc-cu",
        ref_id="ep-없음",
        text="x",
        embedding=[0.0] * EMBEDDING_DIM,
    )
    assert missing is False


# --- 개선 #2: meta_filter (EXISTS 서브쿼리, keyword/vector arm 공유) ------------


def _seed_document_meta(
    session, document_id: str, mime_type: str | None = None, modified_at: datetime | None = None
) -> None:
    """`document_meta` 한 건을 `document_id` 로 연결해 저장한다."""
    session.add(
        DocumentMeta(
            project="default",
            source=SOURCE_DRIVE,
            external_id=f"ext-{document_id}",
            title="문서",
            url=f"https://example.test/{document_id}",
            modified_at=modified_at,
            last_synced_at=modified_at or datetime(2026, 7, 1, 9, 0, 0),
            document_id=document_id,
            mime_type=mime_type,
        )
    )


def test_search_endpoint_by_text_meta_filter_none_is_noop(db_session) -> None:
    """meta_filter 생략 시 기존 동작과 동일하다(회귀 안전)."""
    _seed_chunk(db_session, "chunk-1", "doc-1", "find pet by id")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(["pet"], top_k=10)

    assert [h.chunk_id for h in hits] == ["chunk-1"]


def test_search_endpoint_by_text_meta_filter_excludes_mismatched_mime_type(db_session) -> None:
    """meta_filter.mime_types 에 안 걸리는 document_id 의 청크는 후보에서 빠진다."""
    _seed_chunk(db_session, "chunk-1", "doc-1", "find pet by id")
    _seed_document_meta(db_session, "doc-1", mime_type="text/plain")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(
        ["pet"], top_k=10, meta_filter=DocumentMetaFilter(mime_types=("application/pdf",))
    )

    assert hits == []


def test_search_endpoint_by_text_meta_filter_matches_mime_type(db_session) -> None:
    """meta_filter.mime_types 가 일치하면 후보에 포함된다."""
    _seed_chunk(db_session, "chunk-1", "doc-1", "find pet by id")
    _seed_document_meta(db_session, "doc-1", mime_type="application/pdf")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(
        ["pet"], top_k=10, meta_filter=DocumentMetaFilter(mime_types=("application/pdf",))
    )

    assert [h.chunk_id for h in hits] == ["chunk-1"]


def test_search_endpoint_by_text_meta_filter_excludes_missing_document_meta(db_session) -> None:
    """대응하는 document_meta 행이 아예 없으면(NULL) 필터 활성 시 제외된다."""
    _seed_chunk(db_session, "chunk-1", "doc-1", "find pet by id")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_endpoint_by_text(
        ["pet"], top_k=10, meta_filter=DocumentMetaFilter(mime_types=("application/pdf",))
    )

    assert hits == []


def test_search_by_vector_meta_filter_none_is_noop(db_session) -> None:
    """meta_filter 생략 시 기존 동작과 동일하다(회귀 안전)."""
    document = Document(
        id="doc-1", project="default", source_url=None, title="t", content_hash="h", raw_text="{}"
    )
    db_session.add(document)
    db_session.add(
        Chunk(
            id="chunk-1",
            document_id="doc-1",
            chunk_type="endpoint",
            ref_id="ep-1",
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_by_vector([0.1] * EMBEDDING_DIM, top_k=5)

    assert [h.chunk_id for h in hits] == ["chunk-1"]


def test_search_by_vector_meta_filter_excludes_mismatched_mime_type(db_session) -> None:
    """meta_filter.mime_types 에 안 걸리는 document_id 의 청크는 후보에서 빠진다."""
    document = Document(
        id="doc-1", project="default", source_url=None, title="t", content_hash="h", raw_text="{}"
    )
    db_session.add(document)
    db_session.add(
        Chunk(
            id="chunk-1",
            document_id="doc-1",
            chunk_type="endpoint",
            ref_id="ep-1",
            text="hello world",
            embedding=[0.1] * EMBEDDING_DIM,
        )
    )
    _seed_document_meta(db_session, "doc-1", mime_type="text/plain")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = repo.search_by_vector(
        [0.1] * EMBEDDING_DIM,
        top_k=5,
        meta_filter=DocumentMetaFilter(mime_types=("application/pdf",)),
    )

    assert hits == []


def test_search_by_vector_meta_filter_raises_ef_search_floor(db_session) -> None:
    """meta_filter 활성 시 hnsw.ef_search 하한이 200 으로 올라간다(P7)."""
    repo = ChunkRepository(db_session)

    repo.search_by_vector(
        [0.1] * EMBEDDING_DIM,
        top_k=5,
        meta_filter=DocumentMetaFilter(mime_types=("application/pdf",)),
    )

    value = db_session.execute(text("SHOW hnsw.ef_search")).scalar()
    assert int(value) == 200
