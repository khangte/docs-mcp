"""/documents 통합 테스트."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_register_document_with_raw(async_client, sample_openapi_3: str) -> None:
    async with async_client as client:
        response = await client.post(
            "/documents",
            json={"raw_document": sample_openapi_3},
        )
    assert response.status_code == 200
    body = response.json()
    # 샘플 문서엔 엔드포인트 4개 (POST /pet, GET/DELETE /pet/{petId}, POST /user)
    assert body["endpoints_count"] == 4
    assert body["schemas_count"] == 2
    assert body["title"] == "Petstore API"
    assert body["source_url"] is None
    assert body["document_id"]


@pytest.mark.asyncio
async def test_register_duplicate_source_url_conflict(
    async_client, sample_openapi_3: str, in_memory_fetcher
) -> None:
    url = "https://example.com/api.json"
    in_memory_fetcher.put(url, sample_openapi_3)
    async with async_client as client:
        first = await client.post("/documents", json={"source_url": url})
        assert first.status_code == 200
        second = await client.post("/documents", json={"source_url": url})
    assert second.status_code == 409
    body = second.json()
    assert body["error"]["type"] == "duplicate_document"


@pytest.mark.asyncio
async def test_register_invalid_requires_xor(async_client) -> None:
    async with async_client as client:
        response = await client.post("/documents", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_documents(async_client, sample_openapi_3: str) -> None:
    async with async_client as client:
        await client.post("/documents", json={"raw_document": sample_openapi_3})
        response = await client.get("/documents")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["title"] == "Petstore API"
    assert items[0]["endpoints_count"] == 4


@pytest.mark.asyncio
async def test_get_document_detail_includes_history(
    async_client, sample_openapi_3: str
) -> None:
    async with async_client as client:
        register = await client.post(
            "/documents", json={"raw_document": sample_openapi_3}
        )
        document_id = register.json()["document_id"]
        response = await client.get(f"/documents/{document_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == document_id
    statuses = [h["status"] for h in body["sync_history"]]
    assert "registered" in statuses


@pytest.mark.asyncio
async def test_get_document_not_found(async_client) -> None:
    async with async_client as client:
        response = await client.get("/documents/does-not-exist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_cascade(async_client, sample_openapi_3: str) -> None:
    async with async_client as client:
        register = await client.post(
            "/documents", json={"raw_document": sample_openapi_3}
        )
        document_id = register.json()["document_id"]
        delete = await client.delete(f"/documents/{document_id}")
        assert delete.status_code == 200
        assert delete.json() == {"deleted": True, "document_id": document_id}
        after = await client.get(f"/documents/{document_id}")
    assert after.status_code == 404
