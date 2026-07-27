# SPEC: 벡터 인덱스 영속화 (pgvector 전환)

## 배경 / 문제

`InMemoryVectorIndex`(`app/services/indexer/vector_index.py`)는 순수 Python dict
기반 벡터 저장소로, 검색 시점의 코사인 유사도 계산을 전부 프로세스 메모리에서
수행한다. DB(`ApiChunk.embedding_json`, `Text` 컬럼에 JSON 직렬화된 `list[float]`)는
영구 저장소로만 쓰이고, 앱 기동 시 `rebuild_vector_index()`가 전량 읽어 메모리로
복사해 재시작 손실을 임시 보완한다.

문제:
1. 멀티 워커로 띄우면 워커마다 인메모리 인덱스가 따로 놀아 검색 결과가
   워커에 따라 달라진다(쓰기가 다른 워커의 인덱스에 반영되지 않음).
2. 데이터가 커지면 기동마다 전량 재계산 비용이 커진다.

## 결정 사항

- **운영 DB는 postgres로 고정** (sqlite 호환성은 더 이상 고려하지 않는다).
- `ApiChunk.embedding_json`(Text/JSON 문자열)을 **pgvector 확장의 `vector` 컬럼**으로
  교체하고, `VectorSearch`가 DB에 코사인 거리 쿼리(`<=>` 연산자)를 직접 던지는
  방식으로 전환한다. `InMemoryVectorIndex`와 `rebuild_vector_index()`는 제거한다.
- 이 프로젝트에는 현재 **alembic이 설치되어 있지 않고** 스키마는
  `Base.metadata.create_all(engine)`으로만 생성된다. `CLAUDE.md`에는 이미
  "alembic 마이그레이션 후 이미지 재빌드" 절차가 문서화되어 있으므로, 이번
  작업에서 **alembic을 함께 도입**한다 — 초기 마이그레이션(현재 스키마 스냅샷)과
  pgvector 확장/컬럼 변경 마이그레이션을 함께 작성한다.

## 범위

### 포함
1. **의존성 추가**: `pgvector`(Python 패키지, SQLAlchemy `Vector` 타입 제공),
   `psycopg[binary]`(postgres 드라이버), `alembic`.
2. **DB 스키마 변경**:
   - `ApiChunk.embedding_json: Text` → `ApiChunk.embedding: Vector(dim)` 컬럼 교체.
     `dim`은 현재 `HashEmbeddingProvider`/Gemini 임베딩 차원(`app/core/config.py`의
     `embedding_dim` 설정값)을 그대로 따른다.
   - `CREATE EXTENSION IF NOT EXISTS vector;` 마이그레이션 포함.
   - pgvector ivfflat 또는 hnsw 인덱스 추가(청크 수가 적은 MVP 단계이므로
     `hnsw` 권장 — ivfflat은 사전 학습 데이터 필요, hnsw는 즉시 사용 가능).
3. **alembic 도입**:
   - `alembic init`으로 `alembic/` 골격 생성, `alembic.ini`/`env.py`가
     `app.core.config.get_settings().database_url`을 읽도록 연결.
   - 기존 8개 테이블을 반영하는 초기 리비전 1개 생성(현재 운영 DB가 아직 없다는
     전제 — 이미 배포된 DB가 있다면 `alembic stamp head`로 베이스라인 처리 필요,
     이는 배포 시점에 확인).
   - `embedding_json` → `embedding vector(dim)` 컬럼 교체 리비전 1개 추가.
4. **코드 변경**:
   - `app/models/openapi.py`: `ApiChunk.embedding_json`/`embedding` property 제거,
     `embedding: Mapped[list[float] | None] = mapped_column(Vector(dim))`로 교체.
   - `app/services/search/vector_search.py`: `InMemoryVectorIndex` 의존 제거,
     `ChunkRepository`(또는 신규 메서드)를 통해 `candidates` 집합 내에서
     `embedding <=> :query_vector` ORDER BY LIMIT top_k 쿼리 실행.
   - `app/repositories/chunk_repository.py`: 코사인 거리 검색 메서드 추가
     (예: `search_by_vector(query_vector, top_k, candidate_ids)`).
   - `app/services/indexer/vector_index.py`: 파일 삭제.
   - `app/services/indexer/indexer_service.py`: `deferred_upserts` 반환/처리 제거
     (embedding은 이제 `ApiChunk.embedding`에 직접 저장되며 커밋과 함께 영속화되므로
     별도 인메모리 upsert 단계가 불필요).
   - `app/services/ingestor/sync_service.py`: `vector_index` 의존성 및
     `upsert_many`/`delete_many` 호출 제거.
   - `app/api/dependencies.py`: `AppState.vector_index` 필드 제거,
     `rebuild_vector_index()` 함수 삭제, `build_services`에서 `VectorSearch` 생성 시
     `vector_index` 대신 `chunk_repo`(세션 기반) 전달.
   - `app/mcp_server.py`/`app/main.py` 등 `rebuild_vector_index` 호출부 제거.
5. **테스트**:
   - 기존 `InMemoryVectorIndex` 단위 테스트 제거/교체.
   - `VectorSearch`가 DB 기반으로 top_k와 candidates 필터를 올바르게 반영하는
     통합 테스트 추가(postgres 테스트 DB 또는 testcontainers 필요 — CI 환경에
     postgres가 없으면 이 테스트는 `pytest.mark.postgres`로 스킵 가능하게 표시).
   - 멀티 워커 시나리오는 별도 자동 테스트 없이, "같은 DB를 보는 두 세션이
     즉시 서로의 쓰기를 본다"는 수준의 통합 테스트로 대체.

### 제외 (이번 작업 범위 아님)
- qdrant 등 별도 벡터 스토어 어댑터 (postgres 고정이므로 불필요).
- ivfflat/hnsw 파라미터 튜닝, 대규모 성능 벤치마크.
- 기존 운영 DB에 대한 실제 마이그레이션 적용/롤아웃 절차 (SPEC 밖, 배포 시 별도 확인).

## 영향 범위 (수정 파일 목록)

- `pyproject.toml` (의존성 추가)
- `alembic.ini`, `alembic/env.py`, `alembic/versions/*.py` (신규)
- `app/models/openapi.py`
- `app/repositories/chunk_repository.py`
- `app/services/search/vector_search.py`
- `app/services/indexer/vector_index.py` (삭제)
- `app/services/indexer/indexer_service.py`
- `app/services/ingestor/sync_service.py`
- `app/api/dependencies.py`
- `app/mcp_server.py`, `app/main.py` (rebuild_vector_index 호출부)
- `tests/` 하위 관련 단위/통합 테스트

## 리스크 / 확인 필요 사항

- **테스트 환경**: 현재 테스트는 sqlite in-memory로 추정된다(`app/core/db.py`가
  sqlite 분기를 갖고 있음). postgres 고정 전환 시 CI/로컬 테스트 실행 환경에
  postgres(+ pgvector 확장)가 반드시 있어야 한다. `docker compose`에 이미 postgres
  서비스가 있으므로(CLAUDE.md 참고) 테스트도 그 인스턴스를 쓰는 방향으로 간다.
- **임베딩 차원 고정**: pgvector `Vector(dim)`은 컬럼 생성 시 차원이 고정된다.
  현재 `HashEmbeddingProvider`/Gemini 임베딩 차원이 설정에 따라 달라질 수 있다면,
  차원 변경 시 마이그레이션이 다시 필요하다는 점을 문서화한다.
- **기존 데이터 마이그레이션**: `embedding_json` → `embedding vector` 컬럼 교체 시
  기존 행의 JSON 문자열을 파싱해 새 컬럼으로 옮기는 데이터 마이그레이션이
  필요하다(단순 컬럼 타입 변경이 아님). alembic 리비전의 `upgrade()`에서
  `UPDATE api_chunk SET embedding = ...` 형태로 처리한다.

## Task Scale

Large — DB 스키마 변경(alembic 신규 도입 포함), 다중 모듈 변경.
Planner → Generator → Evaluator 순서로 진행한다.

## 구현 결과 노트 (완료 후 추가)

- **스키마 분리**: 애플리케이션 테이블은 전용 `app` 스키마에 두고(`Base.metadata = MetaData(schema="app")`),
  `public`은 pgvector 확장 전용으로 비워둔다. 이유: `public`에 애플리케이션과 동일 이름
  테이블이 있으면 `Base.metadata.create_all()`의 존재 확인(checkfirst)이 `search_path`
  상의 다른 스키마 테이블을 "이미 존재"로 오판해 DDL을 건너뛰는 문제가 있었다
  (테스트 환경에서 실제로 재현·확인함). alembic도 `version_table_schema="app"`으로 맞췄다.
- **테스트 격리**: 스키마 단위 격리는 위와 같은 이유로 근본 해결이 안 되어, 테스트마다
  완전히 별도의 **database**(`CREATE DATABASE`)를 만드는 방식으로 전환했다
  (`tests/conftest.py`의 `pg_engine` fixture).
- **docker-compose.yml** 신규 추가: `pgvector/pgvector:pg16` 이미지 기반 postgres 단일 서비스.
- `app/core/db.py`의 sqlite 분기 제거, `app/core/config.py`의 기본 `database_url`을
  postgres로 변경.
