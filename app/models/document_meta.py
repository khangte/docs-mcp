"""Drive/Notion 문서 메타데이터 캐시 ORM 모델.

`document_meta` 는 협업 문서(Google Drive / Notion)의 **메타데이터만** 보관한다.
본문은 저장하지 않고 검색 시점에 원본 API 에서 실시간으로 가져온다(SPEC 기능 6).
다른 도메인 모델과 같은 `Base`/스키마를 재사용하되, 성격이 다른 도메인이므로
파일을 분리한다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import PROJECT_MAX_LENGTH, Base

#: `document_meta.source` 로 허용되는 값.
SOURCE_DRIVE = "drive"
SOURCE_NOTION = "notion"
ALLOWED_SOURCES: frozenset[str] = frozenset({SOURCE_DRIVE, SOURCE_NOTION})


def _utcnow() -> datetime:
    """현재 UTC 시각을 반환한다."""
    return datetime.now(timezone.utc)


class DocumentMeta(Base):
    """Drive/Notion 문서 한 건의 메타데이터(본문 제외).

    Attributes:
        id: 자동 증가 기본키.
        project: 소속 프로젝트명.
        source: 문서 출처(`drive` 또는 `notion`).
        external_id: 출처 시스템의 문서 식별자(Drive file_id / Notion page_id).
        title: 문서 제목(1단계 후보 압축의 키워드 매칭 대상).
        url: 사람이 열어볼 수 있는 원본 문서 URL.
        modified_at: 원본 시스템 기준 최종 수정 시각(변경 감지에 사용).
        last_synced_at: 이 행이 마지막으로 갱신된 서버 시각.
        document_id: 본문이 색인됐을 때 대응하는 `document.id`(결정적 ID:
            `deterministic_document_id` 가 만드는 `f"{source}:{sha256(...)[:16]}"`
            해시, `document_body_indexer.py` 참조). 본문 색인 전이거나
            원본 `Document` 가 삭제되면 NULL.
        mime_type: 출처 시스템의 MIME 타입(Drive 전용, Notion 은 항상 NULL).
        created_at: 원본 시스템 기준 생성 시각. Drive/Notion 모두 채워진다.
        owner: 문서 소유자 이메일 또는 표시 이름(Drive 전용, Notion 은 항상 NULL).
    """

    __tablename__ = "document_meta"
    __table_args__ = (
        UniqueConstraint(
            "project", "source", "external_id", name="uq_document_meta_project_source_external"
        ),
        Index("ix_document_meta_source", "source"),
        Index("ix_document_meta_project", "project"),
        #: `get_document` 의 (source, external_id) 포인트 조회
        #: (`find_latest_by_source_and_external_id`) 전용 인덱스.
        Index("ix_document_meta_source_external_id", "source", "external_id"),
        #: title/url 의 선행 와일드카드 ILIKE('%token%')를 GIN trgm 인덱스로 처리하기 위함
        #: (1단계 후보 필터, `DocumentMetaRepository.search_by_tokens`).
        Index(
            "ix_document_meta_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "ix_document_meta_url_trgm",
            "url",
            postgresql_using="gin",
            postgresql_ops={"url": "gin_trgm_ops"},
        ),
        #: FK 는 PostgreSQL 이 인덱스를 자동 생성하지 않는다. keyword/vector arm 의
        #: EXISTS 서브쿼리(document_meta.document_id = <chunk 의 document_id>)와
        #: 기존 list_by_document_ids 의 IN 조회가 이 인덱스를 쓴다.
        Index("ix_document_meta_document_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project: Mapped[str] = mapped_column(String(PROJECT_MAX_LENGTH), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    modified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), nullable=True
    )
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(320), nullable=True)
