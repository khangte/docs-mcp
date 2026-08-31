"""P3 local cross-encoder rerank 단위 테스트(순수 로직, DB·실모델 불필요).

`docs/architect-review/96` §2/§3 계약을 고정한다:
  - flag off(reranker None) 이면 rerank 단계가 실행되지 않고 baseline 순서 그대로.
  - 재점수 폭 N 은 항상 min(50, len(base_wide)) — 50 밖 후보는 승격 대상이 아니다.
  - baseline final `both` 후보는 원 slot 에 HARD lock — 점수와 무관하게 이동 금지.
  - 모델 asset 부재·inference 실패·score 개수 불일치는 baseline 순서로 fail-closed.
  - 재점수는 non-locked slot 에만 영향(Recall 변화가 lock 밖 순열 효과임을 증명).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.search.cross_encoder_reranker import (
    RERANK_WIDTH,
    CrossEncoderUnavailableError,
    LocalCrossEncoderReranker,
    apply_slot_lock,
    cross_encoder_enabled,
    rerank_document,
)
from app.services.search.endpoint_candidate_search import (
    CandidateSearchOptions,
    EndpointCandidateSearch,
)
from app.services.search.rrf import FusedResult
from tests.unit.test_endpoint_candidate_search import (
    _StubChunkRepo,
    _StubEndpointRepo,
    _StubKeyword,
    _StubVector,
)


def _fused(ref_id: str, match_type: str = "vector", score: float = 0.0) -> FusedResult:
    """테스트용 FusedResult 한 건."""
    return FusedResult(ref_id=ref_id, score=score, match_type=match_type)


def _wide(specs: list[tuple[str, str]]) -> list[FusedResult]:
    """(ref_id, match_type) 목록을 base_wide 리스트로."""
    return [_fused(ref_id, mt) for ref_id, mt in specs]


# --- apply_slot_lock: both-arm HARD slot lock ------------------------------


def test_all_both_final_is_byte_identical_regardless_of_scores() -> None:
    """base_final 이 전부 `both` 이면(q08/q09/q11/q12 류) 결과는 baseline 과 완전히 같다."""
    base_wide = _wide([(f"e{i}", "both") for i in range(10)]) + _wide(
        [(f"v{i}", "vector") for i in range(40)]
    )
    # 재점수는 base_final 을 완전히 뒤집으려 시도한다(낮은 rank 일수록 높은 점수).
    scores = {f.ref_id: float(i) for i, f in enumerate(base_wide[:RERANK_WIDTH])}

    result = apply_slot_lock(base_wide, k=10, scores=scores)

    assert [f.ref_id for f in result] == [f"e{i}" for i in range(10)]


def test_locked_both_ref_never_moves_even_with_lowest_score() -> None:
    """`both` slot 은 최저 점수를 줘도 원 위치·상대 순서가 보존된다."""
    base_wide = _wide(
        [
            ("k0", "keyword"),
            ("k1", "keyword"),
            ("b2", "both"),
            ("k3", "keyword"),
            ("k4", "keyword"),
            ("b5", "both"),
            ("k6", "keyword"),
            ("k7", "keyword"),
            ("k8", "keyword"),
            ("k9", "keyword"),
        ]
    ) + _wide([(f"x{i}", "vector") for i in range(40)])
    scores = {f.ref_id: 0.0 for f in base_wide[:RERANK_WIDTH]}
    scores["b2"] = -100.0
    scores["b5"] = -100.0
    # non-locked 중 하나를 크게 밀어올린다.
    scores["x0"] = 999.0

    result = apply_slot_lock(base_wide, k=10, scores=scores)
    ids = [f.ref_id for f in result]

    assert ids[2] == "b2"
    assert ids[5] == "b5"
    assert ids.index("b2") < ids.index("b5")  # 상대 순서 유지
    assert ids[0] == "x0"  # 빈 slot 0 은 최고 점수 non-locked 로 채워진다


def test_rescoring_permutes_only_non_locked_slots() -> None:
    """locked slot 의 id 집합·위치는 불변이고, 재배열은 non-locked slot 안에서만 일어난다."""
    base_wide = _wide(
        [
            ("a", "keyword"),
            ("b", "both"),
            ("c", "vector"),
            ("d", "both"),
            ("e", "keyword"),
        ]
    ) + _wide([(f"p{i}", "vector") for i in range(45)])
    scores = {f.ref_id: 0.0 for f in base_wide[:RERANK_WIDTH]}
    scores.update({"a": 1.0, "c": 5.0, "e": 3.0, "p0": 9.0})

    result = apply_slot_lock(base_wide, k=5, scores=scores)
    ids = [f.ref_id for f in result]

    # locked(b, d) 위치 그대로.
    assert ids[1] == "b"
    assert ids[3] == "d"
    # 빈 slot 0,2,4 는 non-locked pool 을 점수 내림차순으로: p0(9) > c(5) > e(3).
    assert [ids[0], ids[2], ids[4]] == ["p0", "c", "e"]


# --- apply_slot_lock: N=50 경계 -------------------------------------------


def test_rerank_width_is_capped_at_50_candidates() -> None:
    """base_wide 51번째 이후 후보는 점수가 아무리 높아도 승격되지 않는다."""
    base_wide = _wide([(f"c{i:02d}", "vector") for i in range(60)])
    scores = {f.ref_id: 0.0 for f in base_wide[:RERANK_WIDTH]}
    scores["c00"] = 1.0  # pool 안 최고
    # c55 는 pool 밖 — scores 에 없다(승격 후보가 아니므로 KeyError 도 나면 안 됨).

    result = apply_slot_lock(base_wide, k=10, scores=scores)
    ids = [f.ref_id for f in result]

    assert "c55" not in ids
    assert ids[0] == "c00"
    assert all(int(i[1:]) < RERANK_WIDTH for i in ids)


def test_candidate_at_rank_49_can_be_promoted_but_rank_50_cannot() -> None:
    """경계 정확: index 49 는 pool 안, index 50 은 밖."""
    base_wide = _wide([(f"c{i:02d}", "vector") for i in range(60)])
    scores = {f.ref_id: 0.0 for f in base_wide[:RERANK_WIDTH]}
    scores["c49"] = 100.0

    result = apply_slot_lock(base_wide, k=5, scores=scores)

    assert result[0].ref_id == "c49"


# --- apply_slot_lock: tie-break 결정성 ----------------------------------


def test_score_ties_break_by_original_rank_then_ref_id() -> None:
    """동점은 원 base_wide rank 오름차순, 그다음 ref_id 오름차순으로만 푼다(§2.2)."""
    base_wide = _wide([("z", "vector"), ("m", "vector"), ("a", "vector")])
    scores = {"z": 1.0, "m": 1.0, "a": 1.0}

    result = apply_slot_lock(base_wide, k=3, scores=scores)

    # 전부 동점 → 원 순서 유지(z, m, a). ref_id 정렬로 뒤집히지 않는다.
    assert [f.ref_id for f in result] == ["z", "m", "a"]


def test_equal_score_different_rank_prefers_lower_original_rank() -> None:
    """같은 점수면 base_wide 에서 먼저 나온 후보가 앞선다."""
    base_wide = _wide([("first", "vector"), ("second", "vector")]) + _wide(
        [(f"x{i}", "vector") for i in range(48)]
    )
    scores = {f.ref_id: 0.0 for f in base_wide[:RERANK_WIDTH]}
    scores["first"] = 5.0
    scores["second"] = 5.0

    result = apply_slot_lock(base_wide, k=2, scores=scores)

    assert [f.ref_id for f in result] == ["first", "second"]


# --- apply_slot_lock: 경계 입력 ---------------------------------------------


def test_k_zero_returns_empty() -> None:
    """K<=0 이면 빈 리스트."""
    assert apply_slot_lock(_wide([("a", "vector")]), k=0, scores={"a": 1.0}) == []


def test_empty_base_wide_returns_empty() -> None:
    """base_wide 가 비면 빈 리스트."""
    assert apply_slot_lock([], k=10, scores={}) == []


def test_base_wide_shorter_than_k_has_no_none_padding() -> None:
    """후보 수가 k 보다 적으면 있는 수만 반환(None 패딩 없음)."""
    base_wide = _wide([("a", "vector"), ("b", "keyword"), ("c", "vector")])
    scores = {"a": 1.0, "b": 3.0, "c": 2.0}

    result = apply_slot_lock(base_wide, k=10, scores=scores)

    assert [f.ref_id for f in result] == ["b", "c", "a"]
    assert all(f is not None for f in result)


# --- cross_encoder_enabled: opt-in flag 좁히기 ---------------------------


@pytest.mark.parametrize("raw", ["false", "", "0", "no", "off", "garbage", None])
def test_flag_defaults_to_disabled(raw: str | None) -> None:
    """미설정·미인식 값은 전부 비활성(opt-in)."""
    assert cross_encoder_enabled(raw) is False


@pytest.mark.parametrize("raw", ["true", "1", "YES", " on ", "True"])
def test_flag_recognized_true_tokens(raw: str) -> None:
    """명시적 참 토큰만 활성."""
    assert cross_encoder_enabled(raw) is True


def test_flag_passes_through_bool() -> None:
    """이미 bool 이면 그대로."""
    assert cross_encoder_enabled(True) is True
    assert cross_encoder_enabled(False) is False


# --- rerank_document: format v1 고정 직렬화 -----------------------------


def _endpoint(**over: object) -> SimpleNamespace:
    """format v1 직렬화용 endpoint 스텁."""
    base = {
        "method": "post",
        "path": "/v1/refunds",
        "summary": "Create a refund",
        "operation_id": "createRefund",
        "description": "Refund a charge.",
        "parameters": [
            SimpleNamespace(name="charge", description="ID of the charge"),
            SimpleNamespace(name="amount", description=""),
        ],
        "request_body": SimpleNamespace(schema={"properties": {"currency": {}, "reason": {}}}),
        "responses": [
            SimpleNamespace(schema={"properties": {"id": {}, "status": {}}}),
            SimpleNamespace(schema={"properties": {"status": {}, "amount": {}}}),
        ],
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_rerank_document_fixed_order_and_labels() -> None:
    """method/path 첫 행 + 고정 라벨 순서."""
    text = rerank_document(_endpoint())

    assert text.splitlines() == [
        "POST /v1/refunds",
        "summary: Create a refund",
        "operation_id: createRefund",
        "description: Refund a charge.",
        "parameters: charge: ID of the charge amount",
        "request_body_fields: currency reason",
        "response_fields: id status amount",
    ]


def test_rerank_document_omits_missing_fields_without_blank_lines() -> None:
    """누락 필드는 빈 행을 만들지 않고 생략한다."""
    text = rerank_document(
        _endpoint(
            summary="",
            operation_id=None,
            description="",
            parameters=[],
            request_body=None,
            responses=[],
        )
    )

    assert text == "POST /v1/refunds"


def test_rerank_document_survives_lazy_load_error_on_relation() -> None:
    """관계 접근이 튀어도 그 행만 생략하고 나머지는 직렬화한다."""

    class Boom:
        method = "GET"
        path = "/x"
        summary = "s"
        operation_id = None
        description = ""

        @property
        def parameters(self) -> list[object]:
            raise RuntimeError("detached")

        @property
        def request_body(self) -> object:
            raise RuntimeError("detached")

        @property
        def responses(self) -> list[object]:
            raise RuntimeError("detached")

    assert rerank_document(Boom()) == "GET /x\nsummary: s"


# --- LocalCrossEncoderReranker: fake scorer 주입 + 오프라인 degrade ------


def test_local_reranker_uses_injected_scorer() -> None:
    """scorer 주입 시 실모델 load 없이 그대로 위임한다."""
    calls: list[tuple[str, list[str]]] = []

    def fake(query: str, docs: list[str]) -> list[float]:
        calls.append((query, docs))
        return [float(len(d)) for d in docs]

    reranker = LocalCrossEncoderReranker(scorer=fake)

    assert reranker.score_pairs("q", ["a", "bb", "ccc"]) == [1.0, 2.0, 3.0]
    assert reranker.score_pairs("q", []) == []
    assert calls == [("q", ["a", "bb", "ccc"])]


def test_local_reranker_raises_on_score_count_mismatch() -> None:
    """scorer 가 개수를 안 맞추면 CrossEncoderUnavailableError."""
    reranker = LocalCrossEncoderReranker(scorer=lambda _q, _d: [1.0])

    with pytest.raises(CrossEncoderUnavailableError):
        reranker.score_pairs("q", ["a", "b"])


def test_local_reranker_offline_load_fails_closed_without_asset() -> None:
    """asset 이 로컬 cache 에 없으면 network 없이 CrossEncoderUnavailableError 로 실패한다."""
    with pytest.raises(CrossEncoderUnavailableError):
        LocalCrossEncoderReranker(
            model_name="does-not-exist/never-bundled-xyz",
            revision="0000000000000000000000000000000000000000",
        )


# --- EndpointCandidateSearch._apply_cross_encoder_rerank: degrade 경로 --


def _search(reranker: object | None) -> EndpointCandidateSearch:
    """repo/검색기 스텁만 채운 EndpointCandidateSearch(rerank 경로만 검증)."""
    stub = SimpleNamespace()
    return EndpointCandidateSearch(
        chunk_repo=stub,
        endpoint_repo=stub,
        keyword_search=stub,
        vector_search=stub,
        cross_encoder_reranker=reranker,
    )


def _pool_endpoints(ref_ids: list[str]) -> dict[str, SimpleNamespace]:
    return {
        rid: SimpleNamespace(
            method="GET",
            path=f"/{rid}",
            summary="",
            operation_id=None,
            description="",
            parameters=[],
            request_body=None,
            responses=[],
        )
        for rid in ref_ids
    }


def test_rerank_disabled_returns_fallback_identity() -> None:
    """reranker 가 None 이면 fallback 객체를 그대로(동일 identity) 돌려준다 — rerank 미실행."""
    search = _search(None)
    base_wide = _wide([(f"e{i}", "vector") for i in range(5)])
    fallback = base_wide[:3]

    out = search._apply_cross_encoder_rerank("q", base_wide, fallback, top_k=3)

    assert out is fallback


def test_rerank_degrades_to_fallback_when_score_pairs_raises() -> None:
    """score_pairs 예외 시 baseline 순서(fallback)로 fail-closed."""

    class Boom:
        def score_pairs(self, query: str, documents: list[str]) -> list[float]:
            raise RuntimeError("model exploded")

    search = _search(Boom())
    base_wide = _wide([(f"e{i}", "vector") for i in range(5)])
    search._endpoint_repo = SimpleNamespace(
        get_many=lambda ids: _pool_endpoints(list(ids))
    )
    fallback = base_wide[:3]

    out = search._apply_cross_encoder_rerank("q", base_wide, fallback, top_k=3)

    assert out is fallback


def test_rerank_degrades_when_score_count_mismatches_pool() -> None:
    """score 개수가 pool 과 다르면 fallback."""
    search = _search(SimpleNamespace(score_pairs=lambda _q, _d: [1.0, 2.0]))
    base_wide = _wide([(f"e{i}", "vector") for i in range(5)])
    search._endpoint_repo = SimpleNamespace(
        get_many=lambda ids: _pool_endpoints(list(ids))
    )
    fallback = base_wide[:3]

    out = search._apply_cross_encoder_rerank("q", base_wide, fallback, top_k=3)

    assert out is fallback


def test_rerank_degrades_when_candidate_endpoint_missing() -> None:
    """pool 후보 endpoint 를 못 찾으면 fallback."""
    search = _search(SimpleNamespace(score_pairs=lambda _q, d: [0.0] * len(d)))
    base_wide = _wide([(f"e{i}", "vector") for i in range(5)])
    search._endpoint_repo = SimpleNamespace(get_many=lambda _ids: {})  # 전부 미조회
    fallback = base_wide[:3]

    out = search._apply_cross_encoder_rerank("q", base_wide, fallback, top_k=3)

    assert out is fallback


def test_rerank_applies_slot_lock_on_happy_path() -> None:
    """정상 경로: non-locked slot 이 점수 순으로 재배열되고 fallback 과 달라진다.

    pool 순서는 base_wide[:n](e0..e4) 이므로 scorer 는 그 순서로 점수를 돌려준다.
    """
    search = _search(
        SimpleNamespace(score_pairs=lambda _q, docs: [0.0, 0.0, 9.0, 0.0, 0.0][: len(docs)])
    )
    base_wide = _wide([(f"e{i}", "vector") for i in range(5)])
    search._endpoint_repo = SimpleNamespace(
        get_many=lambda ids: _pool_endpoints(list(ids))
    )
    fallback = base_wide[:3]

    out = search._apply_cross_encoder_rerank("q", base_wide, fallback, top_k=3)

    assert out is not fallback
    assert out[0].ref_id == "e2"  # 최고 점수가 빈 slot 0 으로


# --- composition wiring: flag off -> LocalCrossEncoderReranker 미생성 --------


@pytest.mark.parametrize("raw", ["false", "", "0", "no", "off", "garbage", None])
def test_composition_skips_reranker_when_flag_off(raw: str | None) -> None:
    """flag off/미인식이면 composition 이 reranker 를 아예 만들지 않는다(None)."""
    from app.composition import _build_cross_encoder_reranker

    assert _build_cross_encoder_reranker(raw) is None


def test_composition_builds_reranker_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """flag on 이면 LocalCrossEncoderReranker 를 생성해 돌려준다."""
    import app.composition as comp

    sentinel = object()
    monkeypatch.setattr(comp, "LocalCrossEncoderReranker", lambda: sentinel)
    assert comp._build_cross_encoder_reranker("true") is sentinel


def test_composition_degrades_to_none_when_asset_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """flag on 이어도 asset load 실패는 None 으로 degrade — startup 을 죽이지 않는다."""
    import app.composition as comp

    def _boom() -> object:
        raise CrossEncoderUnavailableError("no asset")

    monkeypatch.setattr(comp, "LocalCrossEncoderReranker", _boom)
    assert comp._build_cross_encoder_reranker("true") is None


# --- search() 전 경로: flag off = exact/RRF/fallback baseline parity --------


def test_search_flag_off_matches_plain_rrf_baseline() -> None:
    """cross_encoder_reranker=None 이면 search() 전 경로가 순수 RRF 컷과 byte-identical."""
    kw = [f"k{i:02d}" for i in range(1, 6)]
    von = [f"v{i:02d}" for i in range(1, 26)]
    search = EndpointCandidateSearch(
        chunk_repo=_StubChunkRepo(),
        endpoint_repo=_StubEndpointRepo(),
        keyword_search=_StubKeyword(kw),
        vector_search=_StubVector(kw + von),
        cross_encoder_reranker=None,
    )

    res = search.search("q", CandidateSearchOptions(top_k=10))

    assert [c.endpoint_id for c in res] == kw + ["v01", "v02", "v03", "v04", "v05"]


def test_search_p3_on_forces_arm_rescue_quota_zero() -> None:
    """P3 ON + arm_rescue_quota>0 조합: quota 를 0 으로 강제해 P2 구제가 안 일어난다."""
    both10 = [f"b{i:02d}" for i in range(1, 11)]
    vonly = [f"v{i:02d}" for i in range(1, 21)]
    search = EndpointCandidateSearch(
        chunk_repo=_StubChunkRepo(),
        endpoint_repo=_StubEndpointRepo(),
        keyword_search=_StubKeyword(both10),
        vector_search=_StubVector(both10 + vonly),
        arm_rescue_quota=5,
        cross_encoder_reranker=SimpleNamespace(score_pairs=lambda _q, d: [0.0] * len(d)),
    )

    assert search._arm_rescue_quota == 0
    res = search.search("q", CandidateSearchOptions(top_k=10))
    assert [c.endpoint_id for c in res] == both10  # vonly 구제 없음
