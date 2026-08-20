"""엔드포인트 상세 조회 서비스 테스트.

SPEC 기능 2 의 검증 기준을 그대로 옮긴다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.composition import build_services
from app.core.errors import EndpointNotFoundError
from app.services.endpoints.endpoint_details_service import EndpointDetailsService


def _counting_example_service(delegate):
    """호출 위임 서비스를 감싸 generate() 호출 횟수를 세는 페이크."""
    ns = SimpleNamespace(generate_call_count=0)

    def generate(endpoint_id: str, fmt: str):
        ns.generate_call_count += 1
        return delegate.generate(endpoint_id, fmt)

    ns.generate = generate
    return ns


def _register(app_state, raw: str) -> str:
    """샘플 문서를 등록하고 document_id 를 반환한다."""
    bundle = next(build_services(app_state))
    return bundle.sync_service.register(
        project="default", source_url=None, raw_document=raw
    ).document.id


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
    counting = _counting_example_service(bundle.example_service)
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
    counting = _counting_example_service(bundle.example_service)
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


# --- 순회 힌트 링크: referenced_schema_refs / related_endpoints --------------
#
# docs/architect-review/12_rag_depth_directions.md 후보2(얇은 버전) — 서버는 판단하지 않고
# 다음 홉 후보만 노출한다. 밟을지는 호출측(Claude)이 정한다.


def test_referenced_schema_refs_dedups_across_request_body_and_responses(
    app_state, sample_openapi_3: str
) -> None:
    """requestBody·응답이 같은 스키마를 참조해도 한 번만 노출한다(None 은 제외)."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "POST", "/pet")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    # requestBody(Pet) + 응답 200(Pet) + 응답 400(schema_ref 없음) → Pet 한 번만.
    assert result.referenced_schema_refs == ["#/components/schemas/Pet"]


def test_referenced_schema_refs_from_parameter_schema_ref(
    app_state, sample_openapi_3: str
) -> None:
    """파라미터의 schema_ref 도 집계에 포함된다(있는 경우)."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "GET", "/pet/{petId}")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    # petId 파라미터는 schema_ref 가 없고, 응답 200 만 Pet 을 참조한다.
    assert result.referenced_schema_refs == ["#/components/schemas/Pet"]


def test_referenced_schema_refs_empty_when_endpoint_has_no_refs(
    app_state, sample_openapi_3: str
) -> None:
    """어디에도 $ref 가 없으면 빈 리스트다."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "DELETE", "/pet/{petId}")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    assert result.referenced_schema_refs == []


def test_related_endpoints_includes_same_tag_and_path_prefix_siblings(
    app_state, sample_openapi_3: str
) -> None:
    """같은 태그·경로 접두사를 공유하는 다른 엔드포인트를 후보로 노출한다(자기 자신 제외)."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "GET", "/pet/{petId}")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    related = {(r.method, r.path) for r in result.related_endpoints}
    assert related == {("POST", "/pet"), ("DELETE", "/pet/{petId}")}
    assert (endpoint_id, "GET", "/pet/{petId}") not in {
        (r.endpoint_id, r.method, r.path) for r in result.related_endpoints
    }


def test_related_endpoints_excludes_unrelated_tag_and_prefix(
    app_state, sample_openapi_3: str
) -> None:
    """태그도 경로 접두사도 겹치지 않는 엔드포인트는 후보에서 빠진다."""
    doc_id = _register(app_state, sample_openapi_3)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "POST", "/user")

    result = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    assert result.related_endpoints == []


def test_related_endpoints_capped_and_deterministic(app_state) -> None:
    """관련 엔드포인트가 많아도 상한을 넘지 않고, 반복 호출 시 순서가 같다."""
    paths = {
        f"/pet/sub{i}": {
            "get": {
                "operationId": f"getSub{i}",
                "summary": f"sub {i}",
                "tags": ["pet"],
                "responses": {"200": {"description": "ok"}},
            }
        }
        for i in range(15)
    }
    raw = json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "Many", "version": "1"},
            "paths": {
                "/pet": {
                    "get": {
                        "operationId": "listPets",
                        "summary": "list pets",
                        "tags": ["pet"],
                        "responses": {"200": {"description": "ok"}},
                    }
                },
                **paths,
            },
        }
    )
    doc_id = _register(app_state, raw)
    endpoint_id = _find_endpoint_id(app_state, doc_id, "GET", "/pet")

    first = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)
    second = _bundle(app_state).endpoint_details_service.get_details(endpoint_id)

    assert 0 < len(first.related_endpoints) <= 10
    assert first.related_endpoints == second.related_endpoints


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
    counting = _counting_example_service(bundle.example_service)
    service = EndpointDetailsService(
        endpoint_repo=bundle.endpoint_repo, example_service=counting
    )

    with pytest.raises(EndpointNotFoundError):
        service.get_details("does-not-exist", include_example=True)

    assert counting.generate_call_count == 0
