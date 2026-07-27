"""MCP (Model Context Protocol) 서버 진입점.

FastMCP를 사용하여 기존 RAG 및 검색 서비스를 Claude와 같은 LLM에게 도구로 제공한다.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from app.api.dependencies import AppState, build_services
from app.bootstrap import bootstrap_app_state
from app.core.db import managed_session
from app.core.errors import DomainError, DocumentNotFoundError, EndpointNotFoundError, IntegrationError
from app.core.config import get_settings
from app.core.logging import get_logger
from app.mcp_types import (
    Citation,
    DocumentSummary,
    EndpointDetails,
    EndpointSearchResult,
    ErrorPayload,
    RagAnswer,
    RegisterDocumentResult,
)
from app.repositories.document_repository import DocumentRepository
from app.services.search.search_service import SearchOptions

_LOG = get_logger("docs_mcp.mcp", level=get_settings().log_level)


def _run_bundle(app_state: AppState, fn):
    """build_services 번들을 열고 fn(bundle)을 실행한 뒤 세션을 닫는다."""
    bundle_iter = build_services(app_state)
    bundle = next(bundle_iter)
    try:
        return fn(bundle)
    finally:
        try:
            next(bundle_iter)
        except StopIteration:
            pass


def to_error_payload(error: DomainError | IntegrationError) -> ErrorPayload:
    """DomainError/IntegrationError를 클라이언트에 노출할 에러 페이로드로 변환한다.

    스택트레이스는 서버 로그에만 남기고, 클라이언트에는 code/message만 전달한다.
    """
    code = error.code if isinstance(error, DomainError) else "integration_error"
    _LOG.error("mcp tool error: %s", code, exc_info=error)
    return {"error": True, "code": code, "message": str(error)}


def create_mcp_server(app_state: AppState) -> FastMCP:
    """FastMCP 서버 인스턴스를 생성하고 도구들을 등록한다."""
    mcp = FastMCP("docs-mcp")
    session_factory = app_state.session_factory

    @mcp.tool()
    async def list_documents() -> list[DocumentSummary] | ErrorPayload:
        """등록된 모든 OpenAPI 문서의 요약 목록을 반환한다.

        Returns:
            각 원소가 document_id, title, version, source_url, endpoints_count,
            indexed_at 필드를 갖는 리스트. 조회 중 도메인/외부 연동 오류가
            발생하면 error/code/message 필드를 담은 ErrorPayload를 대신 반환한다.
        """
        def _sync() -> list[DocumentSummary] | ErrorPayload:
            try:
                with managed_session(session_factory) as session:
                    repo = DocumentRepository(session)
                    docs = repo.list_all()
                    return [
                        {
                            "document_id": d.id,
                            "title": d.title,
                            "version": d.version,
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
        source_url: str | None = None,
        raw_document: str | dict[str, Any] | None = None,
        title_override: str | None = None,
    ) -> RegisterDocumentResult | ErrorPayload:
        """신규 OpenAPI 문서를 등록한다. URL 또는 원문 중 하나를 제공해야 한다.

        Args:
            source_url: 문서를 가져올 URL. raw_document를 생략할 때 사용.
            raw_document: OpenAPI 문서 원문(JSON 문자열 또는 dict). source_url을
                생략할 때 사용.
            title_override: 문서 제목을 강제로 지정하고 싶을 때 사용, 생략 시
                문서 자체의 제목을 사용한다.

        Returns:
            document_id, title, version, endpoints_count, chunks_count, status
            필드를 갖는 dict. 이미 등록된 문서이거나 파싱에 실패하는 등 도메인
            오류가 발생하면 error/code/message 필드를 담은 ErrorPayload를 대신
            반환한다.
        """
        if isinstance(raw_document, dict):
            raw_document = json.dumps(raw_document)
        raw_doc_captured = raw_document

        def _sync() -> RegisterDocumentResult | ErrorPayload:
            def _inner(bundle):
                result = bundle.sync_service.register(
                    source_url=source_url,
                    raw_document=raw_doc_captured,
                    title_override=title_override,
                )
                doc = result.document
                return {
                    "document_id": doc.id,
                    "title": doc.title,
                    "version": doc.version,
                    "endpoints_count": result.endpoints_count,
                    "chunks_count": result.chunks_count,
                    "status": result.status,
                }
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def search_endpoints(
        query: str,
        top_k: int = 5,
        mode: str = "hybrid",
        document_id: str | None = None,
    ) -> list[EndpointSearchResult] | ErrorPayload:
        """자연어로 API 엔드포인트를 검색한다. 하이브리드/키워드/벡터 모드를 지원한다.

        Args:
            query: 검색할 자연어 질의.
            top_k: 반환할 최대 결과 수.
            mode: "hybrid" | "keyword" | "vector" 중 하나.
            document_id: 특정 문서로 검색 범위를 제한하고 싶을 때 지정.

        Returns:
            각 원소가 endpoint_id, method, path, summary, score, snippet 필드를
            갖는 리스트, score가 높을수록 관련도가 높다. 검색 중 도메인/외부
            연동 오류가 발생하면 error/code/message 필드를 담은 ErrorPayload를
            대신 반환한다.
        """
        def _sync() -> list[EndpointSearchResult] | ErrorPayload:
            def _inner(bundle):
                options = SearchOptions(top_k=top_k, mode=mode, document_id=document_id)
                results = bundle.search_service.search(query, options)
                return [
                    {
                        "endpoint_id": r.endpoint_id,
                        "method": r.method,
                        "path": r.path,
                        "summary": r.summary,
                        "score": round(r.score, 4),
                        "snippet": r.snippet,
                    }
                    for r in results
                ]
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def query_rag(
        question: str,
        top_k: int = 5,
        document_id: str | None = None,
    ) -> RagAnswer | ErrorPayload:
        """API 명세에 대해 자연어로 질문하고 RAG 기반의 답변을 받는다.

        Args:
            question: API 명세에 대해 묻고 싶은 자연어 질문.
            top_k: 답변 생성에 근거로 사용할 최대 검색 결과 수.
            document_id: 특정 문서로 질의 범위를 제한하고 싶을 때 지정.

        Returns:
            answer(생성된 답변), citations(근거가 된 method/path/snippet 목록),
            is_grounded(답변이 실제 문서 근거에 기반했는지 여부)를 담은 dict.
            질의 처리 중 도메인/외부 연동 오류가 발생하면 error/code/message
            필드를 담은 ErrorPayload를 대신 반환한다.
        """
        def _sync() -> RagAnswer | ErrorPayload:
            def _inner(bundle):
                result = bundle.rag_service.answer(
                    question=question,
                    top_k=top_k,
                    document_id=document_id,
                )
                citations: list[Citation] = [
                    {"method": c.method, "path": c.path, "snippet": c.snippet}
                    for c in result.citations
                ]
                return {
                    "answer": result.answer,
                    "citations": citations,
                    "is_grounded": result.is_grounded,
                }
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def get_endpoint_details(endpoint_id: str) -> EndpointDetails | ErrorPayload:
        """특정 엔드포인트의 상세 정보(파라미터, 요청/응답 스펙)와 호출 예시 코드를 조회한다.

        Args:
            endpoint_id: search_endpoints 등에서 얻은 엔드포인트 식별자.

        Returns:
            endpoint_id, method, path, summary, description, example_code(curl
            예시 코드) 필드를 갖는 dict. endpoint_id가 존재하지 않거나 예시
            생성 중 오류가 발생하면 error/code/message 필드를 담은
            ErrorPayload를 대신 반환한다.
        """
        def _sync() -> EndpointDetails | ErrorPayload:
            def _inner(bundle):
                endpoint = bundle.endpoint_repo.get(endpoint_id)
                if not endpoint:
                    raise EndpointNotFoundError(endpoint_id)
                details = {
                    "endpoint_id": endpoint.id,
                    "method": endpoint.method,
                    "path": endpoint.path,
                    "summary": endpoint.summary,
                    "description": endpoint.description,
                }
                example = bundle.example_service.generate(endpoint_id, "curl")
                details["example_code"] = example["code"]
                return details
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.resource("document://{document_id}/raw")
    async def get_raw_document(document_id: str) -> str:
        """등록된 특정 OpenAPI 문서의 원문(JSON/YAML)을 반환한다."""
        def _sync() -> str:
            with managed_session(session_factory) as session:
                repo = DocumentRepository(session)
                doc = repo.get(document_id)
                if not doc:
                    raise DocumentNotFoundError(document_id)
                return doc.raw_text
        return await anyio.to_thread.run_sync(_sync)

    return mcp


def main() -> None:
    """CLI 진입점."""
    from app.core.config import get_settings
    cfg = get_settings()
    app_state = bootstrap_app_state(cfg)
    mcp_server = create_mcp_server(app_state)
    mcp_server.run()


if __name__ == "__main__":
    main()
