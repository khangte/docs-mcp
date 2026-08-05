"""snippet_generator.py 의 스니펫 시작 위치 선택(_best_snippet_start) 단위 테스트.

_match_positions(search_scorer.py) 가 내는 토큰별 매치 후보 중, 어느 위치를
스니펫 시작점으로 고를지는 이 모듈의 책임이다 — 단순 최솟값이 아니라
"실제 잘라낸 스니펫 구간에 질의 토큰이 몇 종류나 들어오는지"(커버리지)로
고른다.
"""

from __future__ import annotations

from app.services.documents.snippet_generator import (
    SNIPPET_MAX_CHARS,
    _best_snippet_start,
    _build_snippet,
)


def test_picks_earliest_when_single_candidate() -> None:
    """후보가 하나뿐이면 그 위치를 그대로 쓴다."""
    body = "앞부분 잡담. " * 3 + "핵심은 refresh 토큰 회전이다."

    start = _best_snippet_start(body, {"refresh"})

    assert body[start:].startswith("refresh")


def test_prefers_higher_coverage_over_earliest_position() -> None:
    """실사례 회귀: 흔한 토큰이 앞쪽에, 핵심(멀티토큰 밀집) 구간이 뒤쪽에 있으면
    커버리지가 더 높은 뒤쪽 구간을 선택한다(단순 최솟값이었다면 앞쪽이 선택됨).

    실제 재현: 'api'는 문서 극초반에 홀로 매치되고, '주문목록'(collapse)과
    '조회' 등 다른 토큰들은 뒤쪽 한 구간에 몰려 있다 — 사용자가 원하는 건
    후자다.
    """
    body = (
        "POST /api/user/signup 회원가입 설명. "
        + "무관한 설명이 계속 이어진다. " * 40
        + "주문 목록 조회 API 엔드포인트는 GET /api/orders 이다."
    )

    start = _best_snippet_start(body, {"주문목록", "조회", "api"})

    order_pos = body.find("주문 목록")
    assert start == order_pos


def test_tie_breaks_to_earliest_position() -> None:
    """커버리지가 동점이면 가장 이른 위치로 결정적으로 타이브레이크한다."""
    body = "먼저 나오는 refresh. " + "무관 " * 10 + "나중에 나오는 refresh."

    start = _best_snippet_start(body, {"refresh"})

    assert start == body.find("refresh")


def test_no_candidates_returns_none() -> None:
    """매치가 전혀 없으면 None."""
    assert _best_snippet_start("관련 없는 본문", {"검색어"}) is None


def test_build_snippet_uses_best_coverage_window() -> None:
    """_build_snippet 전체 흐름에서도 커버리지가 높은 구간이 스니펫으로 나온다."""
    body = (
        "POST /api/user/signup 회원가입 설명. "
        + "무관한 설명이 계속 이어진다. " * 40
        + "주문 목록 조회 API 엔드포인트는 GET /api/orders 이다."
    )

    snippet = _build_snippet(body, {"주문목록", "조회", "api"})

    assert "주문" in snippet
    assert "signup" not in snippet


def test_build_snippet_still_respects_max_chars() -> None:
    """커버리지 선택 로직이 추가돼도 스니펫 길이 상한은 그대로 지켜진다."""
    body = "핵심 refresh 토큰 " + "본문 " * 200

    snippet = _build_snippet(body, {"refresh"})

    assert len(snippet) <= SNIPPET_MAX_CHARS
