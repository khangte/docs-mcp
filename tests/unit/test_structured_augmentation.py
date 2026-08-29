"""Pure bounded structured augmentation postprocessor 테스트.

설계 84 §4 안전계약(`docs/architect-review/87` §2·§4)을 고정 `FusedResult`
리스트로 검증한다. DB·SQL 없음.
"""

from __future__ import annotations

from app.services.search.rrf import FusedResult
from app.services.search.structured_augmentation import (
    MAX_STRUCTURED_PROMOTION,
    apply_structured_augmentation,
)


def _fr(ref_id: str, score: float, arms: tuple[str, ...]) -> FusedResult:
    """테스트용 FusedResult — match_type 은 arms 로부터 대충 정한다."""
    match_type = "both" if len(arms) > 1 else (arms[0] if arms else "vector")
    return FusedResult(
        ref_id=ref_id,
        score=score,
        match_type=match_type,  # type: ignore[arg-type]
        contributing_arms=arms,
    )


# rank1 protected / rank2 vec(0.0) / rank3 vec(0.9) / rank4 protected / rank5 vec(0.0)
_BASE_WIDE = [
    _fr("keyword-a", 0.05, ("keyword",)),
    _fr("vec-x", 0.030, ("vector",)),
    _fr("vec-y", 0.020, ("vector",)),
    _fr("keyword-b", 0.010, ("keyword",)),
    _fr("vec-z", 0.005, ("vector",)),
]
_PROTECTED = frozenset({"keyword-a", "keyword-b"})
_SCORES = {"vec-x": 0.0, "vec-y": 0.9, "vec-z": 0.0}


def test_protected_absolute_rank_is_preserved() -> None:
    """protected ref 는 base/final 절대 순위가 완전히 동일하다."""
    outcome = apply_structured_augmentation(
        _BASE_WIDE, protected_ref_ids=_PROTECTED, augmentation_scores=_SCORES
    )
    base_rank = {x.ref_id: i for i, x in enumerate(_BASE_WIDE, 1)}
    final_rank = {x.ref_id: i for i, x in enumerate(outcome.fused, 1)}

    assert all(final_rank[r] == base_rank[r] for r in ("keyword-a", "keyword-b"))


def test_unprotected_displacement_is_at_most_one() -> None:
    """어떤 ref 도 `MAX_STRUCTURED_PROMOTION` 칸을 넘어 이동하지 않는다."""
    outcome = apply_structured_augmentation(
        _BASE_WIDE, protected_ref_ids=_PROTECTED, augmentation_scores=_SCORES
    )
    base_rank = {x.ref_id: i for i, x in enumerate(_BASE_WIDE, 1)}
    final_rank = {x.ref_id: i for i, x in enumerate(outcome.fused, 1)}

    assert all(
        abs(final_rank[r] - base_rank[r]) <= MAX_STRUCTURED_PROMOTION for r in base_rank
    )


def test_multiset_of_refs_is_unchanged() -> None:
    """base-wide 밖에서 유입되거나 빠지는 ref 가 없다."""
    outcome = apply_structured_augmentation(
        _BASE_WIDE, protected_ref_ids=_PROTECTED, augmentation_scores=_SCORES
    )

    assert [x.ref_id for x in outcome.fused].count("vec-y") == 1
    assert {x.ref_id for x in outcome.fused} == {x.ref_id for x in _BASE_WIDE}
    assert len(outcome.fused) == len(_BASE_WIDE)


def test_rrf_fields_are_never_mutated() -> None:
    """score/match_type/contributing_arms 는 ref 별로 전부 그대로다(같은 인스턴스)."""
    outcome = apply_structured_augmentation(
        _BASE_WIDE, protected_ref_ids=_PROTECTED, augmentation_scores=_SCORES
    )

    assert all(
        (after.score, after.match_type, after.contributing_arms)
        == (before.score, before.match_type, before.contributing_arms)
        for before, after in zip(
            sorted(_BASE_WIDE, key=lambda x: x.ref_id),
            sorted(outcome.fused, key=lambda x: x.ref_id),
            strict=True,
        )
    )


def test_expected_single_swap_happens() -> None:
    """유일하게 점수 우위(vec-y > vec-x)인 인접쌍만 자리를 바꾼다."""
    outcome = apply_structured_augmentation(
        _BASE_WIDE, protected_ref_ids=_PROTECTED, augmentation_scores=_SCORES
    )

    assert [x.ref_id for x in outcome.fused] == [
        "keyword-a",
        "vec-y",
        "vec-x",
        "keyword-b",
        "vec-z",
    ]


def test_all_zero_scores_are_complete_no_op() -> None:
    """모든 augmentation score 가 0 이면 순서·인스턴스가 base-wide 와 동일하다."""
    outcome = apply_structured_augmentation(
        _BASE_WIDE,
        protected_ref_ids=_PROTECTED,
        augmentation_scores={"vec-x": 0.0, "vec-y": 0.0, "vec-z": 0.0},
    )

    assert outcome.fused == tuple(_BASE_WIDE)


def test_equal_scores_are_no_op() -> None:
    """동점 인접쌍 `[1.0, 1.0]` 은 swap 하지 않는다(strict-greater)."""
    base = [_fr("a", 0.02, ("vector",)), _fr("b", 0.01, ("vector",))]
    outcome = apply_structured_augmentation(
        base, protected_ref_ids=frozenset(), augmentation_scores={"a": 1.0, "b": 1.0}
    )

    assert [x.ref_id for x in outcome.fused] == ["a", "b"]


def test_top_down_non_overlap_swaps() -> None:
    """전부 unprotected, score `[0,3,4,5]` -> `[r2,r1,r4,r3]`, ref 당 이동 최대 1회."""
    base = [
        _fr("r1", 0.04, ("vector",)),
        _fr("r2", 0.03, ("vector",)),
        _fr("r3", 0.02, ("vector",)),
        _fr("r4", 0.01, ("vector",)),
    ]
    outcome = apply_structured_augmentation(
        base,
        protected_ref_ids=frozenset(),
        augmentation_scores={"r1": 0.0, "r2": 3.0, "r3": 4.0, "r4": 5.0},
    )

    assert [x.ref_id for x in outcome.fused] == ["r2", "r1", "r4", "r3"]


def test_trace_recomputes_base_and_final_rank_by_ref() -> None:
    """trace row 는 ref_id 기준 base/final rank 와 immutable RRF 필드를 담는다."""
    outcome = apply_structured_augmentation(
        _BASE_WIDE, protected_ref_ids=_PROTECTED, augmentation_scores=_SCORES
    )
    by_ref = {row.ref_id: row for row in outcome.trace}

    assert set(by_ref) == {x.ref_id for x in _BASE_WIDE}
    assert (by_ref["vec-y"].base_rank, by_ref["vec-y"].final_rank) == (3, 2)
    assert (by_ref["vec-x"].base_rank, by_ref["vec-x"].final_rank) == (2, 3)
    assert by_ref["keyword-a"].protected is True
    assert by_ref["vec-y"].protected is False
    assert by_ref["vec-y"].augmentation_score == 0.9
    assert by_ref["keyword-a"].augmentation_score == 0.0
    assert by_ref["vec-x"].rrf_score == 0.030
    assert by_ref["keyword-a"].contributing_arms == ("keyword",)
