"""Notion 응답 → 평문/FileMeta 순수 변환 함수 테스트.

HTTP 를 타지 않는 순수 함수만 다룬다(어댑터 순회 테스트는
`tests/unit/test_notion_page_source.py`).
"""

from __future__ import annotations

from datetime import datetime

from app.services.documents.sources.notion_blocks import (
    UNTITLED,
    block_plain_text,
    cells_to_plain,
    child_page_to_file_meta,
    page_title,
    property_plain_text,
    rich_text_to_plain,
    to_file_meta,
)


def test_rich_text_to_plain_joins_plain_text_parts() -> None:
    """rich_text 배열의 plain_text 조각들이 순서대로 이어 붙는다."""
    items = [{"plain_text": "트러블"}, {"plain_text": "슈팅"}]

    assert rich_text_to_plain(items) == "트러블슈팅"


def test_rich_text_to_plain_returns_empty_for_non_list() -> None:
    """리스트가 아니면 빈 문자열을 돌려준다."""
    assert rich_text_to_plain(None) == ""


def test_block_plain_text_extracts_paragraph() -> None:
    """paragraph 블록의 rich_text 가 평문으로 나온다."""
    block = {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "본문 한 줄"}]}}

    assert block_plain_text(block) == "본문 한 줄"


def test_page_title_falls_back_to_untitled() -> None:
    """title 타입 속성이 없으면 UNTITLED 를 돌려준다."""
    assert page_title({"properties": {}}) == UNTITLED


def test_to_file_meta_uses_page_url_when_present() -> None:
    """페이지 응답의 url 이 있으면 그대로 쓴다."""
    page = {
        "id": "page-1",
        "url": "https://www.notion.so/team/page-1",
        "properties": {"이름": {"type": "title", "title": [{"plain_text": "장애 기록"}]}},
        "last_edited_time": "2026-07-03T00:00:00.000Z",
    }

    meta = to_file_meta(page)

    assert meta.external_id == "page-1"
    assert meta.title == "장애 기록"
    assert meta.url == "https://www.notion.so/team/page-1"


def test_to_file_meta_populates_created_at_from_created_time() -> None:
    """created_time 을 created_at 으로 채운다(mime_type/owner 는 항상 None, 개선 #2 T2)."""
    page = {
        "id": "page-1",
        "url": "https://www.notion.so/team/page-1",
        "properties": {"이름": {"type": "title", "title": [{"plain_text": "장애 기록"}]}},
        "created_time": "2026-06-01T00:00:00.000Z",
    }

    meta = to_file_meta(page)

    assert meta.created_at == datetime(2026, 6, 1, 0, 0, 0)
    assert meta.mime_type is None
    assert meta.owner is None


def test_child_page_to_file_meta_uses_block_id_as_page_id() -> None:
    """child_page 블록의 id 가 곧 하위 페이지의 page id 다."""
    block = {
        "id": "abc-def",
        "type": "child_page",
        "child_page": {"title": "하위 문서"},
        "last_edited_time": "2026-07-04T00:00:00.000Z",
    }

    meta = child_page_to_file_meta(block)

    assert meta.external_id == "abc-def"
    assert meta.title == "하위 문서"
    assert meta.url == "https://www.notion.so/abcdef"


def test_child_page_to_file_meta_populates_created_at_from_created_time() -> None:
    """created_time 을 created_at 으로 채운다(개선 #2 T2)."""
    block = {
        "id": "abc-def",
        "type": "child_page",
        "child_page": {"title": "하위 문서"},
        "created_time": "2026-06-02T00:00:00.000Z",
    }

    meta = child_page_to_file_meta(block)

    assert meta.created_at == datetime(2026, 6, 2, 0, 0, 0)


def test_heading_blocks_get_markdown_prefix() -> None:
    """heading_1/2/3 은 markdown 헤딩 마커를 달고 나온다(파서가 섹션을 쪼갤 수 있게)."""
    h1 = {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "장애 대응"}]}}
    h2 = {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "트러블슈팅"}]}}
    h3 = {"type": "heading_3", "heading_3": {"rich_text": [{"plain_text": "원인"}]}}

    assert block_plain_text(h1) == "# 장애 대응"
    assert block_plain_text(h2) == "## 트러블슈팅"
    assert block_plain_text(h3) == "### 원인"


def test_list_blocks_get_markdown_prefix() -> None:
    """리스트/인용 블록도 markdown 마커를 단다."""
    bullet = {
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"plain_text": "재시도 로직 추가"}]},
    }
    numbered = {
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"plain_text": "로그 확인"}]},
    }
    quote = {"type": "quote", "quote": {"rich_text": [{"plain_text": "인용문"}]}}

    assert block_plain_text(bullet) == "- 재시도 로직 추가"
    assert block_plain_text(numbered) == "1. 로그 확인"
    assert block_plain_text(quote) == "> 인용문"


def test_to_do_prefix_reflects_checked_state() -> None:
    """to_do 는 체크 상태를 markdown 체크박스로 표현한다."""
    done = {"type": "to_do", "to_do": {"rich_text": [{"plain_text": "배포"}], "checked": True}}
    todo = {"type": "to_do", "to_do": {"rich_text": [{"plain_text": "롤백"}], "checked": False}}

    assert block_plain_text(done) == "- [x] 배포"
    assert block_plain_text(todo) == "- [ ] 롤백"


def test_empty_block_stays_empty_without_prefix() -> None:
    """텍스트가 없는 블록에는 마커만 남지 않는다(빈 문자열)."""
    empty_heading = {"type": "heading_2", "heading_2": {"rich_text": []}}

    assert block_plain_text(empty_heading) == ""


def test_table_row_cells_join_with_pipe() -> None:
    """표 한 행의 셀들이 ' | ' 로 이어진 한 줄이 된다."""
    block = {
        "type": "table_row",
        "table_row": {
            "cells": [
                [{"plain_text": "타임아웃"}],
                [{"plain_text": "커넥션 풀 고갈"}],
                [{"plain_text": "해결"}],
            ]
        },
    }

    assert block_plain_text(block) == "타임아웃 | 커넥션 풀 고갈 | 해결"


def test_cells_to_plain_skips_empty_cells() -> None:
    """빈 셀은 건너뛰고 구분자만 남기지 않는다."""
    assert cells_to_plain([[{"plain_text": "A"}], [], [{"plain_text": "B"}]]) == "A | B"


def test_cells_to_plain_returns_empty_for_non_list() -> None:
    """cells 가 리스트가 아니면 빈 문자열이다."""
    assert cells_to_plain(None) == ""


def test_image_caption_is_extracted() -> None:
    """이미지/파일 블록의 caption 도 본문에 포함된다."""
    block = {
        "type": "image",
        "image": {"caption": [{"plain_text": "장애 그래프"}], "type": "file"},
    }

    assert block_plain_text(block) == "장애 그래프"


def test_bookmark_url_and_caption_are_extracted() -> None:
    """북마크는 caption 과 url 이 모두 본문에 들어간다."""
    block = {
        "type": "bookmark",
        "bookmark": {"caption": [{"plain_text": "런북"}], "url": "https://example.test/runbook"},
    }

    assert block_plain_text(block) == "런북 https://example.test/runbook"


def test_child_page_title_is_extracted_without_heading_prefix() -> None:
    """child_page 제목은 본문에 들어가되 헤딩 마커는 붙지 않는다.

    마커를 붙이면 파서가 섹션 경계로 잡는데, Task 4 이후 그 섹션의 본문이
    비어 파서가 섹션 자체를 버려 제목이 통째로 사라진다.
    """
    block = {"type": "child_page", "child_page": {"title": "재발 방지 대책"}}

    assert block_plain_text(block) == "재발 방지 대책"


def test_child_database_title_is_extracted() -> None:
    """child_database 제목도 본문에 남는다(블록 자체엔 행이 없다)."""
    block = {"type": "child_database", "child_database": {"title": "트러블슈팅 내역"}}

    assert block_plain_text(block) == "트러블슈팅 내역"


def test_equation_expression_is_extracted() -> None:
    """수식 블록은 expression 을 평문으로 남긴다."""
    block = {"type": "equation", "equation": {"expression": "p99 = 1200ms"}}

    assert block_plain_text(block) == "p99 = 1200ms"


def test_property_plain_text_handles_common_types() -> None:
    """DB 행에서 실제로 많이 쓰는 속성 타입들이 평문으로 나온다."""
    assert property_plain_text(
        {"type": "title", "title": [{"plain_text": "타임아웃 장애"}]}
    ) == "타임아웃 장애"
    assert property_plain_text(
        {"type": "rich_text", "rich_text": [{"plain_text": "커넥션 풀 고갈"}]}
    ) == "커넥션 풀 고갈"
    assert property_plain_text({"type": "select", "select": {"name": "P1"}}) == "P1"
    assert property_plain_text({"type": "status", "status": {"name": "해결"}}) == "해결"
    assert property_plain_text(
        {"type": "multi_select", "multi_select": [{"name": "DB"}, {"name": "네트워크"}]}
    ) == "DB, 네트워크"
    assert property_plain_text(
        {"type": "people", "people": [{"name": "홍길동"}]}
    ) == "홍길동"
    assert property_plain_text({"type": "number", "number": 3}) == "3"
    assert property_plain_text(
        {"type": "url", "url": "https://example.test/runbook"}
    ) == "https://example.test/runbook"
    assert property_plain_text({"type": "checkbox", "checkbox": True}) == "true"


def test_property_plain_text_formats_date_range() -> None:
    """날짜 속성은 start 만 있으면 그대로, end 가 있으면 범위로 낸다."""
    assert property_plain_text(
        {"type": "date", "date": {"start": "2026-07-03", "end": None}}
    ) == "2026-07-03"
    assert property_plain_text(
        {"type": "date", "date": {"start": "2026-07-03", "end": "2026-07-05"}}
    ) == "2026-07-03 ~ 2026-07-05"


def test_property_plain_text_returns_empty_for_unsupported_types() -> None:
    """미지원 타입(rollup 등)과 빈 값은 빈 문자열이다."""
    assert property_plain_text({"type": "select", "select": None}) == ""
    assert property_plain_text({}) == ""


def test_property_plain_text_excludes_relation_as_uuid_noise() -> None:
    """relation 은 UUID 뿐이라 검색 신호가 아니고 tsvector·임베딩을 오염시키므로
    계속 빈 문자열이다(51번 판정, 의도적 제외를 고정하는 가드)."""
    assert property_plain_text({"type": "relation", "relation": [{"id": "x"}]}) == ""


def test_property_plain_text_recurses_into_formula_scalars() -> None:
    """formula 결과는 GET /pages 응답 안에 이미 있어 추가 호출 없이 재귀 적용된다."""
    assert property_plain_text(
        {"type": "formula", "formula": {"type": "string", "string": "P1"}}
    ) == "P1"
    assert property_plain_text(
        {"type": "formula", "formula": {"type": "number", "number": 42}}
    ) == "42"
    assert property_plain_text(
        {
            "type": "formula",
            "formula": {"type": "date", "date": {"start": "2026-07-03", "end": None}},
        }
    ) == "2026-07-03"
    assert property_plain_text(
        {"type": "formula", "formula": {"type": "boolean", "boolean": True}}
    ) == "true"


def test_property_plain_text_formula_empty_result_is_blank_not_false() -> None:
    """수식 결과가 없으면 boolean False 로 오인되지 않도록 빈 문자열을 낸다."""
    assert property_plain_text(
        {"type": "formula", "formula": {"type": "boolean", "boolean": None}}
    ) == ""
    assert property_plain_text(
        {"type": "formula", "formula": {"type": "number", "number": None}}
    ) == ""
