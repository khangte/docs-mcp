"""FastAPI 앱 팩토리."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.dependencies import AppState, rebuild_vector_index
from src.api.routes import documents as documents_routes
from src.api.routes import endpoints as endpoints_routes
from src.api.routes import health as health_routes
from src.api.routes import query as query_routes
from src.api.routes import search as search_routes
from src.api.routes import sync as sync_routes
from src.core.config import Settings, get_settings
from src.core.db import create_db_engine
from src.core.errors import (
    APIError,
    DocumentNotFoundError,
    DomainError,
    DuplicateDocumentError,
    EndpointNotFoundError,
    IntegrationError,
    RepositoryError,
    ValidationError,
)
from src.core.logging import get_logger
from src.models.openapi import create_all
from src.services.ingestor.openapi_fetcher import HttpOpenAPIFetcher, OpenAPIFetcher

_LOG = get_logger("docs_mcp.api")


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable]
    ):
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
    return {"error": {"type": code, "message": message, "trace_id": trace_id}}


def create_app(
    settings: Settings | None = None,
    fetcher: OpenAPIFetcher | None = None,
    app_state: AppState | None = None,
) -> FastAPI:
    cfg = settings or get_settings()
    if app_state is None:
        engine = create_db_engine(cfg.database_url)
        create_all(engine)
        fetcher_impl: OpenAPIFetcher = fetcher or HttpOpenAPIFetcher()
        app_state = AppState.from_engine(
            engine=engine,
            fetcher=fetcher_impl,
            embedding_dim=cfg.embedding_dim,
            hybrid_alpha=cfg.hybrid_alpha,
        )
        rebuild_vector_index(app_state)

    app = FastAPI(title="docs-mcp OpenAPI RAG")
    app.state.app_state = app_state
    app.add_middleware(TraceIdMiddleware)

    app.include_router(health_routes.router)
    app.include_router(documents_routes.router)
    app.include_router(sync_routes.router)
    app.include_router(endpoints_routes.router)
    app.include_router(search_routes.router)
    app.include_router(query_routes.router)

    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=422,
            content=_error_payload("validation_error", str(exc.errors()), trace_id),
        )

    @app.exception_handler(ValidationError)
    async def on_domain_validation(request: Request, exc: ValidationError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=422,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

    @app.exception_handler(DocumentNotFoundError)
    async def on_doc_not_found(request: Request, exc: DocumentNotFoundError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=404,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

    @app.exception_handler(EndpointNotFoundError)
    async def on_ep_not_found(request: Request, exc: EndpointNotFoundError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=404,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

    @app.exception_handler(DuplicateDocumentError)
    async def on_dup_doc(request: Request, exc: DuplicateDocumentError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=409,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

    @app.exception_handler(DomainError)
    async def on_domain(request: Request, exc: DomainError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=400,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

    @app.exception_handler(IntegrationError)
    async def on_integration(request: Request, exc: IntegrationError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=502,
            content=_error_payload("integration_error", str(exc), trace_id),
        )

    @app.exception_handler(RepositoryError)
    async def on_repository(request: Request, exc: RepositoryError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=500,
            content=_error_payload("repository_error", str(exc), trace_id),
        )

    @app.exception_handler(APIError)
    async def on_api(request: Request, exc: APIError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, str(exc), trace_id),
        )


def get_default_app() -> FastAPI:
    """uvicorn --factory 로 사용하거나, 직접 import 에서 호출."""
    return create_app()

