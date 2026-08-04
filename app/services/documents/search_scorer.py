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
