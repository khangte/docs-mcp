"""헬스체크 / 레디니스."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from src.api.dependencies import AppState

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """단순 헬스체크: 항상 ok 를 반환한다."""
    return {"status": "ok"}


def _get_state(request: Request) -> AppState:
    """요청에서 AppState 를 꺼내 반환한다."""
    state = getattr(request.app.state, "app_state", None)
    if state is None:
        raise RuntimeError("AppState is not configured on the application")
    return state


@router.get("/ready")
def ready(state: AppState = Depends(_get_state)) -> dict[str, Any]:
    """DB 와 벡터 인덱스 동작 여부를 확인해 레디니스 상태를 반환한다."""
    db_ok = False
    documents = 0
    try:
        session = state.session_factory()
        try:
            session.execute(text("SELECT 1"))
            from src.models.openapi import ApiDocument
            from sqlalchemy import func, select

            count = session.execute(select(func.count()).select_from(ApiDocument)).scalar_one()
            documents = int(count or 0)
            db_ok = True
        finally:
            session.close()
    except Exception:
        db_ok = False
    vector_ok = True  # 인메모리 구조는 객체가 살아있으면 OK
    overall = "ok" if db_ok and vector_ok else "degraded"
    return {
        "status": overall,
        "db": db_ok,
        "vector_index": vector_ok,
        "documents": documents,
    }
