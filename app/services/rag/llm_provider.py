"""LLM Provider Protocol + 템플릿/Gemini 기반 구현."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.errors import IntegrationError

logger = logging.getLogger(__name__)


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
        lines = [f"질문: {question}", "관련 API:"]
        for idx, ctx in enumerate(context, start=1):
            summary = ctx.summary or "(요약 없음)"
            lines.append(
                f"{idx}. {ctx.method} {ctx.path} — {summary} [{ctx.endpoint_id}]"
            )
            if ctx.snippet:
                lines.append(f"   근거: {ctx.snippet[:140]}")
        return LLMAnswer(text="\n".join(lines), is_grounded=True)


_SYSTEM_INSTRUCTION = (
    "당신은 OpenAPI 문서 기반 RAG 어시스턴트입니다. "
    "아래에 주어지는 '관련 API' 컨텍스트에 근거해서만 답변하세요. "
    "컨텍스트에 없는 내용을 추측하거나 지어내지 마세요. "
    "답변에는 근거로 사용한 API 의 method, path 를 명시하세요."
)

_RETRYABLE_STATUS_CODES = {429, 503}
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0


def _build_prompt(question: str, context: list[CitationCtx]) -> str:
    """질문과 인용 컨텍스트를 Gemini 프롬프트 문자열로 조립한다."""
    lines = [f"질문: {question}", "", "관련 API:"]
    for idx, ctx in enumerate(context, start=1):
        summary = ctx.summary or "(요약 없음)"
        lines.append(f"{idx}. {ctx.method} {ctx.path} — {summary} [{ctx.endpoint_id}]")
        if ctx.snippet:
            lines.append(f"   근거: {ctx.snippet[:500]}")
    return "\n".join(lines)


def _status_code_of(exc: Exception) -> int | None:
    """genai 예외에서 HTTP 상태 코드를 추출한다. 없으면 None."""
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


class GeminiLLMProvider:
    """Gemini API 를 호출해 RAG 컨텍스트 기반 답변을 생성하는 프로바이더."""

    def __init__(self, api_key: str, model: str, client: genai.Client | None = None) -> None:
        """API 키/모델명을 보관하고, 주입되지 않으면 genai.Client 를 직접 생성한다."""
        self._model = model
        self._client = client if client is not None else genai.Client(api_key=api_key)

    def generate(self, question: str, context: list[CitationCtx]) -> LLMAnswer:
        """컨텍스트가 비면 안내 문구를 즉시 반환하고, 있으면 Gemini 를 호출해 답변을 만든다."""
        if not context:
            return LLMAnswer(text=NO_RESULT_MESSAGE, is_grounded=False)

        prompt = _build_prompt(question, context)
        try:
            response_text = self._generate_with_retry(prompt)
        except Exception as exc:
            logger.error("Gemini API 호출 실패: %s", exc)
            raise IntegrationError(f"Gemini API 호출 실패: {exc}") from exc

        return LLMAnswer(text=response_text, is_grounded=True)

    def _generate_with_retry(self, prompt: str) -> str:
        """429/503 응답은 지수 백오프로 재시도하고, 그 외 오류는 즉시 전파한다."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=_SYSTEM_INSTRUCTION,
                    ),
                )
                if not response.text:
                    raise ValueError("Gemini 응답에 텍스트가 없습니다 (빈 응답 또는 차단됨)")
                return response.text
            except genai_errors.APIError as exc:
                last_exc = exc
                status_code = _status_code_of(exc)
                is_last_attempt = attempt == _MAX_ATTEMPTS - 1
                if status_code not in _RETRYABLE_STATUS_CODES or is_last_attempt:
                    raise
                delay = _RETRY_BASE_DELAY_SECONDS * (2**attempt)
                logger.warning(
                    "Gemini API 재시도 가능 오류(status=%s), %.1f초 후 재시도 (%d/%d)",
                    status_code, delay, attempt + 1, _MAX_ATTEMPTS,
                )
                time.sleep(delay)
        # 도달 불가하지만 타입 체커를 위해 명시
        assert last_exc is not None
        raise last_exc
