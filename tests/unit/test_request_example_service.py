"""요청 예시 생성 서비스 테스트."""

from __future__ import annotations

import pytest

from app.core.errors import EndpointNotFoundError, ValidationError


def _register_and_find_get_pet(services_factory, sample_openapi_3: str):
    services = services_factory()
    result = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    endpoints = services.endpoint_repo.list_by_document(result.document.id)
    get_pet = next(e for e in endpoints if e.method == "GET" and e.path == "/pet/{petId}")
    return services, get_pet


def test_curl_example_contains_method_and_path(
    services_factory, sample_openapi_3: str
) -> None:
    services, ep = _register_and_find_get_pet(services_factory, sample_openapi_3)
    payload = services.example_service.generate(ep.id, "curl")
    assert payload["format"] == "curl"
    assert "curl -X GET" in payload["code"]
    assert "/pet/" in payload["code"]
    # path 파라미터는 schema 에 example=42 가 있어 42로 치환
    assert "42" in payload["code"]
    assert "{petId}" not in payload["code"]


def test_fetch_example_valid_js_call(services_factory, sample_openapi_3: str) -> None:
    services, ep = _register_and_find_get_pet(services_factory, sample_openapi_3)
    payload = services.example_service.generate(ep.id, "fetch")
    assert payload["format"] == "fetch"
    assert payload["code"].startswith("fetch(")
    assert "/pet/42" in payload["code"]
    assert '"method": "GET"' in payload["code"]


def test_axios_example_contains_method_and_url(
    services_factory, sample_openapi_3: str
) -> None:
    services, ep = _register_and_find_get_pet(services_factory, sample_openapi_3)
    payload = services.example_service.generate(ep.id, "axios")
    assert payload["format"] == "axios"
    assert payload["code"].startswith("axios(")
    assert '"method": "get"' in payload["code"]
    assert "/pet/42" in payload["code"]


def test_python_example_uses_requests(services_factory, sample_openapi_3: str) -> None:
    services, ep = _register_and_find_get_pet(services_factory, sample_openapi_3)
    payload = services.example_service.generate(ep.id, "python")
    assert payload["format"] == "python"
    assert "import requests" in payload["code"]
    assert "requests.get(" in payload["code"]
    assert "/pet/42" in payload["code"]


def test_unsupported_format_raises(services_factory, sample_openapi_3: str) -> None:
    services, ep = _register_and_find_get_pet(services_factory, sample_openapi_3)
    with pytest.raises(ValidationError):
        services.example_service.generate(ep.id, "ruby")


def test_missing_endpoint_raises(services_factory) -> None:
    services = services_factory()
    with pytest.raises(EndpointNotFoundError):
        services.example_service.generate("does-not-exist", "curl")


def test_deterministic_for_same_inputs(services_factory, sample_openapi_3: str) -> None:
    services, ep = _register_and_find_get_pet(services_factory, sample_openapi_3)
    one = services.example_service.generate(ep.id, "curl")
    two = services.example_service.generate(ep.id, "curl")
    assert one == two
