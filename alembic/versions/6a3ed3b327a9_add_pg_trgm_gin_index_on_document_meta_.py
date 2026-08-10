"""add pg_trgm gin index on document_meta title/url

Revision ID: 6a3ed3b327a9
Revises: ff8aa8f36266
Create Date: 2026-08-10 15:57:42.425994

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6a3ed3b327a9'
down_revision: Union[str, Sequence[str], None] = 'ff8aa8f36266'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 확장은 DB 전역(스키마 무관). IF NOT EXISTS로 멱등.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_document_meta_title_trgm",
        "document_meta",
        ["title"],
        unique=False,
        schema="app",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_document_meta_url_trgm",
        "document_meta",
        ["url"],
        unique=False,
        schema="app",
        postgresql_using="gin",
        postgresql_ops={"url": "gin_trgm_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_document_meta_title_trgm", table_name="document_meta", schema="app")
    op.drop_index("ix_document_meta_url_trgm", table_name="document_meta", schema="app")
    # 확장은 다른 곳이 쓸 수 있으니 downgrade에서 DROP하지 않는다(보수적).
