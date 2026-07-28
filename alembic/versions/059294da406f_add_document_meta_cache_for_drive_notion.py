"""add document_meta cache for drive notion

Revision ID: 059294da406f
Revises: b336d80334c8
Create Date: 2026-07-28 16:58:46.680178

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '059294da406f'
down_revision: Union[str, Sequence[str], None] = 'b336d80334c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drive/Notion 메타 캐시 테이블 `app.document_meta` 를 생성한다.

    본문은 저장하지 않고 메타데이터만 보관한다. (source, external_id) 유니크
    제약으로 upsert 대상을 식별하고, source 인덱스로 출처별 조회를 돕는다.
    """
    op.create_table(
        'document_meta',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('external_id', sa.String(length=256), nullable=False),
        sa.Column('title', sa.String(length=1024), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('modified_at', sa.DateTime(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source', 'external_id', name='uq_document_meta_source_external'),
        schema='app',
    )
    op.create_index(
        'ix_document_meta_source', 'document_meta', ['source'], unique=False, schema='app'
    )


def downgrade() -> None:
    """`app.document_meta` 테이블과 인덱스를 제거한다."""
    op.drop_index('ix_document_meta_source', table_name='document_meta', schema='app')
    op.drop_table('document_meta', schema='app')
