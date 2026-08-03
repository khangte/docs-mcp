"""DOCX 바이너리에서 텍스트를 추출한다.

문단 텍스트를 순서대로 이어붙여 하나의 문자열로 반환한다. 표/이미지/스타일
보존은 다루지 않는다(YAGNI, 후속 단계).
"""

from __future__ import annotations

from io import BytesIO

from docx import Document

from app.core.errors import ParserError


def extract_text(data: bytes) -> str:
    """DOCX 바이트에서 문단 텍스트를 순서대로 이어붙여 반환한다.

    Raises:
        ParserError: 손상된 파일이거나 DOCX 형식이 아닌 경우.
    """
    try:
        document = Document(BytesIO(data))
        paragraphs = [p.text for p in document.paragraphs]
    except Exception as exc:
        raise ParserError(f"failed to parse DOCX: {exc}") from exc
    return "\n\n".join(paragraphs)
