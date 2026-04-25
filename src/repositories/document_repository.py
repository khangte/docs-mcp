"""문서 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.models.openapi import ApiDocument


class DocumentRepository:
    """`api_document` CRUD."""

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def add(self, document: ApiDocument) -> None:
        """문서를 세션에 추가한다."""
        self._session.add(document)

    def get(self, document_id: str) -> ApiDocument | None:
        """ID 로 문서를 조회한다."""
        return self._session.get(ApiDocument, document_id)

    def find_by_source_url(self, source_url: str) -> ApiDocument | None:
        """source_url 로 문서를 조회한다."""
        stmt = select(ApiDocument).where(ApiDocument.source_url == source_url)
        return self._session.execute(stmt).scalar_one_or_none()

    def list_all(self) -> Sequence[ApiDocument]:
        """색인 시각 내림차순으로 전체 문서를 반환한다."""
        stmt = select(ApiDocument).order_by(desc(ApiDocument.indexed_at))
        return self._session.execute(stmt).scalars().all()

    def delete(self, document: ApiDocument) -> None:
        """문서를 세션에서 삭제한다."""
        self._session.delete(document)
