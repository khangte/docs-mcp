"""v3 sealed-split candidate gate 판정기(설계 85 §6~§8).

네 개의 augmentation trace JSON(`baseline_off`, `candidate_off`, `baseline_on`,
`candidate_on`)을 받아 freeze 85 의 gate/final 계약을 정적으로 fail-closed 판정한다.
DB·검색·holdout 개봉은 하지 않는다.

OFF/ON 은 augmentation 축이 아니라 query variants 축이다(88번 §5 C1).

| run          | variants | augmentation |
|--------------|----------|--------------|
| baseline_off | OFF      | OFF          |
| candidate_off| OFF      | ON           |
| baseline_on  | ON       | OFF          |
| candidate_on | ON       | ON           |

판정 순서(엄격): 모든 HARD 를 먼저 끝낸 뒤에만 EFFECTIVENESS 를 본다.

HARD:
 1. eval identity(§2·§6.1.1~2): product/rules/query/split/corpus/candidate-contract
    frozen 일치 + 단일 40자 implementation SHA + 단일 shared-index fingerprint.
 2. execution identity(§6.1.2, 88 §5 C4): arm/variants_enabled/augmentation_enabled
    조합이 위 표와 일치, lexical_field=="text", strategy 가 fallback parity 를
    포함하는 실행축, top_k==10.
 3. ID/metadata(88 §5 C4): 중복 없는 query ID 집합이 frozen gate96/scored120 과
    정확히 같고, 각 row 의 split/category/language/answer_mode/pair metadata 가
    네 report 상호 및 frozen fixture 와 일치.
 4. fallback exactness(§6.1.3, A2): `unaffected_paths.fallback` 이 해당 scope 의
    전체 query ID 를 덮는 `id -> raw rank` map 이고 baseline/candidate 가 exact
    parity. 비었거나 부분 커버리지면 FAIL. exact/document 의 빈 map 은 pinned
    implementation SHA 의 source 경계 + 제품 회귀가 근거인 structural-isolation
    표현으로 유지하지만(§6.2 item 8), 세 map 의 공집합 동일성만으로는 절대
    PASS 하지 않는다 — HARD 8 의 동적 PASS 근거는 이 full-coverage fallback map 이다.
 5. candidate-specific HARD 9항목(§6.2): text/vector arm parity, base-wide RRF
    parity, protected absolute slot, bounded displacement, zero-score no-op,
    no injection/drop, unaffected-path parity, route-pair 10/10(gate) · 12/12(final).
 6. 공통 HARD(§6.1.4~8): C1 loss zero, C1~C7별 hit 순손실 ≤1 · MRR 하락 ≤0.02,
    C6 coverage/complete non-regression, empty-result 증가 0.
 7. (final) holdout HARD(§8.2): holdout Recall@10 OFF/ON non-decline,
    holdout MRR 하락 OFF/ON ≤0.01.
 8. boundary crossing identity(§7.8~9): crossing net == Top-10 recall paired 순증
    == 리포트가 주장하는 effectiveness gain/recall_net.

EFFECTIVENESS(§7 / §8.3, HARD 전항 PASS 후에만):
 - headline Recall@10 / MRR / nDCG@10 을 metrics.py any-hit 정의로 재계산해
   OFF/ON non-decline.
 - Recall@10 OFF/ON `+3pp` 및 hit 순증 gate `+3` · final `+4`.
 - MRR 두 arm 중 최소 하나가 `+0.02` 이상.
 - targeted C2+C3+C5 hit 순증: 한 arm `+3` 이상, 다른 arm `0` 이상.
 - Korean ON hit 순증 `+2` 이상.
 - effective route pair: gate `>=2`; final 은 gate `>=2` · holdout `>=1` · all `>=3`.
 - boundary crossing net gate `+3` · final `+4` 이상.
 - (final) holdout combined(OFF+ON) hit win > loss 이고 최소 1 win.

두 headline 지표(MRR/nDCG)는 단일-관련 근사에서 rank 에 대해 단조라 서로 얽혀
있다. 개별 변조 테스트가 두 지표를 동시에 건드릴 수 있고, 산술적으로 결합된
하한(crossing net 과 recall paired 순증)은 같은 synthetic 에서 분리되지 않는다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_FIXTURE_DIR = Path(__file__).parent
sys.path.insert(0, str(_FIXTURE_DIR))

import run_corpus_eval as rce  # noqa: E402
from metrics import dcg_at, reciprocal_rank  # noqa: E402

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
_TOP_K = 10

# --- freeze 85 threshold (문면 그대로, 재해석 금지) ---
_RECALL_PP_FLOOR = 3.0
_GATE_HIT_NET = 3
_FINAL_HIT_NET = 4
_MRR_ACTIVATION = 0.02
_PER_CAT_HIT_LOSS_MAX = 1
_PER_CAT_MRR_DROP_MAX = 0.02
_TARGETED_NET_FLOOR = 3
_KOREAN_NET_FLOOR = 2
_GATE_CROSS_FLOOR = 3
_FINAL_CROSS_FLOOR = 4
_HOLDOUT_MRR_DROP_MAX = 0.01
_GATE_EFF_PAIR_FLOOR = 2
_FINAL_EFF_PAIR_GATE = 2
_FINAL_EFF_PAIR_HOLDOUT = 1
_FINAL_EFF_PAIR_ALL = 3
_EPS = 1e-9

_TARGETED_CATEGORIES = ("C2-한글패러프레이즈", "C3-영문의역", "C5-decoy구분")
_C1_CATEGORY = "C1-직접키워드"
# arm/variants/augmentation 역할 표(88 §5 C1). positional 인자가 맞아도 이 조합과
# 어긋나면 FAIL.
_ROLE_TABLE = (
    ("baseline", False, False),
    ("candidate", False, True),
    ("baseline", True, False),
    ("candidate", True, True),
)
# strategy 가 fallback rank 를 report 에 채우는 실행축(A2). rrf 단독은 fallback map
# 이 공집합이라 parity 를 실측하지 못한다.
_FALLBACK_PARITY_STRATEGIES = frozenset({"both", "fallback"})


# --------------------------------------------------------------------------
# frozen fixture metadata
# --------------------------------------------------------------------------
def _frozen_meta(scope: str) -> dict[str, dict]:
    """frozen `queries_gate_v3.json` 에서 scope 의 id -> metadata 를 읽는다.

    scope `gate` 는 `split == "gate"` scored 96건, `final` 은 scored 120건
    (gate + holdout). split/category/language/answer_mode/pair metadata 만 담는다.
    """
    raw = json.loads((_FIXTURE_DIR / "queries_gate_v3.json").read_text())
    out: dict[str, dict] = {}
    for r in raw:
        if r.get("evaluation_role") != "scored":
            continue
        if scope == "gate" and r["split"] != "gate":
            continue
        out[r["id"]] = {
            "split": r["split"],
            "category": r["category"],
            "language": r["language"],
            "answer_mode": r["answer_mode"],
            "pair_id": r.get("pair_id"),
            "pair_role": r.get("pair_role"),
        }
    return out


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


def check_execution_roles(reports: list[dict]) -> None:
    """report root 의 실행 역할이 _ROLE_TABLE·text·fallback-parity·top_k 계약과 맞는지."""
    errs: list[str] = []
    for i, (rep, (arm, ve, ae)) in enumerate(zip(reports, _ROLE_TABLE, strict=True)):
        if rep.get("arm") != arm:
            errs.append(f"run[{i}] arm={rep.get('arm')!r} != {arm!r}")
        if bool(rep.get("variants_enabled")) != ve:
            errs.append(f"run[{i}] variants_enabled={rep.get('variants_enabled')!r} != {ve}")
        if bool(rep.get("augmentation_enabled")) != ae:
            errs.append(
                f"run[{i}] augmentation_enabled={rep.get('augmentation_enabled')!r} != {ae}"
            )
        if rep.get("lexical_field") != "text":
            errs.append(f"run[{i}] lexical_field={rep.get('lexical_field')!r} != 'text'")
        if rep.get("strategy") not in _FALLBACK_PARITY_STRATEGIES:
            errs.append(
                f"run[{i}] strategy={rep.get('strategy')!r} 가 fallback parity 실행축 아님 "
                f"(허용 {sorted(_FALLBACK_PARITY_STRATEGIES)})"
            )
        if rep.get("top_k") != _TOP_K:
            errs.append(f"run[{i}] top_k={rep.get('top_k')!r} != {_TOP_K}")
    if errs:
        raise ValueError("execution identity 위반:\n  - " + "\n  - ".join(errs))


def check_id_and_metadata(reports: list[dict], *, scope: str) -> None:
    """중복 없는 query ID 집합 == frozen scope, row metadata == frozen + 상호 일치."""
    meta = _frozen_meta(scope)
    want = set(meta)
    errs: list[str] = []
    meta_keys = ("split", "category", "language", "answer_mode", "pair_id", "pair_role")
    for i, rep in enumerate(reports):
        ids = [r["id"] for r in rep["queries"]]
        if len(ids) != len(set(ids)):
            errs.append(f"run[{i}] 중복 query ID")
        if set(ids) != want:
            missing = sorted(want - set(ids))[:3]
            extra = sorted(set(ids) - want)[:3]
            errs.append(f"run[{i}] ID 집합 != frozen {scope} (missing {missing} extra {extra})")
        for r in rep["queries"]:
            m = meta.get(r["id"])
            if m is None:
                continue
            for k in meta_keys:
                if r.get(k) != m[k]:
                    errs.append(f"run[{i}] {r['id']} {k}={r.get(k)!r} != frozen {m[k]!r}")
    if errs:
        raise ValueError("ID/metadata 위반:\n  - " + "\n  - ".join(errs))


def check_fallback_exactness(
    baseline_off: dict,
    candidate_off: dict,
    baseline_on: dict,
    candidate_on: dict,
    *,
    scope: str,
) -> None:
    """HARD 8 동적 근거: fallback map 이 scope 전체 ID 를 덮고 baseline==candidate.

    exact/document 는 pinned SHA source 경계 + 제품 회귀가 근거인 structural-proof
    표현이므로 빈 map 을 허용하지만, 여기서 fallback 이 비면 세 map 공집합 동일성
    경로가 되어 FAIL 한다.
    """
    meta = _frozen_meta(scope)
    want = set(meta)
    errs: list[str] = []
    arms = (("OFF", baseline_off, candidate_off), ("ON", baseline_on, candidate_on))
    for label, base, cand in arms:
        for tag, rep in ((f"{label}/baseline", base), (f"{label}/candidate", cand)):
            fb = (rep.get("unaffected_paths") or {}).get("fallback") or {}
            if set(fb) != want:
                errs.append(
                    f"{tag} fallback map 이 {scope} 전체 ID 를 덮지 않음 "
                    f"(덮음 {len(set(fb) & want)}/{len(want)})"
                )
        bfb = (base.get("unaffected_paths") or {}).get("fallback") or {}
        cfb = (cand.get("unaffected_paths") or {}).get("fallback") or {}
        diff = sorted(k for k in want if bfb.get(k) != cfb.get(k))
        if diff:
            errs.append(f"{label} fallback rank parity 깨짐: baseline/candidate 불일치 {diff[:5]}")
    if errs:
        raise ValueError("fallback exactness(HARD §6.1.3) 위반:\n  - " + "\n  - ".join(errs))


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


def _ar(row: dict) -> int | None:
    """metrics.py 가 요구하는 int|None 형태의 any-hit answer_rank."""
    r = row.get("answer_rank")
    return r if isinstance(r, int) else None


def _cap11(rank: object) -> int:
    """verdict 69 §3.4 effective-pair: top-10 밖 미검출은 11 로 절단."""
    return rank if isinstance(rank, int) and rank <= _TOP_K else 11


def _hit(rank: object) -> int:
    return 1 if isinstance(rank, int) and rank <= _TOP_K else 0


def _recall(rows: list[dict]) -> float:
    return sum(_hit(r.get("answer_rank")) for r in rows) / len(rows) if rows else 0.0


def _mrr(rows: list[dict]) -> float:
    return sum(reciprocal_rank(_ar(r)) for r in rows) / len(rows) if rows else 0.0


def _ndcg(rows: list[dict]) -> float:
    return sum(dcg_at(_ar(r), _TOP_K) for r in rows) / len(rows) if rows else 0.0


def _hit_net(base_rows: list[dict], cand_rows: list[dict]) -> int:
    base = {r["id"]: _hit(r.get("answer_rank")) for r in base_rows}
    cand = {r["id"]: _hit(r.get("answer_rank")) for r in cand_rows}
    return sum(cand[i] - base[i] for i in base if i in cand)


def _hit_loss(base_rows: list[dict], cand_rows: list[dict]) -> int:
    cand = {r["id"]: _hit(r.get("answer_rank")) for r in cand_rows}
    return sum(
        1 for r in base_rows
        if _hit(r.get("answer_rank")) == 1 and cand.get(r["id"], 0) == 0
    )


def _coverage(row: dict) -> float:
    per = row.get("per_accepted_ranks") or []
    return sum(_hit(x) for x in per) / len(per) if per else 0.0


def _complete(row: dict) -> int:
    per = row.get("per_accepted_ranks") or []
    return 1 if per and all(_hit(x) for x in per) else 0


def _split_rows(report: dict, split: str | None) -> list[dict]:
    if split is None:
        return report["queries"]
    return [r for r in report["queries"] if r["split"] == split]


def _categories(report: dict) -> list[str]:
    seen: list[str] = []
    for r in report["queries"]:
        if r["category"] not in seen:
            seen.append(r["category"])
    return seen


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

    _arms = (("OFF", baseline_off, candidate_off), ("ON", baseline_on, candidate_on))
    for label, base, cand in _arms:
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
            fw, bw = _refs(row["final_wide"]), _refs(row["base_wide"])
            if _max_structured_score(row) == 0.0 and fw != bw:
                errs.append(f"Zero-score no-op: {row['id']} final-wide 가 base-wide 와 다름")
            if sorted(fw) != sorted(bw):
                errs.append(f"No injection/drop: {row['id']} base-wide multiset 변화")

    split_filter = "gate" if route_pairs == _GATE_ROUTE_PAIRS else None
    for label, base, cand in _arms:
        safe, total = _pair_safe_count(base, cand, split_filter)
        if total != route_pairs or safe < route_pairs:
            errs.append(
                f"Pair gate({label}): {safe}/{route_pairs} pair-safe (route pair {total})"
            )

    if errs:
        raise ValueError("candidate HARD 위반:\n  - " + "\n  - ".join(errs))


def check_common_hard(
    baseline_off: dict,
    candidate_off: dict,
    baseline_on: dict,
    candidate_on: dict,
    *,
    mode: str,
) -> None:
    """freeze 85 §6.1.4~8 공통 HARD 를 두 variants arm 각각에 대해 판정한다."""
    errs: list[str] = []
    arms = (("OFF", baseline_off, candidate_off), ("ON", baseline_on, candidate_on))
    for label, base, cand in arms:
        b_rows, c_rows = base["queries"], cand["queries"]
        # C1 loss zero
        b1 = [r for r in b_rows if r["category"] == _C1_CATEGORY]
        c1 = [r for r in c_rows if r["category"] == _C1_CATEGORY]
        loss = _hit_loss(b1, c1)
        if loss != 0:
            errs.append(f"C1 loss zero({label}): {loss}건 top-10 이탈")
        # per-category floor
        for cat in _categories(base):
            bc = [r for r in b_rows if r["category"] == cat]
            cc = [r for r in c_rows if r["category"] == cat]
            if not bc:
                continue
            cat_loss = _hit_loss(bc, cc)
            if cat_loss > _PER_CAT_HIT_LOSS_MAX:
                errs.append(
                    f"Per-category floor({label}/{cat}): hit 순손실 {cat_loss} "
                    f"> {_PER_CAT_HIT_LOSS_MAX}"
                )
            drop = _mrr(bc) - _mrr(cc)
            if drop > _PER_CAT_MRR_DROP_MAX + _EPS:
                errs.append(
                    f"Per-category floor({label}/{cat}): MRR 하락 {drop:.4f} "
                    f"> {_PER_CAT_MRR_DROP_MAX}"
                )
        # C6 coverage/complete non-regression
        c_by_id = _by_id(cand)
        for r in (x for x in b_rows if x.get("answer_mode") == "all"):
            cr = c_by_id.get(r["id"], {})
            if _coverage(cr) < _coverage(r) - _EPS:
                errs.append(
                    f"C6 coverage({label}/{r['id']}): {_coverage(cr):.2f} < {_coverage(r):.2f}"
                )
            if _complete(cr) < _complete(r):
                errs.append(f"C6 complete({label}/{r['id']}): {_complete(cr)} < {_complete(r)}")
        # empty-result 증가 0
        be = sum(1 for r in b_rows if r.get("result_empty"))
        ce = sum(1 for r in c_rows if r.get("result_empty"))
        if ce > be:
            errs.append(f"Empty-result({label}): {ce} > baseline {be}")
    if errs:
        raise ValueError("common HARD 위반:\n  - " + "\n  - ".join(errs))


def check_final_holdout_hard(
    baseline_off: dict,
    candidate_off: dict,
    baseline_on: dict,
    candidate_on: dict,
) -> None:
    """freeze 85 §8.2: sealed holdout Recall non-decline + MRR 하락 ≤0.01(각 arm)."""
    errs: list[str] = []
    arms = (("OFF", baseline_off, candidate_off), ("ON", baseline_on, candidate_on))
    for label, base, cand in arms:
        bh = _split_rows(base, "holdout")
        ch = _split_rows(cand, "holdout")
        if _recall(ch) < _recall(bh) - _EPS:
            errs.append(
                f"Holdout Recall@10({label}): 후보 {_recall(ch):.3f} < baseline {_recall(bh):.3f}"
            )
        drop = _mrr(bh) - _mrr(ch)
        if drop > _HOLDOUT_MRR_DROP_MAX + _EPS:
            errs.append(f"Holdout MRR({label}): 하락 {drop:.4f} > {_HOLDOUT_MRR_DROP_MAX}")
    if errs:
        raise ValueError("final holdout HARD 위반:\n  - " + "\n  - ".join(errs))


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
# EFFECTIVENESS (§7 / §8.3)
# --------------------------------------------------------------------------
def _effective_pairs(base_report: dict, cand_report: dict, split: str | None) -> int:
    """verdict 69 §3.4: non-regression 이면서 root/child 중 하나가 개선된 pair 수."""
    base, cand = _by_id(base_report), _by_id(cand_report)
    members: dict[str, list[str]] = {}
    for qid, row in cand.items():
        if not row.get("pair_id"):
            continue
        if split is not None and row["split"] != split:
            continue
        members.setdefault(row["pair_id"], []).append(qid)
    eff = 0
    for ids in members.values():
        deltas = [_cap11(cand[q]["answer_rank"]) - _cap11(base[q]["answer_rank"]) for q in ids]
        if all(d <= 0 for d in deltas) and any(d < 0 for d in deltas):
            eff += 1
    return eff


def check_effectiveness(
    baseline_off: dict,
    candidate_off: dict,
    baseline_on: dict,
    candidate_on: dict,
    *,
    mode: str,
) -> None:
    """HARD 전항 PASS 후에만 호출. freeze 85 §7(gate) / §8.3(final) 하한을 판정."""
    errs: list[str] = []
    arms = (("OFF", baseline_off, candidate_off), ("ON", baseline_on, candidate_on))
    hit_floor = _GATE_HIT_NET if mode == "gate" else _FINAL_HIT_NET
    cross_floor = _GATE_CROSS_FLOOR if mode == "gate" else _FINAL_CROSS_FLOOR

    # 1) headline non-decline (nDCG -> MRR -> Recall). 단일-관련 근사에서 MRR/nDCG 는
    #    rank 에 단조라 개별 변조가 둘을 함께 건드릴 수 있다.
    for label, base, cand in arms:
        br, cr = base["queries"], cand["queries"]
        if _ndcg(cr) < _ndcg(br) - _EPS:
            errs.append(f"nDCG@10 non-decline({label}): {_ndcg(cr):.4f} < baseline {_ndcg(br):.4f}")
        if _mrr(cr) < _mrr(br) - _EPS:
            errs.append(f"MRR non-decline({label}): {_mrr(cr):.4f} < baseline {_mrr(br):.4f}")
        if _recall(cr) < _recall(br) - _EPS:
            errs.append(
                f"Recall@10 non-decline({label}): {_recall(cr):.4f} < baseline {_recall(br):.4f}"
            )
    if errs:
        raise ValueError("EFFECTIVENESS 위반:\n  - " + "\n  - ".join(errs))

    # 2) Recall@10 activation: +3pp 및 hit 순증 하한
    for label, base, cand in arms:
        dpp = (_recall(cand["queries"]) - _recall(base["queries"])) * 100
        if dpp < _RECALL_PP_FLOOR - _EPS:
            errs.append(f"Recall@10({label}): +{dpp:.2f}pp < +{_RECALL_PP_FLOOR}pp")
        net = _hit_net(base["queries"], cand["queries"])
        if net < hit_floor:
            errs.append(f"Recall@10({label}): hit 순증 {net:+d} < +{hit_floor}")

    # 3) MRR activation: 최소 한 arm 이 +0.02 이상
    mrr_deltas = [_mrr(c["queries"]) - _mrr(b["queries"]) for _, b, c in arms]
    if all(d < _MRR_ACTIVATION - _EPS for d in mrr_deltas):
        errs.append(
            f"MRR activation: 두 arm 모두 +{_MRR_ACTIVATION} 미만 "
            f"({[round(d, 4) for d in mrr_deltas]})"
        )

    # 4) targeted C2+C3+C5: 한 arm +3 이상, 다른 arm 0 이상
    tnets = []
    for _, base, cand in arms:
        bt = [r for r in base["queries"] if r["category"] in _TARGETED_CATEGORIES]
        ct = [r for r in cand["queries"] if r["category"] in _TARGETED_CATEGORIES]
        tnets.append(_hit_net(bt, ct))
    if not (max(tnets) >= _TARGETED_NET_FLOOR and min(tnets) >= 0):
        errs.append(
            f"Targeted C2+C3+C5: 순증 {tnets} "
            f"(한 arm >= +{_TARGETED_NET_FLOOR}, 다른 arm >= 0)"
        )

    # 5) Korean ON: hit 순증 +2 이상
    bk = [r for r in baseline_on["queries"] if r["language"] == "ko"]
    ck = [r for r in candidate_on["queries"] if r["language"] == "ko"]
    kn = _hit_net(bk, ck)
    if kn < _KOREAN_NET_FLOOR:
        errs.append(f"Korean ON: hit 순증 {kn:+d} < +{_KOREAN_NET_FLOOR}")

    # 6) effective route pair 하한
    if mode == "gate":
        for label, base, cand in arms:
            ep = _effective_pairs(base, cand, "gate")
            if ep < _GATE_EFF_PAIR_FLOOR:
                errs.append(f"Effective route pair({label}): gate {ep} < {_GATE_EFF_PAIR_FLOOR}")
    else:
        for label, base, cand in arms:
            g = _effective_pairs(base, cand, "gate")
            h = _effective_pairs(base, cand, "holdout")
            a = _effective_pairs(base, cand, None)
            if g < _FINAL_EFF_PAIR_GATE:
                errs.append(f"Effective route pair({label}): gate {g} < {_FINAL_EFF_PAIR_GATE}")
            if h < _FINAL_EFF_PAIR_HOLDOUT:
                errs.append(
                    f"Effective route pair({label}): holdout {h} < {_FINAL_EFF_PAIR_HOLDOUT}"
                )
            if a < _FINAL_EFF_PAIR_ALL:
                errs.append(f"Effective route pair({label}): all {a} < {_FINAL_EFF_PAIR_ALL}")

    # 7) boundary crossing net 하한
    for label, cand in (("OFF", candidate_off), ("ON", candidate_on)):
        cn = crossing_net(cand["queries"])
        if cn < cross_floor:
            errs.append(f"Boundary crossing({label}): net {cn:+d} < +{cross_floor}")

    # 8) (final) holdout combined 방향성
    if mode == "final":
        win = loss = 0
        for _, base, cand in arms:
            bmap = {r["id"]: _hit(r.get("answer_rank")) for r in _split_rows(base, "holdout")}
            for r in _split_rows(cand, "holdout"):
                d = _hit(r.get("answer_rank")) - bmap.get(r["id"], 0)
                if d > 0:
                    win += 1
                elif d < 0:
                    loss += 1
        if not (win > loss and win >= 1):
            errs.append(f"Holdout combined: win {win} loss {loss} (win>loss 이고 >=1 win)")

    if errs:
        raise ValueError("EFFECTIVENESS 위반:\n  - " + "\n  - ".join(errs))


# --------------------------------------------------------------------------
# top-level comparators
# --------------------------------------------------------------------------
def check_scope(reports: list[dict], *, mode: str) -> None:
    """gate 모드는 gate 전용 split, final 모드는 all/final scope 를 요구한다."""
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


def _compare(
    baseline_off: dict,
    candidate_off: dict,
    baseline_on: dict,
    candidate_on: dict,
    *,
    mode: str,
) -> None:
    reports = [baseline_off, candidate_off, baseline_on, candidate_on]
    scope = "gate" if mode == "gate" else "final"

    # ---- HARD (freeze 85 §6 / §8.2) : EFFECTIVENESS 앞에서 전부 판정 ----
    check_eval_identity(reports)
    check_execution_roles(reports)
    check_scope(reports, mode=mode)
    check_id_and_metadata(reports, scope=scope)
    check_fallback_exactness(
        baseline_off, candidate_off, baseline_on, candidate_on, scope=scope
    )
    route_pairs = _GATE_ROUTE_PAIRS if mode == "gate" else _FINAL_ROUTE_PAIRS
    _run_hard(baseline_off, candidate_off, baseline_on, candidate_on, route_pairs)
    check_common_hard(baseline_off, candidate_off, baseline_on, candidate_on, mode=mode)
    if mode == "final":
        check_final_holdout_hard(baseline_off, candidate_off, baseline_on, candidate_on)
    check_boundary_identity(candidate_off)
    check_boundary_identity(candidate_on)

    # ---- EFFECTIVENESS (freeze 85 §7 / §8.3) : HARD 전항 PASS 후에만 ----
    check_effectiveness(
        baseline_off, candidate_off, baseline_on, candidate_on, mode=mode
    )


def compare_gate(
    baseline_off: dict, candidate_off: dict, baseline_on: dict, candidate_on: dict
) -> None:
    """gate 96 HARD 전항 + EFFECTIVENESS 를 판정한다(FAIL 시 ValueError)."""
    _compare(baseline_off, candidate_off, baseline_on, candidate_on, mode="gate")


def compare_final(
    baseline_off: dict, candidate_off: dict, baseline_on: dict, candidate_on: dict
) -> None:
    """holdout 개봉 뒤 final 120 HARD + holdout safety + final EFFECTIVENESS 를 판정한다."""
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
    print(f"PASS ({args.mode}) — HARD 전항 + EFFECTIVENESS 통과")


if __name__ == "__main__":
    main()
