# docs-mcp: RAG MCP Server

Markdown·CSV·PDF/DOCX·OpenAPI(Swagger) 문서와 Google Drive/Notion 협업 문서를 수집·색인하고, 하이브리드 검색(키워드+벡터)으로 필요한 문서 내용을 찾아주는 **MCP 서버**입니다. Claude Desktop/Code 등 MCP 호환 클라이언트에 도구로 등록해 사용하며, 최종 자연어 답변은 서버가 아니라 호출 LLM(Claude/ChatGPT)이 검색 결과를 근거로 생성합니다.

> 클라이언트별 등록, 전체 도구 목록, 배치 자동화 같은 운영 상세는 [`docs/operations.md`](docs/operations.md) 에 있습니다.

## 주요 기능

- **다양한 문서 소스 관리**: URL 또는 원문으로 Markdown, CSV, PDF/DOCX, OpenAPI 3.x/Swagger 2.0 문서를 등록·조회·삭제. Google Drive/Notion 은 폴더/DB 매핑으로 연결합니다.
- **하이브리드 검색(RRF 융합)**: 키워드(Postgres FTS)와 벡터 유사도를 **RRF(Reciprocal Rank Fusion)**로 항상 융합해 문서 섹션을 찾습니다.
- **OpenAPI 전용 도구**: OpenAPI/Swagger 로 등록한 문서는 엔드포인트 검색·상세 조회(`curl` 예시 생성 포함)·`$ref` 펼치기·태그 목록을 추가로 제공합니다.
- **자동 재색인**: 원문 해시를 비교해 변경된 문서만 다시 색인합니다.
- **프로젝트 격리**: 하나의 프로세스·DB 로 여러 프로젝트 문서를 함께 서비스하고 `project` 로 검색 범위를 좁힙니다.

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

## 검색 아키텍처 (요약)

검색은 **키워드 arm**(Postgres FTS)과 **벡터 arm**(pgvector 코사인 + HNSW)을 **RRF(Reciprocal Rank Fusion)로 항상 융합**합니다. 최종 답변은 서버가 고르지 않고 호출 LLM 이 반환된 후보(top_k) 중에서 선택합니다 — 서버는 **후보 피더**이고 품질 지표는 recall@k 입니다(확장 평가셋 84질의에서 Recall@3 88%·@10 95%).

위 수치는 자체 제작 평가셋 기준입니다. 실제 대형 스펙(Stripe/GitHub OpenAPI) 20질의에서는
Recall@10 50%로 떨어지며, 그 간격과 대응 경위는 아래 구현 여정 문서에 정리해 두었습니다.

- [`docs/implementation-journey.md`](docs/implementation-journey.md) — 로드맵 0~5가 실제로 어떤 순서·판단을 거쳐 구현됐는지(커밋·설계문서 매핑, 기각된 개선안 포함)
- [`docs/search-flow.md`](docs/search-flow.md) — 두 검색 경로의 전체 흐름(단계·코드 위치·다이어그램)
- [`docs/architect-review/03_search_performance_improvements.md`](docs/architect-review/03_search_performance_improvements.md) — 성능 개선 P1~P6 및 구현 상태
- [`docs/architect-review/09_search_quality_post_rrf.md`](docs/architect-review/09_search_quality_post_rrf.md) — 평가셋·RRF 실측·K 스윕·리랭킹(P3) 착수 검토

## 시작하기

아래 1~3 은 준비 단계입니다. 끝나면 [MCP 연동](#mcp-연동)에서 클라이언트에 서버를 등록하며, 등록 후에는 클라이언트가 프로세스를 직접 실행하므로 서버를 따로 띄울 필요가 없습니다.

### 1. 의존성 설치

이 프로젝트는 [uv](https://docs.astral.sh/uv/)로 의존성을 관리합니다.

```bash
uv sync --extra test
```

### 2. 데이터베이스 준비

PostgreSQL(+`pgvector` 확장)이 필요합니다. `docker-compose.yml`로 로컬 인스턴스를 띄울 수 있습니다.

```bash
docker compose up -d postgres
uv run alembic upgrade head
```

### 3. 환경 설정

<!-- AUTO-GENERATED: app/core/config.py 기준 -->

`.env.example`을 참고해 `.env` 파일 또는 환경변수로 설정을 조절할 수 있습니다.
모든 변수에 기본값이 있어 `.env` 없이도 기동하지만, DB 접속 정보가 기본값과
다르면 `DOCS_MCP_DATABASE_URL` 은 반드시 지정해야 합니다.

**필수 — 이 값 없이는 서버가 뜨지 않습니다.**

| 변수                    | 설명                           | 기본값                                                           |
| ----------------------- | ------------------------------ | ---------------------------------------------------------------- |
| `DOCS_MCP_DATABASE_URL` | PostgreSQL(+pgvector) 연결 URL | `postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp` |

**선택 — Google Drive / Notion 문서 검색을 쓸 때만.** 자격증명만 여기 두고,
"어떤 폴더/페이지를 볼지"는 `register_drive_source`/`register_notion_source`/
`register_notion_page` 도구로 등록하세요(project 별 다중 등록 가능, 재시작
불필요). 전부 비워두면 협업 문서 도구만 비활성화되고, OpenAPI·Markdown 등
등록 문서 검색은 그대로 동작합니다.

| 변수                                  | 설명                                                  | 기본값 |
| ------------------------------------- | ------------------------------------------------------ | ------ |
| `DOCS_MCP_DRIVE_SERVICE_ACCOUNT_FILE` | Drive 서비스 계정 키 파일 경로                          | (없음) |
| `DOCS_MCP_DRIVE_SERVICE_ACCOUNT_JSON` | Drive 서비스 계정 키 JSON 문자열(파일 경로보다 우선)    | (없음) |
| `DOCS_MCP_NOTION_TOKEN`               | Notion Integration Token. 비우면 Notion 소스 비활성     | (없음) |

**튜닝 — 기본값으로 두어도 정상 동작합니다.**

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

<!-- /AUTO-GENERATED -->

- [`docs/operations.md`](docs/operations.md#레거시-환경변수) — `project` 개념 도입 전 하위호환용 레거시 슬롯(`DOCS_MCP_DRIVE_FOLDER_ID` 등)

- Google Drive 를 쓰려면 서비스 계정을 하나 만들고, 검색 대상 폴더를 그 서비스
  계정 이메일에 **뷰어로 공유**합니다. 팀원 개별 OAuth 로그인은 필요 없습니다.
- Notion 은 Integration 을 만들어 토큰을 발급하고, 대상 페이지/데이터베이스를
  해당 Integration 에 연결합니다.

## MCP 연동

진입점은 `app/mcp/server.py` 이며, 아래처럼 등록해두면 클라이언트가 `command`+`args`로
프로세스를 실행해 stdio 로 통신합니다. 단 MCP 서버가 DB 에 접속하므로 **PostgreSQL(+pgvector)은
미리 떠 있어야** 합니다(위 2단계).

`claude mcp add` 로 등록합니다. `--` 뒤가 MCP 서버를 실제로 실행할 `command`+`args`이며,
stdio 가 기본 전송이라 `--transport` 는 필요 없습니다.

```bash
claude mcp add docs-mcp -s user \
  -e DOCS_MCP_DATABASE_URL=postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp \
  -- uv run --directory /path/to/docs-mcp python -m app.mcp.server
```

- `/path/to/docs-mcp` 는 이 저장소의 **실제 절대경로**로 바꾸세요. `--directory` 로 프로젝트
  경로를 고정하므로 현재 작업 디렉터리와 무관합니다.
- `-s user` 는 전역(모든 프로젝트) 등록입니다. 생략하면 `local`(현재 폴더에서만) 이 되고,
  특정 프로젝트에만 쓰려면 `-s project` 로 등록하세요.

등록 확인·제거:

```bash
claude mcp list              # 등록된 MCP 서버 목록
claude mcp remove docs-mcp   # 등록 해제
```

- [`docs/operations.md`](docs/operations.md#mcp-클라이언트-등록) — Claude Desktop(JSON 설정)·`uvx` 로 실행하는 방법

## 프로젝트 격리

하나의 프로세스·하나의 DB 로 여러 프로젝트의 문서를 함께 서비스합니다.
`register_document` 는 `project` 지정이 필수이고, 조회·검색 도구들은 `project` 로
범위를 좁힐 수 있습니다(생략 시 전체 프로젝트 대상). 소스 매핑 등록/변경은 서버
재시작 없이 다음 호출부터 반영됩니다.

> `project` 는 단순 문자열 태그이며 **보안 경계가 아닙니다.** 막아주는 것은 "여러
> 프로젝트의 검색 결과가 섞이는 문제"뿐입니다.

- [`docs/operations.md`](docs/operations.md#프로젝트-격리-상세) — 세부 사항(자격증명 공유 범위, 기존 문서 백필 취급)

## 제공되는 도구 (Tools)

MCP 도구 17개를 제공합니다. 대표 도구는 다음과 같습니다.

| 도구                   | 설명                                                                                   |
| ---------------------- | ---------------------------------------------------------------------------------------- |
| `register_document`    | 신규 문서를 등록한다(URL 또는 원문, `doc_type` 생략 시 자동 판별)                          |
| `search_documents`     | 팀 협업 문서(Google Drive / Notion)를 검색한다(제목·색인 본문 청크 3-arm 순위 융합)         |
| `search_endpoints`     | 자연어/키워드로 OpenAPI 엔드포인트 후보를 검색한다                                         |
| `get_endpoint_details` | 특정 엔드포인트의 상세 정보를 조회한다(`curl` 예시 생성 포함)                              |
| `refresh_index`        | 협업 문서 메타 캐시를 원본과 동기화한다                                                    |

- [`docs/operations.md`](docs/operations.md#제공되는-도구-전체-목록) — 전체 17개 도구의 인자·반환 필드 표, 에러 페이로드 규약

## 문서별 등록 방법

준비 단계(1~3)와 MCP 서버 등록은 이미 끝났다고 가정합니다 →
[시작하기](#시작하기), [MCP 연동](#mcp-연동).

**(A) Markdown/CSV/OpenAPI — URL 또는 원문으로 등록**

```
register_document(project="my-api", source_url="https://example.com/openapi.json")
register_document(project="my-api", raw_document="<원문 문자열 또는 dict>")
```

`doc_type` 을 생략하면 URL 확장자와 원문 내용으로 openapi/csv/markdown 을 자동
판별합니다(규칙은 `app/services/parser/document_router.py` 의 `detect_doc_type`).
애매하면 `doc_type="openapi"|"markdown"|"csv"` 로 지정하세요. `raw_document` 가
dict 이면 JSON 문자열로 변환됩니다.

**(B) PDF/DOCX — base64 원문 + doc_type 필수**

```
register_document(project="my-api", raw_document="<base64 인코딩 문자열>", doc_type="pdf")
```

자동 판별 대상이 아니라 **`doc_type` 필수**이고, **`source_url` 이 아니라
`raw_document` 로만** 등록됩니다. 텍스트 추출 후 markdown 과 동일하게 섹션화됩니다.

**(C) Google Drive — 폴더 매핑**

```
register_drive_source(project="my-api", folder_id="<Drive 폴더 ID>")
```

폴더 자체를 색인하지는 않습니다. 매핑 후 `refresh_index` 를 실행해야
메타 캐시(제목·수정일)가 채워지고 `search_documents` 대상이 됩니다.

Google 네이티브 문서(Docs/Sheets/Slides)는 물론, PDF/DOCX/XLSX/PPTX
바이너리 파일도 업로드해두면 텍스트를 추출해 검색 대상이 됩니다. 그 외
바이너리(이미지/영상 등)는 텍스트 추출을 지원하지 않아 조회 시 오류로
처리됩니다.

**(D) Notion — 데이터베이스 또는 페이지 매핑**

```
register_notion_source(project="my-api", database_id="<Notion DB ID>")
# 또는: 특정 페이지 하위의 페이지·데이터베이스(그 안의 행 포함)를 재귀 탐색해 대상으로
register_notion_page(project="my-api", page_id="<Notion 페이지 ID>")
```

한 project 는 database 매핑과 page 매핑을 동시에 가질 수 없습니다(나중
호출이 이전 매핑을 덮어씀). Drive와 마찬가지로 매핑 후 `refresh_index` 를
실행해야 검색 대상이 됩니다.

## 자동 동기화 (배치)

`refresh_index` 를 수동 호출하지 않도록 원샷 CLI 를 제공하고, 주기는 OS
스케줄러(systemd timer 또는 cron)가 소유합니다.

```bash
uv run python -m app.scripts.refresh_documents [--include-registered] [--no-index-bodies]
```

메타 캐시 동기화는 1시간마다, 등록 문서 재색인(`--include-registered`)은 1일 1회
야간에 돌리기를 권장합니다. 중복 실행과 `refresh_index` MCP 도구와의 동시 실행은
같은 Postgres advisory lock 을 공유해 막습니다.

본문 색인(협업 문서를 fetch 해 섹션 청크로 색인)은 기본으로 켜져 있습니다.
기본 검색 전략(`indexed`)의 keyword/vector arm 신호가 여기서 채워지므로,
끄면 검색이 제목 매칭만으로 조용히 퇴화합니다. 비용(문서마다 fetch + 파싱 +
임베딩)을 아껴야 하는 대량 초기 동기화에서만 `--no-index-bodies` 로 끕니다.
- [`docs/operations.md`](docs/operations.md#자동-동기화-배치) — 타이머/크론 설정 예시, 실행 환경 함정, 종료코드 규약

## 테스트 실행

```bash
docker compose up -d postgres
DOCS_MCP_TEST_DATABASE_URL=postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp \
  uv run pytest
```

테스트는 매번 격리된 PostgreSQL database를 생성/삭제하므로(`tests/conftest.py`),
`postgres` 서비스가 실행 중이어야 합니다.
