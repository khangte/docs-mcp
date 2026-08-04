"""Drive/Notion 문서 메타 캐시 저장소."""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.document_meta import DocumentMeta

_WHITESPACE_RE = re.compile(r"\s+")


def collapse(text: str) -> str:
    """공백을 모두 제거하고 소문자화한 문자열을 반환한다.

    '트러블슈팅'과 '트러블 슈팅'처럼 공백 유무만 다른 질의/제목이 서로
    다른 토큰 집합으로 쪼개져 매칭에 실패하는 문제를 흡수하기 위한 보조
    키다. SQL 1단계 필터(`search_by_tokens`)와 서비스 2단계 점수 계산
    (`_title_score`/`_body_score`) 양쪽에서 이 함수 하나만 공유해 써야
    두 계층의 판단이 어긋나지 않는다.
    """
    return _WHITESPACE_RE.sub("", text or "").lower()


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
        query: str = "",
    ) -> Sequence[DocumentMeta]:
        """제목 또는 URL 에 토큰 중 하나라도 포함된 행만 SQL 로 걸러 반환한다.

        1차 필터(어떤 토큰이라도 포함하는 행)는 SQL 로 내리고, 점수 계산과
        순위 결정만 Python 이 한다. 전체 행을 적재하지 않으므로 캐시 규모가
        커져도 1단계가 가볍게 유지된다.

        점수 계산은 여전히 Python 이 담당한다. SQL 은 "가능성 있는 행"만
        좁혀주고, 최종 순위는 토큰 겹침 비율로 서비스가 정한다.

        토큰 패턴 외에 질의 전체를 `collapse()` 한(공백 제거) 패턴도 OR 로
        더한다. '트러블슈팅'(공백 없음) 질의로 '트러블 슈팅'(공백 있음)
        제목을 찾거나 그 반대인 경우, 토큰 단위 매칭만으로는 이 1단계에서
        후보가 아예 빠져 2단계(본문 fetch)까지 못 간다.

        Args:
            tokens: 소문자로 정규화된 질의 토큰. 비어 있으면 빈 결과를 돌려준다.
            source: 특정 출처로 범위를 제한할 때 지정.
            project: 특정 project 로 범위를 제한할 때 지정.
            query: 원본 질의 문자열. 공백을 제거한(`collapse`) 패턴을 추가
                매칭 조건으로 쓴다. 비어 있으면 이 추가 조건은 생략된다.

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
        conditions = [
            *[DocumentMeta.title.ilike(p, escape="\\") for p in patterns],
            *[DocumentMeta.url.ilike(p, escape="\\") for p in patterns],
        ]
        collapsed_query = collapse(query)
        if collapsed_query:
            # title/url 도 공백을 제거한 뒤 비교해야 '트러블슈팅' 질의가
            # '트러블 슈팅'(공백 있음) title 과 매칭된다. 원본 title 에 그냥
            # collapse 된 패턴을 대면 공백 자체가 어긋나 항상 실패한다.
            collapsed_pattern = f"%{_escape_like(collapsed_query)}%"
            collapsed_title = func.replace(func.lower(DocumentMeta.title), " ", "")
            collapsed_url = func.replace(func.lower(DocumentMeta.url), " ", "")
            conditions.append(collapsed_title.ilike(collapsed_pattern, escape="\\"))
            conditions.append(collapsed_url.ilike(collapsed_pattern, escape="\\"))
        stmt = stmt.where(or_(*conditions))
        stmt = stmt.order_by(DocumentMeta.source, DocumentMeta.external_id)
        return self._session.execute(stmt).scalars().all()

    def delete(self, meta: DocumentMeta) -> None:
        """메타 행을 세션에서 삭제한다."""
        self._session.delete(meta)


def _escape_like(token: str) -> str:
    """LIKE/ILIKE 패턴에서 특수 의미를 갖는 문자를 이스케이프한다."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
