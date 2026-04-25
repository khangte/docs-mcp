"""MCP (Model Context Protocol) 서버 진입점.

FastMCP를 사용하여 기존 RAG 및 검색 서비스를 Claude와 같은 LLM에게 도구로 제공한다.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.api.dependencies import AppState, build_services, rebuild_vector_index
from src.core.config import get_settings
from src.core.db import create_db_engine
from src.models.openapi import create_all
from src.services.ingestor.openapi_fetcher import HttpOpenAPIFetcher
from src.services.search.search_service import SearchOptions

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docs_mcp.mcp")

# 전역 상태 초기화
cfg = get_settings()
engine = create_db_engine(cfg.database_url)
create_all(engine)
app_state = AppState.from_engine(
    engine=engine,
    fetcher=HttpOpenAPIFetcher(),
    embedding_dim=cfg.embedding_dim,
    hybrid_alpha=cfg.hybrid_alpha,
)
rebuild_vector_index(app_state)

# FastMCP 서버 인스턴스
mcp = FastMCP("docs-mcp")


@contextmanager
def service_context():
    """요청 단위 서비스 번들을 관리하는 컨텍스트 매니저."""
    bundle_iter = build_services(app_state)
    bundle = next(bundle_iter)
    try:
        yield bundle
    finally:
        try:
            next(bundle_iter)
        except StopIteration:
            pass


@mcp.tool()
def list_documents() -> list[dict[str, Any]]:
    """등록된 모든 OpenAPI 문서의 요약 목록을 반환한다."""
    with service_context() as services:
        docs = services.document_repo.list_all()
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


@mcp.tool()
def register_document(
    source_url: str | None = None,
    raw_document: str | None = None,
    title_override: str | None = None,
) -> dict[str, Any]:
    """신규 OpenAPI 문서를 등록한다. URL 또는 원문 중 하나를 제공해야 한다."""
    with service_context() as services:
        result = services.sync_service.register(
            source_url=source_url,
            raw_document=raw_document,
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


@mcp.tool()
def search_endpoints(
    query: str,
    top_k: int = 5,
    mode: str = "hybrid",
    document_id: str | None = None,
) -> list[dict[str, Any]]:
    """자연어로 API 엔드포인트를 검색한다. 하이브리드/키워드/벡터 모드를 지원한다."""
    with service_context() as services:
        options = SearchOptions(top_k=top_k, mode=mode, document_id=document_id)
        results = services.search_service.search(query, options)
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


@mcp.tool()
def query_rag(
    question: str,
    top_k: int = 5,
    document_id: str | None = None,
) -> dict[str, Any]:
    """API 명세에 대해 자연어로 질문하고 RAG 기반의 답변을 받는다."""
    with service_context() as services:
        result = services.rag_service.answer(
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


@mcp.tool()
def get_endpoint_details(endpoint_id: str) -> dict[str, Any]:
    """특정 엔드포인트의 상세 정보(파라미터, 요청/응답 스펙)와 호출 예시 코드를 조회한다."""
    with service_context() as services:
        endpoint = services.endpoint_repo.get(endpoint_id)
        if not endpoint:
            return {"error": "Endpoint not found"}

        # 기본 정보 조립
        details = {
            "endpoint_id": endpoint.id,
            "method": endpoint.method,
            "path": endpoint.path,
            "summary": endpoint.summary,
            "description": endpoint.description,
        }

        # 예시 코드 생성 (curl이 기본)
        example = services.example_service.generate(endpoint_id, "curl")
        details["example_code"] = example["code"]

        return details


@mcp.resource("document://{document_id}/raw")
def get_raw_document(document_id: str) -> str:
    """등록된 특정 OpenAPI 문서의 원문(JSON/YAML)을 반환한다."""
    with service_context() as services:
        doc = services.document_repo.get(document_id)
        if not doc:
            return "Document not found"
        return doc.raw_text


if __name__ == "__main__":
    mcp.run()
