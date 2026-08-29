"""`compare_v3_candidate.py` 판정기와 `run_corpus_eval.py` 의 augmentation trace CLI 단위 테스트.

85번 설계 §6(공통 HARD + candidate-specific HARD 9항목)·§7(gate EFFECTIVENESS)·
§8(final HARD/EFFECTIVENESS)·§2(eval identity 분리)를 synthetic baseline/candidate
trace 로 검증한다. DB·검색·holdout 은 건드리지 않는다 — frozen fixture metadata 와
정적 판정기만 본다.

synthetic happy-path 규칙(88번 §5 C1 해석 잠금):

| report        | variants | augmentation |
|---------------|----------|--------------|
| baseline_off  | OFF      | OFF          |
| candidate_off | OFF      | ON           |
| baseline_on   | ON       | OFF          |
| candidate_on  | ON       | ON           |

두 candidate run 은 augmentation 이 active 이고 gate/final 하한을 전부 충족해야 한다.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).parents[1] / "fixtures" / "corpus_eval"
sys.path.insert(0, str(_DIR))

import compare_v3_candidate as cmp  # noqa: E402
import run_corpus_eval as rce  # noqa: E402

# --- freeze §0 identity bundle (구현 계획 87 §0) ---
_PRODUCT_SHA = "961bccad9d7d7f169ea5ee17c81581782c441bec"
_RULES_SHA = "dbc29008aa9803fd708bf619d263f76925e4d2a6"
_QUERY_SHA = "1da41901a225990492ead8215eb6a5bfde8afde987cafb60f0f74d03cbd84fdf"
_SPLIT_SHA = "701c43479425848c7af8f74360b88adb8f375d8bc801986ea2f684b5d45541e6"
_IMPL_SHA = "a" * 40
_FINGERPRINT = "b" * 64

_GATE_META = cmp._frozen_meta("gate")
_FINAL_META = cmp._frozen_meta("final")
_TARGETED = {"C2-한글패러프레이즈", "C3-영문의역", "C5-decoy구분"}
_C1 = "C1-직접키워드"
_C6 = "C6-다개념"


# --------------------------------------------------------------------------
# synthetic behavior plan — 특정 frozen ID 에 augmentation 효과를 심는다
# --------------------------------------------------------------------------
def _child_of(meta: dict, pid: str) -> str:
    return next(i for i in meta if meta[i]["pair_id"] == pid and meta[i]["pair_role"] == "child")


def _plan(meta: dict) -> dict[str, str]:
    """id -> behavior. 미지정은 'stable'.

    - cross(5+): base rank 11 -> final rank 10 (11->10 crossing). targeted category, ko>=3.
    - top(4): base rank 2 -> final rank 1 (상위 adjacent swap, MRR +0.02 견인).
    - pairimp: pair child 를 rank 3 -> 2 로 올려 effective pair 를 만든다.
    """
    ids = sorted(meta)
    gate_ids = [i for i in ids if meta[i]["split"] == "gate"]
    hold_ids = [i for i in ids if meta[i]["split"] == "holdout"]

    def _cross_pool(lang: str) -> list[str]:
        return [
            i for i in gate_ids
            if meta[i]["category"] in _TARGETED
            and not meta[i]["pair_id"]
            and meta[i]["language"] == lang
        ]

    cross = _cross_pool("ko")[:3] + _cross_pool("en")[:2]
    used = set(cross)
    top = [
        i for i in gate_ids
        if not meta[i]["pair_id"]
        and meta[i]["category"] not in (_C1, _C6)
        and i not in used
    ][:4]

    plan: dict[str, str] = {}
    for i in cross:
        plan[i] = "cross"
    for i in top:
        plan[i] = "top"

    gate_pairs = sorted({meta[i]["pair_id"] for i in gate_ids if meta[i]["pair_id"]})
    for pid in gate_pairs[:2]:
        plan[_child_of(meta, pid)] = "pairimp"

    if hold_ids:
        hold_pairs = sorted({meta[i]["pair_id"] for i in hold_ids if meta[i]["pair_id"]})
        for pid in hold_pairs[:1]:
            plan[_child_of(meta, pid)] = "pairimp"
        hold_singles = [i for i in hold_ids if not meta[i]["pair_id"]]
        plan[hold_singles[0]] = "cross"
    return plan


_GATE_PLAN = _plan(_GATE_META)
_FINAL_PLAN = _plan(_FINAL_META)

# behavior -> (answer_idx0, vector_only_extra_idx0, swap_lo_idx0 | None)
_BEHAVIOR = {
    "stable": (5, (), None),
    "cross": (10, (), 9),
    "top": (1, (0, 1), 0),
    "pairimp": (2, (1, 2), 1),
}


# --------------------------------------------------------------------------
# synthetic trace row builder
# --------------------------------------------------------------------------
def _fr(ref_id: str, rank: int, arms: list[str]) -> dict:
    return {"ref_id": ref_id, "rank": rank, "rrf_score": round(1.0 / (60 + rank), 6), "arms": arms}


def _hit(ref_id: str, rank: int) -> dict:
    return {"ref_id": ref_id, "score": round(1.0 / (60 + rank), 6), "rank": rank}


def _row(qid: str, meta_row: dict, behavior: str, *, arm: str) -> dict:
    ans_idx, vec_extra, swap_lo = _BEHAVIOR[behavior]
    refs = [f"{qid}-b{i:02d}" for i in range(12)]
    vec_idx = set(vec_extra) | {9, 10, 11}
    arms = [["vector"] if i in vec_idx else ["keyword"] for i in range(12)]
    base_wide = [_fr(refs[i], i + 1, arms[i]) for i in range(12)]
    protected = sorted(refs[i] for i in range(12) if arms[i] == ["keyword"])
    kw_refs = [refs[i] for i in range(12) if arms[i] == ["keyword"]]
    vc_refs = [refs[i] for i in range(12) if arms[i] == ["vector"]]
    keyword = [_hit(r, n) for n, r in enumerate(kw_refs, start=1)]
    vector = [_hit(r, n) for n, r in enumerate(vc_refs, start=1)]

    augmented = arm == "candidate" and behavior != "stable"
    final_wide = copy.deepcopy(base_wide)
    scores: list[dict] = []
    if augmented:
        lo = swap_lo
        final_wide[lo], final_wide[lo + 1] = final_wide[lo + 1], final_wide[lo]
        final_wide[lo]["rank"], final_wide[lo + 1]["rank"] = lo + 1, lo + 2
        scores = [
            {"ref_id": refs[lo], "score": 0.0},
            {"ref_id": refs[lo + 1], "score": 7.0},
        ]

    base_answer_rank = ans_idx + 1
    if behavior == "stable":
        answer_rank: int | None = base_answer_rank
    elif arm == "candidate":
        answer_rank = {"cross": 10, "top": 1, "pairimp": 2}[behavior]
    else:
        answer_rank = None if behavior == "cross" else base_answer_rank

    if meta_row["answer_mode"] == "all":
        second = answer_rank + 1 if isinstance(answer_rank, int) else 40
        per_accepted = [answer_rank, second]
    else:
        per_accepted = [answer_rank]

    return {
        "id": qid,
        "split": meta_row["split"],
        "category": meta_row["category"],
        "language": meta_row["language"],
        "answer_mode": meta_row["answer_mode"],
        "pair_id": meta_row["pair_id"],
        "pair_role": meta_row["pair_role"],
        "keyword": keyword,
        "vector": vector,
        "base_wide": base_wide,
        "protected_ref_ids": protected,
        "structured_scores": scores,
        "final_wide": final_wide,
        "answer_ref_id": refs[ans_idx],
        "base_answer_rank": base_answer_rank,
        "answer_rank": answer_rank,
        "per_accepted_ranks": per_accepted,
        "result_empty": False,
    }


def _identity() -> dict:
    return {
        "product_source_sha": _PRODUCT_SHA,
        "rules_git_sha": _RULES_SHA,
        "query_sha256": _QUERY_SHA,
        "split_sha256": _SPLIT_SHA,
        "corpus_sha256": dict(rce._V3_CORPUS_SHA256),
        "candidate_contract": copy.deepcopy(rce._V3_CANDIDATE_CONTRACT),
        "implementation_git_sha": _IMPL_SHA,
        "shared_index_fingerprint": _FINGERPRINT,
    }


def _report(meta: dict, plan: dict, *, arm: str, variants: bool, scope: str) -> dict:
    rows = [_row(qid, meta[qid], plan.get(qid, "stable"), arm=arm) for qid in sorted(meta)]
    fallback = {qid: 7 for qid in meta}
    return {
        "identity": _identity(),
        "arm": arm,
        "variants_enabled": variants,
        "augmentation_enabled": arm == "candidate",
        "lexical_field": "text",
        "strategy": "both",
        "top_k": 10,
        "split_scope": "gate" if scope == "gate" else "all",
        "queries": rows,
        "unaffected_paths": {"exact": {}, "document": {}, "fallback": fallback},
        "effectiveness": {
            "gain": cmp.crossing_net(rows),
            "recall_net": cmp.recall_net(rows),
        },
    }


def _gate_four() -> tuple[dict, dict, dict, dict]:
    m, p = _GATE_META, _GATE_PLAN
    return (
        _report(m, p, arm="baseline", variants=False, scope="gate"),
        _report(m, p, arm="candidate", variants=False, scope="gate"),
        _report(m, p, arm="baseline", variants=True, scope="gate"),
        _report(m, p, arm="candidate", variants=True, scope="gate"),
    )


def _final_four() -> tuple[dict, dict, dict, dict]:
    m, p = _FINAL_META, _FINAL_PLAN
    return (
        _report(m, p, arm="baseline", variants=False, scope="final"),
        _report(m, p, arm="candidate", variants=False, scope="final"),
        _report(m, p, arm="baseline", variants=True, scope="final"),
        _report(m, p, arm="candidate", variants=True, scope="final"),
    )


def _cross_id(meta: dict, plan: dict) -> str:
    return next(i for i in sorted(meta) if plan.get(i) == "cross" and meta[i]["split"] == "gate")


def _sync_eff(*reports: dict) -> None:
    """crossing/recall 구조를 변조한 뒤 report 의 effectiveness 블록을 실측에 맞춘다.

    이 재동기화 없이 변조하면 `check_boundary_identity` 가 먼저 FAIL 해서
    정작 검증하려는 EFFECTIVENESS 하한에 도달하지 못한다.
    """
    for rep in reports:
        rep["effectiveness"] = {
            "gain": cmp.crossing_net(rep["queries"]),
            "recall_net": cmp.recall_net(rep["queries"]),
        }


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------
def test_frozen_synthetic_four_run_passes_gate() -> None:
    cmp.compare_gate(*_gate_four())


def test_frozen_synthetic_four_run_passes_final() -> None:
    cmp.compare_final(*_final_four())


# --------------------------------------------------------------------------
# §2 / §6.1.1~2: eval identity
# --------------------------------------------------------------------------
def test_identity_trips_when_implementation_sha_missing() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    del c_on["identity"]["implementation_git_sha"]
    with pytest.raises(ValueError, match="eval identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_identity_trips_when_implementation_sha_not_forty_hex() -> None:
    reports = list(_gate_four())
    reports[3]["identity"]["implementation_git_sha"] = "abc123"
    with pytest.raises(ValueError, match="eval identity"):
        cmp.check_eval_identity(reports)


def test_identity_trips_when_implementation_sha_differs_between_runs() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["identity"]["implementation_git_sha"] = "c" * 40
    with pytest.raises(ValueError, match="eval identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_identity_trips_when_shared_index_fingerprint_differs() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_off["identity"]["shared_index_fingerprint"] = "d" * 64
    with pytest.raises(ValueError, match="eval identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_identity_trips_on_wrong_product_source_sha() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    b_off["identity"]["product_source_sha"] = "0" * 40
    with pytest.raises(ValueError, match="eval identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_identity_trips_on_tampered_candidate_contract() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["identity"]["candidate_contract"]["MAX_STRUCTURED_PROMOTION"] = 2
    with pytest.raises(ValueError, match="eval identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# §6.1.2 / 88 §5 C4: execution identity (arm/variants/augmentation/field/strategy/top_k)
# --------------------------------------------------------------------------
def test_execution_role_trips_when_arm_label_swapped() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_off["arm"] = "baseline"
    with pytest.raises(ValueError, match="execution identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_execution_role_trips_when_variants_flag_wrong() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    b_on["variants_enabled"] = False
    with pytest.raises(ValueError, match="execution identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_execution_role_trips_when_candidate_augmentation_off() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["augmentation_enabled"] = False
    with pytest.raises(ValueError, match="execution identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_execution_role_trips_when_lexical_field_not_text() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_off["lexical_field"] = "structured"
    with pytest.raises(ValueError, match="execution identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_execution_role_trips_when_strategy_lacks_fallback_parity() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    for rep in (b_off, c_off, b_on, c_on):
        rep["strategy"] = "rrf"
    with pytest.raises(ValueError, match="execution identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_execution_role_trips_when_top_k_not_ten() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["top_k"] = 20
    with pytest.raises(ValueError, match="execution identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# 88 §5 C4: ID 집합 / metadata 일치
# --------------------------------------------------------------------------
def test_id_set_trips_when_gate_row_missing() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_off["queries"].pop()
    with pytest.raises(ValueError, match="ID/metadata"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_id_set_trips_on_duplicate_query_id() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["queries"].append(copy.deepcopy(c_on["queries"][0]))
    with pytest.raises(ValueError, match="ID/metadata"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_id_set_trips_when_row_category_disagrees_with_frozen() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_off["queries"][0]["category"] = "C7-대형엔드포인트세부"
    with pytest.raises(ValueError, match="ID/metadata"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_id_set_trips_when_row_language_disagrees_with_frozen() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    row = next(r for r in c_on["queries"] if r["language"] == "ko")
    row["language"] = "en"
    with pytest.raises(ValueError, match="ID/metadata"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_gate_mode_rejects_holdout_row() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["queries"][0]["split"] = "holdout"
    with pytest.raises(ValueError, match="ID/metadata|gate mode"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# A2 / §6.1.3: fallback exactness — full-coverage id->rank parity
# --------------------------------------------------------------------------
def test_fallback_parity_trips_when_map_empty() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    for rep in (b_off, c_off, b_on, c_on):
        rep["unaffected_paths"]["fallback"] = {}
    with pytest.raises(ValueError, match="fallback exactness"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_fallback_parity_trips_on_partial_coverage() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    some_id = next(iter(_GATE_META))
    for rep in (b_off, c_off, b_on, c_on):
        rep["unaffected_paths"]["fallback"].pop(some_id)
    with pytest.raises(ValueError, match="fallback exactness"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_fallback_parity_trips_when_baseline_candidate_ranks_differ() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    some_id = next(iter(_GATE_META))
    c_off["unaffected_paths"]["fallback"][some_id] = 999
    with pytest.raises(ValueError, match="fallback exactness"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_all_three_unaffected_maps_empty_still_fails_hard8() -> None:
    """세 map 공집합 동일성만으로는 HARD 8 을 PASS 시킬 수 없다(88 §5)."""
    b_off, c_off, b_on, c_on = _gate_four()
    for rep in (b_off, c_off, b_on, c_on):
        rep["unaffected_paths"] = {"exact": {}, "document": {}, "fallback": {}}
    with pytest.raises(ValueError, match="fallback exactness"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# §6.2 candidate-specific HARD 9항목
# --------------------------------------------------------------------------
def test_hard1_text_arm_parity_trips_on_score_diff() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_off["queries"][0]["keyword"][0]["score"] += 1.0
    with pytest.raises(ValueError, match="Text-arm parity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard2_vector_arm_parity_trips_on_rank_diff() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["queries"][0]["vector"][0]["rank"] = 99
    with pytest.raises(ValueError, match="Vector-arm parity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard3_base_wide_parity_trips_on_arm_contribution_diff() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["queries"][0]["base_wide"][0]["arms"] = ["vector"]
    with pytest.raises(ValueError, match="Base-wide RRF parity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard4_protected_absolute_slot_trips_on_move() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    cid = _cross_id(_GATE_META, _GATE_PLAN)
    row = next(r for r in c_on["queries"] if r["id"] == cid)
    row["final_wide"][0], row["final_wide"][1] = row["final_wide"][1], row["final_wide"][0]
    row["final_wide"][0]["rank"], row["final_wide"][1]["rank"] = 1, 2
    with pytest.raises(ValueError, match="Protected absolute-slot"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard5_bounded_displacement_trips_beyond_one() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    cid = _cross_id(_GATE_META, _GATE_PLAN)
    row = next(r for r in c_on["queries"] if r["id"] == cid)
    fw = row["final_wide"]
    fw[9], fw[11] = fw[11], fw[9]
    fw[9]["rank"], fw[11]["rank"] = 10, 12
    with pytest.raises(ValueError, match="Bounded displacement"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard6_zero_score_no_op_trips_on_reorder() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    row = next(r for r in c_on["queries"] if _GATE_PLAN.get(r["id"], "stable") == "stable")
    row["final_wide"][2], row["final_wide"][3] = row["final_wide"][3], row["final_wide"][2]
    row["final_wide"][2]["rank"], row["final_wide"][3]["rank"] = 3, 4
    with pytest.raises(ValueError, match="Zero-score no-op"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard7_no_injection_trips_on_outside_ref() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    cid = _cross_id(_GATE_META, _GATE_PLAN)
    row = next(r for r in c_on["queries"] if r["id"] == cid)
    row["final_wide"][5]["ref_id"] = "outside-ref-x"
    with pytest.raises(ValueError, match="No injection"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard8_unaffected_path_parity_trips_on_exact_diff() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["unaffected_paths"]["exact"] = {"q": ["GET /different"]}
    with pytest.raises(ValueError, match="Unaffected-path parity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard9_pair_gate_trips_below_ten_of_ten() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    row = next(
        r for r in c_on["queries"]
        if r["pair_id"] and r["pair_role"] == "child" and _GATE_PLAN.get(r["id"]) is None
    )
    row["answer_rank"] = None
    with pytest.raises(ValueError, match="Pair gate"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# §6.1.4~8: 공통 HARD
# --------------------------------------------------------------------------
def test_common_hard_c1_loss_zero_trips() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c1_id = next(i for i in _GATE_META if _GATE_META[i]["category"] == _C1)
    row = next(r for r in c_off["queries"] if r["id"] == c1_id)
    row["answer_rank"] = None  # baseline 6 -> candidate 미검출
    with pytest.raises(ValueError, match="C1 loss zero"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_common_hard_per_category_hit_loss_floor_trips() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c7 = [r for r in c_on["queries"] if r["category"] == "C7-대형엔드포인트세부"][:2]
    for r in c7:
        r["answer_rank"] = None
    with pytest.raises(ValueError, match="Per-category floor"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_common_hard_c6_complete_regression_trips() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    row = next(r for r in c_on["queries"] if r["answer_mode"] == "all")
    row["per_accepted_ranks"] = [6, 40]  # baseline [6,7] 대비 complete 상실
    with pytest.raises(ValueError, match="C6"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_common_hard_empty_result_increase_trips() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["queries"][0]["result_empty"] = True
    with pytest.raises(ValueError, match="Empty-result"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# §7 gate EFFECTIVENESS
# --------------------------------------------------------------------------
def test_effectiveness_recall_floor_trips_when_crossings_removed() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    for rep in (c_off, c_on):
        for r in rep["queries"]:
            if r["base_answer_rank"] == 11:
                r["answer_rank"] = None
                r["final_wide"] = copy.deepcopy(r["base_wide"])
                r["structured_scores"] = []
    _sync_eff(c_off, c_on)
    with pytest.raises(ValueError, match="Recall@10"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_effectiveness_mrr_activation_floor_trips() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    # top(2->1) 승격을 되돌려 MRR 견인을 없앤다 — 두 arm 모두 +0.02 미만.
    for rep in (c_off, c_on):
        for r in rep["queries"]:
            if _GATE_PLAN.get(r["id"]) == "top":
                r["answer_rank"] = 2
                r["final_wide"] = copy.deepcopy(r["base_wide"])
                r["structured_scores"] = []
    with pytest.raises(ValueError, match="MRR"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_effectiveness_headline_non_decline_trips() -> None:
    """후보 개선을 무효화하고 rank 9->10 저하를 얇게 심으면 headline nDCG/MRR non-decline FAIL.

    단일-관련 근사에서 MRR·nDCG 는 rank 에 단조라 한 시나리오가 두 지표를 함께 깬다.
    저하를 9->10 구간에 두면 per-category MRR 하한(평균 0.02)에는 걸리지 않는다.
    """
    b_off, c_off, b_on, c_on = _gate_four()
    for rep in (c_off, c_on):
        for r in rep["queries"]:
            beh = _GATE_PLAN.get(r["id"], "stable")
            if beh == "stable":
                continue
            r["answer_rank"] = None if beh == "cross" else _BEHAVIOR[beh][0] + 1
            r["final_wide"] = copy.deepcopy(r["base_wide"])
            r["structured_scores"] = []
    movable = [
        r["id"] for r in b_off["queries"]
        if _GATE_PLAN.get(r["id"], "stable") == "stable"
        and not r["pair_id"]
        and r["category"] not in (_C1, _C6)
    ][:8]
    for rep in (b_off, c_off, b_on, c_on):
        base_side = rep in (b_off, b_on)
        for r in rep["queries"]:
            if r["id"] in movable:
                r["answer_rank"] = 9 if base_side else 10
                r["per_accepted_ranks"] = [r["answer_rank"]]
    _sync_eff(c_off, c_on)
    with pytest.raises(ValueError, match="non-decline"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_effectiveness_targeted_floor_trips() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    for rep in (c_off, c_on):
        for r in rep["queries"]:
            if r["category"] in _TARGETED and r["base_answer_rank"] == 11:
                r["answer_rank"] = None
                r["final_wide"] = copy.deepcopy(r["base_wide"])
                r["structured_scores"] = []
    _sync_eff(c_off, c_on)
    with pytest.raises(ValueError, match="Targeted|Recall@10"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_effectiveness_korean_floor_trips() -> None:
    """ko crossing 을 2건만 남기면 전체 Recall +3pp 는 아슬하게 통과하나 Korean ON +2 는 FAIL."""
    b_off, c_off, b_on, c_on = _gate_four()
    ko_cross = [
        r for r in c_on["queries"]
        if r["language"] == "ko" and _GATE_PLAN.get(r["id"]) == "cross"
    ][:2]
    for r in ko_cross:
        r["answer_rank"] = None
        r["final_wide"] = copy.deepcopy(r["base_wide"])
        r["structured_scores"] = []
    _sync_eff(c_on)
    with pytest.raises(ValueError, match="Korean"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_effectiveness_effective_pairs_floor_trips() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    for rep in (c_off, c_on):
        for r in rep["queries"]:
            if _GATE_PLAN.get(r["id"]) == "pairimp":
                r["answer_rank"] = 3
                r["final_wide"] = copy.deepcopy(r["base_wide"])
                r["structured_scores"] = []
    with pytest.raises(ValueError, match="route pair|Effective"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_effectiveness_crossing_floor_trips_below_three() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    n = 0
    for rep in (c_off, c_on):
        n = 0
        for r in rep["queries"]:
            if r["base_answer_rank"] == 11 and n < 3:
                r["answer_rank"] = None
                r["final_wide"] = copy.deepcopy(r["base_wide"])
                r["structured_scores"] = []
                n += 1
        rep["effectiveness"] = {
            "gain": cmp.crossing_net(rep["queries"]),
            "recall_net": cmp.recall_net(rep["queries"]),
        }
    with pytest.raises(ValueError, match="crossing|Recall@10"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# §7.8~9 boundary crossing identity
# --------------------------------------------------------------------------
def test_crossing_and_recall_net_match_on_valid_candidate_on() -> None:
    _, _, _, c_on = _gate_four()
    rows = c_on["queries"]
    assert cmp.crossing_net(rows) == cmp.recall_net(rows)
    assert cmp.crossing_net(rows) >= 3


def test_boundary_identity_trips_when_recall_net_diverges_from_crossing() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    victim = next(
        r["id"] for r in c_on["queries"]
        if _GATE_PLAN.get(r["id"], "stable") == "stable"
        and not r["pair_id"]
        and r["category"] not in (_C1, _C6)
    )
    # baseline/candidate 양쪽을 같은 값으로 옮겨 per-category 하한은 건드리지 않되,
    # candidate report 의 per-row base_answer_rank 가 crossing 이 아닌 recall 진입을 만든다.
    for rep in (b_on, c_on):
        row = next(r for r in rep["queries"] if r["id"] == victim)
        row["base_answer_rank"] = 15
        row["answer_rank"] = 8
    with pytest.raises(ValueError, match="boundary crossing"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_boundary_identity_trips_when_gain_inflated_by_safety_counts() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    c_on["effectiveness"]["gain"] += 3  # protected/no-op 수를 gain 에 더한 형태
    with pytest.raises(ValueError, match="effectiveness gain"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# HARD -> EFFECTIVENESS 순서 잠금
# --------------------------------------------------------------------------
def test_hard_failure_is_reported_before_effectiveness() -> None:
    b_off, c_off, b_on, c_on = _gate_four()
    # HARD(C1 loss) 와 EFFECTIVENESS(크로싱 전멸) 를 동시에 깬다.
    c1_id = next(i for i in _GATE_META if _GATE_META[i]["category"] == _C1)
    for rep in (c_off, c_on):
        next(r for r in rep["queries"] if r["id"] == c1_id)["answer_rank"] = None
        for r in rep["queries"]:
            if r["base_answer_rank"] == 11:
                r["answer_rank"] = None
                r["final_wide"] = copy.deepcopy(r["base_wide"])
                r["structured_scores"] = []
        rep["effectiveness"] = {
            "gain": cmp.crossing_net(rep["queries"]),
            "recall_net": cmp.recall_net(rep["queries"]),
        }
    with pytest.raises(ValueError, match="C1 loss zero"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# §8 final HARD / EFFECTIVENESS
# --------------------------------------------------------------------------
def test_final_crossing_floor_requires_plus_four() -> None:
    b_off, c_off, b_on, c_on = _final_four()
    n = 0
    for rep in (c_off, c_on):
        n = 0
        for r in rep["queries"]:
            if r["base_answer_rank"] == 11 and n < 3:  # 6 -> 3 crossing: floor +4 미달
                r["answer_rank"] = None
                r["final_wide"] = copy.deepcopy(r["base_wide"])
                r["structured_scores"] = []
                n += 1
        rep["effectiveness"] = {
            "gain": cmp.crossing_net(rep["queries"]),
            "recall_net": cmp.recall_net(rep["queries"]),
        }
    with pytest.raises(ValueError, match="crossing|Recall@10"):
        cmp.compare_final(b_off, c_off, b_on, c_on)


def test_final_holdout_mrr_drop_floor_trips() -> None:
    b_off, c_off, b_on, c_on = _final_four()
    # c_off 의 stable holdout 행을 카테고리당 최대 2건까지 6 -> 9 로 낮춘다. 카테고리당 2건이면
    # per-category MRR 하한(0.02, scored 120 전수 기준)은 넘지 않지만 holdout 집계 MRR 하락은
    # candidate 의 crossing head start 를 상쇄하고 0.01 을 넘겨 FAIL 해야 한다.
    per_cat: dict[str, int] = {}
    for r in c_off["queries"]:
        if r["split"] != "holdout" or r["pair_id"]:
            continue
        if _FINAL_PLAN.get(r["id"], "stable") != "stable":
            continue
        if per_cat.get(r["category"], 0) >= 2:
            continue
        per_cat[r["category"]] = per_cat.get(r["category"], 0) + 1
        r["answer_rank"] = 9  # top-10 유지하되 MRR 크게 하락
        r["per_accepted_ranks"] = [9, 10] if r["answer_mode"] == "all" else [9]
    with pytest.raises(ValueError, match="[Hh]oldout"):
        cmp.compare_final(b_off, c_off, b_on, c_on)


def test_final_holdout_combined_requires_a_win() -> None:
    b_off, c_off, b_on, c_on = _final_four()
    for rep in (c_off, c_on):
        for r in rep["queries"]:
            if r["split"] == "holdout" and r["base_answer_rank"] == 11:
                r["answer_rank"] = None
                r["final_wide"] = copy.deepcopy(r["base_wide"])
                r["structured_scores"] = []
        rep["effectiveness"] = {
            "gain": cmp.crossing_net(rep["queries"]),
            "recall_net": cmp.recall_net(rep["queries"]),
        }
    with pytest.raises(ValueError, match="[Hh]oldout|crossing"):
        cmp.compare_final(b_off, c_off, b_on, c_on)


def test_final_effective_pairs_need_gate_two_holdout_one_all_three() -> None:
    b_off, c_off, b_on, c_on = _final_four()
    for rep in (c_off, c_on):
        for r in rep["queries"]:
            if r["split"] == "holdout" and _FINAL_PLAN.get(r["id"]) == "pairimp":
                r["answer_rank"] = 3
                r["final_wide"] = copy.deepcopy(r["base_wide"])
                r["structured_scores"] = []
    with pytest.raises(ValueError, match="route pair|Effective|[Hh]oldout"):
        cmp.compare_final(b_off, c_off, b_on, c_on)


def test_final_mode_accepts_holdout_rows() -> None:
    b_off, c_off, b_on, c_on = _final_four()
    assert any(r["split"] == "holdout" for r in c_on["queries"])
    cmp.compare_final(b_off, c_off, b_on, c_on)  # 스코프 위반 없이 통과


# --------------------------------------------------------------------------
# runner CLI: augmentation on/off + report-json 경로 가드
# --------------------------------------------------------------------------
def test_runner_defaults_structured_augmentation_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["run_corpus_eval.py"])
    args = rce._parse_args()
    assert args.structured_augmentation == "off"


def test_runner_rejects_structured_lexical_with_augmentation_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_corpus_eval.py", "--lexical-field", "structured", "--structured-augmentation", "on"],
    )
    with pytest.raises(SystemExit):
        rce._parse_args()


def test_runner_accepts_augmentation_on_with_text_lexical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_corpus_eval.py", "--structured-augmentation", "on", "--strategy", "both",
         "--report-json", "/tmp/x/scratchpad/trace.json"],
    )
    args = rce._parse_args()
    assert args.structured_augmentation == "on"
    assert args.report_json == "/tmp/x/scratchpad/trace.json"


def test_runner_rejects_report_json_without_fallback_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--report-json 은 fallback parity 를 실측하는 실행축(--strategy both)을 요구한다(88 A2)."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_corpus_eval.py", "--structured-augmentation", "on", "--strategy", "rrf",
         "--report-json", "/tmp/x/scratchpad/trace.json"],
    )
    with pytest.raises(SystemExit):
        rce._parse_args()


def test_runner_rejects_report_json_in_lookalike_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_corpus_eval.py", "--structured-augmentation", "on", "--strategy", "both",
         "--report-json", "/tmp/scratchpad-evil/trace.json"],
    )
    with pytest.raises(SystemExit):
        rce._parse_args()


def test_runner_rejects_report_json_traversal_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_corpus_eval.py", "--structured-augmentation", "on", "--strategy", "both",
         "--report-json", "scratchpad/../secret/trace.json"],
    )
    with pytest.raises(SystemExit):
        rce._parse_args()
