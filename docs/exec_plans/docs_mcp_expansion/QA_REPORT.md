# QA 검수 보고서 — OpenAPI 재구조화 (커밋 `5870f90`)

> **[2026-08-05]** 이 문서에 등장하는 FastAPI 관련 내용은 코드베이스에서 제거되었습니다. 현재는 MCP 서버 단일 진입점 구조입니다.

- **검수 대상**: 검수 당시 `/home/kang/projects/docs-mcp-expansion` (브랜치
  `feat/docs-mcp-expansion`). 이후 해당 워크트리는 제거됐고 작업분은
  `refactor/260727`에 머지됐다.
- **커밋**: `5870f90` "feat: OpenAPI 도구 재구조화 - 후보 검색 분리 및 resolve_ref/list_tags 추가"
- **범위**: SPEC 기능 1~4, 기능 9의 OpenAPI 부분, Phase 0 결정 5·6·7번
- **범위 밖**: Drive/Notion(기능 5~8) — 별도 에이전트가 동시 구현 중
- **검수일**: 2026-07-28
- **검수 방식**: 읽기 전용. 제품 코드·테스트 코드를 일절 수정하지 않았고,
  동작 확인은 워크트리 밖(스크래치패드)에 임시 프로브 테스트를 만들어 실행했다.

---

**전체 판정**: 조건부 합격
**가중 점수**: 6.9 / 10.0

**항목별 점수**:

- 기능 정확성: 8/10 — SPEC 기능 1~4와 Phase 0 결정 5·6·7이 모두 정확히 구현됐다. 다만 벡터 보조 경로가 실환경에서 후보를 생성하는지 한 번도 실증되지 않았고, `document_id` 오류 처리가 도구마다 다르다.
- 코드 품질: 8/10 — 책임 분리·타입 힌트·한국어 docstring·상수화가 일관되고 mypy 무결점. 신규 파일에 lint 위반 0건. 감점 사유는 `_endpoint_chunks`의 저장소 계약 오용과 도구 간 에러 처리 불일치.
- 성능: 5/10 — `document_id` 미지정 검색이 매 호출마다 전체 청크를 메모리로 적재하고 Python에서 필터링한다. 저장소에 SQL 필터가 이미 있는데 쓰지 않는다.
- 테스트 커버리지: 5/10 — 96건 신규, 211건 전체 통과지만 **핵심 검증 기준 하나가 공허 참(vacuously true)으로 통과**한다. 벡터 보조 후보 생성은 실제로 한 번도 검증되지 않았다.

가중 계산: (8×0.4) + (8×0.3) + (5×0.15) + (5×0.15) = 3.2 + 2.4 + 0.75 + 0.75 = **6.9**

> 기능 정확성·테스트 커버리지 모두 4점 초과이므로 무조건 불합격 조건에는
> 해당하지 않는다. 7.0 미만이므로 **조건부 합격 — 피드백 반영 후 재검수**.

---

## SPEC 기능 체크

- **[PASS] 기능 1: `search_endpoints` 후보 전용 재구조화 (키워드 우선 / 벡터 보조)**
  - `app/services/search/endpoint_candidate_search.py`에 `EndpointCandidateSearch`
    신설. `search()`가 `if keyword_candidates: return keyword_candidates`로
    조기 반환하므로 키워드 1건 이상이면 `_search_by_vector()`에 진입 자체를 하지
    않는다. 임베딩 호출은 `VectorSearch.search()` 내부에서만 일어나므로
    "키워드 성공 시 임베딩 0회"가 **구조적으로** 보장된다.
  - 반환 DTO `EndpointCandidate`는 `endpoint_id/method/path/summary/match_type`
    5개 필드 frozen dataclass. `snippet`/`score` 없음. MCP 응답도
    `{"items": [...]}`로 동일.
  - `chunk_type == "endpoint"` 한정으로 섹션/스키마 청크 혼입을 막았다. 마크다운
    문서만 등록한 상태에서 검색 시 `[]` 반환을 프로브로 확인했다.

- **[PASS] 기능 2: `get_endpoint_details` + `include_example`**
  - `EndpointDetailsService.get_details()`가 삼항식으로
    `include_example`일 때만 `self._example_service.generate(...)`를 평가한다.
    False면 호출 자체가 없다(단락 평가로 보장).
  - `_to_endpoint_details_payload()`가 `result.example_code is not None`일
    때만 `payload["example_code"]`를 추가한다. 기본 호출 시 **키가 아예 없다**.
  - `schema_ref`가 `ParameterItem`/`RequestBodyItem`/`ResponseItem` 세 곳 모두에
    참조 문자열 원본으로 노출되고, 스키마 본문은 `_safe_load`로 파싱만 하지
    `$ref`를 펼치지 않는다.
  - 없는 `endpoint_id` → `EndpointNotFoundError` → `{"error": true,
    "code": "endpoint_not_found", ...}` 표준 포맷.

- **[PASS] 기능 3: `resolve_ref` 신규 도구**
  - `SchemaRefResolver.resolve()`. 중첩 `$ref`는 `describe_type()`이
    `_ref_display_name()`으로 **이름만** 반환하고 재귀 호출하지 않는다. 배열은
    `array<LineItem>`으로 한 단계만 표기. 방문 집합 없이 구조적으로 재귀가
    불가능하게 만든 설계가 깔끔하다.
  - Swagger 2.0 `#/definitions/` 프리픽스도 허용. 잘못된 형식은
    `ValidationError`, 없는 스키마는 `SchemaRefNotFoundError`
    (`code="schema_ref_not_found"`)로 분리했다.
  - 결정성: 프로브에서 반복 호출 시 동일 결과 확인.

- **[PASS] 기능 4: `list_tags` 신규 도구**
  - `TagCatalogService.list_tags()`가 `Counter`로 집계하고
    `(-count, name)` 키로 정렬해 결정적 순서를 보장한다.
  - `document_id` 미등록 시 `DocumentNotFoundError`를 명시적으로 던진다.
  - 태그 없는 문서는 빈 배열. 프로브에서 `[('pet', 3), ('user', 1)]` 확인.

- **[PASS] 기능 9(OpenAPI 범위): `query_rag` 비활성화**
  - `@mcp.tool()` 데코레이터만 제거되고 `query_rag`는 `app/mcp_server.py:388`에
    모듈 레벨 async 함수로 온전히 살아 있다. 미사용 사유 주석 부착.
  - `create_mcp_server()`에 등록된 도구는 `list_documents`,
    `register_document`, `search_endpoints`, `get_endpoint_details`,
    `resolve_ref`, `list_tags` 6개 + 리소스 1개. `query_rag` 없음.
  - README "제공되는 도구" 표 갱신 확인.

---

## Phase 0 결정 사항 준수 여부 (엄격 확인)

### 결정 5 — `mode` 파라미터 제거: **준수**

- `app/mcp_server.py:236` `search_endpoints(query, top_k=5, document_id=None)`.
  `mode` 없음. 통합 테스트가 FastMCP 스키마에서
  `set(properties) == {"query", "top_k", "document_id"}`로 정확히 단언한다
  (`not in` 뿐 아니라 집합 동등성까지 봄 — 좋은 단언).
- `SearchOptions.mode: str = "hybrid"`는 `app/services/search/search_service.py:42`에
  그대로 보존. `SearchService.search()`의 `hybrid|keyword|vector` 분기도 온전.
- FastAPI `/search` 라우트(`app/api/routes/search.py:22`)가 `mode` 쿼리
  파라미터를 계속 받고 `SearchOptions(..., mode=mode)`로 전달한다. **깨지지 않았다.**

### 결정 6 — 점수 임계값 없음: **준수**

- 코드 전역에 `0.3` 같은 신뢰도 임계값 상수·비교가 없다. 상수는
  `MIN_TOP_K=1`, `MAX_TOP_K=50`뿐이다.
- 트리거 조건은 `endpoint_candidate_search.py:104` `if keyword_candidates:`
  단 하나. **리스트가 비었을 때(정확히 0건)만** 벡터 단계로 넘어간다.
- 단, `_search_by_vector()`의 `h.score > 0.0` 필터(`:153`)는 임계값이 아니라
  "무의미한 0점 후보 제거"이며, 기존 `SearchService`(`:130`)의 동일 관행과
  일치한다. 결정 6 위반이 아니라고 판단한다. 다만 이 필터가 아래
  **치명 이슈 1**의 원인이 된다.

### 결정 7 — `include_example` 기본 False: **준수**

- 시그니처 기본값 `False`. FastMCP 스키마의 `default`가 `False`임을 통합
  테스트가 단언.
- False일 때 `example_code` 키 **부재** + 생성 로직 **미호출** 모두 확인.

### Gemini 혼동 여부: **혼동 없음 — 정확히 구분했다**

이 항목은 SPEC이 명시적으로 경고한 함정인데, 올바르게 처리했다.

- `app/services/indexer/embedding_provider.py`(`GeminiEmbeddingProvider`)는
  `git diff 5870f90^ 5870f90 -- app/services/indexer/`가 **빈 출력**이다.
  즉 이번 커밋에서 한 글자도 손대지 않았다. 미사용 주석 없음. `dependencies.py`의
  `_build_embedding_provider()`가 계속 생성하고 `VectorSearch`가 사용한다.
- 미사용 주석은 `app/services/rag/llm_provider.py:3`,
  `app/services/rag/rag_service.py:3`, `app/mcp_server.py:385`,
  `app/mcp_types.py:67,79`에만 붙어 있다. `grep -rn "미사용" app/` 전수 확인 결과
  임베딩 프로바이더 파일은 목록에 없다.
- `llm_provider.py` 상단 주석이 두 "Gemini"의 차이를 명시적으로 경고까지
  해뒀다. 이후 유지보수자가 같은 실수를 하지 않도록 한 조치로, 적절하다.
- `GeminiLLMProvider`/`TemplateLLMProvider`/`RAGService` 모두 삭제되지 않았고
  `ServiceBundle.rag_service`도 유지됐다(`app/api/routes/query.py`가 사용 중).

### 테스트 실효성 — 호출 카운트가 진짜인가: **대체로 진짜, 단 한 곳이 공허 참**

실제로 `tests/fixtures/fakes.py`를 읽고 판정했다.

- `CountingEmbeddingProvider.embed()`가 `self.embed_call_count += 1`로
  **실제 호출을 센다**. 형식만 갖춘 게 아니다. 색인 단계 호출을 배제하기 위한
  `reset_counts()`도 있고, 테스트가 검색 직전에 이를 호출한다.
- `ExplodingEmbeddingProvider.embed()`는 호출되면 `AssertionError`를 던진다.
  카운트 단언보다 강한 보증이며, 두 방식을 병행한 것은 좋은 설계다.
- `test_keyword_hit_does_not_call_embedding_provider`는 `assert candidates`로
  결과가 비지 않았음을 먼저 확인한 뒤 `embed_call_count == 0`을 단언한다.
  "결과가 없어서 호출도 없었다"는 위양성을 차단했다. **실효성 있음.**
- `test_vector_fallback_triggers_only_when_keyword_returns_zero`는
  `embed_call_count == 1`로 정확히 1회를 단언한다. **실효성 있음.**
- **그러나** `test_vector_fallback_results_are_marked_as_vector`는
  `assert all(c.match_type == "vector" for c in candidates)` 하나뿐이고,
  `candidates`가 항상 `[]`라서 **공허 참으로 통과**한다. 아래 치명 이슈 1.

---

## 정적 검사 · 테스트 실행 결과

### `uv run ruff check app/`

**12건 위반 — 전부 이번 커밋 범위 밖의 기존 파일.**

```
main.py:34:51                    F401  HttpOpenAPIFetcher imported but unused
models/openapi.py:78,181,218     E501  Line too long (112/102/102 > 100)
services/examples/request_example_service.py:15:5  F401  ApiParameter unused
services/indexer/chunk_builder.py:61:101           E501  (103 > 100)
services/indexer/indexer_service.py:10:1           I001  Import block un-sorted
services/ingestor/sync_service.py:3:1              I001  Import block un-sorted
services/parser/openapi_parser.py:321:85           E501  (106 > 100)
services/parser/schema_normalizer.py:6:1           I001  Import block un-sorted
services/search/keyword_search.py:6:1              I001  Import block un-sorted
services/search/search_service.py:83:101           E501  (106 > 100)
```

이번 커밋 신규 파일(`endpoint_candidate_search.py`,
`endpoint_details_service.py`, `schema_ref_resolver.py`,
`tag_catalog_service.py`, `mcp_server.py`, `mcp_types.py`,
`dependencies.py`)에서는 **위반 0건**. `git show 5870f90^:...`로 대조한 결과
`keyword_search.py` 등은 이번 커밋에서 변경되지 않았음을 확인했다.

### `uv run mypy app/`

```
mypy: No issues found
```

**무결점.** 신규 코드 타입 힌트 100%. `Any`는 JSON Schema dict 값 등
본질적으로 임의 타입인 곳에만 사용됐다.

### `uv run pytest tests/ -v`

```
211 passed
```

실패·에러·스킵 0건. Drive/Notion 신규 파일로 인한 수집 오류도 없었다
(검수 시점에 해당 파일들이 아직 테스트에 연결되지 않은 상태).

---

## 치명 이슈 (반드시 수정)

### 1. 벡터 보조 후보 생성이 실제로 한 번도 검증되지 않는다 (공허 참 테스트)

**어디**: `tests/unit/test_endpoint_candidate_search.py:175`
`test_vector_fallback_results_are_marked_as_vector`

**무엇이 문제인가**:
이 테스트는 SPEC 기능 1의 검증 기준 "결과 항목이 `match_type="vector"`로
표시된다"를 담당하는 **유일한** 테스트인데, `candidates`가 항상 빈 리스트라
`all()`이 공허 참으로 통과한다. 즉 이 검증 기준은 실질적으로 미검증이다.
`grep -rn "match_type == \"vector\"" tests/` 결과 이 단언 하나뿐이다.

**왜 빈 리스트인가 (근본 원인)**:
테스트 환경에는 Gemini 키가 없어 `HashEmbeddingProvider`가 쓰인다. 프로브로
측정한 결과:

```
PROBE3 hash cos(identical)=1.000000  cos(different)=0.000000
PROBE2 raw vector hits=[(ae06cc5d, 0.0), (ae06cc5d, 0.0), (ae06cc5d, 0.0), (ae06cc5d, 0.0)]
PROBE2 hits total=4  kept_after_score_gt_0=0
```

해시 임베딩은 서로 다른 텍스트에 대해 코사인 유사도가 정확히 `0.0`이다.
`_search_by_vector()`의 `h.score > 0.0` 필터(`endpoint_candidate_search.py:153`)가
이를 전량 제거한다. 결과적으로 **테스트 스위트 전체에서
`_to_candidates(..., "vector", ...)`가 후보를 반환한 적이 단 한 번도 없다.**

의미가 비슷하지만 키워드가 겹치지 않는 질의로도 확인했다:

```
PROBE2 query='강아지 정보 알려줘'      -> 0 items []
PROBE2 query='canine lookup'          -> 0 items []
PROBE2 query='retrieve animal record' -> 0 items []
```

**제품 코드는 정상이다** — 양수 점수를 내는 스텁 벡터 검색기를 주입하면
정상 동작한다:

```
PROBE3 stub vector -> called=1 items=4 types=['vector','vector','vector','vector']
```

따라서 이것은 **제품 버그가 아니라 테스트 설계 결함**이다. 하지만 결과적으로
기능 1의 핵심 분기가 회귀 방어를 전혀 받지 못하고 있으므로 치명으로 분류한다.

**어떻게 고칠 것인가**:
`tests/fixtures/fakes.py`에 양수 유사도를 내는 스텁 벡터 검색기를 추가하고,
`EndpointCandidateSearch`에 주입해 벡터 분기를 실증하는 테스트를 쓴다.
`EndpointCandidateSearch`는 이미 `vector_search`를 생성자 주입받으므로
제품 코드 수정 없이 가능하다.

```python
# tests/fixtures/fakes.py 에 추가
class StubVectorSearch:
    """고정된 양수 점수를 내는 페이크 벡터 검색기.

    HashEmbeddingProvider 는 서로 다른 텍스트에 대해 유사도가 정확히 0.0 이라
    실제 벡터 보조 분기를 테스트로 재현할 수 없다. 이 페이크로 분기를 실증한다.
    """

    def __init__(self, chunk_ids: list[str], score: float = 0.9) -> None:
        self._chunk_ids = chunk_ids
        self._score = score
        self.call_count = 0

    def search(self, query, top_k, candidates=None):
        from app.services.search.vector_search import VectorSearchHit

        self.call_count += 1
        return [
            VectorSearchHit(chunk_id=cid, score=self._score)
            for cid in self._chunk_ids[:top_k]
        ]
```

그리고 `test_endpoint_candidate_search.py`에 최소 3건을 추가한다.

1. `test_vector_fallback_actually_produces_candidates` —
   `assert candidates` (비어 있지 않음)를 **먼저** 단언한 뒤
   `all(c.match_type == "vector" ...)`를 단언. 공허 참 재발 방지.
2. `test_vector_fallback_respects_top_k` — 스텁이 10건을 내도 `top_k=3`이면
   3건만 반환되는지 (`_to_candidates`의 `break` 검증).
3. `test_zero_score_vector_hits_are_discarded` — `score=0.0` 스텁으로
   빈 리스트가 되는지 (현행 필터 동작을 **의도된 사양으로 고정**).

기존 `test_vector_fallback_results_are_marked_as_vector`도 맨 앞에
`assert candidates`를 넣도록 고쳐라. 지금 형태로 남겨두면 같은 함정이 반복된다.

---

## 중대 이슈 (수정 권장)

### 2. `document_id` 미지정 검색이 전체 청크를 메모리로 적재한다

**어디**: `app/services/search/endpoint_candidate_search.py:120`
`EndpointCandidateSearch._endpoint_chunks()`

```python
chunks = self._chunk_repo.list_by_endpoint_filter(document_id=document_id)
return [c for c in chunks if c.chunk_type == "endpoint"]
```

**무엇이 문제인가**:
`ChunkRepository.list_by_endpoint_filter()`는 `method`/`tag`/`document_id`가
모두 `None`이면 `return self.list_all()`로 **전체 청크를 무조건 SELECT** 한다
(`app/repositories/chunk_repository.py:65-66`). `document_id`만 주어져도
`chunk_type` 조건 없이 해당 문서의 전 청크를 가져온다. 그 뒤 Python 리스트
컴프리헨션으로 `chunk_type == "endpoint"`를 걸러 나머지를 버린다.

프로브 측정(샘플 문서 1건, 소규모):

```
PROBE chunks loaded=6  endpoint_only=4  (nonendpoint discarded=2)
```

문서 수·엔드포인트 수에 비례해 낭비가 선형 증가한다. 게다가 `ApiChunk`는
`embedding` 벡터 컬럼(pgvector, `EMBEDDING_DIM` 차원)을 가진 엔터티라서 청크
1건당 전송량이 작지 않다. 검색은 가장 빈번한 호출 경로인데, "후보 압축은
가볍고 빨라야 한다"는 SPEC 기능 1의 설계 취지와 정면으로 어긋난다.
버려질 section/schema 청크의 임베딩까지 매 검색마다 DB에서 끌어온다.

또한 저장소 계약 오용이기도 하다. `list_by_endpoint_filter`는 이름과 달리
"endpoint 청크만"을 보장하지 않으며, 호출부가 그 사실을 Python 필터로
보정하고 있다. 저장소가 이미 `chunk_type == "endpoint"` SQL 필터를
`method`/`tag` 분기 안에서 쓰고 있는데(`:73`), 그 능력을 활용하지 않는다.

**어떻게 고칠 것인가**:
`ChunkRepository`에 endpoint 청크 전용 조회 메서드를 추가하고
`_endpoint_chunks()`가 그것을 호출하게 한다. Python 필터는 제거한다.

```python
# app/repositories/chunk_repository.py
def list_endpoint_chunks(self, document_id: str | None = None) -> Sequence[ApiChunk]:
    """endpoint 타입 청크만 SQL 로 필터링해 반환한다.

    후보 검색은 endpoint 청크만 쓰므로 section/schema 청크를 DB 단계에서
    걸러 불필요한 임베딩 컬럼 전송을 막는다.
    """
    stmt = select(ApiChunk).where(ApiChunk.chunk_type == "endpoint")
    if document_id is not None:
        stmt = stmt.where(ApiChunk.document_id == document_id)
    return self._session.execute(stmt).scalars().all()
```

```python
# app/services/search/endpoint_candidate_search.py
def _endpoint_chunks(self, document_id: str | None) -> list[ApiChunk]:
    """검색 대상이 되는 endpoint 타입 청크만 SQL 필터로 조회한다."""
    return list(self._chunk_repo.list_endpoint_chunks(document_id=document_id))
```

현행 docstring이 이미 "SQL 필터로 조회한다"라고 적혀 있는데 실제로는
Python 필터다. **docstring이 코드와 불일치**하므로 이 수정으로 함께 해소된다.

추가로, `top_k`가 최대 50인데 후보 전량을 메모리에 올려 정렬하는 구조이므로
장기적으로는 `KeywordSearch`에도 SQL 측 사전 필터를 검토할 것.
(이번 수정 필수 범위는 아님.)

### 3. 존재하지 않는 `document_id`에 대한 처리가 도구마다 다르다

**어디**: `app/mcp_server.py`의 `search_endpoints` / `resolve_ref` / `list_tags`

프로브로 확인한 실제 동작:

| 도구 | 없는 `document_id` 입력 시 | 결과 |
|---|---|---|
| `list_tags` | `DocumentNotFoundError` | `{"error": true, "code": "document_not_found"}` |
| `search_endpoints` | 조용히 빈 결과 | `{"items": []}` |
| `resolve_ref` | `SchemaRefNotFoundError` | `{"error": true, "code": "schema_ref_not_found"}` |

```
PROBE unknown document_id -> []
PROBE resolve_ref unknown doc -> SchemaRefNotFoundError code=schema_ref_not_found
```

**무엇이 문제인가**:
같은 의미의 잘못된 입력(등록되지 않은 문서 ID)에 대해 세 도구가 서로 다른
세 가지 반응을 한다. 이 도구들의 소비자는 LLM이다. LLM이 오타 난 문서 ID로
`search_endpoints`를 호출하면 빈 결과를 받고 "해당 문서에 엔드포인트가 없다"고
잘못 결론 내린다. 실제로는 문서 자체가 없는 것이다. `resolve_ref`는 문서가
없는 건지 스키마가 없는 건지 구분되지 않는 코드를 준다.

SPEC 제약 "모든 도구는 `DomainError`/`IntegrationError` 발생 시 동일한
`{"error": true, "code", "message"}` 포맷을 반환한다"는 포맷 통일만 규정하고
어떤 상황을 오류로 볼지는 규정하지 않는다. 따라서 SPEC 위반은 아니지만,
도구 계약의 일관성 결함이며 LLM 오작동을 유발한다.

**어떻게 고칠 것인가**:
`list_tags`가 이미 채택한 "명시적 검증" 방식으로 통일한다.

- `EndpointCandidateSearch._validate()`에 문서 존재 검증을 추가한다.
  `DocumentRepository`를 생성자 주입받고, `options.document_id is not None`
  이면 `self._document_repo.get(document_id) is None`일 때
  `DocumentNotFoundError(document_id)`를 던진다.
- `SchemaRefResolver._find_schema()`도 동일하게, `document_id`가 주어졌을 때
  먼저 문서 존재를 확인하고 없으면 `DocumentNotFoundError`를 던진다.
  그래야 "문서 없음"과 "문서는 있는데 스키마 없음"이 코드로 구분된다.
- 세 도구 모두 `document_id` 미등록 시 `code="document_not_found"`를 반환하는
  테스트를 추가한다.

---

## 경미 이슈 (개선 권장)

### 4. `_run_bundle`의 `fn` 파라미터에 타입 힌트가 없다

**어디**: `app/mcp_server.py:50`

```python
def _run_bundle(app_state: AppState, fn):
```

`fn`에 어노테이션이 없고 반환 타입도 없다. 각 도구의 내부 `_inner(bundle)`도
`bundle` 파라미터가 무타입이다. mypy가 통과하는 이유는 이 지점이 암묵적
`Any`로 처리되기 때문이며, 실제로는 타입 검사가 무력화된 구간이다.
CLAUDE.md의 "타입 힌트를 사용하라" 규칙과 SELF_CHECK의 "신규 코드 타입 힌트
100%" 주장에 어긋난다.

**고치는 법**: `TypeVar`로 제네릭화한다.

```python
from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")

def _run_bundle(app_state: AppState, fn: Callable[[ServiceBundle], _T]) -> _T:
    ...
```

각 `_inner`도 `def _inner(bundle: ServiceBundle) -> EndpointSearchResponse:`
처럼 명시한다. `ServiceBundle`은 `app.api.dependencies`에서 import.

### 5. `resolve_ref`의 문서 간 동명 스키마 모호성 (Generator 자기보고 2번 — 아래 판정 참조)

프로브로 재현했다. 두 문서에 각각 `Pet` 스키마가 있을 때:

```
PROBE ambiguity d1=b6b3ac52 d2=5d3ecd64 -> name=Pet fields=['totally_different']
```

나중에 등록된 문서(`d2`)의 `Pet`이 선택됐다. `DocumentRepository.list_all()`이
`ORDER BY indexed_at DESC`이므로 최신 문서 우선이 맞다.

다만 `indexed_at`은 `datetime.now(timezone.utc)`로 설정되므로 동일 트랜잭션
내 대량 등록 등으로 **값이 완전히 같아지면 정렬이 불안정**해진다(2차 정렬 키
없음). SELF_CHECK의 "결정적"이라는 표현은 이 경우 성립하지 않는다.

**고치는 법**: `DocumentRepository.list_all()`의 정렬에 2차 키를 추가한다.
`.order_by(desc(ApiDocument.indexed_at), ApiDocument.id)`. 1줄 수정으로
동률 시에도 결정성이 보장된다.

### 6. `SELF_CHECK.md`의 lint 위반 건수가 실제와 다르다

`SELF_CHECK.md:139-141`은 "기존 lint 위반(E501 등, ... 등 **6건**)"이라고
적었으나 실제 `ruff check app/`은 **12건**을 보고한다(E501 5건, I001 4건,
F401 2건 — 합 11건에 `main.py` F401 포함하면 12건). 자기 점검 수치가
실측과 어긋난다. 검수자가 수치를 신뢰할 수 없게 만드는 문제이므로
재검수 시 정확한 수치로 갱신하라.

---

## Generator 자기보고 미해결 사항 — 타당성 판정

### 보고 1: `security` 필드를 `get_endpoint_details`에 넣지 않음 → **타당함 (수용)**

검증했다. `grep -rn "security" app/models/openapi.py app/services/parser/openapi_parser.py`
결과가 **완전히 빈 출력**이다. 파서가 `security`를 추출하지 않고 ORM에도
컬럼이 없다. 노출하려면 파서 확장 + 모델 컬럼 추가 + Alembic 마이그레이션이
필요하며, 이는 "기존과 동일 + `schema_ref` 명시 노출"이라는 기능 2의 범위를
명백히 넘는다.

SPEC 데이터 흐름 다이어그램(`:88-91`)이 `Responses/Security 반환`이라고
적은 것은 사실이나, 기능 2의 **출력 정의**(`:237`)는 "기존과 동일 +
`schema_ref` 필드 명시적 노출"이고 **검증 기준**(`:240-243`) 3개 어디에도
`security`가 없다. 검증 기준이 명세의 계약이므로 미구현은 SPEC 위반이 아니다.

**단, 조건부로 수용한다.** 이 항목은 SELF_CHECK "알려진 제약"에만 있고
SPEC에는 반영되지 않았다. SPEC 다이어그램과 구현이 어긋난 채로 남으면
다음 작업자가 혼란을 겪는다. **SPEC 기능 2에 "`security`는 현재 파서 미지원으로
범위 외 — 별도 작업으로 분리"를 한 줄 명기하거나, 별도 후속 태스크로
등록하라.** 문서화 없이 넘어가는 것은 허용하지 않는다.

### 보고 2: `resolve_ref`의 `document_id` 생략 시 모호성 → **부분적으로만 타당 (조건부 수용)**

"동명 스키마가 있으면 최신 문서 것이 선택된다"는 사실 자체는 프로브로
재현 확인했고, `document_id`로 해소 가능하다는 것도 맞다. SPEC 기능 3의
검증 기준 3개(없는 ref 에러 / 중첩 비재귀 / 결정성) 중 어느 것도 문서 간
모호성을 다루지 않으므로 SPEC 위반은 아니다.

**그러나 "결정적"이라는 서술은 부정확하다.** 위 경미 이슈 5에서 지적했듯
`indexed_at` 동률 시 2차 정렬 키가 없어 순서가 불안정하다. SELF_CHECK가
"(결정적이지만 문서 간 모호성은 남는다)"라고 단언한 것은 과장이다.

또한 이 동작이 **사용자에게 전혀 드러나지 않는다**는 점이 더 문제다. 응답
`ResolvedSchema`는 `name`과 `fields`만 담고 **어느 문서에서 왔는지 알려주지
않는다**. LLM이 여러 문서를 등록한 상태에서 `resolve_ref("#/components/schemas/Pet")`을
호출하면 다른 문서의 `Pet`을 받고도 알아챌 방법이 없다. 잘못된 스키마로
코드를 생성할 위험이 실재한다.

**조건부 수용 — 다음 중 하나를 반드시 이행하라**:
- (권장) `ResolvedSchemaResult`에 `document_id: str` 필드를 추가해 어느 문서의
  스키마인지 밝힌다. `SchemaRefResolver.resolve()`가 찾은 `ApiSchema`의
  `document_id`를 함께 반환하면 된다. LLM이 검증할 수 있게 된다.
- 최소한 `DocumentRepository.list_all()`에 2차 정렬 키(`ApiDocument.id`)를
  추가해 진짜 결정성을 확보하고, `resolve_ref` docstring에 "여러 문서에 동명
  스키마가 있으면 가장 최근 등록 문서가 선택되므로 `document_id` 지정을
  권장한다"를 명시한다.

### 보고 3: ruff 기존 위반 12건을 범위 밖으로 둠 → **타당함 (수용)**

`git show 5870f90^:app/services/search/keyword_search.py`와 현재 파일을 `diff`한
결과 **완전히 동일**함을 확인했다. 12건 모두 이번 커밋이 건드리지 않은
파일에 있고, 신규 파일 위반은 0건이다. CLAUDE.md의 "Surgical Changes —
사전 존재 데드코드/위반은 건드리지 마라" 원칙에 정확히 부합한다.
**전적으로 타당하다. 감점하지 않았다.**

단, 보고한 건수가 6건으로 틀렸다(경미 이슈 6). 판단은 옳았으나 수치는
고쳐라.

---

## 구체적 개선 지시 (우선순위 순)

1. **`tests/fixtures/fakes.py` — `StubVectorSearch` 추가 + `tests/unit/test_endpoint_candidate_search.py` 벡터 분기 실증 테스트 3건 추가.**
   현행 `test_vector_fallback_results_are_marked_as_vector`는 빈 리스트에
   `all()`을 걸어 공허 참으로 통과한다. 맨 앞에 `assert candidates`를 넣고,
   양수 점수 스텁을 주입해 `match_type="vector"` 후보가 **실제로 생성되는지**
   검증하라. `top_k` 절단과 `score=0.0` 폐기도 함께 고정하라. (치명 이슈 1)

2. **`app/repositories/chunk_repository.py` — `list_endpoint_chunks()` 신설, `app/services/search/endpoint_candidate_search.py:120 _endpoint_chunks()` 를 그것으로 교체.**
   현재 전체 청크(임베딩 벡터 포함)를 SELECT 한 뒤 Python에서 버린다.
   `WHERE chunk_type = 'endpoint'`를 SQL로 내려라. docstring이 이미
   "SQL 필터로 조회한다"고 적혀 있으니 코드를 문서에 맞춰라. (중대 이슈 2)

3. **`app/services/search/endpoint_candidate_search.py` + `app/services/schemas/schema_ref_resolver.py` — 없는 `document_id` 를 `DocumentNotFoundError` 로 통일.**
   `list_tags`만 오류를 내고 나머지 둘은 빈 결과/다른 코드를 낸다. LLM이
   "문서 없음"과 "결과 없음"을 구분하지 못한다. `DocumentRepository`를 주입해
   명시적으로 검증하고, 세 도구 각각에 테스트를 추가하라. (중대 이슈 3)

4. **`app/services/schemas/schema_ref_resolver.py` + `app/mcp_types.py` — `ResolvedSchemaResult` 에 `document_id` 추가.**
   동명 스키마가 여러 문서에 있을 때 어느 것을 받았는지 LLM이 알 수 없다.
   함께 `app/repositories/document_repository.py:35`의 `order_by`에
   `ApiDocument.id` 2차 키를 추가해 동률 시 결정성을 확보하라. (자기보고 2 판정)

5. **`app/mcp_server.py:50 _run_bundle` — `TypeVar` 로 제네릭 타입 힌트 부여, 각 `_inner(bundle: ServiceBundle) -> ...` 명시.**
   현재 무타입이라 도구 반환 타입 검사가 무력화돼 있다. (경미 이슈 4)

6. **`docs/exec_plans/docs_mcp_expansion/SPEC.md` 기능 2 — `security` 미지원을 범위 외로 명기.**
   SPEC 다이어그램은 `Security 반환`이라 적혀 있으나 파서·ORM 미지원으로
   구현되지 않았다. 문서와 구현의 불일치를 해소하고 후속 태스크로 분리하라.
   아울러 `SELF_CHECK.md:139-141`의 lint 위반 건수를 6건 → 12건으로
   정정하라. (자기보고 1·3 판정, 경미 이슈 6)

---

## 방향 판단

**현재 방향 유지.**

구조 설계는 옳다. 특히 다음 판단들은 재검수에서도 유지할 것을 권한다.

- `SearchService`를 개조하지 않고 `EndpointCandidateSearch`를 신설한 것.
  FastAPI `/search`와 `RAGService`의 하이브리드 계약을 깨지 않으면서
  "문서 성격에 따라 경로를 분리한다"는 SPEC 원칙을 지켰다.
- 벡터 보조 활성 여부를 프로바이더 클래스가 아니라 설정값
  (`is_vector_fallback_available()`)으로 판별한 것. 폴백 구현이 바뀌어도
  조건이 깨지지 않는다.
- `describe_type()` 한 곳에 중첩 처리를 몰아 재귀를 구조적으로 불가능하게
  만든 것. 방문 집합 관리가 필요 없어졌다.
- `example_code`를 서비스 계층에서 `str | None`으로 두고 페이로드 변환
  함수가 키 존재 여부를 결정하게 한 것. 계층 책임 분리가 정확하다.
- 임베딩 미호출 검증에 카운트 페이크와 예외 페이크를 **병행**한 것.

재작업이 필요한 것은 아키텍처가 아니라 **테스트 실효성 1건과 저장소 쿼리
1건, 에러 계약 일관성 1건**이다. 위 6개 지시를 반영한 뒤 재검수하면 합격
가능하다고 판단한다.

---

## 검수 방법 부기

- 워크트리 파일은 **하나도 수정하지 않았다.** 이 `QA_REPORT.md`가 유일한
  생성물이다. 검수 중 `git diff --stat HEAD -- <검수대상 파일들>`로 대상
  파일이 변경되지 않았음을 확인했다.
- 동작 확인용 프로브 테스트는 스크래치패드
  (`/tmp/claude-1000/.../scratchpad/`)에 작성해 실행했으며 워크트리 밖이다.
  프로젝트 fixture 재사용을 위해 스크래치패드에 별도 `conftest.py`를 두고
  `tests.conftest`를 import 했다.
- 검수 도중 다른 에이전트가 Drive/Notion 파일
  (`app/models/document_meta.py`, `app/repositories/document_meta_repository.py`,
  `app/services/documents/` 등)을 워크트리에 추가하기 시작했으나, 검수 대상
  파일에는 영향이 없었고 `pytest` 211건 통과 결과도 그 이전에 측정됐다.
