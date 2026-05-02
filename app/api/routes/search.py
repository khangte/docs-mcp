"""검색 라우트: 하이브리드/키워드/벡터."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import ServiceBundle
from app.api.dependency_providers import get_services
from app.core.errors import ValidationError
from app.schemas.search import SearchItem, SearchResponse
from app.services.search.search_service import SearchOptions

router = APIRouter()

_ALLOWED_MODES = {"hybrid", "keyword", "vector"}


@router.get("/search", response_model=SearchResponse)
def do_search(
    query: str = Query(min_length=1),
    top_k: int = Query(default=5, ge=1, le=20),
    mode: str = Query(default="hybrid"),
    method: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    document_id: str | None = Query(default=None),
    services: ServiceBundle = Depends(get_services),
) -> SearchResponse:
    """하이브리드/키워드/벡터 모드 중 하나로 엔드포인트를 검색해 결과를 반환한다."""
    stripped = query.strip()
    if not stripped:
        raise ValidationError("query must not be empty")
    if mode not in _ALLOWED_MODES:
        raise ValidationError(f"unsupported mode: {mode}")
    options = SearchOptions(
        top_k=top_k, method=method, tag=tag, document_id=document_id, mode=mode
    )
    results = services.search_service.search(stripped, options)
    items = [
        SearchItem(
            endpoint_id=r.endpoint_id,
            document_id=r.document_id,
            method=r.method,
            path=r.path,
            summary=r.summary,
            score=round(min(1.0, max(0.0, r.score)), 6),
            keyword_score=round(r.keyword_score, 6),
            vector_score=round(r.vector_score, 6),
            snippet=r.snippet,
        )
        for r in results
    ]
    return SearchResponse(query=stripped, count=len(items), items=items)
