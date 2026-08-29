"""`tests/fixtures/corpus_eval/run_corpus_eval.py` 의 v2 프리즈 로더 로직 단위 테스트.

80번 설계 §3(v2 신규성 계약)·§6(manifest 선택) 구현을 검증한다. DB·검색은 건드리지
않는다 — frozen fixture 파일과 정적 검증기만 본다.
"""

from __future__ import annotations

import copy
import json
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


def _v2_raw() -> list[dict]:
    return json.loads((_DIR / "queries_gate_v2.json").read_text())


# --- §6: manifest 선택 -------------------------------------------------------
def test_manifest_map_covers_v1_and_v2() -> None:
    assert rce._MANIFEST_BY_QUERY_FILE == {
        "queries_gate_v1.json": "gate_manifest_v1.json",
        "queries_gate_v2.json": "gate_manifest_v2.json",
        "queries_gate_v3.json": "gate_manifest_v3.json",
    }


def test_v1_file_still_loads(ctx: dict) -> None:
    """v1 프리즈는 회귀 없이 계속 통과해야 한다."""
    qs = rce._load_and_validate_queries(
        ctx["vbd"], _DIR / "queries_gate_v1.json", "all", ctx["corpus_sha"]
    )
    assert len(qs) == 120


def test_v2_file_loads_and_passes_novelty(ctx: dict) -> None:
    qs = rce._load_and_validate_queries(
        ctx["vbd"], _DIR / "queries_gate_v2.json", "all", ctx["corpus_sha"]
    )
    assert len(qs) == 120
    # 프리즈 파일 자체가 §3 전항 통과
    rce._validate_v2_novelty(_v2_raw())


# --- §3: 각 신규성 규칙이 위반에 대해 죽는지 --------------------------------
def test_novelty_trips_on_reused_v1_accepted_tuple() -> None:
    raw = _v2_raw()
    raw[5]["accepted"] = [{"doc": "stripe", "method": "GET", "path": "/v1/invoices/{invoice}"}]
    with pytest.raises(ValueError, match="v1 재사용"):
        rce._validate_v2_novelty(raw)


def test_novelty_trips_on_query_colliding_v1() -> None:
    raw = _v2_raw()
    v1_first_query = json.loads((_DIR / "queries_gate_v1.json").read_text())[0]["query"]
    raw[0]["query"] = v1_first_query
    with pytest.raises(ValueError, match="legacy/v1 과 중복"):
        rce._validate_v2_novelty(raw)


def test_novelty_trips_on_internal_accepted_tuple_share() -> None:
    raw = _v2_raw()
    raw[1]["accepted"] = copy.deepcopy(raw[0]["accepted"])
    with pytest.raises(ValueError, match="중복"):
        rce._validate_v2_novelty(raw)


def test_novelty_trips_on_v2p_pair_id_pattern() -> None:
    raw = _v2_raw()
    for r in raw:
        if r.get("pair_id") == "v2p01":
            r["pair_id"] = "p01"
    with pytest.raises(ValueError, match="pair id"):
        rce._validate_v2_novelty(raw)


def test_novelty_trips_on_bad_v2g_id() -> None:
    raw = _v2_raw()
    raw[0]["id"] = "g001"
    with pytest.raises(ValueError, match="id"):
        rce._validate_v2_novelty(raw)


def test_novelty_trips_on_nfkc_equivalent_query_dup() -> None:
    """NFKC 정규화로 같아지는 두 query 는 v2 내부 중복으로 잡힌다."""
    raw = _v2_raw()
    raw[0]["query"] = "ﬁle a credit note"  # U+FB01 ligature
    raw[1]["query"] = "file a credit note"
    with pytest.raises(ValueError, match="정규화 query"):
        rce._validate_v2_novelty(raw)


def test_novelty_trips_on_pair_ids_not_exactly_twelve() -> None:
    """pair id 는 v2p01~v2p12 정확히여야 한다(패턴만으로는 부족)."""
    raw = _v2_raw()
    for r in raw:
        if r.get("pair_id") == "v2p12":
            r["pair_id"] = "v2p13"  # 패턴은 통과하지만 집합이 어긋남
    with pytest.raises(ValueError, match="v2p01~v2p12 정확히"):
        rce._validate_v2_novelty(raw)


# --- §6: manifest 선택/SHA 실검증 -----------------------------------------
def test_unknown_gate_schema_file_rejected(ctx: dict, tmp_path: Path) -> None:
    bogus = tmp_path / "queries_gate_v9.json"
    bogus.write_text(json.dumps([{"evaluation_role": "scored"}]))
    with pytest.raises(ValueError, match="등록되지 않은 gate 스키마"):
        rce._load_and_validate_queries(ctx["vbd"], bogus, "all", ctx["corpus_sha"])


def test_verify_manifest_shas_trips_on_query_sha_mismatch(tmp_path: Path) -> None:
    raw = _v2_raw()
    man = tmp_path / "m.json"
    man.write_text(json.dumps({"query_sha256": "deadbeef"}))
    with pytest.raises(ValueError, match="query_sha256 불일치"):
        rce._verify_manifest_shas(_DIR / "queries_gate_v2.json", raw, man)


def test_verify_manifest_shas_trips_on_split_sha_mismatch(tmp_path: Path) -> None:
    raw = _v2_raw()
    man = tmp_path / "m.json"
    man.write_text(json.dumps({"split_sha256": "deadbeef"}))
    with pytest.raises(ValueError, match="split_sha256 불일치"):
        rce._verify_manifest_shas(_DIR / "queries_gate_v2.json", raw, man)


def test_verify_manifest_shas_passes_for_frozen_v2() -> None:
    raw = _v2_raw()
    rce._verify_manifest_shas(
        _DIR / "queries_gate_v2.json", raw, _DIR / "gate_manifest_v2.json"
    )
