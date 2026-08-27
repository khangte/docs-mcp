"""`document_meta` 에 거는 hard filter — title/keyword/vector 3 arm 이 공유한다.

서비스 레이어에 두면 리포지토리가 상위 레이어를 import 하게 되므로 리포지토리
쪽에 둔다. keyword/vector arm(청크 조회문)에는 JOIN 이 아니라 EXISTS 서브쿼리를
쓴다 - JOIN 은 `document_meta` 에 같은 `document_id` 행이 둘 이상일 때 청크 행을
증식시켜 조용히 순위를 망가뜨릴 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import ColumnElement, exists, func, select

from app.models.document_meta import DocumentMeta


@dataclass(frozen=True)
class DocumentMetaFilter:
    """`document_meta` 에 거는 hard filter 조건(전부 선택).

    Attributes:
        modified_after: 수정 시각 포함(>=), tz-naive UTC.
        modified_before: 수정 시각 포함(<=), tz-naive UTC.
        mime_types: 정확 일치 OR. 빈 튜플이면 조건 없음.
        created_after: 생성 시각 포함(>=), tz-naive UTC.
        created_before: 생성 시각 포함(<=), tz-naive UTC.
        owners: 소유자 정확 일치 OR(대소문자 무시). 빈 튜플이면 조건 없음.
        folder_ids: 폴더 id OR. 대상 폴더나 그 하위(자손 포함) 문서를 남긴다 -
            `folder_ancestor_ids` 배열 중첩(&&)으로 판정한다. 빈 튜플이면 조건 없음.

    필터가 보는 컬럼이 NULL 인 행은 그 필터가 지정되면 제외된다(SQL 3값
    논리 그대로 - 의도된 동작). Notion 문서는 `mime_type`/`owner`/
    `folder_ancestor_ids` 가 항상 NULL 이므로 그 필터를 주면 언제나 빠진다.
    """

    modified_after: datetime | None = None
    modified_before: datetime | None = None
    mime_types: tuple[str, ...] = field(default_factory=tuple)
    created_after: datetime | None = None
    created_before: datetime | None = None
    owners: tuple[str, ...] = field(default_factory=tuple)
    folder_ids: tuple[str, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        """조건이 하나도 없으면 True.

        필드를 나열해 검사하면 필드를 추가할 때 한 줄만 빠뜨려도 필터가
        조용히 무시된다(호출부가 이 값으로 WHERE 부착 여부를 정한다).
        조건 생성 함수에서 파생시켜 그 어긋남을 구조적으로 막는다.
        """
        return not document_meta_conditions(self)


def document_meta_conditions(f: DocumentMetaFilter) -> list[ColumnElement[bool]]:
    """`DocumentMeta` 를 직접 조회하는 문(title arm)에 붙일 WHERE 조건들."""
    conditions: list[ColumnElement[bool]] = []
    if f.modified_after is not None:
        conditions.append(DocumentMeta.modified_at >= f.modified_after)
    if f.modified_before is not None:
        conditions.append(DocumentMeta.modified_at <= f.modified_before)
    if f.mime_types:
        conditions.append(DocumentMeta.mime_type.in_(f.mime_types))
    if f.created_after is not None:
        conditions.append(DocumentMeta.created_at >= f.created_after)
    if f.created_before is not None:
        conditions.append(DocumentMeta.created_at <= f.created_before)
    if f.owners:
        # 이메일 대소문자 표기는 외부 시스템에서 오므로 신뢰할 수 없다.
        # 이 필터에 쓸 인덱스가 없어(후보가 이미 좁혀진 뒤 걸린다)
        # lower() 로 인한 인덱스 손실도 없다.
        conditions.append(
            func.lower(DocumentMeta.owner).in_([o.lower() for o in f.owners])
        )
    if f.folder_ids:
        # 배열 중첩(&&) 한 번으로 "조상 중 하나라도 일치" = 자손 포함 의미론이 나온다.
        # 구분자 문자열 + LIKE 안은 쓰지 않는다 - Drive 폴더 id 에 들어가는 `_` 가
        # LIKE 의 단일 문자 와일드카드라 엉뚱한 폴더가 조용히 매치된다.
        conditions.append(DocumentMeta.folder_ancestor_ids.overlap(list(f.folder_ids)))
    return conditions


def document_meta_exists(
    f: DocumentMetaFilter, document_id_col: ColumnElement
) -> ColumnElement[bool]:
    """청크 조회문에 붙일 EXISTS 서브쿼리(`document_meta.document_id = <chunk 의 document_id>`)."""
    conditions = document_meta_conditions(f)
    conditions.append(DocumentMeta.document_id == document_id_col)
    return exists(select(1).where(*conditions))
