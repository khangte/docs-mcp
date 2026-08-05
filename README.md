# docs-mcp: OpenAPI RAG MCP Server

OpenAPI(Swagger)·Markdown·CSV 문서와 Google Drive/Notion 협업 문서를 수집, 색인하고 하이브리드 검색(키워드+벡터)으로 원하는 API 엔드포인트나 문서 내용을 찾아주는 **MCP 서버**입니다. Claude Desktop/Code 등 MCP 호환 클라이언트에 도구로 등록해 사용하는 것이 주 용도이며, 최종 자연어 답변 생성은 서버가 아니라 호출 LLM(Claude/ChatGPT)이 검색 결과를 근거로 수행합니다.

## 주요 기능

- **다양한 문서 소스 관리**: URL 또는 로컬 텍스트를 통해 OpenAPI 3.x/Swagger 2.0, Markdown, CSV 문서를 등록, 목록 조회 및 삭제할 수 있습니다.
- **하이브리드 검색**: 키워드(토큰 매칭)와 벡터 유사도 검색을 결합하여 원하는 API 엔드포인트 또는 문서 섹션을 정확하게 찾아냅니다.
- **코드 예시 생성**: `get_endpoint_details`에서 `include_example=true`로 조회하면 엔드포인트 상세 정보로부터 `curl` 호출 예시 코드를 즉시 생성합니다.
- **자동 재색인**: 문서의 내용 변경을 감지(해시 비교)하여 변경된 경우에만 지능적으로 인덱스를 업데이트합니다.

## 기술 스택

<!-- AUTO-GENERATED: pyproject.toml, docker-compose.yml, app/core/config.py 기준 -->

- **Backend**: Python 3.11+, FastAPI
- **Database**: PostgreSQL(+`pgvector` 확장) — SQLAlchemy 2.0, Alembic 마이그레이션
- **Search**:
  - pgvector 코사인 거리(`<=>`, HNSW 인덱스) 기반 벡터 검색
  - 임베딩: Gemini API(`google-genai`, `GeminiEmbeddingProvider`) 또는 결정적 해시 기반 폴백(`HashEmbeddingProvider`)
  - 하이브리드 검색 엔진 (Keyword + Vector)
- **문서 파서**: OpenAPI/Swagger, Markdown, CSV (`app/services/parser/document_router.py`가 자동 판별)
- **MCP**: `fastmcp` 서드파티 패키지
- **Schema/DTO**: Pydantic v2
<!-- /AUTO-GENERATED -->

## 시작하기

아래 1~3(의존성 설치 → DB 준비 → 환경 설정)은 준비 단계입니다.
준비가 끝나면 [MCP 연동](#mcp-model-context-protocol-연동) 절에서 MCP 클라이언트에 서버를 등록합니다.
등록 후에는 클라이언트가 필요할 때마다 프로세스를 직접 실행하므로, 사용자가 서버를 따로 띄우는 단계는 없습니다.

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

| 변수                                       | 필수 | 설명                                                                                   | 기본값                                                           |
| ------------------------------------------ | ---- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `DOCS_MCP_DATABASE_URL`                    | No   | PostgreSQL(+pgvector) 연결 URL                                                         | `postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp` |
| `DOCS_MCP_EMBEDDING_DIM`                   | No   | 임베딩 벡터 차원 (pgvector 컬럼 생성 시 고정됨)                                        | `256`                                                            |
| `DOCS_MCP_HYBRID_ALPHA`                    | No   | 하이브리드 검색 키워드 가중치 (0.0=벡터만, 1.0=키워드만)                               | `0.4`                                                            |
| `DOCS_MCP_LOG_LEVEL`                       | No   | 로그 레벨                                                                              | `INFO`                                                           |
| `DOCS_MCP_GEMINI_API_KEY`                  | No   | Gemini API 키. 비워두면 임베딩이 결정적 해시 기반(`HashEmbeddingProvider`)으로 폴백    | (없음)                                                           |
| `DOCS_MCP_GEMINI_EMBEDDING_MODEL`          | No   | Gemini 임베딩 모델                                                                     | `gemini-embedding-001`                                           |
| `DOCS_MCP_DRIVE_FOLDER_ID`                 | No   | 검색 범위로 고정할 Google Drive 폴더 ID(하위 폴더 재귀 포함). 비우면 Drive 소스 비활성 | (없음)                                                           |
| `DOCS_MCP_DRIVE_SERVICE_ACCOUNT_FILE`      | No   | 서비스 계정 키 파일 경로                                                               | (없음)                                                           |
| `DOCS_MCP_DRIVE_SERVICE_ACCOUNT_JSON`      | No   | 서비스 계정 키 JSON 문자열(파일 경로보다 우선)                                         | (없음)                                                           |
| `DOCS_MCP_NOTION_TOKEN`                    | No   | Notion Integration Token. 비우면 Notion 소스 비활성                                    | (없음)                                                           |
| `DOCS_MCP_NOTION_DATABASE_ID`              | No   | 검색 범위를 특정 Notion 데이터베이스 하위로 한정                                       | (없음)                                                           |
| `DOCS_MCP_NOTION_VERSION`                  | No   | Notion REST API 버전(`Notion-Version` 헤더)                                            | `2022-06-28`                                                     |
| `DOCS_MCP_DOCUMENT_SOURCE_TIMEOUT_SECONDS` | No   | Drive/Notion HTTP 타임아웃(초)                                                         | `15.0`                                                           |
| `DOCS_MCP_DOCUMENT_FETCH_MAX_CHARS`        | No   | 문서 본문 fetch 시 잘라낼 최대 문자 수                                                 | `200000`                                                         |

<!-- /AUTO-GENERATED -->

- Google Drive 를 쓰려면 서비스 계정을 하나 만들고, 검색 대상 폴더를 그 서비스
  계정 이메일에 **뷰어로 공유**합니다. 팀원 개별 OAuth 로그인은 필요 없습니다.
- Notion 은 Integration 을 만들어 토큰을 발급하고, 대상 페이지/데이터베이스를
  해당 Integration 에 연결합니다.

준비가 끝났으면 다음 절 [MCP 연동](#mcp-model-context-protocol-연동)에서 MCP 클라이언트에 서버를 등록하세요.

## MCP (Model Context Protocol) 연동

이 프로젝트는 Claude Desktop 및 기타 MCP 호환 클라이언트에서 도구로 사용할 수 있는 MCP 서버 기능을 제공합니다.

> `app/mcp/server.py`는 별도 진입점이며, 아래처럼 등록해두면 MCP 클라이언트(Claude
> Desktop/Code 등)가 필요할 때마다 `command`+`args`로 직접 프로세스를 실행해 stdio로
> 통신합니다. 단, **PostgreSQL(+pgvector)은 미리 떠 있어야** 합니다 — MCP 서버가
> 내부적으로 이 DB에 접속하므로, 등록 전에 `docker compose up -d postgres` 와
> `uv run alembic upgrade head` 는 실행해 두세요.

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

`pyproject.toml`의 `[project.scripts]`에 `docs-mcp = "app.mcp.server:main"`이
등록되어 있어, `uv sync` 로 이 프로젝트에 의존성을 설치하지 않고도
[uvx](https://docs.astral.sh/uv/guides/tools/)로 격리된 환경에서 바로 실행할
수 있습니다.

```bash
uvx --from /path/to/docs-mcp docs-mcp
```

MCP 클라이언트 등록 시 `command`+`args`를 `uv run python -m app.mcp.server`
대신 아래처럼 바꾸면 됩니다.

```json
{
  "mcpServers": {
    "docs-mcp": {
      "command": "uvx",
      "args": ["--from", "/path/to/docs-mcp", "docs-mcp"],
      "env": {
        "DOCS_MCP_DATABASE_URL": "postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp"
      }
    }
  }
}
```

> **`uvx`는 애플리케이션 코드만 격리 설치할 뿐, DB를 대신 띄워주지 않습니다.**
> `uvx` 실행 전에 PostgreSQL(+pgvector)이 별도로 떠 있어야 하고,
> `DOCS_MCP_DATABASE_URL`로 그 위치를 알려줘야 합니다. 이 저장소의
> `docker-compose.yml`(`pgvector/pgvector:pg16` 이미지)을 온프레미스 서버(사내
> 서버, NAS, 개인 VM 등 어디든)에 그대로 올려 `docker compose up -d postgres`로
> 띄우면 되며, 클라우드 관리형 Postgres가 필수 조건은 아닙니다 — pgvector
> 확장이 설치된 PostgreSQL 인스턴스 하나만 있으면 됩니다.
>
> Postgres 자체를 다른 DB(SQLite 등)로 교체하는 것은 별개 작업입니다.
> `app/models/openapi.py`의 임베딩 컬럼이 `pgvector.sqlalchemy.Vector` 타입과
> HNSW 코사인 인덱스(`vector_cosine_ops`)를 직접 사용하므로, DB 교체는
> 모델·리포지토리 계층 재작성이 필요한 코드 변경 작업입니다.

### 4. 제공되는 도구 (Tools)

<!-- AUTO-GENERATED: app/mcp/server.py 도구 docstring 기준 -->

| 도구                     | 설명                                                                                                                                                                             | 반환 필드                                                                                                               |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `list_documents`         | 등록된 문서(OpenAPI/Markdown/CSV)의 요약 목록을 반환한다. `project` 로 범위를 제한할 수 있다(생략 시 전체)                                                                       | document_id, title, version, doc_type, project, source_url, endpoints_count, indexed_at                                 |
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
| `register_notion_page`   | 프로젝트에 Notion 허브 페이지를 매핑한다(upsert). 지정한 페이지 바로 아래(1단계, 재귀 없음)의 하위 페이지들이 검색 대상이 된다                                                   | project, page_id, status                                                                                                |
| `list_notion_sources`    | 등록된 프로젝트→Notion 데이터베이스/페이지 매핑 목록을 반환한다(project 오름차순). `project` 로 범위를 제한할 수 있다                                                            | items[{project, database_id, kind, created_at, updated_at}]                                                             |
| `remove_notion_source`   | 프로젝트의 Notion 데이터베이스/페이지 매핑을 제거한다(멱등 — 미등록 project 도 오류 아님)                                                                                        | project, removed                                                                                                        |

협업 문서(Drive/Notion)는 사전 색인하지 않고 `search_documents` 호출 시점에
본문을 실시간 조회한다(캐시엔 제목·URL·수정일만 저장). 새로 만든 문서가
검색되지 않으면 `refresh_index` 를 먼저 실행한다.

Drive/Notion 자격증명이 없으면 이 세 도구는 등록은 되지만 호출 시 "미구성"
`IntegrationError`(`no document source is configured: ...`)를 반환한다.
**"소스 미설정"과 "검색 결과 0건"은 구별된다** — 소스가 정상 구성됐는데 질의에
맞는 문서가 없으면 `search_documents` 는 오류가 아니라 빈 `items` 를 돌려준다.
어느 경우든 OpenAPI 경로는 영향받지 않는다.

`search_documents` 결과가 0건이거나 기대보다 적으면 문서 제목이 질의와 다른
표현을 쓰고 있을 가능성이 크다(예: "주문조회 API" 질의로 "결제 내역 조회"
문서를 못 찾음). 이럴 때는 같은 `query` 로 재호출하되 `query_variants` 에
동의어·영한 혼용·유사 표현을 담아 넘긴다:

```
search_documents(query="주문조회 API", query_variants=["결제 내역 조회", "order lookup"])
```

`query_variants` 는 1단계 SQL 후보 필터만 넓히고 점수·순위 계산에는 섞이지
않는다 — 상위 결과는 여전히 `query` 원본 토큰과 가장 잘 맞는 문서다.

모든 도구는 `DomainError`/`IntegrationError` 발생 시 스택트레이스 대신
`{"error": true, "code": ..., "message": ...}` 형태의 에러 페이로드를 반환한다
(응답 스키마는 `app/mcp/types.py` 참고).

<!-- /AUTO-GENERATED -->

### 5. 문서별 등록 방법

준비 단계(1~3)와 MCP 서버 등록은 이미 끝났다고 가정합니다 →
[시작하기](#시작하기), [MCP 연동](#mcp-model-context-protocol-연동).

**(A) OpenAPI/Swagger — URL로 등록**

```
register_document(project="my-api", source_url="https://example.com/openapi.json")
```

`doc_type` 은 생략 가능합니다. 원문이 `{` 로 시작하거나 앞부분에
`openapi:`/`swagger:` 가 있으면 자동으로 openapi 로 판별됩니다.

**(B) OpenAPI/Markdown/CSV — 원문 직접 등록**

```
register_document(project="my-api", raw_document="<원문 문자열 또는 dict>")
```

`doc_type` 을 생략하면 다음 순서로 자동 판별합니다(`source_url` 을 함께 준
경우 확장자가 `.md`/`.markdown`→markdown, `.csv`→csv 로 우선 적용):

1. 원문이 `{` 로 시작하거나 앞 200자에 `openapi:`/`swagger:` 가 있으면 → openapi
2. 첫 줄에 쉼표가 있고 `#` 으로 시작하지 않으면 → csv
3. 그 외 → markdown

판별이 애매하면 `doc_type="openapi"|"markdown"|"csv"` 로 직접 지정하세요.
`raw_document` 가 dict 이면 내부적으로 JSON 문자열로 변환됩니다.

**(C) PDF/DOCX — base64 원문 + doc_type 필수**

```
register_document(project="my-api", raw_document="<base64 인코딩 문자열>", doc_type="pdf")
```

PDF/DOCX 는 자동 판별 대상이 아니므로 **`doc_type` 지정이 필수**이고,
**`source_url` 이 아니라 `raw_document` 로만** 등록할 수 있습니다(파일을
base64 로 인코딩해 전달). 텍스트 추출 후 markdown 문서와 동일하게
섹션화됩니다.

**(D) Google Drive — 폴더 매핑**

```
register_drive_source(project="my-api", folder_id="<Drive 폴더 ID>")
```

폴더 자체를 색인하지는 않습니다. 매핑 후 `refresh_index` 를 실행해야
메타 캐시(제목·수정일)가 채워지고 `search_documents` 대상이 됩니다.

Google 네이티브 문서(Docs/Sheets/Slides)는 물론, PDF/DOCX/XLSX/PPTX
바이너리 파일도 업로드해두면 텍스트를 추출해 검색 대상이 됩니다. 그 외
바이너리(이미지/영상 등)는 텍스트 추출을 지원하지 않아 조회 시 오류로
처리됩니다.

**(E) Notion — 데이터베이스 또는 페이지 매핑**

```
register_notion_source(project="my-api", database_id="<Notion DB ID>")
# 또는: 특정 페이지 바로 아래(1단계, 재귀 없음) 하위 페이지들을 대상으로
register_notion_page(project="my-api", page_id="<Notion 페이지 ID>")
```

한 project 는 database 매핑과 page 매핑을 동시에 가질 수 없습니다(나중
호출이 이전 매핑을 덮어씀). Drive와 마찬가지로 매핑 후 `refresh_index` 를
실행해야 검색 대상이 됩니다.

> (D)/(E)로 매핑한 협업 문서는 사전 색인 없이 `search_documents` 호출
> 시점에 원본을 실시간 조회합니다. 새로 만든 문서가 검색되지 않으면
> `refresh_index` 를 먼저 실행하세요(자세한 내용은 위 도구 표 아래 설명 참고).

### 6. 프로젝트 격리

이 서버는 하나의 프로세스·하나의 DB 로 **여러 프로젝트**의 문서를 함께
서비스합니다. `register_document` 는 `project` 지정이 필수이고, 조회·검색
도구들(`list_documents`, `search_endpoints`, `list_tags`, `resolve_ref`,
`search_documents`, `refresh_index`)은 `project` 로 범위를 좁힐 수
있습니다(생략 시 전체 프로젝트 대상 — 하위 호환).

> **`project` 는 단순 문자열 태그이며 보안 경계가 아닙니다.** 인증도, 접근
> 제어도 하지 않습니다. 같은 서버·같은 DB 자격증명에 접근할 수 있는 누구나
> 모든 프로젝트의 문서를 `project` 필터 없이 조회할 수 있습니다. 서로 다른
> 신뢰 수준의 사용자를 프로젝트로 격리하려는 목적이라면 이 기능으로는
> 부족하며, 별도 서버·별도 DB·인증 계층이 필요합니다. 이 기능이 막아주는
> 것은 "여러 프로젝트를 한 서버에서 쓸 때 검색 결과가 서로 섞이는 문제"뿐입니다.

**프로젝트별 Drive 폴더/Notion DB 등록**: `register_drive_source(project,
folder_id)` / `register_notion_source(project, database_id)` (또는 Notion
페이지 하위 트리를 쓰려면 `register_notion_page(project, page_id)`) 로
프로젝트마다 다른 소스를 매핑한 뒤 `refresh_index` 를 실행하면 메타 캐시가
채워집니다. 한 project 는 Notion database 매핑과 page 매핑을 동시에 가질 수
없습니다(나중 호출이 이전 매핑을 덮어씀). 매핑 등록/변경은 서버 재시작 없이
다음 호출부터 바로 반영됩니다. `list_*_sources` 로 확인하고
`remove_*_source` 로 제거합니다(미등록 project 제거는 오류 아님 —
`removed: false`).

자격증명(`DOCS_MCP_DRIVE_SERVICE_ACCOUNT_FILE`/`_JSON`,
`DOCS_MCP_NOTION_TOKEN`)은 서버 전체가 **하나씩만** 갖고, 프로젝트별로
달라지는 것은 그 자격증명으로 접근할 **폴더/DB 범위**뿐입니다(Drive ↔ Notion
동일 원칙).

**기존 문서의 취급**: `project` 개념이 도입되기 전에 등록된 문서는 모두
`project="default"` 로 백필되어 있습니다. 다른 프로젝트로 옮기려면 문서를
재등록하거나, DB 에 직접 SQL 로 `project` 컬럼을 갱신해야 합니다(제공되는
도구 중에는 기존 문서의 project 를 바꾸는 기능이 없습니다).

### 7. 제공되는 리소스 (Resources)

- `document://{document_id}/raw`: 문서 원문 보기

## 테스트 실행

```bash
docker compose up -d postgres
DOCS_MCP_TEST_DATABASE_URL=postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp \
  uv run pytest
```

테스트는 매번 격리된 PostgreSQL database를 생성/삭제하므로(`tests/conftest.py`),
`postgres` 서비스가 실행 중이어야 합니다.
