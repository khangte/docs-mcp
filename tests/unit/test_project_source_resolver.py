"""`ProjectSourceResolver` 단위 테스트 (SPEC 기능 5 검증 기준, 374~380행).

`resolve_for_project`/`resolve_all` 이 project → folder_id/database_id
매핑으로부터 정확한 어댑터를 만드는지, 자격증명 없을 때 조용히 제외하는지,
같은 요청(resolver 인스턴스) 안에서 어댑터가 캐싱되는지 검증한다.
"""

from __future__ import annotations

from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION

# --- resolve_for_project: project 별로 다른 folder_id/database_id 를 쓴다 --------


def test_resolve_for_project_drive_uses_that_projects_folder_id(
    make_project_resolver, fake_drive_source
) -> None:
    """project A/B 의 Drive 어댑터가 각각 매핑된 folder-a/folder-b 를 대상으로 한다."""
    resolver = make_project_resolver(
        drive_mapping={
            "A": ("folder-a", fake_drive_source),
            "B": ("folder-b", fake_drive_source),
        }
    )

    resolver.resolve_for_project("A")
    resolver.resolve_for_project("B")

    assert resolver.drive_builder_calls == ["folder-a", "folder-b"]


def test_resolve_for_project_notion_uses_that_projects_database_id(
    make_project_resolver, fake_notion_source
) -> None:
    """project A/B 의 Notion 어댑터가 각각 매핑된 db-a/db-b 를 대상으로 한다."""
    resolver = make_project_resolver(
        notion_mapping={
            "A": ("db-a", fake_notion_source),
            "B": ("db-b", fake_notion_source),
        }
    )

    resolver.resolve_for_project("A")
    resolver.resolve_for_project("B")

    assert resolver.notion_builder_calls == ["db-a", "db-b"]


# --- 매핑 없는 소스는 키 자체가 없다 ---------------------------------------------


def test_resolve_for_project_without_drive_mapping_has_no_drive_key(
    make_project_resolver, fake_notion_source
) -> None:
    """Drive 매핑이 없는 project 는 반환 매핑에 drive 키가 없다."""
    resolver = make_project_resolver(notion_mapping={"A": ("db-a", fake_notion_source)})

    sources = resolver.resolve_for_project("A")

    assert SOURCE_DRIVE not in sources
    assert SOURCE_NOTION in sources


def test_resolve_for_project_without_notion_mapping_has_no_notion_key(
    make_project_resolver, fake_drive_source
) -> None:
    """Notion 매핑이 없는 project 는 반환 매핑에 notion 키가 없다."""
    resolver = make_project_resolver(drive_mapping={"A": ("folder-a", fake_drive_source)})

    sources = resolver.resolve_for_project("A")

    assert SOURCE_NOTION not in sources
    assert SOURCE_DRIVE in sources


def test_resolve_for_project_with_only_one_side_works_normally(
    make_project_resolver, fake_drive_source
) -> None:
    """한쪽(Drive)만 있는 project 도 정상 동작한다(다른 쪽 부재가 예외를 일으키지 않음)."""
    resolver = make_project_resolver(drive_mapping={"A": ("folder-a", fake_drive_source)})

    sources = resolver.resolve_for_project("A")

    assert sources == {SOURCE_DRIVE: fake_drive_source}


def test_resolve_for_project_no_mapping_returns_empty_dict(make_project_resolver) -> None:
    """매핑이 전혀 없는 project 는 빈 dict 를 반환한다(오류 아님)."""
    resolver = make_project_resolver()

    assert resolver.resolve_for_project("no-such-project") == {}


# --- 캐싱: 같은 folder_id/database_id 는 1회만 빌더 호출 -------------------------


def test_resolve_for_project_caches_drive_builder_call_per_folder_id(
    make_project_resolver, fake_drive_source
) -> None:
    """같은 folder_id 를 공유하는 두 project 를 조회해도 빌더는 1회만 호출된다(캐싱)."""
    resolver = make_project_resolver(
        drive_mapping={
            "A": ("folder-shared", fake_drive_source),
            "B": ("folder-shared", fake_drive_source),
        }
    )

    resolver.resolve_for_project("A")
    resolver.resolve_for_project("B")
    resolver.resolve_for_project("A")

    assert resolver.drive_builder_calls == ["folder-shared"]


def test_resolve_all_caches_notion_builder_call_per_database_id(
    make_project_resolver, fake_notion_source
) -> None:
    """resolve_all() 에서도 같은 database_id 는 빌더가 1회만 호출된다."""
    resolver = make_project_resolver(
        notion_mapping={
            "A": ("db-shared", fake_notion_source),
            "B": ("db-shared", fake_notion_source),
        }
    )

    pairs = resolver.resolve_all()

    assert resolver.notion_builder_calls == ["db-shared"]
    assert len(pairs) == 2  # (A, notion), (B, notion) — 어댑터 인스턴스는 동일


# --- resolve_all: (project, source) 쌍 전체 -------------------------------------


def test_resolve_all_returns_pairs_for_every_registered_project(
    make_project_resolver, fake_drive_source
) -> None:
    """resolve_all() 은 등록된 모든 project 의 (project, source) 쌍을 반환한다."""
    from tests.fixtures.document_sources import FakeDocumentSource

    fake_drive_b = FakeDocumentSource(SOURCE_DRIVE)
    resolver = make_project_resolver(
        drive_mapping={
            "A": ("folder-a", fake_drive_source),
            "B": ("folder-b", fake_drive_b),
        }
    )

    pairs = resolver.resolve_all()

    projects = {project for project, _ in pairs}
    assert projects == {"A", "B"}


def test_resolve_all_excludes_mappings_without_available_adapter(
    make_project_resolver,
) -> None:
    """빌더가 None 을 반환하는(자격증명 없음) 매핑은 resolve_all 결과에서 제외된다."""
    resolver = make_project_resolver(drive_mapping={"A": ("folder-a", None)})

    pairs = resolver.resolve_all()

    assert pairs == []


# --- 자격증명 미구성: 매핑이 있어도 조용히 제외 ----------------------------------


def test_missing_drive_credentials_excludes_drive_even_with_mapping(
    make_project_resolver,
) -> None:
    """서비스 계정 자격증명이 없으면(빌더가 None 반환) 매핑 행이 있어도 drive 어댑터가 없다."""
    resolver = make_project_resolver(drive_mapping={"A": ("folder-a", None)})

    sources = resolver.resolve_for_project("A")

    assert SOURCE_DRIVE not in sources


def test_missing_notion_token_excludes_notion_even_with_mapping(
    make_project_resolver,
) -> None:
    """notion_token 이 없으면(빌더가 None 반환) 매핑 행이 있어도 notion 어댑터가 없다."""
    resolver = make_project_resolver(notion_mapping={"A": ("db-a", None)})

    sources = resolver.resolve_for_project("A")

    assert SOURCE_NOTION not in sources
