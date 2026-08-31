"""OpenAPI 전용 ORM 모델.

- api_endpoint / api_parameter / api_request_body / api_response / api_schema
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.document import Document


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
    __table_args__ = (
        UniqueConstraint("document_id", "method", "path", name="uq_endpoint_doc"),
        Index("ix_api_endpoint_method_path", "method", "path"),
        Index("ix_api_endpoint_operation_id", "operation_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    summary: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    document: Mapped[Document] = relationship(back_populates="endpoints")
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
    content_type: Mapped[str] = mapped_column(
        String(128), nullable=False, default="application/json"
    )
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
    content_type: Mapped[str] = mapped_column(
        String(128), nullable=False, default="application/json"
    )
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
    document_id: Mapped[str] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    json_schema: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    document: Mapped[Document] = relationship(back_populates="schemas")

    @property
    def schema(self) -> dict[str, Any]:
        """저장된 json_schema 를 dict 로 디코딩해 반환한다."""
        return _decode_json_dict(self.json_schema)


class EndpointBusinessMetadata(Base):
    """엔드포인트 비즈니스 메타데이터(LLM 생성 설명/키워드/사용자 표현) ORM 모델.

    docs/architect-review/52,54: `api_endpoint.id`는 재색인마다 새로 해시되고
    (`indexer_service.py`의 `idx` 포함 키) 재색인 시 행이 전부 삭제-재생성되므로,
    이 테이블은 `api_endpoint`에 FK를 걸지 않고 `(document_id, method, path)`를
    키로 재색인 후에도 값을 보존한다. `document_id`만 문서 삭제에 연동해
    FK+cascade 한다.
    """

    __tablename__ = "endpoint_business_metadata"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "method", "path", name="uq_business_metadata_doc_method_path"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    business_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    keywords_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    user_phrases_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 프롬프트 입력 payload + PROMPT_VERSION 의 sha256. 재생성 판단 키(55 §3) —
    #: generated_at 은 판단에 쓰지 않고 관측용으로만 쓴다.
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    @property
    def keywords(self) -> list[str]:
        """저장된 keywords_json 을 파싱해 키워드 리스트를 반환한다."""
        try:
            return list(json.loads(self.keywords_json or "[]"))
        except (json.JSONDecodeError, TypeError):
            return []

    @keywords.setter
    def keywords(self, value: list[str]) -> None:
        """키워드 리스트를 JSON 문자열로 직렬화해 저장한다."""
        self.keywords_json = json.dumps(list(value))

    @property
    def user_phrases(self) -> list[str]:
        """저장된 user_phrases_json 을 파싱해 사용자 표현 리스트를 반환한다."""
        try:
            return list(json.loads(self.user_phrases_json or "[]"))
        except (json.JSONDecodeError, TypeError):
            return []

    @user_phrases.setter
    def user_phrases(self, value: list[str]) -> None:
        """사용자 표현 리스트를 JSON 문자열로 직렬화해 저장한다."""
        self.user_phrases_json = json.dumps(list(value))
