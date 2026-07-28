"""Drive/Notion 문서 메타 캐시 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document_meta import DocumentMeta


class DocumentMetaRepository:
    """`document_meta` CRUD."""

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def add(self, meta: DocumentMeta) -> None:
        """메타 행을 세션에 추가한다."""
        self._session.add(meta)

    def find(self, source: str, external_id: str) -> DocumentMeta | None:
        """(source, external_id) 조합으로 메타 행 한 건을 조회한다."""
        stmt = select(DocumentMeta).where(
            DocumentMeta.source == source,
            DocumentMeta.external_id == external_id,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def list_by_source(self, source: str) -> Sequence[DocumentMeta]:
        """특정 source 의 메타 행을 external_id 오름차순으로 반환한다."""
        stmt = (
            select(DocumentMeta)
            .where(DocumentMeta.source == source)
            .order_by(DocumentMeta.external_id)
        )
        return self._session.execute(stmt).scalars().all()

    def list_all(self, source: str | None = None) -> Sequence[DocumentMeta]:
        """메타 행 전체를 반환한다. source 를 주면 해당 출처로만 제한한다."""
        stmt = select(DocumentMeta)
        if source is not None:
            stmt = stmt.where(DocumentMeta.source == source)
        stmt = stmt.order_by(DocumentMeta.source, DocumentMeta.external_id)
        return self._session.execute(stmt).scalars().all()

    def delete(self, meta: DocumentMeta) -> None:
        """메타 행을 세션에서 삭제한다."""
        self._session.delete(meta)
