"""MCP 도구 등록 함수들이 공유하는 실행 헬퍼."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import anyio

from app.composition import AppState, ServiceBundle, build_services
from app.core.errors import DomainError, IntegrationError
from app.core.logging import get_logger
from app.core.config import get_settings
from app.mcp.types import ErrorPayload

_LOG = get_logger("docs_mcp.mcp", level=get_settings().log_level)

# _run_bundle/run_bundle_tool 이 내부 함수의 반환 타입을 그대로 전파하도록 하는
# 제네릭 타입 변수.
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


async def run_bundle_tool(
    app_state: AppState, fn: Callable[[ServiceBundle], _T]
) -> _T | ErrorPayload:
    """MCP 도구 본문 공통 실행부: 스레드 오프로딩 + 번들 오픈 + 오류 변환.

    모든 도구가 반복하던 `anyio.to_thread.run_sync(_sync)` +
    `_run_bundle(app_state, _inner)` + `except (DomainError, IntegrationError)`
    3중 보일러플레이트를 한 곳으로 모은다. 각 도구는 `fn(bundle) -> 결과` 만
    정의하면 된다.
    """
    def _sync() -> _T | ErrorPayload:
        try:
            return _run_bundle(app_state, fn)
        except (DomainError, IntegrationError) as e:
            return to_error_payload(e)
    return await anyio.to_thread.run_sync(_sync)


def to_error_payload(error: DomainError | IntegrationError) -> ErrorPayload:
    """DomainError/IntegrationError를 클라이언트에 노출할 에러 페이로드로 변환한다.

    스택트레이스는 서버 로그에만 남기고, 클라이언트에는 code/message만 전달한다.
    """
    code = error.code if isinstance(error, DomainError) else "integration_error"
    _LOG.error("mcp tool error: %s", code, exc_info=error)
    return {"error": True, "code": code, "message": str(error)}
