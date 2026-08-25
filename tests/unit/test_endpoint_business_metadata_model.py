"""EndpointBusinessMetadata ORM 모델 테스트.

docs/architect-review/52,54: api_endpoint에 FK를 걸지 않고
(document_id, method, path)로 재색인 후에도 값이 살아남는지, JSON 컬럼
property가 tags_json 패턴과 동일하게 동작하는지 확인한다.
"""

from __future__ import annotations

from app.models import DEFAULT_PROJECT, Document, EndpointBusinessMetadata
from app.models.base import _utcnow


def _make_document(session, doc_id: str = "doc-1") -> Document:
    document = Document(
        id=doc_id,
        project=DEFAULT_PROJECT,
        title="t",
        version="unknown",
        content_hash="h",
        raw_text="{}",
        indexed_at=_utcnow(),
    )
    session.add(document)
    session.commit()
    return document


def test_keywords_and_user_phrases_json_roundtrip(db_session) -> None:
    _make_document(db_session)
    metadata = EndpointBusinessMetadata(document_id="doc-1", method="GET", path="/pets")
    metadata.keywords = ["pet", "adopt"]
    metadata.user_phrases = ["find a pet"]
    db_session.add(metadata)
    db_session.commit()

    fetched = db_session.get(EndpointBusinessMetadata, metadata.id)
    assert fetched is not None
    assert fetched.keywords == ["pet", "adopt"]
    assert fetched.user_phrases == ["find a pet"]


def test_defaults_when_not_set(db_session) -> None:
    _make_document(db_session)
    metadata = EndpointBusinessMetadata(document_id="doc-1", method="GET", path="/pets")
    db_session.add(metadata)
    db_session.commit()

    fetched = db_session.get(EndpointBusinessMetadata, metadata.id)
    assert fetched is not None
    assert fetched.business_description == ""
    assert fetched.keywords == []
    assert fetched.user_phrases == []
    assert fetched.generated_at is None
    assert fetched.model is None


def test_survives_endpoint_deletion_keyed_by_document_method_path(db_session) -> None:
    """api_endpoint 행이 지워져도(재색인 시뮬레이션) 이 테이블은 FK가 없어 살아남는다."""
    from app.models import ApiEndpoint

    _make_document(db_session)
    endpoint = ApiEndpoint(
        id="doc-1:ep:1", document_id="doc-1", method="GET", path="/pets", summary="", description=""
    )
    db_session.add(endpoint)
    metadata = EndpointBusinessMetadata(document_id="doc-1", method="GET", path="/pets")
    metadata.business_description = "설명"
    db_session.add(metadata)
    db_session.commit()

    db_session.delete(endpoint)
    db_session.commit()

    fetched = db_session.get(EndpointBusinessMetadata, metadata.id)
    assert fetched is not None
    assert fetched.business_description == "설명"
