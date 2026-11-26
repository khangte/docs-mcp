# docs-mcp: RAG MCP Server

## 프로젝트 개요

- 기간: 2026.07 ~ 2026.08
- OpenAPI·Markdown·PDF 문서와 Google Drive/Notion 협업 문서를 색인하는 MCP 서버
- 하이브리드 검색(키워드+벡터)으로 후보를 골라 호출 LLM에 전달, 최종 답변은 Claude/ChatGPT가 생성
- 서버는 후보 피더 → 품질 지표는 정확도가 아닌 **recall@k**

| 지표 | 값 |
| --- | --- |
| 검색 품질 | Recall@10 **45~60%**, Recall@3 35~40%, MRR 0.36~0.37 (실 코퍼스 1,809 엔드포인트, n=20) |
| 질의 지연 | p50 **21.7ms** / p95 56.7ms / p99 63.5ms (top_k=10, 100 표본) |
| 검색 비용 | **$0** (로컬 CPU 임베딩 + 자체 호스팅 Postgres) |

## 아키텍처

![아키텍처](docs/architecture.png)

- 원본: [`docs/architecture.drawio`](docs/architecture.drawio)
- 키워드 arm과 벡터 arm을 **RRF로 항상 융합** (`app/services/search/rrf.py`)
- 두 신호의 스케일이 달라 점수 대신 ranker 내 **등수만** 사용
- 협업 문서 검색은 제목 arm을 더한 3-arm
- 상세 흐름: [`docs/search_flow.md`](docs/search_flow.md)

## 기술 스택

<!-- AUTO-GENERATED: pyproject.toml, docker-compose.yml, app/core/config.py 기준 -->

- **Backend**: Python 3.11+
- **Database**: PostgreSQL(+`pgvector` 확장) — SQLAlchemy 2.0, Alembic 마이그레이션
- **Search**:
  - pgvector 코사인 거리(`<=>`, HNSW 인덱스) 기반 벡터 검색
  - 임베딩: 로컬 CPU 모델(`sentence-transformers`, `LocalEmbeddingProvider`, 기본 `intfloat/multilingual-e5-small`) 또는 결정적 해시 기반 폴백(`HashEmbeddingProvider`)
- **문서 파서**: Markdown, CSV, PDF/DOCX, OpenAPI/Swagger (`app/services/parser/document_router.py`가 자동 판별). Drive 경유 XLSX/PPTX 도 텍스트 추출
- **MCP**: `fastmcp` 서드파티 패키지
- **Schema/DTO**: Pydantic v2
<!-- /AUTO-GENERATED -->

- **테스트**: 유닛 1,047개 + MCP 계층 통합 테스트, 결정적 RRF 골든 회귀 테스트

## 주요 기능

- **하이브리드 검색**: 키워드(Postgres FTS) + 벡터 유사도 RRF 융합
- **형식별 코드는 전처리에만**: PDF·DOCX·XLSX·PPTX는 텍스트 추출 후 마크다운 섹션화 경로로 합류
  - 파서 계층 전체 749줄, 구조 파싱은 OpenAPI 3.x/Swagger 2.0 변환뿐
- **판단을 클라이언트 LLM에 위임**: 비즈니스 메타데이터를 호출 LLM이 생성해 write-back → 서버의 LLM 비용·키 관리 없음
- **OpenAPI 전용 도구**: 엔드포인트 검색·상세 조회(`curl` 예시 생성)·`$ref` 펼치기·태그 목록
- **자동 재색인**: 원문 해시로 변경분만 재색인
- **프로젝트 격리**: 한 프로세스·한 DB에서 `project` 태그로 분리
  - `register_document`는 `project` 필수, 조회·검색은 생략 시 전체 대상
  - 소스 매핑 변경은 재시작 없이 반영
  - `project`는 **보안 경계 아님** (검색 결과 혼입만 방지)

### 대표 도구 (전체 17개)

| 도구 | 설명 |
| --- | --- |
| `register_document` | 문서 등록(URL 또는 원문, `doc_type` 생략 시 자동 판별) |
| `search_documents` | Drive/Notion 협업 문서 검색(제목·본문 청크 3-arm 융합) |
| `search_endpoints` | 자연어/키워드로 OpenAPI 엔드포인트 후보 검색 |
| `get_endpoint_details` | 엔드포인트 상세 조회(`curl` 예시, 메타데이터 없거나 낡으면 `metadata_request` 동봉) |
| `submit_endpoint_metadata` | 호출 LLM이 만든 비즈니스 메타데이터 저장(검색 청크 즉시 재색인) |
| `refresh_index` | 협업 문서 메타 캐시를 원본과 동기화 |

- 전체 도구 인자·반환 필드: [`docs/operations.md`](docs/operations.md#제공되는-도구-전체-목록)

### 비즈니스 메타데이터 write-back

1. `get_endpoint_details` 응답에 메타데이터가 `missing`/`stale`이면 `metadata_request` 포함 (최신이면 키 없음)
2. 호출 LLM이 생성해 `submit_endpoint_metadata`로 전송
3. 서버가 정규화·길이 제한 후 `source_hash` 비교, 같으면 `already_current`
4. 저장 시 해당 엔드포인트 청크만 재조립·재임베딩

- `DOCS_MCP_METADATA_WRITEBACK_ENABLED=false` → `writeback_disabled` 에러 반환

## 핵심 기술 구현 및 개선

### 기술적 의사결정

| 결정 | 기각한 대안 | 근거 | 문서 |
| --- | --- | --- | --- |
| 벡터 스토어: **pgvector 유지** | Qdrant 전환, 키워드 arm 제거·전량 벡터화 (2회 제기) | 단일 DB 유지, 두 번 검토 후 모두 기각 | [06](docs/architect-review/06_vector_store_qdrant_vs_pgvector.md), [14](docs/architect-review/14_vector_only_and_remaining_rag_levers.md) |
| 임베딩: **로컬 CPU `multilingual-e5-small`** | 해시 임베딩, Gemini 관리형 API | 해시는 의미 유사도 없음, API는 키 미설정 시 조용한 폴백·외부 비용·지연·가용성 의존 | [ADR-0004](docs/adr/0004_local_embedding_provider.md) |
| 키워드 검색: **Postgres FTS(tsvector+GIN)** | 애플리케이션 레벨 토큰 매칭 | DB 인덱스 활용, ASCII↔한글 경계 공백 삽입으로 혼합 복합어 처리 | [04](docs/architect-review/04_search_p1_keyword_fts_design.md) |
| 융합: **RRF(등수만 사용)** | 키워드 우선·0건 시 벡터, 점수 가중합 | 두 신호 스케일 상이, 이전 동작은 `fallback` 롤백 스위치로 보존해 비교 측정 가능 | [07](docs/architect-review/07_search_rrf_reevaluation.md) |
| 긴 섹션: **게이트형 subchunking** | 무조건 parent-child N:1 전환 | e5 512토큰 초과분이 경고 없이 절단 → 진단·로깅 먼저, 조건부 분할 | [25](docs/architect-review/25_unconditional_n1_vs_gated_subchunking.md) |
| 메타데이터: **호출 LLM write-back** | 서버가 LLM API로 배치 사전생성 (옵션 CLI로 강등) | 서버의 LLM 비용·키 관리 제거, `source_hash`는 서버가 계산해 덮어쓰기 통제 | [56](docs/architect-review/56_business_metadata_writeback_design.md) |
| 배치: **OS 스케줄러 + 원샷 CLI** | MCP 서버 내장 스케줄러 | stdio 서버는 세션마다 뜨는 단명 프로세스 → 스케줄러 중복 기동·소멸 | [31](docs/architect-review/31_refresh_index_batch_automation.md) |
| 승격 기준: **sealed holdout 확인 전 flag-off** | gate 셋 개선치로 즉시 승격 | n=20 표준오차 ±11%p, gate 과적합 방지 | [103](docs/architect-review/103_endpoint_representation_promotion_verdict.md) |

### 88% → 60%: 벤치 포화 발견

- 합성 하네스(20 엔드포인트): Recall@3 88%·@10 95%
- 실 코퍼스(Stripe 589 + GitHub 1,220 = **1,809** 엔드포인트): Recall@10 **60%**
- 88%는 성능이 아니라 벤치 포화의 증거

### 측정 신뢰성 확보

- n=20 표준오차 ±11%p대 → Recall@10은 **45~60% 구간**으로 보고
- 벡터 arm `ORDER BY` tie-break 누락으로 재색인마다 순위가 흔들리던 결함 수정 (이전 측정치 무효)
- **sealed holdout** 분리, gate manifest로 질의·코퍼스·판정 기준 프리즈

### 개선안 6건, 전부 데이터로 기각

| 개선안 | 결과 |
| --- | --- |
| P1 벡터 질의 재구성 | 기각 ([95](docs/architect-review/95_p1_vector_reformulation_rejection_verdict.md)) |
| P2 arm 구제(rescue) | 기각 ([93](docs/architect-review/93_p2_arm_rescue_effectiveness_verdict.md)) |
| P3 로컬 cross-encoder 리랭크 | 코드 병합, **미승격** ([99](docs/architect-review/99_p3_feature_on_diagnostic_verdict.md)) |
| B1/B2 RRF ablation | 기각 ([98](docs/architect-review/98_b1_b2_verdict_and_p0_coordinate_reconciliation.md)) |
| 검색시점 키워드 변형 | 트랙 종료 ([74](docs/architect-review/74_p02_coverage_fix_failure_and_keyword_variant_stop_verdict.md)) |
| 결정적 endpoint-representation arm | hard gate 통과(R@10 +0.10), **미승격·dark 유지** ([103](docs/architect-review/103_endpoint_representation_promotion_verdict.md)) |

- endpoint-representation arm: miss 11→9는 n=20 표준오차 안 → sealed holdout 재확인 전까지 미승격
- 원칙: **측정이 개선을 증명하지 못하면 flag-off 유지**

### 남은 과제

- 목표 대비 -45~50%p
- 실패 축: 교차언어 질의 · 흔한 토큰 범람 · RRF 회귀 3건
- 분해 결과: [29번 문서 §13](docs/architect-review/29_search_quality_eval_real_corpus_results.md)

## 실행 방법

- 의존성 설치~환경 설정 준비 후 MCP 연동에서 클라이언트에 등록 (이후 클라이언트가 프로세스 직접 실행)

### 의존성 설치

```bash
uv sync --extra test
```

### 데이터베이스 준비

```bash
docker compose up -d postgres
uv run alembic upgrade head
```

### 환경 설정

<!-- AUTO-GENERATED: app/core/config.py 기준 -->

- `.env` 또는 환경변수로 설정 (`.env.example` 참고)
- 전부 기본값 있음, DB 접속 정보가 다르면 `DOCS_MCP_DATABASE_URL` 지정

**필수**

| 변수                    | 설명                           | 기본값                                                           |
| ----------------------- | ------------------------------ | ---------------------------------------------------------------- |
| `DOCS_MCP_DATABASE_URL` | PostgreSQL(+pgvector) 연결 URL | `postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp` |

**선택 — Drive/Notion 사용 시** (비우면 협업 문서 도구만 비활성)

| 변수                                  | 설명                                                  | 기본값 |
| ------------------------------------- | ------------------------------------------------------ | ------ |
| `DOCS_MCP_DRIVE_SERVICE_ACCOUNT_FILE` | Drive 서비스 계정 키 파일 경로                          | (없음) |
| `DOCS_MCP_DRIVE_SERVICE_ACCOUNT_JSON` | Drive 서비스 계정 키 JSON 문자열(파일 경로보다 우선)    | (없음) |
| `DOCS_MCP_NOTION_TOKEN`               | Notion Integration Token. 비우면 Notion 소스 비활성     | (없음) |

**튜닝**

| 변수                                       | 설명                                                                                     | 기본값                           |
| ------------------------------------------ | ---------------------------------------------------------------------------------------- | -------------------------------- |
| `DOCS_MCP_EMBEDDING_MODEL`                 | 로컬 CPU 임베딩 모델(sentence-transformers). 384차원 고정                                | `intfloat/multilingual-e5-small` |
| `DOCS_MCP_EMBEDDING_BACKEND`               | `local`(실제 의미 유사도) \| `hash`(결정적 해시, 모델 다운로드 없음)                     | `local`                          |
| `DOCS_MCP_SEARCH_STRATEGY`                 | **`search_endpoints` 전용** 검색 전략. `rrf`(키워드+벡터 순위 융합) \| `fallback`(롤백 스위치) | `rrf`                            |
| `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY`        | **`search_documents` 전용** 검색 전략. `indexed`(색인된 본문 청크 + 제목 3-arm 순위 융합) \| `fetch`(본문 실시간 조회 후 가중합, 롤백 스위치). 미인식 값은 `fetch` 로 degrade | `indexed`                        |
| `DOCS_MCP_LOG_LEVEL`                       | 로그 레벨                                                                                | `INFO`                           |
| `DOCS_MCP_DOCUMENT_SOURCE_TIMEOUT_SECONDS` | Drive/Notion HTTP 타임아웃(초)                                                           | `15.0`                           |
| `DOCS_MCP_DOCUMENT_FETCH_MAX_CHARS`        | 문서 본문 fetch 시 잘라낼 최대 문자 수                                                   | `200000`                         |
| `DOCS_MCP_NOTION_VERSION`                  | Notion REST API 버전(`Notion-Version` 헤더)                                              | `2022-06-28`                     |
| `DOCS_MCP_METADATA_WRITEBACK_ENABLED`      | 호출 LLM 이 `submit_endpoint_metadata` 로 비즈니스 메타데이터를 저장하는 경로 스위치. `0`/`false`/`no` 면 비활성 | `true`                           |

<!-- /AUTO-GENERATED -->

- Drive: 서비스 계정 생성 → 대상 폴더를 서비스 계정 이메일에 **뷰어로 공유**
- Notion: Integration 토큰 발급 → 대상 페이지/DB를 Integration에 연결
- 레거시 환경변수: [`docs/operations.md`](docs/operations.md#레거시-환경변수)

### MCP 연동

```bash
claude mcp add docs-mcp -s user \
  -e DOCS_MCP_DATABASE_URL=postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp \
  -- uv run --directory /path/to/docs-mcp python -m app.mcp.server

claude mcp list              # 등록 확인
claude mcp remove docs-mcp   # 등록 해제
```

- PostgreSQL이 미리 떠 있어야 함
- `/path/to/docs-mcp` → 저장소 실제 절대경로
- `-s user` 전역 / 생략 시 `local` / `-s project` 프로젝트 한정
- Claude Desktop·`uvx`: [`docs/operations.md`](docs/operations.md#mcp-클라이언트-등록)

### 문서 등록

```
# Markdown/CSV/OpenAPI — doc_type 생략 시 자동 판별
register_document(project="my-api", source_url="https://example.com/openapi.json")
register_document(project="my-api", raw_document="<원문 문자열 또는 dict>")

# PDF/DOCX — base64 raw_document + doc_type 필수
register_document(project="my-api", raw_document="<base64 인코딩 문자열>", doc_type="pdf")

# Google Drive — 폴더 매핑
register_drive_source(project="my-api", folder_id="<Drive 폴더 ID>")

# Notion — DB 또는 페이지(하위 재귀) 매핑
register_notion_source(project="my-api", database_id="<Notion DB ID>")
register_notion_page(project="my-api", page_id="<Notion 페이지 ID>")
```

- 자동 판별 규칙: `app/services/parser/document_router.py`의 `detect_doc_type`
- Drive/Notion은 매핑 후 `refresh_index` 실행해야 검색 대상
- Drive: 네이티브 문서 + PDF/DOCX/XLSX/PPTX 지원, 이미지/영상 등은 오류
- Notion: 한 project에 database·page 매핑 공존 불가 (나중 호출이 덮어씀)

### 자동 동기화 (배치)

```bash
uv run python -m app.scripts.refresh_documents [--include-registered] [--no-index-bodies]
```

- 주기는 systemd timer 또는 cron이 소유
- 권장: 메타 캐시 1시간마다, `--include-registered` 1일 1회 야간
- 중복·동시 실행은 Postgres advisory lock으로 차단
- `--no-index-bodies`: 대량 초기 동기화에서만 사용 (끄면 검색이 제목 매칭으로 퇴화)
- 설정 예시·종료코드: [`docs/operations.md`](docs/operations.md#자동-동기화-배치)

### 테스트 실행

```bash
docker compose up -d postgres
DOCS_MCP_TEST_DATABASE_URL=postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp \
  uv run pytest
```

- 테스트마다 격리 DB 생성/삭제 (`tests/conftest.py`), `postgres` 실행 필요
