"""MCP 도구 응답 스키마.

각 @mcp.tool() 함수가 실제로 반환하는 dict 구조를 TypedDict로 명시한다.
"""

from __future__ import annotations

from typing import TypedDict


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
    source_url: str | None
    endpoints_count: int
    indexed_at: str | None


class RegisterDocumentResult(TypedDict):
    """register_document 의 반환 타입."""

    document_id: str
    title: str
    version: str
    endpoints_count: int
    chunks_count: int
    status: str


class EndpointSearchResult(TypedDict):
    """search_endpoints 가 반환하는 리스트의 원소 타입."""

    endpoint_id: str
    method: str | None
    path: str | None
    summary: str | None
    score: float
    snippet: str


class Citation(TypedDict):
    """query_rag 응답의 citations 원소 타입."""

    method: str | None
    path: str | None
    snippet: str


class RagAnswer(TypedDict):
    """query_rag 의 반환 타입."""

    answer: str
    citations: list[Citation]
    is_grounded: bool


class EndpointDetails(TypedDict):
    """get_endpoint_details 의 반환 타입."""

    endpoint_id: str
    method: str
    path: str
    summary: str | None
    description: str | None
    example_code: str
