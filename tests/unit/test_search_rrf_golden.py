"""RRF 도입 순위 골든 회귀 테스트(`docs/07-search-rrf-reevaluation.md` 5절, 5.6).

`fallback`/`rrf` 두 전략 각각의 결정적 기대 순위를 스냅샷으로 고정해,
회귀 발생 시 즉시 감지하고(골든 불일치) `DOCS_MCP_SEARCH_STRATEGY=fallback`
한 줄로 되돌릴 수 있음을(옛 배타 분기가 그대로 남아 있음을) 함께 검증한다.
"""

from __future__ import annotations

from app.composition import build_services
from app.services.search.endpoint_candidate_search import CandidateSearchOptions


def _register(app_state, raw: str) -> str:
    """샘플 문서를 등록하고 document_id 를 반환한다."""
    bundle = next(build_services(app_state))
    result = bundle.sync_service.register(
        project="default", source_url=None, raw_document=raw
    )
    return result.document.id


def _bundle(app_state):
    """새 서비스 번들을 만든다."""
    return next(build_services(app_state))


def _shape(candidates) -> list[tuple[str, str, str]]:
    """비교하기 쉬운 (method, path, match_type) 튜플 리스트로 변환한다."""
    return [(c.method, c.path, c.match_type) for c in candidates]


def test_fallback_strategy_golden_ranking(app_state, sample_openapi_3: str) -> None:
    """fallback 전략: 옛 배타 분기 순위가 그대로 보존된다(롤백 스위치 검증)."""
    _register(app_state, sample_openapi_3)
    app_state.search_strategy = "fallback"
    search = _bundle(app_state).candidate_search

    assert _shape(search.search("pet", CandidateSearchOptions(top_k=10))) == [
        ("POST", "/pet", "keyword"),
        ("GET", "/pet/{petId}", "keyword"),
        ("DELETE", "/pet/{petId}", "keyword"),
    ]
    assert _shape(search.search("find pet by id", CandidateSearchOptions(top_k=10))) == [
        ("GET", "/pet/{petId}", "keyword"),
        ("POST", "/pet", "keyword"),
        ("DELETE", "/pet/{petId}", "keyword"),
    ]
    assert _shape(search.search("create user", CandidateSearchOptions(top_k=10))) == [
        ("POST", "/user", "keyword"),
        ("POST", "/pet", "keyword"),
    ]


def test_rrf_strategy_golden_ranking(app_state, sample_openapi_3: str) -> None:
    """rrf 전략(기본): 키워드+벡터 융합 순위가 결정적으로 고정된다.

    `HashEmbeddingProvider`(테스트용, `is_semantic=False`)도 토큰이 겹치면
    양의 코사인 유사도를 내므로 벡터 arm 이 키워드 arm 과 같은 후보를 잡아
    match_type 이 전부 "both" 가 된다 — 이 스냅샷 자체가 회귀 감지 대상이다.

    질의 "pet" 은 addPet/getPetById 가 키워드·벡터 두 arm 모두에서 정확히
    동점(RRF 점수까지 동일)이라 최종 순위가 `ref_id`(등록마다 무작위 문서ID를
    해시 입력에 섞는 `_make_endpoint_id`) 오름차순으로 갈리는데, 이 값은
    실행마다 달라 1·2위 순서가 뒤바뀔 수 있다(엔진 tie-break 자체는
    `docs/07-search-rrf-reevaluation.md` 5.4 명세대로 결정적이지만, 입력값이
    실행마다 다르다). 그래서 이 질의는 "동점 집합"만 검증하고, 동점이 없는
    다른 두 질의는 정확한 순서를 그대로 고정한다.
    """
    _register(app_state, sample_openapi_3)
    search = _bundle(app_state).candidate_search  # 기본 전략 = rrf

    pet_query = _shape(search.search("pet", CandidateSearchOptions(top_k=10)))
    assert set(pet_query[:2]) == {("GET", "/pet/{petId}", "both"), ("POST", "/pet", "both")}
    assert pet_query[2] == ("DELETE", "/pet/{petId}", "both")

    assert _shape(search.search("find pet by id", CandidateSearchOptions(top_k=10))) == [
        ("GET", "/pet/{petId}", "both"),
        ("POST", "/pet", "both"),
        ("DELETE", "/pet/{petId}", "both"),
    ]
    assert _shape(search.search("create user", CandidateSearchOptions(top_k=10))) == [
        ("POST", "/user", "both"),
        ("POST", "/pet", "both"),
    ]
