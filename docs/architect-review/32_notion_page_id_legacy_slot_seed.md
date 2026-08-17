# 32. 레거시 슬롯 `DOCS_MCP_NOTION_PAGE_ID` 시드 설계

- 대상: `app/bootstrap.py::seed_default_sources()`, `.env.example`, `README.md`
- 계기: 외부 세션 재검토 요청 — `notion_page_id` 레거시 슬롯 대칭 건
- 판정: **수정 필요 (3건, 실버그 1건 포함)**

## 배경

`register_notion_page` 도구(런타임 등록)는 이미 `kind="page"` 로 매핑을 저장한다.
`Settings.notion_page_id` 필드와 `DOCS_MCP_NOTION_PAGE_ID` env 키도 이미 반영돼 있다
(`app/core/config.py:76-78`). 남은 것은 부트스트랩 시드 경로가 이 값을 실제로 쓰게 하는 일이다.

현재 `seed_default_sources()` 는 `notion_page_id` 를 전혀 참조하지 않는다.

## 결정

### D1. early-return 가드 누락은 실버그 — 반드시 수정

`app/bootstrap.py:45`

```python
if not cfg.drive_folder_id and not cfg.notion_database_id:
    return
```

`DOCS_MCP_NOTION_PAGE_ID` 만 설정한 사용자는 여기서 조기 반환되어 **시드가 아예 일어나지 않는다.**
`notion_page_id` 를 가드 조건에 포함한다.

### D2. 우선순위 방향 — page 우선

`notion_database_id` 와 `notion_page_id` 가 동시에 설정되면 **page 가 이긴다.**

근거:

1. `app/core/config.py:74-75` docstring 이 이미 그렇게 선언하고 있다 — 구현이 문서를 따라가야지 반대가 아니다.
2. 도구 레벨 규칙 "나중 호출이 이긴다"는 **시간 순서가 있는 호출**에만 성립한다.
   env 변수는 순서 없는 집합이므로 그 규칙을 그대로 옮길 수 없다. 대응되는 규칙은
   "더 구체적인(나중에 추가된) 슬롯이 이긴다"이고, 그것이 page 다.
3. 실무 시나리오상 기존 database 설정 위에 page 변수를 새로 추가한 사용자의 의도는
   page 로의 이전이다.

### D3. 구현 방식 — 순차 시드(database 먼저 → page 나중) 채택하지 않음

재검토 요청이 제안한 "database 를 먼저 시드하고 page 를 나중에 시도해 page 가 최종 우선하게" 하는
방식은 **현재 코드에서 동작하지 않는다.**

`seed_default_sources()` 의 각 시드는 `if repo.get(...) is None:` 가드로 감싸여 있다.
database 를 먼저 넣으면 `(DEFAULT_PROJECT, SOURCE_NOTION)` 행이 생기므로, 이어지는 page upsert 는
`get()` 이 None 이 아니어서 **스킵된다.** 순차 덮어쓰기가 성립하려면 page 쪽 가드를 제거해야 하는데,
가드를 제거하면 재기동할 때마다 사용자가 런타임에 `register_notion_source` 로 등록한 매핑을
env 값이 덮어쓴다 — docstring 이 명시한 "재기동해도 중복 생성 없음" 계약이 깨진다.

따라서 **DB 에 접근하기 전에 값 하나를 고른다.** 쓰기는 여전히 한 번, 가드도 그대로 유지된다.

### D4. `kind` 인자 누락 — 이번 스코프에 포함

`app/bootstrap.py:55` 의 notion upsert 는 `kind` 없이 호출해 `kind=NULL` 행을 만든다.
`ProjectSourceResolver` 가 `notion_row.kind or "database"` 로 폴백하므로(`project_source_resolver.py:70,91`)
현재는 무증상이지만, 도구 경로(`kind="database"` 명시)와 데이터가 불일치한다.

별도 건으로 빼지 않는다. page 시드에는 `kind="page"` 가 **필수**이고 같은 줄을 고치는 중이므로
분리 비용이 이득보다 크다. drive 는 `kind=NULL` 이 설계상 정상이므로 그대로 둔다
(`app/models/project_source.py:32`).

### D5. 동시 설정 시 warning 1줄

둘 다 설정된 상태는 사용자 실수일 가능성이 높고, 무시된 쪽이 조용히 사라지면 진단이 어렵다.
`logging.warning` 한 줄로 어느 쪽이 채택됐는지 남긴다.

## 구현 지시

### 1. `app/bootstrap.py`

모듈 상단에 `import logging` + `logger = logging.getLogger(__name__)` 추가.

`seed_default_sources()` 를 다음 형태로 수정한다.

```python
if cfg.notion_page_id and cfg.notion_database_id:
    logger.warning(
        "DOCS_MCP_NOTION_PAGE_ID 와 DOCS_MCP_NOTION_DATABASE_ID 가 모두 설정돼 "
        "page 를 사용합니다(database 는 무시)."
    )
notion_id, notion_kind = (
    (cfg.notion_page_id, "page")
    if cfg.notion_page_id
    else (cfg.notion_database_id, "database")
)
if not cfg.drive_folder_id and not notion_id:
    return
...
    if notion_id:
        if repo.get(DEFAULT_PROJECT, SOURCE_NOTION) is None:
            repo.upsert(DEFAULT_PROJECT, SOURCE_NOTION, notion_id, kind=notion_kind)
```

drive 시드 블록은 손대지 않는다.

docstring 도 갱신한다: 시드 대상에 `notion_page_id` 를 추가하고, 동시 설정 시 page 우선임을 한 줄로 명시.

### 2. 테스트 — `tests/integration/test_bootstrap_seed.py` (신규)

기존 `pg_engine` fixture(`tests/conftest.py:46`)를 사용한다. 케이스 4개:

- `page_id` 만 설정 → `(default, notion)` 행이 생기고 `location == page_id`, `kind == "page"`
- `database_id` 만 설정 → `location == database_id`, `kind == "database"`
- 둘 다 설정 → page 가 채택됨 (`location == page_id`, `kind == "page"`)
- 이미 행이 있으면 재호출해도 덮어쓰지 않음(멱등)

`Settings` 는 frozen dataclass이므로 테스트에서 `Settings(notion_page_id=..., ...)` 로 직접 생성한다.

### 3. `.env.example`

`DOCS_MCP_NOTION_DATABASE_ID=` (43줄) 바로 아래에 추가:

```
# database_id 와 동시에 설정하면 이 값(page)이 우선합니다.
DOCS_MCP_NOTION_PAGE_ID=
```

### 4. `README.md`

레거시 표(80줄 근처)에 행 추가:

```
| `DOCS_MCP_NOTION_PAGE_ID`      | 기본 프로젝트용 Notion 허브 페이지 ID. 바로 아래 1단계 하위 페이지가 대상 | (없음) |
```

표 아래에 한 줄:

> `DOCS_MCP_NOTION_DATABASE_ID` 와 `DOCS_MCP_NOTION_PAGE_ID` 를 함께 설정하면 page 가 우선하고 database 는 무시됩니다.

## 스코프 밖

- 레거시 env 슬롯이 런타임 등록 매핑을 덮어쓸지 여부(현행: 덮어쓰지 않음) — 변경하지 않는다.
- `project_source` 의 진짜 멀티 소스 지원 — 별건.
