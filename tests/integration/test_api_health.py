"""/health, /ready 통합 테스트."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(async_client) -> None:
    async with async_client as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_returns_ok_when_empty(async_client) -> None:
    async with async_client as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["vector_index"] is True
    assert body["documents"] == 0


@pytest.mark.asyncio
async def test_ready_reports_document_count(
    async_client, sample_openapi_3: str
) -> None:
    async with async_client as client:
        register = await client.post(
            "/documents",
            json={"raw_document": sample_openapi_3},
        )
        assert register.status_code == 200
        ready = await client.get("/ready")
    body = ready.json()
    assert body["documents"] == 1
    assert body["status"] == "ok"
