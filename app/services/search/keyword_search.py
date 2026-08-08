"""키워드 검색 (Postgres FTS `to_tsquery` OR 매칭 + `ts_rank`)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.repositories.chunk_repository import ChunkRepository

_TERM_RE = re.compile(r"[0-9A-Za-z_]+|[가-힣]+")


def tokenize_terms(query: str) -> list[str]:
    """질의를 영숫자/언더스코어 또는 한글 덩어리 단위 소문자 term 으로 자른다.

    `api_chunk.text_tsv` 생성 컬럼이 `to_tsvector('simple', text)` 로 한글
    단어 토큰까지 인덱싱하므로, 질의 쪽 term 화도 한글을 포함해 맞춘다.
    """
    return [t.lower() for t in _TERM_RE.findall(query or "")]


@dataclass
class KeywordHit:
    """키워드 검색 결과 한 건(청크 ID + 엔드포인트 ref_id + ts_rank 점수)."""

    chunk_id: str
    ref_id: str
    score: float


class KeywordSearch:
    """`api_chunk.text_tsv` GIN 인덱스로 endpoint 청크를 키워드 검색한다."""

    def __init__(self, chunk_repo: ChunkRepository) -> None:
        """청크 저장소 의존성을 보관한다."""
        self._chunk_repo = chunk_repo

    def search(
        self,
        query: str,
        top_k: int,
        *,
        document_id: str | None = None,
        project: str | None = None,
    ) -> list[KeywordHit]:
        """질의 term 을 OR 로 결합해 top_k 결과를 `ts_rank` 내림차순으로 반환한다.

        스코프(document_id/project)는 SQL 로 필터링한다 — 후보 청크를
        Python 메모리에 미리 적재하지 않는다.
        """
        terms = tokenize_terms(query)
        if not terms:
            return []
        hits = self._chunk_repo.search_endpoint_by_text(
            terms, top_k=top_k, document_id=document_id, project=project
        )
        return [KeywordHit(chunk_id=h.chunk_id, ref_id=h.ref_id, score=h.score) for h in hits]
