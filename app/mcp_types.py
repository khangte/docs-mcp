"""MCP 도구 응답 스키마.

각 @mcp.tool() 함수가 실제로 반환하는 dict 구조를 TypedDict로 명시한다.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class ErrorPayload(TypedDict):
    """DomainError/IntegrationError 발생 시 도구가 반환하는 에러 페이로드."""

    error: bool
    code: str
    message: str


class DocumentSummary(TypedDict):
    """list_documents 가 반환하는 리스트의 원소 타입."""

    document_id: str
    title: str
    version: str
    doc_type: str
    source_url: str | None
    endpoints_count: int
    indexed_at: str | None


class RegisterDocumentResult(TypedDict):
    """register_document 의 반환 타입."""

    document_id: str
    title: str
    version: str
    doc_type: str
    endpoints_count: int
    sections_count: int
    chunks_count: int
    status: str


class EndpointCandidateItem(TypedDict):
    """search_endpoints 가 반환하는 후보 한 건.

    후보 식별에 필요한 최소 필드만 담는다. 파라미터·응답 등 상세 정보와
    snippet 은 포함하지 않는다(상세는 get_endpoint_details 로 조회).
    """

    endpoint_id: str
    method: str
    path: str
    summary: str
    match_type: Literal["keyword", "vector"]


class EndpointSearchResponse(TypedDict):
    """search_endpoints 의 반환 타입."""

    items: list[EndpointCandidateItem]


class Citation(TypedDict):
    """query_rag 응답의 citations 원소 타입.

    미사용: query_rag 도구 제거로 호출부 없음. RAG 답변생성은 호출
    LLM(Claude/ChatGPT)이 담당. FastAPI `/query` 라우트 계약 참고용으로 보존.
    """

    method: str | None
    path: str | None
    snippet: str


class RagAnswer(TypedDict):
    """query_rag 의 반환 타입.

    미사용: query_rag 도구 제거로 호출부 없음. RAG 답변생성은 호출
    LLM(Claude/ChatGPT)이 담당. FastAPI `/query` 라우트 계약 참고용으로 보존.
    """

    answer: str
    citations: list[Citation]
    is_grounded: bool


class ParameterItem(TypedDict):
    """get_endpoint_details 응답의 parameters 원소 타입."""

    name: str
    location: str
    required: bool
    description: str
    schema: dict[str, Any]
    schema_ref: str | None


class RequestBodyItem(TypedDict):
    """get_endpoint_details 응답의 request_body 타입."""

    content_type: str
    required: bool
    schema: dict[str, Any]
    schema_ref: str | None


class ResponseItem(TypedDict):
    """get_endpoint_details 응답의 responses 원소 타입."""

    status_code: str
    content_type: str
    description: str
    schema: dict[str, Any]
    schema_ref: str | None


class EndpointDetails(TypedDict):
    """get_endpoint_details 의 반환 타입.

    `example_code` 는 include_example=True 로 호출했을 때만 키가 존재한다.
    """

    endpoint_id: str
    document_id: str
    method: str
    path: str
    summary: str
    description: str
    tags: list[str]
    parameters: list[ParameterItem]
    request_body: RequestBodyItem | None
    responses: list[ResponseItem]
    example_code: NotRequired[str]


class SchemaFieldItem(TypedDict):
    """resolve_ref 응답의 fields 원소 타입."""

    name: str
    type: str
    required: bool
    description: str


class ResolvedSchemaResult(TypedDict):
    """resolve_ref 의 반환 타입."""

    name: str
    fields: list[SchemaFieldItem]


class TagItem(TypedDict):
    """list_tags 응답의 tags 원소 타입."""

    name: str
    endpoint_count: int


class TagListResult(TypedDict):
    """list_tags 의 반환 타입."""

    tags: list[TagItem]
