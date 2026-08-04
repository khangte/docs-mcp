"""키워드 검색 (토큰 매칭 수 기반 간단 점수).

SQLite 에서도 동작하도록 LOWER + LIKE 대신 Python 레벨 토큰 매칭으로 점수를 낸다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.openapi import ApiChunk
from app.repositories.chunk_repository import ChunkRepository
from app.services.search.tokenize import tokenize


@dataclass
class KeywordHit:
    """키워드 검색 결과 한 건(청크 ID + 점수)."""

    chunk_id: str
    score: float


class KeywordSearch:
    """청크 텍스트와 쿼리의 토큰 겹침 비율로 키워드 검색을 수행한다."""

    def __init__(self, chunk_repo: ChunkRepository) -> None:
        """청크 저장소 의존성을 보관한다."""
        self._chunk_repo = chunk_repo

    def search(
        self,
        query: str,
        top_k: int,
        candidates: set[str] | None = None,
        chunks: list[ApiChunk] | None = None,
    ) -> list[KeywordHit]:
        """쿼리 토큰 매칭 비율로 점수를 매겨 상위 top_k 결과를 반환한다.

        chunks 가 주어지면 해당 목록을 사용하고, 없으면 저장소에서 전체 조회한다.
        """
        q_tokens = set(tokenize(query))
        if not q_tokens:
            return []
        source = chunks if chunks is not None else list(self._chunk_repo.list_all())
        scored: list[KeywordHit] = []
        for chunk in source:
            if candidates is not None and chunk.id not in candidates:
                continue
            score = _score_chunk(chunk, q_tokens)
            if score > 0:
                scored.append(KeywordHit(chunk_id=chunk.id, score=score))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]


def _score_chunk(chunk: ApiChunk, q_tokens: set[str]) -> float:
    """청크 토큰과 쿼리 토큰의 교집합 비율로 점수를 계산한다."""
    c_tokens = set(tokenize(chunk.text))
    overlap = q_tokens & c_tokens
    if not overlap:
        return 0.0
    # 정규화: 쿼리 토큰 기준 매칭 비율
    return len(overlap) / max(1, len(q_tokens))
