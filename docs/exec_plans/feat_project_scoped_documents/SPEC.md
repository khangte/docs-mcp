# docs-mcp: 프로젝트 단위 문서 격리 (project scoped documents)

## 개요

docs-mcp 는 현재 하나의 MCP 서버 프로세스와 하나의 Postgres DB 를 여러 개발 프로젝트가 공유한다. 그런데 `api_document` / `document_meta` 어디에도 "어느 프로젝트의 문서인가"를 나타내는 정보가 없어서, 프로젝트 A 에서 Claude Code 로 `search_endpoints("주문 조회")` 를 호출하면 프로젝트 B 의 OpenAPI 엔드포인트가 같이 나온다. Google Drive 역시 `DOCS_MCP_DRIVE_FOLDER_ID` 환경변수 하나로 폴더가 서버 전역 고정이라, 프로젝트마다 다른 Drive 폴더를 볼 수 없다.

이 작업은 **DB 도 서버 프로세스도 분리하지 않고**, `project` 라는 문자열 태그 하나로 논리적 격리를 구현한다. 문서 등록 시 소속 프로젝트를 필수로 받고, 조회/검색 도구는 `project` 필터로 범위를 좁힌다. Drive 폴더는 DB 에 프로젝트→폴더 매핑을 두어 프로젝트별로 다른 폴더를 보게 한다.

**대상 사용자**: 여러 개발 프로젝트를 각각 별도의 Claude Code 세션에서 작업하면서, 같은 docs-mcp 서버를 공유하는 개발자.

**처리 데이터**: OpenAPI/Markdown/CSV 명세 문서(`api_document` 및 그 하위 엔드포인트/스키마/섹션/청크), Google Drive/Notion 협업 문서 메타데이터(`document_meta`), 그리고 신규 프로젝트→Drive 폴더 매핑(`project_drive_source`).

## 목표

1. 한 프로젝트에서 등록한 문서가 다른 프로젝트의 검색 결과에 **섞이지 않는다**.
2. 프로젝트마다 **서로 다른 Google Drive 폴더**를 검색 범위로 지정할 수 있다. 서비스 계정 자격증명은 서버 전역으로 계속 공유한다.
3. 프로젝트마다 **서로 다른 Notion 데이터베이스**를 검색 범위로 지정할 수 있다. Integration Token 은 서버 전역으로 계속 공유한다(Drive 의 서비스 계정과 대칭).
4. 기존 DB 에 이미 등록된 문서/메타는 **삭제·재등록 없이** 계속 조회 가능해야 한다(하위 호환).
5. Project 라는 도메인 엔터티를 만들지 않는다. `project` 는 관리 테이블도 CRUD 도 없는 **단순 문자열 식별자**다(YAGNI).

## 비목표 (이번 스코프에서 하지 않는 것)

- **DB/스키마 분리를 하지 않는다.** DB 1개, 서버 프로세스 1개, `app` 스키마 1개를 그대로 유지한다.
- **Project 관리 테이블·CRUD 도구를 만들지 않는다.** `create_project`, `list_projects`, `delete_project` 같은 도구는 없다. 프로젝트는 문서를 등록하는 순간 암묵적으로 "존재하게" 되고, 그 프로젝트의 문서가 전부 삭제되면 암묵적으로 사라진다.
- **인증·권한 부여(authorization)를 하지 않는다.** `project` 는 보안 경계가 아니라 **검색 범위 필터**다. 다른 프로젝트 이름을 알고 그 값을 넘기면 그 프로젝트 문서가 조회된다. 이 서버는 개인 개발 환경에서 로컬로 도는 것을 전제하며, 프로젝트 간 신뢰 경계가 필요하면 별도 서버 인스턴스를 띄우는 것이 올바른 해법이다. 이 사실을 README 에 명시한다.
- **Notion Integration Token 자체는 프로젝트별로 분리하지 않는다.** Drive 의 서비스 계정과 동일하게 서버 전역 1개(`DOCS_MCP_NOTION_TOKEN`)를 계속 공유한다. 프로젝트별로 달라지는 것은 **어느 database_id 를 검색 범위로 쓰는지**뿐이다(Drive 의 "계정은 전역, 폴더는 프로젝트별"과 대칭).
- **FastAPI 라우트(`app/api/routes/`)는 이번 스코프에서 변경하지 않는다.** 프로젝트 격리는 MCP 도구 계약의 문제이고, FastAPI 라우트는 관리/디버깅용 보조 경로다. 단 `sync_service.register()` 시그니처가 바뀌므로 **호출부인 `app/api/routes/documents.py` 는 컴파일이 깨지지 않도록 최소 수정**한다(아래 기능 2 참조).

## 현재 구조에서 격리를 막는 지점

| # | 지점 | 파일 | 문제 |
|---|---|---|---|
| 1 | `ApiDocument` 모델 | `app/models/openapi.py` | 소속 프로젝트 컬럼이 없음 |
| 2 | `DocumentMeta` 모델 | `app/models/document_meta.py` | 소속 프로젝트 컬럼이 없음 |
| 3 | `SyncService.register()` | `app/services/ingestor/sync_service.py` | 프로젝트를 입력받지 않음 |
| 4 | `DocumentRepository.list_all()` | `app/repositories/document_repository.py` | 전체 문서를 무조건 반환 |
| 5 | `ChunkRepository.list_endpoint_chunks()` | `app/repositories/chunk_repository.py` | `document_id` 필터만 있음 |
| 6 | `EndpointRepository.list_all()` | `app/repositories/endpoint_repository.py` | `document_id` 필터만 있음 |
| 7 | `DocumentMetaRepository.search_by_tokens()` / `list_by_source()` / `list_all()` | `app/repositories/document_meta_repository.py` | `source` 필터만 있음 |
| 8 | `AppState.document_sources` | `app/api/dependencies.py` | 부트스트랩 시점에 **1회** 만들어지는 고정 dict. Drive 폴더가 프로세스 수명 동안 하나로 고정됨 |
| 9 | `build_document_sources()` | `app/services/documents/source_factory.py` | `settings.drive_folder_id` 단일 값만 읽음 |
| 10 | MCP 도구 시그니처 | `app/mcp_server.py` | `project` 파라미터가 어디에도 없음 |

**8번이 이번 작업에서 가장 구조적인 변경**이다. Drive 폴더가 프로젝트마다 다르려면 `GoogleDriveSource` 인스턴스가 요청 시점에 DB 매핑을 읽어 프로젝트별로 만들어져야 하는데, 현재는 `AppState` 에 고정 dict 로 박혀 있다.

## 데이터 흐름

### A. 문서 등록 경로 (project 필수)

```
[Claude Code / 프로젝트 "shop-api"]
        │  register_document(project="shop-api", raw_document=...)
        ▼
(1) MCP 도구 register_document          ─ project 를 필수 인자로 받음
        │                                 (누락 시 FastMCP 가 인자 오류)
        ▼
(2) SyncService.register(project=...)   ─ project 정규화·검증
        │                                 (빈 문자열/공백 → ValidationError)
        ▼
(3) ApiDocument(project="shop-api", ...) ─ NOT NULL 컬럼에 저장
        │
        ▼
(4) IndexerService.index_document()     ─ 엔드포인트/스키마/섹션/청크 생성
        │                                 (하위 테이블에는 project 를 복제하지
        │                                  않는다 — document_id JOIN 으로 도달)
        ▼
[api_document.project = "shop-api" 로 영속화]
```

### B. OpenAPI 조회/검색 경로 (project 필터)

```
[Claude Code / 프로젝트 "shop-api"]
        │  search_endpoints(query="주문 조회", project="shop-api")
        ▼
(1) 범위 결정 (DocumentScope)
        │   document_id 가 있으면 → 그 문서 1건으로 확정 (project 무시,
        │                            단 project 도 함께 왔고 불일치하면 오류)
        │   document_id 가 없고 project 가 있으면 → 해당 project 문서 집합
        │   둘 다 없으면 → 전체 (기존 동작 유지)
        ▼
(2) 저장소 질의     ─ ChunkRepository.list_endpoint_chunks(
        │               document_id=..., project=...)
        │             api_chunk ⋈ api_document 조인 후 project 로 필터
        ▼
(3) 키워드 우선 / 벡터 보조 (기존 로직 그대로)
        ▼
[다른 프로젝트 엔드포인트가 섞이지 않은 후보 리스트]
```

`list_documents`, `list_tags`, `resolve_ref` 도 동일한 범위 결정 규칙을 쓴다.

### C. Drive 프로젝트별 폴더 등록 + 갱신 경로 (신규)

```
[Claude Code / 프로젝트 "shop-api"]
        │  register_drive_source(project="shop-api", folder_id="1AbC...")
        ▼
(1) ProjectDriveSourceRepository.upsert()   ─ project 를 PK 로 upsert
        │                                     (프로젝트당 폴더 1개)
        ▼
[project_drive_source: ("shop-api", "1AbC...") 영속화]

--------- 메타 캐시 갱신 (refresh_index) ---------

[refresh_index(project=None)]  ← project 생략 시 등록된 전 프로젝트 순회
        │
        ▼
(1) 대상 결정
        │   Drive: project_drive_source 전 행을 읽어
        │          (project, folder_id) 마다 GoogleDriveSource 를 새로 만든다
        │          (서비스 계정 자격증명은 전역 1개를 공유)
        │   Notion: project_notion_source 전 행을 읽어
        │           (project, database_id) 마다 NotionSource 를 새로 만든다
        │           (Integration Token 은 전역 1개를 공유)
        │           매핑이 없는 project 는 Notion 대상에서 제외된다
        ▼
(2) 소스별 list_files()  ─ 실패해도 그 소스만 failed_sources 에 담고 계속
        │                  (기존 부분 실패 허용 정책 유지)
        ▼
(3) document_meta upsert  ─ (project, source, external_id) 로 매칭
        │                    같은 파일이 두 프로젝트 폴더에 공유돼 있으면
        │                    프로젝트마다 별개 행이 된다(의도된 동작)
        ▼
[document_meta 최신화 완료, 반환값에 프로젝트별 집계 합산]
```

### D. Drive/Notion 검색 경로 (project 필터)

```
search_documents(query="로그인", project="shop-api")
        │
        ▼
(1) 1단계 후보 압축  ─ DocumentMetaRepository.search_by_tokens(
        │                tokens, source=..., project="shop-api", query="로그인")
        │                → SQL WHERE project = 'shop-api' 추가
        │                → title/url 토큰 ILIKE + collapse(query) 공백제거 ILIKE OR
        ▼
(2) 후보 본문 fetch  ─ 각 후보 행의 (project, source) 로 어댑터를 고른다.
        │  + 점수 계산       Drive 후보면 그 project 의 folder_id 로 만든
        │                GoogleDriveSource 를 사용. _title_score/_body_score 는
        │                토큰 겹침 비율과 collapse 부분문자열 점수를 max 로 합성
        ▼
[다른 프로젝트 Drive 폴더의 문서가 섞이지 않은 결과]
```

**공백 변형 질의 매칭**: `collapse()`(공백 제거+소문자화) 보조 키를 1단계
(`search_by_tokens` 의 title/url ILIKE)와 2단계(`_title_score`/`_body_score`)가
**같은 함수 하나로 공유**한다. '트러블슈팅'↔'트러블 슈팅'처럼 공백 유무만 다른
질의/제목은 토큰 집합이 달라져 순수 토큰 매칭으로는 후보에서 빠지는데, 두 계층이
동일한 collapse 판단을 써야 1단계 필터와 2단계 점수가 어긋나지 않는다. 2단계에서
collapse 부분문자열 매칭은 **토큰 1개 겹침과 동등한 상한**(`1/token_count`)만 주어,
`max()` 합성 시 기존 다중 토큰 겹침 순위를 뒤집지 않는다.

### 핵심 데이터 스키마

| 엔터티 | 변경 | 상세 |
|---|---|---|
| `api_document` | **컬럼 추가** | `project VARCHAR(128) NOT NULL DEFAULT 'default'`, 인덱스 `ix_api_document_project` |
| `document_meta` | **컬럼 추가** | `project VARCHAR(128) NOT NULL DEFAULT 'default'`, 기존 UNIQUE `(source, external_id)` → **`(project, source, external_id)` 로 교체**, 인덱스 `ix_document_meta_project` |
| `project_drive_source` | **신규 테이블** | `project VARCHAR(128) PK`, `folder_id VARCHAR(256) NOT NULL`, `created_at TIMESTAMP NOT NULL`, `updated_at TIMESTAMP NOT NULL` |
| `project_notion_source` | **신규 테이블** | `project VARCHAR(128) PK`, `database_id VARCHAR(256) NOT NULL`, `created_at TIMESTAMP NOT NULL`, `updated_at TIMESTAMP NOT NULL` |
| `api_endpoint` / `api_schema` / `api_section` / `api_chunk` / `document_sync_history` | **변경 없음** | 전부 `document_id` 로 `api_document` 에 매달려 있으므로 JOIN 으로 project 에 도달한다. project 를 복제하면 정합성 관리 지점만 늘어난다 |

`project_drive_source`/`project_notion_source` 를 `project` 단일 PK 로 두는 이유: 사용자 요구는 "프로젝트마다 **다른** 폴더/DB"이지 "프로젝트마다 **여러** 폴더/DB"가 아니다. 프로젝트당 1개로 시작하면 upsert 로 갱신이 자명해지고, 나중에 다중 소스가 필요해지면 `(project, folder_id)`/`(project, database_id)` 복합 PK 로 확장하면 된다(YAGNI). 두 테이블을 하나로 합치지 않는 이유는 Drive 와 Notion 이 서로 다른 자격증명·클라이언트를 쓰고, 한쪽만 있고 다른 쪽은 없는 프로젝트가 흔할 것이기 때문이다(둘을 합치면 nullable 컬럼 2개짜리 낮은 응집도 테이블이 된다).

### project 값 규칙

- 타입: `str`, 최대 128자.
- 정규화: 앞뒤 공백 제거(`strip()`). 대소문자는 **변환하지 않는다**(`shop-api` 와 `Shop-API` 는 다른 프로젝트). 폴더명을 그대로 쓰는 용법에서 대소문자 강제 변환은 예측을 깨뜨린다.
- 검증: 정규화 후 빈 문자열이면 `ValidationError`. 128자 초과면 `ValidationError`. 그 외 문자 제한은 두지 않는다(폴더명·저장소명 등 사용자가 이미 쓰는 식별자를 그대로 받기 위함).
- 상수 `DEFAULT_PROJECT = "default"` 를 `app/models/openapi.py` 에 정의하고, 마이그레이션 백필과 Notion 소스가 공유한다.

## 하위 호환 방침 (기존 데이터 처리)

리포지토리의 alembic 이력은 `dfbe6143212a` → `b336d80334c8` → `059294da406f` 3개이고, 이 서버는 이미 실사용 중이다(사용자가 "여러 프로젝트가 하나의 DB 를 공유한다"고 진술). 즉 **`api_document` 와 `document_meta` 에 기존 행이 존재한다고 가정해야 한다.**

기존 `b336d80334c8` 마이그레이션이 `doc_type` 을 추가할 때 쓴 `nullable=False, server_default='openapi'` 패턴을 그대로 따른다. 별도의 "nullable 로 추가 후 UPDATE 백필 후 NOT NULL 로 변경" 3단계를 쓰지 않는 이유는, Postgres 11+ 에서 `server_default` 가 있는 `NOT NULL` 컬럼 추가는 테이블 재작성 없이 즉시 완료되므로 3단계로 나눌 실익이 없기 때문이다.

**결정: `server_default='default'` 로 NOT NULL 컬럼을 한 번에 추가한다.**

- 기존 `api_document` 행은 전부 `project = 'default'` 가 된다.
- 기존 `document_meta` 행은 전부 `project = 'default'` 가 된다.
- `server_default` 는 **마이그레이션에만 남기고 ORM 모델에는 두지 않는다.** 모델에 default 를 두면 애플리케이션 코드가 project 를 빠뜨려도 조용히 `'default'` 로 저장돼 격리가 소리 없이 깨진다. 신규 등록은 반드시 명시적 project 를 요구한다(기능 2 검증 기준).
- 기존 행을 특정 프로젝트로 옮기고 싶은 사용자는 `project='default'` 로 조회한 뒤 재등록하거나 직접 SQL 로 UPDATE 한다. 이번 스코프에서 마이그레이션 도구는 제공하지 않는다(README 에 안내만 기재).

**`document_meta` UNIQUE 제약 교체 순서**(마이그레이션 안에서):
1. `project` 컬럼을 `server_default='default'` 로 추가.
2. 기존 UNIQUE `uq_document_meta_source_external` 을 DROP.
3. 새 UNIQUE `uq_document_meta_project_source_external` 을 `(project, source, external_id)` 로 생성.

2번을 1번보다 먼저 하면 안 된다(중복 방지 창이 생김). 다만 `default` 로 백필된 기존 행들은 `(default, source, external_id)` 가 여전히 유일하므로 새 제약 생성이 실패하지 않는다.

## 미구성 프로젝트 규칙 (Drive/Notion 매핑이 없는 project)

`register_drive_source`/`register_notion_source` 로 소스를 등록하지 않은 프로젝트에서 `search_documents(project=X)` 또는 `refresh_index(project=X)` 를 호출하면:

- 둘 중 하나라도 등록돼 있으면 → 등록된 소스만 대상으로 정상 동작한다(다른 프로젝트의 소스는 섞이지 않는다).
- 둘 다 없으면 → 기존 `NO_SOURCE_CONFIGURED_MESSAGE` 와 구별되는 `IntegrationError`("no document source is registered for project: X ... call register_drive_source or register_notion_source first")를 던진다. **"소스 미설정"과 "프로젝트 미등록"을 구별**하는 것이 핵심이다(기존 SPEC 의 "결과 없음 vs 미설정" 구별 원칙과 같은 계열).

## MCP 도구 계약 (변경 후 최종 형태)

| 도구 | project 파라미터 | 변경 |
|---|---|---|
| `register_document` | **필수** `project: str` | 시그니처 변경 |
| `list_documents` | optional `project: str \| None = None` | 필터 추가 |
| `search_endpoints` | optional `project: str \| None = None` | 필터 추가 |
| `list_tags` | optional `project: str \| None = None` | 필터 추가 |
| `resolve_ref` | optional `project: str \| None = None` | 필터 추가 |
| `search_documents` | optional `project: str \| None = None` | 필터 추가 |
| `refresh_index` | optional `project: str \| None = None` | 필터 추가 |
| `get_endpoint_details` | 없음 | `endpoint_id` 가 이미 문서를 특정하므로 불필요 |
| `get_document` | 없음 | `(source, external_id)` 가 이미 문서를 특정 |
| `document://{document_id}/raw` | 없음 | `document_id` 가 이미 문서를 특정 |
| `register_drive_source` | **필수** | **신규** |
| `list_drive_sources` | optional | **신규** |
| `remove_drive_source` | **필수** | **신규** |
| `register_notion_source` | **필수** | **신규** |
| `list_notion_sources` | optional | **신규** |
| `remove_notion_source` | **필수** | **신규** |

`get_endpoint_details` / `get_document` / raw 리소스에 project 를 넣지 않는 이유: 이미 단일 문서를 특정하는 식별자를 받고 있어 필터가 무의미하며, "project 를 넣었는데 불일치" 케이스만 추가로 만들어낸다. 이들은 프로젝트 격리의 관심사가 아니라 **후속 상세 조회**이고, 상세 조회 대상은 앞선 검색(이미 project 로 필터된)에서 얻은 ID 다.

## 기능 목록

---

### 기능 1: `project` 컬럼 스키마 확장과 마이그레이션

- **설명**: `api_document` 와 `document_meta` 에 `project` 컬럼을 추가하고, 신규 테이블 `project_drive_source` 를 만든다. 기존 행은 `'default'` 로 백필한다.
- **변경 지점**:
  - `app/models/openapi.py` — `ApiDocument.project: Mapped[str]`(`String(128)`, `nullable=False`, **ORM default 없음**), `__table_args__` 에 `Index("ix_api_document_project", "project")` 추가. 모듈 상수 `DEFAULT_PROJECT = "default"`, `PROJECT_MAX_LENGTH = 128` 추가.
  - `app/models/document_meta.py` — `DocumentMeta.project: Mapped[str]`(`String(128)`, `nullable=False`), `__table_args__` 의 UniqueConstraint 를 `("project", "source", "external_id")` 로 교체하고 이름을 `uq_document_meta_project_source_external` 로 변경, `Index("ix_document_meta_project", "project")` 추가.
  - `app/models/project_drive_source.py` — **신규 파일**. `ProjectDriveSource` 모델(`project` PK, `folder_id`, `created_at`, `updated_at`). `app/models/openapi.py` 의 `create_all()` 이 이 모듈도 import 해 `Base.metadata` 등록을 보장해야 한다(기존 `document_meta` 와 동일 패턴).
  - `app/models/project_notion_source.py` — **신규 파일**. `ProjectNotionSource` 모델(`project` PK, `database_id`, `created_at`, `updated_at`). 동일하게 `create_all()` 에 등록.
- **마이그레이션 파일** (alembic revision 2개, `059294da406f` 뒤에 체인):
  1. `<rev1>_add_project_to_documents.py`
     - `op.add_column('api_document', sa.Column('project', sa.String(128), nullable=False, server_default='default'), schema='app')`
     - `op.create_index('ix_api_document_project', 'api_document', ['project'], schema='app')`
     - `op.add_column('document_meta', ... server_default='default' ...)`
     - `op.drop_constraint('uq_document_meta_source_external', 'document_meta', type_='unique', schema='app')`
     - `op.create_unique_constraint('uq_document_meta_project_source_external', 'document_meta', ['project', 'source', 'external_id'], schema='app')`
     - `op.create_index('ix_document_meta_project', ...)`
     - `downgrade()` 는 정확히 역순으로 되돌린다.
  2. `<rev2>_add_project_source_mappings.py`
     - `op.create_table('project_drive_source', ...)` (`project` PK, `folder_id`, `created_at`, `updated_at`, `schema='app'`)
     - `op.create_table('project_notion_source', ...)` (`project` PK, `database_id`, `created_at`, `updated_at`, `schema='app'`)
     - `downgrade()` 는 두 테이블 모두 `op.drop_table(...)`.
  - 2개로 나누는 이유: 1번은 기존 테이블의 파괴적 변경(UNIQUE 교체)을 포함해 롤백 리스크가 있고, 2번은 순수 신규 테이블 2개라 무해하다. 실패 지점과 롤백 단위를 분리한다. Drive/Notion 매핑 테이블을 하나의 리비전에 함께 넣는 이유는 둘 다 "신규 무해 테이블"이라는 같은 리스크 등급이기 때문이다.
- **입력**: `uv run alembic upgrade head` (기존 데이터가 있는 DB).
- **출력**: 기존 모든 행이 `project = 'default'` 인 상태로 스키마 확장 완료.
- **검증 기준**:
  - `project` 없이 등록된 기존 행을 흉내 낸 데이터(마이그레이션 이전 스키마로 삽입)가 upgrade 후 `project = 'default'` 로 조회된다.
  - `upgrade → downgrade → upgrade` 를 연속 실행해도 오류 없이 완료된다.
  - `document_meta` 에 같은 `(source, external_id)` 이지만 project 가 다른 두 행을 삽입할 수 있다(새 UNIQUE 제약 확인).
  - 같은 `(project, source, external_id)` 조합의 두 번째 삽입은 무결성 오류다.
  - `ApiDocument(project=...)` 를 생략하고 flush 하면 DB NOT NULL 위반이 발생한다(ORM default 가 없어야 함을 보장).

---

### 기능 2: `register_document` 의 project 필수화

- **설명**: 문서 등록 시 소속 프로젝트를 반드시 받고, 검증·정규화한 뒤 `api_document.project` 에 저장한다.
- **변경 지점**:
  - `app/services/ingestor/sync_service.py`
    - `SyncService.register(*, project: str, source_url, raw_document, title_override, doc_type)` — `project` 를 **첫 번째 키워드 인자로 필수** 추가.
    - 신규 모듈 함수 `normalize_project(value: str | None, *, required: bool) -> str | None` 를 `app/services/documents/project_scope.py`(신규)에 두고 여기서 호출한다. 정규화·검증 로직을 한 곳에 모아 MCP 도구·서비스·저장소가 같은 규칙을 쓰게 한다.
    - `ApiDocument(...)` 생성 시 `project=normalized_project` 전달.
    - `resync()` 는 project 를 받지 않는다. 기존 문서의 project 를 그대로 유지한다(재동기화가 소속을 바꾸는 것은 의도치 않은 부작용).
  - `app/mcp_server.py` — `register_document(project: str, source_url=None, ...)`. `project` 를 **첫 번째 파라미터**로 두어 FastMCP 가 required 로 노출하게 한다. docstring 의 Args 에 "이 문서가 속할 프로젝트 식별자(보통 프로젝트 폴더명). 이후 검색에서 이 값으로 범위를 좁힌다" 를 명시. 반환 dict 에 `"project"` 키 추가.
  - `app/mcp_types.py` — `RegisterDocumentResult` 에 `project: str` 추가, `DocumentSummary` 에 `project: str` 추가.
  - `app/api/routes/documents.py` — `register_document` 라우트가 `services.sync_service.register(...)` 를 호출하므로 깨진다. `app/schemas/documents.py` 의 `RegisterDocumentRequest` 에 `project: str = DEFAULT_PROJECT` 를 추가하고 그대로 전달한다. (FastAPI 는 관리/디버깅 보조 경로이므로 기본값을 허용한다. MCP 도구만 필수다.) `RegisterDocumentResponse` / `DocumentSummary` 에도 `project` 필드를 추가한다.
  - **주의**: `sync_service.register()` 호출부는 `tests/` 에 약 25곳 존재한다(`tests/unit/test_sync_service.py`, `test_indexer_service.py`, `test_rag_service.py`, `test_schema_ref_resolver.py`, `test_tag_catalog_service.py`, `test_endpoint_candidate_search.py`, `test_endpoint_details_service.py`, `test_request_example_service.py`, `test_search_service.py`, `tests/conftest.py`). 전부 project 명시로 갱신해야 한다.
- **입력**: `{project: str, source_url: str|null, raw_document: str|dict|null, title_override: str|null, doc_type: str|null}`
- **출력**: `{document_id, title, version, doc_type, project, endpoints_count, sections_count, chunks_count, status}` 또는 `ErrorPayload`.
- **검증 기준**:
  - `project` 인자를 생략하고 도구를 호출하면 등록이 성공하지 않는다.
  - `project=""` 또는 `project="   "` 는 `code="validation_error"` 에러 페이로드를 반환하고, 문서가 저장되지 않는다(부분 저장 없음).
  - `project="  shop-api  "` 로 등록하면 DB 에는 `"shop-api"` 로 저장된다.
  - 129자 project 는 `validation_error` 다. 128자는 성공한다(경계값).
  - `project="Shop-API"` 와 `project="shop-api"` 로 각각 등록한 문서는 서로 다른 project 값을 갖는다(대소문자 보존).
  - 등록 응답의 `project` 가 입력 정규화 결과와 일치한다.
  - `resync()` 후에도 문서의 project 가 등록 시점 값 그대로다.

---

### 기능 3: OpenAPI 조회/검색 도구의 project 필터

- **설명**: `list_documents`, `search_endpoints`, `list_tags`, `resolve_ref` 에 optional `project` 필터를 추가한다. `document_id` 와 `project` 가 함께 오면 `document_id` 가 우선하되, 두 값이 모순되면 명시적으로 오류를 낸다.
- **범위 결정 규칙** (`app/services/documents/project_scope.py` 의 공용 함수 `resolve_document_scope(document_repo, document_id, project)` 로 구현하고 4개 서비스가 공유한다 — DRY):
  1. `document_id` 가 주어짐 → 문서 존재를 검증(`DocumentNotFoundError`). `project` 도 함께 주어졌고 그 문서의 `project` 와 다르면 `DocumentNotFoundError`(문서 자체를 못 찾은 것과 같은 취급 — 다른 프로젝트 문서의 존재를 알려주지 않는다). 일치하거나 project 가 없으면 그 문서 1건으로 범위 확정.
  2. `document_id` 없이 `project` 만 → 그 project 로 범위 확정. **project 에 문서가 0건이어도 오류가 아니다**(빈 결과). "미등록 project" 라는 개념이 없기 때문(비목표 참조).
  3. 둘 다 없음 → 전체(기존 동작 유지).
- **변경 지점**:
  - `app/repositories/document_repository.py`
    - `list_all(project: str | None = None)` — project 필터 추가.
    - `list_by_project(project: str)` 는 만들지 않는다(`list_all(project=...)` 로 충분).
  - `app/repositories/chunk_repository.py`
    - `list_endpoint_chunks(document_id=None, project=None)` — project 가 주어지면 `ApiChunk` ⋈ `ApiDocument` 조인 후 `ApiDocument.project == project` 필터. **Python 이 아니라 SQL 로** 내린다(기존 커밋 `perf: 1단계 후보 압축의 제목 매칭을 SQL 로 내림` 과 같은 원칙).
    - `list_by_endpoint_filter(..., project=None)` — 같은 방식으로 project 필터 추가(`SearchService` 경로도 일관되게 동작시키기 위함).
  - `app/repositories/endpoint_repository.py`
    - `list_all(document_id=None, project=None)` — project 는 `ApiDocument` 조인으로 필터.
  - `app/services/search/endpoint_candidate_search.py`
    - `CandidateSearchOptions` 에 `project: str | None = None` 필드 추가(frozen dataclass 이므로 기본값 필수).
    - `_validate()` 에서 `resolve_document_scope()` 호출로 교체.
    - `_endpoint_chunks()` 가 project 를 저장소에 전달.
  - `app/services/tags/tag_catalog_service.py` — `list_tags(document_id=None, project=None)`.
  - `app/services/schemas/schema_ref_resolver.py`
    - `resolve(ref, document_id=None, project=None)`.
    - `_find_schema()` 에서 document_id 가 없고 project 가 있으면 `self._document_repo.list_all(project=project)` 만 순회한다(현재는 `list_all()` 전체 순회). **여러 프로젝트에 동명 스키마가 있을 때 다른 프로젝트 스키마가 선택되는 버그를 막는 핵심 지점.**
  - `app/mcp_server.py` — 4개 도구에 `project: str | None = None` 파라미터 추가 및 docstring 갱신.
  - `app/mcp_types.py` — `DocumentSummary` 에 `project: str` 추가(기능 2와 공유).
- **입력/출력**:
  - `list_documents(project?) -> list[DocumentSummary]` (원소에 `project` 필드 포함)
  - `search_endpoints(query, top_k=5, document_id?, project?) -> EndpointSearchResponse`
  - `list_tags(document_id?, project?) -> TagListResult`
  - `resolve_ref(ref, document_id?, project?) -> ResolvedSchemaResult`
- **검증 기준**:
  - 프로젝트 A 와 B 에 각각 문서를 등록한 뒤 `list_documents(project="A")` 는 A 문서만 반환한다.
  - `search_endpoints(query, project="A")` 결과의 모든 `endpoint_id` 가 A 문서 소속이다. B 문서에만 있는 path 로 검색해도 A 범위에서는 0건이다.
  - project 를 생략하면 A·B 문서가 모두 후보에 나온다(하위 호환).
  - `list_tags(project="A")` 는 B 에만 있는 태그를 포함하지 않는다.
  - A·B 두 문서에 **같은 이름의 컴포넌트 스키마**가 있을 때 `resolve_ref(ref, project="A")` 는 항상 A 문서의 스키마를 반환한다(반복 호출 시 결정성 포함).
  - `document_id`(A 문서) + `project="B"` 조합은 `code="document_not_found"` 에러 페이로드다.
  - `document_id`(A 문서) + `project="A"` 조합은 정상 동작한다.
  - 존재하지 않는 project 로 검색하면 오류가 아니라 빈 결과다.
  - `project` 필터가 적용된 검색에서, 키워드 후보가 0건이면 벡터 보조 단계에서도 다른 프로젝트 청크가 후보 집합에 들어가지 않는다(범위 축소가 1단계뿐 아니라 폴백 경로에도 적용됨).

---

### 기능 4: 프로젝트→Drive 폴더 / Notion DB 매핑 저장소와 관리 도구

- **설명**: 프로젝트마다 다른 Drive 폴더·Notion 데이터베이스를 쓸 수 있도록 매핑을 DB 에 저장하고, 이를 등록/조회/삭제하는 MCP 도구를 신설한다. Drive 와 Notion 은 동일한 패턴을 반복하므로 공용 저장소/서비스 기반을 만들고 각각 얇게 특수화한다.
- **변경 지점**:
  - `app/models/project_drive_source.py` — 기능 1에서 만든 모델(`project` PK, `folder_id`, `created_at`, `updated_at`).
  - `app/models/project_notion_source.py` — **신규**. `ProjectNotionSource` 모델(`project` PK, `database_id`, `created_at`, `updated_at`). `create_all()` 이 이 모듈도 import 하도록 등록.
  - `app/repositories/project_source_repository.py` — **신규**. 제네릭 `ProjectSourceRepositoryBase[ModelT]` 로 `upsert(project, value_column) -> ModelT`, `get(project) -> ModelT | None`, `list_all() -> Sequence[ModelT]`(project 오름차순), `delete(project) -> bool` 공통 구현을 두고, `ProjectDriveSourceRepository`/`ProjectNotionSourceRepository` 가 대상 모델·컬럼명만 지정해 상속한다. 커밋은 서비스가 담당한다(기존 규약과 일치).
  - `app/services/documents/project_source_service.py` — **신규**. `ProjectSourceService` 가 project 정규화, value(folder_id/database_id) 검증(빈 문자열 금지, 256자 상한), upsert/삭제 시 커밋을 공통 처리하고, Drive/Notion 용 얇은 래퍼(`DriveSourceService`/`NotionSourceService`) 또는 `source_kind` 파라미터로 구분한다. 저장소를 MCP 도구가 직접 쓰지 않게 해 검증 지점을 하나로 모은다.
  - `app/api/dependencies.py` — `ServiceBundle` 에 `project_drive_source_repo`, `project_notion_source_repo`, `drive_source_service`, `notion_source_service` 추가 및 `build_services()` 에서 생성.
  - `app/mcp_server.py` — 신규 도구 6개(Drive 3 + Notion 3).
  - `app/mcp_types.py` — `DriveSourceItem`/`NotionSourceItem`(project, folder_id|database_id, created_at, updated_at), `DriveSourceListResult`/`NotionSourceListResult`(items), `RegisterDriveSourceResult`/`RegisterNotionSourceResult`(project, folder_id|database_id, status: `"created"|"updated"`), `RemoveDriveSourceResult`/`RemoveNotionSourceResult`(project, removed: bool) 추가.
- **입력/출력**:
  - `register_drive_source(project: str, folder_id: str) -> {project, folder_id, status}` — 같은 project 로 다시 호출하면 `status="updated"` 로 폴더가 교체된다.
  - `list_drive_sources(project: str | None = None) -> {items: [{project, folder_id, created_at, updated_at}]}`
  - `remove_drive_source(project: str) -> {project, removed: bool}` — 등록되지 않은 project 는 오류가 아니라 `removed: false`(멱등).
  - `register_notion_source(project: str, database_id: str) -> {project, database_id, status}` — Drive 와 동일한 upsert 의미.
  - `list_notion_sources(project: str | None = None) -> {items: [{project, database_id, created_at, updated_at}]}`
  - `remove_notion_source(project: str) -> {project, removed: bool}`
- **검증 기준**:
  - `register_drive_source("A", "folder-a")` 후 `list_drive_sources()` 에 `("A", "folder-a")` 가 정확히 1건 나온다.
  - `register_notion_source("A", "db-a")` 후 `list_notion_sources()` 에 `("A", "db-a")` 가 정확히 1건 나온다.
  - 같은 project 로 다른 folder_id/database_id 를 등록하면 행이 늘지 않고 값만 바뀌며 `status="updated"`, `updated_at` 이 갱신되고 `created_at` 은 유지된다.
  - `folder_id=""`/`database_id=""` 는 `validation_error` 이고 행이 생기지 않는다.
  - `project=""` 는 두 도구 모두 `validation_error` 다.
  - `remove_drive_source("없는프로젝트")`/`remove_notion_source("없는프로젝트")` 는 오류가 아니라 `{removed: false}` 를 반환한다.
  - `remove_drive_source("A")` 후 `list_drive_sources()` 에 A 가 없다. Notion 도 동일.
  - 한 프로젝트에 Drive 만 등록하거나 Notion 만 등록하는 것이 가능하다(둘 다 optional, 서로 독립).
  - `list_drive_sources()`/`list_notion_sources()` 반환 순서가 project 오름차순으로 결정적이다.

---

### 기능 5: 프로젝트별 Drive/Notion 어댑터 구성 (요청 시점 팩토리)

- **설명**: `AppState.document_sources` 고정 dict 구조를 바꿔, Drive·Notion 어댑터를 **요청 시점에 project → folder_id/database_id 매핑으로부터 만들어** 낸다. 서비스 계정 자격증명(Drive)과 Integration Token(Notion)은 서버 전역 1개를 계속 공유한다.
- **왜 필요한가**: 현재 Drive/Notion 어댑터는 `bootstrap_app_state()` 에서 1회 생성되어 프로세스 수명 내내 같은 폴더/DB 를 본다. `register_drive_source`/`register_notion_source` 로 소스를 새로 등록해도 서버를 재시작하지 않으면 반영되지 않는다. 이 기능이 없으면 기능 4는 DB 에 값만 쌓이고 실제로는 아무 효과가 없다.
- **변경 지점**:
  - `app/services/documents/source_factory.py`
    - 기존 `build_document_sources(settings)` 는 **삭제**한다(전역 고정 소스라는 개념 자체가 없어짐).
    - 기존 `_build_drive()` 는 `build_drive_source(settings, folder_id) -> GoogleDriveSource | None` 로 **folder_id 를 인자로 받는 형태**로 바꾼다(`settings.drive_folder_id` 직접 참조 제거).
    - 기존 `_build_notion()` 은 `build_notion_source(settings, database_id) -> NotionSource | None` 로 **database_id 를 인자로 받는 형태**로 바꾼다(`settings.notion_database_id` 직접 참조 제거, `settings.notion_token`/`notion_version` 은 계속 참조).
    - 신규 `build_drive_token_provider(settings) -> ServiceAccountTokenProvider | None` — 자격증명이 없으면 None. **프로젝트마다 새로 만들지 않고 재사용**한다(내부 credentials 캐싱 중복 방지).
    - **주의**: `tests/unit/test_document_source_factory.py` 가 `build_document_sources` 를 직접 테스트하므로 `build_drive_source`/`build_notion_source` 기준으로 재작성해야 한다.
  - `app/api/dependencies.py`
    - `AppState.document_sources` 필드를 **제거**한다(전역 고정 소스가 더 이상 없음).
    - `AppState` 에 `drive_token_provider: ServiceAccountTokenProvider | None` 과 `drive_source_builder: Callable[[str], DocumentSource] | None`, `notion_source_builder: Callable[[str], DocumentSource] | None` 추가. 뒤의 두 콜백은 각각 folder_id/database_id → 어댑터 팩토리로, **테스트에서 페이크를 주입하는 지점**이다(기존 `document_sources` 주입 패턴의 후계).
    - `build_services()` 에서 신규 `ProjectSourceResolver` 를 만들어 `DocumentSearchService` / `DocumentIndexService` 에 주입한다.
  - `app/services/documents/project_source_resolver.py` — **신규**. `ProjectSourceResolver` 가 다음을 담당한다.
    - `resolve_for_project(project: str) -> dict[str, DocumentSource]` — 그 project 의 Drive 어댑터(매핑이 있으면) + 그 project 의 Notion 어댑터(매핑이 있으면). 둘 다 optional, 둘 다 없으면 빈 dict.
    - `resolve_all() -> list[tuple[str, DocumentSource]]` — `(project, source)` 쌍 전체. Drive 는 `project_drive_source` 전 행에서, Notion 은 `project_notion_source` 전 행에서 각각 만든다.
    - 같은 folder_id/database_id 에 대한 어댑터를 resolver 인스턴스(= 요청 1회) 안에서 캐싱해 중복 생성을 막는다.
  - `app/core/config.py` — `drive_folder_id`/`notion_database_id` 필드는 **하위 호환용으로 유지**한다. 값이 설정돼 있으면 서버 기동 시 각각 `project_drive_source`/`project_notion_source` 에 `(DEFAULT_PROJECT, <값>)` 을 없을 때만 seed 한다(`bootstrap_app_state()` 에서 1회). 이렇게 해야 기존 `.env` 를 쓰던 사용자가 아무것도 하지 않아도 이전과 동일하게 동작한다. `notion_token`/`notion_version` 은 여전히 전역 설정으로 직접 참조된다(자격증명이므로 매핑 테이블에 넣지 않는다).
- **입력**: project 문자열 + DB 의 `project_drive_source`/`project_notion_source` 행 + 전역 설정(자격증명).
- **출력**: 그 project 에서 쓸 수 있는 `source_name -> DocumentSource` 매핑(`drive`/`notion` 키는 매핑이 있을 때만 존재).
- **검증 기준**:
  - 프로젝트 A(folder-a), B(folder-b) 를 등록한 뒤 `resolve_for_project("A")` 로 만든 Drive 어댑터가 folder-a 를, `"B"` 가 folder-b 를 대상으로 한다(페이크 빌더의 호출 인자로 단언). Notion 도 database_id 기준으로 동일하게 검증한다.
  - Drive 매핑이 없는 project 는 반환 매핑에 `drive` 키가 없다. Notion 매핑이 없는 project 는 `notion` 키가 없다. 한쪽만 있는 project 도 정상 동작한다.
  - **서버를 재시작하지 않고** `register_drive_source`/`register_notion_source` 로 소스를 바꾸면 다음 `search_documents`/`refresh_index` 호출이 새 소스를 쓴다.
  - 같은 요청 안에서 동일 folder_id/database_id 에 대해 어댑터 빌더가 1회만 호출된다(캐싱 확인).
  - 서비스 계정 자격증명이 없으면 Drive 어댑터가, `notion_token` 이 없으면 Notion 어댑터가 만들어지지 않는다. 매핑 행이 있어도 자격증명이 없으면 조용히 제외된다(에러가 아님 — 기존 "미구성" 정책과 일관).
  - `DOCS_MCP_DRIVE_FOLDER_ID`/`DOCS_MCP_NOTION_DATABASE_ID` 가 설정된 상태로 기동하면 각각 `project_drive_source`/`project_notion_source` 에 `("default", 그 값)` 이 1건씩 생기고, 재기동해도 중복 생성되지 않는다.

---

### 기능 6: `document_meta` 의 project 확장과 `refresh_index` 다중 소스 순회

- **설명**: 메타 캐시 행에 project 를 기록하고, `refresh_index` 가 등록된 모든 `project_drive_source` 를 순회하며 동기화하도록 바꾼다.
- **변경 지점**:
  - `app/repositories/document_meta_repository.py`
    - `find(project, source, external_id)` — 시그니처에 project 추가(새 UNIQUE 키와 일치).
    - `list_by_source(source, project=None)`.
    - `list_all(source=None, project=None)`.
    - `search_by_tokens(tokens, source=None, project=None, query="")` — SQL WHERE 에 project 조건 추가. `query`(원본 질의)를 받아 `collapse()`(공백 제거+소문자화)한 패턴을 title/url ILIKE OR 조건에 더한다. '트러블슈팅' 질의로 '트러블 슈팅' 제목을 잡는 공백 변형 매칭을 1단계 후보에서부터 살리기 위함(빈 문자열이면 이 조건은 생략).
    - 신규 `list_by_project_source(project, source)` — 갱신 시 "이 프로젝트의 이 소스" 기존 행 집합을 가져오는 전용 조회. `_refresh_source` 가 삭제 감지를 할 때 **다른 프로젝트 행까지 지우지 않도록** 하는 핵심 지점이다.
  - `app/services/documents/document_index_service.py`
    - 생성자가 `sources: list[DocumentSource]` 대신 `resolver: ProjectSourceResolver` 를 받는다.
    - `refresh(source: str | None = None, project: str | None = None)`.
    - 대상 결정 로직: `resolver.resolve_all()` 로 `(project, source)` 쌍을 얻고, `source`/`project` 인자로 필터한다.
    - `_refresh_source(project, document_source)` 로 서명 변경. 기존 행 조회는 `list_by_project_source(project, source_name)` 을 쓰고, 신규 행 생성 시 `project=project` 를 넣는다.
    - `_PartialRefreshError` 부분 실패 허용 정책과 `BATCH_SIZE` 커밋 경계는 **그대로 유지**한다. `failed_sources` 원소는 `"drive"` 대신 `"<project>/<source>"` 형식으로 바꿔 어느 프로젝트가 실패했는지 식별 가능하게 한다.
    - "대상이 하나도 없음" 판정: project 가 지정됐는데 그 project 에 Drive 매핑도 Notion 매핑도 없으면 "미구성 프로젝트 규칙"의 `IntegrationError` 를 던진다.
  - `app/services/documents/document_search_service.py`
    - 생성자가 `sources: dict[str, DocumentSource]` 대신 `resolver: ProjectSourceResolver` 를 받는다.
    - `DocumentSearchOptions` 에 `project: str | None = None` 추가.
    - `_select_candidates()` 가 project 를 저장소에 전달.
    - `_rank_with_body()` 가 후보 행의 **`row.project`** 로 어댑터를 고른다(`resolver.resolve_for_project(row.project)[row.source]`). project 마다 Drive 폴더가 다르므로 `row.source` 만으로는 부족하다.
    - `get_document(source, external_id)` 는 project 를 받지 않는다(계약 유지). 어댑터 선택은 **`document_meta` 에서 `(source, external_id)` 를 가진 행의 project** 로 결정하고(같은 external_id 가 여러 project 에 있으면 가장 최근 `last_synced_at` 행을 쓴다), 메타에 없으면 `DEFAULT_PROJECT` 의 해당 source 어댑터로 폴백한다.
    - `_require_configured()` 를 project 인지 방식으로 갱신.
  - `app/mcp_server.py` — `search_documents(query, top_k, source?, project?)`, `refresh_index(source?, project?)`.
  - `app/mcp_types.py` — `DocumentSearchItemPayload` 에 `project: str` 추가(호출 LLM 이 결과가 어느 프로젝트 것인지 알 수 있게).
- **입력/출력**:
  - `search_documents(query, top_k=5, source?, project?) -> {items: [{title, source, project, url, snippet, score}]}`
  - `refresh_index(source?, project?) -> {synced, added, updated, removed, failed_sources}`
- **검증 기준**:
  - 프로젝트 A(folder-a, db-a), B(folder-b, db-b) 에 각각 다른 문서가 든 상태에서 `refresh_index()` 를 호출하면 `document_meta` 에 A 문서는 `project="A"` 로, B 문서는 `project="B"` 로 들어간다(Drive·Notion 모두).
  - `refresh_index(project="A")` 는 B 의 Drive/Notion 어댑터 `list_files()` 를 **호출하지 않는다**(페이크 호출 카운트 0).
  - **같은 external_id 를 가진 문서가 A·B 두 소스(Drive 폴더 또는 Notion DB)에 공유돼 있으면 `document_meta` 에 2행이 생긴다**(project 별로 각 1행). 새 UNIQUE 제약의 직접 검증.
  - A 의 소스에서 문서가 삭제되면 A 행만 `removed` 되고 **B 의 같은 external_id 행은 남아 있다**(교차 프로젝트 삭제 방지 — 이번 작업에서 가장 깨지기 쉬운 지점). Drive·Notion 각각 검증.
  - A 의 Drive 가 실패하고 B 는 성공하면, `failed_sources == ["A/drive"]` 이고 B 의 변경분은 커밋돼 있다(부분 실패 허용 유지). Notion 실패도 동일 패턴(`"A/notion"`)으로 검증.
  - `search_documents(query, project="A")` 결과에 B 문서가 없다(Drive·Notion 모두).
  - `search_documents(query, project="A")` 실행 시 B 의 Drive/Notion 어댑터 `fetch()` 가 한 번도 호출되지 않는다.
  - 1단계 후보가 0건이면 어떤 프로젝트의 `fetch()` 도 호출되지 않는다(기존 검증 기준이 project 확장 후에도 유지됨).
  - Drive 매핑도 Notion 매핑도 없는 project 로 `refresh_index(project=X)` 를 호출하면 `NO_SOURCE_CONFIGURED_MESSAGE` 와 **구별되는** 메시지의 `IntegrationError` 가 나온다.
  - 한 프로젝트가 Drive 만 등록하고 Notion 은 등록하지 않은 상태에서 `refresh_index(project=X)` 는 Drive 만 갱신하고 정상 종료한다(부분 구성 허용).

---

### 기능 7: 프로젝트 간 격리 회귀 테스트 스위트

- **설명**: 격리가 깨지는 시나리오를 한 파일에 모아 회귀 테스트로 고정한다. 기능 1~6 각각의 단위 테스트와 별개로, **여러 계층이 함께 틀어질 때만 드러나는 누수**를 잡는 것이 목적이다.
- **변경 지점** (신규 테스트 파일):
  - `tests/integration/test_mcp_project_isolation.py` — MCP 도구 레벨에서 두 프로젝트(Drive+Notion 모두 다르게 구성)를 등록하고 전 도구를 교차 호출.
  - `tests/unit/test_project_scope.py` — `normalize_project()` / `resolve_document_scope()` 경계값.
  - `tests/unit/test_project_source_resolver.py` — 기능 5 검증 기준(Drive·Notion 모두).
  - `tests/unit/test_project_drive_source_repository.py` — 기능 4 검증 기준(Drive).
  - `tests/unit/test_project_notion_source_repository.py` — 기능 4 검증 기준(Notion).
  - `tests/conftest.py` — 프로젝트 2개가 등록된 상태를 만드는 픽스처(`two_project_services` 등)와, folder_id/database_id → 페이크 어댑터를 돌려주는 `fake_drive_source_builder`/`fake_notion_source_builder` 픽스처 추가. 기존 `fake_document_sources` 픽스처는 더 이상 전역 고정 소스를 표현하지 않으므로 project 별 빌더 방식으로 교체한다.
- **검증 기준**:
  - `register_document` → `search_endpoints` → `get_endpoint_details` → `resolve_ref` 전 흐름을 프로젝트 A 로만 수행했을 때, 어느 단계에서도 B 문서의 식별자가 노출되지 않는다.
  - `list_documents()`(필터 없음)는 A·B 를 모두 반환한다 — 즉 격리는 **필터를 줬을 때만** 적용되며 기본 동작이 조용히 바뀌지 않는다.
  - 프로젝트 A 의 문서를 삭제해도 B 문서와 B 의 `document_meta` 행, `project_drive_source` 행이 영향받지 않는다.
  - `project` 파라미터를 전혀 쓰지 않는 기존 호출 방식(등록 제외)이 여전히 동작한다(하위 호환 회귀).
  - 기존 테스트 스위트(`tests/` 전체)가 전부 통과한다. `register_document` 시그니처 변경으로 수정이 필요한 기존 테스트는 project 를 명시하도록 갱신하되, **검증 의도는 바꾸지 않는다**.

---

### 기능 8: 문서 갱신 (README / `.env.example`)

- **설명**: 프로젝트 격리 사용법과 그 한계를 문서에 반영한다. 잘못 이해하면 "보안 격리"로 오인할 수 있는 기능이라 문서화가 기능의 일부다.
- **변경 지점**:
  - `README.md`
    - "제공되는 도구" 표(AUTO-GENERATED 블록)에 `register_drive_source`, `list_drive_sources`, `remove_drive_source`, `register_notion_source`, `list_notion_sources`, `remove_notion_source` 6행 추가하고, project 파라미터가 생긴 도구들의 설명을 갱신한다.
    - "프로젝트 격리" 절 신설: (a) 여러 프로젝트가 한 서버·한 DB 를 공유하는 구조, (b) `project` 는 문자열 태그이며 **보안 경계가 아니라는 명시**, (c) 프로젝트별 Drive 폴더/Notion DB 등록 절차(`register_drive_source`/`register_notion_source` → `refresh_index`), (d) Drive 는 서비스 계정, Notion 은 Integration Token 이 **서버 전역 공유**이며 프로젝트별로 달라지는 것은 폴더/DB 범위뿐이라는 점, (e) 기존 문서는 `project="default"` 로 백필되며 옮기려면 재등록 또는 직접 SQL 이 필요하다는 점.
  - `.env.example` — `DOCS_MCP_DRIVE_FOLDER_ID`/`DOCS_MCP_NOTION_DATABASE_ID` 주석을 "레거시/기본 프로젝트용. 프로젝트별 폴더·DB 는 `register_drive_source`/`register_notion_source` 도구로 등록한다"로 갱신.
- **검증 기준**:
  - README 도구 표의 도구 목록이 `app/mcp_server.py` 의 `@mcp.tool()` 등록 목록과 정확히 일치한다(누락·잉여 없음).
  - README 에 "`project` 는 보안 경계가 아니다"라는 취지의 문장이 있다.
  - README 에 Drive/Notion 자격증명은 전역 공유, 폴더/DB 는 프로젝트별이라는 대칭 구조가 명시돼 있다.
  - `.env.example` 의 모든 키가 `app/core/config.py` 의 `Settings` 필드와 대응한다(신규 환경변수는 추가하지 않으므로 키 집합은 불변).

---

## 구현 순서 권고

1. 기능 1 (스키마·마이그레이션) — 다른 모든 기능의 전제.
2. 기능 2 (register 필수화) — 데이터가 project 를 갖게 됨.
3. 기능 3 (OpenAPI 필터) — 기능 2 데이터 위에서 검증 가능.
4. 기능 4 (매핑 저장소·도구) — 기능 5의 입력.
5. 기능 5 (요청 시점 어댑터 팩토리) — 기능 6의 전제.
6. 기능 6 (메타 캐시·refresh_index).
7. 기능 7 (격리 회귀 테스트) — 1~6 전체를 가로지름.
8. 기능 8 (문서).

3번까지 마치면 "OpenAPI 문서 격리"만으로도 독립적으로 의미가 있으므로, 중간 검증 지점으로 삼을 수 있다.

## 제약·불변식

- 모든 도구는 `DomainError`/`IntegrationError` 발생 시 기존과 동일한 `{"error": true, "code", "message"}` 포맷을 반환한다(변경 없음).
- `project` 필터가 없는 호출의 동작은 **바뀌지 않는다**(`register_document` 제외). 기존 사용자가 project 를 모르는 채로도 서버를 계속 쓸 수 있어야 한다.
- project 필터링은 **SQL 로** 수행한다. 전체 행을 적재한 뒤 Python 에서 거르지 않는다(기존 성능 원칙 계승).
- 하위 테이블(`api_endpoint`/`api_chunk` 등)에 `project` 를 복제하지 않는다. 정합성 관리 지점을 늘리지 않기 위함이며, 조인 비용은 `document_id` 인덱스와 `ix_api_document_project` 로 감당한다.
- Drive 서비스 계정 자격증명은 서버 전역 1개다. 프로젝트별로 접근 가능한 것은 **폴더**이지 계정이 아니다.
- `ProjectSourceResolver` 는 요청 스코프(`ServiceBundle`) 객체다. 전역 싱글턴으로 만들면 `register_drive_source` 반영이 지연된다.
- `SyncService.resync()` 와 `SyncService.delete()` 는 project 를 인자로 받지 않는다. 이미 `document_id` 로 대상이 확정돼 있다.

## 완료 기준

- [ ] `uv run alembic upgrade head` 가 기존 데이터가 있는 DB 에서 성공하고, 기존 행이 `project='default'` 로 백필된다.
- [ ] `register_document` 가 `project` 없이는 문서를 등록하지 않는다.
- [ ] 두 프로젝트에 각각 문서를 등록했을 때, `project` 필터를 준 모든 조회·검색 도구가 자기 프로젝트 문서만 반환한다.
- [ ] 프로젝트마다 다른 Drive 폴더를 `register_drive_source` 로, 다른 Notion DB 를 `register_notion_source` 로 등록할 수 있고, **서버 재시작 없이** 다음 호출부터 반영된다.
- [ ] `refresh_index()` 가 등록된 전 프로젝트의 Drive 폴더·Notion DB 를 순회하며, 한 프로젝트의 실패가 다른 프로젝트의 성공을 롤백시키지 않는다.
- [ ] 한 프로젝트에서 문서가 삭제돼도(Drive·Notion 무관) 다른 프로젝트의 같은 external_id 행이 지워지지 않는다.
- [ ] `project` 를 주지 않는 기존 호출(등록 제외)의 동작이 변하지 않는다.
- [ ] `ruff check app/` 무경고, `mypy app/` 무오류.
- [ ] `uv run python -m pytest tests/ -v` 전체 통과.
- [ ] README 에 프로젝트 격리 사용법과 "보안 경계 아님", Drive/Notion 자격증명 전역·폴더/DB 프로젝트별이라는 구조가 명시된다.
