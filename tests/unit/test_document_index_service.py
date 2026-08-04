"""DocumentIndexService 단위 테스트 (SPEC 기능 6).

메타 캐시 갱신의 added/updated/removed 집계와 부분 실패 허용, 그리고
프로젝트 확장(다중 소스 순회·교차 프로젝트 삭제 방지)을 검증한다. 본문을
가져오지 않는다는 것도 fetch 호출 카운트로 단언한다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.errors import IntegrationError
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION
from app.models.openapi import DEFAULT_PROJECT
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.services.documents.document_index_service import BATCH_SIZE, DocumentIndexService

_T1 = datetime(2026, 7, 1, 9, 0, 0)
_T2 = datetime(2026, 7, 2, 9, 0, 0)

_PROJECT_A = "A"
_PROJECT_B = "B"


@pytest.fixture()
def meta_repo(db_session) -> DocumentMetaRepository:
    """테스트 세션에 붙은 메타 저장소."""
    return DocumentMetaRepository(db_session)


@pytest.fixture()
def default_resolver(make_project_resolver, fake_drive_source, fake_notion_source):
    """DEFAULT_PROJECT 하나에 Drive/Notion 페이크가 매핑된 resolver."""
    return make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", fake_drive_source)},
        notion_mapping={DEFAULT_PROJECT: ("db-default", fake_notion_source)},
    )


@pytest.fixture()
def index_service(db_session, meta_repo, default_resolver):
    """DEFAULT_PROJECT 에 Drive/Notion 페이크 두 개가 매핑된 갱신 서비스."""
    return DocumentIndexService(session=db_session, meta_repo=meta_repo, resolver=default_resolver)


# --- added / updated / removed 집계 -------------------------------------------


def test_new_files_are_counted_as_added(index_service, meta_repo, fake_drive_source) -> None:
    """신규 파일은 added 로 집계되고 메타 행이 생긴다."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    fake_drive_source.put("d2", "회의록", "본문", modified_at=_T1)

    result = index_service.refresh()

    assert (result.added, result.updated, result.removed, result.synced) == (2, 0, 0, 2)
    assert {m.external_id for m in meta_repo.list_by_source(SOURCE_DRIVE)} == {"d1", "d2"}


def test_unchanged_modified_at_is_not_counted_as_updated(
    index_service, fake_drive_source
) -> None:
    """modified_at 이 이전과 같으면 updated 에 포함되지 않는다."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    index_service.refresh()

    result = index_service.refresh()

    assert result.updated == 0
    assert result.added == 0
    assert result.synced == 1


def test_changed_modified_at_is_counted_as_updated(
    index_service, meta_repo, fake_drive_source
) -> None:
    """modified_at 이 바뀌면 updated 로 집계되고 값이 반영된다."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    index_service.refresh()

    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T2)
    result = index_service.refresh()

    assert result.updated == 1
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1").modified_at == _T2


def test_renamed_title_is_counted_as_updated(
    index_service, meta_repo, fake_drive_source
) -> None:
    """수정 시각이 같아도 제목이 바뀌면 updated 로 집계된다."""
    fake_drive_source.put("d1", "구 제목", "본문", modified_at=_T1)
    index_service.refresh()

    fake_drive_source.put("d1", "새 제목", "본문", modified_at=_T1)
    result = index_service.refresh()

    assert result.updated == 1
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1").title == "새 제목"


def test_deleted_file_is_removed_from_cache(
    index_service, meta_repo, fake_drive_source
) -> None:
    """원본에서 삭제된 파일은 캐시에서 제거되고 removed 로 집계된다."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    fake_drive_source.put("d2", "회의록", "본문", modified_at=_T1)
    index_service.refresh()

    fake_drive_source.remove("d2")
    result = index_service.refresh()

    assert result.removed == 1
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d2") is None
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1") is not None


def test_last_synced_at_is_refreshed_even_when_unchanged(
    index_service, meta_repo, fake_drive_source
) -> None:
    """변경이 없어도 last_synced_at 은 갱신된다(마지막 확인 시각 추적)."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    index_service.refresh()
    first_synced = meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1").last_synced_at

    index_service.refresh()

    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1").last_synced_at >= first_synced


def test_refresh_does_not_fetch_document_bodies(index_service, fake_drive_source) -> None:
    """갱신은 목록/메타만 가져오고 본문 fetch 는 하지 않는다."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    fake_drive_source.reset_counts()

    index_service.refresh()

    assert fake_drive_source.list_call_count == 1
    assert fake_drive_source.fetch_call_count == 0


def test_duplicate_external_ids_are_deduplicated(index_service, fake_drive_source) -> None:
    """소스가 같은 external_id 를 중복해서 돌려줘도 행은 하나만 생긴다."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    fake_drive_source.files.append(fake_drive_source.files[0])

    result = index_service.refresh()

    assert result.added == 1


# --- source 필터 ---------------------------------------------------------------


def test_source_filter_refreshes_only_that_source(
    index_service, meta_repo, fake_drive_source, fake_notion_source
) -> None:
    """source 를 지정하면 해당 소스만 갱신한다."""
    fake_drive_source.put("d1", "드라이브 문서", "본문", modified_at=_T1)
    fake_notion_source.put("n1", "노션 문서", "본문", modified_at=_T1)

    result = index_service.refresh(source=SOURCE_DRIVE)

    assert result.added == 1
    assert fake_notion_source.list_call_count == 0
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_NOTION, "n1") is None


def test_unknown_source_filter_raises_integration_error(index_service) -> None:
    """구성되지 않은 소스 이름을 지정하면 IntegrationError 다."""
    with pytest.raises(IntegrationError):
        index_service.refresh(source="dropbox")


def test_no_configured_source_raises_integration_error(
    db_session, meta_repo, make_project_resolver
) -> None:
    """소스가 하나도 구성돼 있지 않으면 IntegrationError 로 미구성을 알린다."""
    resolver = make_project_resolver()
    service = DocumentIndexService(session=db_session, meta_repo=meta_repo, resolver=resolver)

    with pytest.raises(IntegrationError):
        service.refresh()


# --- 부분 실패 허용 -------------------------------------------------------------


def test_source_level_isolation_when_list_files_fails(
    index_service, meta_repo, fake_drive_source, fake_notion_source
) -> None:
    """소스 간 격리: 한 소스의 `list_files()` 가 실패해도 다른 소스 결과는 남는다.

    이 시나리오는 실패한 소스가 아직 아무 행도 건드리지 않은 상태이므로
    "소스 간 격리"만 검증한다. 한 소스 **내부** 중간 실패는 아래
    `test_mid_save_failure_*` 가 담당한다.
    """
    fake_drive_source.put("d1", "드라이브 문서", "본문", modified_at=_T1)
    fake_notion_source.put("n1", "노션 문서", "본문", modified_at=_T1)
    fake_notion_source.list_should_fail = True

    result = index_service.refresh()

    assert result.added == 1
    assert result.failed_sources == (f"{DEFAULT_PROJECT}/{SOURCE_NOTION}",)
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1") is not None


# --- 소스 내부 중간 실패 (SPEC 기능 6: "이미 처리된 행은 커밋") ------------------


def _seed_many(source, count: int, prefix: str = "d") -> None:
    """페이크 소스에 문서 여러 건을 채운다."""
    for index in range(count):
        source.put(f"{prefix}{index}", f"문서 {index}", "본문", modified_at=_T1)


def test_mid_save_failure_keeps_already_committed_rows(
    db_session, index_service, meta_repo, fake_drive_source
) -> None:
    """저장 도중 실패해도 이미 커밋된 배치의 행은 DB 에 실제로 남는다.

    `list_files()` 는 성공하고 저장 루프 도중에 끊기는 시나리오다. 커밋
    경계가 소스 단위라면 전량 롤백돼 0건이 되므로, 이 테스트가 회귀를 막는다.
    """
    _seed_many(fake_drive_source, BATCH_SIZE * 2 + 5)
    fake_drive_source.fail_listing_at_index = BATCH_SIZE * 2 + 1

    index_service.refresh()

    db_session.expire_all()
    committed = meta_repo.list_by_source(SOURCE_DRIVE)
    assert len(committed) == BATCH_SIZE * 2


def test_mid_save_failure_with_single_source_still_commits(
    db_session, meta_repo, fake_drive_source, make_project_resolver
) -> None:
    """소스가 1개뿐이어도 중간 실패 시 이미 처리된 행이 남는다.

    소스별 커밋 경계만으로는 커버되지 않던 사각지대(Drive 만 설정한 팀)다.
    """
    resolver = make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", fake_drive_source)}
    )
    service = DocumentIndexService(session=db_session, meta_repo=meta_repo, resolver=resolver)
    _seed_many(fake_drive_source, BATCH_SIZE + 3)
    fake_drive_source.fail_listing_at_index = BATCH_SIZE + 1

    result = service.refresh()

    db_session.expire_all()
    assert len(meta_repo.list_by_source(SOURCE_DRIVE)) == BATCH_SIZE
    assert result.failed_sources == (f"{DEFAULT_PROJECT}/{SOURCE_DRIVE}",)


def test_mid_save_failure_counts_match_committed_rows(
    db_session, meta_repo, fake_drive_source, make_project_resolver
) -> None:
    """반환 집계가 롤백분을 세지 않고 실제 커밋된 행 수와 일치한다."""
    resolver = make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", fake_drive_source)}
    )
    service = DocumentIndexService(session=db_session, meta_repo=meta_repo, resolver=resolver)
    _seed_many(fake_drive_source, BATCH_SIZE * 2)
    fake_drive_source.fail_listing_at_index = BATCH_SIZE + 7

    result = service.refresh()

    db_session.expire_all()
    assert result.added == len(meta_repo.list_by_source(SOURCE_DRIVE))
    assert result.added == BATCH_SIZE


def test_mid_save_failure_is_reported_not_silently_succeeded(
    db_session, meta_repo, fake_drive_source, make_project_resolver
) -> None:
    """부분 실패를 조용히 성공으로 위장하지 않고 failed_sources 로 알린다."""
    resolver = make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", fake_drive_source)}
    )
    service = DocumentIndexService(session=db_session, meta_repo=meta_repo, resolver=resolver)
    _seed_many(fake_drive_source, BATCH_SIZE + 2)
    fake_drive_source.fail_listing_at_index = BATCH_SIZE + 1

    result = service.refresh()

    assert result.failed_sources == (f"{DEFAULT_PROJECT}/{SOURCE_DRIVE}",)


def test_failed_items_are_retried_on_next_refresh(
    db_session, meta_repo, fake_drive_source, make_project_resolver
) -> None:
    """실패 지점 이후 항목은 다음 갱신에서 재시도돼 최종적으로 모두 반영된다."""
    resolver = make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", fake_drive_source)}
    )
    service = DocumentIndexService(session=db_session, meta_repo=meta_repo, resolver=resolver)
    total = BATCH_SIZE + 4
    _seed_many(fake_drive_source, total)
    fake_drive_source.fail_listing_at_index = BATCH_SIZE + 1
    service.refresh()

    fake_drive_source.fail_listing_at_index = None
    result = service.refresh()

    db_session.expire_all()
    assert len(meta_repo.list_by_source(SOURCE_DRIVE)) == total
    assert result.failed_sources == ()


def test_mid_save_failure_before_first_batch_commits_nothing(
    db_session, meta_repo, fake_drive_source, make_project_resolver
) -> None:
    """첫 배치를 채우기 전에 실패하면 커밋된 행이 없고 예외로 알린다.

    커밋할 게 아무것도 없는데 성공으로 위장하면 안 된다는 경계 조건이다.
    """
    resolver = make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", fake_drive_source)}
    )
    service = DocumentIndexService(session=db_session, meta_repo=meta_repo, resolver=resolver)
    _seed_many(fake_drive_source, 5)
    fake_drive_source.fail_listing_at_index = 2

    with pytest.raises(IntegrationError):
        service.refresh()

    db_session.expire_all()
    assert list(meta_repo.list_by_source(SOURCE_DRIVE)) == []


# --- 교차 소스 삭제 회귀 방지 (같은 project 내부) --------------------------------


def test_refreshing_one_source_never_deletes_another_sources_rows(
    db_session, index_service, meta_repo, fake_drive_source, fake_notion_source
) -> None:
    """이미 캐시된 다른 source 의 행이 한 source 갱신 때문에 삭제되지 않는다.

    `_refresh_source` 가 `list_by_source()` 대신 `list_all()` 을 쓰면 다른
    출처의 행이 "원본에서 사라진 것"으로 오인돼 통째로 삭제된다. 데이터 유실로
    직결되는 지점이라 회귀 테스트로 고정한다.
    """
    fake_drive_source.put("d1", "드라이브 문서", "본문", modified_at=_T1)
    fake_notion_source.put("n1", "노션 문서", "본문", modified_at=_T1)
    index_service.refresh()

    # drive 에서 파일이 삭제된 뒤 drive 만 갱신한다.
    fake_drive_source.remove("d1")
    result = index_service.refresh(source=SOURCE_DRIVE)

    db_session.expire_all()
    assert result.removed == 1
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1") is None
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_NOTION, "n1") is not None


def test_failed_source_is_retryable_on_next_refresh(
    index_service, meta_repo, fake_notion_source, fake_drive_source
) -> None:
    """실패한 소스는 다음 갱신에서 정상적으로 재시도된다."""
    fake_drive_source.put("d1", "드라이브 문서", "본문", modified_at=_T1)
    fake_notion_source.put("n1", "노션 문서", "본문", modified_at=_T1)
    fake_notion_source.list_should_fail = True
    index_service.refresh()

    fake_notion_source.list_should_fail = False
    result = index_service.refresh()

    assert result.failed_sources == ()
    assert result.added == 1
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_NOTION, "n1") is not None


def test_all_sources_failing_raises_integration_error(
    index_service, fake_drive_source, fake_notion_source
) -> None:
    """모든 소스가 실패하면 조용히 성공하지 않고 IntegrationError 를 던진다."""
    fake_drive_source.list_should_fail = True
    fake_notion_source.list_should_fail = True

    with pytest.raises(IntegrationError):
        index_service.refresh()


def test_failure_does_not_wipe_previously_cached_rows(
    index_service, meta_repo, fake_drive_source, fake_notion_source
) -> None:
    """갱신 실패가 기존 캐시 행을 지워버리지 않는다."""
    fake_drive_source.put("d1", "드라이브 문서", "본문", modified_at=_T1)
    fake_notion_source.put("n1", "노션 문서", "본문", modified_at=_T1)
    index_service.refresh()

    fake_drive_source.list_should_fail = True
    index_service.refresh()

    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1") is not None
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_NOTION, "n1") is not None


# --- 두 소스 동시 갱신 ----------------------------------------------------------


def test_both_sources_are_aggregated(index_service, fake_drive_source, fake_notion_source) -> None:
    """두 소스의 집계가 합산되고 출처별로 행이 분리 저장된다."""
    fake_drive_source.put("shared-id", "드라이브 문서", "본문", modified_at=_T1)
    fake_notion_source.put("shared-id", "노션 문서", "본문", modified_at=_T1)

    result = index_service.refresh()

    assert result.added == 2
    assert result.synced == 2


def test_empty_source_yields_zero_counts(index_service) -> None:
    """소스에 문서가 하나도 없으면 모든 집계가 0 이다."""
    result = index_service.refresh()

    assert (result.synced, result.added, result.updated, result.removed) == (0, 0, 0, 0)


# --- SPEC 기능 6: 다중 프로젝트 순회와 교차 프로젝트 삭제 방지 -------------------


@pytest.fixture()
def fake_drive_source_b():
    """프로젝트 B 전용 Drive 페이크(A 의 fake_drive_source 와 별개 인스턴스)."""
    from tests.fixtures.document_sources import FakeDocumentSource

    return FakeDocumentSource(SOURCE_DRIVE)


@pytest.fixture()
def fake_notion_source_b():
    """프로젝트 B 전용 Notion 페이크."""
    from tests.fixtures.document_sources import FakeDocumentSource

    return FakeDocumentSource(SOURCE_NOTION)


@pytest.fixture()
def two_project_resolver(
    make_project_resolver,
    fake_drive_source,
    fake_notion_source,
    fake_drive_source_b,
    fake_notion_source_b,
):
    """A(folder-a, db-a)/B(folder-b, db-b) 두 프로젝트가 매핑된 resolver."""
    return make_project_resolver(
        drive_mapping={
            _PROJECT_A: ("folder-a", fake_drive_source),
            _PROJECT_B: ("folder-b", fake_drive_source_b),
        },
        notion_mapping={
            _PROJECT_A: ("db-a", fake_notion_source),
            _PROJECT_B: ("db-b", fake_notion_source_b),
        },
    )


@pytest.fixture()
def two_project_index_service(db_session, meta_repo, two_project_resolver):
    """두 프로젝트가 매핑된 DocumentIndexService."""
    return DocumentIndexService(
        session=db_session, meta_repo=meta_repo, resolver=two_project_resolver
    )


def test_refresh_all_projects_tags_documents_with_correct_project(
    two_project_index_service, meta_repo, fake_drive_source, fake_drive_source_b,
    fake_notion_source, fake_notion_source_b,
) -> None:
    """refresh_index() 는 A 문서를 project="A", B 문서를 project="B" 로 저장한다."""
    fake_drive_source.put("d1", "A 드라이브 문서", "본문", modified_at=_T1)
    fake_drive_source_b.put("d1", "B 드라이브 문서", "본문", modified_at=_T1)
    fake_notion_source.put("n1", "A 노션 문서", "본문", modified_at=_T1)
    fake_notion_source_b.put("n1", "B 노션 문서", "본문", modified_at=_T1)

    result = two_project_index_service.refresh()

    assert result.added == 4
    assert meta_repo.find(_PROJECT_A, SOURCE_DRIVE, "d1").title == "A 드라이브 문서"
    assert meta_repo.find(_PROJECT_B, SOURCE_DRIVE, "d1").title == "B 드라이브 문서"
    assert meta_repo.find(_PROJECT_A, SOURCE_NOTION, "n1").title == "A 노션 문서"
    assert meta_repo.find(_PROJECT_B, SOURCE_NOTION, "n1").title == "B 노션 문서"


def test_refresh_with_project_filter_does_not_call_other_projects_list_files(
    two_project_index_service, fake_drive_source_b, fake_notion_source_b
) -> None:
    """refresh_index(project="A") 는 B 의 어댑터 list_files() 를 호출하지 않는다."""
    two_project_index_service.refresh(project=_PROJECT_A)

    assert fake_drive_source_b.list_call_count == 0
    assert fake_notion_source_b.list_call_count == 0


def test_same_external_id_shared_across_projects_creates_two_rows(
    two_project_index_service, meta_repo, fake_drive_source, fake_drive_source_b
) -> None:
    """같은 external_id 가 A·B 두 소스에 공유되면 document_meta 에 2행이 생긴다."""
    fake_drive_source.put("shared", "A 문서", "본문", modified_at=_T1)
    fake_drive_source_b.put("shared", "B 문서", "본문", modified_at=_T1)

    two_project_index_service.refresh()

    assert meta_repo.find(_PROJECT_A, SOURCE_DRIVE, "shared") is not None
    assert meta_repo.find(_PROJECT_B, SOURCE_DRIVE, "shared") is not None


def test_deleting_in_project_a_does_not_remove_project_b_row(
    two_project_index_service, meta_repo, fake_drive_source, fake_drive_source_b
) -> None:
    """A 소스에서 문서가 삭제되면 A 행만 removed 되고 B 의 같은 external_id 행은 남는다.

    SPEC 기능 6 의 핵심 회귀 지점(교차 프로젝트 삭제 방지). 삭제 감지가
    project 로 좁혀지지 않으면 B 행까지 "A 원본에서 사라진 것"으로 오인돼
    함께 삭제된다.
    """
    fake_drive_source.put("shared", "A 문서", "본문", modified_at=_T1)
    fake_drive_source_b.put("shared", "B 문서", "본문", modified_at=_T1)
    two_project_index_service.refresh()

    fake_drive_source.remove("shared")
    result = two_project_index_service.refresh(project=_PROJECT_A)

    assert result.removed == 1
    assert meta_repo.find(_PROJECT_A, SOURCE_DRIVE, "shared") is None
    assert meta_repo.find(_PROJECT_B, SOURCE_DRIVE, "shared") is not None


def test_deleting_notion_in_project_a_does_not_remove_project_b_row(
    two_project_index_service, meta_repo, fake_notion_source, fake_notion_source_b
) -> None:
    """Notion 도 동일하게 교차 프로젝트 삭제가 방지된다."""
    fake_notion_source.put("shared", "A 문서", "본문", modified_at=_T1)
    fake_notion_source_b.put("shared", "B 문서", "본문", modified_at=_T1)
    two_project_index_service.refresh()

    fake_notion_source.remove("shared")
    result = two_project_index_service.refresh(project=_PROJECT_A)

    assert result.removed == 1
    assert meta_repo.find(_PROJECT_A, SOURCE_NOTION, "shared") is None
    assert meta_repo.find(_PROJECT_B, SOURCE_NOTION, "shared") is not None


def test_project_a_drive_fails_b_succeeds_reports_failed_sources_as_project_source(
    two_project_index_service, meta_repo, fake_drive_source, fake_drive_source_b
) -> None:
    """A 의 Drive 가 실패하고 B 는 성공하면 failed_sources==["A/drive"] 이고 B 변경분은 커밋된다."""
    fake_drive_source.list_should_fail = True
    fake_drive_source_b.put("d1", "B 문서", "본문", modified_at=_T1)

    result = two_project_index_service.refresh(source=SOURCE_DRIVE)

    assert result.failed_sources == (f"{_PROJECT_A}/{SOURCE_DRIVE}",)
    assert meta_repo.find(_PROJECT_B, SOURCE_DRIVE, "d1") is not None


def test_project_a_notion_fails_b_succeeds_reports_failed_sources_as_project_source(
    two_project_index_service, meta_repo, fake_notion_source, fake_notion_source_b
) -> None:
    """Notion 실패도 "<project>/notion" 형식으로 동일하게 보고된다."""
    fake_notion_source.list_should_fail = True
    fake_notion_source_b.put("n1", "B 문서", "본문", modified_at=_T1)

    result = two_project_index_service.refresh(source=SOURCE_NOTION)

    assert result.failed_sources == (f"{_PROJECT_A}/{SOURCE_NOTION}",)
    assert meta_repo.find(_PROJECT_B, SOURCE_NOTION, "n1") is not None


def test_project_with_no_mapping_raises_integration_error_distinct_from_global(
    two_project_index_service,
) -> None:
    """Drive/Notion 매핑이 둘 다 없는 project 는 NO_SOURCE_CONFIGURED_MESSAGE 와 구별되는 오류다."""
    from app.services.documents.sources.document_source import NO_SOURCE_CONFIGURED_MESSAGE

    with pytest.raises(IntegrationError) as exc_info:
        two_project_index_service.refresh(project="no-such-project")

    assert str(exc_info.value) != NO_SOURCE_CONFIGURED_MESSAGE
    assert "no-such-project" in str(exc_info.value)


def test_project_with_only_drive_configured_refreshes_drive_only(
    db_session, meta_repo, make_project_resolver, fake_drive_source
) -> None:
    """한 프로젝트가 Drive 만 등록하고 Notion 은 없으면 Drive 만 갱신하고 정상 종료한다."""
    resolver = make_project_resolver(
        drive_mapping={_PROJECT_A: ("folder-a", fake_drive_source)}
    )
    service = DocumentIndexService(session=db_session, meta_repo=meta_repo, resolver=resolver)
    fake_drive_source.put("d1", "A 문서", "본문", modified_at=_T1)

    result = service.refresh(project=_PROJECT_A)

    assert result.added == 1
    assert result.failed_sources == ()
