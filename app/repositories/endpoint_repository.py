"""엔드포인트/스키마 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import ApiEndpoint, ApiSchema, Document, DocumentSection


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

    def get_many(self, endpoint_ids: Sequence[str]) -> dict[str, ApiEndpoint]:
        """여러 ID 를 `WHERE id IN (...)` 한 번으로 배치 조회한다.

        검색 결과 후보(top_k 개)를 결과당 `get()` 으로 반복 조회하던
        N+1 패턴을 없애기 위한 메서드다. 없는 ID 는 반환 매핑에서 조용히
        빠진다(호출측이 존재 여부를 판단). 빈 입력은 쿼리 없이 빈 매핑을
        돌려준다(`IN ()` 은 무의미한 왕복이다).
        """
        if not endpoint_ids:
            return {}
        stmt = select(ApiEndpoint).where(ApiEndpoint.id.in_(endpoint_ids))
        rows = self._session.execute(stmt).scalars().all()
        return {row.id: row for row in rows}

    def list_by_document(self, document_id: str) -> Sequence[ApiEndpoint]:
        """특정 문서의 엔드포인트 목록을 반환한다."""
        stmt = select(ApiEndpoint).where(ApiEndpoint.document_id == document_id)
        return self._session.execute(stmt).scalars().all()

    def list_all(
        self, document_id: str | None = None, project: str | None = None
    ) -> Sequence[ApiEndpoint]:
        """엔드포인트 목록을 (method, path) 오름차순으로 반환한다.

        document_id 가 주어지면 해당 문서로, project 가 주어지면 `Document`
        와 조인해 해당 project 로 범위를 제한한다. 정렬을 고정해 태그 집계
        같은 후속 처리 결과가 결정적이 되도록 한다.
        """
        stmt = select(ApiEndpoint)
        if document_id is not None:
            stmt = stmt.where(ApiEndpoint.document_id == document_id)
        if project is not None:
            stmt = stmt.join(Document, ApiEndpoint.document_id == Document.id).where(
                Document.project == project
            )
        stmt = stmt.order_by(ApiEndpoint.path, ApiEndpoint.method)
        return self._session.execute(stmt).scalars().all()

    def list_related(
        self,
        document_id: str,
        exclude_endpoint_id: str,
        tags: Sequence[str],
        path_prefix: str,
        limit: int,
    ) -> Sequence[ApiEndpoint]:
        """같은 문서 내에서 태그 또는 경로 접두사를 공유하는 엔드포인트를 SQL 로 직접 찾는다.

        `get_endpoint_details` 의 순회 힌트(`related_endpoints`)용이다. 문서
        전체를 적재해 Python 에서 걸러내지 않고, 필터(태그 OR 경로 접두사)와
        `LIMIT` 을 SQL 로 내려 필요한 건수만 가져온다(대량 엔드포인트
        문서에서도 비용이 고정). `tags` 는 `list_by_endpoint_filter` 와 같은
        방식(`tags_json.contains`)으로 매칭하고, `path_prefix` 는 그 자체와
        일치하거나 `f"{path_prefix}/"` 로 시작하는 경로를 매칭한다(첫 경로
        세그먼트 동일 판정). 둘 다 비었으면(빈 tags, 빈 prefix) 매칭 조건이
        없으므로 쿼리 없이 빈 결과를 반환한다.
        """
        conditions = [ApiEndpoint.tags_json.contains(f'"{tag}"') for tag in tags]
        if path_prefix:
            conditions.append(ApiEndpoint.path == path_prefix)
            conditions.append(ApiEndpoint.path.like(f"{path_prefix}/%"))
        if not conditions:
            return []
        stmt = (
            select(ApiEndpoint)
            .where(ApiEndpoint.document_id == document_id)
            .where(ApiEndpoint.id != exclude_endpoint_id)
            .where(or_(*conditions))
            .order_by(ApiEndpoint.path, ApiEndpoint.method)
            .limit(limit)
        )
        return self._session.execute(stmt).scalars().all()

    def list_by_method_path(
        self,
        method: str,
        path: str,
        document_id: str | None = None,
        project: str | None = None,
    ) -> Sequence[ApiEndpoint]:
        """method+path 정확일치로 엔드포인트를 조회한다(exact match 우선 lookup).

        `search_endpoints`의 RRF 융합 앞단에서 "GET /pet/{petId}" 같은
        정확한 질의에 확정적 1위를 주기 위한 조회 경로다(같은 method+path가
        여러 문서에 있으면 전부 반환 — 호출측이 순서를 정한다). method는
        대소문자 무관 매칭되도록 대문자로 정규화한다.
        """
        stmt = select(ApiEndpoint).where(
            ApiEndpoint.method == method.upper(), ApiEndpoint.path == path
        )
        if document_id is not None:
            stmt = stmt.where(ApiEndpoint.document_id == document_id)
        if project is not None:
            stmt = stmt.join(Document, ApiEndpoint.document_id == Document.id).where(
                Document.project == project
            )
        stmt = stmt.order_by(ApiEndpoint.id)
        return self._session.execute(stmt).scalars().all()

    def list_by_operation_id(
        self,
        operation_id: str,
        document_id: str | None = None,
        project: str | None = None,
    ) -> Sequence[ApiEndpoint]:
        """operationId 정확일치로 엔드포인트를 조회한다(exact match 우선 lookup)."""
        stmt = select(ApiEndpoint).where(ApiEndpoint.operation_id == operation_id)
        if document_id is not None:
            stmt = stmt.where(ApiEndpoint.document_id == document_id)
        if project is not None:
            stmt = stmt.join(Document, ApiEndpoint.document_id == Document.id).where(
                Document.project == project
            )
        stmt = stmt.order_by(ApiEndpoint.id)
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

    def get_section(self, section_id: str) -> DocumentSection | None:
        """ID 로 섹션을 조회한다."""
        return self._session.get(DocumentSection, section_id)

    def list_sections_by_document(self, document_id: str) -> Sequence[DocumentSection]:
        """특정 문서의 섹션 목록을 순서대로 반환한다."""
        stmt = (
            select(DocumentSection)
            .where(DocumentSection.document_id == document_id)
            .order_by(DocumentSection.order_index)
        )
        return self._session.execute(stmt).scalars().all()

    def add_section(self, section: DocumentSection) -> None:
        """섹션을 세션에 추가한다."""
        self._session.add(section)
