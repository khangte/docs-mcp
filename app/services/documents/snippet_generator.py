"""협업 문서 검색 결과의 스니펫 생성 순수 함수 모음."""

from __future__ import annotations

import re

from app.models.document_meta import DocumentMeta
from app.services.documents.search_scorer import _match_position

#: 스니펫으로 잘라낼 최대 문자 수.
SNIPPET_MAX_CHARS = 300
#: 매칭 구간 앞쪽에 함께 보여줄 문맥 문자 수.
SNIPPET_LEAD_CHARS = 60


def _build_snippet(body: str, query_tokens: set[str]) -> str:
    """본문에서 질의와 관련된 구간을 잘라 스니펫을 만든다.

    매치 위치 판단은 `_match_position` (search_scorer.py) 을 그대로 쓴다.
    점수 계산과 스니펫 생성이 서로 다른 매칭 기준을 쓰면, 점수는 매치로
    잡히는데 스니펫은 본문 앞부분만 보여주는 불일치가 생기기 때문이다
    (예: 질의 토큰이 '주문목록' 이고 본문엔 '주문 목록' 으로만 등장하는
    경우, collapse 보정 없이는 본문 맨 앞의 다른 토큰 매치로 스니펫이
    잘못 잡힌다).
    """
    if not body:
        return ""
    position = _match_position(body, query_tokens)
    if position is None:
        return _clean_snippet(body[:SNIPPET_MAX_CHARS])
    start = max(0, position - SNIPPET_LEAD_CHARS)
    return _clean_snippet(body[start : start + SNIPPET_MAX_CHARS])


def _clean_snippet(text: str) -> str:
    """스니펫의 연속 공백/줄바꿈을 한 칸으로 정리한다."""
    return re.sub(r"\s+", " ", text).strip()


def _fallback_snippet(row: DocumentMeta, query: str) -> str:
    """본문이 비어 스니펫을 만들 수 없을 때 쓰는 안내 문구."""
    return f"본문에서 '{query}' 관련 구간을 찾지 못했습니다. 제목만 일치: {row.title}"
