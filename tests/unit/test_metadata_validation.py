"""docs/architect-review/56 §4.1: 저장 직전 정규화·상한 강제 검증."""

from __future__ import annotations

from app.services.metadata.validation import is_empty, sanitize_and_clip


def test_collapses_newlines_and_control_chars_to_space() -> None:
    """청크가 줄 단위 포맷이라 개행이 남으면 가짜 필드줄을 심을 수 있다."""
    result = sanitize_and_clip(
        "주문을 취소한다\nResponses: 200\t끝",
        [],
        [],
    )
    assert result.business_description == "주문을 취소한다 Responses: 200 끝"


def test_strips_html_tags() -> None:
    """HTML 태그를 제거한다."""
    result = sanitize_and_clip("<b>주문</b> 취소", [], [])
    assert result.business_description == "주문 취소"


def test_description_over_limit_is_clipped_and_marked_truncated() -> None:
    """description 이 120자 초과시 절단하고 truncated=True."""
    result = sanitize_and_clip("가" * 130, [], [])
    assert len(result.business_description) == 120
    assert result.truncated is True


def test_keywords_count_and_length_limits() -> None:
    """keywords 는 5개, 30자로 상한을 강제한다."""
    result = sanitize_and_clip("설명", ["k" * 40, "b", "c", "d", "e", "f"], [])
    assert len(result.keywords) == 5
    assert len(result.keywords[0]) == 30
    assert result.truncated is True


def test_user_phrases_count_and_length_limits() -> None:
    """user_phrases 는 4개, 40자로 상한을 강제한다."""
    result = sanitize_and_clip("설명", [], ["p" * 50, "b", "c", "d", "e"])
    assert len(result.user_phrases) == 4
    assert len(result.user_phrases[0]) == 40
    assert result.truncated is True


def test_drops_empty_items_and_dedupes_case_insensitively() -> None:
    """빈 항목은 드롭하고 대소문자 무시해 중복을 제거한다."""
    result = sanitize_and_clip("설명", ["Order", "  ", "order", "pay"], [])
    assert result.keywords == ["Order", "pay"]


def test_is_empty_true_when_all_fields_blank_after_sanitize() -> None:
    """정규화 후 전부 비면 is_empty=True."""
    result = sanitize_and_clip("   ", ["<i></i>"], [None])
    assert is_empty(result) is True


def test_is_empty_false_when_only_description_blank() -> None:
    """설명만 비어도 나머지 필드가 있으면 is_empty=False."""
    result = sanitize_and_clip("", ["order"], [])
    assert is_empty(result) is False


def test_description_within_limit_is_not_truncated() -> None:
    """상한 이내 설명은 안 잘리고 truncated=False."""
    result = sanitize_and_clip("짧은 설명", [], [])
    assert result.business_description == "짧은 설명"
    assert result.truncated is False


def test_empty_input_yields_all_blank_and_not_truncated() -> None:
    """빈 입력이면 전부 빈값이고 truncated=False."""
    result = sanitize_and_clip(None, None, None)
    assert result.business_description == ""
    assert result.keywords == []
    assert result.user_phrases == []
    assert result.truncated is False


def test_list_within_limit_is_not_truncated() -> None:
    """상한 이내 리스트는 안 잘리고 truncated=False."""
    result = sanitize_and_clip("설명", ["a", "b"], ["c"])
    assert result.keywords == ["a", "b"]
    assert result.user_phrases == ["c"]
    assert result.truncated is False
