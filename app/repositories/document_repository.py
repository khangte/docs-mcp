"""문서 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import Document


class DocumentRepository:
    """`document` CRUD."""

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def add(self, document: Document) -> None:
        """문서를 세션에 추가한다."""
        self._session.add(document)

    def get(self, document_id: str) -> Document | None:
        """ID 로 문서를 조회한다."""
        return self._session.get(Document, document_id)

    def find_by_source_url(self, source_url: str) -> Document | None:
        """source_url 로 문서를 조회한다."""
        stmt = select(Document).where(Document.source_url == source_url)
        return self._session.execute(stmt).scalar_one_or_none()

    def list_all(self, project: str | None = None) -> Sequence[Document]:
        """색인 시각 내림차순으로 문서를 반환한다.

        `indexed_at` 이 동률일 때(동일 트랜잭션 내 대량 등록 등) 순서가
        흔들리지 않도록 id 를 2차 정렬 키로 사용해 완전 결정성을 보장한다.

        Args:
            project: 주어지면 해당 project 로 범위를 제한한다.
        """
        stmt = select(Document)
        if project is not None:
            stmt = stmt.where(Document.project == project)
        stmt = stmt.order_by(desc(Document.indexed_at), Document.id)
        return self._session.execute(stmt).scalars().all()

    def delete(self, document: Document) -> None:
        """문서를 세션에서 삭제한다."""
        self._session.delete(document)

    def list_resyncable(self, project: str | None = None) -> Sequence[Document]:
        """source_url 이 있는(URL 기반) 문서만 반환한다. raw_document 등록 문서는 제외."""
        stmt = select(Document).where(Document.source_url.is_not(None))
        if project is not None:
            stmt = stmt.where(Document.project == project)
        return self._session.execute(stmt).scalars().all()
