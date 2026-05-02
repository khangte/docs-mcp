"""RAG 질의/응답 DTO."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Citation(BaseModel):
    """RAG 응답 인용 DTO."""

    endpoint_id: str
    method: str
    path: str
    snippet: str


class QueryRequest(BaseModel):
    """RAG 질의 요청 본문."""

    question: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=10)
    document_id: str | None = None
    method: str | None = None

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        """질문 양 끝 공백을 제거하고 빈 문자열을 거부한다."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be empty")
        return stripped


class QueryResponse(BaseModel):
    """RAG 질의 응답 DTO."""

    question: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    used_documents: list[str] = Field(default_factory=list)
    is_grounded: bool
