"""OpenAPI 조회/검색 도구의 project 필터 테스트.

SPEC 기능 3(277~317행) 검증 기준을 그대로 옮긴다.
"""

from __future__ import annotations

import json

import pytest

from app.composition import build_services
from app.core.errors import DocumentNotFoundError
from app.services.search.endpoint_candidate_search import CandidateSearchOptions

_BILLING_DOC: dict = {
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
    "components": {
        "schemas": {
            "Pet": {
                "type": "object",
                "properties": {"invoiceOnly": {"type": "string"}},
            }
        }
    },
}


def _register(app_state, raw: str, project: str) -> str:
    """문서를 등록하고 document_id 를 반환한다."""
    bundle = next(build_services(app_state))
    return bundle.sync_service.register(
        project=project, source_url=None, raw_document=raw
    ).document.id


def _bundle(app_state):
    """새 서비스 번들을 만든다."""
    return next(build_services(app_state))


def test_list_documents_project_filter_returns_only_that_project(
    app_state, sample_openapi_3: str
) -> None:
    """list_documents(project="A") 는 A 문서만 반환한다."""
    doc_a = _register(app_state, sample_openapi_3, "A")
    _register(app_state, json.dumps(_BILLING_DOC), "B")

    docs = _bundle(app_state).document_repo.list_all(project="A")

    assert {d.id for d in docs} == {doc_a}


def test_list_documents_without_project_returns_all(app_state, sample_openapi_3: str) -> None:
    """project 를 생략하면 A·B 문서가 모두 나온다(하위 호환)."""
    doc_a = _register(app_state, sample_openapi_3, "A")
    doc_b = _register(app_state, json.dumps(_BILLING_DOC), "B")

    docs = _bundle(app_state).document_repo.list_all()

    assert {d.id for d in docs} >= {doc_a, doc_b}


def test_search_endpoints_project_filter_restricts_candidates(
    app_state, sample_openapi_3: str
) -> None:
    """search_endpoints(project="A") 결과는 전부 A 문서 소속이다."""
    _register(app_state, sample_openapi_3, "A")
    _register(app_state, json.dumps(_BILLING_DOC), "B")

    candidates = _bundle(app_state).candidate_search.search(
        "pet", CandidateSearchOptions(top_k=10, project="A")
    )

    assert candidates
    assert all(c.path.startswith("/pet") or c.path.startswith("/user") for c in candidates)


def test_search_endpoints_project_filter_excludes_other_project_paths(
    app_state, sample_openapi_3: str
) -> None:
    """B 문서에만 있는 path 로 검색해도 A 범위에서는 0건이다."""
    _register(app_state, sample_openapi_3, "A")
    _register(app_state, json.dumps(_BILLING_DOC), "B")

    candidates = _bundle(app_state).candidate_search.search(
        "invoice", CandidateSearchOptions(top_k=10, project="A")
    )

    assert candidates == []


def test_search_endpoints_without_project_finds_both(app_state, sample_openapi_3: str) -> None:
    """project 를 생략하면 A·B 문서 모두 후보에 나온다(하위 호환)."""
    _register(app_state, sample_openapi_3, "A")
    _register(app_state, json.dumps(_BILLING_DOC), "B")

    candidates = _bundle(app_state).candidate_search.search(
        "invoice", CandidateSearchOptions(top_k=10)
    )

    assert any(c.path == "/invoice" for c in candidates)


def test_list_tags_project_filter_excludes_other_project_tags(
    app_state, sample_openapi_3: str
) -> None:
    """list_tags(project="A") 는 B 에만 있는 태그를 포함하지 않는다."""
    _register(app_state, sample_openapi_3, "A")
    _register(app_state, json.dumps(_BILLING_DOC), "B")

    tags = {t.name for t in _bundle(app_state).tag_catalog_service.list_tags(project="A")}

    assert "billing" not in tags
    assert tags == {"pet", "user"}


def test_resolve_ref_project_filter_picks_correct_project_schema(
    app_state, sample_openapi_3: str
) -> None:
    """A·B 에 동명 스키마(Pet)가 있을 때 project="A" 는 항상 A 의 스키마를 반환한다."""
    _register(app_state, sample_openapi_3, "A")
    _register(app_state, json.dumps(_BILLING_DOC), "B")

    resolver = _bundle(app_state).schema_ref_resolver
    first = resolver.resolve("#/components/schemas/Pet", project="A")
    second = resolver.resolve("#/components/schemas/Pet", project="A")

    field_names = {f.name for f in first.fields}
    assert "invoiceOnly" not in field_names
    assert first == second  # 반복 호출 시 결정성


def test_document_id_with_mismatched_project_raises_not_found(
    app_state, sample_openapi_3: str
) -> None:
    """document_id(A 문서) + project="B" 조합은 document_not_found 다."""
    doc_a = _register(app_state, sample_openapi_3, "A")
    _register(app_state, json.dumps(_BILLING_DOC), "B")

    resolver = _bundle(app_state).schema_ref_resolver
    with pytest.raises(DocumentNotFoundError):
        resolver.resolve("#/components/schemas/Pet", document_id=doc_a, project="B")


def test_document_id_with_matching_project_succeeds(app_state, sample_openapi_3: str) -> None:
    """document_id(A 문서) + project="A" 조합은 정상 동작한다."""
    doc_a = _register(app_state, sample_openapi_3, "A")

    resolver = _bundle(app_state).schema_ref_resolver
    resolved = resolver.resolve("#/components/schemas/Pet", document_id=doc_a, project="A")

    assert resolved.document_id == doc_a


def test_nonexistent_project_returns_empty_not_error(app_state, sample_openapi_3: str) -> None:
    """존재하지 않는 project 로 검색하면 오류가 아니라 빈 결과다."""
    _register(app_state, sample_openapi_3, "A")

    docs = _bundle(app_state).document_repo.list_all(project="no-such-project")
    tags = _bundle(app_state).tag_catalog_service.list_tags(project="no-such-project")
    candidates = _bundle(app_state).candidate_search.search(
        "pet", CandidateSearchOptions(top_k=10, project="no-such-project")
    )

    assert docs == []
    assert tags == []
    assert candidates == []


def test_vector_fallback_respects_project_filter(app_state, sample_openapi_3: str) -> None:
    """키워드 후보 0건이어도 벡터 보조 단계에서 다른 project 청크가 섞이지 않는다.

    SPEC 317행: 범위 축소가 1단계(키워드)뿐 아니라 폴백 경로(벡터)에도
    적용돼야 한다. project="B" 로 좁힌 상태에서 A 전용 키워드로 검색하면
    키워드 0건 → 벡터 폴백으로 넘어가도 B 청크만 후보 집합에 있으므로
    A 관련 결과가 나올 수 없다.
    """
    _register(app_state, sample_openapi_3, "A")
    _register(app_state, json.dumps(_BILLING_DOC), "B")

    bundle = _bundle(app_state)
    # "invoiceOnly" 는 A 문서(petstore)에는 전혀 등장하지 않는 키워드이므로
    # project="B" 범위에서 키워드 0건 → 벡터 폴백으로 전환되게 만든다.
    candidate_chunks = bundle.chunk_repo.list_endpoint_chunks(project="B")
    assert candidate_chunks, "B 프로젝트에 청크가 있어야 폴백 대상이 있다"

    candidates = bundle.candidate_search.search(
        "zzzzz_nothing_matches_here_xxx", CandidateSearchOptions(top_k=10, project="B")
    )

    # 벡터 보조가 활성화돼 있더라도, 후보 집합 자체가 B 로 제한되므로
    # A 문서의 엔드포인트(/pet, /user 등)는 결코 나올 수 없다.
    assert all(c.path == "/invoice" for c in candidates)
