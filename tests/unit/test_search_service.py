"""하이브리드 검색 서비스 테스트."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.services.search.search_service import SearchOptions


def _register(services_factory, raw: str):
    services = services_factory()
    result = services.sync_service.register(
        project="default", source_url=None, raw_document=raw
    )
    return services, result


def test_search_finds_expected_endpoint(services_factory, sample_openapi_3: str) -> None:
    services, _ = _register(services_factory, sample_openapi_3)
    results = services.search_service.search(
        "find pet by id", SearchOptions(top_k=5, mode="hybrid")
    )
    assert results
    top = results[0]
    assert top.method == "GET"
    assert top.path == "/pet/{petId}"


def test_search_method_filter(services_factory, sample_openapi_3: str) -> None:
    services, _ = _register(services_factory, sample_openapi_3)
    results = services.search_service.search(
        "pet user create id",
        SearchOptions(top_k=5, method="POST", mode="hybrid"),
    )
    assert results
    assert all(r.method == "POST" for r in results)


def test_search_tag_filter(services_factory, sample_openapi_3: str) -> None:
    services, _ = _register(services_factory, sample_openapi_3)
    results = services.search_service.search(
        "create",
        SearchOptions(top_k=5, tag="user", mode="hybrid"),
    )
    # user 태그는 POST /user 만 존재
    assert results
    for r in results:
        assert r.path == "/user"


def test_search_no_match_returns_empty(services_factory, sample_openapi_3: str) -> None:
    services, _ = _register(services_factory, sample_openapi_3)
    results = services.search_service.search(
        "zzzzz_nothing_matches_here_xxx",
        SearchOptions(top_k=5, mode="hybrid"),
    )
    assert results == []


def test_search_hybrid_score_combines_keyword_and_vector(
    services_factory, sample_openapi_3: str
) -> None:
    services, _ = _register(services_factory, sample_openapi_3)
    results = services.search_service.search(
        "find pet", SearchOptions(top_k=5, mode="hybrid")
    )
    assert results
    # hybrid 모드에선 각 요소 점수가 함께 기록된다
    for r in results:
        assert 0.0 <= r.score <= 1.0
    # 정렬 내림차순
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_unsupported_mode_raises(services_factory, sample_openapi_3: str) -> None:
    services, _ = _register(services_factory, sample_openapi_3)
    with pytest.raises(ValidationError):
        services.search_service.search(
            "find pet", SearchOptions(top_k=5, mode="invalid-mode")
        )


def test_search_respects_top_k(services_factory, sample_openapi_3: str) -> None:
    services, _ = _register(services_factory, sample_openapi_3)
    results = services.search_service.search(
        "pet", SearchOptions(top_k=1, mode="hybrid")
    )
    assert len(results) <= 1


def test_search_finds_markdown_section(services_factory) -> None:
    raw = "# 팀 온보딩\n환영합니다\n## 개발 환경 설정\nuv sync 를 실행해 의존성을 설치하세요\n"
    services, _ = _register(services_factory, raw)
    results = services.search_service.search(
        "개발 환경 설정 uv sync 의존성", SearchOptions(top_k=5, mode="hybrid")
    )
    assert results
    assert results[0].chunk_type == "section"
    assert results[0].summary == "개발 환경 설정"
