"""Anthropic Messages API httpx 클라이언트 — 재시도/JSON prefill 파싱 전담.

docs/architect-review/55 §1: LLM SDK 를 도입하지 않고 httpx 로 직접 호출한다
(이미 메인 dependency). Anthropic 고유 로직(엔드포인트 URL, 헤더, 응답
파싱)은 전부 이 파일 안에 둔다.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.core.errors import IntegrationError
from app.core.logging import get_logger

_LOG = get_logger("docs_mcp.metadata.llm_client")

_ANTHROPIC_VERSION = "2023-06-01"
_MESSAGES_PATH = "/v1/messages"
_MAX_RETRIES = 5
_RETRYABLE_STATUS = {429, 500, 529}
#: assistant 턴을 이걸로 prefill 해 서두 산문을 원천 차단한다(55 §2.5).
_JSON_PREFILL = "{"


class AnthropicClient:
    """Anthropic Messages API 얇은 클라이언트. `temperature=0` 고정(재현성)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        api_base: str = "https://api.anthropic.com",
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """자격증명·모델·엔드포인트를 보관한다.

        `transport` 는 테스트에서 `httpx.MockTransport` 를 주입하기 위한
        훅이다(운영 경로에서는 None 으로 기본 전송을 쓴다).
        """
        self._api_key = api_key
        self._model = model
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def model(self) -> str:
        """호출에 쓰는 모델명(`endpoint_business_metadata.model` 에 그대로 기록됨)."""
        return self._model

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        """system/user 프롬프트로 호출해 파싱된 JSON dict 를 반환한다.

        파싱 실패 시 1회만 재시도하고, 그래도 실패하면 IntegrationError
        (호출측이 해당 엔드포인트만 건너뛰고 계속 진행하는 근거, 55 §2.5).
        """
        raw_text = self._call(system, user)
        try:
            return _parse_prefilled_json(raw_text)
        except ValueError:
            _LOG.warning("LLM JSON 파싱 실패, 1회 재시도")
            raw_text = self._call(system, user)
            try:
                return _parse_prefilled_json(raw_text)
            except ValueError as exc:
                raise IntegrationError(f"LLM 응답 JSON 파싱 실패: {raw_text[:200]}") from exc

    def _call(self, system: str, user: str) -> str:
        """1회 API 호출(재시도 포함)을 수행해 `content[0].text` 를 반환한다."""
        body = {
            "model": self._model,
            "max_tokens": 1024,
            "temperature": 0,
            "system": system,
            "messages": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": _JSON_PREFILL},
            ],
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        with httpx.Client(
            base_url=self._api_base, timeout=self._timeout, transport=self._transport
        ) as client:
            for attempt in range(_MAX_RETRIES):
                is_last = attempt == _MAX_RETRIES - 1
                try:
                    response = client.post(_MESSAGES_PATH, json=body, headers=headers)
                except httpx.HTTPError as exc:
                    if is_last:
                        raise IntegrationError(f"LLM 호출 실패: {exc}") from exc
                    time.sleep(_backoff_seconds(attempt))
                    continue
                if response.status_code in _RETRYABLE_STATUS and not is_last:
                    time.sleep(_retry_delay_seconds(response, attempt))
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise IntegrationError(
                        f"LLM 호출 실패 (status {response.status_code}): {response.text[:200]}"
                    ) from exc
                return _extract_text(response.json())
        # pragma: no cover — 위 루프가 항상 반환/raise
        raise IntegrationError("LLM 호출 재시도 소진")


def _backoff_seconds(attempt: int) -> float:
    """지수 백오프(0, 1, 2, 4, ...초)."""
    return float(2**attempt) if attempt > 0 else 1.0


def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    """`retry-after` 헤더가 있으면 우선한다(55 §1)."""
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return _backoff_seconds(attempt)


def _extract_text(payload: dict[str, Any]) -> str:
    """Messages API 응답에서 `content[0].text` 를 꺼낸다."""
    content = payload.get("content") or []
    if not content:
        raise IntegrationError("LLM 응답에 content가 없음")
    return str(content[0].get("text", ""))


def _parse_prefilled_json(raw_text: str) -> dict[str, Any]:
    """prefill(`{`)로 시작한 응답을 완전한 JSON으로 복원해 파싱한다."""
    text = raw_text if raw_text.lstrip().startswith("{") else _JSON_PREFILL + raw_text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(parsed, dict):
        raise ValueError("최상위 JSON이 객체가 아님")
    return dict(parsed)
