"""canonical endpoint projection 빌더 테스트 (`docs/architect-review/101` §2.2). DB 불필요."""

from __future__ import annotations

import hashlib

from app.services.indexer.endpoint_projection import (
    _OPERATION_ALIASES_V1,
    CANONICAL_TEXT_MAX_CHARS,
    REPRESENTATION_VERSION,
    build_endpoint_projection,
)
from app.services.parser.openapi_parser import (
    ParsedEndpoint,
    ParsedParameter,
    ParsedRequestBody,
)


def _endpoint(**overrides: object) -> ParsedEndpoint:
    """테스트용 `ParsedEndpoint` — 필요한 필드만 덮어쓴다."""
    base: dict[str, object] = dict(
        method="POST",
        path="/repos/{owner}/{repo}/issues",
        operation_id="repos/create-issue",
        summary="Create an issue",
        description="<p>Create an issue.</p>",
        tags=["issues"],
        parameters=[
            ParsedParameter(name="owner", location="path", required=True),
            ParsedParameter(name="repo", location="path", required=True),
            ParsedParameter(name="state", location="query", required=False),
        ],
        request_body=ParsedRequestBody(
            content_type="application/json",
            schema={"properties": {"title": {}, "body": {}, "assignees": {}}},
            required=True,
        ),
    )
    base.update(overrides)
    return ParsedEndpoint(**base)  # type: ignore[arg-type]


def _lines(text: str) -> dict[str, str]:
    """`label: value` 줄들을 dict 로."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        label, _, value = line.partition(": ")
        out[label] = value
    return out


def test_running_example_matches_format_v1() -> None:
    """§2.2 running example — 각 줄이 고정 template·정렬 규칙과 정확히 일치한다."""
    text = build_endpoint_projection(_endpoint()).canonical_text
    assert text.splitlines() == [
        "MethodPath: POST /repos/{owner}/{repo}/issues",
        "Ancestor: repos repo",
        "Resource: issues issue",
        "Action: create add new register",
        "Phrase: create issue",
        "OperationId: repos/create-issue repos repo create issue",
        "Summary: Create an issue",
        "Description: Create an issue.",
        "Params: path owner path repo query state",
        "Body: assignees body title",
        "Tags: issues",
    ]


def test_line_order_is_fixed_regardless_of_source_order() -> None:
    """tag/body property 입력 순서를 섞어도 줄 순서·정렬 결과는 불변이다."""
    a = build_endpoint_projection(_endpoint()).canonical_text
    b = build_endpoint_projection(
        _endpoint(
            tags=["issues"],
            request_body=ParsedRequestBody(
                content_type="application/json",
                schema={"properties": {"assignees": {}, "title": {}, "body": {}}},
                required=True,
            ),
        )
    ).canonical_text
    assert a == b


def test_empty_fields_are_omitted_not_blank_lines() -> None:
    """정규화 후 빈 값의 줄은 통째로 생략한다(빈 `label:` 줄을 남기지 않는다)."""
    text = build_endpoint_projection(
        _endpoint(
            operation_id=None,
            summary="   ",
            description="",
            tags=[],
            parameters=[],
            request_body=None,
        )
    ).canonical_text
    labels = set(_lines(text))
    assert labels == {"MethodPath", "Ancestor", "Resource", "Action", "Phrase"}
    assert "\n\n" not in text


def test_whitespace_is_collapsed_and_nfkc_normalized() -> None:
    """내부 공백 한 칸화 + NFKC(전각→반각) 정규화."""
    text = build_endpoint_projection(
        _endpoint(summary="Create\t an   　 issue")
    ).canonical_text
    assert _lines(text)["Summary"] == "Create an issue"


def test_description_strips_html_and_caps_at_300_chars() -> None:
    """HTML 태그 제거 후 300자 상한(현 endpoint chunk 와 동일 규칙)."""
    raw = "<p>" + ("word " * 100).strip() + "</p>"
    text = build_endpoint_projection(_endpoint(description=raw)).canonical_text
    desc = _lines(text)["Description"]
    assert "<" not in desc
    assert len(desc) == 300


def test_total_text_capped_in_table_order() -> None:
    """총 상한 초과 시 뒤쪽 줄부터 잘린다 — 앞줄(MethodPath)은 온전하다."""
    text = build_endpoint_projection(
        _endpoint(summary="s " * 4000, description="d " * 4000)
    ).canonical_text
    assert len(text) <= CANONICAL_TEXT_MAX_CHARS
    assert text.startswith("MethodPath: POST /repos/{owner}/{repo}/issues\n")
    # 뒤쪽 줄(Params/Body/Tags)은 예산을 넘겨 잘려 나간다.
    assert "\nTags: issues" not in text


def test_version_and_param_id_subword_rules() -> None:
    """path 의 version 세그먼트는 빠지고, resource 는 결정적 단수형·subword 를 낸다."""
    text = build_endpoint_projection(
        _endpoint(
            method="GET",
            path="/v1/invoices/{invoice}/line_items",
            operation_id=None,
            parameters=[],
            request_body=None,
            tags=[],
        )
    ).canonical_text
    lines = _lines(text)
    assert "v1" not in lines["Ancestor"].split()
    assert lines["Resource"] == "line_items line_item line items item"
    assert lines["Action"] == " ".join(_OPERATION_ALIASES_V1[("GET", "collection")])


def test_source_hash_binds_version_and_text() -> None:
    """source_hash = sha256(version + '\\n' + canonical_text), 텍스트가 바뀌면 바뀐다."""
    proj = build_endpoint_projection(_endpoint())
    expected = hashlib.sha256(
        f"{REPRESENTATION_VERSION}\n{proj.canonical_text}".encode()
    ).hexdigest()
    assert proj.source_hash == expected
    assert proj.representation_version == "v1"

    other = build_endpoint_projection(_endpoint(summary="Different summary"))
    assert other.source_hash != proj.source_hash


def test_builder_is_deterministic() -> None:
    """같은 입력은 항상 같은 canonical_text/hash 를 낸다(§6 결정성 계약)."""
    a = build_endpoint_projection(_endpoint())
    b = build_endpoint_projection(_endpoint())
    assert a == b


def test_no_llm_or_business_metadata_fields_leak_in() -> None:
    """빌더는 OpenAPI 필드만 본다 — metadata/keywords/user-phrase 인자 자체가 없다."""
    import inspect

    sig = inspect.signature(build_endpoint_projection)
    assert list(sig.parameters) == ["endpoint"]
    # ParsedEndpoint 에 없는 신호(비즈니스 메타데이터)는 물리적으로 주입 불가.
    text = build_endpoint_projection(_endpoint()).canonical_text
    assert "keywords" not in text.lower()


def test_alias_table_v1_is_frozen_copy() -> None:
    """format v1 은 alias 표를 복사·동결한다 — 변경은 REPRESENTATION_VERSION 을 요구한다."""
    assert _OPERATION_ALIASES_V1 == {
        ("GET", "collection"): ("list", "index", "all", "browse"),
        ("GET", "item"): ("get", "retrieve", "fetch", "read", "show", "detail"),
        ("POST", "collection"): ("create", "add", "new", "register"),
        ("POST", "item"): ("create", "submit", "send"),
        ("PUT", "collection"): ("replace", "update", "set"),
        ("PUT", "item"): ("replace", "update", "set"),
        ("PATCH", "collection"): ("update", "modify", "edit", "change"),
        ("PATCH", "item"): ("update", "modify", "edit", "change"),
        ("DELETE", "collection"): ("delete", "remove", "clear"),
        ("DELETE", "item"): ("delete", "remove", "destroy"),
    }
