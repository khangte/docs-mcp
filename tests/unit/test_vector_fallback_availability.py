"""벡터 보조 활성화 판별 테스트.

"임베딩 백엔드가 해시 폴백이면 벡터 보조 단계가 에러 없이 자동 생략된다"
(SPEC 기능 1 검증 기준, 로컬 모델 도입 후 판별 기준 갱신)를 판별 로직
단위에서 확인한다. DB 는 필요 없다.
"""

from __future__ import annotations

import pytest

from app.composition import is_vector_fallback_available

ENV_KEY = "DOCS_MCP_EMBEDDING_BACKEND"


def test_disabled_when_backend_is_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """백엔드가 hash 로 지정되면 벡터 보조가 비활성이다."""
    monkeypatch.setenv(ENV_KEY, "hash")

    assert is_vector_fallback_available() is False


def test_enabled_when_backend_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """백엔드 설정이 없으면 기본값(local)이라 벡터 보조가 활성이다."""
    monkeypatch.delenv(ENV_KEY, raising=False)

    assert is_vector_fallback_available() is True


def test_enabled_when_backend_is_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """백엔드가 명시적으로 local 이면 벡터 보조가 활성이다."""
    monkeypatch.setenv(ENV_KEY, "local")

    assert is_vector_fallback_available() is True
