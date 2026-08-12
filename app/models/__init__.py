"""ORM 모델 등록 허브.

전 모델 모듈을 여기서 import 해 `Base.metadata`(=`Base.registry`)에 등록을
보장한다 — 어느 모듈이든 여기 빠지면 그 테이블이 `create_all()`/alembic
autogenerate 대상에서 누락된다(과거 project_source 누락 사건과 동종 버그를
구조적으로 막는 지점).

호출부는 물리 파일 위치와 무관하게 `from app.models import X` 로 통일해서
쓴다 — 파일 재배치에 강건하고, 심볼별로 어느 파일에 있는지 추적할 필요가
없다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.models.base import Base, DEFAULT_PROJECT, PROJECT_MAX_LENGTH, SCHEMA
from app.models.chunk import Chunk, EMBEDDING_DIM, TEXT_TSV_EXPRESSION
from app.models.document import Document, DocumentSection, DocumentSyncHistory
from app.models.document_meta import ALLOWED_SOURCES, SOURCE_DRIVE, SOURCE_NOTION, DocumentMeta
from app.models.openapi import ApiEndpoint, ApiParameter, ApiRequestBody, ApiResponse, ApiSchema
from app.models.project_source import ProjectSource

__all__ = [
    "Base",
    "DEFAULT_PROJECT",
    "PROJECT_MAX_LENGTH",
    "SCHEMA",
    "Document",
    "DocumentSection",
    "DocumentSyncHistory",
    "Chunk",
    "EMBEDDING_DIM",
    "TEXT_TSV_EXPRESSION",
    "ApiEndpoint",
    "ApiParameter",
    "ApiRequestBody",
    "ApiResponse",
    "ApiSchema",
    "ALLOWED_SOURCES",
    "SOURCE_DRIVE",
    "SOURCE_NOTION",
    "DocumentMeta",
    "ProjectSource",
    "create_all",
]


def create_all(engine: Any) -> None:
    """스키마와 테이블 전체 생성 (초기 기동·테스트용).

    이 모듈이 전 모델 모듈을 이미 import 했으므로 `Base.metadata` 에 모든
    테이블이 등록돼 있다.
    """
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    Base.metadata.create_all(engine)
