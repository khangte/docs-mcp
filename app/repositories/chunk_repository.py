"""청크 저장소."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, defer

from app.models.openapi import ApiChunk, ApiDocument, ApiEndpoint

#: 벡터 검색 시 강제할 hnsw.ef_search 하한. 기본값(40)은 RRF 융합용 넓은 후보폭
#: (top_k 최대 200)보다 작아 recall 을 깎을 수 있어 세션 GUC 로 올려 잡는다.
_HNSW_EF_SEARCH = 100


@dataclass
class ChunkVectorHit:
    """벡터 검색 결과 한 건(청크 ID + 엔드포인트 ref_id + 코사인 유사도 점수)."""

    chunk_id: str
    ref_id: str
    score: float


@dataclass
class ChunkTextHit:
    """FTS 키워드 검색 결과 한 건(청크 ID + 엔드포인트 ref_id + ts_rank 점수)."""

    chunk_id: str
    ref_id: str
    score: float


def _quote_tsquery_lexeme(term: str) -> str:
    """term 을 tsquery 리터럴 lexeme 으로 안전하게 인용한다.

    tsquery 문자열은 그 자체로 연산자(& | ! ( ) 등)를 갖는 미니 언어라,
    term 을 작은따옴표로 감싸 "리터럴 lexeme"으로 강제해야 사용자 입력이
    연산자로 해석되지 않는다. 내부에 작은따옴표가 있으면 두 배로 escape.
    """
    return "'" + term.replace("'", "''") + "'"


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

    def list_endpoint_chunks(
        self, document_id: str | None = None, project: str | None = None
    ) -> Sequence[ApiChunk]:
        """endpoint 타입 청크만 SQL 로 필터링해 반환한다.

        후보 검색은 endpoint 청크만 사용하므로 section/schema 청크를 DB 단계에서
        걸러낸다. 반환된 청크의 embedding 컬럼은 호출측(EndpointCandidateSearch)이
        전혀 쓰지 않으므로 `defer()` 로 로딩을 지연해 전송하지 않는다.

        Args:
            document_id: 주어지면 해당 문서로 범위를 제한한다.
            project: 주어지면 `ApiDocument` 와 조인해 해당 project 로
                범위를 제한한다(SQL 로 필터링, Python 필터링 금지).
        """
        stmt = (
            select(ApiChunk)
            .where(ApiChunk.chunk_type == "endpoint")
            .options(defer(ApiChunk.embedding))
        )
        if document_id is not None:
            stmt = stmt.where(ApiChunk.document_id == document_id)
        if project is not None:
            stmt = stmt.join(ApiDocument, ApiChunk.document_id == ApiDocument.id).where(
                ApiDocument.project == project
            )
        return self._session.execute(stmt).scalars().all()

    def list_endpoint_chunk_ids(
        self, document_id: str | None = None, project: str | None = None
    ) -> set[str]:
        """endpoint 타입 청크의 ID만 가볍게 조회한다(다른 컬럼은 적재하지 않음).

        벡터 검색의 스코프(`candidate_ids`)를 만들 때 `list_endpoint_chunks()`
        처럼 전체 `ApiChunk` 로우를 메모리에 올릴 필요가 없어 이 메서드를 쓴다.
        """
        stmt = select(ApiChunk.id).where(ApiChunk.chunk_type == "endpoint")
        if document_id is not None:
            stmt = stmt.where(ApiChunk.document_id == document_id)
        if project is not None:
            stmt = stmt.join(ApiDocument, ApiChunk.document_id == ApiDocument.id).where(
                ApiDocument.project == project
            )
        return set(self._session.execute(stmt).scalars().all())

    def has_endpoint_chunks(
        self, document_id: str | None = None, project: str | None = None
    ) -> bool:
        """조건에 맞는 endpoint 청크가 하나라도 있는지 가벼운 EXISTS 조회로 확인한다.

        `EndpointCandidateSearch` 가 "이 스코프에 endpoint 청크가 아예 없다"를
        빠르게 판별해 키워드/벡터 검색(및 임베딩 API 호출)을 생략하는 데 쓴다.
        전체 청크를 적재하지 않으므로 `list_endpoint_chunks()` 보다 가볍다.
        """
        stmt = select(ApiChunk.id).where(ApiChunk.chunk_type == "endpoint")
        if document_id is not None:
            stmt = stmt.where(ApiChunk.document_id == document_id)
        if project is not None:
            stmt = stmt.join(ApiDocument, ApiChunk.document_id == ApiDocument.id).where(
                ApiDocument.project == project
            )
        stmt = stmt.limit(1)
        return self._session.execute(stmt).first() is not None

    def search_endpoint_by_text(
        self,
        terms: Sequence[str],
        top_k: int,
        document_id: str | None = None,
        project: str | None = None,
    ) -> list[ChunkTextHit]:
        """endpoint 청크를 Postgres FTS(`text_tsv` GIN 인덱스)로 키워드 검색한다.

        `terms` 는 `|`(OR) 로 결합한다 — "질의 term 중 하나라도 겹치면 후보,
        많이 겹칠수록 상위"인 기존 키워드 검색 의미를 유지하기 위함이다
        (`plainto_tsquery`/`websearch_to_tsquery` 의 기본 AND 는 recall 을
        지나치게 좁힌다). 각 term 은 리터럴 lexeme 으로 인용해(`_quote_tsquery_lexeme`)
        tsquery 연산자로 오인되지 않게 한다.

        정렬은 `ts_rank` 내림차순, 동점이면 `id` 오름차순이라 결과가 결정적이다.
        `text_tsv` 컬럼 자체는 필터 전용이라 select 하지 않는다.
        """
        normalized_terms = [t for t in terms if t]
        if top_k <= 0 or not normalized_terms:
            return []
        tsquery_str = " | ".join(_quote_tsquery_lexeme(t) for t in normalized_terms)
        tsq = func.to_tsquery("simple", tsquery_str)
        rank = func.ts_rank(ApiChunk.text_tsv, tsq)
        stmt = (
            select(ApiChunk.id, ApiChunk.ref_id, rank.label("score"))
            .where(ApiChunk.chunk_type == "endpoint")
            .where(ApiChunk.text_tsv.op("@@", is_comparison=True)(tsq))
        )
        if document_id is not None:
            stmt = stmt.where(ApiChunk.document_id == document_id)
        if project is not None:
            stmt = stmt.join(ApiDocument, ApiChunk.document_id == ApiDocument.id).where(
                ApiDocument.project == project
            )
        stmt = stmt.order_by(rank.desc(), ApiChunk.id.asc()).limit(top_k)
        rows = self._session.execute(stmt).all()
        return [
            ChunkTextHit(chunk_id=cid, ref_id=ref_id, score=float(score))
            for cid, ref_id, score in rows
        ]

    def list_by_endpoint_filter(
        self,
        method: str | None = None,
        tag: str | None = None,
        document_id: str | None = None,
        project: str | None = None,
    ) -> Sequence[ApiChunk]:
        """method/tag/document_id/project SQL 필터를 적용해 후보 청크를 반환한다.

        - endpoint 청크: 조건에 맞는 ApiEndpoint 와 JOIN 해 필터링
        - schema/section 청크: method/tag 조건 없이 document_id/project 만 적용
        - project 는 `ApiDocument` 와 조인해 SQL 로 필터링한다.
        필터가 모두 None 이면 전체 청크를 반환한다.
        """
        if method is None and tag is None and document_id is None and project is None:
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
            if project is not None:
                endpoint_stmt = endpoint_stmt.join(
                    ApiDocument, ApiChunk.document_id == ApiDocument.id
                ).where(ApiDocument.project == project)
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

            # schema/section 청크는 method/tag 조건 없이 document_id/project 만 적용
            other_stmt = select(ApiChunk).where(ApiChunk.chunk_type.in_(("schema", "section")))
            if document_id is not None:
                other_stmt = other_stmt.where(ApiChunk.document_id == document_id)
            if project is not None:
                other_stmt = other_stmt.join(
                    ApiDocument, ApiChunk.document_id == ApiDocument.id
                ).where(ApiDocument.project == project)
            other_chunks = list(self._session.execute(other_stmt).scalars().all())

            return endpoint_chunks + other_chunks

        # method/tag 없고 document_id/project 만 있는 경우
        stmt = select(ApiChunk)
        if document_id is not None:
            stmt = stmt.where(ApiChunk.document_id == document_id)
        if project is not None:
            stmt = stmt.join(ApiDocument, ApiChunk.document_id == ApiDocument.id).where(
                ApiDocument.project == project
            )
        return self._session.execute(stmt).scalars().all()

    def search_by_vector(
        self,
        query_vector: list[float],
        top_k: int,
        candidate_ids: set[str] | None = None,
    ) -> list[ChunkVectorHit]:
        """pgvector 코사인 거리(`<=>`)로 top_k 를 유사도 내림차순으로 반환한다.

        `candidate_ids` 가 주어지면 그 안의 청크만 고려한다. `chunk_type` 은
        `candidate_ids` 유무와 무관하게 항상 SQL 로 `endpoint` 로 제한한다
        (Q2: 이전에는 `candidate_ids` 가 "endpoint 만 남기는 필터"를 겸했는데,
        전역 스코프에서 `candidate_ids=None` 을 넘기게 되면서 SQL 자체에
        조건이 없으면 schema 청크가 섞여 들어온다).
        코사인 거리는 [0, 2] 범위이므로 유사도 = 1 - 거리 로 변환한다.
        `ref_id` 를 함께 SQL 로 프로젝션해(`ApiChunk.ref_id`, 조인 불필요),
        호출측이 chunk_id → ref_id 를 역매핑하려고 전체 청크를 메모리에
        적재할 필요가 없게 한다(RRF 융합은 endpoint(ref_id) 단위로 동작).
        """
        if top_k <= 0:
            return []
        ef = max(_HNSW_EF_SEARCH, top_k)
        # SET 은 유틸리티 구문이라 바인드 파라미터를 받지 않는다(PG 파서가 거부).
        # ef 는 두 int 의 max() 결과라 사용자 입력이 섞일 수 없어 f-string 삽입이 안전하다.
        # SET LOCAL 이라 현재 트랜잭션 스코프에 한정되고 세션 전역을 오염시키지 않는다.
        self._session.execute(text(f"SET LOCAL hnsw.ef_search = {ef}"))
        distance = ApiChunk.embedding.cosine_distance(query_vector)
        stmt = (
            select(ApiChunk.id, ApiChunk.ref_id, distance.label("distance"))
            .where(ApiChunk.chunk_type == "endpoint")
            .where(ApiChunk.embedding.is_not(None))
        )
        if candidate_ids is not None:
            if not candidate_ids:
                return []
            stmt = stmt.where(ApiChunk.id.in_(candidate_ids))
        stmt = stmt.order_by(distance.asc()).limit(top_k)
        rows = self._session.execute(stmt).all()
        return [
            ChunkVectorHit(chunk_id=cid, ref_id=ref_id, score=1.0 - float(dist))
            for cid, ref_id, dist in rows
        ]
