"""범용 문서 루트 + 비-openapi 부품 + 동기화 이력 ORM 모델.

- api_document(전 포맷 루트) / api_section(markdown/csv) / document_sync_history
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PROJECT_MAX_LENGTH, _utcnow

if TYPE_CHECKING:
    # 순환 임포트 방지용 타입체크 전용 임포트(런타임엔 평가 안 됨) — 관계
    # 대상 클래스는 forward-ref 문자열로 매퍼 설정 시점에 registry 에서
    # 해석된다(§2 참조). mypy 가 forward-ref 이름을 풀려면 이 임포트가 필요하다.
    from app.models.chunk import ApiChunk
    from app.models.openapi import ApiEndpoint, ApiSchema


class ApiDocument(Base):
    """OpenAPI 문서 메타·원문·하위 엔드포인트/스키마/청크/이력 묶음."""

    __tablename__ = "api_document"
    __table_args__ = (Index("ix_api_document_project", "project"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project: Mapped[str] = mapped_column(String(PROJECT_MAX_LENGTH), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False, default="openapi")  # openapi|markdown|csv
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    endpoints: Mapped[list["ApiEndpoint"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    schemas: Mapped[list["ApiSchema"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    chunks: Mapped[list["ApiChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    sections: Mapped[list["ApiSection"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    sync_history: Mapped[list["DocumentSyncHistory"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentSyncHistory.created_at.desc()",
    )


class ApiSection(Base):
    """텍스트 문서(Markdown/CSV 등)의 제목+본문 단위 ORM 모델."""

    __tablename__ = "api_section"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("api_document.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    document: Mapped[ApiDocument] = relationship(back_populates="sections")


class DocumentSyncHistory(Base):
    """문서 동기화 시도 결과 이력 ORM 모델."""

    __tablename__ = "document_sync_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("api_document.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    document: Mapped[ApiDocument] = relationship(back_populates="sync_history")
