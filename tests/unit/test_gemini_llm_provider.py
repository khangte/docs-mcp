"""GeminiLLMProvider 테스트 (실제 네트워크 호출 없이 클라이언트를 모킹)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from google.genai import errors as genai_errors

from app.core.errors import IntegrationError
from app.services.rag.llm_provider import NO_RESULT_MESSAGE, CitationCtx, GeminiLLMProvider


@dataclass
class _FakeResponse:
    """genai 응답 객체를 흉내내는 더미."""

    text: str


class _FakeModels:
    """client.models.generate_content 호출을 기록/제어하는 더미."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeClient:
    """genai.Client 를 흉내내는 더미 (models 속성만 필요)."""

    def __init__(self, result: Any) -> None:
        self.models = _FakeModels(result)


def _make_api_error(code: int) -> genai_errors.APIError:
    """지정된 HTTP status code 를 갖는 APIError 를 만든다."""
    return genai_errors.APIError(code=code, response_json={"message": "boom"})


def _sample_context() -> list[CitationCtx]:
    return [
        CitationCtx(
            endpoint_id="ep-1",
            method="GET",
            path="/pets/{id}",
            summary="펫 조회",
            snippet="펫을 id 로 조회합니다.",
        )
    ]


def test_empty_context_returns_no_result_message_without_api_call() -> None:
    # Arrange
    client = _FakeClient(_FakeResponse(text="should not be used"))
    provider = GeminiLLMProvider(api_key="dummy", model="gemini-2.0-flash", client=client)

    # Act
    answer = provider.generate(question="아무 질문", context=[])

    # Assert
    assert answer.text == NO_RESULT_MESSAGE
    assert answer.is_grounded is False
    assert client.models.calls == []


def test_context_present_sends_grounded_prompt_and_returns_gemini_text() -> None:
    # Arrange
    client = _FakeClient(_FakeResponse(text="GET /pets/{id} 를 사용하세요."))
    provider = GeminiLLMProvider(api_key="dummy", model="gemini-2.0-flash", client=client)
    context = _sample_context()

    # Act
    answer = provider.generate(question="펫 조회 방법", context=context)

    # Assert
    assert answer.text == "GET /pets/{id} 를 사용하세요."
    assert answer.is_grounded is True
    assert len(client.models.calls) == 1
    prompt = client.models.calls[0]["contents"]
    assert "GET /pets/{id}" in prompt
    assert "ep-1" in prompt
    assert client.models.calls[0]["model"] == "gemini-2.0-flash"


def test_api_failure_raises_integration_error() -> None:
    # Arrange: 재시도 불가능한 오류(400)는 즉시 IntegrationError 로 변환된다
    client = _FakeClient(_make_api_error(code=400))
    provider = GeminiLLMProvider(api_key="dummy", model="gemini-2.0-flash", client=client)

    # Act / Assert
    with pytest.raises(IntegrationError):
        provider.generate(question="펫 조회 방법", context=_sample_context())
    assert len(client.models.calls) == 1


def test_rate_limited_error_retries_then_raises_integration_error(monkeypatch) -> None:
    # Arrange: 429 는 재시도 대상이지만, 계속 실패하면 결국 IntegrationError 로 변환된다
    monkeypatch.setattr("app.services.rag.llm_provider.time.sleep", lambda _seconds: None)
    client = _FakeClient(_make_api_error(code=429))
    provider = GeminiLLMProvider(api_key="dummy", model="gemini-2.0-flash", client=client)

    # Act / Assert
    with pytest.raises(IntegrationError):
        provider.generate(question="펫 조회 방법", context=_sample_context())
    assert len(client.models.calls) == 3  # _MAX_ATTEMPTS 만큼 재시도


def test_blocked_response_raises_integration_error() -> None:
    # Arrange: 응답은 왔지만 text 가 비어있는(차단된) 경우도 IntegrationError 로 변환된다
    client = _FakeClient(_FakeResponse(text=""))
    provider = GeminiLLMProvider(api_key="dummy", model="gemini-2.0-flash", client=client)

    # Act / Assert
    with pytest.raises(IntegrationError):
        provider.generate(question="펫 조회 방법", context=_sample_context())
    assert len(client.models.calls) == 1


def test_rate_limited_error_succeeds_after_retry(monkeypatch) -> None:
    # Arrange: 첫 호출은 503, 두 번째 호출은 성공
    monkeypatch.setattr("app.services.rag.llm_provider.time.sleep", lambda _seconds: None)
    responses = iter([_make_api_error(code=503), _FakeResponse(text="복구된 응답")])

    class _FlakyModels(_FakeModels):
        def generate_content(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result

    client = _FakeClient(None)
    client.models = _FlakyModels(None)
    provider = GeminiLLMProvider(api_key="dummy", model="gemini-2.0-flash", client=client)

    # Act
    answer = provider.generate(question="펫 조회 방법", context=_sample_context())

    # Assert
    assert answer.text == "복구된 응답"
    assert answer.is_grounded is True
    assert len(client.models.calls) == 2
