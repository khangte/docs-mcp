"""generate_business_metadata.py CLI 종료코드 단위 테스트."""

from __future__ import annotations

from app.scripts.generate_business_metadata import EXIT_FAILED, EXIT_OK, _exit_code
from app.services.metadata.generator import GenerationSummary


def test_dry_run_with_targets_is_not_failure() -> None:
    """reviewer 지적: dry-run은 LLM 호출을 안 해 generated=0 이 항상 나오므로
    dry_run=True 일 때는 total>0 이어도 실패로 보면 안 된다."""
    summary = GenerationSummary(total=5, generated=0, failed=[])
    assert _exit_code(summary, dry_run=True) == EXIT_OK


def test_real_run_all_targets_failed_is_failure() -> None:
    summary = GenerationSummary(total=3, generated=0, failed=["a", "b", "c"])
    assert _exit_code(summary, dry_run=False) == EXIT_FAILED


def test_real_run_partial_success_is_ok() -> None:
    summary = GenerationSummary(total=3, generated=1, failed=["a", "b"])
    assert _exit_code(summary, dry_run=False) == EXIT_OK


def test_no_targets_is_ok() -> None:
    summary = GenerationSummary(total=0, generated=0, failed=[])
    assert _exit_code(summary, dry_run=False) == EXIT_OK
