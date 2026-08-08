"""해시 임베딩 Provider 테스트."""

from __future__ import annotations

import math

import pytest

from app.services.indexer.embedding_provider import HashEmbeddingProvider


def test_embedding_deterministic() -> None:
    provider = HashEmbeddingProvider(dim=64)
    a = provider.embed_documents(["find pet by id"])[0]
    b = provider.embed_documents(["find pet by id"])[0]
    assert a == b


def test_embedding_distinguishes_inputs() -> None:
    provider = HashEmbeddingProvider(dim=64)
    a = provider.embed_documents(["find pet by id"])[0]
    b = provider.embed_documents(["create user"])[0]
    assert a != b


def test_embedding_l2_norm_is_1() -> None:
    provider = HashEmbeddingProvider(dim=64)
    v = provider.embed_documents(["find pet by id"])[0]
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6


def test_embedding_empty_input_has_nonzero_norm() -> None:
    provider = HashEmbeddingProvider(dim=32)
    v = provider.embed_documents([""])[0]
    assert sum(abs(x) for x in v) > 0.0


def test_embedding_dim_invalid() -> None:
    with pytest.raises(ValueError):
        HashEmbeddingProvider(dim=0)


def test_embed_query_matches_embed_documents_for_same_text() -> None:
    """해시 프로바이더는 문서/질의 구분이 없는 결정적 임베딩이라 결과가 동일하다."""
    provider = HashEmbeddingProvider(dim=64)
    assert provider.embed_query("find pet by id") == provider.embed_documents(["find pet by id"])[0]


def test_is_semantic_is_false() -> None:
    provider = HashEmbeddingProvider(dim=64)
    assert provider.is_semantic is False
