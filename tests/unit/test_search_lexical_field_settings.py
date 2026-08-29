"""`DOCS_MCP_SEARCH_LEXICAL_FIELD` 설정 읽기 테스트(DB 불필요)."""

from __future__ import annotations

import pytest

from app.core.config import Settings

ENV_KEY = "DOCS_MCP_SEARCH_LEXICAL_FIELD"


def test_defaults_to_text_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """기본값은 text — 배포 즉시 동작이 바뀌지 않는다(78번 §6)."""
    monkeypatch.delenv(ENV_KEY, raising=False)

    assert Settings().search_lexical_field == "text"


def test_reads_structured_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 를 structured 로 두면 그 값을 그대로 읽는다."""
    monkeypatch.setenv(ENV_KEY, "structured")

    assert Settings().search_lexical_field == "structured"
