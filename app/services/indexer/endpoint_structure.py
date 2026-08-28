"""엔드포인트 색인 시점 구조 신호 파생(`docs/architect-review/78` §4).

`method`·`path`·`summary`·`tags`·`operation_id` 다섯 입력만으로 가중 lexical
필드 3종을 결정적으로 만든다. LLM 호출·난수·색인 순서 의존이 없으므로 색인
경로와 백필 경로가 같은 값을 낸다(78번 §4.5 결정성 계약).

`EndpointBusinessMetadata`(LLM 생성)는 여기에 주입하지 않는다 — metadata 는
지금처럼 청크 `text`(가중치 D)에만 들어간다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: 버전 세그먼트(`v1`, `v2.1`). 한 문서의 모든 경로가 공유해 판별력이 0이라
#: leaf 에도 context 에도 넣지 않는다.
_VERSION_RE = re.compile(r"^v[0-9]+(\.[0-9]+)*$", re.IGNORECASE)

#: subword 분해 경계: `_`, `-`, `.`, `/` 및 camelCase 경계.
_SUBWORD_SPLIT_RE = re.compile(r"[_\-./]+|(?<=[a-z0-9])(?=[A-Z])")

#: item shape 의 마지막 param 이름에서 leaf 로 승격하지 않는 subword.
_PARAM_NOISE_SUBWORDS = frozenset({"id"})

#: (METHOD, shape) → operation alias. **`docs/architect-review/78` §4.4 에서
#: 동결한 표다.** 항목 추가·삭제는 새 architect verdict 를 요구한다 — 게이트에서
#: 실패한 질의의 동사를 여기에 더하는 것은 verdict 74 가 (b)(c) 를 반려한 것과
#: 같은 과적합이다.
OPERATION_ALIASES: dict[tuple[str, str], tuple[str, ...]] = {
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


@dataclass(frozen=True)
class EndpointStructure:
    """엔드포인트 1건의 가중 lexical 필드 3종."""

    #: 가중치 A — target leaf 자원 토큰.
    leaf_text: str
    #: 가중치 B — operation alias + summary 원문.
    intent_text: str
    #: 가중치 C — ancestor 경로·param 이름·tags·operationId subword.
    context_text: str


def _split_subwords(segment: str) -> list[str]:
    """세그먼트를 소문자 전체형 + (조각이 2개 이상일 때만) 각 조각으로 분해한다."""
    lowered = segment.lower()
    if not lowered:
        return []
    parts = [part.lower() for part in _SUBWORD_SPLIT_RE.split(segment) if part]
    tokens = [lowered]
    if len(parts) > 1:
        tokens.extend(parts)
    return tokens


def _singularize(token: str) -> str:
    """영어 굴절 규칙만으로 단수형을 만든다.

    `repos` → `repository` 같은 약어 확장은 결정적으로 유도할 수 없으므로
    하지 않는다(78번 §4.2, §11 비범위).
    """
    if len(token) > 3 and token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith("ss"):
        return token
    for suffix in ("ses", "xes", "zes", "ches", "shes"):
        if token.endswith(suffix):
            return token[:-2]
    if len(token) > 2 and token.endswith("s"):
        return token[:-1]
    return token


def _expand(tokens: Iterable[str]) -> list[str]:
    """각 토큰과 그 단수형을 최초 등장 순서로, 중복 없이 나열한다."""
    expanded: list[str] = []
    for token in tokens:
        for candidate in (token, _singularize(token)):
            if candidate and candidate not in expanded:
                expanded.append(candidate)
    return expanded


def _parse_path(path: str) -> tuple[list[str], list[str], str]:
    """path 를 (literal 세그먼트, param 이름, shape) 로 분해한다.

    shape 는 마지막 세그먼트가 `{param}` 이면 `"item"`, 아니면 `"collection"`.
    `/topics` 같은 하위 자원 컬렉션과 `/merge` 같은 action 은 결정적으로
    구분할 수 없으므로 두 부류만 둔다(78번 §4.1).
    """
    segments = [segment for segment in (path or "").split("/") if segment]
    literals: list[str] = []
    params: list[str] = []
    for segment in segments:
        if segment.startswith("{") and segment.endswith("}"):
            params.append(segment[1:-1])
        elif not _VERSION_RE.match(segment):
            literals.append(segment)
    last = segments[-1] if segments else ""
    shape = "item" if last.startswith("{") and last.endswith("}") else "collection"
    return literals, params, shape


def derive_endpoint_structure(
    *,
    method: str,
    path: str,
    summary: str = "",
    tags: Sequence[str] = (),
    operation_id: str | None = None,
) -> EndpointStructure:
    """엔드포인트 1건의 가중 lexical 필드 3종을 결정적으로 만든다."""
    literals, params, shape = _parse_path(path)
    leaf_segment = literals[-1] if literals else ""
    ancestor_segments = literals[:-1]

    leaf_tokens = _expand(_split_subwords(leaf_segment))
    if shape == "item" and params:
        trailing = [
            token
            for token in _split_subwords(params[-1])
            if token not in _PARAM_NOISE_SUBWORDS
        ]
        for token in _expand(trailing):
            if token not in leaf_tokens:
                leaf_tokens.append(token)

    alias = OPERATION_ALIASES.get((method.upper(), shape), ())
    intent_text = " ".join([*alias, summary or ""]).strip()

    context_source: list[str] = []
    for segment in ancestor_segments:
        context_source.extend(_split_subwords(segment))
    for param in params:
        context_source.extend(_split_subwords(param))
    for tag in tags or ():
        context_source.extend(_split_subwords(tag))
    for piece in (operation_id or "").split("/"):
        context_source.extend(_split_subwords(piece))
    context_tokens = [
        token for token in _expand(context_source) if token not in leaf_tokens
    ]

    return EndpointStructure(
        leaf_text=" ".join(leaf_tokens),
        intent_text=intent_text,
        context_text=" ".join(context_tokens),
    )
