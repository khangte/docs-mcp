"""임베딩 Provider 프로토콜 + 해시/로컬 모델 기반 구현.

해시 기반 구현(`HashEmbeddingProvider`)은 외부 호출·모델 다운로드 없이 동일
입력 → 동일 벡터를 보장하며, 테스트 환경이나 모델 로드 실패 시 폴백 용도로
쓰인다. 로컬 모델 기반 구현(`LocalEmbeddingProvider`)은 CPU 에서
SentenceTransformer 를 돌려 실제 의미 유사도를 갖는 벡터를 만든다.
서비스 계층 코드가 어느 구현에도 영향받지 않도록 Protocol 사용.
"""

from __future__ import annotations

import functools
import hashlib
import math
from collections.abc import Sequence
from typing import Protocol

from sentence_transformers import SentenceTransformer

from app.services.search.tokenize import tokenize

#: E5 계열 모델의 학습 규약: 색인 대상 문서엔 "passage: ", 질의엔 "query: "
#: 접두사가 필요하다. 접두사를 섞거나 빠뜨리면 저장 벡터와 질의 벡터가 같은
#: 공간에서 비교되지 않아 벡터 검색 품질이 무너진다.
_PASSAGE_PREFIX = "passage: "
_QUERY_PREFIX = "query: "

#: LocalEmbeddingProvider.embed_query 결과 캐시 크기(질의 문자열 기준).
_QUERY_CACHE_SIZE = 256


class _SentenceEncoder(Protocol):
    """`SentenceTransformer`가 실제로 쓰는 부분만 뽑은 최소 계약(테스트 페이크 주입용)."""

    def encode(
        self, texts: list[str], normalize_embeddings: bool = ...
    ) -> Sequence[Sequence[float]]: ...

    def get_sentence_embedding_dimension(self) -> int: ...


class EmbeddingProvider(Protocol):
    """임베딩 계약.

    색인(문서)과 질의는 비대칭 접두사 처리가 필요할 수 있어 메서드를 분리한다.
    """

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """색인 대상 문서 텍스트들을 임베딩 벡터 리스트로 변환한다."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """질의 텍스트 한 건을 임베딩 벡터로 변환한다."""
        ...

    @property
    def dim(self) -> int:
        """임베딩 벡터 차원 수를 반환한다."""
        ...

    @property
    def is_semantic(self) -> bool:
        """의미 유사도를 갖는 임베딩인지 여부(벡터 fallback 가치 판단용)."""
        ...


class HashEmbeddingProvider:
    """토큰 해시 버킷 누적 + L2 정규화 기반 결정적 임베딩.

    문서/질의 구분(접두사)이 의미 없는 결정적 해시라 `embed_documents`/
    `embed_query` 모두 동일 로직으로 구현한다.
    """

    def __init__(self, dim: int = 256) -> None:
        """차원을 검증해 보관한다."""
        if dim <= 0:
            raise ValueError("dim must be positive")
        self._dim = dim

    @property
    def dim(self) -> int:
        """임베딩 차원 수를 반환한다."""
        return self._dim

    @property
    def is_semantic(self) -> bool:
        """해시 임베딩은 의미 유사도가 없다."""
        return False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """입력 문서 텍스트 각각을 결정적으로 임베딩한 벡터들을 반환한다."""
        return [self._embed_one(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """질의 텍스트 한 건을 결정적으로 임베딩한 벡터를 반환한다."""
        return self._embed_one(text)

    def _embed_one(self, text: str) -> list[float]:
        """텍스트 한 건을 토큰 해시 버킷 누적 후 L2 정규화한 벡터로 만든다."""
        vector = [0.0] * self._dim
        tokens = tokenize(text)
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


class LocalEmbeddingProvider:
    """로컬 CPU SentenceTransformer 모델 기반 임베딩 프로바이더.

    `encoder` 를 주입하지 않으면 `SentenceTransformer(model_name, device="cpu")`
    를 직접 생성한다. 테스트에서는 무거운 실모델 로딩을 피하려고 페이크
    인코더(`.encode(texts, normalize_embeddings=...)`,
    `.get_sentence_embedding_dimension()`)를 주입한다.
    """

    def __init__(self, model_name: str, encoder: _SentenceEncoder | None = None) -> None:
        """모델명을 보관하고, 인코더가 주입되지 않으면 SentenceTransformer 를 로드한다."""
        self._model_name = model_name
        self._encoder: _SentenceEncoder = (
            encoder if encoder is not None else SentenceTransformer(model_name, device="cpu")
        )
        self._embed_query_cached = functools.lru_cache(maxsize=_QUERY_CACHE_SIZE)(
            self._embed_query_uncached
        )

    @property
    def dim(self) -> int:
        """인코더가 보고하는 임베딩 차원 수를 반환한다."""
        return int(self._encoder.get_sentence_embedding_dimension())

    @property
    def is_semantic(self) -> bool:
        """로컬 모델 임베딩은 실제 의미 유사도를 갖는다."""
        return True

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """문서 텍스트들에 "passage: " 접두사를 붙여 인코딩한다. 빈 입력은 그대로 빈 리스트."""
        if not texts:
            return []
        prefixed = [_PASSAGE_PREFIX + text for text in texts]
        vectors = self._encoder.encode(prefixed, normalize_embeddings=True)
        return [list(vector) for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        """질의 텍스트에 "query: " 접두사를 붙여 인코딩한다(원본 질의 기준 LRU 캐시 적용)."""
        return list(self._embed_query_cached(text))

    def _embed_query_uncached(self, text: str) -> list[float]:
        """캐시를 거치지 않고 질의 텍스트를 실제로 인코딩한다."""
        vectors = self._encoder.encode([_QUERY_PREFIX + text], normalize_embeddings=True)
        return list(vectors[0])


def _l2_normalize(vector: list[float]) -> list[float]:
    """벡터를 L2 노름으로 나눠 단위 벡터로 만든다(0 벡터는 그대로 반환)."""
    norm_sq = sum(v * v for v in vector)
    if norm_sq <= 0.0:
        return vector
    norm = math.sqrt(norm_sq)
    return [v / norm for v in vector]
