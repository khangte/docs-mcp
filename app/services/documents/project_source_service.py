"""프로젝트→협업 문서 소스(Drive/Notion) 매핑 등록/조회/삭제 서비스.

project 정규화와 값(location) 검증, upsert/삭제 시 커밋을 여기서 공통
처리해, MCP 도구가 저장소를 직접 건드리지 않고 검증 지점을 하나로 모은다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.models.project_source import ProjectSource
from app.repositories.project_source_repository import ProjectSourceRepository
from app.services.project_scope import normalize_project

#: value(folder_id/database_id/page_id) 최대 길이. 모델 컬럼(String(256))과 일치시킨다.
VALUE_MAX_LENGTH = 256

UpsertStatus = Literal["created", "updated"]


class ProjectSourceService:
    """project → 소스 매핑의 등록/조회/삭제를 검증과 함께 수행한다."""

    def __init__(self, session: Session, repo: ProjectSourceRepository) -> None:
        """세션과 저장소를 보관한다."""
        self._session = session
        self._repo = repo

    def register(
        self, project: str, source_type: str, location: str, kind: str | None = None
    ) -> tuple[ProjectSource, UpsertStatus]:
        """project 에 (source_type, location) 을 매핑한다. 이미 있으면 값을 교체(update)한다.

        Returns:
            (매핑 행, "created"|"updated") 튜플.

        Raises:
            ValidationError: project 나 location 이 비었거나 길이 제한을 넘는 경우.
        """
        normalized_project = normalize_project(project, required=True)
        normalized_location = _normalize_value(location)

        existed = self._repo.get(normalized_project, source_type) is not None
        row = self._repo.upsert(normalized_project, source_type, normalized_location, kind)
        self._session.commit()
        return row, ("updated" if existed else "created")

    def register_page(self, project: str, page_id: str) -> tuple[ProjectSource, UpsertStatus]:
        """project 에 Notion 허브 페이지를 매핑한다(kind="page")."""
        return self.register(project, "notion", page_id, kind="page")

    def list_by_type(self, source_type: str) -> Sequence[ProjectSource]:
        """해당 source_type 의 전체 매핑을 project 오름차순으로 반환한다."""
        return self._repo.list_by_type(source_type)

    def get(self, project: str, source_type: str) -> ProjectSource | None:
        """project 를 정규화한 뒤 (project, source_type) 매핑 행 한 건을 조회한다."""
        normalized_project = normalize_project(project, required=True)
        return self._repo.get(normalized_project, source_type)

    def remove(self, project: str, source_type: str) -> tuple[str, bool]:
        """project 의 source_type 매핑을 삭제한다. 등록돼 있지 않았으면 False(멱등, 오류 아님).

        Returns:
            (정규화된 project, removed) 튜플. 호출부가 정규화 이전 원본이
            아니라 실제로 삭제를 시도한 project 값을 응답에 반영할 수 있게 한다.
        """
        normalized_project = normalize_project(project, required=True)
        removed = self._repo.delete(normalized_project, source_type)
        self._session.commit()
        return normalized_project, removed


def _normalize_value(value: str | None) -> str:
    """location 값을 검증한다. 빈 문자열 금지, 256자 상한."""
    normalized = (value or "").strip()
    if not normalized:
        raise ValidationError("value must not be empty")
    if len(normalized) > VALUE_MAX_LENGTH:
        raise ValidationError(f"value must be at most {VALUE_MAX_LENGTH} characters")
    return normalized
