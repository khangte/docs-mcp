"""prompt.py 단위 테스트: payload 직렬화/절단, 해시 결정성."""

from __future__ import annotations

from app.services.metadata.prompt import (
    EndpointMetadataInput,
    build_payload_json,
    build_user_prompt,
    compute_source_hash,
)


def _input(**overrides: object) -> EndpointMetadataInput:
    base = dict(
        method="GET",
        path="/pet/{petId}",
        summary="Find pet",
        description="Returns a single pet",
        operation_id="getPetById",
        param_names=["petId"],
        body_field_names=[],
        tags=["pet"],
    )
    base.update(overrides)
    return EndpointMetadataInput(**base)  # type: ignore[arg-type]


def test_build_payload_json_strips_html_tags() -> None:
    payload_json = build_payload_json(_input(description="<p>Returns <b>a</b> pet</p>"))
    assert "<" not in payload_json
    assert "Returns a pet" in payload_json


def test_build_payload_json_truncates_to_600_chars() -> None:
    long_description = "x" * 1000
    payload_json = build_payload_json(_input(description=long_description))
    assert payload_json.count("x") == 600


def test_build_payload_json_is_deterministic() -> None:
    first = build_payload_json(_input())
    second = build_payload_json(_input())
    assert first == second


def test_build_user_prompt_contains_payload() -> None:
    payload_json = build_payload_json(_input())
    assert payload_json in build_user_prompt(payload_json)


def test_compute_source_hash_deterministic_for_same_payload() -> None:
    payload_json = build_payload_json(_input())
    assert compute_source_hash(payload_json) == compute_source_hash(payload_json)


def test_compute_source_hash_differs_for_different_payload() -> None:
    a = build_payload_json(_input())
    b = build_payload_json(_input(summary="Different summary"))
    assert compute_source_hash(a) != compute_source_hash(b)


def test_compute_source_hash_is_64_char_hex() -> None:
    payload_json = build_payload_json(_input())
    digest = compute_source_hash(payload_json)
    assert len(digest) == 64
    int(digest, 16)  # raises if not hex
