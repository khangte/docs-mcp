"""SQLAlchemy ORM 모델.

- api_document / api_endpoint / api_parameter / api_request_body / api_response
- api_schema / api_chunk / document_sync_history
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
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

EMBEDDING_DIM = 256

#: 프로젝트 미지정 시 사용하는 기본 프로젝트명.
DEFAULT_PROJECT = "default"
#: `project` 컬럼 최대 길이.
PROJECT_MAX_LENGTH = 128

# public 스키마는 pgvector 확장 전용으로 남겨두고, 애플리케이션 테이블은
# 전용 스키마(app)에 둔다. public 에 동일 이름 테이블이 있으면 SQLAlchemy
# create_all() 의 checkfirst 가 search_path 상의 다른 스키마 테이블을
# "이미 존재"로 오판해 DDL을 건너뛰는 문제를 방지한다.
SCHEMA = "app"


class Base(DeclarativeBase):
    """모든 ORM 모델의 베이스 클래스."""

    metadata = MetaData(schema=SCHEMA)


def _decode_json_dict(raw: str | None) -> dict[str, Any]:
    """JSON 문자열을 dict 로 디코딩한다. 실패 시 빈 dict 를 반환한다."""
    try:
        return dict(json.loads(raw or "{}"))
    except json.JSONDecodeError:
        return {}


def _decode_json_any(raw: str | None) -> Any:
    """JSON 문자열을 임의 타입으로 디코딩한다. None 이거나 실패 시 None 을 반환한다."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _utcnow() -> datetime:
    """현재 UTC 시각을 반환한다."""
    return datetime.now(timezone.utc)


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


class ApiEndpoint(Base):
    """API 엔드포인트(METHOD + PATH) 단위 ORM 모델."""

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
        """저장된 tags_json 을 파싱해 태그 리스트를 반환한다."""
        try:
            return list(json.loads(self.tags_json or "[]"))
        except (json.JSONDecodeError, TypeError):
            return []

    @tags.setter
    def tags(self, value: list[str]) -> None:
        """태그 리스트를 JSON 문자열로 직렬화해 저장한다."""
        self.tags_json = json.dumps(list(value))


class ApiParameter(Base):
    """엔드포인트 파라미터(path/query/header/cookie) ORM 모델."""

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
        """저장된 schema_json 을 dict 로 디코딩해 반환한다."""
        return _decode_json_dict(self.schema_json)

    @schema.setter
    def schema(self, value: dict[str, Any]) -> None:
        """파라미터 스키마 dict 를 JSON 문자열로 직렬화해 저장한다."""
        self.schema_json = json.dumps(value)


class ApiRequestBody(Base):
    """엔드포인트 요청 본문 ORM 모델."""

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
        """저장된 schema_json 을 dict 로 디코딩해 반환한다."""
        return _decode_json_dict(self.schema_json)

    @schema.setter
    def schema(self, value: dict[str, Any]) -> None:
        """요청 바디 스키마 dict 를 JSON 문자열로 저장한다."""
        self.schema_json = json.dumps(value)

    @property
    def example(self) -> Any:
        """저장된 example_json 을 디코딩해 반환한다."""
        return _decode_json_any(self.example_json)

    @example.setter
    def example(self, value: Any) -> None:
        """예시 값을 JSON 문자열로 직렬화해 저장한다."""
        self.example_json = None if value is None else json.dumps(value)


class ApiResponse(Base):
    """엔드포인트 응답 ORM 모델."""

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
        """저장된 schema_json 을 dict 로 디코딩해 반환한다."""
        return _decode_json_dict(self.schema_json)

    @schema.setter
    def schema(self, value: dict[str, Any]) -> None:
        """응답 스키마 dict 를 JSON 문자열로 저장한다."""
        self.schema_json = json.dumps(value)

    @property
    def example(self) -> Any:
        """저장된 example_json 을 디코딩해 반환한다."""
        return _decode_json_any(self.example_json)

    @example.setter
    def example(self, value: Any) -> None:
        """예시 값을 JSON 문자열로 직렬화해 저장한다."""
        self.example_json = None if value is None else json.dumps(value)


class ApiSchema(Base):
    """문서 단위 컴포넌트 스키마 ORM 모델."""

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
        """저장된 json_schema 를 dict 로 디코딩해 반환한다."""
        return _decode_json_dict(self.json_schema)


class ApiSection(Base):
    """텍스트 문서(Markdown/CSV 등)의 제목+본문 단위 ORM 모델."""

    __tablename__ = "api_section"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("api_document.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    document: Mapped[ApiDocument] = relationship(back_populates="sections")


class ApiChunk(Base):
    """검색용 청크(텍스트 + 임베딩) ORM 모델.

    embedding 컬럼 차원은 pgvector 제약상 테이블 생성 시 고정된다.
    EMBEDDING_DIM 변경 시 새 마이그레이션(컬럼 재생성 + 데이터 재색인)이 필요하다.
    """

    __tablename__ = "api_chunk"
    __table_args__ = (
        Index(
            "ix_api_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("api_document.id", ondelete="CASCADE"))
    chunk_type: Mapped[str] = mapped_column(String(32), nullable=False)  # endpoint|schema
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    document: Mapped[ApiDocument] = relationship(back_populates="chunks")


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


def create_all(engine: Any) -> None:
    """스키마와 테이블 전체 생성 (초기 기동·테스트용).

    `Base.metadata` 에 모든 테이블이 등록돼 있어야 하므로, 별도 모듈로 분리된
    모델(`document_meta`)을 여기서 import 해 메타데이터 등록을 보장한다.
    """
    from sqlalchemy import text

    import app.models.document_meta  # noqa: F401  (Base.metadata 등록 목적)
    import app.models.project_drive_source  # noqa: F401  (Base.metadata 등록 목적)
    import app.models.project_notion_source  # noqa: F401  (Base.metadata 등록 목적)

    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    Base.metadata.create_all(engine)
