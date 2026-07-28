"""DocumentIndexService 단위 테스트 (SPEC 기능 6).

메타 캐시 갱신의 added/updated/removed 집계와 부분 실패 허용을 검증한다.
본문을 가져오지 않는다는 것도 fetch 호출 카운트로 단언한다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.errors import IntegrationError
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.services.documents.document_index_service import DocumentIndexService

_T1 = datetime(2026, 7, 1, 9, 0, 0)
_T2 = datetime(2026, 7, 2, 9, 0, 0)


@pytest.fixture()
def meta_repo(db_session) -> DocumentMetaRepository:
    """테스트 세션에 붙은 메타 저장소."""
    return DocumentMetaRepository(db_session)


@pytest.fixture()
def index_service(db_session, meta_repo, fake_drive_source, fake_notion_source):
    """Drive/Notion 페이크 두 개를 주입한 갱신 서비스."""
    return DocumentIndexService(
        session=db_session,
        meta_repo=meta_repo,
        sources=[fake_drive_source, fake_notion_source],
    )


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
    assert meta_repo.find(SOURCE_DRIVE, "d1").modified_at == _T2


def test_renamed_title_is_counted_as_updated(
    index_service, meta_repo, fake_drive_source
) -> None:
    """수정 시각이 같아도 제목이 바뀌면 updated 로 집계된다."""
    fake_drive_source.put("d1", "구 제목", "본문", modified_at=_T1)
    index_service.refresh()

    fake_drive_source.put("d1", "새 제목", "본문", modified_at=_T1)
    result = index_service.refresh()

    assert result.updated == 1
    assert meta_repo.find(SOURCE_DRIVE, "d1").title == "새 제목"


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
    assert meta_repo.find(SOURCE_DRIVE, "d2") is None
    assert meta_repo.find(SOURCE_DRIVE, "d1") is not None


def test_last_synced_at_is_refreshed_even_when_unchanged(
    index_service, meta_repo, fake_drive_source
) -> None:
    """변경이 없어도 last_synced_at 은 갱신된다(마지막 확인 시각 추적)."""
    fake_drive_source.put("d1", "설계서", "본문", modified_at=_T1)
    index_service.refresh()
    first_synced = meta_repo.find(SOURCE_DRIVE, "d1").last_synced_at

    index_service.refresh()

    assert meta_repo.find(SOURCE_DRIVE, "d1").last_synced_at >= first_synced


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
    assert meta_repo.find(SOURCE_NOTION, "n1") is None


def test_unknown_source_filter_raises_integration_error(index_service) -> None:
    """구성되지 않은 소스 이름을 지정하면 IntegrationError 다."""
    with pytest.raises(IntegrationError):
        index_service.refresh(source="dropbox")


def test_no_configured_source_raises_integration_error(db_session, meta_repo) -> None:
    """소스가 하나도 구성돼 있지 않으면 IntegrationError 로 미구성을 알린다."""
    service = DocumentIndexService(session=db_session, meta_repo=meta_repo, sources=[])

    with pytest.raises(IntegrationError):
        service.refresh()


# --- 부분 실패 허용 -------------------------------------------------------------


def test_partial_failure_commits_already_processed_source(
    index_service, meta_repo, fake_drive_source, fake_notion_source
) -> None:
    """한 소스가 실패해도 앞서 처리된 소스의 변경분은 커밋된 채로 남는다."""
    fake_drive_source.put("d1", "드라이브 문서", "본문", modified_at=_T1)
    fake_notion_source.put("n1", "노션 문서", "본문", modified_at=_T1)
    fake_notion_source.list_should_fail = True

    result = index_service.refresh()

    assert result.added == 1
    assert result.failed_sources == (SOURCE_NOTION,)
    assert meta_repo.find(SOURCE_DRIVE, "d1") is not None


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
    assert meta_repo.find(SOURCE_NOTION, "n1") is not None


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

    assert meta_repo.find(SOURCE_DRIVE, "d1") is not None
    assert meta_repo.find(SOURCE_NOTION, "n1") is not None


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
