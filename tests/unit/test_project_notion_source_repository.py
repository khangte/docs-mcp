"""`ProjectNotionSourceRepository` 단위 테스트 (SPEC 기능 4 검증 기준, 339~348행).

`upsert`/`get`/`list_all`/`delete` 공통 계약을 Notion 쪽에서 직접 검증한다.
Drive 판(`test_project_drive_source_repository.py`)과 대칭 구조를 유지한다.
"""

from __future__ import annotations

from app.repositories.project_source_repository import ProjectNotionSourceRepository


def test_upsert_creates_new_row(db_session) -> None:
    """등록되지 않은 project 에 upsert 하면 새 행이 생긴다."""
    repo = ProjectNotionSourceRepository(db_session)

    row = repo.upsert("A", "db-a")
    db_session.commit()

    assert row.project == "A"
    assert row.database_id == "db-a"


def test_upsert_same_project_replaces_value_not_new_row(db_session) -> None:
    """같은 project 로 다시 upsert 하면 행이 늘지 않고 값만 바뀐다."""
    repo = ProjectNotionSourceRepository(db_session)
    repo.upsert("A", "db-a")
    db_session.commit()

    repo.upsert("A", "db-a-v2")
    db_session.commit()

    rows = repo.list_all()
    assert len(rows) == 1
    assert rows[0].database_id == "db-a-v2"


def test_upsert_keeps_created_at_and_bumps_updated_at(db_session) -> None:
    """upsert 로 값을 교체해도 created_at 은 유지되고 updated_at 만 갱신된다."""
    repo = ProjectNotionSourceRepository(db_session)
    repo.upsert("A", "db-a")
    db_session.commit()
    original = repo.get("A")
    created_at = original.created_at
    updated_at_before = original.updated_at

    repo.upsert("A", "db-a-v2")
    db_session.commit()
    db_session.expire_all()

    row = repo.get("A")
    assert row.created_at == created_at
    assert row.updated_at >= updated_at_before


def test_get_returns_none_for_unregistered_project(db_session) -> None:
    """등록되지 않은 project 를 조회하면 None 이다."""
    repo = ProjectNotionSourceRepository(db_session)

    assert repo.get("no-such-project") is None


def test_list_all_returns_project_ascending_order(db_session) -> None:
    """list_all 반환 순서가 project 오름차순으로 결정적이다."""
    repo = ProjectNotionSourceRepository(db_session)
    for project in ("C", "A", "B"):
        repo.upsert(project, f"db-{project.lower()}")
    db_session.commit()

    projects = [row.project for row in repo.list_all()]

    assert projects == ["A", "B", "C"]


def test_delete_existing_project_returns_true_and_removes_row(db_session) -> None:
    """등록된 project 를 delete 하면 True 를 반환하고 목록에서 사라진다."""
    repo = ProjectNotionSourceRepository(db_session)
    repo.upsert("A", "db-a")
    db_session.commit()

    removed = repo.delete("A")
    db_session.commit()

    assert removed is True
    assert repo.get("A") is None


def test_delete_unregistered_project_returns_false_idempotent(db_session) -> None:
    """등록되지 않은 project 를 delete 해도 오류가 아니라 False 다(멱등)."""
    repo = ProjectNotionSourceRepository(db_session)

    assert repo.delete("no-such-project") is False


def test_delete_is_idempotent_across_repeated_calls(db_session) -> None:
    """같은 project 를 두 번 delete 해도 두 번째 호출은 False 이고 오류가 아니다."""
    repo = ProjectNotionSourceRepository(db_session)
    repo.upsert("A", "db-a")
    db_session.commit()

    first = repo.delete("A")
    db_session.commit()
    second = repo.delete("A")

    assert first is True
    assert second is False


def test_notion_and_drive_registration_are_independent(db_session) -> None:
    """한 프로젝트가 Notion 만 등록해도 정상 동작한다(Drive 저장소와 서로 독립)."""
    repo = ProjectNotionSourceRepository(db_session)

    repo.upsert("notion-only", "db-x")
    db_session.commit()

    assert repo.get("notion-only") is not None
