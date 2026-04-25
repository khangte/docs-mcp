"""문서 등록/재색인 오케스트레이터."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.core.errors import (
    DocumentNotFoundError,
    DuplicateDocumentError,
    ValidationError,
)
from src.models.openapi import ApiDocument, DocumentSyncHistory
from src.repositories.chunk_repository import ChunkRepository
from src.repositories.document_repository import DocumentRepository
from src.repositories.endpoint_repository import EndpointRepository
from src.repositories.sync_history_repository import SyncHistoryRepository
from src.services.indexer.indexer_service import IndexerService
from src.services.indexer.vector_index import InMemoryVectorIndex
from src.services.ingestor.openapi_fetcher import OpenAPIFetcher
from src.services.parser.openapi_parser import parse_document


@dataclass
class RegistrationResult:
    """문서 등록/재색인 결과 요약."""

    document: ApiDocument
    endpoints_count: int
    schemas_count: int
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
        vector_index: InMemoryVectorIndex,
    ) -> None:
        """세션·저장소·인덱서·fetcher·벡터 인덱스 의존성을 보관한다."""
        self._session = session
        self._document_repo = document_repo
        self._endpoint_repo = endpoint_repo
        self._chunk_repo = chunk_repo
        self._sync_history_repo = sync_history_repo
        self._indexer = indexer
        self._fetcher = fetcher
        self._vector_index = vector_index

    def register(
        self,
        *,
        source_url: str | None,
        raw_document: str | None,
        title_override: str | None = None,
    ) -> RegistrationResult:
        """원본 URL 또는 원문을 받아 수집·파싱·색인하고 신규 문서로 등록한다."""
        if (source_url is None) == (raw_document is None):
            raise ValidationError("exactly one of source_url or raw_document must be provided")

        if source_url:
            existing = self._document_repo.find_by_source_url(source_url)
            if existing is not None:
                raise DuplicateDocumentError(source_url)
            raw = self._fetcher.fetch(source_url)
        else:
            raw = raw_document or ""

        parsed = parse_document(raw)
        content_hash = _hash(raw)

        document = ApiDocument(
            id=_new_id(),
            source_url=source_url,
            title=title_override or parsed.title,
            version=parsed.version,
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
            return RegistrationResult(
                document=document,
                endpoints_count=len(endpoints),
                schemas_count=0,
                chunks_count=len(chunks),
                content_hash=new_hash,
                previous_hash=previous_hash,
                status="skipped",
            )

        parsed = parse_document(raw)

        # 기존 청크 + 엔드포인트/스키마/파라미터/응답/요청바디 전부 제거 (cascade)
        existing_chunks = self._chunk_repo.list_by_document(document_id)
        chunk_ids_to_remove = [c.id for c in existing_chunks]
        self._chunk_repo.delete_by_document(document_id)
        existing_endpoints = list(self._endpoint_repo.list_by_document(document_id))
        for ep in existing_endpoints:
            self._session.delete(ep)
        # 스키마 제거
        from src.models.openapi import ApiSchema
        from sqlalchemy import delete as sa_delete

        self._session.execute(sa_delete(ApiSchema).where(ApiSchema.document_id == document_id))
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

        self._sync_history_repo.add(
            DocumentSyncHistory(
                document_id=document_id,
                status="reindexed",
                content_hash=new_hash,
            )
        )
        self._session.commit()
        # 벡터 인덱스에서 이전 청크 제거 (새 청크는 indexer 가 upsert)
        self._vector_index.delete_many(chunk_ids_to_remove)

        return RegistrationResult(
            document=document,
            endpoints_count=endpoints_count,
            schemas_count=schemas_count,
            chunks_count=chunks_count,
            content_hash=new_hash,
            previous_hash=previous_hash,
            status="reindexed",
        )

    def delete(self, document_id: str) -> None:
        """문서를 DB 에서 제거하고 벡터 인덱스에서도 관련 청크를 제거한다."""
        document = self._document_repo.get(document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)
        chunk_ids = [c.id for c in self._chunk_repo.list_by_document(document_id)]
        self._document_repo.delete(document)
        self._session.commit()
        self._vector_index.delete_many(chunk_ids)


def _new_id() -> str:
    """16자리 hex 형태의 신규 문서 ID 를 생성한다."""
    return uuid.uuid4().hex[:16]


def _hash(raw: str) -> str:
    """원문 문자열의 SHA-256 해시를 hex 로 반환한다."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
