"""헬스체크 / 레디니스."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text

from app.api.dependencies import AppState
from app.api.dependency_providers import get_app_state
from app.models.openapi import ApiDocument

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """단순 헬스체크: 항상 ok 를 반환한다."""
    return {"status": "ok"}


@router.get("/ready")
def ready(state: AppState = Depends(get_app_state)) -> dict[str, Any]:
    """DB 동작 여부를 확인해 레디니스 상태를 반환한다.

    벡터 검색은 pgvector 로 DB 에 직접 쿼리하므로 별도 헬스체크가 없다.
    """
    db_ok = False
    documents = 0
    try:
        session = state.session_factory()
        try:
            session.execute(text("SELECT 1"))
            count = session.execute(select(func.count()).select_from(ApiDocument)).scalar_one()
            documents = int(count or 0)
            db_ok = True
        finally:
            session.close()
    except Exception:
        db_ok = False
    overall = "ok" if db_ok else "degraded"
    return {
        "status": overall,
        "db": db_ok,
        "documents": documents,
    }
