"""add folder_ancestor_ids, folder_path to document_meta

Revision ID: b7e4a2c9d1f8
Revises: 47fe51335c37
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7e4a2c9d1f8'
down_revision: Union[str, Sequence[str], None] = '47fe51335c37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """`document_meta` 에 폴더 조상 id 배열과 이름 경로 컬럼을 추가한다.

    둘 다 nullable 이라 값은 다음 refresh_index 백필이 채운다. 새 컬럼은
    `document_index_service._apply_changes` 의 `is_changed` 판정에 들어가지
    않으므로 백필이 본문 재fetch 를 트리거하지 않는다.

    `folder_ancestor_ids` 는 동기화 루트부터 직계 부모까지의 폴더 id 목록이며
    필터(`&&` overlap)의 키다. 전용 인덱스는 만들지 않는다 - 필터가 걸리는
    시점에 후보는 title arm 의 trgm 후보이거나 document_id 로 집은 1행이라
    인덱스 이득이 없다(mime_type/created_at 과 같은 판단).
    """
    op.add_column(
        'document_meta',
        sa.Column(
            'folder_ancestor_ids',
            postgresql.ARRAY(sa.String(length=256)),
            nullable=True,
        ),
        schema='app',
    )
    op.add_column(
        'document_meta',
        sa.Column('folder_path', sa.String(length=2048), nullable=True),
        schema='app',
    )


def downgrade() -> None:
    """추가한 컬럼 2개를 제거한다."""
    op.drop_column('document_meta', 'folder_path', schema='app')
    op.drop_column('document_meta', 'folder_ancestor_ids', schema='app')
