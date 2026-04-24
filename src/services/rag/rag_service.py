"""RAG 파이프라인: 검색 → 컨텍스트 조립 → LLM 응답."""

from __future__ import annotations

from dataclasses import dataclass

from src.services.rag.llm_provider import CitationCtx, LLMProvider
from src.services.search.search_service import SearchOptions, SearchService


@dataclass
class Citation:
    endpoint_id: str
    method: str
    path: str
    snippet: str


@dataclass
class RAGAnswer:
    question: str
    answer: str
    citations: list[Citation]
    used_documents: list[str]
    is_grounded: bool


class RAGService:
    def __init__(
        self,
        search_service: SearchService,
        llm_provider: LLMProvider,
    ) -> None:
        self._search = search_service
        self._llm = llm_provider

    def answer(
        self,
        question: str,
        top_k: int = 5,
        document_id: str | None = None,
        method: str | None = None,
    ) -> RAGAnswer:
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
