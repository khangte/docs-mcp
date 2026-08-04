"""문서 등록/재색인 오케스트레이터."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from sqlalchemy import delete as sa_delete

from app.core.errors import (
    DocumentNotFoundError,
    DuplicateDocumentError,
    ValidationError,
)
from app.models.openapi import ApiDocument, ApiSchema, ApiSection, DocumentSyncHistory
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.repositories.sync_history_repository import SyncHistoryRepository
from app.services.project_scope import normalize_project
from app.services.indexer.indexer_service import IndexerService
from app.services.ingestor.openapi_fetcher import OpenAPIFetcher
from app.services.parser.document_router import detect_doc_type, extract_text, parse_document


@dataclass
class RegistrationResult:
    """문서 등록/재색인 결과 요약."""

    document: ApiDocument
    endpoints_count: int
    schemas_count: int
    sections_count: int
    chunks_count: int
    content_hash: str
    status: str  # "registered" | "reindexed" | "skipped"
    previous_hash: str = ""


class SyncService:
    """수집 → 파싱 → 색인 전체 파이프라인 오케스트레이션."""

    def __init__(
        self,
        session: Session,
        document_repo: DocumentRepository,
        endpoint_repo: EndpointRepository,
        chunk_repo: ChunkRepository,
        sync_history_repo: SyncHistoryRepository,
        indexer: IndexerService,
        fetcher: OpenAPIFetcher,
    ) -> None:
        """세션·저장소·인덱서·fetcher 의존성을 보관한다."""
        self._session = session
        self._document_repo = document_repo
        self._endpoint_repo = endpoint_repo
        self._chunk_repo = chunk_repo
        self._sync_history_repo = sync_history_repo
        self._indexer = indexer
        self._fetcher = fetcher

    def register(
        self,
        *,
        project: str,
        source_url: str | None,
        raw_document: str | None,
        title_override: str | None = None,
        doc_type: str | None = None,
    ) -> RegistrationResult:
        """원본 URL 또는 원문을 받아 수집·파싱·색인하고 신규 문서로 등록한다."""
        normalized_project = normalize_project(project, required=True)
        if (source_url is None) == (raw_document is None):
            raise ValidationError("exactly one of source_url or raw_document must be provided")
        if source_url is not None and doc_type in ("pdf", "docx"):
            raise ValidationError(
                "pdf/docx documents must be provided as base64 raw_document, not source_url"
            )

        if source_url:
            existing = self._document_repo.find_by_source_url(source_url)
            if existing is not None:
                raise DuplicateDocumentError(source_url)
            raw = self._fetcher.fetch(source_url)
        else:
            raw = raw_document or ""

        resolved_doc_type = doc_type or detect_doc_type(raw, source_url)
        if resolved_doc_type in ("pdf", "docx"):
            raw = extract_text(raw, resolved_doc_type)
        parsed = parse_document(raw, resolved_doc_type, title_hint=title_override)
        content_hash = _hash(raw)

        document = ApiDocument(
            id=_new_id(),
            project=normalized_project,
            source_url=source_url,
            title=title_override or parsed.title,
            version=parsed.version,
            doc_type=resolved_doc_type,
            content_hash=content_hash,
            raw_text=raw,
            indexed_at=datetime.now(timezone.utc),
        )
        self._document_repo.add(document)
        self._session.flush()

        endpoints_count, chunks_count = self._indexer.index_document(
            document=document, parsed=parsed, is_reindex=False
        )
        schemas_count = len(parsed.schemas)
        sections_count = len(parsed.sections)

        self._sync_history_repo.add(
            DocumentSyncHistory(
                document_id=document.id,
                status="registered",
                content_hash=content_hash,
            )
        )
        self._session.commit()

        return RegistrationResult(
            document=document,
            endpoints_count=endpoints_count,
            schemas_count=schemas_count,
            sections_count=sections_count,
            chunks_count=chunks_count,
            content_hash=content_hash,
            status="registered",
        )

    def resync(
        self,
        document_id: str,
        *,
        force: bool = False,
        raw_override: str | None = None,
    ) -> RegistrationResult:
        """기존 문서를 다시 가져와 해시가 변하면 재색인하고, 동일하면 skip 으로 기록한다."""
        document = self._document_repo.get(document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)

        previous_hash = document.content_hash
        if raw_override is not None:
            raw = raw_override
        elif document.source_url:
            raw = self._fetcher.fetch(document.source_url)
        else:
            raw = document.raw_text

        new_hash = _hash(raw)
        if new_hash == previous_hash and not force:
            self._sync_history_repo.add(
                DocumentSyncHistory(
                    document_id=document_id,
                    status="skipped",
                    content_hash=new_hash,
                )
            )
            self._session.commit()
            chunks = self._chunk_repo.list_by_document(document_id)
            endpoints = self._endpoint_repo.list_by_document(document_id)
            sections = self._endpoint_repo.list_sections_by_document(document_id)
            return RegistrationResult(
                document=document,
                endpoints_count=len(endpoints),
                schemas_count=0,
                sections_count=len(sections),
                chunks_count=len(chunks),
                content_hash=new_hash,
                previous_hash=previous_hash,
                status="skipped",
            )

        resolved_doc_type = document.doc_type or detect_doc_type(raw, document.source_url)
        parsed = parse_document(raw, resolved_doc_type, title_hint=None)

        # 기존 청크 + 엔드포인트/스키마/섹션/파라미터/응답/요청바디 전부 제거 (cascade)
        self._chunk_repo.delete_by_document(document_id)
        existing_endpoints = list(self._endpoint_repo.list_by_document(document_id))
        for ep in existing_endpoints:
            self._session.delete(ep)
        self._session.execute(sa_delete(ApiSchema).where(ApiSchema.document_id == document_id))
        self._session.execute(sa_delete(ApiSection).where(ApiSection.document_id == document_id))
        self._session.flush()

        document.content_hash = new_hash
        document.raw_text = raw
        document.title = parsed.title or document.title
        document.version = parsed.version or document.version
        document.indexed_at = datetime.now(timezone.utc)

        endpoints_count, chunks_count = self._indexer.index_document(
            document=document, parsed=parsed, is_reindex=True
        )
        schemas_count = len(parsed.schemas)
        sections_count = len(parsed.sections)

        self._sync_history_repo.add(
            DocumentSyncHistory(
                document_id=document_id,
                status="reindexed",
                content_hash=new_hash,
            )
        )
        self._session.commit()

        return RegistrationResult(
            document=document,
            endpoints_count=endpoints_count,
            schemas_count=schemas_count,
            sections_count=sections_count,
            chunks_count=chunks_count,
            content_hash=new_hash,
            previous_hash=previous_hash,
            status="reindexed",
        )

    def delete(self, document_id: str) -> None:
        """문서를 DB 에서 제거한다. 소속 청크는 cascade 로 함께 삭제된다."""
        document = self._document_repo.get(document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)
        self._document_repo.delete(document)
        self._session.commit()


def _new_id() -> str:
    """16자리 hex 형태의 신규 문서 ID 를 생성한다."""
    return uuid.uuid4().hex[:16]


def _hash(raw: str) -> str:
    """원문 문자열의 SHA-256 해시를 hex 로 반환한다."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
