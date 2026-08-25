"""docs/architect-review/55 §1: app.services.metadata 는 검색/색인 경로에서 참조되지 않는다.

LLM 호출 비용/지연이 검색·색인 요청 경로로 새어들지 않게, import 경계를
사람 리뷰가 아니라 테스트로 강제한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FORBIDDEN_DIRS = ["app/mcp", "app/services/search", "app/services/indexer"]


def _imports_metadata_package(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.startswith("app.services.metadata") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("app.services.metadata"):
                return True
    return False


def test_metadata_package_not_imported_from_search_or_indexer_or_mcp() -> None:
    offenders = []
    for rel_dir in _FORBIDDEN_DIRS:
        for path in (_PROJECT_ROOT / rel_dir).rglob("*.py"):
            if _imports_metadata_package(path):
                offenders.append(str(path.relative_to(_PROJECT_ROOT)))
    assert offenders == []
