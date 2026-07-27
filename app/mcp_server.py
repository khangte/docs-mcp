"""MCP (Model Context Protocol) 서버 진입점.

FastMCP를 사용하여 기존 RAG 및 검색 서비스를 Claude와 같은 LLM에게 도구로 제공한다.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from fastmcp import FastMCP

from app.api.dependencies import AppState, build_services, rebuild_vector_index
from app.bootstrap import bootstrap_app_state
from app.core.db import managed_session
from app.core.errors import EndpointNotFoundError
from app.core.config import get_settings
from app.core.logging import get_logger
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


def create_mcp_server(app_state: AppState) -> FastMCP:
    """FastMCP 서버 인스턴스를 생성하고 도구들을 등록한다."""
    mcp = FastMCP("docs-mcp")
    session_factory = app_state.session_factory

    @mcp.tool()
    async def list_documents() -> list[dict[str, Any]]:
        """등록된 모든 OpenAPI 문서의 요약 목록을 반환한다."""
        def _sync() -> list[dict[str, Any]]:
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
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def register_document(
        source_url: str | None = None,
        raw_document: str | dict[str, Any] | None = None,
        title_override: str | None = None,
    ) -> dict[str, Any]:
        """신규 OpenAPI 문서를 등록한다. URL 또는 원문 중 하나를 제공해야 한다."""
        if isinstance(raw_document, dict):
            raw_document = json.dumps(raw_document)
        raw_doc_captured = raw_document

        def _sync() -> dict[str, Any]:
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
            return _run_bundle(app_state, _inner)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def search_endpoints(
        query: str,
        top_k: int = 5,
        mode: str = "hybrid",
        document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """자연어로 API 엔드포인트를 검색한다. 하이브리드/키워드/벡터 모드를 지원한다."""
        def _sync() -> list[dict[str, Any]]:
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
            return _run_bundle(app_state, _inner)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def query_rag(
        question: str,
        top_k: int = 5,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """API 명세에 대해 자연어로 질문하고 RAG 기반의 답변을 받는다."""
        def _sync() -> dict[str, Any]:
            def _inner(bundle):
                result = bundle.rag_service.answer(
                    question=question,
                    top_k=top_k,
                    document_id=document_id,
                )
                return {
                    "answer": result.answer,
                    "citations": [
                        {"method": c.method, "path": c.path, "snippet": c.snippet}
                        for c in result.citations
                    ],
                    "is_grounded": result.is_grounded,
                }
            return _run_bundle(app_state, _inner)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def get_endpoint_details(endpoint_id: str) -> dict[str, Any]:
        """특정 엔드포인트의 상세 정보(파라미터, 요청/응답 스펙)와 호출 예시 코드를 조회한다."""
        def _sync() -> dict[str, Any]:
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
            return _run_bundle(app_state, _inner)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.resource("document://{document_id}/raw")
    async def get_raw_document(document_id: str) -> str:
        """등록된 특정 OpenAPI 문서의 원문(JSON/YAML)을 반환한다."""
        def _sync() -> str:
            with managed_session(session_factory) as session:
                repo = DocumentRepository(session)
                doc = repo.get(document_id)
                if not doc:
                    raise EndpointNotFoundError(document_id)
                return doc.raw_text
        return await anyio.to_thread.run_sync(_sync)

    return mcp


def main() -> None:
    """CLI 진입점."""
    from app.core.config import get_settings
    cfg = get_settings()
    app_state = bootstrap_app_state(cfg)
    rebuild_vector_index(app_state)
    mcp_server = create_mcp_server(app_state)
    mcp_server.run()


if __name__ == "__main__":
    main()
