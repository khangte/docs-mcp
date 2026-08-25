"""generator.py 단위 테스트: skip 4분기, 절단, upsert, dry-run."""

from __future__ import annotations

from typing import Any

import pytest

from app.models import EndpointBusinessMetadata
from app.services.metadata.generator import (
    _clip_items,
    _truncate_and_validate,
    generate_business_metadata,
    select_targets,
)


class _FakeLLMClient:
    """실제 HTTP 없이 고정 응답을 돌려주는 테스트용 대역."""

    def __init__(self, model: str = "claude-fake", response: dict[str, Any] | None = None) -> None:
        self.model = model
        self._response = response or {
            "business_description": "펫 정보를 조회한다",
            "keywords": ["pet", "조회"],
            "user_phrases": ["펫 찾기", "동물 검색", "find pet", "get animal"],
        }
        self.calls = 0

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        self.calls += 1
        return self._response


def _register(services_factory, sample_openapi_3: str):
    services = services_factory()
    result = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    return services, result


def test_select_targets_includes_all_endpoints_with_no_existing_row(
    services_factory, sample_openapi_3: str
) -> None:
    services, result = _register(services_factory, sample_openapi_3)
    targets = select_targets(
        services.session, document_ids=[result.document.id], project=None, model="m", force=False
    )
    assert len(targets) == result.endpoints_count


def test_generate_business_metadata_creates_rows(services_factory, sample_openapi_3: str) -> None:
    services, result = _register(services_factory, sample_openapi_3)
    llm = _FakeLLMClient()

    summary = generate_business_metadata(
        services.session, llm, document_ids=[result.document.id]
    )

    assert summary.total == result.endpoints_count
    assert summary.generated == result.endpoints_count
    assert summary.failed == []
    rows = services.session.query(EndpointBusinessMetadata).all()
    assert len(rows) == result.endpoints_count
    assert all(row.model == "claude-fake" for row in rows)
    assert all(row.source_hash for row in rows)


def test_generate_business_metadata_skips_unchanged_rows_on_second_run(
    services_factory, sample_openapi_3: str
) -> None:
    services, result = _register(services_factory, sample_openapi_3)
    llm = _FakeLLMClient()

    generate_business_metadata(services.session, llm, document_ids=[result.document.id])
    llm.calls = 0
    second_summary = generate_business_metadata(
        services.session, llm, document_ids=[result.document.id]
    )

    assert second_summary.total == 0
    assert llm.calls == 0


def test_generate_business_metadata_force_regenerates_unchanged_rows(
    services_factory, sample_openapi_3: str
) -> None:
    services, result = _register(services_factory, sample_openapi_3)
    llm = _FakeLLMClient()

    generate_business_metadata(services.session, llm, document_ids=[result.document.id])
    second_summary = generate_business_metadata(
        services.session, llm, document_ids=[result.document.id], force=True
    )

    assert second_summary.total == result.endpoints_count


def test_generate_business_metadata_regenerates_on_model_change(
    services_factory, sample_openapi_3: str
) -> None:
    services, result = _register(services_factory, sample_openapi_3)
    generate_business_metadata(
        services.session, _FakeLLMClient(model="model-a"), document_ids=[result.document.id]
    )

    summary = generate_business_metadata(
        services.session, _FakeLLMClient(model="model-b"), document_ids=[result.document.id]
    )

    assert summary.total == result.endpoints_count
    rows = services.session.query(EndpointBusinessMetadata).all()
    assert all(row.model == "model-b" for row in rows)


def test_dry_run_does_not_write_rows(services_factory, sample_openapi_3: str) -> None:
    services, result = _register(services_factory, sample_openapi_3)
    llm = _FakeLLMClient()

    summary = generate_business_metadata(
        services.session, llm, document_ids=[result.document.id], dry_run=True
    )

    assert summary.total == result.endpoints_count
    assert llm.calls == 0
    rows = services.session.query(EndpointBusinessMetadata).all()
    assert rows == []


def test_limit_caps_target_count(services_factory, sample_openapi_3: str) -> None:
    services, result = _register(services_factory, sample_openapi_3)
    llm = _FakeLLMClient()

    summary = generate_business_metadata(
        services.session, llm, document_ids=[result.document.id], limit=1
    )

    assert summary.total == 1
    assert summary.generated == 1


def test_generate_business_metadata_records_failure_without_aborting(
    services_factory, sample_openapi_3: str
) -> None:
    from app.core.errors import IntegrationError

    services, result = _register(services_factory, sample_openapi_3)

    class _FlakyLLMClient:
        model = "claude-flaky"

        def generate_json(self, system: str, user: str) -> dict[str, Any]:
            raise IntegrationError("boom")

    summary = generate_business_metadata(
        services.session, _FlakyLLMClient(), document_ids=[result.document.id]
    )

    assert summary.generated == 0
    assert len(summary.failed) == result.endpoints_count
    rows = services.session.query(EndpointBusinessMetadata).all()
    assert rows == []


@pytest.mark.parametrize(
    ("field", "value", "expected_truncated"),
    [
        ("business_description", "x" * 200, True),
        ("business_description", "short", False),
    ],
)
def test_truncate_and_validate_description(
    field: str, value: str, expected_truncated: bool
) -> None:
    description, _keywords, _phrases, truncated = _truncate_and_validate({field: value})
    assert truncated is expected_truncated
    assert len(description) <= 120


def test_truncate_and_validate_caps_keyword_count_and_length() -> None:
    data = {"keywords": ["x" * 50] * 10}
    _description, keywords, _phrases, truncated = _truncate_and_validate(data)
    assert truncated is True
    assert len(keywords) == 5
    assert all(len(k) <= 30 for k in keywords)


def test_truncate_and_validate_caps_phrase_count_and_length() -> None:
    data = {"user_phrases": ["y" * 60] * 10}
    _description, _keywords, phrases, truncated = _truncate_and_validate(data)
    assert truncated is True
    assert len(phrases) == 4
    assert all(len(p) <= 40 for p in phrases)


def test_truncate_and_validate_handles_missing_fields() -> None:
    description, keywords, phrases, truncated = _truncate_and_validate({})
    assert description == ""
    assert keywords == []
    assert phrases == []
    assert truncated is False


def test_clip_items_no_truncation_when_within_limit() -> None:
    clipped, truncated = _clip_items(["ok"], max_chars=10)
    assert clipped == ["ok"]
    assert truncated is False
