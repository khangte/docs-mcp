"""P1(키워드 검색 Postgres FTS 이관) 회귀 검증.

ts_rank 점수값은 옛 파이썬 겹침비율 스코어러와 다를 수밖에 없으므로, "점수
동일"이 아니라 설계문서(`docs/architect-review/04_search_p1_keyword_fts_design.md`) 5번의
불변식 + 특성(characterization) 비교로 검증한다.

- ASCII recall 동치(회귀 게이트): 레거시 스코어러 후보 집합 == FTS 후보 집합.
- 불변식: 단조성(겹침 많을수록 순위 낮지 않음), top_k 상한, 결정성.
- 순위 상관 진단: 실패 게이트가 아닌 로그 전용 리포트.
- 한글 동작 고정(A안 확정 계약): 한글 질의가 키워드에서 잡히고 벡터
  fallback 을 타지 않는다. 공백 변형 교차매칭 불가는 알려진 한계로 고정.
"""

from __future__ import annotations

import json
import logging
import re

import pytest

from app.composition import build_services
from app.models import Chunk
from app.repositories.chunk_repository import ChunkRepository
from app.services.search.endpoint_candidate_search import CandidateSearchOptions
from app.services.search.keyword_search import KeywordSearch

_LOG = logging.getLogger("docs_mcp.tests.search_fts_regression")

# --- 레거시(P1 이전) 파이썬 키워드 스코어러 — 회귀 비교 전용 참조 구현 -------------
# app/services/search/keyword_search.py 의 옛 `tokenize`/`_score_chunk` 를
# 그대로 옮긴 것이다. 프로덕션 코드에는 남기지 않고 이 테스트 파일에만 둔다.

_LEGACY_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _legacy_tokenize(text: str) -> list[str]:
    return [t.lower() for t in _LEGACY_TOKEN_RE.findall(text or "")]


def _legacy_score(chunk_text: str, query_tokens: set[str]) -> float:
    c_tokens = set(_legacy_tokenize(chunk_text))
    overlap = query_tokens & c_tokens
    if not overlap:
        return 0.0
    return len(overlap) / max(1, len(query_tokens))


def _legacy_candidate_scores(chunks: list[Chunk], query: str) -> dict[str, float]:
    """레거시 스코어러로 매칭되는 (ref_id -> score) 매핑(score > 0 만)."""
    query_tokens = set(_legacy_tokenize(query))
    if not query_tokens:
        return {}
    scored: dict[str, float] = {}
    for chunk in chunks:
        score = _legacy_score(chunk.text, query_tokens)
        if score > 0.0:
            scored[chunk.ref_id] = score
    return scored


def _register(app_state, raw: str) -> str:
    """샘플 문서를 등록하고 document_id 를 반환한다."""
    bundle = next(build_services(app_state))
    result = bundle.sync_service.register(project="default", source_url=None, raw_document=raw)
    return result.document.id


def _bundle(app_state):
    """새 서비스 번들을 만든다."""
    return next(build_services(app_state))


_ASCII_QUERIES = [
    "find pet by id",
    "pet",
    "create user",
    "user account",
    "delete pet",
    "add new pet",
    "GET /pet/{petId}",
]


# --- 1. ASCII recall 동치(회귀 게이트) ------------------------------------------


def test_ascii_recall_matches_legacy_candidate_set(app_state, sample_openapi_3: str) -> None:
    """ASCII 질의 집합에 대해 레거시 스코어러 후보 집합과 FTS 후보 집합이 동일하다."""
    _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    chunks = [c for c in bundle.chunk_repo.list_all() if c.chunk_type == "endpoint"]
    keyword_search = KeywordSearch(bundle.chunk_repo)

    for query in _ASCII_QUERIES:
        legacy = set(_legacy_candidate_scores(chunks, query).keys())
        fts_hits = keyword_search.search(query, top_k=100)
        fts = {h.ref_id for h in fts_hits}
        assert fts == legacy, f"recall 불일치: query={query!r} legacy={legacy} fts={fts}"


_MIXED_SCRIPT_OPENAPI = json.dumps(
    {
        "openapi": "3.0.3",
        "info": {"title": "혼합 API", "version": "1.0.0"},
        "paths": {
            "/request": {
                "get": {
                    "operationId": "getRequestStatus",
                    "summary": "GET요청 상태 확인",
                    "description": "GET요청의 처리 상태를 확인한다",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
)


def test_ascii_recall_gate_catches_mixed_script_compound_regression(app_state) -> None:
    """ASCII+한글이 공백 없이 붙은 복합어('GET요청')에서도 순수 ASCII 질의가
    bare lexeme 으로 매칭돼야 한다(architect 판단: 스크립트 경계 공백삽입).

    스크립트 경계 공백삽입 없이 구두점만 공백화하면 'GET요청' 전체가 단일
    lexeme 'get요청'으로 뭉쳐, 옛 스코어러가 잡던 순수 ASCII 'GET' recall 이
    깨진다. 레거시 후보 집합과 비교해 이 회귀를 게이트로 고정한다.
    """
    _register(app_state, _MIXED_SCRIPT_OPENAPI)
    bundle = _bundle(app_state)
    chunks = [c for c in bundle.chunk_repo.list_all() if c.chunk_type == "endpoint"]
    keyword_search = KeywordSearch(bundle.chunk_repo)

    legacy_get = set(_legacy_candidate_scores(chunks, "GET").keys())
    fts_get = {h.ref_id for h in keyword_search.search("GET", top_k=100)}
    assert legacy_get, "레거시도 매칭해야 정상 fixture(자기검증)"
    assert fts_get == legacy_get, f"recall 불일치: legacy={legacy_get} fts={fts_get}"

    fts_korean = {h.ref_id for h in keyword_search.search("요청", top_k=100)}
    assert fts_korean, "한글 절반도 bare lexeme 으로 매칭돼야 한다(A안)"


# --- 2. 불변식 ------------------------------------------------------------------


def test_more_distinct_overlap_ranks_not_lower(db_session) -> None:
    """더 많은 distinct 질의 토큰을 포함한 청크가 더 적게 포함한 청크보다
    순위가 낮지 않다(단조성)."""
    from app.models import Document

    db_session.add(
        Document(
            id="doc1", project="default", title="t", version="v", content_hash="h", raw_text="{}"
        )
    )
    db_session.flush()
    db_session.add(
        Chunk(
            id="more",
            document_id="doc1",
            chunk_type="endpoint",
            ref_id="ep-more",
            text="find pet by id status",
        )
    )
    db_session.add(
        Chunk(
            id="less",
            document_id="doc1",
            chunk_type="endpoint",
            ref_id="ep-less",
            text="find something else entirely",
        )
    )
    db_session.commit()
    search = KeywordSearch(ChunkRepository(db_session))

    hits = search.search("find pet by id", top_k=10)

    scores = {h.chunk_id: h.score for h in hits}
    assert scores["more"] >= scores["less"]


def test_symbol_only_query_returns_empty(db_session) -> None:
    """검색 가능한 term 이 없는 질의(기호만)는 빈 리스트다."""
    from app.models import Document

    db_session.add(
        Document(
            id="doc1", project="default", title="t", version="v", content_hash="h", raw_text="{}"
        )
    )
    db_session.flush()
    db_session.add(
        Chunk(id="c1", document_id="doc1", chunk_type="endpoint", ref_id="ep1", text="find pet")
    )
    db_session.commit()
    search = KeywordSearch(ChunkRepository(db_session))

    assert search.search("!!! ???", top_k=10) == []


def test_repeated_search_is_deterministic(app_state, sample_openapi_3: str) -> None:
    """동일 질의를 반복 호출하면 동일 순서·동일 결과를 반환한다(tie-break 결정성)."""
    _register(app_state, sample_openapi_3)
    keyword_search = KeywordSearch(_bundle(app_state).chunk_repo)

    first = keyword_search.search("pet", top_k=10)
    second = keyword_search.search("pet", top_k=10)

    assert [(h.chunk_id, h.score) for h in first] == [(h.chunk_id, h.score) for h in second]


# --- 3. 순위 상관 진단(비-실패 리포트) --------------------------------------------


def _spearman(a: list[float], b: list[float]) -> float:
    """단순 스피어먼 순위상관(외부 의존성 없이 순수 파이썬으로 계산, 동순위는 평균 순위)."""

    def _rank(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    ra, rb = _rank(a), _rank(b)
    n = len(a)
    mean_a, mean_b = sum(ra) / n, sum(rb) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    var_a = sum((x - mean_a) ** 2 for x in ra)
    var_b = sum((y - mean_b) ** 2 for y in rb)
    if var_a == 0 or var_b == 0:
        return 1.0
    return cov / (var_a * var_b) ** 0.5


@pytest.mark.parametrize("query", _ASCII_QUERIES)
def test_legacy_vs_fts_rank_correlation_diagnostic(
    app_state, sample_openapi_3: str, query: str
) -> None:
    """레거시 vs FTS 순위 상관을 로그로만 남기는 진단 테스트(실패 게이트 아님).

    ts_rank 값은 겹침비율과 다를 수밖에 없다(설계 5번). 급락하는 질의를
    사람이 검토할 수 있도록 상관계수를 로그로 남긴다 — 낮은 상관 자체는 이
    테스트를 실패시키지 않는다(계산이 예외 없이 끝나는 것만 검증).
    """
    _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    chunks = [c for c in bundle.chunk_repo.list_all() if c.chunk_type == "endpoint"]
    keyword_search = KeywordSearch(bundle.chunk_repo)

    legacy = _legacy_candidate_scores(chunks, query)
    fts = {h.ref_id: h.score for h in keyword_search.search(query, top_k=100)}
    common = sorted(set(legacy) & set(fts))

    if len(common) < 2:
        _LOG.info("Spearman 진단 생략(공통 후보 %d건): query=%r", len(common), query)
        return

    correlation = _spearman([legacy[r] for r in common], [fts[r] for r in common])
    _LOG.info("Spearman(legacy, fts)=%.3f query=%r common=%d건", correlation, query, len(common))
    assert correlation is not None  # 계산이 예외 없이 끝났는지만 확인(비-실패 게이트)


# --- 4. 한글 동작 고정(A안 확정 — 필수) -------------------------------------------

_KOREAN_OPENAPI = json.dumps(
    {
        "openapi": "3.0.3",
        "info": {"title": "한글 API", "version": "1.0.0"},
        "paths": {
            "/order": {
                "get": {
                    "operationId": "listOrders",
                    "summary": "주문 목록 조회",
                    "description": "사용자의 주문 목록을 조회한다",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
)


def test_korean_query_matches_keyword_not_vector_fallback(
    app_state, counting_embedding_provider
) -> None:
    """fallback 전략: 한글 summary 를 가진 endpoint 를 한글 질의로 검색하면
    키워드에서 잡히고 match_type='keyword' 다(A안 확정 계약 — 벡터 fallback
    을 타지 않는다).

    FTS 전환 전에는 한글이 토큰화에서 완전히 버려져 항상 벡터 fallback 을
    탔다(design.md 확인된 사실 1번). 이 테스트는 그 전략 변화를 명시적
    계약으로 고정한다. RRF 도입(`docs/architect-review/07_search_rrf_reevaluation.md` 5.6) 이후
    이 배타적 계약은 기본(rrf)이 아니라 `search_strategy="fallback"` 에
    한해서만 유효하므로 전략을 명시적으로 고정한다.
    """
    _register(app_state, _KOREAN_OPENAPI)
    app_state.search_strategy = "fallback"
    counting_embedding_provider.reset_counts()

    candidates = _bundle(app_state).candidate_search.search(
        "주문 목록", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert all(c.match_type == "keyword" for c in candidates)
    assert counting_embedding_provider.embed_call_count == 0


def test_korean_whitespace_variant_does_not_cross_match_known_limitation(app_state) -> None:
    """공백 변형(질의 '주문목록' vs 청크 텍스트의 '주문 목록')은 FTS 로 교차
    매칭되지 않는다 — design.md 2번에 명시된 P1 범위 밖 알려진 한계.

    이 테스트가 실패한다면(=매칭되기 시작하면) 한계가 해소된 것이니 후속
    과제 트래킹에서 제거해도 된다는 신호다.
    """
    _register(app_state, _KOREAN_OPENAPI)  # summary: "주문 목록 조회"(공백 있음)

    candidates = _bundle(app_state).candidate_search.search(
        "주문목록", CandidateSearchOptions(top_k=5)  # 공백 없는 질의
    )

    assert all(c.match_type != "keyword" for c in candidates)
