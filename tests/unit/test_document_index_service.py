"""DocumentIndexService 단위 테스트 (SPEC 기능 6).

메타 캐시 갱신의 added/updated/removed 집계와 부분 실패 허용, 그리고
프로젝트 확장(다중 소스 순회·교차 프로젝트 삭제 방지)을 검증한다. 본문을
가져오지 않는다는 것도 fetch 호출 카운트로 단언한다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.errors import IntegrationError
from app.models import DEFAULT_PROJECT
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION
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
    assert {
        m.external_id
        for m in meta_repo.list_by_project_source(DEFAULT_PROJECT, SOURCE_DRIVE)
    } == {"d1", "d2"}


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


def test_mime_created_owner_backfill_does_not_count_as_updated_or_fetch_body(
    body_index_service, meta_repo, fake_drive_source
) -> None:
    """mime_type/created_at/owner 만 새로 채워지는 백필은 updated·본문 fetch 를 유발하지 않는다.

    새 nullable 컬럼을 `is_changed` 판정에 넣으면 첫 백필에서 전 문서가
    NULL→값으로 바뀌며 updated 로 잡혀 본문을 전량 재fetch 한다(rate limit
    폭발). 이 회귀를 막는 것이 T3 의 핵심 제약이다.
    """
    fake_drive_source.put("d1", "설계서", "# 설계서\n\n본문 내용.", modified_at=_T1)
    body_index_service.refresh(index_bodies=True)
    fake_drive_source.reset_counts()

    fake_drive_source.put(
        "d1",
        "설계서",
        "본문",
        modified_at=_T1,
        mime_type="application/pdf",
        created_at=_T1,
        owner="owner@example.test",
    )
    result = body_index_service.refresh(index_bodies=True)

    assert result.updated == 0
    assert result.added == 0
    assert fake_drive_source.fetch_call_count == 0
    row = meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    assert row.mime_type == "application/pdf"
    assert row.created_at == _T1
    assert row.owner == "owner@example.test"


def test_folder_backfill_does_not_count_as_updated_or_fetch_body(
    body_index_service, meta_repo, fake_drive_source
) -> None:
    """폴더 컬럼 백필은 값만 채우고 updated·본문 재fetch 를 유발하지 않는다(R3).

    이 판정이 깨지면 백필 첫 실행에서 전 문서가 updated 로 잡혀 Drive 본문을
    전량 다시 받는다(rate limit·시간 폭발).
    """
    fake_drive_source.put("d1", "설계서", "# 설계서\n\n본문 내용.", modified_at=_T1)
    body_index_service.refresh(index_bodies=True)
    fake_drive_source.reset_counts()

    fake_drive_source.put(
        "d1",
        "설계서",
        "본문",
        modified_at=_T1,
        folder_ancestor_ids=("root", "sub"),
        folder_path="설계",
    )
    result = body_index_service.refresh(index_bodies=True)

    assert result.updated == 0
    assert result.added == 0
    assert fake_drive_source.fetch_call_count == 0
    row = meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    assert row.folder_ancestor_ids == ["root", "sub"]
    assert row.folder_path == "설계"


def test_new_row_stores_folder_columns(index_service, meta_repo, fake_drive_source) -> None:
    """신규 행에도 폴더 컬럼이 저장된다(루트 직속 파일은 folder_path 가 빈 문자열)."""
    fake_drive_source.put(
        "d1", "설계서", "본문", modified_at=_T1, folder_ancestor_ids=("root",), folder_path=""
    )

    index_service.refresh()

    row = meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    assert row.folder_ancestor_ids == ["root"]
    assert row.folder_path == ""


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


def test_index_bodies_false_logs_warning(
    index_service, fake_drive_source, caplog
) -> None:
    """index_bodies=False(기본값)면 검색이 title 매칭만으로 퇴화할 수 있음을
    경고한다(45번 리뷰 §3.4).

    검색 기본 전략은 indexed(3-arm RRF)인데 refresh 기본은 본문을 색인하지
    않으므로, 이 경고 없이는 section 청크가 비거나 낡은 채 남는 실패가
    조용히 지나간다.
    """
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)

    with caplog.at_level("WARNING"):
        index_service.refresh(index_bodies=False)

    assert "본문 색인을 건너뜀" in caplog.text


def test_index_bodies_true_does_not_log_skip_warning(
    body_index_service, fake_drive_source, caplog
) -> None:
    """index_bodies=True 면 본문 색인을 건너뛴다는 경고를 찍지 않는다."""
    fake_drive_source.put("d1", "설계서", "# 설계서\n\n본문 내용.", modified_at=_T1)

    with caplog.at_level("WARNING"):
        body_index_service.refresh(index_bodies=True)

    assert "본문 색인을 건너뜀" not in caplog.text


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
    committed = meta_repo.list_by_project_source(DEFAULT_PROJECT, SOURCE_DRIVE)
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
    assert len(meta_repo.list_by_project_source(DEFAULT_PROJECT, SOURCE_DRIVE)) == BATCH_SIZE
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
    assert result.added == len(meta_repo.list_by_project_source(DEFAULT_PROJECT, SOURCE_DRIVE))
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
    assert len(meta_repo.list_by_project_source(DEFAULT_PROJECT, SOURCE_DRIVE)) == total
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
    assert list(meta_repo.list_by_project_source(DEFAULT_PROJECT, SOURCE_DRIVE)) == []


# --- 교차 소스 삭제 회귀 방지 (같은 project 내부) --------------------------------


def test_refreshing_one_source_never_deletes_another_sources_rows(
    db_session, index_service, meta_repo, fake_drive_source, fake_notion_source
) -> None:
    """이미 캐시된 다른 source 의 행이 한 source 갱신 때문에 삭제되지 않는다.

    `_refresh_source` 가 source 로만 좁히고 다른 출처 행까지 기준 집합에
    포함시키면 "원본에서 사라진 것"으로 오인돼 통째로 삭제된다. 데이터 유실로
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


# --- index_bodies (설계 §1 Phase2, 문서 6·7·8·9번) -------------------------------


@pytest.fixture()
def body_index_deps(db_session):
    """`index_bodies=True` 에 필요한 저장소·인덱서 묶음."""
    from app.models import EMBEDDING_DIM
    from app.repositories.chunk_repository import ChunkRepository
    from app.repositories.document_repository import DocumentRepository
    from app.repositories.endpoint_repository import EndpointRepository
    from app.services.indexer.embedding_provider import HashEmbeddingProvider
    from app.services.indexer.indexer_service import IndexerService

    document_repo = DocumentRepository(db_session)
    endpoint_repo = EndpointRepository(db_session)
    chunk_repo = ChunkRepository(db_session)
    indexer = IndexerService(
        endpoint_repo=endpoint_repo,
        chunk_repo=chunk_repo,
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
    )
    return {
        "document_repo": document_repo,
        "endpoint_repo": endpoint_repo,
        "chunk_repo": chunk_repo,
        "indexer": indexer,
    }


@pytest.fixture()
def body_index_service(db_session, meta_repo, default_resolver, body_index_deps):
    """`index_bodies=True` 를 쓸 수 있는 DocumentIndexService."""
    return DocumentIndexService(
        session=db_session, meta_repo=meta_repo, resolver=default_resolver, **body_index_deps
    )


def test_index_bodies_false_does_not_fetch(body_index_service, fake_drive_source) -> None:
    """index_bodies 기본값(False)이면 여전히 본문 fetch 를 하지 않는다."""
    fake_drive_source.put("d1", "설계서", "# 설계서\n본문", modified_at=_T1)

    body_index_service.refresh()

    assert fake_drive_source.fetch_call_count == 0


def test_index_bodies_true_fetches_new_document_and_indexes_chunks(
    body_index_service, body_index_deps, fake_drive_source
) -> None:
    """index_bodies=True 면 신규 문서를 fetch 해 청크까지 색인한다."""
    from app.services.documents.document_body_indexer import deterministic_document_id

    fake_drive_source.put("d1", "설계서", "# 설계서\n\n본문 내용.", modified_at=_T1)

    body_index_service.refresh(index_bodies=True)

    assert fake_drive_source.fetch_call_count == 1
    document_id = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    document = body_index_deps["document_repo"].get(document_id)
    assert document is not None
    chunks = body_index_deps["chunk_repo"].list_by_document(document_id)
    assert len(chunks) > 0


def test_index_bodies_records_document_id_on_meta_row(
    body_index_service, meta_repo, fake_drive_source
) -> None:
    """본문 색인에 성공하면 document_meta.document_id 가 채워진다."""
    from app.services.documents.document_body_indexer import deterministic_document_id

    fake_drive_source.put("d1", "설계서", "# 설계서\n\n본문 내용.", modified_at=_T1)

    body_index_service.refresh(index_bodies=True)

    row = meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    assert row.document_id == deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")


def test_index_bodies_skips_fetch_when_modified_at_unchanged(
    body_index_service, fake_drive_source
) -> None:
    """modified_at 이 그대로면 fetch 자체를 스킵한다(문서 7번 게이트)."""
    fake_drive_source.put("d1", "설계서", "# 설계서\n\n본문 내용.", modified_at=_T1)
    body_index_service.refresh(index_bodies=True)
    fake_drive_source.reset_counts()

    body_index_service.refresh(index_bodies=True)

    assert fake_drive_source.fetch_call_count == 0


def test_index_bodies_refetches_when_modified_at_changed(
    body_index_service, body_index_deps, fake_drive_source
) -> None:
    """modified_at 이 바뀌면 다시 fetch 해 청크를 교체한다."""
    from app.services.documents.document_body_indexer import deterministic_document_id

    fake_drive_source.put("d1", "설계서", "# 설계서\n\n원본 내용.", modified_at=_T1)
    body_index_service.refresh(index_bodies=True)
    document_id = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    old_hash = body_index_deps["document_repo"].get(document_id).content_hash
    fake_drive_source.reset_counts()

    fake_drive_source.put("d1", "설계서", "# 설계서\n\n완전히 달라진 내용.", modified_at=_T2)
    body_index_service.refresh(index_bodies=True)

    assert fake_drive_source.fetch_call_count == 1
    new_hash = body_index_deps["document_repo"].get(document_id).content_hash
    assert new_hash != old_hash


def test_index_bodies_removed_file_deletes_document_and_chunks(
    body_index_service, body_index_deps, meta_repo, fake_drive_source
) -> None:
    """원본에서 삭제된 문서는 document_meta 뿐 아니라 대응 Document/청크도 삭제된다(문서 9번)."""
    from app.services.documents.document_body_indexer import deterministic_document_id

    fake_drive_source.put("d1", "설계서", "# 설계서\n\n본문 내용.", modified_at=_T1)
    body_index_service.refresh(index_bodies=True)
    document_id = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    assert body_index_deps["document_repo"].get(document_id) is not None

    fake_drive_source.remove("d1")
    result = body_index_service.refresh(index_bodies=True)

    assert result.removed == 1
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1") is None
    assert body_index_deps["document_repo"].get(document_id) is None
    assert body_index_deps["chunk_repo"].list_by_document(document_id) == []


def test_index_bodies_removed_file_without_prior_body_index_is_safe(
    body_index_service, meta_repo, fake_drive_source
) -> None:
    """본문 색인 전(document_id 없음)에 삭제되면 meta 행만 지우고 에러 없이 끝난다."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    body_index_service.refresh(index_bodies=False)

    fake_drive_source.remove("d1")
    result = body_index_service.refresh(index_bodies=False)

    assert result.removed == 1
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1") is None


def test_index_bodies_backfills_unchanged_row_missing_document_id(
    body_index_service, meta_repo, fake_drive_source
) -> None:
    """메타 변경이 없어도 document_id 가 NULL 이면 index_bodies=True 에서 소급 색인된다.

    doc36 §6-2 백필 회귀: 게이트가 needs_body_index 에만 걸리면, 이미
    메타만 동기화되고 본문 색인이 없던 기존 행은 --index-bodies 를 다시
    돌려도 영원히 색인되지 않는다.
    """
    from app.services.documents.document_body_indexer import deterministic_document_id

    fake_drive_source.put("d1", "설계서", "# 설계서\n\n본문 내용.", modified_at=_T1)
    body_index_service.refresh(index_bodies=False)
    assert meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1").document_id is None
    fake_drive_source.reset_counts()

    body_index_service.refresh(index_bodies=True)

    assert fake_drive_source.fetch_call_count == 1
    row = meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    assert row.document_id == deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")


def test_index_bodies_one_fetch_failure_does_not_block_other_documents(
    body_index_service, body_index_deps, meta_repo, fake_drive_source
) -> None:
    """한 문서의 fetch 실패가 같은 배치의 다른 문서 색인을 막지 않는다.

    fetch 는 DB 쓰기 이전 지점이라 실패를 삼켜도 반쯤 쓰인 상태가 커밋되지
    않는다. 실패한 문서는 document_id 가 NULL 로 남아 다음 실행에서 자동
    재시도된다.
    """
    from app.services.documents.document_body_indexer import deterministic_document_id

    fake_drive_source.put("d1", "실패 문서", "본문", modified_at=_T1)
    fake_drive_source.put("d2", "정상 문서", "# 정상 문서\n\n본문 내용.", modified_at=_T1)
    fake_drive_source.failing_fetch_ids = {"d1"}

    result = body_index_service.refresh(index_bodies=True)

    assert result.failed_sources == ()
    row_d1 = meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    assert row_d1 is not None
    assert row_d1.document_id is None
    row_d2 = meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d2")
    document_id_d2 = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d2")
    assert row_d2.document_id == document_id_d2
    assert body_index_deps["document_repo"].get(document_id_d2) is not None


# --- index_bodies 백필 크래시 3건 수정 (doc42) ------------------------------------


def test_index_bodies_empty_body_is_skipped_without_error(
    body_index_service, meta_repo, fake_drive_source
) -> None:
    """본문이 공백뿐이면 오류 없이 건너뛴다(빈 본문은 오류가 아니다)."""
    fake_drive_source.put("d1", "빈 문서", "   \n\n  ", modified_at=_T1)

    result = body_index_service.refresh(index_bodies=True)

    assert result.failed_sources == ()
    row = meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    assert row is not None
    assert row.document_id is None


def test_index_bodies_becoming_empty_deletes_previously_indexed_document(
    body_index_service, body_index_deps, meta_repo, fake_drive_source
) -> None:
    """이미 색인된 문서의 원문이 빈 문서로 바뀌면 옛 Document/청크가 삭제된다."""
    from app.services.documents.document_body_indexer import deterministic_document_id

    fake_drive_source.put("d1", "설계서", "# 설계서\n\n본문 내용.", modified_at=_T1)
    body_index_service.refresh(index_bodies=True)
    document_id = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    assert body_index_deps["document_repo"].get(document_id) is not None

    fake_drive_source.put("d1", "설계서", "   ", modified_at=_T2)
    result = body_index_service.refresh(index_bodies=True)

    assert result.failed_sources == ()
    assert body_index_deps["document_repo"].get(document_id) is None
    assert body_index_deps["chunk_repo"].list_by_document(document_id) == []
    row = meta_repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    assert row.document_id is None


def test_index_bodies_commits_mid_source_when_only_body_indexing_changes(
    db_session, meta_repo, fake_drive_source, make_project_resolver, body_index_deps
) -> None:
    """본문 색인만 쌓이고 메타 변경이 0건이어도 BATCH_SIZE 경계에서 중간 커밋된다.

    doc36 §6-2 백필 회귀: 커밋 경계가 added/updated/removed 로만 판정되면,
    이번 백필처럼 메타가 전부 무변경(0)인 상태에서는 본문 색인이 아무리
    쌓여도 커밋되지 않고 소스 전체가 끝나야 커밋된다. 그러면 문서 1건이라도
    도중에(list 순회 중단 등으로) 실패할 때 이미 색인한 본문까지 통째로
    롤백된다.
    """
    from app.services.documents.document_body_indexer import deterministic_document_id

    resolver = make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", fake_drive_source)}
    )
    service = DocumentIndexService(
        session=db_session, meta_repo=meta_repo, resolver=resolver, **body_index_deps
    )
    total = BATCH_SIZE + 5
    for index in range(total):
        fake_drive_source.put(
            f"d{index}", f"문서 {index}", f"# 문서 {index}\n\n본문 내용.", modified_at=_T1
        )
    service.refresh(index_bodies=False)  # 메타 행만 생성, document_id 는 전부 NULL

    fake_drive_source.fail_listing_at_index = BATCH_SIZE + 2
    result = service.refresh(index_bodies=True)

    assert result.failed_sources == (f"{DEFAULT_PROJECT}/{SOURCE_DRIVE}",)
    db_session.expire_all()
    first_committed_id = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d0")
    last_committed_id = deterministic_document_id(
        DEFAULT_PROJECT, SOURCE_DRIVE, f"d{BATCH_SIZE - 1}"
    )
    rolled_back_id = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, f"d{BATCH_SIZE}")
    assert body_index_deps["document_repo"].get(first_committed_id) is not None
    assert body_index_deps["document_repo"].get(last_committed_id) is not None
    assert body_index_deps["document_repo"].get(rolled_back_id) is None


# --- 색인 커버리지 가시화 (개선 #5) ------------------------------------------------


def test_unsupported_mime_is_skipped_before_fetch_and_counted_separately(
    body_index_service, fake_drive_source
) -> None:
    """미지원 MIME 은 fetch 전에 걸러지고 unsupported 로, 정상 문서는 unindexed 0 이다."""
    fake_drive_source.put("d1", "이미지", "본문無", modified_at=_T1, mime_type="image/png")
    fake_drive_source.put("d2", "정상 문서", "# 정상\n\n본문 내용.", modified_at=_T1)
    fake_drive_source.unsupported_mime_types = {"image/png"}

    result = body_index_service.refresh(index_bodies=True)

    assert result.unsupported == 1
    assert result.unindexed == 0
    assert "d1" not in fake_drive_source.fetched_ids


def test_unsupported_mime_is_never_refetched_across_multiple_refreshes(
    body_index_service, fake_drive_source
) -> None:
    """미지원 MIME 문서는 여러 번 refresh 해도 fetch 를 시도하지 않는다(영구 재시도 회귀 가드)."""
    fake_drive_source.put("d1", "이미지", "본문無", modified_at=_T1, mime_type="image/png")
    fake_drive_source.unsupported_mime_types = {"image/png"}

    body_index_service.refresh(index_bodies=True)
    assert "d1" not in fake_drive_source.fetched_ids
    fake_drive_source.reset_counts()

    result = body_index_service.refresh(index_bodies=True)

    assert "d1" not in fake_drive_source.fetched_ids
    assert result.unsupported == 1


def test_index_bodies_false_marks_scope_unindexed_except_unsupported(
    index_service, fake_drive_source
) -> None:
    """index_bodies=False 면 범위 전체가 unindexed, 미지원 MIME 문서만 unsupported 다."""
    fake_drive_source.put("d1", "정상 문서", "본문", modified_at=_T1)
    fake_drive_source.put("d2", "이미지", "본문無", modified_at=_T1, mime_type="image/png")
    fake_drive_source.unsupported_mime_types = {"image/png"}

    result = index_service.refresh(index_bodies=False)

    assert result.unindexed == 1
    assert result.unsupported == 1


def test_fetch_failure_on_supported_mime_is_counted_as_unindexed(
    body_index_service, fake_drive_source
) -> None:
    """지원 MIME 문서의 fetch 실패는 unindexed 로 잡힌다."""
    fake_drive_source.put("d1", "실패 문서", "본문", modified_at=_T1)
    fake_drive_source.failing_fetch_ids = {"d1"}

    result = body_index_service.refresh(index_bodies=True)

    assert result.unindexed == 1
    assert result.unsupported == 0


def test_whitespace_only_body_is_counted_as_unindexed(
    body_index_service, fake_drive_source
) -> None:
    """본문이 공백뿐이라 document_id 가 NULL 로 남는 문서는 unindexed 다."""
    fake_drive_source.put("d1", "빈 문서", "   \n\n  ", modified_at=_T1)

    result = body_index_service.refresh(index_bodies=True)

    assert result.unindexed == 1


def test_listing_truncated_aggregates_labels_across_sources(
    index_service, fake_drive_source, fake_notion_source
) -> None:
    """절단된 소스 1개 + 정상 소스 1개 → listing_truncated 에 절단된 소스만 라벨로 담긴다."""
    fake_drive_source.put("d1", "드라이브 문서", "본문", modified_at=_T1)
    fake_notion_source.put("n1", "노션 문서", "본문", modified_at=_T1)
    fake_drive_source.listing_truncated = True

    result = index_service.refresh()

    assert result.listing_truncated == (f"{DEFAULT_PROJECT}/{SOURCE_DRIVE}",)


def test_partial_failure_coverage_reflects_only_committed_batch(
    db_session, meta_repo, fake_drive_source, make_project_resolver
) -> None:
    """중간에 실패해도 coverage(unindexed) 는 커밋된 배치까지만 집계된다."""
    resolver = make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", fake_drive_source)}
    )
    service = DocumentIndexService(session=db_session, meta_repo=meta_repo, resolver=resolver)
    total = BATCH_SIZE + 5
    for index in range(total):
        fake_drive_source.put(f"d{index}", f"문서 {index}", "본문", modified_at=_T1)
    fake_drive_source.fail_listing_at_index = BATCH_SIZE + 2

    result = service.refresh(index_bodies=False)

    assert result.failed_sources == (f"{DEFAULT_PROJECT}/{SOURCE_DRIVE}",)
    assert result.unindexed == BATCH_SIZE
