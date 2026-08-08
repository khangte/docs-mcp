"""벡터 검색 (pgvector 코사인 거리)."""

from __future__ import annotations

from dataclasses import dataclass

from app.repositories.chunk_repository import ChunkRepository
from app.services.indexer.embedding_provider import EmbeddingProvider


@dataclass
class VectorSearchHit:
    """벡터 검색 결과 한 건(청크 ID + 엔드포인트 ref_id + 점수)."""

    chunk_id: str
    ref_id: str
    score: float


class VectorSearch:
    """쿼리를 임베딩해 pgvector 코사인 거리로 DB에서 직접 검색한다."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        chunk_repo: ChunkRepository,
    ) -> None:
        """임베딩 프로바이더와 청크 저장소 의존성을 보관한다."""
        self._embedding_provider = embedding_provider
        self._chunk_repo = chunk_repo

    def search(
        self,
        query: str,
        top_k: int,
        candidates: set[str] | None = None,
    ) -> list[VectorSearchHit]:
        """쿼리를 임베딩해 top_k 결과를 검색하고 음수 점수는 0 으로 잘라 반환한다."""
        if not query or not query.strip():
            return []
        query_vec = self._embedding_provider.embed_query(query)
        hits = self._chunk_repo.search_by_vector(query_vec, top_k=top_k, candidate_ids=candidates)
        # 유사도는 [-1, 1]. 음수도 의미 없는 후보로 간주하지 않지만 UI 상으로는 [0,1] 정규화.
        return [
            VectorSearchHit(chunk_id=h.chunk_id, ref_id=h.ref_id, score=max(0.0, h.score))
            for h in hits
        ]
