"""키워드 검색 (토큰 매칭 수 기반 간단 점수).

SQLite 에서도 동작하도록 LOWER + LIKE 대신 Python 레벨 토큰 매칭으로 점수를 낸다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.models.openapi import ApiChunk
from src.repositories.chunk_repository import ChunkRepository


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class KeywordHit:
    chunk_id: str
    score: float


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


class KeywordSearch:
    def __init__(self, chunk_repo: ChunkRepository) -> None:
        self._chunk_repo = chunk_repo

    def search(
        self,
        query: str,
        top_k: int,
        candidates: set[str] | None = None,
    ) -> list[KeywordHit]:
        q_tokens = set(tokenize(query))
        if not q_tokens:
            return []
        chunks = self._chunk_repo.list_all()
        scored: list[KeywordHit] = []
        for chunk in chunks:
            if candidates is not None and chunk.id not in candidates:
                continue
            score = _score_chunk(chunk, q_tokens)
            if score > 0:
                scored.append(KeywordHit(chunk_id=chunk.id, score=score))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]


def _score_chunk(chunk: ApiChunk, q_tokens: set[str]) -> float:
    c_tokens = set(tokenize(chunk.text))
    overlap = q_tokens & c_tokens
    if not overlap:
        return 0.0
    # 정규화: 쿼리 토큰 기준 매칭 비율
    return len(overlap) / max(1, len(q_tokens))
