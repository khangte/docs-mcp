"""OpenAPI 엔드포인트(검색/상세/스키마/태그) 관련 MCP 도구 등록."""

from __future__ import annotations

import anyio
from fastmcp import FastMCP

from app.composition import AppState, ServiceBundle
from app.core.config import get_settings
from app.core.errors import DocumentNotFoundError
from app.core.logging import get_logger
from app.mcp.payloads import _to_endpoint_details_payload, _to_resolved_schema_payload, _to_tag_list_payload
from app.mcp.tools._common import _run_bundle, run_bundle_tool
from app.mcp.types import (
    EndpointCandidateItem,
    EndpointDetails,
    EndpointSearchResponse,
    ErrorPayload,
    MetadataSubmitResult,
    ResolvedSchemaResult,
    TagListResult,
)
from app.services.metadata.writeback_service import WritebackResult
from app.services.search.endpoint_candidate_search import CandidateSearchOptions

_LOG = get_logger("docs_mcp.mcp", level=get_settings().log_level)


def register_endpoint_tools(mcp: FastMCP, app_state: AppState) -> None:
    """엔드포인트 관련 MCP 도구(search/details/resolve_ref/list_tags)를 등록한다."""

    @mcp.tool()
    async def search_endpoints(
        query: str,
        top_k: int = 5,
        document_id: str | None = None,
        project: str | None = None,
        query_variants: list[str] | None = None,
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
            query_variants: query 와 같은 의미의 동의어·유사 표현 목록.
                query 가 영어가 아니면(한국어 등 비영문 질의) 영문 표현을
                반드시 변형으로 함께 제공한다 — 엔드포인트 문서는 전량
                영문이라 비영문 원본만으로는 키워드 arm(FTS)이 후보를 아예
                못 만들고 벡터 arm도 교차언어 비교라 약해진다. 키워드 arm
                후보 필터를 넓히는 동시에 벡터 arm에도 라우팅돼(원본과
                변형을 각각 임베딩해 히트 병합) 순위 계산에 반영된다. 결과
                0건 또는 부족 시 재질의할 때도 사용.

        Returns:
            items 키에 후보 리스트를 담은 dict. 각 후보는 endpoint_id, method,
            path, summary, match_type("keyword", "vector", "both" 또는
            "exact") 필드를 갖는다. "both"는 키워드·벡터 양쪽에서 모두
            매칭된 후보다. "exact"는 query 가 "GET /pet/{petId}" 같은
            method+path 또는 operationId 와 정확히 일치해 확정적으로 1위에
            놓인 후보다. 매칭이 없으면 items 는 빈 리스트다. document_id가
            등록되지 않았거나 project 와 불일치하면(빈 결과와 구분해)
            code="document_not_found" 에러 페이로드를 반환한다.
        """
        def _inner(bundle: ServiceBundle) -> EndpointSearchResponse:
            options = CandidateSearchOptions(
                top_k=top_k,
                document_id=document_id,
                project=project,
                query_variants=query_variants,
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
        return await run_bundle_tool(app_state, _inner)

    @mcp.tool()
    async def get_endpoint_details(
        endpoint_id: str,
        include_example: bool = False,
    ) -> EndpointDetails | ErrorPayload:
        """특정 엔드포인트의 상세 정보(파라미터·요청/응답 스펙)를 조회한다.

        schema_ref 는 참조 문자열 그대로 반환하며 스키마 본문을 펼치지 않는다.
        스키마 필드가 필요하면 resolve_ref 도구로 따로 조회한다.

        응답에는 다음 순회 후보도 함께 실린다(서버가 자동으로 다음 홉을
        호출하지는 않는다 — 밟을지는 호출측 판단):
        referenced_schema_refs(이 엔드포인트가 참조하는 스키마 ref 모음,
        resolve_ref 로 펼칠 후보), related_endpoints(같은 문서에서 태그 또는
        경로 접두사를 공유하는 다른 엔드포인트, get_endpoint_details 로
        이어서 조회할 후보).

        Args:
            endpoint_id: search_endpoints 등에서 얻은 엔드포인트 식별자.
            include_example: True 일 때만 curl 호출 예시(example_code)를
                생성해 포함한다. 기본값 False 에서는 응답에 example_code 키가
                아예 없다.

        Returns:
            endpoint_id, document_id, method, path, summary, description, tags,
            parameters, request_body, responses, referenced_schema_refs,
            related_endpoints 필드를 갖는 dict. include_example=True 이면
            example_code 가 추가된다. endpoint_id가 존재하지 않으면
            error/code/message 필드를 담은 ErrorPayload를 대신 반환한다.
            메타데이터가 없거나 스펙 변경으로 낡은 경우에만 metadata_request
            (reason/instruction)가 추가된다 — 지시문대로 만들어
            submit_endpoint_metadata 로 보내면 이후 검색 품질이 개선된다.
        """
        def _inner(bundle: ServiceBundle) -> EndpointDetails:
            result = bundle.endpoint_details_service.get_details(
                endpoint_id, include_example=include_example
            )
            payload = _to_endpoint_details_payload(result)
            # 56 §3.1: 상세를 본 뒤라야 근거 있는 메타데이터를 만들 수 있으므로
            # 기여 요청 힌트는 search_endpoints 가 아니라 여기에만 붙인다.
            hint = bundle.metadata_writeback_service.build_request_hint(endpoint_id)
            if hint is not None:
                payload["metadata_request"] = {
                    "reason": hint.reason,
                    "instruction": hint.instruction,
                }
            return payload
        return await run_bundle_tool(app_state, _inner)

    @mcp.tool()
    async def submit_endpoint_metadata(
        endpoint_id: str,
        business_description: str,
        keywords: list[str],
        user_phrases: list[str],
    ) -> MetadataSubmitResult | ErrorPayload:
        """엔드포인트 검색용 비즈니스 메타데이터를 서버에 저장한다.

        get_endpoint_details 응답에 metadata_request 가 실렸을 때, 그 상세
        정보를 근거로 만든 메타데이터를 되돌려주는 도구다. 저장된 값은 해당
        엔드포인트의 검색 청크에 즉시 반영돼 이후 검색 품질을 높인다.

        한 번 저장된 값은 스펙이 바뀌기 전까지 덮어쓰지 않는다(같은 내용을
        다시 보내면 status="already_current"). 잘못 저장된 값의 수정은
        운영자 경로로만 가능하므로, 상세에 없는 사실을 만들어 보내지 않는다.

        Args:
            endpoint_id: get_endpoint_details 로 상세를 확인한 엔드포인트 식별자.
            business_description: 이 엔드포인트가 하는 일을 설명하는 한국어
                1문장(최대 120자, 초과분은 서버가 절단).
            keywords: 검색에 걸릴 만한 영어/한국어 용어(최대 5개, 각 30자).
            user_phrases: 사용자가 실제로 쓸 법한 표현. 한국어 2개 + 영어 2개
                (최대 4개, 각 40자). summary 의 동사와 다른 표현을 최소 1개
                포함한다(cancel<->delete/remove, create<->add/register).

        Returns:
            status("stored" | "already_current" | "rejected"), endpoint_id,
            reindexed(즉시 색인 반영 여부), truncated(상한 초과로 절단됐는지)
            를 담은 dict. status 가 "stored" 가 아니면 reason 이 함께 온다.
            endpoint_id 가 없으면 code="endpoint_not_found", write-back 이
            서버 설정으로 꺼져 있으면 code="writeback_disabled" 에러
            페이로드를 반환한다.
        """
        def _inner(bundle: ServiceBundle) -> MetadataSubmitResult:
            result: WritebackResult = bundle.metadata_writeback_service.submit(
                endpoint_id=endpoint_id,
                business_description=business_description,
                keywords=keywords,
                user_phrases=user_phrases,
            )
            payload: MetadataSubmitResult = {
                "status": result.status,
                "endpoint_id": result.endpoint_id,
                "reindexed": result.reindexed,
                "truncated": result.truncated,
            }
            if result.reason is not None:
                payload["reason"] = result.reason
            return payload
        return await run_bundle_tool(app_state, _inner)

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
        def _inner(bundle: ServiceBundle) -> ResolvedSchemaResult:
            resolved = bundle.schema_ref_resolver.resolve(
                ref, document_id=document_id, project=project
            )
            return _to_resolved_schema_payload(resolved)
        return await run_bundle_tool(app_state, _inner)

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
        def _inner(bundle: ServiceBundle) -> TagListResult:
            summaries = bundle.tag_catalog_service.list_tags(
                document_id=document_id, project=project
            )
            return _to_tag_list_payload(summaries)
        return await run_bundle_tool(app_state, _inner)

    @mcp.resource("document://{document_id}/raw")
    async def get_raw_document(document_id: str) -> str:
        """등록된 문서 한 건의 원문을 반환한다.

        doc_type 과 무관하게 저장된 원문(OpenAPI JSON/YAML, markdown/csv, Drive/Notion
        동기화 본문 등)을 그대로 돌려준다. document_id 는 list_documents 또는
        search_endpoints 에서 얻는다.
        """
        def _inner(bundle: ServiceBundle) -> str:
            doc = bundle.document_repo.get(document_id)
            if not doc:
                raise DocumentNotFoundError(document_id)
            return doc.raw_text

        def _sync() -> str:
            try:
                return _run_bundle(app_state, _inner)
            except DocumentNotFoundError as e:
                _LOG.error("mcp resource error: document_not_found document_id=%s", document_id, exc_info=e)
                raise
        return await anyio.to_thread.run_sync(_sync)
