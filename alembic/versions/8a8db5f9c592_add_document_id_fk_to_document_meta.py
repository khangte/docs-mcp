"""add document_id fk to document_meta

Revision ID: 8a8db5f9c592
Revises: e7ab360a329a
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a8db5f9c592'
down_revision: Union[str, Sequence[str], None] = 'e7ab360a329a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """`document_meta.document_id` nullable FK 추가(본문 색인된 문서와의 조인 키).

    Drive/Notion 문서 삭제 전파 시 대응 `document` 행을 지우면(CASCADE 로
    청크·벡터까지 함께 삭제) 이 컬럼은 SET NULL 로 자동 정리된다.
    """
    op.add_column(
        'document_meta',
        sa.Column('document_id', sa.String(length=64), nullable=True),
        schema='app',
    )
    op.create_foreign_key(
        'fk_document_meta_document_id',
        'document_meta',
        'document',
        ['document_id'],
        ['id'],
        source_schema='app',
        referent_schema='app',
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """`document_meta.document_id` FK 와 컬럼을 제거한다."""
    op.drop_constraint(
        'fk_document_meta_document_id', 'document_meta', schema='app', type_='foreignkey'
    )
    op.drop_column('document_meta', 'document_id', schema='app')
