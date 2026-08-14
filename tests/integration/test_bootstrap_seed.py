"""`seed_default_sources()` 의 notion_page_id/notion_database_id 시드 동작 테스트.

`DOCS_MCP_NOTION_PAGE_ID` 레거시 슬롯이 실제로 시드되는지, database_id 와
동시 설정 시 page 가 우선하는지, 재호출 시 멱등한지를 검증한다.
"""

from __future__ import annotations

from app.bootstrap import seed_default_sources
from app.core.config import Settings
from app.core.db import create_session_factory
from app.models import DEFAULT_PROJECT
from app.models.document_meta import SOURCE_NOTION
from app.repositories.project_source_repository import ProjectSourceRepository


def test_seed_with_only_page_id_creates_page_row(pg_engine) -> None:
    """page_id 만 설정하면 kind='page' 로 시드된다."""
    cfg = Settings(notion_page_id="page-1", notion_database_id=None)

    seed_default_sources(pg_engine, cfg)

    session_factory = create_session_factory(pg_engine)
    with session_factory() as session:
        repo = ProjectSourceRepository(session)
        row = repo.get(DEFAULT_PROJECT, SOURCE_NOTION)
        assert row is not None
        assert row.location == "page-1"
        assert row.kind == "page"


def test_seed_with_only_database_id_creates_database_row(pg_engine) -> None:
    """database_id 만 설정하면 kind='database' 로 시드된다."""
    cfg = Settings(notion_page_id=None, notion_database_id="db-1")

    seed_default_sources(pg_engine, cfg)

    session_factory = create_session_factory(pg_engine)
    with session_factory() as session:
        repo = ProjectSourceRepository(session)
        row = repo.get(DEFAULT_PROJECT, SOURCE_NOTION)
        assert row is not None
        assert row.location == "db-1"
        assert row.kind == "database"


def test_seed_with_both_ids_prefers_page(pg_engine) -> None:
    """둘 다 설정되면 page 가 채택되고 database 는 무시된다."""
    cfg = Settings(notion_page_id="page-1", notion_database_id="db-1")

    seed_default_sources(pg_engine, cfg)

    session_factory = create_session_factory(pg_engine)
    with session_factory() as session:
        repo = ProjectSourceRepository(session)
        row = repo.get(DEFAULT_PROJECT, SOURCE_NOTION)
        assert row is not None
        assert row.location == "page-1"
        assert row.kind == "page"


def test_seed_is_idempotent_and_does_not_overwrite_existing_row(pg_engine) -> None:
    """이미 행이 있으면 재호출해도 값이 바뀌지 않는다."""
    cfg = Settings(notion_page_id="page-1", notion_database_id=None)
    seed_default_sources(pg_engine, cfg)

    cfg2 = Settings(notion_page_id="page-2", notion_database_id=None)
    seed_default_sources(pg_engine, cfg2)

    session_factory = create_session_factory(pg_engine)
    with session_factory() as session:
        repo = ProjectSourceRepository(session)
        row = repo.get(DEFAULT_PROJECT, SOURCE_NOTION)
        assert row is not None
        assert row.location == "page-1"
        assert row.kind == "page"
