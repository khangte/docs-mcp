"""문서 소스 공통 시각 파싱 유틸.

Drive(`modifiedTime`)와 Notion(`last_edited_time`) 모두 RFC3339 문자열로
수정 시각을 돌려주므로, 파싱 규칙을 한 곳에 모아 두 어댑터가 공유한다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import get_logger

_LOG = get_logger("docs_mcp.documents.time")


def parse_rfc3339(value: str | None) -> datetime | None:
    """RFC3339 시각 문자열을 tz-naive UTC datetime 으로 바꾼다.

    `document_meta.modified_at` 컬럼이 timezone 미포함 `DateTime` 이므로 UTC 로
    정규화한 뒤 tzinfo 를 제거한다. 그래야 갱신 시 offset-naive/aware 를 섞어
    비교하다 TypeError 가 나는 일이 없다.

    Args:
        value: `2026-07-28T10:00:00.000Z` 형태의 문자열. None/빈 문자열 허용.

    Returns:
        UTC 기준 tz-naive datetime. 값이 없거나 해석할 수 없으면 None.
    """
    if not value:
        return None
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        _LOG.warning("시각 문자열을 해석할 수 없음: %s", value)
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
