"""협업 문서 검색 결과의 스니펫 생성 순수 함수 모음."""

from __future__ import annotations

import re

from app.models.document_meta import DocumentMeta
from app.services.documents.search_scorer import _match_positions

#: 스니펫으로 잘라낼 최대 문자 수.
SNIPPET_MAX_CHARS = 300
#: 매칭 구간 앞쪽에 함께 보여줄 문맥 문자 수.
SNIPPET_LEAD_CHARS = 60


def _build_snippet(body: str, query_tokens: set[str]) -> str:
    """본문에서 질의와 관련된 구간을 잘라 스니펫을 만든다.

    매치 위치 후보는 `_match_positions` (search_scorer.py) 를 그대로 쓴다.
    점수 계산과 스니펫 생성이 서로 다른 매칭 기준을 쓰면, 점수는 매치로
    잡히는데 스니펫은 엉뚱한 곳을 보여주는 불일치가 생기기 때문이다.

    후보가 여럿이면 단순히 가장 이른 위치를 고르지 않는다 — 흔하고 짧은
    토큰(예: 'api')이 문서 극초반에 우연히 있으면 그 위치가 항상 이겨서,
    질의의 핵심 토큰(예: '주문목록')이 담긴 구간을 못 보여주는 문제가
    있었다. 대신 각 후보 위치로 300자를 잘랐을 때 그 구간에 실제로
    포함되는 질의 토큰 종류 수("커버리지")를 세어, 커버리지가 가장 높은
    후보를 선택한다 — 동점이면 가장 이른 위치로 결정적으로 타이브레이크한다.
    """
    if not body:
        return ""
    start = _best_snippet_start(body, query_tokens)
    if start is None:
        return _clean_snippet(body[:SNIPPET_MAX_CHARS])
    lead_start = max(0, start - SNIPPET_LEAD_CHARS)
    return _clean_snippet(body[lead_start : lead_start + SNIPPET_MAX_CHARS])


def _best_snippet_start(body: str, query_tokens: set[str]) -> int | None:
    """커버리지(질의 토큰 종류 수)가 가장 높은 매치 후보 위치를 고른다.

    동점이면 가장 이른 위치를 선택한다(현재 최소 위치가 항상 이기는
    로직과 동일한 결정적 타이브레이크 — 같은 입력이면 같은 결과).
    """
    positions = sorted(set(_match_positions(body, query_tokens)))
    if not positions:
        return None
    best_start = positions[0]
    best_coverage = -1
    for position in positions:
        lead_start = max(0, position - SNIPPET_LEAD_CHARS)
        window = body[lead_start : lead_start + SNIPPET_MAX_CHARS]
        coverage = _coverage(window, query_tokens)
        if coverage > best_coverage:
            best_coverage = coverage
            best_start = position
    return best_start


def _coverage(window: str, query_tokens: set[str]) -> int:
    """윈도우 안에 실제로 포함되는 질의 토큰(exact 또는 collapse) 종류 수를 센다.

    `_match_positions` 는 토큰마다 매치 위치를 최대 1개씩만 담으므로(토큰
    수로 후보가 한정되는 이유와 동일), 반환된 리스트의 길이가 곧 이
    윈도우에서 매치된 서로 다른 토큰의 개수다. 두 토큰이 우연히 같은
    위치에서 매치돼도 리스트 항목은 각각 별도로 남으므로 `set()` 으로
    위치 중복을 제거하면 안 된다 — 그러면 서로 다른 토큰이 과소 카운트된다.
    """
    return len(_match_positions(window, query_tokens))


def _clean_snippet(text: str) -> str:
    """스니펫의 연속 공백/줄바꿈을 한 칸으로 정리한다."""
    return re.sub(r"\s+", " ", text).strip()


def _fallback_snippet(row: DocumentMeta, query: str) -> str:
    """본문이 비어 스니펫을 만들 수 없을 때 쓰는 안내 문구."""
    return f"본문에서 '{query}' 관련 구간을 찾지 못했습니다. 제목만 일치: {row.title}"
