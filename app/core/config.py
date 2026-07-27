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
            "sqlite+pysqlite:///./docs_mcp.db",
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
    gemini_model: str = field(
        default_factory=lambda: os.environ.get(
            "DOCS_MCP_GEMINI_MODEL", "gemini-2.0-flash"
        )
    )
    gemini_embedding_model: str = field(
        default_factory=lambda: os.environ.get(
            "DOCS_MCP_GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"
        )
    )


def get_settings() -> Settings:
    """테스트/주입 가능하도록 팩토리로 제공."""
    return Settings()
