"""`DOCS_MCP_STRUCTURED_AUGMENTATION_ENABLED` 설정 읽기 테스트(DB 불필요).

기본-OFF postprocessor 스위치가 명시적 truthy 값에만 켜지는지 확인한다
(`docs/architect-review/87` I1).
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

ENV_KEY = "DOCS_MCP_STRUCTURED_AUGMENTATION_ENABLED"


def test_structured_augmentation_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """설정이 없으면 기본값은 False — 배포 즉시 동작이 바뀌지 않는다."""
    monkeypatch.delenv(ENV_KEY, raising=False)

    assert Settings().structured_augmentation_enabled is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes"])
def test_structured_augmentation_reads_explicit_true(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """`1/true/yes`(대소문자 무시)만 True 로 읽는다."""
    monkeypatch.setenv(ENV_KEY, raw)

    assert Settings().structured_augmentation_enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "garbage"])
def test_structured_augmentation_rejects_non_true_values(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """그 밖의 값은 전부 False 로 처리한다(알 수 없는 값은 안전하게 OFF)."""
    monkeypatch.setenv(ENV_KEY, raw)

    assert Settings().structured_augmentation_enabled is False
