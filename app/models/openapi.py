"""OpenAPI 전용 ORM 모델.

- api_endpoint / api_parameter / api_request_body / api_response / api_schema
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.document import ApiDocument


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


class ApiEndpoint(Base):
    """API 엔드포인트(METHOD + PATH) 단위 ORM 모델."""

    __tablename__ = "api_endpoint"
    __table_args__ = (UniqueConstraint("document_id", "method", "path", name="uq_endpoint_doc"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("api_document.id", ondelete="CASCADE"))
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
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

    endpoint: Mapped[ApiEndpoint] = relationship(back_populates="responses")

    @property
    def schema(self) -> dict[str, Any]:
        """저장된 schema_json 을 dict 로 디코딩해 반환한다."""
        return _decode_json_dict(self.schema_json)

    @schema.setter
    def schema(self, value: dict[str, Any]) -> None:
        """응답 스키마 dict 를 JSON 문자열로 저장한다."""
        self.schema_json = json.dumps(value)


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
