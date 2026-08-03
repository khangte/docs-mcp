"""PDF/DOCX 텍스트 추출·섹션화·register 통합 테스트."""

from __future__ import annotations

import base64
from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.core.errors import ParserError, ValidationError
from app.services.parser import docx_parser, pdf_parser
from app.services.parser.document_router import extract_text, parse_document


def _make_pdf_bytes(text: str) -> bytes:
    """지정한 문자열을 본문으로 갖는 최소 1페이지 PDF 바이트를 만든다."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)

    content = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode()
    stream_obj = DecodedStreamObject()
    stream_obj.set_data(content)
    stream_ref = writer._add_object(stream_obj)

    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font_ref = writer._add_object(font)

    resources = DictionaryObject()
    font_dict = DictionaryObject()
    font_dict[NameObject("/F1")] = font_ref
    resources[NameObject("/Font")] = font_dict

    page[NameObject("/Contents")] = stream_ref
    page[NameObject("/Resources")] = resources

    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    """지정한 문단들을 담은 DOCX 바이트를 만든다."""
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buf = BytesIO()
    document.save(buf)
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --- pdf_parser.extract_text -----------------------------------------------


def test_pdf_extract_text_returns_page_content() -> None:
    """PDF 페이지 텍스트가 추출된다."""
    data = _make_pdf_bytes("Hello PDF")

    text = pdf_parser.extract_text(data)

    assert "Hello PDF" in text


def test_pdf_extract_text_raises_parser_error_on_corrupt_bytes() -> None:
    """손상된 PDF 바이트는 ParserError 다."""
    with pytest.raises(ParserError):
        pdf_parser.extract_text(b"not a real pdf")


# --- docx_parser.extract_text ------------------------------------------------


def test_docx_extract_text_joins_paragraphs() -> None:
    """DOCX 문단 텍스트가 순서대로 이어붙여진다."""
    data = _make_docx_bytes(["첫 문단", "둘째 문단"])

    text = docx_parser.extract_text(data)

    assert "첫 문단" in text
    assert "둘째 문단" in text
    assert text.index("첫 문단") < text.index("둘째 문단")


def test_docx_extract_text_raises_parser_error_on_corrupt_bytes() -> None:
    """손상된 DOCX 바이트는 ParserError 다."""
    with pytest.raises(ParserError):
        docx_parser.extract_text(b"not a real docx")


# --- document_router.extract_text (base64 디코드 + 위임) ----------------------


def test_router_extract_text_decodes_base64_and_extracts_pdf() -> None:
    """base64 로 인코딩된 PDF 를 디코드해 텍스트를 추출한다."""
    data = _make_pdf_bytes("Router PDF")

    text = extract_text(_b64(data), "pdf")

    assert "Router PDF" in text


def test_router_extract_text_decodes_base64_and_extracts_docx() -> None:
    """base64 로 인코딩된 DOCX 를 디코드해 텍스트를 추출한다."""
    data = _make_docx_bytes(["Router DOCX 문단"])

    text = extract_text(_b64(data), "docx")

    assert "Router DOCX 문단" in text


def test_router_extract_text_invalid_base64_raises_parser_error() -> None:
    """잘못된 base64 문자열은 ParserError 다."""
    with pytest.raises(ParserError):
        extract_text("!!!not-base64!!!", "pdf")


def test_router_extract_text_unsupported_doc_type_raises_parser_error() -> None:
    """pdf/docx 가 아닌 doc_type 은 ParserError 다."""
    with pytest.raises(ParserError):
        extract_text(_b64(b"x"), "markdown")


# --- parse_document 의 pdf/docx 분기(섹션화) ----------------------------------


def test_parse_document_sections_extracted_pdf_text_like_markdown() -> None:
    """추출된 pdf 텍스트가 markdown 파서로 섹션화된다."""
    extracted = "# 제목\n본문 내용"

    parsed = parse_document(extracted, "pdf")

    assert parsed.sections
    assert parsed.sections[0].title == "제목"


def test_parse_document_sections_extracted_docx_text_without_heading_is_single_section() -> None:
    """헤딩이 없는 추출 텍스트는 '개요' 단일 섹션으로 묶인다."""
    extracted = "그냥 본문 문단"

    parsed = parse_document(extracted, "docx")

    assert len(parsed.sections) == 1
    assert parsed.sections[0].title == "개요"


# --- register() 통합: base64 raw_document + doc_type 지정 ---------------------


def test_register_pdf_stores_extracted_text_as_raw_text(services_factory) -> None:
    """pdf doc_type 으로 등록하면 raw_text 에 추출된 텍스트가 저장된다."""
    data = _make_pdf_bytes("Registered PDF Content")
    services = services_factory()

    result = services.sync_service.register(
        project="default",
        source_url=None,
        raw_document=_b64(data),
        doc_type="pdf",
    )

    assert result.status == "registered"
    assert "Registered PDF Content" in result.document.raw_text
    assert result.sections_count >= 1


def test_register_docx_stores_extracted_text_as_raw_text(services_factory) -> None:
    """docx doc_type 으로 등록하면 raw_text 에 추출된 텍스트가 저장된다."""
    data = _make_docx_bytes(["Registered DOCX paragraph"])
    services = services_factory()

    result = services.sync_service.register(
        project="default",
        source_url=None,
        raw_document=_b64(data),
        doc_type="docx",
    )

    assert result.status == "registered"
    assert "Registered DOCX paragraph" in result.document.raw_text
    assert result.sections_count >= 1


def test_register_then_resync_pdf_reparses_stored_text(services_factory) -> None:
    """pdf 등록 후 resync 하면 저장된 텍스트(raw_text)를 그대로 재파싱해 성립한다."""
    data = _make_pdf_bytes("Resync PDF Content")
    services = services_factory()
    result = services.sync_service.register(
        project="default",
        source_url=None,
        raw_document=_b64(data),
        doc_type="pdf",
    )

    resynced = services.sync_service.resync(result.document.id, force=True)

    assert resynced.status == "reindexed"
    assert "Resync PDF Content" in resynced.document.raw_text


def test_register_pdf_with_source_url_raises_validation_error(services_factory) -> None:
    """pdf/docx 는 base64 raw_document 로만 받는다 — source_url 조합은 ValidationError 다."""
    services = services_factory()

    with pytest.raises(ValidationError):
        services.sync_service.register(
            project="default",
            source_url="https://example.com/document.pdf",
            raw_document=None,
            doc_type="pdf",
        )
