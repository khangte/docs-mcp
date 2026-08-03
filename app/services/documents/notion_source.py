"""Notion 문서 소스 어댑터 (SPEC 기능 5).

Notion Integration Token 하나를 팀 공유로 사용하고, REST API
(`https://api.notion.com/v1`)를 httpx 로 직접 호출한다. `Notion-Version` 헤더가
필수다. 검색 범위는 데이터베이스 ID 가 설정돼 있으면 해당 DB 하위로,
없으면 통합이 접근 가능한 워크스페이스 전체 페이지로 한정된다.

본문은 블록 트리를 재귀 순회해 rich_text 를 평문으로 이어 붙인다.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.models.document_meta import SOURCE_NOTION
from app.services.documents.document_source import FileMeta
from app.services.documents.time_parsing import parse_rfc3339

_LOG = get_logger("docs_mcp.documents.notion")

NOTION_API_BASE = "https://api.notion.com/v1"
DEFAULT_NOTION_VERSION = "2022-06-28"
#: Notion search/query 한 페이지 최대 개수(API 상한 100).
PAGE_SIZE = 100
#: 블록 트리 재귀 깊이 상한(깊은 중첩으로 인한 호출 폭증 방지).
MAX_BLOCK_DEPTH = 4
#: 한 문서에서 순회할 블록 수 상한.
MAX_BLOCKS = 2000
UNTITLED = "(제목 없음)"


class NotionSource:
    """Notion 워크스페이스/데이터베이스를 대상으로 목록/본문을 조회하는 어댑터."""

    def __init__(
        self,
        token: str,
        database_id: str | None = None,
        page_id: str | None = None,
        notion_version: str = DEFAULT_NOTION_VERSION,
        timeout_seconds: float = 15.0,
        max_chars: int = 200_000,
        api_base: str = NOTION_API_BASE,
    ) -> None:
        """토큰·검색 범위·HTTP 옵션을 보관한다.

        Args:
            token: Notion Integration Token.
            database_id: 지정 시 해당 데이터베이스 하위 페이지로 범위를 한정한다.
                `page_id` 와 상호배타.
            page_id: 지정 시 해당 페이지 바로 아래 child_page 블록들을
                목록화 대상으로 삼는다(허브 페이지 하위 문서 탐색용).
                `database_id` 와 상호배타.
            notion_version: `Notion-Version` 헤더 값.
            timeout_seconds: HTTP 타임아웃.
            max_chars: 본문 fetch 시 잘라낼 최대 문자 수.
            api_base: Notion REST API 베이스 URL(테스트에서 교체 가능).

        Raises:
            IntegrationError: 토큰이 비어 있는 경우.
        """
        if not token:
            raise IntegrationError(
                "notion token is not configured: set DOCS_MCP_NOTION_TOKEN"
            )
        self._token = token
        self._database_id = database_id
        self._page_id = page_id
        self._notion_version = notion_version
        self._timeout = timeout_seconds
        self._max_chars = max_chars
        self._api_base = api_base.rstrip("/")

    @property
    def source_name(self) -> str:
        """`document_meta.source` 에 기록할 소스 식별자."""
        return SOURCE_NOTION

    def list_pages(self) -> list[FileMeta]:
        """설정된 범위 안의 Notion 페이지 메타데이터를 반환한다.

        `page_id` 가 설정돼 있으면 그 페이지 바로 아래(1단계, 재귀 없음)
        child_page 블록만 목록화한다(허브 페이지 하위 문서 탐색). 그 외에는
        기존처럼 데이터베이스 쿼리 또는 워크스페이스 검색을 사용한다.

        Raises:
            IntegrationError: 인증 실패·rate limit·네트워크 오류 시.
        """
        if self._page_id:
            with self._client() as client:
                return self._list_child_pages(client, self._page_id)
        path, body = self._list_request_spec()
        with self._client() as client:
            raw_pages = self._paginate(client, path, body)
        return [_to_file_meta(page) for page in raw_pages if page.get("id")]

    def list_files(self) -> list[FileMeta]:
        """`DocumentSource` Protocol 호환 별칭. `list_pages()` 와 동일하다."""
        return self.list_pages()

    def fetch(self, external_id: str) -> str:
        """페이지 본문(블록 트리)을 평문 텍스트로 반환한다.

        Args:
            external_id: Notion page ID.

        Returns:
            블록 순서대로 줄바꿈으로 이어 붙인 평문(최대 문자 수로 잘림).

        Raises:
            IntegrationError: 페이지가 없거나 외부 연동에 실패한 경우.
        """
        if not external_id:
            raise IntegrationError("notion page id must not be empty")

        lines: list[str] = []
        with self._client() as client:
            self._collect_block_text(client, external_id, lines, depth=0)
        return "\n".join(lines)[: self._max_chars]

    # --- 내부 헬퍼 --------------------------------------------------------

    def _list_request_spec(self) -> tuple[str, dict[str, Any]]:
        """검색 범위에 따라 호출할 엔드포인트와 요청 본문을 결정한다."""
        if self._database_id:
            return f"/databases/{self._database_id}/query", {}
        return "/search", {"filter": {"property": "object", "value": "page"}}

    def _client(self) -> httpx.Client:
        """인증/버전 헤더가 붙은 httpx 클라이언트를 만든다(사용 후 반드시 close)."""
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": self._notion_version,
            "Content-Type": "application/json",
        }
        return httpx.Client(base_url=self._api_base, headers=headers, timeout=self._timeout)

    def _paginate(
        self, client: httpx.Client, path: str, body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """커서 기반 페이지네이션을 끝까지 따라가며 results 를 모은다."""
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload = dict(body)
            payload["page_size"] = PAGE_SIZE
            if cursor:
                payload["start_cursor"] = cursor
            data = self._request_json(client, "POST", path, json_body=payload)
            results.extend(data.get("results") or [])
            if not data.get("has_more"):
                return results
            cursor = data.get("next_cursor")
            if not cursor:
                return results

    def _collect_block_text(
        self, client: httpx.Client, block_id: str, lines: list[str], depth: int
    ) -> None:
        """블록 트리를 재귀 순회하며 평문 줄을 lines 에 누적한다."""
        if depth > MAX_BLOCK_DEPTH or len(lines) >= MAX_BLOCKS:
            return
        for block in self._list_children(client, block_id):
            if len(lines) >= MAX_BLOCKS:
                _LOG.warning("notion 블록 수 상한(%d) 도달: %s", MAX_BLOCKS, block_id)
                return
            text = _block_plain_text(block)
            if text:
                lines.append(text)
            if block.get("has_children") and block.get("id"):
                self._collect_block_text(client, str(block["id"]), lines, depth + 1)

    def _list_child_pages(self, client: httpx.Client, page_id: str) -> list[FileMeta]:
        """page_id 바로 아래(1단계) child_page 블록만 FileMeta 목록으로 변환한다.

        재귀하지 않는다 — child_page 안에 또 child_page 가 있어도 이 목록에는
        포함되지 않는다(깊은 순회는 후속 스코프).
        """
        child_pages: list[FileMeta] = []
        for block in self._list_children(client, page_id):
            if block.get("type") != "child_page" or not block.get("id"):
                continue
            child_pages.append(_child_page_to_file_meta(block))
        return child_pages

    def _list_children(self, client: httpx.Client, block_id: str) -> list[dict[str, Any]]:
        """블록 자식 목록을 페이지네이션까지 처리해 반환한다."""
        children: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": PAGE_SIZE}
            if cursor:
                params["start_cursor"] = cursor
            data = self._request_json(
                client, "GET", f"/blocks/{block_id}/children", params=params
            )
            children.extend(data.get("results") or [])
            if not data.get("has_more"):
                return children
            cursor = data.get("next_cursor")
            if not cursor:
                return children

    def _request_json(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Notion API 를 호출하고 실패를 IntegrationError 로 통일 변환한다."""
        try:
            response = client.request(method, path, params=params, json=json_body)
            response.raise_for_status()
            return dict(response.json())
        except httpx.HTTPStatusError as exc:
            raise IntegrationError(_notion_error_message(path, exc.response)) from exc
        except httpx.HTTPError as exc:
            raise IntegrationError(f"notion request failed for {path}: {exc}") from exc
        except ValueError as exc:
            raise IntegrationError(f"notion returned malformed json for {path}") from exc


def _notion_error_message(path: str, response: httpx.Response) -> str:
    """Notion API 오류 상태코드를 사용자에게 보여줄 메시지로 바꾼다."""
    status = response.status_code
    if status in (401, 403):
        return (
            f"notion access denied for {path} (status {status}): "
            "check the integration token and page sharing"
        )
    if status == 404:
        return f"notion document not found: {path}"
    if status == 429:
        return "notion rate limit exceeded; retry later"
    return f"notion request failed for {path} (status {status})"


def _rich_text_to_plain(items: Any) -> str:
    """Notion rich_text 배열을 평문으로 이어 붙인다."""
    if not isinstance(items, list):
        return ""
    parts = [
        str(item.get("plain_text") or "")
        for item in items
        if isinstance(item, dict)
    ]
    return "".join(parts).strip()


def _block_plain_text(block: dict[str, Any]) -> str:
    """블록 한 개에서 평문 텍스트를 추출한다(rich_text 를 갖는 모든 타입 지원)."""
    block_type = str(block.get("type") or "")
    payload = block.get(block_type)
    if not isinstance(payload, dict):
        return ""
    return _rich_text_to_plain(payload.get("rich_text"))


def _page_title(page: dict[str, Any]) -> str:
    """페이지 properties 에서 title 타입 속성을 찾아 제목을 만든다."""
    properties = page.get("properties")
    if isinstance(properties, dict):
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                title = _rich_text_to_plain(prop.get("title"))
                if title:
                    return title
    return UNTITLED


def _child_page_to_file_meta(block: dict[str, Any]) -> FileMeta:
    """`child_page` 타입 블록 하나를 FileMeta 로 변환한다.

    블록 자체의 id 가 하위 페이지의 page id 다(그대로 fetch 대상 external_id).
    """
    block_id = str(block.get("id") or "")
    child_page = block.get("child_page")
    title = UNTITLED
    if isinstance(child_page, dict):
        title = str(child_page.get("title") or "") or UNTITLED
    return FileMeta(
        external_id=block_id,
        title=title,
        url=f"https://www.notion.so/{block_id.replace('-', '')}",
        modified_at=parse_rfc3339(block.get("last_edited_time")),
    )


def _to_file_meta(page: dict[str, Any]) -> FileMeta:
    """Notion 페이지 응답 항목 하나를 FileMeta 로 변환한다."""
    page_id = str(page.get("id") or "")
    return FileMeta(
        external_id=page_id,
        title=_page_title(page),
        url=str(page.get("url") or f"https://www.notion.so/{page_id.replace('-', '')}"),
        modified_at=parse_rfc3339(page.get("last_edited_time")),
    )
