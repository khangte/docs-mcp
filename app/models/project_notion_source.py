"""프로젝트별 Notion 소스 매핑 ORM 모델."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.openapi import PROJECT_MAX_LENGTH, Base


def _utcnow() -> datetime:
    """현재 UTC 시각을 반환한다."""
    return datetime.now(timezone.utc)


class ProjectNotionSource(Base):
    """프로젝트 하나에 매핑된 Notion 데이터베이스 또는 페이지.

    한 프로젝트는 database 또는 page 중 하나만 가진다(동시 불가). `kind` 가
    `"page"` 이면 `database_id` 컬럼에는 page_id 가 저장된다(Notion object id
    형식이 동일해 값 컬럼을 재사용 — 별도 컬럼을 두면 상호배타 nullable 2컬럼이
    생겨 응집도가 낮아진다).

    Attributes:
        project: 소속 프로젝트명(기본키).
        database_id: Notion 데이터베이스 ID 또는(kind="page" 일 때) 페이지 ID.
        kind: `"database"` 또는 `"page"`.
        created_at: 매핑 생성 시각.
        updated_at: 매핑 최종 수정 시각.
    """

    __tablename__ = "project_notion_source"

    project: Mapped[str] = mapped_column(String(PROJECT_MAX_LENGTH), primary_key=True)
    database_id: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="database")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )
