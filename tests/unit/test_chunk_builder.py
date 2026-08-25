"""청크 빌더 테스트."""

from __future__ import annotations

from app.models import EndpointBusinessMetadata
from app.services.indexer.chunk_builder import build_chunks, build_endpoint_chunk_text
from app.services.parser.openapi_parser import (
    ParsedDocument,
    ParsedEndpoint,
    ParsedParameter,
    ParsedRequestBody,
    ParsedSchema,
    ParsedSection,
    parse_document,
)


def _count_words(text: str) -> int:
    return len(text.split())


def test_endpoint_chunk_text_contains_essentials(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    ids = {(e.method, e.path): f"id-{i}" for i, e in enumerate(parsed.endpoints)}
    chunks = build_chunks(parsed, ids)
    endpoint_chunks = [c for c in chunks if c.chunk_type == "endpoint"]
    assert len(endpoint_chunks) == len(parsed.endpoints)
    get_pet_chunk = next(
        c for c in endpoint_chunks if ids[("GET", "/pet/{petId}")] == c.ref_id
    )
    text = get_pet_chunk.text
    assert "[GET]" in text
    assert "/pet/{petId}" in text
    assert "find pet by id" in text.lower()
    assert "petId" in text


def test_endpoint_chunk_text_includes_operation_id() -> None:
    """docs/architect-review/37 5b: operationId 는 청크 헤더에 포함돼 검색 가능해야 한다."""
    endpoint = ParsedEndpoint(
        method="GET",
        path="/pet/{petId}",
        operation_id="getPetById",
        summary="find pet by id",
        description="",
    )
    text = build_endpoint_chunk_text(endpoint)
    assert "getPetById" in text
    assert text.splitlines()[0].startswith("[GET]")


def test_endpoint_chunk_text_omits_operation_id_line_when_absent() -> None:
    """operation_id 가 없으면 OperationId 줄 자체를 생략한다."""
    endpoint = ParsedEndpoint(
        method="GET",
        path="/pet/{petId}",
        operation_id=None,
        summary="find pet by id",
        description="",
    )
    text = build_endpoint_chunk_text(endpoint)
    assert "OperationId" not in text


def test_endpoint_chunk_text_includes_inline_request_body_field_names() -> None:
    """docs/architect-review/30 §9.3-1: request body 인라인 프로퍼티가 Body: 줄로 실린다."""
    endpoint = ParsedEndpoint(
        method="POST",
        path="/v1/charges",
        operation_id="createCharge",
        summary="Create a charge",
        description="",
        request_body=ParsedRequestBody(
            content_type="application/x-www-form-urlencoded",
            schema={
                "type": "object",
                "properties": {"currency": {"type": "string"}, "amount": {"type": "integer"}},
            },
        ),
    )
    text = build_endpoint_chunk_text(endpoint)
    assert "Body: amount, currency" in text


def test_endpoint_chunk_text_places_structured_fields_before_description() -> None:
    """docs/architect-review/30 §11.2: overflow 시 꼬리 truncation에서 고신호
    필드명(Params·Body)이 저신호 description보다 먼저 잘리지 않도록, header 다음
    Params/Body를 description보다 앞에 배치한다."""
    endpoint = ParsedEndpoint(
        method="POST",
        path="/v1/charges",
        operation_id="createCharge",
        summary="Create a charge",
        description="장문 설명 텍스트",
        tags=["payments"],
        parameters=[ParsedParameter(name="idempotency_key", location="header", required=False)],
        request_body=ParsedRequestBody(
            content_type="application/x-www-form-urlencoded",
            schema={"type": "object", "properties": {"currency": {"type": "string"}}},
        ),
    )
    text = build_endpoint_chunk_text(endpoint)
    lines = text.splitlines()
    assert lines[0].startswith("[POST]")
    params_idx = next(i for i, line in enumerate(lines) if line.startswith("Params:"))
    body_idx = next(i for i, line in enumerate(lines) if line.startswith("Body:"))
    description_idx = next(i for i, line in enumerate(lines) if line == "장문 설명 텍스트")
    responses_idx = len(lines)
    assert 0 < params_idx < body_idx < description_idx < responses_idx


def test_endpoint_chunk_text_strips_html_tags_from_description() -> None:
    """docs/architect-review/53/54 arm B: description의 HTML 태그를 제거한다."""
    endpoint = ParsedEndpoint(
        method="GET",
        path="/v1/charges",
        operation_id=None,
        summary="",
        description="<p>Retrieves a <strong>charge</strong>.</p>",
    )
    text = build_endpoint_chunk_text(endpoint)
    assert "<p>" not in text
    assert "<strong>" not in text
    assert "Retrieves a charge." in text


def test_endpoint_chunk_text_truncates_description_to_300_chars() -> None:
    """docs/architect-review/54: HTML strip 후 description을 앞 300자에서 자른다."""
    endpoint = ParsedEndpoint(
        method="GET",
        path="/v1/charges",
        operation_id=None,
        summary="",
        description="x" * 500,
    )
    text = build_endpoint_chunk_text(endpoint)
    description_line = next(line for line in text.splitlines() if line.startswith("x"))
    assert len(description_line) == 300


def test_endpoint_chunk_text_without_metadata_has_no_keywords_or_phrases_lines() -> None:
    """metadata 미주입(기본값)이면 Keywords:/Phrases: 줄이 생기지 않는다(52 §(2))."""
    endpoint = ParsedEndpoint(
        method="GET", path="/v1/charges", operation_id=None, summary="", description=""
    )
    text = build_endpoint_chunk_text(endpoint)
    assert "Keywords:" not in text
    assert "Phrases:" not in text


def test_endpoint_chunk_text_with_metadata_places_keywords_and_phrases_after_header() -> None:
    """docs/architect-review/52 §(3)/54 §4: Keywords:/Phrases:는 header 직후에 온다."""
    endpoint = ParsedEndpoint(
        method="GET",
        path="/v1/charges",
        operation_id="getCharge",
        summary="Get a charge",
        description="설명",
    )
    metadata = EndpointBusinessMetadata(
        document_id="doc-1", method="GET", path="/v1/charges", business_description="비즈니스 설명"
    )
    metadata.keywords = ["결제", "환불"]
    metadata.user_phrases = ["결제 취소하는 법"]
    text = build_endpoint_chunk_text(endpoint, metadata=metadata)
    lines = text.splitlines()
    assert lines[0].startswith("[GET]")
    keywords_idx = next(i for i, line in enumerate(lines) if line.startswith("Keywords:"))
    phrases_idx = next(i for i, line in enumerate(lines) if line.startswith("Phrases:"))
    operation_id_idx = next(i for i, line in enumerate(lines) if line.startswith("OperationId:"))
    assert keywords_idx == 1
    assert phrases_idx == 2
    assert operation_id_idx > phrases_idx
    assert "결제, 환불" in text
    assert "결제 취소하는 법" in text
    assert "비즈니스 설명" in text


def test_schema_chunk_created(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    schema_ids = {i: f"doc:schema:{i}" for i in range(len(parsed.schemas))}
    chunks = build_chunks(parsed, endpoint_ids={}, schema_ids=schema_ids)
    schema_chunks = [c for c in chunks if c.chunk_type == "schema"]
    assert {c.ref_id for c in schema_chunks} == set(schema_ids.values())
    names = {c.text.splitlines()[0] for c in schema_chunks}
    assert names == {"Schema: Pet", "Schema: User"}


def test_schema_chunk_without_schema_ids_is_skipped(sample_openapi_3: str) -> None:
    """schema_ids 미주입(맵에 없음)이면 endpoint/section과 동일하게 건너뛴다."""
    parsed = parse_document(sample_openapi_3)
    chunks = build_chunks(parsed, endpoint_ids={})
    schema_chunks = [c for c in chunks if c.chunk_type == "schema"]
    assert schema_chunks == []


def test_schema_chunk_ref_id_bound_even_for_long_schema_name() -> None:
    """docs/architect-review/29: schema.name이 64자를 넘어도 ref_id는 바운드 id라 영향 없다."""
    long_name = "x" * 135
    parsed = ParsedDocument(
        title="doc",
        version="unknown",
        schemas=[ParsedSchema(name=long_name)],
    )
    chunks = build_chunks(parsed, endpoint_ids={}, schema_ids={0: "doc:schema:0"})
    schema_chunks = [c for c in chunks if c.chunk_type == "schema"]
    assert len(schema_chunks) == 1
    assert schema_chunks[0].ref_id == "doc:schema:0"
    assert len(schema_chunks[0].ref_id) <= 64
    assert long_name in schema_chunks[0].text


def test_empty_endpoint_ids_skips_endpoint_chunks(sample_openapi_3: str) -> None:
    parsed = parse_document(sample_openapi_3)
    chunks = build_chunks(parsed, endpoint_ids={})
    endpoint_chunks = [c for c in chunks if c.chunk_type == "endpoint"]
    assert endpoint_chunks == []


def _long_section_document() -> ParsedDocument:
    content = "\n\n".join(f"para {i} " + "word " * 5 for i in range(10))
    return ParsedDocument(
        title="doc",
        version="unknown",
        sections=[ParsedSection(title="개요", content=content)],
    )


def test_section_without_count_tokens_stays_single_chunk() -> None:
    """count_tokens 미지정(기본 하위호환)이면 섹션당 청크 1개 그대로."""
    parsed = _long_section_document()
    chunks = build_chunks(parsed, endpoint_ids={}, section_ids={0: "doc:section:0"})
    section_chunks = [c for c in chunks if c.chunk_type == "section"]
    assert len(section_chunks) == 1


def test_section_over_token_limit_splits_into_multiple_chunks_same_ref_id() -> None:
    """docs/23: count_tokens 주어지고 상한 초과 시 section_splitter 로 분할, ref_id는 동일 유지."""
    parsed = _long_section_document()
    chunks = build_chunks(
        parsed,
        endpoint_ids={},
        section_ids={0: "doc:section:0"},
        count_tokens=_count_words,
        token_limit=10,
    )
    section_chunks = [c for c in chunks if c.chunk_type == "section"]
    assert len(section_chunks) > 1
    assert {c.ref_id for c in section_chunks} == {"doc:section:0"}
