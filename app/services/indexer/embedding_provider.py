"""임베딩 Provider 프로토콜 + 결정적 해시 기반 구현.

기본 구현은 외부 호출 없이 동일 입력 → 동일 벡터를 보장한다.
추후 실 임베딩 API 로 교체할 때 서비스 계층 코드를 수정하지 않도록 Protocol 사용.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class EmbeddingProvider(Protocol):
    """임베딩 계약."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """입력 텍스트들을 임베딩 벡터 리스트로 변환한다."""
        ...

    @property
    def dim(self) -> int:
        """임베딩 벡터 차원 수를 반환한다."""
        ...


class HashEmbeddingProvider:
    """토큰 해시 버킷 누적 + L2 정규화 기반 결정적 임베딩."""

    def __init__(self, dim: int = 256) -> None:
        """차원을 검증해 보관한다."""
        if dim <= 0:
            raise ValueError("dim must be positive")
        self._dim = dim

    @property
    def dim(self) -> int:
        """임베딩 차원 수를 반환한다."""
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """입력 텍스트 각각을 결정적으로 임베딩한 벡터들을 반환한다."""
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        """텍스트 한 건을 토큰 해시 버킷 누적 후 L2 정규화한 벡터로 만든다."""
        vector = [0.0] * self._dim
        tokens = _tokenize(text)
        if not tokens:
            # 빈 입력도 결정적: zero 벡터 금지를 위해 한 bucket 에 1 을 박는다
            bucket = int(hashlib.sha256(b"__empty__").hexdigest(), 16) % self._dim
            vector[bucket] = 1.0
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            sign_byte = digest[4]
            sign = 1.0 if (sign_byte & 1) == 0 else -1.0
            vector[bucket] += sign
        return _l2_normalize(vector)


def _tokenize(text: str) -> list[str]:
    """입력 텍스트에서 영숫자/언더스코어 토큰을 소문자 리스트로 추출한다."""
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _l2_normalize(vector: list[float]) -> list[float]:
    """벡터를 L2 노름으로 나눠 단위 벡터로 만든다(0 벡터는 그대로 반환)."""
    norm_sq = sum(v * v for v in vector)
    if norm_sq <= 0.0:
        return vector
    norm = math.sqrt(norm_sq)
    return [v / norm for v in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """코사인 유사도. 벡터가 동일 차원이고 정규화돼 있으면 내적과 동치."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
