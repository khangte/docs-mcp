"""add kind to project_notion_source

Revision ID: 316f49510efc
Revises: 2b4bb412644b
Create Date: 2026-08-03 00:00:02.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '316f49510efc'
down_revision: Union[str, Sequence[str], None] = '2b4bb412644b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """`project_notion_source` 에 `kind` 컬럼을 추가한다.

    database/page 매핑을 같은 값 컬럼(`database_id`)으로 구분하기 위한
    구분자다. 기존 행은 server_default 로 `'database'` 백필된다.
    """
    op.add_column(
        'project_notion_source',
        sa.Column('kind', sa.String(length=16), nullable=False, server_default='database'),
        schema='app',
    )


def downgrade() -> None:
    """`kind` 컬럼을 제거한다."""
    op.drop_column('project_notion_source', 'kind', schema='app')
