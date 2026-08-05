"""협업 문서 검색의 토큰화·제목/본문 점수 계산 순수 함수 모음."""

from __future__ import annotations

import re

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


def _match_position(body: str, query_tokens: set[str]) -> int | None:
    """본문에서 질의와 관련된 구간이 시작되는 원본 인덱스를 찾는다.

    스니펫 생성(`_build_snippet`)과 점수 계산(`_title_score`/`_body_score`)이
    서로 다른 매칭 기준을 쓰면 "점수는 매치로 잡히는데 스니펫은 엉뚱한
    곳을 보여주는" 불일치가 생긴다. 이 헬퍼를 양쪽이 공유해 판단 기준을
    하나로 유지한다.

    토큰마다 "정확 매치 우선, 없으면 collapse" 를 각각 독립적으로 적용한
    뒤, 모든 토큰의 후보 위치 중 최솟값을 반환한다 — 일부 토큰만 정확
    매치되는 멀티토큰 질의(예: '주문목록 API')에서, 흔한 토큰('api')이
    본문 앞쪽에서 정확 매치된다는 이유로 다른 토큰('주문목록')의 collapse
    매치 시도 자체를 건너뛰면 안 된다. collapse 를 질의 전체가 아니라
    토큰 단위로 적용해야 '주문목록'이 본문의 '주문 목록' 구간과 제 힘으로
    매치되어 'api' 의 위치와 동등하게 경쟁할 수 있다.

    Args:
        body: 매치 위치를 찾을 본문.
        query_tokens: 질의 토큰 집합.

    Returns:
        원본 `body` 기준 매치 시작 인덱스. 매치가 전혀 없으면 None.
    """
    if not query_tokens:
        return None
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
    if not positions:
        return None
    return min(positions)


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
