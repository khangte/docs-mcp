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
from app.services.documents.sources.document_source import (
    FetchedDocument,
    FileListing,
    FileMeta,
)
from app.services.documents.sources.notion_blocks import (
    block_plain_text,
    child_page_to_file_meta,
    property_plain_text,
    to_file_meta,
)

_LOG = get_logger("docs_mcp.documents.notion")

NOTION_API_BASE = "https://api.notion.com/v1"
DEFAULT_NOTION_VERSION = "2022-06-28"
#: Notion search/query 한 페이지 최대 개수(API 상한 100).
PAGE_SIZE = 100
#: 블록 트리 재귀 깊이 상한(깊은 중첩으로 인한 호출 폭증 방지).
MAX_BLOCK_DEPTH = 4
#: 한 문서에서 순회할 블록 수 상한.
MAX_BLOCKS = 2000
#: 허브 페이지 하위 child_page 재귀 탐색 깊이 상한.
MAX_PAGE_DEPTH = 4
#: 한 허브에서 수집할 하위 페이지 수 상한.
MAX_PAGES = 500
#: 하위 페이지 탐색 시 toggle/column 같은 컨테이너 블록을 몇 단계까지
#: 통과할지. 페이지 중첩 깊이(MAX_PAGE_DEPTH)와 **별도로** 센다 — 같은
#: 카운터를 쓰면 토글 두 겹만으로 페이지 깊이 예산이 소진된다.
MAX_CONTAINER_DEPTH = 3
#: 자식으로 하위 페이지/하위 DB 를 품을 수 있는 컨테이너 블록 타입.
_CONTAINER_BLOCK_TYPES = frozenset(
    {
        "toggle",
        "column_list",
        "column",
        "callout",
        "synced_block",
        "bulleted_list_item",
        "numbered_list_item",
        "to_do",
        "quote",
    }
)


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

        `page_id` 가 설정돼 있으면 그 페이지 하위 트리 전체를 child_page 를
        통해 재귀 탐색해 목록화한다(허브 페이지 하위 문서 탐색).
        `database_id` 가 설정돼 있으면 DB 행을 목록화한 뒤 **각 행 하위
        트리도 같은 방식으로 재귀 탐색**한다 — 행 페이지 안의 하위 페이지·
        하위 DB 가 검색에서 빠지지 않게 한다(`docs/architect-review/50` §2.2).
        둘 다 없으면 워크스페이스 검색 결과를 그대로 쓴다(그 응답 자체가 이미
        중첩 페이지를 포함하므로 추가 재귀가 불필요하다).

        Raises:
            IntegrationError: 인증 실패·rate limit·네트워크 오류 시.
        """
        acc: list[FileMeta] = []
        visited: set[str] = set()
        with self._client() as client:
            if self._page_id:
                self._collect_child_pages(client, self._page_id, acc, visited, 0)
                return acc

            path, body = self._list_request_spec()
            raw_pages = self._paginate(client, path, body)
            if not self._database_id:
                return [to_file_meta(page) for page in raw_pages if page.get("id")]

            for page in raw_pages:
                page_id = str(page.get("id") or "")
                if not page_id or page_id in visited:
                    continue
                if not self._record_page(to_file_meta(page), page_id, acc, visited):
                    _LOG.warning("notion 하위 페이지 수 상한(%d) 도달: %s", MAX_PAGES, page_id)
                    return acc
                self._collect_child_pages(client, page_id, acc, visited, 0)
        return acc

    def list_files(self) -> FileListing:
        """`DocumentSource` Protocol 호환 어댑터. `list_pages()` 결과를 감싼다.

        Notion 은 `MAX_PAGES` 도달 시 여러 지점에서 return 하므로, 내부
        재귀 구조를 고치는 대신 여기서 결과 건수만으로 절단 여부를
        판정한다(상한에 도달한 목록은 정의상 잘린 것이다).
        """
        pages = self.list_pages()
        return FileListing(files=pages, truncated=len(pages) >= MAX_PAGES)

    def supports_text_extraction(self, mime_type: str | None) -> bool:
        """Notion 페이지는 항상 텍스트 추출이 가능하다(mime_type 이 없음)."""
        return True

    def fetch(self, external_id: str) -> FetchedDocument:
        """페이지 속성 + 본문(블록 트리)을 평문 텍스트로 반환한다.

        DB 행의 상태·태그·담당자 같은 속성은 블록이 아니라 페이지 객체에
        있어 `/blocks/{id}/children` 만으로는 절대 잡히지 않는다. 그래서
        `GET /pages/{id}` 를 1회 더 호출해 속성 줄을 본문 앞에 붙인다
        (`docs/architect-review/50` §3 P0-3).

        Args:
            external_id: Notion page ID.

        Returns:
            속성 줄 + 블록 줄을 줄바꿈으로 이어 붙인 평문(최대 문자 수로
            잘림)과 절단 여부.

        Raises:
            IntegrationError: 페이지가 없거나 외부 연동에 실패한 경우.
        """
        if not external_id:
            raise IntegrationError("notion page id must not be empty")

        lines: list[str] = []
        with self._client() as client:
            lines.extend(self._page_property_lines(client, external_id))
            self._collect_block_text(client, external_id, lines, depth=0)
        text = "\n".join(lines)
        truncated = len(text) > self._max_chars
        return FetchedDocument(text[: self._max_chars], truncated)

    def _page_property_lines(self, client: httpx.Client, page_id: str) -> list[str]:
        """페이지 속성을 `"{속성명}: {값}"` 줄 목록으로 만든다.

        조회 실패는 삼키고 빈 목록을 돌려준다 — 속성 하나 때문에 문서 1건의
        본문 색인이 통째로 실패하면 안 된다(블록 본문만으로도 색인 가치가
        있다).
        """
        try:
            page = self._request_json(client, "GET", f"/pages/{page_id}")
        except IntegrationError as exc:
            _LOG.warning(
                "notion 페이지 속성 조회 실패(본문만 색인): %s (%s)", page_id, exc
            )
            return []
        properties = page.get("properties")
        if not isinstance(properties, dict):
            return []
        lines: list[str] = []
        for name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            value = property_plain_text(prop)
            if value:
                lines.append(f"{name}: {value}")
        return lines

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
        """블록 트리를 재귀 순회하며 평문 줄을 lines 에 누적한다.

        `child_page` 에서는 재귀를 멈춘다 — 하위 페이지는 `list_pages()` 가
        독립 문서로 목록화하므로, 여기서 또 타고 들어가면 같은 텍스트가 부모·
        자식 두 문서에 중복 색인되고 부모 히트의 스니펫·URL 이 실제 출처와
        어긋난다(`docs/architect-review/50` §2.3). 제목은
        `block_plain_text` 가 남기므로 부모에서도 하위 문서 이름은 검색된다.
        """
        if depth > MAX_BLOCK_DEPTH or len(lines) >= MAX_BLOCKS:
            return
        for block in self._list_children(client, block_id):
            if len(lines) >= MAX_BLOCKS:
                _LOG.warning("notion 블록 수 상한(%d) 도달: %s", MAX_BLOCKS, block_id)
                return
            text = block_plain_text(block)
            if text:
                lines.append(text)
            if (
                block.get("has_children")
                and block.get("id")
                and block.get("type") != "child_page"
            ):
                self._collect_block_text(client, str(block["id"]), lines, depth + 1)

    def _collect_child_pages(
        self,
        client: httpx.Client,
        page_id: str,
        acc: list[FileMeta],
        visited: set[str],
        depth: int,
        container_depth: int = 0,
    ) -> None:
        """page_id 하위 child_page/child_database 트리를 재귀 순회하며 acc 에 평탄 누적한다.

        child_database 를 만나면 그 database 를 query 해 얻은 행(페이지)들도
        동일하게 재귀 대상에 포함한다. toggle/column 같은 컨테이너 블록은
        하위 페이지를 품을 수 있으므로 `MAX_CONTAINER_DEPTH` 까지 통과해
        내려간다 — 이때 페이지 중첩 깊이(`depth`)는 늘리지 않는다
        (`docs/architect-review/50` §3 P1-2).
        """
        if depth > MAX_PAGE_DEPTH:
            return
        if len(acc) >= MAX_PAGES:
            _LOG.warning("notion 하위 페이지 수 상한(%d) 도달: %s", MAX_PAGES, page_id)
            return
        for block in self._list_children(client, page_id):
            if len(acc) >= MAX_PAGES:
                _LOG.warning("notion 하위 페이지 수 상한(%d) 도달: %s", MAX_PAGES, page_id)
                return
            block_type = block.get("type")
            block_id = str(block.get("id") or "")
            if not block_id:
                continue
            if block_type == "child_page":
                if block_id in visited:
                    continue
                if not self._record_page(child_page_to_file_meta(block), block_id, acc, visited):
                    _LOG.warning("notion 하위 페이지 수 상한(%d) 도달: %s", MAX_PAGES, page_id)
                    return
                self._collect_child_pages(client, block_id, acc, visited, depth + 1)
            elif block_type == "child_database":
                for row in self._paginate(client, f"/databases/{block_id}/query", {}):
                    row_id = str(row.get("id") or "")
                    if not row_id or row_id in visited:
                        continue
                    if not self._record_page(to_file_meta(row), row_id, acc, visited):
                        _LOG.warning("notion 하위 페이지 수 상한(%d) 도달: %s", MAX_PAGES, page_id)
                        return
                    self._collect_child_pages(client, row_id, acc, visited, depth + 1)
            elif (
                block_type in _CONTAINER_BLOCK_TYPES
                and block.get("has_children")
                and container_depth < MAX_CONTAINER_DEPTH
            ):
                self._collect_child_pages(
                    client, block_id, acc, visited, depth, container_depth + 1
                )

    @staticmethod
    def _record_page(
        meta: FileMeta, item_id: str, acc: list[FileMeta], visited: set[str]
    ) -> bool:
        """방문 처리 후 acc 에 추가한다. 상한 도달 시 False(호출자는 순회 중단)."""
        visited.add(item_id)
        acc.append(meta)
        return len(acc) < MAX_PAGES

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
    if status == 400 and path.startswith("/databases/"):
        try:
            hint = response.json().get("additional_data", {}).get("child_data_source_ids")
        except (ValueError, AttributeError):
            hint = None
        _LOG.warning(
            "notion database query 400: %s child_data_source_ids=%s "
            "(multi data source 가능성, docs/architect-review/34_notion_api_version_upgrade_judgment.md 참조)",
            path,
            hint,
        )
        return f"notion database query failed for {path} (status 400): data source 가 여러 개일 수 있음(34번 문서 참조)"
    if status == 429:
        return "notion rate limit exceeded; retry later"
    return f"notion request failed for {path} (status {status})"
