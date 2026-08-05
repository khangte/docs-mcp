"""search_scorer.py 의 매치 위치 판단(_match_position) 단위 테스트.

점수 계산(_title_score/_body_score)과 스니펫 생성(_build_snippet)이 서로
다른 매칭 기준을 쓰면 "점수는 매치로 잡히는데 스니펫은 엉뚱한 곳을
보여주는" 불일치가 생긴다. 이 헬퍼가 그 공유 판단 기준이다.
"""

from __future__ import annotations

from app.services.documents.search_scorer import _match_position


def test_exact_token_match_returns_its_position() -> None:
    """토큰이 본문에 정확히 등장하면 그 위치를 그대로 반환한다."""
    body = "앞부분 잡담. " * 3 + "핵심은 refresh 토큰 회전이다."

    position = _match_position(body, {"refresh"})

    assert body[position:].startswith("refresh")


def test_exact_match_prefers_earliest_position_among_tokens() -> None:
    """여러 토큰이 매치되면 가장 먼저 등장하는 위치를 우선한다."""
    body = "뒤쪽 토큰: beta. 앞쪽 토큰: alpha."

    position = _match_position(body, {"alpha", "beta"})

    assert body[position:].startswith("beta")


def test_no_match_returns_none() -> None:
    """질의 토큰이 본문에 전혀 없으면 None 을 반환한다."""
    assert _match_position("관련 없는 본문", {"검색어"}) is None


def test_collapse_fallback_finds_whitespace_variant() -> None:
    """정확 토큰 매치가 없어도 공백 변형이면 collapse 로 찾아낸다.

    질의 토큰 '주문목록'(붙여쓰기)이 본문에는 '주문 목록'(띄어쓰기)으로만
    있는 경우 — 원본 CSV 데이터에서 실제로 발생한 케이스.
    """
    body = "앞부분 잡담. " * 20 + "여기 주문 목록 데이터가 있다."

    position = _match_position(body, {"주문목록"})

    assert position is not None
    assert body[position:].startswith("주문")


def test_collapse_fallback_returns_original_index_not_collapsed_index() -> None:
    """collapse 경로에서도 반환값은 원본 body 기준 인덱스여야 한다.

    collapsed 문자열은 공백이 빠져 있어 인덱스가 원본과 어긋난다. 공백이
    많은 프리픽스를 둬서, collapsed 인덱스를 그대로 쓰면(환산하지 않으면)
    실제 매치 지점보다 훨씬 앞을 가리키게 되는 상황을 검증한다.
    """
    prefix = "공 백 이 많 은 잡 담 " * 10  # 공백 제거 시 원본 인덱스가 크게 줄어드는 프리픽스
    body = prefix + "주문 목록 데이터"

    position = _match_position(body, {"주문목록"})

    assert position is not None
    assert body[position : position + 2] == "주문"
    assert position >= len(prefix) - 1  # collapsed 인덱스를 그대로 썼다면 훨씬 작은 값이 된다


def test_exact_match_takes_priority_over_collapse_for_same_token() -> None:
    """같은 토큰에 대해 exact 매치가 있으면 그 토큰은 collapse 경로로 안 간다.

    '주문목록'이 본문에 붙여쓰기로도(exact), 띄어쓰기로도(collapse) 있으면
    더 이른 exact 위치를 그 토큰의 후보로 쓴다(토큰별 판단 — 우선순위가
    아니라 "그 토큰 한정" exact 우선).
    """
    body = "앞쪽에 정확히 일치: 주문목록. 뒤쪽엔: 주문 목록."

    position = _match_position(body, {"주문목록"})

    assert body[position:].startswith("주문목록.")


def test_no_query_tokens_and_no_collapse_match_returns_none() -> None:
    """빈 query_tokens 이고 collapse 매치도 없으면 None."""
    assert _match_position("아무 본문", set()) is None


def test_mixed_exact_and_collapse_tokens_compete_for_min_position() -> None:
    """일부 토큰만 exact 매치되는 멀티토큰 질의에서도 모든 토큰이 위치 경쟁에 참여한다.

    회귀 재현(구 버그): "exact_positions 가 하나라도 있으면 즉시 min 반환하고
    collapse 경로 자체를 스킵"하는 구현이었다면, 'api'가 본문 뒤쪽에서
    exact 매치된다는 이유로 '주문목록'의 collapse 매치(본문 앞쪽, '주문
    목록' 띄어쓰기)를 아예 시도하지 않아 뒤쪽 'api' 위치가 잘못 선택된다.
    이 테스트는 '주문목록' 쪽(앞쪽, collapse 로만 매치)이 실제로 이겨야
    함을 검증한다 — 토큰별 독립 판단 없이는 통과할 수 없다.
    """
    body = "여기부터 주문 목록 관련 내용이 나온다. " + "뒷부분 설명. " * 3 + "POST /api/user/signup"

    position = _match_position(body, {"주문목록", "api"})

    order_collapse_pos = body.find("주문 목록")
    api_exact_pos = body.lower().find("api")
    assert order_collapse_pos < api_exact_pos  # 시나리오 전제: collapse 매치가 더 이르다
    assert position == order_collapse_pos
