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
    0 -- 부분 실패/정상
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from app.bootstrap import bootstrap_app_state
from app.composition import build_services
from app.core.errors import IntegrationError
from app.services.documents.registered_resync import resync_registered_documents

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """MCP 도구 `refresh_index` 와 같은 의미의 인자 4종을 파싱한다."""
    parser = argparse.ArgumentParser(
        description="문서 소스 메타 캐시(+선택적 등록 문서) 동기화 배치"
    )
    parser.add_argument("--source", choices=("drive", "notion"), default=None)
    parser.add_argument("--project", default=None)
    parser.add_argument("--include-registered", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    """1회 배치를 실행하고 종료코드를 반환한다."""
    state = bootstrap_app_state()
    bundle_iter = build_services(state)
    bundle = next(bundle_iter)
    try:
        try:
            refresh_result = bundle.document_index_service.refresh(
                source=args.source, project=args.project
            )
        except IntegrationError as exc:
            logger.error("문서 소스 갱신 전체 실패: %s", exc)
            return EXIT_FAILED

        registered_result = None
        if args.include_registered:
            registered_result = resync_registered_documents(
                bundle, project=args.project, force=args.force
            )

        if refresh_result.failed_sources:
            logger.warning(
                "일부 소스 갱신 실패: %s", ", ".join(refresh_result.failed_sources)
            )

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
