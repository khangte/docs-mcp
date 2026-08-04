"""키워드/임베딩 검색 공용 토큰화 유틸."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    """텍스트를 영숫자/언더스코어 단위로 잘라 소문자 토큰 리스트로 반환한다."""
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]
