"""태그 목록 조회 서비스 테스트.

SPEC 기능 4 의 검증 기준을 그대로 옮긴다.
"""

from __future__ import annotations

import json

import pytest

from app.composition import build_services
from app.core.errors import DocumentNotFoundError

UNTAGGED_DOC: dict = {
    "openapi": "3.0.3",
    "info": {"title": "Untagged API", "version": "1.0.0"},
    "paths": {
        "/ping": {
            "get": {
                "operationId": "ping",
                "summary": "Ping",
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


def _register(app_state, raw: str) -> str:
    """문서를 등록하고 document_id 를 반환한다."""
    bundle = next(build_services(app_state))
    return bundle.sync_service.register(
        project="default", source_url=None, raw_document=raw
    ).document.id


def _bundle(app_state):
    """새 서비스 번들을 만든다."""
    return next(build_services(app_state))


def test_lists_tags_with_endpoint_counts(app_state, sample_openapi_3: str) -> None:
    """태그별 엔드포인트 수를 집계해 반환한다."""
    document_id = _register(app_state, sample_openapi_3)

    tags = _bundle(app_state).tag_catalog_service.list_tags(document_id=document_id)

    counts = {t.name: t.endpoint_count for t in tags}
    assert counts == {"pet": 3, "user": 1}


def test_tags_sorted_by_count_desc_then_name(app_state, sample_openapi_3: str) -> None:
    """엔드포인트 수 내림차순, 동수면 이름 오름차순으로 정렬된다."""
    document_id = _register(app_state, sample_openapi_3)

    tags = _bundle(app_state).tag_catalog_service.list_tags(document_id=document_id)

    assert [t.name for t in tags] == ["pet", "user"]
    assert [t.endpoint_count for t in tags] == sorted(
        [t.endpoint_count for t in tags], reverse=True
    )


def test_document_id_filter_returns_only_that_document_tags(
    app_state, sample_openapi_3: str
) -> None:
    """document_id 지정 시 해당 문서의 태그만 반환한다."""
    petstore_id = _register(app_state, sample_openapi_3)
    other_id = _register(
        app_state,
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "Billing API", "version": "1.0.0"},
                "paths": {
                    "/invoice": {
                        "get": {
                            "operationId": "getInvoice",
                            "summary": "Get invoice",
                            "tags": ["billing"],
                            "responses": {"200": {"description": "ok"}},
                        }
                    }
                },
            }
        ),
    )

    service = _bundle(app_state).tag_catalog_service
    petstore_tags = {t.name for t in service.list_tags(document_id=petstore_id)}
    billing_tags = {t.name for t in service.list_tags(document_id=other_id)}

    assert petstore_tags == {"pet", "user"}
    assert billing_tags == {"billing"}


def test_without_document_id_aggregates_across_documents(
    app_state, sample_openapi_3: str
) -> None:
    """document_id 를 생략하면 전체 문서의 태그를 합산한다."""
    _register(app_state, sample_openapi_3)
    _register(
        app_state,
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "More Pets", "version": "1.0.0"},
                "paths": {
                    "/pet/search": {
                        "get": {
                            "operationId": "searchPet",
                            "summary": "Search pet",
                            "tags": ["pet"],
                            "responses": {"200": {"description": "ok"}},
                        }
                    }
                },
            }
        ),
    )

    tags = _bundle(app_state).tag_catalog_service.list_tags()

    counts = {t.name: t.endpoint_count for t in tags}
    assert counts["pet"] == 4
    assert counts["user"] == 1


def test_document_without_tags_returns_empty_list(app_state) -> None:
    """태그가 없는 문서는 빈 배열을 반환한다."""
    document_id = _register(app_state, json.dumps(UNTAGGED_DOC))

    tags = _bundle(app_state).tag_catalog_service.list_tags(document_id=document_id)

    assert tags == []


def test_no_documents_returns_empty_list(app_state) -> None:
    """등록된 문서가 하나도 없으면 빈 배열을 반환한다."""
    assert _bundle(app_state).tag_catalog_service.list_tags() == []


def test_unknown_document_id_raises_not_found(app_state) -> None:
    """존재하지 않는 document_id 는 DocumentNotFoundError 를 발생시킨다."""
    with pytest.raises(DocumentNotFoundError) as exc_info:
        _bundle(app_state).tag_catalog_service.list_tags(document_id="no-such-doc")

    assert exc_info.value.code == "document_not_found"


def test_repeated_calls_are_deterministic(app_state, sample_openapi_3: str) -> None:
    """동일 조건 반복 호출 시 동일 순서·동일 결과를 반환한다."""
    document_id = _register(app_state, sample_openapi_3)
    service = _bundle(app_state).tag_catalog_service

    first = service.list_tags(document_id=document_id)
    second = service.list_tags(document_id=document_id)

    assert first == second
