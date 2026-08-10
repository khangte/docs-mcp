"""`parse_version` 순수 함수 단위 테스트 (P1 항목3: version 필드/개념)."""

from __future__ import annotations

import pytest

from app.services.documents.version_parser import parse_version


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("설계서 v_1.0", "v1.0"),
        ("설계서 v2", "v2"),
        ("설계서 V 3.1", "v3.1"),
        ("...로직_v_1.0", "v1.0"),
    ],
)
def test_parse_version_normalizes_recognized_formats(title: str, expected: str) -> None:
    """v-접두 숫자 버전을 인식하고, 밑줄 구분자를 점으로 정규화한다."""
    assert parse_version(title) == expected


@pytest.mark.parametrize("title", ["rev2 변경 이력", "level2 통합 가이드", "revision2 문서"])
def test_parse_version_excludes_word_internal_v(title: str) -> None:
    """단어 내부의 v(rev2, level2 등)는 버전 표기로 인식하지 않는다."""
    assert parse_version(title) is None


def test_parse_version_uses_last_match_when_multiple_present() -> None:
    """버전 표기가 여러 개면 마지막 매치를 쓴다(제목 접미사 관례)."""
    assert parse_version("v1 초안에서 v2.3 로 개정") == "v2.3"


def test_parse_version_returns_none_when_no_version_marker() -> None:
    """버전 표기가 없으면 None(에러 아님)."""
    assert parse_version("배포 운영 가이드") is None


def test_parse_version_returns_none_for_status_marker_without_version() -> None:
    """상태 마커((최종)/final/draft 등)만 있고 숫자 버전이 없으면 None."""
    assert parse_version("결제 정책 (최종) final draft") is None
