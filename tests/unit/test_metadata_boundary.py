"""docs/architect-review/56 §1.3: LLM 호출 모듈만 요청 경로에서 격리한다.

55 §1 의 원래 취지는 "LLM 호출 비용/지연이 검색·색인 요청 경로로 새지 않게"다.
write-back 경로(`writeback_service`)와 순수 로직(`spec_payload`,`validation`)은
LLM 을 호출하지 않으므로 `app/mcp` 에서 import 해도 그 취지를 깨지 않는다.
검색/색인 계층은 여전히 metadata 패키지 전체를 참조하지 않는다.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: metadata 패키지 전체가 금지되는 디렉터리(검색·색인 경로).
_PACKAGE_FORBIDDEN_DIRS = ["app/services/search", "app/services/indexer"]

#: app/mcp 에서 금지되는 LLM 호출 모듈.
_LLM_MODULES = (
    "app.services.metadata.llm_client",
    "app.services.metadata.generator",
    "app.services.metadata.prompt",
)


def _imported_modules(path: Path) -> set[str]:
    """파일이 import 하는 모듈 이름 집합을 반환한다."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_search_and_indexer_do_not_import_metadata_package() -> None:
    offenders = []
    for rel_dir in _PACKAGE_FORBIDDEN_DIRS:
        for path in (_PROJECT_ROOT / rel_dir).rglob("*.py"):
            if any(m.startswith("app.services.metadata") for m in _imported_modules(path)):
                offenders.append(str(path.relative_to(_PROJECT_ROOT)))
    assert offenders == []


def test_mcp_does_not_import_llm_calling_modules() -> None:
    offenders = []
    for path in (_PROJECT_ROOT / "app/mcp").rglob("*.py"):
        if any(m.startswith(_LLM_MODULES) for m in _imported_modules(path)):
            offenders.append(str(path.relative_to(_PROJECT_ROOT)))
    assert offenders == []
