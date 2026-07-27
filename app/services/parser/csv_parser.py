"""CSV 문서 파서.

원문(CSV 텍스트) → `ParsedDocument` 중간 표현.
각 행을 "컬럼명: 값" 형태의 텍스트로 변환해 하나의 섹션으로 만든다.
"""

from __future__ import annotations

import csv
import io

from app.core.errors import ParserError
from app.services.parser.openapi_parser import ParsedDocument, ParsedSection


def parse_document(raw: str, title_hint: str | None = None) -> ParsedDocument:
    """CSV 원문을 행 단위 섹션으로 분리해 ParsedDocument 로 변환한다."""
    stripped = (raw or "").strip()
    if not stripped:
        raise ParserError("empty document")

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        raise ParserError("csv document has no header row")

    sections: list[ParsedSection] = []
    for idx, row in enumerate(reader):
        content = "\n".join(f"{k}: {v}" for k, v in row.items() if k)
        title = row.get(reader.fieldnames[0]) or f"Row {idx + 1}"
        sections.append(ParsedSection(title=str(title), content=content))

    title = title_hint or "Untitled CSV"
    return ParsedDocument(title=title, version="unknown", sections=sections)
