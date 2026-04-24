"""/sync 통합 테스트."""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_sync_same_hash_skipped(
    async_client, sample_openapi_3: str, in_memory_fetcher
) -> None:
    url = "https://example.com/api.json"
    in_memory_fetcher.put(url, sample_openapi_3)

    async with async_client as client:
        register = await client.post("/documents", json={"source_url": url})
        document_id = register.json()["document_id"]

        # 동일 해시로 재동기화 (fetcher 가 같은 내용을 돌려줌)
        response = await client.post(f"/sync/{document_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "skipped"
    assert body["previous_hash"] == body["new_hash"]


@pytest.mark.asyncio
async def test_sync_content_change_reindexes(
    async_client, sample_openapi_3: str, in_memory_fetcher
) -> None:
    url = "https://example.com/api.json"
    in_memory_fetcher.put(url, sample_openapi_3)

    async with async_client as client:
        register = await client.post("/documents", json={"source_url": url})
        document_id = register.json()["document_id"]

        # fetcher 내용을 변경
        modified = json.loads(sample_openapi_3)
        modified["info"]["title"] = "Petstore API V2"
        modified["info"]["version"] = "2.0.0"
        in_memory_fetcher.put(url, json.dumps(modified))

        response = await client.post(f"/sync/{document_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "reindexed"
    assert body["previous_hash"] != body["new_hash"]


@pytest.mark.asyncio
async def test_sync_force_reindexes_even_without_change(
    async_client, sample_openapi_3: str
) -> None:
    async with async_client as client:
        register = await client.post(
            "/documents", json={"raw_document": sample_openapi_3}
        )
        document_id = register.json()["document_id"]
        response = await client.post(
            f"/sync/{document_id}",
            json={"force": True, "raw_override": sample_openapi_3},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "reindexed"


@pytest.mark.asyncio
async def test_sync_document_not_found(async_client) -> None:
    async with async_client as client:
        response = await client.post("/sync/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["type"] == "document_not_found"
