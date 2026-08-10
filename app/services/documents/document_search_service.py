"""Drive/Notion 문서 검색·원문 조회 서비스 (SPEC 기능 7, 8).

검색은 2단계 후보 압축 구조다.

1. **1단계(캐시, 무료·빠름)**: `document_meta` 의 제목/URL 에 대해 토큰 매칭으로
   후보를 추린다. 후보가 0건이면 **본문 fetch 없이 즉시 빈 리스트를 반환**한다.
2. **2단계(실시간 fetch, 비쌈)**: 1단계 상위 후보 중 **fetch 예산
   (`_body_fetch_budget`) 건만** 본문을 실시간으로 가져와 스니펫을 만들고
   점수를 재계산한다. 예산은 `top_k` 를 오버스캔한 값(상한
   `MAX_BODY_FETCH_CANDIDATES`)이라, title_score 만으로 top_k 컷을 2단계
   이전에 확정하지 않는다 — 최종 top_k 컷은 본문까지 반영한 결합 점수로
   2단계가 정한다. 이 예산 상한이 Drive/Notion API rate limit 과 응답
   지연을 막는 핵심 장치다. fetch 는 응답 지연을 줄이기 위해
   `MAX_CONCURRENT_BODY_FETCHES` 를 상한으로 병렬 실행된다.

본문은 절대 캐시하지 않는다. `get_document` 도 항상 fetch 시점의 최신 원문을
돌려준다.

SPEC 기능 6 이후 소스는 project → folder_id/database_id 매핑에서 요청 시점에
만들어진다(`ProjectSourceResolver`). 같은 `source`(`drive`/`notion`) 라도
project 마다 다른 폴더/DB 를 가리키므로, 후보 행의 `project` 로 어댑터를
고른다(`row.source` 만으로는 부족).

`DocumentSearchOptions.query_variants` 로 호출자(Claude)가 동의어/유사
표현을 함께 넘기면 1단계 SQL 후보 필터만 넓어진다(서버가 자체적으로 LLM을
호출해 질의를 확장하지 않는다 — 그 판단과 비용은 호출자 쪽 모델이 진다).
점수 계산은 항상 원본 질의 토큰만 사용한다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from app.core.errors import IntegrationError, ValidationError
from app.core.logging import get_logger
from app.models.document_meta import ALLOWED_SOURCES, DocumentMeta
from app.models.openapi import DEFAULT_PROJECT
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.services.documents.sources.document_source import (
    NO_SOURCE_CONFIGURED_MESSAGE,
    DocumentSource,
)
from app.services.documents.project_source_resolver import ProjectSourceResolver
from app.services.documents.search_scorer import _body_score, _title_score, documents_tokenize
from app.services.documents.snippet_generator import _build_snippet, _fallback_snippet

_LOG = get_logger("docs_mcp.documents.search")


def _body_fetch_budget(top_k: int, candidate_count: int) -> int:
    """2단계 본문 fetch 예산을 계산한다.

    title_score 만으로 top_k 컷을 2단계 이전에 확정하면, 제목엔 안 걸리고
    본문에만 강하게 걸리는 문서가 애초에 fetch 기회조차 못 받는다. 그래서
    예산은 top_k 보다 `BODY_FETCH_OVERSCAN` 배 넓히되(제목 매칭 약한 후보도
    본문을 열어볼 기회를 준다), `MAX_BODY_FETCH_CANDIDATES` 로 상한을 씌우고
    (rate limit 보호), top_k 자체보다는 작아지지 않으며(최종 결과 수만큼은
    항상 fetch), 후보 수를 넘지 않는다.
    """
    overscan = min(top_k * BODY_FETCH_OVERSCAN, MAX_BODY_FETCH_CANDIDATES)
    return min(max(top_k, overscan), candidate_count)

MIN_TOP_K = 1
MAX_TOP_K = 50
#: 최종 점수에서 제목 매칭이 차지하는 비중(나머지는 본문 매칭).
TITLE_SCORE_WEIGHT = 0.4
BODY_SCORE_WEIGHT = 1.0 - TITLE_SCORE_WEIGHT
#: 2단계 본문 fetch 동시 실행 상한(Drive/Notion rate limit 보호).
MAX_CONCURRENT_BODY_FETCHES = 5
#: 본문 fetch 예산을 top_k 의 몇 배까지 오버스캔할지(title_score만으로 top_k
#: 컷을 2단계 이전에 확정하지 않기 위함).
BODY_FETCH_OVERSCAN = 3
#: 본문 fetch 예산의 절대 상한(오버스캔이 과도하게 커지지 않도록).
MAX_BODY_FETCH_CANDIDATES = 20

__all__ = [
    "DocumentSearchOptions",
    "DocumentSearchItem",
    "DocumentContent",
    "DocumentSearchService",
    "documents_tokenize",
]


@dataclass(frozen=True)
class DocumentSearchOptions:
    """문서 검색 옵션."""

    top_k: int = 5
    source: str | None = None
    project: str | None = None
    #: 호출자(Claude)가 원본 질의와 함께 넘기는 동의어/유사 표현.
    #: 1단계 SQL 후보 필터(search_by_tokens)만 넓히는 데 쓰고, 점수 계산에는
    #: 절대 섞이지 않는다 — 순위는 항상 원본 질의 토큰만으로 결정된다.
    query_variants: list[str] | None = None


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
            options: top_k·source·project 필터 및 query_variants(1단계 SQL
                후보 필터만 넓히는 호출자 제공 동의어).

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
        query_tokens = set(documents_tokenize(normalized_query))
        if not query_tokens:
            raise ValidationError("query must contain at least one searchable token")

        filter_tokens = query_tokens | self._variant_tokens(options.query_variants)
        candidates = self._select_candidates(
            filter_tokens, query_tokens, normalized_query, replace(options, source=normalized_source)
        )
        if not candidates:
            # 2단계를 건너뛴다: 후보가 없으면 외부 API 를 한 번도 호출하지 않는다.
            _LOG.debug("1단계 후보 0건 — 본문 fetch 생략: query=%s", normalized_query)
            return []

        return self._rank_with_body(candidates, query_tokens, normalized_query, options.top_k)

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

    def _variant_tokens(self, query_variants: list[str] | None) -> set[str]:
        """호출자가 넘긴 query_variants 를 SQL 후보 필터용 토큰으로 변환한다.

        빈 문자열/공백만 있는 항목은 documents_tokenize 가 빈 리스트를
        돌려주므로 자연히 걸러진다.
        """
        if not query_variants:
            return set()
        return set(documents_tokenize(" ".join(query_variants)))

    def _select_candidates(
        self,
        filter_tokens: set[str],
        score_tokens: set[str],
        query: str,
        options: DocumentSearchOptions,
    ) -> list[tuple[DocumentMeta, float]]:
        """제목/URL 토큰 매칭으로 상위 fetch 예산 건까지만 후보를 추린다.

        1차 필터(어떤 토큰이라도 포함하는 행)는 SQL 로 내리고, 점수 계산과
        순위 결정만 Python 이 한다. 전체 행을 적재하지 않으므로 캐시 규모가
        커져도 1단계가 가볍게 유지된다.

        `filter_tokens` (원본 + query_variants 토큰)는 SQL 후보 게이트를
        넓히는 데만 쓰고, 점수 계산은 반드시 `score_tokens`(원본 질의
        토큰)만 사용한다 — variant 토큰이 점수·순위 계산에 섞이면 무관한
        문서가 원본 질의와의 정합성 없이 상위에 노출될 수 있다.

        제목에 원본 토큰이 전혀 없어(title_score=0.0) variant 토큰으로만
        SQL 에 걸린 행도 후보 자체에서는 제외하지 않는다 — 애초에 "질의와
        문서 표현이 달라 제목 매칭이 실패하는" 문제를 variant 토큰으로 SQL
        후보만 넓혀 해결하는 것이므로, 여기서 다시 title_score 로 걸러내면
        확장 효과가 무력화된다.

        fetch 예산(`_body_fetch_budget`)은 rate limit 보호를 위해 여전히
        유한하므로, 그 한정된 예산을 원본 신호가 있는 행에 먼저 배분한다:
        정렬은 (원본 매치 여부 내림차순, title_score 내림차순, external_id)
        순이며, 원본 매치 행이 예산을 채우고 남는 자리만 variant-only 매치
        행이 채운다. 예산은 top_k 를 오버스캔해(`BODY_FETCH_OVERSCAN`)
        title_score 만으로 top_k 컷을 2단계 이전에 확정하지 않는다 — 최종
        top_k 컷은 2단계 본문 fetch 후 body_score(역시 원본 토큰만)까지
        반영한 결합 점수로 `_rank_with_body` 가 정한다.
        """
        rows = self._meta_repo.search_by_tokens(
            sorted(filter_tokens),
            source=options.source,
            project=options.project,
            queries=[query, *(options.query_variants or [])],
        )
        scored = [(row, _title_score(row, score_tokens, query)) for row in rows]
        scored.sort(key=lambda pair: (pair[1] <= 0.0, -pair[1], pair[0].external_id))
        budget = _body_fetch_budget(options.top_k, len(scored))
        return scored[:budget]

    # --- 2단계: 후보 본문 실시간 fetch ------------------------------------

    def _rank_with_body(
        self,
        candidates: list[tuple[DocumentMeta, float]],
        query_tokens: set[str],
        query: str,
        top_k: int,
    ) -> list[DocumentSearchItem]:
        """후보 본문을 병렬로 받아 스니펫을 만들고 최종 점수로 재정렬한 뒤 top_k 로 컷한다.

        개별 문서 fetch 실패는 그 문서만 건너뛴다(한 건의 권한 오류가 검색
        전체를 죽이지 않게 한다). 어댑터는 후보 행의 project 로 고른다.
        Drive/Notion 은 외부 API 호출이라 순차 fetch 시 지연이 합산되므로,
        `MAX_CONCURRENT_BODY_FETCHES` 를 상한으로 `ThreadPoolExecutor` 로
        병렬 fetch 한다. `executor.map` 은 완료 순서와 무관하게 입력 순서로
        결과를 모아주므로 공유 리스트에 동시 append 할 필요가 없다(스레드
        안전). 최종 정렬은 fetch 순서와 무관하게 score/title 로 다시 한다.

        `candidates` 는 fetch 예산(`_body_fetch_budget`)만큼 있을 수 있어
        top_k 보다 많다 — title_score 만으로는 걸러지지 않았던 문서도 여기서
        body_score 까지 반영한 결합 점수로 재평가된 뒤에야 최종 top_k 컷이
        일어난다.

        `self._resolver.resolve_for_project()` 는 요청-스코프 SQLAlchemy
        Session 을 읽으므로(스레드 세이프하지 않음) 워커 스레드에 맡기지
        않는다 — executor 를 만들기 전에 후보에 등장하는 project 별로
        메인 스레드에서 미리 resolve 해 두고, 워커에는 순수 I/O 인
        `document_source.fetch()` 만 맡긴다.
        """
        projects = {row.project for row, _ in candidates}
        sources_by_project = {
            project: self._resolver.resolve_for_project(project) for project in projects
        }
        max_workers = min(len(candidates), MAX_CONCURRENT_BODY_FETCHES)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = executor.map(
                lambda pair: self._fetch_and_score(
                    pair[0], pair[1], query_tokens, query, sources_by_project[pair[0].project]
                ),
                candidates,
            )
            items = [item for item in results if item is not None]
        items.sort(key=lambda item: (-item.score, item.title))
        return items[:top_k]

    def _fetch_and_score(
        self,
        row: DocumentMeta,
        title_score: float,
        query_tokens: set[str],
        query: str,
        sources: dict[str, DocumentSource],
    ) -> DocumentSearchItem | None:
        """후보 한 건의 본문을 fetch 해 점수를 매긴다. 실패/미구성이면 None.

        `sources` 는 호출측(`_rank_with_body`)이 메인 스레드에서 미리
        resolve 해 둔 값이다 — 이 메서드는 워커 스레드에서 실행되므로
        Session 을 쓰는 resolve 호출을 여기서 하면 안 된다.
        """
        document_source = sources.get(row.source)
        if document_source is None:
            _LOG.warning(
                "메타 캐시에 있으나 소스가 미구성됨: %s/%s", row.project, row.source
            )
            return None
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
            return None
        body_score = _body_score(body, query_tokens, query)
        return DocumentSearchItem(
            title=row.title,
            source=row.source,
            project=row.project,
            url=row.url,
            snippet=_build_snippet(body, query_tokens) or _fallback_snippet(row, query),
            score=round(TITLE_SCORE_WEIGHT * title_score + BODY_SCORE_WEIGHT * body_score, 4),
        )

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
