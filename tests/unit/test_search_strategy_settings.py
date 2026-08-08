"""`DOCS_MCP_SEARCH_STRATEGY` 설정 읽기 테스트(DB 불필요)."""

from __future__ import annotations

import pytest

from app.core.config import Settings

ENV_KEY = "DOCS_MCP_SEARCH_STRATEGY"


def test_defaults_to_rrf_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """설정이 없으면 기본값은 rrf 다(바로 켜는 안전 롤아웃, 5.5절)."""
    monkeypatch.delenv(ENV_KEY, raising=False)

    assert Settings().search_strategy == "rrf"


def test_reads_fallback_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """롤백 스위치: env 를 fallback 으로 두면 그 값을 그대로 읽는다."""
    monkeypatch.setenv(ENV_KEY, "fallback")

    assert Settings().search_strategy == "fallback"
