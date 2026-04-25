"""청크 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.models.openapi import ApiChunk


class ChunkRepository:
    """`api_chunk` CRUD + document 별 일괄 교체."""

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def add(self, chunk: ApiChunk) -> None:
        """청크 한 건을 세션에 추가한다."""
        self._session.add(chunk)

    def bulk_add(self, chunks: Sequence[ApiChunk]) -> None:
        """청크 여러 건을 한 번에 세션에 추가한다."""
        self._session.add_all(list(chunks))

    def delete_by_document(self, document_id: str) -> int:
        """주어진 문서의 모든 청크를 삭제하고 삭제된 행 수를 반환한다."""
        stmt = delete(ApiChunk).where(ApiChunk.document_id == document_id)
        result = self._session.execute(stmt)
        return int(result.rowcount or 0)

    def list_all(self) -> Sequence[ApiChunk]:
        """전체 청크를 반환한다."""
        stmt = select(ApiChunk)
        return self._session.execute(stmt).scalars().all()

    def list_by_document(self, document_id: str) -> Sequence[ApiChunk]:
        """특정 문서에 속한 청크를 반환한다."""
        stmt = select(ApiChunk).where(ApiChunk.document_id == document_id)
        return self._session.execute(stmt).scalars().all()
