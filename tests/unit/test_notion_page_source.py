"""Notion 페이지 소스 지원 테스트.

child_page 목록화(NotionSource.list_pages page 분기), register_notion_page
upsert/kind 저장, resolver 의 kind 인지 빌더 호출, 기존 database 매핑 회귀를
검증한다.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.errors import ValidationError
from app.models import DEFAULT_PROJECT
from app.repositories.project_source_repository import ProjectSourceRepository
from app.services.documents.sources.notion_source import NotionSource
from app.services.documents.project_source_service import ProjectSourceService

_API_BASE = "https://api.test"


def _patch_client(monkeypatch: pytest.MonkeyPatch, source: Any, handler: Any) -> None:
    """어댑터의 `_client()` 를 MockTransport 기반 클라이언트로 교체한다."""

    def _factory() -> httpx.Client:
        return httpx.Client(
            base_url=_API_BASE,
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer stub-token"},
        )

    monkeypatch.setattr(source, "_client", _factory)


def _json(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


# --- NotionSource: page 분기 child_page 목록화 -----------------------------------


def test_list_pages_with_page_id_lists_only_child_page_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """page_id 가 설정되면 최상위 자식 블록 중 child_page 타입만 목록화한다."""
    blocks = [
        {
            "id": "child-1",
            "type": "child_page",
            "has_children": False,
            "child_page": {"title": "하위 문서 1"},
            "last_edited_time": "2026-07-03T00:00:00.000Z",
        },
        {
            "id": "para-1",
            "type": "paragraph",
            "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "그냥 문단"}]},
        },
        {
            "id": "child-2",
            "type": "child_page",
            "has_children": False,
            "child_page": {"title": "하위 문서 2"},
            "last_edited_time": "2026-07-04T00:00:00.000Z",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return _json({"results": blocks})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["child-1", "child-2"]
    assert [p.title for p in pages] == ["하위 문서 1", "하위 문서 2"]


def test_list_pages_with_page_id_recurses_into_nested_child_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3~4 단계로 중첩된 child_page 트리 전체가 평탄 목록으로 수집된다."""
    tree = {
        "hub-page": [
            {
                "id": "child-1",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "1단계"},
            },
        ],
        "child-1": [
            {
                "id": "child-2",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "2단계"},
            },
        ],
        "child-2": [
            {
                "id": "child-3",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "3단계"},
            },
        ],
        "child-3": [
            {
                "id": "child-4",
                "type": "child_page",
                "has_children": False,
                "child_page": {"title": "4단계"},
            },
        ],
        "child-4": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["child-1", "child-2", "child-3", "child-4"]


def test_list_pages_with_page_id_truncates_branch_beyond_max_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAX_PAGE_DEPTH 를 넘는 가지는 잘리고, 상한 내 페이지는 유지된다."""
    from app.services.documents.sources import notion_source as notion_source_module

    monkeypatch.setattr(notion_source_module, "MAX_PAGE_DEPTH", 1)

    tree = {
        "hub-page": [
            {
                "id": "child-1",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "depth0"},
            },
        ],
        "child-1": [
            {
                "id": "child-2",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "depth1"},
            },
        ],
        "child-2": [
            {
                "id": "child-3",
                "type": "child_page",
                "has_children": False,
                "child_page": {"title": "depth2 - 잘려야 함"},
            },
        ],
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        calls.append(block_id)
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["child-1", "child-2"]
    assert "child-3" not in calls


def test_list_pages_with_page_id_stops_at_max_pages_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MAX_PAGES 도달 시 예외 없이 부분 결과를 반환하고 warning 을 남긴다."""
    from app.services.documents.sources import notion_source as notion_source_module

    monkeypatch.setattr(notion_source_module, "MAX_PAGES", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        child_id = f"{block_id}-c"
        return _json(
            {
                "results": [
                    {
                        "id": child_id,
                        "type": "child_page",
                        "has_children": True,
                        "child_page": {"title": child_id},
                    }
                ]
            }
        )

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    with caplog.at_level("WARNING"):
        pages = source.list_pages()

    assert len(pages) == 2
    assert any("상한" in record.message for record in caplog.records)


def test_list_pages_with_page_id_handles_cycle_without_infinite_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """순환(A→B→A)이 있어도 무한 루프 없이 A, B 각각 한 번만 수집된다."""
    tree = {
        "hub-page": [
            {
                "id": "page-a",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "A"},
            },
        ],
        "page-a": [
            {
                "id": "page-b",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "B"},
            },
        ],
        "page-b": [
            {
                "id": "page-a",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "A"},
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["page-a", "page-b"]


def test_list_pages_with_page_id_untitled_child_page_uses_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """title 이 없는 child_page 는 자리표시자 제목을 쓴다."""
    blocks = [{"id": "child-1", "type": "child_page", "has_children": False, "child_page": {}}]
    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, lambda request: _json({"results": blocks}))

    pages = source.list_pages()

    assert pages[0].title == "(제목 없음)"


def test_fetch_is_unchanged_for_child_page_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch() 는 무변경 — child_page 의 external_id 를 그대로 블록 트리 순회한다."""
    children = {
        "child-1": [
            {
                "id": "b1",
                "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": "하위 문서 본문"}]},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": children.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    assert source.fetch("child-1").text == "하위 문서 본문"


def test_list_pages_without_page_id_still_uses_database_or_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """page_id 가 없으면 기존처럼 database_id/search 분기가 그대로 동작한다(회귀)."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return _json({"results": []})

    source = NotionSource(token="t1", database_id="db-1")
    _patch_client(monkeypatch, source, handler)
    source.list_pages()

    assert paths == ["/databases/db-1/query"]


def test_list_pages_with_page_id_includes_child_database_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """child_database 블록을 만나면 database query 로 행을 조회해 acc 에 합류한다."""
    hub_children = [
        {
            "id": "db-1",
            "type": "child_database",
            "has_children": False,
            "child_database": {"title": "안건 DB"},
        },
    ]
    db_rows = [
        {
            "id": "row-1",
            "properties": {
                "이름": {"type": "title", "title": [{"plain_text": "행 1"}]}
            },
            "url": "https://www.notion.so/row-1",
            "last_edited_time": "2026-07-05T00:00:00.000Z",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/databases/db-1/query":
            return _json({"results": db_rows})
        block_id = request.url.path.split("/")[2]
        return _json({"results": hub_children if block_id == "hub-page" else []})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["row-1"]
    assert pages[0].title == "행 1"


def test_list_pages_with_page_id_recurses_into_database_row_child_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """database row 하위에 child_page 가 있으면 재귀로 마저 수집한다."""
    hub_children = [
        {
            "id": "db-1",
            "type": "child_database",
            "has_children": False,
            "child_database": {"title": "안건 DB"},
        },
    ]
    db_rows = [
        {
            "id": "row-1",
            "properties": {"이름": {"type": "title", "title": [{"plain_text": "행 1"}]}},
            "url": "https://www.notion.so/row-1",
            "last_edited_time": "2026-07-05T00:00:00.000Z",
        },
    ]
    row_children = [
        {
            "id": "nested-1",
            "type": "child_page",
            "has_children": False,
            "child_page": {"title": "행 하위 문서"},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/databases/db-1/query":
            return _json({"results": db_rows})
        block_id = request.url.path.split("/")[2]
        children_by_id = {"hub-page": hub_children, "row-1": row_children}
        return _json({"results": children_by_id.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["row-1", "nested-1"]


# --- ProjectSourceService.register_page --------------------------------------


def test_register_page_creates_row_with_kind_page(db_session) -> None:
    """register_page 는 kind='page' 로 행을 만든다."""
    repo = ProjectSourceRepository(db_session)
    service = ProjectSourceService(db_session, repo)

    row, status = service.register_page("A", "page-a")

    assert status == "created"
    assert row.location == "page-a"
    assert row.kind == "page"


def test_register_page_upsert_replaces_value_and_kind(db_session) -> None:
    """같은 project 로 register_page 를 다시 호출하면 값만 교체된다(행 증가 없음)."""
    repo = ProjectSourceRepository(db_session)
    service = ProjectSourceService(db_session, repo)
    service.register_page("A", "page-a")

    row, status = service.register_page("A", "page-a-v2")

    assert status == "updated"
    assert len(repo.list_by_type("notion")) == 1
    assert row.location == "page-a-v2"
    assert row.kind == "page"


def test_register_database_still_sets_kind_database(db_session) -> None:
    """기존 register()(데이터베이스 등록)는 kind='database' 로 저장된다(회귀)."""
    repo = ProjectSourceRepository(db_session)
    service = ProjectSourceService(db_session, repo)

    row, status = service.register("A", "notion", "db-a", kind="database")

    assert status == "created"
    assert row.kind == "database"


def test_register_page_then_register_database_switches_kind(db_session) -> None:
    """한 프로젝트에 page 로 등록했다가 database 로 재등록하면 kind 가 교체된다."""
    repo = ProjectSourceRepository(db_session)
    service = ProjectSourceService(db_session, repo)
    service.register_page("A", "page-a")

    row, status = service.register("A", "notion", "db-a", kind="database")

    assert status == "updated"
    assert row.kind == "database"
    assert row.location == "db-a"


def test_register_page_empty_value_raises_validation_error(db_session) -> None:
    """register_page 도 기존 _normalize_value 검증을 재사용한다(빈 문자열 금지)."""
    repo = ProjectSourceRepository(db_session)
    service = ProjectSourceService(db_session, repo)

    with pytest.raises(ValidationError):
        service.register_page("A", "")


def test_register_page_empty_project_raises_validation_error(db_session) -> None:
    """register_page 도 project 필수 검증을 재사용한다."""
    repo = ProjectSourceRepository(db_session)
    service = ProjectSourceService(db_session, repo)

    with pytest.raises(ValidationError):
        service.register_page("", "page-a")


# --- ProjectSourceResolver: kind 인지 빌더 호출 -----------------------------


def test_resolver_calls_builder_with_kind_page(make_project_resolver) -> None:
    """resolver 는 kind='page' 매핑에 대해 빌더를 (notion_id, 'page') 로 호출한다."""
    from tests.fixtures.document_sources import FakeDocumentSource

    fake = FakeDocumentSource("notion")
    resolver = make_project_resolver(
        notion_mapping={"A": ("page-a", "page", fake)},
    )

    sources = resolver.resolve_for_project("A")

    assert sources["notion"] is fake
    assert resolver.notion_builder_calls == [("page-a", "page")]


def test_resolver_calls_builder_with_kind_database_by_default(make_project_resolver) -> None:
    """kind 를 명시하지 않은 기존 매핑은 여전히 'database' 로 빌더가 호출된다(회귀)."""
    from tests.fixtures.document_sources import FakeDocumentSource

    fake = FakeDocumentSource("notion")
    resolver = make_project_resolver(notion_mapping={"A": ("db-a", fake)})

    sources = resolver.resolve_for_project("A")

    assert sources["notion"] is fake
    assert resolver.notion_builder_calls == [("db-a", "database")]


def test_resolver_distinguishes_same_id_different_kind(make_project_resolver) -> None:
    """같은 notion_id 라도 kind 가 다르면 별개로 캐싱·조회된다."""
    from tests.fixtures.document_sources import FakeDocumentSource

    fake_db = FakeDocumentSource("notion")
    fake_page = FakeDocumentSource("notion")
    resolver = make_project_resolver(
        notion_mapping={
            "A": ("same-id", "database", fake_db),
            "B": ("same-id", "page", fake_page),
        }
    )

    sources_a = resolver.resolve_for_project("A")
    sources_b = resolver.resolve_for_project("B")

    assert sources_a["notion"] is fake_db
    assert sources_b["notion"] is fake_page


# --- source_factory.build_notion_source: kind 분기 ---------------------------


def test_build_notion_source_kind_page_sets_page_id() -> None:
    """kind='page' 면 NotionSource 가 page_id 로 구성된다."""
    from dataclasses import replace

    from app.core.config import Settings
    from app.services.documents.sources.source_factory import build_notion_source

    settings = replace(Settings(), notion_token="t1")

    source = build_notion_source(settings, "page-x", kind="page")

    assert source is not None
    assert source._page_id == "page-x"
    assert source._database_id is None


def test_build_notion_source_kind_database_sets_database_id() -> None:
    """kind='database'(기본값)면 기존처럼 database_id 로 구성된다(회귀)."""
    from dataclasses import replace

    from app.core.config import Settings
    from app.services.documents.sources.source_factory import build_notion_source

    settings = replace(Settings(), notion_token="t1")

    source = build_notion_source(settings, "db-x")

    assert source is not None
    assert source._database_id == "db-x"
    assert source._page_id is None


def test_build_notion_source_without_token_returns_none_regardless_of_kind() -> None:
    """토큰이 없으면 kind 와 무관하게 None 이다(회귀)."""
    from dataclasses import replace

    from app.core.config import Settings
    from app.services.documents.sources.source_factory import build_notion_source

    settings = replace(Settings(), notion_token=None)

    assert build_notion_source(settings, "page-x", kind="page") is None
    assert build_notion_source(settings, "db-x") is None


# --- ProjectSourceRepository.upsert(kind=...) ---------------------------------


def test_upsert_kind_sets_value_and_kind(db_session) -> None:
    """upsert 는 값 컬럼과 kind 를 함께 세팅한다."""
    repo = ProjectSourceRepository(db_session)

    row = repo.upsert("A", "notion", "page-a", kind="page")
    db_session.commit()

    assert row.location == "page-a"
    assert row.kind == "page"


def test_upsert_kind_omitted_leaves_kind_none(db_session) -> None:
    """kind 를 명시하지 않고 upsert() 로 만든 행은 kind 가 None 이다(호출부가 명시할 책임)."""
    repo = ProjectSourceRepository(db_session)

    row = repo.upsert(DEFAULT_PROJECT, "notion", "db-default")
    db_session.commit()

    assert row.kind is None


def test_fetch_does_not_descend_into_child_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """본문 재귀는 child_page 에서 멈춘다(하위 페이지는 독립 문서로 색인되므로 중복 방지).

    제목은 남고 하위 페이지 본문은 들어오지 않는다.
    """
    tree = {
        "parent-page": [
            {
                "id": "para-1",
                "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": "부모 본문"}]},
            },
            {
                "id": "child-1",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "하위 문서"},
            },
        ],
        "child-1": [
            {
                "id": "para-2",
                "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": "하위 본문"}]},
            },
        ],
    }
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        requested.append(block_id)
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="parent-page")
    _patch_client(monkeypatch, source, handler)

    fetched = source.fetch("parent-page")

    assert "부모 본문" in fetched.text
    assert "하위 문서" in fetched.text
    assert "하위 본문" not in fetched.text
    assert "child-1" not in requested


def test_fetch_still_descends_into_toggle_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """child_page 가 아닌 컨테이너(toggle)에는 계속 재귀한다(회귀 방지)."""
    tree = {
        "parent-page": [
            {
                "id": "toggle-1",
                "type": "toggle",
                "has_children": True,
                "toggle": {"rich_text": [{"plain_text": "접힌 제목"}]},
            },
        ],
        "toggle-1": [
            {
                "id": "para-1",
                "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": "접힌 안쪽 본문"}]},
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="parent-page")
    _patch_client(monkeypatch, source, handler)

    fetched = source.fetch("parent-page")

    assert "접힌 안쪽 본문" in fetched.text


def test_list_pages_with_database_id_recurses_into_row_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """database 모드에서도 각 행 페이지 하위의 child_page 가 독립 문서로 목록화된다."""
    row = {
        "id": "row-1",
        "url": "https://www.notion.so/row-1",
        "properties": {"이름": {"type": "title", "title": [{"plain_text": "장애 A"}]}},
        "last_edited_time": "2026-07-05T00:00:00.000Z",
    }
    children = {
        "row-1": [
            {
                "id": "child-1",
                "type": "child_page",
                "has_children": False,
                "child_page": {"title": "원인 분석"},
            },
        ],
        "child-1": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/databases/"):
            return _json({"results": [row], "has_more": False})
        block_id = request.url.path.split("/")[2]
        return _json({"results": children.get(block_id, [])})

    source = NotionSource(token="t1", database_id="db-1")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["row-1", "child-1"]
    assert [p.title for p in pages] == ["장애 A", "원인 분석"]


def test_list_pages_with_database_id_does_not_duplicate_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """행이 자기 자신을 다시 참조해도 중복 목록화되지 않는다."""
    row = {
        "id": "row-1",
        "url": "https://www.notion.so/row-1",
        "properties": {"이름": {"type": "title", "title": [{"plain_text": "장애 A"}]}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/databases/"):
            return _json({"results": [row], "has_more": False})
        return _json(
            {
                "results": [
                    {
                        "id": "row-1",
                        "type": "child_page",
                        "has_children": False,
                        "child_page": {"title": "장애 A"},
                    }
                ]
            }
        )

    source = NotionSource(token="t1", database_id="db-1")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["row-1"]


def test_collect_child_pages_descends_through_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """toggle 안에 중첩된 child_page 도 목록화된다."""
    tree = {
        "hub-page": [
            {
                "id": "toggle-1",
                "type": "toggle",
                "has_children": True,
                "toggle": {"rich_text": [{"plain_text": "지난 장애"}]},
            },
        ],
        "toggle-1": [
            {
                "id": "child-1",
                "type": "child_page",
                "has_children": False,
                "child_page": {"title": "2026-07 타임아웃"},
            },
        ],
        "child-1": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["child-1"]
    assert [p.title for p in pages] == ["2026-07 타임아웃"]


def test_collect_child_pages_stops_after_container_depth_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """컨테이너 하강은 MAX_CONTAINER_DEPTH 단계에서 멈춘다(호출 폭증 방지)."""
    tree = {
        "hub-page": [{"id": "c1", "type": "toggle", "has_children": True, "toggle": {}}],
        "c1": [{"id": "c2", "type": "toggle", "has_children": True, "toggle": {}}],
        "c2": [{"id": "c3", "type": "toggle", "has_children": True, "toggle": {}}],
        "c3": [{"id": "c4", "type": "toggle", "has_children": True, "toggle": {}}],
        "c4": [
            {
                "id": "deep-page",
                "type": "child_page",
                "has_children": False,
                "child_page": {"title": "너무 깊음"},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert pages == []


def test_container_depth_does_not_consume_page_depth_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """토글을 여러 겹 지나도 페이지 중첩 깊이 예산은 줄지 않는다."""
    tree = {
        "hub-page": [{"id": "t1", "type": "toggle", "has_children": True, "toggle": {}}],
        "t1": [{"id": "t2", "type": "toggle", "has_children": True, "toggle": {}}],
        "t2": [
            {
                "id": "p1",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "1단계"},
            }
        ],
        "p1": [
            {
                "id": "p2",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "2단계"},
            }
        ],
        "p2": [
            {
                "id": "p3",
                "type": "child_page",
                "has_children": False,
                "child_page": {"title": "3단계"},
            }
        ],
        "p3": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["p1", "p2", "p3"]


def test_fetch_prepends_page_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 행 속성이 본문 앞에 '이름: 값' 줄로 붙는다."""
    page = {
        "id": "row-1",
        "properties": {
            "상태": {"type": "status", "status": {"name": "해결"}},
            "태그": {"type": "multi_select", "multi_select": [{"name": "DB"}]},
        },
    }
    blocks = [
        {
            "id": "para-1",
            "type": "paragraph",
            "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "본문 한 줄"}]},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/pages/"):
            return _json(page)
        return _json({"results": blocks})

    source = NotionSource(token="t1", database_id="db-1")
    _patch_client(monkeypatch, source, handler)

    text = source.fetch("row-1").text

    assert text.splitlines()[:2] == ["상태: 해결", "태그: DB"]
    assert "본문 한 줄" in text


def test_fetch_survives_property_lookup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """속성 조회가 실패해도 블록 본문만으로 색인을 계속한다."""
    blocks = [
        {
            "id": "para-1",
            "type": "paragraph",
            "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "본문 한 줄"}]},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/pages/"):
            return httpx.Response(403, json={"message": "denied"})
        return _json({"results": blocks})

    source = NotionSource(token="t1", database_id="db-1")
    _patch_client(monkeypatch, source, handler)

    text = source.fetch("row-1").text

    assert text == "본문 한 줄"
