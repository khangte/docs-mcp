"""엔드포인트 canonical projection 파생 (`docs/architect-review/101` §2.2).

`ParsedEndpoint` 하나만 입력으로 받아, HTTP method/path 와 OpenAPI 원문을
**고정 template** 으로 재배열한 짧은 canonical text 를 결정적으로 만든다.
LLM 호출·번역·동의어 확장·난수·색인 순서 의존이 없으므로 색인 경로와 백필
경로가 같은 값을 낸다(§6 결정성 계약).

`EndpointBusinessMetadata`(LLM 생성)와 `query_variants` 는 여기에 주입하지
않는다 — 이 후보의 source-of-truth 는 업로드된 OpenAPI 하나다.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from app.services.indexer.endpoint_structure import (
    _VERSION_RE,
    _expand,
    _singularize,
    _split_subwords,
)
from app.services.parser.openapi_parser import ParsedEndpoint

#: projection format 식별자. template/정규화/cap/필드 순서를 바꾸면 올린다 —
#: 새 calibration corpus 와 architect verdict 없이 변경 금지(§2.2, §6).
REPRESENTATION_VERSION = "v1"

#: `(HTTP method, collection|item shape) -> action alias` 사전 동결표(§2.2).
#: `endpoint_structure.OPERATION_ALIASES` 를 format v1 에 **복사해 동결**한 것 —
#: 그쪽 표가 나중에 바뀌어도 projection hash 가 조용히 흔들리지 않게 한다.
#: 이 표를 바꾸려면 `REPRESENTATION_VERSION` 을 올리고 architect verdict 를 받는다.
_OPERATION_ALIASES_V1: dict[tuple[str, str], tuple[str, ...]] = {
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

#: 총 canonical text 상한(Unicode code point). 초과 시 §2.2 표 순서(=아래
#: `_LINE_ORDER`)의 뒤쪽 줄부터 잘린다. env tuning 대상이 아니다.
CANONICAL_TEXT_MAX_CHARS = 1024

#: description 은 현 endpoint chunk 와 같은 상한을 쓴다(chunk_builder 와 동일 값).
_DESCRIPTION_MAX_CHARS = 300
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

#: parameter location 정렬 순서(§2.2 "(location, name) 정렬").
_LOCATION_ORDER = {"path": 0, "query": 1, "header": 2, "cookie": 3}


@dataclass(frozen=True)
class EndpointProjection:
    """엔드포인트 1건의 canonical projection 결과."""

    canonical_text: str
    representation_version: str
    source_hash: str


def _norm(value: str) -> str:
    """NFKC 정규화 + 앞뒤 공백 제거 + 내부 공백 한 칸화."""
    text = unicodedata.normalize("NFKC", value or "")
    return _WS_RE.sub(" ", text).strip()


def _literal_segments(path: str) -> list[str]:
    """path 에서 `{param}` 과 version 세그먼트를 뺀 literal 세그먼트만 순서대로."""
    out: list[str] = []
    for segment in (path or "").split("/"):
        if not segment or (segment.startswith("{") and segment.endswith("}")):
            continue
        if _VERSION_RE.match(segment):
            continue
        out.append(segment)
    return out


def _path_shape(path: str) -> str:
    """마지막 세그먼트가 `{param}` 이면 item, 아니면 collection."""
    segments = [s for s in (path or "").split("/") if s]
    last = segments[-1] if segments else ""
    return "item" if last.startswith("{") and last.endswith("}") else "collection"


def _operation_id_tokens(operation_id: str | None) -> str:
    """operationId 원문 + camelCase/`_`/`-`/`/` subword(단수형 포함) 나열."""
    raw = _norm(operation_id or "")
    if not raw:
        return ""
    # `_split_subwords` 는 소문자 전체형 + 조각을 낸다. 원 대소문자 raw 를
    # 앞에 두고, 확장 결과에서 이미 나온 토큰은 건너뛴다.
    out = [raw]
    for token in _expand(_split_subwords(raw)):
        if token not in out:
            out.append(token)
    return " ".join(out)


def _param_line(endpoint: ParsedEndpoint) -> str:
    """path/query/header/cookie parameter 를 `(location, name)` 정렬로 `loc name` 나열."""
    seen: set[tuple[str, str]] = set()
    items: list[tuple[str, str]] = []
    for param in endpoint.parameters:
        loc = _norm(param.location).lower()
        name = _norm(param.name)
        if not name or (loc, name) in seen:
            continue
        seen.add((loc, name))
        items.append((loc, name))
    items.sort(key=lambda it: (_LOCATION_ORDER.get(it[0], 99), it[0], it[1]))
    return " ".join(f"{loc} {name}" for loc, name in items)


def _body_line(endpoint: ParsedEndpoint) -> str:
    """request body 최상위 property 이름을 정렬·중복 제거해 나열."""
    body = endpoint.request_body
    if body is None or not isinstance(body.schema, dict):
        return ""
    properties = body.schema.get("properties")
    if not isinstance(properties, dict):
        return ""
    names = sorted({_norm(str(name)) for name in properties if _norm(str(name))})
    return " ".join(names)


def _tags_line(tags: list[str]) -> str:
    """OpenAPI tags 원문 + subword 를 정렬·중복 제거해 나열."""
    tokens: set[str] = set()
    for tag in tags or ():
        normalized = _norm(tag)
        if not normalized:
            continue
        tokens.add(normalized)
        tokens.update(_split_subwords(normalized))
    return " ".join(sorted(t for t in tokens if t))


def _description_line(description: str) -> str:
    """HTML 태그 제거 후 300자 상한(현 endpoint chunk 와 동일 규칙)."""
    stripped = _HTML_TAG_RE.sub("", description or "")
    return _norm(stripped)[:_DESCRIPTION_MAX_CHARS]


#: 줄 생성 순서 = §2.2 표 순서. cap 초과 시 뒤쪽부터 잘린다.
_LINE_ORDER = (
    "MethodPath",
    "Ancestor",
    "Resource",
    "Action",
    "Phrase",
    "OperationId",
    "Summary",
    "Description",
    "Params",
    "Body",
    "Tags",
)


def _build_lines(endpoint: ParsedEndpoint) -> dict[str, str]:
    """label -> 정규화된 값. 값이 비면 `_compose` 에서 그 줄을 생략한다."""
    method = _norm(endpoint.method).upper()
    path = _norm(endpoint.path)
    literals = _literal_segments(path)
    leaf_segment = literals[-1] if literals else ""
    ancestor_segments = literals[:-1]

    ancestor_tokens = _expand(
        [t for seg in ancestor_segments for t in _split_subwords(seg)]
    )
    resource_tokens = _expand(_split_subwords(leaf_segment))

    shape = _path_shape(path)
    aliases = _OPERATION_ALIASES_V1.get((method, shape), ())
    leaf_singular = _singularize(leaf_segment.lower()) if leaf_segment else ""
    phrase = f"{aliases[0]} {leaf_singular}".strip() if aliases and leaf_singular else ""

    return {
        "MethodPath": f"{method} {path}".strip(),
        "Ancestor": " ".join(ancestor_tokens),
        "Resource": " ".join(resource_tokens),
        "Action": " ".join(aliases),
        "Phrase": phrase,
        "OperationId": _operation_id_tokens(endpoint.operation_id),
        "Summary": _norm(endpoint.summary),
        "Description": _description_line(endpoint.description),
        "Params": _param_line(endpoint),
        "Body": _body_line(endpoint),
        "Tags": _tags_line(list(endpoint.tags)),
    }


def _compose(lines: dict[str, str]) -> str:
    """값이 있는 줄만 `label: value` 로, 총 상한 초과 시 뒤쪽 줄부터 자른다."""
    rendered: list[str] = []
    used = 0
    for label in _LINE_ORDER:
        value = lines.get(label, "")
        if not value:
            continue
        piece = f"{label}: {value}"
        sep = 1 if rendered else 0
        if used + sep + len(piece) > CANONICAL_TEXT_MAX_CHARS:
            remaining = CANONICAL_TEXT_MAX_CHARS - used - sep
            if remaining > 0:
                rendered.append(piece[:remaining])
            break
        rendered.append(piece)
        used += sep + len(piece)
    return "\n".join(rendered)


def build_endpoint_projection(endpoint: ParsedEndpoint) -> EndpointProjection:
    """`ParsedEndpoint` 에서 format v1 canonical projection 을 결정적으로 만든다."""
    canonical_text = _compose(_build_lines(endpoint))
    source_hash = hashlib.sha256(
        f"{REPRESENTATION_VERSION}\n{canonical_text}".encode()
    ).hexdigest()
    return EndpointProjection(
        canonical_text=canonical_text,
        representation_version=REPRESENTATION_VERSION,
        source_hash=source_hash,
    )
