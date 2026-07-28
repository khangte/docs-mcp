# 자체 점검 — OpenAPI 재구조화 (기능 1~4, 9 중 OpenAPI 범위)

> 범위: SPEC의 OpenAPI 경로만. Drive/Notion(기능 5~8)은 이번 작업 범위 밖이며
> 구현하지 않았다.

## SPEC 기능 체크

- [x] **기능 1: `search_endpoints` 재구조화 (후보 전용 + 키워드 우선/벡터 보조)**
  - 구현: `app/services/search/endpoint_candidate_search.py`
    (`EndpointCandidateSearch`, `CandidateSearchOptions`, `EndpointCandidate`)
  - MCP 도구: `app/mcp_server.py::search_endpoints` — `mode` 파라미터 제거
    (Phase 0 결정 5번), 반환은 `{"items": [...]}`
  - 테스트: `tests/unit/test_endpoint_candidate_search.py` (23건),
    `tests/unit/test_vector_fallback_availability.py` (3건),
    `tests/integration/test_mcp_server.py`
  - 검증 기준 대응:
    - 상세 필드 없음 → `test_returns_candidates_without_detail_fields`,
      `test_search_returns_candidate_items_only`
    - 키워드 1건 이상이면 임베딩 미호출 → `test_keyword_hit_does_not_call_embedding_provider`
      (페이크 프로바이더 호출 카운트 == 0),
      `test_keyword_hit_path_never_touches_embedding` (호출 시 즉시 실패하는 페이크)
    - "GET /pet/{petId}" 질의 임베딩 호출 0 → `test_exact_path_query_does_not_call_embedding_provider`
    - 0건일 때만 벡터 보조 + `match_type="vector"` →
      `test_vector_fallback_triggers_only_when_keyword_returns_zero` (카운트 == 1),
      `test_vector_fallback_results_are_marked_as_vector`
    - 키 없으면 에러 없이 생략 → `test_vector_fallback_skipped_when_disabled`,
      `test_vector_fallback_disabled_still_returns_keyword_results`

- [x] **기능 2: `get_endpoint_details` + `include_example`**
  - 구현: `app/services/endpoints/endpoint_details_service.py`
  - MCP 도구: `app/mcp_server.py::get_endpoint_details(endpoint_id, include_example=False)`
  - 테스트: `tests/unit/test_endpoint_details_service.py` (11건)
  - 검증 기준 대응:
    - 없는 ID → `EndpointNotFoundError` → 표준 에러 포맷 →
      `test_unknown_endpoint_raises_not_found`,
      `test_details_unknown_endpoint_returns_error_payload`
    - 스키마 미리 펼침 없음 → `test_schema_body_is_not_pre_expanded`,
      `test_details_exposes_schema_ref`
    - `include_example=False` 시 키 없음 + 생성 미호출 →
      `test_default_does_not_call_example_generation` (카운트 == 0),
      `test_details_default_has_no_example_code_key`

- [x] **기능 3: `resolve_ref` 신규 도구**
  - 구현: `app/services/schemas/schema_ref_resolver.py`
  - MCP 도구: `app/mcp_server.py::resolve_ref`
  - 테스트: `tests/unit/test_schema_ref_resolver.py` (29건)
  - 검증 기준 대응:
    - 없는 ref → 표준 에러 포맷 → `test_unknown_ref_raises_not_found`,
      `test_resolve_ref_unknown_returns_error_payload`
    - 중첩 `$ref` 비재귀 → `test_nested_ref_is_not_expanded_recursively`,
      `test_array_of_ref_shows_item_ref_name_only`
    - 결정성 → `test_repeated_resolution_is_deterministic`,
      `test_resolve_ref_is_deterministic`

- [x] **기능 4: `list_tags` 신규 도구**
  - 구현: `app/services/tags/tag_catalog_service.py`
  - MCP 도구: `app/mcp_server.py::list_tags`
  - 테스트: `tests/unit/test_tag_catalog_service.py` (8건)
  - 검증 기준 대응:
    - `document_id` 지정 시 해당 문서만 → `test_document_id_filter_returns_only_that_document_tags`
    - 태그 없는 문서는 빈 배열 → `test_document_without_tags_returns_empty_list`

- [x] **기능 9 중 OpenAPI 부분: `query_rag` 비활성화**
  - `@mcp.tool()` 등록만 제거하고 구현은 `app/mcp_server.py::query_rag`
    모듈 레벨 함수로 보존 + 미사용 사유 주석
  - `app/services/rag/llm_provider.py`, `app/services/rag/rag_service.py`
    상단에 미사용 사유 주석 추가 (삭제하지 않음)
  - 테스트: `test_query_rag_is_not_registered`, `test_query_rag_source_is_preserved`,
    `test_expected_tools_are_registered`, `test_search_endpoints_has_no_mode_parameter`
  - README "제공되는 도구" 표 갱신 완료

## Phase 0 결정 사항 준수

- **결정 5** (`mode` 제거): MCP 도구 시그니처에서 제거. `SearchService`/
  `SearchOptions`의 `mode` 필드는 그대로 보존했고, FastAPI `/search` 라우트도
  변경하지 않았다. 검증: `test_search_endpoints_has_no_mode_parameter`,
  기존 `tests/unit/test_search_service.py`·`tests/integration/test_api_search.py` 전부 통과.
- **결정 6** (임계값 없음): 점수 임계값 로직을 만들지 않았다. `EndpointCandidateSearch.search()`
  는 키워드 결과 리스트가 비었을 때(`if keyword_candidates: return ...`)만 벡터
  단계로 넘어간다.
- **결정 7** (`include_example`): 기본값 `False`. `example_code`는 생성된
  경우에만 응답 dict에 키를 추가한다(`_to_endpoint_details_payload`).

## 혼동 방지 확인 (SPEC 명시 요구사항)

- `GeminiEmbeddingProvider`(`app/services/indexer/embedding_provider.py`)는
  **손대지 않았다**. `search_endpoints` 벡터 보조가 계속 사용한다.
- `GeminiLLMProvider`/`TemplateLLMProvider`/`RAGService`(`app/services/rag/`)만
  미사용 주석 대상. 두 "Gemini"의 차이를 `llm_provider.py` 상단 주석에 명시했다.
- `app/api/dependencies.py`의 `rag_service`는 번들에서 **제거하지 않았다**.
  `grep` 결과 `app/api/routes/query.py:20`이 계속 사용 중임을 확인했다.

## 코드 자체 평가

- **금지 패턴 사용 여부**: 없음
  - 전역 변수 상태 관리 없음 (모든 상태는 `AppState`/`ServiceBundle` 주입)
  - 빈 `except` 없음 (모든 예외는 도메인 예외로 변환하거나 명시적 처리)
  - 하드코딩 없음 (`MIN_TOP_K`/`MAX_TOP_K`/`EXAMPLE_FORMAT`/
    `LOCAL_SCHEMA_REF_PREFIX` 등 상수화, Gemini 키는 설정 경유)
  - 100줄 이상 함수 없음 (최대 `_to_endpoint_details_payload` 약 50줄)
  - `print()` 없음 (`app/core/logging.get_logger` 사용)
- **타입 힌트 적용률**: 신규 코드 100% (`Any` 는 JSON Schema dict 값 등
  본질적으로 임의 타입인 곳에만 사용)
- **한국어 docstring**: 신규 함수/클래스 100%
- **테스트 케이스 수**: 신규·개편 96건 / 전체 211건 통과
- **파일 크기**: 신규 모듈 최대 191줄 (200~400줄 가이드 이내)

## 주요 설계 결정

1. **`SearchService`를 고치지 않고 `EndpointCandidateSearch`를 신설**했다.
   기존 하이브리드 가중합은 FastAPI `/search` 라우트와 `RAGService`가 계속
   쓰므로, 여기에 키워드 우선 로직을 끼워넣으면 두 계약이 충돌한다. 검색
   전략이 다르면 경로를 분리한다는 SPEC 원칙과도 맞는다.
2. **"Gemini 키 없음" 판별을 프로바이더 종류가 아니라 설정값으로 했다**
   (`is_vector_fallback_available()`). 키가 없으면 `HashEmbeddingProvider`로
   폴백되는데, 클래스 종류로 판별하면 폴백 구현이 바뀔 때마다 조건이 깨진다.
   플래그는 `AppState.vector_fallback_enabled`로 주입해 테스트에서 명시적으로
   제어 가능하게 했다.
3. **후보 검색 대상을 `chunk_type == "endpoint"`로 한정**했다. 기존
   `search_endpoints`는 섹션/스키마 청크까지 섞어 반환했지만, 새 계약은
   `method`/`path`가 필수인 엔드포인트 후보이므로 섹션을 섞으면 빈 문자열
   필드가 생긴다.
4. **`resolve_ref`의 중첩 처리는 `describe_type()` 한 곳에 몰았다.**
   `$ref`는 이름만, 배열은 `array<Item>`으로 한 단계만 표기해 재귀를
   구조적으로 불가능하게 만들었다(방문 집합 관리 불필요).
5. **`example_code`를 `EndpointDetailsResult.example_code: str | None`로 두고
   페이로드 변환 시점에 키 존재 여부를 결정**했다. 서비스 계층은 dict 조립을
   모르게 하고, "키가 아예 없어야 한다"는 도구 계약은 변환 함수가 책임진다.

## 알려진 제약

- `security`(인증 요구사항) 필드는 상세 응답에 포함하지 않았다. 현재 파서·ORM
  어디에도 저장되지 않아 노출하려면 스키마 마이그레이션이 필요하고, 이는 기능
  2의 "기존과 동일 + `schema_ref` 명시 노출" 범위를 넘어선다.
- `resolve_ref`에서 `document_id`를 생략하면 등록 문서를 색인 시각 내림차순으로
  훑어 첫 매칭 스키마를 쓴다. 서로 다른 문서에 동명 스키마가 있으면 최신 문서
  것이 선택된다(결정적이지만 문서 간 모호성은 남는다). `document_id`를 넘기면
  해소된다.
- 리포지토리에 이미 존재하던 lint 위반(E501 등, `app/models/openapi.py`
  `app/services/parser/openapi_parser.py` 등 6건)은 이번 변경 범위 밖이라
  수정하지 않았다.
