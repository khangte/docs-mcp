# docs-mcp: OpenAPI RAG Server

OpenAPI(Swagger) 문서를 수집, 색인하고 RAG(Retrieval-Augmented Generation) 기술을 활용하여 API 명세에 대한 자연어 질의응답 및 검색 서비스를 제공하는 서버입니다.

## 주요 기능

- **OpenAPI 문서 관리**: URL 또는 로컬 텍스트를 통해 OpenAPI 3.x 및 Swagger 2.0 문서를 등록, 목록 조회 및 삭제할 수 있습니다.
- **하이브리드 검색**: 키워드(토큰 매칭)와 벡터 유사도 검색을 결합하여 원하는 API 엔드포인트를 정확하게 찾아냅니다.
- **RAG 질의응답**: 등록된 API 명세를 기반으로 사용자 질문에 답변하고, 근거가 되는 API 경로 및 요약 정보를 함께 제공합니다.
- **코드 예시 생성**: 엔드포인트 상세 정보로부터 `curl`, `fetch`, `axios`, `python(requests)` 등 다양한 포맷의 호출 예시 코드를 즉시 생성합니다.
- **자동 재색인**: 문서의 내용 변경을 감지(해시 비교)하여 변경된 경우에만 지능적으로 인덱스를 업데이트합니다.

## 기술 스택

- **Backend**: Python 3.10+, FastAPI
- **Database**: SQLAlchemy 2.0 (기본 SQLite)
- **Search/RAG**:
  - 인메모리 벡터 인덱스 (InMemoryVectorIndex)
  - 결정적 해시 기반 임베딩 (HashEmbeddingProvider)
  - 하이브리드 검색 엔진 (Keyword + Vector)
- **Documentation**: Pydantic v2 (Schema/DTO)

## 프로젝트 구조

```text
src/
├── app.py           # uvicorn 진입점 (create_app 임포트)
├── bootstrap.py     # AppState 팩토리 (main/mcp_server 공유)
├── main.py          # FastAPI 앱 팩토리
├── mcp_server.py    # MCP 서버 (Claude Desktop 통합)
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

```bash
pip install -r requirements.txt
```

### 2. 환경 설정

`.env` 파일 또는 환경변수를 통해 설정을 조절할 수 있습니다. (기본값은 `src/core/config.py` 참고)

- `DOCS_MCP_DATABASE_URL`: 데이터베이스 연결 URL (기본: `sqlite:///./docs_mcp.db`)
- `DOCS_MCP_LOG_LEVEL`: 로그 레벨 (기본: `INFO`)

### 3. 서버 실행

```bash
uvicorn src.app:app --reload
```

또는 팩토리 패턴 사용:

```bash
uvicorn src.main:create_app --factory --reload
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
      "command": "python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/path/to/docs-mcp",
      "env": {
        "DOCS_MCP_DATABASE_URL": "sqlite:///./docs_mcp.db"
      }
    }
  }
}
```

### 2. 제공되는 도구 (Tools)

- `list_documents`: 등록된 문서 목록 확인
- `register_document`: 새 OpenAPI 문서 등록 (URL/텍스트)
- `search_endpoints`: API 엔드포인트 검색
- `query_rag`: 자연어 질의응답 (RAG)
- `get_endpoint_details`: 엔드포인트 상세 정보 및 코드 예시 조회

### 3. 제공되는 리소스 (Resources)

- `document://{document_id}/raw`: 문서 원문 보기

## 테스트 실행

```bash
pytest tests/
```
