"""OpenAPI 원문 수집기.

Protocol + HTTP 구현 + (테스트용) 파일/메모리 구현.
HTTP 는 `httpx` 로 외부 호출. 테스트에서는 주입형 페이크 사용.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from src.core.errors import IntegrationError


class OpenAPIFetcher(Protocol):
    """OpenAPI 문서 원문을 문자열로 가져온다."""

    def fetch(self, source_url: str) -> str: ...


class HttpOpenAPIFetcher:
    """실제 HTTP 로 원문을 받아오는 구현."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self._timeout = timeout_seconds

    def fetch(self, source_url: str) -> str:
        try:
            response = httpx.get(source_url, timeout=self._timeout)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as exc:
            raise IntegrationError(f"failed to fetch {source_url}: {exc}") from exc


class InMemoryFetcher:
    """테스트용: url → 원문 딕셔너리."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self._mapping: dict[str, str] = dict(mapping or {})

    def put(self, url: str, content: str) -> None:
        self._mapping[url] = content

    def fetch(self, source_url: str) -> str:
        if source_url not in self._mapping:
            raise IntegrationError(f"url not registered in in-memory fetcher: {source_url}")
        return self._mapping[source_url]
