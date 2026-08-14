"""공용 pytest fixture."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.composition import AppState
from app.core.db import create_db_engine, create_session_factory
from app.models import DEFAULT_PROJECT, EMBEDDING_DIM, create_all
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION
from app.repositories.project_source_repository import ProjectSourceRepository
from app.services.indexer.embedding_provider import HashEmbeddingProvider
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
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
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
def fake_drive_source():
    """Drive 를 대신하는 페이크 문서 소스(실제 HTTP 호출 없음)."""
    from tests.fixtures.document_sources import FakeDocumentSource

    return FakeDocumentSource(SOURCE_DRIVE)


@pytest.fixture()
def fake_notion_source():
    """Notion 을 대신하는 페이크 문서 소스(실제 HTTP 호출 없음)."""
    from tests.fixtures.document_sources import FakeDocumentSource

    return FakeDocumentSource(SOURCE_NOTION)


@pytest.fixture()
def app_state(pg_engine, in_memory_fetcher, fake_drive_source, fake_notion_source):
    """테스트용 AppState.

    `embedding_provider` 를 `HashEmbeddingProvider` 로 명시 주입해 무거운
    로컬 모델(SentenceTransformer) 다운로드/로딩 없이 결정적으로 동작하게
    한다(env 오염 없는 dependency override, `is_semantic=False`).
    `vector_fallback_enabled` 는 True 로 고정해 벡터 보조 경로가 이 값에
    좌우되지 않게 한다. 보조 비활성 동작을 검증하는 테스트는 이 값을
    명시적으로 False 로 바꾼다.

    Drive/Notion 어댑터는 이제 project → folder_id/database_id 매핑에서
    요청 시점에 만들어진다(SPEC 기능 5). `drive_source_builder`/
    `notion_source_builder` 를 페이크로 고정해, folder_id/database_id 값과
    무관하게 항상 같은 페이크 어댑터를 돌려주도록 한다 — 실행 환경에
    실제 Drive/Notion 자격증명이 있어도 테스트가 외부 API 를 호출하지 않는다.
    """
    state = AppState.from_engine(
        engine=pg_engine,
        fetcher=in_memory_fetcher,
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
        vector_fallback_enabled=True,
        drive_source_builder=lambda folder_id: fake_drive_source,
        notion_source_builder=lambda notion_id, kind: fake_notion_source,
    )
    return state


@pytest.fixture()
def make_project_resolver(db_session):
    """(project, folder_id, database_id) 매핑을 심고 페이크로 고정된 resolver 를 만드는 헬퍼.

    `ProjectSourceResolver` 단위 테스트(document_index_service/
    document_search_service)에서, 여러 project 에 서로 다른 페이크 소스를
    붙이고 싶을 때 쓴다. project 별로 다른 페이크를 주입할 수 있도록
    folder_id/database_id → DocumentSource 매핑을 딕셔너리로 받는다.
    """
    from app.core.config import Settings
    from app.repositories.project_source_repository import ProjectSourceRepository
    from app.services.documents.project_source_resolver import ProjectSourceResolver

    def _make(
        drive_mapping: dict[str, object] | None = None,
        notion_mapping: dict[str, object] | None = None,
    ):
        """project → (folder_id, 페이크) / (database_id[, kind], 페이크) 매핑으로 만든다.

        Args:
            drive_mapping: `{project: (folder_id, fake_drive_source)}`.
            notion_mapping: `{project: (database_id, fake_notion_source)}` 또는
                `{project: (notion_id, kind, fake_notion_source)}`(3튜플이면
                kind 를 명시, 생략하면 "database").

        반환된 resolver 에는 `drive_builder_calls`/`notion_builder_calls`
        속성(리스트)이 붙어, 빌더가 어떤 folder_id/(notion_id, kind) 로 몇 번
        호출됐는지(캐싱 검증) 테스트가 직접 확인할 수 있다.
        """
        source_repo = ProjectSourceRepository(db_session)
        drive_by_folder: dict[str, object] = {}
        for project, (folder_id, fake) in (drive_mapping or {}).items():
            source_repo.upsert(project, "drive", folder_id)
            drive_by_folder[folder_id] = fake
        notion_by_id_kind: dict[tuple[str, str], object] = {}
        for project, entry in (notion_mapping or {}).items():
            if len(entry) == 3:
                notion_id, kind, fake = entry
            else:
                notion_id, fake = entry
                kind = "database"
            source_repo.upsert(project, "notion", notion_id, kind=kind)
            notion_by_id_kind[(notion_id, kind)] = fake
        db_session.commit()

        drive_builder_calls: list[str] = []
        notion_builder_calls: list[tuple[str, str]] = []

        def _drive_builder(folder_id: str):
            drive_builder_calls.append(folder_id)
            return drive_by_folder.get(folder_id)

        def _notion_builder(notion_id: str, kind: str):
            notion_builder_calls.append((notion_id, kind))
            return notion_by_id_kind.get((notion_id, kind))

        resolver = ProjectSourceResolver(
            settings=Settings(),
            drive_token_provider=None,
            source_repo=source_repo,
            drive_source_builder=_drive_builder,
            notion_source_builder=_notion_builder,
        )
        resolver.drive_builder_calls = drive_builder_calls  # type: ignore[attr-defined]
        resolver.notion_builder_calls = notion_builder_calls  # type: ignore[attr-defined]
        return resolver

    return _make


@pytest.fixture()
def fake_drive_source_builder():
    """folder_id → 페이크 Drive 어댑터를 돌려주는 빌더.

    호출될 때마다 그 folder_id 전용의 새 `FakeDocumentSource` 를 만들고,
    같은 folder_id 로 다시 부르면 이전에 만든 인스턴스를 그대로 재사용한다
    (실제 `build_drive_source` 캐싱 계약과 동일한 모양). project 별로 서로
    다른 폴더를 등록해두면 이 빌더 하나로 서로 다른 페이크가 자동으로
    붙는다 — SPEC 기능 7 프로젝트 격리 테스트가 두 프로젝트에 별개의
    페이크를 붙일 때 쓴다.
    """
    from tests.fixtures.document_sources import FakeDocumentSource

    instances: dict[str, FakeDocumentSource] = {}

    def _build(folder_id: str) -> FakeDocumentSource:
        if folder_id not in instances:
            instances[folder_id] = FakeDocumentSource(SOURCE_DRIVE)
        return instances[folder_id]

    _build.instances = instances  # type: ignore[attr-defined]
    return _build


@pytest.fixture()
def fake_notion_source_builder():
    """(notion_id, kind) → 페이크 Notion 어댑터를 돌려주는 빌더. Drive 판과 동일한 구조.

    kind 는 기본값 "database"로 생략 호출도 허용해, 기존 호출부(`builder(id)`)
    가 그대로 동작하게 한다.
    """
    from tests.fixtures.document_sources import FakeDocumentSource

    instances: dict[tuple[str, str], FakeDocumentSource] = {}

    def _build(notion_id: str, kind: str = "database") -> FakeDocumentSource:
        key = (notion_id, kind)
        if key not in instances:
            instances[key] = FakeDocumentSource(SOURCE_NOTION)
        return instances[key]

    _build.instances = instances  # type: ignore[attr-defined]
    return _build


@pytest.fixture()
def two_project_app_state(
    pg_engine, in_memory_fetcher, fake_drive_source_builder, fake_notion_source_builder
):
    """folder_id/database_id 별로 서로 다른 페이크가 붙는 AppState.

    `app_state` 픽스처는 모든 folder_id/database_id 에 항상 같은 페이크를
    돌려줘 project 개념 없는 기존 테스트에 적합하다. 이 픽스처는 반대로
    project 마다 실제로 다른 어댑터가 붙는지(격리) 검증해야 하는 기능 7
    테스트를 위해, 빌더가 folder_id/database_id 별로 별개의 페이크
    인스턴스를 반환하도록 구성한다.
    """
    return AppState.from_engine(
        engine=pg_engine,
        fetcher=in_memory_fetcher,
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
        vector_fallback_enabled=True,
        drive_source_builder=fake_drive_source_builder,
        notion_source_builder=fake_notion_source_builder,
    )


@pytest.fixture()
def two_project_services(session_factory, fake_drive_source_builder, fake_notion_source_builder):
    """A(folder-a, db-a)/B(folder-b, db-b) 매핑을 DB 에 미리 심어둔다.

    MCP 도구(`register_drive_source` 등)를 거치지 않고 리포지토리로 직접
    매핑을 만들어, 두 프로젝트가 이미 등록된 상태에서 테스트를 시작하고
    싶을 때 쓴다. 반환값은 project → {"drive": 페이크, "notion": 페이크}.
    """
    session = session_factory()
    try:
        source_repo = ProjectSourceRepository(session)
        source_repo.upsert("A", "drive", "folder-a")
        source_repo.upsert("B", "drive", "folder-b")
        source_repo.upsert("A", "notion", "db-a")
        source_repo.upsert("B", "notion", "db-b")
        session.commit()
    finally:
        session.close()

    return {
        "A": {
            "drive": fake_drive_source_builder("folder-a"),
            "notion": fake_notion_source_builder("db-a"),
        },
        "B": {
            "drive": fake_drive_source_builder("folder-b"),
            "notion": fake_notion_source_builder("db-b"),
        },
    }


@pytest.fixture()
def two_project_mcp(two_project_app_state, two_project_services):
    """A(folder-a, db-a)/B(folder-b, db-b) 매핑이 이미 DB 에 심어진 MCP 서버.

    두 프로젝트 모두 Drive+Notion 이 서로 다른 폴더/DB 로 구성된다(SPEC
    기능 7: "두 프로젝트(Drive+Notion 모두 다르게 구성)"). `two_project_services`
    가 매핑을 미리 심어두므로, 이 서버로 바로 `register_document`/
    `refresh_index`/`search_documents` 등을 호출할 수 있다.
    """
    from app.mcp.server import create_mcp_server

    return create_mcp_server(two_project_app_state)


@pytest.fixture()
def seed_default_project_sources(pg_engine, session_factory):
    """DEFAULT_PROJECT 에 Drive/Notion 매핑을 심어 페이크 어댑터가 잡히게 한다.

    `app_state` 의 `drive_source_builder`/`notion_source_builder` 는 folder_id/
    database_id 와 무관하게 항상 페이크를 돌려주지만, `ProjectSourceResolver`
    는 `project_source` 에 매핑 행이 있어야만 빌더를 호출한다. 대부분의
    기존 테스트는 project 개념 없이 동작하므로, DEFAULT_PROJECT 앞으로 더미
    매핑을 미리 심어 페이크가 잡히게 한다.
    """
    session = session_factory()
    try:
        source_repo = ProjectSourceRepository(session)
        source_repo.upsert(DEFAULT_PROJECT, "drive", "fake-folder")
        source_repo.upsert(DEFAULT_PROJECT, "notion", "fake-database")
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def counting_embedding_provider(app_state):
    """app_state 의 임베딩 프로바이더를 호출 카운트 페이크로 감싼다.

    색인(register) 단계에서도 임베딩이 호출되므로, 검색 호출만 세려면
    테스트에서 `reset_counts()` 를 호출한 뒤 검색을 수행한다.
    """
    from tests.fixtures.fakes import CountingEmbeddingProvider

    provider = CountingEmbeddingProvider(app_state.embedding_provider)
    app_state.embedding_provider = provider
    return provider


@pytest.fixture()
def services_factory(app_state):
    """app_state 기반으로 ServiceBundle 을 생성하는 헬퍼 팩토리."""
    from app.composition import build_services

    def _factory():
        return next(build_services(app_state))

    return _factory


