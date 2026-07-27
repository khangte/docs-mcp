"""SyncService 등록/재색인/삭제 경로 테스트."""

from __future__ import annotations

import pytest

from app.core.errors import (
    DocumentNotFoundError,
    DuplicateDocumentError,
    IntegrationError,
    ValidationError,
)
from app.services.ingestor.openapi_fetcher import InMemoryFetcher


def test_register_with_raw_document(services_factory, sample_openapi_3: str) -> None:
    services = services_factory()
    result = services.sync_service.register(
        source_url=None, raw_document=sample_openapi_3
    )
    assert result.status == "registered"
    assert result.document.title == "Petstore API"
    assert result.document.source_url is None


def test_register_with_source_url_uses_fetcher(
    app_state, in_memory_fetcher: InMemoryFetcher, services_factory, sample_openapi_3: str
) -> None:
    in_memory_fetcher.put("https://example.com/openapi.json", sample_openapi_3)
    services = services_factory()
    result = services.sync_service.register(
        source_url="https://example.com/openapi.json", raw_document=None
    )
    assert result.status == "registered"
    assert result.document.source_url == "https://example.com/openapi.json"


def test_register_duplicate_source_url_raises(
    in_memory_fetcher: InMemoryFetcher, services_factory, sample_openapi_3: str
) -> None:
    url = "https://example.com/openapi.json"
    in_memory_fetcher.put(url, sample_openapi_3)
    services = services_factory()
    services.sync_service.register(source_url=url, raw_document=None)

    services2 = services_factory()
    with pytest.raises(DuplicateDocumentError):
        services2.sync_service.register(source_url=url, raw_document=None)


def test_register_requires_exactly_one_source(services_factory) -> None:
    services = services_factory()
    with pytest.raises(ValidationError):
        services.sync_service.register(source_url=None, raw_document=None)
    with pytest.raises(ValidationError):
        services.sync_service.register(source_url="x", raw_document="y")


def test_register_propagates_fetcher_error(
    in_memory_fetcher: InMemoryFetcher, services_factory
) -> None:
    services = services_factory()
    with pytest.raises(IntegrationError):
        services.sync_service.register(
            source_url="https://not-registered.example.com", raw_document=None
        )


def test_resync_not_found_raises(services_factory) -> None:
    services = services_factory()
    with pytest.raises(DocumentNotFoundError):
        services.sync_service.resync("does-not-exist")


def test_delete_document_removes_chunks(
    services_factory, sample_openapi_3: str, app_state
) -> None:
    services = services_factory()
    result = services.sync_service.register(
        source_url=None, raw_document=sample_openapi_3
    )
    document_id = result.document.id
    # 벡터 인덱스에 데이터가 들어있음
    assert app_state.vector_index.size() > 0

    services2 = services_factory()
    services2.sync_service.delete(document_id)

    services3 = services_factory()
    assert services3.document_repo.get(document_id) is None
    # 벡터 인덱스에서도 제거
    assert app_state.vector_index.size() == 0


def test_delete_missing_raises(services_factory) -> None:
    services = services_factory()
    with pytest.raises(DocumentNotFoundError):
        services.sync_service.delete("missing")


def test_register_markdown_document_creates_sections(services_factory) -> None:
    services = services_factory()
    raw = "# 팀 온보딩\n환영합니다\n## 개발 환경 설정\nuv sync 를 실행하세요\n"
    result = services.sync_service.register(source_url=None, raw_document=raw)
    assert result.status == "registered"
    assert result.document.doc_type == "markdown"
    assert result.sections_count == 2
    assert result.endpoints_count == 0


def test_register_csv_document_creates_sections(services_factory) -> None:
    services = services_factory()
    raw = "name,role\nAlice,Engineer\nBob,Designer\n"
    result = services.sync_service.register(source_url=None, raw_document=raw)
    assert result.status == "registered"
    assert result.document.doc_type == "csv"
    assert result.sections_count == 2


def test_register_with_explicit_doc_type_overrides_detection(services_factory) -> None:
    services = services_factory()
    raw = "just plain text, not obviously any type"
    result = services.sync_service.register(
        source_url=None, raw_document=raw, doc_type="markdown"
    )
    assert result.document.doc_type == "markdown"
