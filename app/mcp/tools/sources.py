"""협업 문서 소스(Drive/Notion 매핑, 인덱스 갱신) 관련 MCP 도구 등록."""

from __future__ import annotations

import anyio
from fastmcp import FastMCP

from app.composition import AppState, ServiceBundle
from app.core.errors import DomainError, IntegrationError
from app.core.logging import get_logger
from app.core.config import get_settings
from app.mcp.payloads import _to_drive_source_item, _to_notion_source_item, _to_refresh_payload
from app.mcp.tools._common import _run_bundle, to_error_payload
from app.mcp.types import (
    DriveSourceListResult,
    ErrorPayload,
    NotionSourceListResult,
    RefreshIndexResult,
    RegisteredResyncResult,
    RegisterDriveSourceResult,
    RegisterNotionPageResult,
    RegisterNotionSourceResult,
    RemoveDriveSourceResult,
    RemoveNotionSourceResult,
)

_LOG = get_logger("docs_mcp.mcp", level=get_settings().log_level)


def register_source_tools(mcp: FastMCP, app_state: AppState) -> None:
    """소스 관련 MCP 도구(refresh_index, drive/notion 매핑 CRUD)를 등록한다."""

    @mcp.tool()
    async def refresh_index(
        source: str | None = None,
        project: str | None = None,
        include_registered: bool = False,
        force: bool = False,
    ) -> RefreshIndexResult | ErrorPayload:
        """협업 문서 메타 캐시(제목·수정일)를 원본과 동기화한다.

        문서 목록과 메타데이터만 갱신하고 본문은 저장하지 않는다. 새로 만든
        문서가 search_documents 에 잡히지 않을 때 실행한다.

        Args:
            source: "drive" 또는 "notion" 만 갱신할 때 지정. 생략하면 구성된
                모든 소스를 갱신한다. document 재동기화에는 관여하지
                않는다(drive/notion 개념이 없다).
            project: 특정 프로젝트만 갱신할 때 지정. 생략하면 등록된 전
                프로젝트를 순회한다. include_registered=True 일 때는
                document 대상도 이 project 로 좁힌다.
            include_registered: True 면 register_document 로 등록한
                Document 중 source_url 이 있는(URL 기반) 문서도 원본을
                다시 가져와 재동기화한다. raw_document 로 등록한 문서는
                원본 재fetch가 불가능하므로 자동 제외된다. 기본 False —
                문서마다 원본 재fetch + 재파싱 + 재색인이 필요해 비용이
                크므로 옵트인이다.
            force: include_registered=True 일 때, 해시가 같아도 강제
                재색인할지 여부. include_registered=False 면 무시된다.

        Returns:
            synced(조회 건수), added, updated, removed, failed_sources 를 담은
            dict. failed_sources 의 각 원소는 "<project>/<source>" 형식이다.
            일부 소스만 실패하면 성공한 소스의 변경분은 그대로 반영되고
            실패한 항목이 failed_sources 에 담긴다. 대상이 하나도 구성돼
            있지 않거나 전부 실패하면 error/code/message 필드를 담은
            ErrorPayload를 대신 반환한다.
            include_registered=True 면 registered 키(total/reindexed/
            skipped/failed)가 추가된다. failed 는 resync 에 실패한
            document_id 목록이며, 개별 실패는 다른 문서 처리를 막지 않는다.
            include_registered=False 면 registered 키 자체가 없다(하위호환).
        """
        def _sync() -> RefreshIndexResult | ErrorPayload:
            def _inner(bundle) -> RefreshIndexResult:
                payload = _to_refresh_payload(
                    bundle.document_index_service.refresh(source=source, project=project)
                )
                if include_registered:
                    payload["registered"] = _resync_registered(bundle, project=project, force=force)
                return payload
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def register_drive_source(
        project: str, folder_id: str
    ) -> RegisterDriveSourceResult | ErrorPayload:
        """프로젝트에 Google Drive 폴더를 매핑한다.

        같은 project 로 다시 호출하면 folder_id 가 교체된다(upsert).

        Args:
            project: 매핑할 프로젝트 식별자.
            folder_id: Google Drive 폴더 ID.

        Returns:
            project, folder_id, status("created" 또는 "updated")를 담은 dict.
            project 나 folder_id 가 비었으면 error/code/message 필드를 담은
            ErrorPayload(code="validation_error")를 대신 반환한다.
        """
        def _sync() -> RegisterDriveSourceResult | ErrorPayload:
            def _inner(bundle: ServiceBundle) -> RegisterDriveSourceResult:
                row, status = bundle.project_source_service.register(project, "drive", folder_id)
                return {"project": row.project, "folder_id": row.location, "status": status}
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def list_drive_sources(
        project: str | None = None,
    ) -> DriveSourceListResult | ErrorPayload:
        """등록된 프로젝트→Drive 폴더 매핑 목록을 반환한다.

        Args:
            project: 특정 프로젝트의 매핑만 보고 싶을 때 지정. 생략하면 전체.

        Returns:
            items 키에 project/folder_id/created_at/updated_at 을 갖는 항목
            리스트를 담은 dict. project 오름차순으로 결정적으로 정렬된다.
        """
        def _sync() -> DriveSourceListResult | ErrorPayload:
            def _inner(bundle: ServiceBundle) -> DriveSourceListResult:
                if project is not None:
                    row = bundle.project_source_service.get(project, "drive")
                    rows = [row] if row is not None else []
                else:
                    rows = list(bundle.project_source_service.list_by_type("drive"))
                return {"items": [_to_drive_source_item(r) for r in rows]}
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def remove_drive_source(project: str) -> RemoveDriveSourceResult | ErrorPayload:
        """프로젝트의 Drive 폴더 매핑을 제거한다.

        등록돼 있지 않은 project 를 지정해도 오류가 아니라 removed=False 를
        반환한다(멱등).

        Args:
            project: 매핑을 제거할 프로젝트 식별자.

        Returns:
            project, removed(bool)를 담은 dict.
        """
        def _sync() -> RemoveDriveSourceResult | ErrorPayload:
            def _inner(bundle: ServiceBundle) -> RemoveDriveSourceResult:
                normalized_project, removed = bundle.project_source_service.remove(
                    project, "drive"
                )
                return {"project": normalized_project, "removed": removed}
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def register_notion_source(
        project: str, database_id: str
    ) -> RegisterNotionSourceResult | ErrorPayload:
        """프로젝트에 Notion 데이터베이스를 매핑한다.

        같은 project 로 다시 호출하면 database_id 가 교체된다(upsert).

        Args:
            project: 매핑할 프로젝트 식별자.
            database_id: Notion 데이터베이스 ID.

        Returns:
            project, database_id, status("created" 또는 "updated")를 담은
            dict. project 나 database_id 가 비었으면 error/code/message
            필드를 담은 ErrorPayload(code="validation_error")를 대신 반환한다.
        """
        def _sync() -> RegisterNotionSourceResult | ErrorPayload:
            def _inner(bundle: ServiceBundle) -> RegisterNotionSourceResult:
                row, status = bundle.project_source_service.register(
                    project, "notion", database_id, kind="database"
                )
                return {
                    "project": row.project,
                    "database_id": row.location,
                    "status": status,
                }
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def register_notion_page(
        project: str, page_id: str
    ) -> RegisterNotionPageResult | ErrorPayload:
        """프로젝트에 Notion 허브 페이지를 매핑한다.

        `register_notion_source`(데이터베이스)와 달리, 지정한 페이지 바로
        아래(1단계, 재귀 없음)의 하위 페이지들을 검색 대상 문서로 삼는다.
        같은 project 로 다시 호출하면 page_id 가 교체된다(upsert). 한
        project 는 database 매핑과 page 매핑을 동시에 가질 수 없다 —
        나중 호출이 이전 매핑을 덮어쓴다.

        Args:
            project: 매핑할 프로젝트 식별자.
            page_id: 허브로 쓸 Notion 페이지 ID.

        Returns:
            project, page_id, status("created" 또는 "updated")를 담은 dict.
            project 나 page_id 가 비었으면 error/code/message 필드를 담은
            ErrorPayload(code="validation_error")를 대신 반환한다.
        """
        def _sync() -> RegisterNotionPageResult | ErrorPayload:
            def _inner(bundle: ServiceBundle) -> RegisterNotionPageResult:
                row, status = bundle.project_source_service.register_page(project, page_id)
                return {
                    "project": row.project,
                    "page_id": row.location,
                    "status": status,
                }
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def list_notion_sources(
        project: str | None = None,
    ) -> NotionSourceListResult | ErrorPayload:
        """등록된 프로젝트→Notion 데이터베이스/페이지 매핑 목록을 반환한다.

        Args:
            project: 특정 프로젝트의 매핑만 보고 싶을 때 지정. 생략하면 전체.

        Returns:
            items 키에 project/database_id/kind/created_at/updated_at 을 갖는
            항목 리스트를 담은 dict. `kind` 는 "database" 또는 "page"이며,
            page 매핑이어도 값은 `database_id` 필드에 담긴다. project
            오름차순으로 결정적으로 정렬된다.
        """
        def _sync() -> NotionSourceListResult | ErrorPayload:
            def _inner(bundle: ServiceBundle) -> NotionSourceListResult:
                if project is not None:
                    row = bundle.project_source_service.get(project, "notion")
                    rows = [row] if row is not None else []
                else:
                    rows = list(bundle.project_source_service.list_by_type("notion"))
                return {"items": [_to_notion_source_item(r) for r in rows]}
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def remove_notion_source(project: str) -> RemoveNotionSourceResult | ErrorPayload:
        """프로젝트의 Notion 데이터베이스 매핑을 제거한다.

        등록돼 있지 않은 project 를 지정해도 오류가 아니라 removed=False 를
        반환한다(멱등).

        Args:
            project: 매핑을 제거할 프로젝트 식별자.

        Returns:
            project, removed(bool)를 담은 dict.
        """
        def _sync() -> RemoveNotionSourceResult | ErrorPayload:
            def _inner(bundle: ServiceBundle) -> RemoveNotionSourceResult:
                normalized_project, removed = bundle.project_source_service.remove(
                    project, "notion"
                )
                return {"project": normalized_project, "removed": removed}
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)


def _resync_registered(
    bundle: ServiceBundle, *, project: str | None, force: bool
) -> RegisteredResyncResult:
    """URL 기반 Document 를 순회하며 개별 resync 하고 결과를 집계한다.

    문서 하나가 실패해도 나머지는 계속 진행한다(부분 실패 허용). resync 는
    문서마다 자체 커밋하므로 한 문서의 실패가 다른 문서 결과를 롤백하지 않는다.
    """
    documents = bundle.document_repo.list_resyncable(project)
    reindexed = 0
    skipped = 0
    failed: list[str] = []
    for document in documents:
        try:
            result = bundle.sync_service.resync(document.id, force=force)
        except (DomainError, IntegrationError) as e:
            _LOG.error("registered resync failed: document_id=%s", document.id, exc_info=e)
            bundle.session.rollback()
            failed.append(document.id)
            continue
        if result.status == "reindexed":
            reindexed += 1
        else:
            skipped += 1
    return {
        "total": len(documents),
        "reindexed": reindexed,
        "skipped": skipped,
        "failed": failed,
    }
