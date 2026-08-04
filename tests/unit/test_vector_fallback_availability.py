"""벡터 보조 활성화 판별 테스트.

"Gemini API 키가 없는 환경에서는 벡터 보조 단계가 에러 없이 자동 생략된다"
(SPEC 기능 1 검증 기준)를 판별 로직 단위에서 확인한다. DB 는 필요 없다.
"""

from __future__ import annotations

import pytest

from app.composition import is_vector_fallback_available

ENV_KEY = "DOCS_MCP_GEMINI_API_KEY"


def test_disabled_when_api_key_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """환경변수가 아예 없으면 벡터 보조가 비활성이다."""
    monkeypatch.delenv(ENV_KEY, raising=False)

    assert is_vector_fallback_available() is False


def test_disabled_when_api_key_is_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 문자열 키도 "없음"으로 취급한다."""
    monkeypatch.setenv(ENV_KEY, "")

    assert is_vector_fallback_available() is False


def test_enabled_when_api_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """키가 설정돼 있으면 벡터 보조가 활성이다."""
    monkeypatch.setenv(ENV_KEY, "test-api-key")

    assert is_vector_fallback_available() is True
