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

    def find(self, project: str, source: str, external_id: str) -> DocumentMeta | None:
        """(project, source, external_id) 조합으로 메타 행 한 건을 조회한다."""
        stmt = select(DocumentMeta).where(
            DocumentMeta.project == project,
            DocumentMeta.source == source,
            DocumentMeta.external_id == external_id,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def list_by_project_source(self, project: str, source: str) -> Sequence[DocumentMeta]:
        """(project, source) 조합의 기존 행 집합을 반환한다.

        갱신 시 삭제 감지의 기준 집합으로 쓰인다. `list_by_source(source)` 를
        쓰면 다른 project 의 행까지 "원본에서 사라진 것"으로 오인해 지워버리는
        교차 프로젝트 삭제 사고로 이어지므로, project 로 반드시 좁힌 이 조회를
        갱신 경로에서 사용한다.
        """
        stmt = (
            select(DocumentMeta)
            .where(DocumentMeta.project == project, DocumentMeta.source == source)
            .order_by(DocumentMeta.external_id)
        )
        return self._session.execute(stmt).scalars().all()

    def list_by_source(
        self, source: str, project: str | None = None
    ) -> Sequence[DocumentMeta]:
        """특정 source 의 메타 행을 external_id 오름차순으로 반환한다.

        Args:
            source: 대상 출처.
            project: 주어지면 해당 project 로 범위를 제한한다.
        """
        stmt = select(DocumentMeta).where(DocumentMeta.source == source)
        if project is not None:
            stmt = stmt.where(DocumentMeta.project == project)
        stmt = stmt.order_by(DocumentMeta.external_id)
        return self._session.execute(stmt).scalars().all()

    def list_all(
        self, source: str | None = None, project: str | None = None
    ) -> Sequence[DocumentMeta]:
        """메타 행 전체를 반환한다. source/project 를 주면 그 범위로 제한한다."""
        stmt = select(DocumentMeta)
        if source is not None:
            stmt = stmt.where(DocumentMeta.source == source)
        if project is not None:
            stmt = stmt.where(DocumentMeta.project == project)
        stmt = stmt.order_by(DocumentMeta.source, DocumentMeta.external_id)
        return self._session.execute(stmt).scalars().all()

    def search_by_tokens(
        self,
        tokens: Sequence[str],
        source: str | None = None,
        project: str | None = None,
    ) -> Sequence[DocumentMeta]:
        """제목 또는 URL 에 토큰 중 하나라도 포함된 행만 SQL 로 걸러 반환한다.

        1차 필터(어떤 토큰이라도 포함하는 행)는 SQL 로 내리고, 점수 계산과
        순위 결정만 Python 이 한다. 전체 행을 적재하지 않으므로 캐시 규모가
        커져도 1단계가 가볍게 유지된다.

        점수 계산은 여전히 Python 이 담당한다. SQL 은 "가능성 있는 행"만
        좁혀주고, 최종 순위는 토큰 겹침 비율로 서비스가 정한다.

        Args:
            tokens: 소문자로 정규화된 질의 토큰. 비어 있으면 빈 결과를 돌려준다.
            source: 특정 출처로 범위를 제한할 때 지정.
            project: 특정 project 로 범위를 제한할 때 지정.

        Returns:
            (source, external_id) 순으로 결정적으로 정렬된 후보 행.
        """
        if not tokens:
            return []
        stmt = select(DocumentMeta)
        if source is not None:
            stmt = stmt.where(DocumentMeta.source == source)
        if project is not None:
            stmt = stmt.where(DocumentMeta.project == project)
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
