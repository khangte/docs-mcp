"""문서 타입 판별 및 파서 라우팅.

`doc_type` 이 명시되면 해당 파서로, 아니면 원문 내용으로 추정해 라우팅한다.
"""

from __future__ import annotations

from app.core.errors import ParserError
from app.services.parser import csv_parser, markdown_parser, openapi_parser
from app.services.parser.openapi_parser import ParsedDocument

_KNOWN_TYPES = {"openapi", "markdown", "csv"}


def detect_doc_type(raw: str, source_url: str | None = None) -> str:
    """원문/URL 확장자로 문서 타입을 추정한다."""
    if source_url:
        lower = source_url.lower()
        if lower.endswith(".md") or lower.endswith(".markdown"):
            return "markdown"
        if lower.endswith(".csv"):
            return "csv"

    stripped = (raw or "").strip()
    if stripped.startswith("{") or "openapi:" in stripped[:200] or "swagger:" in stripped[:200]:
        return "openapi"
    first_line = stripped.splitlines()[0] if stripped else ""
    if "," in first_line and not first_line.startswith("#"):
        return "csv"
    return "markdown"


def parse_document(raw: str, doc_type: str, title_hint: str | None = None) -> ParsedDocument:
    """doc_type 에 맞는 파서로 원문을 ParsedDocument 로 변환한다."""
    if doc_type == "openapi":
        return openapi_parser.parse_document(raw)
    if doc_type == "markdown":
        return markdown_parser.parse_document(raw, title_hint=title_hint)
    if doc_type == "csv":
        return csv_parser.parse_document(raw, title_hint=title_hint)
    raise ParserError(f"unsupported doc_type: {doc_type}")
