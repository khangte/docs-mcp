"""앱 상태(AppState) 초기화 팩토리.

main.py와 mcp_server.py가 공유하는 부트스트랩 로직을 한 곳에 모은다.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from app.composition import AppState
from app.core.config import Settings, get_settings
from app.core.db import create_db_engine, create_session_factory, managed_session
from app.models.openapi import DEFAULT_PROJECT, create_all
from app.repositories.project_source_repository import (
    ProjectDriveSourceRepository,
    ProjectNotionSourceRepository,
)
from app.services.ingestor.openapi_fetcher import HttpOpenAPIFetcher


def bootstrap_app_state(cfg: Settings | None = None) -> AppState:
    """설정을 받아 DB 엔진 생성, 테이블 초기화, AppState 구성을 수행한다."""
    if cfg is None:
        cfg = get_settings()
    engine = create_db_engine(cfg.database_url)
    create_all(engine)
    seed_default_sources(engine, cfg)
    return AppState.from_engine(
        engine=engine,
        fetcher=HttpOpenAPIFetcher(),
        embedding_dim=cfg.embedding_dim,
        hybrid_alpha=cfg.hybrid_alpha,
    )


def seed_default_sources(engine: Engine, cfg: Settings) -> None:
    """하위 호환용 `drive_folder_id`/`notion_database_id` 설정을 DEFAULT_PROJECT 로 시드한다.

    ARCH_REVIEW R1: AppState 를 만드는 경로가 `bootstrap_app_state()` 외에도
    `main.py` 의 커스텀 fetcher 우회 경로가 있어, seed 를 `bootstrap_app_state()`
    안에만 두면 그 경로에서 누락된다. 그래서 `create_all()` 직후·AppState
    생성 이전인 이 지점(세션을 직접 열어 처리)을 두 경로가 공통으로 호출하는
    형태로 둔다.

    `project_drive_source`/`project_notion_source` 에 `(DEFAULT_PROJECT, 값)`
    이 이미 있으면 아무 것도 하지 않는다(재기동해도 중복 생성 없음).
    """
    if not cfg.drive_folder_id and not cfg.notion_database_id:
        return
    session_factory = create_session_factory(engine)
    with managed_session(session_factory) as session:
        if cfg.drive_folder_id:
            drive_repo = ProjectDriveSourceRepository(session)
            if drive_repo.get(DEFAULT_PROJECT) is None:
                drive_repo.upsert(DEFAULT_PROJECT, cfg.drive_folder_id)
        if cfg.notion_database_id:
            notion_repo = ProjectNotionSourceRepository(session)
            if notion_repo.get(DEFAULT_PROJECT) is None:
                notion_repo.upsert(DEFAULT_PROJECT, cfg.notion_database_id)
        session.commit()
