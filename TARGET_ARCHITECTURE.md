# docs-mcp 목표 아키텍처 설계

> **상태: 완료 (historical)** — 아래 Phase 1~5는 모두 적용되었다. 이후 pgvector
> 전환(ADR-0002 실현)으로 `InMemoryVectorIndex` 자체가 제거되었으므로,
> 이 문서의 "SQLite + sync SQLAlchemy 유지"·"vector_index RLock" 관련 내용은
> 더 이상 유효하지 않다. 현재 아키텍처는 `ARCHITECTURE.md`를 참고할 것.

두 에이전트(아키텍처 설계 + 리스크 검토)의 결과를 교차 검증하여 통합한 문서입니다.

---

## 1. 최종 권장 아키텍처

### 변경 없는 부분

- `src/api/routes/` 전체 — 라우트 핸들러 로직 그대로
- `src/repositories/` 4개 저장소 메서드 시그니처
- `AppState`, `ServiceBundle` dataclass 구조
- `build_services` 제너레이터 패턴 (FastAPI Depends 용)
- `TraceIdMiddleware`, 스키마 레이어 (`src/schemas/`)
- MCP stdio transport — `FastMCP.run()` 호출 방식 그대로
- SQLite + sync SQLAlchemy (현재 단계 유지)

### 변경되는 부분과 이유

| 파일 | 변경 내용 | 해결하는 리스크 |
|---|---|---|
| `src/services/indexer/vector_index.py` | `threading.RLock` 추가, `upsert_many()`, `replace_all()` 신규, `search()`에서 snapshot 복사 | RISK-ASYNC-1, RISK-TX-4 |
| `src/services/indexer/indexer_service.py` | `index_document()` 반환 타입 변경 — vector upsert를 후처리로 이전, 생성자에서 `vector_index` 제거 | RISK-TX-1 |
| `src/services/ingestor/sync_service.py` | `commit()` 이후 `upsert_many()` 호출, `sa_delete` 직접 실행 → `schema_repo` 위임 | RISK-TX-1, RISK-TX-2, RISK-HC-1 |
| `src/mcp_server.py` | 도구 함수 `async def` 전환, `managed_session` 패턴, `raise EndpointNotFoundError` | RISK-MCP-1/2, RISK-ASYNC-2 |
| `app/main.py` | 198줄 `app = get_default_app()` 제거 | RISK-LC-2 |
| `src/core/db.py` | `managed_session()` 컨텍스트 매니저 추가 | RISK-MCP-1/2 |
| `src/core/logging.py` | `_configured` 전역 플래그 → `root.handlers` 체크로 교체 | 테스트 격리 |
| `src/api/dependencies.py` | `rebuild_vector_index`에서 `replace_all()` 사용 | RISK-LC-3 |

### 신규 파일

| 파일 | 역할 | 해결하는 리스크 |
|---|---|---|
| `src/bootstrap.py` | `bootstrap_app_state(cfg)` 팩토리 — main/mcp 공유 | RISK-HC-2, 중복 제거 |
| `app/app.py` | 한 줄: `app = create_app()` — uvicorn 전용 진입점 | RISK-LC-2 |
| `src/repositories/schema_repository.py` | `ApiSchema` CRUD 분리 | RISK-HC-1, 레이어 경계 |

---

## 2. 의존성 흐름

```
                     core/config, core/db, core/errors
                              │
                              ▼
                        bootstrap.py          ← main.py, mcp_server.py 공유
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
         main.py                        mcp_server.py
     (FastAPI 전용)                    (MCP 전용)
              │                               │
              ▼                               │
    api/dependency_providers.py               │
              │                               │
              ▼                               │
    api/dependencies.py ◄─────────────────────┘
    (AppState, ServiceBundle,           (AppState만 공유)
     build_services)
              │
              ▼
    repositories/* ──► models/openapi.py
              │
    services/*  ──────► repositories/*, core/errors

허용 방향:
  routes → services (ServiceBundle 경유만)
  services → repositories
  repositories → models
  mcp_server.py → bootstrap.py, core/db, services/*
  mcp_server.py → api/dependencies.py (AppState 타입만)
  mcp_server.py ✗ api/routes/* 임포트 금지
  repositories ✗ services 임포트 금지
```

---

## 3. 비동기 마이그레이션 전략

**전제조건**: Step 0을 완료하기 전까지 어떤 비동기 마이그레이션도 시작하지 않습니다.

### Step 0 (즉시 필수) — InMemoryVectorIndex thread safety

```python
# src/services/indexer/vector_index.py
import threading

class InMemoryVectorIndex:
    def __init__(self) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._lock = threading.RLock()

    def search(self, query_vector, top_k, candidates=None):
        with self._lock:
            snapshot = list(self._vectors.items())  # 락 안에서 복사
        # 비싼 코사인 연산은 락 밖에서 수행
        hits = [...]
        ...

    def replace_all(self, pairs: list[tuple[str, list[float]]]) -> None:
        """startup rebuild용 원자적 교체."""
        new_vectors = {cid: list(vec) for cid, vec in pairs}
        with self._lock:
            self._vectors = new_vectors  # dict 대입은 CPython에서 원자적
```

**검증 게이트**: `pytest tests/unit/` 전부 통과

### Step 1 — MCP 도구 async 전환 (FastMCP 이벤트 루프 보호)

```python
# src/mcp_server.py
import anyio

@mcp.tool()
async def search_endpoints(query: str, top_k: int = 5, ...) -> list[dict]:
    return await anyio.to_thread.run_sync(
        lambda: _search_endpoints_sync(session_factory, app_state, query, top_k, ...)
    )
```

sync 서비스 코드는 전혀 건드리지 않습니다. `anyio.to_thread.run_sync`가 동기→비동기 브리지 역할을 합니다.

**검증 게이트**: `pytest tests/integration/test_mcp_server.py` 통과

### Step 2 (미래, 현재 금지) — AsyncSession 추가

`core/db.py`에 `create_async_db_engine()` + `create_async_session_factory()` 추가. 기존 sync 경로는 그대로 유지. `AppState`에 `async_engine: AsyncEngine | None = None` 옵셔널 필드만 추가.

### Step 3 (미래) — 라우트 한 개씩 async 전환

`GET /health` → `GET /documents` 순서로 `async def` 전환. 기존 sync 제너레이터는 마지막 라우트까지 그대로 유지.

### Step 4 (미래) — MCP 도구 natively async

Step 3 완료 후, `anyio.to_thread.run_sync` 래퍼를 제거하고 직접 `await` 사용.

**핵심 불변 원칙**: `mcp_server.run()` 호출 방식은 절대 바꾸지 않습니다. 비동기 마이그레이션은 서비스/저장소 레이어 내부에서만 진행합니다.

---

## 4. MCP Transport 안전 전략

### 문제 1: 세션 누수 (RISK-MCP-1/2)

`service_context()`의 `finally` 블록이 `StopIteration`을 삼키는 구조입니다. `core/db.py`에 `managed_session`을 추가하고 모든 도구에서 이를 사용합니다.

```python
# src/core/db.py 추가
from contextlib import contextmanager

@contextmanager
def managed_session(session_factory: sessionmaker) -> Iterator[Session]:
    """예외 발생 시에도 session.close()를 보장하는 컨텍스트 매니저."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
```

```python
# src/mcp_server.py — 모든 도구에 적용
@mcp.tool()
async def list_documents() -> list[dict[str, Any]]:
    def _sync():
        with managed_session(session_factory) as session:
            repo = DocumentRepository(session)
            docs = repo.list_all()
            return [{...} for d in docs]
    return await anyio.to_thread.run_sync(_sync)
```

`with` 문의 `__exit__`는 예외/정상 종료 모두에서 무조건 호출됩니다. 제너레이터 프로토콜 의존 없이 세션 닫힘을 보장합니다.

### 문제 2: 에러 반환 방식 (get_endpoint_details)

```python
# 현재 — 잘못된 방식
if not endpoint:
    return {"error": "Endpoint not found"}  # MCP 클라이언트가 성공으로 인식

# 수정
if not endpoint:
    raise EndpointNotFoundError(endpoint_id)  # FastMCP → isError: true로 변환
```

`get_raw_document` 리소스의 `return "Document not found"`도 동일하게 수정합니다.

### 문제 3: 모듈 임포트 시 사이드 이펙트 (RISK-LC-2)

```python
# src/main.py 198줄 삭제
app = get_default_app()  # ← 이 줄 제거

# src/app.py (신규, uvicorn 전용)
from app.main import create_app
app = create_app()
```

uvicorn 실행: `uvicorn app.app:app` 또는 `uvicorn app.main:create_app --factory`

### 문제 4: 인덱스 준비 완료 보장 (RISK-MCP-3)

`mcp_server.main()`에서 `rebuild_vector_index()`와 `mcp_server.run()` 사이에 명시적 순서를 유지합니다. `replace_all()` 사용으로 clear→loop 사이의 빈 구간을 제거합니다.

```python
def main():
    cfg = get_settings()
    app_state = bootstrap_app_state(cfg)     # 엔진 생성 + create_all
    rebuild_vector_index(app_state)           # 인덱스 완전 복원 (원자적)
    mcp_server = create_mcp_server(app_state) # 도구 등록
    mcp_server.run()                          # 이 시점부터 연결 수락
```

---

## 5. 권장 디렉토리 구조

```
src/
├── bootstrap.py                 ★ 신규 — bootstrap_app_state(cfg) -> AppState
├── main.py                      ~ 수정 — bootstrap 사용
├── mcp_server.py                ~ 수정 — async 도구, managed_session, raise 에러
│
├── core/
│   ├── config.py                = 유지
│   ├── db.py                    ~ 수정 — managed_session() 추가
│   ├── errors.py                = 유지 (SchemaNotFoundError 필요 시 추가)
│   └── logging.py               ~ 수정 — _configured 제거, root.handlers 체크
│
├── models/
│   └── openapi.py               = 유지 (JSON 헬퍼 추출은 Phase 5)
│
├── repositories/
│   ├── chunk_repository.py      = 유지
│   ├── document_repository.py   = 유지
│   ├── endpoint_repository.py   ~ 수정 — add_schema/get_schema_by_name deprecated
│   ├── sync_history_repository.py = 유지
│   └── schema_repository.py     ★ 신규 — delete_by_document, get_by_name, add
│
├── services/
│   ├── examples/                = 유지
│   ├── indexer/
│   │   ├── chunk_builder.py     = 유지
│   │   ├── embedding_provider.py= 유지
│   │   ├── indexer_service.py   ~ 수정 — vector_index 제거, deferred 반환
│   │   └── vector_index.py      ~ 수정 — RLock, upsert_many, replace_all
│   ├── ingestor/
│   │   ├── openapi_fetcher.py   = 유지
│   │   └── sync_service.py      ~ 수정 — commit 후 upsert, schema_repo 사용
│   ├── parser/                  = 유지
│   ├── rag/                     = 유지
│   └── search/
│       ├── keyword_search.py    ~ 수정 — chunks 파라미터 수용, list_all 제거
│       ├── search_service.py    ~ 수정 — SQL 필터 사용
│       └── vector_search.py     = 유지
│
├── api/
│   ├── dependencies.py          ~ 수정 — rebuild_vector_index replace_all 사용,
│   │                                      schema_repo ServiceBundle 추가
│   ├── dependency_providers.py  = 유지
│   └── routes/                  = 전부 유지
│
└── schemas/                     = 전부 유지

변경 요약: 신규 3개, 수정 12개, 유지 ~25개
```

---

## 6. 단계별 구현 순서

### Phase 1 — 안전/독립적 변경 (리스크: 낮음) ✅ 완료

| 순서 | 파일 | 변경 내용 |
|---|---|---|
| 1 | `src/services/indexer/vector_index.py` | RLock, upsert_many, replace_all, search snapshot |
| 2 | `src/api/dependencies.py` | rebuild_vector_index → replace_all 사용 |
| 3 | `src/core/logging.py` | _configured 전역 제거 |
| 4 | `src/core/db.py` | managed_session 추가 |

**검증**: `pytest tests/unit/` 65 passed ✅ · `pytest tests/` 96 passed ✅

### Phase 2 — 서비스 레이어 수정 (리스크: 중간) ✅ 완료

| 순서 | 파일 | 변경 내용 |
|---|---|---|
| 1 | `src/services/indexer/indexer_service.py` | vector_index 제거, deferred 반환, 지연 임포트 → 상단 이동 |
| 2 | `src/repositories/schema_repository.py` | 신규 생성 |
| 3 | `src/services/ingestor/sync_service.py` | commit 후 upsert_many, schema_repo 사용 |
| 4 | `src/api/dependencies.py` | IndexerService 생성자 업데이트, schema_repo 추가 |
| 5 | `app/main.py` | 198줄 제거 |
| 6 | `app/app.py` | 신규 생성 |

**검증**: `pytest tests/` 전부 통과, `python -c "import app.main"` 로그 출력 없음

> **⚠️ 주의**: `IndexerService` 생성자 변경과 `sync_service.py` 변경은 반드시 같은 커밋에서 수행합니다.

### Phase 3 — MCP transport 재배선 (리스크: 높음) ✅ 완료

| 순서 | 파일 | 변경 내용 |
|---|---|---|
| 1 | `src/bootstrap.py` | 신규 생성 ✅ |
| 2 | `src/mcp_server.py` | async 도구, managed_session, raise 에러, bootstrap 사용 ✅ |
| 3 | `app/main.py` | bootstrap 사용 (선택적 정리) ✅ |

**검증**: `pytest tests/integration/test_mcp_server.py -v` 4 passed ✅ · `pytest tests/` 96 passed ✅

> **⚠️ 순서 제약**: Phase 1 완료 → Phase 2 완료 → 그 후에만 Phase 3 시작.
> Phase 3의 `mcp_server.py`가 `IndexerService`를 직접 생성하는데, Phase 2의 생성자 변경이 먼저 완료되지 않으면 이전 3-인자 방식으로 실수로 호출할 수 있습니다.

### Phase 4 — 검색 성능 (리스크: 중간) ✅ 완료

| 순서 | 파일 | 변경 내용 |
|---|---|---|
| 1 | `src/repositories/chunk_repository.py` | `list_by_endpoint_filter(method, tag, document_id)` 추가 ✅ |
| 2 | `src/services/search/keyword_search.py` | `chunks` 파라미터 수용, list_all 제거 ✅ |
| 3 | `src/services/search/search_service.py` | SQL 필터 사용, chunks를 keyword_search에 전달 ✅ |

**검증**: `pytest tests/` 96 passed ✅ · N+1 쿼리 없음 확인 ✅

### Phase 5 — 기계적 정리 (리스크: 낮음) ✅ 완료

| 순서 | 파일 | 변경 내용 |
|---|---|---|
| 1 | `src/models/openapi.py` | `_decode_json_dict`, `_decode_json_any` 헬퍼 추출, 5개 프로퍼티에 적용 ✅ |
| 2 | `app/main.py` | `_DOMAIN_ERROR_STATUS` 테이블 + `_make_handler` 팩토리로 핸들러 8개 → 1개 루프로 단순화 ✅ |
| 3 | `src/mcp_server.py` | `logging.basicConfig` → `get_logger()` 교체 ✅ (Phase 3에서 선적용) |

**검증**: `pytest tests/` 96 passed ✅

---

## 7. 고위험 리팩터링 경고

### 경고 1: InMemoryVectorIndex 스레드 안전 (RISK-ASYNC-1, RISK-TX-4) — 최우선

**위험**: Phase 2 배포 후 서비스 변경으로 동시 도구 호출 겹침 가능성이 증가합니다. Phase 1(RLock) 없이 Phase 2를 배포하면 `RuntimeError: dictionary changed size during iteration` 발생 창이 오히려 넓어집니다.

**완화**: Phase 1은 절대 선행 조건. `grep -n "_lock" src/services/indexer/vector_index.py`로 RLock 존재 확인 후에만 Phase 2 시작.

### 경고 2: IndexerService 생성자 변경과 vector upsert 이전의 원자성 (RISK-TX-1)

**위험**: `indexer_service.py`에서 `self._vector_index.upsert()` 호출을 제거했지만 실수로 남겨두면, 이전 inline upsert + post-commit upsert_many가 중복 실행됩니다. DB가 롤백되면 inline upsert만 남아 유령 벡터가 생성됩니다.

**완화**: 변경 후 `grep -n "_vector_index" src/services/indexer/indexer_service.py`가 0건 반환해야 합니다. 이 확인 없이 커밋하지 않습니다.

### 경고 3: resync 삭제 순서 변경 금지 (RISK-TX-2)

**위험**: `sync_service.py:resync`에서 삭제 순서는 `chunks → endpoints → schemas → flush → commit → upsert_many(new) → delete_many(old)`입니다. 순서가 바뀌면 SQLite FK 제약 위반 또는 스테일 벡터 잔류가 발생합니다.

**완화**: Phase 2에서 이 순서를 코드 주석으로 명시하고, 순서 변경은 반드시 트랜잭션 동작 검증 테스트와 함께 수행합니다.

### 경고 4: mcp_server.py 변경은 전체 도구 일괄 적용 (RISK-MCP-1/2)

**위험**: `service_context` → `managed_session` 전환을 일부 도구에만 적용하고 나머지를 혼용하면 세션 누수 디버깅이 불가능해집니다.

**완화**: Phase 3의 `mcp_server.py` 변경은 6개 도구/리소스 전체를 한 커밋에서 처리합니다. 절반만 바꾼 상태로 커밋하지 않습니다.

### 경고 5: mcp_server.py의 logging.basicConfig (RISK-MCP-4) ✅ 해소

`logging.basicConfig` 제거, `get_logger("docs_mcp.mcp")` 교체가 Phase 3에서 완료되었습니다.

### 경고 6: module-level app 제거 후 uvicorn 실행 방법 확인

**위험**: `main.py` 198줄 제거 후 기존 `uvicorn app.main:app` 명령이 `AttributeError: module 'app.main' has no attribute 'app'`로 실패합니다.

**완화**: `app/app.py` 생성과 `main.py` 198줄 제거를 같은 커밋에서 수행합니다. `Makefile`, `Dockerfile`, `.ports` 파일의 uvicorn 명령을 `uvicorn app.app:app`으로 일괄 업데이트합니다.

---

## 리스크 참조 테이블

| ID | 파일 | 줄 | 분류 | 심각도 |
|---|---|---|---|---|
| RISK-MCP-1 | `mcp_server.py` | 30–41 | MCP 호환성 | 높음 | ✅ Phase 3 해소 |
| RISK-MCP-2 | `mcp_server.py` | 34, 39–40 | MCP 호환성 | 높음 | ✅ Phase 3 해소 |
| RISK-MCP-3 | `mcp_server.py` | 178–181 | 라이프사이클 | 중간 | ✅ Phase 3 해소 |
| RISK-MCP-4 | `test_mcp_server.py` | 20, 33, 41 | MCP 호환성 | 중간 | ✅ Phase 3 해소 |
| RISK-LC-1 | `mcp_server.py` | 181 | 라이프사이클 | 중간 | ✅ Phase 3 해소 |
| RISK-LC-2 | `main.py` | 198 | 라이프사이클 | 높음 | ✅ Phase 2 해소 |
| RISK-LC-3 | `dependencies.py` | 138–142 | 라이프사이클 | 중간 | ✅ Phase 1 해소 |
| RISK-TX-1 | `sync_service.py` + `indexer_service.py` | 96–111 / 98 | 트랜잭션 | 높음 | ✅ Phase 2 해소 |
| RISK-TX-2 | `sync_service.py` | 198–200 | 트랜잭션 | 중간 | ✅ Phase 2 해소 |
| RISK-TX-4 | `dependencies.py` + `vector_index.py` | 86 | 트랜잭션 | 높음 | ✅ Phase 1 해소 |
| RISK-HC-1 | `sync_service.py` + `dependencies.py` | 44–63 / 97–106 | 숨은 결합 | 중간 | ✅ Phase 2 해소 |
| RISK-HC-2 | `mcp_server.py` | 14 | 숨은 결합 | 중간 | ✅ Phase 3 해소 |
| RISK-ASYNC-1 | `vector_index.py` | 28–72 | 비동기 위험 | 높음 | ✅ Phase 1 해소 |
| RISK-ASYNC-2 | `mcp_server.py` | 44–163 | 비동기 위험 | 높음 | ✅ Phase 3 해소 |
| RISK-IC-1 | `indexer_service.py` | 123, 130, 138 | 임포트 순환 | 낮음 | ✅ Phase 2 해소 |
| RISK-IC-2 | `mcp_server.py` | 68 | 임포트 순환 | 중간 | ✅ Phase 3 해소 |
