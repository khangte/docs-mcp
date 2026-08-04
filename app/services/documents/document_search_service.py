"""Drive/Notion 문서 검색·원문 조회 서비스 (SPEC 기능 7, 8).

검색은 2단계 후보 압축 구조다.

1. **1단계(캐시, 무료·빠름)**: `document_meta` 의 제목/URL 에 대해 토큰 매칭으로
   후보를 추린다. 후보가 0건이면 **본문 fetch 없이 즉시 빈 리스트를 반환**한다.
2. **2단계(실시간 fetch, 비쌈)**: 1단계 상위 후보 **최대 `top_k` 건만** 본문을
   실시간으로 가져와 스니펫을 만들고 점수를 재계산한다. 이 상한이 Drive/Notion
   API rate limit 과 응답 지연을 막는 핵심 장치다.

본문은 절대 캐시하지 않는다. `get_document` 도 항상 fetch 시점의 최신 원문을
돌려준다.

SPEC 기능 6 이후 소스는 project → folder_id/database_id 매핑에서 요청 시점에
만들어진다(`ProjectSourceResolver`). 같은 `source`(`drive`/`notion`) 라도
project 마다 다른 폴더/DB 를 가리키므로, 후보 행의 `project` 로 어댑터를
고른다(`row.source` 만으로는 부족).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.core.errors import IntegrationError, ValidationError
from app.core.logging import get_logger
from app.models.document_meta import ALLOWED_SOURCES, DocumentMeta
from app.models.openapi import DEFAULT_PROJECT
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.services.documents.document_source import (
    NO_SOURCE_CONFIGURED_MESSAGE,
    DocumentSource,
)
from app.services.documents.project_source_resolver import ProjectSourceResolver
from app.services.documents.search_scorer import _body_score, _title_score, tokenize
from app.services.documents.snippet_generator import _build_snippet, _fallback_snippet

_LOG = get_logger("docs_mcp.documents.search")

MIN_TOP_K = 1
MAX_TOP_K = 50
#: 최종 점수에서 제목 매칭이 차지하는 비중(나머지는 본문 매칭).
TITLE_SCORE_WEIGHT = 0.4
BODY_SCORE_WEIGHT = 1.0 - TITLE_SCORE_WEIGHT

__all__ = [
    "DocumentSearchOptions",
    "DocumentSearchItem",
    "DocumentContent",
    "DocumentSearchService",
    "tokenize",
]


@dataclass(frozen=True)
class DocumentSearchOptions:
    """문서 검색 옵션."""

    top_k: int = 5
    source: str | None = None
    project: str | None = None


@dataclass(frozen=True)
class DocumentSearchItem:
    """검색 결과 한 건."""

    title: str
    source: str
    project: str
    url: str
    snippet: str
    score: float


@dataclass(frozen=True)
class DocumentContent:
    """`get_document` 가 반환하는 원문 한 건."""

    title: str
    source: str
    url: str
    content: str


class DocumentSearchService:
    """메타 캐시 1단계 + 본문 실시간 fetch 2단계로 협업 문서를 검색한다."""

    def __init__(
        self,
        meta_repo: DocumentMetaRepository,
        resolver: ProjectSourceResolver,
    ) -> None:
        """저장소와 프로젝트 소스 리졸버를 보관한다.

        Args:
            meta_repo: `document_meta` 저장소(1단계 후보 조회용).
            resolver: project → Drive/Notion 어댑터 요청 시점 팩토리.
        """
        self._meta_repo = meta_repo
        self._resolver = resolver

    def search(self, query: str, options: DocumentSearchOptions) -> list[DocumentSearchItem]:
        """질의에 관련된 협업 문서를 찾아 스니펫과 점수를 붙여 반환한다.

        Args:
            query: 검색할 자연어/키워드 질의.
            options: top_k·source·project 필터.

        Returns:
            점수 내림차순 결과 리스트(최대 top_k 건). 1단계 후보가 없으면
            본문 fetch 없이 빈 리스트.

        Raises:
            ValidationError: 질의가 비었거나 top_k/source 값이 잘못된 경우.
            IntegrationError: 검색 대상 소스가 하나도 구성돼 있지 않은 경우.
                "결과 없음"(빈 리스트)과 "서버 미설정"을 구별하기 위해
                조용히 빈 리스트를 돌려주지 않는다.
        """
        normalized_query = self._validate(query, options)
        normalized_source = self._validate_source(options.source, allow_none=True)
        self._require_configured(normalized_source, options.project)
        query_tokens = set(tokenize(normalized_query))
        if not query_tokens:
            raise ValidationError("query must contain at least one searchable token")

        candidates = self._select_candidates(
            query_tokens, normalized_query, replace(options, source=normalized_source)
        )
        if not candidates:
            # 2단계를 건너뛴다: 후보가 없으면 외부 API 를 한 번도 호출하지 않는다.
            _LOG.debug("1단계 후보 0건 — 본문 fetch 생략: query=%s", normalized_query)
            return []

        return self._rank_with_body(candidates, query_tokens, normalized_query)

    def get_document(self, source: str, external_id: str) -> DocumentContent:
        """문서 한 건의 최신 원문을 조회한다(캐시된 본문이 아니다).

        project 는 인자로 받지 않는다(계약 유지). 어댑터 선택은
        `document_meta` 에서 `(source, external_id)` 를 가진 행의 project 로
        결정한다. 같은 external_id 가 여러 project 에 있으면 가장 최근
        `last_synced_at` 행을 쓴다. 메타에 없으면 `DEFAULT_PROJECT` 의 해당
        source 어댑터로 폴백한다.

        Args:
            source: `drive` 또는 `notion`.
            external_id: 출처 시스템의 문서 식별자.

        Returns:
            제목·출처·URL·본문을 담은 DTO. 제목/URL 은 메타 캐시에 있으면 그
            값을, 없으면 빈 문자열/식별자 기반 기본값을 쓴다.

        Raises:
            ValidationError: source 가 허용값이 아니거나 external_id 가 빈 경우.
            IntegrationError: 소스 미구성, 문서 없음, 외부 연동 실패.
        """
        normalized_source = self._validate_source(source, allow_none=False)
        source_str = str(normalized_source)
        normalized_id = (external_id or "").strip()
        if not normalized_id:
            raise ValidationError("external_id must not be empty")

        row = self._find_meta_row(source_str, normalized_id)
        project = row.project if row is not None else DEFAULT_PROJECT
        document_source = self._require_source(project, source_str)
        content = document_source.fetch(normalized_id)
        return DocumentContent(
            title=row.title if row else "",
            source=source_str,
            url=row.url if row else "",
            content=content,
        )

    def _find_meta_row(self, source: str, external_id: str) -> DocumentMeta | None:
        """(source, external_id) 를 가진 행 중 가장 최근 last_synced_at 행을 찾는다.

        같은 external_id 가 여러 project 에 공유될 수 있으므로(SPEC 기능 6
        검증 기준: A·B 소스에 공유된 문서는 2행), project 를 명시하지 않고
        source/external_id 만으로 조회한다.
        """
        rows = [
            row
            for row in self._meta_repo.list_all(source=source)
            if row.external_id == external_id
        ]
        if not rows:
            return None
        return max(rows, key=lambda row: row.last_synced_at)

    # --- 1단계: 메타 캐시 후보 압축 ----------------------------------------

    def _select_candidates(
        self, query_tokens: set[str], query: str, options: DocumentSearchOptions
    ) -> list[tuple[DocumentMeta, float]]:
        """제목/URL 토큰 매칭으로 상위 top_k 후보만 추린다.

        1차 필터(어떤 토큰이라도 포함하는 행)는 SQL 로 내리고, 점수 계산과
        순위 결정만 Python 이 한다. 전체 행을 적재하지 않으므로 캐시 규모가
        커져도 1단계가 가볍게 유지된다.

        2단계에서 fetch 하는 문서 수가 top_k 를 넘지 않도록, 여기서 이미
        top_k 로 잘라 반환한다.
        """
        rows = self._meta_repo.search_by_tokens(
            sorted(query_tokens), source=options.source, project=options.project, query=query
        )
        scored = [
            (row, score)
            for row, score in ((row, _title_score(row, query_tokens, query)) for row in rows)
            if score > 0.0
        ]
        # 동점 시 external_id 로 결정적 정렬(같은 입력 → 같은 후보 집합).
        scored.sort(key=lambda pair: (-pair[1], pair[0].external_id))
        return scored[: options.top_k]

    # --- 2단계: 후보 본문 실시간 fetch ------------------------------------

    def _rank_with_body(
        self,
        candidates: list[tuple[DocumentMeta, float]],
        query_tokens: set[str],
        query: str,
    ) -> list[DocumentSearchItem]:
        """후보 본문을 실시간으로 받아 스니펫을 만들고 최종 점수로 재정렬한다.

        개별 문서 fetch 실패는 그 문서만 건너뛴다(한 건의 권한 오류가 검색
        전체를 죽이지 않게 한다). 어댑터는 후보 행의 project 로 고른다.
        """
        items: list[DocumentSearchItem] = []
        for row, title_score in candidates:
            sources = self._resolver.resolve_for_project(row.project)
            document_source = sources.get(row.source)
            if document_source is None:
                _LOG.warning(
                    "메타 캐시에 있으나 소스가 미구성됨: %s/%s", row.project, row.source
                )
                continue
            try:
                body = document_source.fetch(row.external_id)
            except IntegrationError as exc:
                _LOG.warning(
                    "문서 본문 조회 실패(건너뜀): %s/%s/%s (%s)",
                    row.project,
                    row.source,
                    row.external_id,
                    exc,
                )
                continue
            body_score = _body_score(body, query_tokens, query)
            items.append(
                DocumentSearchItem(
                    title=row.title,
                    source=row.source,
                    project=row.project,
                    url=row.url,
                    snippet=_build_snippet(body, query_tokens) or _fallback_snippet(row, query),
                    score=round(
                        TITLE_SCORE_WEIGHT * title_score + BODY_SCORE_WEIGHT * body_score, 4
                    ),
                )
            )
        items.sort(key=lambda item: (-item.score, item.title))
        return items

    # --- 검증 헬퍼 --------------------------------------------------------

    def _validate(self, query: str, options: DocumentSearchOptions) -> str:
        """질의·top_k·source 를 검증하고 공백을 제거한 질의를 반환한다."""
        normalized_query = (query or "").strip()
        if not normalized_query:
            raise ValidationError("query must not be empty")
        if not MIN_TOP_K <= options.top_k <= MAX_TOP_K:
            raise ValidationError(
                f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}: {options.top_k}"
            )
        self._validate_source(options.source, allow_none=True)
        return normalized_query

    def _validate_source(self, source: str | None, allow_none: bool) -> str | None:
        """source 값이 허용 범위인지 확인하고 정규화한 값을 반환한다."""
        if source is None:
            if allow_none:
                return None
            raise ValidationError("source must be one of: " + ", ".join(sorted(ALLOWED_SOURCES)))
        normalized = source.strip().lower()
        if normalized not in ALLOWED_SOURCES:
            raise ValidationError(
                f"unknown source: {source} (allowed: {', '.join(sorted(ALLOWED_SOURCES))})"
            )
        return normalized

    def _require_configured(self, source: str | None, project: str | None) -> None:
        """검색 대상 소스가 구성돼 있는지 확인한다.

        "구성은 됐는데 결과가 0건"인 정상 케이스와 "서버에 소스가 아예 설정되지
        않음"을 구별하기 위한 검사다. 전자는 계속 빈 리스트를 돌려줘야 하므로
        여기서는 **구성 여부만** 보고 캐시 내용은 보지 않는다.

        project 가 주어지면 그 project 의 매핑만 보고, 없으면 등록된 전체
        (project, source) 쌍을 본다.

        Raises:
            IntegrationError: 소스가 하나도 없거나, 지정한 source 가 미구성인 경우.
        """
        if project is not None:
            available = self._resolver.resolve_for_project(project)
        else:
            available = {s.source_name: s for _, s in self._resolver.resolve_all()}
        if not available:
            raise IntegrationError(NO_SOURCE_CONFIGURED_MESSAGE)
        if source is not None and source not in available:
            raise IntegrationError(f"document source is not configured: {source}")

    def _require_source(self, project: str, source: str) -> DocumentSource:
        """구성된 어댑터를 반환하고, 없으면 IntegrationError 를 던진다."""
        document_source = self._resolver.resolve_for_project(project).get(source)
        if document_source is None:
            raise IntegrationError(f"document source is not configured: {source}")
        return document_source
