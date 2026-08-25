"""Drive/Notion 문서 메타 캐시 저장소."""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.document_meta import DocumentMeta
from app.repositories.document_filters import DocumentMetaFilter, document_meta_conditions

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

    def search_by_tokens(
        self,
        tokens: Sequence[str],
        source: str | None = None,
        project: str | None = None,
        queries: Sequence[str] = (),
        meta_filter: DocumentMetaFilter | None = None,
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
            queries: 원본 질의 및 variant 문자열 목록. 각 문자열을 공백
                제거한(`collapse`) 패턴으로 만들어 추가 매칭 조건으로 쓴다.
                비어 있거나 collapse 결과가 중복/빈 문자열이면 해당 조건은
                생략된다.
            meta_filter: 날짜/mimeType hard filter. None 이거나 비어 있으면
                조건 없이 기존과 동일하게 동작한다.

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
        # title/url 도 공백을 제거한 뒤 비교해야 '트러블슈팅' 질의가
        # '트러블 슈팅'(공백 있음) title 과 매칭된다. 원본 title 에 그냥
        # collapse 된 패턴을 대면 공백 자체가 어긋나 항상 실패한다.
        collapsed_title = func.replace(func.lower(DocumentMeta.title), " ", "")
        collapsed_url = func.replace(func.lower(DocumentMeta.url), " ", "")
        seen_collapsed: set[str] = set()
        for q in queries:
            collapsed_query = collapse(q)
            if not collapsed_query or collapsed_query in seen_collapsed:
                continue
            seen_collapsed.add(collapsed_query)
            collapsed_pattern = f"%{_escape_like(collapsed_query)}%"
            conditions.append(collapsed_title.ilike(collapsed_pattern, escape="\\"))
            conditions.append(collapsed_url.ilike(collapsed_pattern, escape="\\"))
        stmt = stmt.where(or_(*conditions))
        if meta_filter is not None and not meta_filter.is_empty():
            stmt = stmt.where(*document_meta_conditions(meta_filter))
        stmt = stmt.order_by(DocumentMeta.source, DocumentMeta.external_id)
        return self._session.execute(stmt).scalars().all()

    def list_by_document_ids(self, document_ids: Sequence[str]) -> Sequence[DocumentMeta]:
        """`document_id`(FK, `document.id`) 집합에 대응하는 메타 행을 배치 조회한다.

        문서 검색(doc36 Phase3)이 청크 arm(FTS/벡터) 결과의 `Chunk.document_id`
        를 표시용 메타(title/url/project/source)로 역매핑할 때 쓴다. 문서당
        반복 조회를 피하기 위한 배치 조회이므로 결과당 `find()` 를 호출하지
        않는다. `document_id` 가 NULL(본문 미색인)인 행은 대상이 아니다.
        """
        if not document_ids:
            return []
        stmt = select(DocumentMeta).where(DocumentMeta.document_id.in_(document_ids))
        return self._session.execute(stmt).scalars().all()

    def find_latest_by_source_and_external_id(
        self, source: str, external_id: str
    ) -> DocumentMeta | None:
        """(source, external_id) 를 가진 행 중 가장 최근 last_synced_at 행 한 건을 조회한다.

        `get_document` 전용 포인트 조회다. project 를 명시하지 않는 이유는
        같은 external_id 가 여러 project 에 공유될 수 있어서다(SPEC 기능 6
        검증 기준). `list_all()` 로 그 source 의 행 전체를 앱에 적재한 뒤
        Python 에서 걸러내던 이전 방식과 달리, WHERE + ORDER BY + LIMIT 1 을
        SQL 에 내려 한 행만 왕복한다.
        """
        stmt = (
            select(DocumentMeta)
            .where(DocumentMeta.source == source, DocumentMeta.external_id == external_id)
            .order_by(DocumentMeta.last_synced_at.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalars().first()

    def delete(self, meta: DocumentMeta) -> None:
        """메타 행을 세션에서 삭제한다."""
        self._session.delete(meta)


def _escape_like(token: str) -> str:
    """LIKE/ILIKE 패턴에서 특수 의미를 갖는 문자를 이스케이프한다."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
