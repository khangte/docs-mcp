"""`$ref` 스키마 펼치기 서비스.

`#/components/schemas/<Name>` 형태의 로컬 참조를 실제 필드 목록으로 펼친다.
중첩 `$ref` 는 재귀적으로 펼치지 않고 참조 이름만 `type` 에 표기한다
(무한 재귀 방지 — SPEC 기능 3 검증 기준).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.errors import DocumentNotFoundError, ValidationError
from app.models.openapi import ApiSchema
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository

LOCAL_SCHEMA_REF_PREFIX = "#/components/schemas/"
SWAGGER2_DEFINITION_REF_PREFIX = "#/definitions/"


@dataclass(frozen=True)
class ResolvedField:
    """펼쳐진 스키마의 필드 한 개."""

    name: str
    type: str
    required: bool
    description: str


@dataclass(frozen=True)
class ResolvedSchema:
    """펼쳐진 스키마 전체(이름 + 소속 문서 + 필드 목록).

    `document_id` 를 함께 담아, 여러 문서에 동명 스키마가 있을 때 호출 LLM 이
    어느 문서의 스키마를 받았는지 확인할 수 있게 한다.
    """

    name: str
    document_id: str
    fields: list[ResolvedField]


class SchemaRefNotFoundError(ValidationError):
    """참조가 가리키는 컴포넌트 스키마를 찾을 수 없을 때 발생."""

    def __init__(self, ref: str) -> None:
        """찾지 못한 참조 문자열을 메시지·코드와 함께 보관한다."""
        super().__init__(f"schema ref not found: {ref}")
        self.code = "schema_ref_not_found"
        self.ref = ref


class SchemaRefResolver:
    """로컬 컴포넌트 스키마 참조를 필드 목록으로 펼치는 서비스."""

    def __init__(
        self,
        endpoint_repo: EndpointRepository,
        document_repo: DocumentRepository,
    ) -> None:
        """스키마 조회용 저장소와 문서 목록 조회용 저장소를 보관한다."""
        self._endpoint_repo = endpoint_repo
        self._document_repo = document_repo

    def resolve(self, ref: str, document_id: str | None = None) -> ResolvedSchema:
        """참조 문자열을 펼쳐 필드 목록을 만든다.

        Args:
            ref: `#/components/schemas/Product` 형태의 로컬 참조.
                Swagger 2.0 의 `#/definitions/Product` 도 허용한다.
            document_id: 특정 문서로 조회 범위를 제한할 때 지정. 생략하면
                등록된 모든 문서에서 같은 이름의 스키마를 찾는다. 여러 문서에
                동명 스키마가 있으면 가장 최근 등록 문서가 선택되므로,
                모호성을 없애려면 document_id 지정을 권장한다.

        Returns:
            스키마 이름·소속 document_id·필드 목록. 동일 입력에 대해 항상
            동일 결과(결정성).

        Raises:
            ValidationError: 지원하지 않는 참조 형식인 경우.
            DocumentNotFoundError: document_id 가 등록되지 않은 문서인 경우.
            SchemaRefNotFoundError: 해당 이름의 컴포넌트 스키마가 없는 경우.
        """
        schema_name = parse_local_schema_ref(ref)
        schema = self._find_schema(schema_name, document_id)
        if schema is None:
            raise SchemaRefNotFoundError(ref)
        return ResolvedSchema(
            name=schema.name,
            document_id=schema.document_id,
            fields=extract_fields(schema.schema),
        )

    def _find_schema(self, schema_name: str, document_id: str | None) -> ApiSchema | None:
        """문서 범위에 따라 컴포넌트 스키마를 조회한다.

        document_id 가 주어지면 먼저 문서 존재를 검증해, "문서 자체가 없음"과
        "문서는 있으나 스키마가 없음"이 서로 다른 오류로 구분되게 한다.
        생략하면 등록 문서를 색인 시각 내림차순(동률 시 id 오름차순)으로 훑어
        가장 먼저 매칭되는 스키마를 쓴다.

        Raises:
            DocumentNotFoundError: document_id 가 등록되지 않은 문서인 경우.
        """
        if document_id is not None:
            if self._document_repo.get(document_id) is None:
                raise DocumentNotFoundError(document_id)
            return self._endpoint_repo.get_schema_by_name(document_id, schema_name)
        for document in self._document_repo.list_all():
            found = self._endpoint_repo.get_schema_by_name(document.id, schema_name)
            if found is not None:
                return found
        return None


def parse_local_schema_ref(ref: str) -> str:
    """로컬 컴포넌트 스키마 참조에서 스키마 이름만 추출한다.

    Raises:
        ValidationError: 빈 문자열이거나 로컬 컴포넌트 참조 형식이 아닌 경우.
    """
    normalized = (ref or "").strip()
    if not normalized:
        raise ValidationError("ref must not be empty")
    for prefix in (LOCAL_SCHEMA_REF_PREFIX, SWAGGER2_DEFINITION_REF_PREFIX):
        if normalized.startswith(prefix):
            name = normalized[len(prefix) :]
            if not name:
                raise ValidationError(f"ref is missing a schema name: {ref}")
            return name
    raise ValidationError(
        f"unsupported ref: {ref} (only {LOCAL_SCHEMA_REF_PREFIX}<Name> is supported)"
    )


def extract_fields(json_schema: dict[str, Any]) -> list[ResolvedField]:
    """JSON Schema 의 properties 를 필드 목록으로 변환한다.

    properties 가 없는 스칼라/배열 스키마는 빈 리스트를 반환한다.
    필드 순서는 문서에 정의된 순서를 유지한다.
    """
    properties = json_schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required_names = _required_names(json_schema)
    return [
        ResolvedField(
            name=str(field_name),
            type=describe_type(field_schema),
            required=str(field_name) in required_names,
            description=_description_of(field_schema),
        )
        for field_name, field_schema in properties.items()
    ]


def describe_type(field_schema: Any) -> str:
    """필드 스키마의 타입을 사람이 읽을 수 있는 문자열로 표현한다.

    중첩 `$ref` 는 펼치지 않고 참조 이름(예: `Address`)만 반환한다.
    배열은 `array<Item>` 형태로 원소 타입을 한 단계만 표기한다.
    """
    if not isinstance(field_schema, dict):
        return "unknown"

    ref = field_schema.get("$ref")
    if isinstance(ref, str) and ref:
        return _ref_display_name(ref)

    schema_type = field_schema.get("type")
    if schema_type == "array":
        return f"array<{describe_type(field_schema.get('items'))}>"
    if isinstance(schema_type, str) and schema_type:
        return schema_type
    if isinstance(schema_type, list):
        # JSON Schema 의 union 타입 표기(["string", "null"])
        return " | ".join(str(t) for t in schema_type)
    for composite_key in ("allOf", "oneOf", "anyOf"):
        members = field_schema.get(composite_key)
        if isinstance(members, list) and members:
            return f"{composite_key}<{', '.join(describe_type(m) for m in members)}>"
    return "unknown"


def _ref_display_name(ref: str) -> str:
    """참조 문자열에서 표시용 이름을 뽑는다(로컬이 아니면 원문 그대로)."""
    for prefix in (LOCAL_SCHEMA_REF_PREFIX, SWAGGER2_DEFINITION_REF_PREFIX):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def _required_names(json_schema: dict[str, Any]) -> set[str]:
    """스키마의 required 배열을 문자열 집합으로 정규화한다."""
    required = json_schema.get("required")
    if not isinstance(required, list):
        return set()
    return {str(name) for name in required}


def _description_of(field_schema: Any) -> str:
    """필드 스키마의 description 을 문자열로 반환한다(없으면 빈 문자열)."""
    if not isinstance(field_schema, dict):
        return ""
    return str(field_schema.get("description") or "")
