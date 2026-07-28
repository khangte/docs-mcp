"""엔드포인트 상세 조회 서비스 테스트.

SPEC 기능 2 의 검증 기준을 그대로 옮긴다.
"""

from __future__ import annotations

import pytest

from app.api.dependencies import build_services
from app.core.errors import EndpointNotFoundError
from app.services.endpoints.endpoint_details_service import EndpointDetailsService
from tests.fixtures.fakes import CountingExampleService


def _register(app_state, raw: str) -> str:
    """샘플 문서를 등록하고 document_id 를 반환한다."""
    bundle = next(build_services(app_state))
    return bundle.sync_service.register(source_url=None, raw_document=raw).document.id


def _bundle(app_state):
    """새 서비스 번들을 만든다."""
    return next(build_services(app_state))


def _find_endpoint_id(app_state, document_id: str, method: str, path: str) -> str:
    """(method, path) 로 엔드포인트 ID 를 찾는다."""
    bundle = _bundle(app_state)
    for endpoint in bundle.endpoint_repo.list_by_document(document_id):
        if endpoint.method == method and endpoint.path == path:
            return endpoint.id
    raise AssertionError(f"엔드포인트를 찾을 수 없음: {method} {path}")


# --- include_example 옵션 ----------------------------------------------------


def test_default_omits_example_code(app_state, sample_openapi_3: str) -> None:
    """기본값(include_example=False)에서는 example_code 가 None 이다."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "GET", "/pet/{petId}")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    assert result.example_code is None


def test_default_does_not_call_example_generation(app_state, sample_openapi_3: str) -> None:
    """include_example=False 면 예시 생성 로직이 호출되지 않는다(호출 카운트 0)."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "GET", "/pet/{petId}")
    bundle = _bundle(app_state)
    counting = CountingExampleService(bundle.example_service)
    service = EndpointDetailsService(
        endpoint_repo=bundle.endpoint_repo, example_service=counting
    )

    service.get_details(endpoint_id)

    assert counting.generate_call_count == 0


def test_include_example_true_calls_generation_once(app_state, sample_openapi_3: str) -> None:
    """include_example=True 면 예시 생성이 정확히 1회 호출되고 코드가 채워진다."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "GET", "/pet/{petId}")
    bundle = _bundle(app_state)
    counting = CountingExampleService(bundle.example_service)
    service = EndpointDetailsService(
        endpoint_repo=bundle.endpoint_repo, example_service=counting
    )

    result = service.get_details(endpoint_id, include_example=True)

    assert counting.generate_call_count == 1
    assert result.example_code is not None
    assert result.example_code.startswith("curl -X GET")


# --- schema_ref 명시 노출 / 스키마 미펼침 ------------------------------------


def test_request_body_exposes_schema_ref_without_expanding(
    app_state, sample_openapi_3: str
) -> None:
    """requestBody 의 schema_ref 는 참조 문자열 그대로 노출된다."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "POST", "/pet")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    assert result.request_body is not None
    assert result.request_body.schema_ref == "#/components/schemas/Pet"


def test_response_exposes_schema_ref(app_state, sample_openapi_3: str) -> None:
    """응답의 schema_ref 도 참조 문자열 그대로 노출된다."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "GET", "/pet/{petId}")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    ok_response = next(r for r in result.responses if r.status_code == "200")
    assert ok_response.schema_ref == "#/components/schemas/Pet"


def test_schema_body_is_not_pre_expanded(app_state, sample_openapi_3: str) -> None:
    """참조된 스키마의 필드(properties)가 상세 응답에 미리 펼쳐져 들어가지 않는다."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "POST", "/pet")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    assert result.request_body is not None
    assert "properties" not in result.request_body.schema
    assert result.request_body.schema.get("$ref") == "#/components/schemas/Pet"


def test_parameters_and_responses_are_included(app_state, sample_openapi_3: str) -> None:
    """파라미터·응답 상세가 정상적으로 채워진다."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "GET", "/pet/{petId}")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    assert result.method == "GET"
    assert result.path == "/pet/{petId}"
    assert result.document_id == doc_id
    assert result.tags == ["pet"]
    pet_id = next(p for p in result.parameters if p.name == "petId")
    assert pet_id.location == "path"
    assert pet_id.required is True
    assert pet_id.schema_ref is None
    assert {r.status_code for r in result.responses} == {"200", "404"}


def test_responses_are_sorted_by_status_code(app_state, sample_openapi_3: str) -> None:
    """응답은 상태 코드 오름차순으로 정렬돼 결정적이다."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "GET", "/pet/{petId}")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    codes = [r.status_code for r in result.responses]
    assert codes == sorted(codes)


def test_endpoint_without_request_body_returns_none(
    app_state, sample_openapi_3: str
) -> None:
    """요청 바디가 없는 엔드포인트는 request_body 가 None 이다."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "DELETE", "/pet/{petId}")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    assert result.request_body is None


# --- 에러 케이스 -------------------------------------------------------------


def test_unknown_endpoint_raises_not_found(app_state) -> None:
    """존재하지 않는 endpoint_id 는 EndpointNotFoundError 를 발생시킨다."""
    with pytest.raises(EndpointNotFoundError) as exc_info:
        _bundle(app_state).endpoint_details_service.get_details("does-not-exist")

    assert exc_info.value.code == "endpoint_not_found"
    assert "does-not-exist" in str(exc_info.value)


def test_unknown_endpoint_does_not_call_example_generation(app_state) -> None:
    """엔드포인트가 없으면 include_example=True 여도 예시 생성을 시도하지 않는다."""
    bundle = _bundle(app_state)
    counting = CountingExampleService(bundle.example_service)
    service = EndpointDetailsService(
        endpoint_repo=bundle.endpoint_repo, example_service=counting
    )

    with pytest.raises(EndpointNotFoundError):
        service.get_details("does-not-exist", include_example=True)

    assert counting.generate_call_count == 0
