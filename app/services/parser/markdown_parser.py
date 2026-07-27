"""Markdown 문서 파서.

원문(Markdown 텍스트) → `ParsedDocument` 중간 표현.
`#`/`##` 등 헤딩 단위로 섹션을 분리한다. 첫 heading 이전 내용은 "개요" 섹션으로 묶는다.
"""

from __future__ import annotations

import re

from app.core.errors import ParserError
from app.services.parser.openapi_parser import ParsedDocument, ParsedSection

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def parse_document(raw: str, title_hint: str | None = None) -> ParsedDocument:
    """Markdown 원문을 헤딩 단위 섹션으로 분리해 ParsedDocument 로 변환한다."""
    stripped = (raw or "").strip()
    if not stripped:
        raise ParserError("empty document")

    lines = raw.splitlines()
    sections: list[ParsedSection] = []
    current_title = "개요"
    current_lines: list[str] = []

    def _flush() -> None:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append(ParsedSection(title=current_title, content=content))

    first_heading: str | None = None
    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            _flush()
            current_title = match.group(2).strip()
            if first_heading is None:
                first_heading = current_title
            current_lines = []
        else:
            current_lines.append(line)
    _flush()

    title = title_hint or first_heading or "Untitled Document"
    return ParsedDocument(title=title, version="unknown", sections=sections)
