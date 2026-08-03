"""문서 소스 팩토리 단위 테스트 (SPEC 기능 5).

자격증명 조합에 따라 어떤 어댑터가 만들어지는지 검증한다. 어댑터 생성 자체는
네트워크를 타지 않으므로 실제 호출은 발생하지 않는다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.config import Settings
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION
from app.services.documents.source_factory import (
    build_drive_source,
    build_drive_token_provider,
    build_notion_source,
)


@pytest.fixture()
def bare_settings() -> Settings:
    """Drive/Notion 설정이 모두 비어 있는 기준 설정."""
    return replace(
        Settings(),
        drive_folder_id=None,
        drive_service_account_file=None,
        drive_service_account_json=None,
        notion_token=None,
        notion_database_id=None,
    )


def test_no_drive_credentials_yields_no_token_provider(bare_settings: Settings) -> None:
    """Drive 자격증명이 없으면 토큰 발급기가 만들어지지 않는다."""
    assert build_drive_token_provider(bare_settings) is None


def test_drive_token_provider_is_built_when_credentials_present(bare_settings: Settings) -> None:
    """Drive 자격증명(파일 또는 JSON)이 있으면 토큰 발급기가 만들어진다."""
    settings = replace(bare_settings, drive_service_account_file="/tmp/key.json")

    assert build_drive_token_provider(settings) is not None


def test_drive_source_requires_folder_id(bare_settings: Settings) -> None:
    """토큰 발급기가 있어도 folder_id 가 없으면 Drive 어댑터를 만들지 않는다."""
    settings = replace(bare_settings, drive_service_account_file="/tmp/key.json")
    token_provider = build_drive_token_provider(settings)

    assert build_drive_source(settings, "", token_provider) is None


def test_drive_source_requires_token_provider(bare_settings: Settings) -> None:
    """folder_id 가 있어도 토큰 발급기가 없으면 Drive 어댑터를 만들지 않는다."""
    assert build_drive_source(bare_settings, "folder-1", None) is None


def test_drive_source_is_built_when_fully_configured(bare_settings: Settings) -> None:
    """folder_id 와 토큰 발급기가 모두 있으면 Drive 어댑터가 만들어진다."""
    settings = replace(bare_settings, drive_service_account_file="/tmp/key.json")
    token_provider = build_drive_token_provider(settings)

    source = build_drive_source(settings, "folder-1", token_provider)

    assert source is not None
    assert source.source_name == SOURCE_DRIVE


def test_notion_source_is_built_from_token_only(bare_settings: Settings) -> None:
    """Notion 은 토큰만 있으면 어댑터가 만들어진다(database_id 는 선택)."""
    settings = replace(bare_settings, notion_token="secret-token")

    source = build_notion_source(settings, "db-1")

    assert source is not None
    assert source.source_name == SOURCE_NOTION


def test_notion_source_requires_token(bare_settings: Settings) -> None:
    """토큰이 없으면 Notion 어댑터를 만들지 않는다."""
    assert build_notion_source(bare_settings, "db-1") is None


def test_settings_read_document_source_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """DOCS_MCP_ 접두사 환경변수가 설정 필드로 읽힌다."""
    monkeypatch.setenv("DOCS_MCP_DRIVE_FOLDER_ID", "env-folder")
    monkeypatch.setenv("DOCS_MCP_NOTION_TOKEN", "env-token")
    monkeypatch.setenv("DOCS_MCP_NOTION_VERSION", "2025-01-01")

    settings = Settings()

    assert settings.drive_folder_id == "env-folder"
    assert settings.notion_token == "env-token"
    assert settings.notion_version == "2025-01-01"


def test_blank_env_var_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 문자열 환경변수는 미설정(None)으로 처리된다."""
    monkeypatch.setenv("DOCS_MCP_NOTION_TOKEN", "")

    assert Settings().notion_token is None
