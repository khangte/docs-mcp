"""문서 등록/조회 DTO."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class RegisterDocumentRequest(BaseModel):
    source_url: str | None = None
    raw_document: str | None = None
    title_override: str | None = None

    @model_validator(mode="after")
    def _xor_source(self) -> "RegisterDocumentRequest":
        has_url = self.source_url is not None and self.source_url.strip() != ""
        has_raw = self.raw_document is not None and self.raw_document.strip() != ""
        if has_url == has_raw:  # 둘 다 없거나 둘 다 있으면 에러
            raise ValueError("exactly one of source_url or raw_document must be provided")
        return self


class RegisterDocumentResponse(BaseModel):
    document_id: str
    title: str
    version: str
    source_url: str | None
    endpoints_count: int
    schemas_count: int
    chunks_count: int
    content_hash: str
    indexed_at: datetime


class DocumentSummary(BaseModel):
    document_id: str
    title: str
    version: str
    source_url: str | None
    endpoints_count: int
    indexed_at: datetime


class SyncHistoryItem(BaseModel):
    status: str
    content_hash: str
    error: str | None
    created_at: datetime


class DocumentDetail(DocumentSummary):
    sync_history: list[SyncHistoryItem] = Field(default_factory=list)


class DocumentDeleteResponse(BaseModel):
    deleted: bool
    document_id: str


class SyncRequest(BaseModel):
    force: bool = False
    raw_override: str | None = None  # 테스트/재색인 목적으로 주입 가능


class SyncResponse(BaseModel):
    document_id: str
    status: str
    previous_hash: str
    new_hash: str
    endpoints_count: int
    chunks_count: int
    details: dict[str, Any] | None = None
