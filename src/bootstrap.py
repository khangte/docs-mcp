"""앱 상태(AppState) 초기화 팩토리.

main.py와 mcp_server.py가 공유하는 부트스트랩 로직을 한 곳에 모은다.
"""

from __future__ import annotations

from src.api.dependencies import AppState
from src.core.config import Settings, get_settings
from src.core.db import create_db_engine
from src.models.openapi import create_all
from src.services.ingestor.openapi_fetcher import HttpOpenAPIFetcher


def bootstrap_app_state(cfg: Settings | None = None) -> AppState:
    """설정을 받아 DB 엔진 생성, 테이블 초기화, AppState 구성을 수행한다."""
    if cfg is None:
        cfg = get_settings()
    engine = create_db_engine(cfg.database_url)
    create_all(engine)
    return AppState.from_engine(
        engine=engine,
        fetcher=HttpOpenAPIFetcher(),
        embedding_dim=cfg.embedding_dim,
        hybrid_alpha=cfg.hybrid_alpha,
    )
