"""route-family 제한 rerank 순수 함수 테스트.

`docs/architect-review/68_endpoint_route_family_rerank_and_variants_design.md` §5.1
의 8개 고정 케이스를 그대로 옮긴다. DB·서비스 의존성 없이 `RouteCandidate`
리스트만으로 검증한다.
"""

from __future__ import annotations

from app.services.search.endpoint_route_reranker import (
    RouteCandidate,
    rerank_endpoints_by_route_family,
)


def _c(ref_id: str, method: str, path: str) -> RouteCandidate:
    return RouteCandidate(ref_id=ref_id, method=method, path=path)


def _order(candidates: list[RouteCandidate]) -> list[str]:
    return [c.ref_id for c in candidates]


def test_create_collection_root_precedes_family_child() -> None:
    """CREATE 는 같은 family 의 더 깊은 child 보다 collection root 를 앞세운다."""
    ordered = [
        _c("child", "POST", "/v1/customers/{id}/sources"),
        _c("root", "POST", "/v1/customers"),
    ]
    out = rerank_endpoints_by_route_family(ordered, "create a customer", [])
    assert _order(out) == ["root", "child"]


def test_delete_item_root_precedes_deeper_child() -> None:
    """DELETE 는 더 깊은 child 보다 item root 를 앞세운다."""
    ordered = [
        _c("deep", "DELETE", "/repos/{owner}/{repo}/issues/{number}"),
        _c("root", "DELETE", "/repos/{owner}/{repo}"),
    ]
    out = rerank_endpoints_by_route_family(ordered, "delete a repository", [])
    assert _order(out) == ["root", "deep"]


def test_list_keeps_child_collection_ahead_of_root() -> None:
    """`list commits of a repo` 는 commits collection 을 repo root 보다 앞에 둔다."""
    ordered = [
        _c("root", "GET", "/repos/{owner}/{repo}"),
        _c("commits", "GET", "/repos/{owner}/{repo}/commits"),
    ]
    out = rerank_endpoints_by_route_family(ordered, "list commits of a repo", [])
    assert _order(out) == ["commits", "root"]


def test_bare_noun_query_is_full_noop() -> None:
    """operation 신호가 없는 bare noun 질의는 완전 no-op 한다."""
    ordered = [
        _c("pulls", "GET", "/repos/{owner}/{repo}/pulls"),
        _c("root", "GET", "/repos/{owner}/{repo}"),
    ]
    out = rerank_endpoints_by_route_family(ordered, "pull request", [])
    assert _order(out) == ["pulls", "root"]


def test_multi_intent_list_and_create_is_full_noop() -> None:
    """list 와 create 가 함께 나오는 다의도 질의는 완전 no-op 한다."""
    ordered = [
        _c("child", "POST", "/repos/{owner}/{repo}/issues/{number}"),
        _c("collection", "GET", "/repos/{owner}/{repo}/issues"),
        _c("root", "GET", "/repos/{owner}/{repo}"),
    ]
    out = rerank_endpoints_by_route_family(
        ordered, "이슈 목록 조회하고 새 이슈 생성", []
    )
    assert _order(out) == ["child", "collection", "root"]


def test_unmatched_child_resource_does_not_promote_root() -> None:
    """명시 child resource 가 family 어느 leaf 에도 안 맞으면 root 를 추측 승급하지 않는다."""
    ordered = [
        _c("root", "DELETE", "/repos/{owner}/{repo}"),
        _c("issue", "DELETE", "/repos/{owner}/{repo}/issues/{number}"),
    ]
    out = rerank_endpoints_by_route_family(ordered, "delete a widget", [])
    assert _order(out) == ["root", "issue"]


def test_per_index_family_key_array_is_invariant() -> None:
    """rerank 전후, index별 family root 배열이 완전히 같다(cross-family 순위 불변)."""
    ordered = [
        _c("cust_root", "POST", "/v1/customers"),
        _c("issues", "POST", "/repos/{owner}/{repo}/issues"),
        _c("cust_child", "POST", "/v1/customers/{id}/sources"),
    ]
    out = rerank_endpoints_by_route_family(ordered, "create a customer", [])

    def families(items: list[RouteCandidate]) -> list[str]:
        return [c.path.rsplit("/", 1)[0] if "{id}" in c.path else c.path for c in items]

    # 중간 슬롯(다른 family, 단독)은 그대로여야 한다.
    assert out[1].ref_id == "issues"
    # customers family 두 슬롯 안에서만 재배열됐다.
    assert {out[0].ref_id, out[2].ref_id} == {"cust_root", "cust_child"}
    assert out[0].ref_id == "cust_root"


def test_p09_line_items_child_already_first_stays_first() -> None:
    """p09 축소 재현: query 가 명시한 line_items child 가 이미 1위면 rerank 뒤에도 1위.

    같은 family 에 sessions collection·session item 이 함께 있어도 ancestor context 인
    얕은 sessions 를 위로 올리지 않는다(70번 §2).
    """
    ordered = [
        _c("line_items", "GET", "/v1/checkout/sessions/{session}/line_items"),
        _c("sessions", "GET", "/v1/checkout/sessions"),
        _c("session", "GET", "/v1/checkout/sessions/{session}"),
    ]
    out = rerank_endpoints_by_route_family(
        ordered, "list the line items inside that checkout session", []
    )
    assert _order(out)[0] == "line_items"


def test_parent_and_child_both_named_prefers_deepest_matched_leaf() -> None:
    """parent·child resource 가 질의에 함께 있으면 더 깊은 명시 leaf 를 앞세운다."""
    ordered = [
        _c("issues", "GET", "/repos/{owner}/{repo}/issues"),
        _c("issue", "GET", "/repos/{owner}/{repo}/issues/{issue_number}"),
        _c("comments", "GET", "/repos/{owner}/{repo}/issues/{issue_number}/comments"),
    ]
    out = rerank_endpoints_by_route_family(
        ordered, "list the comments on that issue", []
    )
    assert _order(out) == ["comments", "issues", "issue"]


def test_get_one_keeps_item_endpoint_not_deeper_untargeted_child() -> None:
    """target 없는 더 깊은 child 가 있어도 GET_ONE 은 명시된 item endpoint 를 유지한다."""
    ordered = [
        _c("sessions", "GET", "/v1/checkout/sessions"),
        _c("session", "GET", "/v1/checkout/sessions/{session}"),
        _c("line_items", "GET", "/v1/checkout/sessions/{session}/line_items"),
    ]
    out = rerank_endpoints_by_route_family(ordered, "get the checkout session", [])
    assert _order(out)[0] == "session"


def test_no_explicit_child_resource_keeps_shallow_root() -> None:
    """resource token 이 없으면(명시 child 없음) 기존 shallow root fallback 을 유지한다."""
    ordered = [
        _c("child", "GET", "/v1/things/{id}/parts"),
        _c("root", "GET", "/v1/things"),
    ]
    out = rerank_endpoints_by_route_family(ordered, "list all", [])
    assert _order(out) == ["root", "child"]


def test_deepest_leaf_rerank_keeps_other_family_slot_fixed() -> None:
    """deepest-leaf 승급이 일어나도 다른 family 의 전역 슬롯은 그대로다."""
    ordered = [
        _c("sessions", "GET", "/v1/checkout/sessions"),
        _c("other", "GET", "/v1/refunds"),
        _c("session", "GET", "/v1/checkout/sessions/{session}"),
        _c("line_items", "GET", "/v1/checkout/sessions/{session}/line_items"),
    ]
    out = rerank_endpoints_by_route_family(
        ordered, "list the line items inside that checkout session", []
    )
    assert out[1].ref_id == "other"
    assert out[0].ref_id == "line_items"
    assert {c.ref_id for c in out} == {"sessions", "other", "session", "line_items"}


def test_tie_falls_back_to_original_rank_then_ref_id() -> None:
    """호환성 tuple 이 동점이면 원래 RRF rank, 그다음 ref_id 로 결정적이다."""
    ordered = [
        _c("mmm", "GET", "/v1/things/{id}/parts"),
        _c("kkk", "GET", "/v1/things/{id}/tags"),
        _c("aaa", "GET", "/v1/things"),
    ]
    out = rerank_endpoints_by_route_family(ordered, "list all", [])
    # things root 는 specificity 로 선두. 나머지 둘은 전 match bool 동점 →
    # ref_id "kkk" < "mmm" 이지만 원래 rank(parts=0 < tags=1) 가 먼저다.
    assert _order(out) == ["aaa", "mmm", "kkk"]
