"""스키마 정규화/ref 해석 테스트."""

from __future__ import annotations

from src.services.parser.openapi_parser import parse_document
from src.services.parser.schema_normalizer import (
    collect_referenced_schemas,
    resolve_ref,
    schema_name_from_ref,
)


def test_swagger2_body_parameter_moved_to_request_body(sample_swagger_2: str) -> None:
    parsed = parse_document(sample_swagger_2)
    create_item = next(
        e for e in parsed.endpoints if e.method == "POST" and e.path == "/items"
    )
    # 정규화 후 body 파라미터는 parameters 에서 사라지고 request_body 로 이동
    assert all(p.location != "body" for p in create_item.parameters)
    assert create_item.request_body is not None
    assert create_item.request_body.schema_ref == "#/components/schemas/Item"


def test_swagger2_definitions_accessible_like_components(sample_swagger_2: str) -> None:
    """Swagger2 `definitions` 는 내부적으로 `components.schemas` 와 동등히 다뤄진다."""
    parsed = parse_document(sample_swagger_2)
    # schemas 리스트에 Item 이 있어야 하고 ref 도 components 경로로 해석된다
    assert {s.name for s in parsed.schemas} == {"Item"}
    resolved = resolve_ref("#/components/schemas/Item", parsed.schemas)
    assert resolved is not None
    assert resolved.get("type") == "object"


def test_resolve_ref_returns_none_for_unknown(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    assert resolve_ref("#/components/schemas/NotExist", parsed.schemas) is None


def test_resolve_ref_returns_none_for_non_component_ref(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    assert resolve_ref("#/other/refs/Pet", parsed.schemas) is None
    assert resolve_ref(None, parsed.schemas) is None


def test_schema_name_from_ref() -> None:
    assert schema_name_from_ref("#/components/schemas/Pet") == "Pet"
    assert schema_name_from_ref("#/other/Something") is None
    assert schema_name_from_ref(None) is None


def test_collect_referenced_schemas_only_contains_used(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    # /pet/{petId} GET 만 고려 → Pet 만 포함되어야 한다 (User 제외)
    get_pet = next(
        e for e in parsed.endpoints if e.method == "GET" and e.path == "/pet/{petId}"
    )
    refs = [r.schema_ref for r in get_pet.responses]
    referenced = collect_referenced_schemas(refs, parsed)
    names = {s.name for s in referenced}
    assert names == {"Pet"}


def test_collect_referenced_schemas_empty_when_no_refs(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    referenced = collect_referenced_schemas([None, None], parsed)
    assert referenced == []
