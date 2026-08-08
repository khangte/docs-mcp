# 자체호스팅 Postgres → Supabase 전환 검토

- 상태: 검토(구현 전, lead 판단 대기)
- 일시: 2026-08-08
- 작성: architect
- 관련: `docs/search-performance-improvements.md`, `docker-compose.yml`, `app/core/db.py`, `.env.example`

## 현황 요약
- DB: `docker-compose.yml` 의 로컬 `pgvector/pgvector:pg16`, `localhost:5432/docs_mcp`.
- 드라이버: `postgresql+psycopg`(psycopg3), 동기 SQLAlchemy 2.0.
- 커넥션 풀: `create_db_engine` 이 `create_engine` 기본값 사용 — **명시적 풀 설정 없음**(기본 QueuePool, pool_size=5).
- 배포 형태: **로컬 MCP 서버**. DB 가 같은 호스트의 docker 라 쿼리 왕복이 사실상 sub-ms.
- 검색 특성(직전 분석): 키워드 검색이 **애플리케이션 레벨 풀스캔** — `list_endpoint_chunks()` 로 endpoint 청크 전 행을 앱으로 가져와 Python 이 점수 계산. 즉 **매 쿼리마다 행 집합이 DB→앱으로 전송**된다.

---

## 1. 기능 지원 여부 (pgvector/HNSW, FTS, pg_trgm)
결론: **전부 지원**. Supabase 는 표준 PostgreSQL 관리형 서비스라 검색 스택이 그대로 올라간다.

| 기능 | 지원 | 비고 |
|---|---|---|
| pgvector + HNSW (`vector_cosine_ops`) | ✅ | Supabase 가 공식적으로 밀고 있는 기능. `create extension vector` 로 활성화. 현재 `ix_api_chunk_embedding_hnsw` 그대로 이식 가능. |
| FTS (tsvector + GIN) | ✅ | Postgres 코어 기능. 향후 P1(키워드 FTS 이관)도 문제없음. |
| pg_trgm (GIN) | ✅ | 확장. `create extension pg_trgm`. 향후 P4 그대로 가능. |

- 주의: 확장 활성화는 Supabase 대시보드/SQL(`create extension ...`)로 한 번 켜야 하고, alembic 마이그레이션이 확장 생성까지 포함하면(현재 `CREATE EXTENSION IF NOT EXISTS vector` 있음) 대체로 자동 처리된다. 단 계정 권한에 따라 일부 확장은 대시보드에서만 켜지는 경우가 있어 사전 확인 필요.
- HNSW 인덱스 빌드는 관리형에서도 컴퓨트/메모리를 크게 쓴다 — 소형 인스턴스에서 대량 재색인 시 `maintenance_work_mem` 제약을 받을 수 있음.

## 2. 검색 성능 관점 이득/손해
**핵심 손해: 로컬 docker(sub-ms) → 원격 관리형(인터넷 왕복)으로 바뀌면 매 쿼리가 네트워크 왕복이 된다.**

- **왕복 지연**: localhost 대비 리전 근접 시에도 수 ms~수십 ms, 리전이 멀면 100ms+ 가 쿼리마다 가산. MCP 도구 한 번에 여러 쿼리가 나가면 누적된다.
- **현 구조에서 특히 치명적**: 키워드 검색이 앱 레벨 풀스캔이라 **결과 몇 건이 아니라 후보 행 집합 전체를 원격에서 전송**한다. 로컬에선 공짜였던 전송이 원격에선 대역폭·지연 비용으로 직결 → 직전 분석의 P1/P2 문제가 원격에서 몇 배로 증폭.
- **커넥션 풀링**: 현재 풀 설정이 없어 원격 전환 시 커넥션 수립(SSL 핸드셰이크 포함) 비용이 두드러질 수 있음. Supabase 는 **Supavisor 풀러**(transaction 모드 6543 / session 모드 5432)를 제공하므로 이를 써야 함.
  - **주의(락인성 gotcha)**: Supavisor **transaction 모드는 prepared statement/세션 상태를 보장하지 않는다.** psycopg3 는 기본적으로 서버 사이드 prepared statement 를 쓰므로 transaction 풀러 뒤에서 오류가 날 수 있어 `prepare_threshold=None` 등 조정 또는 session 모드/직결 사용이 필요. → **코드/설정 변경 유발 지점**(이번엔 문서만, 향후 반영 필요).
- **이득**: 사실상 검색 지연 측면 이득은 없음. 이득은 성능이 아니라 운영(3·4항)에서 나온다. 유일한 성능적 이점은 앱과 DB 를 **같은 클라우드 리전에 콜로케이션**해 배포할 때인데, 현재는 로컬 MCP 서버라 오히려 앱↔DB 가 원격으로 벌어진다.

## 3. 마이그레이션 난이도/작업량
전체적으로 **중하(中下)** — DB 자체는 표준 Postgres라 이식은 쉽지만, 로컬 개발 워크플로와 풀러 gotcha 가 손을 탄다.

- **커넥션 문자열**: `DOCS_MCP_DATABASE_URL` 만 교체하면 앱은 동작(드라이버 동일 psycopg3). **SSL 필수**(`sslmode=require`) 반영 필요.
- **alembic**: 마이그레이션은 그대로 Supabase 대상으로 `upgrade head` 실행 가능. 확장 활성화 권한만 선확인. 데이터 이관은 `pg_dump`/`pg_restore`(임베딩 벡터 포함) 1회.
- **로컬 개발 워크플로 영향(중요)**: 현재 `docker compose up -d postgres` + `uv run alembic upgrade head` 로 오프라인·격리 개발이 가능. Supabase 전면 전환 시 로컬 개발도 원격 의존이 되어 **오프라인 개발/테스트 격리성 저하, 공유 DB 오염 위험**. 권장 형태는 **로컬 docker 유지 + Supabase 는 staging/prod 용 별도 환경**(env 분기)으로 두는 것 — 이러면 "전환"이 아니라 "추가 배포 타깃".
- **커넥션 풀 설정 도입**: 원격 전환과 함께 `pool_size`/`pool_pre_ping`/풀러 모드 결정이 필요(2항 gotcha).

## 4. 비용·운영 부담·벤더 락인
- **운영 이득**: 자동 백업/PITR, 업그레이드, 모니터링, 수직 스케일링을 Supabase 가 담당 → 자체 docker 볼륨 백업/관리 부담 감소.
- **비용 발생**:
  - Free 티어: **1주 비활성 시 프로젝트 일시정지** — 간헐적으로 쓰는 MCP 서버 성격과 상성이 나쁨(콜드스타트/정지). 실사용엔 부적합.
  - Pro: 월 고정비(대략 $25~ 수준) + 컴퓨트/스토리지/에그레스 추가과금. 현재 무료(로컬 docker) 대비 순증.
- **벤더 락인**: **DB 만 쓰면 락인은 낮음** — 표준 Postgres라 `pg_dump` 로 언제든 타 Postgres/자체호스팅으로 회수 가능. 단 Supabase Auth/Realtime/Storage/Supavisor 특유 동작에 코드가 엮이면 락인 상승. 이번 검토 범위(순수 DB)에선 락인 리스크 **낮음**, 단 2항의 transaction 풀러 대응이 Supabase 특유 설정으로 스며들 수 있음.

## 5. 결론 및 권장 시점
**지금 이 프로젝트에 전환은 타당하지 않다(현 시점 비권장).**

근거:
1. 목표가 "검색 성능 향상"인데, 로컬 docker → 원격 관리형 전환은 **매 쿼리에 네트워크 왕복을 추가**해 목표와 정면으로 상충한다.
2. 특히 키워드 검색이 **앱 레벨 풀스캔**이라 원격에서 행 전송 비용이 증폭 — 최적화 전 전환은 손해가 가장 큰 타이밍.
3. 현재 배포가 **로컬 MCP 서버**라 관리형 DB 의 최대 이점(앱-DB 콜로케이션, 다중 사용자 공유)을 살릴 상황이 아니다. 운영 이득(백업/스케일)만으로는 성능·비용 손해를 상쇄하지 못한다.

**전환이 정당화되는 조건(그때 재검토):**
- 다중 사용자/공유 배포로 가서 DB 를 여러 클라이언트가 함께 봐야 할 때, 또는 관리형 백업/가용성이 실제 요구가 될 때.
- 그 경우에도 **MCP 서버를 Supabase 와 같은 클라우드 리전에 콜로케이션**해 왕복 지연을 죽이는 형태여야 함.

**권장 시점: 검색 성능 작업 "이후".**
- 먼저 `search-performance-improvements.md` 의 **P1(키워드 FTS 이관)·P2(프로젝션)** 를 끝내 쿼리를 인덱스 기반으로 바꾸고 **결과가 작은 집합만 오가도록** 만든 뒤라야, 원격 DB 를 써도 지연을 감당할 수 있다.
- 즉 순서는 **검색 최적화 → (필요가 생기면) Supabase**. 지금은 로컬 docker 를 유지하고, Supabase 는 "다중 사용자 배포가 필요해지는 시점"의 후보 옵션으로 남긴다.
