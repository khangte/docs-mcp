"""Drive/Notion MCP 도구 통합 테스트 (SPEC 기능 6~9).

`search_documents` / `get_document` / `refresh_index` 세 도구가 실제로 등록되고,
표준 에러 포맷을 지키며, 2단계 후보 압축의 fetch 상한과 프로젝트 격리를
지키는지 검증한다. 소스는 conftest 의 페이크로 주입되므로 실제 HTTP 호출은
발생하지 않는다.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastmcp import FastMCP

from app.mcp_server import create_mcp_server
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION
from app.models.openapi import DEFAULT_PROJECT

DOCUMENT_TOOL_NAMES = {"search_documents", "get_document", "refresh_index"}
_T1 = datetime(2026, 7, 1, 9, 0, 0)


@pytest.fixture()
def mcp_server(app_state) -> FastMCP:
    """페이크 문서 소스를 주입한 AppState 기반 MCP 서버."""
    return create_mcp_server(app_state)


@pytest.fixture()
async def seeded_mcp(
    mcp_server, seed_default_project_sources, fake_drive_source, fake_notion_source
) -> FastMCP:
    """DEFAULT_PROJECT 매핑 + Drive/Notion 페이크에 문서를 넣고 메타 캐시까지 갱신해둔 서버."""
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
    """search_documents 는 query/top_k/source/project 네 파라미터를 노출한다."""
    properties = await _tool_parameters(mcp_server, "search_documents")

    assert set(properties) == {"query", "top_k", "source", "project"}
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
    mcp_server: FastMCP, seed_default_project_sources, fake_drive_source, fake_notion_source
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
    mcp_server: FastMCP, seed_default_project_sources, fake_drive_source, fake_notion_source
) -> None:
    """한 소스만 실패하면 성공 분은 반영되고 실패 소스명이 보고된다."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    fake_notion_source.list_should_fail = True

    payload = _result(await mcp_server.call_tool("refresh_index", arguments={}))

    assert payload["added"] == 1
    assert payload["failed_sources"] == [f"{DEFAULT_PROJECT}/{SOURCE_NOTION}"]


@pytest.mark.asyncio()
async def test_refresh_index_all_failures_return_error_payload(
    mcp_server: FastMCP, seed_default_project_sources, fake_drive_source, fake_notion_source
) -> None:
    """모든 소스가 실패하면 표준 에러 포맷을 반환한다."""
    fake_drive_source.list_should_fail = True
    fake_notion_source.list_should_fail = True

    payload = _result(await mcp_server.call_tool("refresh_index", arguments={}))

    assert payload["error"] is True
    assert payload["code"] == "integration_error"


@pytest.mark.asyncio()
async def test_refresh_index_unknown_source_returns_error_payload(
    mcp_server: FastMCP, seed_default_project_sources
) -> None:
    """알 수 없는 source 는 표준 에러 포맷을 반환한다."""
    payload = _result(
        await mcp_server.call_tool("refresh_index", arguments={"source": "dropbox"})
    )

    assert payload["error"] is True
    assert payload["code"] == "integration_error"


@pytest.mark.asyncio()
async def test_refresh_index_no_project_mapping_returns_error_payload(
    mcp_server: FastMCP,
) -> None:
    """DEFAULT_PROJECT 매핑을 심지 않으면(=소스 미구성) 표준 에러 포맷을 반환한다."""
    payload = _result(await mcp_server.call_tool("refresh_index", arguments={}))

    assert payload["error"] is True
    assert payload["code"] == "integration_error"


# --- 기능 7: search_documents --------------------------------------------------


@pytest.mark.asyncio()
async def test_search_documents_returns_expected_fields(seeded_mcp: FastMCP) -> None:
    """결과 항목은 title/source/project/url/snippet/score 6개 필드를 갖는다."""
    items = _result(await seeded_mcp.call_tool("search_documents", {"query": "로그인"}))["items"]

    assert items
    for item in items:
        assert set(item) == {"title", "source", "project", "url", "snippet", "score"}


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
    mcp_server: FastMCP, seed_default_project_sources, fake_drive_source
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
async def test_search_documents_before_refresh_returns_empty(
    mcp_server: FastMCP, seed_default_project_sources
) -> None:
    """캐시가 비어 있으면(갱신 전) 결과가 없다 — 제약을 계약으로 고정한다.

    소스는 구성돼 있으므로 오류가 아니라 빈 items 여야 한다(미구성과 구별).
    """
    result = _result(await mcp_server.call_tool("search_documents", {"query": "로그인"}))

    assert result["items"] == []


@pytest.mark.asyncio()
async def test_search_documents_unconfigured_returns_error_payload(mcp_server: FastMCP) -> None:
    """소스가 하나도 구성되지 않았으면(=project 매핑 없음) 표준 에러 포맷을 반환한다."""
    payload = _result(await mcp_server.call_tool("search_documents", {"query": "로그인"}))

    assert payload["error"] is True
    assert payload["code"] == "integration_error"
    assert "no document source is configured" in payload["message"]


@pytest.mark.asyncio()
async def test_unconfigured_error_is_consistent_across_tools(mcp_server: FastMCP) -> None:
    """미구성 시 search_documents 와 refresh_index 가 같은 메시지를 돌려준다."""
    search = _result(await mcp_server.call_tool("search_documents", {"query": "로그인"}))
    refresh = _result(await mcp_server.call_tool("refresh_index", arguments={}))

    assert search["message"] == refresh["message"]


# --- SPEC 기능 6: project 필터 및 교차 프로젝트 격리 -----------------------------


@pytest.fixture()
def fake_drive_source_b():
    """프로젝트 B 전용 Drive 페이크."""
    from tests.fixtures.document_sources import FakeDocumentSource

    return FakeDocumentSource(SOURCE_DRIVE)


@pytest.fixture()
def app_state_two_projects(app_state, fake_drive_source, fake_drive_source_b):
    """A(folder-a)/B(folder-b) 두 프로젝트가 서로 다른 페이크로 매핑되도록 재구성한다."""
    fakes_by_folder = {"folder-a": fake_drive_source, "folder-b": fake_drive_source_b}
    app_state.drive_source_builder = lambda folder_id: fakes_by_folder.get(folder_id)
    return app_state


@pytest.fixture()
def two_project_mcp_server(app_state_two_projects) -> FastMCP:
    """A/B 두 프로젝트 Drive 매핑이 구성된 MCP 서버."""
    return create_mcp_server(app_state_two_projects)


async def _seed_two_projects(server: FastMCP) -> None:
    """project A/B 각각에 Drive 폴더 매핑을 등록한다."""
    await server.call_tool(
        "register_drive_source", arguments={"project": "A", "folder_id": "folder-a"}
    )
    await server.call_tool(
        "register_drive_source", arguments={"project": "B", "folder_id": "folder-b"}
    )


@pytest.mark.asyncio()
async def test_search_documents_project_filter_excludes_other_project(
    two_project_mcp_server: FastMCP, fake_drive_source, fake_drive_source_b
) -> None:
    """search_documents(query, project="A") 결과에 B 문서가 없다."""
    await _seed_two_projects(two_project_mcp_server)
    fake_drive_source.put("d1", "로그인 A 문서", "A 본문", modified_at=_T1)
    fake_drive_source_b.put("d2", "로그인 B 문서", "B 본문", modified_at=_T1)
    await two_project_mcp_server.call_tool("refresh_index", arguments={})
    fake_drive_source.reset_counts()
    fake_drive_source_b.reset_counts()

    items = _result(
        await two_project_mcp_server.call_tool(
            "search_documents", {"query": "로그인", "project": "A"}
        )
    )["items"]

    assert {i["title"] for i in items} == {"로그인 A 문서"}
    assert fake_drive_source_b.fetch_call_count == 0


@pytest.mark.asyncio()
async def test_refresh_index_project_filter_skips_other_project_list_files(
    two_project_mcp_server: FastMCP, fake_drive_source, fake_drive_source_b
) -> None:
    """refresh_index(project="A") 는 B 의 어댑터 list_files() 를 호출하지 않는다."""
    await _seed_two_projects(two_project_mcp_server)

    await two_project_mcp_server.call_tool("refresh_index", arguments={"project": "A"})

    assert fake_drive_source_b.list_call_count == 0


@pytest.mark.asyncio()
async def test_refresh_index_deleting_in_project_a_keeps_project_b_row(
    two_project_mcp_server: FastMCP, fake_drive_source, fake_drive_source_b
) -> None:
    """A 소스에서 문서가 삭제되면 A 행만 removed 되고 B 의 같은 external_id 행은 남는다."""
    await _seed_two_projects(two_project_mcp_server)
    fake_drive_source.put("shared", "A 문서", "A 본문", modified_at=_T1)
    fake_drive_source_b.put("shared", "B 문서", "B 본문", modified_at=_T1)
    await two_project_mcp_server.call_tool("refresh_index", arguments={})

    fake_drive_source.remove("shared")
    payload = _result(
        await two_project_mcp_server.call_tool(
            "refresh_index", arguments={"project": "A"}
        )
    )

    assert payload["removed"] == 1
    items = _result(
        await two_project_mcp_server.call_tool(
            "search_documents", {"query": "B", "project": "B"}
        )
    )["items"]
    assert any(i["title"] == "B 문서" for i in items)


@pytest.mark.asyncio()
async def test_register_drive_source_change_is_reflected_without_restart(
    two_project_mcp_server: FastMCP, fake_drive_source, fake_drive_source_b
) -> None:
    """서버 재시작 없이 register_drive_source 로 폴더를 바꾸면 다음 호출부터 새 폴더를 쓴다.

    SPEC 377행 검증 기준. `AppState` 는 Drive/Notion 어댑터를 고정 dict 로
    들고 있지 않고, 매 MCP 도구 호출마다 `ProjectSourceResolver` 가
    `project_drive_source` 를 그 요청의 세션으로 새로 조회해 어댑터를
    만든다. 그래서 같은 프로세스를 재시작하지 않아도 매핑을 바꾸면
    다음 refresh_index/search_documents 호출이 즉시 새 폴더를 본다.
    """
    await two_project_mcp_server.call_tool(
        "register_drive_source", arguments={"project": "A", "folder_id": "folder-a"}
    )
    fake_drive_source.put("old", "폴더 A 문서", "A 본문", modified_at=_T1)
    first_refresh = _result(
        await two_project_mcp_server.call_tool("refresh_index", arguments={"project": "A"})
    )
    assert first_refresh["added"] == 1

    # 서버를 재시작하지 않고 프로젝트 A 의 매핑만 폴더 B 로 교체한다.
    await two_project_mcp_server.call_tool(
        "register_drive_source", arguments={"project": "A", "folder_id": "folder-b"}
    )
    fake_drive_source_b.put("new", "폴더 B 문서", "B 본문", modified_at=_T1)

    second_refresh = _result(
        await two_project_mcp_server.call_tool("refresh_index", arguments={"project": "A"})
    )

    # 재배선 없이도 다음 호출이 새 폴더(folder-b, 즉 fake_drive_source_b)를 본다.
    assert fake_drive_source_b.list_call_count == 1
    assert second_refresh["added"] == 1
    items = _result(
        await two_project_mcp_server.call_tool(
            "search_documents", {"query": "폴더", "project": "A"}
        )
    )["items"]
    assert {i["title"] for i in items} == {"폴더 B 문서"}


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
