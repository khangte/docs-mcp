"""설정값으로부터 문서 소스 어댑터를 구성하는 팩토리.

자격증명이 없는 소스는 조용히 제외한다. Drive 만 설정된 팀, Notion 만 설정된
팀, 둘 다 없는 팀 모두 서버 기동에는 실패하지 않아야 하기 때문이다. 대신
소스가 하나도 없는 상태에서 관련 도구를 호출하면 `IntegrationError` 로
"미구성"임을 명확히 알린다.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.documents.document_source import DocumentSource
from app.services.documents.google_drive_source import (
    GoogleDriveSource,
    ServiceAccountTokenProvider,
)
from app.services.documents.notion_source import NotionSource

_LOG = get_logger("docs_mcp.documents.factory")


def build_document_sources(settings: Settings) -> dict[str, DocumentSource]:
    """설정에서 사용 가능한 문서 소스 어댑터만 골라 매핑으로 반환한다.

    Args:
        settings: 런타임 설정.

    Returns:
        `drive`/`notion` → 어댑터 매핑. 구성된 소스가 없으면 빈 dict.
    """
    sources: dict[str, DocumentSource] = {}
    drive = _build_drive(settings)
    if drive is not None:
        sources[drive.source_name] = drive
    notion = _build_notion(settings)
    if notion is not None:
        sources[notion.source_name] = notion

    if not sources:
        _LOG.info("문서 소스(Drive/Notion) 자격증명이 없어 협업 문서 검색이 비활성화됨")
    return sources


def _build_drive(settings: Settings) -> GoogleDriveSource | None:
    """Drive 설정이 모두 갖춰졌을 때만 어댑터를 만든다."""
    has_credentials = bool(
        settings.drive_service_account_file or settings.drive_service_account_json
    )
    if not settings.drive_folder_id or not has_credentials:
        return None
    token_provider = ServiceAccountTokenProvider(
        service_account_file=settings.drive_service_account_file,
        service_account_json=settings.drive_service_account_json,
    )
    return GoogleDriveSource(
        folder_id=settings.drive_folder_id,
        token_provider=token_provider,
        timeout_seconds=settings.document_source_timeout_seconds,
        max_chars=settings.document_fetch_max_chars,
    )


def _build_notion(settings: Settings) -> NotionSource | None:
    """Notion 토큰이 있을 때만 어댑터를 만든다."""
    if not settings.notion_token:
        return None
    return NotionSource(
        token=settings.notion_token,
        database_id=settings.notion_database_id,
        notion_version=settings.notion_version,
        timeout_seconds=settings.document_source_timeout_seconds,
        max_chars=settings.document_fetch_max_chars,
    )
