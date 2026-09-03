"""문서 타입 판별 및 파서 라우팅.

`doc_type` 이 명시되면 해당 파서로, 아니면 원문 내용으로 추정해 라우팅한다.
"""

from __future__ import annotations

import base64
import binascii

from app.core.errors import ParserError
from app.services.parser import (
    csv_parser,
    docx_parser,
    markdown_parser,
    openapi_parser,
    pdf_parser,
)
from app.services.parser.openapi_parser import ParsedDocument

_KNOWN_TYPES = {"openapi", "markdown", "csv", "pdf", "docx"}
_BINARY_TYPES = {"pdf", "docx"}


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
    if _looks_like_csv(stripped):
        return "csv"
    return "markdown"


def _looks_like_csv(stripped: str) -> bool:
    """헤더 줄과 둘째 줄의 쉼표 필드 개수가 같을 때만 CSV 로 판단한다.

    쉼표 하나만으로 CSV 로 판정하면 `"안녕하세요, 반갑습니다"` 처럼 쉼표
    포함 문장으로 시작하는 마크다운을 오판한다. 데이터 행이 최소 하나
    있고 컬럼 수가 헤더와 일치해야 CSV 로 본다(2줄 미만이면 CSV 여부를
    구조로 판단할 수 없어 markdown 취급).
    """
    lines = stripped.splitlines()
    if len(lines) < 2 or lines[0].startswith("#"):
        return False
    header_fields = lines[0].count(",") + 1
    if header_fields < 2:
        return False
    return lines[1].count(",") + 1 == header_fields


def extract_text(raw_b64: str, doc_type: str) -> str:
    """base64 로 인코딩된 pdf/docx 원문을 디코드해 텍스트를 추출한다.

    Args:
        raw_b64: base64 로 인코딩된 문서 바이너리.
        doc_type: `"pdf"` 또는 `"docx"`.

    Raises:
        ParserError: base64 디코드 실패, doc_type 이 지원 범위 밖, 또는
            추출 자체가 실패(암호화·손상 파일 등)한 경우.
    """
    if doc_type not in _BINARY_TYPES:
        raise ParserError(f"unsupported binary doc_type: {doc_type}")
    try:
        data = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ParserError(f"invalid base64 for {doc_type}: {exc}") from exc

    if doc_type == "pdf":
        return pdf_parser.extract_text(data)
    return docx_parser.extract_text(data)


def parse_document(raw: str, doc_type: str, title_hint: str | None = None) -> ParsedDocument:
    """doc_type 에 맞는 파서로 원문을 ParsedDocument 로 변환한다.

    pdf/docx 는 이미 추출된 텍스트(`raw`)를 markdown 파서로 섹션화한다 —
    `sync_service.register()` 가 원본 base64 를 `extract_text()` 로 먼저
    텍스트로 치환한 뒤 이 함수를 호출하는 구조다.
    """
    if doc_type == "openapi":
        return openapi_parser.parse_document(raw)
    if doc_type == "markdown":
        return markdown_parser.parse_document(raw, title_hint=title_hint)
    if doc_type == "csv":
        return csv_parser.parse_document(raw, title_hint=title_hint)
    if doc_type in _BINARY_TYPES:
        return markdown_parser.parse_document(raw, title_hint=title_hint)
    raise ParserError(f"unsupported doc_type: {doc_type}")
