"""PDF 바이너리에서 텍스트를 추출한다.

페이지 텍스트를 순서대로 이어붙여 하나의 문자열로 반환한다. 레이아웃/표/이미지
OCR 은 다루지 않는다(YAGNI, 후속 단계).
"""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from app.core.errors import ParserError


def extract_text(data: bytes) -> str:
    """PDF 바이트에서 페이지 텍스트를 순서대로 이어붙여 반환한다.

    Raises:
        ParserError: 손상된 파일이거나 암호화되어 읽을 수 없는 경우.
    """
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ParserError("encrypted PDF is not supported")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ParserError:
        raise
    except Exception as exc:
        raise ParserError(f"failed to parse PDF: {exc}") from exc
    return "\n\n".join(pages)
