"""FastAPI Depends 용 공용 provider.

`main.create_app` 이 `app.state.app_state` 에 `AppState` 를 설정한다.
`get_services` 는 Request 에서 AppState 를 꺼내 ServiceBundle 생성기를 돌려준다.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request

from src.api.dependencies import AppState, ServiceBundle, build_services


def get_app_state(request: Request) -> AppState:
    """현재 FastAPI 요청에서 설정된 AppState 를 꺼내 반환한다."""
    state = getattr(request.app.state, "app_state", None)
    if state is None:
        raise RuntimeError("AppState is not configured on the application")
    return state


def get_services(request: Request) -> Iterator[ServiceBundle]:
    """요청 단위 ServiceBundle 생성기를 yield 한다."""
    state = get_app_state(request)
    yield from build_services(state)
