"""OpenAPI 3.x / Swagger 2.0 파서.

원문(JSON/YAML) → `ParsedDocument` 중간 표현.
- Swagger 2.0 의 `body` 파라미터는 정규화 단계에서 requestBody 로 변환된다.
- `$ref` 는 로컬 `#/components/schemas/<Name>` / `#/definitions/<Name>` 만 해석.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.core.errors import ParserError


@dataclass
class ParsedParameter:
    """파싱된 파라미터 정보(이름·위치·필수 여부·스키마 등)."""

    name: str
    location: str
    required: bool
    schema: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    schema_ref: str | None = None


@dataclass
class ParsedRequestBody:
    """파싱된 요청 바디 정보."""

    content_type: str
    schema: dict[str, Any] = field(default_factory=dict)
    required: bool = False
    example: Any | None = None
    schema_ref: str | None = None


@dataclass
class ParsedResponse:
    """파싱된 응답 정보."""

    status_code: str
    content_type: str
    schema: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    example: Any | None = None
    schema_ref: str | None = None


@dataclass
class ParsedEndpoint:
    """파싱된 엔드포인트 정보(메서드·경로·파라미터·응답 등)."""

    method: str
    path: str
    operation_id: str | None
    summary: str
    description: str
    tags: list[str] = field(default_factory=list)
    parameters: list[ParsedParameter] = field(default_factory=list)
    request_body: ParsedRequestBody | None = None
    responses: list[ParsedResponse] = field(default_factory=list)


@dataclass
class ParsedSchema:
    """파싱된 컴포넌트 스키마 정보."""

    name: str
    json_schema: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class ParsedSection:
    """파싱된 텍스트 문서(Markdown/CSV 등)의 제목+본문 섹션."""

    title: str
    content: str


@dataclass
class ParsedDocument:
    """파싱된 문서 중간 표현(엔드포인트·스키마 또는 섹션 묶음)."""

    title: str
    version: str
    endpoints: list[ParsedEndpoint] = field(default_factory=list)
    schemas: list[ParsedSchema] = field(default_factory=list)
    sections: list[ParsedSection] = field(default_factory=list)


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def parse_raw(raw: str) -> dict[str, Any]:
    """문자열을 JSON → YAML 순으로 시도해 dict 로 반환."""
    stripped = (raw or "").strip()
    if not stripped:
        raise ParserError("empty document")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        try:
            data = yaml.safe_load(stripped)
        except yaml.YAMLError as exc:
            raise ParserError(f"failed to parse document: {exc}") from exc
    if not isinstance(data, dict):
        raise ParserError("document must be a mapping at top level")
    return data


def parse_document(raw: str) -> ParsedDocument:
    """원문 문자열을 `ParsedDocument` 로 변환."""
    data = parse_raw(raw)
    if "openapi" in data and isinstance(data["openapi"], str) and data["openapi"].startswith("3."):
        return _parse_openapi3(data)
    if "swagger" in data and isinstance(data["swagger"], str) and data["swagger"].startswith("2."):
        return _parse_swagger2(data)
    raise ParserError("unsupported OpenAPI/Swagger version")


def _get_info(data: dict[str, Any]) -> tuple[str, str]:
    """문서의 info.title / info.version 을 안전하게 추출해 반환한다."""
    info = data.get("info") or {}
    title = str(info.get("title") or "Untitled API")
    version = str(info.get("version") or "unknown")
    return title, version


def _parse_openapi3(data: dict[str, Any]) -> ParsedDocument:
    """OpenAPI 3.x 문서를 ParsedDocument 로 변환한다."""
    title, version = _get_info(data)
    paths = data.get("paths")
    if not isinstance(paths, dict):
        raise ParserError("openapi 3.x document is missing `paths`")
    components = data.get("components") or {}
    component_schemas = components.get("schemas") or {}
    if not isinstance(component_schemas, dict):
        component_schemas = {}

    schemas = [
        ParsedSchema(
            name=str(name),
            json_schema=_ensure_dict(schema),
            description=str(_ensure_dict(schema).get("description") or ""),
        )
        for name, schema in component_schemas.items()
    ]

    endpoints: list[ParsedEndpoint] = []
    for path, methods_obj in paths.items():
        if not isinstance(methods_obj, dict):
            continue
        common_params = methods_obj.get("parameters") or []
        for method, operation in methods_obj.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            endpoints.append(_parse_openapi3_operation(str(path), method, operation, common_params))
    return ParsedDocument(title=title, version=version, endpoints=endpoints, schemas=schemas)


def _parse_openapi3_operation(
    path: str,
    method: str,
    operation: dict[str, Any],
    common_parameters: list[Any],
) -> ParsedEndpoint:
    """OpenAPI 3.x 의 operation 객체 하나를 ParsedEndpoint 로 변환한다."""
    parameters: list[ParsedParameter] = []
    for p in list(common_parameters) + list(operation.get("parameters") or []):
        parameters.append(_parse_parameter_like(p))

    request_body = _parse_openapi3_request_body(operation.get("requestBody"))
    responses = _parse_openapi3_responses(operation.get("responses") or {})
    tags = [str(t) for t in (operation.get("tags") or []) if t is not None]
    return ParsedEndpoint(
        method=method.upper(),
        path=path,
        operation_id=operation.get("operationId"),
        summary=str(operation.get("summary") or ""),
        description=str(operation.get("description") or ""),
        tags=tags,
        parameters=parameters,
        request_body=request_body,
        responses=responses,
    )


def _parse_parameter_like(raw: Any) -> ParsedParameter:
    """OpenAPI/Swagger 파라미터 dict 를 ParsedParameter 로 정규화한다."""
    obj = _ensure_dict(raw)
    schema = obj.get("schema")
    if isinstance(schema, dict):
        schema_dict = schema
    else:
        # swagger 2.0 parameters have type/format at same level
        schema_dict = {
            k: v for k, v in obj.items() if k in {"type", "format", "items", "enum", "example"}
        }
    return ParsedParameter(
        name=str(obj.get("name") or ""),
        location=str(obj.get("in") or "query"),
        required=bool(obj.get("required") or False),
        schema=schema_dict,
        description=str(obj.get("description") or ""),
        schema_ref=_extract_ref(schema_dict),
    )


def _parse_openapi3_request_body(raw: Any) -> ParsedRequestBody | None:
    """OpenAPI 3.x requestBody 객체에서 첫 미디어 타입을 골라 ParsedRequestBody 를 만든다."""
    if not isinstance(raw, dict):
        return None
    content = raw.get("content") or {}
    if not isinstance(content, dict) or not content:
        return None
    content_type, media = next(iter(content.items()))
    media = _ensure_dict(media)
    schema = _ensure_dict(media.get("schema"))
    example = media.get("example")
    if example is None:
        examples = media.get("examples")
        if isinstance(examples, dict) and examples:
            first = next(iter(examples.values()))
            if isinstance(first, dict):
                example = first.get("value")
    return ParsedRequestBody(
        content_type=str(content_type),
        schema=schema,
        required=bool(raw.get("required") or False),
        example=example,
        schema_ref=_extract_ref(schema),
    )


def _parse_openapi3_responses(raw: dict[str, Any]) -> list[ParsedResponse]:
    """OpenAPI 3.x responses 매핑을 ParsedResponse 리스트(상태코드 정렬)로 변환한다."""
    responses: list[ParsedResponse] = []
    for status_code, body in raw.items():
        body = _ensure_dict(body)
        description = str(body.get("description") or "")
        content = body.get("content") or {}
        if isinstance(content, dict) and content:
            content_type, media = next(iter(content.items()))
            media = _ensure_dict(media)
            schema = _ensure_dict(media.get("schema"))
            example = media.get("example")
            responses.append(
                ParsedResponse(
                    status_code=str(status_code),
                    content_type=str(content_type),
                    schema=schema,
                    description=description,
                    example=example,
                    schema_ref=_extract_ref(schema),
                )
            )
        else:
            responses.append(
                ParsedResponse(
                    status_code=str(status_code),
                    content_type="application/json",
                    description=description,
                )
            )
    responses.sort(key=lambda r: r.status_code)
    return responses


def _parse_swagger2(data: dict[str, Any]) -> ParsedDocument:
    """Swagger 2.0 문서를 ParsedDocument 로 변환한다."""
    title, version = _get_info(data)
    paths = data.get("paths")
    if not isinstance(paths, dict):
        raise ParserError("swagger 2.0 document is missing `paths`")
    definitions = data.get("definitions") or {}
    consumes_default = data.get("consumes") or ["application/json"]
    produces_default = data.get("produces") or ["application/json"]
    if not isinstance(definitions, dict):
        definitions = {}

    schemas = [
        ParsedSchema(
            name=str(name),
            json_schema=_ensure_dict(schema),
            description=str(_ensure_dict(schema).get("description") or ""),
        )
        for name, schema in definitions.items()
    ]

    endpoints: list[ParsedEndpoint] = []
    for path, methods_obj in paths.items():
        if not isinstance(methods_obj, dict):
            continue
        for method, operation in methods_obj.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            endpoints.append(
                _parse_swagger2_operation(
                    str(path), method, operation, consumes_default, produces_default
                )
            )
    normalized_endpoints = [_normalize_swagger2_ref(e) for e in endpoints]
    return ParsedDocument(
        title=title, version=version, endpoints=normalized_endpoints, schemas=schemas
    )


def _parse_swagger2_operation(
    path: str,
    method: str,
    operation: dict[str, Any],
    consumes_default: list[str],
    produces_default: list[str],
) -> ParsedEndpoint:
    """Swagger 2.0 의 operation 하나를 ParsedEndpoint 로 변환한다(body 파라미터는 requestBody 로 승격)."""
    parameters: list[ParsedParameter] = []
    body_param: dict[str, Any] | None = None
    for p in operation.get("parameters") or []:
        p_obj = _ensure_dict(p)
        if p_obj.get("in") == "body":
            body_param = p_obj
            continue
        parameters.append(_parse_parameter_like(p_obj))
    request_body: ParsedRequestBody | None = None
    if body_param is not None:
        schema = _ensure_dict(body_param.get("schema"))
        content_type = (consumes_default or ["application/json"])[0]
        request_body = ParsedRequestBody(
            content_type=str(content_type),
            schema=schema,
            required=bool(body_param.get("required") or False),
            example=body_param.get("example"),
            schema_ref=_extract_ref(schema),
        )

    responses: list[ParsedResponse] = []
    for status_code, body in (operation.get("responses") or {}).items():
        body = _ensure_dict(body)
        schema = _ensure_dict(body.get("schema"))
        content_type = (produces_default or ["application/json"])[0]
        responses.append(
            ParsedResponse(
                status_code=str(status_code),
                content_type=str(content_type),
                schema=schema,
                description=str(body.get("description") or ""),
                example=body.get("example"),
                schema_ref=_extract_ref(schema),
            )
        )
    responses.sort(key=lambda r: r.status_code)

    tags = [str(t) for t in (operation.get("tags") or []) if t is not None]
    return ParsedEndpoint(
        method=method.upper(),
        path=path,
        operation_id=operation.get("operationId"),
        summary=str(operation.get("summary") or ""),
        description=str(operation.get("description") or ""),
        tags=tags,
        parameters=parameters,
        request_body=request_body,
        responses=responses,
    )


def _normalize_swagger2_ref(endpoint: ParsedEndpoint) -> ParsedEndpoint:
    """Swagger 2.0 `#/definitions/X` 를 `#/components/schemas/X` 로 치환한다."""

    def _rewrite(ref: str | None) -> str | None:
        """`#/definitions/X` 형 ref 를 OpenAPI 3 스타일로 치환한다."""
        if not ref:
            return ref
        return ref.replace("#/definitions/", "#/components/schemas/")

    for p in endpoint.parameters:
        p.schema_ref = _rewrite(p.schema_ref)
        _rewrite_inline_ref(p.schema)
    if endpoint.request_body is not None:
        endpoint.request_body.schema_ref = _rewrite(endpoint.request_body.schema_ref)
        _rewrite_inline_ref(endpoint.request_body.schema)
    for r in endpoint.responses:
        r.schema_ref = _rewrite(r.schema_ref)
        _rewrite_inline_ref(r.schema)
    return endpoint


def _rewrite_inline_ref(schema: dict[str, Any]) -> None:
    """스키마 dict 안의 $ref 가 swagger2 형식이면 OpenAPI 3 형식으로 치환한다."""
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/definitions/"):
        schema["$ref"] = ref.replace("#/definitions/", "#/components/schemas/")


def _ensure_dict(value: Any) -> dict[str, Any]:
    """값이 dict 면 그대로, 아니면 빈 dict 를 반환한다."""
    if isinstance(value, dict):
        return value
    return {}


def _extract_ref(schema: dict[str, Any]) -> str | None:
    """스키마 dict 의 $ref 문자열을 반환하고, 없으면 None 을 반환한다."""
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref:
        return ref
    return None
