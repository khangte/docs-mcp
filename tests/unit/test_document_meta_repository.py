"""DocumentMetaRepository 단위 테스트.

UNIQUE(source, external_id) 제약과 출처별 조회 동작을 검증한다.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION, DocumentMeta
from app.repositories.document_meta_repository import DocumentMetaRepository

_NOW = datetime(2026, 7, 1, 9, 0, 0)


def _row(source: str, external_id: str, title: str = "문서") -> DocumentMeta:
    """테스트용 메타 행을 만든다."""
    return DocumentMeta(
        source=source,
        external_id=external_id,
        title=title,
        url=f"https://example.test/{source}/{external_id}",
        modified_at=_NOW,
        last_synced_at=_NOW,
    )


@pytest.fixture()
def repo(db_session) -> DocumentMetaRepository:
    """테스트 세션에 붙은 메타 저장소."""
    return DocumentMetaRepository(db_session)


def test_add_and_find(db_session, repo: DocumentMetaRepository) -> None:
    """추가한 행을 (source, external_id) 로 다시 찾을 수 있다."""
    repo.add(_row(SOURCE_DRIVE, "d1", "설계서"))
    db_session.commit()

    found = repo.find(SOURCE_DRIVE, "d1")

    assert found is not None
    assert found.title == "설계서"


def test_find_returns_none_when_absent(repo: DocumentMetaRepository) -> None:
    """없는 조합을 조회하면 None 을 돌려준다."""
    assert repo.find(SOURCE_DRIVE, "nope") is None


def test_same_external_id_across_sources_is_allowed(
    db_session, repo: DocumentMetaRepository
) -> None:
    """external_id 가 같아도 source 가 다르면 별개 행으로 공존한다."""
    repo.add(_row(SOURCE_DRIVE, "shared"))
    repo.add(_row(SOURCE_NOTION, "shared"))
    db_session.commit()

    assert repo.find(SOURCE_DRIVE, "shared") is not None
    assert repo.find(SOURCE_NOTION, "shared") is not None


def test_duplicate_source_and_external_id_violates_unique(
    db_session, repo: DocumentMetaRepository
) -> None:
    """(source, external_id) 중복은 UNIQUE 제약에 걸린다."""
    repo.add(_row(SOURCE_DRIVE, "dup"))
    db_session.commit()
    repo.add(_row(SOURCE_DRIVE, "dup"))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_list_by_source_filters(db_session, repo: DocumentMetaRepository) -> None:
    """list_by_source 는 지정한 출처의 행만 돌려준다."""
    repo.add(_row(SOURCE_DRIVE, "d1"))
    repo.add(_row(SOURCE_NOTION, "n1"))
    db_session.commit()

    assert [m.external_id for m in repo.list_by_source(SOURCE_DRIVE)] == ["d1"]


def test_list_all_without_filter_returns_every_row(
    db_session, repo: DocumentMetaRepository
) -> None:
    """source 를 생략하면 전체 행을 돌려준다."""
    repo.add(_row(SOURCE_DRIVE, "d1"))
    repo.add(_row(SOURCE_NOTION, "n1"))
    db_session.commit()

    assert len(repo.list_all()) == 2


def test_list_all_is_deterministically_ordered(
    db_session, repo: DocumentMetaRepository
) -> None:
    """list_all 은 (source, external_id) 순으로 결정적으로 정렬된다."""
    for external_id in ("d3", "d1", "d2"):
        repo.add(_row(SOURCE_DRIVE, external_id))
    db_session.commit()

    assert [m.external_id for m in repo.list_all()] == ["d1", "d2", "d3"]


def test_delete_removes_row(db_session, repo: DocumentMetaRepository) -> None:
    """삭제한 행은 더 이상 조회되지 않는다."""
    repo.add(_row(SOURCE_DRIVE, "d1"))
    db_session.commit()

    repo.delete(repo.find(SOURCE_DRIVE, "d1"))
    db_session.commit()

    assert repo.find(SOURCE_DRIVE, "d1") is None


def test_empty_repository_returns_empty_lists(repo: DocumentMetaRepository) -> None:
    """행이 없으면 빈 시퀀스를 돌려준다."""
    assert list(repo.list_all()) == []
    assert list(repo.list_by_source(SOURCE_NOTION)) == []


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


def test_search_by_tokens_is_deterministically_ordered(
    db_session, repo: DocumentMetaRepository
) -> None:
    """후보 순서가 (source, external_id) 로 결정적이다."""
    for external_id in ("d3", "d1", "d2"):
        repo.add(_row(SOURCE_DRIVE, external_id, "로그인 문서"))
    db_session.commit()

    assert [m.external_id for m in repo.search_by_tokens(["로그인"])] == ["d1", "d2", "d3"]
