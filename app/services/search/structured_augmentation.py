"""Text-primary bounded structured augmentation postprocessor (pure).

현행 keyword + vector wide RRF 를 primary 로 유지한 채, base-wide RRF 가
완성된 뒤 vector-only 후보만 A/B/C original-query 구조 점수로 **최대 한 칸**
승격하는 순수 함수다(`docs/architect-review/87` §2, I2·I6).

- protected(text keyword-backed) ref 는 절대 순위가 불변이다.
- unprotected 인접쌍만, lower 의 구조 점수가 upper 보다 **엄격히 클 때만**,
  이번 scan 에서 아직 swap 에 참여하지 않았을 때만 자리를 바꾼다.
- `FusedResult` 인스턴스를 복제하거나 score/match_type/contributing_arms 를
  바꾸지 않는다. 순서만 바뀐다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.services.search.rrf import FusedResult

#: 한 후보가 이동할 수 있는 최대 칸 수. env 화하거나 1 이외 값으로 바꾸지 않는다
#: (`docs/architect-review/87` §6).
MAX_STRUCTURED_PROMOTION = 1


@dataclass(frozen=True)
class AugmentationTraceRow:
    """후보 한 건의 base/final rank 와 immutable RRF 필드 스냅샷."""

    ref_id: str
    base_rank: int
    final_rank: int
    augmentation_score: float
    protected: bool
    rrf_score: float
    contributing_arms: tuple[str, ...]


@dataclass(frozen=True)
class AugmentationOutcome:
    """postprocessor 결과 — 재정렬된 fused 와 ref 별 trace."""

    fused: tuple[FusedResult, ...]
    trace: tuple[AugmentationTraceRow, ...]


@dataclass(frozen=True)
class RrfSearchTrace:
    """request-scoped eval trace — base-wide RRF 전후 스냅샷.

    이 모듈에서는 정의만 두고, 실제 채우기는 `EndpointCandidateSearch._search_rrf()`
    가 담당한다(`docs/architect-review/87` Task 4 Step 5).
    """

    augmentation_enabled: bool
    keyword_hits: tuple[tuple[str, float], ...]
    vector_hits: tuple[tuple[str, float], ...]
    base_wide: tuple[FusedResult, ...]
    protected_ref_ids: frozenset[str]
    structured_scores: tuple[tuple[str, float], ...]
    final_wide: tuple[FusedResult, ...]


def apply_structured_augmentation(
    base_wide: Sequence[FusedResult],
    *,
    protected_ref_ids: frozenset[str],
    augmentation_scores: Mapping[str, float],
) -> AugmentationOutcome:
    """base-wide RRF 결과에 bounded adjacent max-one-swap 을 적용한다.

    base rank 2 부터 아래 문서를 promotion candidate 로 보는 top-down scan 이다.
    두 ref 가 모두 protected 가 아니고 이번 scan 에서 아직 swap 에 참여하지
    않았으며 lower 의 구조 점수가 upper 보다 엄격히 클 때만 자리를 바꾼다.
    동점은 no-op 이다.
    """
    ranked = list(base_wide)
    used: set[str] = set()
    for lower_index in range(1, len(ranked)):
        upper_index = lower_index - 1
        upper = ranked[upper_index]
        lower = ranked[lower_index]
        if upper.ref_id in used or lower.ref_id in used:
            continue
        if upper.ref_id in protected_ref_ids or lower.ref_id in protected_ref_ids:
            continue
        if augmentation_scores.get(lower.ref_id, 0.0) <= augmentation_scores.get(
            upper.ref_id, 0.0
        ):
            continue
        ranked[upper_index], ranked[lower_index] = lower, upper
        used.update((upper.ref_id, lower.ref_id))

    base_rank = {fr.ref_id: i for i, fr in enumerate(base_wide, start=1)}
    final_rank = {fr.ref_id: i for i, fr in enumerate(ranked, start=1)}
    for ref_id, before in base_rank.items():
        assert abs(final_rank[ref_id] - before) <= MAX_STRUCTURED_PROMOTION, (
            f"{ref_id} moved {before} -> {final_rank[ref_id]}, "
            f"exceeds MAX_STRUCTURED_PROMOTION={MAX_STRUCTURED_PROMOTION}"
        )

    trace = tuple(
        AugmentationTraceRow(
            ref_id=fr.ref_id,
            base_rank=base_rank[fr.ref_id],
            final_rank=final_rank[fr.ref_id],
            augmentation_score=augmentation_scores.get(fr.ref_id, 0.0),
            protected=fr.ref_id in protected_ref_ids,
            rrf_score=fr.score,
            contributing_arms=fr.contributing_arms,
        )
        for fr in base_wide
    )
    return AugmentationOutcome(fused=tuple(ranked), trace=trace)
