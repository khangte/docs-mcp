"""프로젝트→Drive/Notion 소스 매핑 저장소.

Drive 와 Notion 은 "project 를 PK 로 하고 값 컬럼 하나(folder_id/database_id)를
갖는 매핑 테이블에 upsert 한다"는 동일한 패턴을 반복하므로, 공통 로직을
`ProjectSourceRepositoryBase` 에 두고 대상 모델·컬럼명만 하위 클래스가 지정한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project_drive_source import ProjectDriveSource
from app.models.project_notion_source import ProjectNotionSource

ModelT = TypeVar("ModelT")


class ProjectSourceRepositoryBase(Generic[ModelT]):
    """project → 소스 값 매핑 테이블의 공통 CRUD.

    커밋은 하지 않는다(기존 규약과 일치: 커밋은 서비스가 담당).
    """

    #: 하위 클래스가 지정하는 대상 ORM 모델.
    model: type[ModelT]
    #: 하위 클래스가 지정하는 값 컬럼명(예: "folder_id", "database_id").
    value_attr: str

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def upsert(self, project: str, value: str) -> ModelT:
        """project 행이 있으면 값만 갱신하고, 없으면 새로 만든다.

        기존 행의 `created_at` 은 유지되고 `updated_at` 만 갱신된다(ORM
        `onupdate` 콜백 + 값 변경 시에만 UPDATE 문이 발행됨).
        """
        existing = self.get(project)
        if existing is not None:
            setattr(existing, self.value_attr, value)
            return existing
        row = self.model(project=project, **{self.value_attr: value})  # type: ignore[call-arg]
        self._session.add(row)
        return row

    def get(self, project: str) -> ModelT | None:
        """project 로 매핑 행 한 건을 조회한다."""
        return self._session.get(self.model, project)

    def list_all(self) -> Sequence[ModelT]:
        """전체 매핑을 project 오름차순으로 반환한다(결정적 순서)."""
        stmt = select(self.model).order_by(self.model.project)  # type: ignore[attr-defined]
        return self._session.execute(stmt).scalars().all()

    def delete(self, project: str) -> bool:
        """project 매핑을 삭제한다. 없었으면 False(멱등)."""
        existing = self.get(project)
        if existing is None:
            return False
        self._session.delete(existing)
        return True


class ProjectDriveSourceRepository(ProjectSourceRepositoryBase[ProjectDriveSource]):
    """`project_drive_source` CRUD."""

    model = ProjectDriveSource
    value_attr = "folder_id"


class ProjectNotionSourceRepository(ProjectSourceRepositoryBase[ProjectNotionSource]):
    """`project_notion_source` CRUD."""

    model = ProjectNotionSource
    value_attr = "database_id"

    def upsert_kind(self, project: str, value: str, kind: str) -> ProjectNotionSource:
        """value(database_id 또는 page_id)와 함께 kind 도 세팅해 upsert 한다.

        `kind` 가 바뀌는 경우(예: database → page 재등록)도 이 메서드로
        갱신된다. 기본 `upsert()` 는 값 컬럼만 다루므로 kind 는 여기서 별도로
        반영한다.
        """
        row = self.upsert(project, value)
        row.kind = kind
        return row
