"""RAG 파이프라인: 검색 → 컨텍스트 조립 → LLM 응답."""

from __future__ import annotations

from dataclasses import dataclass

from src.services.rag.llm_provider import CitationCtx, LLMProvider
from src.services.search.search_service import SearchOptions, SearchService


@dataclass
class Citation:
    """RAG 응답에 포함되는 인용 항목."""

    endpoint_id: str
    method: str
    path: str
    snippet: str


@dataclass
class RAGAnswer:
    """RAG 응답 묶음(질문·답변·인용·문서·근거여부)."""

    question: str
    answer: str
    citations: list[Citation]
    used_documents: list[str]
    is_grounded: bool


class RAGService:
    """검색 결과를 LLM 컨텍스트로 합쳐 답변을 생성하는 서비스."""

    def __init__(
        self,
        search_service: SearchService,
        llm_provider: LLMProvider,
    ) -> None:
        """검색 서비스와 LLM 프로바이더 의존성을 보관한다."""
        self._search = search_service
        self._llm = llm_provider

    def answer(
        self,
        question: str,
        top_k: int = 5,
        document_id: str | None = None,
        method: str | None = None,
    ) -> RAGAnswer:
        """질문으로 하이브리드 검색을 수행하고 LLM 답변·인용을 묶어 반환한다."""
        options = SearchOptions(
            top_k=top_k,
            method=method,
            document_id=document_id,
            mode="hybrid",
        )
        hits = self._search.search(question, options)
        context = [
            CitationCtx(
                endpoint_id=h.endpoint_id,
                method=h.method,
                path=h.path,
                summary=h.summary,
                snippet=h.snippet,
            )
            for h in hits
        ]
        llm_answer = self._llm.generate(question, context)
        citations = [
            Citation(
                endpoint_id=h.endpoint_id,
                method=h.method,
                path=h.path,
                snippet=h.snippet,
            )
            for h in hits
        ]
        used_documents = sorted({h.document_id for h in hits})
        return RAGAnswer(
            question=question,
            answer=llm_answer.text,
            citations=citations,
            used_documents=used_documents,
            is_grounded=llm_answer.is_grounded,
        )
