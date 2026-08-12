"""프로젝트→협업 문서 소스(Drive/Notion) 매핑 저장소.

`project_source` 테이블의 PK 는 `(project, source_type, location)` 이지만,
이 저장소는 `(project, source_type)` 단위로 upsert 를 취급한다(§0 스코프
결정 — "타입당 1개 유지"). 진짜 멀티 소스(같은 프로젝트에 같은 타입 소스
여러 개)는 이 물결의 범위 밖이다.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project_source import ProjectSource


class ProjectSourceRepository:
    """`project_source` CRUD.

    커밋은 하지 않는다(커밋은 서비스가 담당).
    """

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def upsert(
        self, project: str, source_type: str, location: str, kind: str | None = None
    ) -> ProjectSource:
        """(project, source_type) 행이 있으면 location/kind 만 갱신하고, 없으면 새로 만든다.

        기존 행의 `created_at` 은 유지되고 `updated_at` 만 갱신된다(ORM
        `onupdate` 콜백 + 값 변경 시에만 UPDATE 문이 발행됨).
        """
        existing = self.get(project, source_type)
        if existing is not None:
            existing.location = location
            existing.kind = kind
            return existing
        row = ProjectSource(project=project, source_type=source_type, location=location, kind=kind)
        self._session.add(row)
        return row

    def get(self, project: str, source_type: str) -> ProjectSource | None:
        """(project, source_type) 로 매핑 행 한 건을 조회한다."""
        stmt = select(ProjectSource).where(
            ProjectSource.project == project, ProjectSource.source_type == source_type
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def list_by_type(self, source_type: str) -> Sequence[ProjectSource]:
        """해당 source_type 의 전체 매핑을 project 오름차순으로 반환한다."""
        stmt = (
            select(ProjectSource)
            .where(ProjectSource.source_type == source_type)
            .order_by(ProjectSource.project)
        )
        return self._session.execute(stmt).scalars().all()

    def list_all(self) -> Sequence[ProjectSource]:
        """전체 매핑을 (project, source_type) 오름차순으로 반환한다(결정적 순서)."""
        stmt = select(ProjectSource).order_by(ProjectSource.project, ProjectSource.source_type)
        return self._session.execute(stmt).scalars().all()

    def delete(self, project: str, source_type: str) -> bool:
        """(project, source_type) 매핑을 삭제한다. 없었으면 False(멱등)."""
        existing = self.get(project, source_type)
        if existing is None:
            return False
        self._session.delete(existing)
        return True
