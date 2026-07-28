"""청크 저장소."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.openapi import ApiChunk, ApiEndpoint


@dataclass
class ChunkVectorHit:
    """벡터 검색 결과 한 건(청크 ID + 코사인 유사도 점수)."""

    chunk_id: str
    score: float


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

    def list_endpoint_chunks(self, document_id: str | None = None) -> Sequence[ApiChunk]:
        """endpoint 타입 청크만 SQL 로 필터링해 반환한다.

        후보 검색은 endpoint 청크만 사용하므로 section/schema 청크를 DB 단계에서
        걸러낸다. 전체 청크를 적재한 뒤 Python 에서 버리면 쓰이지도 않을
        임베딩 벡터 컬럼까지 매 검색마다 전송된다.
        """
        stmt = select(ApiChunk).where(ApiChunk.chunk_type == "endpoint")
        if document_id is not None:
            stmt = stmt.where(ApiChunk.document_id == document_id)
        return self._session.execute(stmt).scalars().all()

    def list_by_endpoint_filter(
        self,
        method: str | None = None,
        tag: str | None = None,
        document_id: str | None = None,
    ) -> Sequence[ApiChunk]:
        """method/tag/document_id SQL 필터를 적용해 후보 청크를 반환한다.

        - endpoint 청크: 조건에 맞는 ApiEndpoint 와 JOIN 해 필터링
        - schema/section 청크: method/tag 조건 없이 document_id 만 적용
        필터가 모두 None 이면 전체 청크를 반환한다.
        """
        if method is None and tag is None and document_id is None:
            return self.list_all()

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

            # schema/section 청크는 method/tag 조건 없이 document_id 만 적용
            other_stmt = select(ApiChunk).where(ApiChunk.chunk_type.in_(("schema", "section")))
            if document_id is not None:
                other_stmt = other_stmt.where(ApiChunk.document_id == document_id)
            other_chunks = list(self._session.execute(other_stmt).scalars().all())

            return endpoint_chunks + other_chunks

        # method/tag 없고 document_id 만 있는 경우
        stmt = select(ApiChunk).where(ApiChunk.document_id == document_id)
        return self._session.execute(stmt).scalars().all()

    def search_by_vector(
        self,
        query_vector: list[float],
        top_k: int,
        candidate_ids: set[str] | None = None,
    ) -> list[ChunkVectorHit]:
        """pgvector 코사인 거리(`<=>`)로 top_k 를 유사도 내림차순으로 반환한다.

        `candidate_ids` 가 주어지면 그 안의 청크만 고려한다.
        코사인 거리는 [0, 2] 범위이므로 유사도 = 1 - 거리 로 변환한다.
        """
        if top_k <= 0:
            return []
        distance = ApiChunk.embedding.cosine_distance(query_vector)
        stmt = (
            select(ApiChunk.id, distance.label("distance"))
            .where(ApiChunk.embedding.is_not(None))
        )
        if candidate_ids is not None:
            if not candidate_ids:
                return []
            stmt = stmt.where(ApiChunk.id.in_(candidate_ids))
        stmt = stmt.order_by(distance.asc()).limit(top_k)
        rows = self._session.execute(stmt).all()
        return [ChunkVectorHit(chunk_id=cid, score=1.0 - float(dist)) for cid, dist in rows]
