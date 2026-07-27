"""공용 pytest fixture."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import AppState
from app.core.db import create_db_engine, create_session_factory
from app.main import create_app
from app.models.openapi import EMBEDDING_DIM, create_all
from app.services.ingestor.openapi_fetcher import InMemoryFetcher
from tests.fixtures.samples import openapi_3_json, swagger_2_json

_ADMIN_DATABASE_URL = os.environ.get(
    "DOCS_MCP_TEST_DATABASE_URL",
    "postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp",
)


def _with_database(url: str, database: str) -> str:
    """URL 의 database 이름만 교체한다."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture()
def sample_openapi_3() -> str:
    return openapi_3_json()


@pytest.fixture()
def sample_swagger_2() -> str:
    return swagger_2_json()


@pytest.fixture()
def pg_engine():
    """테스트마다 완전히 별도의 database 를 만들어 postgres(+pgvector)에 연결하는 엔진.

    같은 database 안에서 스키마만 나누면 SQLAlchemy create_all() 의 존재 확인
    (checkfirst) 이 다른 스키마(예: public)의 동일 이름 테이블을 "이미 존재"로
    오판해 DDL을 건너뛸 수 있다. database 단위로 분리하면 이 문제가 원천 차단된다.
    """
    db_name = f"test_{uuid.uuid4().hex[:12]}"
    admin_engine = create_db_engine(_ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    test_url = _with_database(_ADMIN_DATABASE_URL, db_name)
    setup_engine = create_db_engine(test_url)
    with setup_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    setup_engine.dispose()

    engine = create_db_engine(test_url)
    create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        admin_engine = create_db_engine(_ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.fixture()
def session_factory(pg_engine) -> sessionmaker[Session]:
    return create_session_factory(pg_engine)


@pytest.fixture()
def db_session(session_factory) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def in_memory_fetcher() -> InMemoryFetcher:
    return InMemoryFetcher()


@pytest.fixture()
def app_state(pg_engine, in_memory_fetcher):
    state = AppState.from_engine(
        engine=pg_engine,
        fetcher=in_memory_fetcher,
        embedding_dim=EMBEDDING_DIM,
        hybrid_alpha=0.4,
    )
    return state


@pytest.fixture()
def app(app_state):
    return create_app(app_state=app_state)


@pytest.fixture()
def services_factory(app_state):
    """app_state 기반으로 ServiceBundle 을 생성하는 헬퍼 팩토리."""
    from app.api.dependencies import build_services

    def _factory():
        return next(build_services(app_state))

    return _factory


@pytest.fixture()
def async_client(app):
    """httpx AsyncClient + ASGITransport 기반 테스트 클라이언트."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture()
def seeded_services(services_factory, sample_openapi_3):
    """app_state 와 동일 엔진을 공유하는 서비스로 샘플 문서를 먼저 등록."""
    services = services_factory()
    result = services.sync_service.register(
        source_url=None,
        raw_document=sample_openapi_3,
    )
    return {"services": services, "document_id": result.document.id, "result": result}

