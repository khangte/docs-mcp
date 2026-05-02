"""하이브리드 검색 서비스."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import EndpointNotFoundError
from app.models.openapi import ApiChunk, ApiEndpoint
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.services.search.keyword_search import KeywordSearch
from app.services.search.vector_search import VectorSearch


@dataclass
class SearchResultItem:
    """검색 결과 항목(엔드포인트 메타 + 점수 + 스니펫)."""

    endpoint_id: str
    document_id: str
    method: str
    path: str
    summary: str
    score: float
    keyword_score: float
    vector_score: float
    snippet: str


@dataclass
class SearchOptions:
    """검색 옵션(top_k·필터·모드)."""

    top_k: int = 5
    method: str | None = None
    tag: str | None = None
    document_id: str | None = None
    mode: str = "hybrid"  # hybrid | keyword | vector


class SearchService:
    """키워드/벡터 점수를 합쳐 엔드포인트 단위로 결과를 반환하는 검색 서비스."""

    def __init__(
        self,
        chunk_repo: ChunkRepository,
        endpoint_repo: EndpointRepository,
        keyword_search: KeywordSearch,
        vector_search: VectorSearch,
        hybrid_alpha: float = 0.4,
    ) -> None:
        """저장소·검색기·하이브리드 가중치를 보관한다."""
        self._chunk_repo = chunk_repo
        self._endpoint_repo = endpoint_repo
        self._keyword_search = keyword_search
        self._vector_search = vector_search
        self._alpha = hybrid_alpha

    def search(self, query: str, options: SearchOptions) -> list[SearchResultItem]:
        """후보 청크에 대해 키워드·벡터 점수를 합쳐 정렬한 엔드포인트 결과를 반환한다."""
        if options.mode not in {"hybrid", "keyword", "vector"}:
            from app.core.errors import ValidationError

            raise ValidationError(f"unsupported search mode: {options.mode}")

        candidate_chunks = self._build_candidate_chunks(options)
        if not candidate_chunks:
            return []
        candidate_ids = {c.id for c in candidate_chunks}

        keyword_hits = {
            h.chunk_id: h.score
            for h in self._keyword_search.search(
                query, top_k=len(candidate_ids), chunks=candidate_chunks
            )
        }
        vector_hits = {
            h.chunk_id: h.score
            for h in self._vector_search.search(query, top_k=len(candidate_ids), candidates=candidate_ids)
        }

        results: list[SearchResultItem] = []
        for chunk in candidate_chunks:
            if chunk.chunk_type != "endpoint":
                continue
            endpoint = self._endpoint_repo.get(chunk.ref_id)
            if endpoint is None:
                continue
            k_score = float(keyword_hits.get(chunk.id, 0.0))
            v_score = float(vector_hits.get(chunk.id, 0.0))
            combined = _combine_score(k_score, v_score, options.mode, self._alpha)
            results.append(
                SearchResultItem(
                    endpoint_id=endpoint.id,
                    document_id=endpoint.document_id,
                    method=endpoint.method,
                    path=endpoint.path,
                    summary=endpoint.summary,
                    score=combined,
                    keyword_score=k_score if options.mode != "vector" else 0.0,
                    vector_score=v_score if options.mode != "keyword" else 0.0,
                    snippet=_snippet(chunk),
                )
            )
        results = [r for r in results if r.score > 0.0]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[: options.top_k]

    def _build_candidate_chunks(self, options: SearchOptions) -> list[ApiChunk]:
        """SQL 필터로 후보 청크를 조회한다. N+1 쿼리 없음."""
        return list(
            self._chunk_repo.list_by_endpoint_filter(
                method=options.method,
                tag=options.tag,
                document_id=options.document_id,
            )
        )

    def get_endpoint_or_raise(self, endpoint_id: str) -> ApiEndpoint:
        """엔드포인트를 조회하고 없으면 EndpointNotFoundError 를 발생시킨다."""
        endpoint = self._endpoint_repo.get(endpoint_id)
        if endpoint is None:
            raise EndpointNotFoundError(endpoint_id)
        return endpoint


def _combine_score(
    keyword_score: float, vector_score: float, mode: str, alpha: float
) -> float:
    """모드에 따라 키워드/벡터 점수를 단독 또는 가중합으로 합친다."""
    if mode == "keyword":
        return keyword_score
    if mode == "vector":
        return vector_score
    return alpha * keyword_score + (1.0 - alpha) * vector_score


def _snippet(chunk: ApiChunk) -> str:
    """청크 텍스트가 길면 200자에서 잘라 말줄임표를 붙인 스니펫을 만든다."""
    text = chunk.text or ""
    if len(text) <= 200:
        return text
    return text[:200] + "…"
