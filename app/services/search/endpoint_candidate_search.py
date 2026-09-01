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
from app.services.search.cross_encoder_reranker import (
    RERANK_WIDTH,
    CrossEncoderReranker,
    apply_slot_lock,
    rerank_document,
)
from app.services.search.endpoint_representation_search import EndpointRepresentationSearch
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

#: P2 arm-exclusive rescue quota 상한(`docs/architect-review/92` §6.3). legacy
#: F 건수에 맞춰 늘리면 RRF `k` 튜닝식 과적합이 되므로 하드 상한을 둔다.
_MAX_ARM_RESCUE_QUOTA = 3


def _coerce_arm_rescue_quota(raw: str | int) -> int:
    """원시 설정값을 [0, _MAX_ARM_RESCUE_QUOTA] 정수로 좁힌다(미인식 값은 0=비활성)."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(value, _MAX_ARM_RESCUE_QUOTA))


def _lock_both_slots(
    legacy_base_wide: list[FusedResult],
    tentative_wide: list[FusedResult],
    top_k: int,
) -> list[FusedResult]:
    """§3.3 both-arm slot 보존: legacy base final 의 `both` 후보를 원 slot 에 HARD lock 한다.

    `apply_slot_lock`(P3, score 기준)의 order 기준 쌍둥이다.

    1. `legacy_base_wide[:top_k]` 안 `match_type == "both"` ref 의 0-based slot 을 lock.
    2. 나머지 slot 만 `tentative_wide`(3-arm RRF 점수 내림차순, 동점 ref_id 오름차순 —
       `reciprocal_rank_fuse` 가 이미 결정적으로 정렬) 순서로 앞에서부터 채우되
       locked ref 는 건너뛴다.

    locked 후보의 id·slot·상대 순서는 불변이다(both-arm slot preservation HARD gate).
    tentative pool 이 부족하면 뒤 slot 은 비워 둔 채(= 결과 길이가 top_k 미만) 반환한다.
    """
    if top_k <= 0:
        return []
    locked = {
        slot: f
        for slot, f in enumerate(legacy_base_wide[:top_k])
        if f.match_type == "both"
    }
    locked_ids = {f.ref_id for f in locked.values()}

    result: list[FusedResult | None] = [None] * top_k
    for slot, f in locked.items():
        result[slot] = f
    fill = (f for f in tentative_wide if f.ref_id not in locked_ids)
    for slot in range(top_k):
        if result[slot] is None:
            result[slot] = next(fill, None)
    return [f for f in result if f is not None]


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
        arm_rescue_quota: str | int = 0,
        cross_encoder_reranker: CrossEncoderReranker | None = None,
        endpoint_representation_search: EndpointRepresentationSearch | None = None,
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
            arm_rescue_quota: RRF 융합 후 final top-k 컷 밖의 arm-exclusive(단일
                arm) 후보를 끌어올리는 사전 고정 quota(`docs/architect-review/92`
                §6, P2). 0(기본)이면 완전 비활성 — `base_wide[:top_k]` 와 동일하다.
                원시 env 문자열/정수를 받아 [0, _MAX_ARM_RESCUE_QUOTA] 로 좁히고
                미인식 값은 0 으로 degrade한다.
            cross_encoder_reranker: P3 local cross-encoder(`docs/architect-review/96`).
                None(기본)이면 rerank 단계가 통째로 실행되지 않아 exact/RRF/fallback
                결과·순서가 baseline 과 byte-identical 이다. 주어지면 RRF `base_wide`
                상위 N 을 재점수하되 `both` 후보는 원 slot 에 HARD lock 한다.
                `score_pairs` 가 튀거나 개수가 안 맞으면 baseline 순서로 fail-closed.
                P3 ON 이면 P2 arm_rescue_quota 는 0 으로 강제된다(설계 96 §1/§8.1 —
                단독 candidate 경계에서만 검증하므로 두 단계를 겹치지 않는다).
            endpoint_representation_search: 결정적 endpoint 표현형 arm
                (`docs/architect-review/101`). None(기본)이면 `_search_rrf` 가
                기존 keyword+vector 2-arm RRF 경로를 그대로 타 baseline 과
                byte-identical 이다(projection repository lookup·추가 임베딩
                호출 없음). 주어지면 rrf 전략에서만 세 번째 RRF list 로 편입되고,
                legacy keyword+vector-only base final 안의 `both` 후보는 원 slot 에
                HARD lock 된다(§3.3). fallback 전략은 이 arm 을 호출하지 않는다.
                P2/P3 와의 동시 설정은 composition 이 invalid configuration 으로
                fail-closed 하므로 여기서는 세 옵션이 동시에 활성일 수 없다.
        """
        self._chunk_repo = chunk_repo
        self._endpoint_repo = endpoint_repo
        self._keyword_search = keyword_search
        self._vector_search = vector_search
        self._vector_fallback_enabled = vector_fallback_enabled
        self._document_repo = document_repo
        self._search_strategy = search_strategy
        self._arm_rescue_quota = _coerce_arm_rescue_quota(arm_rescue_quota)
        self._cross_encoder_reranker = cross_encoder_reranker
        self._endpoint_representation_search = endpoint_representation_search
        if cross_encoder_reranker is not None and self._arm_rescue_quota > 0:
            # 설계 96 §1/§8.1: P3 는 P2 arm-rescue quota=0 을 전제로 단독 candidate
            # 경계를 검증한다. P3 ON 에서 두 단계를 겹쳐 실행하지 않는다.
            _LOG.warning(
                "P3 rerank ON — P2 arm_rescue_quota(%d) 를 0 으로 강제(설계 96 §1)",
                self._arm_rescue_quota,
            )
            self._arm_rescue_quota = 0

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

        if self._endpoint_representation_search is not None:
            fused = self._compose_with_representation_arm(
                query, keyword_ref_ids, vector_ref_ids, width, top_k, document_id, project
            )
            return self._to_candidates_from_fused(fused)

        base_wide = reciprocal_rank_fuse(keyword_ref_ids, vector_ref_ids, top_k=width)
        fused = self._apply_arm_rescue(list(base_wide), top_k)
        fused = self._apply_cross_encoder_rerank(query, list(base_wide), fused, top_k)
        return self._to_candidates_from_fused(fused)

    def _compose_with_representation_arm(
        self,
        query: str,
        keyword_ref_ids: list[str],
        vector_ref_ids: list[str],
        width: int,
        top_k: int,
        document_id: str | None,
        project: str | None,
    ) -> list[FusedResult]:
        """feature ON: `endpoint_repr` 를 세 번째 RRF list 로 넣고 both-slot lock 을 적용한다(§3.3).

        같은 query 로 legacy keyword+vector-only RRF `base_wide` 를 함께 계산해
        `base_wide[:top_k]` 안의 모든 `match_type == "both"` endpoint 를 relative
        slot 에 HARD lock 하고, 나머지 slot 만 tentative 3-arm wide 순서로 채운다.
        `top_k` 은 exact 후보 수를 뺀 `remaining_top_k` 이라 exact-relative 처리는
        이미 반영돼 있다.
        """
        assert self._endpoint_representation_search is not None
        repr_result = self._endpoint_representation_search.search(
            query, document_id=document_id, project=project
        )
        tentative_wide = reciprocal_rank_fuse(
            keyword_ref_ids,
            vector_ref_ids,
            top_k=width,
            title_ref_ids=repr_result.ordered_endpoint_ids,
        )
        legacy_base_wide = reciprocal_rank_fuse(
            keyword_ref_ids, vector_ref_ids, top_k=width
        )
        return _lock_both_slots(legacy_base_wide, tentative_wide, top_k)

    def _apply_cross_encoder_rerank(
        self,
        query: str,
        base_wide: list[FusedResult],
        fallback: list[FusedResult],
        top_k: int,
    ) -> list[FusedResult]:
        """P3: RRF `base_wide` 상위 N 을 재점수하고 `both` slot lock 을 적용한다(`docs/96`).

        `cross_encoder_reranker` 가 없으면(flag off) `fallback`(= baseline 순서)을 그대로
        돌려준다 — rerank 코드가 실행되지 않는다. 모델 asset 부재·inference 실패·score
        개수 불일치·후보 endpoint 미조회는 전부 `fallback` 으로 fail-closed 하며 관측
        로그를 남긴다(이 상태는 승급 평가에서 PASS 가 될 수 없다, §2.1).
        """
        reranker = self._cross_encoder_reranker
        if reranker is None:
            return fallback
        n = min(RERANK_WIDTH, len(base_wide))
        if n == 0 or top_k <= 0:
            return fallback

        pool = base_wide[:n]
        try:
            endpoints = self._endpoint_repo.get_many([f.ref_id for f in pool])
            documents: list[str] = []
            for f in pool:
                endpoint = endpoints.get(f.ref_id)
                if endpoint is None:
                    _LOG.warning(
                        "P3 rerank: 후보 endpoint 미조회(%s) — baseline degrade", f.ref_id
                    )
                    return fallback
                documents.append(rerank_document(endpoint))
            scores = reranker.score_pairs(query, documents)
        except Exception:  # DB 조회·직렬화·모델 실패 전부 baseline fail-closed
            _LOG.warning("P3 cross-encoder rerank 실패 — baseline 순서 degrade", exc_info=True)
            return fallback
        if len(scores) != len(pool):
            _LOG.warning(
                "P3 rerank: score 개수 불일치(%d != %d) — baseline 순서 degrade",
                len(scores),
                len(pool),
            )
            return fallback

        scores_by_ref = {f.ref_id: score for f, score in zip(pool, scores, strict=True)}
        return apply_slot_lock(base_wide, top_k, scores_by_ref)

    def _apply_arm_rescue(
        self, base_wide: list[FusedResult], top_k: int
    ) -> list[FusedResult]:
        """RRF 컷 밖의 arm-exclusive(단일 arm) 후보를 사전 고정 quota 만큼 final 로 끌어올린다.

        `docs/architect-review/92` §6 P2. `arm_rescue_quota == 0`(기본)이면
        `base_wide[:top_k]` 와 완전히 동일하다 — 롤아웃 스위치.

        불변식:
        - arm 순위·RRF 순위·RRF 점수는 건드리지 않는다. base-wide 를 재정렬하지
          않고, final top-k 의 **tail 슬롯만** 교체한다.
        - 선택 신호는 `match_type`(single-arm 여부)과 기존 RRF 등수뿐이다.
          route-family·path 길이·structured score 를 쓰지 않는다.
        - rescue 대상은 base-wide 순서(=RRF 점수 내림차순) 그대로 앞에서부터 최대
          quota 건. 결정적이다.
        - 최소 1건의 순수 RRF 히트는 남긴다(`top_k` 가 작을수록 슬롯 하나의 영향이
          커진다는 §6.3 리스크 완화).
        - exact 후보는 `search()` 에서 별도 처리돼 `top_k`(=remaining_top_k)에 이미
          반영되므로 이 경로에서 보호가 필요 없다.
        """
        keep = base_wide[:top_k]
        if self._arm_rescue_quota <= 0 or len(base_wide) <= top_k:
            return keep
        rescued = [f for f in base_wide[top_k:] if f.match_type != "both"][
            : self._arm_rescue_quota
        ]
        n = min(len(rescued), max(top_k - 1, 0))
        if n <= 0:
            return keep
        return keep[: top_k - n] + rescued[:n]

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
