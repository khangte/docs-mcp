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
from app.models.openapi import DEFAULT_PROJECT
from app.repositories.project_source_repository import ProjectNotionSourceRepository
from app.services.documents.notion_source import NotionSource
from app.services.documents.project_source_service import NotionSourceService

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


def test_list_pages_with_page_id_does_not_recurse_into_grandchild_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """child_page 안의 또 다른 child_page(손자)는 목록화되지 않는다(1단계만)."""
    top_level = [
        {
            "id": "child-1",
            "type": "child_page",
            "has_children": True,
            "child_page": {"title": "허브 하위 문서"},
        },
    ]
    # child-1 아래에 또 child_page 가 있어도 호출되면 안 된다.
    grandchild_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        if block_id == "hub-page":
            return _json({"results": top_level})
        grandchild_calls.append(block_id)
        return _json({"results": [{"id": "grandchild", "type": "child_page"}]})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["child-1"]
    assert grandchild_calls == []


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

    assert source.fetch("child-1") == "하위 문서 본문"


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


# --- NotionSourceService.register_page --------------------------------------


def test_register_page_creates_row_with_kind_page(db_session) -> None:
    """register_page 는 kind='page' 로 행을 만든다."""
    repo = ProjectNotionSourceRepository(db_session)
    service = NotionSourceService(db_session, repo)

    row, status = service.register_page("A", "page-a")

    assert status == "created"
    assert row.database_id == "page-a"
    assert row.kind == "page"


def test_register_page_upsert_replaces_value_and_kind(db_session) -> None:
    """같은 project 로 register_page 를 다시 호출하면 값만 교체된다(행 증가 없음)."""
    repo = ProjectNotionSourceRepository(db_session)
    service = NotionSourceService(db_session, repo)
    service.register_page("A", "page-a")

    row, status = service.register_page("A", "page-a-v2")

    assert status == "updated"
    assert len(repo.list_all()) == 1
    assert row.database_id == "page-a-v2"
    assert row.kind == "page"


def test_register_database_still_sets_kind_database(db_session) -> None:
    """기존 register()(데이터베이스 등록)는 kind='database' 로 저장된다(회귀)."""
    repo = ProjectNotionSourceRepository(db_session)
    service = NotionSourceService(db_session, repo)

    row, status = service.register("A", "db-a")

    assert status == "created"
    assert row.kind == "database"


def test_register_page_then_register_database_switches_kind(db_session) -> None:
    """한 프로젝트에 page 로 등록했다가 database 로 재등록하면 kind 가 교체된다."""
    repo = ProjectNotionSourceRepository(db_session)
    service = NotionSourceService(db_session, repo)
    service.register_page("A", "page-a")

    row, status = service.register("A", "db-a")

    assert status == "updated"
    assert row.kind == "database"
    assert row.database_id == "db-a"


def test_register_page_empty_value_raises_validation_error(db_session) -> None:
    """register_page 도 기존 _normalize_value 검증을 재사용한다(빈 문자열 금지)."""
    repo = ProjectNotionSourceRepository(db_session)
    service = NotionSourceService(db_session, repo)

    with pytest.raises(ValidationError):
        service.register_page("A", "")


def test_register_page_empty_project_raises_validation_error(db_session) -> None:
    """register_page 도 project 필수 검증을 재사용한다."""
    repo = ProjectNotionSourceRepository(db_session)
    service = NotionSourceService(db_session, repo)

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
    from app.services.documents.source_factory import build_notion_source

    settings = replace(Settings(), notion_token="t1")

    source = build_notion_source(settings, "page-x", kind="page")

    assert source is not None
    assert source._page_id == "page-x"
    assert source._database_id is None


def test_build_notion_source_kind_database_sets_database_id() -> None:
    """kind='database'(기본값)면 기존처럼 database_id 로 구성된다(회귀)."""
    from dataclasses import replace

    from app.core.config import Settings
    from app.services.documents.source_factory import build_notion_source

    settings = replace(Settings(), notion_token="t1")

    source = build_notion_source(settings, "db-x")

    assert source is not None
    assert source._database_id == "db-x"
    assert source._page_id is None


def test_build_notion_source_without_token_returns_none_regardless_of_kind() -> None:
    """토큰이 없으면 kind 와 무관하게 None 이다(회귀)."""
    from dataclasses import replace

    from app.core.config import Settings
    from app.services.documents.source_factory import build_notion_source

    settings = replace(Settings(), notion_token=None)

    assert build_notion_source(settings, "page-x", kind="page") is None
    assert build_notion_source(settings, "db-x") is None


# --- ProjectNotionSourceRepository.upsert_kind -------------------------------


def test_upsert_kind_sets_value_and_kind(db_session) -> None:
    """upsert_kind 는 값 컬럼과 kind 를 함께 세팅한다."""
    repo = ProjectNotionSourceRepository(db_session)

    row = repo.upsert_kind("A", "page-a", "page")
    db_session.commit()

    assert row.database_id == "page-a"
    assert row.kind == "page"


def test_upsert_kind_default_value_is_database_via_repo(db_session) -> None:
    """kind 를 명시하지 않고 기존 upsert() 로 만든 행은 모델 기본값 'database' 를 갖는다."""
    repo = ProjectNotionSourceRepository(db_session)

    row = repo.upsert(DEFAULT_PROJECT, "db-default")
    db_session.commit()

    assert row.kind == "database"
