"""문서 소스 주기 동기화 배치 진입점 (서버 밖 상주 러너).

MCP stdio 서버 프로세스는 클라이언트가 세션마다 띄우는 단명 프로세스라 그
안에 크론을 두면 "주기 실행" 계약을 지킬 수 없다
(`docs/architect-review/32-refresh-index-batch-automation.md` §0). 이
스크립트는 한 번 돌고 종료하는 원샷 CLI이고, 주기는 OS 스케줄러
(systemd timer/cron)가 소유한다. `refresh_index` MCP 도구와 같은 서비스
함수를 호출하므로 즉시 실행 경로(도구)와 정기 실행 경로(이 스크립트)의
동작이 항상 일치한다.

실행:
    uv run python -m app.scripts.refresh_documents [--source drive|notion]
        [--project PROJECT] [--include-registered] [--force]

종료코드(§4.1):
    1 -- 전 대상 실패(`document_index_service.refresh` 가 IntegrationError)
    0 -- 부분 실패/정상/락 미획득(이미 실행 중)

중복 실행 방지는 Postgres advisory lock 을 쓴다(§3.3). 새 의존성 없이 이미
있는 DB 인프라로 해결되고, 세션 종료(프로세스 종료) 시 자동 해제되므로
명시적 unlock 이 필요 없다. 축 A(메타 동기화)와 축 B(등록 문서 재색인)는
비용·주기가 달라(1시간 vs 1일) 같은 락 키를 쓰면 무거운 축 B 가 가벼운 축
A 의 주기를 막는다 — `--include-registered` 여부로 락 키를 가른다.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.bootstrap import bootstrap_app_state
from app.composition import ServiceBundle, build_services
from app.core.errors import IntegrationError
from app.services.documents.registered_resync import resync_registered_documents

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1

# 임의 고정값. 이 저장소 다른 곳에서 advisory lock 키로 쓰지 않는다.
_LOCK_KEY_META_SYNC = 733100501
_LOCK_KEY_REGISTERED_RESYNC = 733100502


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """MCP 도구 `refresh_index` 와 같은 의미의 인자 5종을 파싱한다."""
    parser = argparse.ArgumentParser(
        description="문서 소스 메타 캐시(+선택적 등록 문서) 동기화 배치"
    )
    parser.add_argument("--source", choices=("drive", "notion"), default=None)
    parser.add_argument("--project", default=None)
    parser.add_argument("--include-registered", action="store_true")
    parser.add_argument("--force", action="store_true")
    # 기본 검색 전략이 indexed 라 본문 색인이 꺼지면 제목 매칭만 남는다.
    # 기본을 켜고 `--no-index-bodies` 로만 끌 수 있게 한다.
    parser.add_argument(
        "--index-bodies",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser.parse_args(argv)


def _select_lock_key(include_registered: bool) -> int:
    """`--include-registered` 여부로 축 A/B 락 키를 가른다(§3.3)."""
    return _LOCK_KEY_REGISTERED_RESYNC if include_registered else _LOCK_KEY_META_SYNC


def _try_advisory_lock(session: Session, lock_key: int) -> bool:
    """세션 단위 advisory lock 획득을 시도한다. 세션(프로세스) 종료 시 자동 해제된다."""
    return bool(
        session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar()
    )


def _execute(
    bundle: ServiceBundle, args: argparse.Namespace, *, lock_acquire: Callable[[], bool]
) -> int:
    """advisory lock 을 시도한 뒤 동기화를 수행하고 종료코드를 반환한다(§4.1)."""
    if not lock_acquire():
        logger.info("배치가 이미 실행 중이라 건너뜀(advisory lock 획득 실패)")
        return EXIT_OK

    try:
        refresh_result = bundle.document_index_service.refresh(
            source=args.source, project=args.project, index_bodies=args.index_bodies
        )
    except IntegrationError as exc:
        logger.error("문서 소스 갱신 전체 실패: %s", exc)
        return EXIT_FAILED

    registered_result = None
    if args.include_registered:
        registered_result = resync_registered_documents(
            bundle.session, bundle.document_repo, bundle.sync_service,
            project=args.project, force=args.force,
        )

    if refresh_result.failed_sources:
        logger.warning("일부 소스 갱신 실패: %s", ", ".join(refresh_result.failed_sources))

    registered_summary = ""
    if registered_result is not None:
        registered_summary = (
            f" registered(total={registered_result['total']}, "
            f"reindexed={registered_result['reindexed']}, "
            f"skipped={registered_result['skipped']}, "
            f"failed={len(registered_result['failed'])})"
        )
    logger.info(
        "동기화 완료: synced=%d added=%d updated=%d removed=%d%s",
        refresh_result.synced,
        refresh_result.added,
        refresh_result.updated,
        refresh_result.removed,
        registered_summary,
    )
    return EXIT_OK


def run(args: argparse.Namespace) -> int:
    """1회 배치를 실행하고 종료코드를 반환한다."""
    state = bootstrap_app_state()
    bundle_iter = build_services(state)
    bundle = next(bundle_iter)
    try:
        lock_key = _select_lock_key(args.include_registered)
        return _execute(
            bundle, args, lock_acquire=lambda: _try_advisory_lock(bundle.session, lock_key)
        )
    finally:
        try:
            next(bundle_iter)
        except StopIteration:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    """설정을 로드해 인자를 파싱하고 배치를 실행한다."""
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
