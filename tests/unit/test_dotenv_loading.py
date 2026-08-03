"""`.env` 파일 로드 회귀 테스트.

`app.core.config` 모듈은 import 시점에 `load_dotenv(override=False)` 를 1회
호출한다. 여기서는 그 동작 자체(값을 읽어옴, 기존 env 를 덮지 않음)를
`load_dotenv` 를 직접 호출해 검증한다 — 모듈 재-import 로 부작용을 재현하는
대신, 실제 프로덕션 코드가 쓰는 것과 동일한 `override=False` 계약을
독립적으로 확인한다.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from app.core.config import Settings


def test_load_dotenv_populates_environment_and_settings_reads_it(
    tmp_path: Path, monkeypatch
) -> None:
    """.env 파일의 값이 로드되어 Settings 가 그 값을 읽는다."""
    monkeypatch.delenv("DOCS_MCP_NOTION_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DOCS_MCP_NOTION_TOKEN=from-dotenv-file\n")

    load_dotenv(env_file, override=False)

    assert Settings().notion_token == "from-dotenv-file"


def test_load_dotenv_does_not_override_existing_env_value(
    tmp_path: Path, monkeypatch
) -> None:
    """이미 프로세스 환경(셸/MCP env 블록)에 있던 값은 .env 파일로 덮이지 않는다."""
    monkeypatch.setenv("DOCS_MCP_NOTION_TOKEN", "from-shell-env")
    env_file = tmp_path / ".env"
    env_file.write_text("DOCS_MCP_NOTION_TOKEN=from-dotenv-file\n")

    load_dotenv(env_file, override=False)

    assert Settings().notion_token == "from-shell-env"
