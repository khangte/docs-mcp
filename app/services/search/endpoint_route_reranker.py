"""RRF 이후 route-family 안에서만 순위를 재배열하는 순수 함수 모듈.

`search_endpoints` 의 기본 `rrf` 전략에서, 넓은 RRF 후보를 endpoint 메타데이터로
hydrate 한 뒤 이 모듈이 **같은 route family 가 차지한 전역 슬롯 안에서만**
operation 의도와 path specificity 가 맞는 후보를 앞으로 보낸다
(`docs/architect-review/68_endpoint_route_family_rerank_and_variants_design.md` §4).

설계 제약:
- RRF 점수·`rrf.py` 공식·가중치는 건드리지 않는다. 순위만 바꾼다.
- family 사이 상대 위치는 불변 — 결과의 index별 family key 배열은 rerank 전후 동일.
- 외부 서비스·repository·설정 의존성 없음. LLM 호출·DB 쓰기 없음.
- lexicon 은 module 상수. env/config tuning surface 로 노출하지 않는다.
- 다의도 질의·CRUD 의미가 없는 bare noun 은 `Intent.NONE` → 전체 no-op.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

__all__ = ["RouteCandidate", "rerank_endpoints_by_route_family"]


class Intent(Enum):
    """질의에서 결정적으로 뽑아낸 operation 의도(§4.2)."""

    LIST = "list"
    CREATE = "create"
    DELETE = "delete"
    GET_ONE = "get_one"
    NONE = "none"


#: intent → 기대 HTTP method. NONE 은 매칭 대상이 아니다.
_EXPECTED_METHOD: dict[Intent, str] = {
    Intent.LIST: "GET",
    Intent.CREATE: "POST",
    Intent.DELETE: "DELETE",
    Intent.GET_ONE: "GET",
}

#: intent → 기대 target shape 이 collection(True) 인가 item(False) 인가.
_EXPECTS_COLLECTION: dict[Intent, bool] = {
    Intent.LIST: True,
    Intent.CREATE: True,
    Intent.DELETE: False,
    Intent.GET_ONE: False,
}

#: 영어 단어 / 한글 접두 결정 토큰(§4.2 표). 한글은 현재 tokenizer 와 같은
#: 한글 덩어리에 대해 접두 일치로 본다("삭제해줘" → "삭제").
_INTENT_TOKENS: dict[Intent, frozenset[str]] = {
    Intent.LIST: frozenset({"list", "all", "목록", "리스트", "전체"}),
    Intent.CREATE: frozenset(
        {"create", "add", "register", "new", "생성", "만들", "등록", "추가"}
    ),
    Intent.DELETE: frozenset(
        {"delete", "remove", "cancel", "terminate", "삭제", "제거", "취소", "해지", "종료"}
    ),
    Intent.GET_ONE: frozenset(
        {"get", "retrieve", "fetch", "details", "information", "상세", "정보"}
    ),
}

#: 단어보다 먼저 봐야 하는 두 단어 구(§4.2).
_INTENT_PHRASES: dict[Intent, tuple[str, ...]] = {
    Intent.DELETE: ("shut down",),
}

#: operation 신호가 아닌, resource token 비교에서 빼는 흔한 기능어.
_STOPWORDS: frozenset[str] = frozenset(
    {"a", "an", "the", "of", "to", "my", "me", "for", "please", "on", "in", "with"}
)

#: 형태 정규화만 허용하는 최소 alias(§4.3). 의미 사전은 넣지 않는다.
_LEAF_ALIASES: dict[str, str] = {
    "repository": "repo",
    "repositories": "repo",
    "repos": "repo",
}

_WORD_RE = re.compile(r"[a-z0-9]+|[가-힣]+")
_EN_RE = re.compile(r"[a-z]+")
_HANGUL_RE = re.compile(r"[가-힣]+")


@dataclass(frozen=True)
class RouteCandidate:
    """rerank 입력 한 건 — 넓은 RRF 후보를 endpoint 로 hydrate 한 최소 필드."""

    ref_id: str
    method: str
    path: str


def _segments(path: str) -> tuple[str, ...]:
    """path 를 세그먼트 배열로 자른다(선행/후행 슬래시 무시)."""
    return tuple(seg for seg in path.split("/") if seg)


def _is_param(segment: str) -> bool:
    """`{...}` path parameter 세그먼트인지."""
    return segment.startswith("{") and segment.endswith("}")


def _normalize_leaf(literal: str) -> str:
    """하이픈·언더스코어 분리 → 마지막 토큰 소문자화 → 단순 복수형 제거 → alias."""
    token = re.split(r"[-_]", literal)[-1].lower()
    if token in _LEAF_ALIASES:
        return _LEAF_ALIASES[token]
    if token.endswith("ies") and len(token) > 3:
        token = token[:-3] + "y"
    elif token.endswith("es") and len(token) > 3:
        token = token[:-2]
    elif token.endswith("s") and len(token) > 1:
        token = token[:-1]
    return _LEAF_ALIASES.get(token, token)


def extract_intent(query: str, variants: Sequence[str]) -> Intent:
    """원문 query + nonblank variants 에서 결정적으로 operation intent 를 뽑는다(§4.2).

    같은 intent 만 나오면 그 intent, 신호가 없거나 서로 다른 intent 가 둘 이상이면
    `Intent.NONE`(전체 rerank 생략). 도메인별 HTTP method 사전은 쓰지 않는다.
    """
    text = " ".join([query, *(v for v in variants if v and v.strip())]).lower()
    found: set[Intent] = set()

    for intent, phrases in _INTENT_PHRASES.items():
        if any(phrase in text for phrase in phrases):
            found.add(intent)

    en_words = set(_EN_RE.findall(text))
    hangul_chunks = _HANGUL_RE.findall(text)
    for intent, tokens in _INTENT_TOKENS.items():
        for token in tokens:
            if token.isascii():
                if token in en_words:
                    found.add(intent)
            elif any(chunk.startswith(token) for chunk in hangul_chunks):
                found.add(intent)

    return found.pop() if len(found) == 1 else Intent.NONE


def _resource_tokens(query: str, variants: Sequence[str]) -> set[str]:
    """질의의 비-operation resource token(정규화된 leaf 형태)."""
    operation_tokens = {t for tokens in _INTENT_TOKENS.values() for t in tokens}
    raw = _WORD_RE.findall(" ".join([query, *(v for v in variants if v and v.strip())]).lower())
    tokens: set[str] = set()
    for word in raw:
        if word in _STOPWORDS or word in operation_tokens:
            continue
        if any(word.startswith(op) for op in operation_tokens if not op.isascii()):
            continue
        tokens.add(_normalize_leaf(word))
    return tokens


def _family_root(segs: tuple[str, ...], all_segs: set[tuple[str, ...]]) -> tuple[str, ...]:
    """후보 집합 안에 실제로 존재하는 가장 짧은 prefix path 를 family root 로 잡는다(§4.3)."""
    best = segs
    for candidate in all_segs:
        if len(candidate) < len(best) and segs[: len(candidate)] == candidate:
            best = candidate
    return best


def _leaf_resource(segs: tuple[str, ...]) -> str:
    """item 이면 마지막 연속 parameter 앞 literal, collection 이면 마지막 literal(§4.3)."""
    idx = len(segs) - 1
    while idx >= 0 and _is_param(segs[idx]):
        idx -= 1
    if idx < 0:
        return ""
    return _normalize_leaf(segs[idx])


@dataclass(frozen=True)
class _Feat:
    """family 내부 정렬에 쓰는 후보 파생값."""

    original_rank: int
    ref_id: str
    method_match: bool
    target_match: bool
    shape_match: bool
    relative_depth: int


def _sortable(feat: _Feat, specificity_match: bool) -> tuple[int, int, int, int, int, str]:
    """§4.4 호환성 tuple 내림차순 + 원래 rank 오름차순 정렬 키."""
    return (
        -int(feat.method_match),
        -int(feat.target_match),
        -int(feat.shape_match),
        -int(specificity_match),
        feat.original_rank,
        feat.ref_id,
    )


def rerank_endpoints_by_route_family(
    ordered: Sequence[RouteCandidate], query: str, variants: Sequence[str]
) -> list[RouteCandidate]:
    """RRF 순서(rank 1 우선)의 후보를 route-family 안에서만 재배열한다.

    intent 가 `NONE` 이거나 family 별 보수적 가드에 걸리면 해당 부분은 입력 순서를
    그대로 둔다. 반환 리스트의 index별 family key 배열은 입력과 완전히 같다.
    """
    items = list(ordered)
    intent = extract_intent(query, variants)
    if intent is Intent.NONE or len(items) < 2:
        return items

    all_segs = {_segments(c.path) for c in items}
    roots = [_family_root(_segments(c.path), all_segs) for c in items]

    resource_tokens = _resource_tokens(query, variants)
    expected_method = _EXPECTED_METHOD[intent]
    expects_collection = _EXPECTS_COLLECTION[intent]

    # family root → 그 family 가 원래 차지한 전역 index 목록(입력 순서 유지).
    family_indices: dict[tuple[str, ...], list[int]] = {}
    for idx, root in enumerate(roots):
        family_indices.setdefault(root, []).append(idx)

    result = list(items)
    for root, indices in family_indices.items():
        if len(indices) < 2:
            continue

        feats: list[_Feat] = []
        for rank, idx in enumerate(indices):
            candidate = items[idx]
            segs = _segments(candidate.path)
            terminal_is_param = bool(segs) and _is_param(segs[-1])
            leaf = _leaf_resource(segs)
            method_match = candidate.method.upper() == expected_method
            shape_match = terminal_is_param != expects_collection
            target_match = bool(resource_tokens) and leaf in resource_tokens
            feats.append(
                _Feat(
                    original_rank=rank,
                    ref_id=candidate.ref_id,
                    method_match=method_match,
                    target_match=target_match,
                    shape_match=shape_match,
                    relative_depth=len(segs) - len(root),
                )
            )

        # 보수적 가드(§4.4): 정합 후보가 없거나, 질의의 child resource 가 이
        # family 어디에도 없으면 승급을 시도하지 않는다.
        if not any(f.method_match and f.shape_match for f in feats):
            continue
        family_leaves = {_leaf_resource(_segments(items[i].path)) for i in indices}
        if resource_tokens and resource_tokens.isdisjoint(family_leaves):
            continue

        # target match 가 있는 family 에서는 질의가 명시한 가장 깊은 leaf 를
        # specificity 기준으로 삼는다(70번 §2.2). ancestor context 로 함께 언급된
        # 얕은 collection/item 이 주 target 인 child 를 밀어내지 않게 한다.
        # target match 가 없으면 기존 보수적 root/item fallback(min depth)을 유지한다.
        if any(f.target_match for f in feats):
            qualifying = [f for f in feats if f.target_match]
            preferred_depth = max(f.relative_depth for f in qualifying)
        else:
            qualifying = [f for f in feats if f.method_match and f.shape_match]
            preferred_depth = min((f.relative_depth for f in qualifying), default=-1)
        qualifying_ids = {id(f) for f in qualifying}

        order = sorted(
            feats,
            key=lambda f: _sortable(
                f, id(f) in qualifying_ids and f.relative_depth == preferred_depth
            ),
        )
        for slot, feat in zip(indices, order, strict=True):
            result[slot] = items[indices[feat.original_rank]]

    return result
