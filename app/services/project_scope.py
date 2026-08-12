"""project 값 정규화·검증. MCP 도구·서비스·저장소가 공유하는 단일 규칙."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, overload

from app.core.errors import DocumentNotFoundError, ValidationError
from app.models import PROJECT_MAX_LENGTH

if TYPE_CHECKING:
    from app.repositories.document_repository import DocumentRepository


def resolve_document_scope(
    document_repo: "DocumentRepository", document_id: str | None, project: str | None
) -> tuple[str | None, str | None]:
    """document_id/project 조합을 검증하고 실제 필터링에 쓸 범위를 확정한다.

    규칙(SPEC 기능 3 280~283행):
    1. document_id 가 있으면 문서 존재를 검증한다. project 도 있고 그 문서의
       project 와 다르면 다른 프로젝트 문서의 존재를 알려주지 않기 위해
       DocumentNotFoundError 로 취급한다. document_id 가 우선이므로 project
       필터는 더 이상 필요 없다 — `(document_id, None)` 을 반환한다.
    2. document_id 없이 project 만 있으면 `(None, project)` 를 그대로
       반환한다(문서 0건이어도 오류 아님 — "미등록 project" 개념이 없다).
    3. 둘 다 없으면 `(None, None)`(전체 범위, 하위 호환).

    Returns:
        저장소의 `list_all(document_id=..., project=...)` 류 메서드에 그대로
        전달할 `(document_id, project)` 쌍.

    Raises:
        DocumentNotFoundError: document_id 가 미등록이거나 project 와 불일치.
    """
    if document_id is not None:
        document = document_repo.get(document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)
        if project is not None and document.project != project:
            raise DocumentNotFoundError(document_id)
        return document_id, None
    return None, project


@overload
def normalize_project(value: str | None, *, required: Literal[True]) -> str: ...
@overload
def normalize_project(value: str | None, *, required: Literal[False]) -> str | None: ...
def normalize_project(value: str | None, *, required: bool) -> str | None:
    """project 값을 trim 후 검증한다. 대소문자는 보존한다.

    빈 문자열이거나 `PROJECT_MAX_LENGTH` 를 초과하면 `ValidationError` 를 낸다.
    `required=False` 이고 값이 없으면 `None` 을 그대로 반환한다.
    """
    if value is None or value.strip() == "":
        if required:
            raise ValidationError("project is required")
        return None

    normalized = value.strip()
    if len(normalized) > PROJECT_MAX_LENGTH:
        raise ValidationError(
            f"project must be at most {PROJECT_MAX_LENGTH} characters"
        )
    return normalized
