"""/search 통합 테스트."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_search_basic(async_client, sample_openapi_3: str) -> None:
    async with async_client as client:
        await client.post("/documents", json={"raw_document": sample_openapi_3})
        response = await client.get(
            "/search", params={"query": "find pet by id", "top_k": 5}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "find pet by id"
    assert body["count"] == len(body["items"])
    assert body["count"] >= 1
    top = body["items"][0]
    assert top["method"] == "GET"
    assert top["path"] == "/pet/{petId}"
    # 점수 0~1 범위
    for item in body["items"]:
        assert 0.0 <= item["score"] <= 1.0


@pytest.mark.asyncio
async def test_search_method_filter(async_client, sample_openapi_3: str) -> None:
    async with async_client as client:
        await client.post("/documents", json={"raw_document": sample_openapi_3})
        response = await client.get(
            "/search",
            params={"query": "pet user create id", "top_k": 5, "method": "POST"},
        )
    assert response.status_code == 200
    items = response.json()["items"]
    for item in items:
        assert item["method"] == "POST"


@pytest.mark.asyncio
async def test_search_top_k_limits_results(
    async_client, sample_openapi_3: str
) -> None:
    async with async_client as client:
        await client.post("/documents", json={"raw_document": sample_openapi_3})
        response = await client.get(
            "/search", params={"query": "pet", "top_k": 1}
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) <= 1


@pytest.mark.asyncio
async def test_search_empty_query_is_422(async_client) -> None:
    async with async_client as client:
        response = await client.get("/search", params={"query": " "})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_keyword_mode_only_keyword_scored(
    async_client, sample_openapi_3: str
) -> None:
    async with async_client as client:
        await client.post("/documents", json={"raw_document": sample_openapi_3})
        response = await client.get(
            "/search",
            params={"query": "find pet", "mode": "keyword", "top_k": 5},
        )
    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    for item in items:
        assert item["vector_score"] == 0.0
