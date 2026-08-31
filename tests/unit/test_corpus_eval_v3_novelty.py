"""`tests/fixtures/corpus_eval/run_corpus_eval.py` 의 v3 프리즈 로더 로직 단위 테스트.

85번 설계 §3(v3 신규성 계약)·§4(pair block 불교집합)·§5(schema 3 manifest)·§6(manifest
선택) 구현을 검증한다. DB·검색·holdout 은 건드리지 않는다 — frozen fixture 파일과 정적
검증기만 본다.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).parents[1] / "fixtures" / "corpus_eval"
sys.path.insert(0, str(_DIR))

import run_corpus_eval as rce  # noqa: E402


@pytest.fixture(scope="module")
def ctx() -> dict:
    """실제 frozen corpus 로 valid_by_doc / corpus_sha 를 만든다."""
    manifest = rce._load_manifest()
    texts = rce._load_corpus_texts(manifest)
    return {
        "vbd": rce._valid_endpoints_by_doc(texts),
        "corpus_sha": {e["source_key"]: e["content_sha256"] for e in manifest},
    }


def _v3_raw() -> list[dict]:
    return json.loads((_DIR / "queries_gate_v3.json").read_text())


# --- §6: manifest 선택 -----------------------------------------------------
def test_manifest_map_registers_v3() -> None:
    assert rce._MANIFEST_BY_QUERY_FILE["queries_gate_v3.json"] == "gate_manifest_v3.json"


def test_v1_v2_still_load(ctx: dict) -> None:
    """v1·v2 프리즈는 회귀 없이 계속 통과해야 한다."""
    for name in ("queries_gate_v1.json", "queries_gate_v2.json"):
        qs = rce._load_and_validate_queries(
            ctx["vbd"], _DIR / name, "all", ctx["corpus_sha"]
        )
        assert len(qs) == 120


def test_v3_file_loads_and_passes_all_static_gates(ctx: dict) -> None:
    qs = rce._load_and_validate_queries(
        ctx["vbd"], _DIR / "queries_gate_v3.json", "all", ctx["corpus_sha"]
    )
    assert len(qs) == 120  # gate96 + holdout24
    raw = _v3_raw()
    rce._validate_v3_novelty(raw)
    rce._validate_v3_manifest(_DIR / "gate_manifest_v3.json")
    rce._verify_manifest_shas(
        _DIR / "queries_gate_v3.json", raw, _DIR / "gate_manifest_v3.json"
    )


def test_v3_split_and_role_distribution() -> None:
    raw = _v3_raw()
    scored = [r for r in raw if r["evaluation_role"] == "scored"]
    diag = [r for r in raw if r["evaluation_role"] == "diagnostic"]
    assert len(scored) == 120 and len(diag) == 4
    assert sum(r["split"] == "gate" for r in scored) == 96
    assert sum(r["split"] == "holdout" for r in scored) == 24
    cats = {}
    for r in scored:
        cats.setdefault(r["category"], [0, 0])
        cats[r["category"]][0 if r["split"] == "gate" else 1] += 1
    assert cats == {
        "C1-직접키워드": [10, 2], "C2-한글패러프레이즈": [19, 5],
        "C3-영문의역": [14, 4], "C4-흔한토큰범람": [10, 2],
        "C5-decoy구분": [19, 5], "C6-다개념": [10, 2],
        "C7-대형엔드포인트세부": [14, 4],
    }
    assert sum(r["domain"] == "stripe" for r in scored) == 60
    assert sum(r["domain"] == "github" for r in scored) == 60
    langs = {ln: sum(r["language"] == ln for r in scored) for ln in ("ko", "en", "code")}
    assert langs == {"ko": 58, "en": 58, "code": 4}


def test_v3_has_twelve_route_pairs() -> None:
    raw = _v3_raw()
    pids = {r["pair_id"] for r in raw if r.get("pair_id")}
    assert sorted(pids) == [f"v3p{i:02d}" for i in range(1, 13)]


def test_v3_c6_rows_are_all_of_with_two_accepted() -> None:
    for r in _v3_raw():
        if r["category"] == "C6-다개념":
            assert r["answer_mode"] == "all"
            assert len(r["accepted"]) == 2


def test_v3_accepted_tuple_count_is_136() -> None:
    tuples = [
        (a["doc"], a["method"], a["path"])
        for r in _v3_raw()
        for a in r["accepted"]
    ]
    assert len(tuples) == 136
    assert len(set(tuples)) == 136  # 내부 충돌 0


# --- §3/§4: 각 신규성 규칙이 위반에 대해 죽는지 --------------------------
def test_novelty_trips_on_reused_v1_accepted_tuple() -> None:
    raw = _v3_raw()
    raw[5]["accepted"] = [{"doc": "stripe", "method": "GET", "path": "/v1/invoices/{invoice}"}]
    with pytest.raises(ValueError, match="v1/v2 재사용"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_reused_v2_query() -> None:
    raw = _v3_raw()
    v2_first = json.loads((_DIR / "queries_gate_v2.json").read_text())[0]["query"]
    raw[0]["query"] = v2_first
    with pytest.raises(ValueError, match="legacy/v1/v2 와 중복"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_internal_accepted_tuple_share() -> None:
    raw = _v3_raw()
    raw[1]["accepted"] = copy.deepcopy(raw[0]["accepted"])
    with pytest.raises(ValueError, match="중복"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_bad_v3g_id() -> None:
    raw = _v3_raw()
    raw[0]["id"] = "g001"
    with pytest.raises(ValueError, match="id"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_v3p_pair_id_pattern() -> None:
    raw = _v3_raw()
    for r in raw:
        if r.get("pair_id") == "v3p01":
            r["pair_id"] = "p01"
    with pytest.raises(ValueError, match="pair id"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_pair_ids_not_exactly_twelve() -> None:
    raw = _v3_raw()
    for r in raw:
        if r.get("pair_id") == "v3p12":
            r["pair_id"] = "v3p13"
    with pytest.raises(ValueError, match="v3p01~v3p12 정확히"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_nfkc_equivalent_query_dup() -> None:
    raw = _v3_raw()
    raw[0]["query"] = "ﬁle a webhook probe"  # U+FB01 ligature
    raw[1]["query"] = "file a webhook probe"
    with pytest.raises(ValueError, match="정규화 query"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_pair_family_reused_from_prior() -> None:
    """§4.3: pair block route family 는 v1/v2 pair family 와 겹치면 안 된다."""
    raw = _v3_raw()
    v2 = json.loads((_DIR / "queries_gate_v2.json").read_text())
    v2_pair = next(r for r in v2 if r.get("pair_id"))
    tgt = next(r for r in raw if r.get("pair_id"))
    for r in raw:
        if r.get("pair_id") == tgt["pair_id"]:
            r["domain"] = v2_pair["domain"]
            r["accepted"] = [dict(v2_pair["accepted"][0])]
    with pytest.raises(ValueError, match="route family|pair accepted endpoint"):
        rce._validate_v3_novelty(raw)


# --- §5: schema 3 manifest — 값 자체를 잠근다 --------------------------
def _man() -> dict:
    return json.loads((_DIR / "gate_manifest_v3.json").read_text())


def _write(tmp_path: Path, man: dict) -> Path:
    p = tmp_path / "m.json"
    p.write_text(json.dumps(man, ensure_ascii=False))
    return p


def test_v3_manifest_frozen_file_passes() -> None:
    rce._validate_v3_manifest(_DIR / "gate_manifest_v3.json")


def test_v3_manifest_requires_schema_version_three(tmp_path: Path) -> None:
    man = _man()
    man["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version"):
        rce._validate_v3_manifest(_write(tmp_path, man))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("dataset_version", "v2"),
        ("status", "draft"),
        ("query_file", "queries_gate_v2.json"),
        ("baseline_lexical_field", "structured"),
        ("candidate_lexical_field", "structured"),
        (
            "rules",
            "docs/architect-review/84_text_primary_bounded_structured_augmentation_design.md",
        ),
        ("rules_git_sha", "0" * 40),
        ("product_source_sha", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"),
    ],
)
def test_v3_manifest_scalar_values_are_locked(tmp_path: Path, field: str, bad: str) -> None:
    man = _man()
    man[field] = bad
    with pytest.raises(ValueError, match=field):
        rce._validate_v3_manifest(_write(tmp_path, man))


def test_v3_manifest_corpus_sha_locked(tmp_path: Path) -> None:
    man = _man()
    man["corpus_sha256"]["stripe"] = "f" * 64
    with pytest.raises(ValueError, match="corpus_sha256"):
        rce._validate_v3_manifest(_write(tmp_path, man))


def test_v3_manifest_novelty_sha_locked(tmp_path: Path) -> None:
    man = _man()
    man["novelty_against"]["v2_query_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="novelty_against"):
        rce._validate_v3_manifest(_write(tmp_path, man))


def test_v3_manifest_novelty_sha_missing_key(tmp_path: Path) -> None:
    man = _man()
    man["novelty_against"].pop("v2_query_sha256")
    with pytest.raises(ValueError, match="novelty_against"):
        rce._validate_v3_manifest(_write(tmp_path, man))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(total=125),
        lambda c: c["category"].update({"C5-decoy구분": 23}),
        lambda c: c["category_gate_holdout"].update({"C2-한글패러프레이즈": [20, 4]}),
        lambda c: c["language"].update(ko=57, en=59),
        lambda c: c["pairs"].update(gate=11, holdout=1),
        lambda c: c["corpus"].update(stripe=61, github=59),
    ],
)
def test_v3_manifest_counts_are_exact(tmp_path: Path, mutate) -> None:
    man = _man()
    mutate(man["counts"])
    with pytest.raises(ValueError, match="counts 불일치"):
        rce._validate_v3_manifest(_write(tmp_path, man))


def test_v3_manifest_requires_max_structured_promotion_one(tmp_path: Path) -> None:
    man = _man()
    man["candidate_contract"]["MAX_STRUCTURED_PROMOTION"] = 2
    with pytest.raises(ValueError, match="MAX_STRUCTURED_PROMOTION"):
        rce._validate_v3_manifest(_write(tmp_path, man))


def test_v3_manifest_requires_structured_query_source_original_only(tmp_path: Path) -> None:
    man = _man()
    man["candidate_contract"]["structured_query_source"] = "query_plus_variants"
    with pytest.raises(ValueError, match="structured_query_source"):
        rce._validate_v3_manifest(_write(tmp_path, man))


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("design_path", "docs/architect-review/85_text_primary_augmentation_v3_freeze_design.md"),
        ("text_lexical_arm", "secondary"),
        ("structured_evidence_weights", ["A", "B", "C", "D"]),
        ("structured_evidence_excludes", ["D", "query_variant"]),
        ("structured_evidence_scope", "all base-wide candidates"),
        ("candidate_injection", "allowed"),
        ("protected_slots", "movable"),
        ("allowed_moves", "any reorder"),
        ("rrf_k", 30),
        ("lexical_arm_weight", 2),
        ("vector_arm_weight", 2),
    ],
)
def test_v3_manifest_candidate_contract_values_are_locked(tmp_path: Path, key: str, bad) -> None:
    man = _man()
    man["candidate_contract"][key] = bad
    with pytest.raises(ValueError, match=f"candidate_contract.{re.escape(key)}"):
        rce._validate_v3_manifest(_write(tmp_path, man))


def test_v3_manifest_frozen_constants_are_locked(tmp_path: Path) -> None:
    man = _man()
    man["candidate_contract"]["frozen_constants"]["_STRUCTURED_RANK_WEIGHTS"] = [0.1, 0.3, 0.5, 1.0]
    with pytest.raises(ValueError, match="frozen_constants"):
        rce._validate_v3_manifest(_write(tmp_path, man))


def test_v3_manifest_frozen_constants_missing_trips(tmp_path: Path) -> None:
    man = _man()
    man["candidate_contract"].pop("frozen_constants")
    with pytest.raises(ValueError, match="frozen_constants"):
        rce._validate_v3_manifest(_write(tmp_path, man))


# --- §3.3/§3.4: 세부 분포 고정 + negative -------------------------------
def test_novelty_trips_on_wrong_diagnostic_distribution() -> None:
    raw = _v3_raw()
    for r in raw:
        if r["evaluation_role"] == "diagnostic" and r["domain"] == "github":
            r["domain"] = "stripe"
    with pytest.raises(ValueError, match="diagnostic (domain|language) 분포"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_wrong_pair_split_distribution() -> None:
    raw = _v3_raw()
    # gate pair 하나를 통째로 holdout 으로 옮긴다 → 10/2 가 9/3 이 된다
    tgt = next(r["pair_id"] for r in raw if r.get("pair_id") and r["split"] == "gate")
    for r in raw:
        if r.get("pair_id") == tgt:
            r["split"] = "holdout"
    with pytest.raises(ValueError, match="pair split"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_wrong_pair_category_distribution() -> None:
    raw = _v3_raw()
    tgt = next(r["pair_id"] for r in raw
              if r.get("pair_id") and r["category"] == "C5-decoy구분")
    for r in raw:
        if r.get("pair_id") == tgt:
            r["category"] = "C1-직접키워드"
    with pytest.raises(ValueError, match="pair category"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_pair_member_category_mismatch() -> None:
    """root/child 가 서로 다른 category 면 validator 가 명시적으로 잡는다
    (대표행을 prs[0] 로 뽑던 시절엔 조용히 통과할 수 있었다)."""
    raw = _v3_raw()
    tgt = next(r["pair_id"] for r in raw if r.get("pair_id"))
    for r in raw:
        if r.get("pair_id") == tgt and r["pair_role"] == "child":
            r["category"] = "C7-대형엔드포인트세부"
    with pytest.raises(ValueError, match=r"root/child category 불일치"):
        rce._validate_v3_novelty(raw)


def test_novelty_trips_on_pair_domain_detail_lock() -> None:
    """holdout pair 하나의 domain 을 양 멤버 모두 뒤집으면 전체 6/6 과
    holdout 1/1 축이 둘 다 깨져야 한다."""
    raw = _v3_raw()
    tgt = next(r["pair_id"] for r in raw
              if r.get("pair_id") and r["split"] == "holdout"
              and r["domain"] == "stripe")
    for r in raw:
        if r.get("pair_id") == tgt:
            r["domain"] = "github"
    with pytest.raises(ValueError) as ei:
        rce._validate_v3_novelty(raw)
    msg = str(ei.value)
    assert "pair domain 6/6 아님" in msg
    assert "holdout pair domain 1/1 아님" in msg


def test_novelty_trips_on_pair_language_detail_lock() -> None:
    """holdout pair 하나의 language 를 양 멤버 모두 뒤집으면 전체 6/6 과
    holdout 1/1 축이 둘 다 깨져야 한다."""
    raw = _v3_raw()
    tgt = next(r["pair_id"] for r in raw
              if r.get("pair_id") and r["split"] == "holdout"
              and r["language"] == "en")
    for r in raw:
        if r.get("pair_id") == tgt:
            r["language"] = "ko"
    with pytest.raises(ValueError) as ei:
        rce._validate_v3_novelty(raw)
    msg = str(ei.value)
    assert "pair language 6/6 아님" in msg
    assert "holdout pair language 1/1 아님" in msg


# --- §4.2 vs §4.3: family 불교집합 scope --------------------------------
def test_general_scored_family_reuse_is_allowed() -> None:
    """§4.3 은 12개 pair block 에만 route-family 불교집합을 건다. 일반 scored single
    두 건이 같은 route family 를 공유해도(엔드포인트는 서로 다르면) 통과해야 한다.
    """
    raw = _v3_raw()
    singles = [r for r in raw
               if not r.get("pair_id") and r["evaluation_role"] == "scored"
               and r["category"] != "C6-다개념" and len(r["accepted"]) == 1]
    donor, acceptor = singles[0], singles[1]
    dpath = donor["accepted"][0]["path"]
    acceptor["accepted"] = [{
        "doc": donor["accepted"][0]["doc"],
        "method": "PATCH" if donor["accepted"][0]["method"] != "PATCH" else "PUT",
        "path": dpath.rstrip("/") + "/scope_probe_sibling",
    }]
    # 같은 route family, 다른 endpoint — novelty 는 통과해야 한다
    rce._validate_v3_novelty(raw)


def test_pair_sharing_family_with_a_single_is_allowed() -> None:
    """pair block 이 일반 single 과 route family 를 공유하는 것도 §4.3 위반 아님."""
    raw = _v3_raw()
    pair_row = next(r for r in raw if r.get("pair_id") and r["pair_role"] == "root")
    fam_path = pair_row["accepted"][0]["path"]
    single = next(r for r in raw
                  if not r.get("pair_id") and r["evaluation_role"] == "scored"
                  and len(r["accepted"]) == 1 and r["category"] != "C6-다개념")
    single["accepted"] = [{
        "doc": pair_row["accepted"][0]["doc"],
        "method": "PATCH" if pair_row["accepted"][0]["method"] != "PATCH" else "PUT",
        "path": fam_path.rstrip("/") + "/single_probe",
    }]
    single["domain"] = pair_row["domain"]
    rce._validate_v3_novelty(raw)


# --- §5: query/split SHA 실검증 ----------------------------------------
def test_verify_manifest_shas_trips_on_query_sha_mismatch(tmp_path: Path) -> None:
    man = tmp_path / "m.json"
    man.write_text(json.dumps({"query_sha256": "deadbeef"}))
    with pytest.raises(ValueError, match="query_sha256 불일치"):
        rce._verify_manifest_shas(_DIR / "queries_gate_v3.json", _v3_raw(), man)


def test_verify_manifest_shas_trips_on_split_sha_mismatch(tmp_path: Path) -> None:
    man = tmp_path / "m.json"
    man.write_text(json.dumps({"split_sha256": "deadbeef"}))
    with pytest.raises(ValueError, match="split_sha256 불일치"):
        rce._verify_manifest_shas(_DIR / "queries_gate_v3.json", _v3_raw(), man)
