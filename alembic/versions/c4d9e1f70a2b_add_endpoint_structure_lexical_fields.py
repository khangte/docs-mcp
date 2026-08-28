"""add endpoint structure lexical fields and weighted search_tsv

Revision ID: c4d9e1f70a2b
Revises: b7e4a2c9d1f8
Create Date: 2026-08-28 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.chunk import SEARCH_TSV_EXPRESSION

# revision identifiers, used by Alembic.
revision: str = 'c4d9e1f70a2b'
down_revision: Union[str, Sequence[str], None] = 'b7e4a2c9d1f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """`chunk` 에 구조 신호 평문 컬럼 3개와 가중 생성 컬럼·부분 GIN 인덱스를 추가한다.

    `docs/architect-review/78` §5. 기존 행의 세 평문 컬럼은 빈 문자열로
    채워지고, 값은 `app/scripts/backfill_endpoint_structure.py` 가 넣는다.
    `text` 와 `embedding` 은 건드리지 않으므로 재임베딩이 필요 없다.
    """
    for column in ('leaf_text', 'intent_text', 'context_text'):
        op.add_column(
            'chunk',
            sa.Column(column, sa.Text(), nullable=False, server_default=''),
            schema='app',
        )
    op.add_column(
        'chunk',
        sa.Column(
            'search_tsv',
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_TSV_EXPRESSION, persisted=True),
            nullable=True,
        ),
        schema='app',
    )
    op.create_index(
        'ix_chunk_search_tsv',
        'chunk',
        ['search_tsv'],
        unique=False,
        schema='app',
        postgresql_using='gin',
        postgresql_where=sa.text("chunk_type = 'endpoint'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_chunk_search_tsv', table_name='chunk', schema='app')
    op.drop_column('chunk', 'search_tsv', schema='app')
    for column in ('context_text', 'intent_text', 'leaf_text'):
        op.drop_column('chunk', column, schema='app')
