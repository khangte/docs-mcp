"""청크 저장소."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.models import Chunk, Document
from app.repositories.document_filters import DocumentMetaFilter, document_meta_exists

#: 벡터 검색 시 강제할 hnsw.ef_search 하한. 기본값(40)은 RRF 융합용 넓은 후보폭
#: (top_k 최대 200)보다 작아 recall 을 깎을 수 있어 세션 GUC 로 올려 잡는다.
_HNSW_EF_SEARCH = 100
#: 메타 필터가 걸렸을 때의 ef_search 하한. HNSW ANN 은 먼저 top-N 을 뽑은 뒤
#: 필터가 걸리는 post-filtering 구조라 유효 후보가 줄어든다 - 평가셋 없는
#: 휴리스틱이라 env 로 노출하지 않는다.
_HNSW_EF_SEARCH_FILTERED = 200


@dataclass
class ChunkVectorHit:
    """벡터 검색 결과 한 건(청크 ID + ref_id + document_id + 코사인 유사도 점수).

    `document_id` 는 문서 검색(doc36 Phase3)이 section 청크 결과를 문서
    단위로 접어 RRF 융합 키로 쓰기 위한 프로젝션이다(엔드포인트 경로는
    `ref_id` 를 계속 쓴다).
    """

    chunk_id: str
    ref_id: str
    document_id: str
    score: float


@dataclass
class ChunkTextHit:
    """FTS 키워드 검색 결과 한 건(청크 ID + ref_id + document_id + ts_rank 점수).

    `document_id` 는 문서 검색(doc36 Phase3)이 section 청크 결과를 문서
    단위로 접어 RRF 융합 키로 쓰기 위한 프로젝션이다(엔드포인트 경로는
    `ref_id` 를 계속 쓴다).
    """

    chunk_id: str
    ref_id: str
    document_id: str
    score: float


def _quote_tsquery_lexeme(term: str) -> str:
    """term 을 tsquery 리터럴 lexeme 으로 안전하게 인용한다.

    tsquery 문자열은 그 자체로 연산자(& | ! ( ) 등)를 갖는 미니 언어라,
    term 을 작은따옴표로 감싸 "리터럴 lexeme"으로 강제해야 사용자 입력이
    연산자로 해석되지 않는다. 내부에 작은따옴표가 있으면 두 배로 escape.
    """
    return "'" + term.replace("'", "''") + "'"


class ChunkRepository:
    """`chunk` CRUD + document 별 일괄 교체."""

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def add(self, chunk: Chunk) -> None:
        """청크 한 건을 세션에 추가한다."""
        self._session.add(chunk)

    def update_endpoint_chunk(
        self, *, document_id: str, ref_id: str, text: str, embedding: list[float]
    ) -> bool:
        """엔드포인트 청크 1건의 텍스트/임베딩을 갱신한다(없으면 False).

        docs/architect-review/56 §4.4: write-back 직후 해당 엔드포인트 청크만
        갱신해 다음 검색부터 반영되게 한다. `text_tsv` 는 STORED generated
        컬럼이라 여기서 건드리지 않아도 FTS 가 함께 갱신된다.
        """
        stmt = select(Chunk).where(
            Chunk.document_id == document_id,
            Chunk.chunk_type == "endpoint",
            Chunk.ref_id == ref_id,
        )
        chunk = self._session.execute(stmt).scalars().first()
        if chunk is None:
            return False
        chunk.text = text
        chunk.embedding = embedding
        self._session.flush()
        return True

    def delete_by_document(self, document_id: str) -> int:
        """주어진 문서의 모든 청크를 삭제하고 삭제된 행 수를 반환한다."""
        stmt = delete(Chunk).where(Chunk.document_id == document_id)
        result = self._session.execute(stmt)
        return int(result.rowcount or 0)

    def list_all(self) -> Sequence[Chunk]:
        """전체 청크를 반환한다."""
        stmt = select(Chunk)
        return self._session.execute(stmt).scalars().all()

    def list_by_document(self, document_id: str) -> Sequence[Chunk]:
        """특정 문서에 속한 청크를 반환한다."""
        stmt = select(Chunk).where(Chunk.document_id == document_id)
        return self._session.execute(stmt).scalars().all()

    def list_endpoint_chunk_ids(
        self,
        document_id: str | None = None,
        project: str | None = None,
        chunk_type: str = "endpoint",
    ) -> set[str]:
        """`chunk_type` 청크의 ID만 가볍게 조회한다(다른 컬럼은 적재하지 않음).

        벡터 검색의 스코프(`candidate_ids`)를 만들 때 전체 `Chunk` 로우를
        메모리에 올릴 필요가 없어 이 메서드를 쓴다. 기본값은 `endpoint`이며,
        `section`을 넘기면 협업 문서 본문 청크 스코프로 그대로 재사용된다.
        """
        stmt = select(Chunk.id).where(Chunk.chunk_type == chunk_type)
        if document_id is not None:
            stmt = stmt.where(Chunk.document_id == document_id)
        if project is not None:
            stmt = stmt.join(Document, Chunk.document_id == Document.id).where(
                Document.project == project
            )
        return set(self._session.execute(stmt).scalars().all())

    def has_endpoint_chunks(
        self,
        document_id: str | None = None,
        project: str | None = None,
        chunk_type: str = "endpoint",
    ) -> bool:
        """조건에 맞는 `chunk_type` 청크가 하나라도 있는지 가벼운 EXISTS 조회로 확인한다.

        `EndpointCandidateSearch` 가 "이 스코프에 endpoint 청크가 아예 없다"를
        빠르게 판별해 키워드/벡터 검색(및 임베딩 API 호출)을 생략하는 데 쓴다.
        전체 청크를 적재하지 않아 가볍다. 기본값은 `endpoint`이며, `section`을
        넘기면 협업 문서 본문 색인 여부 판별에 그대로 재사용된다.
        """
        stmt = select(Chunk.id).where(Chunk.chunk_type == chunk_type)
        if document_id is not None:
            stmt = stmt.where(Chunk.document_id == document_id)
        if project is not None:
            stmt = stmt.join(Document, Chunk.document_id == Document.id).where(
                Document.project == project
            )
        stmt = stmt.limit(1)
        return self._session.execute(stmt).first() is not None

    def search_endpoint_by_text(
        self,
        terms: Sequence[str],
        top_k: int,
        document_id: str | None = None,
        project: str | None = None,
        score_terms: Sequence[str] | None = None,
        chunk_type: str = "endpoint",
        doc_types: Sequence[str] | None = None,
        meta_filter: DocumentMetaFilter | None = None,
    ) -> list[ChunkTextHit]:
        """`chunk_type` 청크를 Postgres FTS(`text_tsv` GIN 인덱스)로 키워드 검색한다.

        기본값은 `endpoint`이며, `section`을 넘기면 협업 문서 본문 청크
        검색에 그대로 재사용된다.

        `terms` 는 `|`(OR) 로 결합한다 — "질의 term 중 하나라도 겹치면 후보,
        많이 겹칠수록 상위"인 기존 키워드 검색 의미를 유지하기 위함이다
        (`plainto_tsquery`/`websearch_to_tsquery` 의 기본 AND 는 recall 을
        지나치게 좁힌다). 각 term 은 리터럴 lexeme 으로 인용해(`_quote_tsquery_lexeme`)
        tsquery 연산자로 오인되지 않게 한다.

        `score_terms` 는 `ts_rank` 계산에만 쓰는 별도 term 집합이다(생략하면
        `terms` 를 그대로 쓴다 — 기존 호출부와 하위 호환). `query_variants`
        배선(`KeywordSearch.search`)이 이 파라미터를 쓴다 — 후보 필터(`terms`)는
        원본+variant term 으로 넓히되, 점수는 원본 term(`score_terms`)만으로
        계산해 variant 매칭만 있는 후보가 원본 매칭 후보보다 부당하게 높은
        순위를 받지 않게 한다(문서 검색과 동일 규약).

        `doc_types` 는 `Document.doc_type` 으로 후보를 좁힌다(45번 리뷰
        §3.2/3.3) — `chunk_type="section"` 은 협업 문서(drive/notion)와
        등록형 문서(markdown/csv 등)가 공유하므로, 문서 검색 경로는 이
        인자로 등록형 문서 청크가 섞이는 것을 SQL 단에서 막는다.

        정렬은 `ts_rank` 내림차순, 동점이면 `id` 오름차순이라 결과가 결정적이다.
        `text_tsv` 컬럼 자체는 필터 전용이라 select 하지 않는다.

        `meta_filter` 는 `document_meta` EXISTS 서브쿼리로 건다(None 또는 빈
        필터면 조건 없이 기존과 동일하게 동작 - 엔드포인트 검색 호출부는
        인자를 넘기지 않으므로 무변경).
        """
        normalized_terms = [t for t in terms if t]
        if top_k <= 0 or not normalized_terms:
            return []
        tsquery_str = " | ".join(_quote_tsquery_lexeme(t) for t in normalized_terms)
        tsq = func.to_tsquery("simple", tsquery_str)

        score_source = terms if score_terms is None else score_terms
        normalized_score_terms = [t for t in score_source if t]
        score_tsq = (
            func.to_tsquery(
                "simple", " | ".join(_quote_tsquery_lexeme(t) for t in normalized_score_terms)
            )
            if normalized_score_terms
            else tsq
        )
        rank = func.ts_rank(Chunk.text_tsv, score_tsq)
        stmt = (
            select(Chunk.id, Chunk.ref_id, Chunk.document_id, rank.label("score"))
            .where(Chunk.chunk_type == chunk_type)
            .where(Chunk.text_tsv.op("@@", is_comparison=True)(tsq))
        )
        if document_id is not None:
            stmt = stmt.where(Chunk.document_id == document_id)
        if project is not None or doc_types is not None:
            stmt = stmt.join(Document, Chunk.document_id == Document.id)
            if project is not None:
                stmt = stmt.where(Document.project == project)
            if doc_types is not None:
                stmt = stmt.where(Document.doc_type.in_(doc_types))
        if meta_filter is not None and not meta_filter.is_empty():
            stmt = stmt.where(document_meta_exists(meta_filter, Chunk.document_id))
        stmt = stmt.order_by(rank.desc(), Chunk.id.asc()).limit(top_k)
        rows = self._session.execute(stmt).all()
        return [
            ChunkTextHit(chunk_id=cid, ref_id=ref_id, document_id=doc_id, score=float(score))
            for cid, ref_id, doc_id, score in rows
        ]

    def search_by_vector(
        self,
        query_vector: list[float],
        top_k: int,
        candidate_ids: set[str] | None = None,
        chunk_type: str = "endpoint",
        document_id: str | None = None,
        project: str | None = None,
        doc_types: Sequence[str] | None = None,
        meta_filter: DocumentMetaFilter | None = None,
    ) -> list[ChunkVectorHit]:
        """pgvector 코사인 거리(`<=>`)로 top_k 를 유사도 내림차순으로 반환한다.

        `candidate_ids` 가 주어지면 그 안의 청크만 고려한다. `chunk_type` 은
        `candidate_ids` 유무와 무관하게 항상 SQL 로 제한한다(기본값 `endpoint`,
        Q2: 이전에는 `candidate_ids` 가 "endpoint 만 남기는 필터"를 겸했는데,
        전역 스코프에서 `candidate_ids=None` 을 넘기게 되면서 SQL 자체에
        조건이 없으면 schema 청크가 섞여 들어온다). `section`을 넘기면 협업
        문서 본문 청크 검색에 그대로 재사용된다.

        `document_id`/`project` 는 SQL 조인으로 스코프를 거는 대안이다
        (`docs/architect-review/39` §2.6) — section 청크는 문서 수 × 섹션
        수라 `candidate_ids` 로 만든 ID 집합이 커질 수 있어, 문서 검색
        경로는 이 인자들을 쓴다. `candidate_ids` 와 동시에 줄 수도 있다
        (둘 다 AND 로 결합). `doc_types` 는 같은 조인에 `Document.doc_type`
        조건을 얹는다(45번 리뷰 §3.2/3.3 — 등록형 문서의 section 청크 배제).

        코사인 거리는 [0, 2] 범위이므로 유사도 = 1 - 거리 로 변환한다.
        `ref_id`/`document_id` 를 함께 SQL 로 프로젝션해(조인 불필요),
        호출측이 chunk_id → ref_id/document_id 를 역매핑하려고 전체 청크를
        메모리에 적재할 필요가 없게 한다.

        `meta_filter` 는 `document_meta` EXISTS 서브쿼리로 건다(None 또는 빈
        필터면 조건 없이 기존과 동일). 필터가 걸리면 ANN 이 post-filtering
        구조가 되어 유효 후보가 줄어들므로 `ef_search` 하한을 올린다.
        """
        if top_k <= 0:
            return []
        has_filter = meta_filter is not None and not meta_filter.is_empty()
        ef = max(_HNSW_EF_SEARCH_FILTERED if has_filter else _HNSW_EF_SEARCH, top_k)
        # SET 은 유틸리티 구문이라 바인드 파라미터를 받지 않는다(PG 파서가 거부).
        # ef 는 두 int 의 max() 결과라 사용자 입력이 섞일 수 없어 f-string 삽입이 안전하다.
        # SET LOCAL 이라 현재 트랜잭션 스코프에 한정되고 세션 전역을 오염시키지 않는다.
        self._session.execute(text(f"SET LOCAL hnsw.ef_search = {ef}"))
        distance = Chunk.embedding.cosine_distance(query_vector)
        stmt = (
            select(Chunk.id, Chunk.ref_id, Chunk.document_id, distance.label("distance"))
            .where(Chunk.chunk_type == chunk_type)
            .where(Chunk.embedding.is_not(None))
        )
        if candidate_ids is not None:
            if not candidate_ids:
                return []
            stmt = stmt.where(Chunk.id.in_(candidate_ids))
        if document_id is not None:
            stmt = stmt.where(Chunk.document_id == document_id)
        if project is not None or doc_types is not None:
            stmt = stmt.join(Document, Chunk.document_id == Document.id)
            if project is not None:
                stmt = stmt.where(Document.project == project)
            if doc_types is not None:
                stmt = stmt.where(Document.doc_type.in_(doc_types))
        if has_filter:
            stmt = stmt.where(document_meta_exists(meta_filter, Chunk.document_id))
        stmt = stmt.order_by(distance.asc(), Chunk.id.asc()).limit(top_k)
        rows = self._session.execute(stmt).all()
        return [
            ChunkVectorHit(chunk_id=cid, ref_id=ref_id, document_id=doc_id, score=1.0 - float(dist))
            for cid, ref_id, doc_id, dist in rows
        ]

    def get_texts_by_ids(self, chunk_ids: Sequence[str]) -> dict[str, str]:
        """chunk_id 집합에 대응하는 text 를 배치 조회한다(문서당 반복 조회 방지).

        문서 검색(doc36 Phase3)이 RRF 승자 청크의 스니펫 원문을 만들 때 쓴다.
        """
        if not chunk_ids:
            return {}
        stmt = select(Chunk.id, Chunk.text).where(Chunk.id.in_(chunk_ids))
        rows = self._session.execute(stmt).all()
        return {cid: chunk_text for cid, chunk_text in rows}
