"""`resync_registered_documents` 부분 실패 허용 계약 단위 테스트.

`docs/architect-review/54_refresh_lock_abort_asymmetry_verdict.md` F2/F3:
DB 레벨 오류(`SQLAlchemyError`, 예: `IntegrityError`)가 나도 문서 하나만
`failed` 에 담기고 나머지 문서는 계속 처리돼야 한다. DB 접속 없이 가짜
document_repo/sync_service 로 검증한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.services.documents.registered_resync import resync_registered_documents


@dataclass
class _FakeDocument:
    id: str


@dataclass
class _FakeResult:
    status: str


class _FakeDocumentRepo:
    def __init__(self, documents: list[_FakeDocument]) -> None:
        self._documents = documents

    def list_resyncable(self, project: str | None) -> list[_FakeDocument]:
        return list(self._documents)


class _FakeSyncService:
    """지정한 document_id 하나만 IntegrityError 를 던지는 가짜 서비스."""

    def __init__(self, failing_id: str) -> None:
        self._failing_id = failing_id
        self.calls: list[str] = []

    def resync(self, document_id: str, *, force: bool = False) -> _FakeResult:
        self.calls.append(document_id)
        if document_id == self._failing_id:
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        return _FakeResult(status="reindexed")


class _FakeSession:
    def __init__(self) -> None:
        self.rollback_calls = 0

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_integrity_error_on_one_document_does_not_stop_the_rest() -> None:
    """3건 중 2번째가 IntegrityError 여도 failed=[2번째], 나머지는 정상 집계된다."""
    documents = [_FakeDocument("doc-1"), _FakeDocument("doc-2"), _FakeDocument("doc-3")]
    document_repo = _FakeDocumentRepo(documents)
    sync_service = _FakeSyncService(failing_id="doc-2")
    session = _FakeSession()

    result = resync_registered_documents(
        session, document_repo, sync_service, project=None, force=False
    )

    assert result["failed"] == ["doc-2"]
    assert result["total"] == 3
    assert result["reindexed"] + result["skipped"] + len(result["failed"]) == 3
    assert sync_service.calls == ["doc-1", "doc-2", "doc-3"]
    assert session.rollback_calls == 1
