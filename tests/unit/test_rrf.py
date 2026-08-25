"""RRF(Reciprocal Rank Fusion) 순위 융합 단위 테스트(순수 함수, DB 불필요)."""

from __future__ import annotations

from app.services.search.rrf import ARM_KEYWORD, ARM_TITLE, ARM_VECTOR, RRF_K, reciprocal_rank_fuse


def test_keyword_only_hit_gets_keyword_match_type() -> None:
    """벡터 arm 에 전혀 없는 ref_id 는 keyword 로 표시된다."""
    fused = reciprocal_rank_fuse(["a", "b"], [], top_k=5)

    assert [f.ref_id for f in fused] == ["a", "b"]
    assert [f.match_type for f in fused] == ["keyword", "keyword"]


def test_vector_only_hit_gets_vector_match_type() -> None:
    """키워드 arm 에 전혀 없는 ref_id 는 vector 로 표시된다."""
    fused = reciprocal_rank_fuse([], ["a", "b"], top_k=5)

    assert [f.ref_id for f in fused] == ["a", "b"]
    assert [f.match_type for f in fused] == ["vector", "vector"]


def test_hit_in_both_arms_gets_both_match_type_and_higher_score() -> None:
    """양쪽 arm 모두에 있는 후보는 match_type="both" 이고 한쪽만 있는 후보보다 점수가 높다."""
    fused = reciprocal_rank_fuse(["a", "b"], ["a", "c"], top_k=5)
    by_ref = {f.ref_id: f for f in fused}

    assert by_ref["a"].match_type == "both"
    assert by_ref["b"].match_type == "keyword"
    assert by_ref["c"].match_type == "vector"
    assert by_ref["a"].score > by_ref["b"].score
    assert by_ref["a"].score > by_ref["c"].score


def test_score_formula_matches_rrf_definition() -> None:
    """score(d) = sum(1/(K+rank)) 공식을 그대로 따른다."""
    fused = reciprocal_rank_fuse(["a"], ["a"], top_k=1)

    assert fused[0].score == 1.0 / (RRF_K + 1) + 1.0 / (RRF_K + 1)


def test_duplicate_ref_id_in_single_arm_uses_first_occurrence_rank() -> None:
    """같은 arm 안에 동일 ref_id 가 여러 번 나와도 첫 등장 등수만 채택한다."""
    fused_dup = reciprocal_rank_fuse(["a", "b", "a"], [], top_k=5)
    fused_plain = reciprocal_rank_fuse(["a", "b"], [], top_k=5)

    assert [(f.ref_id, f.score) for f in fused_dup] == [(f.ref_id, f.score) for f in fused_plain]


def test_tie_break_is_ref_id_ascending() -> None:
    """점수가 같으면 ref_id 오름차순으로 정렬한다(골든 회귀 결정성 전제)."""
    fused = reciprocal_rank_fuse(["z"], ["a"], top_k=5)

    assert [f.ref_id for f in fused] == ["a", "z"]


def test_top_k_cuts_after_fusion() -> None:
    """융합 후 top_k 로 자른다."""
    fused = reciprocal_rank_fuse(["a", "b", "c", "d", "e"], [], top_k=2)

    assert len(fused) == 2
    assert [f.ref_id for f in fused] == ["a", "b"]


def test_empty_inputs_return_empty() -> None:
    """양쪽 arm 모두 비어 있으면 빈 리스트다."""
    assert reciprocal_rank_fuse([], [], top_k=5) == []


# --- doc36 Phase3 #12: 3번째 arm(title) ------------------------------------


def test_title_arm_defaults_to_empty_and_does_not_change_two_arm_result() -> None:
    """title_ref_ids 를 생략하면 기존 2-arm 결과·골든 테스트와 동일하다(하위 호환)."""
    fused = reciprocal_rank_fuse(["a", "b"], ["a", "c"], top_k=5)
    fused_explicit_empty = reciprocal_rank_fuse(["a", "b"], ["a", "c"], top_k=5, title_ref_ids=[])

    assert fused == fused_explicit_empty


def test_title_only_hit_contributes_to_score() -> None:
    """title arm 에만 있는 ref_id 도 결과에 포함되고 점수가 0보다 크다."""
    fused = reciprocal_rank_fuse([], [], top_k=5, title_ref_ids=["a"])

    assert [f.ref_id for f in fused] == ["a"]
    assert fused[0].score > 0.0


def test_title_arm_adds_to_score_when_combined_with_other_arms() -> None:
    """세 arm 모두에 있는 후보가 두 arm에만 있는 후보보다 점수가 높다."""
    fused = reciprocal_rank_fuse(["a"], ["a"], top_k=5, title_ref_ids=["a", "b"])
    by_ref = {f.ref_id: f for f in fused}

    two_arm_only = reciprocal_rank_fuse(["a"], ["a"], top_k=5)

    assert by_ref["a"].score > two_arm_only[0].score


# --- 57번 리뷰 개선1: contributing_arms 노출 --------------------------------


def test_contributing_arms_lists_only_arms_that_hit_in_fixed_order() -> None:
    """기여한 arm 만 (title, keyword, vector) 고정 순서로 담긴다."""
    fused = reciprocal_rank_fuse(["a"], [], top_k=5, title_ref_ids=["a"])

    assert fused[0].contributing_arms == (ARM_TITLE, ARM_KEYWORD)


def test_contributing_arms_all_three_when_all_arms_hit() -> None:
    """세 arm 모두 기여하면 세 값 모두 고정 순서로 담긴다."""
    fused = reciprocal_rank_fuse(["a"], ["a"], top_k=5, title_ref_ids=["a"])

    assert fused[0].contributing_arms == (ARM_TITLE, ARM_KEYWORD, ARM_VECTOR)


def test_contributing_arms_empty_default_on_dataclass() -> None:
    """FusedResult 기본값은 빈 튜플이다(기존 생성부가 깨지지 않도록)."""
    from app.services.search.rrf import FusedResult

    result = FusedResult(ref_id="x", score=1.0, match_type="keyword")

    assert result.contributing_arms == ()
