"""Drive/Notion 메타데이터 캐시 갱신 서비스 (SPEC 기능 6).

`refresh_index` 도구가 호출하는 진입점이다. 각 소스의 `list_files()` 로 얻은
메타데이터만 `document_meta` 에 upsert 하고, **본문은 가져오지 않는다.**

부분 실패 허용이 핵심 요구사항이다. 소스 하나가 실패해도 그 앞에서 이미
처리된 소스의 변경분은 커밋된 상태로 남아야 하고, 실패한 소스는 다음 갱신에서
재시도할 수 있어야 한다. 그래서 소스 단위로 커밋 경계를 나눈다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.models.document_meta import DocumentMeta
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.services.documents.document_source import DocumentSource, FileMeta

_LOG = get_logger("docs_mcp.documents.index")


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
            except IntegrationError as exc:
                # 부분 실패 허용: 이 소스만 롤백하고 앞선 소스의 커밋은 유지한다.
                self._session.rollback()
                _LOG.warning("문서 소스 갱신 실패(다음 갱신에서 재시도 가능): %s (%s)", name, exc)
                failed.append(name)
                continue
            totals.synced += counts.synced
            totals.added += counts.added
            totals.updated += counts.updated
            totals.removed += counts.removed

        if failed and len(failed) == len(targets):
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
            raise IntegrationError(
                "no document source is configured: set google drive or notion credentials"
            )
        if source is None:
            return self._sources
        targets = [s for s in self._sources if s.source_name == source]
        if not targets:
            raise IntegrationError(f"unknown or unconfigured document source: {source}")
        return targets

    def _refresh_source(self, document_source: DocumentSource) -> _SourceCounts:
        """소스 하나의 목록을 반영하고 성공 시에만 커밋한다."""
        source_name = document_source.source_name
        remote_files = document_source.list_files()
        existing = {m.external_id: m for m in self._meta_repo.list_by_source(source_name)}
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        counts = _SourceCounts(synced=len(remote_files))
        seen: set[str] = set()
        for meta in remote_files:
            if not meta.external_id or meta.external_id in seen:
                continue
            seen.add(meta.external_id)
            current = existing.get(meta.external_id)
            if current is None:
                self._meta_repo.add(_new_row(source_name, meta, now))
                counts.added += 1
            elif _apply_changes(current, meta, now):
                counts.updated += 1

        for external_id, row in existing.items():
            if external_id not in seen:
                self._meta_repo.delete(row)
                counts.removed += 1

        self._session.commit()
        _LOG.info(
            "메타 캐시 갱신 완료: source=%s synced=%d added=%d updated=%d removed=%d",
            source_name,
            counts.synced,
            counts.added,
            counts.updated,
            counts.removed,
        )
        return counts


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
