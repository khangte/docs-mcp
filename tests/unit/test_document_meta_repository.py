"""DocumentMetaRepository 단위 테스트.

UNIQUE(project, source, external_id) 제약과 project/출처별 조회 동작을 검증한다.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import DEFAULT_PROJECT, Document
from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION, DocumentMeta
from app.repositories.document_filters import DocumentMetaFilter
from app.repositories.document_meta_repository import DocumentMetaRepository

_NOW = datetime(2026, 7, 1, 9, 0, 0)

_PROJECT_A = "A"
_PROJECT_B = "B"


def _row(
    source: str,
    external_id: str,
    title: str = "문서",
    project: str = DEFAULT_PROJECT,
    modified_at: datetime | None = _NOW,
    mime_type: str | None = None,
) -> DocumentMeta:
    """테스트용 메타 행을 만든다."""
    return DocumentMeta(
        project=project,
        source=source,
        external_id=external_id,
        title=title,
        url=f"https://example.test/{source}/{external_id}",
        modified_at=modified_at,
        last_synced_at=_NOW,
        mime_type=mime_type,
    )


@pytest.fixture()
def repo(db_session) -> DocumentMetaRepository:
    """테스트 세션에 붙은 메타 저장소."""
    return DocumentMetaRepository(db_session)


def test_add_and_find(db_session, repo: DocumentMetaRepository) -> None:
    """추가한 행을 (project, source, external_id) 로 다시 찾을 수 있다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "설계서"))
    db_session.commit()

    found = repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")

    assert found is not None
    assert found.title == "설계서"


def test_find_returns_none_when_absent(repo: DocumentMetaRepository) -> None:
    """없는 조합을 조회하면 None 을 돌려준다."""
    assert repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "nope") is None


def test_same_external_id_across_sources_is_allowed(
    db_session, repo: DocumentMetaRepository
) -> None:
    """external_id 가 같아도 source 가 다르면 별개 행으로 공존한다."""
    repo.add(_row(SOURCE_DRIVE, "shared"))
    repo.add(_row(SOURCE_NOTION, "shared"))
    db_session.commit()

    assert repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "shared") is not None
    assert repo.find(DEFAULT_PROJECT, SOURCE_NOTION, "shared") is not None


# --- doc36 Phase3 #12: list_by_document_ids(Chunk.document_id → 메타 역매핑) ----


def _seed_document(session, document_id: str, project: str = DEFAULT_PROJECT) -> None:
    """`Document` 한 건을 저장한다(FK 참조용, 이미 있으면 건드리지 않는다)."""
    if session.get(Document, document_id) is not None:
        return
    session.add(
        Document(
            id=document_id,
            project=project,
            source_url=None,
            title=f"문서 {document_id}",
            content_hash="hash",
            raw_text="본문",
        )
    )
    session.flush()


def test_list_by_document_ids_returns_matching_rows(db_session, repo: DocumentMetaRepository) -> None:
    """document_id 집합에 해당하는 메타 행만 배치로 돌려준다."""
    _seed_document(db_session, "drive:doc-a")
    _seed_document(db_session, "drive:doc-b")
    row_a = _row(SOURCE_DRIVE, "a", title="A 문서")
    row_a.document_id = "drive:doc-a"
    row_b = _row(SOURCE_DRIVE, "b", title="B 문서")
    row_b.document_id = "drive:doc-b"
    repo.add(row_a)
    repo.add(row_b)
    db_session.commit()

    rows = repo.list_by_document_ids(["drive:doc-a"])

    assert [r.title for r in rows] == ["A 문서"]


def test_list_by_document_ids_ignores_unindexed_rows(db_session, repo: DocumentMetaRepository) -> None:
    """document_id 가 NULL(미색인)인 행은 어떤 조회에도 걸리지 않는다."""
    repo.add(_row(SOURCE_DRIVE, "unindexed"))
    db_session.commit()

    assert repo.list_by_document_ids(["drive:doc-a"]) == []


def test_list_by_document_ids_empty_input_returns_empty(repo: DocumentMetaRepository) -> None:
    """빈 입력이면 쿼리 없이 빈 리스트를 반환한다."""
    assert repo.list_by_document_ids([]) == []


def test_same_external_id_across_projects_is_allowed(
    db_session, repo: DocumentMetaRepository
) -> None:
    """external_id 가 같아도 project 가 다르면 별개 행으로 공존한다(새 UNIQUE 제약)."""
    repo.add(_row(SOURCE_DRIVE, "shared", project=_PROJECT_A))
    repo.add(_row(SOURCE_DRIVE, "shared", project=_PROJECT_B))
    db_session.commit()

    assert repo.find(_PROJECT_A, SOURCE_DRIVE, "shared") is not None
    assert repo.find(_PROJECT_B, SOURCE_DRIVE, "shared") is not None


def test_duplicate_project_source_and_external_id_violates_unique(
    db_session, repo: DocumentMetaRepository
) -> None:
    """(project, source, external_id) 중복은 UNIQUE 제약에 걸린다."""
    repo.add(_row(SOURCE_DRIVE, "dup"))
    db_session.commit()
    repo.add(_row(SOURCE_DRIVE, "dup"))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_list_by_project_source_never_includes_other_projects(
    db_session, repo: DocumentMetaRepository
) -> None:
    """list_by_project_source 는 다른 프로젝트의 같은 소스 행을 절대 포함하지 않는다.

    삭제 감지가 이 조회 결과를 기준 집합으로 쓰므로, 여기서 다른 프로젝트
    행이 섞이면 교차 프로젝트 삭제로 직결된다(SPEC 기능 6 핵심 회귀 지점).
    """
    repo.add(_row(SOURCE_DRIVE, "shared", project=_PROJECT_A))
    repo.add(_row(SOURCE_DRIVE, "shared", project=_PROJECT_B))
    db_session.commit()

    found_a = repo.list_by_project_source(_PROJECT_A, SOURCE_DRIVE)
    found_b = repo.list_by_project_source(_PROJECT_B, SOURCE_DRIVE)

    assert [m.project for m in found_a] == [_PROJECT_A]
    assert [m.project for m in found_b] == [_PROJECT_B]


def test_list_by_project_source_is_ordered_by_external_id(
    db_session, repo: DocumentMetaRepository
) -> None:
    """list_by_project_source 는 external_id 오름차순으로 결정적이다."""
    for external_id in ("d3", "d1", "d2"):
        repo.add(_row(SOURCE_DRIVE, external_id, project=_PROJECT_A))
    db_session.commit()

    found = repo.list_by_project_source(_PROJECT_A, SOURCE_DRIVE)

    assert [m.external_id for m in found] == ["d1", "d2", "d3"]


# --- find_latest_by_source_and_external_id (Q1: 포인트 조회) -------------------


def test_find_latest_by_source_and_external_id_returns_row(
    db_session, repo: DocumentMetaRepository
) -> None:
    """(source, external_id) 로 행 한 건을 찾는다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "설계서"))
    db_session.commit()

    found = repo.find_latest_by_source_and_external_id(SOURCE_DRIVE, "d1")

    assert found is not None
    assert found.title == "설계서"


def test_find_latest_by_source_and_external_id_returns_none_when_absent(
    repo: DocumentMetaRepository,
) -> None:
    """일치하는 행이 없으면 None 을 반환한다."""
    assert repo.find_latest_by_source_and_external_id(SOURCE_DRIVE, "nope") is None


def test_find_latest_by_source_and_external_id_ignores_other_source(
    db_session, repo: DocumentMetaRepository
) -> None:
    """source 가 다르면 매칭하지 않는다."""
    repo.add(_row(SOURCE_NOTION, "shared", "노션 문서"))
    db_session.commit()

    assert repo.find_latest_by_source_and_external_id(SOURCE_DRIVE, "shared") is None


def test_find_latest_by_source_and_external_id_picks_most_recent_across_projects(
    db_session, repo: DocumentMetaRepository
) -> None:
    """같은 external_id 가 여러 project 에 공유되면 가장 최근 last_synced_at 행을 고른다."""
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

    found = repo.find_latest_by_source_and_external_id(SOURCE_DRIVE, "shared")

    assert found is not None
    assert found.title == "B 문서"


def test_delete_removes_row(db_session, repo: DocumentMetaRepository) -> None:
    """삭제한 행은 더 이상 조회되지 않는다."""
    repo.add(_row(SOURCE_DRIVE, "d1"))
    db_session.commit()

    repo.delete(repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1"))
    db_session.commit()

    assert repo.find(DEFAULT_PROJECT, SOURCE_DRIVE, "d1") is None


# --- search_by_tokens (1단계 후보 SQL 필터) --------------------------------------


def test_search_by_tokens_matches_title(db_session, repo: DocumentMetaRepository) -> None:
    """제목에 토큰이 포함된 행만 반환한다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "로그인 인증 설계"))
    repo.add(_row(SOURCE_DRIVE, "d2", "배포 가이드"))
    db_session.commit()

    assert [m.external_id for m in repo.search_by_tokens(["로그인"])] == ["d1"]


def test_search_by_tokens_is_case_insensitive(
    db_session, repo: DocumentMetaRepository
) -> None:
    """대소문자를 무시하고 매칭한다(토큰은 소문자로 정규화돼 들어온다)."""
    repo.add(_row(SOURCE_DRIVE, "d1", "OAuth Design"))
    db_session.commit()

    assert [m.external_id for m in repo.search_by_tokens(["oauth"])] == ["d1"]


def test_search_by_tokens_matches_any_token(
    db_session, repo: DocumentMetaRepository
) -> None:
    """토큰 중 하나라도 걸리면 후보에 포함된다(OR 조건)."""
    repo.add(_row(SOURCE_DRIVE, "d1", "로그인 문서"))
    repo.add(_row(SOURCE_DRIVE, "d2", "배포 문서"))
    db_session.commit()

    found = {m.external_id for m in repo.search_by_tokens(["로그인", "배포"])}

    assert found == {"d1", "d2"}


def test_search_by_tokens_respects_source_filter(
    db_session, repo: DocumentMetaRepository
) -> None:
    """source 를 주면 해당 출처의 행만 후보가 된다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "로그인 문서"))
    repo.add(_row(SOURCE_NOTION, "n1", "로그인 문서"))
    db_session.commit()

    found = repo.search_by_tokens(["로그인"], source=SOURCE_NOTION)

    assert [m.external_id for m in found] == ["n1"]


def test_search_by_tokens_respects_project_filter(
    db_session, repo: DocumentMetaRepository
) -> None:
    """project 를 주면 해당 프로젝트의 행만 후보가 된다."""
    repo.add(_row(SOURCE_DRIVE, "a1", "로그인 문서", project=_PROJECT_A))
    repo.add(_row(SOURCE_DRIVE, "b1", "로그인 문서", project=_PROJECT_B))
    db_session.commit()

    found = repo.search_by_tokens(["로그인"], project=_PROJECT_A)

    assert [m.external_id for m in found] == ["a1"]


def test_search_by_tokens_empty_tokens_returns_empty(
    db_session, repo: DocumentMetaRepository
) -> None:
    """토큰이 비면 전체를 긁어오지 않고 빈 결과를 돌려준다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "로그인 문서"))
    db_session.commit()

    assert list(repo.search_by_tokens([])) == []


def test_search_by_tokens_escapes_like_wildcards(
    db_session, repo: DocumentMetaRepository
) -> None:
    """토큰의 `%` 와 `_` 가 LIKE 와일드카드로 해석되지 않는다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "배포 가이드"))
    db_session.commit()

    # 이스케이프가 없으면 '%' 가 "아무 문자열"이 되어 모든 행이 걸린다.
    assert list(repo.search_by_tokens(["%"])) == []
    assert list(repo.search_by_tokens(["_"])) == []


def test_search_by_tokens_matches_underscore_literally(
    db_session, repo: DocumentMetaRepository
) -> None:
    """`auth_v2` 처럼 밑줄이 든 토큰은 문자 그대로 매칭된다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "auth_v2 스펙"))
    repo.add(_row(SOURCE_DRIVE, "d2", "authXv2 스펙"))
    db_session.commit()

    assert [m.external_id for m in repo.search_by_tokens(["auth_v2"])] == ["d1"]


def test_search_by_tokens_query_collapses_whitespace_variant(
    db_session, repo: DocumentMetaRepository
) -> None:
    """공백 없는 질의로 공백 있는 제목을(양방향) 1단계에서 찾는다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "트러블 슈팅 가이드"))
    db_session.commit()

    found = repo.search_by_tokens(["트러블슈팅"], queries=["트러블슈팅"])

    assert [m.external_id for m in found] == ["d1"]


def test_search_by_tokens_query_collapses_whitespace_variant_reverse(
    db_session, repo: DocumentMetaRepository
) -> None:
    """공백 있는 질의로 공백 없는 제목을 1단계에서 찾는다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "트러블슈팅 가이드"))
    db_session.commit()

    tokens = sorted({"트러블", "슈팅"})
    found = repo.search_by_tokens(tokens, queries=["트러블 슈팅"])

    assert [m.external_id for m in found] == ["d1"]


def test_search_by_tokens_without_queries_skips_collapse_match(
    db_session, repo: DocumentMetaRepository
) -> None:
    """queries 인자를 생략하면 기존(토큰 전용) 매칭 동작이 그대로 유지된다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "트러블 슈팅 가이드"))
    db_session.commit()

    assert list(repo.search_by_tokens(["트러블슈팅"])) == []


def test_search_by_tokens_query_variant_contributes_collapse_match(
    db_session, repo: DocumentMetaRepository
) -> None:
    """queries 리스트의 variant 항목도 collapse 매칭에 기여한다.

    원본 질의 토큰은 제목과 전혀 겹치지 않아도, queries 에 함께 넘긴 variant
    가 제목과 공백 유무만 다르면 그 variant 의 collapse 매칭으로 1단계
    후보에 잡혀야 한다. variant 를 빼면(원본 질의만 있으면) 매칭되지 않아야
    한다는 것도 함께 확인한다.
    """
    repo.add(_row(SOURCE_DRIVE, "d1", "트러블슈팅 가이드"))
    db_session.commit()
    tokens = ["이슈", "해결"]

    with_variant = repo.search_by_tokens(tokens, queries=["이슈 해결", "트러블 슈팅"])
    without_variant = repo.search_by_tokens(tokens, queries=["이슈 해결"])

    assert [m.external_id for m in with_variant] == ["d1"]
    assert list(without_variant) == []


def test_search_by_tokens_is_deterministically_ordered(
    db_session, repo: DocumentMetaRepository
) -> None:
    """후보 순서가 (source, external_id) 로 결정적이다."""
    for external_id in ("d3", "d1", "d2"):
        repo.add(_row(SOURCE_DRIVE, external_id, "로그인 문서"))
    db_session.commit()

    assert [m.external_id for m in repo.search_by_tokens(["로그인"])] == ["d1", "d2", "d3"]


# --- search_by_tokens meta_filter (개선 #2: hard filter) --------------------------


def test_search_by_tokens_meta_filter_none_is_noop(
    db_session, repo: DocumentMetaRepository
) -> None:
    """meta_filter 생략 시 기존 동작과 동일하다(회귀 안전)."""
    repo.add(_row(SOURCE_DRIVE, "d1", "로그인 문서"))
    db_session.commit()

    assert [m.external_id for m in repo.search_by_tokens(["로그인"])] == ["d1"]


def test_search_by_tokens_modified_after_is_inclusive(
    db_session, repo: DocumentMetaRepository
) -> None:
    """modified_after 는 경계값을 포함한다(>=)."""
    repo.add(_row(SOURCE_DRIVE, "d1", "로그인 문서", modified_at=_NOW))
    repo.add(
        _row(SOURCE_DRIVE, "d2", "로그인 문서", modified_at=datetime(2026, 6, 30, 9, 0, 0))
    )
    db_session.commit()

    found = repo.search_by_tokens(
        ["로그인"], meta_filter=DocumentMetaFilter(modified_after=_NOW)
    )

    assert [m.external_id for m in found] == ["d1"]


def test_search_by_tokens_modified_before_is_inclusive(
    db_session, repo: DocumentMetaRepository
) -> None:
    """modified_before 는 경계값을 포함한다(<=)."""
    repo.add(_row(SOURCE_DRIVE, "d1", "로그인 문서", modified_at=_NOW))
    repo.add(
        _row(SOURCE_DRIVE, "d2", "로그인 문서", modified_at=datetime(2026, 7, 2, 9, 0, 0))
    )
    db_session.commit()

    found = repo.search_by_tokens(
        ["로그인"], meta_filter=DocumentMetaFilter(modified_before=_NOW)
    )

    assert [m.external_id for m in found] == ["d1"]


def test_search_by_tokens_date_filter_excludes_null_modified_at(
    db_session, repo: DocumentMetaRepository
) -> None:
    """modified_at 이 NULL 인 행은 날짜 필터가 있으면 제외된다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "로그인 문서", modified_at=None))
    db_session.commit()

    found = repo.search_by_tokens(
        ["로그인"], meta_filter=DocumentMetaFilter(modified_after=_NOW)
    )

    assert list(found) == []


def test_search_by_tokens_mime_types_matches_any(
    db_session, repo: DocumentMetaRepository
) -> None:
    """mime_types 는 목록 중 하나라도 일치하면 통과시킨다(OR)."""
    repo.add(_row(SOURCE_DRIVE, "d1", "로그인 문서", mime_type="application/pdf"))
    repo.add(
        _row(
            SOURCE_DRIVE,
            "d2",
            "로그인 문서",
            mime_type="application/vnd.google-apps.document",
        )
    )
    repo.add(_row(SOURCE_DRIVE, "d3", "로그인 문서", mime_type="text/plain"))
    db_session.commit()

    found = repo.search_by_tokens(
        ["로그인"],
        meta_filter=DocumentMetaFilter(
            mime_types=("application/pdf", "application/vnd.google-apps.document")
        ),
    )

    assert {m.external_id for m in found} == {"d1", "d2"}


def test_search_by_tokens_mime_types_excludes_null_mime_type(
    db_session, repo: DocumentMetaRepository
) -> None:
    """mime_type 이 NULL(예: Notion 문서)이면 mime_types 필터에 걸리지 않는다."""
    repo.add(_row(SOURCE_NOTION, "n1", "로그인 문서", mime_type=None))
    db_session.commit()

    found = repo.search_by_tokens(
        ["로그인"], meta_filter=DocumentMetaFilter(mime_types=("application/pdf",))
    )

    assert list(found) == []
