"""임베딩 Provider 테스트."""

from __future__ import annotations

import math

from src.services.indexer.embedding_provider import HashEmbeddingProvider


def test_embedding_deterministic() -> None:
    provider = HashEmbeddingProvider(dim=64)
    a = provider.embed(["find pet by id"])[0]
    b = provider.embed(["find pet by id"])[0]
    assert a == b


def test_embedding_distinguishes_inputs() -> None:
    provider = HashEmbeddingProvider(dim=64)
    a = provider.embed(["find pet by id"])[0]
    b = provider.embed(["create user"])[0]
    assert a != b


def test_embedding_l2_norm_is_1() -> None:
    provider = HashEmbeddingProvider(dim=64)
    v = provider.embed(["find pet by id"])[0]
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6


def test_embedding_empty_input_has_nonzero_norm() -> None:
    provider = HashEmbeddingProvider(dim=32)
    v = provider.embed([""])[0]
    assert sum(abs(x) for x in v) > 0.0


def test_embedding_dim_invalid() -> None:
    import pytest

    with pytest.raises(ValueError):
        HashEmbeddingProvider(dim=0)
