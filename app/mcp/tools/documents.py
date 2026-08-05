"""문서(OpenAPI/Markdown/CSV, 협업 문서) 관련 MCP 도구 등록."""

from __future__ import annotations

import json
from typing import Any

import anyio
from fastmcp import FastMCP

from app.composition import AppState, ServiceBundle
from app.core.db import managed_session
from app.core.errors import DomainError, IntegrationError
from app.mcp.payloads import _to_document_content_payload, _to_document_search_payload
from app.mcp.tools._common import _run_bundle, to_error_payload
from app.mcp.types import (
    DocumentContentPayload,
    DocumentSearchResponse,
    DocumentSummary,
    ErrorPayload,
    RegisterDocumentResult,
)
from app.repositories.document_repository import DocumentRepository
from app.services.documents.document_search_service import DocumentSearchOptions


def register_document_tools(mcp: FastMCP, app_state: AppState) -> None:
    """문서 관련 MCP 도구(list/register/search_documents/get_document)를 등록한다."""
    session_factory = app_state.session_factory

    @mcp.tool()
    async def list_documents(
        project: str | None = None,
    ) -> list[DocumentSummary] | ErrorPayload:
        """등록된 문서(OpenAPI/Markdown/CSV)의 요약 목록을 반환한다.

        Args:
            project: 특정 프로젝트로 범위를 제한하고 싶을 때 지정. 생략하면
                등록된 모든 프로젝트의 문서를 반환한다(하위 호환).

        Returns:
            각 원소가 document_id, title, version, doc_type, project, source_url,
            endpoints_count, indexed_at 필드를 갖는 리스트. 조회 중 도메인/외부
            연동 오류가 발생하면 error/code/message 필드를 담은 ErrorPayload를
            대신 반환한다.
        """
        def _sync() -> list[DocumentSummary] | ErrorPayload:
            try:
                with managed_session(session_factory) as session:
                    repo = DocumentRepository(session)
                    docs = repo.list_all(project=project)
                    return [
                        {
                            "document_id": d.id,
                            "title": d.title,
                            "version": d.version,
                            "doc_type": d.doc_type,
                            "project": d.project,
                            "source_url": d.source_url,
                            "endpoints_count": len(d.endpoints),
                            "indexed_at": d.indexed_at.isoformat() if d.indexed_at else None,
                        }
                        for d in docs
                    ]
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def register_document(
        project: str,
        source_url: str | None = None,
        raw_document: str | dict[str, Any] | None = None,
        title_override: str | None = None,
        doc_type: str | None = None,
    ) -> RegisterDocumentResult | ErrorPayload:
        """신규 문서를 등록한다. URL 또는 원문 중 하나를 제공해야 한다.

        Args:
            project: 이 문서가 속할 프로젝트 식별자(보통 프로젝트 폴더명).
                이후 검색에서 이 값으로 범위를 좁힌다.
            source_url: 문서를 가져올 URL. raw_document를 생략할 때 사용.
            raw_document: 문서 원문(JSON/YAML/Markdown/CSV 문자열 또는 dict).
                source_url을 생략할 때 사용. pdf/docx는 base64로 인코딩한
                문자열로 전달하고 doc_type을 반드시 명시한다.
            title_override: 문서 제목을 강제로 지정하고 싶을 때 사용, 생략 시
                문서 자체의 제목을 사용한다.
            doc_type: "openapi" | "markdown" | "csv" | "pdf" | "docx" 중 하나,
                생략 시 자동 판별한다(pdf/docx는 자동 판별 대상이 아니므로
                명시가 필수).

        Returns:
            document_id, title, version, doc_type, project, endpoints_count,
            sections_count, chunks_count, status 필드를 갖는 dict. 이미 등록된
            문서이거나 파싱에 실패하는 등 도메인 오류가 발생하면
            error/code/message 필드를 담은 ErrorPayload를 대신 반환한다.
        """
        if isinstance(raw_document, dict):
            raw_document = json.dumps(raw_document)
        raw_doc_captured = raw_document

        def _sync() -> RegisterDocumentResult | ErrorPayload:
            def _inner(bundle):
                result = bundle.sync_service.register(
                    project=project,
                    source_url=source_url,
                    raw_document=raw_doc_captured,
                    title_override=title_override,
                    doc_type=doc_type,
                )
                doc = result.document
                return {
                    "document_id": doc.id,
                    "title": doc.title,
                    "version": doc.version,
                    "doc_type": doc.doc_type,
                    "project": doc.project,
                    "endpoints_count": result.endpoints_count,
                    "sections_count": result.sections_count,
                    "chunks_count": result.chunks_count,
                    "status": result.status,
                }
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def search_documents(
        query: str,
        top_k: int = 5,
        source: str | None = None,
        project: str | None = None,
        query_variants: list[str] | None = None,
    ) -> DocumentSearchResponse | ErrorPayload:
        """팀 협업 문서(Google Drive / Notion)를 자연어·키워드로 검색한다.

        2단계로 동작한다. 먼저 메타 캐시의 제목으로 후보를 추리고, 그 후보
        본문만 원본 API 에서 실시간으로 가져와 스니펫과 점수를 만든다. 따라서
        캐시에 없는 신규 문서는 검색되지 않을 수 있으며, 그럴 때는
        refresh_index 를 먼저 실행한다. OpenAPI 명세 검색은 이 도구가 아니라
        search_endpoints 를 쓴다.

        결과가 0건이거나 기대보다 부족하면, 문서 제목이 질의와 다른 표현을
        쓰고 있을 가능성이 크다(예: "주문조회 API" 질의로 "결제 내역 조회"
        문서를 못 찾음). 이럴 때는 같은 query 로 동의어·영한 혼용·유사
        표현을 query_variants 에 담아 재호출한다.

        Args:
            query: 검색할 자연어 또는 키워드 질의.
            top_k: 반환할 최대 결과 수(1~50). 실시간으로 본문을 가져오는 문서
                수의 상한이기도 하다.
            source: "drive" 또는 "notion" 으로 출처를 한정할 때 지정.
            project: 특정 프로젝트로 검색 범위를 제한하고 싶을 때 지정.
                생략하면 등록된 모든 프로젝트에서 검색한다.
            query_variants: query 와 같은 의미의 동의어·영한 혼용·유사 표현
                목록. 1단계 후보 필터만 넓히고 점수·순위 계산에는 영향을
                주지 않는다 — 여전히 query 원본 토큰과 가장 잘 맞는 문서가
                상위에 온다. 결과 0건 또는 부족 시 재질의할 때 사용.

        Returns:
            items 키에 결과 리스트를 담은 dict. 각 항목은 title, source,
            project, url, snippet, score 필드를 갖는다. 관련 문서가 없으면
            빈 리스트다. 검색 중 도메인/외부 연동 오류가 발생하면
            error/code/message 필드를 담은 ErrorPayload를 대신 반환한다.
        """
        def _sync() -> DocumentSearchResponse | ErrorPayload:
            def _inner(bundle) -> DocumentSearchResponse:
                options = DocumentSearchOptions(
                    top_k=top_k, source=source, project=project, query_variants=query_variants
                )
                items = bundle.document_search_service.search(query, options)
                return _to_document_search_payload(items)
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def get_document(source: str, external_id: str) -> DocumentContentPayload | ErrorPayload:
        """협업 문서 한 건의 전체 원문을 조회한다.

        search_documents 로 찾은 문서의 스니펫만으로 부족할 때 쓴다. 본문은
        캐시하지 않으므로 항상 호출 시점의 최신 내용을 돌려준다.

        Args:
            source: "drive" 또는 "notion".
            external_id: Drive file ID 또는 Notion page ID.

        Returns:
            title, source, url, content 필드를 갖는 dict. 존재하지 않는
            external_id 이거나 권한이 없으면 error/code/message 필드를 담은
            ErrorPayload를 대신 반환한다.
        """
        def _sync() -> DocumentContentPayload | ErrorPayload:
            def _inner(bundle) -> DocumentContentPayload:
                content = bundle.document_search_service.get_document(source, external_id)
                return _to_document_content_payload(content)
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)
