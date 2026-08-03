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

프로젝트 확장(SPEC 기능 6)의 핵심 위험은 **교차 프로젝트 삭제**다. 여러
프로젝트가 같은 `source_name`(`drive`/`notion`)을 공유하므로, 삭제 감지 시
"이 프로젝트의 이 소스" 기존 행 집합(`list_by_project_source`)만 기준으로
삼아야 다른 프로젝트 행이 "원본에서 사라진 것"으로 오인되지 않는다.
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
from app.services.documents.project_source_resolver import ProjectSourceResolver

_LOG = get_logger("docs_mcp.documents.index")

#: 커밋 경계. 이 건수만큼 변경이 쌓일 때마다 커밋해, 도중에 실패해도 직전
#: 배치까지는 DB 에 남게 한다(SPEC 기능 6 "부분 실패 허용").
BATCH_SIZE = 100

#: project 가 지정됐지만 그 project 에 Drive/Notion 매핑이 하나도 없을 때의
#: 메시지. NO_SOURCE_CONFIGURED_MESSAGE(서버 전역 미구성)와 구별해, 호출자가
#: "서버에 소스가 아예 없음"과 "이 프로젝트만 미구성"을 나눠 볼 수 있게 한다.
_NO_PROJECT_SOURCE_MESSAGE_TEMPLATE = (
    "no document source is configured for project: {project}"
)


@dataclass(frozen=True)
class RefreshResult:
    """메타 캐시 갱신 결과 집계.

    Attributes:
        synced: 소스에서 조회한 전체 문서 수.
        added: 새로 생성된 메타 행 수.
        updated: `modified_at`/제목/URL 이 실제로 바뀌어 갱신된 행 수.
        removed: 소스에서 사라져 캐시에서 제거된 행 수.
        failed_sources: 갱신에 실패한 "<project>/<source>" 목록(부분 실패 허용).
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
    """등록된 프로젝트별 문서 소스들의 메타데이터를 `document_meta` 에 동기화한다."""

    def __init__(
        self,
        session: Session,
        meta_repo: DocumentMetaRepository,
        resolver: ProjectSourceResolver,
    ) -> None:
        """세션·저장소·프로젝트 소스 리졸버를 보관한다.

        Args:
            session: 커밋/롤백 경계를 소스 단위로 제어하기 위한 DB 세션.
            meta_repo: `document_meta` 저장소.
            resolver: project → Drive/Notion 어댑터 요청 시점 팩토리.
        """
        self._session = session
        self._meta_repo = meta_repo
        self._resolver = resolver

    def refresh(
        self, source: str | None = None, project: str | None = None
    ) -> RefreshResult:
        """등록된 프로젝트들의 문서 목록을 조회해 메타 캐시를 갱신한다.

        Args:
            source: 특정 소스(`drive`/`notion`)만 갱신할 때 지정. 생략하면 전체.
            project: 특정 프로젝트만 갱신할 때 지정. 생략하면 등록된 전 프로젝트.

        Returns:
            added/updated/removed/synced 집계와 실패한 "<project>/<source>" 목록.

        Raises:
            IntegrationError: 대상이 하나도 구성돼 있지 않거나, 갱신을 시도한
                모든 대상이 실패한 경우.
        """
        targets = self._resolve_targets(source, project)

        totals = _SourceCounts()
        failed: list[str] = []
        for target_project, document_source in targets:
            label = f"{target_project}/{document_source.source_name}"
            try:
                counts = self._refresh_source(target_project, document_source)
            except _PartialRefreshError as exc:
                # 부분 실패 허용: 실패한 소스라도 이미 커밋된 배치는 집계에 넣는다.
                # (`_refresh_source` 가 미커밋 배치만 롤백하고 확정분을 실어 보낸다)
                _merge_counts(totals, exc.committed)
                _LOG.warning("문서 소스 갱신 실패(다음 갱신에서 재시도 가능): %s (%s)", label, exc)
                failed.append(label)
                continue
            _merge_counts(totals, counts)

        # 모든 대상이 실패했고 커밋된 변경도 전혀 없으면 "조용한 무동작"이 되므로
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

    def _resolve_targets(
        self, source: str | None, project: str | None
    ) -> list[tuple[str, DocumentSource]]:
        """갱신 대상 (project, source) 쌍 목록을 결정하고 비어 있으면 예외를 던진다.

        `resolver.resolve_all()` 로 등록된 전 프로젝트의 (project, source)
        쌍을 얻은 뒤 `project`/`source` 인자로 필터한다(ARCH_REVIEW R2:
        source_name 단일 매칭이 아니라 project 축까지 함께 순회해야 여러
        프로젝트를 대상으로 갱신할 수 있다).
        """
        all_pairs = self._resolver.resolve_all()
        if project is not None:
            all_pairs = [(p, s) for p, s in all_pairs if p == project]
            if not all_pairs:
                raise IntegrationError(
                    _NO_PROJECT_SOURCE_MESSAGE_TEMPLATE.format(project=project)
                )
        elif not all_pairs:
            raise IntegrationError(NO_SOURCE_CONFIGURED_MESSAGE)

        if source is None:
            return all_pairs
        targets = [(p, s) for p, s in all_pairs if s.source_name == source]
        if not targets:
            raise IntegrationError(f"unknown or unconfigured document source: {source}")
        return targets

    def _refresh_source(
        self, project: str, document_source: DocumentSource
    ) -> _SourceCounts:
        """프로젝트 하나·소스 하나의 목록을 배치 단위로 커밋하며 반영한다.

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
        # project 로 좁힌 기존 행만 삭제 감지 기준 집합으로 쓴다. 다른
        # 프로젝트가 같은 source_name 을 쓰더라도 그 행은 여기 섞이지 않는다.
        existing = {
            m.external_id: m
            for m in self._meta_repo.list_by_project_source(project, source_name)
        }
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        committed = _SourceCounts(synced=len(remote_files))
        pending = _SourceCounts()
        seen: set[str] = set()
        try:
            for meta in remote_files:
                if not meta.external_id or meta.external_id in seen:
                    continue
                seen.add(meta.external_id)
                self._stage_upsert(project, source_name, meta, existing, now, pending)
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
                "메타 캐시 갱신 중단(직전 배치까지 보존): project=%s source=%s "
                "added=%d updated=%d removed=%d",
                project,
                source_name,
                committed.added,
                committed.updated,
                committed.removed,
            )
            raise _PartialRefreshError(exc, committed) from exc

        self._commit_batch(committed, pending)
        _LOG.info(
            "메타 캐시 갱신 완료: project=%s source=%s synced=%d added=%d updated=%d removed=%d",
            project,
            source_name,
            committed.synced,
            committed.added,
            committed.updated,
            committed.removed,
        )
        return committed

    def _stage_upsert(
        self,
        project: str,
        source_name: str,
        meta: FileMeta,
        existing: dict[str, DocumentMeta],
        now: datetime,
        pending: _SourceCounts,
    ) -> None:
        """문서 한 건의 신규 생성/갱신을 세션에 올리고 미커밋 집계에 반영한다."""
        current = existing.get(meta.external_id)
        if current is None:
            self._meta_repo.add(_new_row(project, source_name, meta, now))
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


def _new_row(project: str, source_name: str, meta: FileMeta, now: datetime) -> DocumentMeta:
    """FileMeta 로부터 신규 `document_meta` 행을 만든다."""
    return DocumentMeta(
        project=project,
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
