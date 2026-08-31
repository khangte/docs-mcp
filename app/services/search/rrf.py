"""RRF(Reciprocal Rank Fusion) 순위 융합.

키워드(ts_rank)와 벡터(코사인 유사도)는 스케일이 서로 달라 점수를 직접 더하면
가중치가 왜곡된다. RRF 는 각 ranker 안에서의 **등수만** 사용하므로 스케일에
무관하게 두 신호를 자연스럽게 합칠 수 있다
(`docs/architect-review/07_search_rrf_reevaluation.md` 3·5절).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

MatchType = Literal["keyword", "vector", "both", "exact"]

#: 표준값(Cormack et al. 2009). 평가셋 없이 튜닝할 근거가 없어 상수로 고정하고
#: env 로 노출하지 않는다(`docs/architect-review/07_search_rrf_reevaluation.md` 5.2).
RRF_K = 60

#: `FusedResult.contributing_arms` 원소값(57번 리뷰 §5 개선1). 순서는 항상 이 나열 순서다.
ARM_TITLE = "title"
ARM_KEYWORD = "keyword"
ARM_VECTOR = "vector"


@dataclass(frozen=True)
class FusedResult:
    """융합 결과 한 건(엔드포인트 ref_id + RRF 점수 + 기여 arm 표기)."""

    ref_id: str
    score: float
    match_type: MatchType
    #: 실제로 점수에 기여한 arm 들, 항상 (title, keyword, vector) 순서로 채워진다.
    #: 기본값 `()` 은 기존 생성부(`FusedResult(ref_id=..., score=..., match_type=...)`)가
    #: 깨지지 않게 하기 위함이다.
    contributing_arms: tuple[str, ...] = ()


def _dedupe_first(ref_ids: Sequence[str]) -> list[str]:
    """같은 ref_id 가 여러 번 나오면 첫 등장만 남긴다(등수 계산 전 전처리)."""
    return list(dict.fromkeys(ref_ids))


def _arm_weight(weights: Mapping[str, float] | None, arm: str) -> float:
    """`weights` 가 None 이면 1.0, 아니면 지정 안 된 arm 도 1.0 으로 취급한다."""
    return 1.0 if weights is None else weights.get(arm, 1.0)


def reciprocal_rank_fuse(
    keyword_ref_ids: Sequence[str],
    vector_ref_ids: Sequence[str],
    *,
    top_k: int,
    k: int = RRF_K,
    title_ref_ids: Sequence[str] = (),
    weights: Mapping[str, float] | None = None,
) -> list[FusedResult]:
    """두/세 ranker 의 순위 리스트를 RRF 공식으로 융합해 top_k 로 자른다.

    `score(d) = Σ_arm w_arm · 1/(k + rank_arm(d))` — 해당 arm 에 후보가 없으면 그
    항은 0. 각 arm 내 중복 ref_id 는 첫 등장 등수만 채택한다. 동점이면
    ref_id 오름차순으로 정렬해 결정적 결과를 보장한다(골든 회귀 테스트 전제).

    `title_ref_ids` 는 문서 검색(doc36 Phase3)이 title 신호를 3번째 arm으로
    편입하기 위한 선택 인자다(`docs/architect-review/39` §2.1). 생략(기본
    빈 시퀀스)하면 점수·정렬에 전혀 관여하지 않아 기존 2-arm 호출부(엔드포인트
    검색)는 무변경이다. `match_type` 은 title 기여 여부와 무관하게 keyword/vector
    두 arm 만으로 계산한다 — title 단독 히트는 편의상 "vector" 로 표시되며,
    이 라벨은 문서 검색 계약에 노출되지 않으므로 무해하다. 정확한 arm별
    기여는 `FusedResult.contributing_arms` 를 보라.

    `weights` 는 arm 별 가중치(`ARM_TITLE`/`ARM_KEYWORD`/`ARM_VECTOR` 키,
    `weights.get(arm, 1.0)`)로 점수식의 `w_arm` 에만 적용된다(57번 리뷰 §5
    개선3). `weights=None`(기본값)이면 전 arm 1.0 — 기존 무가중 동작과
    완전히 같아 엔드포인트 검색(가중치를 넘기지 않는 호출부)은 무변경이다.
    가중치 0 은 그 arm 이 점수에 기여하지 않는다는 뜻일 뿐, `contributing_arms`
    (존재 여부로만 계산, 가중치와 무관)에서는 여전히 남는다.
    """
    keyword_ranks = {
        ref_id: rank for rank, ref_id in enumerate(_dedupe_first(keyword_ref_ids), start=1)
    }
    vector_ranks = {
        ref_id: rank for rank, ref_id in enumerate(_dedupe_first(vector_ref_ids), start=1)
    }
    title_ranks = {
        ref_id: rank for rank, ref_id in enumerate(_dedupe_first(title_ref_ids), start=1)
    }

    fused: list[FusedResult] = []
    for ref_id in keyword_ranks.keys() | vector_ranks.keys() | title_ranks.keys():
        in_keyword = ref_id in keyword_ranks
        in_vector = ref_id in vector_ranks
        in_title = ref_id in title_ranks
        score = 0.0
        if in_keyword:
            score += _arm_weight(weights, ARM_KEYWORD) / (k + keyword_ranks[ref_id])
        if in_vector:
            score += _arm_weight(weights, ARM_VECTOR) / (k + vector_ranks[ref_id])
        if in_title:
            score += _arm_weight(weights, ARM_TITLE) / (k + title_ranks[ref_id])
        match_type: MatchType = (
            "both"
            if in_keyword and in_vector
            else ("keyword" if in_keyword else "vector")
        )
        contributing_arms = tuple(
            arm
            for arm, hit in (
                (ARM_TITLE, in_title),
                (ARM_KEYWORD, in_keyword),
                (ARM_VECTOR, in_vector),
            )
            if hit
        )
        fused.append(
            FusedResult(
                ref_id=ref_id,
                score=score,
                match_type=match_type,
                contributing_arms=contributing_arms,
            )
        )

    fused.sort(key=lambda f: (-f.score, f.ref_id))
    return fused[:top_k]
