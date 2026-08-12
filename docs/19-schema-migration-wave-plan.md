# 19. 스키마 마이그레이션 물결 설계 (project_source 병합 + 죽은 컬럼 제거)

lead 최종 결정(2026-08-12) 3건을 **하나의 alembic 리비전**으로 묶어 설계한다.
구현은 developer 담당. 이 문서는 그대로 따라갈 수 있는 단위로 정리한다.

- 결정 1: `project_drive_source`+`project_notion_source` → 단일 `project_source`
  병합(옵션B, PK `(project, source_type, location)`).
- 결정 2: `api_endpoint.operation_id`, `api_response.example_json` 제거.
- 결정 3: `document_sync_history` **존치**(향후 배치적재 로그 재사용, 그때 조회
  경로도 함께 신설 — 이번 물결에선 스키마만 유지, 손대지 않음).

현행 HEAD: `e2bd26b83408`. 이 리비전은 그 위에 얹는다.

---

## 0. 선결 스코프 결정 — PK 의 `location` 과 "프로젝트당 소스 개수"

`project_drive_source`/`project_notion_source` 는 현재 **PK=project** →
프로젝트당 소스 1개(재등록 시 값 교체)다. 새 PK `(project, source_type,
location)` 는 구조적으로 **프로젝트당 같은 타입 소스 복수**를 허용한다. 여기서
동작 정책을 확정해야 developer 가 헤매지 않는다.

**권고(이번 물결 기본): 현행 "타입당 1개" 의미 유지.**

- 서비스 계층에서 `(project, source_type)` 단위 upsert 로 취급한다:
  기존 행이 있으면 `location`(+notion `kind`)만 갱신하고 `created_at` 보존,
  없으면 새로 insert. → 기존 "register 재호출 = 값 교체" 의미 그대로.
- PK 의 `location` 은 이 정책 아래선 **동일 소스 중복 등록 방지 + 향후 확장
  대비** 역할만 한다(멀티 소스 기능 붙일 때 스키마 재마이그레이션 불필요).
- **진짜 멀티 소스(프로젝트당 drive 폴더 N개)** 는 resolver/index/search/tool/
  payload 를 다 건드리는 별개 동작 변경이라 **이번 물결 범위 밖**. 결정 3의
  sync_history 와 같은 규율 — 스키마만 준비, 소비 경로는 필요할 때.

> lead 확인 요청 1: 위 "타입당 1개 유지" 스코프가 맞는지. (멀티 소스를
> 지금 원하면 범위가 커진다.)

**openapi 행 populate 여부**: `project_source` 는 `source_type='openapi'` 를
담을 수 있게 설계하되, **이번 물결에선 openapi 행을 채우지 않는다.** openapi
소스의 진실원은 여전히 `api_document.source_url` 이고, project_source 를 읽는
openapi 소비자가 아직 없다. 소비자 생기기 전 backfill 은 죽은 데이터(§17~18
에서 반대한 유형).

> lead 확인 요청 2: openapi 행 populate 는 후속으로 미루는 스코프 승인.

---

## 1. 대상 테이블 설계 — `app.project_source`

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `project` | `String(128)` NOT NULL | `PROJECT_MAX_LENGTH` 재사용 |
| `source_type` | `String(16)` NOT NULL | `drive`\|`notion`\|`openapi`(예약) |
| `location` | `String(256)` NOT NULL | folder_id / database_id / page_id (openapi 는 source_url — 미사용) |
| `kind` | `String(16)` NULL | notion 만 사용(`database`\|`page`). drive 는 NULL |
| `created_at` | `DateTime` NOT NULL | |
| `updated_at` | `DateTime` NOT NULL onupdate | |

- PK: `(project, source_type, location)`.
- 인덱스: **추가 안 함**. `source_type` 카디널리티 3, `resolve_all`/`list_*`
  는 소량 스캔이라 인덱스 이득 없음(YAGNI). 필요 시 후속.
- `location` 길이 256 은 현행 두 모델의 값 컬럼과 동일.

---

## 2. Alembic 리비전 (단일 파일, 순서 고정)

`down_revision = "e2bd26b83408"`. **upgrade 순서**:

1. **create** `app.project_source` (위 스키마, PK 포함).
2. **data copy** (타임스탬프 보존):
   - `INSERT INTO app.project_source (project, source_type, location, kind, created_at, updated_at)
     SELECT project, 'drive', folder_id, NULL, created_at, updated_at FROM app.project_drive_source`
   - `INSERT INTO app.project_source (project, source_type, location, kind, created_at, updated_at)
     SELECT project, 'notion', database_id, kind, created_at, updated_at FROM app.project_notion_source`
   - `op.execute()` 로 raw SQL, 스키마 접두사 `app.` 명시.
3. **drop** `app.project_notion_source`, `app.project_drive_source`.
4. **drop column** `app.api_endpoint.operation_id`.
5. **drop column** `app.api_response.example_json`.

**downgrade 순서**(역순):

1. `api_response.example_json` 재추가(`Text`, nullable) — 값은 복원 불가(수용).
2. `api_endpoint.operation_id` 재추가(`String(256)`, nullable) — 값 복원 불가.
3. 구 테이블 2개 재생성.
4. `project_source` → 구 테이블로 역복사(`WHERE source_type='drive'` → drive,
   `='notion'` → notion). `source_type='openapi'` 행이 있으면 downgrade 에서
   버려짐(이번 물결엔 openapi 행 없음 전제라 무해).
5. `project_source` drop.

> `document_sync_history` 는 이 리비전에서 **일절 손대지 않는다**(존치).

적용: `docker compose up -d postgres` 후 `uv run alembic upgrade head`.

---

## 3. 모델 파일 (`app/models`)

- **신규** `app/models/project_source.py`: `ProjectSource`(§1 스키마). `Base`·
  `PROJECT_MAX_LENGTH` 는 `app.models.openapi` 에서 재사용(현행 두 모델과 동일 패턴).
- **삭제** `app/models/project_drive_source.py`, `app/models/project_notion_source.py`.
- **수정** `app/models/openapi.py`:
  - `ApiEndpoint`: `operation_id` 컬럼 제거.
  - `ApiResponse`: `example_json` 컬럼 + `example` property(getter/setter) 제거.
    (`ApiRequestBody.example` 은 **유지** — 요청 예시 생성에서 읽힘.)
  - `create_all()` 의 import: `project_drive_source`/`project_notion_source`
    import 2줄 삭제, `import app.models.project_source` 1줄 추가.
- `app/models/document_meta.py`: 변경 없음.

---

## 4. Repository 개편 (`app/repositories/project_source_repository.py`)

현행: `ProjectSourceRepositoryBase`(Generic) + `ProjectDriveSourceRepository`
+ `ProjectNotionSourceRepository`(+`upsert_kind`). → **단일 클래스로 통합.**

`class ProjectSourceRepository`:
- `upsert(project, source_type, location, kind=None)`: `(project, source_type)`
  로 기존 행 조회 → 있으면 `location`/`kind`/`updated_at` 갱신(§0 정책, created_at
  보존), 없으면 insert. 커밋 안 함(서비스 담당, 현행 규약 유지).
- `get(project, source_type)`: 단건.
- `list_by_type(source_type)`: `project` 오름차순.
- `list_all()`: `project, source_type` 오름차순(결정적).
- `delete(project, source_type)`: 멱등 bool.

> 현행 `get(project)`(PK 단건 `session.get`)는 PK 가 복합·다행 가능이 돼서
> `(project, source_type)` 필터 쿼리로 바뀐다(더는 `session.get` 아님).

---

## 5. Service 개편 (`app/services/documents/project_source_service.py`)

현행: `ProjectSourceService`(Generic) + `DriveSourceService` +
`NotionSourceService`(`register_page`). → **단일 서비스로 통합.**

`class ProjectSourceService`(비제네릭):
- `register(project, source_type, location, kind=None)`: `normalize_project` +
  `_normalize_value(location)` + upsert + commit → `(row, "created"|"updated")`.
- `register_page(project, page_id)`: `register(project,'notion',page_id,kind='page')`
  의 얇은 편의(현행 notion page 의미 보존).
- `list_by_type(source_type)` / `get(project, source_type)` / `remove(project, source_type)`.
- notion database/page 상호배타는 `(project,'notion')` 단일행 upsert 로 자동 보존
  (현행과 동일 — 나중 등록이 이전을 덮음).
- `_normalize_value`/`VALUE_MAX_LENGTH`/`UpsertStatus` 는 그대로 이관.

---

## 6. Resolver 수정 (`project_source_resolver.py`)

- 생성자: `drive_repo`/`notion_repo`(2개) → `source_repo: ProjectSourceRepository`(1개).
- `resolve_for_project`: `source_repo.get(project,'drive')` /
  `source_repo.get(project,'notion')` 로 조회. 행의 `location`→folder_id/notion_id,
  `kind`→notion kind. 나머지(어댑터 빌드·캐시) 그대로.
- `resolve_all`: `source_repo.list_by_type('drive')` 후 `list_by_type('notion')`
  순회(현행 2-repo 순회와 동치). `row.location`/`row.kind` 참조로 교체.
- drive 행의 `kind` 는 NULL — drive 어댑터 빌드는 kind 안 씀, 무시.

---

## 7. Composition 수정 (`app/composition.py`)

- import: 구 repo/service 2쌍 → `ProjectSourceRepository`, `ProjectSourceService`.
- `ServiceBundle` 필드: `project_drive_source_repo`/`project_notion_source_repo`/
  `drive_source_service`/`notion_source_service` (4개) →
  `project_source_repo`/`project_source_service` (2개).
- `build_services`: repo/service 생성 2쌍 → 1쌍. resolver 인자
  `drive_repo=/notion_repo=` → `source_repo=`.
- `drive_source_builder`/`notion_source_builder`(테스트 주입 훅)는 resolver
  내부용이라 **변경 없음**.

---

## 8. MCP 도구 수정 (`app/mcp/tools/sources.py`, `payloads.py`, `types.py`)

**외부 계약(도구 이름·응답 dict 모양)은 유지** — 내부 호출만 통합 서비스로 교체.

- `sources.py`:
  - `register_drive_source`: `bundle.project_source_service.register(project,'drive',folder_id)`.
    응답 `{"project","folder_id":row.location,"status"}` 유지.
  - `register_notion_source`: `register(project,'notion',database_id,kind='database')`.
    응답 `database_id: row.location`.
  - `register_notion_page`: `project_source_service.register_page(project,page_id)`.
    응답 `page_id: row.location`.
  - `list_drive_sources`/`list_notion_sources`: `list_by_type('drive')`/`('notion')`
    또는 `get(project, type)`.
  - `remove_drive_source`/`remove_notion_source`: `remove(project,'drive')`/`('notion')`.
- `payloads.py` `_to_drive_source_item`/`_to_notion_source_item`: 입력 타입을
  `ProjectSource` 로. `folder_id`/`database_id` 는 `row.location`, notion `kind`
  는 `row.kind`. **출력 dict 키·모양 불변**(하위호환).
- `types.py`: `DriveSourceItem`/`NotionSourceItem` 등 응답 TypedDict 모양 불변.
  구 모델 import 만 정리.

---

## 9. 죽은 컬럼 write 경로 제거 (`app/services/indexer/indexer_service.py`)

- `_to_endpoint_entity`(≈131행): `ApiEndpoint(...)` 생성 인자에서
  `operation_id=parsed.operation_id` **삭제**.
- `_to_response_entity`(≈180행): `entity.example = parsed.example` **삭제**.
  (`_to_request_body_entity` 의 `entity.example = parsed.example` 는 **유지**.)
- 파서(`openapi_parser`/`swagger2_parser`)의 `ParsedEndpoint.operation_id`·
  `ParsedResponse.example` 필드는 **손대지 않는다**(DB 미결합·무해, 트리밍은
  선택적 후속). 이번 물결은 DB write 제거까지만.

---

## 10. 테스트·정합성 체크 (developer 수행)

- 구 모델/테이블 참조 정리: `grep -rn "project_drive_source\|project_notion_source\|
  ProjectDriveSource\|ProjectNotionSource" tests app` → 잔존 참조 전부 통합 대상으로 교체.
- 죽은 컬럼 참조: `grep -rn "operation_id\|\.example\b" tests` — response.example/
  operation_id 단정하는 테스트 있으면 제거/수정. request_body.example 테스트는 유지.
- 마이그레이션 왕복: `alembic upgrade head` → `downgrade -1` → `upgrade head`
  무오류. 업그레이드 후 `\d app.project_source`, 구 테이블 부재, 두 컬럼 부재 확인.
- 데이터 이전 검증: 업그레이드 전 구 테이블 행수 == 이전 후 project_source
  대응 행수(drive/notion 각각).
- 커밋은 파일/논리 단위 원자적, 한국어 메시지, 테스트 통과 후(프로젝트 규약).

---

## 부록. 영향 파일 요약

| 구분 | 파일 |
|---|---|
| 신규 | `app/models/project_source.py`, `alembic/versions/<new>_*.py` |
| 삭제 | `app/models/project_drive_source.py`, `app/models/project_notion_source.py` |
| 수정(모델) | `app/models/openapi.py` |
| 수정(repo/service) | `app/repositories/project_source_repository.py`, `app/services/documents/project_source_service.py` |
| 수정(소비처) | `app/services/documents/project_source_resolver.py`, `app/composition.py` |
| 수정(MCP) | `app/mcp/tools/sources.py`, `app/mcp/payloads.py`, `app/mcp/types.py` |
| 수정(write 제거) | `app/services/indexer/indexer_service.py` |
| 불변 | `app/models/document_meta.py`, `document_sync_history` 관련 전부 |

**lead 확인 필요 2건**: §0 "타입당 1개 유지" 스코프, openapi 행 populate 후속 연기.
