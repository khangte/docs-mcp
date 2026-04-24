# docs-mcp (OpenAPI RAG 서버)

## 개요
여러 Swagger/OpenAPI 문서를 수집·정규화해 단일 검색 저장소로 색인하고, 자연어 질의에 대해 관련 엔드포인트·메서드·파라미터·요청/응답 예시를 **근거(citations)와 함께** 생성해 반환하는 RAG 기반 API 서버다. API 소비자(개발자·에이전트)가 수십 개의 서로 다른 OpenAPI 문서를 직접 뒤지지 않고 "이 기능을 하려면 어떤 엔드포인트를 써야 하는가"를 한 창구에서 얻기 위한 도구다. 입력은 OpenAPI 문서 원문(URL/JSON/YAML)이며, 출력은 정규화된 엔드포인트 메타데이터와 인용 기반 자연어 답변이다.

## 데이터 흐름

### 입력 → 처리 → 출력

```
[원문 OpenAPI 문서]
        │
        ▼
(1) 수집(Fetcher)     ─ 외부 HTTP 혹은 주입된 JSON/YAML 문자열
        │  (raw_text + content_hash)
        ▼
(2) 파서(Parser)      ─ OpenAPI 3.x 구조를 내부 중간 표현으로 변환
        │  (paths[*] × methods[*] = endpoints[], components.schemas)
        ▼
(3) 정규화(Normalizer)─ 엔드포인트/파라미터/요청·응답/스키마를 관계형 엔터티로 분해
        │
        ▼
(4) 청크 빌더         ─ 엔드포인트 단위(+ 스키마 단위) 검색용 텍스트 청크 생성
        │             (method + path + summary + params + examples 직렬화)
        ▼
(5) 임베딩 Provider   ─ 청크 텍스트 → 고정 차원 벡터 (결정적 해시 기반 기본 구현)
        │
        ▼
(6) 저장소(Repository)─ api_document / api_endpoint / api_parameter / api_response /
        │             api_request_body / api_schema / api_chunk / document_sync_history
        │             (동일 document 는 한 트랜잭션에서 교체)
        ▼
[영속 저장 + 인메모리 벡터 인덱스]

--------- 질의 경로 ---------

[사용자 자연어 질의]
        │
        ▼
(A) 질의 임베딩 + 토큰화
        │
        ▼
(B) 하이브리드 검색   ─ 키워드 점수(BM25 lite / TF) + 벡터 점수(cosine)
        │             가중합 + filter(method, tag, document_id)
        ▼
(C) 상위 K 청크 조회 → 원본 endpoint/schema 상세 fetch
        │
        ▼
(D) 컨텍스트 조립     ─ 인용 단위(endpoint_id, method, path, snippet)로 구성
        │
        ▼
(E) LLM Provider 응답 ─ 기본: 템플릿 기반 결정적 생성기. 어댑터 교체 가능
        │             검색 결과 0건이면 "해당 API 를 찾을 수 없음" 고정 응답
        ▼
{ answer: str, citations: [ {endpoint_id, method, path, snippet} ], used_documents: [...] }
```

### 핵심 데이터 스키마 (영속)

| 엔터티 | 주요 컬럼 | 관계 |
|--------|-----------|------|
| `api_document` | id(pk), source_url(unique), title, version, content_hash, indexed_at | 1 : N endpoint, 1 : N schema, 1 : N chunk, 1 : N sync_history |
| `api_endpoint` | id(pk), document_id(fk), method, path, operation_id, summary, description, tags | 1 : N parameter, 1 : N response, 1 : 1 request_body |
| `api_parameter` | id(pk), endpoint_id(fk), name, location(in), required, schema_ref, description | parameter.location ∈ {path, query, header, cookie} |
| `api_request_body` | endpoint_id(pk,fk), content_type, schema_ref, required, example | endpoint 1 : 1 |
| `api_response` | id(pk), endpoint_id(fk), status_code, content_type, schema_ref, description, example | |
| `api_schema` | id(pk), document_id(fk), name, json_schema(raw), description | endpoint 가 `$ref` 로 참조 |
| `api_chunk` | id(pk), document_id(fk), chunk_type({endpoint, schema}), ref_id, text, embedding(blob/list) | 검색 단위 |
| `document_sync_history` | id(pk), document_id(fk), status({registered, reindexed, skipped, failed}), content_hash, error, created_at | 재색인 이력 |

### 제약·불변식
- `api_document.source_url` 은 UNIQUE. 같은 URL 재등록 요청은 "중복" 으로 거부하거나 재색인으로 라우팅한다(기능 11 참고).
- `api_chunk.embedding` 은 고정 차원(예: 256)이며 document 단위로 재색인 시 **DELETE + INSERT** 가 한 트랜잭션에서 수행되어야 한다(중간 상태 외부 노출 금지).
- `api_chunk.chunk_type + ref_id` 는 endpoint/schema 둘 중 하나만을 가리킨다(배타).
- `document_sync_history` 는 append-only. 같은 content_hash 재색인은 `skipped` 로 기록.

## 비기능/제약 (이번 MVP 에서 확정)

- 저장소는 **SQLite** 를 기본 구현으로 쓴다. `DocumentRepository`, `EndpointRepository`, `ChunkRepository`, `VectorIndex` 는 인터페이스로 정의되고, 추후 PostgreSQL+pgvector 로 교체 가능하도록 구현체만 바꾸면 되게 한다.
- 벡터 인덱스는 **인메모리 numpy/list 기반 cosine 검색** 을 기본으로 한다. 서비스 기동 시 DB 청크에서 재구성된다.
- 임베딩은 `EmbeddingProvider` 인터페이스. 기본 구현은 **해시 기반 결정적 의사-임베딩**(외부 API 無, 동일 입력 → 동일 벡터). 실제 OpenAI/임베딩 어댑터는 클래스 자리만 제공.
- 응답 생성은 `LLMProvider` 인터페이스. 기본 구현은 **템플릿 기반 결정적 응답 생성기**(검색 결과 컨텍스트를 포맷 문자열에 주입). 실 LLM 어댑터 자리만 마련.
- OpenAPI 원문 수집은 `OpenAPIFetcher` 인터페이스. 테스트에서는 문자열 주입형 페이크를 쓰며, 외부 네트워크 없이 pytest 가 통과해야 한다.
- MCP 서버 구현은 본 범위 **외**. 다만 서비스 계층은 장차 MCP 어댑터가 붙을 수 있도록 UI/transport 의존성을 갖지 않는다.
- 모든 외부 진입점은 Pydantic 입력 검증을 수행하고, 스택트레이스는 외부로 노출되지 않는다.
- 함수 단일 책임·타입 힌트 필수, 전역 변수/빈 except/하드코딩 경로 금지.

## 기능 목록

### 기능 1: OpenAPI 문서 등록
- 설명: 관리자가 OpenAPI 문서를 시스템에 등록한다. 원문은 URL 또는 직접 전송된 JSON/YAML 바디로 받을 수 있으며, 파싱·정규화·청킹·임베딩·저장이 한 번의 요청으로 원자적으로 수행된다.
- 입력:
  - `source_url`: 문자열(선택). 원문 URL. 제공되면 Fetcher 가 원문을 읽어 온다.
  - `raw_document`: 문자열(선택). JSON 또는 YAML 텍스트. `source_url` 없이 바로 주입 가능.
  - `source_url`, `raw_document` 중 **정확히 하나** 필수.
  - `title_override`: 문자열(선택).
- 출력:
  ```
  {
    "document_id": str,
    "title": str,
    "version": str,
    "source_url": str | null,
    "endpoints_count": int,
    "schemas_count": int,
    "chunks_count": int,
    "content_hash": str,
    "indexed_at": iso8601
  }
  ```
- 검증 기준:
  - Petstore 샘플(엔드포인트 ≥ 3개) 등록 시 `endpoints_count` 가 파일 내 `paths × methods` 수와 일치한다.
  - 동일 `source_url` 을 두 번 등록하려 하면 두 번째 호출은 HTTP 409 로 거부되고, 기존 문서는 변경되지 않는다.
  - `raw_document` 가 빈 문자열 또는 JSON/YAML 파싱 실패면 422 를 반환하고 어떤 행도 쓰이지 않는다.
  - 저장 중 예외 발생 시 `api_document` 포함 모든 하위 엔터티가 롤백된다(부분 저장 금지).
  - 등록 성공 직후 `document_sync_history` 에 `status="registered"` 행이 한 건 추가된다.

### 기능 2: OpenAPI 파싱·정규화
- 설명: OpenAPI 3.0/3.1 원문을 내부 도메인 모델(엔드포인트·파라미터·요청·응답·컴포넌트 스키마)로 변환한다. `$ref` 는 동일 문서 내 `#/components/schemas/*` 에 한해 해석한다.
- 입력: 파싱 가능한 OpenAPI 문서 문자열(JSON/YAML).
- 출력:
  - 중간 표현 객체 `ParsedDocument { title, version, endpoints: [ParsedEndpoint], schemas: [ParsedSchema] }`
  - 각 `ParsedEndpoint` 는 `method`, `path`, `operation_id`, `summary`, `description`, `tags`, `parameters[]`, `request_body?`, `responses[]` 를 포함.
- 검증 기준:
  - 동일한 `path` 아래 여러 메서드(GET/POST 등)가 각각 별개의 endpoint 로 분리된다.
  - path 파라미터(`/pet/{petId}`)가 `api_parameter.location="path"`, `required=true` 로 저장된다.
  - `requestBody.content."application/json".schema.$ref` 가 컴포넌트 스키마 ID 로 해석되어 `schema_ref` 에 저장된다.
  - 존재하지 않는 `$ref` 는 파싱 실패가 아니라 `schema_ref=None` 으로 관대하게 처리(기능 5 의 예시 생성에서 대체 문자열 사용).
  - OpenAPI 가 아니거나 최상위 `paths` 가 없는 입력은 명시적 `ParserError` 를 발생시킨다.

### 기능 3: 검색용 청크 빌드·임베딩
- 설명: 각 엔드포인트(와 주요 컴포넌트 스키마)를 검색 단위 청크로 직렬화하고, `EmbeddingProvider` 로 벡터를 생성해 `api_chunk` 에 적재한다.
- 입력: `ParsedDocument`.
- 출력: `List[ChunkRecord { id, document_id, chunk_type, ref_id, text, embedding }]`.
- 검증 기준:
  - 엔드포인트 1개당 최소 1개 청크가 생성된다(method, path, summary, 주요 파라미터명이 텍스트에 포함).
  - 같은 입력 텍스트에 대해 기본 `HashEmbeddingProvider` 가 **항상 동일한 벡터** 를 반환한다(결정성).
  - 임베딩 벡터의 차원은 전 청크에서 일정(기본 256) 하고 norm 이 0 이 아니다.
  - 재색인 시 해당 document 의 기존 청크 전부가 DELETE 된 뒤 신규 INSERT 되며, 중간 상태에서 외부 검색 호출은 **전(前) 상태 혹은 후(後) 상태** 중 하나만 본다(원자성).

### 기능 4: 키워드 + 벡터 하이브리드 검색
- 설명: 자연어 질의에 대해 키워드 매칭 점수와 벡터 유사도 점수를 가중합해 상위 K 개 청크를 반환한다. `method`, `tag`, `document_id` 필터 지원.
- 입력:
  ```
  {
    "query": str (필수, 비어있지 않음),
    "top_k": int (1~20, 기본 5),
    "method": "GET"|"POST"|"PUT"|"PATCH"|"DELETE"|null,
    "tag": str | null,
    "document_id": str | null
  }
  ```
- 출력:
  ```
  {
    "query": str,
    "count": int,
    "items": [
      { "endpoint_id": str, "document_id": str, "method": str, "path": str,
        "summary": str, "score": float, "keyword_score": float,
        "vector_score": float, "snippet": str }
    ]
  }
  ```
- 검증 기준:
  - "find pet by id" 질의 시 `GET /pet/{petId}` 가 top-1 에 포함된다.
  - `method="POST"` 필터가 있으면 결과의 모든 item 이 method=="POST".
  - `top_k=3` 지정 시 items 길이가 3 이하.
  - 검색 결과가 0건일 때 `count=0`, `items=[]` 이며 200 OK.
  - 점수는 `0.0 ≤ score ≤ 1.0` 범위로 정규화되어 내림차순 정렬.
  - 빈 query(공백만) 입력은 422.

### 기능 5: 엔드포인트 상세 조회
- 설명: `endpoint_id` 로 엔드포인트 한 건의 전체 메타(파라미터·요청 바디·응답·연결된 스키마 snippet) 를 반환한다.
- 입력: `endpoint_id: str`.
- 출력:
  ```
  {
    "endpoint_id": str,
    "document_id": str,
    "method": str, "path": str,
    "summary": str, "description": str,
    "tags": [str],
    "parameters": [ { name, in, required, schema, description } ],
    "request_body": { content_type, schema, required, example } | null,
    "responses": [ { status_code, content_type, schema, description, example } ],
    "referenced_schemas": [ { name, json_schema } ]
  }
  ```
- 검증 기준:
  - 존재하지 않는 `endpoint_id` 요청 시 HTTP 404 + `{"detail":"endpoint not found"}`.
  - 반환된 `referenced_schemas` 는 해당 엔드포인트가 실제로 `$ref` 한 스키마 **만** 포함(전체 컴포넌트 덤프 X).
  - `request_body` 가 명시 안 된 GET 엔드포인트는 `null`.
  - 응답 배열은 `status_code` 오름차순.

### 기능 6: 요청 예시 생성
- 설명: 저장된 정규화 스키마를 바탕으로 특정 엔드포인트의 요청 예시를 결정적으로 생성한다. 포맷: `curl`, `python`(requests), `fetch`.
- 입력: `endpoint_id: str`, `format: "curl"|"python"|"fetch"`.
- 출력: `{ "format": str, "code": str, "notes": str | null }`.
- 검증 기준:
  - `GET /pet/{petId}` + `curl` 호출 시 문자열 `curl -X GET` 및 `/pet/` 경로가 포함된다.
  - path 파라미터는 샘플 값(스키마에 `example` 이 있으면 그 값, 없으면 타입별 기본값: int→`1`, str→`"example"`) 으로 치환되어야 한다.
  - `request_body` 가 있으면 출력 코드에 JSON 본문이 포함되고, 없으면 본문 관련 플래그가 생성되지 않는다.
  - 지원하지 않는 format 요청은 422.
  - 존재하지 않는 `endpoint_id` 는 404.
  - 외부 호출 없이 순수 로컬 데이터만으로 동일 입력 → 동일 출력(결정성).

### 기능 7: 근거 기반 RAG 자연어 질의
- 설명: 자연어 질의를 받아 하이브리드 검색으로 컨텍스트를 모으고, `LLMProvider` 가 템플릿에 근거를 주입해 답변을 생성한다. 응답에는 반드시 인용 목록이 포함된다.
- 입력:
  ```
  {
    "question": str (필수, 1자 이상),
    "top_k": int (기본 5, 최대 10),
    "document_id": str | null,
    "method": str | null
  }
  ```
- 출력:
  ```
  {
    "question": str,
    "answer": str,
    "citations": [
      { "endpoint_id": str, "method": str, "path": str, "snippet": str }
    ],
    "used_documents": [ str ],
    "is_grounded": bool
  }
  ```
- 검증 기준 (환각 방지 규칙 포함):
  - 검색 결과가 0건이면 `answer` 은 고정 메시지 `"해당 API 를 찾을 수 없습니다."` 를 포함하고, `citations=[]`, `is_grounded=false`.
  - 검색 결과가 1건 이상이면 `citations` 길이 ≥ 1 이며, 각 citation 의 `endpoint_id` 는 실제 DB 에 존재한다(사후 조회로 검증 가능).
  - `answer` 내부에는 citation 에 포함된 `method path` 문자열이 최소 한 번 이상 등장해야 한다(근거 인용 의무).
  - 같은 질문을 두 번 보내면 동일 응답을 반환한다(결정성).
  - 빈 question 혹은 1자 미만이면 422.
  - `document_id` 필터 지정 시 모든 citation 의 `document_id`(내부 검증 가능) 가 일치.

### 기능 8: 문서 재색인 / 동기화
- 설명: 이미 등록된 문서를 원문에서 다시 수집해 변경 여부를 비교하고, 변경 시 청크·임베딩을 교체한다. 해시가 동일하면 skip.
- 입력: `document_id: str`, 옵션 `force: bool=False`.
- 출력:
  ```
  {
    "document_id": str,
    "status": "reindexed" | "skipped" | "failed",
    "previous_hash": str,
    "new_hash": str,
    "endpoints_count": int,
    "chunks_count": int
  }
  ```
- 검증 기준:
  - 원문 내용이 바뀐 경우 `status="reindexed"`, 기존 청크는 모두 교체되고, `document_sync_history` 에 한 행 추가.
  - 내용이 동일하면 `status="skipped"` 이며 `api_chunk` 행 수는 변하지 않는다.
  - `force=true` 면 해시 동일해도 재색인한다.
  - 재색인 도중 저장 실패 시 이전 청크가 그대로 유지되어야 한다(트랜잭션 원자성).
  - 존재하지 않는 `document_id` 요청은 404.

### 기능 9: 등록된 문서 목록·개별 조회·삭제
- 설명: 시스템에 적재된 문서 카탈로그를 관리한다.
- 입력·출력:
  - `GET /documents` → `[ { document_id, title, version, source_url, endpoints_count, indexed_at } ]`
  - `GET /documents/{id}` → 위 + `sync_history[]` (최근 10건)
  - `DELETE /documents/{id}` → `{ "deleted": true, "document_id": str }`
- 검증 기준:
  - `DELETE` 는 연결된 endpoint/parameter/response/request_body/schema/chunk/sync_history 까지 cascade 로 제거되며, 이후 `GET /documents/{id}` 는 404.
  - 목록 응답은 `indexed_at` 내림차순.
  - 존재하지 않는 id 는 404.
  - 삭제된 문서가 포함되어 있던 벡터 인덱스는 해당 청크 벡터가 즉시 제거되어 후속 검색 결과에 나타나지 않는다.

### 기능 10: 키워드 전용 / 벡터 전용 검색 엔드포인트
- 설명: 디버깅·평가 용도로 키워드 점수만, 벡터 점수만 반환하는 경로를 제공한다(하이브리드 검증용).
- 입력: `GET /search?mode=keyword|vector&query=...&top_k=...`.
- 출력: 기능 4의 item 구조와 동일하되 `keyword_score` 또는 `vector_score` 만 채워지고 나머지는 0.
- 검증 기준:
  - `mode=keyword` 일 때 결과 정렬이 키워드 점수 내림차순.
  - `mode=vector` 일 때 결과 정렬이 벡터 점수 내림차순.
  - 지원 외 `mode` 값은 422.
  - `mode=vector`, 동일 질의 반복 호출 → 동일 순위(결정성).

### 기능 11: 헬스체크 / 준비 체크
- 설명: 프로세스와 저장소 상태를 외부에서 확인할 수 있도록 한다.
- 입력·출력:
  - `GET /health` → `{ "status": "ok" }` (프로세스 살아있음만 확인)
  - `GET /ready` → `{ "status": "ok"|"degraded", "db": bool, "vector_index": bool, "documents": int }`
- 검증 기준:
  - DB 세션이 열리지 않는 상황에서는 `/ready` 가 `degraded` 와 `db=false` 를 반환하되 500 을 내지 않는다.
  - `/health` 는 어떤 상황에서도 200.
  - 기동 직후 문서 0건이면 `/ready` 는 `ok`, `documents=0`.

### 기능 12: 구조화 로깅 & 에러 응답 포맷
- 설명: 모든 요청은 구조화 로그(JSON)를 남기고, 실패 응답은 일관된 에러 포맷을 따른다.
- 입력·출력:
  - 성공: 위 각 기능 정의.
  - 실패: `{ "error": { "type": str, "message": str, "trace_id": str } }` (HTTP 4xx/5xx)
- 검증 기준:
  - 422/404/409/500 모두 동일 JSON 스키마를 따른다(응답 형식 테스트).
  - 내부 스택트레이스가 응답 body 에 포함되지 않는다.
  - 요청마다 `trace_id` 가 로그 한 줄과 응답 에러에 일관되게 기록된다.
  - 로그 한 줄에는 `ts`, `level`, `logger`, `msg`, `trace_id`, `duration_ms` 필드가 들어간다(성공 완료 로그 기준).

### 기능 13: 저장소·임베딩·LLM 인터페이스 교체 가능성
- 설명: MVP 한정으로 SQLite + 인메모리 벡터 + 해시 임베딩 + 템플릿 LLM 을 쓰지만, 이후 pgvector/실 임베딩/실 LLM 으로 무중단 교체 가능한 어댑터 구조를 제공한다.
- 입력·출력: 인터페이스 계약만 정의. 런타임 계약은 아래.
  - `EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]`
  - `LLMProvider.generate(prompt: str, context: list[CitationCtx]) -> LLMAnswer`
  - `VectorIndex.upsert(chunk_id, vector) / search(query_vec, top_k, filter) / delete(chunk_id)`
  - `DocumentRepository / EndpointRepository / ChunkRepository / SyncHistoryRepository` 각각 CRUD 인터페이스.
- 검증 기준:
  - 서비스 계층 코드(`services/*`)는 **구체 구현이 아닌 인터페이스만** 참조한다(정적 import 검사로 `sqlite3`/`openai` 등의 이름이 services 안에 등장하지 않아야 함).
  - 테스트에서 페이크 `EmbeddingProvider`/`LLMProvider`/`OpenAPIFetcher` 를 주입해 전체 파이프라인을 오프라인으로 실행할 수 있다.
  - 기본 구현 교체 시(예: `HashEmbeddingProvider` → 다른 결정적 Provider) 서비스 계층 코드는 변경되지 않는다.
  - `VectorIndex` 의 `delete` 호출 후 동일 chunk_id 는 검색 결과에 다시 등장하지 않는다.
