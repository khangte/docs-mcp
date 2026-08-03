"""애플리케이션 설정 (환경변수 + 기본값).

외부 라이브러리(pydantic-settings) 에 의존하지 않기 위해 dataclass 로 작성.
값 읽기 진입점을 1곳으로 제한해 하드코딩을 방지한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    """서비스 런타임 설정.

    환경변수가 있으면 그 값을, 아니면 기본값을 사용한다.
    """

    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DOCS_MCP_DATABASE_URL",
            "postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp",
        )
    )
    embedding_dim: int = field(
        default_factory=lambda: int(os.environ.get("DOCS_MCP_EMBEDDING_DIM", "256"))
    )
    hybrid_alpha: float = field(
        default_factory=lambda: float(os.environ.get("DOCS_MCP_HYBRID_ALPHA", "0.4"))
    )
    log_level: str = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_LOG_LEVEL", "INFO")
    )
    gemini_api_key: str | None = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_GEMINI_API_KEY") or None
    )
    gemini_embedding_model: str = field(
        default_factory=lambda: os.environ.get(
            "DOCS_MCP_GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"
        )
    )
    # --- Google Drive (문서 검색 소스) ---
    drive_folder_id: str | None = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_DRIVE_FOLDER_ID") or None
    )
    drive_service_account_file: str | None = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_DRIVE_SERVICE_ACCOUNT_FILE") or None
    )
    drive_service_account_json: str | None = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_DRIVE_SERVICE_ACCOUNT_JSON") or None
    )
    # --- Notion (문서 검색 소스) ---
    notion_token: str | None = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_NOTION_TOKEN") or None
    )
    notion_database_id: str | None = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_NOTION_DATABASE_ID") or None
    )
    notion_version: str = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_NOTION_VERSION", "2022-06-28")
    )
    # --- 문서 소스 공통 ---
    document_source_timeout_seconds: float = field(
        default_factory=lambda: float(
            os.environ.get("DOCS_MCP_DOCUMENT_SOURCE_TIMEOUT_SECONDS", "15.0")
        )
    )
    document_fetch_max_chars: int = field(
        default_factory=lambda: int(
            os.environ.get("DOCS_MCP_DOCUMENT_FETCH_MAX_CHARS", "200000")
        )
    )


def get_settings() -> Settings:
    """테스트/주입 가능하도록 팩토리로 제공."""
    return Settings()
