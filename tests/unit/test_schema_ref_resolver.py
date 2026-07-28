"""`$ref` 스키마 펼치기 서비스 테스트.

SPEC 기능 3 의 검증 기준을 그대로 옮긴다.
"""

from __future__ import annotations

import json

import pytest

from app.api.dependencies import build_services
from app.core.errors import ValidationError
from app.services.schemas.schema_ref_resolver import (
    SchemaRefNotFoundError,
    describe_type,
    extract_fields,
    parse_local_schema_ref,
)

NESTED_DOC: dict = {
    "openapi": "3.0.3",
    "info": {"title": "Nested API", "version": "1.0.0"},
    "paths": {
        "/order": {
            "get": {
                "operationId": "getOrder",
                "summary": "Get order",
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Order"}
                            }
                        },
                    }
                },
            }
        }
    },
    "components": {
        "schemas": {
            "Order": {
                "type": "object",
                "description": "주문",
                "properties": {
                    "id": {"type": "integer", "description": "주문 번호"},
                    "customer": {"$ref": "#/components/schemas/Customer"},
                    "items": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/LineItem"},
                    },
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string", "description": "주문 상태"},
                    "note": {},
                },
                "required": ["id", "customer"],
            },
            "Customer": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"$ref": "#/components/schemas/Address"},
                },
                "required": ["name"],
            },
            "Address": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
            "LineItem": {
                "type": "object",
                "properties": {"sku": {"type": "string"}},
            },
            "PlainString": {"type": "string"},
        }
    },
}


def _register(app_state, raw: str) -> str:
    """문서를 등록하고 document_id 를 반환한다."""
    bundle = next(build_services(app_state))
    return bundle.sync_service.register(source_url=None, raw_document=raw).document.id


def _bundle(app_state):
    """새 서비스 번들을 만든다."""
    return next(build_services(app_state))


@pytest.fixture()
def nested_doc_state(app_state):
    """중첩 `$ref` 를 가진 샘플 문서를 등록한 app_state 를 돌려준다."""
    document_id = _register(app_state, json.dumps(NESTED_DOC))
    return app_state, document_id


# --- 정상 케이스 -------------------------------------------------------------


def test_resolves_schema_into_fields(nested_doc_state) -> None:
    """참조가 이름과 필드 목록으로 펼쳐진다."""
    app_state, document_id = nested_doc_state

    resolved = _bundle(app_state).schema_ref_resolver.resolve(
        "#/components/schemas/Customer", document_id=document_id
    )

    assert resolved.name == "Customer"
    assert [f.name for f in resolved.fields] == ["name", "address"]


def test_required_flag_reflects_required_array(nested_doc_state) -> None:
    """required 배열에 있는 필드만 required=True 로 표시된다."""
    app_state, document_id = nested_doc_state

    resolved = _bundle(app_state).schema_ref_resolver.resolve(
        "#/components/schemas/Order", document_id=document_id
    )

    required_names = {f.name for f in resolved.fields if f.required}
    assert required_names == {"id", "customer"}


def test_description_is_carried_over(nested_doc_state) -> None:
    """필드 description 이 그대로 전달된다."""
    app_state, document_id = nested_doc_state

    resolved = _bundle(app_state).schema_ref_resolver.resolve(
        "#/components/schemas/Order", document_id=document_id
    )

    by_name = {f.name: f for f in resolved.fields}
    assert by_name["id"].description == "주문 번호"
    assert by_name["note"].description == ""


def test_resolves_without_document_id(nested_doc_state) -> None:
    """document_id 를 생략해도 등록된 문서에서 스키마를 찾는다."""
    app_state, _ = nested_doc_state

    resolved = _bundle(app_state).schema_ref_resolver.resolve(
        "#/components/schemas/Address"
    )

    assert resolved.name == "Address"
    assert [f.name for f in resolved.fields] == ["city"]


def test_swagger2_definitions_ref_is_accepted(app_state, sample_swagger_2: str) -> None:
    """Swagger 2.0 의 `#/definitions/X` 참조도 해석한다."""
    document_id = _register(app_state, sample_swagger_2)

    resolved = _bundle(app_state).schema_ref_resolver.resolve(
        "#/definitions/Item", document_id=document_id
    )

    assert resolved.name == "Item"
    assert [f.name for f in resolved.fields] == ["id", "name"]


# --- 비재귀 동작 -------------------------------------------------------------


def test_nested_ref_is_not_expanded_recursively(nested_doc_state) -> None:
    """중첩 `$ref` 는 펼치지 않고 참조 이름만 type 에 표기한다."""
    app_state, document_id = nested_doc_state

    resolved = _bundle(app_state).schema_ref_resolver.resolve(
        "#/components/schemas/Order", document_id=document_id
    )

    customer = next(f for f in resolved.fields if f.name == "customer")
    assert customer.type == "Customer"
    # Customer 의 하위 필드(name/address)가 Order 결과에 섞여 들어오지 않는다
    assert "name" not in {f.name for f in resolved.fields}
    assert "address" not in {f.name for f in resolved.fields}


def test_array_of_ref_shows_item_ref_name_only(nested_doc_state) -> None:
    """배열 원소가 `$ref` 면 array<Name> 으로 한 단계만 표기한다."""
    app_state, document_id = nested_doc_state

    resolved = _bundle(app_state).schema_ref_resolver.resolve(
        "#/components/schemas/Order", document_id=document_id
    )

    items = next(f for f in resolved.fields if f.name == "items")
    assert items.type == "array<LineItem>"


def test_scalar_schema_returns_empty_fields(nested_doc_state) -> None:
    """properties 가 없는 스칼라 스키마는 빈 필드 목록을 반환한다."""
    app_state, document_id = nested_doc_state

    resolved = _bundle(app_state).schema_ref_resolver.resolve(
        "#/components/schemas/PlainString", document_id=document_id
    )

    assert resolved.name == "PlainString"
    assert resolved.fields == []


# --- 결정성 -----------------------------------------------------------------


def test_repeated_resolution_is_deterministic(nested_doc_state) -> None:
    """동일 ref 를 반복 호출하면 동일 결과를 반환한다."""
    app_state, document_id = nested_doc_state
    resolver = _bundle(app_state).schema_ref_resolver

    first = resolver.resolve("#/components/schemas/Order", document_id=document_id)
    second = resolver.resolve("#/components/schemas/Order", document_id=document_id)

    assert first == second
    assert [f.name for f in first.fields] == [f.name for f in second.fields]


# --- 에러 케이스 -------------------------------------------------------------


def test_unknown_ref_raises_not_found(nested_doc_state) -> None:
    """존재하지 않는 스키마 참조는 SchemaRefNotFoundError 를 발생시킨다."""
    app_state, document_id = nested_doc_state

    with pytest.raises(SchemaRefNotFoundError) as exc_info:
        _bundle(app_state).schema_ref_resolver.resolve(
            "#/components/schemas/NoSuchSchema", document_id=document_id
        )

    assert exc_info.value.code == "schema_ref_not_found"
    assert "NoSuchSchema" in str(exc_info.value)


def test_unknown_ref_without_any_document_raises_not_found(app_state) -> None:
    """등록된 문서가 없으면 조회 자체가 실패해 SchemaRefNotFoundError 가 난다."""
    with pytest.raises(SchemaRefNotFoundError):
        _bundle(app_state).schema_ref_resolver.resolve("#/components/schemas/Pet")


@pytest.mark.parametrize(
    "bad_ref",
    [
        "",
        "   ",
        "Product",
        "#/components/parameters/PetId",
        "https://example.com/schema.json#/Product",
        "#/components/schemas/",
    ],
)
def test_unsupported_ref_format_raises_validation_error(
    nested_doc_state, bad_ref: str
) -> None:
    """로컬 컴포넌트 스키마 참조 형식이 아니면 ValidationError 로 거부한다."""
    app_state, document_id = nested_doc_state

    with pytest.raises(ValidationError):
        _bundle(app_state).schema_ref_resolver.resolve(bad_ref, document_id=document_id)


# --- 순수 함수 단위 테스트(DB 불필요) ----------------------------------------


def test_parse_local_schema_ref_extracts_name() -> None:
    """참조 문자열에서 스키마 이름만 추출한다."""
    assert parse_local_schema_ref("#/components/schemas/Product") == "Product"
    assert parse_local_schema_ref("  #/definitions/Item  ") == "Item"


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string"}, "string"),
        ({"type": "integer"}, "integer"),
        ({"$ref": "#/components/schemas/Pet"}, "Pet"),
        ({"type": "array", "items": {"type": "string"}}, "array<string>"),
        ({"type": "array", "items": {"$ref": "#/components/schemas/Pet"}}, "array<Pet>"),
        ({"type": ["string", "null"]}, "string | null"),
        ({"allOf": [{"$ref": "#/components/schemas/Pet"}]}, "allOf<Pet>"),
        ({}, "unknown"),
        (None, "unknown"),
    ],
)
def test_describe_type(schema, expected: str) -> None:
    """다양한 스키마 형태의 타입 표현을 확인한다."""
    assert describe_type(schema) == expected


def test_extract_fields_without_properties_returns_empty() -> None:
    """properties 가 없으면 빈 필드 목록을 반환한다."""
    assert extract_fields({"type": "string"}) == []
    assert extract_fields({}) == []


def test_extract_fields_preserves_declaration_order() -> None:
    """필드 순서는 문서 선언 순서를 유지한다."""
    schema = {
        "type": "object",
        "properties": {"z": {"type": "string"}, "a": {"type": "string"}},
    }

    assert [f.name for f in extract_fields(schema)] == ["z", "a"]
