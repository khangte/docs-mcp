"""LLM 기반 검색 질의 확장(SPEC 기능 7 보강).

`document_search_service.search()` 의 1단계 SQL 후보 필터(`search_by_tokens`)는
제목/URL 문자열에 대한 순수 토큰 매칭이라, 질의와 문서의 실제 표현이 다르면
("주문조회 API" vs "결제 내역 조회") 후보가 0건이 되어 2단계(본문 fetch)까지
가지 못한다. `QueryExpander` 는 원본 질의를 LLM 으로 확장한 동의어/유사
토큰들을 돌려줘, 이 SQL 필터의 후보 게이트를 넓히는 용도로만 쓰인다.

점수 계산(`_title_score`/`_body_score`)에는 절대 섞이지 않는다 — 순위는
여전히 원본 질의 토큰만으로 결정된다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from google import genai

from app.core.logging import get_logger

_LOG = get_logger("docs_mcp.documents.query_expander")

#: 확장 토큰 개수 상한. 과도한 확장이 SQL 후보를 지나치게 넓히지 않도록 제한.
MAX_EXPANDED_TOKENS = 8


class QueryExpander(Protocol):
    """질의 → 확장 토큰 리스트 변환 계약."""

    def expand(self, query: str) -> list[str]:
        """질의와 의미가 유사한 추가 검색 토큰들을 반환한다. 실패 시 빈 리스트."""
        ...


class GeminiQueryExpander:
    """Gemini 텍스트 생성 API로 질의를 확장하는 구현.

    호출 실패(네트워크 오류, 응답 파싱 실패 등)는 여기서 삼키고 빈 리스트를
    반환한다 — 질의확장은 검색 후보를 "넓히는" 보조 수단일 뿐이므로, 이
    호출이 실패해도 검색 자체(원본 토큰 매칭)는 계속 동작해야 한다.
    """

    def __init__(self, api_key: str, model: str, client: genai.Client | None = None) -> None:
        """API 키/모델명을 보관하고, 주입되지 않으면 genai.Client 를 직접 생성한다."""
        self._model = model
        self._client = client if client is not None else genai.Client(api_key=api_key)
        self._cached_expand = lru_cache(maxsize=256)(self._expand_uncached)

    def expand(self, query: str) -> list[str]:
        """같은 질의 반복 호출 시 LLM 재호출을 막기 위해 결과를 캐싱한다."""
        return self._cached_expand(query)

    def _expand_uncached(self, query: str) -> list[str]:
        """Gemini 를 호출해 질의와 유사한 토큰들을 뽑아낸다. 실패하면 빈 리스트."""
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=(
                    "다음 검색어와 같은 의미로 문서에 쓰일 법한 동의어/유사 표현을 "
                    f"한국어 단어 단위로 최대 {MAX_EXPANDED_TOKENS}개, 쉼표로만 구분해 나열해줘. "
                    "설명 없이 단어만 출력해.\n"
                    f"검색어: {query}"
                ),
            )
            text = (response.text or "").strip()
            if not text:
                return []
            tokens = [t.strip() for t in text.split(",") if t.strip()]
            return tokens[:MAX_EXPANDED_TOKENS]
        except Exception as exc:  # noqa: BLE001 - 질의확장 실패는 검색을 막지 않는다.
            _LOG.warning("LLM 질의확장 실패(원본 토큰만으로 폴백): query=%s (%s)", query, exc)
            return []
