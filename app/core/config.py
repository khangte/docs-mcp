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
    #: "rrf"(기본, 키워드+벡터 항상 병렬 실행 후 순위 융합) | "fallback"(키워드
    #: 우선, 0건일 때만 벡터 — 롤백 스위치로 상시 보존). search_endpoints 전용.
    search_strategy: str = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_SEARCH_STRATEGY", "rrf")
    )
    #: "indexed"(기본, 색인된 section 청크 title+keyword+vector 3-arm RRF,
    #: doc36 Phase3) | "fetch"(실시간 fetch+토큰겹침 가중합, 백필 이전 기본값).
    #: search_documents 전용. 미인식 값은 안전하게 "fetch"로 degrade한다
    #: (롤백 스위치, `docs/architect-review/43` §2).
    document_search_strategy: str = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_DOCUMENT_SEARCH_STRATEGY", "indexed")
    )
    #: "text"(기본, 기존 `chunk.text_tsv` 단일 필드 무가중 ts_rank — 롤백
    #: 스위치로 상시 보존) | "structured"(`chunk.search_tsv` 가중 A/B/C/D,
    #: `docs/architect-review/78`). search_endpoints 키워드 arm 전용이며
    #: 미인식 값은 안전하게 "text" 로 degrade 한다.
    search_lexical_field: str = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_SEARCH_LEXICAL_FIELD", "text")
    )
    #: search_endpoints RRF 컷 밖의 arm-exclusive(단일 arm) 후보를 final top-k 로
    #: 끌어올리는 사전 고정 quota(`docs/architect-review/92` §6, P2). "0"(기본)이면
    #: 완전 비활성 — 기존 `base_wide[:top_k]` 와 동일하다. 원시 env 문자열을 그대로
    #: 두고 `EndpointCandidateSearch` 가 정수로 좁히며 상한으로 클램프한다(미인식
    #: 값은 "0" 으로 degrade — 롤아웃 스위치).
    search_arm_rescue_quota: str = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_SEARCH_ARM_RESCUE_QUOTA", "0")
    )
    log_level: str = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_LOG_LEVEL", "INFO")
    )
    embedding_model: str = field(
        default_factory=lambda: os.environ.get(
            "DOCS_MCP_EMBEDDING_MODEL", "intfloat/multilingual-e5-small"
        )
    )
    #: "local"(기본, 실제 의미 유사도) | "hash"(결정적 해시, 테스트/모델 로드
    #: 실패 폴백용). 모델 다운로드 없이 백엔드를 강제하고 싶을 때(테스트,
    #: 오프라인 환경) "hash" 로 지정한다.
    embedding_backend: str = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_EMBEDDING_BACKEND", "local")
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
    #: 기본 프로젝트용 Notion 허브 페이지 ID. notion_database_id 와 동시에
    #: 있으면 이 값이 우선한다(seed_default_sources 참고).
    notion_page_id: str | None = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_NOTION_PAGE_ID") or None
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
    # --- 비즈니스 메타데이터 생성 (docs/architect-review/55) ---
    #: DOCS_MCP_ANTHROPIC_API_KEY 가 없으면 ANTHROPIC_API_KEY 로 폴백한다.
    metadata_api_key: str | None = field(
        default_factory=lambda: (
            os.environ.get("DOCS_MCP_ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or None
        )
    )
    metadata_model: str | None = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_METADATA_MODEL") or None
    )
    metadata_api_base: str = field(
        default_factory=lambda: os.environ.get(
            "DOCS_MCP_METADATA_API_BASE", "https://api.anthropic.com"
        )
    )
    #: 호출 LLM write-back 쓰기 경로 킬스위치(docs/architect-review/56 §2.3).
    #: 쓰기 도구를 LLM 에게 여는 설계라 코드 수정 없이 닫을 수단을 둔다.
    #: "0"/"false"/"no"(대소문자 무시) 이면 비활성.
    business_metadata_writeback_enabled: bool = field(
        default_factory=lambda: os.environ.get(
            "DOCS_MCP_METADATA_WRITEBACK_ENABLED", "true"
        ).strip().lower()
        not in ("0", "false", "no")
    )


def get_settings() -> Settings:
    """테스트/주입 가능하도록 팩토리로 제공."""
    return Settings()
