"""docs/16 Phase 0 진단 스크립트(`app.scripts.diagnose_long_sections`) 테스트.

읽기 전용 동작을 검증한다: 테이블이 없으면 스키마를 만들지 않고 빈 결과를
반환하고, section 청크 중 토큰수가 상한을 넘는 것만 doc_type 과 함께 집계한다.
실제 SentenceTransformer 토크나이저는 무겁고 네트워크가 필요해, `count_tokens`
를 주입해 빠른 페이크로 대체한다(모델 로딩 없이 임계값 로직만 검증).
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.db import create_db_engine
from app.models import ApiChunk, ApiDocument, create_all
from app.scripts.diagnose_long_sections import diagnose

_ADMIN_DATABASE_URL = "postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp"


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _count_by_words(text_value: str) -> int:
    """빠른 페이크 토큰 카운터: 공백 분리 단어수를 토큰수로 취급."""
    return len(text_value.split())


@pytest.fixture()
def fresh_db_url():
    """확장만 만든, 애플리케이션 스키마(create_all)는 아직 안 만든 새 DB URL."""
    db_name = f"diagtest_{uuid.uuid4().hex[:12]}"
    admin_engine = create_db_engine(_ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    test_url = _with_database(_ADMIN_DATABASE_URL, db_name)
    setup_engine = create_db_engine(test_url)
    with setup_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    setup_engine.dispose()

    try:
        yield test_url
    finally:
        admin_engine = create_db_engine(_ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
        admin_engine.dispose()


def test_diagnose_returns_empty_and_does_not_create_schema_when_table_missing(
    fresh_db_url,
) -> None:
    result = diagnose(fresh_db_url, count_tokens=_count_by_words, threshold=512)

    assert result == []
    # 스키마를 만들지 않는다 — 읽기 전용 보장의 핵심 단언.
    from sqlalchemy import inspect

    engine = create_db_engine(fresh_db_url)
    assert inspect(engine).has_table("api_chunk", schema="app") is False


def test_diagnose_finds_no_overflow_when_all_sections_within_threshold(
    fresh_db_url,
) -> None:
    engine = create_db_engine(fresh_db_url)
    create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        session.add(
            ApiDocument(
                id="doc1",
                project="default",
                doc_type="markdown",
                title="t",
                content_hash="h",
                raw_text="raw",
            )
        )
        session.add(
            ApiChunk(
                id="doc1:chunk:0",
                document_id="doc1",
                chunk_type="section",
                ref_id="doc1:section:0",
                text="짧은 섹션 내용",
            )
        )
        session.commit()

    result = diagnose(fresh_db_url, count_tokens=_count_by_words, threshold=512)

    assert result == []


def test_diagnose_reports_overflowing_section_with_doc_type(fresh_db_url) -> None:
    engine = create_db_engine(fresh_db_url)
    create_all(engine)
    session_factory = sessionmaker(bind=engine)
    long_text = " ".join(["단어"] * 600)  # 페이크 카운터 기준 600토큰 > 512
    with session_factory() as session:
        session.add(
            ApiDocument(
                id="doc1",
                project="default",
                doc_type="pdf",
                title="t",
                content_hash="h",
                raw_text="raw",
            )
        )
        session.add(
            ApiChunk(
                id="doc1:chunk:0",
                document_id="doc1",
                chunk_type="section",
                ref_id="doc1:section:0",
                text=long_text,
            )
        )
        # endpoint 청크는 진단 대상 아님(섹션만) — 섞여 있어도 결과에 안 나와야 함
        session.add(
            ApiChunk(
                id="doc1:chunk:1",
                document_id="doc1",
                chunk_type="endpoint",
                ref_id="doc1:ep:x",
                text=" ".join(["단어"] * 900),
            )
        )
        session.commit()

    result = diagnose(fresh_db_url, count_tokens=_count_by_words, threshold=512)

    assert len(result) == 1
    overflow = result[0]
    assert overflow.document_id == "doc1"
    assert overflow.doc_type == "pdf"
    assert overflow.ref_id == "doc1:section:0"
    assert overflow.token_count == 600
