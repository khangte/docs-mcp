"""search_scorer.py 의 매치 위치 후보(_match_positions) 단위 테스트.

점수 계산(_title_score/_body_score)과 스니펫 생성(_build_snippet)이 서로
다른 매칭 기준을 쓰면 "점수는 매치로 잡히는데 스니펫은 엉뚱한 곳을
보여주는" 불일치가 생긴다. 이 헬퍼가 그 공유 판단 기준이다.

"여러 후보 중 어느 위치를 스니펫 시작점으로 고를지"(커버리지 비교)는
snippet_generator.py 의 책임이라 여기서 다루지 않는다 — 이 모듈은 토큰별
매치 위치 후보 "목록"만 만든다.
"""

from __future__ import annotations

from app.models.document_meta import DocumentMeta
from app.services.documents.search_scorer import (
    _match_positions,
    _passes_title_gate,
    _token_aligned_concat_match,
    documents_tokenize,
)


def test_exact_token_match_returns_its_position() -> None:
    """토큰이 본문에 정확히 등장하면 그 위치가 후보에 포함된다."""
    body = "앞부분 잡담. " * 3 + "핵심은 refresh 토큰 회전이다."

    positions = _match_positions(body, {"refresh"})

    assert len(positions) == 1
    assert body[positions[0] :].startswith("refresh")


def test_each_token_contributes_at_most_one_position() -> None:
    """토큰마다 후보가 최대 1개씩만 나온다(후보 수 = 토큰 수로 한정)."""
    body = "뒤쪽 토큰: beta. 앞쪽 토큰: alpha."

    positions = _match_positions(body, {"alpha", "beta"})

    assert len(positions) == 2


def test_no_match_returns_empty_list() -> None:
    """질의 토큰이 본문에 전혀 없으면 빈 리스트를 반환한다."""
    assert _match_positions("관련 없는 본문", {"검색어"}) == []


def test_collapse_fallback_finds_whitespace_variant() -> None:
    """정확 토큰 매치가 없어도 공백 변형이면 collapse 로 찾아낸다.

    질의 토큰 '주문목록'(붙여쓰기)이 본문에는 '주문 목록'(띄어쓰기)으로만
    있는 경우 — 원본 CSV 데이터에서 실제로 발생한 케이스.
    """
    body = "앞부분 잡담. " * 20 + "여기 주문 목록 데이터가 있다."

    positions = _match_positions(body, {"주문목록"})

    assert len(positions) == 1
    assert body[positions[0] :].startswith("주문")


def test_collapse_fallback_returns_original_index_not_collapsed_index() -> None:
    """collapse 경로에서도 반환값은 원본 body 기준 인덱스여야 한다.

    collapsed 문자열은 공백이 빠져 있어 인덱스가 원본과 어긋난다. 공백이
    많은 프리픽스를 둬서, collapsed 인덱스를 그대로 쓰면(환산하지 않으면)
    실제 매치 지점보다 훨씬 앞을 가리키게 되는 상황을 검증한다.
    """
    prefix = "공 백 이 많 은 잡 담 " * 10  # 공백 제거 시 원본 인덱스가 크게 줄어드는 프리픽스
    body = prefix + "주문 목록 데이터"

    positions = _match_positions(body, {"주문목록"})

    assert len(positions) == 1
    position = positions[0]
    assert body[position : position + 2] == "주문"
    assert position >= len(prefix) - 1  # collapsed 인덱스를 그대로 썼다면 훨씬 작은 값이 된다


def test_exact_match_takes_priority_over_collapse_for_same_token() -> None:
    """같은 토큰에 대해 exact 매치가 있으면 그 토큰은 collapse 경로로 안 간다.

    '주문목록'이 본문에 붙여쓰기로도(exact), 띄어쓰기로도(collapse) 있으면
    더 이른 exact 위치를 그 토큰의 후보로 쓴다(토큰별 판단 — 우선순위가
    아니라 "그 토큰 한정" exact 우선). 토큰이 하나뿐이므로 후보도 1개다.
    """
    body = "앞쪽에 정확히 일치: 주문목록. 뒤쪽엔: 주문 목록."

    positions = _match_positions(body, {"주문목록"})

    assert len(positions) == 1
    assert body[positions[0] :].startswith("주문목록.")


def test_no_query_tokens_returns_empty_list() -> None:
    """빈 query_tokens 이면 빈 리스트."""
    assert _match_positions("아무 본문", set()) == []


def test_mixed_exact_and_collapse_tokens_both_become_candidates() -> None:
    """일부 토큰만 exact 매치되는 멀티토큰 질의에서도 모든 토큰이 후보를 낸다.

    회귀 재현(구 버그): "exact 매치가 하나라도 있으면 즉시 그 위치만 쓰고
    collapse 경로 자체를 스킵"하는 구현이었다면, 'api'가 exact 매치된다는
    이유로 '주문목록'의 collapse 매치(본문 앞쪽, '주문 목록' 띄어쓰기)가
    후보에 아예 오르지 못한다. 이 테스트는 두 토큰 모두 독립적으로 후보를
    내는지 검증한다 — "어느 후보를 최종 선택할지"는 이 함수의 책임이 아니다.
    """
    body = "여기부터 주문 목록 관련 내용이 나온다. " + "뒷부분 설명. " * 3 + "POST /api/user/signup"

    positions = _match_positions(body, {"주문목록", "api"})

    order_collapse_pos = body.find("주문 목록")
    api_exact_pos = body.lower().find("api")
    assert sorted(positions) == sorted([order_collapse_pos, api_exact_pos])


# --- 57번 리뷰 개선3 T3 개정: 토큰 경계 인지 매치 -----------------------------


def test_token_aligned_concat_match_true_for_contiguous_token_subsequence() -> None:
    """질의 토큰 concat 이 haystack 토큰들의 연속 부분열 concat 과 일치하면 True."""
    haystack_tokens = documents_tokenize("결제 장애 대응 가이드")

    assert _token_aligned_concat_match("결제장애", haystack_tokens) is True


def test_token_aligned_concat_match_false_for_substring_inside_single_token() -> None:
    """토큰 경계를 넘어 단일 토큰 내부에 우연히 들어있는 문자열은 제외한다.

    'api' 는 'rapid' 안에 부분문자열로는 있지만 토큰 경계가 맞지 않는다.
    """
    haystack_tokens = documents_tokenize("Rapid Onboarding Guide")

    assert _token_aligned_concat_match("api", haystack_tokens) is False


def test_token_aligned_concat_match_false_for_misaligned_boundary() -> None:
    """경계가 어긋난 부분 매치('장애대' 같은 절단)는 제외한다."""
    haystack_tokens = documents_tokenize("결제 장애 대응 가이드")

    assert _token_aligned_concat_match("장애대", haystack_tokens) is False


def test_token_aligned_concat_match_true_for_full_token_match() -> None:
    """질의가 haystack 토큰 하나와 정확히 같아도(연속 부분열 길이 1) True."""
    haystack_tokens = documents_tokenize("결제 장애 대응 가이드")

    assert _token_aligned_concat_match("결제", haystack_tokens) is True


def test_token_aligned_concat_match_false_for_empty_query_or_haystack() -> None:
    """질의 또는 haystack 이 비어 있으면 False."""
    assert _token_aligned_concat_match("", documents_tokenize("결제 장애")) is False
    assert _token_aligned_concat_match("결제", []) is False


def _row(title: str, url: str = "https://example.test/x") -> DocumentMeta:
    return DocumentMeta(project="p", source="drive", external_id="x", title=title, url=url)


def test_passes_title_gate_true_for_exact_token_overlap() -> None:
    """filter_tokens 가 title/url 토큰과 정확히 겹치면 통과."""
    row = _row("결제 오류 안내")

    assert _passes_title_gate(row, {"결제"}, ["결제"]) is True


def test_passes_title_gate_false_for_substring_noise() -> None:
    """부분문자열 잡음('api' ⊂ 'rapid')은 게이트를 통과하지 못한다."""
    row = _row("Rapid Onboarding Guide")

    assert _passes_title_gate(row, {"api"}, ["api"]) is False


def test_passes_title_gate_true_for_original_collapse_match() -> None:
    """원본 질의의 collapse(연속 부분열) 매치는 통과한다('결제장애' ↔ '결제 장애 대응')."""
    row = _row("결제 장애 대응 가이드")

    assert _passes_title_gate(row, {"결제장애"}, ["결제장애"]) is True


def test_passes_title_gate_true_for_variant_only_full_token_match() -> None:
    """원본으로는 안 걸리고 variant 토큰 완전 일치로만 걸리는 행도 통과한다(회귀 방지 핵심)."""
    row = _row("Payment Failure Runbook")

    assert (
        _passes_title_gate(
            row, {"결제", "실패", "payment", "failure"}, ["결제 실패", "payment failure"]
        )
        is True
    )


def test_passes_title_gate_true_for_url_only_match() -> None:
    """title 이 아니라 url 로만 매치되는 행도 통과한다."""
    row = _row("무관한 제목", url="https://example.test/payment-failure")

    assert _passes_title_gate(row, {"payment", "failure"}, ["payment failure"]) is True


def test_passes_title_gate_false_for_misaligned_boundary_query() -> None:
    """경계 어긋난 부분 매치는 title/url 어느 쪽으로도 통과하지 못한다."""
    row = _row("결제 장애 대응 가이드")

    assert _passes_title_gate(row, {"장애대"}, ["장애대"]) is False
