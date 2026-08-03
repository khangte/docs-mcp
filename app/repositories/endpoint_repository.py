"""엔드포인트/스키마 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.openapi import ApiDocument, ApiEndpoint, ApiSchema, ApiSection


class EndpointRepository:
    """`api_endpoint` CRUD (+ 스키마/섹션 조회)."""

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

    def list_all(
        self, document_id: str | None = None, project: str | None = None
    ) -> Sequence[ApiEndpoint]:
        """엔드포인트 목록을 (method, path) 오름차순으로 반환한다.

        document_id 가 주어지면 해당 문서로, project 가 주어지면 `ApiDocument`
        와 조인해 해당 project 로 범위를 제한한다. 정렬을 고정해 태그 집계
        같은 후속 처리 결과가 결정적이 되도록 한다.
        """
        stmt = select(ApiEndpoint)
        if document_id is not None:
            stmt = stmt.where(ApiEndpoint.document_id == document_id)
        if project is not None:
            stmt = stmt.join(ApiDocument, ApiEndpoint.document_id == ApiDocument.id).where(
                ApiDocument.project == project
            )
        stmt = stmt.order_by(ApiEndpoint.path, ApiEndpoint.method)
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

    def get_section(self, section_id: str) -> ApiSection | None:
        """ID 로 섹션을 조회한다."""
        return self._session.get(ApiSection, section_id)

    def list_sections_by_document(self, document_id: str) -> Sequence[ApiSection]:
        """특정 문서의 섹션 목록을 순서대로 반환한다."""
        stmt = (
            select(ApiSection)
            .where(ApiSection.document_id == document_id)
            .order_by(ApiSection.order_index)
        )
        return self._session.execute(stmt).scalars().all()

    def add_section(self, section: ApiSection) -> None:
        """섹션을 세션에 추가한다."""
        self._session.add(section)
