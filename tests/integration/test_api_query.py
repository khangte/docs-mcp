"""/query 통합 테스트."""

from __future__ import annotations

import pytest

from app.services.rag.llm_provider import NO_RESULT_MESSAGE


@pytest.mark.asyncio
async def test_query_with_match(async_client, sample_openapi_3: str) -> None:
    async with async_client as client:
        await client.post("/documents", json={"raw_document": sample_openapi_3})
        response = await client.post(
            "/query", json={"question": "find pet by id"}
        )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["answer"], str) and body["answer"]
    assert body["is_grounded"] is True
    assert body["citations"]
    for citation in body["citations"]:
        assert {"endpoint_id", "method", "path", "snippet"}.issubset(citation.keys())


@pytest.mark.asyncio
async def test_query_no_match_fixed_message(async_client) -> None:
    # 문서를 등록하지 않은 상태에서는 검색 결과가 반드시 0건
    async with async_client as client:
        response = await client.post(
            "/query", json={"question": "find anything"}
        )
    assert response.status_code == 200
    body = response.json()
    assert NO_RESULT_MESSAGE in body["answer"]
    assert body["citations"] == []
    assert body["is_grounded"] is False


@pytest.mark.asyncio
async def test_query_empty_question_422(async_client) -> None:
    async with async_client as client:
        response = await client.post("/query", json={"question": " "})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_query_citation_method_path_in_answer(
    async_client, sample_openapi_3: str
) -> None:
    async with async_client as client:
        await client.post("/documents", json={"raw_document": sample_openapi_3})
        response = await client.post(
            "/query", json={"question": "find pet by id"}
        )
    body = response.json()
    first = body["citations"][0]
    assert f"{first['method']} {first['path']}" in body["answer"]
