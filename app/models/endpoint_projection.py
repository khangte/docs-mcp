"""Endpoint canonical projection ORM 모델 (`docs/architect-review/101` §2.1).

문서의 endpoint 당 정확히 한 행. `chunk` 와 별개이며 검색의 3번째 RRF arm
(`endpoint_repr`) 전용 lexical/dense 입력이다. physical identity 는
`endpoint_id -> api_endpoint.id` FK(`ON DELETE CASCADE`), 안정 natural/audit
key 는 `(document_id, method, path)` unique constraint 다. 재색인은 endpoint
row 를 delete/recreate 하므로 cascade 로 이 행도 함께 사라졌다가 재생성된다.
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.chunk import EMBEDDING_DIM
from app.models.document import Document
from app.models.openapi import ApiEndpoint

#: `endpoint_search_projection.canonical_tsv` 생성 컬럼식(모델·alembic 공유 —
#: 어긋나면 autogenerate 가 스푸리어스 diff 를 낸다). `canonical_text` 는 영문
#: 구조 template 이지만, 질의 측 토크나이저(`[0-9A-Za-z_]+|[가-힣]+`)와 토큰
#: 경계를 대칭으로 두려고 같은 문자류만 남기고 나머지를 공백으로 바꾼 뒤
#: `simple` config 로 tsvector 화한다.
CANONICAL_TSV_EXPRESSION = (
    r"to_tsvector('simple', "
    r"regexp_replace(canonical_text, '[^0-9A-Za-z_가-힣]', ' ', 'g'))"
)


class EndpointSearchProjection(Base):
    """endpoint 1건의 결정적 canonical projection(FTS + embedding 입력).

    `canonical_tsv` 는 `canonical_text` 로부터 DB 가 채우는 STORED generated
    컬럼이라 ORM 이 값을 직접 쓰지 않는다. `embedding` 은 semantic provider 가
    있을 때만 채워지고, 없으면 NULL 로 남아 arm 이 dense lookup 을 건너뛴다.
    """

    __tablename__ = "endpoint_search_projection"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "method", "path", name="uq_endpoint_projection_doc_method_path"
        ),
        Index(
            "ix_endpoint_projection_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_endpoint_projection_canonical_tsv", "canonical_tsv", postgresql_using="gin"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("api_endpoint.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    #: 필터 전용 FTS 벡터. 식은 `CANONICAL_TSV_EXPRESSION` 참조.
    canonical_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(CANONICAL_TSV_EXPRESSION, persisted=True),
        nullable=True,
        deferred=True,
    )
    #: projection format 식별자(`endpoint_projection.REPRESENTATION_VERSION`).
    representation_version: Mapped[str] = mapped_column(String(16), nullable=False)
    #: `sha256(version + "\n" + canonical_text)` — 재색인/백필 판단·trace 재현용.
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    endpoint: Mapped[ApiEndpoint] = relationship()
    document: Mapped[Document] = relationship()
