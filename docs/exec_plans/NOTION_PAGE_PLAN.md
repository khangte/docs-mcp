# Notion 페이지 소스 지원 설계안

- 목표: `register_notion_page(project, page_id)` 신설. 기존 `register_notion_source(database_id)` 는 불변. 자동감지 없음(호출 LLM 이 링크 보고 도구 선택).
- 원칙(ponytail): 기존 `project_notion_source`(project PK, database_id String(256)) 와 resolver/repo/service 골격을 재사용. page 는 "kind 가 다른 같은 매핑" 으로 취급.

## 핵심 결정

**결정 1 — 스키마: `kind` 컬럼 추가 + 기존 `database_id` 컬럼을 값 컬럼으로 재사용.**
- `project_notion_source` 에 `kind VARCHAR(16) NOT NULL DEFAULT 'database'` 추가. `database_id` 컬럼은 그대로 두되 **page 일 때는 여기에 page_id 를 저장**한다(둘 다 Notion object id 라 형식 동일, `String(256)` 재사용). 별도 `page_id` 컬럼을 만들지 않는 이유: nullable 2컬럼 상호배타 상태가 생겨 응집도가 낮아지고 repo `value_attr` 단일 컬럼 규약(`project_source_repository.py:31`)이 깨진다.
- **project PK 유지**의 함의: 한 프로젝트는 Notion DB **또는** page 중 하나만 가진다(둘 다 동시 불가). 사용자 요구("페이지도 등록 가능")에 부합하며, 여러 개 필요해지면 그때 `(project, kind, id)` 복합 PK 로 확장(YAGNI, ARCH_REVIEW 의 project_drive_source 확장 판단과 대칭).
- 하위호환: 기존 행은 `kind='database'` 로 백필 → 기존 database 매핑·resolver 동작 무변경.

**결정 2 — NotionSource: page 분기 추가, 블록 순회 로직 재사용.**
- 생성자에 `page_id: str | None = None` 추가(`database_id` 와 상호배타). `source_name` 은 `notion` 유지(document_meta.source 규약 불변).
- `_list_request_spec` 대신 **page 는 `list_pages()` 를 분기**: page_id 가 있으면 `_list_children(client, page_id)`(이미 `notion_source.py:164` 보유)로 최상위 자식 블록을 받아 **`type == "child_page"` 블록만 FileMeta 로** 목록화. 각 child_page 블록의 `id`(=하위 페이지 id)를 external_id, `child_page.title` 을 title 로. → 그 하위 페이지들이 검색 대상 문서가 된다.
- `fetch(external_id)` 는 **완전 무변경**: 이미 `_collect_block_text` 로 임의 page/block id 의 블록 트리를 평문화한다. child_page 의 external_id 로 그대로 fetch 됨.
- page 자신을 문서로 넣을지: child_page 목록만 대상으로 한다(Eat's Uhok 같은 허브 페이지 하위 문서 검색이 목적). page 본문 자체도 넣고 싶으면 후속.

**결정 3 — 마이그레이션: 필요(alembic revision 1개).**
- `op.add_column('project_notion_source', sa.Column('kind', sa.String(16), nullable=False, server_default='database'), schema='app')` 1줄. 기존 행 자동 백필. `server_default` 는 마이그레이션에만(모델 ORM default 는 두되 명시 등록을 강제하지 않음 — 값이 항상 서비스에서 주입되므로). `downgrade` 는 `drop_column`.
- `project_drive_source` 는 무관, 손대지 않음.

**결정 4 — source_factory: `build_notion_source` 를 kind 인지로 확장.**
- 시그니처를 `build_notion_source(settings, notion_id: str, kind: str = "database") -> NotionSource | None` 로 확장(기존 호출 하위호환: kind 생략 시 database). kind=="page" 면 `NotionSource(token=..., page_id=notion_id, ...)`, 아니면 `database_id=notion_id`.
- resolver `_notion_source_for` 를 `(notion_id, kind)` 키로 캐싱하고 `notion_row.kind` 를 넘긴다. `resolve_for_project`/`resolve_all` 이 `notion_row.database_id`(값 컬럼) + `notion_row.kind` 를 읽어 builder 에 전달. → resolver 가 page/db 를 투명하게 처리, 상위 refresh/search 경로는 무변경.

## 변경 지점 / developer 파일 목록

- 수정 `app/models/project_notion_source.py` — `kind: Mapped[str]` (`String(16)`, default `"database"`) 추가.
- 신규 alembic `alembic/versions/<rev>_add_kind_to_project_notion_source.py` — 위 add_column.
- 수정 `app/services/documents/notion_source.py` — 생성자 `page_id` 인자, `list_pages()` 의 page 분기(child_page 목록화 헬퍼 `_list_child_pages`), `_list_request_spec` 는 database 전용으로 유지.
- 수정 `app/services/documents/source_factory.py` — `build_notion_source(settings, notion_id, kind="database")`.
- 수정 `app/services/documents/project_source_resolver.py` — `_notion_source_for(notion_id, kind)` 캐시 키 확장, row 에서 kind 읽어 전달.
- 수정 `app/repositories/project_source_repository.py` — Notion repo 에 `upsert_kind(project, value, kind)` 또는 base `upsert` 에 optional `extra: dict` 추가(최소: NotionSourceService 가 kind 를 세팅하도록 얇은 override).
- 수정 `app/services/documents/project_source_service.py` — `NotionSourceService.register_page(project, page_id)` 와 기존 `register` 를 kind 세팅 포함으로. value 검증은 `_normalize_value` 재사용.
- 수정 `app/mcp_server.py` — 신규 도구 `register_notion_page(project, page_id)`. `remove_notion_source`/`list_notion_sources` 는 그대로 두 kind 를 함께 조회(list 항목에 `kind` 노출).
- 수정 `app/mcp_types.py` — `NotionSourceItem` 에 `kind: Literal["database","page"]`, `RegisterNotionPageResult`(project, page_id, status) 추가.
- 수정 `app/api/dependencies.py` — 변경 불필요(notion_source_service 이미 존재). 확인만.
- 신규 테스트 `tests/unit/test_notion_page_source.py` — child_page 목록화, page fetch, `register_notion_page` upsert/kind 저장, resolver 가 kind=page 로 NotionSource(page_id=) 를 만드는지(페이크 builder 호출 인자 단언), 기존 database 매핑 회귀.

## 미스코프(후속)
- 한 프로젝트에 DB+page 동시(복합 PK). page 본문 자체를 문서로 포함. Notion `/search` 워크스페이스 전역.
