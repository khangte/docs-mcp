"""RRF(Reciprocal Rank Fusion) 순위 융합.

키워드(ts_rank)와 벡터(코사인 유사도)는 스케일이 서로 달라 점수를 직접 더하면
가중치가 왜곡된다. RRF 는 각 ranker 안에서의 **등수만** 사용하므로 스케일에
무관하게 두 신호를 자연스럽게 합칠 수 있다(`docs/architect-review/07-search-rrf-reevaluation.md` 3·5절).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

MatchType = Literal["keyword", "vector", "both"]

#: 표준값(Cormack et al. 2009). 평가셋 없이 튜닝할 근거가 없어 상수로 고정하고
#: env 로 노출하지 않는다(`docs/architect-review/07-search-rrf-reevaluation.md` 5.2).
RRF_K = 60


@dataclass(frozen=True)
class FusedResult:
    """융합 결과 한 건(엔드포인트 ref_id + RRF 점수 + 기여 arm 표기)."""

    ref_id: str
    score: float
    match_type: MatchType


def _dedupe_first(ref_ids: Sequence[str]) -> list[str]:
    """같은 ref_id 가 여러 번 나오면 첫 등장만 남긴다(등수 계산 전 전처리)."""
    seen: set[str] = set()
    result: list[str] = []
    for ref_id in ref_ids:
        if ref_id in seen:
            continue
        seen.add(ref_id)
        result.append(ref_id)
    return result


def reciprocal_rank_fuse(
    keyword_ref_ids: Sequence[str],
    vector_ref_ids: Sequence[str],
    *,
    top_k: int,
    k: int = RRF_K,
) -> list[FusedResult]:
    """두 ranker 의 순위 리스트를 RRF 공식으로 융합해 top_k 로 자른다.

    `score(d) = Σ_arm 1/(k + rank_arm(d))` — 해당 arm 에 후보가 없으면 그
    항은 0. 각 arm 내 중복 ref_id 는 첫 등장 등수만 채택한다. 동점이면
    ref_id 오름차순으로 정렬해 결정적 결과를 보장한다(골든 회귀 테스트 전제).
    """
    keyword_ranks = {ref_id: rank for rank, ref_id in enumerate(_dedupe_first(keyword_ref_ids), start=1)}
    vector_ranks = {ref_id: rank for rank, ref_id in enumerate(_dedupe_first(vector_ref_ids), start=1)}

    fused: list[FusedResult] = []
    for ref_id in keyword_ranks.keys() | vector_ranks.keys():
        in_keyword = ref_id in keyword_ranks
        in_vector = ref_id in vector_ranks
        score = 0.0
        if in_keyword:
            score += 1.0 / (k + keyword_ranks[ref_id])
        if in_vector:
            score += 1.0 / (k + vector_ranks[ref_id])
        match_type: MatchType = "both" if in_keyword and in_vector else ("keyword" if in_keyword else "vector")
        fused.append(FusedResult(ref_id=ref_id, score=score, match_type=match_type))

    fused.sort(key=lambda f: (-f.score, f.ref_id))
    return fused[:top_k]
