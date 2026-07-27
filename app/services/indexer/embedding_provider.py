"""임베딩 Provider 프로토콜 + 해시/Gemini 기반 구현.

해시 기반 구현은 외부 호출 없이 동일 입력 → 동일 벡터를 보장하며,
Gemini API 키가 없을 때 폴백 용도로 쓰인다.
Gemini 기반 구현은 실제 의미 유사도를 갖는 임베딩 API 를 호출한다.
서비스 계층 코드가 어느 구현에도 영향받지 않도록 Protocol 사용.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from typing import Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.errors import IntegrationError

logger = logging.getLogger(__name__)

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


_RETRYABLE_STATUS_CODES = {429, 503}
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0


def _status_code_of(exc: Exception) -> int | None:
    """genai 예외에서 HTTP 상태 코드를 추출한다. 없으면 None."""
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


class GeminiEmbeddingProvider:
    """Gemini 임베딩 API 를 호출해 실제 의미 유사도를 갖는 벡터를 만드는 프로바이더."""

    def __init__(
        self, api_key: str, model: str, dim: int, client: genai.Client | None = None
    ) -> None:
        """API 키/모델명/차원을 보관하고, 주입되지 않으면 genai.Client 를 직접 생성한다."""
        if dim <= 0:
            raise ValueError("dim must be positive")
        self._model = model
        self._dim = dim
        self._client = client if client is not None else genai.Client(api_key=api_key)

    @property
    def dim(self) -> int:
        """임베딩 차원 수를 반환한다."""
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """입력 텍스트들을 Gemini 임베딩 API 로 변환한다. 빈 입력은 API 호출 없이 빈 리스트 반환."""
        if not texts:
            return []

        try:
            vectors = self._embed_with_retry(texts)
        except Exception as exc:
            logger.error("Gemini 임베딩 API 호출 실패: %s", exc)
            raise IntegrationError(f"Gemini 임베딩 API 호출 실패: {exc}") from exc

        return [_l2_normalize(vector) for vector in vectors]

    def _embed_with_retry(self, texts: list[str]) -> list[list[float]]:
        """429/503 응답은 지수 백오프로 재시도하고, 그 외 오류는 즉시 전파한다."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                result = self._client.models.embed_content(
                    model=self._model,
                    contents=texts,
                    config=genai_types.EmbedContentConfig(output_dimensionality=self._dim),
                )
                if not result.embeddings or len(result.embeddings) != len(texts):
                    raise ValueError("Gemini 임베딩 응답 개수가 입력 텍스트 개수와 불일치합니다")
                return [embedding.values for embedding in result.embeddings]
            except genai_errors.APIError as exc:
                last_exc = exc
                status_code = _status_code_of(exc)
                is_last_attempt = attempt == _MAX_ATTEMPTS - 1
                if status_code not in _RETRYABLE_STATUS_CODES or is_last_attempt:
                    raise
                delay = _RETRY_BASE_DELAY_SECONDS * (2**attempt)
                logger.warning(
                    "Gemini 임베딩 API 재시도 가능 오류(status=%s), %.1f초 후 재시도 (%d/%d)",
                    status_code, delay, attempt + 1, _MAX_ATTEMPTS,
                )
                time.sleep(delay)
        # 도달 불가하지만 타입 체커를 위해 명시
        assert last_exc is not None
        raise last_exc


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
