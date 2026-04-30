# 아키텍처 문서 (Simplified)

본 문서는 `docs-mcp`의 핵심 설계 원칙과 구조를 정의한다. 상세 기획은 `docs/product-specs/plan.md`를 참고한다.

## 1. 설계 원칙
- **저장형 검색**: OpenAPI 문서를 실시간 프록시하지 않고, 로컬 DB(PostgreSQL + pgvector)에 색인하여 검색한다.
- **MCP 기반 인터페이스**: 클라이언트는 MCP 도구를 통해서만 문서에 접근한다.
- **관심사 분리**: 수집(Ingestor), 파싱(Parser), 색인(Indexer), 검색(Search) 레이어를 엄격히 분리한다.

## 2. 시스템 구조

```
         [관리자]                         [Claude / MCP 클라이언트]
            |                                       |
     (OpenAPI URL 등록,                   (자연어 질의, 도구 호출)
      재색인 트리거)                               |
            |                                       |
            v                                       v
   +-------------------+                 +-----------------------+
   |   관리 HTTP API   |                 |      MCP 서버         |
   |  (FastAPI routes) |                 |  (tools / adapters)   |
   +---------+---------+                 +-----------+-----------+
             |                                       |
             +------------------+--------------------+
                                v
                      +-------------------+
                      |  내부 서비스 계층 |
                      | (수집/파싱/색인/  |
                      |   검색/예시)      |
                      +---------+---------+
                                |
                    +-----------+-----------+
                    v                       v
           +-----------------+      +------------------+
           | PostgreSQL +    |      | OpenAPI 원본     |
           | pgvector        |      | HTTP 서버        |
           | (저장/검색/이력)|      | (수집 시점에만)  |
           +-----------------+      +------------------+
```

## 3. 레이어 정의 및 의존성

### 3-1. 레이어 역할
- `entry points`: `src/main.py` (FastAPI), `src/mcp_server.py` (MCP Server)
- `api`: HTTP 라우터 및 의존성 주입 (`src/api/routes`)
- `services`: 도메인 로직 (수집, 파싱, 색인, 검색, 예시 생성)
- `repositories`: DB 접근 (SQLAlchemy)
- `models/schemas`: 데이터 모델 및 DTO (Pydantic)
- `core`: 공통 설정, 로깅, DB 세션

### 3-2. 의존 방향
`api / mcp_server` → `services` → `repositories` → `models`
*(단방향 유지, 역참조 및 순환 참조 금지)*

## 4. 핵심 데이터 흐름

### 4-1. 문서 등록 및 색인
1. **Fetch**: 외부 OpenAPI Spec 수집 (해시 검사로 변경분 확인)
2. **Parse**: 엔드포인트 및 스키마 정규화
3. **Index**: 검색 단위로 청크화 및 벡터 임베딩 생성
4. **Store**: PostgreSQL 및 pgvector에 최종 저장

### 4-2. 검색 (Hybrid Search)
- **Vector Search**: pgvector(cosine similarity)를 이용한 의미론적 검색
- **Keyword Search**: Full-text search(tsvector)를 이용한 키워드 매칭
- **Rerank**: 두 결과를 가중 합산하여 최종 순위 결정

## 5. MCP 도구 계약 (Interface)
1. `search_endpoints`: 자연어 질의 기반 API 검색 (Hybrid)
2. `get_endpoint_details`: 특정 API의 상세 명세(파라미터, 스키마 등) 및 호출 예시 조회
3. `query_rag`: API 명세에 대한 자연어 질문 및 RAG 답변 생성
4. `list_documents`: 등록된 문서 목록 조회
5. `register_document`: 신규 OpenAPI 문서 등록 및 색인

## 6. 기술 스택 및 보안
- **Language/Framework**: Python 3.12+, FastAPI
- **Database**: PostgreSQL + pgvector (HNSW index)
- **Auth**: 관리 API는 API Key(`X-API-Key`) 인증, MCP는 읽기 전용으로 제한
- **Observability**: JSON 구조화 로깅 기반 (trace_id 추적)

## 7. ADR (Architecture Decision Records)
주요 결정 사항은 `docs/adr/`에 기록한다.
- ADR-0001: 저장형 검색 구조 채택
- ADR-0002: pgvector 기반 하이브리드 검색 도입
- ADR-0003: MCP 도구의 읽기 전용 경계 유지
