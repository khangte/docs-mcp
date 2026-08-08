"""embedding dim 256 to 384 for local e5 model

Revision ID: ff8aa8f36266
Revises: a17165213545
Create Date: 2026-08-08 17:11:47.446774

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = 'ff8aa8f36266'
down_revision: Union[str, Sequence[str], None] = 'a17165213545'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """256차원 임베딩 컬럼을 384차원(intfloat/multilingual-e5-small)으로 재생성한다.

    pgvector 컬럼은 dim 이 생성 시 고정되어 in-place 확장이 안전하지 않다.
    기존 256차원 벡터는 새 모델과 호환되지 않아 보존 의미가 없으므로 컬럼을
    통째로 지우고 새로 만든다 — 재임베딩은 이 마이그레이션이 아니라
    `app/scripts/reembed.py` 로 별도 수행한다(스키마 변경과 ML 추론 분리).
    """
    op.drop_index('ix_api_chunk_embedding_hnsw', table_name='api_chunk', schema='app')
    op.drop_column('api_chunk', 'embedding', schema='app')
    op.add_column(
        'api_chunk',
        sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True),
        schema='app',
    )
    op.create_index(
        'ix_api_chunk_embedding_hnsw',
        'api_chunk',
        ['embedding'],
        unique=False,
        schema='app',
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_api_chunk_embedding_hnsw', table_name='api_chunk', schema='app')
    op.drop_column('api_chunk', 'embedding', schema='app')
    op.add_column(
        'api_chunk',
        sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=256), nullable=True),
        schema='app',
    )
    op.create_index(
        'ix_api_chunk_embedding_hnsw',
        'api_chunk',
        ['embedding'],
        unique=False,
        schema='app',
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )
