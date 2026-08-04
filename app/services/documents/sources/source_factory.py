"""folder_id/database_id로부터 문서 소스 어댑터를 구성하는 팩토리.

Drive/Notion 은 이제 project → folder_id/database_id 매핑으로부터 **요청
시점에** 어댑터를 만든다(SPEC 기능 5). 자격증명 자체는 서버 전역 1개를
공유하므로, 여기서는 "자격증명 + 특정 folder_id/database_id" 조합만 받아
어댑터 하나를 만드는 역할만 한다. 자격증명이 없으면 조용히 None 을
반환한다 — Drive 만 설정된 팀, Notion 만 설정된 팀, 둘 다 없는 팀 모두
서버 기동에는 실패하지 않아야 하기 때문이다.
"""

from __future__ import annotations

from app.core.config import Settings
from app.services.documents.sources.google_drive_source import (
    GoogleDriveSource,
    ServiceAccountTokenProvider,
)
from app.services.documents.sources.notion_source import NotionSource


def build_drive_token_provider(settings: Settings) -> ServiceAccountTokenProvider | None:
    """Drive 서비스 계정 자격증명으로 토큰 발급기를 만든다.

    자격증명이 없으면 None. 프로젝트마다 새로 만들지 않고 **재사용**해야
    한다(내부 credentials 캐싱이 중복되지 않도록 호출부가 1회만 호출).
    """
    has_credentials = bool(
        settings.drive_service_account_file or settings.drive_service_account_json
    )
    if not has_credentials:
        return None
    return ServiceAccountTokenProvider(
        service_account_file=settings.drive_service_account_file,
        service_account_json=settings.drive_service_account_json,
    )


def build_drive_source(
    settings: Settings,
    folder_id: str,
    token_provider: ServiceAccountTokenProvider | None,
) -> GoogleDriveSource | None:
    """folder_id 와 토큰 발급기가 모두 있을 때만 Drive 어댑터를 만든다."""
    if not folder_id or token_provider is None:
        return None
    return GoogleDriveSource(
        folder_id=folder_id,
        token_provider=token_provider,
        timeout_seconds=settings.document_source_timeout_seconds,
        max_chars=settings.document_fetch_max_chars,
    )


def build_notion_source(
    settings: Settings, notion_id: str, kind: str = "database"
) -> NotionSource | None:
    """Notion 토큰이 있을 때만 어댑터를 만든다.

    Args:
        notion_id: `kind="database"` 면 데이터베이스 ID, `kind="page"` 면
            허브 페이지 ID.
        kind: `"database"`(기본) 또는 `"page"`.
    """
    if not settings.notion_token:
        return None
    if kind == "page":
        return NotionSource(
            token=settings.notion_token,
            page_id=notion_id,
            notion_version=settings.notion_version,
            timeout_seconds=settings.document_source_timeout_seconds,
            max_chars=settings.document_fetch_max_chars,
        )
    return NotionSource(
        token=settings.notion_token,
        database_id=notion_id,
        notion_version=settings.notion_version,
        timeout_seconds=settings.document_source_timeout_seconds,
        max_chars=settings.document_fetch_max_chars,
    )
