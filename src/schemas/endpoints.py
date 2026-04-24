"""엔드포인트 상세 DTO."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ParameterDetail(BaseModel):
    name: str
    location: str = Field(alias="in")
    required: bool
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    description: str = ""

    model_config = {"populate_by_name": True}


class RequestBodyDetail(BaseModel):
    content_type: str
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    required: bool
    example: Any | None = None

    model_config = {"populate_by_name": True}


class ResponseDetail(BaseModel):
    status_code: str
    content_type: str
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    description: str = ""
    example: Any | None = None

    model_config = {"populate_by_name": True}


class SchemaSnippet(BaseModel):
    name: str
    json_schema: dict[str, Any]


class EndpointDetail(BaseModel):
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
