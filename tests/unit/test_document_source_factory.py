"""문서 소스 팩토리 단위 테스트 (SPEC 기능 5 구성 부분).

자격증명 조합에 따라 어떤 어댑터가 만들어지는지 검증한다. 어댑터 생성 자체는
네트워크를 타지 않으므로 실제 호출은 발생하지 않는다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.config import Settings
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION
from app.services.documents.source_factory import build_document_sources


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


def test_no_credentials_yields_no_sources(bare_settings: Settings) -> None:
    """자격증명이 전혀 없으면 소스가 하나도 만들어지지 않는다(기동은 성공)."""
    assert build_document_sources(bare_settings) == {}


def test_drive_requires_both_folder_and_credentials(bare_settings: Settings) -> None:
    """폴더 ID 만 있고 자격증명이 없으면 Drive 어댑터를 만들지 않는다."""
    settings = replace(bare_settings, drive_folder_id="folder-1")

    assert SOURCE_DRIVE not in build_document_sources(settings)


def test_drive_credentials_without_folder_is_skipped(bare_settings: Settings) -> None:
    """자격증명만 있고 폴더 ID 가 없으면 Drive 어댑터를 만들지 않는다."""
    settings = replace(bare_settings, drive_service_account_file="/tmp/key.json")

    assert SOURCE_DRIVE not in build_document_sources(settings)


def test_drive_is_built_when_fully_configured(bare_settings: Settings) -> None:
    """폴더 ID 와 자격증명이 모두 있으면 Drive 어댑터가 만들어진다."""
    settings = replace(
        bare_settings,
        drive_folder_id="folder-1",
        drive_service_account_file="/tmp/key.json",
    )

    sources = build_document_sources(settings)

    assert set(sources) == {SOURCE_DRIVE}
    assert sources[SOURCE_DRIVE].source_name == SOURCE_DRIVE


def test_notion_is_built_from_token_only(bare_settings: Settings) -> None:
    """Notion 은 토큰만 있으면 어댑터가 만들어진다(DB ID 는 선택)."""
    settings = replace(bare_settings, notion_token="secret-token")

    assert set(build_document_sources(settings)) == {SOURCE_NOTION}


def test_both_sources_are_built(bare_settings: Settings) -> None:
    """Drive/Notion 이 모두 설정되면 두 어댑터가 모두 만들어진다."""
    settings = replace(
        bare_settings,
        drive_folder_id="folder-1",
        drive_service_account_json='{"type": "service_account"}',
        notion_token="secret-token",
    )

    assert set(build_document_sources(settings)) == {SOURCE_DRIVE, SOURCE_NOTION}


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
