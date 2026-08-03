"""add project to documents

Revision ID: 48ce217bc4a8
Revises: 059294da406f
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '48ce217bc4a8'
down_revision: Union[str, Sequence[str], None] = '059294da406f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """`api_document`/`document_meta` 에 `project` 컬럼을 추가한다.

    기존 행은 server_default `'default'` 로 백필한다. `document_meta` 의
    유니크 제약을 `(source, external_id)` 에서 `(project, source, external_id)`
    로 교체해 프로젝트별로 같은 외부 문서를 독립적으로 캐시할 수 있게 한다.
    """
    op.add_column(
        'api_document',
        sa.Column('project', sa.String(length=128), nullable=False, server_default='default'),
        schema='app',
    )
    op.create_index(
        'ix_api_document_project', 'api_document', ['project'], unique=False, schema='app'
    )

    op.add_column(
        'document_meta',
        sa.Column('project', sa.String(length=128), nullable=False, server_default='default'),
        schema='app',
    )
    op.drop_constraint(
        'uq_document_meta_source_external', 'document_meta', type_='unique', schema='app'
    )
    op.create_unique_constraint(
        'uq_document_meta_project_source_external',
        'document_meta',
        ['project', 'source', 'external_id'],
        schema='app',
    )
    op.create_index(
        'ix_document_meta_project', 'document_meta', ['project'], unique=False, schema='app'
    )


def downgrade() -> None:
    """`project` 컬럼과 관련 제약·인덱스를 upgrade 의 역순으로 제거한다."""
    op.drop_index('ix_document_meta_project', table_name='document_meta', schema='app')
    op.drop_constraint(
        'uq_document_meta_project_source_external', 'document_meta', type_='unique', schema='app'
    )
    op.create_unique_constraint(
        'uq_document_meta_source_external',
        'document_meta',
        ['source', 'external_id'],
        schema='app',
    )
    op.drop_column('document_meta', 'project', schema='app')

    op.drop_index('ix_api_document_project', table_name='api_document', schema='app')
    op.drop_column('api_document', 'project', schema='app')
