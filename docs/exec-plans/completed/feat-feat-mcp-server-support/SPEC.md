# SPEC: MCP 서버 지원

## 개요
이 프로젝트의 기존 OpenAPI RAG 및 검색 기능을 MCP(Model Context Protocol) 도구로 노출하여 Claude Desktop 등의 LLM 에이전트가 지능적으로 API 문서를 검색하고 활용할 수 있게 한다.

## 데이터 흐름
- LLM (Claude) → MCP Client → Stdio Transport → **MCP Server (FastMCP)** → ServiceBundle → Database/Search

## 기능 목록

### 기능 1: list_documents
- 설명: 시스템에 등록된 모든 OpenAPI 문서 목록을 반환한다.
- 입력: 없음
- 출력: `List[DocumentSummary]`
- 검증 기준: DB의 `api_document` 테이블 내용을 정확히 반영해야 한다.

### 기능 2: register_document
- 설명: 신규 OpenAPI 문서를 등록한다.
- 입력: `source_url` 또는 `raw_document` (JSON/YAML)
- 출력: 등록된 문서의 메타데이터
- 검증 기준: `SyncService`를 통해 파싱, 색인이 원자적으로 수행되어야 한다.

### 기능 3: search_endpoints
- 설명: 자연어 질의로 API 엔드포인트를 검색한다.
- 입력: `query`, `top_k`, `mode` (hybrid/keyword/vector)
- 출력: 검색된 엔드포인트 목록 및 스니펫
- 검증 기준: 하이브리드 검색 엔진의 결과를 반환해야 한다.

### 기능 4: query_rag
- 설명: RAG 기반의 자연어 질의응답을 수행한다.
- 입력: `question`, `top_k`
- 출력: 답변 및 인용(citations)
- 검증 기준: 답변에 근거(citations)가 포함되어야 하며 `is_grounded` 상태를 표시해야 한다.

### 기능 5: get_endpoint_details
- 설명: 엔드포인트 상세 스펙과 호출 예시(curl)를 제공한다.
- 입력: `endpoint_id`
- 출력: 상세 메타데이터 및 `example_code`
- 검증 기준: `ExampleService`를 연동하여 실제 호출 가능한 코드를 포함해야 한다.
