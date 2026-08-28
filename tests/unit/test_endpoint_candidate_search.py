"""엔드포인트 후보 검색(키워드 우선 + 벡터 보조) 테스트.

SPEC 기능 1 의 검증 기준을 그대로 옮긴다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.composition import build_services
from app.core.errors import DocumentNotFoundError, ValidationError
from app.services.search.endpoint_candidate_search import (
    CandidateSearchOptions,
    EndpointCandidateSearch,
)
from app.services.search.keyword_search import KeywordHit, KeywordSearch
from app.services.search.vector_search import VectorSearch, VectorSearchHit
from tests.fixtures.fakes import ExplodingEmbeddingProvider, StubVectorSearch

NO_MATCH_QUERY = "zzzzz_nothing_matches_here_xxx"


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


# --- 정상 케이스: 후보만 반환 ------------------------------------------------


def test_returns_candidates_without_detail_fields(app_state, sample_openapi_3: str) -> None:
    """후보 항목에 파라미터·응답·snippet 같은 상세 필드가 없어야 한다."""
    _register(app_state, sample_openapi_3)
    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    candidate = candidates[0]
    assert set(vars(candidate)) == {
        "endpoint_id",
        "method",
        "path",
        "summary",
        "match_type",
    }
    for absent in ("parameters", "responses", "request_body", "snippet", "score"):
        assert not hasattr(candidate, absent)


def test_keyword_match_finds_expected_endpoint(app_state, sample_openapi_3: str) -> None:
    """키워드로 명확한 질의는 해당 엔드포인트를 최상위로 찾는다.

    기본 전략은 rrf라 벡터 arm도 함께 돌아간다 — 질의·청크 텍스트의 토큰이
    겹치면 해시 임베딩도 양의 유사도를 낼 수 있어(fixtures/fakes.py 미사용,
    실제 HashEmbeddingProvider) match_type이 "keyword" 대신 "both"가 될 수
    있다. 이 테스트의 관심사는 "정답 엔드포인트가 최상위인가"이지 어느
    arm이 기여했는지가 아니므로 match_type은 둘 중 하나만 확인한다.
    """
    _register(app_state, sample_openapi_3)
    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert candidates[0].method == "GET"
    assert candidates[0].path == "/pet/{petId}"
    assert candidates[0].match_type in ("keyword", "both")


def test_respects_top_k(app_state, sample_openapi_3: str) -> None:
    """top_k 를 초과하는 후보를 반환하지 않는다."""
    _register(app_state, sample_openapi_3)
    candidates = _bundle(app_state).candidate_search.search(
        "pet", CandidateSearchOptions(top_k=1)
    )

    assert len(candidates) == 1


def test_document_id_filter_limits_scope(app_state, sample_openapi_3: str) -> None:
    """document_id 를 지정하면 다른 문서의 엔드포인트는 후보에서 빠진다."""
    petstore_id = _register(app_state, sample_openapi_3)
    other_id = _register(
        app_state,
        '{"openapi":"3.0.3","info":{"title":"Other","version":"1"},'
        '"paths":{"/pet/other":{"get":{"operationId":"otherPet","summary":"pet other",'
        '"responses":{"200":{"description":"ok"}}}}}}',
    )
    assert petstore_id != other_id

    candidates = _bundle(app_state).candidate_search.search(
        "pet", CandidateSearchOptions(top_k=10, document_id=other_id)
    )

    assert candidates
    assert all(c.path == "/pet/other" for c in candidates)


def test_section_chunks_are_not_returned_as_endpoints(app_state) -> None:
    """마크다운 섹션 청크는 엔드포인트 후보로 섞여 들어오지 않는다."""
    raw = "# 팀 온보딩\n환영합니다\n## 개발 환경 설정\nuv sync 로 의존성을 설치하세요\n"
    _register(app_state, raw)

    candidates = _bundle(app_state).candidate_search.search(
        "개발 환경 설정 uv sync", CandidateSearchOptions(top_k=5)
    )

    assert candidates == []


# --- fallback 전략(롤백 스위치): 키워드가 맞으면 임베딩 API 를 호출하지 않는다 ---
#
# 기본 전략은 rrf(키워드+벡터 항상 병렬 실행)라 이 배타적 불변식은 더는
# 기본값이 아니다. `docs/architect-review/07_search_rrf_reevaluation.md` 5.6 에 따라 SPEC Phase 0
# 결정 6("키워드 0건일 때만 벡터")은 이제 `search_strategy="fallback"` 에
# 한해서만 유효한 계약이므로, 이 절의 테스트는 전략을 명시적으로 고정한다.


def test_keyword_hit_does_not_call_embedding_provider(
    app_state, counting_embedding_provider, sample_openapi_3: str
) -> None:
    """fallback 전략: 키워드 결과가 1건 이상이면 임베딩 호출 카운트가 0 이다."""
    _register(app_state, sample_openapi_3)
    app_state.search_strategy = "fallback"
    counting_embedding_provider.reset_counts()

    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert counting_embedding_provider.embed_call_count == 0


def test_exact_path_query_does_not_call_embedding_provider(
    app_state, counting_embedding_provider, sample_openapi_3: str
) -> None:
    """fallback 전략: "GET /pet/{petId}" 처럼 명확한 질의도 임베딩 호출이 0 이다.

    exact match 단계(5b)가 1위를 확정적으로 채우고, 나머지는 fallback
    전략(키워드 우선)이 채운다 — 어느 쪽도 임베딩을 호출하지 않는다.
    """
    _register(app_state, sample_openapi_3)
    app_state.search_strategy = "fallback"
    counting_embedding_provider.reset_counts()

    candidates = _bundle(app_state).candidate_search.search(
        "GET /pet/{petId}", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert candidates[0].match_type == "exact"
    assert all(c.match_type in ("exact", "keyword") for c in candidates)
    assert counting_embedding_provider.embed_call_count == 0


def test_keyword_hit_path_never_touches_embedding(app_state, sample_openapi_3: str) -> None:
    """fallback 전략: 임베딩 호출 시 즉시 실패하는 프로바이더로도 키워드 경로는 성공한다."""
    _register(app_state, sample_openapi_3)
    app_state.search_strategy = "fallback"
    app_state.embedding_provider = ExplodingEmbeddingProvider(dim=384)

    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert all(c.match_type == "keyword" for c in candidates)


def test_rrf_strategy_calls_embedding_even_on_keyword_hit(
    app_state, counting_embedding_provider, sample_openapi_3: str
) -> None:
    """rrf 전략(기본)은 키워드가 맞아도 벡터 arm 을 위해 임베딩을 호출한다.

    fallback 전략과의 결정적 차이 — "항상 두 arm 실행"이 rrf 도입의 핵심이다.
    """
    _register(app_state, sample_openapi_3)
    counting_embedding_provider.reset_counts()

    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert counting_embedding_provider.embed_call_count == 1


# --- 벡터 보조: 키워드 0건일 때만 --------------------------------------------


def test_vector_fallback_triggers_only_when_keyword_returns_zero(
    app_state, counting_embedding_provider, sample_openapi_3: str
) -> None:
    """키워드 0건이면 벡터 보조가 시도되어 임베딩이 정확히 1회 호출된다."""
    _register(app_state, sample_openapi_3)
    counting_embedding_provider.reset_counts()

    _bundle(app_state).candidate_search.search(
        NO_MATCH_QUERY, CandidateSearchOptions(top_k=5)
    )

    assert counting_embedding_provider.embed_call_count == 1


def _search_with_stub_vector(
    app_state, stub_score: float, top_k: int, stub_chunk_limit: int | None = None
) -> tuple[list, "StubVectorSearch"]:
    """스텁 벡터 검색기를 주입한 검색기로 키워드 0건 질의를 수행한다.

    `HashEmbeddingProvider` 는 서로 다른 텍스트의 유사도가 정확히 0.0 이라
    실제 임베딩으로는 벡터 분기가 후보를 만들지 못한다. 양수 점수를 내는
    스텁을 주입해야만 분기가 실증된다.
    """
    bundle = _bundle(app_state)
    endpoint_chunks = [
        (c.id, c.ref_id) for c in bundle.chunk_repo.list_all() if c.chunk_type == "endpoint"
    ]
    assert endpoint_chunks, "엔드포인트 청크가 있어야 스텁 검증이 의미 있다"
    stub = StubVectorSearch(endpoint_chunks[:stub_chunk_limit], score=stub_score)
    search = EndpointCandidateSearch(
        chunk_repo=bundle.chunk_repo,
        endpoint_repo=bundle.endpoint_repo,
        keyword_search=KeywordSearch(bundle.chunk_repo),
        vector_search=stub,
        document_repo=bundle.document_repo,
    )
    candidates = search.search(NO_MATCH_QUERY, CandidateSearchOptions(top_k=top_k))
    return candidates, stub


def test_vector_fallback_actually_produces_candidates(
    app_state, sample_openapi_3: str
) -> None:
    """벡터 보조가 실제로 후보를 만들고 전부 match_type="vector" 로 표시된다."""
    _register(app_state, sample_openapi_3)

    candidates, stub = _search_with_stub_vector(app_state, stub_score=0.9, top_k=5)

    # 공허 참 방지: 비어 있지 않음을 먼저 단언한 뒤 match_type 을 검증한다.
    assert candidates
    assert stub.call_count == 1
    assert all(c.match_type == "vector" for c in candidates)
    assert all(c.endpoint_id for c in candidates)


def test_vector_fallback_respects_top_k(app_state, sample_openapi_3: str) -> None:
    """스텁이 더 많이 내놓아도 top_k 만큼만 잘라 반환한다."""
    _register(app_state, sample_openapi_3)

    candidates, _ = _search_with_stub_vector(app_state, stub_score=0.9, top_k=2)

    assert len(candidates) == 2
    assert all(c.match_type == "vector" for c in candidates)


def test_zero_score_vector_hits_are_discarded(app_state, sample_openapi_3: str) -> None:
    """점수가 0.0 인 벡터 후보는 의미 없는 매칭으로 보고 폐기한다(의도된 사양)."""
    _register(app_state, sample_openapi_3)

    candidates, stub = _search_with_stub_vector(app_state, stub_score=0.0, top_k=5)

    assert stub.call_count == 1
    assert candidates == []


def test_vector_fallback_skipped_when_disabled(app_state, sample_openapi_3: str) -> None:
    """벡터 보조가 비활성이면 임베딩 호출 없이 빈 결과를 반환한다(해시 폴백 환경)."""
    _register(app_state, sample_openapi_3)
    app_state.vector_fallback_enabled = False
    app_state.embedding_provider = ExplodingEmbeddingProvider(dim=384)

    candidates = _bundle(app_state).candidate_search.search(
        NO_MATCH_QUERY, CandidateSearchOptions(top_k=5)
    )

    assert candidates == []


def test_vector_fallback_disabled_still_returns_keyword_results(
    app_state, sample_openapi_3: str
) -> None:
    """provider 게이팅(rrf 전략 기본값): 벡터가 비활성이면 키워드 단독으로 degrade한다.

    `vector_fallback_enabled=False`(해시 임베딩 폴백 등)면 rrf 전략이어도
    벡터 arm 을 실행하지 않고 전부 match_type="keyword" 로 나온다
    (`docs/architect-review/07_search_rrf_reevaluation.md` 5.5 불변식).
    """
    _register(app_state, sample_openapi_3)
    app_state.vector_fallback_enabled = False
    app_state.embedding_provider = ExplodingEmbeddingProvider(dim=384)

    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert all(c.match_type == "keyword" for c in candidates)


# --- Q2: 전역 스코프면 벡터 arm 에 candidate_ids=None 을 전달 ------------------


def test_global_scope_passes_no_candidate_ids_to_vector_search(
    app_state, sample_openapi_3: str
) -> None:
    """document_id/project 모두 없으면(전역) 벡터 arm 에 candidates=None 을 넘긴다.

    이전에는 전역 스코프에서도 `list_endpoint_chunk_ids()` 로 endpoint 청크
    ID 전체를 앱 메모리에 적재해 `candidate_ids` 로 넘겼다 — chunk_type
    필터가 SQL 로 내려간 지금은(Q2) 그 전량 로드가 불필요한 낭비다.
    """
    _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    stub = StubVectorSearch([], score=0.9)
    search = EndpointCandidateSearch(
        chunk_repo=bundle.chunk_repo,
        endpoint_repo=bundle.endpoint_repo,
        keyword_search=KeywordSearch(bundle.chunk_repo),
        vector_search=stub,
        document_repo=bundle.document_repo,
    )

    search.search("find pet by id", CandidateSearchOptions(top_k=5))

    assert stub.call_count == 1
    assert stub.last_candidates is None


def test_narrow_scope_still_passes_candidate_ids_to_vector_search(
    app_state, sample_openapi_3: str
) -> None:
    """document_id 로 스코프를 좁히면 여전히 candidate_ids(IN 목록)를 넘긴다."""
    document_id = _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    stub = StubVectorSearch([], score=0.9)
    search = EndpointCandidateSearch(
        chunk_repo=bundle.chunk_repo,
        endpoint_repo=bundle.endpoint_repo,
        keyword_search=KeywordSearch(bundle.chunk_repo),
        vector_search=stub,
        document_repo=bundle.document_repo,
    )

    search.search(
        "find pet by id", CandidateSearchOptions(top_k=5, document_id=document_id)
    )

    assert stub.call_count == 1
    assert stub.last_candidates is not None


# --- query_variants: 키워드 arm 후보 필터만 확장(docs/12 후보4) ----------------


def test_query_variants_widen_keyword_arm_candidate_pool(app_state) -> None:
    """query_variants 를 넘기면 원본 질의만으로는 못 찾는 엔드포인트도 후보에 든다.

    벡터 arm 은 해시 임베딩(비의미론적)으로 비활성화해, 이 테스트가 오직
    키워드 arm 배선만 검증하게 한다.
    """
    raw = (
        '{"openapi":"3.0.3","info":{"title":"Zoo","version":"1"},'
        '"paths":{"/animals":{"get":{"operationId":"listAnimals",'
        '"summary":"동물 조회 엔드포인트","responses":{"200":{"description":"ok"}}}}}}'
    )
    _register(app_state, raw)
    app_state.vector_fallback_enabled = False

    without_variants = _bundle(app_state).candidate_search.search(
        "find pet", CandidateSearchOptions(top_k=5)
    )
    assert without_variants == []

    with_variants = _bundle(app_state).candidate_search.search(
        "find pet", CandidateSearchOptions(top_k=5, query_variants=["동물 조회"])
    )

    assert [c.path for c in with_variants] == ["/animals"]
    assert all(c.match_type == "keyword" for c in with_variants)


def test_query_variants_widen_vector_arm_too(app_state, sample_openapi_3: str) -> None:
    """query_variants 는 벡터 arm 에도 라우팅된다(doc/30 §7.2, doc/12 후보4 뒤집음).

    원본 질의로는 안 잡히는 엔드포인트가 영문 변형으로는 벡터 arm에서
    잡혀야 한다 — 원본과 변형을 각각 임베딩해 히트를 병합하는지 검증.
    """
    _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    endpoint_chunks = [(c.id, c.ref_id) for c in bundle.chunk_repo.list_all() if c.chunk_type == "endpoint"]
    assert endpoint_chunks

    original_query = "고객 새로 등록"
    variant_query = "create customer"
    target_chunk = endpoint_chunks[0]

    queries_seen: list[str] = []

    def stub_search(
        query: str, top_k: int, candidates: set[str] | None = None
    ) -> list[VectorSearchHit]:
        queries_seen.append(query)
        chunks = [target_chunk] if query == variant_query else []
        return [
            VectorSearchHit(chunk_id=chunk_id, ref_id=ref_id, score=0.9)
            for chunk_id, ref_id in chunks[:top_k]
        ]

    stub = SimpleNamespace(search=stub_search)
    search = EndpointCandidateSearch(
        chunk_repo=bundle.chunk_repo,
        endpoint_repo=bundle.endpoint_repo,
        keyword_search=KeywordSearch(bundle.chunk_repo),
        vector_search=stub,
        document_repo=bundle.document_repo,
    )

    candidates = search.search(
        original_query,
        CandidateSearchOptions(top_k=5, query_variants=[variant_query]),
    )

    assert queries_seen == [original_query, variant_query]
    assert any(c.endpoint_id == target_chunk[1] for c in candidates)


# --- RRF 융합: match_type="both" --------------------------------------------


def test_rrf_both_match_type_when_keyword_and_vector_agree(
    app_state, sample_openapi_3: str
) -> None:
    """키워드·벡터 두 arm 모두에서 같은 엔드포인트가 나오면 match_type="both" 다."""
    _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    endpoint_chunks = [(c.id, c.ref_id) for c in bundle.chunk_repo.list_all() if c.chunk_type == "endpoint"]
    keyword_search = KeywordSearch(bundle.chunk_repo)
    keyword_hits = keyword_search.search("find pet by id", top_k=50)
    assert keyword_hits, "키워드 arm 이 최소 1건은 잡아야 시나리오가 의미 있다"
    top_ref_id = keyword_hits[0].ref_id
    top_chunk_id = next(cid for cid, ref_id in endpoint_chunks if ref_id == top_ref_id)

    stub = StubVectorSearch([(top_chunk_id, top_ref_id)], score=0.9)
    search = EndpointCandidateSearch(
        chunk_repo=bundle.chunk_repo,
        endpoint_repo=bundle.endpoint_repo,
        keyword_search=keyword_search,
        vector_search=stub,
        document_repo=bundle.document_repo,
    )

    candidates = search.search("find pet by id", CandidateSearchOptions(top_k=5))

    top = next(c for c in candidates if c.endpoint_id == top_ref_id)
    assert top.match_type == "both"


def test_rrf_keyword_only_arm_hit_keeps_keyword_match_type(
    app_state, sample_openapi_3: str
) -> None:
    """벡터 arm 에 전혀 없는 후보는 keyword 로 남는다(양 arm 융합이어도 배타 표기 유지)."""
    _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    keyword_search = KeywordSearch(bundle.chunk_repo)
    stub = StubVectorSearch([], score=0.9)
    search = EndpointCandidateSearch(
        chunk_repo=bundle.chunk_repo,
        endpoint_repo=bundle.endpoint_repo,
        keyword_search=keyword_search,
        vector_search=stub,
        document_repo=bundle.document_repo,
    )

    candidates = search.search("find pet by id", CandidateSearchOptions(top_k=5))

    assert candidates
    assert all(c.match_type == "keyword" for c in candidates)


# --- 엣지 케이스 -------------------------------------------------------------


def test_empty_index_returns_empty_without_embedding(app_state) -> None:
    """문서가 하나도 없으면 임베딩 호출 없이 빈 리스트를 반환한다."""
    app_state.embedding_provider = ExplodingEmbeddingProvider(dim=384)

    candidates = _bundle(app_state).candidate_search.search(
        "anything", CandidateSearchOptions(top_k=5)
    )

    assert candidates == []


@pytest.mark.parametrize("blank_query", ["", "   ", "\n\t"])
def test_blank_query_raises_validation_error(
    app_state, sample_openapi_3: str, blank_query: str
) -> None:
    """빈 질의는 ValidationError 로 거부한다."""
    _register(app_state, sample_openapi_3)

    with pytest.raises(ValidationError):
        _bundle(app_state).candidate_search.search(
            blank_query, CandidateSearchOptions(top_k=5)
        )


@pytest.mark.parametrize("bad_top_k", [0, -1, 51])
def test_out_of_range_top_k_raises_validation_error(
    app_state, sample_openapi_3: str, bad_top_k: int
) -> None:
    """top_k 가 허용 범위(1~50)를 벗어나면 ValidationError 로 거부한다."""
    _register(app_state, sample_openapi_3)

    with pytest.raises(ValidationError):
        _bundle(app_state).candidate_search.search(
            "pet", CandidateSearchOptions(top_k=bad_top_k)
        )


def test_unknown_document_id_raises_not_found(app_state, sample_openapi_3: str) -> None:
    """미등록 document_id 는 빈 결과가 아니라 DocumentNotFoundError 로 구분된다."""
    _register(app_state, sample_openapi_3)

    with pytest.raises(DocumentNotFoundError) as exc_info:
        _bundle(app_state).candidate_search.search(
            "pet", CandidateSearchOptions(top_k=5, document_id="no-such-doc")
        )

    assert exc_info.value.code == "document_not_found"


def test_boundary_top_k_values_are_accepted(app_state, sample_openapi_3: str) -> None:
    """경계값 top_k=1 과 top_k=50 은 허용된다."""
    _register(app_state, sample_openapi_3)
    search = _bundle(app_state).candidate_search

    assert len(search.search("pet", CandidateSearchOptions(top_k=1))) == 1
    assert len(search.search("pet", CandidateSearchOptions(top_k=50))) >= 1


def test_query_is_stripped_before_matching(app_state, sample_openapi_3: str) -> None:
    """앞뒤 공백이 있는 질의도 동일하게 매칭된다."""
    _register(app_state, sample_openapi_3)
    search = _bundle(app_state).candidate_search

    padded = search.search("  find pet by id  ", CandidateSearchOptions(top_k=5))
    plain = search.search("find pet by id", CandidateSearchOptions(top_k=5))

    assert [c.endpoint_id for c in padded] == [c.endpoint_id for c in plain]


def test_repeated_search_is_deterministic(app_state, sample_openapi_3: str) -> None:
    """동일 질의를 반복 호출하면 동일 순서·동일 결과를 반환한다."""
    _register(app_state, sample_openapi_3)
    search = _bundle(app_state).candidate_search

    first = search.search("pet", CandidateSearchOptions(top_k=5))
    second = search.search("pet", CandidateSearchOptions(top_k=5))

    assert first == second


def test_missing_endpoint_row_is_skipped(app_state, sample_openapi_3: str) -> None:
    """청크가 가리키는 엔드포인트가 없으면 그 후보만 건너뛰고 나머지를 반환한다."""
    _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    chunks = [c for c in bundle.chunk_repo.list_all() if c.chunk_type == "endpoint"]
    dangling = chunks[0]
    dangling.ref_id = "missing-endpoint-id"
    bundle.session.commit()

    search = EndpointCandidateSearch(
        chunk_repo=bundle.chunk_repo,
        endpoint_repo=bundle.endpoint_repo,
        keyword_search=KeywordSearch(bundle.chunk_repo),
        vector_search=VectorSearch(app_state.embedding_provider, bundle.chunk_repo),
    )
    candidates = search.search("pet", CandidateSearchOptions(top_k=10))

    # 공허 참 방지: 나머지 후보가 실제로 남아 있어야 "건너뛰기"가 검증된다.
    assert candidates
    assert all(c.endpoint_id != "missing-endpoint-id" for c in candidates)


# --- 5b: exact match 우선 단계 (docs/architect-review/37) -------------------


def test_method_path_exact_query_ranks_target_first(app_state, sample_openapi_3: str) -> None:
    """"GET /pet/{petId}" 처럼 method+path 가 정확히 일치하는 질의는 확정적으로 1위다."""
    _register(app_state, sample_openapi_3)
    candidates = _bundle(app_state).candidate_search.search(
        "GET /pet/{petId}", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert candidates[0].method == "GET"
    assert candidates[0].path == "/pet/{petId}"
    assert candidates[0].match_type == "exact"


def test_method_path_exact_query_is_case_insensitive_on_method(
    app_state, sample_openapi_3: str
) -> None:
    """method 소문자 질의("get /pet/{petId}")도 exact match 로 잡힌다."""
    _register(app_state, sample_openapi_3)
    candidates = _bundle(app_state).candidate_search.search(
        "get /pet/{petId}", CandidateSearchOptions(top_k=5)
    )

    assert candidates[0].path == "/pet/{petId}"
    assert candidates[0].match_type == "exact"


def test_operation_id_exact_query_ranks_target_first(app_state, sample_openapi_3: str) -> None:
    """operationId 그대로("getPetById")를 질의하면 확정적으로 1위다."""
    _register(app_state, sample_openapi_3)
    candidates = _bundle(app_state).candidate_search.search(
        "getPetById", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert candidates[0].path == "/pet/{petId}"
    assert candidates[0].method == "GET"
    assert candidates[0].match_type == "exact"


def test_exact_match_backfills_remaining_slots_with_rrf(app_state, sample_openapi_3: str) -> None:
    """exact match 1건 + 나머지는 기존 RRF 결과로 top_k 를 채운다(중복 없이)."""
    _register(app_state, sample_openapi_3)
    candidates = _bundle(app_state).candidate_search.search(
        "GET /pet/{petId}", CandidateSearchOptions(top_k=5)
    )

    assert candidates[0].match_type == "exact"
    assert len(candidates) > 1
    ids = [c.endpoint_id for c in candidates]
    assert len(ids) == len(set(ids))


def test_non_exact_query_has_no_exact_candidates(app_state, sample_openapi_3: str) -> None:
    """일반 자연어 질의는 exact match 단계를 건너뛴다(match_type에 "exact" 없음)."""
    _register(app_state, sample_openapi_3)
    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert all(c.match_type != "exact" for c in candidates)


def test_exact_match_respects_document_scope(app_state, sample_openapi_3: str) -> None:
    """document_id 로 범위를 좁히면 다른 문서의 동일 method+path는 exact 후보에 섞이지 않는다."""
    document_id = _register(app_state, sample_openapi_3)
    other_document_id = _register(app_state, sample_openapi_3)
    assert document_id != other_document_id

    bundle = _bundle(app_state)
    candidates = bundle.candidate_search.search(
        "GET /pet/{petId}", CandidateSearchOptions(top_k=5, document_id=document_id)
    )

    exact_candidates = [c for c in candidates if c.match_type == "exact"]
    assert len(exact_candidates) == 1
    matched_endpoint = bundle.endpoint_repo.get(exact_candidates[0].endpoint_id)
    assert matched_endpoint is not None
    assert matched_endpoint.document_id == document_id


# --- B: endpoint keyword arm variants 대칭화 (docs/architect-review/68 §3) ----


class _ScriptedKeywordSearch:
    """질의 문자열 → ref_id 순위 리스트로 고정 응답하는 KeywordSearch 대역.

    각 호출의 (query, query_variants) 를 기록해, RRF 경로가 variant 를 독립
    검색으로 분해하는지(공용 계약에 variant term 을 섞지 않는지) 검증한다.
    """

    def __init__(self, script: dict[str, list[str]]) -> None:
        self._script = script
        self.calls: list[tuple[str, list[str] | None]] = []

    def search(
        self,
        query: str,
        top_k: int,
        *,
        document_id: str | None = None,
        project: str | None = None,
        query_variants: list[str] | None = None,
    ) -> list[KeywordHit]:
        self.calls.append((query, query_variants))
        ref_ids = self._script.get(query, [])
        return [
            KeywordHit(chunk_id=f"chunk-{ref_id}", ref_id=ref_id, score=1.0)
            for ref_id in ref_ids[:top_k]
        ]


def _rrf_only_search(bundle, keyword_search) -> EndpointCandidateSearch:
    """벡터 arm 을 끄고 키워드 arm 만 RRF 로 태우는 검색기를 만든다."""
    return EndpointCandidateSearch(
        chunk_repo=bundle.chunk_repo,
        endpoint_repo=bundle.endpoint_repo,
        keyword_search=keyword_search,
        vector_search=SimpleNamespace(search=lambda *a, **k: []),
        vector_fallback_enabled=False,
        document_repo=bundle.document_repo,
    )


def _endpoint_ref_ids(bundle) -> list[str]:
    """등록된 문서의 endpoint 청크 ref_id 목록(= 엔드포인트 id)."""
    return [c.ref_id for c in bundle.chunk_repo.list_all() if c.chunk_type == "endpoint"]


def test_keyword_variant_independent_rank_feeds_rrf(app_state, sample_openapi_3: str) -> None:
    """variant 를 독립 키워드 검색해 얻은 상위 등수가 실제 RRF 순위에 기여한다.

    원문에서 꼴찌였던 후보가 variant 검색에서 1위면, 병합 후 원문 중위권
    후보보다 앞서야 한다(벡터 arm 대칭).
    """
    _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    refs = _endpoint_ref_ids(bundle)
    assert len(refs) >= 3
    first, middle, last = refs[0], refs[1], refs[2]

    keyword_search = _ScriptedKeywordSearch(
        {
            "원문 질의": [first, middle, last],
            "english variant": [last],
        }
    )
    search = _rrf_only_search(bundle, keyword_search)

    out = search.search(
        "원문 질의",
        CandidateSearchOptions(top_k=3, query_variants=["english variant"]),
    )
    ids = [c.endpoint_id for c in out]

    assert ids.index(last) < ids.index(middle)
    assert keyword_search.calls == [("원문 질의", None), ("english variant", None)]


def test_keyword_no_variants_single_call_same_order(app_state, sample_openapi_3: str) -> None:
    """variant 가 없으면 키워드 SQL 은 한 번만 돌고 순위는 기존과 같다."""
    _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    refs = _endpoint_ref_ids(bundle)
    assert len(refs) >= 3
    scripted_order = [refs[1], refs[0], refs[2]]

    keyword_search = _ScriptedKeywordSearch({"주문 목록 조회": scripted_order})
    search = _rrf_only_search(bundle, keyword_search)

    out = search.search("주문 목록 조회", CandidateSearchOptions(top_k=3))

    assert [c.endpoint_id for c in out] == scripted_order
    assert keyword_search.calls == [("주문 목록 조회", None)]


def test_keyword_blank_and_duplicate_variants_are_dropped(app_state, sample_openapi_3: str) -> None:
    """공백·원문 중복·상호 중복 variant 는 키워드 검색 호출 전에 제거된다."""
    _register(app_state, sample_openapi_3)
    bundle = _bundle(app_state)
    refs = _endpoint_ref_ids(bundle)
    assert len(refs) >= 2

    keyword_search = _ScriptedKeywordSearch({"주문 목록 조회": [refs[0]], "list orders": [refs[1]]})
    search = _rrf_only_search(bundle, keyword_search)

    search.search(
        "주문 목록 조회",
        CandidateSearchOptions(
            top_k=5,
            query_variants=["", "   ", "주문 목록 조회", "list orders", "list orders"],
        ),
    )

    assert keyword_search.calls == [
        ("주문 목록 조회", None),
        ("list orders", None),
    ]
