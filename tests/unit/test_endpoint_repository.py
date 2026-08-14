"""EndpointRepository 단위 테스트."""

from __future__ import annotations

from app.models import ApiEndpoint, Document
from app.repositories.endpoint_repository import EndpointRepository


def _seed_endpoint(
    session,
    endpoint_id: str,
    document_id: str = "doc-1",
    method: str = "GET",
    path: str | None = None,
    tags: list[str] | None = None,
    project: str = "default",
    operation_id: str | None = None,
) -> None:
    """엔드포인트 한 건을 저장한다(문서가 없으면 함께 만든다)."""
    if session.get(Document, document_id) is None:
        session.add(
            Document(
                id=document_id,
                project=project,
                source_url=None,
                title="샘플 문서",
                content_hash="hash",
                raw_text="{}",
            )
        )
        session.flush()
    endpoint = ApiEndpoint(
        id=endpoint_id,
        document_id=document_id,
        method=method,
        path=path or f"/{endpoint_id}",
        operation_id=operation_id,
        summary=f"{endpoint_id} 조회",
    )
    if tags is not None:
        endpoint.tags = tags
    session.add(endpoint)


# --- Q3: get_many(ids) 배치 조회 ---------------------------------------------


def test_get_many_returns_mapping_for_existing_ids(db_session) -> None:
    """존재하는 ID 들을 id → ApiEndpoint 매핑으로 반환한다."""
    _seed_endpoint(db_session, "ep-1")
    _seed_endpoint(db_session, "ep-2")
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.get_many(["ep-1", "ep-2"])

    assert set(result.keys()) == {"ep-1", "ep-2"}
    assert result["ep-1"].path == "/ep-1"
    assert result["ep-2"].path == "/ep-2"


def test_get_many_omits_missing_ids(db_session) -> None:
    """존재하지 않는 ID 는 반환 매핑에서 빠진다(예외 없음)."""
    _seed_endpoint(db_session, "ep-1")
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.get_many(["ep-1", "missing-id"])

    assert set(result.keys()) == {"ep-1"}


def test_get_many_empty_input_returns_empty_mapping(db_session) -> None:
    """빈 ID 목록을 넘기면 쿼리 없이 빈 매핑을 반환한다."""
    repo = EndpointRepository(db_session)

    assert repo.get_many([]) == {}


# --- list_related: 순회 힌트(태그/경로 접두사 OR, SQL 필터+LIMIT) --------------


def test_list_related_matches_by_shared_tag(db_session) -> None:
    """태그를 공유하면 경로 접두사가 달라도 후보에 든다."""
    _seed_endpoint(db_session, "self", path="/pet/{id}", tags=["pet"])
    _seed_endpoint(db_session, "sib", path="/store/order", tags=["pet"])
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_related(
        document_id="doc-1",
        exclude_endpoint_id="self",
        tags=["pet"],
        path_prefix="/pet",
        limit=10,
    )

    assert [e.id for e in result] == ["sib"]


def test_list_related_matches_by_path_prefix(db_session) -> None:
    """태그가 달라도 경로 접두사(첫 세그먼트)를 공유하면 후보에 든다."""
    _seed_endpoint(db_session, "self", path="/pet/{id}", tags=["pet"])
    _seed_endpoint(db_session, "sib", path="/pet/food", tags=["nutrition"])
    _seed_endpoint(db_session, "unrelated", path="/petstore", tags=["other"])
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_related(
        document_id="doc-1",
        exclude_endpoint_id="self",
        tags=["pet"],
        path_prefix="/pet",
        limit=10,
    )

    # "/petstore" 는 첫 세그먼트가 "petstore" 라 "/pet" 접두사와 다르다.
    assert [e.id for e in result] == ["sib"]


def test_list_related_excludes_self(db_session) -> None:
    """exclude_endpoint_id 로 지정한 자기 자신은 결과에서 빠진다."""
    _seed_endpoint(db_session, "self", path="/pet/{id}", tags=["pet"])
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_related(
        document_id="doc-1",
        exclude_endpoint_id="self",
        tags=["pet"],
        path_prefix="/pet",
        limit=10,
    )

    assert result == []


def test_list_related_scopes_to_document(db_session) -> None:
    """다른 문서의 엔드포인트는 태그/경로가 겹쳐도 후보에서 빠진다."""
    _seed_endpoint(db_session, "self", document_id="doc-1", path="/pet/{id}", tags=["pet"])
    _seed_endpoint(db_session, "other-doc", document_id="doc-2", path="/pet/food", tags=["pet"])
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_related(
        document_id="doc-1",
        exclude_endpoint_id="self",
        tags=["pet"],
        path_prefix="/pet",
        limit=10,
    )

    assert result == []


def test_list_related_respects_limit(db_session) -> None:
    """limit 를 초과하는 결과는 반환하지 않는다."""
    _seed_endpoint(db_session, "self", path="/pet/{id}", tags=["pet"])
    for i in range(5):
        _seed_endpoint(db_session, f"sib-{i}", path=f"/pet/{i}", tags=["pet"])
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_related(
        document_id="doc-1",
        exclude_endpoint_id="self",
        tags=["pet"],
        path_prefix="/pet",
        limit=2,
    )

    assert len(result) == 2


def test_list_related_no_tags_or_prefix_returns_empty_without_query(db_session) -> None:
    """태그도 경로 접두사도 없으면 쿼리 없이 빈 결과를 반환한다."""
    _seed_endpoint(db_session, "self", path="/", tags=[])
    _seed_endpoint(db_session, "other", path="/pet", tags=["pet"])
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_related(
        document_id="doc-1",
        exclude_endpoint_id="self",
        tags=[],
        path_prefix="",
        limit=10,
    )

    assert result == []


# --- 5b: method+path / operationId 정확일치 조회 ------------------------------


def test_list_by_method_path_returns_exact_match(db_session) -> None:
    """method+path 가 정확히 일치하는 엔드포인트를 반환한다."""
    _seed_endpoint(db_session, "ep-1", method="GET", path="/pet/{petId}")
    _seed_endpoint(db_session, "ep-2", method="POST", path="/pet")
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_by_method_path("GET", "/pet/{petId}")

    assert [e.id for e in result] == ["ep-1"]


def test_list_by_method_path_is_case_insensitive_on_method(db_session) -> None:
    """method 는 대소문자 무관하게 매칭된다(내부적으로 upper 정규화)."""
    _seed_endpoint(db_session, "ep-1", method="GET", path="/pet/{petId}")
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_by_method_path("get", "/pet/{petId}")

    assert [e.id for e in result] == ["ep-1"]


def test_list_by_method_path_no_match_returns_empty(db_session) -> None:
    """일치하는 게 없으면 빈 시퀀스를 반환한다."""
    _seed_endpoint(db_session, "ep-1", method="GET", path="/pet/{petId}")
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_by_method_path("GET", "/nope")

    assert list(result) == []


def test_list_by_method_path_scopes_to_document(db_session) -> None:
    """document_id 를 주면 다른 문서의 동일 method+path는 제외된다."""
    _seed_endpoint(db_session, "ep-1", document_id="doc-1", method="GET", path="/pet")
    _seed_endpoint(db_session, "ep-2", document_id="doc-2", method="GET", path="/pet")
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_by_method_path("GET", "/pet", document_id="doc-1")

    assert [e.id for e in result] == ["ep-1"]


def test_list_by_method_path_scopes_to_project(db_session) -> None:
    """project 를 주면 다른 프로젝트의 동일 method+path는 제외된다."""
    _seed_endpoint(
        db_session, "ep-1", document_id="doc-1", method="GET", path="/pet", project="proj-a"
    )
    _seed_endpoint(
        db_session, "ep-2", document_id="doc-2", method="GET", path="/pet", project="proj-b"
    )
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_by_method_path("GET", "/pet", project="proj-a")

    assert [e.id for e in result] == ["ep-1"]


def test_list_by_operation_id_returns_exact_match(db_session) -> None:
    """operationId 가 정확히 일치하는 엔드포인트를 반환한다."""
    _seed_endpoint(db_session, "ep-1", operation_id="getPetById")
    _seed_endpoint(db_session, "ep-2", operation_id="deletePet")
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_by_operation_id("getPetById")

    assert [e.id for e in result] == ["ep-1"]


def test_list_by_operation_id_no_match_returns_empty(db_session) -> None:
    """operationId 가 없는 엔드포인트도 매칭 대상에서 자연히 빠진다."""
    _seed_endpoint(db_session, "ep-1", operation_id=None)
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_by_operation_id("getPetById")

    assert list(result) == []


def test_list_by_operation_id_scopes_to_document(db_session) -> None:
    """document_id 를 주면 다른 문서의 동일 operationId는 제외된다."""
    _seed_endpoint(db_session, "ep-1", document_id="doc-1", operation_id="getPetById")
    _seed_endpoint(db_session, "ep-2", document_id="doc-2", operation_id="getPetById")
    db_session.commit()
    repo = EndpointRepository(db_session)

    result = repo.list_by_operation_id("getPetById", document_id="doc-1")

    assert [e.id for e in result] == ["ep-1"]
