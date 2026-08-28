"""엔드포인트 구조 신호 백필 스크립트 테스트(78번 §5.3)."""

from __future__ import annotations

from sqlalchemy import select

from app.models import ApiEndpoint, Chunk, Document
from app.scripts.backfill_endpoint_structure import backfill_endpoint_structure


def _seed(session) -> None:
    """문서 1건 + 엔드포인트 2건 + 대응 청크 2건 + section 청크 1건을 넣는다."""
    session.add(
        Document(
            id="doc-b",
            project="default",
            source_url=None,
            title="문서",
            content_hash="hash",
            raw_text="{}",
        )
    )
    session.flush()
    session.add_all(
        [
            ApiEndpoint(
                id="ep-root",
                document_id="doc-b",
                method="GET",
                path="/repos/{owner}/{repo}",
                operation_id="repos/get",
                summary="Get a repository",
                description="",
                tags_json='["repos"]',
            ),
            ApiEndpoint(
                id="ep-child",
                document_id="doc-b",
                method="GET",
                path="/repos/{owner}/{repo}/topics",
                operation_id="repos/get-all-topics",
                summary="Get all repository topics",
                description="",
                tags_json='["repos"]',
            ),
        ]
    )
    session.add_all(
        [
            Chunk(
                id="c-root",
                document_id="doc-b",
                chunk_type="endpoint",
                ref_id="ep-root",
                text="root text",
            ),
            Chunk(
                id="c-child",
                document_id="doc-b",
                chunk_type="endpoint",
                ref_id="ep-child",
                text="child text",
            ),
            Chunk(
                id="c-sec",
                document_id="doc-b",
                chunk_type="section",
                ref_id="sec-0",
                text="본문",
            ),
        ]
    )
    session.commit()


def test_backfill_fills_endpoint_structure_fields(db_session, session_factory) -> None:
    """endpoint 청크 3필드를 색인 경로와 같은 값으로 채운다."""
    _seed(db_session)

    updated = backfill_endpoint_structure(session_factory)

    db_session.expire_all()
    assert updated == 2
    root = db_session.get(Chunk, "c-root")
    child = db_session.get(Chunk, "c-child")
    assert root.leaf_text == "repos repo"
    assert root.intent_text == "get retrieve fetch read show detail Get a repository"
    assert root.context_text == "owner get"
    assert child.leaf_text == "topics topic"


def test_backfill_leaves_text_and_embedding_untouched(db_session, session_factory) -> None:
    """78번 §3.2 전제: 백필은 text/embedding 을 건드리지 않는다."""
    _seed(db_session)

    backfill_endpoint_structure(session_factory)

    db_session.expire_all()
    assert db_session.get(Chunk, "c-root").text == "root text"
    assert db_session.get(Chunk, "c-root").embedding is None


def test_backfill_skips_non_endpoint_chunks(db_session, session_factory) -> None:
    """section 청크는 대상이 아니다(생성 컬럼이 NULL 이어야 한다)."""
    _seed(db_session)

    backfill_endpoint_structure(session_factory)

    db_session.expire_all()
    section = db_session.get(Chunk, "c-sec")
    assert section.leaf_text == ""
    assert section.intent_text == ""
    assert section.context_text == ""


def test_backfill_is_idempotent(db_session, session_factory) -> None:
    """두 번 돌려도 같은 값이다(78번 §4.5 결정성)."""
    _seed(db_session)

    backfill_endpoint_structure(session_factory)
    db_session.expire_all()
    first = db_session.execute(
        select(Chunk.id, Chunk.leaf_text, Chunk.intent_text, Chunk.context_text)
        .where(Chunk.chunk_type == "endpoint")
        .order_by(Chunk.id)
    ).all()

    backfill_endpoint_structure(session_factory)
    db_session.expire_all()
    second = db_session.execute(
        select(Chunk.id, Chunk.leaf_text, Chunk.intent_text, Chunk.context_text)
        .where(Chunk.chunk_type == "endpoint")
        .order_by(Chunk.id)
    ).all()

    assert first == second


def test_backfill_scopes_to_document_id(db_session, session_factory) -> None:
    """`document_id` 를 주면 그 문서만 갱신한다."""
    _seed(db_session)

    assert backfill_endpoint_structure(session_factory, document_id="없는문서") == 0
    assert backfill_endpoint_structure(session_factory, document_id="doc-b") == 2
