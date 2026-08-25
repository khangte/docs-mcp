"""add endpoint_business_metadata

Revision ID: f3a9c1d7e2b4
Revises: 8a8db5f9c592
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9c1d7e2b4'
down_revision: Union[str, Sequence[str], None] = '8a8db5f9c592'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """docs/architect-review/52,54: api_endpoint 에 FK 없이 (document_id, method,
    path) 를 키로 재색인 후에도 값을 보존하는 비즈니스 메타데이터 테이블."""
    op.create_table(
        'endpoint_business_metadata',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('document_id', sa.String(length=64), nullable=False),
        sa.Column('method', sa.String(length=16), nullable=False),
        sa.Column('path', sa.String(length=512), nullable=False),
        sa.Column('business_description', sa.Text(), nullable=False, server_default=''),
        sa.Column('keywords_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('user_phrases_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('model', sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(['document_id'], ['app.document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'document_id', 'method', 'path', name='uq_business_metadata_doc_method_path'
        ),
        schema='app',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('endpoint_business_metadata', schema='app')
