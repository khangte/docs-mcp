# 아키텍처 문서 (Simplified)

본 문서는 `docs-mcp`의 핵심 설계 원칙과 구조를 정의한다. 상세 기획은 `docs/product_specs/plan.md`를 참고한다.

## 1. 설계 원칙

- **저장형 검색**: OpenAPI 문서를 실시간 프록시하지 않고, 로컬 DB(PostgreSQL + pgvector)에 색인하여 검색한다.
- **MCP 기반 인터페이스**: 클라이언트는 MCP 도구를 통해서만 문서에 접근한다.
- **관심사 분리**: 수집(Ingestor), 파싱(Parser), 색인(Indexer), 검색(Search) 레이어를 엄격히 분리한다.

## 2. 시스템 구조

```
         [관리자]                         [Claude / MCP 클라이언트]
            |                                       |
     (OpenAPI URL 등록,                   (자연어 질의, 도구 호출)
      재색인 트리거)                               |
            |                                       |
            v                                       v
   +-------------------+                 +-----------------------+
   |   관리 HTTP API   |                 |      MCP 서버         |
   |  (FastAPI routes) |                 |  (tools / adapters)   |
   +---------+---------+                 +-----------+-----------+
             |                                       |
             +------------------+--------------------+
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
├── bootstrap.py     # AppState 팩토리 (web/mcp 공유)
├── composition.py   # 컴포지션 루트 (AppState/ServiceBundle/build_services)
├── web/             # FastAPI 웹 진입점
│   ├── main.py      # FastAPI 앱 팩토리 + uvicorn 진입점
│   ├── dependency_providers.py  # FastAPI 의존성 주입 함수
│   └── routes/      # FastAPI 라우트
├── mcp/             # MCP 서버 진입점
│   ├── server.py    # MCP 서버 (Claude Desktop 통합, 도구 등록은 tools/ 위임)
│   ├── tools/       # 도메인별 MCP 도구 정의 (documents/endpoints/sources)
│   └── types.py     # MCP 도구 응답 TypedDict 스키마
├── core/            # 공통 설정, DB 엔진, 예외 및 로깅
├── models/          # SQLAlchemy ORM 모델 (Base, ApiDocument 등)
├── repositories/    # 데이터베이스 액세스 레이어 (CRUD)
├── schemas/         # Pydantic DTO (요청/응답 모델)
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

- `entry points`: `app/main.py` (FastAPI), `app/mcp_server.py` (MCP Server)
- `api`: HTTP 라우터 및 의존성 주입 (`app/api/routes`)
- `services`: 도메인 로직 (수집, 파싱, 색인, 검색, 예시 생성)
- `repositories`: DB 접근 (SQLAlchemy)
- `models/schemas`: 데이터 모델 및 DTO (Pydantic)
- `core`: 공통 설정, 로깅, DB 세션

### 4-2. 의존 방향

`api / mcp_server` → `services` → `repositories` → `models`
_(단방향 유지, 역참조 및 순환 참조 금지)_

## 5. 핵심 데이터 흐름

### 5-1. 문서 등록 및 색인

1. **Fetch**: 외부 문서(OpenAPI/Markdown/CSV) 수집 (해시 검사로 변경분 확인)
2. **Detect & Parse**: `doc_type` 자동 판별(`document_router.py`) 후 엔드포인트/스키마(OpenAPI) 또는
   섹션(Markdown/CSV)으로 정규화
3. **Index**: 검색 단위로 청크화하고 임베딩 생성(Gemini API 또는 결정적 해시 폴백)
4. **Store**: PostgreSQL 및 pgvector에 최종 저장

### 5-2. 검색 (Hybrid Search)

- **Vector Search**: pgvector(cosine similarity, HNSW 인덱스)를 이용한 의미론적 검색
- **Keyword Search**: 토큰 매칭(현재는 애플리케이션 레벨 구현, tsvector 전환은 TODO) 기반 키워드 검색
- **Rerank**: 두 결과를 가중 합산(`hybrid_alpha`)하여 최종 순위 결정

### 5-3. 프로젝트 단위 격리

- 하나의 서버·하나의 DB가 여러 프로젝트를 함께 서비스한다. 문서 등록 시
  `project` 를 필수로 받아 저장하고, 조회·검색 도구는 선택적 `project` 필터로
  결과 범위를 좁힌다(생략 시 전체 프로젝트 대상, 하위 호환).
- Drive/Notion 어댑터는 고정 설정이 아니라 요청 시점에
  `project_drive_source`/`project_notion_source` 매핑(project → folder_id/
  database_id)으로부터 `ProjectSourceResolver` 가 만들어낸다. 서비스 계정
  자격증명(Drive)과 Integration Token(Notion)은 서버 전역에서 공유하고,
  프로젝트마다 달라지는 것은 폴더/DB 범위뿐이다.

## 6. MCP 도구 계약 (Interface)

**OpenAPI 문서**

1. `list_documents`: 등록된 문서 목록 조회 (`project` 필터 가능)
2. `register_document`: 신규 문서 등록 및 색인 (`project` 필수)
3. `search_endpoints`: 자연어 질의 기반 API 검색 (Hybrid, `project`/`document_id` 필터 가능)
4. `get_endpoint_details`: 특정 API의 상세 명세(파라미터, 스키마 등) 및 호출 예시 조회
5. `resolve_ref`: `$ref` 컴포넌트 스키마 필드 펼치기 (`project`/`document_id` 필터 가능)
6. `list_tags`: 등록 문서의 태그 목록 조회 (`project`/`document_id` 필터 가능)

**협업 문서 (Google Drive / Notion)** 7. `search_documents`: Drive/Notion 문서 검색 (`project` 필터 가능) 8. `get_document`: 협업 문서 원문 실시간 조회 9. `refresh_index`: 협업 문서 메타 캐시 동기화 (`project`/`source` 필터 가능)

**프로젝트→소스 매핑 (Drive/Notion 각 3종, 대칭)** 10. `register_drive_source` / `register_notion_source`: 프로젝트에 Drive 폴더/Notion DB 매핑 등록 11. `list_drive_sources` / `list_notion_sources`: 매핑 목록 조회 12. `remove_drive_source` / `remove_notion_source`: 매핑 삭제(멱등)

## 7. 기술 스택 및 보안

- **Language/Framework**: Python 3.11+, FastAPI, `fastmcp`
- **Database**: PostgreSQL + pgvector (HNSW index), Alembic 마이그레이션
- **LLM/임베딩**: Gemini API(`GeminiLLMProvider`, `GeminiEmbeddingProvider`) 우선 사용,
  API 키 미설정 시 `TemplateLLMProvider`/`HashEmbeddingProvider`로 자동 폴백
- **Auth**: 현재 관리 API/MCP 모두 별도 인증 없음(로컬/신뢰 환경 전제, API Key 인증은 TODO)
- **`project` 필터는 검색 범위 축소 도구일 뿐 보안 경계가 아니다**: 인증·접근 제어를 수행하지 않으며, 서버에 접근 가능한 누구나 `project` 필터 없이 모든 프로젝트의 데이터를 조회할 수 있다.
- **Observability**: JSON 구조화 로깅 기반 (trace_id 추적)

## 8. ADR (Architecture Decision Records)

주요 결정 사항은 `docs/adr/`에 기록한다.

- ADR-0001: 저장형 검색 구조 채택
- ADR-0002: pgvector 기반 하이브리드 검색 도입
- ADR-0003: MCP 도구의 읽기 전용 경계 유지
