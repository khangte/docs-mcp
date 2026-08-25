"""add mime_type, created_at, owner to document_meta

Revision ID: 47fe51335c37
Revises: a1c4e8f2b6d3
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '47fe51335c37'
down_revision: Union[str, Sequence[str], None] = 'a1c4e8f2b6d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """`document_meta` 에 mime_type/created_at/owner nullable 컬럼과 document_id 인덱스를 추가한다.

    전부 nullable 이라 값은 다음 refresh_index 백필이 채운다(본문 재fetch 는 트리거하지
    않는다 - document_index_service._apply_changes 참조). document_id 인덱스는
    keyword/vector arm 의 EXISTS 서브쿼리가 이 컬럼으로 조회하는데 FK 는 PostgreSQL 이
    인덱스를 자동 생성하지 않아 필요하다.
    """
    op.add_column(
        'document_meta', sa.Column('mime_type', sa.String(length=128), nullable=True), schema='app'
    )
    op.add_column(
        'document_meta', sa.Column('created_at', sa.DateTime(), nullable=True), schema='app'
    )
    op.add_column(
        'document_meta', sa.Column('owner', sa.String(length=320), nullable=True), schema='app'
    )
    op.create_index(
        'ix_document_meta_document_id',
        'document_meta',
        ['document_id'],
        unique=False,
        schema='app',
    )


def downgrade() -> None:
    """추가한 인덱스와 컬럼 3개를 제거한다."""
    op.drop_index('ix_document_meta_document_id', table_name='document_meta', schema='app')
    op.drop_column('document_meta', 'owner', schema='app')
    op.drop_column('document_meta', 'created_at', schema='app')
    op.drop_column('document_meta', 'mime_type', schema='app')
