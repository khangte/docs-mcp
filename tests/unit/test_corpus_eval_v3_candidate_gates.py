"""`compare_v3_candidate.py` 판정기와 `run_corpus_eval.py` 의 augmentation trace CLI 단위 테스트.

85번 설계 §6.2(candidate-specific HARD 9항목)·§7.8~9(boundary crossing identity)·
§2.1~2.2(eval identity 분리)를 synthetic baseline/candidate trace 로 검증한다.
DB·검색·holdout 은 건드리지 않는다 — frozen 상수와 정적 판정기만 본다.
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


# --------------------------------------------------------------------------
# synthetic trace builders
# --------------------------------------------------------------------------
def _fr(ref_id: str, rank: int, arms: list[str]) -> dict:
    return {"ref_id": ref_id, "rank": rank, "rrf_score": round(1.0 / (60 + rank), 6), "arms": arms}


def _hit(ref_id: str, rank: int) -> dict:
    return {"ref_id": ref_id, "score": round(1.0 / (60 + rank), 6), "rank": rank}


def _pair_row(pair_id: str, role: str, split: str) -> dict:
    """4-wide base. protected {p1,p2}, 정답 ref = p3, 승격 없음(structured 0)."""
    refs = [f"{pair_id}{role[0]}{i}" for i in range(1, 5)]
    base = [_fr(refs[i], i + 1, ["keyword"] if i < 2 else ["vector"]) for i in range(4)]
    return {
        "id": f"{pair_id}-{role}",
        "split": split,
        "category": "C5-decoy구분",
        "pair_id": pair_id,
        "pair_role": role,
        "keyword": [_hit(refs[0], 1), _hit(refs[1], 2)],
        "vector": [_hit(refs[2], 1), _hit(refs[3], 2)],
        "base_wide": base,
        "protected_ref_ids": sorted([refs[0], refs[1]]),
        "structured_scores": [],
        "final_wide": copy.deepcopy(base),
        "answer_ref_id": refs[2],
        "base_answer_rank": 3,
        "answer_rank": 3,
    }


def _promo_row(qid: str, *, augmented: bool) -> dict:
    """12-wide base. protected r01..r09(keyword), r10/r11/r12 vector-only.

    augmented=True 면 structured score 로 r11 을 r10 위로 한 칸 승격(11->10 crossing).
    """
    refs = [f"{qid}-r{i:02d}" for i in range(1, 13)]
    base = [
        _fr(refs[i], i + 1, ["keyword"] if i < 9 else ["vector"]) for i in range(12)
    ]
    final = copy.deepcopy(base)
    scores: list[dict] = []
    ans_rank = 11
    if augmented:
        scores = [{"ref_id": refs[9], "score": 0.0}, {"ref_id": refs[10], "score": 5.0},
                  {"ref_id": refs[11], "score": 0.0}]
        final[9], final[10] = final[10], final[9]
        final[9]["rank"], final[10]["rank"] = 10, 11
        ans_rank = 10
    return {
        "id": qid,
        "split": "gate",
        "category": "C2-한글패러프레이즈",
        "pair_id": None,
        "pair_role": None,
        "keyword": [_hit(refs[i], i + 1) for i in range(9)],
        "vector": [_hit(refs[i], i - 8) for i in range(9, 12)],
        "base_wide": base,
        "protected_ref_ids": sorted(refs[:9]),
        "structured_scores": scores,
        "final_wide": final,
        "answer_ref_id": refs[10],
        "base_answer_rank": 11,
        "answer_rank": ans_rank,
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


def _report(*, arm: str, augmentation_enabled: bool) -> dict:
    rows: list[dict] = []
    for n in range(1, 11):
        pid = f"v3p{n:02d}"
        rows.append(_pair_row(pid, "root", "gate"))
        rows.append(_pair_row(pid, "child", "gate"))
    # 3 single promo rows: candidate+ON 에서만 승격
    do_aug = augmentation_enabled and arm == "candidate"
    for n in range(1, 4):
        rows.append(_promo_row(f"v3g{n:03d}", augmented=do_aug))
    net = 3 if do_aug else 0
    return {
        "identity": _identity(),
        "arm": arm,
        "augmentation_enabled": augmentation_enabled,
        "split_scope": "gate",
        "queries": rows,
        "unaffected_paths": {"exact": {"q": ["GET /x"]}, "fallback": {}, "document": {}},
        "effectiveness": {"gain": net, "recall_net": net},
    }


def _four() -> tuple[dict, dict, dict, dict]:
    return (
        _report(arm="baseline", augmentation_enabled=False),
        _report(arm="candidate", augmentation_enabled=False),
        _report(arm="baseline", augmentation_enabled=True),
        _report(arm="candidate", augmentation_enabled=True),
    )


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------
def test_frozen_synthetic_four_run_passes_gate() -> None:
    cmp.compare_gate(*_four())


# --------------------------------------------------------------------------
# §6.1.1~2 / §2: eval identity
# --------------------------------------------------------------------------
def test_identity_trips_when_implementation_sha_missing() -> None:
    b_off, c_off, b_on, c_on = _four()
    del c_on["identity"]["implementation_git_sha"]
    with pytest.raises(ValueError, match="eval identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_identity_trips_when_implementation_sha_not_forty_hex() -> None:
    reports = list(_four())
    reports[3]["identity"]["implementation_git_sha"] = "abc123"
    with pytest.raises(ValueError, match="eval identity"):
        cmp.check_eval_identity(reports)


def test_identity_trips_when_implementation_sha_differs_between_runs() -> None:
    b_off, c_off, b_on, c_on = _four()
    c_on["identity"]["implementation_git_sha"] = "c" * 40
    with pytest.raises(ValueError, match="eval identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_identity_trips_when_shared_index_fingerprint_differs() -> None:
    b_off, c_off, b_on, c_on = _four()
    c_off["identity"]["shared_index_fingerprint"] = "d" * 64
    with pytest.raises(ValueError, match="eval identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_identity_trips_on_wrong_product_source_sha() -> None:
    b_off, c_off, b_on, c_on = _four()
    b_off["identity"]["product_source_sha"] = "0" * 40
    with pytest.raises(ValueError, match="eval identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_identity_trips_on_tampered_candidate_contract() -> None:
    b_off, c_off, b_on, c_on = _four()
    c_on["identity"]["candidate_contract"]["MAX_STRUCTURED_PROMOTION"] = 2
    with pytest.raises(ValueError, match="eval identity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# gate mode scope
# --------------------------------------------------------------------------
def test_gate_mode_rejects_holdout_row() -> None:
    b_off, c_off, b_on, c_on = _four()
    c_on["queries"][0]["split"] = "holdout"
    with pytest.raises(ValueError, match="gate mode"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# §6.2 candidate-specific HARD 9항목
# --------------------------------------------------------------------------
def test_hard1_text_arm_parity_trips_on_score_diff() -> None:
    b_off, c_off, b_on, c_on = _four()
    c_off["queries"][0]["keyword"][0]["score"] += 1.0
    with pytest.raises(ValueError, match="Text-arm parity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard2_vector_arm_parity_trips_on_rank_diff() -> None:
    b_off, c_off, b_on, c_on = _four()
    c_on["queries"][0]["vector"][0]["rank"] = 99
    with pytest.raises(ValueError, match="Vector-arm parity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard3_base_wide_parity_trips_on_arm_contribution_diff() -> None:
    b_off, c_off, b_on, c_on = _four()
    c_on["queries"][0]["base_wide"][0]["arms"] = ["vector"]
    with pytest.raises(ValueError, match="Base-wide RRF parity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard4_protected_absolute_slot_trips_on_move() -> None:
    b_off, c_off, b_on, c_on = _four()
    row = next(r for r in c_on["queries"] if r["id"] == "v3g001")
    row["final_wide"][0], row["final_wide"][1] = row["final_wide"][1], row["final_wide"][0]
    row["final_wide"][0]["rank"], row["final_wide"][1]["rank"] = 1, 2
    with pytest.raises(ValueError, match="Protected absolute-slot"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard5_bounded_displacement_trips_beyond_one() -> None:
    b_off, c_off, b_on, c_on = _four()
    row = next(r for r in c_on["queries"] if r["id"] == "v3g002")
    # r12 를 rank 10 으로, r10 을 rank 12 로 (|Δ|=2)
    fw = row["final_wide"]
    fw[9], fw[11] = fw[11], fw[9]
    fw[9]["rank"], fw[11]["rank"] = 10, 12
    row["structured_scores"] = [{"ref_id": row["answer_ref_id"], "score": 0.0},
                                {"ref_id": f"{row['id']}-r12", "score": 9.0}]
    with pytest.raises(ValueError, match="Bounded displacement"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard6_zero_score_no_op_trips_on_reorder() -> None:
    b_off, c_off, b_on, c_on = _four()
    row = next(r for r in c_on["queries"] if r["pair_id"] == "v3p03" and r["pair_role"] == "root")
    row["final_wide"][2], row["final_wide"][3] = row["final_wide"][3], row["final_wide"][2]
    row["final_wide"][2]["rank"], row["final_wide"][3]["rank"] = 3, 4
    with pytest.raises(ValueError, match="Zero-score no-op"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard7_no_injection_trips_on_outside_ref() -> None:
    b_off, c_off, b_on, c_on = _four()
    row = next(r for r in c_on["queries"] if r["id"] == "v3g003")
    row["final_wide"][9]["ref_id"] = "outside-ref-x"
    with pytest.raises(ValueError, match="No injection"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard8_unaffected_path_parity_trips_on_exact_diff() -> None:
    b_off, c_off, b_on, c_on = _four()
    c_on["unaffected_paths"]["exact"] = {"q": ["GET /different"]}
    with pytest.raises(ValueError, match="Unaffected-path parity"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_hard9_pair_gate_trips_below_ten_of_ten() -> None:
    b_off, c_off, b_on, c_on = _four()
    row = next(r for r in c_on["queries"] if r["pair_id"] == "v3p04" and r["pair_role"] == "child")
    row["answer_rank"] = None  # baseline 3 -> candidate 미검출: pair 하나 regress
    with pytest.raises(ValueError, match="Pair gate"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# §7.8~9 boundary crossing identity
# --------------------------------------------------------------------------
def test_crossing_and_recall_net_match_on_valid_candidate_on() -> None:
    _, _, _, c_on = _four()
    rows = c_on["queries"]
    assert cmp.crossing_net(rows) == 3
    assert cmp.recall_net(rows) == 3


def test_boundary_identity_trips_when_recall_net_diverges_from_crossing() -> None:
    b_off, c_off, b_on, c_on = _four()
    # max-one-swap 으로 설명 안 되는 recall 이득을 심는다(base 15 -> final 8).
    # pair 가 아닌 promo single 을 골라 pair gate 가 먼저 걸리지 않게 한다.
    row = next(r for r in c_on["queries"] if r["id"] == "v3g001")
    row["base_answer_rank"] = 15
    row["answer_rank"] = 8
    with pytest.raises(ValueError, match="boundary crossing"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


def test_boundary_identity_trips_when_gain_inflated_by_safety_counts() -> None:
    b_off, c_off, b_on, c_on = _four()
    c_on["effectiveness"]["gain"] = 6  # protected/no-op query 수를 gain 에 더한 형태
    with pytest.raises(ValueError, match="effectiveness gain"):
        cmp.compare_gate(b_off, c_off, b_on, c_on)


# --------------------------------------------------------------------------
# runner CLI: augmentation on/off
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
        ["run_corpus_eval.py", "--structured-augmentation", "on", "--report-json",
         "/tmp/x/scratchpad/trace.json"],
    )
    args = rce._parse_args()
    assert args.structured_augmentation == "on"
    assert args.report_json == "/tmp/x/scratchpad/trace.json"
