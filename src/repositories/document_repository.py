"""문서 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.models.openapi import ApiDocument


class DocumentRepository:
    """`api_document` CRUD."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, document: ApiDocument) -> None:
        self._session.add(document)

    def get(self, document_id: str) -> ApiDocument | None:
        return self._session.get(ApiDocument, document_id)

    def find_by_source_url(self, source_url: str) -> ApiDocument | None:
        stmt = select(ApiDocument).where(ApiDocument.source_url == source_url)
        return self._session.execute(stmt).scalar_one_or_none()

    def list_all(self) -> Sequence[ApiDocument]:
        stmt = select(ApiDocument).order_by(desc(ApiDocument.indexed_at))
        return self._session.execute(stmt).scalars().all()

    def delete(self, document: ApiDocument) -> None:
        self._session.delete(document)
