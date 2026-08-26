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

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime

from app.core.errors import IntegrationError, ValidationError
from app.core.logging import get_logger
from app.models import DEFAULT_PROJECT
from app.models.document_meta import ALLOWED_SOURCES, DocumentMeta
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_filters import DocumentMetaFilter
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.services.documents.document_body_indexer import deterministic_document_id
from app.services.documents.project_source_resolver import ProjectSourceResolver
from app.services.documents.search_scorer import (
    COMPOUND_TERM_LIMIT,
    _body_score,
    _passes_title_gate,
    _title_score,
    compound_terms_for_tokens,
    documents_tokenize,
)
from app.services.documents.snippet_generator import _build_snippet, _fallback_snippet
from app.services.documents.sources.document_source import (
    NO_SOURCE_CONFIGURED_MESSAGE,
    DocumentSource,
)
from app.services.documents.sources.time_parsing import parse_rfc3339
from app.services.documents.version_parser import parse_version
from app.services.indexer.embedding_provider import EmbeddingProvider
from app.services.search.rrf import (
    ARM_KEYWORD,
    ARM_TITLE,
    ARM_VECTOR,
    FusedResult,
    reciprocal_rank_fuse,
)

_LOG = get_logger("docs_mcp.documents.search")

#: indexed 전략(RRF) 각 arm에서 가져올 후보 폭. 엔드포인트 경로
#: (`EndpointCandidateSearch`)와 동일 규칙(`docs/architect-review/39` §2.1).
_RRF_MIN_CANDIDATE_WIDTH = 50
_RRF_CANDIDATE_WIDTH_MULTIPLIER = 4
#: `document_search_strategy` 가 이 값일 때만 색인 기반(RRF) 경로를 쓴다.
#: 그 외(미인식 값 포함)는 모두 기존 fetch 경로로 degrade한다(doc39 §2.7).
DOCUMENT_SEARCH_STRATEGY_INDEXED = "indexed"

#: title arm 가중치. title 후보는 permissive 한 ILIKE 부분문자열 게이트에서 나오고,
#: RRF 안에서는 등수 차이가 거의 소멸해(k=60) 사실상 "존재 보너스"로 작동한다.
#: 본문 신호(keyword/vector) arm 의 절반으로 둬 제목만 스친 문서가 본문 정답 문서와
#: 동급이 되는 것을 막는다(57번 리뷰 §5 개선3). 평가셋이 없어 튜닝 근거가 없으므로
#: env 로 노출하지 않고 상수로 고정한다(RRF_K 와 같은 방침).
TITLE_ARM_WEIGHT = 0.5

#: `mime_types` 필터 원소 개수/길이 상한(개선 #2 — T8 검증).
_MAX_MIME_TYPES = 20
_MAX_MIME_TYPE_LENGTH = 128
#: `owners` 필터 원소 개수/길이 상한. 길이는 `document_meta.owner` 컬럼 폭과 같다.
_MAX_OWNERS = 20
_MAX_OWNER_LENGTH = 320

#: keyword/vector arm 이 SQL 에서 이미 `chunk_type='section'` 으로 좁혀 조회하므로,
#: 승자 청크의 chunk_type 은 DB 를 다시 읽지 않고 이 상수로 취급한다(57번 리뷰 §5 개선1).
#: 나중에 다른 chunk_type 이 이 arm 들에 들어오면, 그때는 이 상수 대신 히트 DTO
#: (`ChunkTextHit`/`ChunkVectorHit`) 에 chunk_type 필드를 얹어야 한다.
_SECTION_CHUNK_TYPE = "section"

#: `DocumentSearchItem.match_reasons` 에 실리는 고정 문자열(57번 리뷰 §5 개선1).
#: LLM 이 그대로 파싱하는 계약이므로 임의로 바꾸지 않는다.
REASON_TITLE_MATCH = "제목·URL 매칭"
REASON_KEYWORD_MATCH = "본문 키워드 일치"
REASON_VECTOR_MATCH = "본문 의미 유사"
REASON_UNINDEXED = "본문 미색인 — 제목 매칭만으로 검색됨"
REASON_LIVE_FETCH_MATCH = "실시간 본문 매칭"
_ARM_REASON = {
    ARM_TITLE: REASON_TITLE_MATCH,
    ARM_KEYWORD: REASON_KEYWORD_MATCH,
    ARM_VECTOR: REASON_VECTOR_MATCH,
}


def _filter_match_reasons(project: str | None, source: str | None) -> tuple[str, ...]:
    """project/source 필터가 지정됐을 때의 근거 문구를 만든다(indexed/fetch 두 전략이 공유)."""
    reasons: list[str] = []
    if project:
        reasons.append(f"프로젝트 필터 일치: {project}")
    if source:
        reasons.append(f"출처 필터 일치: {source}")
    return tuple(reasons)


def _build_match_reasons(
    contributing_arms: tuple[str, ...], project: str | None, source: str | None, indexed: bool
) -> tuple[str, ...]:
    """arm 기여 -> 필터 일치 -> 강등 신호 순서로 사람이 읽는 근거 문자열을 만든다.

    순수 함수(모듈 상수 문자열만 사용)라 단위 테스트가 쉽다. `contributing_arms`
    는 이미 (title, keyword, vector) 고정 순서라 그대로 순회하면 된다.
    """
    reasons = [_ARM_REASON[arm] for arm in contributing_arms]
    reasons.extend(_filter_match_reasons(project, source))
    if not indexed:
        reasons.append(REASON_UNINDEXED)
    return tuple(reasons)


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


def _dedupe_first_with_chunk(hits: Sequence[object]) -> tuple[list[str], dict[str, str]]:
    """청크 히트 리스트를 문서 ID 순위(첫 등장만)로 접고, 문서별 승자 청크 ID 를 함께 뽑는다.

    `ChunkTextHit`/`ChunkVectorHit` 둘 다 `document_id`/`chunk_id` 속성을
    가지므로 하나의 헬퍼로 공유한다(doc39 §2.2 — 문서 하나가 섹션 수만큼
    슬롯을 먹지 않도록 dedupe). `reciprocal_rank_fuse` 내부의
    `_dedupe_first` 와 동일한 "첫 등장 등수만 채택" 규칙이다.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    chunk_by_doc: dict[str, str] = {}
    for hit in hits:
        document_id = hit.document_id  # type: ignore[attr-defined]
        if document_id in seen:
            continue
        seen.add(document_id)
        ordered.append(document_id)
        chunk_by_doc[document_id] = hit.chunk_id  # type: ignore[attr-defined]
    return ordered, chunk_by_doc

__all__ = [
    "DocumentSearchOptions",
    "DocumentSearchItem",
    "DocumentContent",
    "DocumentSearchService",
    "MatchedChunk",
    "documents_tokenize",
]


@dataclass(frozen=True)
class MatchedChunk:
    """`DocumentSearchItem.matched_chunks` 원소 하나(어느 arm 이 어떤 청크로 히트했는지)."""

    chunk_id: str
    text: str
    chunk_type: str
    arm: str


def _assemble_matched_chunks(
    document_id: str | None,
    keyword_chunk_by_doc: dict[str, str],
    vector_chunk_by_doc: dict[str, str],
    chunk_texts: dict[str, str],
) -> tuple[MatchedChunk, ...]:
    """문서 한 건의 keyword/vector 승자 청크를 `MatchedChunk` 튜플로 조립한다.

    keyword 승자 -> vector 승자 순서로 훑되, 같은 chunk_id 면 항목 하나로 합쳐
    `arm="both"` 로 표시한다. text 를 못 찾은 chunk_id(배치 조회 대상에서
    빠졌거나 삭제된 경우)는 건너뛴다.
    """
    arm_by_chunk_id: dict[str, str] = {}
    ordered_ids: list[str] = []
    keyword_id = keyword_chunk_by_doc.get(document_id) if document_id else None
    vector_id = vector_chunk_by_doc.get(document_id) if document_id else None
    for chunk_id, arm in ((keyword_id, ARM_KEYWORD), (vector_id, ARM_VECTOR)):
        if not chunk_id:
            continue
        if chunk_id in arm_by_chunk_id:
            arm_by_chunk_id[chunk_id] = "both"
        else:
            arm_by_chunk_id[chunk_id] = arm
            ordered_ids.append(chunk_id)
    return tuple(
        MatchedChunk(
            chunk_id=cid,
            text=chunk_texts[cid],
            chunk_type=_SECTION_CHUNK_TYPE,
            arm=arm_by_chunk_id[cid],
        )
        for cid in ordered_ids
        if cid in chunk_texts
    )


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
    #: 원본 시각 문자열(ISO8601, 예: "2026-08-01" 또는 "2026-08-01T09:00:00Z").
    #: 양끝 포함(>=/<=). `document_meta.modified_at` 이 NULL 인 문서는
    #: 하나라도 지정되면 제외된다(SQL 3값 논리).
    modified_after: str | None = None
    modified_before: str | None = None
    #: Drive `mimeType` 정확 일치 목록(OR). Notion 문서는 mime_type 이 항상
    #: NULL 이라 이 필터를 지정하면 always 제외된다.
    mime_types: list[str] | None = None
    #: 원본 시각 문자열(ISO8601). 양끝 포함(>=/<=).
    #: `document_meta.created_at` 이 NULL 인 문서는 하나라도 지정되면 제외된다.
    created_after: str | None = None
    created_before: str | None = None
    #: 문서 소유자 정확 일치 목록(OR, 대소문자 무시). Drive 는 소유자 이메일이
    #: 우선 저장되고 없을 때만 표시 이름이 들어간다. Notion 문서는 owner 가
    #: 항상 NULL 이라 이 필터를 지정하면 always 제외된다.
    owners: list[str] | None = None


@dataclass(frozen=True)
class DocumentSearchItem:
    """검색 결과 한 건.

    `score`: `document_search_strategy="indexed"`(기본)에서는 RRF 점수
    (`0.0x` 스케일)를 그대로 담는다. `"fetch"`(롤백 스위치)에서는 기존
    가중합 점수(`TITLE_SCORE_WEIGHT*title_score + BODY_SCORE_WEIGHT*body_score`,
    [0,1] 스케일)다 — 두 전략의 절대값은 서로 비교 불가하며, **순서 정보만
    의미가 있다**(`docs/architect-review/39` §2.5).
    """

    title: str
    source: str
    project: str
    url: str
    snippet: str
    score: float
    #: title 에서 파싱한 버전 표기(예: "v1.0"). 없으면 None — 순위엔 영향 없다.
    version: str | None
    #: get_document(source, external_id) 에 그대로 넘기는 값(45번 리뷰 §3.1 —
    #: 이 필드 없이는 호출자가 url 을 파싱해 external_id 를 역산해야 했다).
    external_id: str = ""
    #: 스니펫이 만들어진 시점(협업 문서의 `document_meta.last_synced_at`).
    #: `"fetch"` 전략(라이브 fetch, 스니펫이 항상 최신)과 title-only 매치는
    #: 캐시 발췌가 아니므로 None이다. doc36 Phase0-2 가 예고한 유일한 겉면
    #: 계약 변경(스니펫 출처가 동기화 시점 캐시로 바뀔 수 있음)을 명시한다.
    snippet_as_of: datetime | None = None
    #: 이 문서를 히트시킨 승자 청크들(어느 arm 이 어떤 본문 조각으로 뽑았는지).
    #: fetch 전략(라이브 fetch)에서는 청크 개념이 없어 항상 빈 튜플이다.
    matched_chunks: tuple[MatchedChunk, ...] = ()
    #: 사람이 읽는 근거 문자열 목록(순서 고정, LLM 이 파싱하는 계약).
    match_reasons: tuple[str, ...] = ()
    #: 원본 시스템 기준 최종 수정 시각(`document_meta.modified_at`, naive datetime).
    modified_at: datetime | None = None
    #: 이 문서의 본문이 색인돼 대응 `document` 행이 있으면 True.
    #: `document_meta.document_id` 유무로만 판단하며, 청크 존재 여부와 100%
    #: 동치는 아니다 — False 면 제목 매칭만으로 검색된 결과라는 뜻이다.
    indexed: bool = False
    #: 출처 시스템의 MIME 타입(`document_meta.mime_type`). Drive 전용,
    #: Notion·백필 전 Drive 문서는 None.
    mime_type: str | None = None
    #: 문서 소유자 이메일 또는 표시 이름(`document_meta.owner`). Drive 전용,
    #: Notion·백필 전 Drive 문서는 None. 호출자가 이 값을 그대로 `owners`
    #: 필터에 복사해 넣을 수 있도록 노출한다.
    owner: str | None = None


@dataclass(frozen=True)
class DocumentContent:
    """`get_document` 가 반환하는 원문 한 건.

    title/url 은 메타 캐시에 있으면 그 값, **메타 캐시에 없으면 둘 다 `""`**
    로 확정된다(식별자 기반 기본값이 아니다). `""`은 "이 문서의 메타데이터가
    캐시돼 있지 않다"는 뜻일 뿐, content 는 이 경우에도 fetch 시점의 최신
    원문이다 — content 유무와 메타 유무는 독립이다.
    """

    title: str
    source: str
    url: str
    content: str
    #: title 에서 파싱한 버전 표기(예: "v1.0"). 없으면 None — 순위엔 영향 없다.
    version: str | None
    #: 어댑터가 max_chars 로 잘랐으면 True. search 경로(스니펫)에는 노출되지
    #: 않는다 — 이 필드는 원문 조회(get_document)에만 의미가 있다.
    truncated: bool


class DocumentSearchService:
    """메타 캐시 1단계 + 본문 실시간 fetch 2단계로 협업 문서를 검색한다."""

    def __init__(
        self,
        meta_repo: DocumentMetaRepository,
        resolver: ProjectSourceResolver,
        chunk_repo: ChunkRepository | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        vector_fallback_enabled: bool = True,
        document_search_strategy: str = "indexed",
    ) -> None:
        """저장소와 프로젝트 소스 리졸버를 보관한다.

        Args:
            meta_repo: `document_meta` 저장소(1단계/title arm 후보 조회용).
            resolver: project → Drive/Notion 어댑터 요청 시점 팩토리.
            chunk_repo: `document_search_strategy="indexed"`(기본) 의
                keyword/vector arm 이 section 청크를 조회하는 저장소. 생략하면
                `"indexed"` 를 요청해도 `"fetch"` 로 degrade한다(아래 참조).
            embedding_provider: `"indexed"` 의 벡터 arm이 질의를 임베딩할 때
                쓴다. 후보마다 재호출하지 않고 요청당 1회만 호출한다
                (`docs/architect-review/39` §1.2 — (A)안이 반려된 이유 중
                하나가 후보마다 임베딩 API를 부르는 N+1 이었다).
            vector_fallback_enabled: False 면 `"indexed"` 의 벡터 arm을
                통째로 생략한다(해시 임베딩 등 `is_semantic=False` 배포).
            document_search_strategy: `"indexed"`(기본, 색인된 section 청크
                title+keyword+vector 3-arm RRF) | `"fetch"`(실시간 fetch+
                가중합, 롤백 스위치). `chunk_repo`/`embedding_provider` 가
                없거나 미인식 값이면 안전하게 `"fetch"`로 degrade한다
                (`docs/architect-review/43` §2 — doc36 §6-2 백필 완료로
                기본을 전환).
        """
        self._meta_repo = meta_repo
        self._resolver = resolver
        self._chunk_repo = chunk_repo
        self._embedding_provider = embedding_provider
        self._vector_fallback_enabled = vector_fallback_enabled
        self._document_search_strategy = document_search_strategy

    def search(self, query: str, options: DocumentSearchOptions) -> list[DocumentSearchItem]:
        """질의에 관련된 협업 문서를 찾아 스니펫과 점수를 붙여 반환한다.

        Args:
            query: 검색할 자연어/키워드 질의.
            options: top_k·source·project 필터, query_variants(1단계 SQL
                후보 필터만 넓히는 호출자 제공 동의어), modified_after/
                modified_before/mime_types/created_after/created_before/
                owners(날짜·mimeType·생성시각·소유자 hard filter, 3 arm
                전부에 SQL 로 적용된다).

        Returns:
            점수 내림차순 결과 리스트(최대 top_k 건). 1단계 후보가 없으면
            본문 fetch 없이 빈 리스트.

        Raises:
            ValidationError: 질의가 비었거나 top_k/source/날짜/mime_types/
                owners 값이 잘못된 경우.
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
        meta_filter = self._build_meta_filter(options)

        if (
            self._document_search_strategy == DOCUMENT_SEARCH_STRATEGY_INDEXED
            and self._chunk_repo is not None
            and self._embedding_provider is not None
        ):
            return self._search_indexed(
                normalized_query,
                query_tokens,
                filter_tokens,
                normalized_source,
                options,
                meta_filter,
            )

        candidates = self._select_candidates(
            filter_tokens,
            query_tokens,
            normalized_query,
            replace(options, source=normalized_source),
            meta_filter,
        )
        if not candidates:
            # 2단계를 건너뛴다: 후보가 없으면 외부 API 를 한 번도 호출하지 않는다.
            _LOG.debug("1단계 후보 0건 — 본문 fetch 생략: query=%s", normalized_query)
            return []

        return self._rank_with_body(
            candidates,
            query_tokens,
            normalized_query,
            options.top_k,
            options.project,
            normalized_source,
        )

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
            제목·출처·URL·본문·버전·절단 여부를 담은 DTO. title/url 은 메타
            캐시에 있으면 그 값을, **메타 캐시에 없으면 title/url 둘 다 `""`**
            로 확정된다(식별자 기반 기본값이 아니다) — `""`은 "이 문서의
            메타데이터가 캐시돼 있지 않다"는 뜻이며, `content`는 이 경우에도
            여전히 방금 fetch한 authoritative 최신 원문이다. version 은
            title 에서 파싱한 값(`parse_version`)이며 표기 없으면 None.
            truncated 는 어댑터가 `max_chars` 로 잘랐으면 True.

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
        fetched = document_source.fetch(normalized_id)
        title = row.title if row else ""
        return DocumentContent(
            title=title,
            source=source_str,
            url=row.url if row else "",
            content=fetched.text,
            version=parse_version(title),
            truncated=fetched.truncated,
        )

    def _find_meta_row(self, source: str, external_id: str) -> DocumentMeta | None:
        """(source, external_id) 를 가진 행 중 가장 최근 last_synced_at 행을 찾는다.

        같은 external_id 가 여러 project 에 공유될 수 있으므로(SPEC 기능 6
        검증 기준: A·B 소스에 공유된 문서는 2행), project 를 명시하지 않고
        source/external_id 만으로 조회한다. 저장소의 인덱스 포인트 조회
        메서드에 그대로 위임한다(그 source 행 전체를 적재하지 않는다).
        """
        return self._meta_repo.find_latest_by_source_and_external_id(source, external_id)

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
        meta_filter: DocumentMetaFilter,
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
            meta_filter=meta_filter,
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
        project: str | None,
        source: str | None,
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
                    pair[0],
                    pair[1],
                    query_tokens,
                    query,
                    sources_by_project[pair[0].project],
                    project,
                    source,
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
        project: str | None,
        source: str | None,
    ) -> DocumentSearchItem | None:
        """후보 한 건의 본문을 fetch 해 점수를 매긴다. 실패/미구성이면 None.

        `sources` 는 호출측(`_rank_with_body`)이 메인 스레드에서 미리
        resolve 해 둔 값이다 — 이 메서드는 워커 스레드에서 실행되므로
        Session 을 쓰는 resolve 호출을 여기서 하면 안 된다. `project`/`source`
        는 요청 필터값(순수 문자열)이라 스레드 안전하며, match_reasons 의
        필터 일치 문구를 만드는 데만 쓴다.
        """
        document_source = sources.get(row.source)
        if document_source is None:
            _LOG.warning(
                "메타 캐시에 있으나 소스가 미구성됨: %s/%s", row.project, row.source
            )
            return None
        try:
            # .truncated 는 버린다 — 스니펫은 본래 발췌라 절단 개념이
            # 무의미하고, truncated 는 원문 조회 경로(get_document)에만 의미가
            # 있다.
            body = document_source.fetch(row.external_id).text
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
            version=parse_version(row.title),
            external_id=row.external_id,
            match_reasons=(REASON_LIVE_FETCH_MATCH, *_filter_match_reasons(project, source)),
            modified_at=row.modified_at,
            indexed=row.document_id is not None,
            mime_type=row.mime_type,
            owner=row.owner,
        )

    # --- indexed 전략: title+keyword+vector 3-arm RRF ---------------------
    # docs/architect-review/39_document_search_phase3_rrf_verdict.md

    def _search_indexed(
        self,
        query: str,
        query_tokens: set[str],
        filter_tokens: set[str],
        source: str | None,
        options: DocumentSearchOptions,
        meta_filter: DocumentMetaFilter,
    ) -> list[DocumentSearchItem]:
        """title(document_meta) + keyword/vector(section 청크) 3-arm RRF 로 검색한다.

        융합 키는 `Document.id` 다 — title arm 은
        `deterministic_document_id(project,source,external_id)` 로 순수
        계산하고(색인 여부와 무관하게 동일 키), keyword/vector arm 은
        `Chunk.document_id` 를 그대로 쓴다(doc39 §2.2). 그래서 미색인
        문서도 title arm 단독으로 자연스럽게 결과에 남는다(별도 폴백 분기
        불필요, doc39 §2.3).
        """
        project = options.project
        width = max(options.top_k * _RRF_CANDIDATE_WIDTH_MULTIPLIER, _RRF_MIN_CANDIDATE_WIDTH)
        assert self._chunk_repo is not None
        assert self._embedding_provider is not None

        title_ids, title_meta = self._title_arm(
            filter_tokens,
            query_tokens,
            query,
            source,
            project,
            options.query_variants,
            width,
            meta_filter,
        )

        #: keyword/vector arm 이 도는 section 청크는 협업 문서와 등록형 문서가
        #: 공유하므로, doc_type 으로 협업 문서(drive/notion)만 남긴다 —
        #: source 지정 시엔 그 소스로 더 좁힌다(45번 리뷰 §3.2/3.3).
        doc_types = [source] if source is not None else list(ALLOWED_SOURCES)

        keyword_ids: list[str] = []
        vector_ids: list[str] = []
        keyword_chunk_by_doc: dict[str, str] = {}
        vector_chunk_by_doc: dict[str, str] = {}
        if self._chunk_repo.has_endpoint_chunks(
            project=project, chunk_type=_SECTION_CHUNK_TYPE
        ):
            keyword_ids, keyword_chunk_by_doc = self._keyword_arm(
                filter_tokens,
                query_tokens,
                query,
                options.query_variants,
                project,
                width,
                doc_types,
                meta_filter,
            )
            if self._vector_fallback_enabled:
                vector_ids, vector_chunk_by_doc = self._vector_arm(
                    query, project, width, doc_types, meta_filter
                )

        fused = reciprocal_rank_fuse(
            keyword_ids,
            vector_ids,
            top_k=width,
            title_ref_ids=title_ids,
            weights={ARM_TITLE: TITLE_ARM_WEIGHT},
        )
        if not fused:
            return []

        meta_by_doc_id = dict(title_meta)
        missing_ids = [f.ref_id for f in fused if f.ref_id not in meta_by_doc_id]
        for row in self._meta_repo.list_by_document_ids(missing_ids):
            if row.document_id:
                meta_by_doc_id[row.document_id] = row

        #: 스니펫 선택용 병합 dict(벡터 승자를 keyword 승자가 덮어씀, 기존 동작 유지).
        winner_chunk_by_doc = {**vector_chunk_by_doc, **keyword_chunk_by_doc}
        #: matched_chunks 는 keyword/vector 원본 승자를 각각 보존해야 하므로
        #: 두 dict 의 값 합집합을 배치 조회한다(호출 횟수는 여전히 1회, id 개수만 최대 2배).
        chunk_texts = self._chunk_repo.get_texts_by_ids(
            list(set(keyword_chunk_by_doc.values()) | set(vector_chunk_by_doc.values()))
        )

        items: list[DocumentSearchItem] = []
        for fused_result in fused:
            row = meta_by_doc_id.get(fused_result.ref_id)
            if row is None:
                _LOG.warning("융합 결과가 참조하는 문서 메타를 찾을 수 없음: %s", fused_result.ref_id)
                continue
            if source is not None and row.source != source:
                continue
            item = self._build_indexed_item(
                row,
                fused_result,
                keyword_chunk_by_doc,
                vector_chunk_by_doc,
                winner_chunk_by_doc,
                chunk_texts,
                query,
                query_tokens,
                project,
                source,
            )
            items.append(item)
            if len(items) >= options.top_k:
                break
        return items

    def _build_indexed_item(
        self,
        row: DocumentMeta,
        fused_result: FusedResult,
        keyword_chunk_by_doc: dict[str, str],
        vector_chunk_by_doc: dict[str, str],
        winner_chunk_by_doc: dict[str, str],
        chunk_texts: dict[str, str],
        query: str,
        query_tokens: set[str],
        project: str | None,
        source: str | None,
    ) -> DocumentSearchItem:
        """메타 행 + RRF 융합 결과 + (있으면) 승자 청크 text 로 결과 아이템 한 건을 만든다."""
        winner_chunk_id = row.document_id and winner_chunk_by_doc.get(row.document_id)
        if winner_chunk_id:
            body = chunk_texts.get(winner_chunk_id, "")
            snippet = _build_snippet(body, query_tokens) or _fallback_snippet(row, query)
            snippet_as_of: datetime | None = row.last_synced_at
        else:
            snippet = _fallback_snippet(row, query)
            snippet_as_of = None
        indexed = row.document_id is not None
        return DocumentSearchItem(
            title=row.title,
            source=row.source,
            project=row.project,
            url=row.url,
            snippet=snippet,
            score=fused_result.score,
            version=parse_version(row.title),
            external_id=row.external_id,
            snippet_as_of=snippet_as_of,
            matched_chunks=_assemble_matched_chunks(
                row.document_id, keyword_chunk_by_doc, vector_chunk_by_doc, chunk_texts
            ),
            match_reasons=_build_match_reasons(
                fused_result.contributing_arms, project, source, indexed
            ),
            modified_at=row.modified_at,
            indexed=indexed,
            mime_type=row.mime_type,
            owner=row.owner,
        )

    def _title_arm(
        self,
        filter_tokens: set[str],
        score_tokens: set[str],
        query: str,
        source: str | None,
        project: str | None,
        query_variants: list[str] | None,
        width: int,
        meta_filter: DocumentMetaFilter,
    ) -> tuple[list[str], dict[str, DocumentMeta]]:
        """title/url 토큰 매칭 순위를 문서 ID 리스트 + 메타 dict 로 만든다.

        문서 ID 는 `deterministic_document_id` 로 순수 계산한다 — 이 문서가
        본문 색인이 됐는지와 무관하게(`document_meta.document_id` 가 NULL
        이어도) 동일한 값이 나오므로, keyword/vector arm 의 `Chunk.document_id`
        와 같은 키 공간에서 만난다(doc39 §2.2).

        SQL 1단계 후보(`search_by_tokens`)는 `ILIKE '%token%'` 부분문자열
        매칭이라 토큰 경계를 무시한다 — 질의 'api' 가 제목의 'rapid' 안에
        들어 있어도 후보로 올라온다. 정렬 전에 `_passes_title_gate` 로
        그런 잡음 행을 제외한다(57번 리뷰 §5 개선3 T3 개정) — 원본/variant
        토큰이 title/url 토큰과 완전 일치하거나 토큰 경계를 지킨 연속
        부분열로 일치하는 행만 남는다. variant-only 로만 걸린 행(예:
        원본 "결제 실패" + variant "payment failure", 제목이 영문뿐이라
        원본 토큰과는 안 겹침)도 이 게이트를 통과해야 한다 — 안 그러면
        `query_variants` 기능 자체가 무력화된다. 정렬 키·점수 계산은
        게이트와 무관하게 그대로다.
        """
        rows = self._meta_repo.search_by_tokens(
            sorted(filter_tokens),
            source=source,
            project=project,
            queries=[query, *(query_variants or [])],
            meta_filter=meta_filter,
        )
        scored = [(row, _title_score(row, score_tokens, query)) for row in rows]
        queries = [query, *(query_variants or [])]
        scored = [
            pair for pair in scored if _passes_title_gate(pair[0], filter_tokens, queries)
        ]
        scored.sort(key=lambda pair: (pair[1] <= 0.0, -pair[1], pair[0].external_id))
        top = scored[:width]
        ids_by_row = [
            (deterministic_document_id(row.project, row.source, row.external_id), row)
            for row, _ in top
        ]
        ordered_ids = [doc_id for doc_id, _ in ids_by_row]
        meta_by_id = dict(ids_by_row)
        return ordered_ids, meta_by_id

    def _keyword_arm(
        self,
        filter_tokens: set[str],
        score_tokens: set[str],
        query: str,
        query_variants: list[str] | None,
        project: str | None,
        width: int,
        doc_types: Sequence[str],
        meta_filter: DocumentMetaFilter,
    ) -> tuple[list[str], dict[str, str]]:
        """section 청크를 FTS 로 검색해 문서 ID 순위 + 문서별 승자 청크 ID 를 만든다.

        58번 §4 keyword arm 한글 복합어 대칭: `filter_tokens`/`score_tokens`
        (집합)와 별개로, 순서가 필요한 concat/split 복합어 term 은 여기서
        `documents_tokenize(query)` 를 다시 호출해 만든다. 필터 측은 원본
        질의 토큰 + 각 variant 문자열을 개별 토큰화한 결과에서 파생하고
        (variant 끼리·원본과 variant 를 가로질러 concat 하지 않는다 — 서로
        다른 문장이라 이어붙일 근거가 없다), 점수 측은 원본 질의 토큰에서
        파생한 것만 쓴다 — concat/split term 은 원본 질의와 같은 표층
        문자열의 띄어쓰기 변형이라, variant 처럼 점수에서 뺄 이유가 없다
        (빼면 복합어로만 걸린 문서가 keyword arm 최하위로 밀려 RRF 기여가
        사실상 사라진다).
        """
        assert self._chunk_repo is not None
        original_tokens = documents_tokenize(query)
        concat_terms, phrase_terms = compound_terms_for_tokens(original_tokens)

        filter_terms = filter_tokens | set(concat_terms)
        filter_phrase_terms = list(phrase_terms)
        seen_phrase_terms = set(phrase_terms)
        # 원본 파생이 먼저 COMPOUND_TERM_LIMIT 예산을 가져가고, variant 는
        # 남는 예산만 나눠 쓴다(59 §F3) — variant 수가 늘어도 전체 상한은
        # COMPOUND_TERM_LIMIT 로 고정된다.
        remaining_budget = COMPOUND_TERM_LIMIT - len(concat_terms) - len(phrase_terms)
        for variant in query_variants or []:
            if remaining_budget <= 0:
                break
            variant_concat, variant_phrase = compound_terms_for_tokens(
                documents_tokenize(variant), limit=remaining_budget
            )
            remaining_budget -= len(variant_concat) + len(variant_phrase)
            filter_terms |= set(variant_concat)
            for phrase in variant_phrase:
                if phrase not in seen_phrase_terms:
                    seen_phrase_terms.add(phrase)
                    filter_phrase_terms.append(phrase)

        hits = self._chunk_repo.search_endpoint_by_text(
            list(filter_terms),
            top_k=width,
            project=project,
            score_terms=list(score_tokens | set(concat_terms)),
            chunk_type=_SECTION_CHUNK_TYPE,
            doc_types=doc_types,
            meta_filter=meta_filter,
            phrase_terms=filter_phrase_terms or None,
            score_phrase_terms=phrase_terms,
        )
        return _dedupe_first_with_chunk(hits)

    def _vector_arm(
        self,
        query: str,
        project: str | None,
        width: int,
        doc_types: Sequence[str],
        meta_filter: DocumentMetaFilter,
    ) -> tuple[list[str], dict[str, str]]:
        """section 청크를 벡터 검색해 문서 ID 순위 + 문서별 승자 청크 ID 를 만든다.

        질의 임베딩은 요청당 1회만 호출한다(후보마다 호출하던 (A)안의 N+1
        을 없앤 이유, doc39 §1.2).
        """
        assert self._chunk_repo is not None
        assert self._embedding_provider is not None
        query_vec = self._embedding_provider.embed_query(query)
        hits = self._chunk_repo.search_by_vector(
            query_vec,
            top_k=width,
            project=project,
            chunk_type=_SECTION_CHUNK_TYPE,
            doc_types=doc_types,
            meta_filter=meta_filter,
        )
        positive_hits = [h for h in hits if h.score > 0.0]
        return _dedupe_first_with_chunk(positive_hits)

    # --- 검증 헬퍼 --------------------------------------------------------

    def _validate(self, query: str, options: DocumentSearchOptions) -> str:
        """질의·top_k·source·메타 필터 옵션을 검증하고 공백을 제거한 질의를 반환한다."""
        normalized_query = (query or "").strip()
        if not normalized_query:
            raise ValidationError("query must not be empty")
        if not MIN_TOP_K <= options.top_k <= MAX_TOP_K:
            raise ValidationError(
                f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}: {options.top_k}"
            )
        self._validate_source(options.source, allow_none=True)
        self._validate_meta_filter_options(options)
        return normalized_query

    def _validate_meta_filter_options(self, options: DocumentSearchOptions) -> None:
        """modified_*/created_*/mime_types/owners 입력값을 검증한다.

        구성(DocumentMetaFilter 조립)은 검증 통과 후 `_build_meta_filter` 가 한다.
        수정 시각 축과 생성 시각 축은 서로 교차 검증하지 않는다 - "8월에
        만들어졌고 7월 이전에 수정된" 같은 조합은 의미상 정상 질의다.
        """
        after = self._parse_filter_datetime(options.modified_after, "modified_after")
        before = self._parse_filter_datetime(options.modified_before, "modified_before")
        if after is not None and before is not None and after > before:
            raise ValidationError("modified_after must not be after modified_before")
        created_after = self._parse_filter_datetime(options.created_after, "created_after")
        created_before = self._parse_filter_datetime(options.created_before, "created_before")
        if (
            created_after is not None
            and created_before is not None
            and created_after > created_before
        ):
            raise ValidationError("created_after must not be after created_before")
        if options.mime_types is not None:
            if not options.mime_types:
                raise ValidationError("mime_types must not be empty when provided")
            if len(options.mime_types) > _MAX_MIME_TYPES:
                raise ValidationError(
                    f"mime_types must have at most {_MAX_MIME_TYPES} entries"
                )
            for mime in options.mime_types:
                stripped = mime.strip()
                if not stripped or len(stripped) > _MAX_MIME_TYPE_LENGTH:
                    raise ValidationError(f"invalid mime_type entry: {mime!r}")
        if options.owners is not None:
            if not options.owners:
                raise ValidationError("owners must not be empty when provided")
            if len(options.owners) > _MAX_OWNERS:
                raise ValidationError(f"owners must have at most {_MAX_OWNERS} entries")
            for owner in options.owners:
                stripped_owner = owner.strip()
                if not stripped_owner or len(stripped_owner) > _MAX_OWNER_LENGTH:
                    raise ValidationError(f"invalid owner entry: {owner!r}")

    def _parse_filter_datetime(self, value: str | None, field_name: str) -> datetime | None:
        """ISO8601 문자열을 tz-naive UTC datetime 으로 파싱한다.

        날짜만(예: "2026-08-01") 도 허용된다 - `fromisoformat` 이 자정으로
        해석한다(`parse_rfc3339` 참조).
        """
        if value is None:
            return None
        parsed = parse_rfc3339(value)
        if parsed is None:
            raise ValidationError(f"{field_name} must be an ISO8601 datetime")
        return parsed

    def _build_meta_filter(self, options: DocumentSearchOptions) -> DocumentMetaFilter:
        """검증을 통과한 옵션으로 `DocumentMetaFilter` 를 만든다(호출 전 `_validate` 필수)."""
        mime_types = tuple(m.strip() for m in options.mime_types) if options.mime_types else ()
        owners = tuple(o.strip() for o in options.owners) if options.owners else ()
        return DocumentMetaFilter(
            modified_after=parse_rfc3339(options.modified_after),
            modified_before=parse_rfc3339(options.modified_before),
            mime_types=mime_types,
            created_after=parse_rfc3339(options.created_after),
            created_before=parse_rfc3339(options.created_before),
            owners=owners,
        )

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
