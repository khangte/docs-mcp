"""MCP 도구 결과를 응답 dict(TypedDict)로 변환하는 순수 함수 모음."""

from __future__ import annotations

from typing import Literal, cast

from app.mcp.types import (
    DocumentContentPayload,
    DocumentSearchItemPayload,
    DocumentSearchResponse,
    DriveSourceItem,
    EndpointDetails,
    MatchedChunkPayload,
    NotionSourceItem,
    ParameterItem,
    RefreshCoverage,
    RefreshIndexResult,
    RelatedEndpointItem,
    RequestBodyItem,
    ResolvedSchemaResult,
    ResponseItem,
    SchemaFieldItem,
    TagItem,
    TagListResult,
)
from app.models.project_source import ProjectSource
from app.services.documents.document_index_service import RefreshResult
from app.services.documents.document_search_service import (
    DocumentContent,
    DocumentSearchItem,
)
from app.services.endpoints.endpoint_details_service import EndpointDetailsResult
from app.services.schema_resolution.schema_ref_resolver import ResolvedSchema
from app.services.tags.tag_catalog_service import TagSummary


def _to_endpoint_details_payload(result: EndpointDetailsResult) -> EndpointDetails:
    """엔드포인트 상세 결과를 MCP 응답 dict 로 변환한다.

    example_code 는 생성된 경우에만 키를 추가한다(include_example=False 이면
    키 자체가 존재하지 않아야 한다).
    """
    parameters: list[ParameterItem] = [
        {
            "name": p.name,
            "location": p.location,
            "required": p.required,
            "description": p.description,
            "schema": p.schema,
            "schema_ref": p.schema_ref,
        }
        for p in result.parameters
    ]
    request_body: RequestBodyItem | None = None
    if result.request_body is not None:
        request_body = {
            "content_type": result.request_body.content_type,
            "required": result.request_body.required,
            "schema": result.request_body.schema,
            "schema_ref": result.request_body.schema_ref,
        }
    responses: list[ResponseItem] = [
        {
            "status_code": r.status_code,
            "content_type": r.content_type,
            "description": r.description,
            "schema": r.schema,
            "schema_ref": r.schema_ref,
        }
        for r in result.responses
    ]
    related_endpoints: list[RelatedEndpointItem] = [
        {"endpoint_id": r.endpoint_id, "method": r.method, "path": r.path}
        for r in result.related_endpoints
    ]
    payload: EndpointDetails = {
        "endpoint_id": result.endpoint_id,
        "document_id": result.document_id,
        "method": result.method,
        "path": result.path,
        "summary": result.summary,
        "description": result.description,
        "tags": result.tags,
        "parameters": parameters,
        "request_body": request_body,
        "responses": responses,
        "referenced_schema_refs": result.referenced_schema_refs,
        "related_endpoints": related_endpoints,
    }
    if result.example_code is not None:
        payload["example_code"] = result.example_code
    return payload


def _to_resolved_schema_payload(resolved: ResolvedSchema) -> ResolvedSchemaResult:
    """펼쳐진 스키마를 MCP 응답 dict 로 변환한다."""
    fields: list[SchemaFieldItem] = [
        {
            "name": f.name,
            "type": f.type,
            "required": f.required,
            "description": f.description,
        }
        for f in resolved.fields
    ]
    return {
        "name": resolved.name,
        "document_id": resolved.document_id,
        "fields": fields,
    }


def _to_tag_list_payload(summaries: list[TagSummary]) -> TagListResult:
    """태그 집계 결과를 MCP 응답 dict 로 변환한다."""
    tags: list[TagItem] = [
        {"name": s.name, "endpoint_count": s.endpoint_count} for s in summaries
    ]
    return {"tags": tags}


def _to_document_search_payload(items: list[DocumentSearchItem]) -> DocumentSearchResponse:
    """협업 문서 검색 결과를 MCP 응답 dict 로 변환한다."""
    payload_items: list[DocumentSearchItemPayload] = [
        {
            "title": item.title,
            "source": cast(Literal["drive", "notion"], item.source),
            "project": item.project,
            "url": item.url,
            "snippet": item.snippet,
            "score": item.score,
            "version": item.version,
            "snippet_as_of": item.snippet_as_of.isoformat() if item.snippet_as_of else None,
            "external_id": item.external_id,
            "matched_chunks": [
                cast(
                    MatchedChunkPayload,
                    {
                        "chunk_id": chunk.chunk_id,
                        "text": chunk.text,
                        "chunk_type": chunk.chunk_type,
                        "arm": chunk.arm,
                    },
                )
                for chunk in item.matched_chunks
            ],
            "match_reasons": list(item.match_reasons),
            "modified_at": item.modified_at.isoformat() if item.modified_at else None,
            "indexed": item.indexed,
            "mime_type": item.mime_type,
            "owner": item.owner,
            "folder_path": item.folder_path,
            "folder_id": item.folder_id,
        }
        for item in items
    ]
    return {"items": payload_items}


def _to_document_content_payload(content: DocumentContent) -> DocumentContentPayload:
    """협업 문서 원문 조회 결과를 MCP 응답 dict 로 변환한다."""
    return {
        "title": content.title,
        "source": cast(Literal["drive", "notion"], content.source),
        "url": content.url,
        "content": content.content,
        "version": content.version,
        "truncated": content.truncated,
    }


def _to_refresh_payload(result: RefreshResult) -> RefreshIndexResult:
    """메타 캐시 갱신 집계를 MCP 응답 dict 로 변환한다."""
    coverage: RefreshCoverage = {
        "unindexed": result.unindexed,
        "unsupported": result.unsupported,
        "listing_truncated": list(result.listing_truncated),
    }
    return {
        "synced": result.synced,
        "added": result.added,
        "updated": result.updated,
        "removed": result.removed,
        "failed_sources": list(result.failed_sources),
        "coverage": coverage,
    }


def _to_drive_source_item(row: ProjectSource) -> DriveSourceItem:
    """`ProjectSource`(drive) 행을 MCP 응답 dict 로 변환한다."""
    return {
        "project": row.project,
        "folder_id": row.location,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _to_notion_source_item(row: ProjectSource) -> NotionSourceItem:
    """`ProjectSource`(notion) 행을 MCP 응답 dict 로 변환한다."""
    return {
        "project": row.project,
        "database_id": row.location,
        "kind": cast(Literal["database", "page"], row.kind),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
