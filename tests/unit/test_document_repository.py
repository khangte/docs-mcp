"""DocumentRepository.list_resyncable 테스트."""

from __future__ import annotations

from app.services.ingestor.openapi_fetcher import InMemoryFetcher


def test_list_resyncable_excludes_raw_document(
    services_factory, in_memory_fetcher: InMemoryFetcher, sample_openapi_3: str
) -> None:
    in_memory_fetcher.put("https://example.com/openapi.json", sample_openapi_3)
    services = services_factory()
    services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    services.sync_service.register(
        project="default", source_url="https://example.com/openapi.json", raw_document=None
    )

    services2 = services_factory()
    resyncable = services2.document_repo.list_resyncable()

    assert len(resyncable) == 1
    assert resyncable[0].source_url == "https://example.com/openapi.json"


def test_list_resyncable_filters_by_project(
    services_factory, in_memory_fetcher: InMemoryFetcher, sample_openapi_3: str
) -> None:
    in_memory_fetcher.put("https://example.com/a.json", sample_openapi_3)
    in_memory_fetcher.put("https://example.com/b.json", sample_openapi_3)
    services = services_factory()
    services.sync_service.register(
        project="shop-a", source_url="https://example.com/a.json", raw_document=None
    )
    services.sync_service.register(
        project="shop-b", source_url="https://example.com/b.json", raw_document=None
    )

    services2 = services_factory()
    resyncable = services2.document_repo.list_resyncable(project="shop-a")

    assert len(resyncable) == 1
    assert resyncable[0].project == "shop-a"


def test_list_resyncable_empty_when_no_url_documents(
    services_factory, sample_openapi_3: str
) -> None:
    services = services_factory()
    services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )

    services2 = services_factory()
    assert services2.document_repo.list_resyncable() == []
