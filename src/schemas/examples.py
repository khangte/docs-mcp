"""요청 예시 생성 DTO."""

from __future__ import annotations

from pydantic import BaseModel


class ExampleResponse(BaseModel):
    format: str
    code: str
    notes: str | None = None
