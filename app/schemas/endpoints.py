"""엔드포인트 상세 DTO."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ParameterDetail(BaseModel):
    """엔드포인트 파라미터 상세 DTO."""

    name: str
    location: str = Field(alias="in")
    required: bool
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    description: str = ""

    model_config = {"populate_by_name": True}


class RequestBodyDetail(BaseModel):
    """요청 바디 상세 DTO."""

    content_type: str
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    required: bool
    example: Any | None = None

    model_config = {"populate_by_name": True}


class ResponseDetail(BaseModel):
    """응답 상세 DTO."""

    status_code: str
    content_type: str
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    description: str = ""
    example: Any | None = None

    model_config = {"populate_by_name": True}


class SchemaSnippet(BaseModel):
    """참조된 컴포넌트 스키마 스니펫."""

    name: str
    json_schema: dict[str, Any]


class EndpointDetail(BaseModel):
    """엔드포인트 상세 응답 DTO."""

    endpoint_id: str
    document_id: str
    method: str
    path: str
    summary: str
    description: str
    tags: list[str] = Field(default_factory=list)
    parameters: list[ParameterDetail] = Field(default_factory=list)
    request_body: RequestBodyDetail | None = None
    responses: list[ResponseDetail] = Field(default_factory=list)
    referenced_schemas: list[SchemaSnippet] = Field(default_factory=list)
