"""DocumentSearchService 단위 테스트 (SPEC 기능 7, 8, 프로젝트 확장).

핵심 검증 대상은 "2단계 후보 압축"이 실제로 외부 API 호출을 줄이는지와,
project 필터가 검색·fetch 범위를 올바르게 좁히는지다. 페이크 소스의
`fetch_call_count` 로 다음을 단언한다.

- 1단계 후보가 0건이면 fetch 가 0회다.
- 한 번의 검색에서 fetch 수는 `top_k` 가 아니라 fetch 예산(`_body_fetch_budget`,
  top_k 를 오버스캔한 뒤 상한을 씌운 값)을 넘지 않는다. 최종 결과 개수는
  여전히 `top_k` 로 컷된다.
- project="A" 로 좁히면 B 의 어댑터 fetch 가 한 번도 호출되지 않는다.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from app.core.errors import IntegrationError, ValidationError
from app.models import DEFAULT_PROJECT, EMBEDDING_DIM, Chunk, Document
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION, DocumentMeta
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.services.documents.document_search_service import (
    BODY_FETCH_OVERSCAN,
    MAX_BODY_FETCH_CANDIDATES,
    MAX_CONCURRENT_BODY_FETCHES,
    REASON_KEYWORD_MATCH,
    REASON_LIVE_FETCH_MATCH,
    REASON_TITLE_MATCH,
    REASON_UNINDEXED,
    REASON_VECTOR_MATCH,
    DocumentSearchOptions,
    DocumentSearchService,
    _assemble_matched_chunks,
    _body_fetch_budget,
    _build_match_reasons,
    documents_tokenize,
)
from app.services.documents.sources.document_source import FetchedDocument
from app.services.indexer.embedding_provider import HashEmbeddingProvider
from app.services.search.rrf import ARM_KEYWORD, ARM_TITLE, ARM_VECTOR
from tests.fixtures.document_sources import ExplodingDocumentSource

_PROJECT_A = "A"
_PROJECT_B = "B"


class _SlowTrackingDocumentSource:
    """fetch 마다 `delay` 만큼 지연하며 동시 실행 수를 기록하는 페이크 소스.

    병렬 fetch(P3) 검증 전용: 벽시계 시간이 순차 합산이 아닌지, 동시 실행 수가
    상한을 넘지 않는지를 확인하는 데 쓴다.
    """

    def __init__(self, source_name: str, bodies: dict[str, str], delay: float = 0.15) -> None:
        self._source_name = source_name
        self.bodies = bodies
        self.delay = delay
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0
        self.fetch_call_count = 0

    @property
    def source_name(self) -> str:
        return self._source_name

    def list_files(self):  # pragma: no cover - list_files 는 검색 경로에서 안 쓰임
        return []

    def fetch(self, external_id: str) -> FetchedDocument:
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            self.fetch_call_count += 1
        try:
            time.sleep(self.delay)
            if external_id not in self.bodies:
                raise IntegrationError(f"fake slow source: not found {external_id}")
            return FetchedDocument(text=self.bodies[external_id], truncated=False)
        finally:
            with self._lock:
                self._active -= 1


def _seed_meta(
    session,
    source: str,
    external_id: str,
    title: str,
    url: str | None = None,
    project: str = DEFAULT_PROJECT,
) -> DocumentMeta:
    """`document_meta` 행 하나를 저장하고 반환한다."""
    row = DocumentMeta(
        project=project,
        source=source,
        external_id=external_id,
        title=title,
        url=url or f"https://example.test/{source}/{external_id}",
        modified_at=datetime(2026, 7, 1, 12, 0, 0),
        last_synced_at=datetime(2026, 7, 1, 12, 0, 0),
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture()
def default_resolver(make_project_resolver, fake_drive_source, fake_notion_source):
    """DEFAULT_PROJECT 하나에 Drive/Notion 페이크가 매핑된 resolver."""
    return make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", fake_drive_source)},
        notion_mapping={DEFAULT_PROJECT: ("db-default", fake_notion_source)},
    )


@pytest.fixture()
def search_service(db_session, default_resolver):
    """DEFAULT_PROJECT 에 페이크 소스가 매핑된 DocumentSearchService."""
    return DocumentSearchService(
        meta_repo=DocumentMetaRepository(db_session),
        resolver=default_resolver,
    )


# --- 토크나이저 ---------------------------------------------------------------


def test_documents_tokenize_handles_korean_and_ascii() -> None:
    """한글 덩어리와 영숫자 토큰을 모두 소문자로 잘라낸다."""
    assert documents_tokenize("로그인 Auth_v2 설계") == ["로그인", "auth_v2", "설계"]


def test_documents_tokenize_returns_empty_for_symbols_only() -> None:
    """기호만 있는 문자열은 빈 토큰 리스트가 된다."""
    assert documents_tokenize("!!! ???") == []


# --- 기능 7: 1단계 후보 압축 ---------------------------------------------------


def test_returns_empty_without_fetch_when_no_candidate(
    db_session, search_service, fake_drive_source
) -> None:
    """1단계 후보가 0건이면 본문 fetch 없이 즉시 빈 리스트를 반환한다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "배포 운영 가이드")
    fake_drive_source.bodies["d1"] = "본문"
    fake_drive_source.reset_counts()

    items = search_service.search("전혀관계없는키워드", DocumentSearchOptions())

    assert items == []
    assert fake_drive_source.fetch_call_count == 0


def test_no_candidate_never_touches_source(db_session, make_project_resolver) -> None:
    """후보가 없으면 소스 어댑터 자체가 호출되지 않는다(폭발 페이크로 단언)."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "배포 운영 가이드")
    exploding = ExplodingDocumentSource(SOURCE_DRIVE)
    resolver = make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", exploding)}
    )
    service = DocumentSearchService(meta_repo=DocumentMetaRepository(db_session), resolver=resolver)

    assert service.search("무관한질의어", DocumentSearchOptions()) == []


def test_title_match_document_is_included(
    db_session, search_service, fake_drive_source
) -> None:
    """제목에 쿼리 단어가 포함된 문서는 1단계 후보에 반드시 들어간다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 인증 설계서")
    fake_drive_source.bodies["d1"] = "OAuth 기반 로그인 흐름을 설명한다."

    items = search_service.search("로그인", DocumentSearchOptions())

    assert [i.title for i in items] == ["로그인 인증 설계서"]
    assert items[0].source == SOURCE_DRIVE
    assert items[0].project == DEFAULT_PROJECT


def test_whitespace_variant_query_matches_title_without_space(
    db_session, search_service, fake_drive_source
) -> None:
    """공백 없는 질의('트러블슈팅')로 공백 있는 제목('트러블 슈팅')을 찾는다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "트러블 슈팅 가이드")
    fake_drive_source.bodies["d1"] = "장애 대응 절차를 정리한다."

    items = search_service.search("트러블슈팅", DocumentSearchOptions())

    assert [i.title for i in items] == ["트러블 슈팅 가이드"]


def test_whitespace_variant_query_matches_title_with_space(
    db_session, search_service, fake_drive_source
) -> None:
    """공백 있는 질의('트러블 슈팅')로 공백 없는 제목('트러블슈팅')을 찾는다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "트러블슈팅 가이드")
    fake_drive_source.bodies["d1"] = "장애 대응 절차를 정리한다."

    items = search_service.search("트러블 슈팅", DocumentSearchOptions())

    assert [i.title for i in items] == ["트러블슈팅 가이드"]


# --- P1: version 필드 파싱(항목3) ----------------------------------------------


def test_search_item_includes_version_parsed_from_title(
    db_session, search_service, fake_drive_source
) -> None:
    """검색 결과 아이템에는 title 에서 파싱한 version 이 실린다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 설계서 v_1.0")
    fake_drive_source.bodies["d1"] = "로그인 흐름 설명"

    items = search_service.search("로그인", DocumentSearchOptions())

    assert [i.version for i in items] == ["v1.0"]


def test_search_item_version_is_none_without_version_marker(
    db_session, search_service, fake_drive_source
) -> None:
    """제목에 버전 표기가 없으면 version 은 None 이다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 설계서")
    fake_drive_source.bodies["d1"] = "로그인 흐름 설명"

    items = search_service.search("로그인", DocumentSearchOptions())

    assert [i.version for i in items] == [None]


def test_search_item_includes_external_id_for_get_document_handoff(
    db_session, search_service, fake_drive_source
) -> None:
    """검색 결과에 external_id 가 실려 get_document(source, external_id) 로 그대로 넘길 수 있다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 설계서")
    fake_drive_source.bodies["d1"] = "로그인 흐름 설명"

    items = search_service.search("로그인", DocumentSearchOptions())

    assert [i.external_id for i in items] == ["d1"]


def test_search_ranking_is_unaffected_by_version_presence(
    db_session, search_service, fake_drive_source
) -> None:
    """version 유무는 순위에 영향을 주지 않는다(회귀) — title_score/body_score 계산은
    version 파싱과 무관하게 그대로다."""
    _seed_meta(db_session, SOURCE_DRIVE, "versioned", "로그인 설계서 v2.0")
    _seed_meta(db_session, SOURCE_DRIVE, "unversioned", "로그인 설계서")
    fake_drive_source.bodies["versioned"] = "로그인 흐름 설명"
    fake_drive_source.bodies["unversioned"] = "로그인 흐름 설명"

    items = search_service.search("로그인", DocumentSearchOptions(top_k=2))

    assert {i.title for i in items} == {"로그인 설계서 v2.0", "로그인 설계서"}
    assert len({i.score for i in items}) == 1


# --- P1: 본문 fetch 예산(top_k 컷을 2단계 이전으로 당기지 않기 위한 오버스캔) -----


@pytest.mark.parametrize(
    ("top_k", "candidate_count", "expected"),
    [
        (1, 100, 3),
        (5, 100, 15),
        (10, 100, 20),
        (50, 100, 50),
        (5, 2, 2),
        (1, 1, 1),
    ],
)
def test_body_fetch_budget_scales_with_overscan_and_caps(
    top_k: int, candidate_count: int, expected: int
) -> None:
    """fetch 예산은 top_k*BODY_FETCH_OVERSCAN 만큼 넓히되 MAX_BODY_FETCH_CANDIDATES 로
    캡을 씌우고, top_k 자체보다는 절대 작아지지 않으며, 후보 수를 넘지 않는다."""
    assert _body_fetch_budget(top_k, candidate_count) == expected


def test_body_fetch_budget_overscan_and_cap_constants() -> None:
    """예산 계산에 쓰이는 상수 값 자체가 의도대로 설정돼 있는지 확인한다."""
    assert BODY_FETCH_OVERSCAN == 3
    assert MAX_BODY_FETCH_CANDIDATES == 20


def test_fetch_count_bounded_by_budget_not_top_k(
    db_session, search_service, fake_drive_source
) -> None:
    """후보가 top_k 보다 많으면 fetch 는 top_k 가 아니라 fetch 예산만큼 이뤄지고,
    최종 결과는 여전히 top_k 건으로 컷된다."""
    candidate_count = 10
    top_k = 3
    for index in range(candidate_count):
        external_id = f"d{index}"
        _seed_meta(db_session, SOURCE_DRIVE, external_id, f"로그인 문서 {index}")
        fake_drive_source.bodies[external_id] = "로그인 관련 본문"
    fake_drive_source.reset_counts()

    items = search_service.search("로그인", DocumentSearchOptions(top_k=top_k))

    assert fake_drive_source.fetch_call_count == _body_fetch_budget(top_k, candidate_count)
    assert len(items) == top_k


def test_low_title_rank_candidate_with_strong_body_reaches_top_k_via_fetch_budget(
    db_session, search_service, fake_drive_source
) -> None:
    """title_score 만으로 top_k 컷하면 영영 fetch 되지 못했을 문서가, 예산 확장 덕에
    2단계까지 진출해 강한 body_score 로 최종 top_k 를 뒤집는다(P1 회귀 테스트).

    질의 토큰 4개(결제/오류/상세/원인) 중 제목에 몇 개가 겹치느냐로 1단계
    순위가 갈린다: rank1(3/4)이 1위, rank2·rank3(2/4 동률)이 공동 2위,
    rank4·rank5(1/4)가 공동 4위. top_k=1 을 그대로 예산으로 썼다면 rank1만
    fetch 됐을 것이다. 예산이 top_k*BODY_FETCH_OVERSCAN=3 으로 넓어지면
    rank3 도 fetch 대상에 들고, rank3 의 본문이 질의 토큰을 모두 담고 있어
    결합점수(title*0.4+body*0.6)로 rank1 을 역전한다.
    """
    query = "결제 오류 상세 원인"
    _seed_meta(db_session, SOURCE_DRIVE, "rank1", "결제 오류 상세 안내")
    _seed_meta(db_session, SOURCE_DRIVE, "rank2", "결제 오류 매뉴얼")
    _seed_meta(db_session, SOURCE_DRIVE, "rank3", "오류 원인 조사서")
    _seed_meta(db_session, SOURCE_DRIVE, "rank4", "결제 확인 가이드")
    _seed_meta(db_session, SOURCE_DRIVE, "rank5", "상세 페이지 개편")
    fake_drive_source.bodies["rank1"] = "이 페이지는 안내서 사용법을 설명한다."
    fake_drive_source.bodies["rank2"] = "이 매뉴얼은 사용 팁을 정리한다."
    fake_drive_source.bodies["rank3"] = "결제 오류 상세 원인 관련 문서 전체 분석."
    fake_drive_source.bodies["rank4"] = "본문은 fetch 되지 않아야 한다."
    fake_drive_source.bodies["rank5"] = "본문은 fetch 되지 않아야 한다."
    fake_drive_source.reset_counts()

    items = search_service.search(query, DocumentSearchOptions(top_k=1))

    assert [i.title for i in items] == ["오류 원인 조사서"]
    assert fake_drive_source.fetch_call_count == 3
    assert "rank4" not in fake_drive_source.fetched_ids
    assert "rank5" not in fake_drive_source.fetched_ids


# --- P3: 2단계 본문 fetch 병렬화 ------------------------------------------------


def test_body_fetches_run_in_parallel_not_sequential(
    db_session, make_project_resolver
) -> None:
    """5건 fetch 가 순차 합산이 아니라 병렬로 실행돼 벽시계 시간이 짧게 끝난다."""
    delay = 0.15
    candidate_count = 5
    slow_source = _SlowTrackingDocumentSource(SOURCE_DRIVE, bodies={}, delay=delay)
    for index in range(candidate_count):
        external_id = f"d{index}"
        _seed_meta(db_session, SOURCE_DRIVE, external_id, f"로그인 문서 {index}")
        slow_source.bodies[external_id] = "로그인 본문"
    resolver = make_project_resolver(drive_mapping={DEFAULT_PROJECT: ("folder", slow_source)})
    service = DocumentSearchService(meta_repo=DocumentMetaRepository(db_session), resolver=resolver)

    started = time.monotonic()
    items = service.search("로그인", DocumentSearchOptions(top_k=candidate_count))
    elapsed = time.monotonic() - started

    assert len(items) == candidate_count
    # 순차 실행이면 candidate_count * delay(약 0.75초) 이상 걸린다.
    # 병렬 실행이면 한 라운드(약 delay)에 가깝게 끝난다.
    assert elapsed < delay * candidate_count * 0.7


def test_concurrent_fetches_never_exceed_cap(db_session, make_project_resolver) -> None:
    """동시 실행 중인 fetch 수가 MAX_CONCURRENT_BODY_FETCHES 를 넘지 않는다."""
    delay = 0.1
    candidate_count = MAX_CONCURRENT_BODY_FETCHES + 3
    slow_source = _SlowTrackingDocumentSource(SOURCE_DRIVE, bodies={}, delay=delay)
    for index in range(candidate_count):
        external_id = f"d{index}"
        _seed_meta(db_session, SOURCE_DRIVE, external_id, f"로그인 문서 {index}")
        slow_source.bodies[external_id] = "로그인 본문"
    resolver = make_project_resolver(drive_mapping={DEFAULT_PROJECT: ("folder", slow_source)})
    service = DocumentSearchService(meta_repo=DocumentMetaRepository(db_session), resolver=resolver)

    items = service.search("로그인", DocumentSearchOptions(top_k=candidate_count))

    assert len(items) == candidate_count
    assert slow_source.max_active <= MAX_CONCURRENT_BODY_FETCHES
    # 병렬 실행이 실제로 일어났는지도 함께 확인(구현이 순차로 퇴행하지 않았는지).
    assert slow_source.max_active > 1


def test_one_failure_among_parallel_fetches_does_not_affect_others(
    db_session, make_project_resolver
) -> None:
    """병렬 fetch 중 한 건이 IntegrationError 를 던져도 나머지는 정상 반환된다."""
    slow_source = _SlowTrackingDocumentSource(SOURCE_DRIVE, bodies={}, delay=0.05)
    for index in range(5):
        external_id = f"d{index}"
        _seed_meta(db_session, SOURCE_DRIVE, external_id, f"로그인 문서 {index}")
        slow_source.bodies[external_id] = "로그인 본문"
    # d2 는 본문을 등록하지 않아 fetch 시 IntegrationError 가 발생한다.
    del slow_source.bodies["d2"]
    resolver = make_project_resolver(drive_mapping={DEFAULT_PROJECT: ("folder", slow_source)})
    service = DocumentSearchService(meta_repo=DocumentMetaRepository(db_session), resolver=resolver)

    items = service.search("로그인", DocumentSearchOptions(top_k=5))

    assert {i.title for i in items} == {
        "로그인 문서 0",
        "로그인 문서 1",
        "로그인 문서 3",
        "로그인 문서 4",
    }


def test_project_source_resolution_never_runs_on_worker_thread(
    db_session, make_project_resolver
) -> None:
    """resolve_for_project() 는 요청-스코프 Session 을 읽으므로 워커 스레드가 아니라
    메인 스레드에서만 호출돼야 한다(Session 은 스레드 세이프하지 않다).
    """
    slow_source = _SlowTrackingDocumentSource(SOURCE_DRIVE, bodies={}, delay=0.05)
    for index in range(5):
        external_id = f"d{index}"
        _seed_meta(db_session, SOURCE_DRIVE, external_id, f"로그인 문서 {index}")
        slow_source.bodies[external_id] = "로그인 본문"
    resolver = make_project_resolver(drive_mapping={DEFAULT_PROJECT: ("folder", slow_source)})
    call_threads: list[str] = []
    original_resolve = resolver.resolve_for_project

    def _tracking_resolve(project: str):
        call_threads.append(threading.current_thread().name)
        return original_resolve(project)

    resolver.resolve_for_project = _tracking_resolve  # type: ignore[method-assign]
    service = DocumentSearchService(meta_repo=DocumentMetaRepository(db_session), resolver=resolver)

    service.search("로그인", DocumentSearchOptions(top_k=5))

    assert call_threads
    assert all(name == threading.main_thread().name for name in call_threads)


# --- 기능 7: source 필터 -------------------------------------------------------


def test_source_filter_restricts_results(
    db_session, search_service, fake_drive_source, fake_notion_source
) -> None:
    """source 필터를 주면 결과가 해당 출처만 포함한다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 설계")
    _seed_meta(db_session, SOURCE_NOTION, "n1", "로그인 회의록")
    fake_drive_source.bodies["d1"] = "드라이브 로그인 본문"
    fake_notion_source.bodies["n1"] = "노션 로그인 본문"

    items = search_service.search("로그인", DocumentSearchOptions(source=SOURCE_NOTION))

    assert [i.source for i in items] == [SOURCE_NOTION]
    assert fake_drive_source.fetch_call_count == 0


def test_without_source_filter_both_sources_appear(
    db_session, search_service, fake_drive_source, fake_notion_source
) -> None:
    """source 를 생략하면 두 출처의 문서가 모두 후보가 된다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 설계")
    _seed_meta(db_session, SOURCE_NOTION, "n1", "로그인 회의록")
    fake_drive_source.bodies["d1"] = "드라이브 로그인 본문"
    fake_notion_source.bodies["n1"] = "노션 로그인 본문"

    items = search_service.search("로그인", DocumentSearchOptions())

    assert {i.source for i in items} == {SOURCE_DRIVE, SOURCE_NOTION}


# --- 기능 6: project 필터 -------------------------------------------------------


@pytest.fixture()
def fake_drive_source_b():
    """프로젝트 B 전용 Drive 페이크."""
    from tests.fixtures.document_sources import FakeDocumentSource

    return FakeDocumentSource(SOURCE_DRIVE)


@pytest.fixture()
def two_project_resolver(make_project_resolver, fake_drive_source, fake_drive_source_b):
    """A(folder-a)/B(folder-b) 두 프로젝트가 매핑된 resolver."""
    return make_project_resolver(
        drive_mapping={
            _PROJECT_A: ("folder-a", fake_drive_source),
            _PROJECT_B: ("folder-b", fake_drive_source_b),
        }
    )


@pytest.fixture()
def two_project_search_service(db_session, two_project_resolver):
    """A/B 두 프로젝트가 매핑된 DocumentSearchService."""
    return DocumentSearchService(
        meta_repo=DocumentMetaRepository(db_session), resolver=two_project_resolver
    )


def test_project_filter_excludes_other_project_results(
    db_session, two_project_search_service, fake_drive_source, fake_drive_source_b
) -> None:
    """search_documents(query, project="A") 결과에 B 문서가 없다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 A 문서", project=_PROJECT_A)
    _seed_meta(db_session, SOURCE_DRIVE, "d2", "로그인 B 문서", project=_PROJECT_B)
    fake_drive_source.bodies["d1"] = "A 본문"
    fake_drive_source_b.bodies["d2"] = "B 본문"

    items = two_project_search_service.search(
        "로그인", DocumentSearchOptions(project=_PROJECT_A)
    )

    assert {i.title for i in items} == {"로그인 A 문서"}


def test_project_filter_never_calls_other_projects_fetch(
    db_session, two_project_search_service, fake_drive_source, fake_drive_source_b
) -> None:
    """search_documents(project="A") 는 B 의 어댑터 fetch() 를 한 번도 호출하지 않는다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 A 문서", project=_PROJECT_A)
    _seed_meta(db_session, SOURCE_DRIVE, "d2", "로그인 B 문서", project=_PROJECT_B)
    fake_drive_source.bodies["d1"] = "A 본문"
    fake_drive_source_b.bodies["d2"] = "B 본문"

    two_project_search_service.search("로그인", DocumentSearchOptions(project=_PROJECT_A))

    assert fake_drive_source_b.fetch_call_count == 0


def test_project_filter_no_candidate_calls_no_fetch(
    db_session, two_project_search_service, fake_drive_source, fake_drive_source_b
) -> None:
    """1단계 후보가 0건이면 어떤 프로젝트의 fetch() 도 호출되지 않는다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "무관한 제목", project=_PROJECT_A)
    fake_drive_source.bodies["d1"] = "본문"

    items = two_project_search_service.search(
        "존재하지않는키워드", DocumentSearchOptions(project=_PROJECT_A)
    )

    assert items == []
    assert fake_drive_source.fetch_call_count == 0
    assert fake_drive_source_b.fetch_call_count == 0


# --- 기능 7: 2단계 스니펫·점수 --------------------------------------------------


def test_snippet_contains_query_context(
    db_session, search_service, fake_drive_source
) -> None:
    """스니펫은 본문에서 질의어가 등장하는 구간을 잘라 만든다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "인증 문서")
    fake_drive_source.bodies["d1"] = "앞부분 잡담. " * 20 + "핵심은 refresh 토큰 회전이다."

    items = search_service.search("인증 refresh", DocumentSearchOptions())

    assert "refresh" in items[0].snippet


def test_snippet_finds_whitespace_variant_match_not_file_start(
    db_session, search_service, fake_drive_source
) -> None:
    """질의 토큰이 붙여쓰기('주문목록')여도 본문의 띄어쓰기 표현('주문 목록')
    구간에서 스니펫이 시작되지, 본문 뒤쪽의 무관한 exact 매치로 잘못 잡히지 않는다.

    실제 회귀 사례: 질의 '주문목록 API' 검색 시, 본문에 '주문목록'이라는
    붙은 문자열이 없어 collapse 보정 없이는 다른 토큰('api')의 exact 매치
    위치로 스니펫이 잘못 잡혔다. 이 테스트는 '주문 목록'(collapse 매치)이
    'api'(exact 매치)보다 본문에서 더 앞에 있는 상황을 구성해, "exact 매치가
    하나라도 있으면 collapse 시도 자체를 건너뛰는" 구버그를 재현한다 —
    그 버그가 있으면 뒤쪽 'api' 위치가 선택돼 '주문' 관련 내용이 스니펫에
    빠진다.
    """
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "API 명세서")
    fake_drive_source.bodies["d1"] = (
        "여기부터 주문 목록 조회 관련 설명이 이어진다. "
        + "무관한 설명이 계속 이어진다. " * 60
        + "맨 뒤쪽에서야 POST /api/user/signup 이 등장한다."
    )

    items = search_service.search("주문목록 API", DocumentSearchOptions())

    assert "주문" in items[0].snippet
    assert "signup" not in items[0].snippet


def test_body_match_outranks_title_only_match(
    db_session, search_service, fake_drive_source
) -> None:
    """제목만 걸린 문서보다 본문까지 일치하는 문서의 점수가 높다."""
    _seed_meta(db_session, SOURCE_DRIVE, "hit", "로그인 상세 설계")
    _seed_meta(db_session, SOURCE_DRIVE, "miss", "로그인 목차")
    fake_drive_source.bodies["hit"] = "세션 만료 정책을 정리한다."
    fake_drive_source.bodies["miss"] = "관련 없는 내용."

    items = search_service.search("로그인 세션", DocumentSearchOptions())

    assert items[0].title == "로그인 상세 설계"
    assert items[0].score > items[1].score


def test_empty_body_falls_back_to_helpful_snippet(
    db_session, search_service, fake_drive_source
) -> None:
    """본문이 비어 있어도 빈 스니펫이 아니라 상황을 설명하는 문구를 돌려준다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 빈문서")
    fake_drive_source.bodies["d1"] = ""

    items = search_service.search("로그인", DocumentSearchOptions())

    assert items[0].snippet
    assert "로그인 빈문서" in items[0].snippet


def test_fetch_failure_skips_only_that_document(
    db_session, search_service, fake_drive_source
) -> None:
    """개별 문서 fetch 실패는 그 문서만 건너뛰고 검색 전체를 실패시키지 않는다."""
    _seed_meta(db_session, SOURCE_DRIVE, "ok", "로그인 정상")
    _seed_meta(db_session, SOURCE_DRIVE, "bad", "로그인 권한없음")
    fake_drive_source.bodies["ok"] = "로그인 본문"
    fake_drive_source.bodies["bad"] = "로그인 본문"
    fake_drive_source.failing_fetch_ids = {"bad"}

    items = search_service.search("로그인", DocumentSearchOptions())

    assert [i.title for i in items] == ["로그인 정상"]


def test_results_are_deterministic(db_session, search_service, fake_drive_source) -> None:
    """같은 입력이면 같은 결과 순서를 돌려준다(결정성)."""
    for index in range(5):
        _seed_meta(db_session, SOURCE_DRIVE, f"d{index}", "로그인 문서")
        fake_drive_source.bodies[f"d{index}"] = "동일 본문"

    first = search_service.search("로그인", DocumentSearchOptions(top_k=3))
    second = search_service.search("로그인", DocumentSearchOptions(top_k=3))

    assert [i.title for i in first] == [i.title for i in second]
    assert [i.score for i in first] == [i.score for i in second]


# --- 기능 7: 입력 검증 ---------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_raises_validation_error(search_service, query: str) -> None:
    """빈 질의는 ValidationError 다."""
    with pytest.raises(ValidationError):
        search_service.search(query, DocumentSearchOptions())


def test_symbol_only_query_raises_validation_error(search_service) -> None:
    """검색 가능한 토큰이 없는 질의는 ValidationError 다."""
    with pytest.raises(ValidationError):
        search_service.search("!!!", DocumentSearchOptions())


@pytest.mark.parametrize("top_k", [0, -1, 51])
def test_out_of_range_top_k_raises_validation_error(search_service, top_k: int) -> None:
    """top_k 경계값(1~50)을 벗어나면 ValidationError 다."""
    with pytest.raises(ValidationError):
        search_service.search("로그인", DocumentSearchOptions(top_k=top_k))


@pytest.mark.parametrize("top_k", [1, 50])
def test_boundary_top_k_is_accepted(search_service, top_k: int) -> None:
    """top_k 경계값 1 과 50 은 허용된다."""
    assert search_service.search("로그인", DocumentSearchOptions(top_k=top_k)) == []


def test_unknown_source_raises_validation_error(search_service) -> None:
    """허용되지 않은 source 값은 ValidationError 다."""
    with pytest.raises(ValidationError):
        search_service.search("로그인", DocumentSearchOptions(source="dropbox"))


# --- 미구성 vs 결과 없음 구별 ---------------------------------------------------


def test_unconfigured_sources_raise_integration_error(db_session, make_project_resolver) -> None:
    """소스가 하나도 구성돼 있지 않으면 침묵하지 않고 IntegrationError 를 낸다.

    빈 리스트로 응답하면 호출 LLM 이 "관련 문서 없음"과 "서버 미설정"을
    구별할 수 없다.
    """
    resolver = make_project_resolver()
    service = DocumentSearchService(meta_repo=DocumentMetaRepository(db_session), resolver=resolver)

    with pytest.raises(IntegrationError, match="no document source is configured"):
        service.search("로그인", DocumentSearchOptions())


def test_unconfigured_specific_source_raises_integration_error(
    db_session, fake_drive_source, make_project_resolver
) -> None:
    """Drive 만 구성된 상태에서 notion 을 지정하면 IntegrationError 다."""
    resolver = make_project_resolver(
        drive_mapping={DEFAULT_PROJECT: ("folder-default", fake_drive_source)}
    )
    service = DocumentSearchService(meta_repo=DocumentMetaRepository(db_session), resolver=resolver)

    with pytest.raises(IntegrationError, match="not configured"):
        service.search("로그인", DocumentSearchOptions(source=SOURCE_NOTION))


def test_configured_but_empty_cache_still_returns_empty_list(search_service) -> None:
    """소스는 구성됐고 캐시만 비어 있으면 오류가 아니라 빈 리스트다(과잉 교정 방지)."""
    assert search_service.search("로그인", DocumentSearchOptions()) == []


def test_configured_with_no_matching_document_returns_empty_list(
    db_session, search_service, fake_drive_source
) -> None:
    """구성도 되고 캐시도 찼는데 매칭만 없으면 빈 리스트다(오류 아님)."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "배포 운영 가이드")
    fake_drive_source.bodies["d1"] = "본문"

    assert search_service.search("전혀무관한질의", DocumentSearchOptions()) == []


def test_unconfigured_message_matches_refresh_index(
    db_session, fake_drive_source, make_project_resolver
) -> None:
    """미구성 메시지가 refresh_index 경로와 동일해 사용자 혼선을 줄인다."""
    from app.services.documents.document_index_service import DocumentIndexService

    empty_resolver = make_project_resolver()
    search = DocumentSearchService(
        meta_repo=DocumentMetaRepository(db_session), resolver=empty_resolver
    )
    index = DocumentIndexService(
        session=db_session,
        meta_repo=DocumentMetaRepository(db_session),
        resolver=make_project_resolver(),
    )

    with pytest.raises(IntegrationError) as search_error:
        search.search("로그인", DocumentSearchOptions())
    with pytest.raises(IntegrationError) as index_error:
        index.refresh()

    assert str(search_error.value) == str(index_error.value)


# --- 기능 8: get_document ------------------------------------------------------


def test_get_document_returns_latest_content(
    db_session, search_service, fake_drive_source
) -> None:
    """원문 조회는 캐시가 아니라 fetch 시점의 최신 본문을 돌려준다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "배포 가이드")
    fake_drive_source.bodies["d1"] = "v1 본문"

    first = search_service.get_document(SOURCE_DRIVE, "d1")
    fake_drive_source.bodies["d1"] = "v2 본문"
    second = search_service.get_document(SOURCE_DRIVE, "d1")

    assert first.content == "v1 본문"
    assert second.content == "v2 본문"
    assert second.title == "배포 가이드"


def test_get_document_unknown_id_raises_integration_error(search_service) -> None:
    """존재하지 않는 external_id 는 IntegrationError 다."""
    with pytest.raises(IntegrationError):
        search_service.get_document(SOURCE_DRIVE, "no-such-file")


def test_get_document_unknown_source_raises_validation_error(search_service) -> None:
    """허용되지 않은 source 는 ValidationError 다."""
    with pytest.raises(ValidationError):
        search_service.get_document("dropbox", "d1")


def test_get_document_empty_external_id_raises_validation_error(search_service) -> None:
    """빈 external_id 는 ValidationError 다."""
    with pytest.raises(ValidationError):
        search_service.get_document(SOURCE_DRIVE, "  ")


def test_get_document_unconfigured_source_raises_integration_error(
    db_session, make_project_resolver
) -> None:
    """구성되지 않은 소스로 조회하면 IntegrationError 다."""
    resolver = make_project_resolver()
    service = DocumentSearchService(meta_repo=DocumentMetaRepository(db_session), resolver=resolver)

    with pytest.raises(IntegrationError):
        service.get_document(SOURCE_NOTION, "n1")


def test_get_document_without_cached_meta_still_returns_content(
    search_service, fake_drive_source
) -> None:
    """메타 캐시에 없는 문서도 본문 조회는 가능하다(계약: title/url 은 "" 로 확정).

    메타가 없으면 DEFAULT_PROJECT 의 해당 source 어댑터로 폴백한다. content 는
    fetch 시점의 값 그대로이고, title/url 은 "메타데이터가 캐시되지 않음"을
    뜻하는 빈 문자열이다 — 식별자 기반 기본값이 아니다(항목4 계약 고정).
    예외 없이 정상 반환된다.
    """
    fake_drive_source.bodies["orphan"] = "캐시에 없는 문서 본문"

    content = search_service.get_document(SOURCE_DRIVE, "orphan")

    assert content.content == "캐시에 없는 문서 본문"
    assert content.title == ""
    assert content.url == ""


def test_get_document_propagates_truncated_flag_from_source(
    db_session, search_service, fake_drive_source
) -> None:
    """어댑터가 truncated=True 로 fetch 했으면 get_document 결과에도 그대로 실린다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "대용량 문서")
    fake_drive_source.bodies["d1"] = "잘린 본문"
    fake_drive_source.truncated_ids.add("d1")

    content = search_service.get_document(SOURCE_DRIVE, "d1")

    assert content.truncated is True


def test_get_document_not_truncated_by_default(
    db_session, search_service, fake_drive_source
) -> None:
    """어댑터가 truncated=False 로 fetch 하면 get_document 결과도 False 다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "일반 문서")
    fake_drive_source.bodies["d1"] = "짧은 본문"

    content = search_service.get_document(SOURCE_DRIVE, "d1")

    assert content.truncated is False


def test_search_ranking_and_snippet_are_unaffected_by_truncated_body(
    db_session, search_service, fake_drive_source
) -> None:
    """검색 경로(스니펫/점수)는 어댑터의 truncated 플래그에 영향받지 않는다(회귀).

    _fetch_and_score 는 `document_source.fetch(...).text` 만 쓰고
    `.truncated` 는 버린다 — 원문 조회 경로(get_document)에만 의미가 있다.
    """
    _seed_meta(db_session, SOURCE_DRIVE, "truncated_doc", "로그인 설계서 A")
    _seed_meta(db_session, SOURCE_DRIVE, "plain_doc", "로그인 설계서 B")
    fake_drive_source.bodies["truncated_doc"] = "로그인 흐름 상세 설명"
    fake_drive_source.bodies["plain_doc"] = "로그인 흐름 상세 설명"
    fake_drive_source.truncated_ids.add("truncated_doc")

    items = search_service.search("로그인", DocumentSearchOptions(top_k=2))

    by_title = {i.title: i for i in items}
    assert by_title["로그인 설계서 A"].score == by_title["로그인 설계서 B"].score
    assert by_title["로그인 설계서 A"].snippet == by_title["로그인 설계서 B"].snippet


def test_get_document_includes_version_parsed_from_title(
    db_session, search_service, fake_drive_source
) -> None:
    """get_document 결과에도 title 에서 파싱한 version 이 실린다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "배포 가이드 v3")
    fake_drive_source.bodies["d1"] = "본문"

    content = search_service.get_document(SOURCE_DRIVE, "d1")

    assert content.version == "v3"


def test_get_document_without_cached_meta_has_none_version(
    search_service, fake_drive_source
) -> None:
    """메타 캐시에 없는 문서는 title 이 빈 문자열이라 version 도 None 이다."""
    fake_drive_source.bodies["orphan"] = "캐시에 없는 문서 본문"

    content = search_service.get_document(SOURCE_DRIVE, "orphan")

    assert content.version is None


def test_get_document_picks_most_recently_synced_project_when_shared(
    db_session, two_project_search_service, fake_drive_source, fake_drive_source_b
) -> None:
    """같은 external_id 가 여러 project 에 있으면 가장 최근 last_synced_at 행의 project 를 쓴다."""
    older = DocumentMeta(
        project=_PROJECT_A,
        source=SOURCE_DRIVE,
        external_id="shared",
        title="A 문서",
        url="https://a",
        last_synced_at=datetime(2026, 7, 1, 9, 0, 0),
    )
    newer = DocumentMeta(
        project=_PROJECT_B,
        source=SOURCE_DRIVE,
        external_id="shared",
        title="B 문서",
        url="https://b",
        last_synced_at=datetime(2026, 7, 2, 9, 0, 0),
    )
    db_session.add(older)
    db_session.add(newer)
    db_session.commit()
    fake_drive_source_b.bodies["shared"] = "B 본문"

    content = two_project_search_service.get_document(SOURCE_DRIVE, "shared")

    assert content.title == "B 문서"
    assert content.content == "B 본문"


# --- 질의확장(query_variants, 1단계 SQL 후보 게이트 확장) -----------------------


def test_query_variants_widen_sql_candidate_gate(
    db_session, search_service, fake_drive_source
) -> None:
    """제목에 원본 질의 토큰이 없어도 query_variants 토큰과 일치하면 후보에 포함된다.

    질의확장은 이제 서버(LLM 호출)가 아니라 호출자(Claude)가 search_documents
    호출 시 query_variants 로 동의어를 함께 넘기는 방식이다.
    """
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "결제 내역 조회 가이드")
    fake_drive_source.bodies["d1"] = "주문 상태를 확인하는 방법을 설명한다."

    items = search_service.search(
        "주문조회 API", DocumentSearchOptions(query_variants=["결제", "내역"])
    )

    assert [i.title for i in items] == ["결제 내역 조회 가이드"]


def test_query_variants_do_not_affect_score(
    db_session, search_service, fake_drive_source
) -> None:
    """query_variants 토큰은 후보를 넓히는 용도로만 쓰이고, 점수 계산에는 원본 토큰만 반영된다.

    "로그인"만 검색해도 확장 토큰("계정")이 점수 계산에 섞이면 제목에
    "계정"만 있고 "로그인"이 없는 문서도 원본 토큰 일치 문서와 동일하거나
    더 높은 점수를 받을 수 있다. 원본 토큰만 점수에 반영돼야 순위가
    질의 그대로의 정합성을 유지한다.
    """
    _seed_meta(db_session, SOURCE_DRIVE, "exact", "로그인 설계서")
    _seed_meta(db_session, SOURCE_DRIVE, "expanded_only", "계정 관리 문서")
    fake_drive_source.bodies["exact"] = "로그인 흐름 설명"
    fake_drive_source.bodies["expanded_only"] = "계정 정보 변경 절차"

    items = search_service.search(
        "로그인", DocumentSearchOptions(query_variants=["계정"])
    )

    titles = [i.title for i in items]
    assert "로그인 설계서" in titles
    assert titles.index("로그인 설계서") < (
        titles.index("계정 관리 문서") if "계정 관리 문서" in titles else len(titles)
    )


def test_no_query_variants_behaves_like_before(
    db_session, search_service, fake_drive_source
) -> None:
    """query_variants 를 주지 않으면(기본 None) 기존 동작과 동일하게 원본 토큰만 쓴다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 설계서")
    fake_drive_source.bodies["d1"] = "로그인 흐름 설명"

    items = search_service.search("로그인", DocumentSearchOptions())

    assert [i.title for i in items] == ["로그인 설계서"]


def test_top_k_one_original_match_wins_fetch_slot_over_variant_only(
    db_session, search_service, fake_drive_source
) -> None:
    """원본매치는 fetch 예산이 후보 수보다 부족해도 항상 fetch 슬롯을 먼저 받는다.

    fetch 예산은 이제 top_k 가 아니라 top_k*BODY_FETCH_OVERSCAN(상한
    MAX_BODY_FETCH_CANDIDATES)으로 정해지지만(top_k=1 → 예산 3), 그 예산도
    후보 4건(원본매치 1 + 확장전용매치 3)보다 작다. 정렬키(원본매치 여부
    우선)가 예산이 부족한 상황에서도 원본매치를 항상 먼저 슬롯에 채워야
    하는 회귀 방지 테스트 — 남는 슬롯 2개만 확장전용매치가 external_id
    오름차순으로 채운다.
    """
    _seed_meta(db_session, SOURCE_DRIVE, "original", "로그인 설계서")
    _seed_meta(db_session, SOURCE_DRIVE, "expanded_1", "계정 관리 문서 A")
    _seed_meta(db_session, SOURCE_DRIVE, "expanded_2", "계정 관리 문서 B")
    _seed_meta(db_session, SOURCE_DRIVE, "expanded_3", "계정 관리 문서 C")
    fake_drive_source.bodies["original"] = "로그인 흐름 설명"
    fake_drive_source.bodies["expanded_1"] = "계정 정보 변경 절차 A"
    fake_drive_source.bodies["expanded_2"] = "계정 정보 변경 절차 B"
    fake_drive_source.bodies["expanded_3"] = "계정 정보 변경 절차 C"

    items = search_service.search(
        "로그인", DocumentSearchOptions(top_k=1, query_variants=["계정"])
    )

    assert [i.title for i in items] == ["로그인 설계서"]
    assert fake_drive_source.fetch_call_count == 3
    assert "expanded_3" not in fake_drive_source.fetched_ids


def test_top_k_two_includes_both_but_original_match_ranks_first(
    db_session, search_service, fake_drive_source
) -> None:
    """top_k=2 로 슬롯이 남으면 확장전용매치도 포함되지만, 원본매치가 항상 상위다.

    정렬키 1순위(title_score<=0)가 두 그룹을 분리한 뒤, fetch 슬롯이 남으면
    확장전용매치도 2단계까지 진출할 수 있어야 한다(확장의 목적). 다만 최종
    결과 순서에서도 원본매치가 앞서야 한다.
    """
    _seed_meta(db_session, SOURCE_DRIVE, "original", "로그인 설계서")
    _seed_meta(db_session, SOURCE_DRIVE, "expanded_only", "계정 관리 문서")
    fake_drive_source.bodies["original"] = "로그인 흐름 설명"
    fake_drive_source.bodies["expanded_only"] = "계정 정보 변경 절차"

    items = search_service.search(
        "로그인", DocumentSearchOptions(top_k=2, query_variants=["계정"])
    )

    assert {i.title for i in items} == {"로그인 설계서", "계정 관리 문서"}
    assert items[0].title == "로그인 설계서"


def test_query_variant_whitespace_difference_reaches_collapse_match(
    db_session, search_service, fake_drive_source
) -> None:
    """원본 질의와 variant 가 공백 유무만 다를 때, variant 의 collapse 매칭으로 후보에 든다.

    제목 "트러 블슈팅 가이드"는 중간에 공백이 끼어 있어, variant "트러블슈팅"
    (공백 없음)이 토큰 단위 ILIKE 로는 절대 부분일치하지 않는다(문자열 중간에
    공백이 끼어 있어 substring 이 끊긴다) — 오직 collapse(공백 제거) 매칭만
    이 문서를 찾을 수 있다. 원본 질의 "이슈 해결"도 제목과 전혀 무관해
    토큰 경로가 우연히 성공할 여지를 차단한다. _select_candidates 가
    query_variants 원문을 collapse 매칭용 queries 에 그대로 실어 보내야
    (P0 수정 대상) 이 문서가 1단계 후보에 잡힌다.
    """
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "트러 블슈팅 가이드")
    fake_drive_source.bodies["d1"] = "장애 대응 절차를 정리한다."

    items = search_service.search(
        "이슈 해결", DocumentSearchOptions(query_variants=["트러블슈팅"])
    )

    assert [i.title for i in items] == ["트러 블슈팅 가이드"]


def test_query_variants_ignores_blank_and_empty_entries(
    db_session, search_service, fake_drive_source
) -> None:
    """query_variants 에 빈 문자열/공백만 있는 항목이 섞여 있어도 정상 동작한다."""
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 설계서")
    fake_drive_source.bodies["d1"] = "로그인 흐름 설명"

    items = search_service.search(
        "로그인", DocumentSearchOptions(query_variants=["", "   "])
    )

    assert [i.title for i in items] == ["로그인 설계서"]


# --- doc36 Phase3 #12: indexed 전략(title+keyword+vector 3-arm RRF) -----------
# docs/architect-review/39_document_search_phase3_rrf_verdict.md §3-6


def _seed_indexed_document(
    session,
    document_id: str,
    project: str,
    source: str,
    external_id: str,
    title: str,
    url: str,
    chunk_texts: list[str],
) -> None:
    """색인된 문서 한 건(Document + section 청크 N개 + document_meta.document_id 연결)을 저장한다."""
    session.add(
        Document(
            id=document_id,
            project=project,
            source_url=None,
            title=title,
            content_hash="hash",
            raw_text="\n".join(chunk_texts),
            doc_type=source,
        )
    )
    session.flush()
    for idx, chunk_text in enumerate(chunk_texts):
        session.add(
            Chunk(
                id=f"{document_id}-c{idx}",
                document_id=document_id,
                chunk_type="section",
                ref_id=f"{document_id}-sec{idx}",
                text=chunk_text,
            )
        )
    session.add(
        DocumentMeta(
            project=project,
            source=source,
            external_id=external_id,
            title=title,
            url=url,
            last_synced_at=datetime(2026, 7, 1, 12, 0, 0),
            document_id=document_id,
        )
    )
    session.commit()


@pytest.fixture()
def indexed_search_service(db_session, default_resolver):
    """indexed 전략(벡터 arm 비활성 — HashEmbeddingProvider 는 의미 유사도가 없어 결정성을 위해 끈다)."""
    return DocumentSearchService(
        meta_repo=DocumentMetaRepository(db_session),
        resolver=default_resolver,
        chunk_repo=ChunkRepository(db_session),
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
        vector_fallback_enabled=False,
        document_search_strategy="indexed",
    )


def test_indexed_strategy_finds_body_only_match_absent_from_title(
    db_session, indexed_search_service
) -> None:
    """제목엔 전혀 없고 본문(색인된 section 청크)에만 있는 질의 토큰으로도 문서를 찾는다.

    Phase3(본문 색인) 도입의 존재 이유 그 자체 — title 게이트를 유일한 후보
    공급원으로 남기는 구조(doc39 §1.1 이 반려한 (A)안)라면 이 테스트는
    영원히 실패한다.
    """
    _seed_indexed_document(
        db_session,
        document_id="drive:doc-body-only",
        project=DEFAULT_PROJECT,
        source=SOURCE_DRIVE,
        external_id="body-only",
        title="완전히 무관한 제목",
        url="https://example.test/drive/body-only",
        chunk_texts=["여기에만 리프레시토큰회전 이라는 특수 표현이 등장한다."],
    )

    items = indexed_search_service.search("리프레시토큰회전", DocumentSearchOptions())

    assert [i.title for i in items] == ["완전히 무관한 제목"]
    assert "리프레시토큰회전" in items[0].snippet
    assert items[0].snippet_as_of is not None
    assert items[0].external_id == "body-only"


def test_indexed_strategy_collapses_multiple_section_hits_into_one_result(
    db_session, indexed_search_service
) -> None:
    """한 문서의 여러 section 청크가 모두 히트해도 결과는 문서 1건으로 접힌다.

    융합 키가 `ref_id`(섹션 ID)가 아니라 `Chunk.document_id` 여야 성립한다
    (doc39 §2.2) — ref_id 로 접으면 문서 하나가 섹션 수만큼 슬롯을 먹는다.
    """
    _seed_indexed_document(
        db_session,
        document_id="drive:doc-multi-section",
        project=DEFAULT_PROJECT,
        source=SOURCE_DRIVE,
        external_id="multi-section",
        title="무관 제목",
        url="https://example.test/drive/multi-section",
        chunk_texts=[
            "첫 섹션에도 멀티섹션검색어 가 등장한다.",
            "둘째 섹션에도 멀티섹션검색어 가 등장한다.",
        ],
    )

    items = indexed_search_service.search("멀티섹션검색어", DocumentSearchOptions())

    assert len(items) == 1


def test_indexed_strategy_unindexed_document_survives_via_title_arm(
    db_session, indexed_search_service
) -> None:
    """본문이 색인되지 않은 문서(document_meta.document_id=NULL)도 title 매칭만으로 살아남는다.

    title arm 의 문서 키는 `deterministic_document_id(project,source,external_id)`
    로 순수 계산되므로, 이 문서를 표시하려면 title arm 자체가 들고 있는
    `DocumentMeta` 를 메타 원천으로 써야 한다 — `document_meta.document_id`
    로만 역매핑하면(그 컬럼이 NULL이라) 이 문서는 조용히 유실된다(회귀 방지).
    """
    _seed_meta(db_session, SOURCE_DRIVE, "unindexed", "미색인 특수문서 제목")

    items = indexed_search_service.search("미색인", DocumentSearchOptions())

    assert [i.title for i in items] == ["미색인 특수문서 제목"]
    assert items[0].snippet_as_of is None
    assert items[0].external_id == "unindexed"


def test_indexed_strategy_source_filter_excludes_other_source_body_match(
    db_session, indexed_search_service
) -> None:
    """source 필터는 body-only 매치에도 그대로 적용된다(청크 arm 은 project 스코프만 걸기 때문에 별도 후처리가 필요)."""
    _seed_indexed_document(
        db_session,
        document_id="notion:doc-wrong-source",
        project=DEFAULT_PROJECT,
        source=SOURCE_NOTION,
        external_id="wrong-source",
        title="무관 제목",
        url="https://example.test/notion/wrong-source",
        chunk_texts=["소스필터전용검색어 가 여기 있다."],
    )

    items = indexed_search_service.search(
        "소스필터전용검색어", DocumentSearchOptions(source=SOURCE_DRIVE)
    )

    assert items == []


def test_indexed_strategy_excludes_registered_document_section_chunks(
    db_session, indexed_search_service, caplog
) -> None:
    """등록형 문서(doc_type=markdown 등)의 section 청크는 keyword/vector arm 후보에서 제외된다.

    register_document 경로도 doc_type=drive/notion 이 아닌 채로
    chunk_type="section" 청크를 만든다(45번 리뷰 §3.2). 이 청크가 후보에
    섞이면 document_meta 에 대응 행이 없어 "메타를 찾을 수 없음" WARNING이
    찍히고 recall 이 깎인다 — doc_type 푸시다운이 있으면 둘 다 발생하지 않는다.
    """
    db_session.add(
        Document(
            id="registered-doc",
            project=DEFAULT_PROJECT,
            source_url=None,
            title="등록형 문서",
            content_hash="hash",
            raw_text="본문",
            doc_type="markdown",
        )
    )
    db_session.flush()
    db_session.add(
        Chunk(
            id="registered-doc-c0",
            document_id="registered-doc",
            chunk_type="section",
            ref_id="registered-doc-sec0",
            text="등록형문서전용검색어 가 여기 있다.",
        )
    )
    db_session.commit()

    with caplog.at_level("WARNING"):
        items = indexed_search_service.search("등록형문서전용검색어", DocumentSearchOptions())

    assert items == []
    assert "메타를 찾을 수 없음" not in caplog.text


def test_indexed_strategy_recall_survives_alongside_registered_document(
    db_session, indexed_search_service
) -> None:
    """등록형 문서 청크가 같은 project 에 섞여 있어도 협업 문서 body-only 매치는 살아남는다."""
    db_session.add(
        Document(
            id="registered-doc-2",
            project=DEFAULT_PROJECT,
            source_url=None,
            title="등록형 문서2",
            content_hash="hash",
            raw_text="본문",
            doc_type="markdown",
        )
    )
    db_session.flush()
    db_session.add(
        Chunk(
            id="registered-doc-2-c0",
            document_id="registered-doc-2",
            chunk_type="section",
            ref_id="registered-doc-2-sec0",
            text="공용검색어 가 여기도 있다.",
        )
    )
    db_session.commit()
    _seed_indexed_document(
        db_session,
        document_id="drive:doc-shared-term",
        project=DEFAULT_PROJECT,
        source=SOURCE_DRIVE,
        external_id="shared-term",
        title="무관 제목",
        url="https://example.test/drive/shared-term",
        chunk_texts=["여기도 공용검색어 가 등장한다."],
    )

    items = indexed_search_service.search("공용검색어", DocumentSearchOptions())

    assert [i.title for i in items] == ["무관 제목"]


def test_indexed_strategy_source_filter_does_not_starve_top_k(
    db_session, indexed_search_service, monkeypatch
) -> None:
    """source 필터를 걸어도, 다른 source 문서가 fused 후보 폭을 다 차지해 top_k 를
    못 채우는 일이 없다(45번 리뷰 §3.3) — keyword/vector arm 자체가 source 로
    좁혀야 한다. width 를 인위적으로 1로 낮춰, 다른 source 문서가 유일한
    후보 슬롯을 차지하면 즉시 재현되는 회귀를 검증한다.
    """
    import app.services.documents.document_search_service as service_module

    monkeypatch.setattr(service_module, "_RRF_MIN_CANDIDATE_WIDTH", 1)
    monkeypatch.setattr(service_module, "_RRF_CANDIDATE_WIDTH_MULTIPLIER", 1)

    _seed_indexed_document(
        db_session,
        document_id="drive:doc-other-source",
        project=DEFAULT_PROJECT,
        source=SOURCE_DRIVE,
        external_id="other-source",
        title="드라이브 문서",
        url="https://example.test/drive/other-source",
        chunk_texts=["폭독점검색어 가 드라이브 문서에도 있다."],
    )
    _seed_indexed_document(
        db_session,
        document_id="notion:doc-target-source",
        project=DEFAULT_PROJECT,
        source=SOURCE_NOTION,
        external_id="target-source",
        title="노션 문서",
        url="https://example.test/notion/target-source",
        chunk_texts=["폭독점검색어 가 노션 문서에도 있다."],
    )

    items = indexed_search_service.search(
        "폭독점검색어", DocumentSearchOptions(top_k=1, source=SOURCE_NOTION)
    )

    assert [i.title for i in items] == ["노션 문서"]


def test_unrecognized_document_search_strategy_degrades_to_fetch(
    db_session, default_resolver, fake_drive_source
) -> None:
    """인식 못 하는 전략 값은 fetch(기존 경로)로 degrade 한다(doc39 §2.7)."""
    service = DocumentSearchService(
        meta_repo=DocumentMetaRepository(db_session),
        resolver=default_resolver,
        chunk_repo=ChunkRepository(db_session),
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
        vector_fallback_enabled=False,
        document_search_strategy="not-a-real-strategy",
    )
    _seed_meta(db_session, SOURCE_DRIVE, "d1", "로그인 설계서")
    fake_drive_source.bodies["d1"] = "로그인 흐름 설명"

    items = service.search("로그인", DocumentSearchOptions())

    assert [i.title for i in items] == ["로그인 설계서"]


# --- 57번 리뷰 개선1: 근거·메타 필드 -----------------------------------------


def test_build_match_reasons_orders_arms_then_filters_then_degrade_signal() -> None:
    """arm 기여 -> 필터 일치 -> 강등 신호 순서로, 고정 문자열을 낸다."""
    reasons = _build_match_reasons(
        contributing_arms=(ARM_TITLE, ARM_KEYWORD, ARM_VECTOR),
        project="payments",
        source="drive",
        indexed=False,
    )

    assert reasons == (
        REASON_TITLE_MATCH,
        REASON_KEYWORD_MATCH,
        REASON_VECTOR_MATCH,
        "프로젝트 필터 일치: payments",
        "출처 필터 일치: drive",
        REASON_UNINDEXED,
    )


def test_build_match_reasons_no_filters_no_degrade_when_indexed() -> None:
    """필터가 없고 indexed=True 면 arm 기여 문구만 남는다."""
    reasons = _build_match_reasons(
        contributing_arms=(ARM_KEYWORD,), project=None, source=None, indexed=True
    )

    assert reasons == (REASON_KEYWORD_MATCH,)


def test_build_match_reasons_empty_arms_returns_only_filters() -> None:
    """기여 arm 이 없으면(이론상 발생하지 않지만) 필터 문구만 남는다."""
    reasons = _build_match_reasons(
        contributing_arms=(), project=None, source="notion", indexed=True
    )

    assert reasons == ("출처 필터 일치: notion",)


def test_assemble_matched_chunks_two_entries_when_winners_differ() -> None:
    """keyword 승자와 vector 승자가 다른 청크면 항목 2건이 각 arm 라벨로 담긴다."""
    chunks = _assemble_matched_chunks(
        document_id="doc1",
        keyword_chunk_by_doc={"doc1": "chunk-kw"},
        vector_chunk_by_doc={"doc1": "chunk-vec"},
        chunk_texts={"chunk-kw": "키워드 승자 텍스트", "chunk-vec": "벡터 승자 텍스트"},
    )

    assert [(c.chunk_id, c.arm) for c in chunks] == [
        ("chunk-kw", ARM_KEYWORD),
        ("chunk-vec", ARM_VECTOR),
    ]
    assert chunks[0].chunk_type == "section"


def test_assemble_matched_chunks_merges_same_winner_into_both() -> None:
    """keyword/vector 승자가 같은 청크면 항목 1건, arm="both" 로 합쳐진다."""
    chunks = _assemble_matched_chunks(
        document_id="doc1",
        keyword_chunk_by_doc={"doc1": "chunk-shared"},
        vector_chunk_by_doc={"doc1": "chunk-shared"},
        chunk_texts={"chunk-shared": "공유 승자 텍스트"},
    )

    assert len(chunks) == 1
    assert chunks[0].chunk_id == "chunk-shared"
    assert chunks[0].arm == "both"


def test_assemble_matched_chunks_skips_chunk_id_missing_text() -> None:
    """chunk_texts 에 없는 chunk_id 는 건너뛴다."""
    chunks = _assemble_matched_chunks(
        document_id="doc1",
        keyword_chunk_by_doc={"doc1": "chunk-missing"},
        vector_chunk_by_doc={},
        chunk_texts={},
    )

    assert chunks == ()


def test_assemble_matched_chunks_no_winners_returns_empty() -> None:
    """이 문서에 대해 어느 arm 에도 승자 청크가 없으면 빈 튜플이다(예: title 단독 매치)."""
    chunks = _assemble_matched_chunks(
        document_id="doc1", keyword_chunk_by_doc={}, vector_chunk_by_doc={}, chunk_texts={}
    )

    assert chunks == ()


def test_indexed_strategy_matched_chunks_single_arm_document(
    db_session, indexed_search_service
) -> None:
    """벡터 arm 이 꺼진 fixture 에서는 keyword 승자 하나만 matched_chunks 에 담긴다."""
    _seed_indexed_document(
        db_session,
        document_id="drive:doc-single-arm",
        project=DEFAULT_PROJECT,
        source=SOURCE_DRIVE,
        external_id="single-arm",
        title="완전히 무관한 제목",
        url="https://example.test/drive/single-arm",
        chunk_texts=["첫 섹션에 근거필드테스트어휘 라는 표현이 있다."],
    )

    items = indexed_search_service.search("근거필드테스트어휘", DocumentSearchOptions())

    assert len(items) == 1
    item = items[0]
    assert len(item.matched_chunks) == 1
    assert item.matched_chunks[0].arm == ARM_KEYWORD
    assert item.matched_chunks[0].chunk_type == "section"
    assert item.indexed is True
    assert item.modified_at is None
    assert REASON_KEYWORD_MATCH in item.match_reasons


def test_indexed_strategy_unindexed_document_marks_degrade_signal(
    db_session, indexed_search_service
) -> None:
    """title arm 단독 매치(document_id NULL)는 match_reasons 에 강등 신호가 들어가고

    indexed=False 다.
    """
    _seed_meta(db_session, SOURCE_DRIVE, "unindexed-reason", "미색인근거필드테스트제목")

    items = indexed_search_service.search("미색인근거필드테스트제목", DocumentSearchOptions())

    assert len(items) == 1
    item = items[0]
    assert item.indexed is False
    assert item.matched_chunks == ()
    assert REASON_UNINDEXED in item.match_reasons
    assert REASON_TITLE_MATCH in item.match_reasons


def test_indexed_strategy_get_texts_by_ids_called_once(
    db_session, indexed_search_service, monkeypatch
) -> None:
    """승자 청크 텍스트 조회(get_texts_by_ids)는 요청당 정확히 1회다(추가 SQL 왕복 0 원칙)."""
    _seed_indexed_document(
        db_session,
        document_id="drive:doc-call-count",
        project=DEFAULT_PROJECT,
        source=SOURCE_DRIVE,
        external_id="call-count",
        title="무관 제목",
        url="https://example.test/drive/call-count",
        chunk_texts=["호출횟수테스트어휘 가 여기 있다."],
    )
    original = ChunkRepository.get_texts_by_ids
    call_count = 0

    def _spy(self, chunk_ids):
        nonlocal call_count
        call_count += 1
        return original(self, chunk_ids)

    monkeypatch.setattr(ChunkRepository, "get_texts_by_ids", _spy)

    indexed_search_service.search("호출횟수테스트어휘", DocumentSearchOptions())

    assert call_count == 1


def test_fetch_strategy_item_carries_same_evidence_fields(
    db_session, search_service, fake_drive_source
) -> None:
    """fetch 전략(롤백 스위치)도 같은 계약(matched_chunks/match_reasons/modified_at/indexed)을

    채운다.
    """
    _seed_meta(
        db_session, SOURCE_DRIVE, "fetch-evidence", "근거계약테스트문서", project=DEFAULT_PROJECT
    )
    fake_drive_source.bodies["fetch-evidence"] = "근거계약테스트문서 본문 내용"

    items = search_service.search(
        "근거계약테스트문서", DocumentSearchOptions(project=DEFAULT_PROJECT)
    )

    assert len(items) == 1
    item = items[0]
    assert item.matched_chunks == ()
    assert item.match_reasons[0] == REASON_LIVE_FETCH_MATCH
    assert f"프로젝트 필터 일치: {DEFAULT_PROJECT}" in item.match_reasons
    assert item.indexed is False
    assert item.modified_at is not None
    assert fake_drive_source.fetch_call_count == 1


# --- 57번 리뷰 §5 개선3 T3 개정: title arm 토큰 경계 게이트 --------------------


def test_indexed_strategy_title_gate_excludes_substring_noise(
    db_session, indexed_search_service
) -> None:
    """부분문자열 잡음('api' ⊂ 'Rapid')은 title arm 게이트에서 걸러져 결과에서 사라진다."""
    _seed_meta(db_session, SOURCE_DRIVE, "substring-noise", "Rapid Onboarding Guide")

    items = indexed_search_service.search("api", DocumentSearchOptions())

    assert items == []


def test_indexed_strategy_title_gate_passes_original_collapse_match(
    db_session, indexed_search_service
) -> None:
    """원본 질의의 collapse(연속 부분열) 매치는 게이트를 통과한다('결제장애' ↔ '결제 장애 대응')."""
    _seed_meta(db_session, SOURCE_DRIVE, "collapse-match", "결제 장애 대응 가이드")

    items = indexed_search_service.search("결제장애", DocumentSearchOptions())

    assert [i.title for i in items] == ["결제 장애 대응 가이드"]


def test_indexed_strategy_title_gate_passes_variant_only_full_token_match(
    db_session, indexed_search_service
) -> None:
    """원본으로 안 걸리고 variant 토큰 완전 일치로만 걸리는 행도 살아남는다(회귀 방지 핵심)."""
    _seed_meta(db_session, SOURCE_DRIVE, "variant-only", "Payment Failure Runbook")

    items = indexed_search_service.search(
        "결제 실패", DocumentSearchOptions(query_variants=["payment failure"])
    )

    assert [i.title for i in items] == ["Payment Failure Runbook"]


def test_indexed_strategy_title_only_document_ranks_below_body_matched_document(
    db_session, indexed_search_service
) -> None:
    """제목만 걸린 문서와 본문(keyword) 걸린 문서가 함께 있으면 본문 매치 문서가 위다."""
    _seed_meta(db_session, SOURCE_DRIVE, "title-only", "공통검색어테스트 문서")
    _seed_indexed_document(
        db_session,
        document_id="drive:doc-title-vs-body",
        project=DEFAULT_PROJECT,
        source=SOURCE_DRIVE,
        external_id="body-hit",
        title="완전히 무관한 제목",
        url="https://example.test/drive/body-hit",
        chunk_texts=["공통검색어테스트 라는 표현이 본문에 있다."],
    )

    items = indexed_search_service.search("공통검색어테스트", DocumentSearchOptions())

    assert [i.external_id for i in items] == ["body-hit", "title-only"]
