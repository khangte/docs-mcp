"""llm_client.py 단위 테스트: prefill JSON 파싱, 재시도/백오프 분기.

실제 네트워크를 타지 않도록 `httpx.MockTransport` 를 주입한다.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.errors import IntegrationError
from app.services.metadata import llm_client as llm_client_module
from app.services.metadata.llm_client import AnthropicClient


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """재시도 테스트가 실제로 몇 초씩 기다리지 않게 한다."""
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _seconds: None)


def _client(handler) -> AnthropicClient:
    return AnthropicClient(
        api_key="test-key",
        model="claude-test",
        transport=httpx.MockTransport(handler),
    )


def _messages_response(text: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json={"content": [{"text": text}]})


def test_generate_json_parses_prefilled_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _messages_response('"business_description": "설명"}')

    client = _client(handler)
    result = client.generate_json("system", "user")
    assert result == {"business_description": "설명"}


def test_generate_json_parses_response_with_leading_brace() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _messages_response('{"business_description": "설명"}')

    client = _client(handler)
    result = client.generate_json("system", "user")
    assert result == {"business_description": "설명"}


def test_generate_json_retries_once_then_raises_on_persistent_bad_json() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _messages_response("not json")

    client = _client(handler)
    with pytest.raises(IntegrationError):
        client.generate_json("system", "user")
    assert len(calls) == 2


def test_generate_json_recovers_after_one_bad_json_response() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return _messages_response("not json")
        return _messages_response('"ok": true}')

    client = _client(handler)
    result = client.generate_json("system", "user")
    assert result == {"ok": True}
    assert len(calls) == 2


def test_retryable_status_retries_then_succeeds() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return _messages_response('"ok": true}')

    client = _client(handler)
    result = client.generate_json("system", "user")
    assert result == {"ok": True}
    assert len(calls) == 2


def test_retry_after_header_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        llm_client_module.time, "sleep", lambda seconds: sleep_calls.append(seconds)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if not sleep_calls:
            return httpx.Response(429, headers={"retry-after": "7"})
        return _messages_response('"ok": true}')

    client = _client(handler)
    client.generate_json("system", "user")
    assert sleep_calls[0] == 7.0


def test_non_retryable_status_fails_immediately() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": "bad request"})

    client = _client(handler)
    with pytest.raises(IntegrationError):
        client.generate_json("system", "user")
    assert len(calls) == 1


def test_generate_json_sends_temperature_zero_and_prefill() -> None:
    seen_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_body.update(json.loads(request.content))
        return _messages_response('"ok": true}')

    client = _client(handler)
    client.generate_json("system", "user")
    assert seen_body["temperature"] == 0
    assert seen_body["messages"][-1] == {"role": "assistant", "content": "{"}
