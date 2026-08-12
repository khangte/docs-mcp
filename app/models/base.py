"""전 모델의 declarative base + 크로스컷 상수.

어떤 모델 모듈도 import 하지 않는 리프 모듈이다(순환 참조 방지).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# public 스키마는 pgvector 확장 전용으로 남겨두고, 애플리케이션 테이블은
# 전용 스키마(app)에 둔다. public 에 동일 이름 테이블이 있으면 SQLAlchemy
# create_all() 의 존재 확인(checkfirst)이 search_path 상의 다른 스키마 테이블을
# "이미 존재"로 오판해 DDL을 건너뛰는 문제를 방지한다.
SCHEMA = "app"

#: 프로젝트 미지정 시 사용하는 기본 프로젝트명.
DEFAULT_PROJECT = "default"
#: `project` 컬럼 최대 길이.
PROJECT_MAX_LENGTH = 128


class Base(DeclarativeBase):
    """모든 ORM 모델의 베이스 클래스."""

    metadata = MetaData(schema=SCHEMA)


def _utcnow() -> datetime:
    """현재 UTC 시각을 반환한다."""
    return datetime.now(timezone.utc)
