"""하이브리드 검색 서비스."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.errors import EndpointNotFoundError
from src.models.openapi import ApiChunk, ApiEndpoint
from src.repositories.chunk_repository import ChunkRepository
from src.repositories.endpoint_repository import EndpointRepository
from src.services.search.keyword_search import KeywordSearch
from src.services.search.vector_search import VectorSearch


@dataclass
class SearchResultItem:
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
    top_k: int = 5
    method: str | None = None
    tag: str | None = None
    document_id: str | None = None
    mode: str = "hybrid"  # hybrid | keyword | vector


class SearchService:
    def __init__(
        self,
        chunk_repo: ChunkRepository,
        endpoint_repo: EndpointRepository,
        keyword_search: KeywordSearch,
        vector_search: VectorSearch,
        hybrid_alpha: float = 0.4,
    ) -> None:
        self._chunk_repo = chunk_repo
        self._endpoint_repo = endpoint_repo
        self._keyword_search = keyword_search
        self._vector_search = vector_search
        self._alpha = hybrid_alpha

    def search(self, query: str, options: SearchOptions) -> list[SearchResultItem]:
        if options.mode not in {"hybrid", "keyword", "vector"}:
            from src.core.errors import ValidationError

            raise ValidationError(f"unsupported search mode: {options.mode}")

        candidate_chunks = self._build_candidate_chunks(options)
        if not candidate_chunks:
            return []
        candidate_ids = {c.id for c in candidate_chunks}

        keyword_hits = {
            h.chunk_id: h.score
            for h in self._keyword_search.search(query, top_k=len(candidate_ids), candidates=candidate_ids)
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
        chunks = list(self._chunk_repo.list_all())
        if options.document_id:
            chunks = [c for c in chunks if c.document_id == options.document_id]
        if options.method is None and options.tag is None:
            return chunks
        # 필터링은 endpoint 에 따른다
        kept: list[ApiChunk] = []
        for chunk in chunks:
            if chunk.chunk_type != "endpoint":
                # schema 청크는 method/tag 로 필터링하지 않음
                kept.append(chunk)
                continue
            endpoint = self._endpoint_repo.get(chunk.ref_id)
            if endpoint is None:
                continue
            if options.method and endpoint.method.upper() != options.method.upper():
                continue
            if options.tag and options.tag not in endpoint.tags:
                continue
            kept.append(chunk)
        return kept

    def get_endpoint_or_raise(self, endpoint_id: str) -> ApiEndpoint:
        endpoint = self._endpoint_repo.get(endpoint_id)
        if endpoint is None:
            raise EndpointNotFoundError(endpoint_id)
        return endpoint


def _combine_score(
    keyword_score: float, vector_score: float, mode: str, alpha: float
) -> float:
    if mode == "keyword":
        return keyword_score
    if mode == "vector":
        return vector_score
    return alpha * keyword_score + (1.0 - alpha) * vector_score


def _snippet(chunk: ApiChunk) -> str:
    text = chunk.text or ""
    if len(text) <= 200:
        return text
    return text[:200] + "…"
