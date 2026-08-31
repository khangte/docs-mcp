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
from sqlalchemy import text

from app.composition import AppState
from app.mcp.server import create_mcp_server
from app.models import DEFAULT_PROJECT, EMBEDDING_DIM
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION
from app.services.indexer.embedding_provider import HashEmbeddingProvider

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
    # index_bodies=False: 이 fixture 를 쓰는 테스트들은 fetch 시점 후보 압축·
    # snippet 폴백(2단계 fetch 전략)을 검증하므로 본문이 미리 색인되면 안 된다.
    await mcp_server.call_tool("refresh_index", arguments={"index_bodies": False})
    fake_drive_source.reset_counts()
    fake_notion_source.reset_counts()
    return mcp_server


@pytest.fixture()
async def fetch_strategy_seeded_mcp(
    pg_engine,
    in_memory_fetcher,
    fake_drive_source,
    fake_notion_source,
    seed_default_project_sources,
) -> FastMCP:
    """document_search_strategy="fetch"(롤백 스위치) 로 고정한 MCP 서버(57번 리뷰 §5 개선1 T6)."""
    state = AppState.from_engine(
        engine=pg_engine,
        fetcher=in_memory_fetcher,
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
        vector_fallback_enabled=True,
        drive_source_builder=lambda folder_id: fake_drive_source,
        notion_source_builder=lambda notion_id, kind: fake_notion_source,
        metadata_writeback_enabled=True,
        document_search_strategy="fetch",
    )
    server = create_mcp_server(state)
    fake_drive_source.put("d1", "로그인 인증 설계서", "OAuth 로그인 흐름 상세", modified_at=_T1)
    await server.call_tool("refresh_index", arguments={"index_bodies": False})
    fake_drive_source.reset_counts()
    return server


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
    """search_documents 는 12개 파라미터를 노출한다(62번: created/owners/folder_ids 추가)."""
    properties = await _tool_parameters(mcp_server, "search_documents")

    assert set(properties) == {
        "query",
        "top_k",
        "source",
        "project",
        "query_variants",
        "modified_after",
        "modified_before",
        "mime_types",
        "created_after",
        "created_before",
        "owners",
        "folder_ids",
    }
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
async def test_refresh_index_response_includes_coverage_keys(
    mcp_server: FastMCP, seed_default_project_sources, fake_drive_source, fake_notion_source
) -> None:
    """refresh_index 응답에 coverage 3키가 항상 있고 기존 계약은 그대로다(개선 #5 T9)."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    fake_notion_source.put("n1", "회의록", "본문", modified_at=_T1)

    payload = _result(await mcp_server.call_tool("refresh_index", arguments={}))

    assert payload["coverage"].keys() == {"unindexed", "unsupported", "listing_truncated"}
    assert payload["coverage"]["unindexed"] == 0
    assert payload["coverage"]["unsupported"] == 0
    assert payload["coverage"]["listing_truncated"] == []
    assert {"synced", "added", "updated", "removed", "failed_sources", "coverage"} <= payload.keys()


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


@pytest.mark.asyncio()
async def test_refresh_index_returns_refresh_in_progress_when_batch_holds_lock(
    mcp_server: FastMCP, seed_default_project_sources, session_factory
) -> None:
    """배치 CLI 가 축 A lock 을 쥔 동안 refresh_index 를 부르면 충돌 에러를 반환한다.

    `docs/architect-review/53_data_flow_scenarios.md` 케이스 4 리스크:
    advisory lock 이 배치 CLI 에만 있어 두 writer 가 같은 document_meta 행에
    동시에 붙을 수 있었다. MCP 도구도 같은 lock key 를 잡아야 한다.
    """
    from app.services.documents.refresh_lock import LOCK_KEY_META_SYNC, advisory_unlock

    holder = session_factory()
    holder.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY_META_SYNC})
    try:
        payload = _result(await mcp_server.call_tool("refresh_index", arguments={}))

        assert payload["error"] is True
        assert payload["code"] == "refresh_in_progress"
    finally:
        advisory_unlock(holder, LOCK_KEY_META_SYNC)
        holder.close()


@pytest.mark.asyncio()
async def test_refresh_index_releases_lock_after_aborted_transaction(
    mcp_server: FastMCP, seed_default_project_sources, pg_engine, monkeypatch
) -> None:
    """DB 레벨 오류로 트랜잭션이 aborted 여도 finally 의 방어적 rollback 이 락을 지킨다.

    `document_repo.list_resyncable` 호출은 문서별 try/except(F2) 밖에 있어, 거기서
    나는 SQLAlchemyError 는 그대로 finally 까지 전파된다. rollback 없이 그 상태로
    advisory_unlock 을 호출하면 unlock 쿼리 자체가 실패해 락이 안 풀린다
    (`docs/architect-review/54_refresh_lock_abort_asymmetry_verdict.md` F1/F3).
    검증은 **완전히 별도의 엔진(별도 커넥션 풀)** 으로 한다 — `session_factory`
    로 새 세션만 열면, 도구 호출이 반납한(락이 눌어붙은 채인) 바로 그 물리
    커넥션을 커넥션 풀이 그대로 재배정할 수 있어(LIFO 체크인) 재진입으로
    항상 성공해버려 검증이 무의미해진다.
    """
    from fastmcp.exceptions import ToolError
    from sqlalchemy.exc import SQLAlchemyError

    from app.core.db import create_db_engine
    from app.repositories.document_repository import DocumentRepository
    from app.services.documents.refresh_lock import LOCK_KEY_REGISTERED_RESYNC

    def _boom(self, project: str | None) -> list:
        self._session.execute(text("SELECT 1/0"))  # 실제 DB 에러로 트랜잭션을 abort 시킨다
        return []  # pragma: no cover - DB 가 먼저 에러를 던진다

    monkeypatch.setattr(DocumentRepository, "list_resyncable", _boom)

    with pytest.raises((ToolError, SQLAlchemyError)):
        await mcp_server.call_tool("refresh_index", arguments={"include_registered": True})

    verify_engine = create_db_engine(pg_engine.url.render_as_string(hide_password=False))
    try:
        with verify_engine.connect() as conn:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY_REGISTERED_RESYNC}
            ).scalar()
            assert acquired is True
            conn.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY_REGISTERED_RESYNC}
            )
    finally:
        verify_engine.dispose()


@pytest.mark.asyncio()
async def test_refresh_index_default_omits_registered_key(
    mcp_server: FastMCP, seed_default_project_sources
) -> None:
    """include_registered 를 생략하면(기존 호출) registered 키가 없다(하위호환)."""
    payload = _result(await mcp_server.call_tool("refresh_index", arguments={}))

    assert "registered" not in payload


@pytest.mark.asyncio()
async def test_refresh_index_include_registered_resyncs_url_documents(
    mcp_server: FastMCP,
    app_state,
    in_memory_fetcher,
    seed_default_project_sources,
    sample_openapi_3: str,
) -> None:
    """include_registered=True 면 URL 기반 문서만 resync 되고 raw_document 문서는 제외된다."""
    from app.composition import build_services

    in_memory_fetcher.put("https://example.com/openapi.json", sample_openapi_3)
    services = next(build_services(app_state))
    services.sync_service.register(
        project="default", source_url="https://example.com/openapi.json", raw_document=None
    )
    services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )

    payload = _result(
        await mcp_server.call_tool("refresh_index", arguments={"include_registered": True})
    )

    assert payload["registered"]["total"] == 1
    assert payload["registered"]["skipped"] == 1
    assert payload["registered"]["reindexed"] == 0
    assert payload["registered"]["failed"] == []


@pytest.mark.asyncio()
async def test_refresh_index_include_registered_zero_targets_returns_empty_aggregate(
    mcp_server: FastMCP, seed_default_project_sources
) -> None:
    """URL 기반 문서가 하나도 없으면 registered 는 0 집계로 정상 반환된다."""
    payload = _result(
        await mcp_server.call_tool("refresh_index", arguments={"include_registered": True})
    )

    assert payload["registered"] == {"total": 0, "reindexed": 0, "skipped": 0, "failed": []}


@pytest.mark.asyncio()
async def test_refresh_index_include_registered_partial_failure_continues(
    mcp_server: FastMCP,
    app_state,
    in_memory_fetcher,
    seed_default_project_sources,
    sample_openapi_3: str,
) -> None:
    """문서 하나의 resync 가 실패해도 나머지는 계속 진행되고 failed 에 담긴다."""
    from app.composition import build_services

    in_memory_fetcher.put("https://example.com/ok.json", sample_openapi_3)
    in_memory_fetcher.put("https://example.com/bad.json", sample_openapi_3)
    services = next(build_services(app_state))
    ok_result = services.sync_service.register(
        project="default", source_url="https://example.com/ok.json", raw_document=None
    )
    bad_result = services.sync_service.register(
        project="default", source_url="https://example.com/bad.json", raw_document=None
    )
    # 등록 후 매핑을 제거해 resync 시점에만 IntegrationError 가 발생하게 한다
    in_memory_fetcher.remove("https://example.com/bad.json")

    payload = _result(
        await mcp_server.call_tool("refresh_index", arguments={"include_registered": True})
    )

    assert payload["registered"]["total"] == 2
    assert payload["registered"]["failed"] == [bad_result.document.id]
    assert ok_result.document.id not in payload["registered"]["failed"]


@pytest.mark.asyncio()
async def test_refresh_index_include_registered_rolls_back_failed_reindex(
    mcp_server: FastMCP,
    app_state,
    in_memory_fetcher,
    seed_default_project_sources,
    sample_openapi_3: str,
) -> None:
    """delete+flush 이후(색인 단계) 실패한 문서의 미커밋 삭제가 다음 문서의
    commit 에 딸려가 커밋되지 않는다(세션 공유로 인한 데이터 유실 방지)."""
    from app.core.errors import IntegrationError

    class _FailOnceEmbeddingProvider:
        """첫 embed_documents 호출만 실패하고 이후 호출은 정상 위임하는 페이크."""

        def __init__(self, delegate) -> None:
            self._delegate = delegate
            self._calls = 0

        @property
        def dim(self) -> int:
            return self._delegate.dim

        @property
        def is_semantic(self) -> bool:
            return self._delegate.is_semantic

        def embed_documents(
            self, texts: list[str], labels: list[str] | None = None
        ) -> list[list[float]]:
            self._calls += 1
            if self._calls == 1:
                raise IntegrationError("embedding provider unavailable")
            return self._delegate.embed_documents(texts, labels=labels)

        def embed_query(self, text: str) -> list[float]:
            return self._delegate.embed_query(text)

    in_memory_fetcher.put("https://example.com/doc-a.json", sample_openapi_3)
    in_memory_fetcher.put("https://example.com/doc-b.json", sample_openapi_3)
    from app.composition import build_services

    services = next(build_services(app_state))
    doc_a = services.sync_service.register(
        project="default", source_url="https://example.com/doc-a.json", raw_document=None
    )
    doc_b = services.sync_service.register(
        project="default", source_url="https://example.com/doc-b.json", raw_document=None
    )
    before_counts = {
        doc.document.id: (
            len(services.endpoint_repo.list_by_document(doc.document.id)),
            len(services.chunk_repo.list_by_document(doc.document.id)),
        )
        for doc in (doc_a, doc_b)
    }
    assert all(endpoints > 0 and chunks > 0 for endpoints, chunks in before_counts.values())

    # register 단계는 이미 끝났으므로 새 페이크의 호출 카운터는 resync 단계부터
    # 시작한다. 순회상 첫 문서만 실패하고 두 번째는 정상 재색인된다.
    app_state.embedding_provider = _FailOnceEmbeddingProvider(app_state.embedding_provider)

    payload = _result(
        await mcp_server.call_tool(
            "refresh_index", arguments={"include_registered": True, "force": True}
        )
    )

    assert len(payload["registered"]["failed"]) == 1
    failed_id = payload["registered"]["failed"][0]

    verify_services = next(build_services(app_state))
    before_endpoints, before_chunks = before_counts[failed_id]
    assert len(verify_services.endpoint_repo.list_by_document(failed_id)) == before_endpoints
    assert len(verify_services.chunk_repo.list_by_document(failed_id)) == before_chunks


# --- 기능 7: search_documents --------------------------------------------------


_DOCUMENT_SEARCH_ITEM_FIELDS = {
    "external_id",
    "title",
    "source",
    "project",
    "url",
    "snippet",
    "score",
    "version",
    "snippet_as_of",
    "matched_chunks",
    "match_reasons",
    "modified_at",
    "indexed",
    "mime_type",
    "owner",
    "folder_path",
    "folder_id",
}


@pytest.mark.asyncio()
async def test_search_documents_returns_expected_fields(seeded_mcp: FastMCP) -> None:
    """결과 항목은 근거·메타 필드 4개(matched_chunks/match_reasons/modified_at/indexed)를
    포함해 17개 필드를 갖는다(57번 리뷰 §5 개선1, 62번: folder_path/folder_id 추가)."""
    items = _result(await seeded_mcp.call_tool("search_documents", {"query": "로그인"}))["items"]

    assert items
    for item in items:
        assert set(item) == _DOCUMENT_SEARCH_ITEM_FIELDS


@pytest.mark.asyncio()
async def test_search_documents_fetch_strategy_has_same_field_set(
    fetch_strategy_seeded_mcp: FastMCP,
) -> None:
    """롤백 스위치인 "fetch" 전략도 indexed 전략과 같은 키 집합을 낸다.

    클라이언트가 전략별로 분기하지 않도록 보장한다(57번 리뷰 §5 개선1 T6).
    """
    items = _result(
        await fetch_strategy_seeded_mcp.call_tool("search_documents", {"query": "로그인"})
    )["items"]

    assert items
    for item in items:
        assert set(item) == _DOCUMENT_SEARCH_ITEM_FIELDS
        assert item["matched_chunks"] == []
        assert item["match_reasons"]


@pytest.mark.asyncio()
async def test_search_documents_snippet_as_of_null_without_body_index(
    seeded_mcp: FastMCP,
) -> None:
    """본문 청크가 색인되지 않은(fetch 폴백/제목 단독 매치) 결과는 snippet_as_of 가 null 이다."""
    items = _result(await seeded_mcp.call_tool("search_documents", {"query": "로그인"}))["items"]

    assert items
    assert all(i["snippet_as_of"] is None for i in items)


@pytest.mark.asyncio()
async def test_search_documents_version_is_null_without_version_marker(
    seeded_mcp: FastMCP,
) -> None:
    """제목에 버전 표기가 없는 문서는 version 이 null 로 실린다."""
    items = _result(await seeded_mcp.call_tool("search_documents", {"query": "로그인"}))["items"]

    assert all(i["version"] is None for i in items if i["title"] == "로그인 인증 설계서")


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
async def test_search_documents_indexed_strategy_does_not_fetch_bodies(
    mcp_server: FastMCP, seed_default_project_sources, fake_drive_source
) -> None:
    """기본 전략(indexed)은 색인된 청크만 읽고 검색 시 라이브 fetch 를 하지 않으며,
    최종 결과 개수는 top_k 로 컷된다.

    2단계 라이브 fetch 예산(`_body_fetch_budget`) 자체의 가드는
    `tests/unit/test_document_search_service.py:272` 에 살아 있다 — 이
    통합 테스트가 fetch_call_count==0 을 단언해도 커버리지 손실은 없다.
    """
    candidate_count = 8
    top_k = 2
    for index in range(candidate_count):
        fake_drive_source.put(f"d{index}", f"로그인 문서 {index}", "로그인 본문", modified_at=_T1)
    await mcp_server.call_tool("refresh_index", arguments={})
    fake_drive_source.reset_counts()

    result = await mcp_server.call_tool(
        "search_documents", {"query": "로그인", "top_k": top_k}
    )

    assert fake_drive_source.fetch_call_count == 0
    assert len(result.structured_content["result"]["items"]) == top_k


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
async def test_search_documents_mime_types_filter_and_response_field(
    seeded_mcp: FastMCP, fake_drive_source
) -> None:
    """mime_types 로 필터링되고, 남는 결과는 mime_type 응답 필드를 담는다(개선 #2 T9/T10)."""
    fake_drive_source.put(
        "d1",
        "로그인 인증 설계서",
        "OAuth 로그인 흐름 상세",
        modified_at=_T1,
        mime_type="application/pdf",
    )
    await seeded_mcp.call_tool("refresh_index", arguments={"index_bodies": False})

    matched = _result(
        await seeded_mcp.call_tool(
            "search_documents",
            {"query": "로그인", "mime_types": ["application/pdf"]},
        )
    )["items"]
    excluded = _result(
        await seeded_mcp.call_tool(
            "search_documents",
            {"query": "로그인", "mime_types": ["text/plain"]},
        )
    )["items"]

    assert [i["title"] for i in matched] == ["로그인 인증 설계서"]
    assert matched[0]["mime_type"] == "application/pdf"
    assert excluded == []


@pytest.mark.asyncio()
async def test_search_documents_exposes_owner(seeded_mcp: FastMCP, fake_drive_source) -> None:
    """응답 항목에 owner 가 실린다(다음 질의의 owners 값으로 그대로 쓸 수 있다)."""
    fake_drive_source.put(
        "d1",
        "로그인 인증 설계서",
        "OAuth 로그인 흐름 상세",
        modified_at=_T1,
        owner="owner@example.test",
    )
    await seeded_mcp.call_tool("refresh_index", arguments={"index_bodies": False})

    items = _result(
        await seeded_mcp.call_tool("search_documents", {"query": "로그인"})
    )["items"]

    assert [i["owner"] for i in items if i["title"] == "로그인 인증 설계서"] == [
        "owner@example.test"
    ]


@pytest.mark.asyncio()
async def test_search_documents_owners_filter(seeded_mcp: FastMCP, fake_drive_source) -> None:
    """owners 를 주면 그 소유자 문서만 남는다."""
    fake_drive_source.put(
        "d1",
        "로그인 인증 설계서",
        "OAuth 로그인 흐름 상세",
        modified_at=_T1,
        owner="owner@example.test",
    )
    await seeded_mcp.call_tool("refresh_index", arguments={"index_bodies": False})

    matched = _result(
        await seeded_mcp.call_tool(
            "search_documents", {"query": "로그인", "owners": ["owner@example.test"]}
        )
    )["items"]
    excluded = _result(
        await seeded_mcp.call_tool(
            "search_documents", {"query": "로그인", "owners": ["other@example.test"]}
        )
    )["items"]

    assert [i["title"] for i in matched] == ["로그인 인증 설계서"]
    assert excluded == []


@pytest.mark.asyncio()
async def test_search_documents_folder_ids_filter(
    seeded_mcp: FastMCP, fake_drive_source
) -> None:
    """folder_ids 를 주면 대상 폴더의 자손 문서까지 남고, 응답에 folder_path/folder_id 가 실린다."""
    fake_drive_source.put(
        "d1",
        "로그인 인증 설계서",
        "OAuth 로그인 흐름 상세",
        modified_at=_T1,
        folder_ancestor_ids=("root", "sub", "leaf"),
        folder_path="설계/인증/로그인",
    )
    fake_drive_source.put(
        "d2",
        "로그인 배포 메모",
        "로그인 릴리스 노트",
        modified_at=_T1,
        folder_ancestor_ids=("other",),
        folder_path="배포",
    )
    await seeded_mcp.call_tool("refresh_index", arguments={"index_bodies": False})

    matched = _result(
        await seeded_mcp.call_tool(
            "search_documents", {"query": "로그인", "folder_ids": ["sub"]}
        )
    )["items"]
    excluded = _result(
        await seeded_mcp.call_tool(
            "search_documents", {"query": "로그인", "folder_ids": ["nonexistent"]}
        )
    )["items"]

    assert [i["title"] for i in matched] == ["로그인 인증 설계서"]
    assert matched[0]["folder_path"] == "설계/인증/로그인"
    assert matched[0]["folder_id"] == "leaf"
    assert excluded == []


@pytest.mark.asyncio()
async def test_search_documents_created_filters(seeded_mcp: FastMCP, fake_drive_source) -> None:
    """created_after/created_before 가 생성 시각 축으로 걸린다."""
    fake_drive_source.put(
        "d1",
        "로그인 인증 설계서",
        "OAuth 로그인 흐름 상세",
        modified_at=_T1,
        created_at=_T1,
    )
    await seeded_mcp.call_tool("refresh_index", arguments={"index_bodies": False})

    matched = _result(
        await seeded_mcp.call_tool(
            "search_documents", {"query": "로그인", "created_after": _T1.isoformat()}
        )
    )["items"]
    excluded = _result(
        await seeded_mcp.call_tool(
            "search_documents", {"query": "로그인", "created_before": "2020-01-01"}
        )
    )["items"]

    assert [i["title"] for i in matched] == ["로그인 인증 설계서"]
    assert excluded == []


@pytest.mark.asyncio()
async def test_search_documents_modified_after_filter(
    seeded_mcp: FastMCP, fake_drive_source
) -> None:
    """modified_after 이전에 수정된 문서는 결과에서 제외된다(개선 #2 T8)."""
    items = _result(
        await seeded_mcp.call_tool(
            "search_documents",
            {"query": "로그인", "modified_after": "2026-07-02T00:00:00Z"},
        )
    )["items"]

    assert items == []


@pytest.mark.asyncio()
async def test_search_documents_invalid_modified_after_returns_error_payload(
    seeded_mcp: FastMCP,
) -> None:
    """modified_after 가 ISO8601 이 아니면 표준 에러 포맷을 반환한다."""
    payload = _result(
        await seeded_mcp.call_tool(
            "search_documents", {"query": "로그인", "modified_after": "이상한값"}
        )
    )

    assert payload["error"] is True
    assert payload["code"] == "validation_error"


@pytest.mark.asyncio()
async def test_search_documents_empty_query_returns_error_payload(
    seeded_mcp: FastMCP,
) -> None:
    """빈 질의는 표준 에러 포맷을 반환한다."""
    payload = _result(await seeded_mcp.call_tool("search_documents", {"query": "   "}))

    assert payload["error"] is True
    assert payload["code"] == "validation_error"


@pytest.mark.asyncio()
async def test_search_documents_query_variants_widen_candidates(seeded_mcp: FastMCP) -> None:
    """원본 질의 토큰만으로는 0건이어도 query_variants 를 넘기면 후보를 찾는다.

    seeded_mcp 는 "배포 운영 가이드" 문서를 갖고 있다. "무중단 서비스 릴리즈"
    라는, 제목과 겹치는 토큰이 하나도 없는 질의로는 0건이어야 하고,
    query_variants 로 "배포"를 넘기면 그 문서를 찾아야 한다.
    """
    empty = _result(
        await seeded_mcp.call_tool("search_documents", {"query": "무중단 서비스 릴리즈"})
    )
    assert empty["items"] == []

    widened = _result(
        await seeded_mcp.call_tool(
            "search_documents",
            {"query": "무중단 서비스 릴리즈", "query_variants": ["배포"]},
        )
    )
    assert "배포 운영 가이드" in {i["title"] for i in widened["items"]}


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
    `project_source` 를 그 요청의 세션으로 새로 조회해 어댑터를
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
    """원문 조회가 제목·출처·URL·본문·버전을 모두 반환한다."""
    payload = _result(
        await seeded_mcp.call_tool(
            "get_document", {"source": SOURCE_DRIVE, "external_id": "d1"}
        )
    )

    assert set(payload) == {"title", "source", "url", "content", "version", "truncated"}
    assert payload["content"] == "OAuth 로그인 흐름 상세"
    assert payload["title"] == "로그인 인증 설계서"
    assert payload["source"] == SOURCE_DRIVE
    assert payload["version"] is None
    assert payload["truncated"] is False


@pytest.mark.asyncio()
async def test_get_document_and_search_documents_expose_parsed_version(
    mcp_server: FastMCP, seed_default_project_sources, fake_drive_source
) -> None:
    """제목에 버전 표기가 있으면 search_documents/get_document 둘 다 version 을 채워 반환한다."""
    fake_drive_source.put("d1", "결제 정책 v_1.0", "결제 정책 본문", modified_at=_T1)
    await mcp_server.call_tool("refresh_index", arguments={})

    search_items = _result(
        await mcp_server.call_tool("search_documents", {"query": "결제"})
    )["items"]
    get_payload = _result(
        await mcp_server.call_tool(
            "get_document", {"source": SOURCE_DRIVE, "external_id": "d1"}
        )
    )

    assert [i["version"] for i in search_items] == ["v1.0"]
    assert get_payload["version"] == "v1.0"


@pytest.mark.asyncio()
async def test_get_document_propagates_truncated_flag(
    seeded_mcp: FastMCP, fake_drive_source
) -> None:
    """어댑터가 truncated=True 로 fetch 했으면 payload 에도 그대로 실린다."""
    fake_drive_source.truncated_ids.add("d1")

    payload = _result(
        await seeded_mcp.call_tool(
            "get_document", {"source": SOURCE_DRIVE, "external_id": "d1"}
        )
    )

    assert payload["truncated"] is True


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
