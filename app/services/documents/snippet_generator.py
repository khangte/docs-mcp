"""협업 문서 검색 결과의 스니펫 생성 순수 함수 모음."""

from __future__ import annotations

import re

from app.models.document_meta import DocumentMeta

#: 스니펫으로 잘라낼 최대 문자 수.
SNIPPET_MAX_CHARS = 300
#: 매칭 구간 앞쪽에 함께 보여줄 문맥 문자 수.
SNIPPET_LEAD_CHARS = 60


def _build_snippet(body: str, query_tokens: set[str]) -> str:
    """본문에서 질의 토큰이 처음 등장하는 구간을 잘라 스니펫을 만든다."""
    if not body:
        return ""
    lowered = body.lower()
    positions = [pos for pos in (lowered.find(t) for t in query_tokens) if pos >= 0]
    if not positions:
        return _clean_snippet(body[:SNIPPET_MAX_CHARS])
    start = max(0, min(positions) - SNIPPET_LEAD_CHARS)
    return _clean_snippet(body[start : start + SNIPPET_MAX_CHARS])


def _clean_snippet(text: str) -> str:
    """스니펫의 연속 공백/줄바꿈을 한 칸으로 정리한다."""
    return re.sub(r"\s+", " ", text).strip()


def _fallback_snippet(row: DocumentMeta, query: str) -> str:
    """본문이 비어 스니펫을 만들 수 없을 때 쓰는 안내 문구."""
    return f"본문에서 '{query}' 관련 구간을 찾지 못했습니다. 제목만 일치: {row.title}"
