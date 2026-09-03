"""Markdown/CSV 파서 및 문서 타입 라우팅 테스트."""

from __future__ import annotations

import pytest

from app.core.errors import ParserError
from app.services.parser import csv_parser, markdown_parser
from app.services.parser.document_router import detect_doc_type, parse_document


def test_markdown_parser_splits_by_heading() -> None:
    raw = "# Title\nintro\n## Section A\ncontent a\n## Section B\ncontent b\n"
    parsed = markdown_parser.parse_document(raw)
    assert parsed.title == "Title"
    titles = [s.title for s in parsed.sections]
    assert titles == ["Title", "Section A", "Section B"]


def test_markdown_parser_rejects_empty() -> None:
    with pytest.raises(ParserError):
        markdown_parser.parse_document("   ")


def test_csv_parser_builds_section_per_row() -> None:
    raw = "name,role\nAlice,Engineer\nBob,Designer\n"
    parsed = csv_parser.parse_document(raw)
    assert len(parsed.sections) == 2
    assert parsed.sections[0].title == "Alice"
    assert "role: Engineer" in parsed.sections[0].content


def test_csv_parser_rejects_empty() -> None:
    with pytest.raises(ParserError):
        csv_parser.parse_document("")


def test_detect_doc_type_by_extension() -> None:
    assert detect_doc_type("anything", "https://example.com/doc.md") == "markdown"
    assert detect_doc_type("anything", "https://example.com/doc.csv") == "csv"


def test_detect_doc_type_by_content() -> None:
    assert detect_doc_type('{"openapi": "3.0.0"}') == "openapi"
    assert detect_doc_type("name,role\nAlice,Engineer\n") == "csv"
    assert detect_doc_type("# Heading\nbody") == "markdown"


def test_detect_doc_type_comma_sentence_is_not_csv() -> None:
    """쉼표 포함 문장으로 시작하는 마크다운을 CSV 로 오판하지 않는다."""
    assert detect_doc_type("안녕하세요, 반갑습니다\n\n# 제목\n본문") == "markdown"
    assert detect_doc_type("Hello, world") == "markdown"


def test_detect_doc_type_csv_requires_matching_column_count() -> None:
    """헤더와 둘째 줄의 컬럼 수가 다르면 CSV 로 보지 않는다."""
    assert detect_doc_type("a,b\nsingle line without matching commas") == "markdown"


def test_parse_document_routes_by_doc_type() -> None:
    md = parse_document("# Title\nbody", "markdown")
    assert md.sections[0].title == "Title"

    csv_doc = parse_document("name\nAlice\n", "csv")
    assert csv_doc.sections[0].title == "Alice"


def test_parse_document_rejects_unknown_doc_type() -> None:
    with pytest.raises(ParserError):
        parse_document("x", "unknown")
