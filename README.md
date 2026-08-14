# docs-mcp: RAG MCP Server

Markdown·CSV·PDF/DOCX·OpenAPI(Swagger) 문서와 Google Drive/Notion 협업 문서를 수집·색인하고, 하이브리드 검색(키워드+벡터)으로 필요한 문서 내용을 찾아주는 **MCP 서버**입니다. Claude Desktop/Code 등 MCP 호환 클라이언트에 도구로 등록해 사용하며, 최종 자연어 답변은 서버가 아니라 호출 LLM(Claude/ChatGPT)이 검색 결과를 근거로 생성합니다.

## 주요 기능

- **다양한 문서 소스 관리**: URL 또는 원문으로 Markdown, CSV, PDF/DOCX, OpenAPI 3.x/Swagger 2.0 문서를 등록·조회·삭제. Google Drive/Notion 은 폴더/DB 매핑으로 연결합니다.
- **하이브리드 검색(RRF 융합)**: 키워드(Postgres FTS)와 벡터 유사도를 **RRF(Reciprocal Rank Fusion)**로 항상 융합해 문서 섹션을 찾습니다.
- **OpenAPI 전용 도구**: OpenAPI/Swagger 로 등록한 문서는 엔드포인트 검색·상세 조회(`curl` 예시 생성 포함)·`$ref` 펼치기·태그 목록을 추가로 제공합니다.
- **자동 재색인**: 원문 해시를 비교해 변경된 문서만 다시 색인합니다.

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

## 시작하기

아래 1~3 은 준비 단계입니다. 끝나면 [MCP 연동](#mcp-model-context-protocol-연동)에서 클라이언트에 서버를 등록하며, 등록 후에는 클라이언트가 프로세스를 직접 실행하므로 서버를 따로 띄울 필요가 없습니다.

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
불필요 — 아래 [프로젝트 격리](#4-프로젝트-격리) 참고). 전부 비워두면 협업
문서 도구만 비활성화되고, OpenAPI·Markdown 등 등록 문서 검색은 그대로 동작합니다.

| 변수                                  | 설명                                                  | 기본값 |
| ------------------------------------- | ------------------------------------------------------ | ------ |
| `DOCS_MCP_DRIVE_SERVICE_ACCOUNT_FILE` | Drive 서비스 계정 키 파일 경로                          | (없음) |
| `DOCS_MCP_DRIVE_SERVICE_ACCOUNT_JSON` | Drive 서비스 계정 키 JSON 문자열(파일 경로보다 우선)    | (없음) |
| `DOCS_MCP_NOTION_TOKEN`               | Notion Integration Token. 비우면 Notion 소스 비활성     | (없음) |

**레거시 — project 개념 도입 전 하위호환용, 새로 시작한다면 비워두세요.**
`project="default"` 전용 슬롯 1개뿐이고 값을 바꾸면 서버 재시작이 필요합니다.
위 도구로 등록하는 쪽을 권장합니다.

| 변수                           | 설명                                                                | 기본값 |
| ------------------------------ | --------------------------------------------------------------------- | ------ |
| `DOCS_MCP_DRIVE_FOLDER_ID`     | 기본 프로젝트용 Google Drive 폴더 ID(하위 폴더 재귀 포함)              | (없음) |
| `DOCS_MCP_NOTION_DATABASE_ID`  | 기본 프로젝트용 Notion 데이터베이스 ID. 비우면 워크스페이스 전체가 대상 | (없음) |
| `DOCS_MCP_NOTION_PAGE_ID`      | 기본 프로젝트용 Notion 허브 페이지 ID. 하위 페이지/데이터베이스를 재귀 탐색(최대 4단계)한 결과가 대상 | (없음) |

> `DOCS_MCP_NOTION_DATABASE_ID` 와 `DOCS_MCP_NOTION_PAGE_ID` 를 함께 설정하면 page 가 우선하고 database 는 무시됩니다.

**튜닝 — 기본값으로 두어도 정상 동작합니다.**

| 변수                                       | 설명                                                                                     | 기본값                           |
| ------------------------------------------ | ---------------------------------------------------------------------------------------- | -------------------------------- |
| `DOCS_MCP_EMBEDDING_MODEL`                 | 로컬 CPU 임베딩 모델(sentence-transformers). 384차원 고정                                | `intfloat/multilingual-e5-small` |
| `DOCS_MCP_EMBEDDING_BACKEND`               | `local`(실제 의미 유사도) \| `hash`(결정적 해시, 모델 다운로드 없음)                     | `local`                          |
| `DOCS_MCP_SEARCH_STRATEGY`                 | `search_endpoints` 검색 전략. `rrf`(키워드+벡터 순위 융합) \| `fallback`(롤백 스위치)    | `rrf`                            |
| `DOCS_MCP_LOG_LEVEL`                       | 로그 레벨                                                                                | `INFO`                           |
| `DOCS_MCP_DOCUMENT_SOURCE_TIMEOUT_SECONDS` | Drive/Notion HTTP 타임아웃(초)                                                           | `15.0`                           |
| `DOCS_MCP_DOCUMENT_FETCH_MAX_CHARS`        | 문서 본문 fetch 시 잘라낼 최대 문자 수                                                   | `200000`                         |
| `DOCS_MCP_NOTION_VERSION`                  | Notion REST API 버전(`Notion-Version` 헤더)                                              | `2022-06-28`                     |

<!-- /AUTO-GENERATED -->

- Google Drive 를 쓰려면 서비스 계정을 하나 만들고, 검색 대상 폴더를 그 서비스
  계정 이메일에 **뷰어로 공유**합니다. 팀원 개별 OAuth 로그인은 필요 없습니다.
- Notion 은 Integration 을 만들어 토큰을 발급하고, 대상 페이지/데이터베이스를
  해당 Integration 에 연결합니다.

## MCP (Model Context Protocol) 연동

진입점은 `app/mcp/server.py` 이며, 아래처럼 등록해두면 클라이언트가 `command`+`args`로
프로세스를 실행해 stdio 로 통신합니다. 단 MCP 서버가 DB 에 접속하므로 **PostgreSQL(+pgvector)은
미리 떠 있어야** 합니다(위 2단계).

### 1. Claude Desktop 설정 (macOS/Windows)

Claude Desktop의 설정 파일(`claude_desktop_config.json`)에 다음과 같이 서버를 추가합니다.

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "docs-mcp": {
      "command": "uv",
      "args": ["run", "python", "-m", "app.mcp.server"],
      "cwd": "/path/to/docs-mcp",
      "env": {
        "DOCS_MCP_DATABASE_URL": "postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp"
      }
    }
  }
}
```

### 2. Claude Code (CLI) 설정

`claude mcp add` 로 등록합니다. `--` 뒤가 MCP 서버를 실제로 실행할 `command`+`args`이며,
stdio 가 기본 전송이라 `--transport` 는 필요 없습니다.

```bash
claude mcp add docs-mcp -s user \
  -e DOCS_MCP_DATABASE_URL=postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp \
  -- uv run --directory /path/to/docs-mcp python -m app.mcp.server
```

- `/path/to/docs-mcp` 는 이 저장소의 **실제 절대경로**로 바꾸세요. `--directory` 로 프로젝트
  경로를 고정하므로(현재 작업 디렉터리와 무관), Desktop JSON 의 `cwd` 에 대응합니다.
- `-s user` 는 전역(모든 프로젝트) 등록입니다. 생략하면 `local`(현재 폴더에서만) 이 되고,
  특정 프로젝트에만 쓰려면 `-s` 를 생략하거나 `-s project` 로 등록하세요.

등록 확인·제거:

```bash
claude mcp list              # 등록된 MCP 서버 목록
claude mcp remove docs-mcp   # 등록 해제
```

### 3. uvx로 실행 (uv 프로젝트 설치 없이 실행)

`pyproject.toml`에 `docs-mcp` 스크립트가 등록되어 있어, `uv sync` 없이도
[uvx](https://docs.astral.sh/uv/guides/tools/)로 바로 실행할 수 있습니다.
`command`+`args`를 `uvx`+`["--from", "/path/to/docs-mcp", "docs-mcp"]`로
바꾸면 되며, `DOCS_MCP_DATABASE_URL`은 위와 동일하게 전달합니다.

> `uvx`는 애플리케이션 코드만 격리 설치할 뿐 DB는 대신 띄워주지 않으므로,
> 실행 전 PostgreSQL(+pgvector)이 별도로 떠 있어야 합니다(온프레미스 서버 어디든
> `docker compose up -d postgres`로 가능, 클라우드 관리형 Postgres 필수 아님).

### 4. 프로젝트 격리

이 서버는 하나의 프로세스·하나의 DB 로 **여러 프로젝트**의 문서를 함께
서비스합니다. `register_document` 는 `project` 지정이 필수이고, 조회·검색
도구들(`list_documents`, `search_endpoints`, `list_tags`, `resolve_ref`,
`search_documents`, `refresh_index`)은 `project` 로 범위를 좁힐 수
있습니다(생략 시 전체 프로젝트 대상 — 하위 호환).

> **`project` 는 단순 문자열 태그이며 보안 경계가 아닙니다.** 인증도 접근 제어도
> 없어, 같은 DB 자격증명을 가진 누구나 `project` 필터 없이 모든 문서를 조회할 수
> 있습니다. 막아주는 것은 "여러 프로젝트의 검색 결과가 섞이는 문제"뿐이므로, 신뢰
> 수준이 다른 사용자를 격리하려면 별도 서버·DB·인증 계층이 필요합니다.

프로젝트별 Drive/Notion 소스 매핑은 `register_drive_source`/`register_notion_source`/
`register_notion_page`(아래 도구 표 참고)로 하며, 매핑 등록/변경은 서버 재시작
없이 다음 호출부터 반영됩니다. 자격증명(`DOCS_MCP_DRIVE_SERVICE_ACCOUNT_FILE`/`_JSON`,
`DOCS_MCP_NOTION_TOKEN`)은 서버 전체가 하나씩만 갖고, 프로젝트별로 달라지는
것은 그 자격증명으로 접근할 **폴더/DB 범위**뿐입니다.

**기존 문서의 취급**: `project` 개념이 도입되기 전에 등록된 문서는 모두
`project="default"` 로 백필되어 있습니다. 다른 프로젝트로 옮기려면 문서를
재등록하거나, DB 에 직접 SQL 로 `project` 컬럼을 갱신해야 합니다(제공되는
도구 중에는 기존 문서의 project 를 바꾸는 기능이 없습니다).

### 5. 제공되는 도구 (Tools)

<!-- AUTO-GENERATED: app/mcp/server.py 도구 docstring 기준 -->

| 도구                     | 설명                                                                                                                                                                             | 반환 필드                                                                                                               |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `list_documents`         | 등록된 문서(Markdown/CSV/PDF/DOCX/OpenAPI)의 요약 목록을 반환한다. `project` 로 범위를 제한할 수 있다(생략 시 전체)                                                                       | document_id, title, version, doc_type, project, source_url, endpoints_count, indexed_at                                 |
| `register_document`      | 신규 문서를 등록한다. `project`(필수)와 URL 또는 원문 중 하나를 제공해야 한다 (`doc_type`으로 openapi/markdown/csv 강제 지정 가능, 생략 시 자동 판별)                            | document_id, title, version, doc_type, project, endpoints_count, sections_count, chunks_count, status                   |
| `search_endpoints`       | 자연어/키워드로 엔드포인트 **후보만** 가볍게 검색한다 (키워드 우선, 0건일 때만 벡터 보조). `project`/`document_id` 로 범위를 제한할 수 있다                                      | items[{endpoint_id, method, path, summary, match_type}]                                                                 |
| `get_endpoint_details`   | 특정 엔드포인트의 상세 정보를 조회한다 (`include_example=true`일 때만 curl 예시 포함)                                                                                            | endpoint_id, document_id, method, path, summary, description, tags, parameters, request_body, responses, (example_code) |
| `resolve_ref`            | `$ref` 컴포넌트 스키마를 필드 목록으로 펼친다 (중첩 `$ref`는 이름만 표기). `project`/`document_id` 로 여러 프로젝트의 동명 스키마 중 하나를 특정할 수 있다                       | name, document_id, fields[{name, type, required, description}]                                                          |
| `list_tags`              | 등록 문서의 태그 목록과 태그별 엔드포인트 수를 반환한다. `project`/`document_id` 로 범위를 제한할 수 있다                                                                        | tags[{name, endpoint_count}]                                                                                            |
| `search_documents`       | 팀 협업 문서(Google Drive / Notion)를 검색한다 (메타 캐시로 후보를 추린 뒤 후보 본문만 실시간 조회). `project` 로 범위를 제한할 수 있다. 결과 0건/부족 시 `query_variants`(동의어·유사 표현 목록)로 후보 필터만 넓혀 재질의할 수 있다(점수·순위엔 영향 없음) | items[{title, source, project, url, snippet, score}]                                                                    |
| `get_document`           | `source`("drive"/"notion")와 `external_id`(Drive file ID 또는 Notion page ID)로 협업 문서 한 건의 전체 원문을 조회한다 (항상 최신 원문, 캐시 아님)                               | title, source, url, content                                                                                             |
| `refresh_index`          | 협업 문서 메타 캐시(제목·수정일)를 원본과 동기화한다 (본문은 저장하지 않음). `project` 로 특정 프로젝트만 갱신할 수 있다. `include_registered=true`(기본 false)면 URL로 등록한 ApiDocument 도 원본을 재fetch+재색인한다(`raw_document` 등록분은 자동 제외, `force=true` 로 해시 동일해도 강제 재색인) | synced, added, updated, removed, failed_sources, (include_registered=true 일 때만) registered{total, reindexed, skipped, failed} |
| `register_drive_source`  | 프로젝트에 Google Drive 폴더를 매핑한다(upsert, 같은 project 재호출 시 폴더 교체)                                                                                                | project, folder_id, status                                                                                              |
| `list_drive_sources`     | 등록된 프로젝트→Drive 폴더 매핑 목록을 반환한다(project 오름차순). `project` 로 범위를 제한할 수 있다                                                                            | items[{project, folder_id, created_at, updated_at}]                                                                     |
| `remove_drive_source`    | 프로젝트의 Drive 폴더 매핑을 제거한다(멱등 — 미등록 project 도 오류 아님)                                                                                                        | project, removed                                                                                                        |
| `register_notion_source` | 프로젝트에 Notion 데이터베이스를 매핑한다(upsert, 같은 project 재호출 시 DB 교체). 한 project 는 database 매핑과 page 매핑을 동시에 가질 수 없다(나중 호출이 이전 매핑을 덮어씀) | project, database_id, status                                                                                            |
| `register_notion_page`   | 프로젝트에 Notion 허브 페이지를 매핑한다(upsert). 지정한 페이지 하위의 페이지·데이터베이스(그 안의 행 포함)를 재귀 탐색(최대 4단계)한 결과가 검색 대상이 된다                    | project, page_id, status                                                                                                |
| `list_notion_sources`    | 등록된 프로젝트→Notion 데이터베이스/페이지 매핑 목록을 반환한다(project 오름차순). `project` 로 범위를 제한할 수 있다                                                            | items[{project, database_id, kind, created_at, updated_at}]                                                             |
| `remove_notion_source`   | 프로젝트의 Notion 데이터베이스/페이지 매핑을 제거한다(멱등 — 미등록 project 도 오류 아님)                                                                                        | project, removed                                                                                                        |

협업 문서(Drive/Notion)는 사전 색인하지 않고 `search_documents` 호출 시점에
본문을 실시간 조회한다(캐시엔 제목·URL·수정일만 저장). 새로 만든 문서가
검색되지 않으면 `refresh_index` 를 먼저 실행한다.

Drive/Notion 자격증명이 없으면 협업 문서 도구(`search_documents`/`get_document`/
`refresh_index`)는 등록은 되지만 호출 시 "미구성" `IntegrationError`
(`no document source is configured: ...`)를 반환한다. **"소스 미설정"과 "검색 결과
0건"은 구별된다** — 소스가 정상 구성됐는데 맞는 문서가 없으면 오류가 아니라 빈
`items` 를 돌려준다. 어느 경우든 OpenAPI 경로는 영향받지 않는다.

결과가 0건이거나 기대보다 적으면 문서 제목이 질의와 다른 표현을 쓰는 경우가 많다.
같은 `query` 로 재호출하되 `query_variants` 에 동의어·영한 혼용을 담아 넘긴다:

```
search_documents(query="주문조회 API", query_variants=["결제 내역 조회", "order lookup"])
```

`query_variants` 는 1단계 SQL 후보 필터만 넓히고 점수·순위에는 섞이지 않는다.

모든 도구는 `DomainError`/`IntegrationError` 발생 시 스택트레이스 대신
`{"error": true, "code": ..., "message": ...}` 형태의 에러 페이로드를 반환한다
(응답 스키마는 `app/mcp/types.py` 참고).

<!-- /AUTO-GENERATED -->

### 6. 제공되는 리소스 (Resources)

- `document://{document_id}/raw`: 문서 원문 보기

### 7. 문서별 등록 방법

준비 단계(1~3)와 MCP 서버 등록은 이미 끝났다고 가정합니다 →
[시작하기](#시작하기), [MCP 연동](#mcp-model-context-protocol-연동).

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

`refresh_index` 를 수동 호출하지 않도록 메타 캐시(+선택적 등록 문서 재색인)를
갱신하는 원샷 CLI 를 제공합니다. MCP stdio 서버는 세션마다 뜨는 단명 프로세스라
스케줄러를 품을 수 없으므로, 이 스크립트는 **한 번 돌고 종료**하고 주기는 OS
스케줄러(systemd timer 또는 cron)가 소유합니다
(설계: [`docs/architect-review/32-refresh-index-batch-automation.md`](docs/architect-review/32-refresh-index-batch-automation.md)).

```bash
uv run python -m app.scripts.refresh_documents \
  [--source drive|notion] [--project PROJECT] [--include-registered] [--force]
```

인자는 `refresh_index` 도구와 동일한 의미입니다. 두 축을 다른 주기로
돌립니다:

- **축 A(메타 캐시 동기화)** — 문서 목록·제목·수정일만 갱신(본문 미조회).
  **1시간마다** 권장. 실측 1틱 **47초**(1시간 예산의 1.3%)라 여유가 큽니다. 단
  Drive 하위 폴더 BFS 순회로 호출 수가 폴더 수에 비례하니, 폴더 트리가 큰
  프로젝트는 1틱을 직접 재보고 주기를 늘리세요.
- **축 B(등록 문서 재색인, `--include-registered`)** — `source_url` 이 있는 문서마다
  원본을 재fetch·재파싱·재임베딩합니다. 변경이 없어도 fetch 비용이 들어 **1일
  1회(야간)** 만 돌립니다. `--force` 는 배치에서 쓰지 않습니다(해시 동일 시 skip 이
  정상 경로).

중복 실행은 Postgres advisory lock 으로 막습니다. 두 축은 락 키가 달라 축 B 실행
중에도 축 A 틱이 굶지 않습니다.

### systemd user timer (권장)

```ini
# ~/.config/systemd/user/docs-refresh.service
[Service]
Type=oneshot
WorkingDirectory=/home/<user>/projects/docs-mcp
ExecStart=/home/<user>/.local/bin/uv run python -m app.scripts.refresh_documents

# ~/.config/systemd/user/docs-refresh.timer
[Timer]
OnBootSec=2min
OnUnitActiveSec=1h
Persistent=true
[Install]
WantedBy=timers.target
```

축 B는 같은 형태로 `docs-resync.service`(`ExecStart=... --include-registered`)
+ `OnCalendar=daily` 타이머를 하나 더 둡니다.

```bash
systemctl --user enable --now docs-refresh.timer docs-resync.timer
```

### cron (systemd 미가용 환경, 예: WSL2)

```cron
0 * * * * cd /home/<user>/projects/docs-mcp && /home/<user>/.local/bin/uv run python -m app.scripts.refresh_documents >> output/logs/refresh.log 2>&1
30 3 * * * cd /home/<user>/projects/docs-mcp && /home/<user>/.local/bin/uv run python -m app.scripts.refresh_documents --include-registered >> output/logs/resync.log 2>&1
```

### 실행 환경 함정 (크론/타이머가 손으로 돌릴 때와 다르게 깨지는 지점)

- **cwd** — 설정 로딩이 `.env` 를 cwd 기준 상향 탐색으로 찾습니다. cron의
  기본 cwd(홈)에서는 `.env` 를 못 찾고, Drive 서비스계정 파일 상대경로도
  같이 깨집니다. → `WorkingDirectory=`(systemd) / `cd <repo> &&`(cron)
  **필수**.
- **PATH** — cron 의 PATH 에는 `uv` 가 없습니다. **절대경로**로 씁니다.
- **자격증명** — `DOCS_MCP_NOTION_TOKEN`, Drive 서비스계정 파일이 배치를
  실행하는 사용자 권한으로 읽혀야 합니다(`secrets/` 권한 확인). 누락되면
  소스 전량이 실패해 exit code 1 로 드러납니다.
- **로그** — stderr에 JSON 한 줄을 남깁니다. systemd 면 journal이 받고,
  cron 이면 `output/logs/` 로 리다이렉트하세요(이미 `.gitignore` 대상).

### 종료코드

| 상황                                     | 종료코드 |
| ---------------------------------------- | -------- |
| 전 대상 실패(모든 소스 갱신 실패)        | 1        |
| 부분 실패/락 미획득(이미 실행 중)/정상   | 0        |

부분 실패를 1로 올리지 않는 이유는 실패한 항목이 다음 갱신에서 자동
재시도되기 때문입니다(WARN 로그에 실패한 `<project>/<source>` 가 남으므로
지속 실패는 로그로 추적됩니다).

## 검색 아키텍처 (요약)

검색은 **키워드 arm**(Postgres FTS)과 **벡터 arm**(pgvector 코사인 + HNSW)을 **RRF(Reciprocal Rank Fusion)로 항상 융합**합니다. 최종 답변은 서버가 고르지 않고 호출 LLM 이 반환된 후보(top_k) 중에서 선택합니다 — 서버는 **후보 피더**이고 품질 지표는 recall@k 입니다(확장 평가셋 84질의에서 Recall@3 88%·@10 95%).

- [`docs/search-flow.md`](docs/search-flow.md) — 두 검색 경로의 전체 흐름(단계·코드 위치·다이어그램)
- [`docs/architect-review/03-search-performance-improvements.md`](docs/architect-review/03-search-performance-improvements.md) — 성능 개선 P1~P6 및 구현 상태
- [`docs/architect-review/09-search-quality-post-rrf.md`](docs/architect-review/09-search-quality-post-rrf.md) — 평가셋·RRF 실측·K 스윕·리랭킹(P3) 착수 검토

## 테스트 실행

```bash
docker compose up -d postgres
DOCS_MCP_TEST_DATABASE_URL=postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp \
  uv run pytest
```

테스트는 매번 격리된 PostgreSQL database를 생성/삭제하므로(`tests/conftest.py`),
`postgres` 서비스가 실행 중이어야 합니다.
