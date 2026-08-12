"""프로젝트 간 격리 회귀 테스트 스위트 (SPEC 기능 7, 427~442행).

기능 1~6 각각의 단위 테스트와 별개로, MCP 도구 레벨에서 여러 계층
(OpenAPI 문서/엔드포인트/스키마 + Drive/Notion 메타 캐시 + 소스 매핑)이
함께 얽힐 때만 드러나는 프로젝트 간 누수를 잡는다. 두 프로젝트(A/B)를
Drive+Notion 모두 다르게 구성한 뒤, 전체 도구를 교차 호출해 격리를 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastmcp import FastMCP

_T1 = datetime(2026, 7, 1, 9, 0, 0)

_BILLING_DOC: dict = {
    "openapi": "3.0.3",
    "info": {"title": "Billing API", "version": "1.0.0"},
    "paths": {
        "/invoice": {
            "get": {
                "operationId": "getInvoice",
                "summary": "Get invoice",
                "tags": ["billing"],
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
    "components": {
        "schemas": {
            "Pet": {
                "type": "object",
                "properties": {"invoiceOnly": {"type": "string"}},
            }
        }
    },
}


def _result(call_result) -> dict:
    """FastMCP 도구 호출 결과에서 구조화 페이로드를 꺼낸다."""
    return call_result.structured_content["result"]


async def _register_openapi_docs(
    server: FastMCP, sample_openapi_3: str
) -> tuple[str, str]:
    """프로젝트 A(petstore)/B(billing) 에 각각 OpenAPI 문서를 등록한다."""
    reg_a = _result(
        await server.call_tool(
            "register_document", {"project": "A", "raw_document": sample_openapi_3}
        )
    )
    reg_b = _result(
        await server.call_tool(
            "register_document",
            {"project": "B", "raw_document": json.dumps(_BILLING_DOC)},
        )
    )
    return reg_a["document_id"], reg_b["document_id"]


async def _seed_collab_docs(server: FastMCP, handles: dict) -> None:
    """프로젝트 A/B 의 Drive+Notion 페이크에 서로 다른 문서를 채우고 캐시를 갱신한다."""
    handles["A"]["drive"].put("d-a", "A 드라이브 문서", "A 드라이브 본문", modified_at=_T1)
    handles["A"]["notion"].put("n-a", "A 노션 문서", "A 노션 본문", modified_at=_T1)
    handles["B"]["drive"].put("d-b", "B 드라이브 문서", "B 드라이브 본문", modified_at=_T1)
    handles["B"]["notion"].put("n-b", "B 노션 문서", "B 노션 본문", modified_at=_T1)
    await server.call_tool("refresh_index", arguments={})
    for handle in handles.values():
        handle["drive"].reset_counts()
        handle["notion"].reset_counts()


@pytest.fixture()
async def isolated_mcp(two_project_mcp: FastMCP, two_project_services: dict) -> FastMCP:
    """A/B 프로젝트에 OpenAPI 문서 + Drive/Notion 협업 문서가 모두 채워진 MCP 서버."""
    return two_project_mcp


@pytest.fixture()
def isolated_services_factory(two_project_app_state):
    """isolated_mcp 와 동일한 AppState 를 공유하는 ServiceBundle 팩토리.

    `sync_service.delete()` 처럼 MCP 도구로 노출되지 않은 저수준 조작이
    필요할 때, isolated_mcp 가 실제로 쓰는 것과 같은 DB/엔진을 바라보는
    서비스 번들을 만든다.
    """
    from app.composition import build_services

    def _factory():
        return next(build_services(two_project_app_state))

    return _factory


# --- 검증 기준 1: A 전용 흐름에서 B 식별자가 어디서도 노출되지 않는다 -----------


@pytest.mark.asyncio()
async def test_full_flow_with_project_a_never_exposes_project_b_identifiers(
    isolated_mcp: FastMCP, two_project_services: dict, sample_openapi_3: str
) -> None:
    """register_document → search_endpoints → get_endpoint_details → resolve_ref
    를 project="A" 로만 수행했을 때, 어느 단계에서도 B 문서의 식별자가 노출되지 않는다.
    """
    doc_a, doc_b = await _register_openapi_docs(isolated_mcp, sample_openapi_3)

    search = _result(
        await isolated_mcp.call_tool(
            "search_endpoints", {"query": "pet", "project": "A"}
        )
    )
    endpoint_ids = [item["endpoint_id"] for item in search["items"]]
    assert endpoint_ids, "A 프로젝트에서 pet 관련 엔드포인트가 나와야 한다"

    for endpoint_id in endpoint_ids:
        details = _result(
            await isolated_mcp.call_tool(
                "get_endpoint_details", {"endpoint_id": endpoint_id}
            )
        )
        assert details["document_id"] == doc_a
        assert details["document_id"] != doc_b

    resolved = _result(
        await isolated_mcp.call_tool(
            "resolve_ref", {"ref": "#/components/schemas/Pet", "project": "A"}
        )
    )
    assert resolved["document_id"] == doc_a
    # B 의 Pet 스키마는 invoiceOnly 필드를 갖는다 — 섞였다면 여기 나타난다.
    field_names = {f["name"] for f in resolved["fields"]}
    assert "invoiceOnly" not in field_names

    # search_documents/get_document 도 같은 흐름에 포함해 협업 문서 계층도 검증한다.
    await _seed_collab_docs(isolated_mcp, two_project_services)
    doc_search = _result(
        await isolated_mcp.call_tool(
            "search_documents", {"query": "문서", "project": "A"}
        )
    )
    titles = {item["title"] for item in doc_search["items"]}
    assert "B 드라이브 문서" not in titles
    assert "B 노션 문서" not in titles
    for item in doc_search["items"]:
        assert item["project"] == "A"


# --- 검증 기준 2: list_documents(필터 없음)는 A·B 모두 반환 --------------------


@pytest.mark.asyncio()
async def test_list_documents_without_filter_returns_both_projects(
    isolated_mcp: FastMCP, sample_openapi_3: str
) -> None:
    """list_documents()(필터 없음)는 A·B 를 모두 반환한다.

    격리는 필터를 줬을 때만 적용되며, project 를 생략한 기본 동작이
    조용히 바뀌지 않는다는 하위 호환 계약을 고정한다.
    """
    doc_a, doc_b = await _register_openapi_docs(isolated_mcp, sample_openapi_3)

    listed = _result(await isolated_mcp.call_tool("list_documents", arguments={}))
    ids = {item["document_id"] for item in listed}

    assert doc_a in ids
    assert doc_b in ids


# --- 검증 기준 3: A 삭제가 B 문서/B document_meta/project_source 에 영향 없음 --


@pytest.mark.asyncio()
async def test_deleting_project_a_document_does_not_affect_project_b(
    isolated_mcp: FastMCP,
    isolated_services_factory,
    two_project_services: dict,
    sample_openapi_3: str,
) -> None:
    """프로젝트 A 의 문서를 삭제해도 B 문서와 B 의 document_meta 행,
    project_source 행이 영향받지 않는다.
    """
    doc_a, doc_b = await _register_openapi_docs(isolated_mcp, sample_openapi_3)
    await _seed_collab_docs(isolated_mcp, two_project_services)

    # A 의 OpenAPI 문서를 삭제한다(MCP 도구로 노출되지 않은 저수준 조작).
    isolated_services_factory().sync_service.delete(doc_a)

    # B 의 OpenAPI 문서는 여전히 조회된다.
    listed = _result(await isolated_mcp.call_tool("list_documents", arguments={}))
    remaining_ids = {item["document_id"] for item in listed}
    assert doc_b in remaining_ids
    assert doc_a not in remaining_ids

    # B 의 협업 문서(document_meta) 는 여전히 검색된다.
    doc_search = _result(
        await isolated_mcp.call_tool(
            "search_documents", {"query": "B", "project": "B"}
        )
    )
    titles = {item["title"] for item in doc_search["items"]}
    assert "B 드라이브 문서" in titles
    assert "B 노션 문서" in titles

    # B 의 project_source(drive/notion) 매핑도 그대로 남는다.
    drive_list = _result(
        await isolated_mcp.call_tool("list_drive_sources", {"project": "B"})
    )
    notion_list = _result(
        await isolated_mcp.call_tool("list_notion_sources", {"project": "B"})
    )
    assert [item["folder_id"] for item in drive_list["items"]] == ["folder-b"]
    assert [item["database_id"] for item in notion_list["items"]] == ["db-b"]


# --- 검증 기준 4: project 를 쓰지 않는 기존 호출 방식이 여전히 동작 ------------


@pytest.mark.asyncio()
async def test_calls_without_project_parameter_still_work(
    isolated_mcp: FastMCP, sample_openapi_3: str
) -> None:
    """project 파라미터를 전혀 쓰지 않는 기존 호출 방식(등록 제외)이 여전히 동작한다.

    (register_document 는 기능 2 에서 project 가 필수가 됐으므로 제외 —
    SPEC 442행이 명시한 예외.)
    """
    doc_a, _ = await _register_openapi_docs(isolated_mcp, sample_openapi_3)

    search = _result(
        await isolated_mcp.call_tool("search_endpoints", {"query": "pet"})
    )
    assert search["items"], "project 없이도 검색이 동작해야 한다"

    tags = _result(await isolated_mcp.call_tool("list_tags", arguments={}))
    assert tags["tags"], "project 없이도 태그 조회가 동작해야 한다"

    resolved = _result(
        await isolated_mcp.call_tool("resolve_ref", {"ref": "#/components/schemas/Pet"})
    )
    assert resolved["name"] == "Pet"

    listed = _result(await isolated_mcp.call_tool("list_documents", arguments={}))
    assert any(item["document_id"] == doc_a for item in listed)
