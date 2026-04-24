"""SQLAlchemy 엔진/세션 팩토리.

sync SQLAlchemy 2.0 세션을 사용한다 (테스트 단순성 목적).
main.py 에서 생성해 의존성으로 주입하고, 테스트에서는 in-memory URL 로 새로 만든다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(database_url: str, **engine_kwargs: Any) -> Engine:
    """엔진 생성. SQLite in-memory 인 경우에도 스레드 간 공유 가능하게 옵션 부여."""

    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(
        database_url,
        connect_args=connect_args,
        future=True,
        **engine_kwargs,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """세션 팩토리를 반환."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """요청 범위 세션 생성기 (FastAPI Depends 호환 형태)."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
