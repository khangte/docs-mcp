"""FastAPI 앱 팩토리."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.dependencies import AppState
from app.api.routes import documents as documents_routes
from app.api.routes import endpoints as endpoints_routes
from app.api.routes import health as health_routes
from app.api.routes import search as search_routes
from app.api.routes import sync as sync_routes
from app.bootstrap import bootstrap_app_state, seed_default_sources
from app.core.config import Settings, get_settings
from app.core.errors import (
    APIError,
    DocumentNotFoundError,
    DomainError,
    DuplicateDocumentError,
    EndpointNotFoundError,
    IntegrationError,
    RepositoryError,
    ValidationError,
)
from app.core.logging import get_logger
from app.services.ingestor.openapi_fetcher import HttpOpenAPIFetcher, OpenAPIFetcher

_LOG = get_logger("docs_mcp.api", level=get_settings().log_level)


class TraceIdMiddleware(BaseHTTPMiddleware):
    """요청마다 trace_id 를 부여하고 처리 시간을 로그로 남기는 미들웨어."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable]
    ):
        """trace_id 를 주입하고 요청 처리 결과/소요 시간을 로깅한 뒤 응답에 헤더를 추가한다."""
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:16]
        request.state.trace_id = trace_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = int((time.perf_counter() - start) * 1000)
            _LOG.error(
                "request failed",
                exc_info=True,
                extra={"trace_id": trace_id, "duration_ms": duration},
            )
            raise
        duration = int((time.perf_counter() - start) * 1000)
        _LOG.info(
            "request completed",
            extra={"trace_id": trace_id, "duration_ms": duration},
        )
        response.headers["X-Trace-Id"] = trace_id
        return response


def _error_payload(code: str, message: str, trace_id: str) -> dict[str, dict[str, str]]:
    """에러 응답 본문 구조를 일관된 형태로 만들어 반환한다."""
    return {"error": {"type": code, "message": message, "trace_id": trace_id}}


def create_app(
    settings: Settings | None = None,
    fetcher: OpenAPIFetcher | None = None,
    app_state: AppState | None = None,
) -> FastAPI:
    """설정·fetcher·app_state 를 받아 라우트와 예외 핸들러를 등록한 FastAPI 앱을 생성한다."""
    cfg = settings or get_settings()
    if app_state is None:
        if fetcher is not None:
            # 테스트 등 커스텀 fetcher 주입 경로
            from app.core.db import create_db_engine
            from app.models.openapi import create_all
            engine = create_db_engine(cfg.database_url)
            create_all(engine)
            seed_default_sources(engine, cfg)
            app_state = AppState.from_engine(
                engine=engine,
                fetcher=fetcher,
                embedding_dim=cfg.embedding_dim,
                hybrid_alpha=cfg.hybrid_alpha,
            )
        else:
            app_state = bootstrap_app_state(cfg)

    app = FastAPI(title="docs-mcp OpenAPI RAG")
    app.state.app_state = app_state
    app.add_middleware(TraceIdMiddleware)

    app.include_router(health_routes.router)
    app.include_router(documents_routes.router)
    app.include_router(sync_routes.router)
    app.include_router(endpoints_routes.router)
    app.include_router(search_routes.router)

    _register_exception_handlers(app)
    return app


_DOMAIN_ERROR_STATUS: list[tuple[type[Exception], int]] = [
    (RequestValidationError, 422),
    (ValidationError, 422),
    (DocumentNotFoundError, 404),
    (EndpointNotFoundError, 404),
    (DuplicateDocumentError, 409),
    (DomainError, 400),
    (IntegrationError, 502),
    (RepositoryError, 500),
]


def _register_exception_handlers(app: FastAPI) -> None:
    """앱에 예외 → HTTP 상태코드 매핑 핸들러를 일괄 등록한다."""

    def _make_handler(status: int):
        async def handler(request: Request, exc: Exception) -> JSONResponse:
            trace_id = getattr(request.state, "trace_id", "")
            code = getattr(exc, "code", type(exc).__name__.lower())
            if isinstance(exc, RequestValidationError):
                msg = str(exc.errors())
            else:
                msg = str(exc)
            return JSONResponse(status_code=status, content=_error_payload(code, msg, trace_id))
        return handler

    for exc_type, status in _DOMAIN_ERROR_STATUS:
        app.add_exception_handler(exc_type, _make_handler(status))

    @app.exception_handler(APIError)
    async def on_api(request: Request, exc: APIError) -> JSONResponse:
        """APIError 를 지정된 상태코드의 응답으로 변환한다."""
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

app = create_app()
