"""엔드포인트/스키마 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.openapi import ApiEndpoint, ApiSchema


class EndpointRepository:
    """`api_endpoint` CRUD (+ 스키마 조회)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, endpoint: ApiEndpoint) -> None:
        self._session.add(endpoint)

    def get(self, endpoint_id: str) -> ApiEndpoint | None:
        return self._session.get(ApiEndpoint, endpoint_id)

    def list_by_document(self, document_id: str) -> Sequence[ApiEndpoint]:
        stmt = select(ApiEndpoint).where(ApiEndpoint.document_id == document_id)
        return self._session.execute(stmt).scalars().all()

    def get_schema_by_name(self, document_id: str, name: str) -> ApiSchema | None:
        stmt = select(ApiSchema).where(
            ApiSchema.document_id == document_id,
            ApiSchema.name == name,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def add_schema(self, schema: ApiSchema) -> None:
        self._session.add(schema)
