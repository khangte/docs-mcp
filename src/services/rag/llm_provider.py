"""LLM Provider Protocol + 템플릿 기반 결정적 구현."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class CitationCtx:
    endpoint_id: str
    method: str
    path: str
    summary: str
    snippet: str


@dataclass
class LLMAnswer:
    text: str
    is_grounded: bool


class LLMProvider(Protocol):
    def generate(self, question: str, context: list[CitationCtx]) -> LLMAnswer: ...


NO_RESULT_MESSAGE = "해당 API 를 찾을 수 없습니다."


class TemplateLLMProvider:
    """결정적 템플릿 기반 응답 생성기.

    검색 결과 없으면 고정 문구, 있으면 method+path+요약+인용 형태로 조립.
    """

    def generate(self, question: str, context: list[CitationCtx]) -> LLMAnswer:
        if not context:
            return LLMAnswer(text=NO_RESULT_MESSAGE, is_grounded=False)
        lines = [f"질문: {question}", "관련 API:"]
        for idx, ctx in enumerate(context, start=1):
            summary = ctx.summary or "(요약 없음)"
            lines.append(
                f"{idx}. {ctx.method} {ctx.path} — {summary} [{ctx.endpoint_id}]"
            )
            if ctx.snippet:
                lines.append(f"   근거: {ctx.snippet[:140]}")
        return LLMAnswer(text="\n".join(lines), is_grounded=True)
