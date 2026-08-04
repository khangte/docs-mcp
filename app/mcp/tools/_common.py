"""MCP 도구 등록 함수들이 공유하는 실행 헬퍼."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from app.composition import AppState, ServiceBundle, build_services
from app.core.errors import DomainError, IntegrationError
from app.core.logging import get_logger
from app.core.config import get_settings
from app.mcp.types import ErrorPayload

_LOG = get_logger("docs_mcp.mcp", level=get_settings().log_level)

# _run_bundle 이 내부 함수의 반환 타입을 그대로 전파하도록 하는 제네릭 타입 변수.
_T = TypeVar("_T")


def _run_bundle(app_state: AppState, fn: Callable[[ServiceBundle], _T]) -> _T:
    """build_services 번들을 열고 fn(bundle)을 실행한 뒤 세션을 닫는다."""
    bundle_iter = build_services(app_state)
    bundle = next(bundle_iter)
    try:
        return fn(bundle)
    finally:
        try:
            next(bundle_iter)
        except StopIteration:
            pass


def to_error_payload(error: DomainError | IntegrationError) -> ErrorPayload:
    """DomainError/IntegrationError를 클라이언트에 노출할 에러 페이로드로 변환한다.

    스택트레이스는 서버 로그에만 남기고, 클라이언트에는 code/message만 전달한다.
    """
    code = error.code if isinstance(error, DomainError) else "integration_error"
    _LOG.error("mcp tool error: %s", code, exc_info=error)
    return {"error": True, "code": code, "message": str(error)}
