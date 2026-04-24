"""RAG 서비스 테스트."""

from __future__ import annotations

from src.services.rag.llm_provider import NO_RESULT_MESSAGE


def test_rag_no_results_returns_fixed_message(services_factory) -> None:
    # 문서가 등록되지 않은 상태 → 검색 결과 0건
    services = services_factory()
    answer = services.rag_service.answer(question="find anything")
    assert NO_RESULT_MESSAGE in answer.answer
    assert answer.citations == []
    assert answer.is_grounded is False


def test_rag_with_results_includes_method_path(
    services_factory, sample_openapi_3: str
) -> None:
    services = services_factory()
    services.sync_service.register(source_url=None, raw_document=sample_openapi_3)

    answer = services.rag_service.answer(question="find pet by id")
    assert answer.citations
    assert answer.is_grounded is True
    # 답변에는 매칭된 citation 의 method+path 가 반드시 포함된다
    first = answer.citations[0]
    assert f"{first.method} {first.path}" in answer.answer
    # 각 citation 은 실제 저장된 endpoint 의 id 여야 한다
    for c in answer.citations:
        assert services.endpoint_repo.get(c.endpoint_id) is not None


def test_rag_deterministic_on_repeat(services_factory, sample_openapi_3: str) -> None:
    services = services_factory()
    services.sync_service.register(source_url=None, raw_document=sample_openapi_3)

    a = services.rag_service.answer(question="find pet by id")
    b = services.rag_service.answer(question="find pet by id")
    assert a.answer == b.answer
    assert [c.endpoint_id for c in a.citations] == [c.endpoint_id for c in b.citations]


def test_rag_method_filter_limits_citations(
    services_factory, sample_openapi_3: str
) -> None:
    services = services_factory()
    services.sync_service.register(source_url=None, raw_document=sample_openapi_3)

    answer = services.rag_service.answer(question="pet create", method="POST")
    for c in answer.citations:
        assert c.method == "POST"
