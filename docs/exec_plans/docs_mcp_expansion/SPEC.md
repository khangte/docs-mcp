# docs-mcp: 통합 확장 계획 (OpenAPI 재구조화 + Google Drive/Notion 검색 추가)

## 개요

docs-mcp는 현재 OpenAPI 문서를 사전에 Postgres(pgvector)에 등록·색인하고,
검색은 하이브리드(키워드+벡터)로, 답변은 서버 자체 LLM(Gemini)이 생성하는
"사전 등록형 RAG 서버"다. 이 문서는 두 개의 독립적이지만 같은 원칙을 공유하는
확장을 하나의 계획으로 통합한다.

1. **OpenAPI 파이프라인 재구조화**: 검색/상세조회/`$ref` 펼치기 역할을
   분리하고, 임베딩(AI API) 호출을 "키워드 실패 시 보조"로 격하한다.
2. **Google Drive/Notion 문서 검색 신규 추가**: 팀 프로젝트 문서(작성자가
   제각각인 협업 문서)를 실시간 조회 방식으로 검색 가능하게 한다.

두 확장을 관통하는 공통 원칙은 다음과 같다.

- **Generation은 항상 호출 LLM(Claude/ChatGPT)이 담당한다.** MCP 서버는
  OpenAPI든 Drive/Notion이든 "찾아주는 역할"까지만 하고, 검색 결과를 조합해
  자연어로 설명하는 일은 하지 않는다. 서버 내부 답변생성 도구(`query_rag`,
  Gemini/Template)는 MCP 도구 등록에서 제외한다. (RAG 구조 자체가 없어지는
  게 아니라, Generation 주체가 서버 내부 LLM에서 호출 LLM으로 옮겨간다.)
- **AI API(임베딩)는 주력이 아니라 보조다.** 키워드/구조적 매칭으로 충분히
  찾아지는 질의에는 임베딩 API를 호출하지 않는다. 실패했거나 신뢰도가
  낮을 때만 벡터 검색을 보조로 사용한다.
- **문서 성격에 따라 검색 전략을 분리한다.** 정형·안정적 스펙 문서(OpenAPI)와
  자유형식·수시 변경 협업 문서(Drive/Notion)는 서로 다른 전략이 맞으므로,
  하나의 파이프라인으로 억지 통합하지 않고 독립된 경로로 병존시킨다.

| | OpenAPI | Drive/Notion |
|---|---|---|
| 문서 성격 | 정형 스펙, URL 고정, 변경 드묾 | 자유형식 협업 문서, 수시로 변경 |
| 적합한 전략 | 사전 색인 + 키워드 우선/벡터 보조 검색 | 실시간 조회 (최신성 우선) |
| 이번 작업 | 기존 도구 재구조화(이름 유지, 내부 로직 개선) | 신규 구축 |

## 현재 구조의 문제 (이번 통합 확장이 해결하는 것)

1. **검색과 상세조회가 분리되지 않음**: `search_endpoints`가 검색과 동시에
   snippet까지 만들어 반환한다. 후보 압축(가볍고 빠름)과 상세 조회(무겁지만
   필요한 것만)가 한 단계에 뭉쳐 있다.
2. **`$ref` 스키마를 펼쳐보는 독립 기능이 없음**: 파싱 시점에 `schema_ref`
   문자열로만 저장되고, `get_endpoint_details` 내부에서만 처리되어 LLM이
   필요한 깊이만큼 온디맨드로 파고들 수 없다.
3. **AI API(임베딩/Gemini)가 항상 주력 경로임**: 키워드+벡터 점수를 요청마다
   항상 가중합(hybrid)한다. 정확한 path/operationId를 아는 질의에도 매번
   임베딩 API를 호출해 불필요한 외부 호출 비용이 든다.
4. **서버가 자체 LLM으로 답변까지 생성함**: `query_rag`가 검색 결과를 Gemini/
   Template에 넘겨 답변 문장을 서버 안에서 만든다. Claude/ChatGPT를 통해
   쓰는 구조에서는 이중 생성이며, 정작 호출 LLM이 더 잘할 수 있는 일을
   서버가 대신하고 있다.
5. **팀 협업 문서(Drive/Notion)를 검색할 방법 자체가 없음**: OpenAPI 전용
   구조라 자유형식 문서는 등록·검색 대상이 아니다.

## 데이터 흐름

### A. OpenAPI 경로 (재구조화, 도구 이름 유지)

```
[openapi.yaml/json]
        │
        ▼
(1) Parser            ─ paths/schemas/tags 추출, $ref 수집, 내부 모델(Endpoint/
        │               Schema/Tag/Ref)로 변환   [기존 openapi_parser.py 그대로]
        ▼
(2) Indexer            ─ operationId/path/summary/tag/schema 별 검색 인덱스 생성
        │               [기존 indexer_service.py를 목적별 인덱스로 세분화]
        ▼
[영속 저장(Postgres) + pgvector 검색 인덱스]

--------- 질의 경로 (LLM 도구 호출) ---------

[사용자] "상품추천 조회 기능이 뭐야?"
        │
        ▼
[Claude/ChatGPT] 의미 이해 후 MCP 도구 호출 시작
        │
        ▼
(3) Search             ─ search_endpoints(query) : 후보만 가볍게 반환
        │                 1차: 키워드/구조적 매칭(operationId/path/tag)
        │                 실패(0건) 또는 신뢰도 낮음 → 보조로 벡터 검색 시도
        │                 (Gemini 키 없으면 보조 단계 자체를 건너뜀)
        ▼
[{endpoint_id, method, path, summary, match_type}, ...]  ← 상세 정보 없음
        │
        ▼
[Claude/ChatGPT] 후보 중 필요한 것 선택
        │
        ▼
(4) Retriever          ─ get_endpoint_details(endpoint_id) : 선택된 엔드포인트의
        │                 Method/Path/Summary/Parameters/RequestBody/
        │                 Responses/Security 반환. schema_ref는 참조
        │                 문자열 그대로(펼치지 않음)
        ▼
[Claude/ChatGPT] 필요하면 스키마 상세 요청
        │
        ▼
(5) Resolver           ─ resolve_ref(ref) : #/components/schemas/Product
        │                 → { name, fields: [{name, type, ...}] } 로 펼침 (신규)
        ▼
[Claude/ChatGPT] 모은 정보를 조합해 최종 자연어 답변 생성 → 사용자에게 응답
```

### B. Drive/Notion 경로 (신규)

```
[사용자] "우리 문서 중 로그인 관련 문서 찾아줘"
        │
        ▼
[Claude/ChatGPT] MCP 도구 search_documents(query) 호출  ← OpenAPI 도구와 별개
        │
        ▼
(1) 메타 캐시 조회       ─ document_meta 테이블에서 title/파일명/source 로
        │                  가벼운 키워드 매칭 → 후보 N개 (본문 없음, 빠름)
        ▼
(2) 후보 본문 실시간 fetch ─ GoogleDriveSource.fetch(file_id) 또는
        │                  NotionSource.fetch(page_id) 로 원문 텍스트 획득
        ▼
(3) 본문 매칭/스니펫 추출  ─ 후보 본문에서 쿼리 관련 구간을 잘라 스니펫 생성,
        │                  캐시 매칭 점수와 합쳐 재정렬
        ▼
[{title, source, url, snippet, score}, ...] 반환
        │
        ▼
[Claude/ChatGPT] 결과를 근거로 자연어 답변 생성 → 사용자에게 응답

--------- 캐시 갱신 경로 (별도, 검색과 분리) ---------

[refresh_index MCP 도구 또는 주기 실행]
        │
        ▼
(A) GoogleDriveSource.list_files() ─ Drive API, 지정 폴더 1개 한정(재귀 포함)
(B) NotionSource.list_pages()      ─ Notion search API, 워크스페이스/DB 한정
        │
        ▼
(C) document_meta upsert  ─ external_id 로 매칭, modified_at 다르면 갱신
        │                   (본문은 가져오지 않음 — 목록/메타만)
        ▼
[document_meta 최신화 완료]
```

### MCP 도구 전체 계약 (통합 후 최종 형태)

| 도구 | 경로 | 역할 | 상태 |
|---|---|---|---|
| `register_document` | OpenAPI | 문서 등록·파싱·색인 | 유지(변경 없음) |
| `search_endpoints` | OpenAPI | 후보 검색(키워드 우선+벡터 보조) | 재구조화 — snippet 제거, `match_type` 추가, 내부 폴백 로직 변경. **도구 이름은 유지** |
| `get_endpoint_details` | OpenAPI | 엔드포인트 상세 조회 | 유지 + `example_code` 생성 책임 분리 검토(Phase 0) |
| `resolve_ref` | OpenAPI | `$ref` 스키마 펼치기 | 신규 |
| `list_tags` | OpenAPI | 태그 목록 조회(탐색 보조) | 신규 |
| `list_documents` | OpenAPI | 등록 문서 목록 | 유지(변경 없음) |
| `document://{document_id}/raw` | OpenAPI | 원문 리소스 | 유지(변경 없음) |
| `search_documents` | Drive/Notion | 문서 검색(2단계 후보 압축) | 신규 |
| `get_document` | Drive/Notion | 원문 조회 | 신규 |
| `refresh_index` | Drive/Notion | 메타 캐시 갱신 | 신규 |
| ~~`query_rag`~~ | 공통 | 서버 내부 답변생성 | **비활성화**(코드 보존 + 미사용 주석, MCP 도구 등록만 제거) |

### 핵심 데이터 스키마

| 엔터티 | 주요 컬럼 | 비고 |
|--------|-----------|------|
| (기존) `ApiDocument/ApiEndpoint/ApiParameter/ApiRequestBody/ApiResponse/ApiSchema/ApiChunk/DocumentSyncHistory` | 변경 없음 | OpenAPI 경로가 계속 사용 |
| `document_meta` (신규) | id(pk), source(`drive`\|`notion`), external_id, title, url, modified_at, last_synced_at | Drive/Notion 메타 캐시 전용. UNIQUE(source, external_id). 기존 Postgres 인스턴스 재사용, 신규 인프라 도입 없음 |

`document_meta`는 본문을 저장하지 않는다(메타데이터만). 본문은 항상 Drive/
Notion API에서 실시간으로 가져온다.

### 제약·불변식 (공통)

- 검색 도구(`search_endpoints`, `search_documents`)는 최종 자연어 답변을
  만들지 않는다. 항상 구조화된 후보/결과 리스트만 반환한다.
- 모든 도구는 `DomainError`/`IntegrationError` 발생 시 동일한
  `{"error": true, "code", "message"}` 포맷을 반환한다.
- 벡터 검색(임베딩 API 호출)은 키워드 검색 결과가 **0건일 때만** 트리거된다
  (Phase 0 결정 6번).
- `resolve_ref`는 동일 문서 내 `#/components/schemas/*` 참조만 해석하고,
  중첩 `$ref`는 재귀적으로 펼치지 않는다(무한 재귀 방지).
- Drive/Notion 검색 시 한 번의 `search_documents` 호출이 실시간 fetch하는
  문서 수는 `top_k`를 초과하지 않는다(API rate limit/응답 지연 방지).
- Drive/Notion API 인증 실패, rate limit 등은 `IntegrationError`로 통일.

## 비기능/제약

- Gemini API 키가 없는 환경에서도 `search_endpoints`가 키워드 매칭만으로
  동작해야 한다(현재처럼 hybrid 가중합으로 항상 벡터를 섞지 않는다).
- Google Drive 인증: 서비스 계정(1개) 고정. 검색 대상 폴더를 서비스 계정
  이메일에 "뷰어로 공유"해두는 방식 — 팀원 개별 OAuth 로그인 불필요.
- Drive 검색 범위: 설정값(`DOCS_MCP_DRIVE_FOLDER_ID`)으로 지정한 폴더 1개로
  고정(하위 폴더 포함 재귀 탐색이 기본).
- Notion 인증: Notion Integration Token 하나를 팀 공유로 사용.
- `document_meta` 저장소는 기존 Postgres 인스턴스를 재사용한다.
- Drive/Notion 서비스 계층은 `DocumentSource`(Protocol) 인터페이스만
  참조하고 구체 SDK를 직접 import하지 않는다(기존 `OpenAPIFetcher` 패턴과
  동일 원칙 — 어댑터 교체·테스트 페이크 주입 가능해야 함).
- 서버 내부 답변생성 코드(`GeminiLLMProvider`, `TemplateLLMProvider`,
  `RAGService`)는 삭제하지 않는다. `query_rag` 도구 제거로 사용처가
  없어지지만, 코드는 그대로 남기고 파일/클래스 상단에 미사용 사유 주석만
  추가한다(예: `# 미사용: query_rag 도구 제거로 호출부 없음. RAG 답변생성은
  호출 LLM(Claude/ChatGPT)이 담당.`).
- 반대로 `GeminiEmbeddingProvider`(OpenAPI 벡터검색용 임베딩)는
  `search_endpoints`가 계속 사용하므로 **그대로 유지**하고 미사용 주석
  대상이 아니다 — "Gemini"라는 이름 때문에 두 프로바이더를 혼동해 함께
  지우거나 함께 주석 처리하지 않도록 구현 단계에서 명확히 구분한다.

## 기능 목록

### 기능 1: OpenAPI 검색 — 후보 전용 검색 + 키워드 우선/벡터 보조 폴백
- 설명: `search_endpoints`가 자연어/키워드 질의로 후보만 가볍게 반환하도록
  재구조화한다. 벡터 검색(임베딩 API)은 키워드 검색이 불충분할 때만 보조로
  트리거한다.
- 입력: `{query: str, top_k: int(기본 5), document_id: str | null}`
  (`mode` 파라미터 제거 — Phase 0 결정 5번)
- 출력:
  ```
  {
    "items": [
      { "endpoint_id": str, "method": str, "path": str,
        "summary": str, "match_type": "keyword"|"vector" }
    ]
  }
  ```
  (기존 `snippet`, `score` 세부 필드는 제거하거나 최소화 — 상세는 기능 2에서)
- 검증 기준:
  - 반환 항목에 상세 필드(파라미터·응답 등)가 없다.
  - 키워드 검색 결과가 1건 이상이면 임베딩 API가 호출되지 않는다(호출 로그
    또는 페이크 프로바이더 호출 카운트로 검증 가능).
  - 키워드 검색 결과가 0건일 때만 벡터 검색이 보조로 시도되고, 결과 항목이
    `match_type="vector"`로 표시된다.
  - Gemini API 키가 없는 환경에서는 벡터 보조 단계가 에러 없이 자동
    생략되고 키워드 결과만 반환된다.
  - "GET /pet/{petId}"처럼 키워드로 명확히 찾아지는 질의는 임베딩 API 호출
    카운트가 0이다.

### 기능 2: OpenAPI 엔드포인트 상세 조회 (기존 유지 + 책임 정리)
- 설명: `get_endpoint_details`로 특정 엔드포인트의 전체 상세를 조회한다.
  `schema_ref`는 펼치지 않고 참조 문자열 그대로 반환한다(펼치기는 기능 3).
- 입력: `{endpoint_id: str, include_example: bool(기본 False)}`
  (Phase 0 결정 7번)
- 출력: 기존과 동일 + `schema_ref` 필드 명시적 노출(참조 문자열 그대로).
  `include_example=True`일 때만 `example_code` 키가 포함된다.
- **범위 외 — `security`(인증 요구사항)**: 위 데이터 흐름 다이어그램(A 경로
  4단계)에는 "Responses/Security 반환"이라 적혀 있으나, 현재 파서
  (`openapi_parser.py`)가 `security`를 추출하지 않고 ORM(`ApiEndpoint`)에도
  컬럼이 없다. 지원하려면 파서 확장 + 모델 컬럼 추가 + Alembic 마이그레이션이
  필요하므로 **이번 기능 2의 범위에서 제외하고 별도 후속 태스크로 분리한다.**
  아래 검증 기준에도 포함하지 않는다.
- 검증 기준:
  - 존재하지 않는 `endpoint_id`는 `EndpointNotFoundError` → 표준 에러 포맷.
  - 응답에 스키마 본문이 미리 펼쳐져 들어가지 않는다.
  - `include_example=False`(기본) 응답에는 `example_code` 키가 없고, 예시
    생성 로직이 호출되지 않는다.

### 기능 3: `$ref` 스키마 펼치기 (신규)
- 설명: `#/components/schemas/*` 참조를 실제 필드 목록으로 펼치는 독립 도구
  `resolve_ref`를 추가한다.
- 입력: `{ref: str}` (예: `#/components/schemas/Product`)
- 출력: `{ "name": str, "fields": [{ "name": str, "type": str, "required": bool, "description": str }] }`
- 검증 기준:
  - 존재하지 않는 `ref`는 표준 에러 포맷.
  - 중첩 `$ref`는 재귀적으로 펼치지 않고 참조 이름만 `type`에 표기.
  - 동일 `ref` 반복 호출 시 동일 결과(결정성).

### 기능 4: OpenAPI 태그 목록 조회 (신규, 탐색 보조)
- 설명: 등록된 문서의 태그 목록을 반환해 LLM이 검색 범위를 좁히는 데 쓴다.
- 입력: `{document_id: str | null}`
- 출력: `{ "tags": [{ "name": str, "endpoint_count": int }] }`
- 검증 기준:
  - `document_id` 지정 시 해당 문서의 태그만 반환.
  - 태그가 없는 문서는 빈 배열.

### 기능 5: Drive/Notion 소스 어댑터 (신규)
- 설명: Google Drive, Notion 각각에 대해 "파일 목록 조회(메타만)"와 "특정
  문서 본문 조회"를 수행하는 어댑터. 공통 `DocumentSource` Protocol 구현.
- 입력: Google 서비스 계정 키 / Notion Integration Token, 검색 대상 범위
  (Drive 폴더 ID 1개 고정, Notion 워크스페이스/DB ID). Drive는 서비스 계정
  이메일을 대상 폴더에 "뷰어로 공유"하는 방식으로 접근 권한을 부여한다.
- 출력:
  - `list_files() -> list[FileMeta]` where `FileMeta = {external_id, title, url, modified_at}`
  - `fetch(external_id) -> str` (Google Docs는 export API로 텍스트 변환,
    Notion은 블록 트리를 평문으로 변환)
- 검증 기준:
  - 지정한 `DOCS_MCP_DRIVE_FOLDER_ID` 폴더(및 하위 폴더) 안의 파일만
    `list_files()`에 나타난다.
  - 서비스 계정에 공유되지 않은 폴더/파일은 Drive API 응답에 나타나지
    않는다(권한 경계는 Google 측 보장, 서버가 별도 필터링 불필요).
  - Notion은 지정한 워크스페이스/DB 하위만 나타난다.
  - `fetch()`가 존재하지 않는 `external_id`를 받으면 `IntegrationError`.
  - 인증 실패는 스택트레이스 없이 `IntegrationError`로 변환.
  - 동일 `external_id` 반복 fetch 시 (문서가 안 바뀌었다면) 동일 텍스트
    반환(결정성).

### 기능 6: Drive/Notion 메타데이터 캐시 및 갱신 (신규)
- 설명: Drive/Notion 파일 목록(제목·수정일)만 주기적으로 `document_meta`에
  upsert한다. 본문은 저장하지 않는다.
- 입력: 없음(내부적으로 기능 5의 `list_files()` 호출) 또는 MCP 도구
  `refresh_index()` 수동 트리거.
- 출력: `{synced: int, added: int, updated: int, removed: int}`.
- 검증 기준:
  - 신규 파일은 `added`로 집계되고 `document_meta`에 신규 행이 생긴다.
  - 삭제된 파일은 `document_meta`에서 제거되고 `removed`로 집계된다.
  - `modified_at`이 이전과 같으면 `updated`에 포함되지 않는다.
  - 갱신 중 예외가 나도 이미 처리된 행은 커밋되어 있고, 실패한 항목만
    다음 갱신에서 재시도 가능하다(부분 실패 허용).

### 기능 7: Drive/Notion 문서 검색 (신규, 2단계 후보 압축)
- 설명: 자연어 쿼리로 관련 문서를 찾는다. 1단계로 캐시에서 제목 기반 후보를
  추리고, 2단계로 후보 본문만 실시간 fetch해 스니펫과 점수를 만든다.
- 입력: `{query: str, top_k: int(기본 5), source: "drive"|"notion"|null}`.
- 출력:
  ```
  {
    "items": [
      { "title": str, "source": "drive"|"notion", "url": str,
        "snippet": str, "score": float }
    ]
  }
  ```
- 검증 기준:
  - 제목에 쿼리 단어가 포함된 문서가 캐시 1단계 후보에 반드시 포함된다.
  - 1단계 후보 수가 0이면 본문 fetch 없이 빈 리스트를 즉시 반환한다.
  - 한 번의 검색 호출에서 실시간 fetch하는 문서 수는 `top_k`를 초과하지
    않는다.
  - `source` 필터 지정 시 결과의 모든 항목이 해당 source만 포함한다.
  - 캐시에 없는 신규 문서는 검색되지 않을 수 있음(제약으로 문서화, `refresh_index`
    재실행 필요).

### 기능 8: Drive/Notion 문서 원문 조회 (신규)
- 설명: 검색 결과에서 특정 문서를 선택해 전체 본문을 조회하는 보조 도구.
- 입력: `{source: "drive"|"notion", external_id: str}`.
- 출력: `{title: str, source: str, url: str, content: str}`.
- 검증 기준:
  - 존재하지 않는 `external_id`는 `IntegrationError` 페이로드.
  - 반환된 `content`는 fetch 시점의 최신 원문(캐시된 본문 아님).

### 기능 9: MCP 도구 구성 통합 (query_rag 비활성화 + 신규 도구 등록)
- 설명: OpenAPI 신규/재구조화 도구와 Drive/Notion 신규 도구를 모두 등록하고,
  서버 내부 답변생성 도구(`query_rag`)만 MCP 등록에서 제외한다.
- 변경 내용:
  - 유지(이름 변경 없음): `register_document`, `search_endpoints`(내부
    로직만 재구조화), `get_endpoint_details`, `list_documents`,
    `document://{document_id}/raw`
  - 신규 추가(OpenAPI): `resolve_ref`, `list_tags`
  - 신규 추가(Drive/Notion): `search_documents`, `get_document`,
    `refresh_index`
  - 비활성화(코드 보존 + 미사용 주석, `@mcp.tool()` 등록만 제거): `query_rag`
- 검증 기준:
  - MCP 서버 기동 후 기존 5개 도구 이름이 변경 없이 유지된다
    (`register_document`, `search_endpoints`, `get_endpoint_details`,
    `list_documents`, 단 `query_rag`는 제외).
  - `query_rag`는 MCP 도구 목록에는 나타나지 않되, 소스 코드(`RAGService`,
    `LLMProvider` 등)는 그대로 남아 있고 미사용 사유 주석이 붙어 있다.
  - 신규 5개 도구(`resolve_ref`, `list_tags`, `search_documents`,
    `get_document`, `refresh_index`)가 추가된다.
  - 모든 도구는 `DomainError`/`IntegrationError` 발생 시 동일한 표준 에러
    포맷을 반환한다.
  - README의 "제공되는 도구" 표가 최종 도구 목록 기준으로 갱신된다.

## Phase 0 결정 사항

1. **Drive 인증 방식**: ✅ 확정 — 서비스 계정 고정, 대상 폴더를 서비스 계정에
   "공유"하는 방식. 팀원 개별 OAuth 불필요.
2. **검색 범위 한정**: ✅ 확정 — Drive는 설정된 폴더 1개
   (`DOCS_MCP_DRIVE_FOLDER_ID`)로 한정. Notion은 워크스페이스/DB ID로
   한정(값은 구현 단계에서 확정).
3. **Gemini 코드 처리 범위**: ✅ 확정 — 삭제하지 않는다. `GeminiLLMProvider`
   (답변생성용, `services/rag/llm_provider.py`)는 사용처가 없어지지만 코드는
   보존하고 미사용 사유 주석만 추가한다. `GeminiEmbeddingProvider`(OpenAPI
   벡터검색용 임베딩, `services/indexer/embedding_provider.py`)는
   `search_endpoints`가 계속 사용하므로 그대로 유지되고 미사용 주석 대상이
   아니다 — 이 둘을 혼동하지 않도록 구현 단계에서 명확히 구분한다.
4. **`services/rag/` 디렉터리 삭제 여부**: ✅ 확정 — 삭제하지 않는다.
   `RAGService`, `LLMProvider`, `GeminiLLMProvider`, `TemplateLLMProvider`
   전부 그대로 남기고, 각 파일 상단에 미사용 주석만 추가한다.
5. **`mode=hybrid|keyword|vector` 파라미터 유지 여부**: ✅ 확정 — MCP 도구
   `search_endpoints`의 시그니처에서 `mode` 파라미터를 **제거**한다. 항상
   "키워드 우선 + 벡터 보조" 단일 동작. `SearchService`/`SearchOptions`의
   `mode` 필드는 내부 구현·테스트·디버깅용으로 그대로 남긴다(도구 계약에서만
   빠짐). 잘못된 mode를 LLM이 고를 여지를 없애는 것이 목적.
6. **"신뢰도 낮음" 임계값의 구체적 수치**: ✅ 확정 — 임계값을 두지 않는다.
   **키워드 검색 결과가 0건일 때만** 벡터 보조를 트리거한다. 점수 스케일
   튜닝 의존성을 없애고 임베딩 호출을 최소화하기 위함. (SPEC 본문의 "최상위
   점수가 임계값 미만" 조건은 이 결정으로 대체됨.)
7. **`example_code` 생성 책임 분리 방식**: ✅ 확정 — 별도 도구를 만들지 않고
   `get_endpoint_details(endpoint_id, include_example: bool = False)` 옵션
   파라미터로 남긴다. 기본값 `False`로 상세 조회를 가볍게 하고, 필요할 때만
   curl 예시를 생성한다. `include_example=False`면 응답에 `example_code`
   키가 없다.

## 이번 문서 작업 범위

본 SPEC은 계획 문서만 정리한 것이며, 코드는 변경하지 않는다. 구현은 Phase 0
잔여 결정 사항(5~7번) 확정 후 CLAUDE.md 워크플로우(Generator → Evaluator)를
거쳐 별도로 진행한다. OpenAPI 재구조화와 Drive/Notion 신규 추가는 서로
의존성이 없으므로 별개 작업 단위로 순서 없이 진행 가능하다.
