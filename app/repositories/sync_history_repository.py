"""동기화 이력 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import DocumentSyncHistory


class SyncHistoryRepository:
    """`document_sync_history` append-only."""

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def add(self, record: DocumentSyncHistory) -> None:
        """동기화 이력 한 건을 세션에 추가한다."""
        self._session.add(record)

    def list_by_document(
        self, document_id: str, limit: int = 10
    ) -> Sequence[DocumentSyncHistory]:
        """문서별 최근 이력을 created_at 내림차순으로 limit 건만 반환한다."""
        stmt = (
            select(DocumentSyncHistory)
            .where(DocumentSyncHistory.document_id == document_id)
            .order_by(desc(DocumentSyncHistory.created_at))
            .limit(limit)
        )
        return self._session.execute(stmt).scalars().all()
