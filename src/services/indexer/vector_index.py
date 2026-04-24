"""인메모리 벡터 인덱스.

chunk_id → vector 맵을 유지해, 주어진 쿼리 벡터와의 코사인 유사도로 top-k 를 리턴.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.services.indexer.embedding_provider import cosine_similarity


@dataclass
class VectorHit:
    chunk_id: str
    score: float


class InMemoryVectorIndex:
    """현재 로드된 벡터만 조회하는 인메모리 인덱스."""

    def __init__(self) -> None:
        self._vectors: dict[str, list[float]] = {}

    def upsert(self, chunk_id: str, vector: list[float]) -> None:
        self._vectors[chunk_id] = list(vector)

    def delete(self, chunk_id: str) -> None:
        self._vectors.pop(chunk_id, None)

    def delete_many(self, chunk_ids: list[str]) -> None:
        for cid in chunk_ids:
            self._vectors.pop(cid, None)

    def clear(self) -> None:
        self._vectors.clear()

    def size(self) -> int:
        return len(self._vectors)

    def has(self, chunk_id: str) -> bool:
        return chunk_id in self._vectors

    def search(
        self,
        query_vector: list[float],
        top_k: int,
        candidates: set[str] | None = None,
    ) -> list[VectorHit]:
        """top-k 코사인 유사도 결과를 내림차순으로 반환.

        `candidates` 가 주어지면 그 안의 chunk 만 고려.
        """
        if top_k <= 0:
            return []
        hits: list[VectorHit] = []
        for cid, vec in self._vectors.items():
            if candidates is not None and cid not in candidates:
                continue
            score = cosine_similarity(query_vector, vec)
            hits.append(VectorHit(chunk_id=cid, score=score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
