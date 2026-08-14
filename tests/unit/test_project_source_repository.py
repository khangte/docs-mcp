"""`ProjectSourceRepository` 단위 테스트 (project_source 병합 후, §4).

`upsert`/`get`/`list_by_type`/`delete` 계약을 drive/notion 양쪽에서
검증한다. PK 는 `(project, source_type, location)` 이지만 서비스 정책상
`(project, source_type)` 단위 upsert 로 취급된다(§0 스코프 결정).
"""

from __future__ import annotations

from app.repositories.project_source_repository import ProjectSourceRepository


def test_upsert_creates_new_row(db_session) -> None:
    """등록되지 않은 (project, source_type) 에 upsert 하면 새 행이 생긴다."""
    repo = ProjectSourceRepository(db_session)

    row = repo.upsert("A", "drive", "folder-a")
    db_session.commit()

    assert row.project == "A"
    assert row.source_type == "drive"
    assert row.location == "folder-a"
    assert row.kind is None


def test_upsert_same_type_replaces_value_not_new_row(db_session) -> None:
    """같은 (project, source_type) 로 다시 upsert 하면 행이 늘지 않고 값만 바뀐다."""
    repo = ProjectSourceRepository(db_session)
    repo.upsert("A", "drive", "folder-a")
    db_session.commit()

    repo.upsert("A", "drive", "folder-a-v2")
    db_session.commit()

    rows = repo.list_by_type("drive")
    assert len(rows) == 1
    assert rows[0].location == "folder-a-v2"


def test_upsert_keeps_created_at_and_bumps_updated_at(db_session) -> None:
    """upsert 로 값을 교체해도 created_at 은 유지되고 updated_at 만 갱신된다."""
    repo = ProjectSourceRepository(db_session)
    repo.upsert("A", "drive", "folder-a")
    db_session.commit()
    original = repo.get("A", "drive")
    created_at = original.created_at
    updated_at_before = original.updated_at

    repo.upsert("A", "drive", "folder-a-v2")
    db_session.commit()
    db_session.expire_all()

    row = repo.get("A", "drive")
    assert row.created_at == created_at
    assert row.updated_at >= updated_at_before


def test_get_returns_none_for_unregistered_project(db_session) -> None:
    """등록되지 않은 (project, source_type) 을 조회하면 None 이다."""
    repo = ProjectSourceRepository(db_session)

    assert repo.get("no-such-project", "drive") is None


def test_list_by_type_returns_project_ascending_order(db_session) -> None:
    """list_by_type 반환 순서가 project 오름차순으로 결정적이다."""
    repo = ProjectSourceRepository(db_session)
    for project in ("C", "A", "B"):
        repo.upsert(project, "drive", f"folder-{project.lower()}")
    db_session.commit()

    projects = [row.project for row in repo.list_by_type("drive")]

    assert projects == ["A", "B", "C"]


def test_list_by_type_excludes_other_source_type(db_session) -> None:
    """list_by_type("drive") 는 notion 행을 포함하지 않는다."""
    repo = ProjectSourceRepository(db_session)
    repo.upsert("A", "drive", "folder-a")
    repo.upsert("A", "notion", "db-a")
    db_session.commit()

    rows = repo.list_by_type("drive")

    assert [r.source_type for r in rows] == ["drive"]


def test_delete_existing_project_returns_true_and_removes_row(db_session) -> None:
    """등록된 (project, source_type) 을 delete 하면 True 를 반환하고 목록에서 사라진다."""
    repo = ProjectSourceRepository(db_session)
    repo.upsert("A", "drive", "folder-a")
    db_session.commit()

    removed = repo.delete("A", "drive")
    db_session.commit()

    assert removed is True
    assert repo.get("A", "drive") is None


def test_delete_unregistered_project_returns_false_idempotent(db_session) -> None:
    """등록되지 않은 (project, source_type) 을 delete 해도 오류가 아니라 False 다(멱등)."""
    repo = ProjectSourceRepository(db_session)

    assert repo.delete("no-such-project", "drive") is False


def test_delete_is_idempotent_across_repeated_calls(db_session) -> None:
    """같은 (project, source_type) 을 두 번 delete 해도 두 번째 호출은 False 이고 오류가 아니다."""
    repo = ProjectSourceRepository(db_session)
    repo.upsert("A", "drive", "folder-a")
    db_session.commit()

    first = repo.delete("A", "drive")
    db_session.commit()
    second = repo.delete("A", "drive")

    assert first is True
    assert second is False


def test_drive_and_notion_registration_are_independent(db_session) -> None:
    """한 프로젝트가 drive 만 등록해도 notion 삭제/조회에 영향을 주지 않는다."""
    repo = ProjectSourceRepository(db_session)

    repo.upsert("drive-only", "drive", "folder-x")
    db_session.commit()

    assert repo.get("drive-only", "drive") is not None
    assert repo.get("drive-only", "notion") is None


def test_upsert_with_kind_sets_kind_column(db_session) -> None:
    """kind 인자를 주면 notion 행의 kind 가 함께 저장된다."""
    repo = ProjectSourceRepository(db_session)

    row = repo.upsert("A", "notion", "page-a", kind="page")
    db_session.commit()

    assert row.location == "page-a"
    assert row.kind == "page"


def test_upsert_kind_switch_replaces_kind_on_same_project(db_session) -> None:
    """같은 (project, 'notion') 에 kind 를 바꿔 다시 upsert 하면 kind 가 교체된다."""
    repo = ProjectSourceRepository(db_session)
    repo.upsert("A", "notion", "page-a", kind="page")
    db_session.commit()

    row = repo.upsert("A", "notion", "db-a", kind="database")
    db_session.commit()

    assert len(repo.list_by_type("notion")) == 1
    assert row.kind == "database"
    assert row.location == "db-a"
