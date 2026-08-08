"""엔드포인트 후보 검색 (키워드 우선 + 벡터 보조).

`search_endpoints` MCP 도구 전용 검색 경로다. 기존 `SearchService`(하이브리드
가중합)와 달리 다음 원칙을 따른다.

1. 항상 키워드/구조적 매칭을 먼저 수행한다.
2. 키워드 결과가 **정확히 0건일 때만** 벡터 검색을 보조로 시도한다.
   점수 임계값은 두지 않는다(SPEC Phase 0 결정 6번).
3. 벡터 보조가 비활성(임베딩 API 키 미설정)이면 그 단계를 조용히 생략한다.
4. 상세 정보(파라미터·응답·스니펫)는 반환하지 않는다. 후보 식별에 필요한
   최소 필드만 돌려주고, 상세는 `get_endpoint_details` 가 담당한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.models.openapi import ApiChunk
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.services.project_scope import resolve_document_scope
from app.services.search.keyword_search import KeywordSearch
from app.services.search.vector_search import VectorSearch

_LOG = get_logger("docs_mcp.search.candidate")

MatchType = Literal["keyword", "vector"]

MIN_TOP_K = 1
MAX_TOP_K = 50


@dataclass(frozen=True)
class EndpointCandidate:
    """검색 후보 한 건(상세 정보 없이 식별에 필요한 최소 필드만 담는다)."""

    endpoint_id: str
    method: str
    path: str
    summary: str
    match_type: MatchType


@dataclass(frozen=True)
class CandidateSearchOptions:
    """후보 검색 옵션(반환 개수 상한과 문서 범위 제한)."""

    top_k: int = 5
    document_id: str | None = None
    project: str | None = None


class EndpointCandidateSearch:
    """키워드 우선·벡터 보조 전략으로 엔드포인트 후보만 반환하는 검색 서비스."""

    def __init__(
        self,
        chunk_repo: ChunkRepository,
        endpoint_repo: EndpointRepository,
        keyword_search: KeywordSearch,
        vector_search: VectorSearch,
        vector_fallback_enabled: bool = True,
        document_repo: DocumentRepository | None = None,
    ) -> None:
        """저장소·검색기와 벡터 보조 활성화 여부를 보관한다.

        Args:
            chunk_repo: 후보 청크 조회용 저장소.
            endpoint_repo: 청크 ref_id → 엔드포인트 조회용 저장소.
            keyword_search: 1차 키워드 검색기.
            vector_search: 키워드 0건일 때만 쓰는 보조 벡터 검색기.
            vector_fallback_enabled: False 면 벡터 보조 단계를 통째로 생략한다.
            document_repo: document_id 존재 검증용 저장소. 주입하면 미등록
                문서 ID 를 빈 결과가 아니라 DocumentNotFoundError 로 구분한다.
        """
        self._chunk_repo = chunk_repo
        self._endpoint_repo = endpoint_repo
        self._keyword_search = keyword_search
        self._vector_search = vector_search
        self._vector_fallback_enabled = vector_fallback_enabled
        self._document_repo = document_repo

    def search(self, query: str, options: CandidateSearchOptions) -> list[EndpointCandidate]:
        """질의에 대한 엔드포인트 후보 목록을 반환한다.

        키워드 결과가 1건 이상이면 벡터 검색기(=임베딩 API)를 호출하지 않는다.

        Args:
            query: 검색할 자연어/키워드 질의.
            options: top_k·document_id 옵션.

        Returns:
            `match_type` 이 표시된 후보 리스트. 매칭이 없으면 빈 리스트.

        Raises:
            ValidationError: 질의가 비었거나 top_k 가 허용 범위를 벗어난 경우.
            DocumentNotFoundError: document_id 가 등록되지 않은 문서인 경우.
        """
        normalized_query, document_id, project = self._validate(query, options)

        if not self._chunk_repo.has_endpoint_chunks(document_id=document_id, project=project):
            return []

        keyword_candidates = self._search_by_keyword(
            normalized_query, options.top_k, document_id, project
        )
        if keyword_candidates:
            return keyword_candidates

        return self._search_by_vector(normalized_query, options.top_k, document_id, project)

    def _validate(
        self, query: str, options: CandidateSearchOptions
    ) -> tuple[str, str | None, str | None]:
        """질의·top_k 를 검증하고, document_id/project 범위를 확정한다.

        미등록 document_id 를 빈 결과로 흘려보내면 호출 LLM 이 "문서가 없음"과
        "결과가 없음"을 구분할 수 없으므로 명시적으로 오류를 낸다.
        `document_repo` 가 주입되지 않았으면(테스트 등) 범위 검증을 생략하고
        옵션 값을 그대로 통과시킨다.
        """
        normalized_query = (query or "").strip()
        if not normalized_query:
            raise ValidationError("query must not be empty")
        if not MIN_TOP_K <= options.top_k <= MAX_TOP_K:
            raise ValidationError(
                f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}: {options.top_k}"
            )
        if self._document_repo is None:
            return normalized_query, options.document_id, options.project
        document_id, project = resolve_document_scope(
            self._document_repo, options.document_id, options.project
        )
        return normalized_query, document_id, project

    def _endpoint_chunks(
        self, document_id: str | None, project: str | None
    ) -> list[ApiChunk]:
        """검색 대상이 되는 endpoint 타입 청크만 SQL 필터로 조회한다.

        벡터 보조(`_search_by_vector`)가 후보 스코프를 만들 때만 쓴다 —
        키워드 검색은 `search_endpoint_by_text` 로 완전히 SQL 화되어
        이 메서드를 거치지 않는다.
        """
        return list(
            self._chunk_repo.list_endpoint_chunks(document_id=document_id, project=project)
        )

    def _search_by_keyword(
        self, query: str, top_k: int, document_id: str | None, project: str | None
    ) -> list[EndpointCandidate]:
        """Postgres FTS 로 1차 키워드 검색을 수행해 후보를 만든다."""
        hits = self._keyword_search.search(
            query, top_k=top_k, document_id=document_id, project=project
        )
        ordered_ref_ids = [h.ref_id for h in hits]
        return self._to_candidates(ordered_ref_ids, "keyword", top_k)

    def _search_by_vector(
        self, query: str, top_k: int, document_id: str | None, project: str | None
    ) -> list[EndpointCandidate]:
        """키워드 0건일 때만 호출되는 벡터 보조 검색.

        벡터 보조가 비활성이면 임베딩 호출 없이 빈 리스트를 반환한다.
        """
        if not self._vector_fallback_enabled:
            _LOG.debug("벡터 보조 검색 생략: 임베딩 API 키 미설정")
            return []

        chunks = self._endpoint_chunks(document_id, project)
        candidate_ids = {c.id for c in chunks}
        hits = self._vector_search.search(query, top_k=top_k, candidates=candidate_ids)
        chunk_by_id = {c.id: c for c in chunks}
        ordered_ref_ids = [
            chunk_by_id[h.chunk_id].ref_id
            for h in hits
            if h.chunk_id in chunk_by_id and h.score > 0.0
        ]
        return self._to_candidates(ordered_ref_ids, "vector", top_k)

    def _to_candidates(
        self, ordered_ref_ids: list[str], match_type: MatchType, top_k: int
    ) -> list[EndpointCandidate]:
        """엔드포인트 ID 순서를 유지한 채 후보 DTO 로 변환한다(중복·유실 제거)."""
        candidates: list[EndpointCandidate] = []
        seen: set[str] = set()
        for endpoint_id in ordered_ref_ids:
            if endpoint_id in seen:
                continue
            endpoint = self._endpoint_repo.get(endpoint_id)
            if endpoint is None:
                _LOG.warning("청크가 참조하는 엔드포인트를 찾을 수 없음: %s", endpoint_id)
                continue
            seen.add(endpoint_id)
            candidates.append(
                EndpointCandidate(
                    endpoint_id=endpoint.id,
                    method=endpoint.method,
                    path=endpoint.path,
                    summary=endpoint.summary,
                    match_type=match_type,
                )
            )
            if len(candidates) >= top_k:
                break
        return candidates
