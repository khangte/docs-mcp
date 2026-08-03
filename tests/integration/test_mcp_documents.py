"""Drive/Notion MCP 도구 통합 테스트 (SPEC 기능 7~9).

`search_documents` / `get_document` / `refresh_index` 세 도구가 실제로 등록되고,
표준 에러 포맷을 지키며, 2단계 후보 압축의 fetch 상한을 지키는지 검증한다.
소스는 conftest 의 페이크로 주입되므로 실제 HTTP 호출은 발생하지 않는다.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastmcp import FastMCP

from app.mcp_server import create_mcp_server
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION

DOCUMENT_TOOL_NAMES = {"search_documents", "get_document", "refresh_index"}
_T1 = datetime(2026, 7, 1, 9, 0, 0)


@pytest.fixture()
def mcp_server(app_state) -> FastMCP:
    """페이크 문서 소스를 주입한 AppState 기반 MCP 서버."""
    return create_mcp_server(app_state)


@pytest.fixture()
async def seeded_mcp(mcp_server, fake_drive_source, fake_notion_source) -> FastMCP:
    """Drive/Notion 페이크에 문서를 넣고 메타 캐시까지 갱신해둔 서버."""
    fake_drive_source.put("d1", "로그인 인증 설계서", "OAuth 로그인 흐름 상세", modified_at=_T1)
    fake_drive_source.put("d2", "배포 운영 가이드", "무중단 배포 절차", modified_at=_T1)
    fake_notion_source.put("n1", "로그인 회의록", "로그인 정책 논의 기록", modified_at=_T1)
    await mcp_server.call_tool("refresh_index", arguments={})
    fake_drive_source.reset_counts()
    fake_notion_source.reset_counts()
    return mcp_server


async def _tool_names(mcp: FastMCP) -> set[str]:
    """등록된 MCP 도구 이름 집합을 반환한다."""
    return {tool.name for tool in await mcp.list_tools()}


async def _tool_parameters(mcp: FastMCP, name: str) -> dict:
    """특정 MCP 도구의 입력 스키마 properties 를 반환한다."""
    tool = next(t for t in await mcp.list_tools() if t.name == name)
    return tool.parameters["properties"]


def _result(call_result) -> dict:
    """FastMCP 도구 호출 결과에서 구조화 페이로드를 꺼낸다."""
    return call_result.structured_content["result"]


# --- 기능 9: 도구 등록 ---------------------------------------------------------


@pytest.mark.asyncio()
async def test_document_tools_are_registered(mcp_server: FastMCP) -> None:
    """Drive/Notion 신규 도구 3개가 MCP 도구 목록에 등록된다."""
    assert DOCUMENT_TOOL_NAMES <= await _tool_names(mcp_server)


@pytest.mark.asyncio()
async def test_openapi_tools_are_not_broken(mcp_server: FastMCP) -> None:
    """Drive/Notion 도구 추가가 기존 OpenAPI 도구를 밀어내지 않는다."""
    names = await _tool_names(mcp_server)

    assert {"register_document", "search_endpoints", "get_endpoint_details"} <= names


@pytest.mark.asyncio()
async def test_search_documents_signature(mcp_server: FastMCP) -> None:
    """search_documents 는 query/top_k/source 세 파라미터를 노출한다."""
    properties = await _tool_parameters(mcp_server, "search_documents")

    assert set(properties) == {"query", "top_k", "source"}
    assert properties["top_k"]["default"] == 5


@pytest.mark.asyncio()
async def test_get_document_signature(mcp_server: FastMCP) -> None:
    """get_document 는 source/external_id 두 파라미터를 노출한다."""
    assert set(await _tool_parameters(mcp_server, "get_document")) == {
        "source",
        "external_id",
    }


# --- 기능 6: refresh_index -----------------------------------------------------


@pytest.mark.asyncio()
async def test_refresh_index_reports_counts(
    mcp_server: FastMCP, fake_drive_source, fake_notion_source
) -> None:
    """refresh_index 가 synced/added/updated/removed 집계를 반환한다."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    fake_notion_source.put("n1", "회의록", "본문", modified_at=_T1)

    payload = _result(await mcp_server.call_tool("refresh_index", arguments={}))

    assert payload["added"] == 2
    assert payload["synced"] == 2
    assert payload["failed_sources"] == []


@pytest.mark.asyncio()
async def test_refresh_index_partial_failure_is_reported(
    mcp_server: FastMCP, fake_drive_source, fake_notion_source
) -> None:
    """한 소스만 실패하면 성공 분은 반영되고 실패 소스명이 보고된다."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    fake_notion_source.list_should_fail = True

    payload = _result(await mcp_server.call_tool("refresh_index", arguments={}))

    assert payload["added"] == 1
    assert payload["failed_sources"] == [SOURCE_NOTION]


@pytest.mark.asyncio()
async def test_refresh_index_all_failures_return_error_payload(
    mcp_server: FastMCP, fake_drive_source, fake_notion_source
) -> None:
    """모든 소스가 실패하면 표준 에러 포맷을 반환한다."""
    fake_drive_source.list_should_fail = True
    fake_notion_source.list_should_fail = True

    payload = _result(await mcp_server.call_tool("refresh_index", arguments={}))

    assert payload["error"] is True
    assert payload["code"] == "integration_error"


@pytest.mark.asyncio()
async def test_refresh_index_unknown_source_returns_error_payload(
    mcp_server: FastMCP,
) -> None:
    """알 수 없는 source 는 표준 에러 포맷을 반환한다."""
    payload = _result(
        await mcp_server.call_tool("refresh_index", arguments={"source": "dropbox"})
    )

    assert payload["error"] is True
    assert payload["code"] == "integration_error"


# --- 기능 7: search_documents --------------------------------------------------


@pytest.mark.asyncio()
async def test_search_documents_returns_expected_fields(seeded_mcp: FastMCP) -> None:
    """결과 항목은 title/source/url/snippet/score 5개 필드를 갖는다."""
    items = _result(await seeded_mcp.call_tool("search_documents", {"query": "로그인"}))["items"]

    assert items
    for item in items:
        assert set(item) == {"title", "source", "url", "snippet", "score"}


@pytest.mark.asyncio()
async def test_search_documents_finds_title_match(seeded_mcp: FastMCP) -> None:
    """제목에 질의어가 있는 문서가 결과에 포함된다."""
    items = _result(await seeded_mcp.call_tool("search_documents", {"query": "로그인"}))["items"]

    assert "로그인 인증 설계서" in {i["title"] for i in items}


@pytest.mark.asyncio()
async def test_search_documents_no_candidate_skips_fetch(
    seeded_mcp: FastMCP, fake_drive_source, fake_notion_source
) -> None:
    """후보가 없으면 빈 리스트를 반환하고 본문 fetch 를 하지 않는다."""
    result = _result(
        await seeded_mcp.call_tool("search_documents", {"query": "존재하지않는질의어"})
    )

    assert result["items"] == []
    assert fake_drive_source.fetch_call_count == 0
    assert fake_notion_source.fetch_call_count == 0


@pytest.mark.asyncio()
async def test_search_documents_fetch_count_respects_top_k(
    mcp_server: FastMCP, fake_drive_source
) -> None:
    """한 번의 검색이 fetch 하는 문서 수는 top_k 를 넘지 않는다."""
    for index in range(8):
        fake_drive_source.put(f"d{index}", f"로그인 문서 {index}", "로그인 본문", modified_at=_T1)
    await mcp_server.call_tool("refresh_index", arguments={})
    fake_drive_source.reset_counts()

    await mcp_server.call_tool("search_documents", {"query": "로그인", "top_k": 2})

    assert fake_drive_source.fetch_call_count == 2


@pytest.mark.asyncio()
async def test_search_documents_source_filter(
    seeded_mcp: FastMCP, fake_drive_source
) -> None:
    """source 필터를 주면 결과가 해당 출처만 포함한다."""
    items = _result(
        await seeded_mcp.call_tool(
            "search_documents", {"query": "로그인", "source": SOURCE_NOTION}
        )
    )["items"]

    assert {i["source"] for i in items} == {SOURCE_NOTION}
    assert fake_drive_source.fetch_call_count == 0


@pytest.mark.asyncio()
async def test_search_documents_empty_query_returns_error_payload(
    seeded_mcp: FastMCP,
) -> None:
    """빈 질의는 표준 에러 포맷을 반환한다."""
    payload = _result(await seeded_mcp.call_tool("search_documents", {"query": "   "}))

    assert payload["error"] is True
    assert payload["code"] == "validation_error"


@pytest.mark.asyncio()
async def test_search_documents_invalid_top_k_returns_error_payload(
    seeded_mcp: FastMCP,
) -> None:
    """범위를 벗어난 top_k 는 표준 에러 포맷을 반환한다."""
    payload = _result(
        await seeded_mcp.call_tool("search_documents", {"query": "로그인", "top_k": 0})
    )

    assert payload["error"] is True
    assert payload["code"] == "validation_error"


@pytest.mark.asyncio()
async def test_search_documents_unknown_source_returns_error_payload(
    seeded_mcp: FastMCP,
) -> None:
    """알 수 없는 source 는 표준 에러 포맷을 반환한다."""
    payload = _result(
        await seeded_mcp.call_tool(
            "search_documents", {"query": "로그인", "source": "dropbox"}
        )
    )

    assert payload["error"] is True
    assert payload["code"] == "validation_error"


@pytest.mark.asyncio()
async def test_search_documents_before_refresh_returns_empty(mcp_server: FastMCP) -> None:
    """캐시가 비어 있으면(갱신 전) 결과가 없다 — 제약을 계약으로 고정한다.

    소스는 구성돼 있으므로 오류가 아니라 빈 items 여야 한다(미구성과 구별).
    """
    result = _result(await mcp_server.call_tool("search_documents", {"query": "로그인"}))

    assert result["items"] == []


@pytest.mark.asyncio()
async def test_search_documents_unconfigured_returns_error_payload(app_state) -> None:
    """소스가 하나도 구성되지 않았으면 침묵하지 않고 표준 에러 포맷을 반환한다."""
    app_state.document_sources = {}
    server = create_mcp_server(app_state)

    payload = _result(await server.call_tool("search_documents", {"query": "로그인"}))

    assert payload["error"] is True
    assert payload["code"] == "integration_error"
    assert "no document source is configured" in payload["message"]


@pytest.mark.asyncio()
async def test_unconfigured_error_is_consistent_across_tools(app_state) -> None:
    """미구성 시 search_documents 와 refresh_index 가 같은 메시지를 돌려준다."""
    app_state.document_sources = {}
    server = create_mcp_server(app_state)

    search = _result(await server.call_tool("search_documents", {"query": "로그인"}))
    refresh = _result(await server.call_tool("refresh_index", arguments={}))

    assert search["message"] == refresh["message"]


# --- 기능 8: get_document ------------------------------------------------------


@pytest.mark.asyncio()
async def test_get_document_returns_full_content(seeded_mcp: FastMCP) -> None:
    """원문 조회가 제목·출처·URL·본문을 모두 반환한다."""
    payload = _result(
        await seeded_mcp.call_tool(
            "get_document", {"source": SOURCE_DRIVE, "external_id": "d1"}
        )
    )

    assert payload["content"] == "OAuth 로그인 흐름 상세"
    assert payload["title"] == "로그인 인증 설계서"
    assert payload["source"] == SOURCE_DRIVE


@pytest.mark.asyncio()
async def test_get_document_returns_latest_not_cached(
    seeded_mcp: FastMCP, fake_drive_source
) -> None:
    """캐시가 아니라 호출 시점의 최신 원문을 반환한다."""
    fake_drive_source.bodies["d1"] = "수정된 본문"

    payload = _result(
        await seeded_mcp.call_tool(
            "get_document", {"source": SOURCE_DRIVE, "external_id": "d1"}
        )
    )

    assert payload["content"] == "수정된 본문"


@pytest.mark.asyncio()
async def test_get_document_unknown_id_returns_error_payload(seeded_mcp: FastMCP) -> None:
    """존재하지 않는 external_id 는 스택트레이스 없이 표준 에러 포맷을 반환한다."""
    payload = _result(
        await seeded_mcp.call_tool(
            "get_document", {"source": SOURCE_DRIVE, "external_id": "no-such-file"}
        )
    )

    assert payload["error"] is True
    assert payload["code"] == "integration_error"
    assert "Traceback" not in payload["message"]


@pytest.mark.asyncio()
async def test_get_document_unknown_source_returns_error_payload(
    seeded_mcp: FastMCP,
) -> None:
    """알 수 없는 source 는 표준 에러 포맷을 반환한다."""
    payload = _result(
        await seeded_mcp.call_tool(
            "get_document", {"source": "dropbox", "external_id": "d1"}
        )
    )

    assert payload["error"] is True
    assert payload["code"] == "validation_error"
