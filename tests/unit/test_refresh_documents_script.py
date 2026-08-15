"""app/scripts/refresh_documents.py 종료코드 규약 단위 테스트.

`docs/architect-review/32-refresh-index-batch-automation.md` §4.1 의 4분기
(전 대상 실패/부분 실패/락 미획득/정상)를 가짜 bundle·lock_acquire 로
검증한다. DB·외부 API 없이 `_execute()` 만 직접 호출한다.
"""

from __future__ import annotations

import argparse
import logging

from app.core.errors import IntegrationError
from app.scripts.refresh_documents import EXIT_FAILED, EXIT_OK, _execute
from app.services.documents.document_index_service import RefreshResult


class _FakeDocumentIndexService:
    """refresh() 호출을 기록하고 미리 정한 결과/예외를 반환하는 가짜 서비스."""

    def __init__(self, *, result: RefreshResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[str | None, str | None, bool]] = []

    def refresh(
        self, source: str | None, project: str | None, index_bodies: bool = False
    ) -> RefreshResult:
        self.calls.append((source, project, index_bodies))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class _FakeDocumentRepo:
    """등록 문서 재동기화 대상이 없다고 응답하는 가짜 저장소(DB 접근 없음)."""

    def list_resyncable(self, project: str | None) -> list:
        return []


class _FakeSession:
    def rollback(self) -> None:  # pragma: no cover - 실패 경로가 없어 호출되지 않음
        raise AssertionError("resyncable 문서가 없으므로 rollback 이 호출되면 안 된다")


class _FakeBundle:
    def __init__(self, index_service: _FakeDocumentIndexService):
        self.document_index_service = index_service
        self.document_repo = _FakeDocumentRepo()
        self.sync_service = None
        self.session = _FakeSession()


def _args(
    *, include_registered: bool = False, index_bodies: bool = True
) -> argparse.Namespace:
    return argparse.Namespace(
        source=None,
        project=None,
        include_registered=include_registered,
        force=False,
        index_bodies=index_bodies,
    )


def test_lock_not_acquired_returns_ok_without_running_sync(caplog) -> None:
    """락 미획득 시 INFO 로그 후 exit 0, refresh 는 호출되지 않는다."""
    index_service = _FakeDocumentIndexService(result=None)
    bundle = _FakeBundle(index_service)

    with caplog.at_level(logging.INFO):
        code = _execute(bundle, _args(), lock_acquire=lambda: False)

    assert code == EXIT_OK
    assert index_service.calls == []
    assert any("이미 실행 중" in r.message for r in caplog.records)


def test_all_sources_failed_returns_failed_exit_code() -> None:
    """refresh 가 IntegrationError 를 던지면(전 대상 실패) exit 1."""
    index_service = _FakeDocumentIndexService(error=IntegrationError("전부 실패"))
    bundle = _FakeBundle(index_service)

    code = _execute(bundle, _args(), lock_acquire=lambda: True)

    assert code == EXIT_FAILED


def test_partial_failure_returns_ok_with_warning(caplog) -> None:
    """일부 소스만 실패하면 WARN 로그를 남기고 exit 0."""
    result = RefreshResult(
        synced=5, added=1, updated=0, removed=0, failed_sources=("default/drive",)
    )
    index_service = _FakeDocumentIndexService(result=result)
    bundle = _FakeBundle(index_service)

    with caplog.at_level(logging.INFO):
        code = _execute(bundle, _args(), lock_acquire=lambda: True)

    assert code == EXIT_OK
    assert any("일부 소스 갱신 실패" in r.message for r in caplog.records)


def test_success_returns_ok_and_logs_aggregate(caplog) -> None:
    """정상 완료 시 exit 0, 집계가 INFO 로그에 담긴다."""
    result = RefreshResult(synced=3, added=1, updated=1, removed=0, failed_sources=())
    index_service = _FakeDocumentIndexService(result=result)
    bundle = _FakeBundle(index_service)

    with caplog.at_level(logging.INFO):
        code = _execute(bundle, _args(), lock_acquire=lambda: True)

    assert code == EXIT_OK
    assert any("동기화 완료" in r.message and "synced=3" in r.message for r in caplog.records)


def test_include_registered_adds_registered_summary_to_log(caplog) -> None:
    """include_registered=True 면 registered 집계가 로그에 함께 담긴다."""
    result = RefreshResult(synced=0, added=0, updated=0, removed=0, failed_sources=())
    index_service = _FakeDocumentIndexService(result=result)
    bundle = _FakeBundle(index_service)

    with caplog.at_level(logging.INFO):
        code = _execute(
            bundle, _args(include_registered=True), lock_acquire=lambda: True
        )

    assert code == EXIT_OK
    assert any("registered(total=0" in r.message for r in caplog.records)


def test_index_bodies_flag_passed_to_refresh() -> None:
    """--index-bodies 주면 refresh 가 index_bodies=True 로 호출된다."""
    result = RefreshResult(synced=0, added=0, updated=0, removed=0, failed_sources=())
    index_service = _FakeDocumentIndexService(result=result)
    bundle = _FakeBundle(index_service)

    _execute(bundle, _args(index_bodies=True), lock_acquire=lambda: True)

    assert index_service.calls == [(None, None, True)]


def test_index_bodies_defaults_to_true() -> None:
    """플래그를 안 주면 refresh 가 index_bodies=True 로 호출된다.

    기본 검색 전략이 indexed(3-arm RRF)라, 본문 색인이 꺼진 채 갱신하면
    keyword/vector arm 이 비어 제목 매칭만으로 조용히 퇴화한다.
    """
    result = RefreshResult(synced=0, added=0, updated=0, removed=0, failed_sources=())
    index_service = _FakeDocumentIndexService(result=result)
    bundle = _FakeBundle(index_service)

    _execute(bundle, _args(), lock_acquire=lambda: True)

    assert index_service.calls == [(None, None, True)]


def test_no_index_bodies_flag_disables_body_indexing() -> None:
    """--no-index-bodies 를 주면 refresh 가 index_bodies=False 로 호출된다."""
    result = RefreshResult(synced=0, added=0, updated=0, removed=0, failed_sources=())
    index_service = _FakeDocumentIndexService(result=result)
    bundle = _FakeBundle(index_service)

    _execute(bundle, _args(index_bodies=False), lock_acquire=lambda: True)

    assert index_service.calls == [(None, None, False)]
