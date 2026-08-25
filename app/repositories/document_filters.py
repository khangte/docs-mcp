"""`document_meta` 에 거는 hard filter — title/keyword/vector 3 arm 이 공유한다.

서비스 레이어에 두면 리포지토리가 상위 레이어를 import 하게 되므로 리포지토리
쪽에 둔다. keyword/vector arm(청크 조회문)에는 JOIN 이 아니라 EXISTS 서브쿼리를
쓴다 - JOIN 은 `document_meta` 에 같은 `document_id` 행이 둘 이상일 때 청크 행을
증식시켜 조용히 순위를 망가뜨릴 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import ColumnElement, exists, select

from app.models.document_meta import DocumentMeta


@dataclass(frozen=True)
class DocumentMetaFilter:
    """`document_meta` 에 거는 hard filter 조건(전부 선택).

    Attributes:
        modified_after: 포함(>=), tz-naive UTC.
        modified_before: 포함(<=), tz-naive UTC.
        mime_types: 정확 일치 OR. 빈 튜플이면 조건 없음.

    `modified_at` 이 NULL 인 행은 날짜 필터가 하나라도 있으면 제외된다
    (SQL 3값 논리 그대로 - 의도된 동작).
    """

    modified_after: datetime | None = None
    modified_before: datetime | None = None
    mime_types: tuple[str, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        """조건이 하나도 없으면 True."""
        return (
            self.modified_after is None
            and self.modified_before is None
            and not self.mime_types
        )


def document_meta_conditions(f: DocumentMetaFilter) -> list[ColumnElement[bool]]:
    """`DocumentMeta` 를 직접 조회하는 문(title arm)에 붙일 WHERE 조건들."""
    conditions: list[ColumnElement[bool]] = []
    if f.modified_after is not None:
        conditions.append(DocumentMeta.modified_at >= f.modified_after)
    if f.modified_before is not None:
        conditions.append(DocumentMeta.modified_at <= f.modified_before)
    if f.mime_types:
        conditions.append(DocumentMeta.mime_type.in_(f.mime_types))
    return conditions


def document_meta_exists(
    f: DocumentMetaFilter, document_id_col: ColumnElement
) -> ColumnElement[bool]:
    """청크 조회문에 붙일 EXISTS 서브쿼리(`document_meta.document_id = <chunk 의 document_id>`)."""
    conditions = document_meta_conditions(f)
    conditions.append(DocumentMeta.document_id == document_id_col)
    return exists(select(1).where(*conditions))
