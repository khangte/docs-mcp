"""endpoint projection 색인/재색인/write-back/삭제/백필 lifecycle (`docs/architect-review/101` §5).

색인 경로와 백필 경로가 **같은** canonical text/hash 를 내는지(§6 결정성),
flag 와 무관하게 색인 시 projection 이 항상 만들어지는지(§5.1), 임베딩 실패가
문서 트랜잭션을 통째로 되감는지(원자성)를 본다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import (
    EMBEDDING_DIM,
    ApiEndpoint,
    Chunk,
    Document,
    EndpointSearchProjection,
)
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_projection_repository import EndpointProjectionRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.scripts.backfill_endpoint_projection import (
    audit_endpoint_projection,
    backfill_endpoint_projection,
)
from app.services.indexer.embedding_provider import HashEmbeddingProvider
from app.services.indexer.endpoint_chunk_refresher import refresh_endpoint_chunk
from app.services.indexer.endpoint_projection import build_endpoint_projection
from app.services.indexer.indexer_service import IndexerService
from app.services.metadata.writeback_service import MetadataWritebackService
from app.services.parser.document_router import parse_document

_DOC_ID = "doc-pj"


class _SemanticHashProvider:
    """`HashEmbeddingProvider` 를 감싸 `is_semantic` 만 True 로 바꾼 페이크."""

    def __init__(self, semantic: bool = True) -> None:
        self._delegate = HashEmbeddingProvider(dim=EMBEDDING_DIM)
        self._semantic = semantic
        self.doc_label_calls: list[list[str]] = []

    @property
    def dim(self) -> int:
        return self._delegate.dim

    @property
    def is_semantic(self) -> bool:
        return self._semantic

    def embed_documents(
        self, texts: list[str], labels: list[str] | None = None
    ) -> list[list[float]]:
        self.doc_label_calls.append(list(labels or []))
        return self._delegate.embed_documents(texts, labels=labels)

    def embed_query(self, text: str) -> list[float]:
        return self._delegate.embed_query(text)


class _FailProjectionEmbedProvider(_SemanticHashProvider):
    """projection 텍스트 임베딩 시점에만 터지는 페이크(청크 임베딩은 통과)."""

    def embed_documents(
        self, texts: list[str], labels: list[str] | None = None
    ) -> list[list[float]]:
        if labels and any(":projection:" in label for label in labels):
            raise RuntimeError("projection 임베딩 실패 시뮬레이션")
        return super().embed_documents(texts, labels=labels)


def _projection_embed_calls(provider: _SemanticHashProvider) -> list[list[str]]:
    """projection 라벨이 실린 embed_documents 호출만 추린다."""
    return [
        labels
        for labels in provider.doc_label_calls
        if any(":projection:" in label for label in labels)
    ]


def _index(session, raw: str, provider, doc_id: str = _DOC_ID):
    """샘플 문서를 projection_repo 를 단 IndexerService 로 색인하고 커밋한다."""
    document = Document(
        id=doc_id,
        project="default",
        source_url=None,
        title="t",
        version="1",
        doc_type="openapi",
        content_hash="h",
        raw_text=raw,
    )
    session.add(document)
    session.flush()
    endpoint_repo = EndpointRepository(session)
    chunk_repo = ChunkRepository(session)
    projection_repo = EndpointProjectionRepository(session)
    IndexerService(
        endpoint_repo=endpoint_repo,
        chunk_repo=chunk_repo,
        embedding_provider=provider,
        projection_repo=projection_repo,
    ).index_document(document=document, parsed=parse_document(raw, "openapi"))
    session.commit()
    return document, endpoint_repo, chunk_repo, projection_repo


def _expected_hashes(raw: str) -> dict[tuple[str, str], str]:
    """`(method, path) -> source_hash` (빌더가 직접 낸 기대값)."""
    parsed = parse_document(raw, "openapi")
    return {
        (ep.method, ep.path): build_endpoint_projection(ep).source_hash
        for ep in parsed.endpoints
    }


def test_initial_index_builds_one_projection_per_endpoint(
    db_session, sample_openapi_3
) -> None:
    """색인하면 endpoint 마다 projection 1행 — version v1, 빌더와 같은 hash."""
    _, endpoint_repo, _, projection_repo = _index(
        db_session, sample_openapi_3, HashEmbeddingProvider(dim=EMBEDDING_DIM)
    )
    endpoints = endpoint_repo.list_by_document(_DOC_ID)
    rows = projection_repo.list_by_document(_DOC_ID)
    assert len(rows) == len(endpoints)

    expected = _expected_hashes(sample_openapi_3)
    for row in rows:
        assert row.representation_version == "v1"
        assert row.embedding is None  # 비의미 프로바이더 → dense vector 없음(§5.2)
        assert row.source_hash == expected[(row.method, row.path)]
        assert projection_repo.get_by_endpoint(row.endpoint_id) is row


def test_semantic_provider_writes_projection_embedding(
    db_session, sample_openapi_3
) -> None:
    """의미 프로바이더면 projection 에도 dense vector 를 채운다."""
    _, _, _, projection_repo = _index(
        db_session, sample_openapi_3, _SemanticHashProvider(semantic=True)
    )
    rows = projection_repo.list_by_document(_DOC_ID)
    assert rows
    for row in rows:
        assert row.embedding is not None
        assert len(list(row.embedding)) == EMBEDDING_DIM


def test_full_reindex_replaces_projections_with_stable_ids(
    services_factory, sample_openapi_3
) -> None:
    """재색인은 projection 을 지웠다 다시 만든다 — id 는 (doc, method, path) 수렴, orphan 없음."""
    import json

    services = services_factory()
    reg = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    doc_id = reg.document.id
    repo = EndpointProjectionRepository(services.session)
    before = {r.id: r.source_hash for r in repo.list_by_document(doc_id)}
    assert before

    modified = json.loads(sample_openapi_3)
    modified["info"]["version"] = "2.0.0"
    services.sync_service.resync(doc_id, raw_override=json.dumps(modified))

    after_rows = repo.list_by_document(doc_id)
    after = {r.id: r.source_hash for r in after_rows}
    assert set(after) == set(before)  # 같은 id 로 수렴
    assert after == before  # 원문 endpoint 부분 불변 → hash 불변
    live_endpoint_ids = {
        e.id for e in services.endpoint_repo.list_by_document(doc_id)
    }
    assert all(r.endpoint_id in live_endpoint_ids for r in after_rows)  # orphan 없음


def test_delete_document_cascades_to_projections(
    services_factory, sample_openapi_3
) -> None:
    """문서를 지우면 FK CASCADE 로 projection 도 사라진다."""
    services = services_factory()
    reg = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    doc_id = reg.document.id
    repo = EndpointProjectionRepository(services.session)
    assert repo.count(document_id=doc_id) > 0

    services.sync_service.delete(doc_id)
    assert repo.count(document_id=doc_id) == 0


def test_projection_embedding_failure_rolls_back_whole_document(
    app_state, services_factory, session_factory, sample_openapi_3
) -> None:
    """projection 임베딩이 실패하면 문서/엔드포인트/청크/projection 이 하나도 안 남는다(원자성)."""
    app_state.embedding_provider = _FailProjectionEmbedProvider(semantic=True)
    services = services_factory()

    with pytest.raises(RuntimeError):
        services.sync_service.register(
            project="default", source_url=None, raw_document=sample_openapi_3
        )

    with session_factory() as check:
        assert check.scalars(select(Document)).all() == []
        assert check.scalars(select(ApiEndpoint)).all() == []
        assert check.scalars(select(Chunk)).all() == []
        assert check.scalars(select(EndpointSearchProjection)).all() == []


def test_writeback_refresh_self_heals_missing_projection(
    db_session, sample_openapi_3
) -> None:
    """write-back 경로에 projection_repo 를 주면 빠진 행을 같은 트랜잭션에서 되살린다."""
    provider = HashEmbeddingProvider(dim=EMBEDDING_DIM)
    _, endpoint_repo, chunk_repo, projection_repo = _index(
        db_session, sample_openapi_3, provider
    )
    endpoint = endpoint_repo.list_by_document(_DOC_ID)[0]

    db_session.delete(projection_repo.get_by_endpoint(endpoint.id))
    db_session.commit()
    assert projection_repo.get_by_endpoint(endpoint.id) is None

    service = MetadataWritebackService(
        session=db_session,
        endpoint_repo=endpoint_repo,
        document_repo=DocumentRepository(db_session),
        chunk_repo=chunk_repo,
        embedding_provider=provider,
        enabled=True,
        projection_repo=projection_repo,
    )
    result = service.submit(endpoint.id, "리소스를 만든다", ["thing"], ["생성"])
    assert result.status == "stored"

    healed = projection_repo.get_by_endpoint(endpoint.id)
    assert healed is not None
    target = next(
        ep
        for ep in parse_document(sample_openapi_3, "openapi").endpoints
        if ep.method == endpoint.method and ep.path == endpoint.path
    )
    assert healed.source_hash == build_endpoint_projection(target).source_hash


def test_writeback_refresh_is_hash_guarded(db_session, sample_openapi_3) -> None:
    """projection 행이 최신이면 refresh 는 projection 임베딩을 다시 부르지 않는다."""
    provider = _SemanticHashProvider(semantic=True)
    document, endpoint_repo, chunk_repo, projection_repo = _index(
        db_session, sample_openapi_3, provider
    )
    endpoint = endpoint_repo.list_by_document(_DOC_ID)[0]
    provider.doc_label_calls.clear()

    # 행이 이미 최신 → projection 임베딩 호출 0
    refresh_endpoint_chunk(
        document=document,
        endpoint=endpoint,
        metadata=None,
        chunk_repo=chunk_repo,
        embedding_provider=provider,
        projection_repo=projection_repo,
    )
    assert _projection_embed_calls(provider) == []

    # 저장된 hash 가 낡으면 다시 self-heal 한다
    stale = projection_repo.get_by_endpoint(endpoint.id)
    stale.source_hash = "stale-hash"
    db_session.commit()
    provider.doc_label_calls.clear()
    refresh_endpoint_chunk(
        document=document,
        endpoint=endpoint,
        metadata=None,
        chunk_repo=chunk_repo,
        embedding_provider=provider,
        projection_repo=projection_repo,
    )
    assert len(_projection_embed_calls(provider)) == 1
    assert projection_repo.get_by_endpoint(endpoint.id).source_hash != "stale-hash"


def test_backfill_matches_index_path_and_is_idempotent(
    services_factory, session_factory, sample_openapi_3
) -> None:
    """백필이 만드는 hash 집계가 색인 경로와 같고(§6), 두 번 돌려도 안 바뀐다."""
    services = services_factory()
    reg = services.sync_service.register(
        project="default", source_url=None, raw_document=sample_openapi_3
    )
    doc_id = reg.document.id
    index_digest = audit_endpoint_projection(session_factory, document_id=doc_id).digest

    # 행을 전부 지우고 백필로 복구
    with session_factory() as wipe:
        assert EndpointProjectionRepository(wipe).delete_by_document(doc_id) > 0
        wipe.commit()

    provider = HashEmbeddingProvider(dim=EMBEDDING_DIM)
    assert backfill_endpoint_projection(
        session_factory, provider, document_id=doc_id
    ) == reg.endpoints_count
    first = audit_endpoint_projection(session_factory, document_id=doc_id)
    assert first.count == reg.endpoints_count
    assert first.digest == index_digest  # 색인 경로와 byte 단위로 같은 projection

    backfill_endpoint_projection(session_factory, provider, document_id=doc_id)
    again = audit_endpoint_projection(session_factory, document_id=doc_id)
    assert again.digest == first.digest
