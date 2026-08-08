"""add text_tsv generated column for endpoint fts

Revision ID: a17165213545
Revises: 316f49510efc
Create Date: 2026-08-08 14:34:06.612637

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.openapi import TEXT_TSV_EXPRESSION


# revision identifiers, used by Alembic.
revision: str = 'a17165213545'
down_revision: Union[str, Sequence[str], None] = '316f49510efc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'api_chunk',
        sa.Column(
            'text_tsv',
            postgresql.TSVECTOR(),
            sa.Computed(TEXT_TSV_EXPRESSION, persisted=True),
            nullable=True,
        ),
        schema='app',
    )
    op.create_index(
        'ix_api_chunk_text_tsv',
        'api_chunk',
        ['text_tsv'],
        unique=False,
        schema='app',
        postgresql_using='gin',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_api_chunk_text_tsv', table_name='api_chunk', schema='app')
    op.drop_column('api_chunk', 'text_tsv', schema='app')
