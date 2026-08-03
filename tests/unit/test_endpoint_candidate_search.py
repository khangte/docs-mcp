"""엔드포인트 후보 검색(키워드 우선 + 벡터 보조) 테스트.

SPEC 기능 1 의 검증 기준을 그대로 옮긴다.
"""

from __future__ import annotations

import pytest

from app.api.dependencies import build_services
from app.core.errors import DocumentNotFoundError, ValidationError
from app.services.search.endpoint_candidate_search import (
    CandidateSearchOptions,
    EndpointCandidateSearch,
)
from app.services.search.keyword_search import KeywordSearch
from app.services.search.vector_search import VectorSearch
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
    """키워드로 명확한 질의는 해당 엔드포인트를 최상위로 찾는다."""
    _register(app_state, sample_openapi_3)
    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert candidates[0].method == "GET"
    assert candidates[0].path == "/pet/{petId}"
    assert candidates[0].match_type == "keyword"


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


# --- 핵심 검증: 키워드가 맞으면 임베딩 API 를 호출하지 않는다 -----------------


def test_keyword_hit_does_not_call_embedding_provider(
    app_state, counting_embedding_provider, sample_openapi_3: str
) -> None:
    """키워드 결과가 1건 이상이면 임베딩 프로바이더 호출 카운트가 0 이다."""
    _register(app_state, sample_openapi_3)
    counting_embedding_provider.reset_counts()

    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert counting_embedding_provider.embed_call_count == 0


def test_exact_path_query_does_not_call_embedding_provider(
    app_state, counting_embedding_provider, sample_openapi_3: str
) -> None:
    """"GET /pet/{petId}" 처럼 구조적으로 명확한 질의도 임베딩 호출이 0 이다."""
    _register(app_state, sample_openapi_3)
    counting_embedding_provider.reset_counts()

    candidates = _bundle(app_state).candidate_search.search(
        "GET /pet/{petId}", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert all(c.match_type == "keyword" for c in candidates)
    assert counting_embedding_provider.embed_call_count == 0


def test_keyword_hit_path_never_touches_embedding(app_state, sample_openapi_3: str) -> None:
    """임베딩이 호출되면 즉시 실패하는 프로바이더로도 키워드 경로가 성공한다."""
    _register(app_state, sample_openapi_3)
    app_state.embedding_provider = ExplodingEmbeddingProvider(dim=256)

    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert all(c.match_type == "keyword" for c in candidates)


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
    endpoint_chunk_ids = [
        c.id for c in bundle.chunk_repo.list_endpoint_chunks()
    ]
    assert endpoint_chunk_ids, "엔드포인트 청크가 있어야 스텁 검증이 의미 있다"
    stub = StubVectorSearch(endpoint_chunk_ids[:stub_chunk_limit], score=stub_score)
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
    """벡터 보조가 비활성이면 임베딩 호출 없이 빈 결과를 반환한다(Gemini 키 없는 환경)."""
    _register(app_state, sample_openapi_3)
    app_state.vector_fallback_enabled = False
    app_state.embedding_provider = ExplodingEmbeddingProvider(dim=256)

    candidates = _bundle(app_state).candidate_search.search(
        NO_MATCH_QUERY, CandidateSearchOptions(top_k=5)
    )

    assert candidates == []


def test_vector_fallback_disabled_still_returns_keyword_results(
    app_state, sample_openapi_3: str
) -> None:
    """벡터 보조가 비활성이어도 키워드 결과는 정상 반환된다."""
    _register(app_state, sample_openapi_3)
    app_state.vector_fallback_enabled = False
    app_state.embedding_provider = ExplodingEmbeddingProvider(dim=256)

    candidates = _bundle(app_state).candidate_search.search(
        "find pet by id", CandidateSearchOptions(top_k=5)
    )

    assert candidates
    assert all(c.match_type == "keyword" for c in candidates)


# --- 엣지 케이스 -------------------------------------------------------------


def test_empty_index_returns_empty_without_embedding(app_state) -> None:
    """문서가 하나도 없으면 임베딩 호출 없이 빈 리스트를 반환한다."""
    app_state.embedding_provider = ExplodingEmbeddingProvider(dim=256)

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
