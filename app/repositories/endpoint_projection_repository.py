"""`endpoint_search_projection` 저장소 (`docs/architect-review/101`).

CRUD + document/project 스코프 + 감사(count/hash) 조회(Unit 1)에 더해
`endpoint_repr` arm 용 scope-filtered FTS/vector lookup(Unit 3)을 제공한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, text
from sqlalchemy.orm import Session

from app.models import Document, EndpointSearchProjection
from app.repositories.chunk_repository import _quote_tsquery_lexeme

#: projection HNSW 검색의 `hnsw.ef_search` 하한(endpoint chunk 벡터 검색과 같은 값).
_HNSW_EF_SEARCH = 100


@dataclass(frozen=True)
class ProjectionHit:
    """projection lookup 결과 1건(endpoint 신원 + 점수). rank 는 호출자가 순서로 매긴다."""

    endpoint_id: str
    method: str
    path: str
    score: float


@dataclass(frozen=True)
class ProjectionAuditRow:
    """감사용 경량 행 — projection 1건의 신원과 format 지문만."""

    endpoint_id: str
    document_id: str
    method: str
    path: str
    representation_version: str
    source_hash: str


class EndpointProjectionRepository:
    """`endpoint_search_projection` CRUD + 스코프 + 감사 조회."""

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def upsert(
        self,
        *,
        id: str,
        endpoint_id: str,
        document_id: str,
        method: str,
        path: str,
        canonical_text: str,
        embedding: list[float] | None,
        representation_version: str,
        source_hash: str,
    ) -> EndpointSearchProjection:
        """endpoint 1건의 projection 을 생성하거나 제자리 갱신한다.

        색인·backfill·write-back refresh 가 같은 endpoint 를 여러 번 넣을 수
        있으므로 `(document_id, method, path)` 로 기존 행을 찾아 덮어쓴다.
        `canonical_tsv` 는 STORED generated 라 직접 쓰지 않는다.
        """
        row = self._session.execute(
            select(EndpointSearchProjection).where(
                EndpointSearchProjection.document_id == document_id,
                EndpointSearchProjection.method == method,
                EndpointSearchProjection.path == path,
            )
        ).scalar_one_or_none()
        if row is None:
            row = EndpointSearchProjection(
                id=id,
                endpoint_id=endpoint_id,
                document_id=document_id,
                method=method,
                path=path,
                canonical_text=canonical_text,
                embedding=embedding,
                representation_version=representation_version,
                source_hash=source_hash,
            )
            self._session.add(row)
        else:
            row.id = id
            row.endpoint_id = endpoint_id
            row.canonical_text = canonical_text
            row.embedding = embedding
            row.representation_version = representation_version
            row.source_hash = source_hash
        self._session.flush()
        return row

    def get_by_endpoint(self, endpoint_id: str) -> EndpointSearchProjection | None:
        """endpoint id 로 projection 1건을 조회한다(없으면 None)."""
        return self._session.execute(
            select(EndpointSearchProjection).where(
                EndpointSearchProjection.endpoint_id == endpoint_id
            )
        ).scalar_one_or_none()

    def list_by_document(
        self, document_id: str
    ) -> Sequence[EndpointSearchProjection]:
        """문서 1건의 projection 을 method/path 정렬로 반환한다."""
        return (
            self._session.execute(
                select(EndpointSearchProjection)
                .where(EndpointSearchProjection.document_id == document_id)
                .order_by(
                    EndpointSearchProjection.method.asc(),
                    EndpointSearchProjection.path.asc(),
                )
            )
            .scalars()
            .all()
        )

    def delete_by_document(self, document_id: str) -> int:
        """문서 1건의 projection 을 모두 삭제하고 삭제 행 수를 반환한다.

        재색인은 endpoint cascade 로도 지워지지만, projection 만 재생성하는
        경로(포맷 버전업 등)를 위해 명시 삭제도 제공한다.
        """
        # DML 실행 결과는 CursorResult 지만 Session.execute 스텁은 Result[Any] 로
        # 좁혀 rowcount 를 노출하지 않는다. DML 한정으로 명시 캐스팅한다.
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                delete(EndpointSearchProjection).where(
                    EndpointSearchProjection.document_id == document_id
                )
            ),
        )
        return max(result.rowcount, 0)

    def count(
        self, *, document_id: str | None = None, project: str | None = None
    ) -> int:
        """스코프 안의 projection 행 수(§6 source coverage gate 감사용)."""
        stmt = select(func.count()).select_from(EndpointSearchProjection)
        stmt = self._scope(stmt, document_id=document_id, project=project)
        return int(self._session.execute(stmt).scalar_one())

    def list_audit_rows(
        self, *, document_id: str | None = None, project: str | None = None
    ) -> list[ProjectionAuditRow]:
        """스코프 안 projection 의 신원 + version/hash 만 경량 조회한다."""
        stmt = select(
            EndpointSearchProjection.endpoint_id,
            EndpointSearchProjection.document_id,
            EndpointSearchProjection.method,
            EndpointSearchProjection.path,
            EndpointSearchProjection.representation_version,
            EndpointSearchProjection.source_hash,
        )
        stmt = self._scope(stmt, document_id=document_id, project=project)
        stmt = stmt.order_by(
            EndpointSearchProjection.document_id.asc(),
            EndpointSearchProjection.method.asc(),
            EndpointSearchProjection.path.asc(),
        )
        return [
            ProjectionAuditRow(
                endpoint_id=eid,
                document_id=did,
                method=method,
                path=path,
                representation_version=version,
                source_hash=source_hash,
            )
            for eid, did, method, path, version, source_hash in self._session.execute(
                stmt
            ).all()
        ]

    def search_projection_by_text(
        self,
        terms: Sequence[str],
        top_k: int,
        *,
        document_id: str | None = None,
        project: str | None = None,
    ) -> list[ProjectionHit]:
        """`canonical_tsv`(simple FTS, GIN) 로 term OR 매칭해 `ts_rank` 내림차순 top_k.

        term 은 리터럴 lexeme 으로 인용해(`_quote_tsquery_lexeme`) tsquery
        연산자 오인을 막고, `|` 로 결합한다(endpoint keyword arm 과 같은 규약).
        동점은 `endpoint_id` 오름차순이라 결정적이다.
        """
        normalized = [t for t in terms if t]
        if top_k <= 0 or not normalized:
            return []
        tsq = func.to_tsquery(
            "simple", " | ".join(_quote_tsquery_lexeme(t) for t in normalized)
        )
        rank = func.ts_rank(EndpointSearchProjection.canonical_tsv, tsq)
        stmt = select(
            EndpointSearchProjection.endpoint_id,
            EndpointSearchProjection.method,
            EndpointSearchProjection.path,
            rank.label("score"),
        ).where(
            EndpointSearchProjection.canonical_tsv.op("@@", is_comparison=True)(tsq)
        )
        stmt = self._scope(stmt, document_id=document_id, project=project)
        stmt = stmt.order_by(
            rank.desc(), EndpointSearchProjection.endpoint_id.asc()
        ).limit(top_k)
        return [
            ProjectionHit(endpoint_id=eid, method=method, path=path, score=float(score))
            for eid, method, path, score in self._session.execute(stmt).all()
        ]

    def search_projection_by_vector(
        self,
        query_vector: list[float],
        top_k: int,
        *,
        document_id: str | None = None,
        project: str | None = None,
    ) -> list[ProjectionHit]:
        """pgvector 코사인 거리(HNSW)로 유사도 내림차순 top_k. 동점은 `endpoint_id` 오름차순.

        `embedding` 이 NULL 인 행(비의미 프로바이더로 색인)은 제외한다.
        점수 = 1 - 코사인거리(endpoint chunk 벡터 검색과 같은 변환).
        """
        if top_k <= 0:
            return []
        ef = max(_HNSW_EF_SEARCH, top_k)
        # SET 은 유틸리티 구문이라 바인드 파라미터 불가. ef 는 두 int 의 max() 라
        # 사용자 입력이 섞일 수 없어 f-string 삽입이 안전하다. SET LOCAL 이라
        # 현재 트랜잭션 스코프에 한정된다(chunk_repository.search_by_vector 와 동일).
        self._session.execute(text(f"SET LOCAL hnsw.ef_search = {ef}"))
        distance = EndpointSearchProjection.embedding.cosine_distance(query_vector)
        stmt = select(
            EndpointSearchProjection.endpoint_id,
            EndpointSearchProjection.method,
            EndpointSearchProjection.path,
            distance.label("distance"),
        ).where(EndpointSearchProjection.embedding.is_not(None))
        stmt = self._scope(stmt, document_id=document_id, project=project)
        stmt = stmt.order_by(
            distance.asc(), EndpointSearchProjection.endpoint_id.asc()
        ).limit(top_k)
        return [
            ProjectionHit(
                endpoint_id=eid, method=method, path=path, score=1.0 - float(dist)
            )
            for eid, method, path, dist in self._session.execute(stmt).all()
        ]

    def _scope(self, stmt, *, document_id: str | None, project: str | None):
        """`document_id`/`project` 스코프를 statement 에 얹는다(기존 endpoint search 규약)."""
        if document_id is not None:
            stmt = stmt.where(EndpointSearchProjection.document_id == document_id)
        if project is not None:
            stmt = stmt.join(
                Document, EndpointSearchProjection.document_id == Document.id
            ).where(Document.project == project)
        return stmt
