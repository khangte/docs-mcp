# 아키텍처 문서 (Simplified)

본 문서는 `docs-mcp`의 핵심 설계 원칙과 구조를 정의한다. 검색 로직 상세는 `docs/search-flow.md`,
운영 절차는 `docs/operations.md`, 결정 기록은 `docs/adr/` 를 참고한다.

> 착수 시점의 초기 기획서는 `docs/archive/plan.md` 로 아카이브했다(FastAPI·`src/` 기반으로
> 현재 구조와 어긋나므로 **설계 근거로 인용하지 않는다**). ADR 의 `관련: plan.md §N` 인용이
> 갈 곳을 잃지 않게 보존만 한 것이다.

## 1. 설계 원칙

- **저장형 검색**: OpenAPI 문서를 실시간 프록시하지 않고, 로컬 DB(PostgreSQL + pgvector)에 색인하여 검색한다.
- **MCP 기반 인터페이스**: 클라이언트는 MCP 도구를 통해서만 문서에 접근한다.
- **관심사 분리**: 수집(Ingestor), 파싱(Parser), 색인(Indexer), 검색(Search) 레이어를 엄격히 분리한다.

## 2. 시스템 구조

```
          [Claude / MCP 클라이언트]                [OS 스케줄러]
                      |                     (systemd timer / cron)
   (자연어 질의, 도구 호출, 등록/재색인)                |
                      |                                |
                      v                                v
          +-----------------------+     +---------------------------+
          |      MCP 서버         |     |  동기화 배치 (원샷 CLI)   |
          |  (tools / adapters)   |     | app/scripts/              |
          |                       |     |   refresh_documents.py    |
          +-----------+-----------+     +-------------+-------------+
                      |                               |
                      +--------------+----------------+
                                     |
                                     v
                           +-------------------+
                           |  내부 서비스 계층 |
                           | (수집/파싱/색인/  |
                           |   검색/예시)      |
                           +---------+---------+
                                     |
                         +-----------+-----------+
                         v                       v
                +-----------------+      +------------------+
                | PostgreSQL +    |      | OpenAPI 원본 /   |
                | pgvector        |      | Drive / Notion   |
                | (저장/검색/이력,|      | (수집·조회 시점에|
                |  project 태깅)  |      |  만 접근)        |
                +-----------------+      +------------------+
```

## 3. 프로젝트 구조

```text
app/
├── bootstrap.py     # AppState 팩토리
├── composition.py   # 컴포지션 루트 (AppState/ServiceBundle/build_services)
├── mcp/             # MCP 서버 진입점
│   ├── server.py    # MCP 서버 (Claude Desktop 통합, 도구 등록은 tools/ 위임)
│   ├── tools/       # 도메인별 MCP 도구 정의 (documents/endpoints/sources)
│   ├── payloads.py  # MCP 도구 결과 → 응답 dict(TypedDict) 변환 순수 함수
│   └── types.py     # MCP 도구 응답 TypedDict 스키마
├── core/            # 공통 설정, DB 엔진, 예외 및 로깅
├── models/          # SQLAlchemy ORM 모델 (Base, Document, Chunk, DocumentMeta 등)
├── repositories/    # 데이터베이스 액세스 레이어 (CRUD)
├── schemas/         # Pydantic DTO (요청/응답 모델)
├── scripts/         # 운영 배치 스크립트 (원샷 CLI)
│   ├── diagnose_long_sections.py  # 과대 섹션 진단(운영 조사용)
│   ├── reembed.py            # 임베딩 모델/차원 교체 후 재임베딩
│   └── refresh_documents.py  # 문서 소스 주기 동기화(OS 스케줄러가 주기 소유)
└── services/        # 비즈니스 로직
    ├── project_scope.py  # project 필터 범위 해석 (documents/ 밖 공용 위치)
    ├── documents/   # Drive/Notion 협업 문서 소스 어댑터(sources/)·메타 캐시·검색
    ├── endpoints/   # 엔드포인트 상세 조회 서비스
    ├── examples/    # 호출 예시 코드 생성 서비스
    ├── indexer/     # 청크 생성 및 벡터 색인 서비스
    ├── ingestor/    # 문서 수집 및 동기화 서비스
    ├── parser/      # OpenAPI/Swagger 파서 및 정규화
    ├── schema_resolution/  # $ref 스키마 해석 서비스
    ├── search/      # 하이브리드 검색 서비스
    └── tags/        # 태그 집계 서비스
```

## 4. 레이어 정의 및 의존성

### 4-1. 레이어 역할

- `entry points`: `app/mcp/server.py` (MCP Server), `app/scripts/` (운영 배치 CLI)
- `services`: 도메인 로직 (수집, 파싱, 색인, 검색, 예시 생성)
- `repositories`: DB 접근 (SQLAlchemy)
- `models/schemas`: 데이터 모델 및 DTO (Pydantic)
- `core`: 공통 설정, 로깅, DB 세션

### 4-2. 의존 방향

`app.mcp.server` → `services` → `repositories` → `models`
`app.scripts.*` → `services` → `repositories` → `models`
_(단방향 유지, 역참조 및 순환 참조 금지. 배치는 MCP 계층을 거치지 않고 서비스 계층을 직접 호출한다 —
그래서 도구에서만 쓰이던 로직도 서비스 계층에 있어야 한다: `registered_resync.py`)_

## 5. 핵심 데이터 흐름

### 5-1. 문서 등록 및 색인

1. **Fetch**: 외부 문서(OpenAPI/Markdown/CSV) 수집 (해시 검사로 변경분 확인)
2. **Detect & Parse**: `doc_type` 자동 판별(`document_router.py`) 후 엔드포인트/스키마(OpenAPI) 또는
   섹션(Markdown/CSV)으로 정규화
3. **Index**: 검색 단위로 청크화하고 임베딩 생성(로컬 CPU 모델 또는 결정적 해시 폴백)
4. **Store**: PostgreSQL 및 pgvector에 최종 저장

### 5-2. 검색 (Hybrid Search)

- **Vector Search**: pgvector(cosine similarity, HNSW 인덱스)를 이용한 의미론적 검색
- **Keyword Search**: Postgres FTS(`to_tsquery` OR 매칭 + `ts_rank`, `chunk.text_tsv` 생성컬럼 + GIN 인덱스) 기반 키워드 검색
- **Rerank**: 키워드/벡터 결과를 RRF(Reciprocal Rank Fusion, `RRF_K=60`)로 순위 융합(기본 `rrf` 전략). 롤백용 `fallback` 전략은 키워드 우선·0건일 때만 벡터를 보조로 시도하는 배타적 분기이며, `hybrid_alpha` 가중합은 두 전략 어디에도 적용되지 않는 legacy 설정이다(과거 `SearchService` 하이브리드 전용 — 검색 로직(`app/services/`)에서는 미참조이며, `.env.example`/README 에서도 제거했다. config/bootstrap/composition 의 기본값 배선만 잔존한다)

### 5-3. 프로젝트 단위 격리

- 하나의 서버·하나의 DB가 여러 프로젝트를 함께 서비스한다. 문서 등록 시
  `project` 를 필수로 받아 저장하고, 조회·검색 도구는 선택적 `project` 필터로
  결과 범위를 좁힌다(생략 시 전체 프로젝트 대상, 하위 호환).
- Drive/Notion 어댑터는 고정 설정이 아니라 요청 시점에
  `project_drive_source`/`project_notion_source` 매핑(project → folder_id/
  database_id)으로부터 `ProjectSourceResolver` 가 만들어낸다. 서비스 계정
  자격증명(Drive)과 Integration Token(Notion)은 서버 전역에서 공유하고,
  프로젝트마다 달라지는 것은 폴더/DB 범위뿐이다.
- Drive 원문 조회 시 Google 네이티브 문서는 export API 로 평문 변환하고,
  PDF/DOCX/XLSX/PPTX 바이너리는 `alt=media` 로 다운로드한 뒤 MIME 타입별
  파서(`app/services/parser/`)로 라우팅해 텍스트를 추출한다. 매핑에 없는
  바이너리는 다운로드 자체를 하지 않고 즉시 실패시키며, `max_download_bytes`
  로 파싱 진입 전 과대 파일을 차단한다.

### 5-4. 주기 동기화 (배치)

- 협업 문서 메타 캐시는 `refresh_index` 도구로 즉시 갱신할 수도 있고, 같은 서비스 함수를
  호출하는 원샷 CLI(`app/scripts/refresh_documents.py`)를 OS 스케줄러가 주기적으로 돌려
  갱신할 수도 있다. **앱은 스케줄을 모른다** — MCP stdio 서버가 클라이언트 세션마다 뜨고
  지는 단명 프로세스라 서버 안에 스케줄러를 둘 수 없기 때문이다. 두 경로가 같은 서비스
  함수(`document_index_service.refresh`, `resync_registered_documents`)를 쓰므로 동작이
  갈리지 않는다.
- 두 축을 주기·비용이 달라 분리한다. **축 A(메타 캐시 동기화, 1시간)** 는 목록·제목·수정일만
  갱신하고 본문을 조회하지 않는다. **축 B(등록 문서 재색인, `--include-registered`, 1일 1회)**
  는 `source_url` 이 있는 문서마다 원본을 재fetch·재파싱·재임베딩한다.
- 틱이 주기보다 길어져 겹치는 것은 Postgres advisory lock 으로 막는다(새 의존성 0, 프로세스
  종료 시 자동 해제). 락 키는 두 축이 다르다 — 같은 키면 무거운 축 B 가 가벼운 축 A 를 굶긴다.
- 설계·실측 근거: `docs/architect-review/32-refresh-index-batch-automation.md`,
  운영 방법(타이머 유닛·cron·실행 환경 함정·종료코드)은 `docs/operations.md` "자동 동기화" 절.

## 6. MCP 도구 계약 (Interface)

**OpenAPI 문서**

1. `list_documents`: 등록된 문서 목록 조회 (`project` 필터 가능)
2. `register_document`: 신규 문서 등록 및 색인 (`project` 필수)
3. `search_endpoints`: 자연어 질의 기반 API 검색 (Hybrid, `project`/`document_id` 필터 가능)
4. `get_endpoint_details`: 특정 API의 상세 명세(파라미터, 스키마 등) 및 호출 예시 조회
5. `resolve_ref`: `$ref` 컴포넌트 스키마 필드 펼치기 (`project`/`document_id` 필터 가능)
6. `list_tags`: 등록 문서의 태그 목록 조회 (`project`/`document_id` 필터 가능)

**협업 문서 (Google Drive / Notion)**

7. `search_documents`: Drive/Notion 문서 검색 (`project` 필터, 결과 부족 시 `query_variants` 로 후보 필터 확장 가능)
8. `get_document`: 협업 문서 원문 실시간 조회
9. `refresh_index`: 협업 문서 메타 캐시 동기화 (`project`/`source` 필터, `include_registered`+`force` 로 URL 기반 `Document` 재동기화 가능)

**프로젝트→소스 매핑 (Drive 3종, Notion 4종)**

10. `register_drive_source` / `register_notion_source`: 프로젝트에 Drive 폴더/Notion DB 매핑 등록
11. `register_notion_page`: 프로젝트에 Notion 허브 페이지 매핑 등록(지정 페이지 하위 페이지·데이터베이스를 재귀 탐색(최대 4단계)해 검색 대상으로 삼음, Drive 에는 대응 도구 없음)
12. `list_drive_sources` / `list_notion_sources`: 매핑 목록 조회
13. `remove_drive_source` / `remove_notion_source`: 매핑 삭제(멱등)

**리소스**

14. `document://{document_id}/raw`: 등록된 OpenAPI 문서의 원문(JSON/YAML) 조회

전체 17개 도구의 인자·반환 필드는 `docs/operations.md` "제공되는 도구 전체 목록" 을 참고한다.

## 7. 기술 스택 및 보안

- **Language/Framework**: Python 3.11+, `fastmcp`
- **Database**: PostgreSQL + pgvector (HNSW index), Alembic 마이그레이션
- **임베딩**: 로컬 CPU 모델(`sentence-transformers`, `LocalEmbeddingProvider`, 기본
  `intfloat/multilingual-e5-small`) 우선 사용, 모델 로드 실패 또는 테스트 환경에서는
  `HashEmbeddingProvider`(결정적 해시)로 자동 폴백
- **Auth**: 현재 관리 API/MCP 모두 별도 인증 없음(로컬/신뢰 환경 전제, API Key 인증은 TODO)
- **`project` 필터는 검색 범위 축소 도구일 뿐 보안 경계가 아니다**: 인증·접근 제어를 수행하지 않으며, 서버에 접근 가능한 누구나 `project` 필터 없이 모든 프로젝트의 데이터를 조회할 수 있다.
- **Observability**: JSON 구조화 로깅 기반 (trace_id 추적)

## 8. ADR (Architecture Decision Records)

주요 결정 사항은 `docs/adr/`에 기록한다.

- ADR-0001: 저장형 검색 구조 채택
- ADR-0002: pgvector 기반 하이브리드 검색 도입
- ADR-0003: MCP 도구의 읽기 전용 경계 유지
- ADR-0004: 임베딩 프로바이더를 관리형 API 에서 로컬 CPU 모델로 전환
