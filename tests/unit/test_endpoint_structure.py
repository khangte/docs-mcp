"""엔드포인트 구조 신호 파생 테스트(78번 설계 §4). DB 불필요."""

from __future__ import annotations

from app.services.indexer.endpoint_structure import (
    OPERATION_ALIASES,
    derive_endpoint_structure,
)


def test_child_collection_route_puts_leaf_in_a_field() -> None:
    """78번 §4.3 child 예시 — leaf/intent/context가 문서와 정확히 일치한다."""
    structure = derive_endpoint_structure(
        method="GET",
        path="/repos/{owner}/{repo}/topics",
        summary="Get all repository topics",
        tags=["repos"],
        operation_id="repos/get-all-topics",
    )

    assert structure.leaf_text == "topics topic"
    assert structure.intent_text == "list index all browse Get all repository topics"
    assert structure.context_text == (
        "repos repo owner get-all-topics get-all-topic get all"
    )


def test_root_item_route_uses_trailing_param_as_leaf() -> None:
    """78번 §4.3 root 예시 — item shape는 마지막 param 이름도 leaf에 넣는다."""
    structure = derive_endpoint_structure(
        method="GET",
        path="/repos/{owner}/{repo}",
        summary="Get a repository",
        tags=["repos"],
        operation_id="repos/get",
    )

    assert structure.leaf_text == "repos repo"
    assert structure.intent_text == "get retrieve fetch read show detail Get a repository"
    assert structure.context_text == "owner get"


def test_version_segment_is_dropped() -> None:
    """`v1` 은 코퍼스 전체가 공유해 판별력이 0이라 leaf/context 어디에도 넣지 않는다."""
    structure = derive_endpoint_structure(
        method="POST", path="/v1/customers", summary="Create a customer"
    )

    assert structure.leaf_text == "customers customer"
    assert "v1" not in structure.context_text
    assert structure.intent_text.startswith("create add new register ")


def test_item_route_param_id_subword_is_dropped_from_leaf() -> None:
    """`{subscription_exposed_id}` 의 `id` 는 leaf 판별에 기여하지 않으므로 뺀다."""
    structure = derive_endpoint_structure(
        method="DELETE",
        path="/v1/subscriptions/{subscription_exposed_id}",
        summary="Cancel a subscription",
    )

    leaf_tokens = structure.leaf_text.split()
    assert leaf_tokens[:2] == ["subscriptions", "subscription"]
    assert "id" not in leaf_tokens
    assert "subscription_exposed_id" in leaf_tokens
    assert structure.intent_text == "delete remove destroy Cancel a subscription"


def test_snake_case_leaf_is_split_into_subwords() -> None:
    """`line_items` 는 전체형과 조각을 모두 낸다(verdict 74 §5.1)."""
    structure = derive_endpoint_structure(
        method="GET", path="/v1/invoices/{invoice}/line_items", summary=""
    )

    leaf_tokens = structure.leaf_text.split()
    assert "line_items" in leaf_tokens
    assert "line" in leaf_tokens
    assert "items" in leaf_tokens
    assert "item" in leaf_tokens


def test_singularization_rules() -> None:
    """영어 굴절 규칙만 적용한다 — 약어 확장은 하지 않는다."""
    cases = {
        "/a/topics": "topic",
        "/a/categories": "category",
        "/a/boxes": "box",
        "/a/classes": "class",
        "/a/address": "address",
        "/a/pulls": "pull",
    }
    for path, expected in cases.items():
        structure = derive_endpoint_structure(method="GET", path=path)
        assert expected in structure.leaf_text.split(), path

    repository = derive_endpoint_structure(method="GET", path="/repos/{repo}")
    assert "repository" not in repository.leaf_text


def test_empty_and_param_only_paths_do_not_raise() -> None:
    """literal 세그먼트가 없는 path도 예외 없이 빈 leaf를 낸다."""
    assert derive_endpoint_structure(method="GET", path="").leaf_text == ""
    assert derive_endpoint_structure(method="GET", path="/").leaf_text == ""
    only_param = derive_endpoint_structure(method="GET", path="/{id}")
    assert only_param.leaf_text == ""
    assert "id" not in only_param.leaf_text.split()


def test_unknown_method_yields_no_alias() -> None:
    """HEAD/OPTIONS/TRACE 및 미인식 method는 alias를 만들지 않는다."""
    for method in ("HEAD", "OPTIONS", "TRACE", "PROPFIND"):
        structure = derive_endpoint_structure(
            method=method, path="/v1/customers", summary="Summary text"
        )
        assert structure.intent_text == "Summary text", method


def test_alias_table_is_frozen_as_specified() -> None:
    """78번 §4.4 동결 표. 항목 추가·삭제는 새 architect verdict를 요구한다."""
    assert OPERATION_ALIASES == {
        ("GET", "collection"): ("list", "index", "all", "browse"),
        ("GET", "item"): ("get", "retrieve", "fetch", "read", "show", "detail"),
        ("POST", "collection"): ("create", "add", "new", "register"),
        ("POST", "item"): ("create", "submit", "send"),
        ("PUT", "collection"): ("replace", "update", "set"),
        ("PUT", "item"): ("replace", "update", "set"),
        ("PATCH", "collection"): ("update", "modify", "edit", "change"),
        ("PATCH", "item"): ("update", "modify", "edit", "change"),
        ("DELETE", "collection"): ("delete", "remove", "clear"),
        ("DELETE", "item"): ("delete", "remove", "destroy"),
    }


def test_derivation_is_deterministic() -> None:
    """같은 입력은 항상 같은 세 문자열을 낸다(78번 §4.5 결정성 계약)."""
    kwargs = dict(
        method="GET",
        path="/repos/{owner}/{repo}/topics",
        summary="Get all repository topics",
        tags=["repos"],
        operation_id="repos/get-all-topics",
    )
    assert derive_endpoint_structure(**kwargs) == derive_endpoint_structure(**kwargs)
