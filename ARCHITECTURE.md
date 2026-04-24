# 아키텍처 문서

> 변경이 생기면 이 문서와 `docs/adr/` 를 먼저 갱신한 뒤 구현에 들어간다.
> 상세 기획과 트레이드오프는 `docs/product-specs/plan.md` 참고.

---

## 1. 개요 & 범위

docs-mcp 는 **OpenAPI 문서를 수집·정규화·색인한 저장소** 위에 **MCP 서버 인터페이스** 를 얹은 단일 서비스다.
클라이언트(Claude)는 원본 Swagger 문서를 직접 보지 않고, MCP 도구를 통해 검색/상세조회/예시생성 기능만 사용한다.

핵심 설계 축:

- **저장형 검색 구조**. 요청 시점에 외부 OpenAPI 서버로 프록시하지 않는다.
- **질의 해석은 Claude, 검색 실행은 서버**. 서버는 자연어 파싱을 하지 않는다.
- **한 저장소에 검색 시스템과 MCP 서버가 공존**. 단, 계층은 분리한다.

범위 외: 실제 운영 API 호출 대행, 문서 편집, 세분화된 접근 통제, 실시간 프록시.

---

## 2. 컨텍스트 다이어그램

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
           | PostgreSQL +    |      | OpenAPI 원본     |
           | pgvector        |      | HTTP 서버        |
           | (저장/검색/이력)|      | (수집 시점에만)  |
           +-----------------+      +------------------+
                    ^
                    | (선택) 임베딩 API — 외부 LLM 제공자
                    +-- (키는 core.config 의 비밀값)
```

신뢰 경계:

- 관리 HTTP API 는 내부망/인증 필요. 쓰기 권한.
- MCP 서버는 읽기 전용 인터페이스로 노출.
- OpenAPI 원본 서버는 불안정·외부. 타임아웃·재시도 필수.

---

## 3. 컴포넌트 & 레이어 구조

### 3-1. 레이어 정의

| 레이어 | 경로 | 책임 |
|--------|------|------|
| `core` | `src/core/` | 설정, 로깅, DB 세션, 공통 타입 |
| `models` | `src/models/` | SQLAlchemy ORM (스키마 정의) |
| `schemas` | `src/schemas/` | Pydantic DTO (입출력 계약) |
| `repositories` | `src/repositories/` | DB 접근만. 비즈니스 규칙 없음 |
| `services` | `src/services/{ingestor,parser,indexer,search,examples}/` | 도메인 로직 (수집/파싱/색인/검색/예시) |
| `api` | `src/api/routes/`, `src/api/dependencies.py` | 관리용 HTTP 진입점 |
| `mcp` | `src/mcp/{server.py,tools/,adapters/}` | MCP 도구 진입점 |

### 3-2. 의존 방향 (허용)

```
    api  ──┐
           ├──▶ services ──▶ repositories ──▶ models
    mcp  ──┘        │
                    └──▶ schemas (DTO 공유)
            core 는 모든 레이어에서 사용 가능
```

### 3-3. 금지 규칙

- `models` / `repositories` → `services` 참조 금지
- `services` → `api` / `mcp` 참조 금지 (진입점을 도메인이 알면 안 됨)
- `mcp.tools` → `repositories` 직접 호출 금지. 반드시 `services` 경유
- `api` 와 `mcp` 는 서로 호출하지 않는다. 공통 로직은 `services` 로 내린다
- 순환 의존 금지. 어기면 `ruff` 규칙/리뷰에서 차단

---

## 4. 주요 데이터 흐름

### 4-1. 문서 등록

```
관리자 → POST /documents { url }
      → api.routes.documents
      → services.ingestor.sync_service.register(url)
          1) openapi_fetcher.fetch(url)            # 원문 수집 (해시 계산)
          2) openapi_parser.parse(raw)             # 엔드포인트/스키마 정규화
          3) schema_normalizer.normalize(parsed)
          4) chunk_builder.build(normalized)       # 검색 단위 청크
          5) embedding_service.embed(chunks)       # 벡터 생성
          6) repositories.* 로 트랜잭션 저장
          7) document_sync_history 기록
      → 응답 { document_id, endpoints_count, indexed_at }
```

트랜잭션 경계: 수집(1) 은 외부 호출이므로 트랜잭션 밖. 저장(6) 은 한 트랜잭션으로 묶어 실패 시 전체 롤백.

### 4-2. 검색 (MCP)

```
Claude → tools.search_api_docs(query, top_k, filters?)
      → adapters.search_adapter
      → services.search.search_service.search(...)
          ├─ services.search.keyword_search  (tsvector GIN)
          └─ services.search.vector_search   (pgvector cosine)
          → 결과 병합·재랭킹 (가중치 설정값)
      → repositories.chunk_repository 로 조회
      → Claude 가 소비할 MCP 응답 포맷으로 변환
```

`get_endpoint_detail`, `generate_request_example` 은 같은 패턴으로 `endpoint_repository`, `request_example_service` 를 사용.

### 4-3. 재색인 / 동기화

```
트리거 3종:
  - 수동:      POST /sync/{document_id}
  - 주기:      스케줄러 (설정된 주기)
  - 변경감지:  재수집한 원문의 콘텐츠 해시가 이전과 다를 때만 재처리

흐름:
  sync_service.resync(document_id)
    → fetcher.fetch (해시 비교)
    → 변경 없음 → sync_history 에 "skipped" 기록 후 종료
    → 변경 있음 → parser → indexer → save (기존 row 업데이트/새 청크 교체)
                → sync_history 에 "reindexed" 기록
```

ETag/Last-Modified 는 참고용. 최종 판단은 해시.

---

## 5. 도메인 모델 (ER 요약)

```
api_document (1) ──< api_endpoint (1) ──< api_parameter
       │                    │
       │                    ├──< api_response
       │                    └──── api_request_body (1:1)
       │
       ├──< api_schema          (컴포넌트 스키마)
       ├──< api_chunk           (검색용 텍스트 + embedding)
       └──< document_sync_history

qa_history  (독립, 질의 이력 — 선택적)
```

핵심 제약:

- `api_document.source_url` UNIQUE
- `api_chunk` 는 참조 대상이 endpoint/schema 둘 중 하나 (`chunk_type` + `ref_id`)
- `api_chunk.embedding` 은 pgvector 컬럼. 인덱스는 **HNSW (기본)** 로 시작, 데이터량 증가 시 재평가
- `api_chunk.text` 에 `tsvector` 파생 컬럼 + GIN 인덱스 (키워드 검색)
- `document_sync_history(document_id, created_at DESC)` 복합 인덱스 — 최근 이력 조회 핫패스

분리 원칙: **원문 (검증·재색인용) / 정규화 (관계형 구조) / 청크 (검색 전용)** 는 서로 독립적으로 교체 가능해야 한다.

---

## 6. MCP 도구 계약

### 6-1. `search_api_docs`

```
입력:
  query: str                       # 필수
  top_k: int = 5                   # 1~20
  method?: "GET"|"POST"|...        # 필터
  tag?: str
  status_code?: int

출력:
  {
    "query": str,
    "count": int,
    "items": [
      { "endpoint_id": str, "method": str, "path": str,
        "summary": str, "score": float, "snippet": str }
    ]
  }
```

- 서버는 `query` 를 자연어 파싱하지 않는다. 토크나이즈·임베딩만 수행.
- 정렬은 서버 책임 (키워드 + 벡터 가중 합).

### 6-2. `get_endpoint_detail`

```
입력:  { endpoint_id: str }
출력:  { method, path, summary, description,
         parameters[], request_body?, responses[],
         schemas[], security[], examples[] }
```

- `schemas` 는 해당 엔드포인트가 참조하는 컴포넌트만 포함 (전체 문서 X).

### 6-3. `generate_request_example`

```
입력:  { endpoint_id: str, format: "curl"|"fetch"|"axios"|"python" }
출력:  { format: str, code: str, notes?: str }
```

- 예시는 저장된 정규화 스키마 기반으로 결정적으로 생성. 외부 API 호출 없음.

### 공통 규칙

- 모든 도구는 읽기 전용. 상태 변경 MCP 도구는 현재 범위 외.
- 에러는 MCP 표준 오류 형태로 반환. 스택트레이스 노출 금지.
- 도구 스키마 변경은 **하위 호환 + ADR** 을 요구한다.

---

## 7. 런타임 프로세스 & 포트

### 7-1. 프로세스 구성

| 프로세스 | 실행 | 역할 |
|----------|------|------|
| API 서버 | `uvicorn src.main:app` (FastAPI) | 관리 HTTP API |
| MCP 서버 | `python -m src.mcp.server` | Claude 연결 |
| 백그라운드 | 초기: API 서버 내 APScheduler / 후기: 별도 워커 | 주기 동기화 |

초기엔 단일 프로세스에 통합 가능. **도메인/레이어 경계를 지켜두면 나중에 프로세스 분리 비용이 작다.**

### 7-2. 포트 규칙

`scripts/start_task.sh` 가 worktree 별로 `.ports` 를 생성한다. task 격리 목적이므로 실행 시 이 파일을 로드해 쓴다.

```
API_PORT   # FastAPI
DB_PORT    # PostgreSQL (로컬 컨테이너 등)
MCP_PORT   # MCP 서버
```

프로덕션 기본값은 `core.config` 의 환경변수로 지정하고, 로컬/CI 는 `.ports` 를 우선한다.

### 7-3. 마이그레이션

- Alembic 이 스키마 원천. 수동 DDL 금지.
- 서비스 기동 전 `alembic upgrade head` 가 선행돼야 한다.

---

## 8. 폴더 구조

```
docs-mcp/
├── CLAUDE.md                      ← 오케스트레이터 (Claude Code가 자동으로 읽음)
├── AGENTS.md                      ← 에이전트 목록 및 역할
├── ARCHITECTURE.md                ← 본 문서
├── .claude/
│   └── harness_workflow.md        ← 하네스 실행 흐름 (0~5단계)
├── agents/
│   ├── planner.md
│   ├── generator.md
│   ├── evaluator.md
│   ├── evaluation_criteria.md
│   └── report_template.md
├── scripts/
│   └── start_task.sh              ← worktree + EXEC_PLAN + 포트 + 로그 생성
├── docs/
│   ├── product-specs/plan.md      ← 프로젝트 기획 문서
│   ├── exec-plans/
│   │   ├── active/                ← (현재 미사용. 진행 중 산출물은 worktree 루트)
│   │   └── completed/<type>-<task>/
│   │       ├── EXEC_PLAN.md
│   │       ├── SPEC.md
│   │       ├── SELF_CHECK.md
│   │       └── QA_REPORT.md
│   └── references/                ← 외부 레퍼런스 (LLM-friendly 덤프 등)
├── src/
│   ├── main.py
│   ├── core/ models/ schemas/ repositories/
│   ├── services/{ingestor,parser,indexer,search,examples}/
│   ├── api/{routes/,dependencies.py}
│   └── mcp/{server.py,tools/,adapters/}
├── tests/
├── data/{raw/,processed/}
├── output/logs/<type>-<task>/     ← task별 실행 로그
└── .worktrees/<type>-<task>/      ← 작업용 worktree (EXEC_PLAN/SPEC/SELF_CHECK/QA_REPORT, .ports)
```

---

## 9. 문서 수명주기

- **영속 문서**: `CLAUDE.md`, `AGENTS.md`, `ARCHITECTURE.md`, `docs/product-specs/`, `docs/references/`, `agents/`
- **task-local (worktree 루트, 진행 중에만)**: `EXEC_PLAN.md`, `SPEC.md`, `SELF_CHECK.md`, `QA_REPORT.md`
- **아카이브 (병합 시 이동)**: 위 4종 → `docs/exec-plans/completed/<type>-<task>/`

상세 절차는 `.claude/harness_workflow.md` 참고.

---

## 10. 에러 처리 & 재시도 정책

### 10-1. 예외 계층

| 계층 | 예외 타입 | 의미 |
|------|-----------|------|
| integration | `IntegrationError` | 외부 HTTP(OpenAPI/임베딩) 실패 |
| repository | `RepositoryError` | DB 접근 실패 (제약 위반, 데드락) |
| domain | `DomainError` (+ 하위) | 비즈니스 규칙 위반 (문서 중복, 해시 불일치 등) |
| transport | `APIError` / MCP 표준 에러 | 진입점에서 사용자-facing 변환 |

- `services` 는 domain/repository 예외만 발생시킨다. HTTP 상태코드를 알지 못한다.
- 변환 책임은 `api.routes` / `mcp.tools` 가 진다. 스택트레이스는 외부에 노출하지 않는다.

### 10-2. 재시도

| 호출 | 정책 |
|------|------|
| OpenAPI 원본 fetch | 타임아웃 10s, 지수백오프 재시도 3회, 최종 실패 시 `IntegrationError` |
| 임베딩 API | 타임아웃 30s, 재시도 2회, 배치 단위 부분 실패 허용 |
| DB 트랜잭션 | 직렬화 충돌 1회 재시도. 그 외는 그대로 전파 |
| MCP 도구 핸들러 | 서버 내부 재시도 없음. 실패는 Claude 에 그대로 반환 |

Circuit breaker, bulkhead 는 트래픽이 생긴 뒤 ADR 로 재검토.

---

## 11. 비동기 & 트랜잭션 모델

- FastAPI 전체 `async`. DB 는 async SQLAlchemy 세션.
- **세션 수명은 요청 단위**. `api.dependencies.get_session` 으로 주입.
- 외부 HTTP 호출은 **세션/트랜잭션 밖**. DB 커넥션을 오래 붙잡지 않는다.
- 문서 등록은 "수집 → 저장" 의 두 단계. 저장 구간만 한 트랜잭션.
- **재색인의 원자성**: 문서 단위로 `DELETE 기존 청크 + INSERT 신규 청크` 를 같은 트랜잭션에 넣어 중간 상태가 외부에 보이지 않게 한다.
- 백그라운드 작업(주기 동기화)은 별도 세션 팩토리. 요청 세션과 섞지 않는다.

---

## 12. 관측성(Observability) 기준선

현재 단계(MVP)에서는 **로깅만 필수**, 메트릭/트레이싱은 도입 시점을 ADR 로 결정한다.

### 12-1. 구조화 로깅

- 포맷: JSON. `core.logging` 에서 단일 로거 팩토리 제공.
- 필수 필드: `ts`, `level`, `logger`, `msg`, `trace_id`, `task_id`(있으면), `document_id`(있으면), `tool_name`(MCP), `duration_ms`(작업 완료 로그).
- `trace_id` 는 요청 진입점(api/mcp)에서 생성해 컨텍스트 변수로 전파.
- 예외 로그는 `exc_info=True` 필수. 사용자 입력 전체 덤프 금지 (PII 가능성).

### 12-2. 헬스체크

- `/health` — 프로세스 살아있는지 (DB 핑 없음)
- `/ready` — DB 연결 + 마이그레이션 버전 확인. 둘 다 OK 일 때만 200

### 12-3. 메트릭 / 트레이싱

- 초기 미도입. 검색 응답시간 p95 같은 NFR 모니터링 시점에 OpenTelemetry 도입 예정 → ADR.

---

## 13. 보안 모델

| 경계 | 인증/권한 |
|------|-----------|
| 관리 HTTP API | API Key 헤더 (`X-API-Key`). 1차 단계. OAuth/SSO 는 이후 ADR |
| MCP 서버 | Claude 가 연결하는 **읽기 전용** 인터페이스. 상태 변경 도구 없음 |
| 데이터베이스 | 서비스 전용 계정. 최소 권한. 마이그레이션 계정 분리 |

비밀 관리:

- 로컬: `.env` (커밋 금지, `.env.example` 만 커밋)
- 프로덕션: 시크릿 매니저 사용 (후속 ADR 에서 선택)
- 코드에 하드코딩 금지. `core.config` 만이 환경변수를 읽는다.

입력 방어선:

- Pydantic 스키마가 1차 검증 (타입, 범위, 길이)
- ORM 파라미터 바인딩으로 SQL 인젝션 방지
- OpenAPI 원문은 신뢰하지 않는다. 파서는 깊이/크기 상한을 둔다.

---

## 14. 비기능 요구(NFR) 목표치

초기 목표. 성능 테스트 결과로 조정하고 변경 시 ADR 기록.

| 항목 | 목표 | 측정 조건 |
|------|------|-----------|
| 검색 p95 응답시간 | < 400 ms | 10만 청크 저장, `top_k=5`, 단일 인스턴스 |
| 검색 Top-1 정확도 | > 70 % | 평가셋 구축 후 재측정 (ADR 필요) |
| 검색 Top-3 정확도 | > 90 % | 위와 동일 |
| 문서 재색인 소요 | < 30 s / 문서 | 엔드포인트 200개 기준 |
| 기동 시간 | < 10 s | `alembic upgrade head` 제외 |
| 가용성 | 단일 인스턴스 재시작 복구 | HA 는 범위 외 |

`docs/product-specs/plan.md §18` 의 지표와 여기서의 숫자가 어긋나면 **이 문서가 우선**한다. `plan.md` 는 기획 문서, 본 문서는 구현 기준.

---

## 15. 배포 & 환경 계층

### 15-1. 환경 단계

| 환경 | 용도 | 데이터 |
|------|------|--------|
| local | 개발자 로컬 + worktree | 시드 데이터 / 개인 문서 |
| ci | PR 검증 (lint, type, test) | 임시 pg 컨테이너 |
| staging | 통합 검증 | 익명화된 샘플 |
| prod | 운영 | 실 문서 |

### 15-2. 컨테이너/프로세스

- 단일 Dockerfile. **같은 이미지, 다른 명령어** 로 API / MCP / 워커를 분리 실행한다.
- 엔트리포인트:
  - API: `uvicorn src.main:app --host 0.0.0.0 --port $API_PORT`
  - MCP: `python -m src.mcp.server`
  - 워커: `python -m src.services.ingestor.sync_service --scheduler`
- 마이그레이션은 기동 전 별도 잡 (`alembic upgrade head`). 앱 프로세스에서는 실행하지 않는다.

### 15-3. CI 게이트

- `ruff`, `mypy`, `pytest` 모두 통과해야 머지 가능 (`CLAUDE.md` 규칙과 일치).
- 하네스 산출물(`EXEC_PLAN/SPEC/SELF_CHECK/QA_REPORT`)이 `docs/exec-plans/completed/<type>-<task>/` 에 존재하지 않으면 `develop` 병합 PR 을 차단 (후속 훅으로 구현).

---

## 16. ADR 인덱스

아키텍처 결정은 `docs/adr/` 에 개별 파일로 기록한다. 포맷은 간단한 MADR(변형):

```
# ADR-XXXX: <제목>
상태: proposed | accepted | superseded-by ADR-YYYY
컨텍스트: <왜 결정이 필요했나>
결정: <무엇을 정했나>
결과: <장단점 / 후속 영향>
```

초기 등록 후보 (구현과 병행해 생성):

| 번호 | 제목 | 상태 |
|------|------|------|
| 0001 | 저장형 검색 구조 채택 (실시간 프록시 배제) | 예정 |
| 0002 | 벡터 검색 백엔드로 pgvector 채택 | 예정 |
| 0003 | MCP 서버는 읽기 전용 경계로 유지 | 예정 |
| 0004 | 관측성은 구조화 로깅만 먼저, 메트릭/트레이싱은 유보 | 예정 |
| 0005 | 관리 API 인증은 API Key 로 시작 | 예정 |
| 0006 | 벡터 인덱스는 HNSW 로 시작, 임계치 초과 시 재평가 | 예정 |

이 ADR 중 하나라도 바뀌면 본 문서의 관련 섹션을 같은 PR 에서 갱신한다.
