"""엔드포인트 상세 조회 서비스.

`get_endpoint_details` MCP 도구가 쓰는 조회 경로다. 다음을 보장한다.

1. `schema_ref` 는 참조 문자열 그대로 노출하고 스키마 본문을 미리 펼치지
   않는다(펼치기는 `resolve_ref` 도구의 책임).
2. 호출 예시 코드는 `include_example=True` 일 때만 생성한다. 기본값에서는
   예시 생성 로직 자체를 호출하지 않는다(SPEC Phase 0 결정 7번).
3. `referenced_schema_refs`/`related_endpoints` 순회 힌트를 함께 실어
   준다(`docs/architect-review/12-rag-depth-directions.md` 후보2 얇은 버전) — 서버는 다음
   홉을 자동으로 밟지 않고 후보만 노출한다. `search_endpoints` 는 의도적으로
   얇은 후보만 반환하는 2단계 분리 설계라 이 힌트를 싣지 않는다(추가
   parameter/response 조회가 필요해 그 분리를 훼손하므로) — endpoint_id로
   이 도구를 다시 부르면 되므로 원칙상 문제없다(architect 승인).
   `referenced_schema_refs`는 이미 로드된 parameters/request_body/responses
   에서 dedup만 하므로 추가 DB 왕복이 없다. `related_endpoints`는 문서
   전체를 적재해 Python 에서 거르지 않고, 태그/경로 접두사 필터와 LIMIT를
   `EndpointRepository.list_related` SQL 쿼리 한 번으로 내려 받는다(N+1
   금지, architect 조건).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import EndpointNotFoundError
from app.models.openapi import ApiEndpoint, ApiParameter, ApiRequestBody, ApiResponse
from app.repositories.endpoint_repository import EndpointRepository
from app.services.examples.request_example_service import RequestExampleService

EXAMPLE_FORMAT = "curl"

#: related_endpoints 순회 힌트의 반환 개수 상한(무제한 페이로드 방지).
_MAX_RELATED_ENDPOINTS = 10


@dataclass(frozen=True)
class ParameterDetails:
    """엔드포인트 파라미터 상세(스키마 본문은 그대로, 참조는 문자열로)."""

    name: str
    location: str
    required: bool
    description: str
    schema: dict[str, Any]
    schema_ref: str | None


@dataclass(frozen=True)
class RequestBodyDetails:
    """요청 바디 상세."""

    content_type: str
    required: bool
    schema: dict[str, Any]
    schema_ref: str | None


@dataclass(frozen=True)
class ResponseDetails:
    """응답 상세."""

    status_code: str
    content_type: str
    description: str
    schema: dict[str, Any]
    schema_ref: str | None


@dataclass(frozen=True)
class RelatedEndpointSummary:
    """순회 힌트로 노출하는 관련 엔드포인트 한 건(식별에 필요한 최소 필드만)."""

    endpoint_id: str
    method: str
    path: str


@dataclass(frozen=True)
class EndpointDetailsResult:
    """엔드포인트 상세 조회 결과.

    `example_code` 는 `include_example=True` 로 조회했을 때만 채워진다.
    None 이면 호출자가 응답에서 해당 키를 아예 제외한다.

    `referenced_schema_refs`/`related_endpoints` 는 순회 힌트다 — 서버가
    다음 홉을 판단해 자동으로 호출하지 않고, 후보만 노출한다(밟을지는
    호출측 판단).
    """

    endpoint_id: str
    document_id: str
    method: str
    path: str
    summary: str
    description: str
    tags: list[str] = field(default_factory=list)
    parameters: list[ParameterDetails] = field(default_factory=list)
    request_body: RequestBodyDetails | None = None
    responses: list[ResponseDetails] = field(default_factory=list)
    example_code: str | None = None
    #: 이 엔드포인트의 parameters/request_body/responses 가 참조하는
    #: schema_ref 를 중복 없이 등장 순서대로 모은 목록(resolve_ref 로 펼칠
    #: 다음 홉 후보). $ref 가 하나도 없으면 빈 리스트.
    referenced_schema_refs: list[str] = field(default_factory=list)
    #: 같은 문서 내에서 태그 또는 경로 접두사(첫 세그먼트)를 공유하는 다른
    #: 엔드포인트(자기 자신 제외, 최대 `_MAX_RELATED_ENDPOINTS` 건).
    related_endpoints: list[RelatedEndpointSummary] = field(default_factory=list)


class EndpointDetailsService:
    """엔드포인트 상세를 조립해 반환하는 서비스."""

    def __init__(
        self,
        endpoint_repo: EndpointRepository,
        example_service: RequestExampleService,
    ) -> None:
        """엔드포인트 저장소와 예시 생성 서비스 의존성을 보관한다."""
        self._endpoint_repo = endpoint_repo
        self._example_service = example_service

    def get_details(
        self, endpoint_id: str, include_example: bool = False
    ) -> EndpointDetailsResult:
        """엔드포인트 상세를 조회한다.

        Args:
            endpoint_id: 조회할 엔드포인트 식별자.
            include_example: True 일 때만 curl 예시 코드를 생성해 포함한다.

        Returns:
            파라미터·요청바디·응답과 각 `schema_ref` 를 담은 상세 결과.

        Raises:
            EndpointNotFoundError: 해당 ID 의 엔드포인트가 없는 경우.
        """
        endpoint = self._endpoint_repo.get(endpoint_id)
        if endpoint is None:
            raise EndpointNotFoundError(endpoint_id)

        example_code = (
            self._example_service.generate(endpoint_id, EXAMPLE_FORMAT)["code"]
            if include_example
            else None
        )
        related_rows = self._endpoint_repo.list_related(
            document_id=endpoint.document_id,
            exclude_endpoint_id=endpoint.id,
            tags=endpoint.tags,
            path_prefix=_path_prefix(endpoint.path),
            limit=_MAX_RELATED_ENDPOINTS,
        )
        return _to_details(endpoint, example_code, related_rows)


def _to_details(
    endpoint: ApiEndpoint,
    example_code: str | None,
    related_rows: Sequence[ApiEndpoint],
) -> EndpointDetailsResult:
    """ORM 엔드포인트 엔터티를 상세 결과 DTO 로 변환한다."""
    parameters = [_to_parameter(p) for p in endpoint.parameters]
    request_body = (
        _to_request_body(endpoint.request_body) if endpoint.request_body is not None else None
    )
    responses = sorted(
        (_to_response(r) for r in endpoint.responses),
        key=lambda r: r.status_code,
    )
    return EndpointDetailsResult(
        endpoint_id=endpoint.id,
        document_id=endpoint.document_id,
        method=endpoint.method,
        path=endpoint.path,
        summary=endpoint.summary,
        description=endpoint.description,
        tags=list(endpoint.tags),
        parameters=parameters,
        request_body=request_body,
        responses=responses,
        example_code=example_code,
        referenced_schema_refs=_collect_schema_refs(parameters, request_body, responses),
        related_endpoints=[
            RelatedEndpointSummary(endpoint_id=r.id, method=r.method, path=r.path)
            for r in related_rows
        ],
    )


def _collect_schema_refs(
    parameters: list[ParameterDetails],
    request_body: RequestBodyDetails | None,
    responses: list[ResponseDetails],
) -> list[str]:
    """parameters/request_body/responses 의 schema_ref 를 중복 없이 등장 순서대로 모은다."""
    refs = [p.schema_ref for p in parameters]
    if request_body is not None:
        refs.append(request_body.schema_ref)
    refs.extend(r.schema_ref for r in responses)
    return list(dict.fromkeys(ref for ref in refs if ref))


def _path_prefix(path: str) -> str:
    """경로의 첫 세그먼트를 접두사로 뽑는다(예: "/pet/{petId}" → "/pet")."""
    segments = [s for s in path.split("/") if s]
    return f"/{segments[0]}" if segments else ""


def _to_parameter(parameter: ApiParameter) -> ParameterDetails:
    """ORM 파라미터를 상세 DTO 로 변환한다."""
    return ParameterDetails(
        name=parameter.name,
        location=parameter.location,
        required=bool(parameter.required),
        description=parameter.description,
        schema=_safe_load(parameter.schema_json),
        schema_ref=parameter.schema_ref,
    )


def _to_request_body(body: ApiRequestBody) -> RequestBodyDetails:
    """ORM 요청 바디를 상세 DTO 로 변환한다."""
    return RequestBodyDetails(
        content_type=body.content_type,
        required=bool(body.required),
        schema=_safe_load(body.schema_json),
        schema_ref=body.schema_ref,
    )


def _to_response(response: ApiResponse) -> ResponseDetails:
    """ORM 응답을 상세 DTO 로 변환한다."""
    return ResponseDetails(
        status_code=response.status_code,
        content_type=response.content_type,
        description=response.description,
        schema=_safe_load(response.schema_json),
        schema_ref=response.schema_ref,
    )


def _safe_load(raw: str | None) -> dict[str, Any]:
    """JSON 문자열을 dict 로 파싱하고 실패하면 빈 dict 를 반환한다."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
