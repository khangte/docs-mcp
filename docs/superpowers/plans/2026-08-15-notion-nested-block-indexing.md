# Notion Nested Block 색인 갭 보강 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notion 페이지의 하위 데이터베이스 행·하위 페이지·본문 텍스트(표/속성 포함)가 모두 독립 문서 또는 본문으로 색인되게 해, docs-mcp 자체 검색만으로 Notion 검색 수준을 재현한다.

**Architecture:** 검색 로직(title+FTS+vector 3-arm RRF)은 이미 요구를 만족하므로 건드리지 않는다. 변경은 전부 수집 단계(`NotionSource`)에 있다 — (a) 블록 평문 추출을 타입별로 넓히고 markdown 헤딩 마커를 복원해 `markdown_parser` 가 섹션을 실제로 쪼개게 하고, (b) 하위 페이지/DB 행 목록화 커버리지를 넓히고, (c) 본문 재귀와 목록화의 역할을 분리해 중복 색인을 없앤다. 마지막으로 `refresh_index` 의 `index_bodies` 기본값을 `True` 로 전환해 기본 검색 전략(`indexed`)과 정합을 맞춘다.

**Tech Stack:** Python 3.11+, httpx (Notion REST API v1), SQLAlchemy, pytest + `httpx.MockTransport`, uv

**Spec:** `docs/architect-review/50_notion_nested_block_indexing_gap_and_design.md`

## Global Constraints

- 타입 힌트 필수. Python 함수·클래스에 **한국어** docstring 필수.
- `print()` 금지 — `app.core.logging.get_logger` 사용.
- 한자(CJK Unified Ideographs) 사용 금지. "분석"은 U+BD84 U+C11D 로만 쓴다.
- 파일·폴더·함수는 `snake_case`, 클래스는 `PascalCase`.
- 파일 경로는 문서에서 백틱으로 감싼다.
- 테스트 실행은 `uv run pytest`. 커버리지 최소 80% 유지.
- 외부 API 호출은 테스트에서 실제로 나가면 안 된다 — `httpx.MockTransport` 로만 검증한다.
- Notion API 버전은 `DOCS_MCP_NOTION_VERSION` 기본값 `2022-06-28` 을 유지한다(업그레이드는 `docs/architect-review/34` 별건).
- 커밋 메시지는 `<type>: <설명>` (feat/fix/refactor/docs/test/chore).
- **developer 는 커밋만 하고 push 하지 않는다.** 커밋 자체도 lead 지시가 있을 때만 — 기본은 워킹트리에 두고 보고한다(`.team` 규칙). 아래 각 Task 의 커밋 스텝은 lead 가 커밋을 지시한 경우의 경계 표시로 읽는다.

## 변경 대상 파일 구조

| 파일 | 역할 | 상태 |
|---|---|---|
| `app/services/documents/sources/notion_blocks.py` | Notion 응답 → 평문/`FileMeta` **순수 변환** 함수 모음. HTTP 를 모른다. | 신규(Task 1 에서 기존 코드 이동) |
| `app/services/documents/sources/notion_source.py` | Notion REST 호출·페이지네이션·트리 순회 어댑터 | 수정 |
| `app/mcp/tools/sources.py` | `refresh_index` MCP 도구 시그니처 | 수정(Task 8) |
| `app/scripts/refresh_documents.py` | 배치 갱신 CLI 인자 | 수정(Task 8) |
| `tests/unit/test_notion_blocks.py` | 순수 변환 함수 단위 테스트(HTTP mock 불필요) | 신규 |
| `tests/unit/test_notion_page_source.py` | 목록화/본문 순회 테스트(기존 파일에 추가) | 수정 |
| `tests/unit/test_refresh_documents_script.py` | CLI 기본값 테스트 | 수정(Task 8) |

분리 근거: `notion_source.py` 는 현재 362줄이고 이 계획으로 순수 변환 로직이 약 2배가 된다. HTTP 어댑터와 순수 변환을 갈라놓으면 Task 2·3·7 의 테스트가 `MockTransport` 없이 값 비교만으로 끝난다.

---

### Task 1: 순수 변환 함수를 `notion_blocks.py` 로 이동 (동작 변경 없음)

기존 동작을 **한 글자도 바꾸지 않는** 순수 이동이다. 이후 Task 2·3·7 이 이 모듈만 건드린다.

**Files:**
- Create: `app/services/documents/sources/notion_blocks.py`
- Modify: `app/services/documents/sources/notion_source.py` (모듈 하단 `_rich_text_to_plain`/`_block_plain_text`/`_page_title`/`_child_page_to_file_meta`/`_to_file_meta` 삭제 + `UNTITLED` 이동, 상단에 import 추가, 호출부 5곳 이름 변경)
- Test: `tests/unit/test_notion_blocks.py` (신규)

**Interfaces:**
- Consumes: `app.services.documents.sources.document_source.FileMeta`, `app.services.documents.sources.time_parsing.parse_rfc3339`
- Produces (이후 모든 Task 가 이 이름을 쓴다):
  - `UNTITLED: str = "(제목 없음)"`
  - `rich_text_to_plain(items: Any) -> str`
  - `block_plain_text(block: dict[str, Any]) -> str`
  - `page_title(page: dict[str, Any]) -> str`
  - `child_page_to_file_meta(block: dict[str, Any]) -> FileMeta`
  - `to_file_meta(page: dict[str, Any]) -> FileMeta`

- [ ] **Step 1: 회귀 테스트를 먼저 쓴다(이동 전 동작 고정)**

`tests/unit/test_notion_blocks.py` 신규 생성:

```python
"""Notion 응답 → 평문/FileMeta 순수 변환 함수 테스트.

HTTP 를 타지 않는 순수 함수만 다룬다(어댑터 순회 테스트는
`tests/unit/test_notion_page_source.py`).
"""

from __future__ import annotations

from app.services.documents.sources.notion_blocks import (
    UNTITLED,
    block_plain_text,
    child_page_to_file_meta,
    page_title,
    rich_text_to_plain,
    to_file_meta,
)


def test_rich_text_to_plain_joins_plain_text_parts() -> None:
    """rich_text 배열의 plain_text 조각들이 순서대로 이어 붙는다."""
    items = [{"plain_text": "트러블"}, {"plain_text": "슈팅"}]

    assert rich_text_to_plain(items) == "트러블슈팅"


def test_rich_text_to_plain_returns_empty_for_non_list() -> None:
    """리스트가 아니면 빈 문자열을 돌려준다."""
    assert rich_text_to_plain(None) == ""


def test_block_plain_text_extracts_paragraph() -> None:
    """paragraph 블록의 rich_text 가 평문으로 나온다."""
    block = {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "본문 한 줄"}]}}

    assert block_plain_text(block) == "본문 한 줄"


def test_page_title_falls_back_to_untitled() -> None:
    """title 타입 속성이 없으면 UNTITLED 를 돌려준다."""
    assert page_title({"properties": {}}) == UNTITLED


def test_to_file_meta_uses_page_url_when_present() -> None:
    """페이지 응답의 url 이 있으면 그대로 쓴다."""
    page = {
        "id": "page-1",
        "url": "https://www.notion.so/team/page-1",
        "properties": {"이름": {"type": "title", "title": [{"plain_text": "장애 기록"}]}},
        "last_edited_time": "2026-07-03T00:00:00.000Z",
    }

    meta = to_file_meta(page)

    assert meta.external_id == "page-1"
    assert meta.title == "장애 기록"
    assert meta.url == "https://www.notion.so/team/page-1"


def test_child_page_to_file_meta_uses_block_id_as_page_id() -> None:
    """child_page 블록의 id 가 곧 하위 페이지의 page id 다."""
    block = {
        "id": "abc-def",
        "type": "child_page",
        "child_page": {"title": "하위 문서"},
        "last_edited_time": "2026-07-04T00:00:00.000Z",
    }

    meta = child_page_to_file_meta(block)

    assert meta.external_id == "abc-def"
    assert meta.title == "하위 문서"
    assert meta.url == "https://www.notion.so/abcdef"
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `uv run pytest tests/unit/test_notion_blocks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.documents.sources.notion_blocks'`

- [ ] **Step 3: `notion_blocks.py` 생성 (기존 함수를 그대로 옮기고 이름의 선행 밑줄만 제거)**

`app/services/documents/sources/notion_blocks.py`:

```python
"""Notion API 응답 → 평문/`FileMeta` 순수 변환 함수 모음.

HTTP 호출을 모른다 — `notion_source.NotionSource` 가 받아온 dict 를 넣으면
평문 텍스트나 `FileMeta` 가 나온다. 어댑터에서 분리해 둔 덕에 이 모듈의
테스트는 `httpx.MockTransport` 없이 값 비교만으로 끝난다.
"""

from __future__ import annotations

from typing import Any

from app.services.documents.sources.document_source import FileMeta
from app.services.documents.sources.time_parsing import parse_rfc3339

UNTITLED = "(제목 없음)"


def rich_text_to_plain(items: Any) -> str:
    """Notion rich_text 배열을 평문으로 이어 붙인다."""
    if not isinstance(items, list):
        return ""
    parts = [
        str(item.get("plain_text") or "")
        for item in items
        if isinstance(item, dict)
    ]
    return "".join(parts).strip()


def block_plain_text(block: dict[str, Any]) -> str:
    """블록 한 개에서 평문 텍스트를 추출한다(rich_text 를 갖는 모든 타입 지원)."""
    block_type = str(block.get("type") or "")
    payload = block.get(block_type)
    if not isinstance(payload, dict):
        return ""
    return rich_text_to_plain(payload.get("rich_text"))


def page_title(page: dict[str, Any]) -> str:
    """페이지 properties 에서 title 타입 속성을 찾아 제목을 만든다."""
    properties = page.get("properties")
    if isinstance(properties, dict):
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                title = rich_text_to_plain(prop.get("title"))
                if title:
                    return title
    return UNTITLED


def child_page_to_file_meta(block: dict[str, Any]) -> FileMeta:
    """`child_page` 타입 블록 하나를 FileMeta 로 변환한다.

    블록 자체의 id 가 하위 페이지의 page id 다(그대로 fetch 대상 external_id).
    """
    block_id = str(block.get("id") or "")
    child_page = block.get("child_page")
    title = UNTITLED
    if isinstance(child_page, dict):
        title = str(child_page.get("title") or "") or UNTITLED
    return FileMeta(
        external_id=block_id,
        title=title,
        url=f"https://www.notion.so/{block_id.replace('-', '')}",
        modified_at=parse_rfc3339(block.get("last_edited_time")),
    )


def to_file_meta(page: dict[str, Any]) -> FileMeta:
    """Notion 페이지 응답 항목 하나를 FileMeta 로 변환한다."""
    page_id = str(page.get("id") or "")
    return FileMeta(
        external_id=page_id,
        title=page_title(page),
        url=str(page.get("url") or f"https://www.notion.so/{page_id.replace('-', '')}"),
        modified_at=parse_rfc3339(page.get("last_edited_time")),
    )
```

- [ ] **Step 4: `notion_source.py` 에서 옮긴 함수들을 삭제하고 import 로 대체**

`app/services/documents/sources/notion_source.py`:

1. import 블록에 추가 (기존 `from app.services.documents.sources.document_source import ...` 아래). `notion_source.py` 가 **직접 호출하는 3개만** 가져온다 — `UNTITLED`/`page_title`/`rich_text_to_plain` 은 `notion_blocks.py` 내부에서만 쓰이므로 여기서 import 하면 ruff 가 `F401` 로 잡는다:

```python
from app.services.documents.sources.notion_blocks import (
    block_plain_text,
    child_page_to_file_meta,
    to_file_meta,
)
```

2. 파일 하단의 `_rich_text_to_plain`, `_block_plain_text`, `_page_title`, `_child_page_to_file_meta`, `_to_file_meta` 정의 5개를 **삭제**한다(`_notion_error_message` 는 HTTP 응답을 다루므로 `notion_source.py` 에 남긴다).
3. 모듈 상단 상수 `UNTITLED = "(제목 없음)"` 줄을 **삭제**한다(위 import 로 대체).
4. 호출부 이름을 바꾼다:
   - `list_pages()`: `_to_file_meta(page)` → `to_file_meta(page)`
   - `_collect_block_text()`: `_block_plain_text(block)` → `block_plain_text(block)`
   - `_collect_child_pages()`: `_child_page_to_file_meta(block)` → `child_page_to_file_meta(block)`, `_to_file_meta(row)` → `to_file_meta(row)`
5. `from typing import Any` 는 `_list_request_spec`/`_paginate` 등이 계속 쓰므로 유지한다.

> `UNTITLED`, `page_title`, `rich_text_to_plain` 은 `notion_source.py` 안에서 직접 쓰이지 않게 될 수 있다. 그 경우 import 목록에서 **빼라** — ruff 가 `F401` 로 잡는다. Step 6 의 lint 로 확인한다.

- [ ] **Step 5: 테스트 통과 확인 (신규 + 기존 회귀 전부)**

Run: `uv run pytest tests/unit/test_notion_blocks.py tests/unit/test_notion_page_source.py tests/unit/test_document_sources.py -v`
Expected: PASS (전부)

- [ ] **Step 6: lint 확인**

Run: `uv run ruff check app/services/documents/sources/`
Expected: `All checks passed!`

- [ ] **Step 7: 커밋**

```bash
git add app/services/documents/sources/notion_blocks.py app/services/documents/sources/notion_source.py tests/unit/test_notion_blocks.py
git commit -m "refactor: Notion 순수 변환 함수를 notion_blocks 모듈로 분리"
```

---

### Task 2 (P0-2): 헤딩·리스트 마커 복원

**왜 먼저 하나:** 지금은 `heading_1` 이 평문만 남아 `markdown_parser.parse_document` 의 `^#{1,6}\s+` 정규식에 안 걸린다. 그래서 모든 Notion 문서가 "개요" 섹션 1개로 파싱되고, 청크 앵커가 전부 `# 개요` 다. 이 Task 뒤에야 나머지 변경의 효과를 섹션 단위로 관찰할 수 있다.

**Files:**
- Modify: `app/services/documents/sources/notion_blocks.py` (`block_plain_text` + 신규 `_block_prefix`, `_BLOCK_PREFIXES`)
- Test: `tests/unit/test_notion_blocks.py`

**Interfaces:**
- Consumes: Task 1 의 `block_plain_text(block) -> str`
- Produces: `block_plain_text` 의 반환값이 markdown 마커를 포함하게 된다. 시그니처는 그대로다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/unit/test_notion_blocks.py` 하단에 추가:

```python
def test_heading_blocks_get_markdown_prefix() -> None:
    """heading_1/2/3 은 markdown 헤딩 마커를 달고 나온다(파서가 섹션을 쪼갤 수 있게)."""
    h1 = {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "장애 대응"}]}}
    h2 = {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "트러블슈팅"}]}}
    h3 = {"type": "heading_3", "heading_3": {"rich_text": [{"plain_text": "원인"}]}}

    assert block_plain_text(h1) == "# 장애 대응"
    assert block_plain_text(h2) == "## 트러블슈팅"
    assert block_plain_text(h3) == "### 원인"


def test_list_blocks_get_markdown_prefix() -> None:
    """리스트/인용 블록도 markdown 마커를 단다."""
    bullet = {
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"plain_text": "재시도 로직 추가"}]},
    }
    numbered = {
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"plain_text": "로그 확인"}]},
    }
    quote = {"type": "quote", "quote": {"rich_text": [{"plain_text": "인용문"}]}}

    assert block_plain_text(bullet) == "- 재시도 로직 추가"
    assert block_plain_text(numbered) == "1. 로그 확인"
    assert block_plain_text(quote) == "> 인용문"


def test_to_do_prefix_reflects_checked_state() -> None:
    """to_do 는 체크 상태를 markdown 체크박스로 표현한다."""
    done = {"type": "to_do", "to_do": {"rich_text": [{"plain_text": "배포"}], "checked": True}}
    todo = {"type": "to_do", "to_do": {"rich_text": [{"plain_text": "롤백"}], "checked": False}}

    assert block_plain_text(done) == "- [x] 배포"
    assert block_plain_text(todo) == "- [ ] 롤백"


def test_empty_block_stays_empty_without_prefix() -> None:
    """텍스트가 없는 블록에는 마커만 남지 않는다(빈 문자열)."""
    empty_heading = {"type": "heading_2", "heading_2": {"rich_text": []}}

    assert block_plain_text(empty_heading) == ""
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_notion_blocks.py -k "prefix or empty_block" -v`
Expected: FAIL — `AssertionError: assert '장애 대응' == '# 장애 대응'`

- [ ] **Step 3: 구현**

`app/services/documents/sources/notion_blocks.py` 의 `UNTITLED` 아래에 상수를 추가하고 `block_plain_text` 를 교체한다:

```python
#: 블록 타입 → markdown 마커. Notion 평문에는 서식 정보가 없어서, 이 마커를
#: 되살려야 `markdown_parser.parse_document` 가 헤딩 단위로 섹션을 쪼갠다.
#: 마커가 없으면 문서 전체가 "개요" 섹션 1개가 되고 모든 청크 앵커가
#: `# 개요` 로 뭉개진다(`docs/architect-review/50` §2.4).
_BLOCK_PREFIXES: dict[str, str] = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "quote": "> ",
}


def _block_prefix(block_type: str, payload: dict[str, Any]) -> str:
    """블록 타입(과 to_do 의 체크 상태)에 대응하는 markdown 마커를 돌려준다."""
    if block_type == "to_do":
        return "- [x] " if payload.get("checked") else "- [ ] "
    return _BLOCK_PREFIXES.get(block_type, "")


def block_plain_text(block: dict[str, Any]) -> str:
    """블록 한 개에서 평문 텍스트를 추출한다(markdown 마커 포함).

    텍스트가 비면 마커만 남기지 않고 빈 문자열을 돌려준다 — 빈 헤딩 줄이
    파서에 섹션 경계로 잡히면 내용 없는 섹션이 생긴다.
    """
    block_type = str(block.get("type") or "")
    payload = block.get(block_type)
    if not isinstance(payload, dict):
        return ""
    text = rich_text_to_plain(payload.get("rich_text"))
    if not text:
        return ""
    return _block_prefix(block_type, payload) + text
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_notion_blocks.py -v`
Expected: PASS

- [ ] **Step 5: 기존 테스트 회귀 확인**

Run: `uv run pytest tests/unit -q`
Expected: PASS. 실패가 나면 그 테스트가 마커 없는 평문을 기대하고 있는 것이므로 기대값을 마커 포함으로 고친다(구현을 되돌리지 않는다).

- [ ] **Step 6: 커밋**

```bash
git add app/services/documents/sources/notion_blocks.py tests/unit/test_notion_blocks.py
git commit -m "feat: Notion 블록 평문에 markdown 헤딩/리스트 마커 복원"
```

> **재색인 영향:** 본문 텍스트가 바뀌므로 `index_document_body` 의 `content_hash` 가 달라져 다음 `refresh_index(index_bodies=True)` 에서 해당 문서들이 자동 재색인된다. 마이그레이션은 필요 없다.

---

### Task 3 (P0-1): 표·캡션·URL·하위 문서 제목까지 본문에 넣기

현재 `block_plain_text` 는 `rich_text` 한 경로만 본다. 그래서 **Notion 단순 표(`table_row.cells`)가 통째로 유실**된다 — 트러블슈팅 내역이 표로 적힌 경우 본문 검색이 0건이 되는 최대 구멍이다.

**Files:**
- Modify: `app/services/documents/sources/notion_blocks.py` (`block_plain_text` 확장 + 신규 `cells_to_plain`)
- Test: `tests/unit/test_notion_blocks.py`

**Interfaces:**
- Consumes: Task 2 의 `block_plain_text`, `_block_prefix`, `rich_text_to_plain`
- Produces: `cells_to_plain(cells: Any) -> str` (표 한 행을 `" | "` 로 이어 붙인 문자열)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/unit/test_notion_blocks.py` 상단 import 에 `cells_to_plain` 을 추가하고, 파일 하단에 다음을 추가:

```python
def test_table_row_cells_join_with_pipe() -> None:
    """표 한 행의 셀들이 ' | ' 로 이어진 한 줄이 된다."""
    block = {
        "type": "table_row",
        "table_row": {
            "cells": [
                [{"plain_text": "타임아웃"}],
                [{"plain_text": "커넥션 풀 고갈"}],
                [{"plain_text": "해결"}],
            ]
        },
    }

    assert block_plain_text(block) == "타임아웃 | 커넥션 풀 고갈 | 해결"


def test_cells_to_plain_skips_empty_cells() -> None:
    """빈 셀은 건너뛰고 구분자만 남기지 않는다."""
    assert cells_to_plain([[{"plain_text": "A"}], [], [{"plain_text": "B"}]]) == "A | B"


def test_cells_to_plain_returns_empty_for_non_list() -> None:
    """cells 가 리스트가 아니면 빈 문자열이다."""
    assert cells_to_plain(None) == ""


def test_image_caption_is_extracted() -> None:
    """이미지/파일 블록의 caption 도 본문에 포함된다."""
    block = {
        "type": "image",
        "image": {"caption": [{"plain_text": "장애 그래프"}], "type": "file"},
    }

    assert block_plain_text(block) == "장애 그래프"


def test_bookmark_url_and_caption_are_extracted() -> None:
    """북마크는 caption 과 url 이 모두 본문에 들어간다."""
    block = {
        "type": "bookmark",
        "bookmark": {"caption": [{"plain_text": "런북"}], "url": "https://example.test/runbook"},
    }

    assert block_plain_text(block) == "런북 https://example.test/runbook"


def test_child_page_title_is_extracted_without_heading_prefix() -> None:
    """child_page 제목은 본문에 들어가되 헤딩 마커는 붙지 않는다.

    마커를 붙이면 파서가 섹션 경계로 잡는데, Task 4 이후 그 섹션의 본문이
    비어 파서가 섹션 자체를 버려 제목이 통째로 사라진다.
    """
    block = {"type": "child_page", "child_page": {"title": "재발 방지 대책"}}

    assert block_plain_text(block) == "재발 방지 대책"


def test_child_database_title_is_extracted() -> None:
    """child_database 제목도 본문에 남는다(블록 자체엔 행이 없다)."""
    block = {"type": "child_database", "child_database": {"title": "트러블슈팅 내역"}}

    assert block_plain_text(block) == "트러블슈팅 내역"


def test_equation_expression_is_extracted() -> None:
    """수식 블록은 expression 을 평문으로 남긴다."""
    block = {"type": "equation", "equation": {"expression": "p99 = 1200ms"}}

    assert block_plain_text(block) == "p99 = 1200ms"
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_notion_blocks.py -k "cells or caption or bookmark or child_page_title or child_database_title or equation" -v`
Expected: FAIL — 첫 실패는 `ImportError: cannot import name 'cells_to_plain'`

- [ ] **Step 3: 구현**

`app/services/documents/sources/notion_blocks.py` 에 `cells_to_plain` 을 추가하고 `block_plain_text` 를 교체한다:

```python
def cells_to_plain(cells: Any) -> str:
    """`table_row.cells`(rich_text 배열의 배열)를 ' | ' 로 이은 한 줄로 만든다.

    행 단위 한 줄이 FTS·임베딩 양쪽에서 가장 자연스럽다 — 셀을 줄바꿈으로
    쪼개면 같은 행의 증상·원인·해결이 서로 다른 청크로 흩어질 수 있다.
    """
    if not isinstance(cells, list):
        return ""
    values = [rich_text_to_plain(cell) for cell in cells]
    return " | ".join(value for value in values if value)


def block_plain_text(block: dict[str, Any]) -> str:
    """블록 한 개에서 평문 텍스트를 추출한다(markdown 마커 포함).

    `rich_text` 만 보던 기존 구현이 표(`cells`)·캡션(`caption`)·하위 문서
    제목(`title`)·링크(`url`)·수식(`expression`)을 전부 흘렸다
    (`docs/architect-review/50` §2.4). 한 블록이 여러 필드를 동시에 갖는
    경우(예: bookmark 의 caption+url)가 있으므로 모두 모아 공백으로 잇는다.

    텍스트가 비면 마커만 남기지 않고 빈 문자열을 돌려준다 — 빈 헤딩 줄이
    파서에 섹션 경계로 잡히면 내용 없는 섹션이 생긴다.
    """
    block_type = str(block.get("type") or "")
    payload = block.get(block_type)
    if not isinstance(payload, dict):
        return ""
    parts = [
        rich_text_to_plain(payload.get("rich_text")),
        cells_to_plain(payload.get("cells")),
        rich_text_to_plain(payload.get("caption")),
        # child_page / child_database 의 제목(문자열). 헤딩 마커는 일부러 안
        # 붙인다 — 마커를 붙이면 본문 없는 섹션이 되어 파서가 버린다.
        str(payload.get("title") or "").strip(),
        # bookmark / embed / link_preview 의 대상 URL.
        str(payload.get("url") or "").strip(),
        # equation 의 수식 원문.
        str(payload.get("expression") or "").strip(),
    ]
    text = " ".join(part for part in parts if part)
    if not text:
        return ""
    return _block_prefix(block_type, payload) + text
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_notion_blocks.py -v`
Expected: PASS

- [ ] **Step 5: 회귀 확인**

Run: `uv run pytest tests/unit -q && uv run ruff check app/services/documents/sources/`
Expected: PASS / `All checks passed!`

- [ ] **Step 6: 커밋**

```bash
git add app/services/documents/sources/notion_blocks.py tests/unit/test_notion_blocks.py
git commit -m "feat: Notion 표 셀/캡션/링크/하위 문서 제목을 본문 색인에 포함"
```

---

### Task 4 (P0-4): 본문 재귀에서 `child_page` 진입 중단

지금은 `_collect_block_text` 가 `has_children=true` 인 `child_page` 를 타고 내려가 하위 페이지 본문을 부모 문서에 빨아들인다. 하위 페이지는 이미 독립 문서로 목록화되므로 (1) 같은 텍스트가 두 문서에 중복 색인되고 (2) `MAX_BLOCKS=2000` 예산을 잠식하며 (3) 부모가 히트했을 때 스니펫·URL 이 실제 출처와 어긋난다.

**Files:**
- Modify: `app/services/documents/sources/notion_source.py:171-185` (`_collect_block_text`)
- Test: `tests/unit/test_notion_page_source.py`

**Interfaces:**
- Consumes: Task 3 의 `block_plain_text`
- Produces: `NotionSource.fetch()` 반환 텍스트에서 하위 페이지 본문이 빠진다(하위 페이지 **제목**은 Task 3 덕에 그대로 남는다).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/unit/test_notion_page_source.py` 하단에 추가:

```python
def test_fetch_does_not_descend_into_child_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """본문 재귀는 child_page 에서 멈춘다(하위 페이지는 독립 문서로 색인되므로 중복 방지).

    제목은 남고 하위 페이지 본문은 들어오지 않는다.
    """
    tree = {
        "parent-page": [
            {
                "id": "para-1",
                "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": "부모 본문"}]},
            },
            {
                "id": "child-1",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "하위 문서"},
            },
        ],
        "child-1": [
            {
                "id": "para-2",
                "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": "하위 본문"}]},
            },
        ],
    }
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        requested.append(block_id)
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="parent-page")
    _patch_client(monkeypatch, source, handler)

    fetched = source.fetch("parent-page")

    assert "부모 본문" in fetched.text
    assert "하위 문서" in fetched.text
    assert "하위 본문" not in fetched.text
    assert "child-1" not in requested


def test_fetch_still_descends_into_toggle_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """child_page 가 아닌 컨테이너(toggle)에는 계속 재귀한다(회귀 방지)."""
    tree = {
        "parent-page": [
            {
                "id": "toggle-1",
                "type": "toggle",
                "has_children": True,
                "toggle": {"rich_text": [{"plain_text": "접힌 제목"}]},
            },
        ],
        "toggle-1": [
            {
                "id": "para-1",
                "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": "접힌 안쪽 본문"}]},
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="parent-page")
    _patch_client(monkeypatch, source, handler)

    fetched = source.fetch("parent-page")

    assert "접힌 안쪽 본문" in fetched.text
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_notion_page_source.py -k "does_not_descend or toggle_children" -v`
Expected: `test_fetch_does_not_descend_into_child_pages` FAIL — `AssertionError: assert '하위 본문' not in ...`. `test_fetch_still_descends_into_toggle_children` 는 이미 PASS(회귀 가드).

- [ ] **Step 3: 구현**

`app/services/documents/sources/notion_source.py` 의 `_collect_block_text` 를 교체한다:

```python
    def _collect_block_text(
        self, client: httpx.Client, block_id: str, lines: list[str], depth: int
    ) -> None:
        """블록 트리를 재귀 순회하며 평문 줄을 lines 에 누적한다.

        `child_page` 에서는 재귀를 멈춘다 — 하위 페이지는 `list_pages()` 가
        독립 문서로 목록화하므로, 여기서 또 타고 들어가면 같은 텍스트가 부모·
        자식 두 문서에 중복 색인되고 부모 히트의 스니펫·URL 이 실제 출처와
        어긋난다(`docs/architect-review/50` §2.3). 제목은
        `block_plain_text` 가 남기므로 부모에서도 하위 문서 이름은 검색된다.
        """
        if depth > MAX_BLOCK_DEPTH or len(lines) >= MAX_BLOCKS:
            return
        for block in self._list_children(client, block_id):
            if len(lines) >= MAX_BLOCKS:
                _LOG.warning("notion 블록 수 상한(%d) 도달: %s", MAX_BLOCKS, block_id)
                return
            text = block_plain_text(block)
            if text:
                lines.append(text)
            if (
                block.get("has_children")
                and block.get("id")
                and block.get("type") != "child_page"
            ):
                self._collect_block_text(client, str(block["id"]), lines, depth + 1)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_notion_page_source.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/services/documents/sources/notion_source.py tests/unit/test_notion_page_source.py
git commit -m "fix: Notion 본문 재귀가 child_page 를 타고 들어가 중복 색인하던 문제"
```

---

### Task 5 (P1-1): `kind="database"` 에서도 행 하위를 재귀 목록화

`kind="page"`(허브) 모드는 `_collect_child_pages` 로 하위 페이지·DB 행을 재귀 목록화하지만, `kind="database"` 모드는 DB 행만 뽑고 끝난다. 그래서 행 페이지 안의 하위 페이지·하위 DB 가 검색에 안 잡힌다.

**Files:**
- Modify: `app/services/documents/sources/notion_source.py:87-106` (`list_pages`)
- Test: `tests/unit/test_notion_page_source.py`

**Interfaces:**
- Consumes: 기존 `_collect_child_pages(client, page_id, acc, visited, depth) -> None`, `_record_page(meta, item_id, acc, visited) -> bool`, `to_file_meta`
- Produces: `list_pages()` 가 database 분기에서도 하위 트리를 평탄 목록으로 돌려준다. 반환 타입 `list[FileMeta]` 는 그대로다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/unit/test_notion_page_source.py` 하단에 추가:

```python
def test_list_pages_with_database_id_recurses_into_row_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """database 모드에서도 각 행 페이지 하위의 child_page 가 독립 문서로 목록화된다."""
    row = {
        "id": "row-1",
        "url": "https://www.notion.so/row-1",
        "properties": {"이름": {"type": "title", "title": [{"plain_text": "장애 A"}]}},
        "last_edited_time": "2026-07-05T00:00:00.000Z",
    }
    children = {
        "row-1": [
            {
                "id": "child-1",
                "type": "child_page",
                "has_children": False,
                "child_page": {"title": "원인 분석"},
            },
        ],
        "child-1": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/databases/"):
            return _json({"results": [row], "has_more": False})
        block_id = request.url.path.split("/")[2]
        return _json({"results": children.get(block_id, [])})

    source = NotionSource(token="t1", database_id="db-1")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["row-1", "child-1"]
    assert [p.title for p in pages] == ["장애 A", "원인 분석"]


def test_list_pages_with_database_id_does_not_duplicate_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """행이 자기 자신을 다시 참조해도 중복 목록화되지 않는다."""
    row = {
        "id": "row-1",
        "url": "https://www.notion.so/row-1",
        "properties": {"이름": {"type": "title", "title": [{"plain_text": "장애 A"}]}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/databases/"):
            return _json({"results": [row], "has_more": False})
        return _json(
            {
                "results": [
                    {
                        "id": "row-1",
                        "type": "child_page",
                        "has_children": False,
                        "child_page": {"title": "장애 A"},
                    }
                ]
            }
        )

    source = NotionSource(token="t1", database_id="db-1")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["row-1"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_notion_page_source.py -k "database_id_recurses or does_not_duplicate" -v`
Expected: `test_list_pages_with_database_id_recurses_into_row_children` FAIL — `AssertionError: assert ['row-1'] == ['row-1', 'child-1']`

- [ ] **Step 3: 구현**

`app/services/documents/sources/notion_source.py` 의 `list_pages` 를 교체한다:

```python
    def list_pages(self) -> list[FileMeta]:
        """설정된 범위 안의 Notion 페이지 메타데이터를 반환한다.

        `page_id` 가 설정돼 있으면 그 페이지 하위 트리 전체를 child_page 를
        통해 재귀 탐색해 목록화한다(허브 페이지 하위 문서 탐색).
        `database_id` 가 설정돼 있으면 DB 행을 목록화한 뒤 **각 행 하위
        트리도 같은 방식으로 재귀 탐색**한다 — 행 페이지 안의 하위 페이지·
        하위 DB 가 검색에서 빠지지 않게 한다(`docs/architect-review/50` §2.2).
        둘 다 없으면 워크스페이스 검색 결과를 그대로 쓴다(그 응답 자체가 이미
        중첩 페이지를 포함하므로 추가 재귀가 불필요하다).

        Raises:
            IntegrationError: 인증 실패·rate limit·네트워크 오류 시.
        """
        acc: list[FileMeta] = []
        visited: set[str] = set()
        with self._client() as client:
            if self._page_id:
                self._collect_child_pages(client, self._page_id, acc, visited, 0)
                return acc

            path, body = self._list_request_spec()
            raw_pages = self._paginate(client, path, body)
            if not self._database_id:
                return [to_file_meta(page) for page in raw_pages if page.get("id")]

            for page in raw_pages:
                page_id = str(page.get("id") or "")
                if not page_id or page_id in visited:
                    continue
                if not self._record_page(to_file_meta(page), page_id, acc, visited):
                    _LOG.warning("notion 하위 페이지 수 상한(%d) 도달: %s", MAX_PAGES, page_id)
                    return acc
                self._collect_child_pages(client, page_id, acc, visited, 0)
        return acc
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_notion_page_source.py tests/unit/test_document_sources.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/services/documents/sources/notion_source.py tests/unit/test_notion_page_source.py
git commit -m "feat: Notion database 모드에서 행 하위 페이지/DB 도 목록화"
```

---

### Task 6 (P1-2): 하위 페이지 탐색이 toggle·column 등 컨테이너를 통과

`_collect_child_pages` 는 페이지 직속 자식만 본다. toggle 이나 2단 칼럼 안에 넣은 하위 페이지·하위 DB 는 목록화되지 않는다(현행 docstring 이 스코프 밖이라 명시).

페이지 중첩 깊이(`MAX_PAGE_DEPTH`)와 컨테이너 하강 깊이를 **별도 카운터**로 센다. 같은 카운터를 쓰면 토글 두 겹만으로 페이지 깊이 예산이 소진된다.

**Files:**
- Modify: `app/services/documents/sources/notion_source.py:33-36`(상수), `:187-234`(`_collect_child_pages`)
- Test: `tests/unit/test_notion_page_source.py`

**Interfaces:**
- Consumes: Task 5 의 `list_pages`, 기존 `_record_page`
- Produces: `_collect_child_pages(client, page_id, acc, visited, depth, container_depth: int = 0) -> None` — 인자 하나가 기본값과 함께 늘어난다(기존 호출부는 그대로 동작).
- 신규 모듈 상수: `MAX_CONTAINER_DEPTH: int = 3`, `_CONTAINER_BLOCK_TYPES: frozenset[str]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/unit/test_notion_page_source.py` 하단에 추가:

```python
def test_collect_child_pages_descends_through_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """toggle 안에 중첩된 child_page 도 목록화된다."""
    tree = {
        "hub-page": [
            {
                "id": "toggle-1",
                "type": "toggle",
                "has_children": True,
                "toggle": {"rich_text": [{"plain_text": "지난 장애"}]},
            },
        ],
        "toggle-1": [
            {
                "id": "child-1",
                "type": "child_page",
                "has_children": False,
                "child_page": {"title": "2026-07 타임아웃"},
            },
        ],
        "child-1": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["child-1"]
    assert [p.title for p in pages] == ["2026-07 타임아웃"]


def test_collect_child_pages_stops_after_container_depth_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """컨테이너 하강은 MAX_CONTAINER_DEPTH 단계에서 멈춘다(호출 폭증 방지)."""
    tree = {
        "hub-page": [{"id": "c1", "type": "toggle", "has_children": True, "toggle": {}}],
        "c1": [{"id": "c2", "type": "toggle", "has_children": True, "toggle": {}}],
        "c2": [{"id": "c3", "type": "toggle", "has_children": True, "toggle": {}}],
        "c3": [{"id": "c4", "type": "toggle", "has_children": True, "toggle": {}}],
        "c4": [
            {
                "id": "deep-page",
                "type": "child_page",
                "has_children": False,
                "child_page": {"title": "너무 깊음"},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert pages == []


def test_container_depth_does_not_consume_page_depth_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """토글을 여러 겹 지나도 페이지 중첩 깊이 예산은 줄지 않는다."""
    tree = {
        "hub-page": [{"id": "t1", "type": "toggle", "has_children": True, "toggle": {}}],
        "t1": [{"id": "t2", "type": "toggle", "has_children": True, "toggle": {}}],
        "t2": [
            {
                "id": "p1",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "1단계"},
            }
        ],
        "p1": [
            {
                "id": "p2",
                "type": "child_page",
                "has_children": True,
                "child_page": {"title": "2단계"},
            }
        ],
        "p2": [
            {
                "id": "p3",
                "type": "child_page",
                "has_children": False,
                "child_page": {"title": "3단계"},
            }
        ],
        "p3": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.split("/")[2]
        return _json({"results": tree.get(block_id, [])})

    source = NotionSource(token="t1", page_id="hub-page")
    _patch_client(monkeypatch, source, handler)

    pages = source.list_pages()

    assert [p.external_id for p in pages] == ["p1", "p2", "p3"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_notion_page_source.py -k "descends_through_toggle or container_depth" -v`
Expected: `descends_through_toggle` FAIL — `AssertionError: assert [] == ['child-1']`

- [ ] **Step 3: 상수 추가**

`app/services/documents/sources/notion_source.py` 의 `MAX_PAGES = 500` 아래에 추가한다:

```python
#: 하위 페이지 탐색 시 toggle/column 같은 컨테이너 블록을 몇 단계까지
#: 통과할지. 페이지 중첩 깊이(MAX_PAGE_DEPTH)와 **별도로** 센다 — 같은
#: 카운터를 쓰면 토글 두 겹만으로 페이지 깊이 예산이 소진된다.
MAX_CONTAINER_DEPTH = 3
#: 자식으로 하위 페이지/하위 DB 를 품을 수 있는 컨테이너 블록 타입.
_CONTAINER_BLOCK_TYPES = frozenset(
    {
        "toggle",
        "column_list",
        "column",
        "callout",
        "synced_block",
        "bulleted_list_item",
        "numbered_list_item",
        "to_do",
        "quote",
    }
)
```

- [ ] **Step 4: `_collect_child_pages` 교체**

```python
    def _collect_child_pages(
        self,
        client: httpx.Client,
        page_id: str,
        acc: list[FileMeta],
        visited: set[str],
        depth: int,
        container_depth: int = 0,
    ) -> None:
        """page_id 하위 child_page/child_database 트리를 재귀 순회하며 acc 에 평탄 누적한다.

        child_database 를 만나면 그 database 를 query 해 얻은 행(페이지)들도
        동일하게 재귀 대상에 포함한다. toggle/column 같은 컨테이너 블록은
        하위 페이지를 품을 수 있으므로 `MAX_CONTAINER_DEPTH` 까지 통과해
        내려간다 — 이때 페이지 중첩 깊이(`depth`)는 늘리지 않는다
        (`docs/architect-review/50` §3 P1-2).
        """
        if depth > MAX_PAGE_DEPTH:
            return
        if len(acc) >= MAX_PAGES:
            _LOG.warning("notion 하위 페이지 수 상한(%d) 도달: %s", MAX_PAGES, page_id)
            return
        for block in self._list_children(client, page_id):
            if len(acc) >= MAX_PAGES:
                _LOG.warning("notion 하위 페이지 수 상한(%d) 도달: %s", MAX_PAGES, page_id)
                return
            block_type = block.get("type")
            block_id = str(block.get("id") or "")
            if not block_id:
                continue
            if block_type == "child_page":
                if block_id in visited:
                    continue
                if not self._record_page(child_page_to_file_meta(block), block_id, acc, visited):
                    _LOG.warning("notion 하위 페이지 수 상한(%d) 도달: %s", MAX_PAGES, page_id)
                    return
                self._collect_child_pages(client, block_id, acc, visited, depth + 1)
            elif block_type == "child_database":
                for row in self._paginate(client, f"/databases/{block_id}/query", {}):
                    row_id = str(row.get("id") or "")
                    if not row_id or row_id in visited:
                        continue
                    if not self._record_page(to_file_meta(row), row_id, acc, visited):
                        _LOG.warning("notion 하위 페이지 수 상한(%d) 도달: %s", MAX_PAGES, page_id)
                        return
                    self._collect_child_pages(client, row_id, acc, visited, depth + 1)
            elif (
                block_type in _CONTAINER_BLOCK_TYPES
                and block.get("has_children")
                and container_depth < MAX_CONTAINER_DEPTH
            ):
                self._collect_child_pages(
                    client, block_id, acc, visited, depth, container_depth + 1
                )
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_notion_page_source.py -v`
Expected: PASS

- [ ] **Step 6: 회귀 + lint**

Run: `uv run pytest tests/unit -q && uv run ruff check app/services/documents/sources/`
Expected: PASS / `All checks passed!`

- [ ] **Step 7: 커밋**

```bash
git add app/services/documents/sources/notion_source.py tests/unit/test_notion_page_source.py
git commit -m "feat: Notion 하위 페이지 탐색이 toggle/column 컨테이너를 통과"
```

---

### Task 7 (P0-3): 페이지 properties 를 본문 머리에 첨부

`fetch()` 는 `/blocks/{id}/children` 만 본다. 그래서 DB 행 페이지의 상태·태그·담당자·날짜 같은 속성이 통째로 색인에서 빠진다 — DB 행 정보의 절반이다.

**Files:**
- Modify: `app/services/documents/sources/notion_blocks.py` (신규 `property_plain_text`)
- Modify: `app/services/documents/sources/notion_source.py:112-133` (`fetch`) + 신규 `_page_property_lines`
- Test: `tests/unit/test_notion_blocks.py`, `tests/unit/test_notion_page_source.py`

**Interfaces:**
- Consumes: Task 1 의 `rich_text_to_plain`, 기존 `_request_json`
- Produces:
  - `property_plain_text(prop: dict[str, Any]) -> str` — 속성 하나를 평문으로. 미지원 타입은 `""`.
  - `NotionSource._page_property_lines(client: httpx.Client, page_id: str) -> list[str]` — `"{속성명}: {값}"` 줄들. 조회 실패 시 빈 리스트.

- [ ] **Step 1: 순수 함수 테스트를 쓴다**

`tests/unit/test_notion_blocks.py` 의 import 에 `property_plain_text` 를 추가하고 하단에 추가:

```python
def test_property_plain_text_handles_common_types() -> None:
    """DB 행에서 실제로 많이 쓰는 속성 타입들이 평문으로 나온다."""
    assert property_plain_text(
        {"type": "title", "title": [{"plain_text": "타임아웃 장애"}]}
    ) == "타임아웃 장애"
    assert property_plain_text(
        {"type": "rich_text", "rich_text": [{"plain_text": "커넥션 풀 고갈"}]}
    ) == "커넥션 풀 고갈"
    assert property_plain_text({"type": "select", "select": {"name": "P1"}}) == "P1"
    assert property_plain_text({"type": "status", "status": {"name": "해결"}}) == "해결"
    assert property_plain_text(
        {"type": "multi_select", "multi_select": [{"name": "DB"}, {"name": "네트워크"}]}
    ) == "DB, 네트워크"
    assert property_plain_text(
        {"type": "people", "people": [{"name": "홍길동"}]}
    ) == "홍길동"
    assert property_plain_text({"type": "number", "number": 3}) == "3"
    assert property_plain_text(
        {"type": "url", "url": "https://example.test/runbook"}
    ) == "https://example.test/runbook"
    assert property_plain_text({"type": "checkbox", "checkbox": True}) == "true"


def test_property_plain_text_formats_date_range() -> None:
    """날짜 속성은 start 만 있으면 그대로, end 가 있으면 범위로 낸다."""
    assert property_plain_text(
        {"type": "date", "date": {"start": "2026-07-03", "end": None}}
    ) == "2026-07-03"
    assert property_plain_text(
        {"type": "date", "date": {"start": "2026-07-03", "end": "2026-07-05"}}
    ) == "2026-07-03 ~ 2026-07-05"


def test_property_plain_text_returns_empty_for_unsupported_types() -> None:
    """미지원 타입(relation/formula/rollup 등)과 빈 값은 빈 문자열이다."""
    assert property_plain_text({"type": "relation", "relation": [{"id": "x"}]}) == ""
    assert property_plain_text({"type": "select", "select": None}) == ""
    assert property_plain_text({}) == ""
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_notion_blocks.py -k property_plain_text -v`
Expected: FAIL — `ImportError: cannot import name 'property_plain_text'`

- [ ] **Step 3: `property_plain_text` 구현**

`app/services/documents/sources/notion_blocks.py` 하단에 추가:

```python
def property_plain_text(prop: dict[str, Any]) -> str:
    """페이지/DB 행 속성 하나를 평문 문자열로 만든다.

    DB 행에서 실제로 검색 신호가 되는 타입만 다룬다 — relation(대상 페이지
    제목을 알려면 추가 호출이 필요)·rollup·formula 는 비용 대비 이득이 작아
    빈 문자열을 낸다(필요해지면 그때 넓힌다).
    """
    prop_type = str(prop.get("type") or "")
    value = prop.get(prop_type)
    if prop_type in ("title", "rich_text"):
        return rich_text_to_plain(value)
    if prop_type in ("select", "status"):
        return str(value.get("name") or "") if isinstance(value, dict) else ""
    if prop_type in ("multi_select", "people"):
        if not isinstance(value, list):
            return ""
        names = [str(item.get("name") or "") for item in value if isinstance(item, dict)]
        return ", ".join(name for name in names if name)
    if prop_type == "date":
        if not isinstance(value, dict):
            return ""
        start = str(value.get("start") or "")
        end = str(value.get("end") or "")
        if not start:
            return ""
        return f"{start} ~ {end}" if end else start
    if prop_type == "checkbox":
        return "true" if value else "false"
    if prop_type in ("number", "url", "email", "phone_number"):
        return str(value) if value is not None else ""
    return ""
```

- [ ] **Step 4: 순수 함수 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_notion_blocks.py -v`
Expected: PASS

- [ ] **Step 5: 어댑터 배선 테스트를 쓴다**

`tests/unit/test_notion_page_source.py` 하단에 추가:

```python
def test_fetch_prepends_page_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 행 속성이 본문 앞에 '이름: 값' 줄로 붙는다."""
    page = {
        "id": "row-1",
        "properties": {
            "상태": {"type": "status", "status": {"name": "해결"}},
            "태그": {"type": "multi_select", "multi_select": [{"name": "DB"}]},
        },
    }
    blocks = [
        {
            "id": "para-1",
            "type": "paragraph",
            "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "본문 한 줄"}]},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/pages/"):
            return _json(page)
        return _json({"results": blocks})

    source = NotionSource(token="t1", database_id="db-1")
    _patch_client(monkeypatch, source, handler)

    text = source.fetch("row-1").text

    assert text.splitlines()[:2] == ["상태: 해결", "태그: DB"]
    assert "본문 한 줄" in text


def test_fetch_survives_property_lookup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """속성 조회가 실패해도 블록 본문만으로 색인을 계속한다."""
    blocks = [
        {
            "id": "para-1",
            "type": "paragraph",
            "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "본문 한 줄"}]},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/pages/"):
            return httpx.Response(403, json={"message": "denied"})
        return _json({"results": blocks})

    source = NotionSource(token="t1", database_id="db-1")
    _patch_client(monkeypatch, source, handler)

    text = source.fetch("row-1").text

    assert text == "본문 한 줄"
```

- [ ] **Step 6: 실패 확인**

Run: `uv run pytest tests/unit/test_notion_page_source.py -k "page_properties or property_lookup_failure" -v`
Expected: `test_fetch_prepends_page_properties` FAIL — `AssertionError: assert ['본문 한 줄'] == ['상태: 해결', '태그: DB']`

- [ ] **Step 7: `fetch` 배선 구현**

`app/services/documents/sources/notion_source.py` 의 import 에 `property_plain_text` 를 추가하고, `fetch` 를 교체한 뒤 `_page_property_lines` 를 `_collect_block_text` 위에 넣는다:

```python
    def fetch(self, external_id: str) -> FetchedDocument:
        """페이지 속성 + 본문(블록 트리)을 평문 텍스트로 반환한다.

        DB 행의 상태·태그·담당자 같은 속성은 블록이 아니라 페이지 객체에
        있어 `/blocks/{id}/children` 만으로는 절대 잡히지 않는다. 그래서
        `GET /pages/{id}` 를 1회 더 호출해 속성 줄을 본문 앞에 붙인다
        (`docs/architect-review/50` §3 P0-3).

        Args:
            external_id: Notion page ID.

        Returns:
            속성 줄 + 블록 줄을 줄바꿈으로 이어 붙인 평문(최대 문자 수로
            잘림)과 절단 여부.

        Raises:
            IntegrationError: 페이지가 없거나 외부 연동에 실패한 경우.
        """
        if not external_id:
            raise IntegrationError("notion page id must not be empty")

        lines: list[str] = []
        with self._client() as client:
            lines.extend(self._page_property_lines(client, external_id))
            self._collect_block_text(client, external_id, lines, depth=0)
        text = "\n".join(lines)
        truncated = len(text) > self._max_chars
        return FetchedDocument(text[: self._max_chars], truncated)

    def _page_property_lines(self, client: httpx.Client, page_id: str) -> list[str]:
        """페이지 속성을 `"{속성명}: {값}"` 줄 목록으로 만든다.

        조회 실패는 삼키고 빈 목록을 돌려준다 — 속성 하나 때문에 문서 1건의
        본문 색인이 통째로 실패하면 안 된다(블록 본문만으로도 색인 가치가
        있다).
        """
        try:
            page = self._request_json(client, "GET", f"/pages/{page_id}")
        except IntegrationError as exc:
            _LOG.warning(
                "notion 페이지 속성 조회 실패(본문만 색인): %s (%s)", page_id, exc
            )
            return []
        properties = page.get("properties")
        if not isinstance(properties, dict):
            return []
        lines: list[str] = []
        for name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            value = property_plain_text(prop)
            if value:
                lines.append(f"{name}: {value}")
        return lines
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_notion_page_source.py tests/unit/test_notion_blocks.py tests/unit/test_document_sources.py -v`
Expected: PASS

- [ ] **Step 9: 회귀 + lint**

Run: `uv run pytest tests/unit -q && uv run ruff check app/`
Expected: PASS / `All checks passed!`

- [ ] **Step 10: 커밋**

```bash
git add app/services/documents/sources/notion_blocks.py app/services/documents/sources/notion_source.py tests/unit/test_notion_blocks.py tests/unit/test_notion_page_source.py
git commit -m "feat: Notion 페이지 속성(상태/태그/담당자 등)을 본문 색인에 포함"
```

> **비용 메모:** 문서 1건당 API 호출이 +1 이다. 본문 색인은 `content_hash` 게이트가 걸린 배치라 영향이 작지만, `document_search_strategy="fetch"`(롤백 스위치) 경로에서는 검색 1회당 후보 수만큼 늘어난다. 기본 전략은 `"indexed"` 이므로 수용한다.

---

### Task 8 (P2-1): `index_bodies` 기본값을 `True` 로 전환

`document_search_strategy="indexed"` 가 이미 기본인데 본문 색인이 옵트인이면, 기본 경로끼리 어긋나 keyword/vector arm 이 비고 검색이 제목 매칭만으로 조용히 퇴화한다. lead 승인 완료(정합성 > 비용).

**Files:**
- Modify: `app/mcp/tools/sources.py:33` (기본값) + `:56-61` (docstring)
- Modify: `app/scripts/refresh_documents.py:60` (argparse)
- Test: `tests/unit/test_refresh_documents_script.py:145`

**Interfaces:**
- Consumes: 기존 `DocumentIndexService.refresh(source, project, index_bodies)` — 시그니처 변경 없음
- Produces: `refresh_index()` MCP 도구와 `refresh_documents` CLI 의 **기본 동작**이 본문 색인 포함으로 바뀐다. CLI 는 `--no-index-bodies` 로 끌 수 있다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/unit/test_refresh_documents_script.py` 의 `test_index_bodies_defaults_to_false` 를 다음 두 테스트로 교체한다(기존 `test_index_bodies_flag_passed_to_refresh` 는 그대로 둔다):

```python
def test_index_bodies_defaults_to_true() -> None:
    """플래그를 안 주면 refresh 가 index_bodies=True 로 호출된다.

    기본 검색 전략이 indexed(3-arm RRF)라, 본문 색인이 꺼진 채 갱신하면
    keyword/vector arm 이 비어 제목 매칭만으로 조용히 퇴화한다.
    """
    bundle = _bundle()

    _execute(bundle, _args(), lock_acquire=lambda: True)

    assert bundle.document_index_service.calls == [(None, None, True)]


def test_no_index_bodies_flag_disables_body_indexing() -> None:
    """--no-index-bodies 를 주면 refresh 가 index_bodies=False 로 호출된다."""
    bundle = _bundle()

    _execute(bundle, _args(index_bodies=False), lock_acquire=lambda: True)

    assert bundle.document_index_service.calls == [(None, None, False)]
```

그리고 같은 파일의 `_args()` 헬퍼 기본값을 `index_bodies: bool = True` 로 바꾼다(`_args` 정의는 `tests/unit/test_refresh_documents_script.py:57` 근처).

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_refresh_documents_script.py -v`
Expected: FAIL — `AssertionError: assert [(None, None, False)] == [(None, None, True)]`

- [ ] **Step 3: CLI 기본값 전환**

`app/scripts/refresh_documents.py`:

1. 파일 상단 import 에 `import argparse` 가 없으면 추가한다(`_parse_args` 가 이미 `argparse.ArgumentParser` 를 쓰므로 대개 있다).
2. `parser.add_argument("--index-bodies", action="store_true")` 를 아래로 교체:

```python
    # 기본 검색 전략이 indexed 라 본문 색인이 꺼지면 제목 매칭만 남는다.
    # 기본을 켜고 `--no-index-bodies` 로만 끌 수 있게 한다.
    parser.add_argument(
        "--index-bodies",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
```

- [ ] **Step 4: MCP 도구 기본값 전환**

`app/mcp/tools/sources.py`:

1. `index_bodies: bool = False,` → `index_bodies: bool = True,`
2. docstring 의 `index_bodies` 설명을 교체:

```
            index_bodies: True(기본) 면 신규/변경된 Drive/Notion 문서의
                본문을 fetch 해 document/chunk 에 색인한다(검색 랭킹용
                벡터 생성). 기본 검색 전략이 indexed(제목+키워드+벡터
                3-arm RRF)라 본문 색인이 없으면 keyword/vector arm 이 비어
                검색이 제목 매칭만으로 조용히 퇴화한다. 비용(문서마다
                fetch + 파싱 + 임베딩)을 아껴야 하는 대량 초기 동기화에서만
                False 를 준다. 원본에서 삭제된 문서의 청크·벡터 삭제는 이
                플래그와 무관하게 항상 수행된다.
```

3. 도구 요약 첫 줄도 실제 동작에 맞춘다: `"""협업 문서 메타 캐시(제목·수정일)를 원본과 동기화한다.` 다음 문단 `"문서 목록과 메타데이터만 갱신하고 본문은 저장하지 않는다."` 를 `"문서 목록·메타데이터를 갱신하고, 기본적으로 본문까지 색인한다."` 로 바꾼다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_refresh_documents_script.py -v`
Expected: PASS

- [ ] **Step 6: 기본값을 단언하는 다른 테스트가 있는지 확인**

Run: `grep -rn "index_bodies\|refresh_index" tests/`
기대: `tests/unit/test_refresh_documents_script.py`, `tests/unit/test_document_index_service.py` 만 나온다. `tests/integration/` 에서 `refresh_index` 를 호출하며 본문 색인이 안 도는 것을 전제하는 테스트가 있으면 `index_bodies=False` 를 명시적으로 넘기도록 고친다(기본값을 되돌리지 않는다).

- [ ] **Step 7: 전체 테스트 + lint**

Run: `uv run pytest -q && uv run ruff check app/`
Expected: PASS / `All checks passed!`

- [ ] **Step 8: 문서 갱신**

`README.md` 에서 `refresh_index` / `--index-bodies` 를 설명하는 대목을 찾아 기본값이 켜짐으로 바뀐 사실과 `--no-index-bodies` 를 반영한다.

Run: `grep -n "index-bodies\|index_bodies\|refresh_index" README.md`

- [ ] **Step 9: 커밋**

```bash
git add app/mcp/tools/sources.py app/scripts/refresh_documents.py tests/unit/test_refresh_documents_script.py README.md
git commit -m "feat: refresh_index 의 index_bodies 기본값을 True 로 전환"
```

---

## 최종 검증 (전 Task 완료 후)

- [ ] **전체 테스트 + 커버리지**

Run: `uv run pytest --cov=app --cov-report=term-missing -q`
Expected: PASS, 커버리지 80% 이상.

- [ ] **실코퍼스 수동 확인 (Notion 자격증명이 있는 환경에서만)**

```bash
uv run python -m app.scripts.refresh_documents --source notion
```

그 뒤 MCP `search_documents(query="트러블슈팅 내역", source="notion", top_k=10)` 로 아래 6가지를 확인한다 — `docs/architect-review/50` §4 의 검증 기준이다.

1. 허브/DB 하위의 "트러블슈팅" **행이 독립 결과**로 나온다 (Task 5).
2. toggle 안에 중첩된 하위 페이지가 결과에 나온다 (Task 6).
3. 표 셀에만 "트러블슈팅" 이 있는 페이지가 나오고, 스니펫이 그 표 행을 보여준다 (Task 3).
4. DB 행의 `상태: 해결` 같은 속성 값으로도 검색된다 (Task 7).
5. 같은 텍스트가 부모/자식 두 결과로 중복 노출되지 않는다 (Task 4).
6. 청크 앵커가 `# 개요` 가 아닌 실제 헤딩이다 (Task 2). 확인 SQL:

```sql
SELECT c.id, left(c.text, 60)
FROM chunk c JOIN document d ON d.id = c.document_id
WHERE d.doc_type = 'notion' AND c.chunk_type = 'section'
LIMIT 20;
```

- [ ] **결과를 architect(:0.1) 에 보고**

```bash
say :0.1 "[developer] Notion nested block 색인 보강 8개 Task 완료. 테스트/커버리지 결과: {요약}. 실코퍼스 검증 {통과/미실행 사유}"
```

## 설계 이탈 시

구현 중 이 계획대로 하면 안 되는 이유를 발견하면 **직접 판단하지 말고** architect(:0.1) 에 사유와 대안을 보내 승인을 받는다.

```bash
say :0.1 "[developer] Task N 설계 이탈 요청: {무엇이} {왜} 안 되는지. 대안: {안}"
```
