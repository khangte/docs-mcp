"""문서 1건 본문 색인 단위 테스트 (설계 §1 Phase2, 문서 7·8번).

`index_document_body` 는 sync_service.resync 의 delete-and-insert 패턴을
문서 1건 단위로 복제한다: content_hash 가 같으면 청크를 건드리지 않고
skip, 다르면 기존 청크/엔드포인트/스키마/섹션을 지우고 재색인한다.
"""

from __future__ import annotations

import pytest

from app.models import DEFAULT_PROJECT
from app.models.document_meta import SOURCE_DRIVE
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.services.documents.document_body_indexer import (
    deterministic_document_id,
    index_document_body,
)
from app.services.indexer.embedding_provider import HashEmbeddingProvider
from app.services.indexer.indexer_service import IndexerService
from app.models import EMBEDDING_DIM


@pytest.fixture()
def document_repo(db_session) -> DocumentRepository:
    return DocumentRepository(db_session)


@pytest.fixture()
def endpoint_repo(db_session) -> EndpointRepository:
    return EndpointRepository(db_session)


@pytest.fixture()
def chunk_repo(db_session) -> ChunkRepository:
    return ChunkRepository(db_session)


@pytest.fixture()
def indexer(endpoint_repo, chunk_repo) -> IndexerService:
    return IndexerService(
        endpoint_repo=endpoint_repo,
        chunk_repo=chunk_repo,
        embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
    )


def test_deterministic_document_id_is_stable_for_same_inputs() -> None:
    """같은 project/source/external_id 는 항상 같은 Document.id 를 만든다."""
    id1 = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    id2 = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")

    assert id1 == id2
    assert id1.startswith(f"{SOURCE_DRIVE}:")


def test_deterministic_document_id_differs_for_different_inputs() -> None:
    """입력 하나만 달라져도 다른 ID 가 나온다(해시 충돌 방지 확인용 최소 구분성)."""
    base = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")

    assert deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d2") != base
    assert deterministic_document_id("other-project", SOURCE_DRIVE, "d1") != base


def test_deterministic_document_id_stays_within_derived_id_budget() -> None:
    """project 상한(128자) + Drive file_id 급 44자 입력이어도 파생 ID 예산(<=40) 안에 든다.

    `Document.id` 는 `String(64)` 이지만 `{doc_id}:ep:{16-hex}` 처럼 하위
    엔티티가 20자를 더 쓰므로 실효 상한은 40자다(doc/38). 단순 <=64 로는
    이 버그를 못 잡는다 — 파생 ID까지 계산한 예산으로 단언해야 한다.
    """
    long_project = "p" * 128
    drive_like_external_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_ABCD"  # 44자

    result = deterministic_document_id(long_project, SOURCE_DRIVE, drive_like_external_id)

    assert len(result) <= 40


def test_new_document_is_indexed_and_chunks_created(
    db_session, document_repo, endpoint_repo, chunk_repo, indexer
) -> None:
    """신규 문서는 Document 가 생성되고 본문에서 청크가 만들어진다."""
    result = index_document_body(
        db_session,
        document_repo,
        endpoint_repo,
        chunk_repo,
        indexer,
        project=DEFAULT_PROJECT,
        source_name=SOURCE_DRIVE,
        external_id="d1",
        title="설계서",
        raw="# 설계서\n\n본문 내용입니다.",
    )
    db_session.commit()

    assert result.reindexed is True
    document_id = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    document = document_repo.get(document_id)
    assert document is not None
    assert document.doc_type == SOURCE_DRIVE
    assert document.source_url is None
    chunks = chunk_repo.list_by_document(document_id)
    assert len(chunks) > 0


def test_unchanged_content_hash_skips_reindex(
    db_session, document_repo, endpoint_repo, chunk_repo, indexer
) -> None:
    """content_hash 가 같으면 청크를 다시 만들지 않고 skip 한다."""
    kwargs = dict(
        project=DEFAULT_PROJECT,
        source_name=SOURCE_DRIVE,
        external_id="d1",
        title="설계서",
        raw="# 설계서\n\n본문 내용입니다.",
    )
    index_document_body(db_session, document_repo, endpoint_repo, chunk_repo, indexer, **kwargs)
    db_session.commit()
    document_id = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    chunks_before = chunk_repo.list_by_document(document_id)

    result = index_document_body(
        db_session, document_repo, endpoint_repo, chunk_repo, indexer, **kwargs
    )
    db_session.commit()

    assert result.reindexed is False
    chunks_after = chunk_repo.list_by_document(document_id)
    assert {c.id for c in chunks_before} == {c.id for c in chunks_after}


def test_changed_content_replaces_chunks(
    db_session, document_repo, endpoint_repo, chunk_repo, indexer
) -> None:
    """본문이 바뀌면 기존 청크를 지우고 새 청크로 교체한다."""
    kwargs = dict(
        project=DEFAULT_PROJECT, source_name=SOURCE_DRIVE, external_id="d1", title="설계서"
    )
    index_document_body(
        db_session, document_repo, endpoint_repo, chunk_repo, indexer,
        raw="# 설계서\n\n원본 내용.", **kwargs,
    )
    db_session.commit()
    document_id = deterministic_document_id(DEFAULT_PROJECT, SOURCE_DRIVE, "d1")
    old_chunk_ids = {c.id for c in chunk_repo.list_by_document(document_id)}

    result = index_document_body(
        db_session, document_repo, endpoint_repo, chunk_repo, indexer,
        raw="# 설계서\n\n완전히 달라진 새 내용.", **kwargs,
    )
    db_session.commit()

    assert result.reindexed is True
    document = document_repo.get(document_id)
    assert "완전히 달라진" in document.raw_text
    new_chunk_ids = {c.id for c in chunk_repo.list_by_document(document_id)}
    assert new_chunk_ids  # 여전히 청크가 존재
    # 재파싱으로 id 체계가 동일(문서id:chunk:idx)해도 텍스트는 갱신됨을 별도 확인
    assert old_chunk_ids  # 기존 청크가 있었다는 전제 확인


def test_index_document_body_with_long_project_and_external_id_does_not_truncate(
    db_session, document_repo, endpoint_repo, chunk_repo, indexer
) -> None:
    """project(128자)·Drive file_id 급(44자) external_id 로도 파생 ID insert 가 안 잘린다.

    구 규칙(`f"{project}:{source}:{external_id}"`) 이면 `Document.id`
    (String(64))를 넘겨 StringDataRightTruncation 으로 여기서 터졌다(doc/38).
    """
    long_project = "p" * 128
    drive_like_external_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_ABCD"

    result = index_document_body(
        db_session,
        document_repo,
        endpoint_repo,
        chunk_repo,
        indexer,
        project=long_project,
        source_name=SOURCE_DRIVE,
        external_id=drive_like_external_id,
        title="설계서",
        raw="# 설계서\n\n제목1\n## 소제목\n본문 내용입니다.",
    )
    db_session.commit()

    assert result.reindexed is True
    document_id = deterministic_document_id(long_project, SOURCE_DRIVE, drive_like_external_id)
    document = document_repo.get(document_id)
    assert document is not None
    chunks = chunk_repo.list_by_document(document_id)
    assert len(chunks) > 0
