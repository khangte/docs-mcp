"""SQLAlchemy 엔진/세션 팩토리.

sync SQLAlchemy 2.0 세션을 사용한다 (테스트 단순성 목적).
postgres(+pgvector) 고정 운영을 전제로 하며, main.py 에서 생성해 의존성으로 주입한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(database_url: str, **engine_kwargs: Any) -> Engine:
    """postgres 엔진을 생성한다."""
    return create_engine(database_url, future=True, **engine_kwargs)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """세션 팩토리를 반환."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def managed_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """예외 발생 시에도 session.close() 를 보장하는 컨텍스트 매니저.

    MCP 도구처럼 제너레이터 프로토콜을 사용할 수 없는 컨텍스트에서 사용한다.
    """
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
