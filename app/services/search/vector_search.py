"""벡터 검색 (인메모리 코사인)."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.indexer.embedding_provider import EmbeddingProvider
from app.services.indexer.vector_index import InMemoryVectorIndex


@dataclass
class VectorSearchHit:
    """벡터 검색 결과 한 건(청크 ID + 점수)."""

    chunk_id: str
    score: float


class VectorSearch:
    """쿼리를 임베딩해 인메모리 벡터 인덱스에서 코사인 유사도로 검색한다."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        vector_index: InMemoryVectorIndex,
    ) -> None:
        """임베딩 프로바이더와 벡터 인덱스 의존성을 보관한다."""
        self._embedding_provider = embedding_provider
        self._vector_index = vector_index

    def search(
        self,
        query: str,
        top_k: int,
        candidates: set[str] | None = None,
    ) -> list[VectorSearchHit]:
        """쿼리를 임베딩해 벡터 인덱스에서 top_k 결과를 검색하고 음수 점수는 0 으로 잘라 반환한다."""
        if not query or not query.strip():
            return []
        query_vec = self._embedding_provider.embed([query])[0]
        hits = self._vector_index.search(query_vec, top_k=top_k, candidates=candidates)
        # 유사도는 [-1, 1]. 음수도 의미 없는 후보로 간주하지 않지만 UI 상으로는 [0,1] 정규화.
        return [
            VectorSearchHit(chunk_id=h.chunk_id, score=max(0.0, h.score))
            for h in hits
        ]
