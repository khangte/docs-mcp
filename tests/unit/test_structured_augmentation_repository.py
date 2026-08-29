"""`ChunkRepository.score_endpoint_structured_augmentation` 테스트.

base-wide vector-only 후보 전용 A/B/C original-query batch scorer 계약
(`docs/architect-review/87` §2, I4/I5)을 검증한다.

- weight 배열 `{D,C,B,A}={0.0,0.2,0.4,1.0}` — D lexeme 매칭은 점수 0
- `ref_id IN (...)` 필터 밖 청크는 결과에 없음
- SQL round-trip 정확히 1회, 빈 입력은 SQL 없이 `{}`
"""

from __future__ import annotations

import sqlalchemy as sa

from app.models import Chunk, Document
from app.repositories.chunk_repository import ChunkRepository


class _StatementCounter:
    """엔진에 `before_cursor_execute` 리스너를 붙여 실행된 statement 수를 센다."""

    def __init__(self, engine: sa.Engine) -> None:
        self.count = 0
        self._engine = engine

    def __enter__(self) -> "_StatementCounter":
        sa.event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc: object) -> None:
        sa.event.remove(self._engine, "before_cursor_execute", self._on_execute)

    def _on_execute(
        self,
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        self.count += 1


def _seed(session: sa.orm.Session) -> None:
    """동일 lexeme 'probe' 가 각각 leaf(A)/intent(B)/context(C)/text(D)-only 인
    endpoint 청크와 ref filter 밖 A-hit 청크를 넣는다."""
    session.add(
        Document(
            id="doc1",
            project="default",
            source_url=None,
            title="문서",
            content_hash="hash",
            raw_text="{}",
        )
    )
    rows = [
        ("c-a", "ref-a", "probe", "", "", "leaf hit"),
        ("c-b", "ref-b", "", "probe", "", "intent hit"),
        ("c-c", "ref-c", "", "", "probe", "context hit"),
        ("c-d", "ref-d", "", "", "", "probe"),
        ("c-out", "ref-outside", "probe", "", "", "outside ref"),
    ]
    for cid, ref_id, leaf, intent, context, txt in rows:
        session.add(
            Chunk(
                id=cid,
                document_id="doc1",
                chunk_type="endpoint",
                ref_id=ref_id,
                text=txt,
                leaf_text=leaf,
                intent_text=intent,
                context_text=context,
            )
        )
    session.commit()


def test_scores_abc_descending_and_d_only_is_zero(db_session) -> None:
    """A > B > C > 0, D-only 는 0, ref filter 밖은 결과에 없음, SQL 1회."""
    _seed(db_session)
    repo = ChunkRepository(db_session)

    with _StatementCounter(db_session.get_bind()) as counter:
        scores = repo.score_endpoint_structured_augmentation(
            ["probe"], ["ref-a", "ref-b", "ref-c", "ref-d"]
        )

    assert counter.count == 1
    assert scores["ref-a"] > scores["ref-b"] > scores["ref-c"] > 0.0
    assert scores.get("ref-d", 0.0) == 0.0
    assert "ref-outside" not in scores


def test_empty_terms_or_refs_return_empty_without_sql(db_session) -> None:
    """term 또는 ref 가 비면 SQL 없이 `{}` 를 반환한다."""
    _seed(db_session)
    repo = ChunkRepository(db_session)

    with _StatementCounter(db_session.get_bind()) as counter:
        assert repo.score_endpoint_structured_augmentation([], ["ref-a"]) == {}
        assert repo.score_endpoint_structured_augmentation(["probe"], []) == {}

    assert counter.count == 0
