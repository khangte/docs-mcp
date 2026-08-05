"""DocumentSearchService 단위 테스트 (SPEC 기능 7, 8, 프로젝트 확장).

핵심 검증 대상은 "2단계 후보 압축"이 실제로 외부 API 호출을 줄이는지와,
project 필터가 검색·fetch 범위를 올바르게 좁히는지다. 페이크 소스의
`fetch_call_count` 로 다음을 단언한다.

- 1단계 후보가 0건이면 fetch 가 0회다.
- 한 번의 검색에서 fetch 수가 `top_k` 를 넘지 않는다.
- project="A" 로 좁히면 B 의 어댑터 fetch 가 한 번도 호출되지 않는다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.errors import IntegrationError, ValidationError
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION, DocumentMeta
from app.models.openapi import DEFAULT_PROJECT
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.services.documents.document_search_service import (
    DocumentSearchOptions,
    DocumentSearchService,
    documents_tokenize,
)
from tests.fixtures.document_sources import ExplodingDocumentSource

_PROJECT_A = "A"
_PROJECT_B = "B"


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


def test_fetch_count_never_exceeds_top_k(
    db_session, search_service, fake_drive_source
) -> None:
    """후보가 top_k 보다 많아도 실시간 fetch 는 top_k 건으로 제한된다."""
    for index in range(10):
        external_id = f"d{index}"
        _seed_meta(db_session, SOURCE_DRIVE, external_id, f"로그인 문서 {index}")
        fake_drive_source.bodies[external_id] = "로그인 관련 본문"
    fake_drive_source.reset_counts()

    items = search_service.search("로그인", DocumentSearchOptions(top_k=3))

    assert fake_drive_source.fetch_call_count == 3
    assert len(items) == 3


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
    """메타 캐시에 없는 문서도 본문 조회는 가능하다(제목/URL 만 비어 있음).

    메타가 없으면 DEFAULT_PROJECT 의 해당 source 어댑터로 폴백한다.
    """
    fake_drive_source.bodies["orphan"] = "캐시에 없는 문서 본문"

    content = search_service.get_document(SOURCE_DRIVE, "orphan")

    assert content.content == "캐시에 없는 문서 본문"
    assert content.title == ""


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
    """top_k=1 이고 원본매치 1건 + 확장전용매치 1건이 동시 후보일 때, 원본매치만 반환된다.

    top_k 는 2단계 본문 fetch 상한(rate limit 보호)의 핵심 장치다. 확장
    토큰으로만 걸린 후보가 이 한정된 fetch 슬롯을 원본 매치 문서로부터
    빼앗으면 rate limit 보호 의도가 깨진다. 정렬키 (title_score<=0 오름차순
    1순위)가 원본매치를 항상 앞세워야 하는 회귀 방지 테스트.
    """
    _seed_meta(db_session, SOURCE_DRIVE, "original", "로그인 설계서")
    _seed_meta(db_session, SOURCE_DRIVE, "expanded_only", "계정 관리 문서")
    fake_drive_source.bodies["original"] = "로그인 흐름 설명"
    fake_drive_source.bodies["expanded_only"] = "계정 정보 변경 절차"

    items = search_service.search(
        "로그인", DocumentSearchOptions(top_k=1, query_variants=["계정"])
    )

    assert [i.title for i in items] == ["로그인 설계서"]
    assert fake_drive_source.fetch_call_count == 1
    assert "expanded_only" not in fake_drive_source.fetched_ids


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
