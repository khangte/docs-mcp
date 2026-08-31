"""Swagger 2.0 문서 파싱.

`body` 파라미터는 requestBody 로 승격하고, `#/definitions/` 참조는
OpenAPI 3 스타일(`#/components/schemas/`)로 치환한다.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ParserError
from app.services.parser.openapi_parser import (
    _HTTP_METHODS,
    ParsedDocument,
    ParsedEndpoint,
    ParsedRequestBody,
    ParsedResponse,
    ParsedSchema,
    _get_info,
    _parse_parameter_like,
)
from app.services.parser.schema_normalizer import (
    _ensure_dict,
    _extract_ref,
    _rewrite_inline_ref,
)


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
    """Swagger 2.0 의 operation 하나를 ParsedEndpoint 로 변환한다
    (body 파라미터는 requestBody 로 승격)."""
    parameters: list[Any] = []
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
