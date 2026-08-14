"""테스트용 페이크 구현.

호출 여부/횟수를 검증해야 하는 의존성(임베딩 프로바이더, 예시 생성 서비스)을
얇게 감싸 호출 카운트를 노출한다.
"""

from __future__ import annotations

from app.services.indexer.embedding_provider import EmbeddingProvider
from app.services.search.vector_search import VectorSearchHit


class CountingEmbeddingProvider:
    """임베딩 호출 횟수를 세는 페이크 프로바이더.

    실제 벡터 생성은 위임 대상 프로바이더에 맡기고, `embed_documents`/
    `embed_query` 가 합쳐서 몇 번 불렸는지만 추가로 기록한다. "키워드로
    찾아지면 임베딩 API 를 호출하지 않는다"는 SPEC 검증 기준을 카운트로
    확인하기 위한 도구다.
    """

    def __init__(self, delegate: EmbeddingProvider) -> None:
        """위임 프로바이더를 보관하고 호출 카운터를 0 으로 초기화한다."""
        self._delegate = delegate
        self.embed_call_count = 0
        self.embedded_texts: list[list[str]] = []

    @property
    def dim(self) -> int:
        """위임 프로바이더의 임베딩 차원 수를 그대로 반환한다."""
        return self._delegate.dim

    @property
    def is_semantic(self) -> bool:
        """위임 프로바이더의 is_semantic 값을 그대로 반환한다."""
        return self._delegate.is_semantic

    def embed_documents(
        self, texts: list[str], labels: list[str] | None = None
    ) -> list[list[float]]:
        """호출 횟수와 입력 텍스트를 기록한 뒤 위임 프로바이더로 문서를 임베딩한다."""
        self.embed_call_count += 1
        self.embedded_texts.append(list(texts))
        return self._delegate.embed_documents(texts, labels=labels)

    def embed_query(self, text: str) -> list[float]:
        """호출 횟수와 입력 텍스트를 기록한 뒤 위임 프로바이더로 질의를 임베딩한다."""
        self.embed_call_count += 1
        self.embedded_texts.append([text])
        return self._delegate.embed_query(text)

    def reset_counts(self) -> None:
        """호출 카운터와 기록을 초기화한다(색인 단계 호출을 제외하고 셀 때 사용)."""
        self.embed_call_count = 0
        self.embedded_texts = []


class ExplodingEmbeddingProvider:
    """호출되면 즉시 실패하는 페이크 프로바이더.

    "이 경로에서는 임베딩이 절대 호출되면 안 된다"를 카운트가 아니라
    예외로 단언하고 싶을 때 쓴다.
    """

    def __init__(self, dim: int = 256) -> None:
        """임베딩 차원만 보관한다."""
        self._dim = dim

    @property
    def dim(self) -> int:
        """임베딩 차원 수를 반환한다."""
        return self._dim

    @property
    def is_semantic(self) -> bool:
        """호출되지 않아야 하는 경로용 페이크라 값 자체는 의미 없다."""
        return False

    def embed_documents(
        self, texts: list[str], labels: list[str] | None = None
    ) -> list[list[float]]:
        """호출되면 AssertionError 를 발생시킨다."""
        raise AssertionError(
            f"임베딩 프로바이더가 호출되면 안 되는 경로에서 호출됨 (texts={len(texts)}건)"
        )

    def embed_query(self, text: str) -> list[float]:
        """호출되면 AssertionError 를 발생시킨다."""
        raise AssertionError("임베딩 프로바이더가 호출되면 안 되는 경로에서 호출됨(질의)")


class StubVectorSearch:
    """고정된 점수를 내는 페이크 벡터 검색기.

    `HashEmbeddingProvider` 는 서로 다른 텍스트의 코사인 유사도가 정확히 0.0 이라
    실제 벡터 보조 분기를 재현할 수 없다(점수 0 후보가 전량 폐기되어 결과가 항상
    빈 리스트가 된다). 이 페이크로 양수 점수를 강제해 벡터 분기를 실증한다.
    """

    def __init__(self, chunks: list[tuple[str, str]], score: float = 0.9) -> None:
        """반환할 (청크 ID, ref_id) 목록과 고정 점수를 보관하고 호출 카운터를 초기화한다."""
        self._chunks = list(chunks)
        self._score = score
        self.call_count = 0
        #: 마지막 호출에 전달된 candidates(Q2: 전역 스코프면 None 이어야 한다).
        self.last_candidates: set[str] | None = None

    def search(
        self,
        query: str,
        top_k: int,
        candidates: set[str] | None = None,
    ) -> list[VectorSearchHit]:
        """보관한 (청크 ID, ref_id) 를 고정 점수로 top_k 만큼 반환한다."""
        self.call_count += 1
        self.last_candidates = candidates
        allowed = [
            (chunk_id, ref_id)
            for chunk_id, ref_id in self._chunks
            if candidates is None or chunk_id in candidates
        ]
        return [
            VectorSearchHit(chunk_id=chunk_id, ref_id=ref_id, score=self._score)
            for chunk_id, ref_id in allowed[:top_k]
        ]


