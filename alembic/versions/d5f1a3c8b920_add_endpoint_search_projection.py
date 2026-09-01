"""add endpoint_search_projection table

Revision ID: d5f1a3c8b920
Revises: c4d9e1f70a2b
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.chunk import EMBEDDING_DIM
from app.models.endpoint_projection import CANONICAL_TSV_EXPRESSION

# revision identifiers, used by Alembic.
revision: str = 'd5f1a3c8b920'
down_revision: Union[str, Sequence[str], None] = 'c4d9e1f70a2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """`endpoint_search_projection` 테이블 + HNSW/GIN 인덱스를 만든다.

    `docs/architect-review/101` §2.1. endpoint 당 한 행이며 physical identity 는
    `endpoint_id -> api_endpoint.id` FK(CASCADE), 안정 key 는
    `(document_id, method, path)` unique. 기존 문서의 행은 백필 스크립트
    (`app/scripts/backfill_endpoint_projection.py`) 가 채운다.
    """
    op.create_table(
        'endpoint_search_projection',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('endpoint_id', sa.String(length=64), nullable=False),
        sa.Column('document_id', sa.String(length=64), nullable=False),
        sa.Column('method', sa.String(length=16), nullable=False),
        sa.Column('path', sa.String(length=512), nullable=False),
        sa.Column('canonical_text', sa.Text(), nullable=False),
        sa.Column(
            'embedding',
            pgvector.sqlalchemy.vector.VECTOR(dim=EMBEDDING_DIM),
            nullable=True,
        ),
        sa.Column(
            'canonical_tsv',
            postgresql.TSVECTOR(),
            sa.Computed(CANONICAL_TSV_EXPRESSION, persisted=True),
            nullable=True,
        ),
        sa.Column('representation_version', sa.String(length=16), nullable=False),
        sa.Column('source_hash', sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ['endpoint_id'], ['app.api_endpoint.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['document_id'], ['app.document.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'document_id', 'method', 'path',
            name='uq_endpoint_projection_doc_method_path',
        ),
        schema='app',
    )
    op.create_index(
        'ix_endpoint_projection_embedding_hnsw',
        'endpoint_search_projection',
        ['embedding'],
        unique=False,
        schema='app',
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )
    op.create_index(
        'ix_endpoint_projection_canonical_tsv',
        'endpoint_search_projection',
        ['canonical_tsv'],
        unique=False,
        schema='app',
        postgresql_using='gin',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_endpoint_projection_canonical_tsv',
        table_name='endpoint_search_projection',
        schema='app',
    )
    op.drop_index(
        'ix_endpoint_projection_embedding_hnsw',
        table_name='endpoint_search_projection',
        schema='app',
    )
    op.drop_table('endpoint_search_projection', schema='app')
