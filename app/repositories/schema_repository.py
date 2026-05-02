"""ApiSchema 저장소."""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session

from app.models.openapi import ApiSchema


class SchemaRepository:
    """ApiSchema CRUD."""

    def __init__(self, session: Session) -> None:
        """세션을 보관한다."""
        self._session = session

    def add(self, schema: ApiSchema) -> None:
        """스키마를 세션에 추가한다."""
        self._session.add(schema)

    def delete_by_document(self, document_id: str) -> None:
        """특정 문서에 속한 스키마를 모두 삭제한다."""
        self._session.execute(
            sa_delete(ApiSchema).where(ApiSchema.document_id == document_id)
        )
