"""Drive/Notion 메타데이터 캐시 갱신 서비스 (SPEC 기능 6).

`refresh_index` 도구가 호출하는 진입점이다. 각 소스의 `list_files()` 로 얻은
메타데이터만 `document_meta` 에 upsert 하고, **본문은 가져오지 않는다.**

부분 실패 허용이 핵심 요구사항이다. 갱신 도중 예외가 나도 **이미 처리된 행은
커밋된 상태로 남아야** 하고, 실패한 항목만 다음 갱신에서 재시도할 수 있어야
한다. 그래서 커밋 경계를 **배치 단위**로 낮춘다(`BATCH_SIZE` 건마다 커밋).

소스 단위로만 커밋하면 소스가 1개인 환경이나 한 소스 처리 도중의 실패에서
전량 롤백돼 위 요구사항을 충족하지 못한다.

집계(`added`/`updated`/`removed`)는 **실제로 커밋된 행만** 센다. 커밋에
실패해 롤백된 배치는 집계에 넣지 않으므로, 반환값과 DB 상태가 항상 일치한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.models.document_meta import DocumentMeta
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.services.documents.document_source import (
    NO_SOURCE_CONFIGURED_MESSAGE,
    DocumentSource,
    FileMeta,
)

_LOG = get_logger("docs_mcp.documents.index")

#: 커밋 경계. 이 건수만큼 변경이 쌓일 때마다 커밋해, 도중에 실패해도 직전
#: 배치까지는 DB 에 남게 한다(SPEC 기능 6 "부분 실패 허용").
BATCH_SIZE = 100


@dataclass(frozen=True)
class RefreshResult:
    """메타 캐시 갱신 결과 집계.

    Attributes:
        synced: 소스에서 조회한 전체 문서 수.
        added: 새로 생성된 메타 행 수.
        updated: `modified_at`/제목/URL 이 실제로 바뀌어 갱신된 행 수.
        removed: 소스에서 사라져 캐시에서 제거된 행 수.
        failed_sources: 갱신에 실패한 소스 이름 목록(부분 실패 허용).
    """

    synced: int
    added: int
    updated: int
    removed: int
    failed_sources: tuple[str, ...] = ()


@dataclass
class _SourceCounts:
    """소스 하나를 처리하는 동안 누적하는 가변 카운터."""

    synced: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0

    @property
    def total_changes(self) -> int:
        """커밋 경계 판정에 쓰는 실제 변경 건수(`synced` 는 조회 수라 제외)."""
        return self.added + self.updated + self.removed


class _PartialRefreshError(Exception):
    """소스 갱신이 중단됐지만 일부 배치는 커밋된 상태임을 알리는 내부 예외.

    호출자(`refresh`)가 "이미 커밋된 분"을 집계에 반영할 수 있도록 원인 예외와
    확정 집계를 함께 실어 나른다. 서비스 밖으로는 노출되지 않는다.
    """

    def __init__(self, cause: Exception, committed: _SourceCounts) -> None:
        """원인 예외와 커밋 완료된 집계를 보관한다."""
        super().__init__(str(cause))
        self.cause = cause
        self.committed = committed


class DocumentIndexService:
    """등록된 문서 소스들의 메타데이터를 `document_meta` 에 동기화한다."""

    def __init__(
        self,
        session: Session,
        meta_repo: DocumentMetaRepository,
        sources: list[DocumentSource],
    ) -> None:
        """세션·저장소·소스 목록을 보관한다.

        Args:
            session: 커밋/롤백 경계를 소스 단위로 제어하기 위한 DB 세션.
            meta_repo: `document_meta` 저장소.
            sources: 동기화 대상 문서 소스 어댑터 목록. 비어 있어도 된다
                (자격증명 미설정 환경에서는 빈 목록으로 구성된다).
        """
        self._session = session
        self._meta_repo = meta_repo
        self._sources = list(sources)

    def refresh(self, source: str | None = None) -> RefreshResult:
        """소스들의 문서 목록을 조회해 메타 캐시를 갱신한다.

        Args:
            source: 특정 소스(`drive`/`notion`)만 갱신할 때 지정. 생략하면 전체.

        Returns:
            added/updated/removed/synced 집계와 실패한 소스 목록.

        Raises:
            IntegrationError: 대상 소스가 하나도 구성돼 있지 않거나, 갱신을
                시도한 모든 소스가 실패한 경우.
        """
        targets = self._resolve_targets(source)

        totals = _SourceCounts()
        failed: list[str] = []
        for document_source in targets:
            name = document_source.source_name
            try:
                counts = self._refresh_source(document_source)
            except _PartialRefreshError as exc:
                # 부분 실패 허용: 실패한 소스라도 이미 커밋된 배치는 집계에 넣는다.
                # (`_refresh_source` 가 미커밋 배치만 롤백하고 확정분을 실어 보낸다)
                _merge_counts(totals, exc.committed)
                _LOG.warning("문서 소스 갱신 실패(다음 갱신에서 재시도 가능): %s (%s)", name, exc)
                failed.append(name)
                continue
            _merge_counts(totals, counts)

        # 모든 소스가 실패했고 커밋된 변경도 전혀 없으면 "조용한 무동작"이 되므로
        # 예외로 알린다. 반대로 일부라도 커밋됐다면 그 사실이 집계와
        # `failed_sources` 로 전달되어야 하므로 정상 반환한다.
        if failed and len(failed) == len(targets) and totals.total_changes == 0:
            raise IntegrationError(
                f"failed to refresh every document source: {', '.join(failed)}"
            )

        return RefreshResult(
            synced=totals.synced,
            added=totals.added,
            updated=totals.updated,
            removed=totals.removed,
            failed_sources=tuple(failed),
        )

    def _resolve_targets(self, source: str | None) -> list[DocumentSource]:
        """갱신 대상 소스 목록을 결정하고 비어 있으면 예외를 던진다."""
        if not self._sources:
            raise IntegrationError(NO_SOURCE_CONFIGURED_MESSAGE)
        if source is None:
            return self._sources
        targets = [s for s in self._sources if s.source_name == source]
        if not targets:
            raise IntegrationError(f"unknown or unconfigured document source: {source}")
        return targets

    def _refresh_source(self, document_source: DocumentSource) -> _SourceCounts:
        """소스 하나의 목록을 배치 단위로 커밋하며 반영한다.

        `BATCH_SIZE` 건마다 커밋하므로, 도중에 예외가 나도 직전 배치까지는
        DB 에 남는다. 실패 지점 이후 항목은 다음 갱신에서 재시도된다.

        Returns:
            **실제로 커밋된** 변경만 반영한 집계.

        Raises:
            _PartialRefreshError: 목록 조회나 배치 처리 중 실패한 경우. 직전까지
                커밋된 배치는 보존되며 그 집계가 예외에 실려 전달된다.
        """
        source_name = document_source.source_name
        try:
            remote_files = document_source.list_files()
        except Exception as exc:
            # 목록 조회 실패: 이 소스는 아무 행도 건드리지 않았다(확정분 0건).
            raise _PartialRefreshError(exc, _SourceCounts()) from exc
        existing = {m.external_id: m for m in self._meta_repo.list_by_source(source_name)}
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        committed = _SourceCounts(synced=len(remote_files))
        pending = _SourceCounts()
        seen: set[str] = set()
        try:
            for meta in remote_files:
                if not meta.external_id or meta.external_id in seen:
                    continue
                seen.add(meta.external_id)
                self._stage_upsert(source_name, meta, existing, now, pending)
                if pending.total_changes >= BATCH_SIZE:
                    pending = self._commit_batch(committed, pending)

            for external_id, row in existing.items():
                if external_id not in seen:
                    self._meta_repo.delete(row)
                    pending.removed += 1
                    if pending.total_changes >= BATCH_SIZE:
                        pending = self._commit_batch(committed, pending)
        except Exception as exc:
            # 마지막 배치는 미완성이므로 버리고, 이미 커밋된 배치만 남긴다.
            self._session.rollback()
            _LOG.warning(
                "메타 캐시 갱신 중단(직전 배치까지 보존): source=%s added=%d updated=%d removed=%d",
                source_name,
                committed.added,
                committed.updated,
                committed.removed,
            )
            raise _PartialRefreshError(exc, committed) from exc

        self._commit_batch(committed, pending)
        _LOG.info(
            "메타 캐시 갱신 완료: source=%s synced=%d added=%d updated=%d removed=%d",
            source_name,
            committed.synced,
            committed.added,
            committed.updated,
            committed.removed,
        )
        return committed

    def _stage_upsert(
        self,
        source_name: str,
        meta: FileMeta,
        existing: dict[str, DocumentMeta],
        now: datetime,
        pending: _SourceCounts,
    ) -> None:
        """문서 한 건의 신규 생성/갱신을 세션에 올리고 미커밋 집계에 반영한다."""
        current = existing.get(meta.external_id)
        if current is None:
            self._meta_repo.add(_new_row(source_name, meta, now))
            pending.added += 1
        elif _apply_changes(current, meta, now):
            pending.updated += 1

    def _commit_batch(
        self, committed: _SourceCounts, pending: _SourceCounts
    ) -> _SourceCounts:
        """미커밋 변경을 커밋하고 그만큼만 확정 집계로 옮긴다.

        커밋이 성공한 뒤에야 `committed` 에 더하므로, 반환되는 집계는 항상
        DB 에 실재하는 변경만 센다.

        Returns:
            비워진 새 미커밋 집계.
        """
        self._session.commit()
        committed.added += pending.added
        committed.updated += pending.updated
        committed.removed += pending.removed
        return _SourceCounts()


def _merge_counts(totals: _SourceCounts, counts: _SourceCounts) -> None:
    """소스 하나의 집계를 전체 집계에 누적한다."""
    totals.synced += counts.synced
    totals.added += counts.added
    totals.updated += counts.updated
    totals.removed += counts.removed


def _new_row(source_name: str, meta: FileMeta, now: datetime) -> DocumentMeta:
    """FileMeta 로부터 신규 `document_meta` 행을 만든다."""
    return DocumentMeta(
        source=source_name,
        external_id=meta.external_id,
        title=meta.title,
        url=meta.url,
        modified_at=meta.modified_at,
        last_synced_at=now,
    )


def _apply_changes(row: DocumentMeta, meta: FileMeta, now: datetime) -> bool:
    """기존 행에 변경분을 반영하고 실제로 바뀌었는지 여부를 반환한다.

    `modified_at`·제목·URL 이 모두 같으면 `last_synced_at` 만 갱신하고 `updated`
    집계에는 넣지 않는다(SPEC 검증 기준: 수정 시각이 같으면 updated 아님).
    """
    row.last_synced_at = now
    is_changed = (
        row.modified_at != meta.modified_at
        or row.title != meta.title
        or row.url != meta.url
    )
    if not is_changed:
        return False
    row.title = meta.title
    row.url = meta.url
    row.modified_at = meta.modified_at
    return True
