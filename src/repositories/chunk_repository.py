"""청크 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.models.openapi import ApiChunk


class ChunkRepository:
    """`api_chunk` CRUD + document 별 일괄 교체."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, chunk: ApiChunk) -> None:
        self._session.add(chunk)

    def bulk_add(self, chunks: Sequence[ApiChunk]) -> None:
        self._session.add_all(list(chunks))

    def delete_by_document(self, document_id: str) -> int:
        stmt = delete(ApiChunk).where(ApiChunk.document_id == document_id)
        result = self._session.execute(stmt)
        return int(result.rowcount or 0)

    def list_all(self) -> Sequence[ApiChunk]:
        stmt = select(ApiChunk)
        return self._session.execute(stmt).scalars().all()

    def list_by_document(self, document_id: str) -> Sequence[ApiChunk]:
        stmt = select(ApiChunk).where(ApiChunk.document_id == document_id)
        return self._session.execute(stmt).scalars().all()
