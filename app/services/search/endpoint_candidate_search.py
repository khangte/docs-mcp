"""엔드포인트 후보 검색 (키워드+벡터 RRF 순위 융합, 롤백용 배타 전략 병존).

`search_endpoints` MCP 도구 전용 검색 경로다. 기존 `SearchService`(하이브리드
가중합)와 달리 다음 원칙을 따른다(`docs/architect-review/07_search_rrf_reevaluation.md` 5절).

1. **`rrf`(기본) 전략**: 키워드/벡터 두 ranker를 항상 병렬로(더 넓게) 실행해
   RRF(Reciprocal Rank Fusion)로 순위를 융합한다.
2. **`fallback` 전략(롤백 스위치)**: 항상 키워드를 먼저 수행하고, 결과가
   **정확히 0건일 때만** 벡터를 보조로 시도한다(옛 SPEC Phase 0 결정 6번 —
   이 전략에 한해 유효).
3. 벡터 arm 이 비활성(해시 임베딩 폴백 등 `is_semantic=False`)이면 두 전략
   모두 벡터 단계를 조용히 생략하고 키워드 단독 순위로 degrade한다.
4. 상세 정보(파라미터·응답·스니펫)는 반환하지 않는다. 후보 식별에 필요한
   최소 필드만 돌려주고, 상세는 `get_endpoint_details` 가 담당한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.services.project_scope import resolve_document_scope
from app.services.search.keyword_search import KeywordSearch
from app.services.search.rrf import FusedResult, MatchType, reciprocal_rank_fuse
from app.services.search.vector_search import VectorSearch

_LOG = get_logger("docs_mcp.search.candidate")

__all__ = [
    "CandidateSearchOptions",
    "EndpointCandidate",
    "EndpointCandidateSearch",
    "MatchType",
]

MIN_TOP_K = 1
MAX_TOP_K = 50

#: RRF 융합 전 각 ranker 에서 가져올 후보 폭
#: (`docs/architect-review/07_search_rrf_reevaluation.md` 5.3).
#: 정답이 한쪽 arm 의 상위에만 있어도 융합에서 건질 수 있도록 top_k 보다 넓게 본다.
_MIN_CANDIDATE_WIDTH = 50
_CANDIDATE_WIDTH_MULTIPLIER = 4

#: "GET /pet/{petId}" 형태의 method+path exact 질의를 잡아내는 패턴
#: (`docs/architect-review/37_user_rag_proposal_vs_our_design_diff.md` 5b).
_METHOD_PATH_RE = re.compile(
    r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE)\s+(/\S+)$", re.IGNORECASE
)


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
    #: 호출자(Claude)가 원본 질의와 함께 넘기는 동의어/유사 표현. 키워드
    #: arm(FTS OR 후보 필터)을 넓히는 동시에 벡터 arm에도 라우팅된다 — 각
    #: 변형을 원본과 별도로 임베딩해 히트를 등수 기준으로 병합한다
    #: (`docs/architect-review/29_search_quality_eval_real_corpus_results.md`
    #: §7.2. 교차언어 질의에서 벡터 arm이 유일 신호가 되는 사례가 실측돼,
    #: `docs/architect-review/12_rag_depth_directions.md` 후보4의 "벡터 arm은
    #: 손대지 않는다" 결정을 뒤집었다).
    query_variants: list[str] | None = None


class EndpointCandidateSearch:
    """RRF 순위 융합(기본) 또는 키워드 우선·벡터 보조(롤백)로 엔드포인트 후보만
    반환하는 검색 서비스."""

    def __init__(
        self,
        chunk_repo: ChunkRepository,
        endpoint_repo: EndpointRepository,
        keyword_search: KeywordSearch,
        vector_search: VectorSearch,
        vector_fallback_enabled: bool = True,
        document_repo: DocumentRepository | None = None,
        search_strategy: str = "rrf",
    ) -> None:
        """저장소·검색기와 벡터 보조 활성화 여부·검색 전략을 보관한다.

        Args:
            chunk_repo: 후보 청크 조회용 저장소.
            endpoint_repo: 청크 ref_id → 엔드포인트 조회용 저장소.
            keyword_search: 키워드 검색기.
            vector_search: 벡터 검색기.
            vector_fallback_enabled: False 면 벡터 단계를 통째로 생략한다
                (해시 임베딩 폴백 등 `is_semantic=False` 배포).
            document_repo: document_id 존재 검증용 저장소. 주입하면 미등록
                문서 ID 를 빈 결과가 아니라 DocumentNotFoundError 로 구분한다.
            search_strategy: "fallback"(키워드 우선, 0건일 때만 벡터 — 롤백
                스위치)이면 옛 배타 분기, 그 외 값(기본 "rrf" 포함)은 모두
                RRF 융합으로 처리한다. `Settings.search_strategy` 의 원시 env
                문자열을 그대로 받는다 — `embedding_backend` 등 이 코드베이스의
                다른 env 기반 설정과 동일하게 Literal 로 좁히지 않고 비교로
                분기해, 인식 못 하는 값은 안전하게 rrf 로 degrade한다.
        """
        self._chunk_repo = chunk_repo
        self._endpoint_repo = endpoint_repo
        self._keyword_search = keyword_search
        self._vector_search = vector_search
        self._vector_fallback_enabled = vector_fallback_enabled
        self._document_repo = document_repo
        self._search_strategy = search_strategy

    def search(self, query: str, options: CandidateSearchOptions) -> list[EndpointCandidate]:
        """질의에 대한 엔드포인트 후보 목록을 반환한다.

        `search_strategy="rrf"`(기본)면 키워드·벡터 두 ranker를 항상 실행해
        RRF로 융합한다. `search_strategy="fallback"`이면 키워드 결과가 1건
        이상일 때 벡터 검색기(=임베딩 API)를 호출하지 않는다.

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

        exact_candidates = self._search_exact(normalized_query, document_id, project)
        remaining_top_k = options.top_k - len(exact_candidates)
        if remaining_top_k <= 0:
            return exact_candidates[: options.top_k]

        if self._search_strategy == "fallback":
            rest = self._search_fallback(
                normalized_query, remaining_top_k, document_id, project, options.query_variants
            )
        else:
            rest = self._search_rrf(
                normalized_query,
                remaining_top_k,
                document_id,
                project,
                options.query_variants,
            )
        seen_ids = {c.endpoint_id for c in exact_candidates}
        return exact_candidates + [c for c in rest if c.endpoint_id not in seen_ids]

    def _search_exact(
        self, query: str, document_id: str | None, project: str | None
    ) -> list[EndpointCandidate]:
        """method+path 또는 operationId 가 질의와 정확히 일치하는 엔드포인트를 우선 반환한다.

        RRF는 등수 기반 융합이라 정확 일치라도 다른 신호와 섞여 확정적
        1위를 보장하지 못한다(`docs/architect-review/37` 5b) — 이 단계가
        그 결정적 lookup이다. `"GET /pet/{petId}"` 형태면 method+path로,
        아니면 질의 전체를 operationId 정확일치로 조회한다.
        """
        method_path = _METHOD_PATH_RE.match(query)
        if method_path:
            method, path = method_path.group(1).upper(), method_path.group(2)
            endpoints = self._endpoint_repo.list_by_method_path(method, path, document_id, project)
        else:
            endpoints = self._endpoint_repo.list_by_operation_id(query, document_id, project)
        return [
            EndpointCandidate(
                endpoint_id=e.id,
                method=e.method,
                path=e.path,
                summary=e.summary,
                match_type="exact",
            )
            for e in endpoints
        ]

    def _search_fallback(
        self,
        query: str,
        top_k: int,
        document_id: str | None,
        project: str | None,
        query_variants: list[str] | None,
    ) -> list[EndpointCandidate]:
        """키워드 우선·벡터는 0건일 때만(롤백 스위치, 옛 배타 분기 그대로)."""
        keyword_candidates = self._search_by_keyword(
            query, top_k, document_id, project, query_variants
        )
        if keyword_candidates:
            return keyword_candidates
        return self._search_by_vector(query, top_k, document_id, project)

    def _search_rrf(
        self,
        query: str,
        top_k: int,
        document_id: str | None,
        project: str | None,
        query_variants: list[str] | None,
    ) -> list[EndpointCandidate]:
        """키워드·벡터 두 ranker를 항상 병렬 실행해 RRF로 융합한다.

        RRF 는 `width`(top_k 보다 넓게)로 base-wide 를 만든 뒤 top_k 로 자른다
        (`docs/architect-review/07` 5.3). `query_variants` 는 keyword/vector
        arm 양쪽에 전달한다.
        """
        width = max(top_k * _CANDIDATE_WIDTH_MULTIPLIER, _MIN_CANDIDATE_WIDTH)

        keyword_hits = self._keyword_search.search(
            query,
            top_k=width,
            document_id=document_id,
            project=project,
            query_variants=query_variants,
        )
        keyword_ref_ids = [h.ref_id for h in keyword_hits]

        vector_hits: list[tuple[str, float]] = []
        if self._vector_fallback_enabled:
            candidate_ids = (
                self._chunk_repo.list_endpoint_chunk_ids(document_id=document_id, project=project)
                if document_id is not None or project is not None
                else None
            )
            vector_hits = self._search_vector_with_variants(
                query, query_variants, width, candidate_ids
            )
        else:
            _LOG.debug("벡터 arm 생략(rrf 전략, 키워드 단독 degrade): 임베딩 백엔드 비의미론적")
        vector_ref_ids = [ref_id for ref_id, _ in vector_hits]

        base_wide = reciprocal_rank_fuse(keyword_ref_ids, vector_ref_ids, top_k=width)
        return self._to_candidates_from_fused(list(base_wide[:top_k]))

    def _search_vector_with_variants(
        self,
        query: str,
        query_variants: list[str] | None,
        width: int,
        candidate_ids: set[str] | None,
    ) -> list[tuple[str, float]]:
        """원본 질의 + `query_variants` 를 각각 벡터 검색해 등수 최솟값으로 병합한다.

        교차언어(예: 한글 원본 + 영문 변형) 질의에서 벡터 arm이 원본만으로는
        약하고 변형(동일언어 비교)에서 강해지는 사례를 놓치지 않기 위해,
        어느 한 질의에서든 상위였던 후보를 살린다(§7.2). 반환은 `(ref_id,
        score)` 이고 정렬 키는 `(best_rank, ref_id)` 로 현행과 동일하다 —
        score 는 eval trace 비교용이며 RRF 계산·정렬에는 쓰지 않는다. 같은
        best rank 가 여러 variant 에서 나오면 큰 score 를 택한다.
        """
        best_rank: dict[str, int] = {}
        best_score: dict[str, float] = {}
        for candidate_query in [query, *(query_variants or [])]:
            hits = self._vector_search.search(
                candidate_query, top_k=width, candidates=candidate_ids
            )
            for rank, hit in enumerate(hits, start=1):
                if hit.score <= 0.0:
                    continue
                if hit.ref_id not in best_rank or rank < best_rank[hit.ref_id]:
                    best_rank[hit.ref_id] = rank
                    best_score[hit.ref_id] = hit.score
                elif rank == best_rank[hit.ref_id] and hit.score > best_score[hit.ref_id]:
                    best_score[hit.ref_id] = hit.score
        return [
            (ref_id, best_score[ref_id])
            for ref_id, _ in sorted(best_rank.items(), key=lambda item: (item[1], item[0]))
        ]

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

    def _search_by_keyword(
        self,
        query: str,
        top_k: int,
        document_id: str | None,
        project: str | None,
        query_variants: list[str] | None = None,
    ) -> list[EndpointCandidate]:
        """Postgres FTS 로 1차 키워드 검색을 수행해 후보를 만든다."""
        hits = self._keyword_search.search(
            query,
            top_k=top_k,
            document_id=document_id,
            project=project,
            query_variants=query_variants,
        )
        ordered_ref_ids = [h.ref_id for h in hits]
        return self._to_candidates(ordered_ref_ids, "keyword", top_k)

    def _search_by_vector(
        self, query: str, top_k: int, document_id: str | None, project: str | None
    ) -> list[EndpointCandidate]:
        """키워드 0건일 때만 호출되는 벡터 보조 검색(`fallback` 전략 전용).

        벡터 보조가 비활성이면 임베딩 호출 없이 빈 리스트를 반환한다.
        """
        if not self._vector_fallback_enabled:
            _LOG.debug("벡터 보조 검색 생략: 임베딩 API 키 미설정")
            return []

        candidate_ids = self._chunk_repo.list_endpoint_chunk_ids(
            document_id=document_id, project=project
        )
        hits = self._vector_search.search(query, top_k=top_k, candidates=candidate_ids)
        ordered_ref_ids = [h.ref_id for h in hits if h.score > 0.0]
        return self._to_candidates(ordered_ref_ids, "vector", top_k)

    def _to_candidates(
        self, ordered_ref_ids: list[str], match_type: MatchType, top_k: int
    ) -> list[EndpointCandidate]:
        """엔드포인트 ID 순서를 유지한 채 후보 DTO 로 변환한다(중복·유실 제거).

        결과당 `get()` 을 반복하던 N+1 을 `get_many()` 배치 조회 한 번으로
        대체한다(Q3).
        """
        endpoints = self._endpoint_repo.get_many(ordered_ref_ids)
        candidates: list[EndpointCandidate] = []
        seen: set[str] = set()
        for endpoint_id in ordered_ref_ids:
            if endpoint_id in seen:
                continue
            endpoint = endpoints.get(endpoint_id)
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

    def _to_candidates_from_fused(
        self, fused: list[FusedResult]
    ) -> list[EndpointCandidate]:
        """RRF 융합 결과(ref_id + match_type)를 후보 DTO 로 변환한다.

        결과당 `get()` 을 반복하던 N+1 을 `get_many()` 배치 조회 한 번으로
        대체한다(Q3).
        """
        endpoints = self._endpoint_repo.get_many([item.ref_id for item in fused])
        candidates: list[EndpointCandidate] = []
        for item in fused:
            endpoint = endpoints.get(item.ref_id)
            if endpoint is None:
                _LOG.warning("청크가 참조하는 엔드포인트를 찾을 수 없음: %s", item.ref_id)
                continue
            candidates.append(
                EndpointCandidate(
                    endpoint_id=endpoint.id,
                    method=endpoint.method,
                    path=endpoint.path,
                    summary=endpoint.summary,
                    match_type=item.match_type,
                )
            )
        return candidates
