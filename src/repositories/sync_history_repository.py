"""동기화 이력 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.models.openapi import DocumentSyncHistory


class SyncHistoryRepository:
    """`document_sync_history` append-only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: DocumentSyncHistory) -> None:
        self._session.add(record)

    def list_by_document(
        self, document_id: str, limit: int = 10
    ) -> Sequence[DocumentSyncHistory]:
        stmt = (
            select(DocumentSyncHistory)
            .where(DocumentSyncHistory.document_id == document_id)
            .order_by(desc(DocumentSyncHistory.created_at))
            .limit(limit)
        )
        return self._session.execute(stmt).scalars().all()
