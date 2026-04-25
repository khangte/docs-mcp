"""엔드포인트/스키마 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.openapi import ApiEndpoint, ApiSchema


class EndpointRepository:
    """`api_endpoint` CRUD (+ 스키마 조회)."""

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def add(self, endpoint: ApiEndpoint) -> None:
        """엔드포인트를 세션에 추가한다(연관 객체도 함께 add 된다)."""
        self._session.add(endpoint)

    def get(self, endpoint_id: str) -> ApiEndpoint | None:
        """ID 로 엔드포인트를 조회한다."""
        return self._session.get(ApiEndpoint, endpoint_id)

    def list_by_document(self, document_id: str) -> Sequence[ApiEndpoint]:
        """특정 문서의 엔드포인트 목록을 반환한다."""
        stmt = select(ApiEndpoint).where(ApiEndpoint.document_id == document_id)
        return self._session.execute(stmt).scalars().all()

    def get_schema_by_name(self, document_id: str, name: str) -> ApiSchema | None:
        """문서 내 스키마 이름으로 컴포넌트 스키마를 조회한다."""
        stmt = select(ApiSchema).where(
            ApiSchema.document_id == document_id,
            ApiSchema.name == name,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def add_schema(self, schema: ApiSchema) -> None:
        """컴포넌트 스키마를 세션에 추가한다."""
        self._session.add(schema)
