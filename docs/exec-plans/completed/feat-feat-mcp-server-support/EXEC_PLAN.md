# EXEC_PLAN

## 목표
- 이 프로젝트를 Claude 및 ChatGPT에서 사용할 수 있는 MCP(Model Context Protocol) 서버로 전환하여, LLM이 OpenAPI 문서를 지능적으로 검색하고 자연어로 질의응답할 수 있게 한다.

## 접근법
- Anthropic의 공식 Python MCP SDK(`mcp`)를 사용하여 기존의 RAG 및 검색 서비스를 MCP Tool로 노출한다.
- `src/mcp_server.py` 진입점을 신설하여 stdio 방식의 MCP 통신을 지원한다.
- 기존의 FastAPI `AppState`와 `ServiceBundle` 인프라를 재사용하여 코드 중복을 최소화한다.

## 단계별 계획
1. **의존성 추가**: `pyproject.toml`에 `mcp`, `anyio` 등을 추가하고 설치한다. [완료]
2. **MCP 서버 구현**: `src/mcp_server.py`를 생성하고 `McpServer` 인스턴스를 초기화한다. [완료]
3. **도구(Tools) 등록**: [완료]
    - `list_documents`: 등록된 문서 목록 조회
    - `register_document`: 새 OpenAPI 문서 등록
    - `search_endpoints`: 엔드포인트 하이브리드 검색
    - `query_rag`: RAG 기반 자연어 질의응답
    - `get_endpoint_details`: 엔드포인트 상세 정보 및 예시 코드 조회
4. **통합 테스트**: `mcp` 클라이언트를 모방하여 각 도구가 정상 작동하는지 검증한다. [완료]
5. **README 업데이트**: MCP 서버 연동 방법(Claude Desktop 설정 등)을 문서화한다. [완료]

## 완료 기준
- [x] `src/mcp_server.py`를 통해 stdio 기반 MCP 서버 실행 가능
- [x] Claude Desktop에서 해당 서버의 도구들을 인식하고 호출 가능
- [x] 검색 및 RAG 질의응답 결과가 MCP를 통해 정상적으로 LLM에게 전달됨
