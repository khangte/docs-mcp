"""SQLAlchemy ORM 모델.

- api_document / api_endpoint / api_parameter / api_request_body / api_response
- api_schema / api_chunk / document_sync_history
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """모든 ORM 모델의 베이스 클래스."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApiDocument(Base):
    __tablename__ = "api_document"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
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
    sync_history: Mapped[list["DocumentSyncHistory"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentSyncHistory.created_at.desc()",
    )


class ApiEndpoint(Base):
    __tablename__ = "api_endpoint"
    __table_args__ = (UniqueConstraint("document_id", "method", "path", name="uq_endpoint_doc"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("api_document.id", ondelete="CASCADE"))
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    summary: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    document: Mapped[ApiDocument] = relationship(back_populates="endpoints")
    parameters: Mapped[list["ApiParameter"]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )
    responses: Mapped[list["ApiResponse"]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )
    request_body: Mapped["ApiRequestBody | None"] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan", uselist=False
    )

    @property
    def tags(self) -> list[str]:
        try:
            return list(json.loads(self.tags_json or "[]"))
        except json.JSONDecodeError:
            return []

    @tags.setter
    def tags(self, value: list[str]) -> None:
        self.tags_json = json.dumps(list(value))


class ApiParameter(Base):
    __tablename__ = "api_parameter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("api_endpoint.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    location: Mapped[str] = mapped_column(String(32), nullable=False)  # path/query/header/cookie
    required: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    schema_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)

    endpoint: Mapped[ApiEndpoint] = relationship(back_populates="parameters")

    @property
    def schema(self) -> dict[str, Any]:
        try:
            return dict(json.loads(self.schema_json or "{}"))
        except json.JSONDecodeError:
            return {}

    @schema.setter
    def schema(self, value: dict[str, Any]) -> None:
        self.schema_json = json.dumps(value)


class ApiRequestBody(Base):
    __tablename__ = "api_request_body"

    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("api_endpoint.id", ondelete="CASCADE"), primary_key=True
    )
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/json")
    schema_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    schema_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    required: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    example_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    endpoint: Mapped[ApiEndpoint] = relationship(back_populates="request_body")

    @property
    def schema(self) -> dict[str, Any]:
        try:
            return dict(json.loads(self.schema_json or "{}"))
        except json.JSONDecodeError:
            return {}

    @schema.setter
    def schema(self, value: dict[str, Any]) -> None:
        self.schema_json = json.dumps(value)

    @property
    def example(self) -> Any:
        if self.example_json is None:
            return None
        try:
            return json.loads(self.example_json)
        except json.JSONDecodeError:
            return None

    @example.setter
    def example(self, value: Any) -> None:
        self.example_json = None if value is None else json.dumps(value)


class ApiResponse(Base):
    __tablename__ = "api_response"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("api_endpoint.id", ondelete="CASCADE"))
    status_code: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/json")
    schema_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    schema_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    example_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    endpoint: Mapped[ApiEndpoint] = relationship(back_populates="responses")

    @property
    def schema(self) -> dict[str, Any]:
        try:
            return dict(json.loads(self.schema_json or "{}"))
        except json.JSONDecodeError:
            return {}

    @schema.setter
    def schema(self, value: dict[str, Any]) -> None:
        self.schema_json = json.dumps(value)

    @property
    def example(self) -> Any:
        if self.example_json is None:
            return None
        try:
            return json.loads(self.example_json)
        except json.JSONDecodeError:
            return None

    @example.setter
    def example(self, value: Any) -> None:
        self.example_json = None if value is None else json.dumps(value)


class ApiSchema(Base):
    __tablename__ = "api_schema"
    __table_args__ = (UniqueConstraint("document_id", "name", name="uq_schema_doc_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("api_document.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    json_schema: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    document: Mapped[ApiDocument] = relationship(back_populates="schemas")

    @property
    def schema(self) -> dict[str, Any]:
        try:
            return dict(json.loads(self.json_schema or "{}"))
        except json.JSONDecodeError:
            return {}


class ApiChunk(Base):
    __tablename__ = "api_chunk"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("api_document.id", ondelete="CASCADE"))
    chunk_type: Mapped[str] = mapped_column(String(32), nullable=False)  # endpoint|schema
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    document: Mapped[ApiDocument] = relationship(back_populates="chunks")

    @property
    def embedding(self) -> list[float]:
        try:
            return [float(x) for x in json.loads(self.embedding_json or "[]")]
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

    @embedding.setter
    def embedding(self, value: list[float]) -> None:
        self.embedding_json = json.dumps(list(value))


class DocumentSyncHistory(Base):
    __tablename__ = "document_sync_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("api_document.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    document: Mapped[ApiDocument] = relationship(back_populates="sync_history")


def create_all(engine: Any) -> None:
    """테이블 전체 생성 (초기 기동·테스트용)."""
    Base.metadata.create_all(engine)
