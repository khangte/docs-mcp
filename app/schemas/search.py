"""검색 요청/응답 DTO."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
