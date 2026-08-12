"""rename api_document/api_section/api_chunk to document/document_section/chunk

Revision ID: 70cbcb0e75b3
Revises: 622d43cae0bf
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '70cbcb0e75b3'
down_revision: Union[str, Sequence[str], None] = '622d43cae0bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """`api_document`/`api_section`/`api_chunk` 를 실제 스코프(전 포맷 공용)에
    맞춰 `document`/`document_section`/`chunk` 로 rename 한다(문서 22).

    `document_sync_history`·openapi 5종(`api_endpoint` 등)은 손대지 않는다
    — 이미 이름과 스코프가 일치한다. Postgres `RENAME TABLE` 은 데이터·FK·
    인덱스를 자동 보존한다(무손실). 제약/인덱스 이름도 정합을 위해 함께
    바꾼다(선택 사항이지만 lead 승인).
    """
    op.rename_table('api_document', 'document', schema='app')
    op.rename_table('api_section', 'document_section', schema='app')
    op.rename_table('api_chunk', 'chunk', schema='app')

    op.execute('ALTER TABLE app.document RENAME CONSTRAINT api_document_pkey TO document_pkey')
    op.execute(
        'ALTER TABLE app.document RENAME CONSTRAINT api_document_source_url_key '
        'TO document_source_url_key'
    )
    op.execute(
        'ALTER TABLE app.document_section RENAME CONSTRAINT api_section_pkey '
        'TO document_section_pkey'
    )
    op.execute(
        'ALTER TABLE app.document_section RENAME CONSTRAINT api_section_document_id_fkey '
        'TO document_section_document_id_fkey'
    )
    op.execute('ALTER TABLE app.chunk RENAME CONSTRAINT api_chunk_pkey TO chunk_pkey')
    op.execute(
        'ALTER TABLE app.chunk RENAME CONSTRAINT api_chunk_document_id_fkey '
        'TO chunk_document_id_fkey'
    )

    op.execute('ALTER INDEX app.ix_api_document_project RENAME TO ix_document_project')
    op.execute(
        'ALTER INDEX app.ix_api_chunk_embedding_hnsw RENAME TO ix_chunk_embedding_hnsw'
    )
    op.execute('ALTER INDEX app.ix_api_chunk_text_tsv RENAME TO ix_chunk_text_tsv')


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('ALTER INDEX app.ix_chunk_text_tsv RENAME TO ix_api_chunk_text_tsv')
    op.execute(
        'ALTER INDEX app.ix_chunk_embedding_hnsw RENAME TO ix_api_chunk_embedding_hnsw'
    )
    op.execute('ALTER INDEX app.ix_document_project RENAME TO ix_api_document_project')

    op.execute(
        'ALTER TABLE app.chunk RENAME CONSTRAINT chunk_document_id_fkey '
        'TO api_chunk_document_id_fkey'
    )
    op.execute('ALTER TABLE app.chunk RENAME CONSTRAINT chunk_pkey TO api_chunk_pkey')
    op.execute(
        'ALTER TABLE app.document_section RENAME CONSTRAINT document_section_document_id_fkey '
        'TO api_section_document_id_fkey'
    )
    op.execute(
        'ALTER TABLE app.document_section RENAME CONSTRAINT document_section_pkey '
        'TO api_section_pkey'
    )
    op.execute(
        'ALTER TABLE app.document RENAME CONSTRAINT document_source_url_key '
        'TO api_document_source_url_key'
    )
    op.execute('ALTER TABLE app.document RENAME CONSTRAINT document_pkey TO api_document_pkey')

    op.rename_table('chunk', 'api_chunk', schema='app')
    op.rename_table('document_section', 'api_section', schema='app')
    op.rename_table('document', 'api_document', schema='app')
