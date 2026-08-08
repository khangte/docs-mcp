"""OpenAPI 엔드포인트(검색/상세/스키마/태그) 관련 MCP 도구 등록."""

from __future__ import annotations

import anyio
from fastmcp import FastMCP

from app.composition import AppState, ServiceBundle
from app.core.db import managed_session
from app.core.errors import DocumentNotFoundError, DomainError, IntegrationError
from app.mcp.payloads import _to_endpoint_details_payload, _to_resolved_schema_payload, _to_tag_list_payload
from app.mcp.tools._common import _run_bundle, to_error_payload
from app.mcp.types import (
    EndpointCandidateItem,
    EndpointDetails,
    EndpointSearchResponse,
    ErrorPayload,
    ResolvedSchemaResult,
    TagListResult,
)
from app.repositories.document_repository import DocumentRepository
from app.services.search.endpoint_candidate_search import CandidateSearchOptions


def register_endpoint_tools(mcp: FastMCP, app_state: AppState) -> None:
    """엔드포인트 관련 MCP 도구(search/details/resolve_ref/list_tags)를 등록한다."""
    session_factory = app_state.session_factory

    @mcp.tool()
    async def search_endpoints(
        query: str,
        top_k: int = 5,
        document_id: str | None = None,
        project: str | None = None,
    ) -> EndpointSearchResponse | ErrorPayload:
        """자연어/키워드로 API 엔드포인트 후보를 가볍게 검색한다.

        키워드(FTS)와 벡터(임베딩) 검색을 항상 함께 수행해 RRF로 순위를
        융합한다. 상세 정보는 포함하지 않으므로, 후보를 고른 뒤
        get_endpoint_details 로 상세를 조회한다.

        Args:
            query: 검색할 자연어 또는 키워드 질의.
            top_k: 반환할 최대 후보 수(1~50).
            document_id: 특정 문서로 검색 범위를 제한하고 싶을 때 지정.
            project: 특정 프로젝트로 검색 범위를 제한하고 싶을 때 지정.
                document_id 와 함께 오면 document_id 가 우선하되, 그 문서가
                해당 project 소속이 아니면 document_not_found 오류가 된다.

        Returns:
            items 키에 후보 리스트를 담은 dict. 각 후보는 endpoint_id, method,
            path, summary, match_type("keyword", "vector" 또는 "both") 필드를
            갖는다. "both"는 키워드·벡터 양쪽에서 모두 매칭된 후보다.
            매칭이 없으면 items 는 빈 리스트다. document_id가 등록되지 않았거나
            project 와 불일치하면(빈 결과와 구분해) code="document_not_found"
            에러 페이로드를 반환한다.
        """
        def _sync() -> EndpointSearchResponse | ErrorPayload:
            def _inner(bundle: ServiceBundle) -> EndpointSearchResponse:
                options = CandidateSearchOptions(
                    top_k=top_k, document_id=document_id, project=project
                )
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
            def _inner(bundle: ServiceBundle) -> EndpointDetails:
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
        project: str | None = None,
    ) -> ResolvedSchemaResult | ErrorPayload:
        """`$ref` 로 표기된 컴포넌트 스키마를 실제 필드 목록으로 펼친다.

        중첩 `$ref` 는 재귀적으로 펼치지 않고 참조 이름만 type 에 표기한다.
        더 깊이 필요하면 그 이름으로 resolve_ref 를 다시 호출한다.

        Args:
            ref: `#/components/schemas/Product` 형태의 로컬 참조 문자열.
            document_id: 특정 문서의 스키마로 한정하고 싶을 때 지정. 생략하면
                등록된 문서 전체에서 같은 이름의 스키마를 찾으며, 여러 문서에
                동명 스키마가 있으면 **가장 최근 등록 문서**가 선택된다.
                모호성을 없애려면 document_id 지정을 권장한다.
            project: 특정 프로젝트로 한정하고 싶을 때 지정. document_id 와
                함께 오면 document_id 가 우선한다. 여러 프로젝트에 동명
                스키마가 있을 때 다른 프로젝트 스키마가 섞이지 않게 한다.

        Returns:
            name(스키마 이름), document_id(스키마가 속한 문서),
            fields(name/type/required/description 목록)를 담은 dict.
            참조 형식이 잘못됐거나, document_id가 미등록이거나 project 와
            불일치하거나, 해당 스키마가 없으면 error/code/message 필드를 담은
            ErrorPayload를 대신 반환한다.
        """
        def _sync() -> ResolvedSchemaResult | ErrorPayload:
            def _inner(bundle: ServiceBundle) -> ResolvedSchemaResult:
                resolved = bundle.schema_ref_resolver.resolve(
                    ref, document_id=document_id, project=project
                )
                return _to_resolved_schema_payload(resolved)
            try:
                return _run_bundle(app_state, _inner)
            except (DomainError, IntegrationError) as e:
                return to_error_payload(e)
        return await anyio.to_thread.run_sync(_sync)

    @mcp.tool()
    async def list_tags(
        document_id: str | None = None,
        project: str | None = None,
    ) -> TagListResult | ErrorPayload:
        """등록된 문서의 태그 목록과 태그별 엔드포인트 수를 반환한다.

        search_endpoints 로 검색하기 전에 어떤 기능 영역이 있는지 훑어보는
        탐색 보조 도구다.

        Args:
            document_id: 특정 문서의 태그만 보고 싶을 때 지정. 생략하면 전체.
            project: 특정 프로젝트의 태그만 보고 싶을 때 지정. document_id 와
                함께 오면 document_id 가 우선한다.

        Returns:
            tags 키에 name/endpoint_count 를 갖는 항목 리스트를 담은 dict.
            엔드포인트 수 내림차순으로 정렬되며, 태그가 없으면 빈 리스트다.
            document_id가 존재하지 않거나 project 와 불일치하면
            error/code/message 필드를 담은 ErrorPayload를 대신 반환한다.
        """
        def _sync() -> TagListResult | ErrorPayload:
            def _inner(bundle: ServiceBundle) -> TagListResult:
                summaries = bundle.tag_catalog_service.list_tags(
                    document_id=document_id, project=project
                )
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
