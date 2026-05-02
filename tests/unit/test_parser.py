"""OpenAPI/Swagger 파서 테스트."""

from __future__ import annotations

import json

import pytest

from app.core.errors import ParserError
from app.services.parser.openapi_parser import parse_document
from app.services.parser.schema_normalizer import (
    collect_referenced_schemas,
    resolve_ref,
)


def test_parse_openapi3_minimum(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    assert parsed.title == "Petstore API"
    methods_paths = {(e.method, e.path) for e in parsed.endpoints}
    assert ("POST", "/pet") in methods_paths
    assert ("GET", "/pet/{petId}") in methods_paths
    assert ("DELETE", "/pet/{petId}") in methods_paths
    assert ("POST", "/user") in methods_paths
    # schemas
    assert {s.name for s in parsed.schemas} == {"Pet", "User"}


def test_parse_openapi3_path_parameter_required(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    get_pet = next(e for e in parsed.endpoints if e.method == "GET" and e.path == "/pet/{petId}")
    pet_id = next(p for p in get_pet.parameters if p.name == "petId")
    assert pet_id.location == "path"
    assert pet_id.required is True


def test_parse_openapi3_ref_resolution(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    add_pet = next(e for e in parsed.endpoints if e.method == "POST" and e.path == "/pet")
    assert add_pet.request_body is not None
    assert add_pet.request_body.schema_ref == "#/components/schemas/Pet"
    resolved = resolve_ref(add_pet.request_body.schema_ref, parsed.schemas)
    assert resolved is not None
    assert resolved.get("type") == "object"


def test_parse_swagger2_body_to_request_body(sample_swagger_2: str) -> None:
    parsed = parse_document(sample_swagger_2)
    create_item = next(e for e in parsed.endpoints if e.method == "POST" and e.path == "/items")
    assert create_item.request_body is not None
    # swagger 2.0 #/definitions/Item 이 components/schemas 로 치환됐어야 함
    assert create_item.request_body.schema_ref == "#/components/schemas/Item"


def test_parse_rejects_unknown_version() -> None:
    raw = json.dumps({"info": {"title": "x", "version": "1"}, "paths": {}})
    with pytest.raises(ParserError):
        parse_document(raw)


def test_parse_rejects_missing_paths_openapi3() -> None:
    raw = json.dumps({"openapi": "3.0.0", "info": {"title": "x", "version": "1"}})
    with pytest.raises(ParserError):
        parse_document(raw)


def test_parse_rejects_empty() -> None:
    with pytest.raises(ParserError):
        parse_document("")


def test_collect_referenced_schemas(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    refs = []
    for ep in parsed.endpoints:
        if ep.request_body is not None:
            refs.append(ep.request_body.schema_ref)
        for r in ep.responses:
            refs.append(r.schema_ref)
    referenced = collect_referenced_schemas(refs, parsed)
    names = {s.name for s in referenced}
    assert "Pet" in names
    assert "User" in names
