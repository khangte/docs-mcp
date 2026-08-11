"""EndpointRepository 단위 테스트."""

from __future__ import annotations

from app.models.openapi import ApiDocument, ApiEndpoint
from app.repositories.endpoint_repository import EndpointRepository


def _seed_endpoint(session, endpoint_id: str, document_id: str = "doc-1") -> None:
    """엔드포인트 한 건을 저장한다(문서가 없으면 함께 만든다)."""
    if session.get(ApiDocument, document_id) is None:
        session.add(
            ApiDocument(
                id=document_id,
                project="default",
                source_url=None,
                title="샘플 문서",
                content_hash="hash",
                raw_text="{}",
            )
        )
        session.flush()
    session.add(
        ApiEndpoint(
            id=endpoint_id,
            document_id=document_id,
            method="GET",
            path=f"/{endpoint_id}",
            summary=f"{endpoint_id} 조회",
        )
    )


# --- Q3: get_many(ids) 배치 조회 ---------------------------------------------


def test_get_many_returns_mapping_for_existing_ids(db_session) -> None:
    """존재하는 ID 들을 id → ApiEndpoint 매핑으로 반환한다."""
    _seed_endpoint(db_session, "ep-1")
    _seed_endpoint(db_session, "ep-2")
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.get_many(["ep-1", "ep-2"])

    assert set(result.keys()) == {"ep-1", "ep-2"}
    assert result["ep-1"].path == "/ep-1"
    assert result["ep-2"].path == "/ep-2"


def test_get_many_omits_missing_ids(db_session) -> None:
    """존재하지 않는 ID 는 반환 매핑에서 빠진다(예외 없음)."""
    _seed_endpoint(db_session, "ep-1")
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.get_many(["ep-1", "missing-id"])

    assert set(result.keys()) == {"ep-1"}


def test_get_many_empty_input_returns_empty_mapping(db_session) -> None:
    """빈 ID 목록을 넘기면 쿼리 없이 빈 매핑을 반환한다."""
    repo = EndpointRepository(db_session)

    assert repo.get_many([]) == {}
