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
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.services.documents.document_body_indexer import (
    deterministic_document_id,
    index_document_body,
)
from app.services.documents.sources.document_source import (
    NO_SOURCE_CONFIGURED_MESSAGE,
    DocumentSource,
    FileMeta,
)
from app.services.documents.project_source_resolver import ProjectSourceResolver
from app.services.indexer.indexer_service import IndexerService

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
    fetched_bodies: int = 0

    @property
    def total_changes(self) -> int:
        """커밋 경계 판정에 쓰는 실제 변경 건수(`synced` 는 조회 수라 제외).

        `fetched_bodies` 를 빼면, 메타가 안 바뀐 백필(added=updated=0)에서
        본문 fetch(빈 본문 스킵의 구 Document 삭제 포함, DB 쓰기가 있는 매
        fetch 성공)가 아무리 쌓여도 이 값이 0 이라 커밋 경계에 영영 도달하지
        못한다. 그러면 소스 하나를 끝까지 처리해야 커밋되고, 그 전에 문서
        1건이라도 실패하면 이미 처리한 본문까지 통째로 롤백된다.
        """
        return self.added + self.updated + self.removed + self.fetched_bodies


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
        document_repo: DocumentRepository | None = None,
        endpoint_repo: EndpointRepository | None = None,
        chunk_repo: ChunkRepository | None = None,
        indexer: IndexerService | None = None,
    ) -> None:
        """세션·저장소·프로젝트 소스 리졸버를 보관한다.

        Args:
            session: 커밋/롤백 경계를 소스 단위로 제어하기 위한 DB 세션.
            meta_repo: `document_meta` 저장소.
            resolver: project → Drive/Notion 어댑터 요청 시점 팩토리.
            document_repo: `refresh(index_bodies=True)` 에 필요한 `document`
                저장소(본문 색인 + 삭제 전파). 본문 색인을 쓰지 않으면 생략 가능.
            endpoint_repo: 본문 색인 재색인 시 기존 엔드포인트를 지우기 위한 저장소.
            chunk_repo: 본문 색인 시 청크 delete-and-insert 에 쓰는 저장소.
            indexer: 파싱된 본문을 청크로 만들어 저장하는 인덱서.
        """
        self._session = session
        self._meta_repo = meta_repo
        self._resolver = resolver
        self._document_repo = document_repo
        self._endpoint_repo = endpoint_repo
        self._chunk_repo = chunk_repo
        self._indexer = indexer

    def refresh(
        self,
        source: str | None = None,
        project: str | None = None,
        index_bodies: bool = False,
    ) -> RefreshResult:
        """등록된 프로젝트들의 문서 목록을 조회해 메타 캐시를 갱신한다.

        Args:
            source: 특정 소스(`drive`/`notion`)만 갱신할 때 지정. 생략하면 전체.
            project: 특정 프로젝트만 갱신할 때 지정. 생략하면 등록된 전 프로젝트.
            index_bodies: True 면 신규/변경된 문서의 본문을 fetch 해
                `document`/`chunk` 에 색인한다(옵트인, 비용이 크다). 원본에서
                삭제된 문서의 대응 `Document` 삭제(청크·벡터 CASCADE)는 이
                플래그와 무관하게 항상 수행된다.

        Returns:
            added/updated/removed/synced 집계와 실패한 "<project>/<source>" 목록.

        Raises:
            IntegrationError: 대상이 하나도 구성돼 있지 않거나, 갱신을 시도한
                모든 대상이 실패한 경우.
            ValueError: index_bodies=True 인데 필요한 저장소/인덱서가 주입되지
                않은 경우.
        """
        if index_bodies and (
            self._document_repo is None
            or self._endpoint_repo is None
            or self._chunk_repo is None
            or self._indexer is None
        ):
            raise ValueError(
                "index_bodies=True requires document_repo/endpoint_repo/chunk_repo/indexer"
            )
        if not index_bodies:
            # 검색 기본 전략은 indexed(3-arm RRF)라 본문 색인 없이는 keyword/
            # vector arm 이 비어 title 매칭만으로 조용히 퇴화한다(45번 리뷰 §3.4).
            _LOG.warning(
                "index_bodies=False — 본문 색인을 건너뜀: 검색은 제목 매칭만 사용"
            )
        targets = self._resolve_targets(source, project)

        totals = _SourceCounts()
        failed: list[str] = []
        for target_project, document_source in targets:
            label = f"{target_project}/{document_source.source_name}"
            try:
                counts = self._refresh_source(target_project, document_source, index_bodies)
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
        self, project: str, document_source: DocumentSource, index_bodies: bool
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
                self._stage_upsert(
                    project, source_name, meta, existing, now, pending, document_source, index_bodies
                )
                if pending.total_changes >= BATCH_SIZE:
                    pending = self._commit_batch(committed, pending)

            for external_id, row in existing.items():
                if external_id not in seen:
                    self._delete_removed(row)
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
            "메타 캐시 갱신 완료: project=%s source=%s synced=%d added=%d updated=%d "
            "removed=%d fetched_bodies=%d",
            project,
            source_name,
            committed.synced,
            committed.added,
            committed.updated,
            committed.removed,
            committed.fetched_bodies,
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
        document_source: DocumentSource,
        index_bodies: bool,
    ) -> None:
        """문서 한 건의 신규 생성/갱신을 세션에 올리고 미커밋 집계에 반영한다."""
        current = existing.get(meta.external_id)
        if current is None:
            row = _new_row(project, source_name, meta, now)
            self._meta_repo.add(row)
            pending.added += 1
            needs_body_index = True
        else:
            row = current
            needs_body_index = _apply_changes(current, meta, now)
            if needs_body_index:
                pending.updated += 1

        # 문서 7번 게이트: 메타 변경 또는 본문 미색인(document_id NULL)이 아니면
        # fetch 자체를 건너뛴다. content_hash 2차 게이트는 fetch 한 문서에 한해
        # index_document_body 내부에서 처리한다. document_id NULL 은 "본문 미색인"
        # 신호이므로, 색인 성공 시 채워지는 document_id 가 다음 실행부터 이 조건을
        # 자동으로 다시 좁힌다(비용 자기 종료, doc36 §6-2 백필 회귀 수정).
        if index_bodies and (needs_body_index or row.document_id is None):
            if self._index_body(project, source_name, meta, document_source, row):
                pending.fetched_bodies += 1

    def _index_body(
        self,
        project: str,
        source_name: str,
        meta: FileMeta,
        document_source: DocumentSource,
        row: DocumentMeta,
    ) -> bool:
        """문서 한 건의 본문을 fetch 해 색인하고 메타 행에 document_id 를 기록한다.

        fetch 실패는 이 문서만 건너뛰고 로그로만 남긴다. fetch 는 DB 쓰기
        이전 지점이라 실패를 삼켜도 반쯤 쓰인 상태가 커밋되지 않는다(반대로
        `index_document_body` 내부 실패는 절대 여기서 감싸면 안 된다 — 청크
        삭제/삽입이 이미 세션에 올라간 뒤라 다음 `_commit_batch` 에 반쯤 쓰인
        상태로 실려 커밋된다). document_id 가 NULL 로 남으므로 다음 실행에서
        자동 재시도된다.

        본문이 공백뿐이면 오류가 아니라 정상적인 "색인할 게 없음"이므로
        `index_document_body`(가 던질 `ParserError("empty document")`) 를
        타기 전에 선검사로 건너뛴다. 이전에 색인된 적이 있었다면(원문이
        빈 문서로 바뀐 경우) 그 `Document` 를 지워 옛 스니펫이 검색에 계속
        나가는 걸 막는다.

        Returns:
            fetch 성공 여부(색인/스킵/빈 문서 처리 결과와 무관). 셋 다 DB
            쓰기를 동반하므로, 커밋 경계 카운터(`pending.fetched_bodies`)에
            반영할지 호출자가 판단하는 데 쓴다. "색인 건수"가 아니다 — 빈
            본문 스킵도 True 를 반환한다.
        """
        assert self._document_repo is not None
        assert self._endpoint_repo is not None
        assert self._chunk_repo is not None
        assert self._indexer is not None
        try:
            fetched = document_source.fetch(meta.external_id)
        except IntegrationError as exc:
            _LOG.warning(
                "본문 fetch 실패(다음 갱신에서 재시도 가능): project=%s source=%s "
                "external_id=%s (%s)",
                project,
                source_name,
                meta.external_id,
                exc,
            )
            return False

        document_id = deterministic_document_id(project, source_name, meta.external_id)
        if not fetched.text.strip():
            _LOG.warning(
                "본문이 비어 있어 색인을 건너뜀: project=%s source=%s external_id=%s",
                project,
                source_name,
                meta.external_id,
            )
            document = self._document_repo.get(document_id)
            if document is not None:
                self._document_repo.delete(document)
            row.document_id = None
            return True

        index_document_body(
            self._session,
            self._document_repo,
            self._endpoint_repo,
            self._chunk_repo,
            self._indexer,
            project=project,
            source_name=source_name,
            external_id=meta.external_id,
            title=meta.title,
            raw=fetched.text,
        )
        row.document_id = document_id
        return True

    def _delete_removed(self, row: DocumentMeta) -> None:
        """원본에서 사라진 메타 행과, 본문이 색인돼 있었다면 대응 Document 도 지운다.

        Document 삭제는 청크·벡터를 CASCADE 로 함께 지운다. `document_id` 가
        NULL(본문 색인 전)이면 메타 행만 지운다.
        """
        if row.document_id is not None and self._document_repo is not None:
            document = self._document_repo.get(row.document_id)
            if document is not None:
                self._document_repo.delete(document)
        self._meta_repo.delete(row)

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
        committed.fetched_bodies += pending.fetched_bodies
        return _SourceCounts()


def _merge_counts(totals: _SourceCounts, counts: _SourceCounts) -> None:
    """소스 하나의 집계를 전체 집계에 누적한다."""
    totals.synced += counts.synced
    totals.added += counts.added
    totals.updated += counts.updated
    totals.removed += counts.removed
    totals.fetched_bodies += counts.fetched_bodies


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
        mime_type=meta.mime_type,
        created_at=meta.created_at,
        owner=meta.owner,
    )


def _apply_changes(row: DocumentMeta, meta: FileMeta, now: datetime) -> bool:
    """기존 행에 변경분을 반영하고 실제로 바뀌었는지 여부를 반환한다.

    `modified_at`·제목·URL 이 모두 같으면 `last_synced_at` 만 갱신하고 `updated`
    집계에는 넣지 않는다(SPEC 검증 기준: 수정 시각이 같으면 updated 아님).

    `mime_type`/`created_at`/`owner` 는 `is_changed` 판정에 절대 넣지 말 것. 이 반환값은
    `_stage_upsert` 에서 `needs_body_index` 로 쓰여 본문 재fetch·재색인을 트리거한다.
    새 nullable 컬럼을 판정에 넣으면 백필 첫 실행에서 전 문서가 NULL → 값 으로 바뀌며
    `updated` 로 잡혀 본문을 전량 다시 받는다(rate limit·시간 폭발). 항상 대입만 하면
    백필은 UPDATE 한 번으로 끝난다 - 컬럼을 더 추가할 때도 이 규약을 지킬 것.
    """
    row.last_synced_at = now
    row.mime_type = meta.mime_type
    row.created_at = meta.created_at
    row.owner = meta.owner
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
