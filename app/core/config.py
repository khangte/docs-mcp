"""애플리케이션 설정 (환경변수 + 기본값).

외부 라이브러리(pydantic-settings) 에 의존하지 않기 위해 dataclass 로 작성.
값 읽기 진입점을 1곳으로 제한해 하드코딩을 방지한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import find_dotenv, load_dotenv

# 모듈 import 시점에 1회 로드한다(Settings 필드의 os.environ 접근보다 먼저
# 실행되도록, 그리고 get_settings() 캐시 이전에 프로세스 환경에 반영되도록).
# override=False: 이미 셸이나 MCP 클라이언트의 env 블록으로 주입된 값이
# .env 파일 값에 덮이지 않는다(우선순위: 명시적 env > .env > 기본값).
# .env 파일이 없으면 조용히 넘어간다(load_dotenv 기본 동작).
load_dotenv(find_dotenv(), override=False)


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
    gemini_query_expansion_model: str = field(
        default_factory=lambda: os.environ.get(
            "DOCS_MCP_GEMINI_QUERY_EXPANSION_MODEL", "gemini-2.5-flash"
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
