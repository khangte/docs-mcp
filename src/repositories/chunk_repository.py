"""청크 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.models.openapi import ApiChunk, ApiEndpoint


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

    def list_by_endpoint_filter(
        self,
        method: str | None = None,
        tag: str | None = None,
        document_id: str | None = None,
    ) -> Sequence[ApiChunk]:
        """method/tag/document_id SQL 필터를 적용해 후보 청크를 반환한다.

        - endpoint 청크: 조건에 맞는 ApiEndpoint 와 JOIN 해 필터링
        - schema 청크: method/tag 조건 없이 document_id 만 적용
        필터가 모두 None 이면 전체 청크를 반환한다.
        """
        if method is None and tag is None and document_id is None:
            return self.list_all()

        conditions_endpoint = []
        conditions_schema = []

        if document_id is not None:
            conditions_endpoint.append(ApiChunk.document_id == document_id)
            conditions_schema.append(ApiChunk.document_id == document_id)

        if method is not None or tag is not None:
            # endpoint 청크는 ApiEndpoint JOIN 필터
            endpoint_stmt = (
                select(ApiChunk)
                .join(ApiEndpoint, ApiChunk.ref_id == ApiEndpoint.id)
                .where(ApiChunk.chunk_type == "endpoint")
            )
            if document_id is not None:
                endpoint_stmt = endpoint_stmt.where(ApiChunk.document_id == document_id)
            if method is not None:
                endpoint_stmt = endpoint_stmt.where(
                    ApiEndpoint.method == method.upper()
                )
            if tag is not None:
                # tags_json 에 JSON 배열로 저장되어 있으므로 LIKE 검색
                endpoint_stmt = endpoint_stmt.where(
                    ApiEndpoint.tags_json.contains(f'"{tag}"')
                )
            endpoint_chunks = list(self._session.execute(endpoint_stmt).scalars().all())

            # schema 청크는 method/tag 조건 없이 document_id 만 적용
            schema_stmt = select(ApiChunk).where(ApiChunk.chunk_type == "schema")
            if document_id is not None:
                schema_stmt = schema_stmt.where(ApiChunk.document_id == document_id)
            schema_chunks = list(self._session.execute(schema_stmt).scalars().all())

            return endpoint_chunks + schema_chunks

        # method/tag 없고 document_id 만 있는 경우
        stmt = select(ApiChunk)
        for cond in conditions_schema:
            stmt = stmt.where(cond)
        return self._session.execute(stmt).scalars().all()
