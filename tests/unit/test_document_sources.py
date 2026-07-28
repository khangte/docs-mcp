"""Drive/Notion 소스 어댑터 단위 테스트 (SPEC 기능 5).

실제 자격증명이 없으므로 `httpx.MockTransport` 로 REST 응답을 흉내 내
"응답 파싱"과 "오류 → IntegrationError 변환"만 검증한다. 네트워크로 나가는
테스트는 만들지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import pytest

from app.core.errors import IntegrationError
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION
from app.services.documents.document_source import DocumentSource, FileMeta
from app.services.documents.google_drive_source import (
    FOLDER_MIME_TYPE,
    GOOGLE_DOC_MIME_TYPE,
    GoogleDriveSource,
    ServiceAccountTokenProvider,
)
from app.services.documents.notion_source import NotionSource
from app.services.documents.time_parsing import parse_rfc3339

_API_BASE = "https://api.test"


class StubTokenProvider:
    """토큰 발급을 흉내 내는 스텁(실제 서명/네트워크 없음)."""

    def __init__(self, value: str = "stub-token") -> None:
        """반환할 토큰 값을 보관한다."""
        self._value = value
        self.call_count = 0

    def token(self) -> str:
        """고정 토큰을 반환하고 호출 횟수를 기록한다."""
        self.call_count += 1
        return self._value


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    source: Any,
    handler: Any,
) -> None:
    """어댑터의 `_client()` 를 MockTransport 기반 클라이언트로 교체한다."""

    def _factory() -> httpx.Client:
        return httpx.Client(
            base_url=_API_BASE,
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer stub-token"},
        )

    monkeypatch.setattr(source, "_client", _factory)


def _json(payload: dict[str, Any]) -> httpx.Response:
    """JSON 200 응답을 만든다."""
    return httpx.Response(200, json=payload)


# --- 공통 시각 파싱 -------------------------------------------------------------


def test_parse_rfc3339_converts_zulu_to_naive_utc() -> None:
    """Z 접미사 시각을 tz-naive UTC datetime 으로 변환한다."""
    assert parse_rfc3339("2026-07-28T10:00:00.000Z") == datetime(2026, 7, 28, 10, 0, 0)


def test_parse_rfc3339_normalizes_offset() -> None:
    """오프셋이 붙은 시각도 UTC 기준으로 정규화한다."""
    assert parse_rfc3339("2026-07-28T19:00:00+09:00") == datetime(2026, 7, 28, 10, 0, 0)


@pytest.mark.parametrize("value", [None, "", "not-a-date"])
def test_parse_rfc3339_returns_none_for_unparsable(value: str | None) -> None:
    """해석할 수 없는 값은 예외 대신 None 을 돌려준다."""
    assert parse_rfc3339(value) is None


# --- Protocol 준수 --------------------------------------------------------------


def test_adapters_satisfy_document_source_protocol() -> None:
    """두 어댑터 모두 DocumentSource Protocol 을 만족한다."""
    drive = GoogleDriveSource(folder_id="f1", token_provider=StubTokenProvider())
    notion = NotionSource(token="t1")

    assert isinstance(drive, DocumentSource)
    assert isinstance(notion, DocumentSource)
    assert (drive.source_name, notion.source_name) == (SOURCE_DRIVE, SOURCE_NOTION)


# --- Drive: 설정 검증 -----------------------------------------------------------


def test_drive_without_folder_id_raises_integration_error() -> None:
    """폴더 ID 미설정은 IntegrationError 로 명확히 알린다."""
    with pytest.raises(IntegrationError, match="DOCS_MCP_DRIVE_FOLDER_ID"):
        GoogleDriveSource(folder_id="", token_provider=StubTokenProvider())


def test_token_provider_without_credentials_raises_integration_error() -> None:
    """서비스 계정 자격증명이 전혀 없으면 IntegrationError 다."""
    with pytest.raises(IntegrationError, match="credentials are not configured"):
        ServiceAccountTokenProvider()


def test_token_provider_with_invalid_json_raises_integration_error() -> None:
    """잘못된 서비스 계정 JSON 은 스택트레이스 대신 IntegrationError 다."""
    provider = ServiceAccountTokenProvider(service_account_json="{not json")

    with pytest.raises(IntegrationError, match="invalid google service account credentials"):
        provider.token()


# --- Drive: list_files ----------------------------------------------------------


def test_drive_list_files_recurses_into_subfolders(monkeypatch: pytest.MonkeyPatch) -> None:
    """지정 폴더의 하위 폴더까지 재귀 탐색하고 폴더 자체는 결과에서 제외한다."""
    pages = {
        "root": {
            "files": [
                {
                    "id": "sub",
                    "name": "하위폴더",
                    "mimeType": FOLDER_MIME_TYPE,
                },
                {
                    "id": "f1",
                    "name": "루트 문서",
                    "mimeType": GOOGLE_DOC_MIME_TYPE,
                    "webViewLink": "https://drive.test/f1",
                    "modifiedTime": "2026-07-01T00:00:00Z",
                },
            ]
        },
        "sub": {
            "files": [
                {
                    "id": "f2",
                    "name": "하위 문서",
                    "mimeType": GOOGLE_DOC_MIME_TYPE,
                    "webViewLink": "https://drive.test/f2",
                    "modifiedTime": "2026-07-02T00:00:00Z",
                }
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        folder = "sub" if "'sub' in parents" in query else "root"
        return _json(pages[folder])

    source = GoogleDriveSource(folder_id="root", token_provider=StubTokenProvider())
    _patch_client(monkeypatch, source, handler)

    files = source.list_files()

    assert [f.external_id for f in files] == ["f1", "f2"]
    assert files[0].url == "https://drive.test/f1"
    assert files[1].modified_at == datetime(2026, 7, 2, 0, 0, 0)


def test_drive_list_files_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    """nextPageToken 이 있으면 다음 페이지까지 이어서 조회한다."""
    responses = [
        {
            "files": [{"id": "f1", "name": "첫장", "mimeType": GOOGLE_DOC_MIME_TYPE}],
            "nextPageToken": "tok",
        },
        {"files": [{"id": "f2", "name": "둘째장", "mimeType": GOOGLE_DOC_MIME_TYPE}]},
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = responses[calls["n"]]
        calls["n"] += 1
        return _json(payload)

    source = GoogleDriveSource(folder_id="root", token_provider=StubTokenProvider())
    _patch_client(monkeypatch, source, handler)

    assert [f.external_id for f in source.list_files()] == ["f1", "f2"]


def test_drive_list_files_empty_folder_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 폴더는 빈 리스트를 돌려준다."""
    source = GoogleDriveSource(folder_id="root", token_provider=StubTokenProvider())
    _patch_client(monkeypatch, source, lambda request: _json({"files": []}))

    assert source.list_files() == []


# --- Drive: fetch ---------------------------------------------------------------


def test_drive_fetch_uses_export_for_google_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google 네이티브 문서는 export API 로 평문 변환해 가져온다."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/export"):
            assert request.url.params["mimeType"] == "text/plain"
            return httpx.Response(200, text="문서 평문 본문")
        return _json({"id": "f1", "mimeType": GOOGLE_DOC_MIME_TYPE})

    source = GoogleDriveSource(folder_id="root", token_provider=StubTokenProvider())
    _patch_client(monkeypatch, source, handler)

    assert source.fetch("f1") == "문서 평문 본문"
    assert seen[-1].endswith("/export")


def test_drive_fetch_uses_alt_media_for_plain_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """일반 파일은 alt=media 다운로드로 가져온다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, text="plain text body")
        return _json({"id": "f1", "mimeType": "text/plain"})

    source = GoogleDriveSource(folder_id="root", token_provider=StubTokenProvider())
    _patch_client(monkeypatch, source, handler)

    assert source.fetch("f1") == "plain text body"


def test_drive_fetch_truncates_to_max_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    """과대 응답은 설정된 최대 문자 수로 잘린다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, text="a" * 5000)
        return _json({"id": "f1", "mimeType": "text/plain"})

    source = GoogleDriveSource(
        folder_id="root", token_provider=StubTokenProvider(), max_chars=100
    )
    _patch_client(monkeypatch, source, handler)

    assert len(source.fetch("f1")) == 100


def test_drive_fetch_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    """문서가 바뀌지 않으면 반복 fetch 가 동일한 텍스트를 돌려준다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/export"):
            return httpx.Response(200, text="변하지 않는 본문")
        return _json({"id": "f1", "mimeType": GOOGLE_DOC_MIME_TYPE})

    source = GoogleDriveSource(folder_id="root", token_provider=StubTokenProvider())
    _patch_client(monkeypatch, source, handler)

    assert source.fetch("f1") == source.fetch("f1")


def test_drive_fetch_empty_id_raises_integration_error() -> None:
    """빈 external_id 는 외부 호출 없이 IntegrationError 다."""
    source = GoogleDriveSource(folder_id="root", token_provider=StubTokenProvider())

    with pytest.raises(IntegrationError, match="must not be empty"):
        source.fetch("")


# --- Drive: 오류 변환 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "access denied"),
        (403, "access denied"),
        (404, "not found"),
        (429, "rate limit"),
        (500, "request failed"),
    ],
)
def test_drive_http_errors_become_integration_error(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    """인증 실패·rate limit·미존재는 모두 IntegrationError 로 변환된다."""
    source = GoogleDriveSource(folder_id="root", token_provider=StubTokenProvider())
    _patch_client(monkeypatch, source, lambda request: httpx.Response(status))

    with pytest.raises(IntegrationError, match=expected):
        source.fetch("missing")


def test_drive_network_error_becomes_integration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """네트워크 오류도 스택트레이스 노출 없이 IntegrationError 로 변환된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    source = GoogleDriveSource(folder_id="root", token_provider=StubTokenProvider())
    _patch_client(monkeypatch, source, handler)

    with pytest.raises(IntegrationError, match="request failed"):
        source.list_files()


def test_drive_malformed_json_becomes_integration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON 이 깨져 있어도 IntegrationError 로 감싼다."""
    source = GoogleDriveSource(folder_id="root", token_provider=StubTokenProvider())
    _patch_client(monkeypatch, source, lambda request: httpx.Response(200, text="<html>"))

    with pytest.raises(IntegrationError, match="malformed json"):
        source.list_files()


# --- Notion: 설정 검증 ----------------------------------------------------------


def test_notion_without_token_raises_integration_error() -> None:
    """토큰 미설정은 IntegrationError 로 명확히 알린다."""
    with pytest.raises(IntegrationError, match="DOCS_MCP_NOTION_TOKEN"):
        NotionSource(token="")


def test_notion_sends_version_header() -> None:
    """Notion-Version 헤더가 클라이언트에 설정된다."""
    source = NotionSource(token="t1", notion_version="2022-06-28")

    client = source._client()
    try:
        assert client.headers["Notion-Version"] == "2022-06-28"
        assert client.headers["Authorization"] == "Bearer t1"
    finally:
        client.close()


# --- Notion: list_pages ---------------------------------------------------------


def test_notion_list_pages_uses_search_without_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB ID 가 없으면 /search 로 워크스페이스 페이지를 조회한다."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return _json(
            {
                "results": [
                    {
                        "id": "p1",
                        "url": "https://notion.test/p1",
                        "last_edited_time": "2026-07-03T00:00:00.000Z",
                        "properties": {
                            "Name": {
                                "type": "title",
                                "title": [{"plain_text": "회의록"}],
                            }
                        },
                    }
                ]
            }
        )

    source = NotionSource(token="t1")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert paths == ["/search"]
    assert pages == [
        FileMeta(
            external_id="p1",
            title="회의록",
            url="https://notion.test/p1",
            modified_at=datetime(2026, 7, 3, 0, 0, 0),
        )
    ]


def test_notion_list_pages_queries_database_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB ID 가 설정되면 해당 데이터베이스 하위로 범위가 한정된다."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return _json({"results": []})

    source = NotionSource(token="t1", database_id="db-1")
    _patch_client(monkeypatch, source, handler)
    source.list_pages()

    assert paths == ["/databases/db-1/query"]


def test_notion_list_pages_follows_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """has_more 이면 next_cursor 로 다음 페이지까지 조회한다."""
    responses = [
        {"results": [{"id": "p1", "properties": {}}], "has_more": True, "next_cursor": "c1"},
        {"results": [{"id": "p2", "properties": {}}], "has_more": False},
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = responses[calls["n"]]
        calls["n"] += 1
        return _json(payload)

    source = NotionSource(token="t1")
    _patch_client(monkeypatch, source, handler)

    assert [p.external_id for p in source.list_pages()] == ["p1", "p2"]


def test_notion_page_without_title_uses_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """title 속성이 없는 페이지는 자리표시자 제목을 쓴다."""
    source = NotionSource(token="t1")
    _patch_client(
        monkeypatch,
        source,
        lambda request: _json({"results": [{"id": "p1", "properties": {}}]}),
    )

    assert source.list_pages()[0].title == "(제목 없음)"


def test_notion_list_files_is_alias_of_list_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Protocol 호환용 list_files() 가 list_pages() 와 같은 결과를 준다."""
    source = NotionSource(token="t1")
    _patch_client(
        monkeypatch,
        source,
        lambda request: _json({"results": [{"id": "p1", "properties": {}}]}),
    )

    assert source.list_files() == source.list_pages()


# --- Notion: fetch --------------------------------------------------------------


def test_notion_fetch_flattens_block_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """블록 트리를 재귀 순회해 rich_text 를 평문으로 이어 붙인다."""
    children = {
        "p1": [
            {
                "id": "b1",
                "type": "heading_1",
                "has_children": False,
                "heading_1": {"rich_text": [{"plain_text": "제목"}]},
            },
            {
                "id": "b2",
                "type": "paragraph",
                "has_children": True,
                "paragraph": {"rich_text": [{"plain_text": "부모 문단"}]},
            },
        ],
        "b2": [
            {
                "id": "b3",
                "type": "bulleted_list_item",
                "has_children": False,
                "bulleted_list_item": {"rich_text": [{"plain_text": "자식 항목"}]},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": children.get(block_id, [])})

    source = NotionSource(token="t1")
    _patch_client(monkeypatch, source, handler)

    assert source.fetch("p1") == "제목\n부모 문단\n자식 항목"


def test_notion_fetch_skips_blocks_without_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """rich_text 가 없는 블록(구분선 등)은 빈 줄로 끼어들지 않는다."""
    blocks = [
        {"id": "b1", "type": "divider", "has_children": False, "divider": {}},
        {
            "id": "b2",
            "type": "paragraph",
            "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "본문"}]},
        },
    ]
    source = NotionSource(token="t1")
    _patch_client(monkeypatch, source, lambda request: _json({"results": blocks}))

    assert source.fetch("p1") == "본문"


def test_notion_fetch_empty_page_returns_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """블록이 하나도 없는 페이지는 빈 문자열을 돌려준다."""
    source = NotionSource(token="t1")
    _patch_client(monkeypatch, source, lambda request: _json({"results": []}))

    assert source.fetch("p1") == ""


def test_notion_fetch_empty_id_raises_integration_error() -> None:
    """빈 page id 는 외부 호출 없이 IntegrationError 다."""
    with pytest.raises(IntegrationError, match="must not be empty"):
        NotionSource(token="t1").fetch("")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "access denied"),
        (404, "not found"),
        (429, "rate limit"),
        (500, "request failed"),
    ],
)
def test_notion_http_errors_become_integration_error(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    """Notion 오류 응답도 IntegrationError 로 통일 변환된다."""
    source = NotionSource(token="t1")
    _patch_client(monkeypatch, source, lambda request: httpx.Response(status))

    with pytest.raises(IntegrationError, match=expected):
        source.fetch("missing")
