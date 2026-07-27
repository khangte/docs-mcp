"""LLM Provider Protocol + 템플릿 기반 결정적 구현."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class CitationCtx:
    """LLM 컨텍스트로 전달할 인용 정보."""

    endpoint_id: str
    method: str
    path: str
    summary: str
    snippet: str


@dataclass
class LLMAnswer:
    """LLM 응답 텍스트와 근거 보유 여부."""

    text: str
    is_grounded: bool


class LLMProvider(Protocol):
    """LLM 생성 인터페이스."""

    def generate(self, question: str, context: list[CitationCtx]) -> LLMAnswer:
        """질문과 인용 컨텍스트를 받아 답변을 생성한다."""
        ...


NO_RESULT_MESSAGE = "해당 API 를 찾을 수 없습니다."


class TemplateLLMProvider:
    """결정적 템플릿 기반 응답 생성기.

    검색 결과 없으면 고정 문구, 있으면 method+path+요약+인용 형태로 조립.
    """

    def generate(self, question: str, context: list[CitationCtx]) -> LLMAnswer:
        """컨텍스트가 비면 안내 문구를, 있으면 결정적 템플릿으로 답변을 만들어 반환한다."""
        if not context:
            return LLMAnswer(text=NO_RESULT_MESSAGE, is_grounded=False)
        lines = [f"질문: {question}", "관련 자료:"]
        for idx, ctx in enumerate(context, start=1):
            summary = ctx.summary or "(요약 없음)"
            label = f"{ctx.method} {ctx.path} — {summary}" if ctx.method else summary
            lines.append(f"{idx}. {label} [{ctx.endpoint_id}]")
            if ctx.snippet:
                lines.append(f"   근거: {ctx.snippet[:140]}")
        return LLMAnswer(text="\n".join(lines), is_grounded=True)
