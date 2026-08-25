"""비즈니스 메타데이터 저장 직전 정규화·상한 강제(LLM 무관 순수 로직).

docs/architect-review/56 §4.1: CLI 생성 경로와 호출 LLM write-back 경로가
같은 규칙을 쓰도록 한 곳에 모은다. 개행/제어문자 제거가 특히 중요하다 —
청크 텍스트가 줄 단위 포맷이라(`chunk_builder.build_endpoint_chunk_text`)
저장값에 개행이 남으면 가짜 필드 줄을 심을 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")

#: 55 §2.3 에서 확정한 상한(청크 480토큰 예산 안에서 약 155토큰).
KEYWORDS_MAX_COUNT = 5
KEYWORDS_MAX_CHARS = 30
PHRASES_MAX_COUNT = 4
PHRASES_MAX_CHARS = 40
DESCRIPTION_MAX_CHARS = 120


@dataclass(frozen=True)
class SanitizedMetadata:
    """정규화·절단을 마친 메타데이터 1건."""

    business_description: str
    keywords: list[str]
    user_phrases: list[str]
    truncated: bool


def sanitize_and_clip(
    business_description: object, keywords: object, user_phrases: object
) -> SanitizedMetadata:
    """HTML/제어문자를 제거하고 개수·길이 상한을 강제한다.

    입력 타입을 `object` 로 받는 이유는 LLM 이 만든 JSON 을 그대로 넘길 수
    있게 하기 위해서다 — 리스트가 아니면 빈 리스트로 취급한다.
    """
    description, desc_truncated = _clip_text(
        _sanitize_text(business_description), DESCRIPTION_MAX_CHARS
    )
    kw_items, kw_truncated = _clip_items(keywords, KEYWORDS_MAX_COUNT, KEYWORDS_MAX_CHARS)
    ph_items, ph_truncated = _clip_items(user_phrases, PHRASES_MAX_COUNT, PHRASES_MAX_CHARS)
    return SanitizedMetadata(
        business_description=description,
        keywords=kw_items,
        user_phrases=ph_items,
        truncated=desc_truncated or kw_truncated or ph_truncated,
    )


def is_empty(result: SanitizedMetadata) -> bool:
    """세 필드가 정규화 후 전부 비었는지 여부(저장 거부 판단용)."""
    return not (result.business_description or result.keywords or result.user_phrases)


def _sanitize_text(value: object) -> str:
    """HTML 태그·제어문자를 없애고 연속 공백을 한 칸으로 접는다."""
    if value is None:
        return ""
    text = _HTML_TAG_RE.sub("", str(value))
    text = _CONTROL_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _clip_text(text: str, max_chars: int) -> tuple[str, bool]:
    """문자열을 상한으로 자르고 잘렸는지 여부를 함께 반환한다."""
    if len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def _clip_items(value: object, max_count: int, max_chars: int) -> tuple[list[str], bool]:
    """리스트를 정규화·중복제거하고 개수·길이 상한을 적용한다."""
    if not isinstance(value, (list, tuple)):
        return [], False
    truncated = False
    seen: set[str] = set()
    items: list[str] = []
    for raw in value:
        text = _sanitize_text(raw)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        clipped, item_truncated = _clip_text(text, max_chars)
        truncated = truncated or item_truncated
        items.append(clipped)
    if len(items) > max_count:
        items = items[:max_count]
        truncated = True
    return items, truncated
