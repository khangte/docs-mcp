"""EndpointProjectionRepository 단위 테스트 (`docs/architect-review/101` §2.1)."""

from __future__ import annotations

from app.models import ApiEndpoint, Document, EndpointSearchProjection
from app.repositories.endpoint_projection_repository import EndpointProjectionRepository


def _seed_document(session, doc_id: str, project: str = "default") -> None:
    """`Document` 한 건을 저장한다."""
    session.add(
        Document(
            id=doc_id,
            project=project,
            source_url=None,
            title=f"문서 {doc_id}",
            content_hash="hash",
            raw_text="{}",
            doc_type="openapi",
        )
    )
    session.flush()


def _seed_endpoint(
    session, endpoint_id: str, document_id: str, method: str, path: str
) -> None:
    """`ApiEndpoint` 한 건을 저장한다."""
    session.add(
        ApiEndpoint(
            id=endpoint_id,
            document_id=document_id,
            method=method,
            path=path,
            operation_id=None,
            summary="",
            description="",
        )
    )
    session.flush()


def _upsert(repo: EndpointProjectionRepository, **kw: object) -> EndpointSearchProjection:
    """기본값을 채운 upsert 헬퍼."""
    params: dict[str, object] = dict(
        id="p1",
        endpoint_id="e1",
        document_id="doc1",
        method="GET",
        path="/pets",
        canonical_text="MethodPath: GET /pets",
        embedding=None,
        representation_version="v1",
        source_hash="h1",
    )
    params.update(kw)
    return repo.upsert(**params)  # type: ignore[arg-type]


def test_upsert_inserts_then_updates_in_place(db_session) -> None:
    """같은 `(document_id, method, path)` 두 번 upsert 하면 행이 하나로 유지되고 갱신된다."""
    _seed_document(db_session, "doc1")
    _seed_endpoint(db_session, "e1", "doc1", "GET", "/pets")
    repo = EndpointProjectionRepository(db_session)

    _upsert(repo, canonical_text="old", source_hash="hash-old")
    _upsert(repo, canonical_text="new", source_hash="hash-new", embedding=[0.1] * 384)
    db_session.commit()

    rows = repo.list_by_document("doc1")
    assert len(rows) == 1
    assert rows[0].canonical_text == "new"
    assert rows[0].source_hash == "hash-new"
    assert rows[0].embedding is not None


def test_get_by_endpoint_and_document_scope(db_session) -> None:
    """endpoint id 조회와 document/project 스코프 count 가 각각 맞다."""
    _seed_document(db_session, "doc1", project="alpha")
    _seed_document(db_session, "doc2", project="beta")
    _seed_endpoint(db_session, "e1", "doc1", "GET", "/pets")
    _seed_endpoint(db_session, "e2", "doc2", "GET", "/orders")
    repo = EndpointProjectionRepository(db_session)
    _upsert(repo, id="p1", endpoint_id="e1", document_id="doc1", path="/pets")
    _upsert(repo, id="p2", endpoint_id="e2", document_id="doc2", path="/orders")
    db_session.commit()

    assert repo.get_by_endpoint("e1").document_id == "doc1"
    assert repo.get_by_endpoint("missing") is None
    assert repo.count() == 2
    assert repo.count(document_id="doc1") == 1
    assert repo.count(project="alpha") == 1
    assert repo.count(project="beta") == 1


def test_list_audit_rows_carries_version_and_hash(db_session) -> None:
    """감사 조회는 신원 + version/hash 를 정렬된 순서로 낸다(§6 source coverage)."""
    _seed_document(db_session, "doc1")
    _seed_endpoint(db_session, "e1", "doc1", "POST", "/pets")
    _seed_endpoint(db_session, "e2", "doc1", "GET", "/pets")
    repo = EndpointProjectionRepository(db_session)
    _upsert(repo, id="p1", endpoint_id="e1", method="POST", source_hash="h-post")
    _upsert(repo, id="p2", endpoint_id="e2", method="GET", source_hash="h-get")
    db_session.commit()

    rows = repo.list_audit_rows(document_id="doc1")
    assert [(r.method, r.source_hash) for r in rows] == [
        ("GET", "h-get"),
        ("POST", "h-post"),
    ]
    assert all(r.representation_version == "v1" for r in rows)


def test_delete_by_document(db_session) -> None:
    """document 단위 명시 삭제."""
    _seed_document(db_session, "doc1")
    _seed_endpoint(db_session, "e1", "doc1", "GET", "/pets")
    repo = EndpointProjectionRepository(db_session)
    _upsert(repo)
    db_session.commit()

    assert repo.delete_by_document("doc1") == 1
    db_session.commit()
    assert repo.count(document_id="doc1") == 0


def test_endpoint_delete_cascades_to_projection(db_session) -> None:
    """endpoint row 를 지우면 FK CASCADE 로 projection 도 사라진다(재색인 계약)."""
    _seed_document(db_session, "doc1")
    _seed_endpoint(db_session, "e1", "doc1", "GET", "/pets")
    repo = EndpointProjectionRepository(db_session)
    _upsert(repo)
    db_session.commit()

    db_session.delete(db_session.get(ApiEndpoint, "e1"))
    db_session.commit()
    assert repo.count() == 0
