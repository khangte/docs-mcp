"""merge project_source, drop dead columns

Revision ID: 622d43cae0bf
Revises: e2bd26b83408
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '622d43cae0bf'
down_revision: Union[str, Sequence[str], None] = 'e2bd26b83408'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """`project_drive_source`+`project_notion_source` 를 `project_source` 로 병합하고,
    `api_endpoint.operation_id`/`api_response.example_json` 죽은 컬럼을 제거한다.
    """
    op.create_table(
        'project_source',
        sa.Column('project', sa.String(length=128), nullable=False),
        sa.Column('source_type', sa.String(length=16), nullable=False),
        sa.Column('location', sa.String(length=256), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('project', 'source_type', 'location'),
        schema='app',
    )

    op.execute(
        "INSERT INTO app.project_source "
        "(project, source_type, location, kind, created_at, updated_at) "
        "SELECT project, 'drive', folder_id, NULL, created_at, updated_at "
        "FROM app.project_drive_source"
    )
    op.execute(
        "INSERT INTO app.project_source "
        "(project, source_type, location, kind, created_at, updated_at) "
        "SELECT project, 'notion', database_id, kind, created_at, updated_at "
        "FROM app.project_notion_source"
    )

    op.drop_table('project_notion_source', schema='app')
    op.drop_table('project_drive_source', schema='app')

    op.drop_column('api_endpoint', 'operation_id', schema='app')
    op.drop_column('api_response', 'example_json', schema='app')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'api_response',
        sa.Column('example_json', sa.Text(), nullable=True),
        schema='app',
    )
    op.add_column(
        'api_endpoint',
        sa.Column('operation_id', sa.String(length=256), nullable=True),
        schema='app',
    )

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
        sa.Column('kind', sa.String(length=16), nullable=False, server_default='database'),
        sa.PrimaryKeyConstraint('project'),
        schema='app',
    )

    op.execute(
        "INSERT INTO app.project_drive_source (project, folder_id, created_at, updated_at) "
        "SELECT project, location, created_at, updated_at "
        "FROM app.project_source WHERE source_type = 'drive'"
    )
    op.execute(
        "INSERT INTO app.project_notion_source "
        "(project, database_id, kind, created_at, updated_at) "
        "SELECT project, location, kind, created_at, updated_at "
        "FROM app.project_source WHERE source_type = 'notion'"
    )

    op.drop_table('project_source', schema='app')
