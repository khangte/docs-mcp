"""로컬 임베딩 프로바이더(E5 접두사 규약) 테스트.

실제 SentenceTransformer 모델 로딩 없이 페이크 인코더로 접두사 적용·정규화·
차원 계약을 검증한다. 실모델 의미 유사도 스모크는 opt-in(`@pytest.mark.slow`)으로
분리한다(`pytest -m 'not slow'`가 기본 실행 — 모델 다운로드/네트워크 불필요).
"""

from __future__ import annotations

import logging
import math

import pytest

from app.models import EMBEDDING_DIM
from app.services.indexer.embedding_provider import LocalEmbeddingProvider


class _FakeTokenizer:
    """`SentenceTransformer.tokenizer` 를 대신하는 페이크. 고정 토큰수를 돌려준다."""

    def __init__(self, token_count: int) -> None:
        self._token_count = token_count

    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
        return [0] * self._token_count


class _FakeEncoder:
    """SentenceTransformer 를 대신하는 페이크. 인코딩 요청 인자를 그대로 기록한다."""

    def __init__(self, dim: int = EMBEDDING_DIM, tokenizer: _FakeTokenizer | None = None) -> None:
        self._dim = dim
        self.encode_calls: list[list[str]] = []
        if tokenizer is not None:
            self.tokenizer = tokenizer

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


def test_embed_query_caches_same_query_and_skips_re_encoding() -> None:
    """같은 질의로 두 번 호출하면 인코더는 한 번만 불린다."""
    # Arrange
    encoder = _FakeEncoder()
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    # Act
    first = provider.embed_query("로그인")
    second = provider.embed_query("로그인")

    # Assert
    assert encoder.encode_calls == [["query: 로그인"]]
    assert first == second


def test_embed_query_different_queries_are_encoded_separately() -> None:
    """다른 질의는 각각 인코딩된다(캐시가 과도하게 뭉개지 않음)."""
    # Arrange
    encoder = _FakeEncoder()
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    # Act
    provider.embed_query("로그인")
    provider.embed_query("로그아웃")

    # Assert
    assert encoder.encode_calls == [["query: 로그인"], ["query: 로그아웃"]]


def test_embed_query_returns_defensive_copy_not_shared_with_cache() -> None:
    """반환된 벡터를 호출측이 변형해도 캐시된 값·다음 호출 결과에 영향 없다."""
    # Arrange
    encoder = _FakeEncoder()
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    # Act
    first = provider.embed_query("로그인")
    first[0] = 999.0
    second = provider.embed_query("로그인")

    # Assert
    assert second[0] != 999.0
    assert encoder.encode_calls == [["query: 로그인"]]


def test_embed_documents_warns_when_token_count_exceeds_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """docs/16 Phase 1: 안전 상한(480토큰) 초과 시 경고 로그가 남는다."""
    encoder = _FakeEncoder(tokenizer=_FakeTokenizer(token_count=600))
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    with caplog.at_level(logging.WARNING):
        provider.embed_documents(["긴 섹션 텍스트"], labels=["doc1:section:0"])

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "doc1:section:0" in warnings[0].message
    assert "600" in warnings[0].message


def test_embed_documents_no_warning_when_within_threshold(caplog: pytest.LogCaptureFixture) -> None:
    encoder = _FakeEncoder(tokenizer=_FakeTokenizer(token_count=100))
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    with caplog.at_level(logging.WARNING):
        provider.embed_documents(["짧은 텍스트"], labels=["doc1:section:0"])

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_embed_documents_no_warning_when_encoder_lacks_tokenizer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """토크나이저를 노출하지 않는 페이크(기존 테스트들)는 경고 없이 그대로 동작한다."""
    encoder = _FakeEncoder()  # tokenizer 속성 없음
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    with caplog.at_level(logging.WARNING):
        result = provider.embed_documents(["아무 텍스트"], labels=["doc1:section:0"])

    assert len(result) == 1
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_embed_documents_without_labels_still_warns(caplog: pytest.LogCaptureFixture) -> None:
    """labels 를 안 넘겨도(레거시 호출부) 경고 자체는 그대로 동작한다."""
    encoder = _FakeEncoder(tokenizer=_FakeTokenizer(token_count=600))
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    with caplog.at_level(logging.WARNING):
        provider.embed_documents(["긴 텍스트"])

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_count_tokens_uses_encoder_tokenizer() -> None:
    encoder = _FakeEncoder(tokenizer=_FakeTokenizer(token_count=42))
    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    assert provider.count_tokens("아무 텍스트") == 42


def test_count_tokens_returns_zero_when_encoder_lacks_tokenizer() -> None:
    """docs/23 §3: 토크나이저 없는 인코더는 죽지 않고 예산 이내(0)로 간주한다."""
    encoder = _FakeEncoder()  # tokenizer 속성 없음

    provider = LocalEmbeddingProvider("fake-model", encoder=encoder)

    assert provider.count_tokens("아무 텍스트") == 0


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
