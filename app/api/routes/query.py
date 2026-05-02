"""RAG 질의 라우트."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import ServiceBundle
from app.api.dependency_providers import get_services
from app.schemas.query import Citation, QueryRequest, QueryResponse

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
def answer_query(
    body: QueryRequest,
    services: ServiceBundle = Depends(get_services),
) -> QueryResponse:
    """RAG 파이프라인으로 질의에 답하고 인용을 함께 반환한다."""
    result = services.rag_service.answer(
        question=body.question,
        top_k=body.top_k,
        document_id=body.document_id,
        method=body.method,
    )
    return QueryResponse(
        question=result.question,
        answer=result.answer,
        citations=[
            Citation(
                endpoint_id=c.endpoint_id,
                method=c.method,
                path=c.path,
                snippet=c.snippet,
            )
            for c in result.citations
        ],
        used_documents=result.used_documents,
        is_grounded=result.is_grounded,
    )
