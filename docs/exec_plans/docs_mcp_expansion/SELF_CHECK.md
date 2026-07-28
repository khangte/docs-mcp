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

## QA 피드백 반영 (QA_REPORT.md, 조건부 합격 6.9/10)

지적 6건을 모두 반영했다. 방향 판단은 "현재 방향 유지"였으므로 아키텍처는
그대로 두고 지적된 결함만 고쳤다.

1. **[치명] 공허 참 테스트 제거** — `test_vector_fallback_results_are_marked_as_vector`가
   빈 리스트에 `all()`을 걸어 통과하던 문제. 근본 원인은 `HashEmbeddingProvider`의
   코사인 유사도가 서로 다른 텍스트에 대해 정확히 `0.0`이라(직접 재현 확인)
   `score > 0.0` 필터가 후보를 전량 제거한 것이다.
   `tests/fixtures/fakes.py`에 `StubVectorSearch`(양수 점수 고정)를 추가하고
   테스트 3건으로 교체했다: `test_vector_fallback_actually_produces_candidates`
   (`assert candidates`를 **먼저** 단언), `test_vector_fallback_respects_top_k`,
   `test_zero_score_vector_hits_are_discarded`(현행 필터를 의도된 사양으로 고정).
   **검증**: `_search_by_vector`의 `"vector"`를 `"keyword"`로 바꾸는 회귀를
   일부러 주입해 새 테스트 2건이 실패함을 확인했다(구 테스트는 통과했을 것).
   같은 패턴을 전수 점검해 `test_missing_endpoint_row_is_skipped`에도
   `assert candidates` 가드를 추가했다.
2. **[중대] `_endpoint_chunks()` SQL 필터화** — `ChunkRepository.list_endpoint_chunks()`를
   신설해 `WHERE chunk_type = 'endpoint'`를 SQL 로 내렸다. 기존
   `list_by_endpoint_filter`의 시그니처·동작은 `SearchService`/FastAPI 라우트가
   쓰므로 건드리지 않았다. docstring 과 실제 동작의 불일치도 함께 해소됐다.
3. **[중대] `document_id` 에러 계약 통일** — 세 도구 모두 미등록 `document_id`에
   대해 `DocumentNotFoundError`(`code="document_not_found"`)를 낸다.
   `EndpointCandidateSearch._validate()`와 `SchemaRefResolver._find_schema()`에
   문서 존재 검증을 추가했다. `resolve_ref`는 이제 "문서 없음"과 "문서는 있으나
   스키마 없음"이 서로 다른 코드로 구분된다. 세 도구를 한 번에 검증하는
   파라미터화 통합 테스트를 추가했다.
4. **`resolve_ref` 모호성 해소** — `ResolvedSchema`/`ResolvedSchemaResult`에
   `document_id`를 추가해 어느 문서의 스키마인지 밝힌다. 아울러
   `DocumentRepository.list_all()`의 정렬에 2차 키(`ApiDocument.id`)를 추가해
   `indexed_at` 동률 시에도 완전 결정성을 확보했다.
5. **`_run_bundle` 제네릭 타입 힌트** — `TypeVar("_T")` + `Callable[[ServiceBundle], _T]`로
   반환 타입이 전파되게 했다. 내 담당 도구의 `_inner(bundle: ServiceBundle) -> ...`도
   모두 명시했다.
6. **문서 정정** — lint 위반 6건 → **12건**으로 정정, `security` 미지원을
   SPEC 기능 2에 "범위 외"로 명기했다.

## 알려진 제약

- **`security`(인증 요구사항) 필드는 이번 범위 외다.** 현재 파서(`openapi_parser.py`)가
  `security`를 추출하지 않고 ORM(`ApiEndpoint`)에도 컬럼이 없어, 노출하려면
  파서 확장 + 모델 컬럼 추가 + Alembic 마이그레이션이 필요하다. 이는 기능 2의
  출력 정의("기존과 동일 + `schema_ref` 명시 노출")와 검증 기준 3개를 모두
  넘어선다.
  **SPEC 불일치 주의**: SPEC 데이터 흐름 다이어그램(A 경로 4단계)에는
  "Responses/Security 반환"이라 적혀 있으나 기능 2의 출력 정의·검증 기준에는
  `security`가 없다. 이 불일치는 SPEC 기능 2에 "범위 외" 주석으로 명기했으며,
  실제 지원은 **별도 후속 태스크**로 분리한다.
- `resolve_ref`에서 `document_id`를 생략하면 등록 문서를 색인 시각 내림차순
  (동률 시 `id` 오름차순)으로 훑어 첫 매칭 스키마를 쓴다. 서로 다른 문서에
  동명 스키마가 있으면 가장 최근 등록 문서 것이 선택된다. 이제 응답에
  `document_id`가 포함되므로 호출 LLM 이 어느 문서의 스키마를 받았는지 확인할
  수 있고, `document_id`를 넘기면 모호성 자체가 사라진다.
- **임베딩 컬럼 지연 로딩(`load_only`/`defer`)은 도입하지 않았다.** `chunk_type`
  SQL 필터로 전송량 문제의 주된 원인(불필요한 section/schema 청크)을 이미
  제거했고, 남은 endpoint 청크의 임베딩은 벡터 보조 경로에서 실제로 쓰일 수
  있다. 측정 없이 컬럼 지연 로딩까지 넣는 것은 과설계라고 판단했다. 대규모
  문서에서 병목이 실측되면 그때 도입한다.
- 리포지토리에 이미 존재하던 lint 위반은 이번 변경 범위 밖이라 수정하지 않았다.
  실측 **12건**(E501 5건 / I001 4건 / F401 2건 + `main.py` F401 1건):
  `app/main.py`, `app/models/openapi.py`, `app/services/examples/`,
  `app/services/indexer/`, `app/services/ingestor/`, `app/services/parser/`,
  `app/services/search/keyword_search.py`, `app/services/search/search_service.py`.
  변경 전 베이스라인은 15건이었고 내 변경이 3건을 줄였다(신규 위반 0건).

---

# 자체 점검 — Drive/Notion 문서 검색 (SPEC 기능 5~8 + 기능 9 도구 등록)

> 위 섹션(OpenAPI 재구조화, 기능 1~4)과는 별개 작업 단위다. 기능 1~4 는 커밋
> `5870f90` 에서 이미 완료됐고, 이 섹션은 그 위에 Drive/Notion 경로를 추가한
> 작업을 기록한다. OpenAPI 관련 코드는 건드리지 않았다.

## SPEC 기능 체크

- [x] **기능 5: Drive/Notion 소스 어댑터**
  - 인터페이스: `app/services/documents/document_source.py`
    (`DocumentSource` Protocol + `FileMeta` DTO)
  - 구현: `app/services/documents/google_drive_source.py`,
    `app/services/documents/notion_source.py`
  - 구성 팩토리: `app/services/documents/source_factory.py`
  - 공통 시각 파싱: `app/services/documents/time_parsing.py`
  - 테스트: `tests/unit/test_document_sources.py` (39건),
    `tests/unit/test_document_source_factory.py` (9건)
- [x] **기능 6: 메타데이터 캐시 및 갱신**
  - 모델: `app/models/document_meta.py` (`UNIQUE(source, external_id)`, 본문 미저장)
  - 저장소: `app/repositories/document_meta_repository.py`
  - 서비스: `app/services/documents/document_index_service.py`
  - 마이그레이션: `alembic/versions/059294da406f_add_document_meta_cache_for_drive_notion.py`
    (down_revision `b336d80334c8`)
  - 테스트: `tests/unit/test_document_index_service.py` (17건),
    `tests/unit/test_document_meta_repository.py` (9건)
- [x] **기능 7: 2단계 후보 압축 문서 검색**
  - 구현: `app/services/documents/document_search_service.py`
    (`DocumentSearchService.search`)
  - 테스트: `tests/unit/test_document_search_service.py` (28건)
- [x] **기능 8: 문서 원문 조회**
  - 구현: `DocumentSearchService.get_document` (같은 파일)
  - 테스트: 위 파일 내 `get_document` 섹션 6건
- [x] **기능 9(Drive/Notion 범위): MCP 도구 3개 등록**
  - `search_documents` / `get_document` / `refresh_index` (`app/mcp_server.py`)
  - 반환 TypedDict: `app/mcp_types.py`
    (`DocumentSearchResponse`, `DocumentContentPayload`, `RefreshIndexResult`)
  - 테스트: `tests/integration/test_mcp_documents.py` (21건)

## SPEC 검증 기준 → 테스트 대응

| SPEC 검증 기준 | 테스트 |
|---|---|
| 1단계 후보 0건이면 본문 fetch 없이 빈 리스트 | `test_returns_empty_without_fetch_when_no_candidate` (fetch 카운트 0), `test_no_candidate_never_touches_source` (폭발 페이크) |
| 한 번의 검색이 fetch 하는 수 ≤ `top_k` | `test_fetch_count_never_exceeds_top_k`, `test_search_documents_fetch_count_respects_top_k` |
| 제목에 쿼리 단어가 있으면 후보 포함 | `test_title_match_document_is_included` |
| `source` 필터 지정 시 해당 source 만 | `test_source_filter_restricts_results`, `test_search_documents_source_filter` |
| 삭제된 파일이 캐시에서 제거되고 `removed` 집계 | `test_deleted_file_is_removed_from_cache` |
| `modified_at` 동일하면 `updated` 미포함 | `test_unchanged_modified_at_is_not_counted_as_updated` |
| 부분 실패 시 처리된 행은 커밋됨 | `test_partial_failure_commits_already_processed_source`, `test_failed_source_is_retryable_on_next_refresh` |
| 갱신이 본문을 가져오지 않음 | `test_refresh_does_not_fetch_document_bodies` |
| 없는 `external_id` → `IntegrationError` | `test_get_document_unknown_id_raises_integration_error`, `test_get_document_unknown_id_returns_error_payload` |
| 인증 실패를 스택트레이스 없이 변환 | `test_drive_http_errors_become_integration_error`, `test_notion_http_errors_become_integration_error` |
| 반복 fetch 결정성 | `test_drive_fetch_is_deterministic`, `test_results_are_deterministic` |
| 표준 에러 포맷 | `test_mcp_documents.py` 의 `*_returns_error_payload` 6건 |

## 코드 자체 평가

- **금지 패턴 사용 여부**: 없음.
  - 전역 변수 상태 없음(어댑터는 `AppState.document_sources` 로 주입).
  - 빈 `except` 없음. 모든 예외는 로깅하거나 `IntegrationError` 로 변환한다.
  - 하드코딩 없음. API 베이스 URL·타임아웃·상한값은 모듈 상수 또는
    `DOCS_MCP_` 접두사 환경변수로 관리한다.
  - 최장 함수는 `GoogleDriveSource.list_files` 로 약 25줄이다. 100줄 초과 없음.
- **타입 힌트 적용률**: 신규 코드 100%. `uv run mypy app/` → `No issues found`.
  `Any` 는 외부 JSON 응답 dict 등 본질적으로 임의 타입인 경계에만 썼다.
- **테스트 케이스 수**: 신규 125건 (전체 336건 통과, 기존 211건 무회귀).
- **검증 방식**: 실제 자격증명이 없으므로 두 층으로 나눠 검증했다.
  - `DocumentSource` Protocol 을 구현한 페이크(`tests/fixtures/document_sources.py`)를
    주입해 검색·캐시·도구 배선 로직을 완전히 덮었다.
  - 어댑터 자체의 응답 파싱과 오류 변환은 `httpx.MockTransport` 로 검증했다.
    실제 네트워크로 나가는 테스트는 만들지 않았다.

## 주요 설계 결정

1. **`google-api-python-client` 를 쓰지 않고 httpx 로 REST 를 직접 호출했다.**
   기존 `HttpOpenAPIFetcher` 와 동일한 방식으로 통일하고 의존성 트리를
   가볍게 유지하기 위해서다. `google-auth` 만 추가했는데, 서비스 계정 JWT
   서명·토큰 갱신은 직접 구현하면 보안 위험이 크기 때문이다. 이미
   `google-genai` 의 전이 의존성으로 설치돼 있던 패키지라 실질적인 신규
   설치는 없고, 직접 import 하므로 `pyproject.toml` 에 명시만 했다.
2. **본문을 캐시하지 않는다.** `document_meta` 는 메타만 담는다. 협업 문서는
   수시로 바뀌므로 최신성이 정확도보다 중요하고, 본문 저장은 저장 비용과
   무효화 로직을 함께 불러온다. 대신 1단계 제목 매칭으로 후보를 압축해
   실시간 fetch 횟수를 `top_k` 로 묶었다.
3. **`top_k` 절단을 1단계(후보 선별) 시점에 수행했다.** 2단계에서 자르면
   이미 fetch 한 뒤 버리는 셈이라 "fetch 수 ≤ `top_k`" 불변식이 깨진다.
   `_select_candidates()` 가 `[: options.top_k]` 로 자른 뒤에야 fetch 가 시작된다.
4. **부분 실패 허용을 소스 단위 커밋 경계로 구현했다.** `_refresh_source()` 가
   소스 하나를 끝낼 때마다 `commit()` 하고, 실패하면 그 소스만 `rollback()` 한다.
   전체가 실패했을 때만 `IntegrationError` 를 올려 "조용한 무동작"을 막았다.
   개별 소스 실패는 `failed_sources` 로 보고해 호출 LLM 이 재시도를 판단할 수 있다.
5. **검색 중 개별 문서 fetch 실패는 그 문서만 건너뛴다.** 문서 한 건의 권한
   오류가 검색 전체를 죽이면 협업 환경에서 사용 불가능해진다.
6. **한글 토크나이저를 별도로 뒀다.** 기존 `keyword_search.tokenize` 는
   `[A-Za-z0-9_]+` 만 인식해 "로그인 설계서" 같은 한국어 제목을 전혀 자르지
   못한다. 협업 문서 제목은 대부분 한국어이므로 `document_search_service.tokenize`
   에서 한글 음절 범위를 함께 인식하게 했다. OpenAPI 쪽 토크나이저는 영문
   `operationId`/`path` 매칭용이라 그대로 두는 편이 맞다고 판단했다.
7. **자격증명이 없어도 서버가 기동된다.** `build_document_sources()` 가 구성
   가능한 소스만 담고, 없으면 빈 dict 를 돌려준다. Drive 만 쓰는 팀, Notion 만
   쓰는 팀 모두 지원하기 위해서다. 미구성 상태에서 도구를 호출하면
   `IntegrationError` 로 "미구성"임을 명확히 알린다.
8. **어댑터를 `AppState` 에, 서비스를 `ServiceBundle` 에 두었다.**
   어댑터는 프로세스 수명 동안 재사용(토큰 캐싱)하고, 서비스는 요청 스코프
   세션에 묶여야 하기 때문이다. `OpenAPIFetcher` 와 동일한 배치다.

## 알려진 제약

- **캐시에 없는 신규 문서는 검색되지 않는다.** SPEC 기능 7 에 명시된 제약이며,
  `refresh_index` 재실행으로 해소한다. README 와 도구 docstring 에 명시했고
  `test_search_documents_before_refresh_returns_empty` 로 계약을 고정했다.
- **1단계 매칭은 제목·URL 토큰 겹침만 본다.** 제목에 질의어가 전혀 없고 본문에만
  있는 문서는 후보에 들지 못한다. 이는 "본문 fetch 수를 `top_k` 로 제한한다"는
  SPEC 불변식과 맞바꾼 결과다. 본문 기반 1단계를 하려면 본문을 색인해야 하는데,
  그것은 "실시간 조회로 최신성 우선"이라는 설계 전제와 충돌한다.
- **Drive 폴더 재귀 탐색에 `MAX_FOLDERS = 500` 상한이 있다.** 초과 시 경고
  로그를 남기고 남은 하위 폴더를 건너뛴다. 무한 루프·API 폭주 방지가 목적이며,
  실제로 넘는 팀이 나오면 상수를 설정값으로 올리면 된다.
- **Notion 블록 순회에 깊이 4, 블록 2000 상한이 있다.** 같은 이유다. 상한을
  넘는 문서는 앞부분만 반환된다.
- **`refresh_index` 는 수동 트리거만 지원한다.** SPEC 이 "MCP 도구 또는 주기
  실행"이라 했으나 주기 실행 스케줄러는 이번 범위에서 제외했다. 서버 프로세스
  모델(MCP stdio) 상 스케줄러를 두려면 별도 워커가 필요하다.
- **실제 Drive/Notion API 를 상대로는 검증하지 못했다.** 자격증명이 없어
  `httpx.MockTransport` 로 응답 스키마를 흉내 냈다. 실환경 연동 시
  `webViewLink` 부재나 공유 드라이브 권한 등에서 조정이 필요할 수 있다.
- **리포지토리에 이미 존재하던 lint 위반 12건**(`app/models/openapi.py`,
  `app/services/parser/openapi_parser.py` 등 이번 범위 밖 파일)은 수정하지
  않았다. 이번에 추가한 파일의 위반은 0건이다.

---

## QA 지적 반영 (QA_REPORT_DRIVE_NOTION.md, 조건부 합격 6.9/10)

### 1. [필수] `_refresh_source()` 커밋 경계를 배치 단위로 낮춤 — SPEC 위반 해소

**지적**: 두 루프가 전부 끝난 뒤 맨 마지막에 한 번만 `commit()` 해서, 소스 내부
중간 실패 시 전량 롤백됐다. 프로브 실측 `committed_rows=0`. SPEC 기능 6 의
"갱신 중 예외가 나도 이미 처리된 행은 커밋되어 있고" 를 충족하지 못했다.

**인정한다.** 내 자기보고("source별 커밋 경계")는 source 가 2개 이상일 때만
성립했고, source 1개 환경과 소스 내부 중간 실패는 커버하지 못했다. 자기보고가
실제 보장 범위보다 넓게 읽히도록 쓰여 있었다.

**수정**: `BATCH_SIZE = 100` 상수를 두고 변경 건수가 그만큼 쌓일 때마다 커밋한다.
- `_commit_batch()` 가 **커밋에 성공한 뒤에야** 미커밋 집계를 확정 집계로 옮긴다.
  롤백된 배치는 집계에 절대 들어가지 않으므로 반환값과 DB 상태가 항상 일치한다.
- `_PartialRefreshError` 내부 예외로 "확정된 집계"를 `refresh()` 까지 실어 보내,
  실패한 소스라도 이미 커밋된 분이 집계에 반영된다.
- 모든 소스가 실패했어도 **커밋된 변경이 있으면** 예외 대신 정상 반환하고
  `failed_sources` 로 실패를 알린다. 커밋된 게 전혀 없을 때만 `IntegrationError`
  를 던져 "조용한 무동작"을 막는다.

**재현 검증**: 프로브 2와 동일 시나리오(source 1개, 205건 중 201번째에서 실패)
결과가 `committed_rows=0` → **`committed_rows=200, added=200`** 로 바뀌었다.
집계와 실제 커밋 행 수가 정확히 일치한다.

### 2. [필수] 소스 내부 중간 실패를 실제로 검증하는 테스트 추가

**지적**: 기존 `test_partial_failure_commits_already_processed_source` 는
`list_files()` **단계**에서 실패시켜, 그 시점엔 아무 행도 안 건드렸으므로
"source 간 격리"만 검증했다. 테스트는 통과하는데 SPEC 은 위반인 상태를 위장했다.

**인정한다.** 검증 대상을 잘못 잡은 테스트였다.

**수정**:
- 기존 테스트는 `test_source_level_isolation_when_list_files_fails` 로 이름을
  바꾸고, docstring 에 "이 시나리오는 소스 간 격리만 검증하며 내부 중간 실패는
  별도 테스트가 담당한다"를 명시했다.
- `_FailingFileList` 페이크를 추가했다. `len()`/인덱싱은 정상 리스트처럼 동작해
  서비스가 목록 조회 성공으로 판단하고 저장 루프에 진입하지만, 순회 중 N 번째
  항목에서 `IntegrationError` 가 터진다(페이지네이션 중 rate limit 재현).
- 신규 테스트 6건:
  - `test_mid_save_failure_keeps_already_committed_rows` — 실패 후 **DB 를 직접
    조회해** 행이 남아 있는지 단언
  - `test_mid_save_failure_with_single_source_still_commits` — source 1개
    사각지대
  - `test_mid_save_failure_counts_match_committed_rows` — 집계 == 실제 커밋 행 수
  - `test_mid_save_failure_is_reported_not_silently_succeeded` — 실패 보고
  - `test_failed_items_are_retried_on_next_refresh` — 재시도로 최종 전건 반영
  - `test_mid_save_failure_before_first_batch_commits_nothing` — 첫 배치 전 실패
    경계 조건

### 3. [필수] 미구성 소스에서 `search_documents` 침묵 해소

**지적**: `get_document`/`refresh_index` 는 명확한 `IntegrationError` 를 내는데
`search_documents` 만 빈 리스트를 조용히 반환해, "결과 없음"과 "서버 미설정"이
구별되지 않았다.

**수정**: `search()` 에 `_require_configured()` 를 추가해 소스가 하나도 없거나
지정한 source 가 미구성이면 `IntegrationError` 를 던진다. 메시지는
`document_source.py` 의 `NO_SOURCE_CONFIGURED_MESSAGE` 상수로 한 곳에 모아
세 도구가 **완전히 동일한 문구**를 쓰게 했다(`test_unconfigured_message_matches_refresh_index`,
`test_unconfigured_error_is_consistent_across_tools` 로 고정).

**과잉 교정 방지**: 구성은 됐는데 결과만 0건인 정상 케이스는 계속 빈 리스트다.
`_require_configured()` 는 **구성 여부만** 보고 캐시 내용은 보지 않는다.
`test_configured_but_empty_cache_still_returns_empty_list`,
`test_configured_with_no_matching_document_returns_empty_list`,
그리고 기존 `test_search_documents_before_refresh_returns_empty` 가 이를 지킨다.

### 4. [필수] `README.md` 정정

3번 수정으로 "세 도구 모두 미구성 시 `IntegrationError`" 가 사실이 됐다. 더해
**"소스 미설정"과 "검색 결과 0건"이 구별된다**는 점을 명시적으로 덧붙였다.
독자가 빈 `items` 를 보고 설정 문제로 오해하지 않도록 하기 위해서다.

### 5. [권장, 반영함] 1단계 후보 조회를 SQL 로 내림

**지적**: `_select_candidates()` 가 `list_all()` 로 `document_meta` 전량을
Python 에 적재한다(O(N)). 같은 브랜치의 `chunk_repository.list_endpoint_chunks()`
가 정확히 이 문제를 SQL 로 이미 고쳤는데 일관성이 없다.

**반영했다.** "과설계면 근거를 남기고 넘어가도 된다"는 선택지가 있었으나,
**이미 같은 브랜치에 확립된 원칙이 있는데 한쪽만 예외로 두는 것이 오히려
유지보수 부채**라고 판단했다. 후임자가 두 경로를 비교하며 "왜 여기만 다르지"를
매번 되묻게 된다.

`DocumentMetaRepository.search_by_tokens(tokens, source)` 를 추가해
`WHERE (title ILIKE ANY OR url ILIKE ANY)` 로 1차 필터를 DB 에 내렸다. 점수
계산과 최종 순위는 여전히 Python 이 담당한다(SQL 은 후보를 좁히기만 한다).

구현 중 발견한 함정 하나를 함께 막았다: 내 토크나이저는 `auth_v2` 처럼 `_` 를
토큰 문자로 취급하는데, `_` 는 LIKE 에서 "임의의 한 문자" 와일드카드다.
이스케이프하지 않으면 `auth_v2` 가 `authXv2` 까지 잘못 매칭한다.
`_escape_like()` 로 `\`, `%`, `_` 를 모두 이스케이프하고
`test_search_by_tokens_matches_underscore_literally`,
`test_search_by_tokens_escapes_like_wildcards` 로 고정했다.

### 6. [권장, 반영함] 교차 소스 삭제 회귀 테스트

Evaluator 프로브로 "버그 없음"이 확인됐지만 이를 고정하는 테스트가 없었다.
`test_refreshing_one_source_never_deletes_another_sources_rows` 를 추가했다.
notion 이 **이미 캐시된 상태에서** drive 만 갱신하며 drive 파일을 삭제하는
시나리오로, `list_by_source()` → `list_all()` 같은 실수로 다른 출처 데이터가
통째로 날아가는 회귀를 막는다.

### 미반영 (근거 명시)

- **[권장 7] `conftest.py` 테스트 종료 로그 소음**: `pg_engine` 은 OpenAPI
  트랙과 공유하는 픽스처다. 이번 범위(Drive/Notion)를 벗어나고, 테스트 결과에
  영향이 없는 로그 소음이라 손대지 않았다. 별도 정리 태스크가 적절하다.
- **[권장 8] 429 재시도 부재**: 아래 "알려진 제약"에 추가했다. SPEC 이 요구하지
  않았고, 백오프 정책은 실환경 rate limit 실측 없이 정하면 추측이 된다.

## 알려진 제약 (추가)

- **Drive/Notion 429 자동 재시도(백오프)가 없다.** 에러 메시지로 "retry later"
  를 안내할 뿐 서버가 스스로 재시도하지 않는다. `refresh_index` 는 배치 커밋
  덕분에 재실행하면 실패 지점부터 이어서 진행되므로 수동 재시도로 복구된다.
- **`BATCH_SIZE = 100` 은 실측이 아닌 기본값이다.** 커밋 횟수(내구성)와 트랜잭션
  오버헤드(속도)의 절충점으로 잡았다. 실환경 문서 규모가 확인되면 조정 여지가 있다.
- **`search_by_tokens` 의 `ILIKE` 는 인덱스를 타지 않는다.** 선행 와일드카드
  (`%token%`) 패턴이라 순차 스캔이다. 전량 적재보다는 명백히 낫지만(ORM 객체
  생성과 네트워크 전송이 사라진다), 캐시가 수만 건을 넘으면 `pg_trgm` GIN
  인덱스가 필요하다. 현재 협업 문서 규모(수천 건)에서는 과설계로 판단해 두지 않았다.
