"""add operation_id to api_endpoint

Revision ID: e7ab360a329a
Revises: 70cbcb0e75b3
Create Date: 2026-08-14 22:45:57.695585

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7ab360a329a'
down_revision: Union[str, Sequence[str], None] = '70cbcb0e75b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """operation_id 컬럼 추가 + exact match 조회용 인덱스 2개(operation_id, method+path)."""
    op.add_column(
        'api_endpoint',
        sa.Column('operation_id', sa.String(length=256), nullable=True),
        schema='app',
    )
    op.create_index(
        'ix_api_endpoint_operation_id',
        'api_endpoint',
        ['operation_id'],
        unique=False,
        schema='app',
    )
    op.create_index(
        'ix_api_endpoint_method_path',
        'api_endpoint',
        ['method', 'path'],
        unique=False,
        schema='app',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_api_endpoint_method_path', table_name='api_endpoint', schema='app')
    op.drop_index('ix_api_endpoint_operation_id', table_name='api_endpoint', schema='app')
    op.drop_column('api_endpoint', 'operation_id', schema='app')
