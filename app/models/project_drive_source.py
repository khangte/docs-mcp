"""프로젝트별 Google Drive 소스 매핑 ORM 모델."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.openapi import PROJECT_MAX_LENGTH, Base


def _utcnow() -> datetime:
    """현재 UTC 시각을 반환한다."""
    return datetime.now(timezone.utc)


class ProjectDriveSource(Base):
    """프로젝트 하나에 매핑된 Google Drive 폴더.

    Attributes:
        project: 소속 프로젝트명(기본키).
        folder_id: Google Drive 폴더 ID.
        created_at: 매핑 생성 시각.
        updated_at: 매핑 최종 수정 시각.
    """

    __tablename__ = "project_drive_source"

    project: Mapped[str] = mapped_column(String(PROJECT_MAX_LENGTH), primary_key=True)
    folder_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )
