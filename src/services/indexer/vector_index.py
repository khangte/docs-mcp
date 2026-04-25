"""인메모리 벡터 인덱스.

chunk_id → vector 맵을 유지해, 주어진 쿼리 벡터와의 코사인 유사도로 top-k 를 리턴.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.services.indexer.embedding_provider import cosine_similarity


@dataclass
class VectorHit:
    """벡터 검색 결과 한 건(청크 ID + 유사도 점수)."""

    chunk_id: str
    score: float


class InMemoryVectorIndex:
    """현재 로드된 벡터만 조회하는 인메모리 인덱스."""

    def __init__(self) -> None:
        """청크ID→벡터 저장용 빈 dict 를 초기화한다."""
        self._vectors: dict[str, list[float]] = {}

    def upsert(self, chunk_id: str, vector: list[float]) -> None:
        """청크 벡터를 추가하거나 갱신한다."""
        self._vectors[chunk_id] = list(vector)

    def delete(self, chunk_id: str) -> None:
        """청크 벡터를 인덱스에서 제거한다."""
        self._vectors.pop(chunk_id, None)

    def delete_many(self, chunk_ids: list[str]) -> None:
        """여러 청크의 벡터를 인덱스에서 한 번에 제거한다."""
        for cid in chunk_ids:
            self._vectors.pop(cid, None)

    def clear(self) -> None:
        """모든 벡터를 비운다."""
        self._vectors.clear()

    def size(self) -> int:
        """저장된 벡터 개수를 반환한다."""
        return len(self._vectors)

    def has(self, chunk_id: str) -> bool:
        """주어진 청크 ID 의 벡터가 인덱스에 있는지 여부를 반환한다."""
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
