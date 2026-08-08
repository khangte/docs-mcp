"""로컬 임베딩 프로바이더(E5 접두사 규약) 테스트.

실제 SentenceTransformer 모델 로딩 없이 페이크 인코더로 접두사 적용·정규화·
차원 계약을 검증한다. 실모델 의미 유사도 스모크는 opt-in(`@pytest.mark.slow`)으로
분리한다(`pytest -m 'not slow'`가 기본 실행 — 모델 다운로드/네트워크 불필요).
"""

from __future__ import annotations

import math

import pytest

from app.models.openapi import EMBEDDING_DIM
from app.services.indexer.embedding_provider import LocalEmbeddingProvider


class _FakeEncoder:
    """SentenceTransformer 를 대신하는 페이크. 인코딩 요청 인자를 그대로 기록한다."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim
        self.encode_calls: list[list[str]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts: list[str], normalize_embeddings: bool = False) -> list[list[float]]:
        self.encode_calls.append(list(texts))
        vectors = []
        for text in texts:
            seed = sum(ord(c) for c in text) or 1
            vector = [((seed * (i + 1)) % 97) / 97 for i in range(self._dim)]
            if normalize_embeddings:
                norm = math.sqrt(sum(v * v for v in vector)) or 1.0
                vector = [v / norm for v in vector]
            vectors.append(vector)
        return vectors


def test_embed_documents_applies_passage_prefix() -> None:
    encoder = _FakeEncoder()
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    provider.embed_documents(["사용자 인증 API"])

    assert encoder.encode_calls == [["passage: 사용자 인증 API"]]


def test_embed_query_applies_query_prefix() -> None:
    encoder = _FakeEncoder()
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    provider.embed_query("로그인")

    assert encoder.encode_calls == [["query: 로그인"]]


def test_embed_documents_empty_input_skips_encoding() -> None:
    encoder = _FakeEncoder()
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    result = provider.embed_documents([])

    assert result == []
    assert encoder.encode_calls == []


def test_dim_matches_embedding_dim_constant() -> None:
    provider = LocalEmbeddingProvider("fake-model", encoder=_FakeEncoder(dim=EMBEDDING_DIM))

    assert provider.dim == EMBEDDING_DIM == 384


def test_is_semantic_is_true() -> None:
    provider = LocalEmbeddingProvider("fake-model", encoder=_FakeEncoder())

    assert provider.is_semantic is True


def test_returned_vectors_are_l2_normalized() -> None:
    provider = LocalEmbeddingProvider("fake-model", encoder=_FakeEncoder())

    vector = provider.embed_query("정규화 확인")
    norm = math.sqrt(sum(v * v for v in vector))

    assert abs(norm - 1.0) < 1e-6


@pytest.mark.slow
def test_semantic_similarity_ranks_related_pair_higher() -> None:
    """실제 모델(다국어 E5)을 로드해 의미 유사도가 실제로 동작하는지 확인한다.

    최초 실행 시 HuggingFace 에서 모델을 내려받아 네트워크가 필요하다.
    """
    from app.core.config import get_settings

    provider = LocalEmbeddingProvider(get_settings().embedding_model)

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    query = provider.embed_query("로그인")
    related = provider.embed_documents(["사용자 인증 API"])[0]
    unrelated = provider.embed_documents(["결제 취소 처리"])[0]

    assert cosine(query, related) > cosine(query, unrelated)
