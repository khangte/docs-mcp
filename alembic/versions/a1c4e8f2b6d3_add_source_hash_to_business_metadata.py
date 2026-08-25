"""add source_hash to endpoint_business_metadata

Revision ID: a1c4e8f2b6d3
Revises: f3a9c1d7e2b4
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c4e8f2b6d3'
down_revision: Union[str, Sequence[str], None] = 'f3a9c1d7e2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """docs/architect-review/55 §3: 스펙/프롬프트 변경 감지용 재생성 판단 키."""
    op.add_column(
        'endpoint_business_metadata',
        sa.Column('source_hash', sa.String(length=64), nullable=False, server_default=''),
        schema='app',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('endpoint_business_metadata', 'source_hash', schema='app')
