"""프로젝트별 협업 문서 소스(Drive/Notion/openapi 예약) 매핑 ORM 모델."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.openapi import PROJECT_MAX_LENGTH, Base


def _utcnow() -> datetime:
    """현재 UTC 시각을 반환한다."""
    return datetime.now(timezone.utc)


class ProjectSource(Base):
    """프로젝트 하나에 매핑된 협업 문서 소스 한 건(source_type 당 1개).

    PK 는 `(project, source_type, location)` 이지만, 서비스 계층은
    `(project, source_type)` 단위 upsert 로 취급한다(기존 "재등록=값 교체"
    의미 유지 — `app/services/documents/project_source_service.py` 참조).
    `location` 을 PK 에 포함한 것은 향후 프로젝트당 동일 타입 소스 복수
    지원 시 재마이그레이션을 피하기 위함이며, 이번 물결에선 실제로
    복수를 허용하지 않는다.

    Attributes:
        project: 소속 프로젝트명.
        source_type: 소스 종류(`drive`|`notion`|`openapi`(예약)).
        location: Drive folder_id / Notion database_id 또는 page_id.
        kind: Notion 전용(`database`|`page`). drive 는 NULL.
        created_at: 매핑 생성 시각.
        updated_at: 매핑 최종 수정 시각.
    """

    __tablename__ = "project_source"

    project: Mapped[str] = mapped_column(String(PROJECT_MAX_LENGTH), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    location: Mapped[str] = mapped_column(String(256), primary_key=True)
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )
