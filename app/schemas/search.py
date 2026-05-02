"""검색 요청/응답 DTO."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class SearchItem(BaseModel):
    """검색 결과 항목 DTO."""

    endpoint_id: str
    document_id: str
    method: str
    path: str
    summary: str
    score: float
    keyword_score: float = 0.0
    vector_score: float = 0.0
    snippet: str


class SearchResponse(BaseModel):
    """검색 응답 DTO."""

    query: str
    count: int
    items: list[SearchItem] = Field(default_factory=list)


class SearchRequest(BaseModel):
    """검색 요청 DTO."""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    method: str | None = None
    tag: str | None = None
    document_id: str | None = None

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        """쿼리 양 끝 공백을 제거하고 빈 문자열을 거부한다."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be empty")
        return stripped
