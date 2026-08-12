"""MCP 서버 통합 테스트.

SPEC 기능 1~4 의 도구 계약과 기능 9(OpenAPI 범위) 의 도구 구성을 검증한다.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from app.mcp.server import create_mcp_server

OPENAPI_TOOL_NAMES = {
    "list_documents",
    "register_document",
    "search_endpoints",
    "get_endpoint_details",
    "resolve_ref",
    "list_tags",
}


@pytest.fixture()
def mcp_server(app_state) -> FastMCP:
    """테스트용 AppState를 사용하는 MCP 서버 인스턴스."""
    return create_mcp_server(app_state)


@pytest.fixture()
async def seeded_mcp(mcp_server: FastMCP, sample_openapi_3: str) -> FastMCP:
    """샘플 OpenAPI 문서를 등록해둔 MCP 서버."""
    await mcp_server.call_tool(
        "register_document", arguments={"project": "default", "raw_document": sample_openapi_3}
    )
    return mcp_server


async def _tool_names(mcp: FastMCP) -> set[str]:
    """등록된 MCP 도구 이름 집합을 반환한다."""
    return {tool.name for tool in await mcp.list_tools()}


async def _tool_parameters(mcp: FastMCP, name: str) -> dict:
    """특정 MCP 도구의 입력 스키마 properties 를 반환한다."""
    tool = next(t for t in await mcp.list_tools() if t.name == name)
    return tool.parameters["properties"]


async def _first_candidate(mcp: FastMCP, query: str) -> dict:
    """search_endpoints 로 첫 번째 후보를 가져온다."""
    result = await mcp.call_tool("search_endpoints", arguments={"query": query})
    items = result.structured_content["result"]["items"]
    assert items, f"후보가 비어 있음: {query}"
    return items[0]


# --- 기능 9(OpenAPI 범위): 도구 구성 -----------------------------------------


@pytest.mark.asyncio()
async def test_expected_tools_are_registered(mcp_server: FastMCP) -> None:
    """기존 도구 이름이 유지되고 신규 도구 2개가 추가돼 있다."""
    assert OPENAPI_TOOL_NAMES <= await _tool_names(mcp_server)


@pytest.mark.asyncio()
async def test_query_rag_is_not_registered(mcp_server: FastMCP) -> None:
    """query_rag 는 MCP 도구 목록에 나타나지 않는다."""
    assert "query_rag" not in await _tool_names(mcp_server)


@pytest.mark.asyncio()
async def test_search_endpoints_has_no_mode_parameter(mcp_server: FastMCP) -> None:
    """search_endpoints 시그니처에서 mode 파라미터가 제거됐다(Phase 0 결정 5번)."""
    properties = await _tool_parameters(mcp_server, "search_endpoints")

    assert "mode" not in properties
    assert set(properties) == {"query", "top_k", "document_id", "project", "query_variants"}


@pytest.mark.asyncio()
async def test_get_endpoint_details_exposes_include_example(mcp_server: FastMCP) -> None:
    """get_endpoint_details 시그니처에 include_example 이 기본 False 로 노출된다."""
    properties = await _tool_parameters(mcp_server, "get_endpoint_details")

    assert set(properties) == {"endpoint_id", "include_example"}
    assert properties["include_example"]["default"] is False


# --- 기능 1: search_endpoints -----------------------------------------------


@pytest.mark.asyncio()
async def test_search_returns_candidate_items_only(seeded_mcp: FastMCP) -> None:
    """후보 항목은 5개 필드만 갖고 상세 필드/snippet 을 포함하지 않는다."""
    result = await seeded_mcp.call_tool("search_endpoints", arguments={"query": "pet"})
    items = result.structured_content["result"]["items"]

    assert items
    for item in items:
        assert set(item) == {"endpoint_id", "method", "path", "summary", "match_type"}


@pytest.mark.asyncio()
async def test_search_marks_match_type_from_contract_values(seeded_mcp: FastMCP) -> None:
    """후보의 match_type 은 계약값(keyword/vector/both) 중 하나이고 정답 엔드포인트를 찾는다.

    기본 전략은 rrf(키워드+벡터 항상 병렬 실행)라 어느 arm 이 기여했는지는
    임베딩 유사도에 따라 달라질 수 있다 — "both" 도 유효한 결과다
    (`docs/architect-review/07-search-rrf-reevaluation.md` 5.1). fallback 전략의 배타적
    match_type="keyword" 계약은 `test_search_marks_keyword_match_type_under_fallback_strategy`
    가 별도로 고정한다.
    """
    candidate = await _first_candidate(seeded_mcp, "find pet by id")

    assert candidate["match_type"] in ("keyword", "vector", "both")
    assert candidate["path"] == "/pet/{petId}"


@pytest.mark.asyncio()
async def test_search_marks_keyword_match_type_under_fallback_strategy(
    app_state, sample_openapi_3: str
) -> None:
    """롤백 스위치: fallback 전략이면 키워드로 찾은 후보는 match_type="keyword" 로 고정된다."""
    app_state.search_strategy = "fallback"
    mcp = create_mcp_server(app_state)
    await mcp.call_tool(
        "register_document", arguments={"project": "default", "raw_document": sample_openapi_3}
    )

    candidate = await _first_candidate(mcp, "find pet by id")

    assert candidate["match_type"] == "keyword"
    assert candidate["path"] == "/pet/{petId}"


@pytest.mark.asyncio()
async def test_search_empty_query_returns_error_payload(seeded_mcp: FastMCP) -> None:
    """빈 질의는 표준 에러 포맷으로 반환된다."""
    result = await seeded_mcp.call_tool("search_endpoints", arguments={"query": "   "})
    payload = result.structured_content["result"]

    assert payload["error"] is True
    assert payload["code"] == "validation_error"


@pytest.mark.asyncio()
async def test_search_no_match_returns_empty_items(seeded_mcp: FastMCP) -> None:
    """매칭이 전혀 없으면 items 가 빈 리스트다."""
    result = await seeded_mcp.call_tool(
        "search_endpoints", arguments={"query": "zzzzz_nothing_matches_here_xxx"}
    )

    assert result.structured_content["result"]["items"] == []


@pytest.mark.asyncio()
async def test_search_query_variants_widen_keyword_candidate_pool(
    app_state, sample_openapi_3: str
) -> None:
    """query_variants 로 넘긴 동의어도 키워드 arm 후보 필터에 반영된다(docs/12 후보4).

    벡터 arm 은 해시 임베딩(비의미론적)이라 비활성화해 키워드 arm 배선만 검증한다.
    """
    app_state.vector_fallback_enabled = False
    mcp = create_mcp_server(app_state)
    await mcp.call_tool(
        "register_document", arguments={"project": "default", "raw_document": sample_openapi_3}
    )

    without_variants = await mcp.call_tool(
        "search_endpoints", arguments={"query": "강아지를 아이디로 찾기"}
    )
    assert without_variants.structured_content["result"]["items"] == []

    with_variants = await mcp.call_tool(
        "search_endpoints",
        arguments={
            "query": "강아지를 아이디로 찾기",
            "query_variants": ["find pet by id"],
        },
    )
    items = with_variants.structured_content["result"]["items"]

    assert items
    assert any(item["path"] == "/pet/{petId}" for item in items)


# --- 기능 2: get_endpoint_details -------------------------------------------


@pytest.mark.asyncio()
async def test_details_default_has_no_example_code_key(seeded_mcp: FastMCP) -> None:
    """include_example 기본값(False)에서는 example_code 키 자체가 없다."""
    candidate = await _first_candidate(seeded_mcp, "find pet by id")

    result = await seeded_mcp.call_tool(
        "get_endpoint_details", arguments={"endpoint_id": candidate["endpoint_id"]}
    )
    details = result.structured_content["result"]

    assert "example_code" not in details
    assert details["endpoint_id"] == candidate["endpoint_id"]


@pytest.mark.asyncio()
async def test_details_with_include_example_adds_example_code(
    seeded_mcp: FastMCP,
) -> None:
    """include_example=True 면 example_code 가 포함된다."""
    candidate = await _first_candidate(seeded_mcp, "find pet by id")

    result = await seeded_mcp.call_tool(
        "get_endpoint_details",
        arguments={"endpoint_id": candidate["endpoint_id"], "include_example": True},
    )
    details = result.structured_content["result"]

    assert details["example_code"].startswith("curl -X GET")


@pytest.mark.asyncio()
async def test_details_exposes_schema_ref(seeded_mcp: FastMCP) -> None:
    """상세 응답이 schema_ref 를 참조 문자열 그대로 노출한다."""
    candidate = await _first_candidate(seeded_mcp, "add new pet store")

    result = await seeded_mcp.call_tool(
        "get_endpoint_details", arguments={"endpoint_id": candidate["endpoint_id"]}
    )
    details = result.structured_content["result"]

    assert details["request_body"]["schema_ref"] == "#/components/schemas/Pet"
    assert "properties" not in details["request_body"]["schema"]


@pytest.mark.asyncio()
async def test_details_exposes_traversal_hints(seeded_mcp: FastMCP) -> None:
    """상세 응답에 순회 힌트(referenced_schema_refs·related_endpoints)가 실린다.

    서버는 다음 홉을 자동으로 호출하지 않는다 — 힌트는 후보 노출일 뿐이다
    (`docs/architect-review/12-rag-depth-directions.md` 후보2 얇은 버전).
    """
    candidate = await _first_candidate(seeded_mcp, "find pet by id")

    result = await seeded_mcp.call_tool(
        "get_endpoint_details", arguments={"endpoint_id": candidate["endpoint_id"]}
    )
    details = result.structured_content["result"]

    assert details["referenced_schema_refs"] == ["#/components/schemas/Pet"]
    related = {(r["method"], r["path"]) for r in details["related_endpoints"]}
    assert related  # /pet 태그를 공유하는 다른 엔드포인트가 있어야 검증이 의미 있다
    assert (candidate["method"], candidate["path"]) not in related


@pytest.mark.asyncio()
async def test_search_endpoints_do_not_carry_traversal_hints(seeded_mcp: FastMCP) -> None:
    """search_endpoints 후보는 얇은 후보 계약을 유지하며 순회 힌트를 싣지 않는다.

    순회 힌트는 get_endpoint_details 전용이다 — search_endpoints 에까지
    실으면 파라미터/응답 추가 조회가 필요해 2단계 분리(후보 피더 정체성)를
    훼손한다(architect 판단, docs/12 후보2/후보3 근거).
    """
    result = await seeded_mcp.call_tool("search_endpoints", arguments={"query": "pet"})
    items = result.structured_content["result"]["items"]

    assert items
    for item in items:
        assert "referenced_schema_refs" not in item
        assert "related_endpoints" not in item


@pytest.mark.asyncio()
async def test_details_unknown_endpoint_returns_error_payload(
    mcp_server: FastMCP,
) -> None:
    """존재하지 않는 endpoint_id 조회 시 스택트레이스 대신 표준 에러 포맷을 반환한다."""
    result = await mcp_server.call_tool(
        "get_endpoint_details", arguments={"endpoint_id": "does-not-exist"}
    )
    payload = result.structured_content["result"]

    assert payload["error"] is True
    assert payload["code"] == "endpoint_not_found"
    assert "does-not-exist" in payload["message"]


# --- 기능 3: resolve_ref -----------------------------------------------------


@pytest.mark.asyncio()
async def test_resolve_ref_expands_schema(seeded_mcp: FastMCP) -> None:
    """`$ref` 가 name/fields 구조로 펼쳐진다."""
    result = await seeded_mcp.call_tool(
        "resolve_ref", arguments={"ref": "#/components/schemas/Pet"}
    )
    resolved = result.structured_content["result"]

    assert resolved["name"] == "Pet"
    assert [f["name"] for f in resolved["fields"]] == ["id", "name", "status"]
    name_field = next(f for f in resolved["fields"] if f["name"] == "name")
    assert name_field["required"] is True
    assert name_field["type"] == "string"


@pytest.mark.asyncio()
async def test_resolve_ref_exposes_document_id(seeded_mcp: FastMCP) -> None:
    """resolve_ref 응답이 스키마가 속한 document_id 를 밝힌다."""
    docs = await seeded_mcp.call_tool("list_documents", arguments={})
    document_id = docs.structured_content["result"][0]["document_id"]

    result = await seeded_mcp.call_tool(
        "resolve_ref", arguments={"ref": "#/components/schemas/Pet"}
    )
    resolved = result.structured_content["result"]

    assert resolved["document_id"] == document_id


@pytest.mark.asyncio()
async def test_resolve_ref_is_deterministic(seeded_mcp: FastMCP) -> None:
    """같은 ref 를 두 번 호출하면 동일한 응답을 반환한다."""
    first = await seeded_mcp.call_tool(
        "resolve_ref", arguments={"ref": "#/components/schemas/Pet"}
    )
    second = await seeded_mcp.call_tool(
        "resolve_ref", arguments={"ref": "#/components/schemas/Pet"}
    )

    assert first.structured_content["result"] == second.structured_content["result"]


@pytest.mark.asyncio()
async def test_resolve_ref_unknown_returns_error_payload(seeded_mcp: FastMCP) -> None:
    """존재하지 않는 ref 는 표준 에러 포맷을 반환한다."""
    result = await seeded_mcp.call_tool(
        "resolve_ref", arguments={"ref": "#/components/schemas/NoSuchSchema"}
    )
    payload = result.structured_content["result"]

    assert payload["error"] is True
    assert payload["code"] == "schema_ref_not_found"


@pytest.mark.asyncio()
async def test_resolve_ref_bad_format_returns_error_payload(seeded_mcp: FastMCP) -> None:
    """참조 형식이 잘못되면 validation_error 를 반환한다."""
    result = await seeded_mcp.call_tool("resolve_ref", arguments={"ref": "Pet"})
    payload = result.structured_content["result"]

    assert payload["error"] is True
    assert payload["code"] == "validation_error"


# --- 기능 4: list_tags -------------------------------------------------------


@pytest.mark.asyncio()
async def test_list_tags_returns_counts(seeded_mcp: FastMCP) -> None:
    """태그 목록과 태그별 엔드포인트 수를 반환한다."""
    result = await seeded_mcp.call_tool("list_tags", arguments={})
    tags = result.structured_content["result"]["tags"]

    assert {t["name"]: t["endpoint_count"] for t in tags} == {"pet": 3, "user": 1}


@pytest.mark.asyncio()
async def test_list_tags_empty_when_no_documents(mcp_server: FastMCP) -> None:
    """등록 문서가 없으면 빈 배열을 반환한다."""
    result = await mcp_server.call_tool("list_tags", arguments={})

    assert result.structured_content["result"]["tags"] == []


@pytest.mark.asyncio()
async def test_list_tags_unknown_document_returns_error_payload(
    mcp_server: FastMCP,
) -> None:
    """존재하지 않는 document_id 는 표준 에러 포맷을 반환한다."""
    result = await mcp_server.call_tool(
        "list_tags", arguments={"document_id": "no-such-doc"}
    )
    payload = result.structured_content["result"]

    assert payload["error"] is True
    assert payload["code"] == "document_not_found"


# --- 도구 간 에러 계약 일관성 -------------------------------------------------


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("search_endpoints", {"query": "pet", "document_id": "no-such-doc"}),
        ("resolve_ref", {"ref": "#/components/schemas/Pet", "document_id": "no-such-doc"}),
        ("list_tags", {"document_id": "no-such-doc"}),
    ],
)
async def test_unknown_document_id_is_document_not_found_everywhere(
    seeded_mcp: FastMCP, tool_name: str, arguments: dict
) -> None:
    """세 도구 모두 미등록 document_id 에 대해 동일한 오류 코드를 반환한다.

    빈 결과로 흘려보내면 호출 LLM 이 "문서 없음"과 "결과 없음"을 구분할 수 없다.
    """
    result = await seeded_mcp.call_tool(tool_name, arguments=arguments)
    payload = result.structured_content["result"]

    assert payload["error"] is True
    assert payload["code"] == "document_not_found"
    assert "no-such-doc" in payload["message"]


# --- 기존 도구 회귀 -----------------------------------------------------------


@pytest.mark.asyncio()
async def test_list_documents_empty(mcp_server: FastMCP) -> None:
    """문서가 없을 때 list_documents 도구가 빈 목록을 반환하는지 확인."""
    result = await mcp_server.call_tool("list_documents", arguments={})

    assert result.structured_content["result"] == []


@pytest.mark.asyncio()
async def test_register_and_search(mcp_server: FastMCP, sample_openapi_3: str) -> None:
    """문서를 등록하고 검색 도구를 통해 결과를 확인."""
    reg_result = await mcp_server.call_tool(
        "register_document", arguments={"project": "default", "raw_document": sample_openapi_3}
    )
    reg_data = reg_result.structured_content["result"]
    assert reg_data["status"] == "registered"
    assert reg_data["endpoints_count"] > 0

    search_result = await mcp_server.call_tool(
        "search_endpoints", arguments={"query": "pet", "top_k": 3}
    )
    items = search_result.structured_content["result"]["items"]

    assert items
    assert any("pet" in i["path"].lower() or "pet" in i["summary"].lower() for i in items)


# --- 기능 4: 프로젝트→Drive/Notion 소스 매핑 도구 -----------------------------


@pytest.mark.asyncio()
async def test_register_drive_source_then_list_has_one_entry(mcp_server: FastMCP) -> None:
    """register_drive_source 후 list_drive_sources 에 정확히 1건 나온다."""
    reg = await mcp_server.call_tool(
        "register_drive_source", arguments={"project": "A", "folder_id": "folder-a"}
    )
    assert reg.structured_content["result"]["status"] == "created"

    listed = await mcp_server.call_tool("list_drive_sources", arguments={})
    items = listed.structured_content["result"]["items"]

    assert [(i["project"], i["folder_id"]) for i in items] == [("A", "folder-a")]


@pytest.mark.asyncio()
async def test_register_notion_source_then_list_has_one_entry(mcp_server: FastMCP) -> None:
    """register_notion_source 후 list_notion_sources 에 정확히 1건 나온다."""
    reg = await mcp_server.call_tool(
        "register_notion_source", arguments={"project": "A", "database_id": "db-a"}
    )
    assert reg.structured_content["result"]["status"] == "created"

    listed = await mcp_server.call_tool("list_notion_sources", arguments={})
    items = listed.structured_content["result"]["items"]

    assert [(i["project"], i["database_id"]) for i in items] == [("A", "db-a")]


@pytest.mark.asyncio()
async def test_register_drive_source_upsert_replaces_value_and_keeps_created_at(
    mcp_server: FastMCP,
) -> None:
    """같은 project 로 재등록하면 행이 늘지 않고 값만 바뀌며 status=updated."""
    await mcp_server.call_tool(
        "register_drive_source", arguments={"project": "A", "folder_id": "folder-a"}
    )
    first_list = await mcp_server.call_tool("list_drive_sources", arguments={})
    created_at = first_list.structured_content["result"]["items"][0]["created_at"]

    reg2 = await mcp_server.call_tool(
        "register_drive_source", arguments={"project": "A", "folder_id": "folder-a-v2"}
    )
    assert reg2.structured_content["result"]["status"] == "updated"

    listed = await mcp_server.call_tool("list_drive_sources", arguments={})
    items = listed.structured_content["result"]["items"]

    assert len(items) == 1
    assert items[0]["folder_id"] == "folder-a-v2"
    assert items[0]["created_at"] == created_at
    assert items[0]["updated_at"] >= created_at


@pytest.mark.asyncio()
async def test_register_drive_source_empty_folder_id_is_validation_error(
    mcp_server: FastMCP,
) -> None:
    """folder_id="" 는 validation_error 이고 행이 생기지 않는다."""
    result = await mcp_server.call_tool(
        "register_drive_source", arguments={"project": "A", "folder_id": ""}
    )
    payload = result.structured_content["result"]

    assert payload["error"] is True
    assert payload["code"] == "validation_error"

    listed = await mcp_server.call_tool("list_drive_sources", arguments={})
    assert listed.structured_content["result"]["items"] == []


@pytest.mark.asyncio()
async def test_register_notion_source_empty_database_id_is_validation_error(
    mcp_server: FastMCP,
) -> None:
    """database_id="" 는 validation_error 이고 행이 생기지 않는다."""
    result = await mcp_server.call_tool(
        "register_notion_source", arguments={"project": "A", "database_id": ""}
    )
    payload = result.structured_content["result"]

    assert payload["error"] is True
    assert payload["code"] == "validation_error"


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "tool_name", ["register_drive_source", "register_notion_source"]
)
async def test_register_source_empty_project_is_validation_error(
    mcp_server: FastMCP, tool_name: str
) -> None:
    """project="" 는 두 도구 모두 validation_error 다."""
    value_key = "folder_id" if tool_name == "register_drive_source" else "database_id"
    result = await mcp_server.call_tool(
        tool_name, arguments={"project": "", value_key: "x"}
    )
    payload = result.structured_content["result"]

    assert payload["error"] is True
    assert payload["code"] == "validation_error"


@pytest.mark.asyncio()
async def test_remove_drive_source_unknown_project_is_not_error(mcp_server: FastMCP) -> None:
    """remove_drive_source("없는프로젝트") 는 오류가 아니라 removed=False 다."""
    result = await mcp_server.call_tool(
        "remove_drive_source", arguments={"project": "없는프로젝트"}
    )
    payload = result.structured_content["result"]

    assert "error" not in payload or payload.get("error") is not True
    assert payload["removed"] is False


@pytest.mark.asyncio()
async def test_remove_notion_source_unknown_project_is_not_error(mcp_server: FastMCP) -> None:
    """remove_notion_source("없는프로젝트") 는 오류가 아니라 removed=False 다."""
    result = await mcp_server.call_tool(
        "remove_notion_source", arguments={"project": "없는프로젝트"}
    )
    payload = result.structured_content["result"]

    assert "error" not in payload or payload.get("error") is not True
    assert payload["removed"] is False


@pytest.mark.asyncio()
async def test_remove_drive_source_then_list_no_longer_has_it(mcp_server: FastMCP) -> None:
    """remove_drive_source("A") 후 list_drive_sources 에 A 가 없다."""
    await mcp_server.call_tool(
        "register_drive_source", arguments={"project": "A", "folder_id": "folder-a"}
    )

    removed = await mcp_server.call_tool("remove_drive_source", arguments={"project": "A"})
    assert removed.structured_content["result"]["removed"] is True

    listed = await mcp_server.call_tool("list_drive_sources", arguments={})
    assert listed.structured_content["result"]["items"] == []


@pytest.mark.asyncio()
async def test_remove_notion_source_then_list_no_longer_has_it(mcp_server: FastMCP) -> None:
    """remove_notion_source("A") 후 list_notion_sources 에 A 가 없다."""
    await mcp_server.call_tool(
        "register_notion_source", arguments={"project": "A", "database_id": "db-a"}
    )

    removed = await mcp_server.call_tool("remove_notion_source", arguments={"project": "A"})
    assert removed.structured_content["result"]["removed"] is True

    listed = await mcp_server.call_tool("list_notion_sources", arguments={})
    assert listed.structured_content["result"]["items"] == []


@pytest.mark.asyncio()
async def test_project_can_register_drive_only_or_notion_only(mcp_server: FastMCP) -> None:
    """한 프로젝트에 Drive 만 등록하거나 Notion 만 등록하는 것이 가능하다."""
    await mcp_server.call_tool(
        "register_drive_source", arguments={"project": "drive-only", "folder_id": "folder-x"}
    )
    await mcp_server.call_tool(
        "register_notion_source", arguments={"project": "notion-only", "database_id": "db-x"}
    )

    drive_list = await mcp_server.call_tool("list_drive_sources", arguments={})
    notion_list = await mcp_server.call_tool("list_notion_sources", arguments={})

    drive_projects = {i["project"] for i in drive_list.structured_content["result"]["items"]}
    notion_projects = {i["project"] for i in notion_list.structured_content["result"]["items"]}

    assert drive_projects == {"drive-only"}
    assert notion_projects == {"notion-only"}


@pytest.mark.asyncio()
async def test_list_drive_sources_ordered_by_project_ascending(mcp_server: FastMCP) -> None:
    """list_drive_sources 반환 순서가 project 오름차순으로 결정적이다."""
    for project in ("C", "A", "B"):
        await mcp_server.call_tool(
            "register_drive_source",
            arguments={"project": project, "folder_id": f"folder-{project.lower()}"},
        )

    listed = await mcp_server.call_tool("list_drive_sources", arguments={})
    projects = [i["project"] for i in listed.structured_content["result"]["items"]]

    assert projects == ["A", "B", "C"]


@pytest.mark.asyncio()
async def test_list_notion_sources_ordered_by_project_ascending(mcp_server: FastMCP) -> None:
    """list_notion_sources 반환 순서가 project 오름차순으로 결정적이다."""
    for project in ("C", "A", "B"):
        await mcp_server.call_tool(
            "register_notion_source",
            arguments={"project": project, "database_id": f"db-{project.lower()}"},
        )

    listed = await mcp_server.call_tool("list_notion_sources", arguments={})
    projects = [i["project"] for i in listed.structured_content["result"]["items"]]

    assert projects == ["A", "B", "C"]
