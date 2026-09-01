"""Unit 4 — `endpoint_repr` 세 번째 RRF list 편입, both-slot lock, config 상호배타.

`docs/architect-review/101` §3.2/§3.3, §6 both-arm slot preservation gate.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import get_args

import pytest

from app.composition import build_services
from app.services.search.endpoint_candidate_search import (
    CandidateSearchOptions,
    EndpointCandidateSearch,
    _lock_both_slots,
)
from app.services.search.endpoint_representation_search import EndpointRepresentationResult
from app.services.search.rrf import FusedResult, MatchType

# --- 순수 both-slot lock 로직 -------------------------------------------------


def _fr(ref_id: str, match_type: MatchType = "vector") -> FusedResult:
    return FusedResult(ref_id=ref_id, score=0.0, match_type=match_type)


def test_lock_pins_legacy_both_slots_and_fills_rest_from_tentative() -> None:
    """legacy final 의 both 는 원 slot 고정, 나머지 slot 만 tentative 순서로 채운다."""
    legacy = [_fr("b", "both"), _fr("a", "keyword")]
    tentative = [_fr("b", "both"), _fr("a", "keyword"), _fr("c"), _fr("d")]

    out = _lock_both_slots(legacy, tentative, top_k=3)

    assert [f.ref_id for f in out] == ["b", "a", "c"]
    assert out[0].match_type == "both"  # locked snapshot 유지


def test_lock_overrides_tentative_when_it_would_move_a_both() -> None:
    """tentative 가 both 를 밀어내려 해도 legacy slot 이 이긴다(preservation gate)."""
    legacy = [_fr("b", "both"), _fr("a", "keyword")]
    # tentative 는 a 를 0번 slot 에 두려 하지만 b 가 lock 돼 있다
    tentative = [_fr("a", "keyword"), _fr("b", "both")]

    out = _lock_both_slots(legacy, tentative, top_k=2)

    assert [f.ref_id for f in out] == ["b", "a"]


def test_lock_never_duplicates_a_locked_ref() -> None:
    """locked ref 가 tentative 상위에도 있으면 fill 에서 건너뛴다."""
    legacy = [_fr("x", "both"), _fr("y", "both"), _fr("z", "keyword")]
    tentative = [_fr("x", "both"), _fr("z"), _fr("y", "both"), _fr("w")]

    out = _lock_both_slots(legacy, tentative, top_k=3)

    assert [f.ref_id for f in out] == ["x", "y", "z"]
    assert len({f.ref_id for f in out}) == 3


def test_lock_is_deterministic() -> None:
    legacy = [_fr("b", "both"), _fr("a", "keyword"), _fr("e", "keyword")]
    tentative = [_fr("b", "both"), _fr("d"), _fr("a", "keyword"), _fr("c"), _fr("e")]
    runs = [_lock_both_slots(legacy, tentative, top_k=4) for _ in range(3)]
    assert [[f.ref_id for f in r] for r in runs] == [["b", "d", "a", "c"]] * 3


def test_lock_empty_top_k() -> None:
    assert _lock_both_slots([_fr("a", "both")], [_fr("a", "both")], top_k=0) == []


# --- _search_rrf 가 arm 을 세 번째 list 로 편입 ------------------------------


class _Hit:
    def __init__(self, ref_id: str, score: float = 0.9) -> None:
        self.ref_id = ref_id
        self.score = score


class _FakeKeyword:
    def __init__(self, ref_ids: list[str]) -> None:
        self._ids = ref_ids
        self.queries: list[str] = []

    def search(self, query, *, top_k, document_id=None, project=None, query_variants=None):
        self.queries.append(query)
        return [_Hit(i) for i in self._ids]


class _FakeVector:
    def __init__(self, ref_ids: list[str]) -> None:
        self._ids = ref_ids

    def search(self, query, *, top_k, candidates=None):
        return [_Hit(i) for i in self._ids]


class _FakeEndpointRepo:
    def get_many(self, ref_ids):
        return {
            r: SimpleNamespace(id=r, method="GET", path=f"/{r}", summary=r)
            for r in ref_ids
        }


class _FakeArm:
    def __init__(self, ordered: list[str]) -> None:
        self._ordered = ordered
        self.calls = 0

    def search(self, query, *, document_id=None, project=None):
        self.calls += 1
        return EndpointRepresentationResult(
            ordered_endpoint_ids=list(self._ordered),
            trace=[],
            fts_hit_ids=[],
            vector_hit_ids=[],
            dense_enabled=False,
        )


def _search(arm: _FakeArm, keyword: list[str], vector: list[str]) -> EndpointCandidateSearch:
    return EndpointCandidateSearch(
        chunk_repo=None,  # document_id/project 미지정이라 접근 안 함
        endpoint_repo=_FakeEndpointRepo(),
        keyword_search=_FakeKeyword(keyword),
        vector_search=_FakeVector(vector),
        vector_fallback_enabled=True,
        endpoint_representation_search=arm,
    )


def test_representation_arm_enters_as_third_rrf_list() -> None:
    """arm 이 올린 endpoint 가 non-locked slot 에 편입되고, legacy both 는 고정된다."""
    arm = _FakeArm(["c", "d"])
    out = _search(arm, keyword=["a", "b"], vector=["b"])._search_rrf(
        "q", 3, None, None, None
    )

    assert arm.calls == 1
    assert [c.endpoint_id for c in out] == ["b", "a", "c"]
    assert [c.match_type for c in out] == ["both", "keyword", "vector"]


def test_representation_arm_cannot_displace_locked_both() -> None:
    """arm 이 legacy non-both(a)를 두 arm 히트로 만들어도 slot0 both(b)는 불변."""
    arm = _FakeArm(["a"])
    out = _search(arm, keyword=["a", "b"], vector=["b"])._search_rrf(
        "q", 2, None, None, None
    )

    assert [c.endpoint_id for c in out] == ["b", "a"]
    assert out[0].match_type == "both"


def test_match_type_public_contract_unchanged() -> None:
    """§3.2: public match_type 계약값은 keyword/vector/both/exact 로 고정."""
    assert set(get_args(MatchType)) == {"keyword", "vector", "both", "exact"}


# --- composition: flag on/off, P2/P3 상호배타 fail-closed --------------------


def test_flag_off_builds_no_representation_arm(app_state) -> None:
    bundle = next(build_services(app_state))
    assert bundle.candidate_search._endpoint_representation_search is None


def test_flag_on_builds_representation_arm(app_state) -> None:
    app_state.search_endpoint_representation_enabled = "true"
    bundle = next(build_services(app_state))
    assert bundle.candidate_search._endpoint_representation_search is not None


def test_representation_and_p2_are_mutually_exclusive(app_state) -> None:
    app_state.search_endpoint_representation_enabled = "true"
    app_state.search_arm_rescue_quota = "2"
    with pytest.raises(ValueError, match="invalid configuration"):
        next(build_services(app_state))


def test_representation_and_p3_are_mutually_exclusive(app_state) -> None:
    app_state.search_endpoint_representation_enabled = "true"
    app_state.search_cross_encoder_enabled = "true"
    with pytest.raises(ValueError, match="invalid configuration"):
        next(build_services(app_state))


# --- fallback 전략은 arm 을 호출하지 않는다 --------------------------------


def test_fallback_strategy_never_invokes_representation_arm() -> None:
    arm = _FakeArm(["c"])
    search = EndpointCandidateSearch(
        chunk_repo=None,
        endpoint_repo=_FakeEndpointRepo(),
        keyword_search=_FakeKeyword(["a", "b"]),
        vector_search=_FakeVector(["b"]),
        vector_fallback_enabled=True,
        search_strategy="fallback",
        endpoint_representation_search=arm,
    )

    out = search._search_fallback("q", 3, None, None, None)

    assert arm.calls == 0
    assert [c.endpoint_id for c in out] == ["a", "b"]


# --- 실색인 통합: ON 경로 결정성 + 계약 -----------------------------------


def test_representation_arm_on_end_to_end_is_deterministic(
    app_state, sample_openapi_3: str
) -> None:
    """flag ON 에서 실색인 후보 검색을 3회 반복해도 순서가 같고 계약 match_type 만 낸다."""
    app_state.search_endpoint_representation_enabled = "true"
    next(build_services(app_state)).sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )

    contract = {"keyword", "vector", "both", "exact"}
    runs: list[list[str]] = []
    for _ in range(3):
        cands = next(build_services(app_state)).candidate_search.search(
            "add a new pet", CandidateSearchOptions(top_k=5)
        )
        assert cands
        assert all(c.match_type in contract for c in cands)
        runs.append([c.endpoint_id for c in cands])
    assert runs[0] == runs[1] == runs[2]


def test_non_semantic_flag_on_is_byte_identical_to_off(
    app_state, sample_openapi_3: str
) -> None:
    """`docs/architect-review/102`: 비의미 프로바이더면 flag ON final 이 flag OFF 와 동일."""
    next(build_services(app_state)).sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    queries = ["add a new pet", "find pet by id", "delete a pet", "create user"]

    def _run(enabled: str) -> list[list[tuple[str, str, str]]]:
        app_state.search_endpoint_representation_enabled = enabled
        cs = next(build_services(app_state)).candidate_search
        return [
            [
                (c.method, c.path, c.match_type)
                for c in cs.search(q, CandidateSearchOptions(top_k=5))
            ]
            for q in queries
        ]

    assert _run("true") == _run("false")
