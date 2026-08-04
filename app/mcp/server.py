"""MCP (Model Context Protocol) 서버 진입점.

FastMCP를 사용하여 기존 RAG 및 검색 서비스를 Claude와 같은 LLM에게 도구로 제공한다.
"""

from __future__ import annotations

from fastmcp import FastMCP

from app.composition import AppState
from app.bootstrap import bootstrap_app_state
from app.mcp.tools.documents import register_document_tools
from app.mcp.tools.endpoints import register_endpoint_tools
from app.mcp.tools.sources import register_source_tools


def create_mcp_server(app_state: AppState) -> FastMCP:
    """FastMCP 서버 인스턴스를 생성하고 도구들을 등록한다."""
    mcp = FastMCP("docs-mcp")
    register_document_tools(mcp, app_state)
    register_endpoint_tools(mcp, app_state)
    register_source_tools(mcp, app_state)
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
