"""OpenAPI 원문 수집기.

Protocol + HTTP 구현 + (테스트용) 파일/메모리 구현.
HTTP 는 `httpx` 로 외부 호출. 테스트에서는 주입형 페이크 사용.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.core.errors import IntegrationError


class OpenAPIFetcher(Protocol):
    """OpenAPI 문서 원문을 문자열로 가져온다."""

    def fetch(self, source_url: str) -> str:
        """source_url 의 OpenAPI 원문을 문자열로 반환한다."""
        ...


class HttpOpenAPIFetcher:
    """실제 HTTP 로 원문을 받아오는 구현."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        """HTTP 타임아웃을 보관한다."""
        self._timeout = timeout_seconds

    def fetch(self, source_url: str) -> str:
        """httpx 로 GET 요청해 응답 본문을 반환하고 실패 시 IntegrationError 로 변환한다."""
        try:
            response = httpx.get(source_url, timeout=self._timeout)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as exc:
            raise IntegrationError(f"failed to fetch {source_url}: {exc}") from exc


class InMemoryFetcher:
    """테스트용: url → 원문 딕셔너리."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        """URL→원문 매핑을 보관한다."""
        self._mapping: dict[str, str] = dict(mapping or {})

    def put(self, url: str, content: str) -> None:
        """URL 에 대응하는 원문을 매핑에 등록한다."""
        self._mapping[url] = content

    def remove(self, url: str) -> None:
        """매핑에서 URL 을 제거해 이후 fetch 가 실패하도록 만든다(테스트용)."""
        self._mapping.pop(url, None)

    def fetch(self, source_url: str) -> str:
        """매핑된 원문을 반환하고, 없으면 IntegrationError 를 발생시킨다."""
        if source_url not in self._mapping:
            raise IntegrationError(f"url not registered in in-memory fetcher: {source_url}")
        return self._mapping[source_url]
