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
    project: str
    source_url: str | None
    endpoints_count: int
    indexed_at: str | None


class RegisterDocumentResult(TypedDict):
    """register_document 의 반환 타입."""

    document_id: str
    title: str
    version: str
    doc_type: str
    project: str
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
    match_type: Literal["keyword", "vector", "both"]


class EndpointSearchResponse(TypedDict):
    """search_endpoints 의 반환 타입."""

    items: list[EndpointCandidateItem]


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
    """resolve_ref 의 반환 타입.

    `document_id` 는 스키마가 속한 문서를 밝혀, 여러 문서에 동명 스키마가 있을 때
    어느 문서의 것을 받았는지 확인할 수 있게 한다.
    """

    name: str
    document_id: str
    fields: list[SchemaFieldItem]


class TagItem(TypedDict):
    """list_tags 응답의 tags 원소 타입."""

    name: str
    endpoint_count: int


class TagListResult(TypedDict):
    """list_tags 의 반환 타입."""

    tags: list[TagItem]


class DocumentSearchItemPayload(TypedDict):
    """search_documents 가 반환하는 결과 한 건.

    score 는 제목 매칭(1단계)과 본문 매칭(2단계)을 합산한 0.0~1.0 값이다.
    """

    title: str
    source: Literal["drive", "notion"]
    project: str
    url: str
    snippet: str
    score: float
    version: str | None


class DocumentSearchResponse(TypedDict):
    """search_documents 의 반환 타입."""

    items: list[DocumentSearchItemPayload]


class DocumentContentPayload(TypedDict):
    """get_document 의 반환 타입(fetch 시점의 최신 원문)."""

    title: str
    source: Literal["drive", "notion"]
    url: str
    content: str
    version: str | None


class RegisteredResyncResult(TypedDict):
    """include_registered=True 일 때 URL 기반 ApiDocument 재동기화 집계."""

    total: int
    reindexed: int
    skipped: int
    failed: list[str]


class RefreshIndexResult(TypedDict):
    """refresh_index 의 반환 타입.

    `failed_sources` 는 부분 실패한 소스 이름 목록이다. 비어 있으면 전부 성공.
    `registered` 는 include_registered=True 로 호출했을 때만 키가 존재한다.
    """

    synced: int
    added: int
    updated: int
    removed: int
    failed_sources: list[str]
    registered: NotRequired[RegisteredResyncResult]


class DriveSourceItem(TypedDict):
    """list_drive_sources 가 반환하는 매핑 한 건."""

    project: str
    folder_id: str
    created_at: str
    updated_at: str


class NotionSourceItem(TypedDict):
    """list_notion_sources 가 반환하는 매핑 한 건.

    `kind` 가 `"page"` 이면 `database_id` 필드에는 page_id 가 담긴다(값
    컬럼을 database/page 가 공유).
    """

    project: str
    database_id: str
    kind: Literal["database", "page"]
    created_at: str
    updated_at: str


class DriveSourceListResult(TypedDict):
    """list_drive_sources 의 반환 타입."""

    items: list[DriveSourceItem]


class NotionSourceListResult(TypedDict):
    """list_notion_sources 의 반환 타입."""

    items: list[NotionSourceItem]


class RegisterDriveSourceResult(TypedDict):
    """register_drive_source 의 반환 타입."""

    project: str
    folder_id: str
    status: Literal["created", "updated"]


class RegisterNotionSourceResult(TypedDict):
    """register_notion_source 의 반환 타입."""

    project: str
    database_id: str
    status: Literal["created", "updated"]


class RegisterNotionPageResult(TypedDict):
    """register_notion_page 의 반환 타입."""

    project: str
    page_id: str
    status: Literal["created", "updated"]


class RemoveDriveSourceResult(TypedDict):
    """remove_drive_source 의 반환 타입."""

    project: str
    removed: bool


class RemoveNotionSourceResult(TypedDict):
    """remove_notion_source 의 반환 타입."""

    project: str
    removed: bool
