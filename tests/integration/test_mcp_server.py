"""MCP 서버 통합 테스트."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP

from app.mcp_server import create_mcp_server


@pytest.fixture()
def mcp_server(app_state) -> FastMCP:
    """테스트용 AppState를 사용하는 MCP 서버 인스턴스."""
    return create_mcp_server(app_state)


@pytest.mark.asyncio()
async def test_mcp_list_documents_empty(mcp_server: FastMCP):
    """문서가 없을 때 list_documents 도구가 빈 목록을 반환하는지 확인."""
    _, raw_result = await mcp_server.call_tool("list_documents", arguments={})
    # FastMCP는 list를 반환하는 도구의 결과를 {'result': [...]} 로 감쌈
    assert raw_result["result"] == []


@pytest.mark.asyncio()
async def test_mcp_register_and_search(mcp_server: FastMCP, sample_openapi_3):
    """문서를 등록하고 검색 도구를 통해 결과를 확인."""
    # 1. 문서 등록 (dict 반환 -> 그대로)
    _, reg_data = await mcp_server.call_tool(
        "register_document",
        arguments={"raw_document": sample_openapi_3}
    )
    assert reg_data["status"] == "registered"
    assert reg_data["endpoints_count"] > 0

    # 2. 엔드포인트 검색 (list 반환 -> {'result': [...]})
    _, search_raw = await mcp_server.call_tool(
        "search_endpoints",
        arguments={"query": "pet", "top_k": 3}
    )
    search_data = search_raw["result"]
    assert len(search_data) > 0
    assert any("pet" in r["path"].lower() or "pet" in r["summary"].lower() for r in search_data)


@pytest.mark.asyncio()
async def test_mcp_query_rag(mcp_server: FastMCP, sample_openapi_3):
    """RAG 질의응답 도구 확인."""
    # 문서 등록
    await mcp_server.call_tool(
        "register_document",
        arguments={"raw_document": sample_openapi_3}
    )

    # RAG 질의 (dict 반환 -> 그대로)
    _, query_data = await mcp_server.call_tool(
        "query_rag",
        arguments={"question": "How can I find a pet by ID?"}
    )
    assert "answer" in query_data
    assert query_data["is_grounded"] is True


@pytest.mark.asyncio()
async def test_mcp_get_details(mcp_server: FastMCP, sample_openapi_3):
    """엔드포인트 상세 정보 및 예시 코드 조회 확인."""
    # 문서 등록
    await mcp_server.call_tool(
        "register_document",
        arguments={"raw_document": sample_openapi_3}
    )

    # 검색
    _, search_raw = await mcp_server.call_tool(
        "search_endpoints",
        arguments={"query": "getPetById"}
    )
    search_data = search_raw["result"]
    endpoint_id = search_data[0]["endpoint_id"]

    # 상세 정보 (dict 반환 -> 그대로)
    _, detail_data = await mcp_server.call_tool(
        "get_endpoint_details",
        arguments={"endpoint_id": endpoint_id}
    )
    assert detail_data["endpoint_id"] == endpoint_id
    assert "example_code" in detail_data
