# docs-mcp: OpenAPI RAG Server

OpenAPI(Swagger) 문서를 수집, 색인하고 RAG(Retrieval-Augmented Generation) 기술을 활용하여 API 명세에 대한 자연어 질의응답 및 검색 서비스를 제공하는 서버입니다.

## 주요 기능

- **다양한 문서 소스 관리**: URL 또는 로컬 텍스트를 통해 OpenAPI 3.x/Swagger 2.0, Markdown, CSV 문서를 등록, 목록 조회 및 삭제할 수 있습니다.
- **하이브리드 검색**: 키워드(토큰 매칭)와 벡터 유사도 검색을 결합하여 원하는 API 엔드포인트 또는 문서 섹션을 정확하게 찾아냅니다.
- **RAG 질의응답**: 등록된 문서를 기반으로 사용자 질문에 답변하고, 근거가 되는 API 경로/섹션 및 요약 정보를 함께 제공합니다. Gemini API 키가 설정되면 실제 LLM이 답변을 생성하고, 없으면 템플릿 기반으로 폴백합니다.
- **코드 예시 생성**: 엔드포인트 상세 정보로부터 `curl`, `fetch`, `axios`, `python(requests)` 등 다양한 포맷의 호출 예시 코드를 즉시 생성합니다.
- **자동 재색인**: 문서의 내용 변경을 감지(해시 비교)하여 변경된 경우에만 지능적으로 인덱스를 업데이트합니다.

## 기술 스택

<!-- AUTO-GENERATED: pyproject.toml, docker-compose.yml, app/core/config.py 기준 -->
- **Backend**: Python 3.11+, FastAPI
- **Database**: PostgreSQL(+`pgvector` 확장) — SQLAlchemy 2.0, Alembic 마이그레이션
- **Search/RAG**:
  - pgvector 코사인 거리(`<=>`, HNSW 인덱스) 기반 벡터 검색
  - 임베딩: Gemini API(`google-genai`, `GeminiEmbeddingProvider`) 또는 결정적 해시 기반 폴백(`HashEmbeddingProvider`)
  - LLM 답변: Gemini API(`GeminiLLMProvider`) 또는 템플릿 기반 폴백(`TemplateLLMProvider`)
  - 하이브리드 검색 엔진 (Keyword + Vector)
- **문서 파서**: OpenAPI/Swagger, Markdown, CSV (`app/services/parser/document_router.py`가 자동 판별)
- **MCP**: `fastmcp` 서드파티 패키지
- **Documentation**: Pydantic v2 (Schema/DTO)
<!-- /AUTO-GENERATED -->

## 프로젝트 구조

```text
app/
├── bootstrap.py     # AppState 팩토리 (main/mcp_server 공유)
├── main.py          # FastAPI 앱 팩토리 + uvicorn 진입점
├── mcp_server.py    # MCP 서버 (Claude Desktop 통합)
├── mcp_types.py     # MCP 도구 응답 TypedDict 스키마
├── api/             # FastAPI 라우트 및 의존성 주입
├── core/            # 공통 설정, DB 엔진, 예외 및 로깅
├── models/          # SQLAlchemy ORM 모델 (Base, ApiDocument 등)
├── repositories/    # 데이터베이스 액세스 레이어 (CRUD)
├── schemas/         # Pydantic DTO (요청/응답 모델)
└── services/        # 비즈니스 로직
    ├── examples/    # 호출 예시 코드 생성 서비스
    ├── indexer/     # 청크 생성 및 벡터 색인 서비스
    ├── ingestor/    # 문서 수집 및 동기화 서비스
    ├── parser/      # OpenAPI/Swagger 파서 및 정규화
    ├── rag/         # RAG 파이프라인 및 LLM 프로바이더
    └── search/      # 하이브리드 검색 서비스
```

## 시작하기

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

| 변수 | 필수 | 설명 | 기본값 |
|------|------|------|--------|
| `DOCS_MCP_DATABASE_URL` | No | PostgreSQL(+pgvector) 연결 URL | `postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp` |
| `DOCS_MCP_EMBEDDING_DIM` | No | 임베딩 벡터 차원 (pgvector 컬럼 생성 시 고정됨) | `256` |
| `DOCS_MCP_HYBRID_ALPHA` | No | 하이브리드 검색 키워드 가중치 (0.0=벡터만, 1.0=키워드만) | `0.4` |
| `DOCS_MCP_LOG_LEVEL` | No | 로그 레벨 | `INFO` |
| `DOCS_MCP_GEMINI_API_KEY` | No | Gemini API 키. 비워두면 LLM/임베딩이 각각 템플릿·해시 기반으로 폴백 | (없음) |
| `DOCS_MCP_GEMINI_MODEL` | No | Gemini 답변 생성 모델 | `gemini-2.0-flash` |
| `DOCS_MCP_GEMINI_EMBEDDING_MODEL` | No | Gemini 임베딩 모델 | `gemini-embedding-001` |
<!-- /AUTO-GENERATED -->

### 4. 서버 실행

```bash
uv run uvicorn app.main:create_app --factory --reload
```

서버가 실행되면 `http://localhost:8000/docs`에서 Swagger UI를 통해 API를 테스트할 수 있습니다.

## 주요 API 가이드

- **문서 등록**: `POST /documents` (URL 또는 raw_document 전달)
- **하이브리드 검색**: `GET /search?query=...&mode=hybrid`
- **RAG 질문**: `POST /query` (JSON: `{"question": "사용자 정보를 조회하는 API는 뭐야?"}`)
- **예시 생성**: `GET /endpoints/{endpoint_id}/example?format=curl`

## MCP (Model Context Protocol) 연동

이 프로젝트는 Claude Desktop 및 기타 MCP 호환 클라이언트에서 도구로 사용할 수 있는 MCP 서버 기능을 제공합니다.

### 1. Claude Desktop 설정 (macOS/Windows)

Claude Desktop의 설정 파일(`claude_desktop_config.json`)에 다음과 같이 서버를 추가합니다.

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "docs-mcp": {
      "command": "uv",
      "args": ["run", "python", "-m", "app.mcp_server"],
      "cwd": "/path/to/docs-mcp",
      "env": {
        "DOCS_MCP_DATABASE_URL": "postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp"
      }
    }
  }
}
```

### 2. 제공되는 도구 (Tools)

<!-- AUTO-GENERATED: app/mcp_server.py 도구 docstring 기준 -->
| 도구 | 설명 | 반환 필드 |
|------|------|-----------|
| `list_documents` | 등록된 모든 문서(OpenAPI/Markdown/CSV)의 요약 목록을 반환한다 | document_id, title, version, doc_type, source_url, endpoints_count, indexed_at |
| `register_document` | 신규 문서를 등록한다. URL 또는 원문 중 하나를 제공해야 한다 (`doc_type`으로 openapi/markdown/csv 강제 지정 가능, 생략 시 자동 판별) | document_id, title, version, doc_type, endpoints_count, sections_count, chunks_count, status |
| `search_endpoints` | 자연어로 API 엔드포인트 또는 문서 섹션을 검색한다 (하이브리드/키워드/벡터 모드) | endpoint_id, chunk_type, method, path, summary, score, snippet |
| `query_rag` | API 명세에 대해 자연어로 질문하고 RAG 기반 답변을 받는다 | answer, citations(method/path/snippet), is_grounded |
| `get_endpoint_details` | 특정 엔드포인트의 상세 정보와 호출 예시 코드를 조회한다 | endpoint_id, method, path, summary, description, example_code |

모든 도구는 `DomainError`/`IntegrationError` 발생 시 스택트레이스 대신
`{"error": true, "code": ..., "message": ...}` 형태의 에러 페이로드를 반환한다
(응답 스키마는 `app/mcp_types.py` 참고).
<!-- /AUTO-GENERATED -->

### 3. 제공되는 리소스 (Resources)

- `document://{document_id}/raw`: 문서 원문 보기

## 테스트 실행

```bash
docker compose up -d postgres
DOCS_MCP_TEST_DATABASE_URL=postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp \
  uv run pytest
```

테스트는 매번 격리된 PostgreSQL database를 생성/삭제하므로(`tests/conftest.py`),
`postgres` 서비스가 실행 중이어야 합니다.
