"""GeminiQueryExpander 테스트 (실제 네트워크 호출 없이 클라이언트를 모킹)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.documents.query_expander import GeminiQueryExpander


@dataclass
class _FakeGenerateResponse:
    """genai generate_content 응답 객체를 흉내내는 더미."""

    text: str | None


class _FakeModels:
    """client.models.generate_content 호출을 기록/제어하는 더미."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> _FakeGenerateResponse:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeClient:
    """genai.Client 를 흉내내는 더미 (models 속성만 필요)."""

    def __init__(self, result: Any) -> None:
        self.models = _FakeModels(result)


def test_expand_parses_comma_separated_tokens() -> None:
    client = _FakeClient(_FakeGenerateResponse(text="결제, 내역, 주문"))
    expander = GeminiQueryExpander(api_key="dummy", model="gemini-2.5-flash", client=client)

    tokens = expander.expand("주문조회 API")

    assert tokens == ["결제", "내역", "주문"]
    assert client.models.calls[0]["model"] == "gemini-2.5-flash"


def test_expand_returns_empty_list_on_empty_response() -> None:
    client = _FakeClient(_FakeGenerateResponse(text=""))
    expander = GeminiQueryExpander(api_key="dummy", model="gemini-2.5-flash", client=client)

    assert expander.expand("아무거나") == []


def test_expand_returns_empty_list_on_api_failure() -> None:
    """LLM 호출이 예외를 던지면 검색을 막지 않도록 빈 리스트로 삼킨다."""
    client = _FakeClient(RuntimeError("network boom"))
    expander = GeminiQueryExpander(api_key="dummy", model="gemini-2.5-flash", client=client)

    assert expander.expand("아무거나") == []


def test_expand_caches_repeated_query_without_recalling_api() -> None:
    """같은 질의로 반복 호출해도 LLM 은 한 번만 호출된다(lru_cache)."""
    client = _FakeClient(_FakeGenerateResponse(text="결제, 내역"))
    expander = GeminiQueryExpander(api_key="dummy", model="gemini-2.5-flash", client=client)

    first = expander.expand("주문조회 API")
    second = expander.expand("주문조회 API")

    assert first == second == ["결제", "내역"]
    assert len(client.models.calls) == 1


def test_expand_truncates_to_max_expanded_tokens() -> None:
    many_tokens = ",".join(f"토큰{i}" for i in range(20))
    client = _FakeClient(_FakeGenerateResponse(text=many_tokens))
    expander = GeminiQueryExpander(api_key="dummy", model="gemini-2.5-flash", client=client)

    tokens = expander.expand("질의")

    assert len(tokens) == 8
