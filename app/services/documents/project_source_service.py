"""프로젝트→Drive/Notion 소스 매핑 등록/조회/삭제 서비스.

project 정규화와 값(folder_id/database_id) 검증, upsert/삭제 시 커밋을 여기서
공통 처리해, MCP 도구가 저장소를 직접 건드리지 않고 검증 지점을 하나로 모은다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Generic, Literal, TypeVar

from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.models.project_drive_source import ProjectDriveSource
from app.models.project_notion_source import ProjectNotionSource
from app.repositories.project_source_repository import (
    ProjectNotionSourceRepository,
    ProjectSourceRepositoryBase,
)
from app.services.project_scope import normalize_project

#: value(folder_id/database_id) 최대 길이. 모델 컬럼(String(256))과 일치시킨다.
VALUE_MAX_LENGTH = 256

UpsertStatus = Literal["created", "updated"]

ModelT = TypeVar("ModelT")


class ProjectSourceService(Generic[ModelT]):
    """project → 소스 값 매핑의 등록/조회/삭제를 검증과 함께 수행한다."""

    def __init__(self, session: Session, repo: ProjectSourceRepositoryBase[ModelT]) -> None:
        """세션과 저장소를 보관한다."""
        self._session = session
        self._repo = repo

    def register(self, project: str, value: str) -> tuple[ModelT, UpsertStatus]:
        """project 에 값을 매핑한다. 이미 있으면 값을 교체(update)한다.

        Returns:
            (매핑 행, "created"|"updated") 튜플.

        Raises:
            ValidationError: project 나 value 가 비었거나 길이 제한을 넘는 경우.
        """
        normalized_project = normalize_project(project, required=True)
        normalized_value = _normalize_value(value)

        existed = self._repo.get(normalized_project) is not None
        row = self._repo.upsert(normalized_project, normalized_value)
        self._session.commit()
        return row, ("updated" if existed else "created")

    def list_all(self) -> Sequence[ModelT]:
        """전체 매핑을 project 오름차순으로 반환한다."""
        return self._repo.list_all()

    def get(self, project: str) -> ModelT | None:
        """project 를 정규화한 뒤 매핑 행 한 건을 조회한다."""
        normalized_project = normalize_project(project, required=True)
        return self._repo.get(normalized_project)

    def remove(self, project: str) -> tuple[str, bool]:
        """project 매핑을 삭제한다. 등록돼 있지 않았으면 False(멱등, 오류 아님).

        Returns:
            (정규화된 project, removed) 튜플. 호출부가 정규화 이전 원본이
            아니라 실제로 삭제를 시도한 project 값을 응답에 반영할 수 있게 한다.
        """
        normalized_project = normalize_project(project, required=True)
        removed = self._repo.delete(normalized_project)
        self._session.commit()
        return normalized_project, removed


def _normalize_value(value: str | None) -> str:
    """folder_id/database_id 값을 검증한다. 빈 문자열 금지, 256자 상한."""
    normalized = (value or "").strip()
    if not normalized:
        raise ValidationError("value must not be empty")
    if len(normalized) > VALUE_MAX_LENGTH:
        raise ValidationError(f"value must be at most {VALUE_MAX_LENGTH} characters")
    return normalized


class DriveSourceService(ProjectSourceService[ProjectDriveSource]):
    """`ProjectDriveSourceRepository` 전용 얇은 래퍼."""


class NotionSourceService(ProjectSourceService[ProjectNotionSource]):
    """`ProjectNotionSourceRepository` 전용 얇은 래퍼.

    한 프로젝트는 database 또는 page 중 하나만 가진다(같은 project PK 를
    공유). `register()`(기존, database 용)와 `register_page()`(page 용) 는
    각각 `kind` 를 명시적으로 세팅한다.
    """

    def __init__(self, session: Session, repo: ProjectNotionSourceRepository) -> None:
        """Notion 소스 저장소로 고정한 서비스를 만든다."""
        super().__init__(session, repo)
        self._notion_repo = repo

    def register(self, project: str, value: str) -> tuple[ProjectNotionSource, UpsertStatus]:
        """project 에 Notion 데이터베이스를 매핑한다(kind="database")."""
        return self._register(project, value, kind="database")

    def register_page(
        self, project: str, page_id: str
    ) -> tuple[ProjectNotionSource, UpsertStatus]:
        """project 에 Notion 허브 페이지를 매핑한다(kind="page")."""
        return self._register(project, page_id, kind="page")

    def _register(
        self, project: str, value: str, kind: str
    ) -> tuple[ProjectNotionSource, UpsertStatus]:
        """project 정규화·value 검증 후 kind 를 명시해 upsert 한다."""
        normalized_project = normalize_project(project, required=True)
        normalized_value = _normalize_value(value)

        existed = self._notion_repo.get(normalized_project) is not None
        row = self._notion_repo.upsert_kind(normalized_project, normalized_value, kind)
        self._session.commit()
        return row, ("updated" if existed else "created")
