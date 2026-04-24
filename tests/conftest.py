"""공용 pytest fixture."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import AppState
from src.core.db import create_db_engine, create_session_factory
from src.main import create_app
from src.models.openapi import create_all
from src.services.ingestor.openapi_fetcher import InMemoryFetcher
from tests.fixtures.samples import openapi_3_json, swagger_2_json


@pytest.fixture()
def sample_openapi_3() -> str:
    return openapi_3_json()


@pytest.fixture()
def sample_swagger_2() -> str:
    return swagger_2_json()


@pytest.fixture()
def sqlite_engine():
    # StaticPool + in-memory: 모든 세션이 동일 DB 연결을 공유하게 만든다
    # (sqlite3 는 커넥션마다 별도 메모리 DB 를 갖기 때문에 필요).
    from sqlalchemy.pool import StaticPool

    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
    )
    create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def session_factory(sqlite_engine) -> sessionmaker[Session]:
    return create_session_factory(sqlite_engine)


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
def app_state(sqlite_engine, in_memory_fetcher):
    state = AppState.from_engine(
        engine=sqlite_engine,
        fetcher=in_memory_fetcher,
        embedding_dim=128,
        hybrid_alpha=0.4,
    )
    return state


@pytest.fixture()
def app(app_state):
    return create_app(app_state=app_state)


@pytest.fixture()
def services_factory(app_state):
    """app_state 기반으로 ServiceBundle 을 생성하는 헬퍼 팩토리."""
    from src.api.dependencies import build_services

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

