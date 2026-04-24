"""/endpoints 상세/예시 통합 테스트."""

from __future__ import annotations

import pytest


async def _register_and_pick_get_pet(client, sample_openapi_3: str) -> str:
    await client.post("/documents", json={"raw_document": sample_openapi_3})
    listed = await client.get("/documents")
    document_id = listed.json()[0]["document_id"]
    # 엔드포인트 ID 는 검색 결과로 찾는다 (공개 API)
    search = await client.get(
        "/search", params={"query": "find pet by id", "top_k": 5}
    )
    items = search.json()["items"]
    for item in items:
        if item["method"] == "GET" and item["path"] == "/pet/{petId}":
            return item["endpoint_id"]
    raise AssertionError(
        f"GET /pet/{{petId}} not found in search results for document {document_id}"
    )


@pytest.mark.asyncio
async def test_get_endpoint_detail(async_client, sample_openapi_3: str) -> None:
    async with async_client as client:
        endpoint_id = await _register_and_pick_get_pet(client, sample_openapi_3)
        response = await client.get(f"/endpoints/{endpoint_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "GET"
    assert body["path"] == "/pet/{petId}"
    assert any(p["name"] == "petId" and p["in"] == "path" for p in body["parameters"])
    # GET 엔드포인트는 request_body 가 없다
    assert body["request_body"] is None
    # referenced_schemas 는 실제 사용된 Pet 만 포함
    names = {s["name"] for s in body["referenced_schemas"]}
    assert names == {"Pet"}


@pytest.mark.asyncio
async def test_get_endpoint_example_curl(
    async_client, sample_openapi_3: str
) -> None:
    async with async_client as client:
        endpoint_id = await _register_and_pick_get_pet(client, sample_openapi_3)
        response = await client.get(
            f"/endpoints/{endpoint_id}/example", params={"format": "curl"}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "curl"
    assert "curl " in body["code"]
    assert "/pet/42" in body["code"]


@pytest.mark.asyncio
async def test_get_endpoint_not_found(async_client) -> None:
    async with async_client as client:
        response = await client.get("/endpoints/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["type"] == "endpoint_not_found"


@pytest.mark.asyncio
async def test_example_invalid_format_rejected(
    async_client, sample_openapi_3: str
) -> None:
    async with async_client as client:
        endpoint_id = await _register_and_pick_get_pet(client, sample_openapi_3)
        response = await client.get(
            f"/endpoints/{endpoint_id}/example", params={"format": "ruby"}
        )
    assert response.status_code == 422
