## docs-mcp 프로젝트

**목적**: OpenAPI 문서를 RAG(Retrieval-Augmented Generation) 기술로 색인하고 자연어 질의응답을 제공하는 MCP 서버

**기술 스택**:
- Python 3.11+
- FastAPI 0.110+ (REST API)
- SQLAlchemy 2.0 (ORM, SQLite)
- Pydantic v2 (데이터 검증)
- MCP 1.0 (Claude Desktop 통합)
- 벡터 검색: 인메모리 인덱스 + 해시 임베딩

**주요 기능**:
- OpenAPI/Swagger 문서 등록 및 관리
- 하이브리드 검색 (키워드 + 벡터)
- RAG 기반 질의응답
- 자동 코드 예시 생성 (curl, fetch, axios, python)

**현재 구조**:
- `src/`: 메인 애플리케이션 코드
- `tests/`: 테스트 코드
- `docs/`: 문서
- `harness/`: 플래너, 제너레이터, 평가자 오케스트레이터
