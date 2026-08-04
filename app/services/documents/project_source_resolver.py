"""project → Drive/Notion 어댑터 요청 시점 팩토리 (SPEC 기능 5).

`project_drive_source`/`project_notion_source` 매핑 테이블을 조회해, 그
project 가 실제로 쓸 수 있는 `DocumentSource` 어댑터를 그때그때 만들어낸다.
서비스 계정 자격증명(Drive)과 Integration Token(Notion)은 서버 전역에서
공유하는 `drive_token_provider`/`notion` 설정을 그대로 재사용하고, project
마다 달라지는 것은 folder_id/database_id 뿐이다.

resolver 인스턴스(=요청 1회) 안에서 같은 folder_id/database_id 에 대한
어댑터를 캐싱해, 여러 project 가 같은 폴더/DB 를 공유하거나 `resolve_all()`
로 반복 조회할 때 중복 생성을 막는다.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import Settings
from app.repositories.project_source_repository import (
    ProjectDriveSourceRepository,
    ProjectNotionSourceRepository,
)
from app.services.documents.sources.document_source import DocumentSource
from app.services.documents.sources.google_drive_source import ServiceAccountTokenProvider
from app.services.documents.sources.source_factory import build_drive_source, build_notion_source


class ProjectSourceResolver:
    """project → Drive/Notion `DocumentSource` 어댑터를 요청 시점에 만든다."""

    def __init__(
        self,
        settings: Settings,
        drive_token_provider: ServiceAccountTokenProvider | None,
        drive_repo: ProjectDriveSourceRepository,
        notion_repo: ProjectNotionSourceRepository,
        drive_source_builder: Callable[[str], DocumentSource | None] | None = None,
        notion_source_builder: Callable[[str, str], DocumentSource | None] | None = None,
    ) -> None:
        """설정·Drive 토큰 발급기·매핑 저장소를 보관하고 어댑터 캐시를 초기화한다.

        `drive_source_builder`/`notion_source_builder` 를 주입하면 기본
        `build_drive_source`/`build_notion_source` 대신 그 콜백으로 어댑터를
        만든다(테스트에서 페이크를 주입하는 지점, `AppState.drive_source_builder`
        /`notion_source_builder` 의 후계). `notion_source_builder` 는
        `(notion_id, kind)` 두 인자를 받는다.
        """
        self._settings = settings
        self._drive_token_provider = drive_token_provider
        self._drive_repo = drive_repo
        self._notion_repo = notion_repo
        self._drive_source_builder = drive_source_builder
        self._notion_source_builder = notion_source_builder
        self._drive_cache: dict[str, DocumentSource | None] = {}
        self._notion_cache: dict[tuple[str, str], DocumentSource | None] = {}

    def resolve_for_project(self, project: str) -> dict[str, DocumentSource]:
        """그 project 에서 쓸 수 있는 소스만 담은 매핑을 반환한다.

        Drive 매핑이 없거나 자격증명이 없으면 `drive` 키가, Notion 매핑이
        없거나 토큰이 없으면 `notion` 키가 아예 존재하지 않는다.
        """
        sources: dict[str, DocumentSource] = {}

        drive_row = self._drive_repo.get(project)
        if drive_row is not None:
            drive_source = self._drive_source_for(drive_row.folder_id)
            if drive_source is not None:
                sources[drive_source.source_name] = drive_source

        notion_row = self._notion_repo.get(project)
        if notion_row is not None:
            notion_source = self._notion_source_for(notion_row.database_id, notion_row.kind)
            if notion_source is not None:
                sources[notion_source.source_name] = notion_source

        return sources

    def resolve_all(self) -> list[tuple[str, DocumentSource]]:
        """등록된 모든 project 의 (project, source) 쌍을 반환한다.

        Drive 는 `project_drive_source` 전 행에서, Notion 은
        `project_notion_source` 전 행에서 각각 만든다. 자격증명이 없어
        어댑터를 만들 수 없는 행은 조용히 제외된다.
        """
        pairs: list[tuple[str, DocumentSource]] = []
        for drive_row in self._drive_repo.list_all():
            drive_source = self._drive_source_for(drive_row.folder_id)
            if drive_source is not None:
                pairs.append((drive_row.project, drive_source))
        for notion_row in self._notion_repo.list_all():
            notion_source = self._notion_source_for(notion_row.database_id, notion_row.kind)
            if notion_source is not None:
                pairs.append((notion_row.project, notion_source))
        return pairs

    def _drive_source_for(self, folder_id: str) -> DocumentSource | None:
        """folder_id 에 대한 Drive 어댑터를 캐시에서 찾거나 새로 만든다."""
        if folder_id not in self._drive_cache:
            if self._drive_source_builder is not None:
                self._drive_cache[folder_id] = self._drive_source_builder(folder_id)
            else:
                self._drive_cache[folder_id] = build_drive_source(
                    self._settings, folder_id, self._drive_token_provider
                )
        return self._drive_cache[folder_id]

    def _notion_source_for(self, notion_id: str, kind: str) -> DocumentSource | None:
        """(notion_id, kind) 에 대한 Notion 어댑터를 캐시에서 찾거나 새로 만든다."""
        cache_key = (notion_id, kind)
        if cache_key not in self._notion_cache:
            if self._notion_source_builder is not None:
                self._notion_cache[cache_key] = self._notion_source_builder(notion_id, kind)
            else:
                self._notion_cache[cache_key] = build_notion_source(
                    self._settings, notion_id, kind
                )
        return self._notion_cache[cache_key]
