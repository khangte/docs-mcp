# 79. 엔드포인트 색인 구조 신호 구현 계획

> **실행자 안내:** developer가 Task 1부터 순서대로 실행한다. 각 Task는 독립적으로
> 테스트 가능한 산출물로 끝난다. **developer는 커밋하지 않는다** — Task 끝의
> "커밋 경계"는 lead가 나중에 쪼갤 단위와 메시지를 미리 확정해 둔 것이고,
> developer는 워킹트리를 그대로 두고 lead에 보고한다.
> 서브에이전트·worktree 분기는 쓰지 않는다.

**Goal:** 엔드포인트 lexical 표현을 색인 시점에 leaf/intent/context/full-text 네
등급으로 구조화하고, 가중 tsvector로 키워드 arm의 density 역전을 제거한다.

**Architecture:** `chunk`에 결정적 파생 평문 컬럼 3개를 추가하고, 그 셋과 기존 `text`를
`setweight` A/B/C/D로 묶은 생성 컬럼 `search_tsv`를 만든다. `text`·`embedding`은 불변이라
재임베딩이 없고 벡터 arm은 비트 단위로 같다. 키워드 arm은 설정 스위치
`DOCS_MCP_SEARCH_LEXICAL_FIELD`로 `text_tsv`(기본, 현행) / `search_tsv`(신규) 중 하나를 쓴다.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.x ORM, PostgreSQL 15+ (`tsvector`/`setweight`/
`ts_rank`/부분 GIN 인덱스), alembic, pytest, uv, ruff.

**Spec:** `docs/architect-review/78_endpoint_index_structure_signal_design.md`
(lead 승인 완료 — D1~D7 전부 권고대로)

## Global Constraints

- 타입 힌트 필수. `print()` 금지, `logging` 사용. 함수·클래스에 한국어 docstring.
- 한자 사용 금지 — "분석"(U+BD84 U+C11D)처럼 한글로 쓴다.
- 파일·폴더·함수 `snake_case`, 클래스 `PascalCase`. 문서에서 파일 경로는 백틱으로 감싼다.
- 상대 import 금지(ruff `flake8-tidy-imports` `ban-relative-imports = "all"`), line-length 100.
- `app/models/chunk.py`의 **`TEXT_TSV_EXPRESSION` 리터럴을 편집하지 않는다.** 한 바이트라도
  달라지면 alembic autogenerate가 기존 컬럼에 스푸리어스 diff를 낸다.
- `app/services/search/endpoint_candidate_search.py`, `app/services/search/rrf.py`,
  `app/services/search/vector_search.py`는 **변경하지 않는다**.
- `chunk.text`, `chunk.embedding`에 쓰는 코드를 추가하지 않는다(재임베딩 0이 이 설계의 전제).
- `OPERATION_ALIASES` 표(Task 1)와 `ts_rank` 가중치 `{0.1, 0.2, 0.4, 1.0}`(Task 5)은
  **동결값**이다. 게이트 결과를 보고 조정하지 않는다 — 조정이 필요하다는 판단이 서면
  architect에 보고하고 멈춘다.
- 기본 배포 동작은 무변경이다: `search_lexical_field` 기본값은 `"text"`.
- 테스트 실행: `uv run --extra test pytest -m 'not slow'`. 린트: `uv run ruff check .`
  (`uv run mypy`는 `pyproject.toml`에 mypy가 dev 그룹에 있으므로
  `uv run --group dev mypy app`으로 실행한다).

---

## 파일 구조

| 경로 | 책임 | 상태 |
|---|---|---|
| `app/services/indexer/endpoint_structure.py` | path 파싱·subword·단수화·alias 표·`derive_endpoint_structure` (순수 함수, DB/ORM 무관) | 신규 |
| `app/services/indexer/chunk_builder.py` | `BuiltChunk`에 파생 3필드 담기 | 수정 |
| `app/models/chunk.py` | 컬럼 3개 + `SEARCH_TSV_EXPRESSION` + 부분 GIN 인덱스 | 수정 |
| `alembic/versions/c4d9e1f70a2b_add_endpoint_structure_lexical_fields.py` | 컬럼·생성 컬럼·인덱스 마이그레이션 | 신규 |
| `app/services/indexer/indexer_service.py` | `Chunk(...)` 생성 시 3필드 전달 | 수정 |
| `app/repositories/chunk_repository.py` | `search_endpoint_by_text`의 `lexical_field` 분기 | 수정 |
| `app/services/search/keyword_search.py` | `lexical_field` 보관·전달 | 수정 |
| `app/core/config.py` | `search_lexical_field` 설정 | 수정 |
| `app/composition.py` | `AppState.search_lexical_field` + `KeywordSearch` 배선 | 수정 |
| `app/scripts/backfill_endpoint_structure.py` | 기존 색인의 3필드 백필(임베딩 호출 0) | 신규 |
| `tests/fixtures/corpus_eval/run_corpus_eval.py` | `--lexical-field` 축 | 수정 |
| `docs/adr/0005_weighted_endpoint_lexical_index.md` | 신규 ADR | 신규 |
| `docs/adr/0002_pgvector_hybrid_search.md` | 후속 영향 1줄 | 수정 |

테스트: `tests/unit/test_endpoint_structure.py`(신규),
`tests/unit/test_backfill_endpoint_structure.py`(신규),
`tests/unit/test_search_lexical_field_settings.py`(신규), 그리고 기존
`test_chunk_builder.py` / `test_chunk_repository.py` / `test_keyword_search.py` /
`test_indexer_service.py` / `test_endpoint_chunk_refresher.py` 확장.

---

## Task 1: 구조 신호 파생 모듈

**Files:**
- Create: `app/services/indexer/endpoint_structure.py`
- Test: `tests/unit/test_endpoint_structure.py`

**Interfaces:**
- Consumes: 없음 (표준 라이브러리만)
- Produces:
  - `EndpointStructure` — frozen dataclass, 필드 `leaf_text: str`, `intent_text: str`, `context_text: str`
  - `derive_endpoint_structure(*, method: str, path: str, summary: str = "", tags: Sequence[str] = (), operation_id: str | None = None) -> EndpointStructure`
  - `OPERATION_ALIASES: dict[tuple[str, str], tuple[str, ...]]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_endpoint_structure.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_endpoint_structure.py -q
```
기대: `ModuleNotFoundError: No module named 'app.services.indexer.endpoint_structure'`

- [ ] **Step 3: 구현**

`app/services/indexer/endpoint_structure.py`:

```python
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
```

- [ ] **Step 4: 통과 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_endpoint_structure.py -q && uv run ruff check app/services/indexer/endpoint_structure.py tests/unit/test_endpoint_structure.py
```
기대: 테스트 전건 PASS, ruff 무경고.

- [ ] **Step 5: 커밋 경계 (lead 실행)**

`feat(indexer): derive deterministic endpoint structure signals`
대상: `app/services/indexer/endpoint_structure.py`, `tests/unit/test_endpoint_structure.py`

---

## Task 2: 청크 빌더에 파생 필드 연결

**Files:**
- Modify: `app/services/indexer/chunk_builder.py` (`BuiltChunk` 정의, `build_chunks` 엔드포인트 루프)
- Test: `tests/unit/test_chunk_builder.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 1의 `derive_endpoint_structure`, `EndpointStructure`
- Produces: `BuiltChunk`가 `leaf_text: str = ""`, `intent_text: str = ""`, `context_text: str = ""` 필드를 갖는다 (schema·section 청크는 기본값 빈 문자열)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_chunk_builder.py` 끝에 추가:

```python
def test_endpoint_chunk_carries_structure_fields() -> None:
    """78번 §4: endpoint 청크는 leaf/intent/context 파생 필드를 함께 담는다."""
    endpoint = ParsedEndpoint(
        method="GET",
        path="/repos/{owner}/{repo}/topics",
        operation_id="repos/get-all-topics",
        summary="Get all repository topics",
        description="",
        tags=["repos"],
    )
    document = ParsedDocument(title="t", version="1", endpoints=[endpoint])
    chunks = build_chunks(document, {("GET", "/repos/{owner}/{repo}/topics"): "ep-1"})

    endpoint_chunk = next(c for c in chunks if c.chunk_type == "endpoint")
    assert endpoint_chunk.leaf_text == "topics topic"
    assert endpoint_chunk.intent_text == (
        "list index all browse Get all repository topics"
    )
    assert endpoint_chunk.context_text == (
        "repos repo owner get-all-topics get-all-topic get all"
    )


def test_endpoint_chunk_text_is_unchanged_by_structure_fields() -> None:
    """구조 필드는 `text` 를 바꾸지 않는다 — 재임베딩 0 이 이 설계의 전제다."""
    endpoint = ParsedEndpoint(
        method="GET",
        path="/repos/{owner}/{repo}/topics",
        operation_id="repos/get-all-topics",
        summary="Get all repository topics",
        description="",
        tags=["repos"],
    )
    document = ParsedDocument(title="t", version="1", endpoints=[endpoint])
    chunks = build_chunks(document, {("GET", "/repos/{owner}/{repo}/topics"): "ep-1"})

    endpoint_chunk = next(c for c in chunks if c.chunk_type == "endpoint")
    assert endpoint_chunk.text == build_endpoint_chunk_text(endpoint)
    assert "list index all browse" not in endpoint_chunk.text


def test_schema_and_section_chunks_have_empty_structure_fields() -> None:
    """endpoint 가 아닌 청크는 구조 필드가 빈 문자열이다(생성 컬럼이 NULL 이 된다)."""
    document = ParsedDocument(
        title="t",
        version="1",
        schemas=[ParsedSchema(name="Pet", json_schema={}, description="")],
        sections=[ParsedSection(title="Intro", content="hello")],
    )
    chunks = build_chunks(document, {}, {0: "sec-0"}, {0: "sch-0"})

    for chunk in chunks:
        assert chunk.chunk_type in ("schema", "section")
        assert chunk.leaf_text == ""
        assert chunk.intent_text == ""
        assert chunk.context_text == ""
```

> `ParsedDocument`/`ParsedSection`/`ParsedSchema` 시그니처는 기존 테스트가 쓰는 형태를
> 그대로 따른다. 인자 이름이 다르면 `app/services/parser/openapi_parser.py`의
> dataclass 정의에 맞춘다.

- [ ] **Step 2: 실패 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_chunk_builder.py -q -k structure
```
기대: `AttributeError: 'BuiltChunk' object has no attribute 'leaf_text'`

- [ ] **Step 3: 구현**

`app/services/indexer/chunk_builder.py` — import 추가:

```python
from app.services.indexer.endpoint_structure import derive_endpoint_structure
```

`BuiltChunk` 교체:

```python
@dataclass
class BuiltChunk:
    """청크 텍스트 빌드 결과(타입/참조ID/텍스트 + 엔드포인트 구조 신호).

    `leaf_text`/`intent_text`/`context_text` 는 endpoint 청크에만 채워지고
    schema·section 청크는 빈 문자열이다(`docs/architect-review/78` §4.3).
    이 세 필드는 `text` 에 섞이지 않는다 — `text` 가 바뀌면 재임베딩이
    필요해지고 벡터 arm 불변 전제가 깨진다.
    """

    chunk_type: str  # "endpoint" | "schema" | "section"
    ref_id: str  # endpoint_id, schema_id 또는 section_id
    text: str
    leaf_text: str = ""
    intent_text: str = ""
    context_text: str = ""
```

`build_chunks` 의 엔드포인트 루프에서 `chunks.append(...)` 부분 교체:

```python
        metadata = (business_metadata or {}).get((endpoint.method, endpoint.path))
        structure = derive_endpoint_structure(
            method=endpoint.method,
            path=endpoint.path,
            summary=endpoint.summary or "",
            tags=endpoint.tags,
            operation_id=endpoint.operation_id,
        )
        chunks.append(
            BuiltChunk(
                chunk_type="endpoint",
                ref_id=eid,
                text=build_endpoint_chunk_text(endpoint, metadata=metadata),
                leaf_text=structure.leaf_text,
                intent_text=structure.intent_text,
                context_text=structure.context_text,
            )
        )
```

- [ ] **Step 4: 통과 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_chunk_builder.py -q && uv run ruff check app/services/indexer/chunk_builder.py
```

- [ ] **Step 5: 커밋 경계 (lead 실행)**

`feat(indexer): carry structure signals on built endpoint chunks`

---

## Task 3: 모델 컬럼 · 가중 생성 컬럼 · 마이그레이션

**Files:**
- Modify: `app/models/chunk.py`
- Create: `alembic/versions/c4d9e1f70a2b_add_endpoint_structure_lexical_fields.py`
- Test: `tests/unit/test_chunk_repository.py` (테스트 추가)

**Interfaces:**
- Consumes: 없음
- Produces:
  - `Chunk.leaf_text`, `Chunk.intent_text`, `Chunk.context_text` (`Mapped[str]`, NOT NULL, 기본값 `""`)
  - `Chunk.search_tsv` (`Mapped[str | None]`, STORED generated, deferred)
  - 모듈 상수 `SEARCH_TSV_EXPRESSION: str`, 헬퍼 `_norm_sql(column: str) -> str`
  - 인덱스 `ix_chunk_search_tsv` (GIN, `WHERE chunk_type = 'endpoint'`)
  - alembic head가 `b7e4a2c9d1f8` → `c4d9e1f70a2b` 로 이동

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_chunk_repository.py` 끝에 추가:

```python
def test_text_tsv_expression_matches_shared_normalization() -> None:
    """`TEXT_TSV_EXPRESSION` 은 기존 리터럴 그대로여야 한다(alembic 스푸리어스 diff 방지)."""
    from app.models.chunk import TEXT_TSV_EXPRESSION, _norm_sql

    assert TEXT_TSV_EXPRESSION == f"to_tsvector('simple', {_norm_sql('text')})"


def test_search_tsv_weights_endpoint_fields(db_session) -> None:
    """endpoint 청크의 search_tsv 는 A/B/C/D 가중치를 갖는다."""
    from sqlalchemy import select

    from app.models import Chunk

    _seed_document(db_session, "doc-w")
    db_session.add(
        Chunk(
            id="c-w1",
            document_id="doc-w",
            chunk_type="endpoint",
            ref_id="ep-w1",
            text="[GET] /repos/{owner}/{repo}/topics — Get all repository topics",
            leaf_text="topics topic",
            intent_text="list index all browse Get all repository topics",
            context_text="repos repo owner",
        )
    )
    db_session.commit()

    tsv = db_session.execute(
        select(Chunk.search_tsv).where(Chunk.id == "c-w1")
    ).scalar_one()
    assert "'topics':1A" in tsv
    assert "'list':" in tsv and "B" in tsv
    assert "'repos':" in tsv


def test_search_tsv_is_null_for_non_endpoint_chunks(db_session) -> None:
    """section/schema 청크는 search_tsv 가 NULL 이라 부분 인덱스 비용이 0이다."""
    from sqlalchemy import select

    from app.models import Chunk

    _seed_document(db_session, "doc-w2")
    db_session.add(
        Chunk(
            id="c-w2",
            document_id="doc-w2",
            chunk_type="section",
            ref_id="sec-1",
            text="본문 텍스트",
        )
    )
    db_session.commit()

    assert (
        db_session.execute(select(Chunk.search_tsv).where(Chunk.id == "c-w2")).scalar_one()
        is None
    )


def test_search_tsv_lexemes_are_superset_of_text_tsv(db_session) -> None:
    """78번 §8.3 불변식: 후보 집합이 줄어드는 일은 구조적으로 불가능하다."""
    import re

    from sqlalchemy import select

    from app.models import Chunk

    _seed_document(db_session, "doc-w3")
    db_session.add(
        Chunk(
            id="c-w3",
            document_id="doc-w3",
            chunk_type="endpoint",
            ref_id="ep-w3",
            text="[DELETE] /v1/subscriptions/{id} — Cancel a subscription 구독 취소",
            leaf_text="subscriptions subscription",
            intent_text="delete remove destroy Cancel a subscription",
            context_text="v1",
        )
    )
    db_session.commit()

    row = db_session.execute(
        select(Chunk.text_tsv, Chunk.search_tsv).where(Chunk.id == "c-w3")
    ).one()
    lexemes = lambda tsv: set(re.findall(r"'([^']+)'", tsv or ""))
    assert lexemes(row[0]) <= lexemes(row[1])
```

- [ ] **Step 2: 실패 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_chunk_repository.py -q -k "search_tsv or normalization"
```
기대: `ImportError: cannot import name '_norm_sql'` / `TypeError: 'leaf_text' is an invalid keyword argument`

- [ ] **Step 3: 모델 구현**

`app/models/chunk.py` — `TEXT_TSV_EXPRESSION` 정의 **아래에** 추가(위 리터럴은 건드리지 않는다):

```python
def _norm_sql(column: str) -> str:
    """`TEXT_TSV_EXPRESSION` 이 `text` 에 쓰는 3단 정규화를 임의 컬럼에 적용한다.

    (1) ASCII/언더스코어→한글 경계 공백 삽입 (2) 한글→ASCII/언더스코어 경계
    공백 삽입 (3) 나머지 문자를 공백으로 치환. 질의 측 토크나이저
    (`keyword_search.tokenize_terms`)와 토큰 경계를 대칭으로 유지한다.
    """
    return (
        r"regexp_replace(regexp_replace(regexp_replace("
        + column
        + r", '([0-9A-Za-z_])([가-힣])', '\1 \2', 'g'), "
        r"'([가-힣])([0-9A-Za-z_])', '\1 \2', 'g'), "
        r"'[^0-9A-Za-z_가-힣]', ' ', 'g')"
    )


#: `chunk.search_tsv` 생성 컬럼식(`docs/architect-review/78` §5.1).
#: leaf(A) / intent(B) / context(C) / 기존 text(D) 를 `setweight` 로 묶는다.
#: D 가 기존 `text` 전체이므로 이 벡터의 lexeme 집합은 항상 `text_tsv` 의
#: 상위집합이다 — 이 변경으로 후보 집합이 줄어들 수 없다(78번 §8.3 불변식).
#: endpoint 가 아닌 청크는 NULL 이라 부분 GIN 인덱스 비용이 0이다.
SEARCH_TSV_EXPRESSION = (
    "CASE WHEN chunk_type = 'endpoint' THEN "
    f"setweight(to_tsvector('simple', {_norm_sql('leaf_text')}), 'A') || "
    f"setweight(to_tsvector('simple', {_norm_sql('intent_text')}), 'B') || "
    f"setweight(to_tsvector('simple', {_norm_sql('context_text')}), 'C') || "
    f"setweight(to_tsvector('simple', {_norm_sql('text')}), 'D') "
    "ELSE NULL END"
)
```

`Chunk.__table_args__` 에 인덱스 추가:

```python
        Index(
            "ix_chunk_search_tsv",
            "search_tsv",
            postgresql_using="gin",
            postgresql_where=text("chunk_type = 'endpoint'"),
        ),
```

(`from sqlalchemy import ... , text` 를 import 에 추가한다.)

`Chunk` 본문에서 `text` 컬럼 아래, `text_tsv` 위에 추가:

```python
    #: 가중치 A — target leaf 자원 토큰(`endpoint_structure.derive_endpoint_structure`).
    #: endpoint 가 아닌 청크는 빈 문자열이다.
    leaf_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: 가중치 B — operation alias + summary 원문.
    intent_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: 가중치 C — ancestor 경로·param 이름·tags·operationId subword.
    context_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
```

`text_tsv` 아래에 추가:

```python
    #: 엔드포인트 키워드 arm 용 가중 FTS 벡터(78번 설계). 필터 전용 — 일반
    #: 조회에서 select 하지 않는다. 식은 `SEARCH_TSV_EXPRESSION` 참조.
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(SEARCH_TSV_EXPRESSION, persisted=True),
        nullable=True,
        deferred=True,
    )
```

- [ ] **Step 4: 마이그레이션 작성**

`alembic/versions/c4d9e1f70a2b_add_endpoint_structure_lexical_fields.py`:

```python
"""add endpoint structure lexical fields and weighted search_tsv

Revision ID: c4d9e1f70a2b
Revises: b7e4a2c9d1f8
Create Date: 2026-08-28 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.chunk import SEARCH_TSV_EXPRESSION

# revision identifiers, used by Alembic.
revision: str = 'c4d9e1f70a2b'
down_revision: Union[str, Sequence[str], None] = 'b7e4a2c9d1f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """`chunk` 에 구조 신호 평문 컬럼 3개와 가중 생성 컬럼·부분 GIN 인덱스를 추가한다.

    `docs/architect-review/78` §5. 기존 행의 세 평문 컬럼은 빈 문자열로
    채워지고, 값은 `app/scripts/backfill_endpoint_structure.py` 가 넣는다.
    `text` 와 `embedding` 은 건드리지 않으므로 재임베딩이 필요 없다.
    """
    for column in ('leaf_text', 'intent_text', 'context_text'):
        op.add_column(
            'chunk',
            sa.Column(column, sa.Text(), nullable=False, server_default=''),
            schema='app',
        )
    op.add_column(
        'chunk',
        sa.Column(
            'search_tsv',
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_TSV_EXPRESSION, persisted=True),
            nullable=True,
        ),
        schema='app',
    )
    op.create_index(
        'ix_chunk_search_tsv',
        'chunk',
        ['search_tsv'],
        unique=False,
        schema='app',
        postgresql_using='gin',
        postgresql_where=sa.text("chunk_type = 'endpoint'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_chunk_search_tsv', table_name='chunk', schema='app')
    op.drop_column('chunk', 'search_tsv', schema='app')
    for column in ('context_text', 'intent_text', 'leaf_text'):
        op.drop_column('chunk', column, schema='app')
```

- [ ] **Step 5: 통과 확인 + autogenerate 무diff 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run alembic upgrade head \
  && uv run --extra test pytest tests/unit/test_chunk_repository.py tests/unit/test_alembic_env_metadata.py -q \
  && uv run alembic check
```
기대: 마이그레이션 성공, 테스트 PASS, `alembic check`가 "No new upgrade operations detected"
(`alembic check`가 이 버전에서 없으면 `uv run alembic revision --autogenerate -m tmp` 후
생성된 파일의 `upgrade()`가 비었는지 확인하고 **그 임시 파일은 삭제**한다).

- [ ] **Step 6: 커밋 경계 (lead 실행)**

`feat(model): add weighted search_tsv over endpoint structure fields`

---

## Task 4: 색인 서비스 배선

**Files:**
- Modify: `app/services/indexer/indexer_service.py` (`index_document` 의 `Chunk(...)` 생성부)
- Test: `tests/unit/test_indexer_service.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 2의 `BuiltChunk.leaf_text`/`intent_text`/`context_text`, Task 3의 `Chunk` 컬럼
- Produces: 색인된 endpoint 청크 행이 세 컬럼을 채운 상태

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_indexer_service.py` 끝에 추가:

```python
def test_indexed_endpoint_chunk_persists_structure_fields(db_session, sample_openapi_3) -> None:
    """색인 경로가 구조 신호 3필드를 DB 행에 그대로 넣는다(78번 §6)."""
    from sqlalchemy import select

    from app.models import Chunk
    from app.services.parser.openapi_parser import parse_document

    document = _seed_document(db_session)  # 이 파일의 기존 헬퍼를 쓴다
    parsed = parse_document(sample_openapi_3)
    _build_indexer(db_session).index_document(document, parsed)  # 기존 헬퍼
    db_session.commit()

    rows = db_session.execute(
        select(Chunk.leaf_text, Chunk.intent_text, Chunk.context_text).where(
            Chunk.chunk_type == "endpoint"
        )
    ).all()
    assert rows
    assert all(leaf for leaf, _intent, _context in rows)
    assert any("get" in intent for _leaf, intent, _context in rows)
```

> 이 파일에 `_seed_document` / `_build_indexer` 에 해당하는 헬퍼가 없으면 기존
> 테스트가 `IndexerService` 를 만들고 `Document` 를 넣는 방식을 그대로 복사해 쓴다.

- [ ] **Step 2: 실패 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_indexer_service.py -q -k structure
```
기대: 세 컬럼이 전부 빈 문자열이라 `assert all(leaf ...)` 실패.

- [ ] **Step 3: 구현**

`app/services/indexer/indexer_service.py` 의 `Chunk(...)` 생성부 교체:

```python
        for idx, (built, vector) in enumerate(zip(built_chunks, embeddings, strict=True)):
            chunk = Chunk(
                id=f"{document.id}:chunk:{idx}",
                document_id=document.id,
                chunk_type=built.chunk_type,
                ref_id=built.ref_id,
                text=built.text,
                leaf_text=built.leaf_text,
                intent_text=built.intent_text,
                context_text=built.context_text,
                embedding=vector,
            )
            self._chunk_repo.add(chunk)
```

- [ ] **Step 4: 통과 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_indexer_service.py -q
```

- [ ] **Step 5: 커밋 경계 (lead 실행)**

`feat(indexer): persist endpoint structure fields on index`

---

## Task 5: 저장소 `lexical_field` 분기

**Files:**
- Modify: `app/repositories/chunk_repository.py` (`search_endpoint_by_text`, 파일 상단 상수)
- Test: `tests/unit/test_chunk_repository.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 3의 `Chunk.search_tsv`
- Produces: `ChunkRepository.search_endpoint_by_text(..., lexical_field: str = "text")` —
  `"structured"` 면 `search_tsv` + 가중 `ts_rank`, 그 외 값은 전부 `text_tsv` + 무가중
  `ts_rank`(기존 동작, 미인식 값 안전 degrade)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_chunk_repository.py` 끝에 추가:

```python
def _seed_endpoint_chunk_with_structure(
    session, chunk_id: str, document_id: str, *, text: str, leaf: str, intent: str, context: str
) -> None:
    """구조 필드를 채운 endpoint 청크 한 건을 저장한다."""
    from app.models import Chunk

    _seed_document(session, document_id)
    session.add(
        Chunk(
            id=chunk_id,
            document_id=document_id,
            chunk_type="endpoint",
            ref_id=f"ep-{chunk_id}",
            text=text,
            leaf_text=leaf,
            intent_text=intent,
            context_text=context,
        )
    )


def test_structured_field_ranks_leaf_match_above_description_flood(db_session) -> None:
    """78번 §2: leaf 가 A 가중이면 설명 반복이 많은 형제를 이긴다."""
    repo = ChunkRepository(db_session)
    _seed_endpoint_chunk_with_structure(
        db_session,
        "c-target",
        "doc-s",
        text="[GET] /repos/{owner}/{repo}/topics — Get all repository topics",
        leaf="topics topic",
        intent="list index all browse Get all repository topics",
        context="repos repo owner",
    )
    _seed_endpoint_chunk_with_structure(
        db_session,
        "c-flood",
        "doc-s",
        text=(
            "[GET] /repos/{owner}/{repo}/collaborators — List repository collaborators. "
            "topics topics topics topics topics topics about the repository"
        ),
        leaf="collaborators collaborator",
        intent="list index all browse List repository collaborators",
        context="repos repo owner",
    )
    db_session.commit()

    flat = repo.search_endpoint_by_text(["topics"], top_k=5)
    weighted = repo.search_endpoint_by_text(["topics"], top_k=5, lexical_field="structured")

    assert flat[0].chunk_id == "c-flood"
    assert weighted[0].chunk_id == "c-target"


def test_lexical_field_defaults_and_unknown_values_use_text_tsv(db_session) -> None:
    """기본값과 미인식 값은 기존 `text_tsv` 경로로 안전하게 degrade 한다."""
    repo = ChunkRepository(db_session)
    _seed_endpoint_chunk_with_structure(
        db_session, "c-d1", "doc-d", text="find pet by id", leaf="pets pet", intent="", context=""
    )
    db_session.commit()

    default_hits = repo.search_endpoint_by_text(["pet"], top_k=5)
    unknown_hits = repo.search_endpoint_by_text(["pet"], top_k=5, lexical_field="nope")

    assert [h.chunk_id for h in default_hits] == ["c-d1"]
    assert [h.chunk_id for h in unknown_hits] == ["c-d1"]
    assert default_hits[0].score == unknown_hits[0].score


def test_structured_field_does_not_shrink_candidate_set(db_session) -> None:
    """78번 §8.3: D 가 `text` 전체이므로 flat 에서 잡히던 것은 전부 잡힌다."""
    repo = ChunkRepository(db_session)
    _seed_endpoint_chunk_with_structure(
        db_session,
        "c-only-desc",
        "doc-n",
        text="[POST] /v1/charges — Create a charge. 통화 단위를 지정한다",
        leaf="charges charge",
        intent="create add new register Create a charge",
        context="v1",
    )
    db_session.commit()

    flat = repo.search_endpoint_by_text(["통화"], top_k=5)
    weighted = repo.search_endpoint_by_text(["통화"], top_k=5, lexical_field="structured")

    assert {h.chunk_id for h in flat} == {"c-only-desc"}
    assert {h.chunk_id for h in weighted} == {"c-only-desc"}
```

- [ ] **Step 2: 실패 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_chunk_repository.py -q -k "lexical or structured"
```
기대: `TypeError: search_endpoint_by_text() got an unexpected keyword argument 'lexical_field'`

- [ ] **Step 3: 구현**

`app/repositories/chunk_repository.py` 상단 상수 영역에 추가:

```python
#: 구조화 lexical 필드용 `ts_rank` 가중치 배열 `{D, C, B, A}`. Postgres 기본값을
#: 그대로 쓰며 `docs/architect-review/78` §6.1 에서 상수로 동결했다 — 게이트
#: 결과를 보고 조정하지 않는다(조정은 verdict 74 가 반려한 과적합과 같은 경로).
_STRUCTURED_RANK_WEIGHTS = text("'{0.1, 0.2, 0.4, 1.0}'::float4[]")

#: `lexical_field` 로 구조화 벡터를 고르는 값. 그 외 모든 값은 기존 `text_tsv`.
_LEXICAL_FIELD_STRUCTURED = "structured"
```

(`from sqlalchemy import ..., text` 가 이미 없으면 import 에 추가한다.)

`search_endpoint_by_text` 시그니처에 인자 추가(맨 끝):

```python
        lexical_field: str = "text",
```

docstring 에 문단 추가:

```
        `lexical_field` 는 어느 lexical 벡터로 필터·채점할지 고른다.
        `"text"`(기본)는 기존 `text_tsv` + 무가중 `ts_rank` 로 현행 동작 그대로다.
        `"structured"` 는 `search_tsv`(leaf A / intent B / context C / text D) +
        `_STRUCTURED_RANK_WEIGHTS` 가중 `ts_rank` 를 쓴다
        (`docs/architect-review/78` §6). 미인식 값은 `"text"` 로 degrade 한다 —
        `search_strategy`/`document_search_strategy` 와 같은 롤백 스위치 규약이다.
```

`rank`/`stmt` 계산부 교체:

```python
        if lexical_field == _LEXICAL_FIELD_STRUCTURED:
            lexical_column = Chunk.search_tsv
            rank = func.ts_rank(_STRUCTURED_RANK_WEIGHTS, lexical_column, score_tsq)
        else:
            lexical_column = Chunk.text_tsv
            rank = func.ts_rank(lexical_column, score_tsq)
        stmt = (
            select(Chunk.id, Chunk.ref_id, Chunk.document_id, rank.label("score"))
            .where(Chunk.chunk_type == chunk_type)
            .where(lexical_column.op("@@", is_comparison=True)(tsq))
        )
```

- [ ] **Step 4: 통과 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_chunk_repository.py tests/unit/test_search_fts_regression.py -q && uv run ruff check app/repositories/chunk_repository.py
```

- [ ] **Step 5: 커밋 경계 (lead 실행)**

`feat(search): select weighted lexical vector via lexical_field`

---

## Task 6: 검색 배선 · 설정 스위치

**Files:**
- Modify: `app/services/search/keyword_search.py`
- Modify: `app/core/config.py`
- Modify: `app/composition.py`
- Test: `tests/unit/test_search_lexical_field_settings.py` (신규),
  `tests/unit/test_keyword_search.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 5의 `search_endpoint_by_text(..., lexical_field=...)`
- Produces:
  - `KeywordSearch(chunk_repo, lexical_field: str = "text")`
  - `Settings.search_lexical_field: str` (env `DOCS_MCP_SEARCH_LEXICAL_FIELD`, 기본 `"text"`)
  - `AppState.search_lexical_field: str = "text"` + `from_engine(..., search_lexical_field=None)`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_search_lexical_field_settings.py`:

```python
"""`DOCS_MCP_SEARCH_LEXICAL_FIELD` 설정 읽기 테스트(DB 불필요)."""

from __future__ import annotations

import pytest

from app.core.config import Settings

ENV_KEY = "DOCS_MCP_SEARCH_LEXICAL_FIELD"


def test_defaults_to_text_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """기본값은 text — 배포 즉시 동작이 바뀌지 않는다(78번 §6)."""
    monkeypatch.delenv(ENV_KEY, raising=False)

    assert Settings().search_lexical_field == "text"


def test_reads_structured_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 를 structured 로 두면 그 값을 그대로 읽는다."""
    monkeypatch.setenv(ENV_KEY, "structured")

    assert Settings().search_lexical_field == "structured"
```

`tests/unit/test_keyword_search.py` 끝에 추가:

```python
def test_keyword_search_passes_lexical_field_through(db_session) -> None:
    """`lexical_field="structured"` 는 구조화 벡터 경로로 라우팅된다."""
    _seed_document(db_session, "doc-l")
    db_session.add(
        Chunk(
            id="c-l1",
            document_id="doc-l",
            chunk_type="endpoint",
            ref_id="ep-l1",
            text="[GET] /v1/customers — List all customers",
            leaf_text="customers customer",
            intent_text="list index all browse List all customers",
            context_text="v1",
        )
    )
    db_session.commit()
    repo = ChunkRepository(db_session)

    structured = KeywordSearch(repo, lexical_field="structured").search("customer", top_k=5)
    flat = KeywordSearch(repo).search("customer", top_k=5)

    assert [h.ref_id for h in structured] == ["ep-l1"]
    assert [h.ref_id for h in flat] == []


def test_keyword_search_degrades_unknown_lexical_field(db_session) -> None:
    """미인식 값은 기존 text 경로로 degrade 한다."""
    _seed_chunk(db_session, "c-l2", "doc-l2", "find pet by id", ref_id="ep-l2")
    db_session.commit()
    repo = ChunkRepository(db_session)

    hits = KeywordSearch(repo, lexical_field="바보값").search("pet", top_k=5)

    assert [h.ref_id for h in hits] == ["ep-l2"]
```

- [ ] **Step 2: 실패 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_search_lexical_field_settings.py tests/unit/test_keyword_search.py -q
```
기대: `AttributeError: 'Settings' object has no attribute 'search_lexical_field'`,
`TypeError: KeywordSearch() got an unexpected keyword argument 'lexical_field'`

- [ ] **Step 3: 구현**

`app/core/config.py` — `document_search_strategy` 정의 아래에 추가:

```python
    #: "text"(기본, 기존 `chunk.text_tsv` 단일 필드 무가중 ts_rank — 롤백
    #: 스위치로 상시 보존) | "structured"(`chunk.search_tsv` 가중 A/B/C/D,
    #: `docs/architect-review/78`). search_endpoints 키워드 arm 전용이며
    #: 미인식 값은 안전하게 "text" 로 degrade 한다.
    search_lexical_field: str = field(
        default_factory=lambda: os.environ.get("DOCS_MCP_SEARCH_LEXICAL_FIELD", "text")
    )
```

`app/services/search/keyword_search.py`:

```python
class KeywordSearch:
    """`chunk` FTS 벡터로 endpoint 청크를 키워드 검색한다."""

    def __init__(self, chunk_repo: ChunkRepository, lexical_field: str = "text") -> None:
        """청크 저장소와 lexical 벡터 선택값을 보관한다.

        `lexical_field` 는 `"structured"` 일 때만 가중 `search_tsv` 경로를
        쓰고, 그 외 값은 전부 기존 `text_tsv` 로 degrade 한다
        (`docs/architect-review/78` §6, 롤백 스위치 규약).
        """
        self._chunk_repo = chunk_repo
        self._lexical_field = "structured" if lexical_field == "structured" else "text"
```

`search()` 의 저장소 호출에 인자 추가:

```python
        hits = self._chunk_repo.search_endpoint_by_text(
            filter_terms,
            top_k=top_k,
            document_id=document_id,
            project=project,
            score_terms=terms,
            lexical_field=self._lexical_field,
        )
```

`app/composition.py`:

- `AppState` 필드 추가 (`document_search_strategy` 아래):

```python
    #: "text"(기본, 기존 `text_tsv`) | "structured"(가중 `search_tsv`, 78번 설계).
    #: `KeywordSearch` 로 그대로 전달된다.
    search_lexical_field: str = "text"
```

- `from_engine` 시그니처에 `search_lexical_field: str | None = None,` 추가하고
  `cls(...)` 인자에 추가:

```python
            search_lexical_field=(
                settings.search_lexical_field
                if search_lexical_field is None
                else search_lexical_field
            ),
```

- `build_services` 안 `keyword_search` 생성부 교체:

```python
        keyword_search = KeywordSearch(chunk_repo, lexical_field=state.search_lexical_field)
```

- [ ] **Step 4: 통과 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_search_lexical_field_settings.py tests/unit/test_keyword_search.py tests/unit/test_endpoint_candidate_search.py -q && uv run ruff check app
```

- [ ] **Step 5: 커밋 경계 (lead 실행)**

`feat(search): add DOCS_MCP_SEARCH_LEXICAL_FIELD rollout switch`

---

## Task 7: write-back 경로 구조 필드 보존 회귀

**Files:**
- Test only: `tests/unit/test_endpoint_chunk_refresher.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 3~4 산출물, 기존 `refresh_endpoint_chunk` / `ChunkRepository.update_endpoint_chunk`
- Produces: 코드 변경 없음 — 회귀 가드만 추가

이 Task는 코드를 바꾸지 않는다. `update_endpoint_chunk` 는 `text` 와 `embedding` 만
쓰므로 세 구조 컬럼은 그대로 남아야 한다. **그 사실을 테스트로 못박는 것**이 산출물이다.
테스트가 실패하면 그때 `update_endpoint_chunk` 를 고친다.

- [ ] **Step 1: 테스트 작성**

`tests/unit/test_endpoint_chunk_refresher.py` 끝에 추가:

```python
def test_metadata_writeback_preserves_structure_fields(db_session) -> None:
    """78번 §5.4: metadata write-back 은 A/B/C 구조 컬럼을 비우지 않는다."""
    from sqlalchemy import select

    from app.models import Chunk
    from app.repositories.chunk_repository import ChunkRepository

    repo = ChunkRepository(db_session)
    _seed_document(db_session, "doc-wb")  # 이 파일의 기존 헬퍼
    db_session.add(
        Chunk(
            id="c-wb",
            document_id="doc-wb",
            chunk_type="endpoint",
            ref_id="ep-wb",
            text="원래 텍스트",
            leaf_text="topics topic",
            intent_text="list index all browse Get all repository topics",
            context_text="repos repo owner",
        )
    )
    db_session.commit()

    assert repo.update_endpoint_chunk(
        document_id="doc-wb", ref_id="ep-wb", text="갱신된 텍스트", embedding=[0.0] * 384
    )
    db_session.commit()

    row = db_session.execute(
        select(Chunk.text, Chunk.leaf_text, Chunk.intent_text, Chunk.context_text).where(
            Chunk.id == "c-wb"
        )
    ).one()
    assert row[0] == "갱신된 텍스트"
    assert row[1] == "topics topic"
    assert row[2] == "list index all browse Get all repository topics"
    assert row[3] == "repos repo owner"
```

> `_seed_document` 헬퍼가 이 파일에 없으면 `tests/unit/test_keyword_search.py` 의 것을
> 그대로 복사한다. 임베딩 차원 384는 `app/models/chunk.py` 의 `EMBEDDING_DIM` 이다 —
> 상수를 import 해서 `[0.0] * EMBEDDING_DIM` 로 쓰는 편이 낫다.

- [ ] **Step 2: 실행**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_endpoint_chunk_refresher.py -q
```
기대: PASS(코드 변경 불필요). FAIL 이면 `update_endpoint_chunk` 가 세 컬럼을 건드리는
것이므로 그 부분을 제거하고 다시 실행한다.

- [ ] **Step 3: 커밋 경계 (lead 실행)**

`test(indexer): guard structure fields across metadata write-back`

---

## Task 8: 백필 스크립트

**Files:**
- Create: `app/scripts/backfill_endpoint_structure.py`
- Test: `tests/unit/test_backfill_endpoint_structure.py`

**Interfaces:**
- Consumes: Task 1의 `derive_endpoint_structure`, Task 3의 `Chunk` 컬럼
- Produces:
  - `backfill_endpoint_structure(session_factory, *, document_id: str | None = None, batch_size: int = 500) -> int`
  - `main() -> None` (CLI: `--document-id`, `--batch-size`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_backfill_endpoint_structure.py`:

```python
"""엔드포인트 구조 신호 백필 스크립트 테스트(78번 §5.3)."""

from __future__ import annotations

from sqlalchemy import select

from app.models import ApiEndpoint, Chunk, Document
from app.scripts.backfill_endpoint_structure import backfill_endpoint_structure


def _seed(session) -> None:
    """문서 1건 + 엔드포인트 2건 + 대응 청크 2건 + section 청크 1건을 넣는다."""
    session.add(
        Document(
            id="doc-b",
            project="default",
            source_url=None,
            title="문서",
            content_hash="hash",
            raw_text="{}",
        )
    )
    session.flush()
    session.add_all(
        [
            ApiEndpoint(
                id="ep-root",
                document_id="doc-b",
                method="GET",
                path="/repos/{owner}/{repo}",
                operation_id="repos/get",
                summary="Get a repository",
                description="",
                tags_json='["repos"]',
            ),
            ApiEndpoint(
                id="ep-child",
                document_id="doc-b",
                method="GET",
                path="/repos/{owner}/{repo}/topics",
                operation_id="repos/get-all-topics",
                summary="Get all repository topics",
                description="",
                tags_json='["repos"]',
            ),
        ]
    )
    session.add_all(
        [
            Chunk(
                id="c-root",
                document_id="doc-b",
                chunk_type="endpoint",
                ref_id="ep-root",
                text="root text",
            ),
            Chunk(
                id="c-child",
                document_id="doc-b",
                chunk_type="endpoint",
                ref_id="ep-child",
                text="child text",
            ),
            Chunk(
                id="c-sec",
                document_id="doc-b",
                chunk_type="section",
                ref_id="sec-0",
                text="본문",
            ),
        ]
    )
    session.commit()


def test_backfill_fills_endpoint_structure_fields(db_session, session_factory) -> None:
    """endpoint 청크 3필드를 색인 경로와 같은 값으로 채운다."""
    _seed(db_session)

    updated = backfill_endpoint_structure(session_factory)

    db_session.expire_all()
    assert updated == 2
    root = db_session.get(Chunk, "c-root")
    child = db_session.get(Chunk, "c-child")
    assert root.leaf_text == "repos repo"
    assert root.intent_text == "get retrieve fetch read show detail Get a repository"
    assert root.context_text == "owner get"
    assert child.leaf_text == "topics topic"


def test_backfill_leaves_text_and_embedding_untouched(db_session, session_factory) -> None:
    """78번 §3.2 전제: 백필은 text/embedding 을 건드리지 않는다."""
    _seed(db_session)

    backfill_endpoint_structure(session_factory)

    db_session.expire_all()
    assert db_session.get(Chunk, "c-root").text == "root text"
    assert db_session.get(Chunk, "c-root").embedding is None


def test_backfill_skips_non_endpoint_chunks(db_session, session_factory) -> None:
    """section 청크는 대상이 아니다(생성 컬럼이 NULL 이어야 한다)."""
    _seed(db_session)

    backfill_endpoint_structure(session_factory)

    db_session.expire_all()
    section = db_session.get(Chunk, "c-sec")
    assert section.leaf_text == ""
    assert section.intent_text == ""
    assert section.context_text == ""


def test_backfill_is_idempotent(db_session, session_factory) -> None:
    """두 번 돌려도 같은 값이다(78번 §4.5 결정성)."""
    _seed(db_session)

    backfill_endpoint_structure(session_factory)
    db_session.expire_all()
    first = db_session.execute(
        select(Chunk.id, Chunk.leaf_text, Chunk.intent_text, Chunk.context_text)
        .where(Chunk.chunk_type == "endpoint")
        .order_by(Chunk.id)
    ).all()

    backfill_endpoint_structure(session_factory)
    db_session.expire_all()
    second = db_session.execute(
        select(Chunk.id, Chunk.leaf_text, Chunk.intent_text, Chunk.context_text)
        .where(Chunk.chunk_type == "endpoint")
        .order_by(Chunk.id)
    ).all()

    assert first == second


def test_backfill_scopes_to_document_id(db_session, session_factory) -> None:
    """`document_id` 를 주면 그 문서만 갱신한다."""
    _seed(db_session)

    assert backfill_endpoint_structure(session_factory, document_id="없는문서") == 0
    assert backfill_endpoint_structure(session_factory, document_id="doc-b") == 2
```

> `session_factory` 는 `tests/conftest.py` 가 이미 제공하는 픽스처다(`db_session` 이
> 이것으로 만들어진다).

- [ ] **Step 2: 실패 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_backfill_endpoint_structure.py -q
```
기대: `ModuleNotFoundError: No module named 'app.scripts.backfill_endpoint_structure'`

- [ ] **Step 3: 구현**

`app/scripts/backfill_endpoint_structure.py`:

```python
"""기존 색인의 엔드포인트 구조 신호 3필드를 채우는 배치.

alembic 마이그레이션(`c4d9e1f70a2b`)은 컬럼만 만들고 값은 빈 문자열로 둔다.
이 스크립트가 `api_endpoint` 의 method/path/summary/tags/operationId 로부터
`chunk.leaf_text`/`intent_text`/`context_text` 를 채운다.

`chunk.text` 와 `chunk.embedding` 은 건드리지 않는다 — 재임베딩 0 이
`docs/architect-review/78` §3.2 의 전제이고, 전체 재색인 대신 이 스크립트를
쓰는 이유이기도 하다(재색인은 verdict 70 이 기록한 `api_endpoint.id` 재해시
비결정성을 다시 건드린다).

실행 순서:
    uv run alembic upgrade head
    uv run python -m app.scripts.backfill_endpoint_structure
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.bootstrap import bootstrap_app_state
from app.models import ApiEndpoint, Chunk
from app.services.indexer.endpoint_structure import derive_endpoint_structure

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 500


def backfill_endpoint_structure(
    session_factory: sessionmaker[Session],
    *,
    document_id: str | None = None,
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> int:
    """endpoint 청크의 구조 신호 3필드를 다시 계산해 채운다.

    Args:
        session_factory: 배치마다 새 세션을 여는 팩토리.
        document_id: 주어지면 그 문서의 청크만 갱신한다.
        batch_size: 한 트랜잭션에서 갱신할 청크 수.

    Returns:
        갱신한 endpoint 청크 수. 참조하는 엔드포인트 행이 없는 청크는 건너뛴다.
    """
    with session_factory() as session:
        stmt = select(Chunk.id).where(Chunk.chunk_type == "endpoint")
        if document_id is not None:
            stmt = stmt.where(Chunk.document_id == document_id)
        chunk_ids = list(session.execute(stmt.order_by(Chunk.id)).scalars())

    total = 0
    for start in range(0, len(chunk_ids), batch_size):
        batch_ids = chunk_ids[start : start + batch_size]
        with session_factory() as session:
            chunks = list(
                session.execute(
                    select(Chunk).where(Chunk.id.in_(batch_ids)).order_by(Chunk.id)
                ).scalars()
            )
            endpoints = {
                endpoint.id: endpoint
                for endpoint in session.execute(
                    select(ApiEndpoint).where(
                        ApiEndpoint.id.in_({chunk.ref_id for chunk in chunks})
                    )
                ).scalars()
            }
            for chunk in chunks:
                endpoint = endpoints.get(chunk.ref_id)
                if endpoint is None:
                    logger.warning("청크가 참조하는 엔드포인트 없음: %s", chunk.id)
                    continue
                structure = derive_endpoint_structure(
                    method=endpoint.method,
                    path=endpoint.path,
                    summary=endpoint.summary or "",
                    tags=endpoint.tags,
                    operation_id=endpoint.operation_id,
                )
                chunk.leaf_text = structure.leaf_text
                chunk.intent_text = structure.intent_text
                chunk.context_text = structure.context_text
                total += 1
            session.commit()
        logger.info("구조 신호 백필 진행: %d/%d", total, len(chunk_ids))

    logger.info("구조 신호 백필 완료: 총 %d개 청크", total)
    return total


def _parse_args() -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", default=None, help="이 문서의 청크만 갱신한다.")
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH_SIZE)
    return parser.parse_args()


def main() -> None:
    """설정을 로드해 AppState 를 구성하고 구조 신호를 백필한다."""
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    state = bootstrap_app_state()
    backfill_endpoint_structure(
        state.session_factory, document_id=args.document_id, batch_size=args.batch_size
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 통과 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest tests/unit/test_backfill_endpoint_structure.py -q && uv run ruff check app/scripts/backfill_endpoint_structure.py
```

- [ ] **Step 5: 전체 회귀 + 타입 확인**

```bash
cd /home/kang/projects/docs-mcp && uv run --extra test pytest -m 'not slow' -q && uv run ruff check . && uv run --group dev mypy app
```
기대: 전건 PASS. `mypy` 실패 항목은 이번 변경분에 한해 고치고, 기존부터 있던 오류는
architect에 보고한다(사전 상태와 구분해서 보고할 것).

- [ ] **Step 6: 커밋 경계 (lead 실행)**

`feat(scripts): backfill endpoint structure fields without re-embedding`

---

## Task 9: 평가 러너 lexical-field 축

**Files:**
- Modify: `tests/fixtures/corpus_eval/run_corpus_eval.py` (`_parse_args`, `_evaluate_and_report`)

**Interfaces:**
- Consumes: Task 6의 `AppState.search_lexical_field`
- Produces: `--lexical-field {text,structured}` (기본 `text`) CLI 옵션. 지정값이
  `state.search_lexical_field` 로 들어가 `build_services` 가 그 값으로 `KeywordSearch` 를 만든다

- [ ] **Step 1: 인자 추가**

`_parse_args()` 의 `--with-variants` 위에 추가:

```python
    parser.add_argument(
        "--lexical-field",
        choices=("text", "structured"),
        default="text",
        help="키워드 arm 이 쓸 lexical 벡터. text=현행 chunk.text_tsv(baseline), "
        "structured=가중 chunk.search_tsv(78번 설계 candidate). 같은 공유 인덱스 위에서 "
        "이 값만 바꿔 baseline/candidate 를 비교한다(78번 §8.1).",
    )
```

- [ ] **Step 2: 상태 반영**

`_evaluate_and_report()` 의 전략 루프에서 `state.search_strategy = strategy` 바로 아래에 추가:

```python
        state.search_lexical_field = args.lexical_field
```

그리고 fingerprint 출력부(`print(f"- fixture commit: {_fixture_commit()}")` 아래)에 추가:

```python
    print(f"- lexical field: {args.lexical_field}")
```

> `_print_shared_index_fingerprint` 가 `args` 를 받지 않으면 `_evaluate_and_report` 의
> 리포트 헤더 쪽에 한 줄 출력하는 것으로 대신한다 — 중요한 것은 **모든 실행 기록에
> 어느 lexical field 로 돌렸는지가 남는 것**이다.

- [ ] **Step 3: 동작 확인 (색인 없이 인자만)**

```bash
cd /home/kang/projects/docs-mcp && uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --help | grep -A3 lexical-field
```
기대: 옵션이 출력된다.

- [ ] **Step 4: 커밋 경계 (lead 실행)**

`test(eval): add lexical-field axis to corpus eval runner`

---

## Task 10: ADR-0005 저작 · ADR-0002 후속 영향

**Files:**
- Create: `docs/adr/0005_weighted_endpoint_lexical_index.md`
- Modify: `docs/adr/0002_pgvector_hybrid_search.md` (결과 섹션에 1줄 추가)
- Modify: `docs/adr/README.md` (목록에 0005 추가 — 파일에 목록이 있으면)

**Interfaces:** 없음 (문서)

- [ ] **Step 1: ADR-0005 작성**

`docs/adr/0005_weighted_endpoint_lexical_index.md`:

```markdown
# ADR-0005: 엔드포인트 lexical 표현의 색인 시점 구조화와 가중 tsvector 채택

- 상태: accepted
- 일시: 2026-08-28
- 관련: `docs/architect-review/78_endpoint_index_structure_signal_design.md`,
  `docs/architect-review/74_p02_coverage_fix_failure_and_keyword_variant_stop_verdict.md`,
  ADR-0002

## 컨텍스트

엔드포인트 키워드 검색은 `chunk.text_tsv`(= `to_tsvector('simple', text)`) 단일 필드
위에서 `ts_rank` 로 채점한다. 이 표현에서는 target 자원을 지시하는 path leaf 토큰,
ancestor context 토큰, 300자로 잘린 설명 안의 우연한 반복이 **모두 같은 무게**를 갖는다.
그래서 짧고 정확한 정답 청크가 길고 부정확한 형제 청크의 term density 에 밀린다.

verdict 74 는 이 문제를 search-time 후처리(coverage 임계, 기여 예산, variant pool 억제)로
고치려던 네 가지 후보를 전부 반려했다. 평평한 표현이 구분하지 못하는 정보를 후처리로
복원할 수 없다는 것이 실 코퍼스 게이트의 결론이었다.

## 결정

엔드포인트 lexical 표현을 **색인 시점에 네 등급으로 구조화**하고, 가중 tsvector 로 채점한다.

1. `chunk` 에 결정적 파생 평문 컬럼 3개를 둔다 — `leaf_text`(A), `intent_text`(B),
   `context_text`(C). 값은 `method`·`path`·`summary`·`tags`·`operation_id` 에서만 만든다.
   LLM 생성물(`EndpointBusinessMetadata`)은 주입하지 않는다.
2. 그 셋과 기존 `text`(D)를 `setweight` 로 묶은 생성 컬럼 `search_tsv` 를 두고,
   `ts_rank('{0.1, 0.2, 0.4, 1.0}', search_tsv, tsquery)` 로 채점한다. 가중치 배열은
   Postgres 기본값 그대로 상수 고정하며 평가 결과를 보고 조정하지 않는다.
3. `method` × path shape(item/collection) → operation alias 표를 동결한다. 항목 추가·삭제는
   새 architect verdict 를 요구한다. 게이트에서 실패한 질의의 동사를 표에 더하는 것은
   verdict 74 가 반려한 과적합과 같은 경로다.
4. `text` 와 `embedding` 은 바꾸지 않는다. 따라서 재임베딩이 없고 벡터 arm 은 비트
   단위로 불변이며, 기존 색인 반영은 재색인이 아니라 백필 스크립트로 한다.
5. 롤백은 `DOCS_MCP_SEARCH_LEXICAL_FIELD` 설정 하나로 한다(기본 `text` = 현행 동작).
   기존 `text_tsv` 컬럼과 인덱스는 존치하며, 협업 문서(`chunk_type="section"`) 검색
   경로는 계속 그것을 쓴다.

## 결과

- 장점: term density 역전을 Postgres 내장 기능으로 직접 교정한다. 벡터 arm 이 불변이라
  순위 변화가 lexical arm 에 귀속되고, 같은 공유 인덱스 위에서 컬럼만 바꿔
  baseline/candidate 를 비교할 수 있다. 재임베딩 비용 0, 롤백은 설정 한 줄.
- 단점: `chunk` 테이블 rewrite 1회와 엔드포인트 행에 대한 두 번째 GIN 인덱스가 든다.
  lexical 표현이 두 벌(`text_tsv`/`search_tsv`)이 되어 승급 확정까지 공존한다.
- 한계: 한글 전용 질의는 여전히 이 경로로 풀리지 않는다. 영문 OpenAPI 원문에 한글
  원천이 없어 결정적 생성 계약을 지키면서 한글 신호를 만들 수 없다 — 클라이언트가
  넘기는 `query_variants` 와 벡터 arm 이 그 역할을 계속 맡는다.
- 후속 영향: `app/services/indexer/endpoint_structure.py`(신규),
  `app/models/chunk.py`, `app/repositories/chunk_repository.py`,
  `app/services/search/keyword_search.py`, `app/scripts/backfill_endpoint_structure.py`.
- ADR-0003(MCP 읽기 전용 경계)은 **개정 대상이 아니다.** 이 결정은 상류 API 를 호출하지
  않고 MCP 도구 표면도 바꾸지 않는다(78번 §9.1).
```

- [ ] **Step 2: ADR-0002 에 후속 영향 1줄 추가**

`docs/adr/0002_pgvector_hybrid_search.md` 의 "결과" 목록 끝에 추가:

```markdown
- **후속 결정(ADR-0005)**: 엔드포인트 keyword arm 의 lexical 표현을 단일
  `to_tsvector('simple', text)` 에서 색인 시점 구조화 + 가중 `setweight` 다중 필드로
  옮겼다. 하이브리드 구성(FTS × 벡터 RRF) 자체는 그대로다.
  `docs/adr/0005_weighted_endpoint_lexical_index.md` 참고.
```

- [ ] **Step 3: 확인**

```bash
cd /home/kang/projects/docs-mcp && grep -rn "0005" docs/adr/ | head
```

- [ ] **Step 4: 커밋 경계 (lead 실행)**

`docs(adr): record weighted endpoint lexical index decision`

---

## Task 11: p02 개발 회귀 게이트 실행

**Files:**
- 코드 변경 없음. 산출물: `docs/eval-results/06_2026-XX-XX_structured_lexical_p02_gate.md`

**Interfaces:**
- Consumes: Task 1~9 전부
- Produces: p02 root/child capped rank 표 + PASS/FAIL 판정

**이 Task 가 이 트랙의 게이트다.** 78번 §8.2: p02 를 통과하지 못하면 그 자리에서
멈추고 aggregate 지표를 근거로 진행하지 않는다.

- [ ] **Step 1: 공유 인덱스 1개 생성**

```bash
cd /home/kang/projects/docs-mcp && uv run alembic upgrade head
uv run python tests/fixtures/corpus_eval/run_corpus_eval.py \
  --mode preflight --queries-file tests/fixtures/corpus_eval/queries_gate_v1.json
```
출력된 `--db-url` 과 shared-index fingerprint 를 기록한다.

- [ ] **Step 2: 구조 필드 백필**

```bash
cd /home/kang/projects/docs-mcp && DOCS_MCP_DATABASE_URL='<preflight 가 출력한 db-url>' \
  uv run python -m app.scripts.backfill_endpoint_structure
```
기대 로그: `구조 신호 백필 완료: 총 1809개 청크`(github 1220 + stripe 589).
수가 다르면 멈추고 architect에 보고한다.

- [ ] **Step 3: 벡터 arm 불변 확인 (78번 §8.3)**

```bash
cd /home/kang/projects/docs-mcp && psql '<db-url>' -Atc "
SELECT md5(string_agg(id || ':' || text, E'\n' ORDER BY id)) FROM app.chunk;
SELECT count(*) FROM app.chunk WHERE chunk_type='endpoint' AND embedding IS NULL;
SELECT count(*) FROM app.chunk WHERE chunk_type='endpoint' AND leaf_text = '';
SELECT count(*) FROM app.chunk WHERE chunk_type<>'endpoint' AND search_tsv IS NOT NULL;
"
```
기대: 두 번째·세 번째·네 번째 값이 모두 `0`. 첫 번째 해시는 결과 문서에 기록한다
(백필 전후 비교용 — Step 2 직전에 같은 쿼리를 한 번 돌려 두 값이 같은지 확인한다).

- [ ] **Step 4: baseline / candidate 4회 실행**

```bash
cd /home/kang/projects/docs-mcp
for FIELD in text structured; do
  for VARIANTS in "" "--with-variants"; do
    uv run python tests/fixtures/corpus_eval/run_corpus_eval.py \
      --mode eval --db-url '<db-url>' \
      --queries-file tests/fixtures/corpus_eval/queries_gate_v1.json \
      --split all --strategy rrf --top-k 10 \
      --lexical-field "$FIELD" $VARIANTS
  done
done
```

- [ ] **Step 5: p02 판정**

각 실행에서 `g003`(root, `GET /repos/{owner}/{repo}`)과 `g004`(child,
`GET /repos/{owner}/{repo}/topics`)의 순위를 뽑는다. 미검출은 11로 cap 한다.

```text
delta(q) = rank_structured(q) - rank_text(q)
PASS  <=>  OFF/ON 각각에서 delta(root) <= 0 이고 delta(child) <= 0
```

- [ ] **Step 6: 결과 문서 작성**

`docs/eval-results/` 의 다음 순번(현재 최대 `05` → `06`)으로
`06_<날짜>_structured_lexical_p02_gate.md` 를 만든다. `docs/eval-results/README.md`
규약대로 **실제 실행 산출물만** 붙인다(추정치·손계산 금지). 최소 포함 항목:

- 실행 커밋 SHA 또는 미커밋 워킹트리 상태와 그 source-state SHA-256
- shared-index fingerprint, query SHA-256, corpus SHA-256
- Step 3 의 4개 무결성 쿼리 결과
- OFF/ON × text/structured 4행의 g003·g004 capped rank 와 delta
- PASS/FAIL 판정 한 줄

- [ ] **Step 7: architect 보고**

```bash
say architect "[developer] 78번 구조 신호 p02 게이트 {PASS|FAIL}. root delta OFF/ON={..}/{..}, child delta OFF/ON={..}/{..}. docs/eval-results/06_....md"
```

FAIL 이면 **여기서 멈춘다.** 가중치·alias 표를 조정해 재시도하지 않는다.

---

## Task 12: v1 exposed regression 전량 실행

**전제:** Task 11 PASS.

**Files:**
- 코드 변경 없음. 산출물: `docs/eval-results/07_2026-XX-XX_structured_lexical_v1_gate.md`

- [ ] **Step 1: 같은 공유 인덱스에서 HARD 항목 실행**

Task 11 Step 4 의 4회 실행 출력을 그대로 쓴다(재실행 불필요). 추가로 결정성 검증:

```bash
cd /home/kang/projects/docs-mcp && uv run python tests/fixtures/corpus_eval/run_corpus_eval.py \
  --mode determinism --db-url '<db-url>' \
  --queries-file tests/fixtures/corpus_eval/queries_gate_v1.json \
  --split all --lexical-field structured
```

- [ ] **Step 2: HARD 판정표 채우기 (69번 §7.1 전항)**

| 항목 | 확인 방법 |
|---|---|
| 프리즈 무결성 | query/corpus SHA 가 `gate_manifest_v1.json` 값과 일치, 자동 검증 오류 0 |
| 실행 동등성 | 두 실행의 fixture commit·DB URL·인덱스 지문 동일 |
| fallback control | `--strategy fallback` OFF/ON 의 per-query capped rank 가 text/structured 간 동일한지 확인 |
| C1 exact/direct control | C1 카테고리 top-10 hit loss 0 |
| category 회귀 | C1~C7 각각 R@10 hit 순감소 최대 1건, MRR 하락 최대 0.02 |
| C6 all-of | coverage@10·complete@10 이 baseline 이상 |
| route pair | **gate 10쌍 / holdout 2쌍 / 전체 12쌍 각각** 69번 §3.4 산식으로 non-regression 판정 |
| empty result | OFF/ON empty-result count 가 baseline 대비 증가 없음 |
| sealed holdout | OFF/ON 각각 R@10 baseline 이상, MRR 하락 0.01 이하 |
| 추가 불변식 | 78번 §8.3 네 항목(lexeme 상위집합·벡터 arm 불변·파생 결정성·문서 검색 무변경) |

**route pair 는 split 마다 빠짐없이 계산한다.** aggregate 지표는 HARD 전항 통과 후에만 읽는다.

- [ ] **Step 3: EFFECTIVENESS 기록 (판정 아님)**

HARD 통과 시 69번 §7.2 항목을 표로 기록한다. v1 은 노출된 개발 코퍼스이므로
**이 수치로 승급하지 않는다**(verdict 74 §6.2). 승급 판단은 v2 프리즈 이후다.

- [ ] **Step 4: 결과 문서 + 보고**

`docs/eval-results/07_<날짜>_structured_lexical_v1_gate.md` 작성 후:

```bash
say architect "[developer] 78번 구조 신호 v1 exposed regression 완료. HARD {n}/{m} PASS. docs/eval-results/07_....md"
```

- [ ] **Step 5: 인덱스 정리**

```bash
cd /home/kang/projects/docs-mcp && uv run python tests/fixtures/corpus_eval/run_corpus_eval.py \
  --mode cleanup --db-url '<db-url>'
```

---

## 범위 밖 (이 계획에서 하지 않는 것)

verdict 74 §6.3 과 78번 §11 을 그대로 승계한다.

- **v2 프리즈 저작 착수 금지.** Task 11·12 통과 후 architect 설계로 별도 착수한다.
- 약어 사전(`repos` → `repository`), path specificity / route-family rerank 재도입,
  구조 텍스트의 임베딩 반영, `ts_rank_cd` 전환, `RRF_K`·RRF arm 가중치 변경,
  `text_tsv` 통합·삭제, 한글 alias.
- `queries_gate_v1.json` / `gate_manifest_v1.json` 의 질의·라벨·variant·pair 수정.
- `OPERATION_ALIASES` 표와 `_STRUCTURED_RANK_WEIGHTS` 조정.

## 자기 점검 (architect)

- **스펙 커버리지**: 78번 §4.1~4.5 → Task 1, §4.3 필드 배치 → Task 2·3,
  §5.1~5.2 → Task 3, §5.3 → Task 8, §5.4 → Task 7, §6 코드 지점 10개 →
  Task 2·3·4·5·6·8·9, §6.1 가중치 → Task 5, §8.1 단일 인덱스 이중 컬럼 → Task 9·11,
  §8.2 실행 순서 → Task 11·12, §8.3 불변식 → Task 3·11·12, §8.4 단위 테스트 계약 →
  Task 1·3·5·6·7, §9 ADR → Task 10, §11 비범위 → "범위 밖" 절. 누락 없음.
- **타입 일관성**: `derive_endpoint_structure`(Task 1) → `BuiltChunk`(Task 2) →
  `Chunk` 컬럼(Task 3) → `IndexerService`(Task 4) / 백필(Task 8) 로 필드명
  `leaf_text`/`intent_text`/`context_text` 가 끝까지 동일. `lexical_field` 문자열
  `"text"`/`"structured"` 가 저장소(Task 5)·검색기(Task 6)·설정(Task 6)·러너(Task 9)에서 동일.
- **78번 §4.3 예시와 Task 1 테스트 기대값 일치**: 설계 문서의 두 예시를 실제 파생
  결과와 맞춰 갱신했다(operationId·tags 기여 포함).
