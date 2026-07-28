"""MCP (Model Context Protocol) 서버 진입점.

FastMCP를 사용하여 기존 RAG 및 검색 서비스를 Claude와 같은 LLM에게 도구로 제공한다.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from fastmcp import FastMCP

from app.api.dependencies import AppState, build_services
from app.bootstrap import bootstrap_app_state
from app.core.config import get_settings
from app.core.db import managed_session
from app.core.errors import (
    DocumentNotFoundError,
    DomainError,
    IntegrationError,
)
from app.core.logging import get_logger
from app.mcp_types import (
    Citation,
    DocumentSummary,
    EndpointCandidateItem,
    EndpointDetails,
    EndpointSearchResponse,
    ErrorPayload,
    ParameterItem,
    RagAnswer,
    RegisterDocumentResult,
    RequestBodyItem,
    ResolvedSchemaResult,
    ResponseItem,
    SchemaFieldItem,
    TagItem,
    TagListResult,
)
from app.repositories.document_repository import DocumentRepository
from app.services.endpoints.endpoint_details_service import EndpointDetailsResult
from app.services.schemas.schema_ref_resolver import ResolvedSchema
from app.services.search.endpoint_candidate_search import CandidateSearchOptions
from app.services.tags.tag_catalog_service import TagSummary

_LOG = get_logger("docs_mcp.mcp", level=get_settings().log_level)


def _run_bundle(app_state: AppState, fn):
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


def _to_endpoint_details_payload(result: EndpointDetailsResult) -> EndpointDetails:
    """엔드포인트 상세 결과를 MCP 응답 dict 로 변환한다.

    example_code 는 생성된 경우에만 키를 추가한다(include_example=False 이면
    키 자체가 존재하지 않아야 한다).
    """
    parameters: list[ParameterItem] = [
        {
            "name": p.name,
            "location": p.location,
            "required": p.required,
            "description": p.description,
            "schema": p.schema,
            "schema_ref": p.schema_ref,
        }
        for p in result.parameters
    ]
    request_body: RequestBodyItem | None = None
    if result.request_body is not None:
        request_body = {
            "content_type": result.request_body.content_type,
            "required": result.request_body.required,
            "schema": result.request_body.schema,
            "schema_ref": result.request_body.schema_ref,
        }
    responses: list[ResponseItem] = [
        {
            "status_code": r.status_code,
            "content_type": r.content_type,
            "description": r.description,
            "schema": r.schema,
            "schema_ref": r.schema_ref,
        }
        for r in result.responses
    ]
    payload: EndpointDetails = {
        "endpoint_id": result.endpoint_id,
        "document_id": result.document_id,
        "method": result.method,
        "path": result.path,
        "summary": result.summary,
        "description": result.description,
        "tags": result.tags,
        "parameters": parameters,
        "request_body": request_body,
        "responses": responses,
    }
    if result.example_code is not None:
        payload["example_code"] = result.example_code
    return payload


def _to_resolved_schema_payload(resolved: ResolvedSchema) -> ResolvedSchemaResult:
    """펼쳐진 스키마를 MCP 응답 dict 로 변환한다."""
    fields: list[SchemaFieldItem] = [
        {
            "name": f.name,
            "type": f.type,
            "required": f.required,
            "description": f.description,
        }
        for f in resolved.fields
    ]
    return {"name": resolved.name, "fields": fields}


def _to_tag_list_payload(summaries: list[TagSummary]) -> TagListResult:
    """태그 집계 결과를 MCP 응답 dict 로 변환한다."""
    tags: list[TagItem] = [
        {"name": s.name, "endpoint_count": s.endpoint_count} for s in summaries
    ]
    return {"tags": tags}


def create_mcp_server(app_state: AppState) -> FastMCP:
    """FastMCP 서버 인스턴스를 생성하고 도구들을 등록한다."""
    mcp = FastMCP("docs-mcp")
    session_factory = app_state.session_factory

    @mcp.tool()
    async def list_documents() -> list[DocumentSummary] | ErrorPayload:
        """등록된 모든 문서(OpenAPI/Markdown/CSV)의 요약 목록을 반환한다.

        Returns:
            각 원소가 document_id, title, version, doc_type, source_url,
            endpoints_count, indexed_at 필드를 갖는 리스트. 조회 중 도메인/외부
            연동 오류가 발생하면 error/code/message 필드를 담은 ErrorPayload를
            대신 반환한다.
        """
        def _sync() -> list[DocumentSummary] | ErrorPayload:
            try:
                with managed_session(session_factory) as session:
                    repo = DocumentRepository(session)
                    docs = repo.list_all()
                    return [
                        {
                            "document_id": d.id,
                            "title": d.title,
                            "version": d.version,
                            "doc_type": d.doc_type,
                            "source_url": d.source_url,
                            "endpoints_count": len(d.endpoints),
                            "indexed_at": d.indexed_at.isoformat() if d.indexed_at else None,
                        }
                        for d in docs
                    ]
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def register_document(
        source_url: str | None = None,
        raw_document: str | dict[str, Any] | None = None,
        title_override: str | None = None,
        doc_type: str | None = None,
    ) -> RegisterDocumentResult | ErrorPayload:
        """신규 문서를 등록한다. URL 또는 원문 중 하나를 제공해야 한다.

        Args:
            source_url: 문서를 가져올 URL. raw_document를 생략할 때 사용.
            raw_document: 문서 원문(JSON/YAML/Markdown/CSV 문자열 또는 dict).
                source_url을 생략할 때 사용.
            title_override: 문서 제목을 강제로 지정하고 싶을 때 사용, 생략 시
                문서 자체의 제목을 사용한다.
            doc_type: "openapi" | "markdown" | "csv" 중 하나, 생략 시 자동 판별한다.

        Returns:
            document_id, title, version, doc_type, endpoints_count,
            sections_count, chunks_count, status 필드를 갖는 dict. 이미 등록된
            문서이거나 파싱에 실패하는 등 도메인 오류가 발생하면
            error/code/message 필드를 담은 ErrorPayload를 대신 반환한다.
        """
        if isinstance(raw_document, dict):
            raw_document = json.dumps(raw_document)
        raw_doc_captured = raw_document

        def _sync() -> RegisterDocumentResult | ErrorPayload:
            def _inner(bundle):
                result = bundle.sync_service.register(
                    source_url=source_url,
                    raw_document=raw_doc_captured,
                    title_override=title_override,
                    doc_type=doc_type,
                )
                doc = result.document
                return {
                    "document_id": doc.id,
                    "title": doc.title,
                    "version": doc.version,
                    "doc_type": doc.doc_type,
                    "endpoints_count": result.endpoints_count,
                    "sections_count": result.sections_count,
                    "chunks_count": result.chunks_count,
                    "status": result.status,
                }
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def search_endpoints(
        query: str,
        top_k: int = 5,
        document_id: str | None = None,
    ) -> EndpointSearchResponse | ErrorPayload:
        """자연어/키워드로 API 엔드포인트 후보를 가볍게 검색한다.

        키워드·구조적 매칭(operationId/path/tag/summary)을 먼저 수행하고, 결과가
        0건일 때만 벡터 검색을 보조로 시도한다. 상세 정보는 포함하지 않으므로,
        후보를 고른 뒤 get_endpoint_details 로 상세를 조회한다.

        Args:
            query: 검색할 자연어 또는 키워드 질의.
            top_k: 반환할 최대 후보 수(1~50).
            document_id: 특정 문서로 검색 범위를 제한하고 싶을 때 지정.

        Returns:
            items 키에 후보 리스트를 담은 dict. 각 후보는 endpoint_id, method,
            path, summary, match_type("keyword" 또는 "vector") 필드를 갖는다.
            검색 중 도메인/외부 연동 오류가 발생하면 error/code/message 필드를
            담은 ErrorPayload를 대신 반환한다.
        """
        def _sync() -> EndpointSearchResponse | ErrorPayload:
            def _inner(bundle) -> EndpointSearchResponse:
                options = CandidateSearchOptions(top_k=top_k, document_id=document_id)
                candidates = bundle.candidate_search.search(query, options)
                items: list[EndpointCandidateItem] = [
                    {
                        "endpoint_id": c.endpoint_id,
                        "method": c.method,
                        "path": c.path,
                        "summary": c.summary,
                        "match_type": c.match_type,
                    }
                    for c in candidates
                ]
                return {"items": items}
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def get_endpoint_details(
        endpoint_id: str,
        include_example: bool = False,
    ) -> EndpointDetails | ErrorPayload:
        """특정 엔드포인트의 상세 정보(파라미터·요청/응답 스펙)를 조회한다.

        schema_ref 는 참조 문자열 그대로 반환하며 스키마 본문을 펼치지 않는다.
        스키마 필드가 필요하면 resolve_ref 도구로 따로 조회한다.

        Args:
            endpoint_id: search_endpoints 등에서 얻은 엔드포인트 식별자.
            include_example: True 일 때만 curl 호출 예시(example_code)를
                생성해 포함한다. 기본값 False 에서는 응답에 example_code 키가
                아예 없다.

        Returns:
            endpoint_id, document_id, method, path, summary, description, tags,
            parameters, request_body, responses 필드를 갖는 dict.
            include_example=True 이면 example_code 가 추가된다. endpoint_id가
            존재하지 않으면 error/code/message 필드를 담은 ErrorPayload를
            대신 반환한다.
        """
        def _sync() -> EndpointDetails | ErrorPayload:
            def _inner(bundle) -> EndpointDetails:
                result = bundle.endpoint_details_service.get_details(
                    endpoint_id, include_example=include_example
                )
                return _to_endpoint_details_payload(result)
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def resolve_ref(
        ref: str,
        document_id: str | None = None,
    ) -> ResolvedSchemaResult | ErrorPayload:
        """`$ref` 로 표기된 컴포넌트 스키마를 실제 필드 목록으로 펼친다.

        중첩 `$ref` 는 재귀적으로 펼치지 않고 참조 이름만 type 에 표기한다.
        더 깊이 필요하면 그 이름으로 resolve_ref 를 다시 호출한다.

        Args:
            ref: `#/components/schemas/Product` 형태의 로컬 참조 문자열.
            document_id: 특정 문서의 스키마로 한정하고 싶을 때 지정. 생략하면
                등록된 문서 전체에서 같은 이름의 스키마를 찾는다.

        Returns:
            name(스키마 이름)과 fields(name/type/required/description 목록)를
            담은 dict. 참조 형식이 잘못됐거나 해당 스키마가 없으면
            error/code/message 필드를 담은 ErrorPayload를 대신 반환한다.
        """
        def _sync() -> ResolvedSchemaResult | ErrorPayload:
            def _inner(bundle) -> ResolvedSchemaResult:
                resolved = bundle.schema_ref_resolver.resolve(ref, document_id=document_id)
                return _to_resolved_schema_payload(resolved)
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def list_tags(document_id: str | None = None) -> TagListResult | ErrorPayload:
        """등록된 문서의 태그 목록과 태그별 엔드포인트 수를 반환한다.

        search_endpoints 로 검색하기 전에 어떤 기능 영역이 있는지 훑어보는
        탐색 보조 도구다.

        Args:
            document_id: 특정 문서의 태그만 보고 싶을 때 지정. 생략하면 전체.

        Returns:
            tags 키에 name/endpoint_count 를 갖는 항목 리스트를 담은 dict.
            엔드포인트 수 내림차순으로 정렬되며, 태그가 없으면 빈 리스트다.
            document_id가 존재하지 않으면 error/code/message 필드를 담은
            ErrorPayload를 대신 반환한다.
        """
        def _sync() -> TagListResult | ErrorPayload:
            def _inner(bundle) -> TagListResult:
                summaries = bundle.tag_catalog_service.list_tags(document_id=document_id)
                return _to_tag_list_payload(summaries)
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.resource("document://{document_id}/raw")
    async def get_raw_document(document_id: str) -> str:
        """등록된 특정 OpenAPI 문서의 원문(JSON/YAML)을 반환한다."""
        def _sync() -> str:
            with managed_session(session_factory) as session:
                repo = DocumentRepository(session)
                doc = repo.get(document_id)
                if not doc:
                    raise DocumentNotFoundError(document_id)
                return doc.raw_text
        return await anyio.to_thread.run_sync(_sync)

    return mcp


# 미사용: query_rag 도구 제거로 호출부 없음. RAG 답변생성은 호출
# LLM(Claude/ChatGPT)이 담당. MCP 도구 등록(@mcp.tool())만 제거하고 구현은
# 보존한다(FastAPI `/query` 라우트는 계속 RAGService 를 직접 사용).
async def query_rag(
    app_state: AppState,
    question: str,
    top_k: int = 5,
    document_id: str | None = None,
) -> RagAnswer | ErrorPayload:
    """API 명세에 대해 자연어로 질문하고 RAG 기반의 답변을 받는다.

    미사용: MCP 도구로 등록하지 않는다(위 주석 참고).

    Args:
        app_state: 서비스 번들을 만들기 위한 앱 상태.
        question: API 명세에 대해 묻고 싶은 자연어 질문.
        top_k: 답변 생성에 근거로 사용할 최대 검색 결과 수.
        document_id: 특정 문서로 질의 범위를 제한하고 싶을 때 지정.

    Returns:
        answer(생성된 답변), citations(근거가 된 method/path/snippet 목록),
        is_grounded(답변이 실제 문서 근거에 기반했는지 여부)를 담은 dict.
        질의 처리 중 도메인/외부 연동 오류가 발생하면 error/code/message
        필드를 담은 ErrorPayload를 대신 반환한다.
    """
    def _sync() -> RagAnswer | ErrorPayload:
        def _inner(bundle) -> RagAnswer:
            result = bundle.rag_service.answer(
                question=question,
                top_k=top_k,
                document_id=document_id,
            )
            citations: list[Citation] = [
                {"method": c.method, "path": c.path, "snippet": c.snippet}
                for c in result.citations
            ]
            return {
                "answer": result.answer,
                "citations": citations,
                "is_grounded": result.is_grounded,
            }
        try:
            return _run_bundle(app_state, _inner)
        except (DomainError, IntegrationError) as e:
            return to_error_payload(e)
    return await anyio.to_thread.run_sync(_sync)


def main() -> None:
    """CLI 진입점."""
    from app.core.config import get_settings
    cfg = get_settings()
    app_state = bootstrap_app_state(cfg)
    mcp_server = create_mcp_server(app_state)
    mcp_server.run()


if __name__ == "__main__":
    main()
