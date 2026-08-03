"""`normalize_project()` / `resolve_document_scope()` 경계값 테스트 (SPEC 기능 7).

`test_project_scope_filter.py`(기능 3, OpenAPI 조회/검색 통합 시나리오)와
겹치지 않도록, 여기서는 두 함수 자체의 순수 로직·경계값만 검증한다.
"""

from __future__ import annotations

import pytest

from app.core.errors import DocumentNotFoundError, ValidationError
from app.models.openapi import PROJECT_MAX_LENGTH
from app.services.documents.project_scope import normalize_project, resolve_document_scope

# --- normalize_project ---------------------------------------------------------


def test_normalize_project_strips_whitespace() -> None:
    """앞뒤 공백은 제거되고 내부 값은 그대로 유지된다."""
    assert normalize_project("  shop-api  ", required=True) == "shop-api"


def test_normalize_project_preserves_case() -> None:
    """대소문자는 보존된다(정규화 대상이 아님)."""
    assert normalize_project("Shop-API", required=True) == "Shop-API"
    assert normalize_project("shop-api", required=True) == "shop-api"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_normalize_project_blank_required_raises_validation_error(value: str) -> None:
    """required=True 일 때 빈 문자열/공백만 있는 값은 ValidationError 다."""
    with pytest.raises(ValidationError):
        normalize_project(value, required=True)


def test_normalize_project_none_required_raises_validation_error() -> None:
    """required=True 일 때 None 도 ValidationError 다."""
    with pytest.raises(ValidationError):
        normalize_project(None, required=True)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_normalize_project_blank_not_required_returns_none(value: str | None) -> None:
    """required=False 이면 빈 값은 예외 없이 None 을 반환한다."""
    assert normalize_project(value, required=False) is None


def test_normalize_project_max_length_boundary_is_accepted() -> None:
    """PROJECT_MAX_LENGTH 길이는 성공한다(경계값)."""
    value = "p" * PROJECT_MAX_LENGTH
    assert normalize_project(value, required=True) == value


def test_normalize_project_over_max_length_raises_validation_error() -> None:
    """PROJECT_MAX_LENGTH + 1 은 ValidationError 다(경계값)."""
    value = "p" * (PROJECT_MAX_LENGTH + 1)
    with pytest.raises(ValidationError):
        normalize_project(value, required=True)


def test_normalize_project_trimmed_length_is_what_counts() -> None:
    """길이 제한은 trim 이후 값 기준이다 — 앞뒤 공백은 길이에 포함되지 않는다."""
    inner = "p" * PROJECT_MAX_LENGTH
    padded = f"  {inner}  "
    assert normalize_project(padded, required=True) == inner


def test_normalize_project_not_required_with_value_still_validates_length() -> None:
    """required=False 라도 값이 주어지면 길이 검증은 그대로 적용된다."""
    with pytest.raises(ValidationError):
        normalize_project("p" * (PROJECT_MAX_LENGTH + 1), required=False)


# --- resolve_document_scope ------------------------------------------------------


class _FakeDocument:
    """document_repo.get() 이 반환하는 최소 페이크(project 속성만 필요)."""

    def __init__(self, project: str) -> None:
        self.project = project


class _FakeDocumentRepo:
    """resolve_document_scope 테스트용 최소 document_repo 페이크."""

    def __init__(self, documents: dict[str, str]) -> None:
        """document_id → project 매핑으로 페이크 저장소를 만든다."""
        self._documents = documents

    def get(self, document_id: str) -> _FakeDocument | None:
        project = self._documents.get(document_id)
        return _FakeDocument(project) if project is not None else None


def test_resolve_scope_both_none_returns_both_none() -> None:
    """document_id/project 둘 다 없으면 (None, None) — 전체 범위(하위 호환)."""
    repo = _FakeDocumentRepo({})
    assert resolve_document_scope(repo, None, None) == (None, None)


def test_resolve_scope_project_only_returns_project_unchanged() -> None:
    """document_id 없이 project 만 있으면 그대로 (None, project) 를 반환한다."""
    repo = _FakeDocumentRepo({})
    assert resolve_document_scope(repo, None, "A") == (None, "A")


def test_resolve_scope_project_only_does_not_touch_repo() -> None:
    """project 만 있는 경로는 document_repo.get() 을 호출하지 않는다(문서 존재 확인 불필요)."""

    class _ExplodingRepo:
        def get(self, document_id: str):
            raise AssertionError("document_id 없이는 repo.get() 이 호출되면 안 된다")

    assert resolve_document_scope(_ExplodingRepo(), None, "A") == (None, "A")


def test_resolve_scope_document_id_only_returns_document_id_and_none_project() -> None:
    """document_id 만 있으면 (document_id, None) — project 필터는 더 이상 불필요."""
    repo = _FakeDocumentRepo({"doc1": "A"})
    assert resolve_document_scope(repo, "doc1", None) == ("doc1", None)


def test_resolve_scope_unknown_document_id_raises_not_found() -> None:
    """등록되지 않은 document_id 는 DocumentNotFoundError 다."""
    repo = _FakeDocumentRepo({})
    with pytest.raises(DocumentNotFoundError):
        resolve_document_scope(repo, "no-such-doc", None)


def test_resolve_scope_document_id_with_matching_project_succeeds() -> None:
    """document_id 와 project 가 일치하면 (document_id, None) 을 반환한다."""
    repo = _FakeDocumentRepo({"doc1": "A"})
    assert resolve_document_scope(repo, "doc1", "A") == ("doc1", None)


def test_resolve_scope_document_id_with_mismatched_project_raises_not_found() -> None:
    """document_id 가 존재해도 그 문서의 project 와 다르면 DocumentNotFoundError 다.

    다른 프로젝트 문서의 존재를 알려주지 않기 위해, "문서 자체가 없음"과
    동일한 예외로 취급한다(SPEC 281행).
    """
    repo = _FakeDocumentRepo({"doc1": "A"})
    with pytest.raises(DocumentNotFoundError):
        resolve_document_scope(repo, "doc1", "B")


def test_resolve_scope_document_id_priority_over_project() -> None:
    """document_id 가 우선이므로, 존재하고 project 도 일치하면 project 는 결과에서 사라진다."""
    repo = _FakeDocumentRepo({"doc1": "A"})
    document_id, project = resolve_document_scope(repo, "doc1", "A")
    assert document_id == "doc1"
    assert project is None
