"""Drive/Notion 문서 메타 캐시 저장소."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.document_meta import DocumentMeta


class DocumentMetaRepository:
    """`document_meta` CRUD."""

    def __init__(self, session: Session) -> None:
        """세션을 보관해 이후 쿼리에 사용한다."""
        self._session = session

    def add(self, meta: DocumentMeta) -> None:
        """메타 행을 세션에 추가한다."""
        self._session.add(meta)

    def find(self, source: str, external_id: str) -> DocumentMeta | None:
        """(source, external_id) 조합으로 메타 행 한 건을 조회한다."""
        stmt = select(DocumentMeta).where(
            DocumentMeta.source == source,
            DocumentMeta.external_id == external_id,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def list_by_source(self, source: str) -> Sequence[DocumentMeta]:
        """특정 source 의 메타 행을 external_id 오름차순으로 반환한다."""
        stmt = (
            select(DocumentMeta)
            .where(DocumentMeta.source == source)
            .order_by(DocumentMeta.external_id)
        )
        return self._session.execute(stmt).scalars().all()

    def list_all(self, source: str | None = None) -> Sequence[DocumentMeta]:
        """메타 행 전체를 반환한다. source 를 주면 해당 출처로만 제한한다."""
        stmt = select(DocumentMeta)
        if source is not None:
            stmt = stmt.where(DocumentMeta.source == source)
        stmt = stmt.order_by(DocumentMeta.source, DocumentMeta.external_id)
        return self._session.execute(stmt).scalars().all()

    def search_by_tokens(
        self, tokens: Sequence[str], source: str | None = None
    ) -> Sequence[DocumentMeta]:
        """제목 또는 URL 에 토큰 중 하나라도 포함된 행만 SQL 로 걸러 반환한다.

        1단계 후보 압축용이다. 전체 행을 ORM 객체로 적재한 뒤 Python 에서
        버리면 문서 수에 선형 비례하는 낭비가 생기므로, 매칭 자체를 DB 로
        내린다(같은 브랜치의 `ChunkRepository.list_endpoint_chunks()` 와 동일 원칙).

        점수 계산은 여전히 Python 이 담당한다. SQL 은 "가능성 있는 행"만
        좁혀주고, 최종 순위는 토큰 겹침 비율로 서비스가 정한다.

        Args:
            tokens: 소문자로 정규화된 질의 토큰. 비어 있으면 빈 결과를 돌려준다.
            source: 특정 출처로 범위를 제한할 때 지정.

        Returns:
            (source, external_id) 순으로 결정적으로 정렬된 후보 행.
        """
        if not tokens:
            return []
        stmt = select(DocumentMeta)
        if source is not None:
            stmt = stmt.where(DocumentMeta.source == source)
        # ILIKE 는 대소문자를 무시한다. 토큰에 든 LIKE 와일드카드는 이스케이프해
        # 사용자 입력이 패턴으로 해석되지 않게 한다.
        patterns = [f"%{_escape_like(token)}%" for token in tokens]
        stmt = stmt.where(
            or_(
                *[DocumentMeta.title.ilike(p, escape="\\") for p in patterns],
                *[DocumentMeta.url.ilike(p, escape="\\") for p in patterns],
            )
        )
        stmt = stmt.order_by(DocumentMeta.source, DocumentMeta.external_id)
        return self._session.execute(stmt).scalars().all()

    def delete(self, meta: DocumentMeta) -> None:
        """메타 행을 세션에서 삭제한다."""
        self._session.delete(meta)


def _escape_like(token: str) -> str:
    """LIKE/ILIKE 패턴에서 특수 의미를 갖는 문자를 이스케이프한다."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
