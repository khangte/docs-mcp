"""문서 등록/조회 DTO."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.openapi import DEFAULT_PROJECT


class RegisterDocumentRequest(BaseModel):
    """문서 등록 요청 본문."""

    project: str = DEFAULT_PROJECT
    source_url: str | None = None
    raw_document: str | None = None
    title_override: str | None = None
    doc_type: str | None = None  # openapi|markdown|csv, 생략 시 자동 판별

    @model_validator(mode="after")
    def _xor_source(self) -> "RegisterDocumentRequest":
        """source_url 과 raw_document 중 정확히 하나만 주어졌는지 검증한다."""
        has_url = self.source_url is not None and self.source_url.strip() != ""
        has_raw = self.raw_document is not None and self.raw_document.strip() != ""
        if has_url == has_raw:  # 둘 다 없거나 둘 다 있으면 에러
            raise ValueError("exactly one of source_url or raw_document must be provided")
        return self


class RegisterDocumentResponse(BaseModel):
    """문서 등록 응답."""

    document_id: str
    title: str
    version: str
    source_url: str | None
    doc_type: str
    project: str
    endpoints_count: int
    schemas_count: int
    sections_count: int
    chunks_count: int
    content_hash: str
    indexed_at: datetime


class DocumentSummary(BaseModel):
    """문서 목록용 요약 DTO."""

    document_id: str
    title: str
    version: str
    source_url: str | None
    project: str
    endpoints_count: int
    indexed_at: datetime


class SyncHistoryItem(BaseModel):
    """동기화 이력 한 건을 표현하는 DTO."""

    status: str
    content_hash: str
    error: str | None
    created_at: datetime


class DocumentDetail(DocumentSummary):
    """문서 상세 + 최근 동기화 이력 DTO."""

    sync_history: list[SyncHistoryItem] = Field(default_factory=list)


class DocumentDeleteResponse(BaseModel):
    """문서 삭제 응답."""

    deleted: bool
    document_id: str


class SyncRequest(BaseModel):
    """문서 재동기화 요청 본문."""

    force: bool = False
    raw_override: str | None = None  # 테스트/재색인 목적으로 주입 가능


class SyncResponse(BaseModel):
    """문서 재동기화 응답."""

    document_id: str
    status: str
    previous_hash: str
    new_hash: str
    endpoints_count: int
    chunks_count: int
    details: dict[str, Any] | None = None
