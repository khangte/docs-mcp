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
    """앱에 예외 → HTTP 상태코드 매핑 핸들러를 일괄 등록한다."""

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI 요청 검증 오류를 422 응답으로 변환한다."""
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=422,
            content=_error_payload("validation_error", str(exc.errors()), trace_id),
        )

    @app.exception_handler(ValidationError)
    async def on_domain_validation(request: Request, exc: ValidationError) -> JSONResponse:
        """도메인 검증 오류를 422 응답으로 변환한다."""
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=422,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

    @app.exception_handler(DocumentNotFoundError)
    async def on_doc_not_found(request: Request, exc: DocumentNotFoundError) -> JSONResponse:
        """문서 미존재 오류를 404 응답으로 변환한다."""
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=404,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

    @app.exception_handler(EndpointNotFoundError)
    async def on_ep_not_found(request: Request, exc: EndpointNotFoundError) -> JSONResponse:
        """엔드포인트 미존재 오류를 404 응답으로 변환한다."""
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=404,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

    @app.exception_handler(DuplicateDocumentError)
    async def on_dup_doc(request: Request, exc: DuplicateDocumentError) -> JSONResponse:
        """문서 중복 등록 오류를 409 응답으로 변환한다."""
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=409,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

    @app.exception_handler(DomainError)
    async def on_domain(request: Request, exc: DomainError) -> JSONResponse:
        """그 밖의 도메인 오류를 400 응답으로 변환한다."""
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=400,
            content=_error_payload(exc.code, str(exc), trace_id),
        )

    @app.exception_handler(IntegrationError)
    async def on_integration(request: Request, exc: IntegrationError) -> JSONResponse:
        """외부 통합(HTTP/LLM 등) 실패를 502 응답으로 변환한다."""
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=502,
            content=_error_payload("integration_error", str(exc), trace_id),
        )

    @app.exception_handler(RepositoryError)
    async def on_repository(request: Request, exc: RepositoryError) -> JSONResponse:
        """저장소 오류를 500 응답으로 변환한다."""
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=500,
            content=_error_payload("repository_error", str(exc), trace_id),
        )

    @app.exception_handler(APIError)
    async def on_api(request: Request, exc: APIError) -> JSONResponse:
        """APIError 를 지정된 상태코드의 응답으로 변환한다."""
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, str(exc), trace_id),
        )


def get_default_app() -> FastAPI:
    """uvicorn --factory 로 사용하거나, 직접 import 에서 호출."""
    return create_app()


app = get_default_app()

