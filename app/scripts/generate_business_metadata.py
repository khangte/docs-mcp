"""엔드포인트 비즈니스 메타데이터 생성 배치 (LLM 호출, 원샷 CLI).

docs/architect-review/55 §4: `refresh_documents.py` 와 같은 얇은 CLI 구조
(argparse → bootstrap_app_state → 서비스 함수 호출 → 종료코드). 재생성
여부는 `generator.select_targets` 의 skip 규칙이 곧 증분 실행이라 이
스크립트에 별도 모드가 없다.

실행:
    uv run python -m app.scripts.generate_business_metadata
        [--document-id ID ...] [--project PROJECT] [--force] [--limit N]
        [--dry-run] [--concurrency N] [--model MODEL]

종료코드(refresh_documents.py 와 같은 규약):
    1 -- 전 대상 실패(대상이 있었는데 전부 실패)
    0 -- 부분 실패/정상/대상 없음

`--reindex` 는 없다 — 완료 로그에 다음 명령(`refresh_documents.py
--include-registered`)만 안내한다. 재색인은 사람이 별도로 판단해 돌린다.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from app.bootstrap import bootstrap_app_state
from app.composition import build_services
from app.core.config import get_settings
from app.services.metadata.generator import GenerationSummary, generate_business_metadata
from app.services.metadata.llm_client import AnthropicClient

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """55 §4 플래그: 스코프(document-id/project), 실행 제어(force/limit/dry-run),
    성능(concurrency), 모델 선택(model)."""
    parser = argparse.ArgumentParser(description="엔드포인트 비즈니스 메타데이터 LLM 생성 배치")
    parser.add_argument("--document-id", action="append", default=None, dest="document_ids")
    parser.add_argument("--project", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--model", default=None)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    """API 키/모델을 먼저 검증(fail-fast)한 뒤 생성 배치를 실행한다."""
    settings = get_settings()

    if not settings.metadata_api_key:
        logger.error(
            "DOCS_MCP_ANTHROPIC_API_KEY(또는 ANTHROPIC_API_KEY)가 설정되지 않음"
        )
        return EXIT_FAILED

    model = args.model or settings.metadata_model
    if not model:
        logger.error("--model 또는 DOCS_MCP_METADATA_MODEL 중 하나는 반드시 필요함")
        return EXIT_FAILED

    llm_client = AnthropicClient(
        api_key=settings.metadata_api_key,
        model=model,
        api_base=settings.metadata_api_base,
    )

    state = bootstrap_app_state(settings)
    bundle_iter = build_services(state)
    bundle = next(bundle_iter)
    try:
        summary = generate_business_metadata(
            bundle.session,
            llm_client,
            document_ids=args.document_ids,
            project=args.project,
            force=args.force,
            limit=args.limit,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
        )
    finally:
        try:
            next(bundle_iter)
        except StopIteration:
            pass

    if summary.failed:
        logger.warning("생성 실패 %d건: %s", len(summary.failed), ", ".join(summary.failed))

    logger.info(
        "생성 완료: total=%d generated=%d failed=%d",
        summary.total,
        summary.generated,
        len(summary.failed),
    )
    if not args.dry_run and summary.generated > 0:
        logger.info(
            "색인에 반영하려면: uv run python -m app.scripts.refresh_documents --include-registered"
        )

    return _exit_code(summary, dry_run=args.dry_run)


def _exit_code(summary: GenerationSummary, *, dry_run: bool) -> int:
    """dry-run 은 항상 성공 취급 - 대상 확인 절차가 실패로 보이면 안 된다."""
    if not dry_run and summary.total > 0 and summary.generated == 0:
        return EXIT_FAILED
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """설정을 로드해 인자를 파싱하고 배치를 실행한다."""
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
