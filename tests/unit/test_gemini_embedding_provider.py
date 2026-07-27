"""GeminiEmbeddingProvider 테스트 (실제 네트워크 호출 없이 클라이언트를 모킹)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pytest
from google.genai import errors as genai_errors

from app.core.errors import IntegrationError
from app.services.indexer.embedding_provider import GeminiEmbeddingProvider


@dataclass
class _FakeEmbedding:
    """genai 임베딩 객체를 흉내내는 더미."""

    values: list[float]


@dataclass
class _FakeEmbedResponse:
    """genai embed_content 응답 객체를 흉내내는 더미."""

    embeddings: list[_FakeEmbedding] = field(default_factory=list)


class _FakeModels:
    """client.models.embed_content 호출을 기록/제어하는 더미."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def embed_content(self, **kwargs: Any) -> _FakeEmbedResponse:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeClient:
    """genai.Client 를 흉내내는 더미 (models 속성만 필요)."""

    def __init__(self, result: Any) -> None:
        self.models = _FakeModels(result)


def _make_api_error(code: int) -> genai_errors.APIError:
    """지정된 HTTP status code 를 갖는 APIError 를 만든다."""
    return genai_errors.APIError(code=code, response_json={"message": "boom"})


def _make_response(vectors: list[list[float]]) -> _FakeEmbedResponse:
    """지정된 벡터들을 담은 fake embed_content 응답을 만든다."""
    return _FakeEmbedResponse(embeddings=[_FakeEmbedding(values=v) for v in vectors])


def test_empty_texts_returns_empty_list_without_api_call() -> None:
    # Arrange
    client = _FakeClient(_make_response([[1.0, 0.0]]))
    provider = GeminiEmbeddingProvider(
        api_key="dummy", model="gemini-embedding-001", dim=2, client=client
    )

    # Act
    result = provider.embed([])

    # Assert
    assert result == []
    assert client.models.calls == []


def test_dim_property_returns_configured_dimension() -> None:
    # Arrange
    client = _FakeClient(_make_response([[1.0, 0.0, 0.0]]))
    provider = GeminiEmbeddingProvider(
        api_key="dummy", model="gemini-embedding-001", dim=3, client=client
    )

    # Assert
    assert provider.dim == 3


def test_normal_response_returns_vector_per_text_with_configured_dim() -> None:
    # Arrange
    client = _FakeClient(
        _make_response([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    )
    provider = GeminiEmbeddingProvider(
        api_key="dummy", model="gemini-embedding-001", dim=3, client=client
    )
    texts = ["첫 번째 문서", "두 번째 문서", "세 번째 문서"]

    # Act
    vectors = provider.embed(texts)

    # Assert
    assert len(vectors) == len(texts)
    for vector in vectors:
        assert len(vector) == 3
    assert len(client.models.calls) == 1
    assert client.models.calls[0]["contents"] == texts
    assert client.models.calls[0]["model"] == "gemini-embedding-001"
    assert client.models.calls[0]["config"].output_dimensionality == 3


def test_api_failure_raises_integration_error() -> None:
    # Arrange: 재시도 불가능한 오류(400)는 즉시 IntegrationError 로 변환된다
    client = _FakeClient(_make_api_error(code=400))
    provider = GeminiEmbeddingProvider(
        api_key="dummy", model="gemini-embedding-001", dim=3, client=client
    )

    # Act / Assert
    with pytest.raises(IntegrationError):
        provider.embed(["문서"])
    assert len(client.models.calls) == 1


def test_rate_limited_error_retries_then_raises_integration_error(monkeypatch) -> None:
    # Arrange: 429 는 재시도 대상이지만, 계속 실패하면 결국 IntegrationError 로 변환된다
    monkeypatch.setattr(
        "app.services.indexer.embedding_provider.time.sleep", lambda _seconds: None
    )
    client = _FakeClient(_make_api_error(code=429))
    provider = GeminiEmbeddingProvider(
        api_key="dummy", model="gemini-embedding-001", dim=3, client=client
    )

    # Act / Assert
    with pytest.raises(IntegrationError):
        provider.embed(["문서"])
    assert len(client.models.calls) == 3  # _MAX_ATTEMPTS 만큼 재시도


def test_rate_limited_error_succeeds_after_retry(monkeypatch) -> None:
    # Arrange: 첫 호출은 503, 두 번째 호출은 성공
    monkeypatch.setattr(
        "app.services.indexer.embedding_provider.time.sleep", lambda _seconds: None
    )
    responses = iter([_make_api_error(code=503), _make_response([[1.0, 0.0, 0.0]])])

    class _FlakyModels(_FakeModels):
        def embed_content(self, **kwargs: Any) -> _FakeEmbedResponse:
            self.calls.append(kwargs)
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result

    client = _FakeClient(None)
    client.models = _FlakyModels(None)
    provider = GeminiEmbeddingProvider(
        api_key="dummy", model="gemini-embedding-001", dim=3, client=client
    )

    # Act
    vectors = provider.embed(["문서"])

    # Assert
    assert len(vectors) == 1
    assert len(client.models.calls) == 2


def test_mismatched_embedding_count_raises_integration_error() -> None:
    # Arrange: 응답 개수가 입력 텍스트 개수와 다르면 방어적으로 오류 처리한다
    client = _FakeClient(_make_response([[1.0, 0.0]]))
    provider = GeminiEmbeddingProvider(
        api_key="dummy", model="gemini-embedding-001", dim=2, client=client
    )

    # Act / Assert
    with pytest.raises(IntegrationError):
        provider.embed(["문서 하나", "문서 둘"])


def test_returned_vectors_are_l2_normalized() -> None:
    # Arrange: output_dimensionality 지정 시 비정규화될 수 있으므로 항상 정규화되어야 한다
    client = _FakeClient(_make_response([[3.0, 4.0, 0.0]]))
    provider = GeminiEmbeddingProvider(
        api_key="dummy", model="gemini-embedding-001", dim=3, client=client
    )

    # Act
    vectors = provider.embed(["문서"])

    # Assert
    norm = math.sqrt(sum(v * v for v in vectors[0]))
    assert norm == pytest.approx(1.0, abs=1e-9)
