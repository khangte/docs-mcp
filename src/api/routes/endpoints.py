"""엔드포인트 상세 + 예시."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import ServiceBundle
from src.api.dependency_providers import get_services
from src.core.errors import EndpointNotFoundError
from src.models.openapi import ApiEndpoint, ApiParameter
from src.schemas.endpoints import (
    EndpointDetail,
    ParameterDetail,
    RequestBodyDetail,
    ResponseDetail,
    SchemaSnippet,
)
from src.schemas.examples import ExampleResponse

router = APIRouter()


@router.get("/endpoints/{endpoint_id}", response_model=EndpointDetail)
def get_endpoint(
    endpoint_id: str,
    services: ServiceBundle = Depends(get_services),
) -> EndpointDetail:
    endpoint = services.endpoint_repo.get(endpoint_id)
    if endpoint is None:
        raise EndpointNotFoundError(endpoint_id)

    parameters = [_to_parameter_detail(p) for p in endpoint.parameters]
    responses = sorted(
        [
            ResponseDetail(
                status_code=r.status_code,
                content_type=r.content_type,
                schema=_safe_load(r.schema_json),
                description=r.description,
                example=_safe_example(r.example_json),
            )
            for r in endpoint.responses
        ],
        key=lambda r: r.status_code,
    )
    request_body: RequestBodyDetail | None = None
    if endpoint.request_body is not None:
        request_body = RequestBodyDetail(
            content_type=endpoint.request_body.content_type,
            schema=_safe_load(endpoint.request_body.schema_json),
            required=bool(endpoint.request_body.required),
            example=_safe_example(endpoint.request_body.example_json),
        )

    referenced = _collect_referenced_schemas(endpoint, services)
    return EndpointDetail(
        endpoint_id=endpoint.id,
        document_id=endpoint.document_id,
        method=endpoint.method,
        path=endpoint.path,
        summary=endpoint.summary,
        description=endpoint.description,
        tags=endpoint.tags,
        parameters=parameters,
        request_body=request_body,
        responses=responses,
        referenced_schemas=referenced,
    )


@router.get("/endpoints/{endpoint_id}/example", response_model=ExampleResponse)
def get_example(
    endpoint_id: str,
    format: str = Query(default="curl"),
    services: ServiceBundle = Depends(get_services),
) -> ExampleResponse:
    payload = services.example_service.generate(endpoint_id, format)
    return ExampleResponse(
        format=payload["format"],
        code=payload["code"],
        notes=payload.get("notes"),
    )


def _to_parameter_detail(p: ApiParameter) -> ParameterDetail:
    return ParameterDetail(
        name=p.name,
        **{"in": p.location},
        required=bool(p.required),
        schema=_safe_load(p.schema_json),
        description=p.description,
    )


def _safe_load(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _safe_example(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _collect_referenced_schemas(
    endpoint: ApiEndpoint, services: ServiceBundle
) -> list[SchemaSnippet]:
    refs: list[str] = []
    for p in endpoint.parameters:
        if p.schema_ref:
            refs.append(p.schema_ref)
    if endpoint.request_body is not None and endpoint.request_body.schema_ref:
        refs.append(endpoint.request_body.schema_ref)
    for r in endpoint.responses:
        if r.schema_ref:
            refs.append(r.schema_ref)
    names = set()
    for ref in refs:
        if ref.startswith("#/components/schemas/"):
            names.add(ref.split("/")[-1])
    snippets: list[SchemaSnippet] = []
    for name in sorted(names):
        schema = services.endpoint_repo.get_schema_by_name(endpoint.document_id, name)
        if schema is None:
            continue
        snippets.append(SchemaSnippet(name=name, json_schema=_safe_load(schema.json_schema)))
    return snippets
