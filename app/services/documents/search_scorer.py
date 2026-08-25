"""협업 문서 검색의 토큰화·제목/본문 점수 계산 순수 함수 모음."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.models.document_meta import DocumentMeta
from app.repositories.document_meta_repository import collapse

_TOKEN_RE = re.compile(r"[0-9A-Za-z_]+|[가-힣]+")


def documents_tokenize(text: str) -> list[str]:
    """텍스트를 영숫자/언더스코어 또는 한글 덩어리 단위 소문자 토큰으로 자른다.

    협업 문서 제목에는 한글이 흔하므로 OpenAPI 쪽 토크나이저와 달리 한글
    음절 범위를 함께 인식한다.
    """
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _title_score(row: DocumentMeta, query_tokens: set[str], query: str) -> float:
    """제목(+URL)과 질의 토큰의 겹침 비율로 1단계 점수를 계산한다."""
    haystack = set(documents_tokenize(row.title)) | set(documents_tokenize(row.url))
    overlap = query_tokens & haystack
    token_score = len(overlap) / len(query_tokens) if overlap else 0.0
    collapsed_score = _collapse_match_score(
        query, collapse(row.title) + collapse(row.url), len(query_tokens)
    )
    return max(token_score, collapsed_score)


def _body_score(body: str, query_tokens: set[str], query: str) -> float:
    """본문과 질의 토큰의 겹침 비율로 2단계 점수를 계산한다."""
    if not body:
        return 0.0
    overlap = query_tokens & set(documents_tokenize(body))
    token_score = len(overlap) / len(query_tokens) if overlap else 0.0
    collapsed_score = _collapse_match_score(query, collapse(body), len(query_tokens))
    return max(token_score, collapsed_score)


def _collapse_match_score(query: str, collapsed_haystack: str, token_count: int) -> float:
    """공백 변형(예: '트러블슈팅' vs '트러블 슈팅')을 흡수하는 보수적 점수.

    질의를 공백 제거한 문자열이 (역시 공백 제거한) haystack 에 부분
    문자열로 포함되면, 토큰 1개가 겹친 것과 동등한 점수만 준다. 이미
    토큰 단위로 여러 개가 겹친 경우보다 우선하지 않도록 상한을 낮게 잡아,
    `max()` 로 기존 겹침 비율과 합성했을 때 순위 의미가 뒤집히지 않게
    한다.
    """
    collapsed_query = collapse(query)
    if not collapsed_query or not collapsed_haystack:
        return 0.0
    if collapsed_query not in collapsed_haystack:
        return 0.0
    return 1 / token_count


def _token_aligned_concat_match(query: str, haystack_tokens: Sequence[str]) -> bool:
    """질의 토큰 concat 이 haystack 토큰들의 **연속 부분열** concat 과 정확히 일치하는지.

    `_collapse_match_score` 와 달리 토큰 경계를 존중한다. '결제장애' 는 제목
    ['결제','장애','대응'] 의 연속 부분열 '결제'+'장애' 와 일치해 통과하지만,
    'api' 는 ['rapid','onboarding'] 의 어떤 연속 부분열과도 같지 않아 탈락한다
    (부분문자열로는 'rapid' 안에 들어 있지만 경계가 맞지 않는다).
    """
    target = "".join(documents_tokenize(query))
    if not target or not haystack_tokens:
        return False
    boundaries = {0}
    joined_parts: list[str] = []
    offset = 0
    for token in haystack_tokens:
        joined_parts.append(token)
        offset += len(token)
        boundaries.add(offset)
    joined = "".join(joined_parts)
    pos = joined.find(target)
    while pos >= 0:
        if pos in boundaries and pos + len(target) in boundaries:
            return True
        pos = joined.find(target, pos + 1)
    return False


def _passes_title_gate(row: DocumentMeta, filter_tokens: set[str], queries: Sequence[str]) -> bool:
    """title/url 이 질의와 토큰 수준에서 실제로 겹치는지(부분문자열 잡음 배제).

    SQL 1단계(`search_by_tokens`)는 `ILIKE '%token%'` 부분문자열 매칭이라
    토큰 경계를 무시한다 — 질의 토큰 'api' 가 제목의 'rapid' 안에 들어 있어도
    후보로 올라온다. `_title_score`(`_collapse_match_score` 경유)도 같은
    문제가 있어 게이트로 재사용할 수 없다(57번 리뷰 §5 개선3 T3 개정).
    이 함수는 별도로 토큰 경계를 지켜 그 잡음만 걸러낸다 — (원본 ∪ variant
    토큰) 중 하나가 title/url 토큰과 완전 일치하거나, 어떤 질의의 토큰
    concat 이 title/url 토큰들의 연속 부분열과 완전 일치하면 통과.
    title 토큰열과 url 토큰열은 따로 본다(둘을 이어붙인 경계를 넘는 매치는
    허용하지 않는다).
    """
    title_tokens = documents_tokenize(row.title)
    url_tokens = documents_tokenize(row.url)
    if filter_tokens & (set(title_tokens) | set(url_tokens)):
        return True
    return any(
        _token_aligned_concat_match(q, title_tokens) or _token_aligned_concat_match(q, url_tokens)
        for q in queries
    )


def _match_positions(body: str, query_tokens: set[str]) -> list[int]:
    """토큰별 최초 매치 위치(원본 `body` 기준 인덱스) 후보 목록을 만든다.

    스니펫 생성(`_build_snippet`)과 점수 계산(`_title_score`/`_body_score`)이
    서로 다른 매칭 기준을 쓰면 "점수는 매치로 잡히는데 스니펫은 엉뚱한
    곳을 보여주는" 불일치가 생긴다. 이 헬퍼를 양쪽이 공유해 판단 기준을
    하나로 유지한다.

    토큰마다 "정확 매치 우선, 없으면 collapse" 를 각각 독립적으로 적용해
    후보를 하나씩만 낸다(후보 수 = 토큰 수로 한정 — 흔한 토큰이 본문에
    수백 번 나와도 후보가 폭증하지 않는다). collapse 를 질의 전체가 아니라
    토큰 단위로 적용해야 '주문목록'이 본문의 '주문 목록' 구간과 제 힘으로
    매치되어 'api' 의 위치와 동등한 후보로 나란히 설 수 있다.

    단순히 이 후보들의 최솟값만 취하면, 흔하고 짧은 토큰이 문서 극초반에
    우연히 있을 때 그 위치가 항상 이겨서 정작 질의의 핵심 토큰이 담긴
    구간을 못 보여주는 문제가 있다(예: 'api'가 194번째, '주문목록'의
    collapse 매치가 2522번째인 문서에서 'api' 위치만 선택됨). 그래서 이
    함수는 최솟값을 고르지 않고 후보 전체를 반환한다 — 실제 스니펫 구간
    선택(커버리지 비교)은 호출자(`_build_snippet`)의 몫이다.

    Args:
        body: 매치 위치를 찾을 본문.
        query_tokens: 질의 토큰 집합.

    Returns:
        각 토큰의 매치 위치 후보 목록(매치 없는 토큰은 제외). 중복 위치는
        제거하지 않는다 — 호출자가 정렬해 순회한다.
    """
    if not query_tokens:
        return []
    lowered = body.lower()
    collapsed_body, index_map = _collapse_with_index_map(body)
    positions: list[int] = []
    for token in query_tokens:
        exact_pos = lowered.find(token)
        if exact_pos >= 0:
            positions.append(exact_pos)
            continue
        collapsed_token = collapse(token)
        if not collapsed_token:
            continue
        collapsed_pos = collapsed_body.find(collapsed_token)
        if collapsed_pos >= 0:
            positions.append(index_map[collapsed_pos])
    return positions


def _collapse_with_index_map(text: str) -> tuple[str, list[int]]:
    """공백을 제거하며, collapsed 인덱스 -> 원본 인덱스 매핑을 함께 만든다."""
    collapsed_chars: list[str] = []
    index_map: list[int] = []
    for original_index, char in enumerate(text or ""):
        if char.isspace():
            continue
        collapsed_chars.append(char.lower())
        index_map.append(original_index)
    return "".join(collapsed_chars), index_map
