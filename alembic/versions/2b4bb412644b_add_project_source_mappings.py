"""add project source mappings

Revision ID: 2b4bb412644b
Revises: 48ce217bc4a8
Create Date: 2026-08-03 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2b4bb412644b'
down_revision: Union[str, Sequence[str], None] = '48ce217bc4a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """프로젝트별 Drive/Notion 소스 매핑 테이블을 생성한다.

    둘 다 신규 테이블 추가만 수행하는 무해한 변경이라 같은 리비전에 묶는다.
    """
    op.create_table(
        'project_drive_source',
        sa.Column('project', sa.String(length=128), nullable=False),
        sa.Column('folder_id', sa.String(length=256), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('project'),
        schema='app',
    )
    op.create_table(
        'project_notion_source',
        sa.Column('project', sa.String(length=128), nullable=False),
        sa.Column('database_id', sa.String(length=256), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('project'),
        schema='app',
    )


def downgrade() -> None:
    """`project_drive_source`/`project_notion_source` 테이블을 제거한다."""
    op.drop_table('project_notion_source', schema='app')
    op.drop_table('project_drive_source', schema='app')
