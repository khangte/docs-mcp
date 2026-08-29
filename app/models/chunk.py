"""검색 코어(포맷 무관) ORM 모델 — 청크 텍스트 + 임베딩 + FTS."""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.document import Document

#: `intfloat/multilingual-e5-small` 고정 차원. 로컬 모델은 dim 이 모델 자체에
#: 고정돼 독립 튜닝 대상이 아니므로, 이 상수 하나가 pgvector 컬럼 dim 의
#: 유일한 진실원이다(부트스트랩에서 provider.dim 과 일치를 assert 한다).
EMBEDDING_DIM = 384

#: `chunk.text_tsv` 생성 컬럼식(모델·alembic 마이그레이션이 공유 — 두 곳이
#: 어긋나면 alembic autogenerate 가 스푸리어스 diff 를 낼 수 있다).
#: 순서: (1) ASCII/언더스코어→한글 경계에 공백 삽입 (2) 한글→ASCII/언더스코어
#: 경계에 공백 삽입 (3) 나머지(영숫자/언더스코어/한글이 아닌 모든 문자)를
#: 공백으로 치환 (4) `simple` config 로 tsvector 화.
#: (1)(2) 가 없으면 'GET요청' 같은 스크립트 혼합 복합어가 단일 lexeme
#: 'get요청' 으로 뭉쳐, 순수 ASCII/한글 질의 어느 쪽으로도 안 잡힌다
#: (architect 판단, ASCII recall 동치 게이트 보존).
TEXT_TSV_EXPRESSION = (
    r"to_tsvector('simple', regexp_replace(regexp_replace(regexp_replace("
    r"text, '([0-9A-Za-z_])([가-힣])', '\1 \2', 'g'), "
    r"'([가-힣])([0-9A-Za-z_])', '\1 \2', 'g'), "
    r"'[^0-9A-Za-z_가-힣]', ' ', 'g'))"
)


def _norm_sql(column: str) -> str:
    """`TEXT_TSV_EXPRESSION` 이 `text` 에 쓰는 3단 정규화를 임의 컬럼에 적용한다.

    (1) ASCII/언더스코어→한글 경계 공백 삽입 (2) 한글→ASCII/언더스코어 경계
    공백 삽입 (3) 나머지 문자를 공백으로 치환. 질의 측 토크나이저
    (`keyword_search.tokenize_terms`)와 토큰 경계를 대칭으로 유지한다.
    """
    return (
        r"regexp_replace(regexp_replace(regexp_replace("
        + column
        + r", '([0-9A-Za-z_])([가-힣])', '\1 \2', 'g'), "
        r"'([가-힣])([0-9A-Za-z_])', '\1 \2', 'g'), "
        r"'[^0-9A-Za-z_가-힣]', ' ', 'g')"
    )


#: `chunk.search_tsv` 생성 컬럼식(`docs/architect-review/78` §5.1).
#: leaf(A) / intent(B) / context(C) / 기존 text(D) 를 `setweight` 로 묶는다.
#: D 가 기존 `text` 전체이므로 이 벡터의 lexeme 집합은 항상 `text_tsv` 의
#: 상위집합이다 — 이 변경으로 후보 집합이 줄어들 수 없다(78번 §8.3 불변식).
#: endpoint 가 아닌 청크는 NULL 이라 부분 GIN 인덱스 비용이 0이다.
SEARCH_TSV_EXPRESSION = (
    "CASE WHEN chunk_type = 'endpoint' THEN "
    f"setweight(to_tsvector('simple', {_norm_sql('leaf_text')}), 'A') || "
    f"setweight(to_tsvector('simple', {_norm_sql('intent_text')}), 'B') || "
    f"setweight(to_tsvector('simple', {_norm_sql('context_text')}), 'C') || "
    f"setweight(to_tsvector('simple', {_norm_sql('text')}), 'D') "
    "ELSE NULL END"
)


class Chunk(Base):
    """검색용 청크(텍스트 + 임베딩 + FTS) ORM 모델.

    embedding 컬럼 차원은 pgvector 제약상 테이블 생성 시 고정된다.
    EMBEDDING_DIM 변경 시 새 마이그레이션(컬럼 재생성 + 데이터 재색인)이 필요하다.
    `text_tsv` 는 `text` 로부터 DB 가 자동 채우는 STORED generated 컬럼이라
    ORM 이 값을 직접 쓰지 않는다(인서트/업데이트 시 코드 변경 불필요).
    """

    __tablename__ = "chunk"
    __table_args__ = (
        Index(
            "ix_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunk_text_tsv", "text_tsv", postgresql_using="gin"),
        Index(
            "ix_chunk_search_tsv",
            "search_tsv",
            postgresql_using="gin",
            postgresql_where=text("chunk_type = 'endpoint'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    chunk_type: Mapped[str] = mapped_column(String(32), nullable=False)  # endpoint|schema
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: 가중치 A — target leaf 자원 토큰(`endpoint_structure.derive_endpoint_structure`).
    #: endpoint 가 아닌 청크는 빈 문자열이다.
    leaf_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: 가중치 B — operation alias + summary 원문.
    intent_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: 가중치 C — ancestor 경로·param 이름·tags·operationId subword.
    context_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    #: 키워드 검색용 FTS 벡터(simple config, 한글 포함). 필터 전용 — select 하지 않는다.
    #: 식은 `TEXT_TSV_EXPRESSION` 참조(경로 컴파운드·스크립트 혼합 복합어 방지
    #: 위한 정규화 근거는 그쪽 주석에 있다). 질의 측 토크나이저
    #: (`[0-9A-Za-z_]+|[가-힣]+`, keyword_search.tokenize_terms)와 토큰 경계가
    #: 대칭이어야 한다(architect 판단, ASCII recall 동치 게이트 보존).
    text_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(TEXT_TSV_EXPRESSION, persisted=True),
        nullable=True,
        deferred=True,
    )
    #: 엔드포인트 키워드 arm 용 가중 FTS 벡터(78번 설계). 필터 전용 — 일반
    #: 조회에서 select 하지 않는다. 식은 `SEARCH_TSV_EXPRESSION` 참조.
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(SEARCH_TSV_EXPRESSION, persisted=True),
        nullable=True,
        deferred=True,
    )

    document: Mapped[Document] = relationship(back_populates="chunks")
