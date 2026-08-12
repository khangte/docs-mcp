"""Drive/Notion 문서 메타데이터 캐시 ORM 모델.

`document_meta` 는 협업 문서(Google Drive / Notion)의 **메타데이터만** 보관한다.
본문은 저장하지 않고 검색 시점에 원본 API 에서 실시간으로 가져온다(SPEC 기능 6).
다른 도메인 모델과 같은 `Base`/스키마를 재사용하되, 성격이 다른 도메인이므로
파일을 분리한다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
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
