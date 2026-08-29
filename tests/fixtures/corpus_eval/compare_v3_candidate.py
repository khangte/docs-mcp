"""v3 sealed-split candidate gate 판정기(설계 85 §6~§7).

네 개의 augmentation trace JSON(`baseline_off`, `candidate_off`, `baseline_on`,
`candidate_on`)을 받아 다음을 정적으로 판정한다. DB·검색은 실행하지 않는다.

1. eval identity(§2·§6.1.1~2): product/rules/query/split/corpus/candidate-contract
   가 frozen 값과 일치하고, 네 run 이 하나의 40자 implementation SHA 와 하나의
   shared-index fingerprint 를 공유한다.
2. candidate-specific HARD 9항목(§6.2): text/vector arm parity, base-wide RRF
   parity, protected absolute slot, bounded displacement, zero-score no-op,
   no injection/drop, unaffected-path parity, gate route-pair 10/10.
3. boundary crossing identity(§7.8~9): `base 11 -> final 10` gain 에서
   `base 10 -> final 11` loss 를 뺀 crossing net 이 Top-10 recall 의 paired
   순증과 정확히 같고, 리포트가 주장하는 effectiveness gain/recall_net 과도
   일치한다. protected/no-op 수를 gain 에 더한 리포트는 여기서 FAIL 한다.

`gate` 모드는 split 이 gate 전용이어야 하며 holdout row 가 있으면 거부한다.
`final` 모드는 holdout 을 허용하고 route-pair 를 12/12 로 본다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import run_corpus_eval as rce  # noqa: E402

# --- 구현 계획 87 §0 frozen identity bundle ---
_PRODUCT_SHA = "961bccad9d7d7f169ea5ee17c81581782c441bec"
_RULES_SHA = "dbc29008aa9803fd708bf619d263f76925e4d2a6"
_QUERY_SHA = "1da41901a225990492ead8215eb6a5bfde8afde987cafb60f0f74d03cbd84fdf"
_SPLIT_SHA = "701c43479425848c7af8f74360b88adb8f375d8bc801986ea2f684b5d45541e6"

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_GATE_ROUTE_PAIRS = 10
_FINAL_ROUTE_PAIRS = 12
_MAX_PROMOTION = rce._V3_CANDIDATE_CONTRACT["MAX_STRUCTURED_PROMOTION"]
_MISS_RANK = 10**9


# --------------------------------------------------------------------------
# eval identity
# --------------------------------------------------------------------------
def check_eval_identity(reports: list[dict]) -> None:
    """네 run 의 identity 블록이 frozen 값과 일치하고 서로 동일한지 검사한다."""
    errs: list[str] = []
    impl_shas: set[str] = set()
    fingerprints: set[str] = set()
    for i, rep in enumerate(reports):
        ident = rep.get("identity", {})
        for key, want in (
            ("product_source_sha", _PRODUCT_SHA),
            ("rules_git_sha", _RULES_SHA),
            ("query_sha256", _QUERY_SHA),
            ("split_sha256", _SPLIT_SHA),
        ):
            if ident.get(key) != want:
                errs.append(f"run[{i}] {key} != frozen (실제 {ident.get(key)!r})")
        if ident.get("corpus_sha256") != rce._V3_CORPUS_SHA256:
            errs.append(f"run[{i}] corpus_sha256 != frozen")
        if ident.get("candidate_contract") != rce._V3_CANDIDATE_CONTRACT:
            errs.append(f"run[{i}] candidate_contract != frozen")
        impl = ident.get("implementation_git_sha")
        if isinstance(impl, str) and _HEX40.match(impl):
            impl_shas.add(impl)
        else:
            errs.append(f"run[{i}] implementation_git_sha 가 40자 hex 아님: {impl!r}")
        fp = ident.get("shared_index_fingerprint")
        if isinstance(fp, str) and _HEX64.match(fp):
            fingerprints.add(fp)
        else:
            errs.append(f"run[{i}] shared_index_fingerprint 가 64자 hex 아님: {fp!r}")
    if len(impl_shas) > 1:
        errs.append(f"implementation_git_sha 가 run 마다 다름: {sorted(impl_shas)}")
    if len(fingerprints) > 1:
        errs.append(f"shared_index_fingerprint 가 run 마다 다름: {sorted(fingerprints)}")
    if errs:
        raise ValueError("eval identity 위반:\n  - " + "\n  - ".join(errs))


# --------------------------------------------------------------------------
# row helpers
# --------------------------------------------------------------------------
def _by_id(report: dict) -> dict[str, dict]:
    return {r["id"]: r for r in report["queries"]}


def _refs(entries: list[dict]) -> list[str]:
    return [e["ref_id"] for e in entries]


def _rank_map(entries: list[dict]) -> dict[str, int]:
    return {e["ref_id"]: e["rank"] for e in entries}


def _max_structured_score(row: dict) -> float:
    return max((s["score"] for s in row.get("structured_scores", [])), default=0.0)


def _cap(rank: object) -> int:
    return rank if isinstance(rank, int) else _MISS_RANK


# --------------------------------------------------------------------------
# candidate-specific HARD 9항목
# --------------------------------------------------------------------------
def _check_arm_parity(
    baseline: dict, candidate: dict, field: str, label: str, errs: list[str]
) -> None:
    base_rows, cand_rows = _by_id(baseline), _by_id(candidate)
    for qid, brow in base_rows.items():
        if brow.get(field) != cand_rows.get(qid, {}).get(field):
            errs.append(f"{label}: {qid} 에서 baseline/candidate 불일치")


def _pair_safe_count(baseline: dict, candidate: dict, split_filter: str | None) -> tuple[int, int]:
    base_rows, cand_rows = _by_id(baseline), _by_id(candidate)
    members: dict[str, list[str]] = {}
    for qid, row in cand_rows.items():
        if not row.get("pair_id"):
            continue
        if split_filter is not None and row["split"] != split_filter:
            continue
        members.setdefault(row["pair_id"], []).append(qid)
    safe = 0
    for ids in members.values():
        if all(_cap(cand_rows[q]["answer_rank"]) <= _cap(base_rows[q]["answer_rank"]) for q in ids):
            safe += 1
    return safe, len(members)


def _run_hard(
    baseline_off: dict,
    candidate_off: dict,
    baseline_on: dict,
    candidate_on: dict,
    route_pairs: int,
) -> None:
    errs: list[str] = []

    for label, base, cand in (("OFF", baseline_off, candidate_off), ("ON", baseline_on, candidate_on)):
        _check_arm_parity(base, cand, "keyword", "Text-arm parity", errs)
        _check_arm_parity(base, cand, "vector", "Vector-arm parity", errs)
        _check_arm_parity(base, cand, "base_wide", "Base-wide RRF parity", errs)
        if base.get("unaffected_paths") != cand.get("unaffected_paths"):
            errs.append(f"Unaffected-path parity: {label} 에서 exact/fallback/document 결과 불일치")

    for cand in (candidate_off, candidate_on):
        for row in cand["queries"]:
            base_rank = _rank_map(row["base_wide"])
            final_rank = _rank_map(row["final_wide"])
            for ref in row.get("protected_ref_ids", []):
                if ref in base_rank and base_rank[ref] != final_rank.get(ref):
                    errs.append(
                        f"Protected absolute-slot: {row['id']} {ref} "
                        f"{base_rank[ref]}->{final_rank.get(ref)}"
                    )
            for ref, br in base_rank.items():
                if ref in final_rank and abs(final_rank[ref] - br) > _MAX_PROMOTION:
                    errs.append(
                        f"Bounded displacement: {row['id']} {ref} {br}->{final_rank[ref]}"
                    )
            if _max_structured_score(row) == 0.0 and _refs(row["final_wide"]) != _refs(row["base_wide"]):
                errs.append(f"Zero-score no-op: {row['id']} final-wide 가 base-wide 와 다름")
            if sorted(_refs(row["final_wide"])) != sorted(_refs(row["base_wide"])):
                errs.append(f"No injection/drop: {row['id']} base-wide multiset 변화")

    for label, base, cand in (("OFF", baseline_off, candidate_off), ("ON", baseline_on, candidate_on)):
        safe, total = _pair_safe_count(base, cand, "gate" if route_pairs == _GATE_ROUTE_PAIRS else None)
        if total != route_pairs or safe < route_pairs:
            errs.append(f"Pair gate({label}): {safe}/{route_pairs} pair-safe (route pair {total}개)")

    if errs:
        raise ValueError("candidate HARD 위반:\n  - " + "\n  - ".join(errs))


# --------------------------------------------------------------------------
# boundary crossing identity (§7.8~9)
# --------------------------------------------------------------------------
def crossing_net(rows: list[dict]) -> int:
    """`base 11 -> final 10` gain 에서 `base 10 -> final 11` loss 를 뺀 값."""
    up = sum(1 for r in rows if r.get("base_answer_rank") == 11 and r.get("answer_rank") == 10)
    down = sum(1 for r in rows if r.get("base_answer_rank") == 10 and r.get("answer_rank") == 11)
    return up - down


def recall_net(rows: list[dict]) -> int:
    """Top-10 recall 의 paired 순증(진입 - 이탈)."""
    gain = sum(
        1 for r in rows
        if _cap(r.get("base_answer_rank")) > 10 and _cap(r.get("answer_rank")) <= 10
    )
    loss = sum(
        1 for r in rows
        if _cap(r.get("base_answer_rank")) <= 10 and _cap(r.get("answer_rank")) > 10
    )
    return gain - loss


def check_boundary_identity(candidate_report: dict) -> None:
    """crossing net == recall net == 리포트가 주장하는 gain/recall_net 인지 검사한다."""
    rows = candidate_report["queries"]
    c_net = crossing_net(rows)
    r_net = recall_net(rows)
    errs: list[str] = []
    if c_net != r_net:
        errs.append(f"boundary crossing net {c_net} != Top-10 recall paired 순증 {r_net}")
    eff = candidate_report.get("effectiveness", {})
    if eff.get("recall_net") != r_net:
        errs.append(f"effectiveness recall_net {eff.get('recall_net')!r} != 실측 {r_net}")
    if eff.get("gain") != c_net:
        errs.append(
            f"effectiveness gain {eff.get('gain')!r} != crossing net {c_net} "
            f"(protected/no-op 수를 gain 에 더했는지 확인)"
        )
    if errs:
        raise ValueError("boundary/effectiveness identity 위반:\n  - " + "\n  - ".join(errs))


# --------------------------------------------------------------------------
# top-level comparators
# --------------------------------------------------------------------------
def _compare(
    baseline_off: dict,
    candidate_off: dict,
    baseline_on: dict,
    candidate_on: dict,
    *,
    mode: str,
) -> None:
    reports = [baseline_off, candidate_off, baseline_on, candidate_on]
    check_eval_identity(reports)

    errs: list[str] = []
    for i, rep in enumerate(reports):
        scope = rep.get("split_scope")
        if mode == "gate":
            if scope != "gate":
                errs.append(f"run[{i}] split_scope={scope!r} (gate 여야 함)")
            for row in rep["queries"]:
                if row["split"] != "gate":
                    errs.append(f"run[{i}] holdout row {row['id']} 가 gate 판정에 섞임")
        elif scope not in ("all", "final"):
            errs.append(f"run[{i}] split_scope={scope!r} (final 판정은 all/final)")
    if errs:
        raise ValueError(f"{mode} mode 스코프 위반:\n  - " + "\n  - ".join(errs))

    route_pairs = _GATE_ROUTE_PAIRS if mode == "gate" else _FINAL_ROUTE_PAIRS
    _run_hard(baseline_off, candidate_off, baseline_on, candidate_on, route_pairs)
    check_boundary_identity(candidate_off)
    check_boundary_identity(candidate_on)


def compare_gate(
    baseline_off: dict, candidate_off: dict, baseline_on: dict, candidate_on: dict
) -> None:
    """gate 96 HARD 전항 + boundary identity 를 판정한다(FAIL 시 ValueError)."""
    _compare(baseline_off, candidate_off, baseline_on, candidate_on, mode="gate")


def compare_final(
    baseline_off: dict, candidate_off: dict, baseline_on: dict, candidate_on: dict
) -> None:
    """holdout 개봉 뒤 final 120 HARD + boundary identity 를 판정한다."""
    _compare(baseline_off, candidate_off, baseline_on, candidate_on, mode="final")


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-off", required=True)
    parser.add_argument("--candidate-off", required=True)
    parser.add_argument("--baseline-on", required=True)
    parser.add_argument("--candidate-on", required=True)
    parser.add_argument("--mode", choices=("gate", "final"), default="gate")
    args = parser.parse_args()
    fn = compare_gate if args.mode == "gate" else compare_final
    try:
        fn(
            _load(args.baseline_off),
            _load(args.candidate_off),
            _load(args.baseline_on),
            _load(args.candidate_on),
        )
    except ValueError as exc:
        print(f"FAIL ({args.mode})\n{exc}")
        raise SystemExit(1) from exc
    print(f"PASS ({args.mode}) — HARD 전항 + boundary identity 통과")


if __name__ == "__main__":
    main()
