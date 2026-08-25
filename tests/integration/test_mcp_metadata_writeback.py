"""docs/architect-review/56 §2,§3: write-back 도구 계약과 종단 반영."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from app.mcp.server import create_mcp_server


@pytest.fixture()
async def seeded_mcp(app_state, sample_openapi_3: str) -> FastMCP:
    """샘플 OpenAPI 문서를 등록해둔 MCP 서버."""
    mcp = create_mcp_server(app_state)
    await mcp.call_tool(
        "register_document", arguments={"project": "default", "raw_document": sample_openapi_3}
    )
    return mcp


async def _first_endpoint_id(mcp: FastMCP) -> str:
    """list_tags 대신 search_endpoints 로 첫 후보의 endpoint_id 를 얻는다."""
    result = await mcp.call_tool("search_endpoints", arguments={"query": "pet"})
    items = result.structured_content["result"]["items"]
    assert items
    return items[0]["endpoint_id"]


@pytest.mark.asyncio()
async def test_submit_tool_is_registered(seeded_mcp: FastMCP) -> None:
    """submit_endpoint_metadata 도구가 등록된다."""
    names = {tool.name for tool in await seeded_mcp.list_tools()}
    assert "submit_endpoint_metadata" in names


@pytest.mark.asyncio()
async def test_submit_returns_stored_and_reindexed(seeded_mcp: FastMCP) -> None:
    """저장하면 stored 와 reindexed=True 를 반환한다."""
    endpoint_id = await _first_endpoint_id(seeded_mcp)
    result = await seeded_mcp.call_tool(
        "submit_endpoint_metadata",
        arguments={
            "endpoint_id": endpoint_id,
            "business_description": "반려동물을 등록한다",
            "keywords": ["pet", "등록"],
            "user_phrases": ["반려동물 추가", "add a pet"],
        },
    )
    payload = result.structured_content["result"]
    assert payload["status"] == "stored"
    assert payload["reindexed"] is True
    assert payload["endpoint_id"] == endpoint_id


@pytest.mark.asyncio()
async def test_resubmitting_same_content_returns_already_current(seeded_mcp: FastMCP) -> None:
    """같은 내용 재제출은 already_current 를 반환한다."""
    endpoint_id = await _first_endpoint_id(seeded_mcp)
    arguments = {
        "endpoint_id": endpoint_id,
        "business_description": "반려동물을 등록한다",
        "keywords": ["pet"],
        "user_phrases": ["반려동물 추가"],
    }
    await seeded_mcp.call_tool("submit_endpoint_metadata", arguments=arguments)
    result = await seeded_mcp.call_tool("submit_endpoint_metadata", arguments=arguments)
    assert result.structured_content["result"]["status"] == "already_current"


@pytest.mark.asyncio()
async def test_unknown_endpoint_returns_error_payload(seeded_mcp: FastMCP) -> None:
    """없는 엔드포인트는 에러 페이로드를 반환한다."""
    result = await seeded_mcp.call_tool(
        "submit_endpoint_metadata",
        arguments={
            "endpoint_id": "없는-엔드포인트",
            "business_description": "설명",
            "keywords": [],
            "user_phrases": [],
        },
    )
    payload = result.structured_content["result"]
    assert payload["code"] == "endpoint_not_found"


@pytest.mark.asyncio()
async def test_submit_returns_writeback_disabled_when_switch_off(
    app_state_writeback_disabled, sample_openapi_3: str
) -> None:
    """킬스위치가 꺼지면 submit 은 writeback_disabled 를 반환한다."""
    mcp = create_mcp_server(app_state_writeback_disabled)
    await mcp.call_tool(
        "register_document", arguments={"project": "default", "raw_document": sample_openapi_3}
    )
    endpoint_id = await _first_endpoint_id(mcp)
    result = await mcp.call_tool(
        "submit_endpoint_metadata",
        arguments={
            "endpoint_id": endpoint_id,
            "business_description": "설명",
            "keywords": [],
            "user_phrases": [],
        },
    )
    assert result.structured_content["result"]["code"] == "writeback_disabled"


@pytest.mark.asyncio()
async def test_details_include_missing_hint_when_metadata_absent(seeded_mcp: FastMCP) -> None:
    """메타데이터가 없으면 상세에 missing 힌트가 붙는다."""
    endpoint_id = await _first_endpoint_id(seeded_mcp)
    result = await seeded_mcp.call_tool(
        "get_endpoint_details", arguments={"endpoint_id": endpoint_id}
    )
    hint = result.structured_content["result"]["metadata_request"]
    assert hint["reason"] == "missing"
    assert "submit_endpoint_metadata" in hint["instruction"]


@pytest.mark.asyncio()
async def test_details_omit_hint_after_metadata_stored(seeded_mcp: FastMCP) -> None:
    """저장 후에는 상세에 힌트 키가 없다."""
    endpoint_id = await _first_endpoint_id(seeded_mcp)
    await seeded_mcp.call_tool(
        "submit_endpoint_metadata",
        arguments={
            "endpoint_id": endpoint_id,
            "business_description": "반려동물을 등록한다",
            "keywords": ["pet"],
            "user_phrases": ["반려동물 추가"],
        },
    )
    result = await seeded_mcp.call_tool(
        "get_endpoint_details", arguments={"endpoint_id": endpoint_id}
    )
    assert "metadata_request" not in result.structured_content["result"]


@pytest.mark.asyncio()
async def test_details_omit_hint_when_switch_off(
    app_state_writeback_disabled, sample_openapi_3: str
) -> None:
    """킬스위치가 꺼지면 힌트도 안 붙는다."""
    mcp = create_mcp_server(app_state_writeback_disabled)
    await mcp.call_tool(
        "register_document", arguments={"project": "default", "raw_document": sample_openapi_3}
    )
    endpoint_id = await _first_endpoint_id(mcp)
    result = await mcp.call_tool("get_endpoint_details", arguments={"endpoint_id": endpoint_id})
    assert "metadata_request" not in result.structured_content["result"]


@pytest.mark.asyncio()
async def test_stored_user_phrase_is_searchable(seeded_mcp: FastMCP) -> None:
    """docs/architect-review/56 §4.4: 저장 즉시 청크가 갱신돼 FTS 에 걸린다."""
    endpoint_id = await _first_endpoint_id(seeded_mcp)
    await seeded_mcp.call_tool(
        "submit_endpoint_metadata",
        arguments={
            "endpoint_id": endpoint_id,
            "business_description": "반려동물을 새로 등록한다",
            "keywords": ["펫등록"],
            "user_phrases": ["반려동물 추가하기"],
        },
    )
    result = await seeded_mcp.call_tool("search_endpoints", arguments={"query": "펫등록"})
    items = result.structured_content["result"]["items"]
    assert endpoint_id in {item["endpoint_id"] for item in items}
