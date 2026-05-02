# QA Report — feat-openapi-rag-server

**전체 판정**: 합격
**가중 점수**: 8.5 / 10.0

**항목별 점수**:
- 기능 정확성: 9/10 — SPEC 13개 기능 모두 구현 및 검증, 엣지 케이스(빈 query/문서 미존재/중복 source_url/지원 외 mode·format) 응답 코드 일치.
- 코드 품질: 7/10 — 단일 책임·타입 힌트·금지 패턴 통제는 양호하나, 서비스 레이어가 sqlalchemy 구체 구현(`Session`, `sa_delete`)을 직접 참조해 SPEC 13 의 "구체 구현이 아닌 인터페이스만 참조" 원칙을 일부 위배.
- 성능: 8/10 — 인메모리 코사인/토큰 점수 O(N) 으로 적절. 단, 모든 검색 호출이 매번 `chunk_repo.list_all()` 을 두 번(키워드/벡터 후보 빌드+점수) 가까이 돌아 N 이 커지면 비효율 가능.
- 테스트 커버리지: 9/10 — 92개 테스트 (유닛 65 + 통합 27) 전부 통과. SPEC 의 거의 모든 검증 기준이 1:1 로 매핑되어 검증됨. 다만 트랜잭션 롤백·환각 방지 핵심 보장 일부는 결정론에 의존 검증.

---

## SPEC 기능 체크

- [PASS] 기능 1 (OpenAPI 문서 등록): `services/ingestor/sync_service.py::SyncService.register` — `source_url`/`raw_document` XOR 검증, 중복 URL 시 `DuplicateDocumentError`(409), 파싱 실패 시 `ParserError`(422 매핑), `document_sync_history` 에 `registered` 행 추가. `tests/integration/test_api_documents.py::test_register_duplicate_source_url_conflict`, `test_register_invalid_requires_xor` 로 확인.
- [PASS] 기능 2 (파싱·정규화): `services/parser/openapi_parser.py` — OpenAPI 3.x + Swagger 2.0 분기, path 파라미터 `required=true` 정상 (`test_parse_openapi3_path_parameter_required`), 알 수 없는 ref 는 `schema_ref=None` 으로 관대 처리, 미지 버전 / 누락 paths / 빈 입력 모두 `ParserError`.
- [PASS] 기능 3 (청크 빌드·임베딩): `services/indexer/chunk_builder.py` + `embedding_provider.HashEmbeddingProvider` — 결정성·L2 정규화 검증(`test_embedding_deterministic`, `test_embedding_l2_norm_is_1`), 차원 256 기본, 재색인 시 청크 교체(`test_reindex_replaces_chunks`).
- [PASS] 기능 4 (하이브리드 검색): `services/search/search_service.py` — α·(1-α) 가중합, `find pet by id` → top-1 `GET /pet/{petId}` 보장(`test_search_finds_expected_endpoint`), method/tag/document_id 필터, top_k 절단, score ∈ [0,1] 정렬.
- [PASS] 기능 5 (엔드포인트 상세): `api/routes/endpoints.py::get_endpoint` — 미존재 시 `EndpointNotFoundError`(404), `referenced_schemas` 가 실제 ref 만 포함, responses 가 `status_code` 오름차순(`test_get_endpoint_detail`).
- [PASS] 기능 6 (요청 예시 생성): `services/examples/request_example_service.py` — curl/python/fetch/axios 4종 지원, path 파라미터 치환, 결정성 (`test_deterministic_for_same_inputs`), 미지 format 422, 미지 endpoint 404. (SPEC 은 curl/python/fetch 3개 명시 — axios 추가는 초과 충족이며 결격 없음.)
- [PASS] 기능 7 (RAG 자연어 질의): `services/rag/rag_service.py` + `TemplateLLMProvider` — 검색 0건 시 고정 메시지 `"해당 API 를 찾을 수 없습니다."` + `is_grounded=False` + `citations=[]`, 결과 1+건 시 답변에 `METHOD PATH` 포함 (`test_rag_with_results_includes_method_path`), 결정성(`test_rag_deterministic_on_repeat`).
- [PASS] 기능 8 (재색인/동기화): `SyncService.resync` — 동일 해시 `skipped`(`test_sync_same_hash_skipped`), 변경 시 `reindexed`, `force=true` 강제(`test_sync_force_reindexes_even_without_change`), 미존재 404.
- [PASS] 기능 9 (목록/상세/삭제): `api/routes/documents.py` — cascade 삭제 후 GET 404(`test_delete_document_cascade`), 목록 `indexed_at` 내림차순, 벡터 인덱스에서 청크 즉시 제거(`SyncService.delete` → `vector_index.delete_many`).
- [PASS] 기능 10 (키워드/벡터 전용): `api/routes/search.py` mode 파라미터 — keyword 모드에서 `vector_score=0`, vector 모드에서 `keyword_score=0`(`test_search_keyword_mode_only_keyword_scored`), 미지 mode 422.
- [PASS] 기능 11 (헬스/레디니스): `api/routes/health.py` — `/health` 무조건 200, `/ready` 가 DB 실패 시 `degraded` + `db=false` 로 200 유지 (broad except 로 격리), 빈 DB 일 때 `documents=0` (`test_ready_returns_ok_when_empty`).
- [PASS] 기능 12 (에러 응답 포맷): `main._register_exception_handlers` + `TraceIdMiddleware` — 422/404/409/500 모두 `{error: {type, message, trace_id}}` 통일. 응답 헤더 `X-Trace-Id` 일관 주입. 스택트레이스는 `_LOG.error(exc_info=True)` 로 로그에만 흐른다.
- [PASS] 기능 13 (인터페이스 교체 가능성): `EmbeddingProvider`/`LLMProvider`/`OpenAPIFetcher` Protocol 정의. 테스트는 `InMemoryFetcher` 주입으로 네트워크 없이 전체 파이프라인 검증(`test_register_with_source_url_uses_fetcher`). 다만 SPEC 의 정적 import 검사 측면에서는 `services/ingestor/sync_service.py` 가 `sqlalchemy` 를 직접 import 한다 — SPEC 본문은 `sqlite3/openai` 만 명시 금지하므로 형식적 위반은 아니나, 정신상 약한 위반.

---

## 테스트 실행 결과

```
============================== 92 passed in 1.53s ==============================
```

- 유닛 65 + 통합 27. 모두 통과. 실행 시간 < 2초로 외부 네트워크 의존 없음 확인.

---

## 주요 감점 사유 / 구체적 개선 지시

1. `src/services/ingestor/sync_service.py` 의 sqlalchemy 누수
   - 파일 상단에서 `from sqlalchemy.orm import Session` import.
   - `resync()` 본문(170-172 라인) 에서 `from sqlalchemy import delete as sa_delete` 후 `self._session.execute(sa_delete(ApiSchema)...)` 직접 호출.
   - 또한 166-167 라인에서 `self._session.delete(ep)` 로 ORM 세션 메서드를 직접 호출.
   - **수정 방향**: `EndpointRepository.delete_by_document(document_id)` 와 `EndpointRepository.delete_schemas_by_document(document_id)` 를 추가하고, `SyncService` 는 `Session` 타입 시그니처를 제거하라. `commit/flush` 도 `UnitOfWork` 같은 래퍼나 repository 가 노출하는 transactional 메서드로 옮기면 SPEC 13 의 "서비스 계층은 인터페이스만 참조" 를 정합적으로 충족.

2. `src/services/search/search_service.py::SearchService.search` 의 후보 빌드 비효율
   - 49-70 라인에서 `_build_candidate_chunks()` → `chunk_repo.list_all()` 한 번, 이후 `keyword_search.search()` 와 `vector_search.search()` 가 각자 또 `chunk_repo.list_all()` / 인메모리 dict 순회를 한다. 같은 후보 집합을 두 단계가 따로 다시 스캔하므로 N 이 커지면 3N 스캔.
   - **수정 방향**: `_build_candidate_chunks()` 결과를 두 검색 모듈에 공유해 점수만 계산하도록 시그니처를 `score_candidates(query, candidate_chunks)` 형태로 통합하거나, KeywordSearch/VectorSearch 가 candidates set 만 받고 chunk lookup 은 service 가 한 번만 하도록 리팩터링.

3. `src/services/indexer/indexer_service.py::_to_parameter_entity` / `_to_request_body_entity` / `_to_response_entity` 의 함수 내 import + assert
   - 루프마다 함수 안에서 `from app.services.parser.openapi_parser import ParsedParameter` (등) import + `assert isinstance(...)`.
   - **수정 방향**: import 는 모듈 상단으로 옮기고, 시그니처를 `parsed: ParsedParameter` 로 정확히 타이핑. assert 는 타입 시스템으로 대체. 함수 단일 책임은 양호하나 동적 import + assert 는 코드 품질 감점 요소.

4. `src/api/routes/health.py::ready` 의 broad `except Exception:`
   - 43-44 라인의 광범위 except 는 의도(테스트로 보장된 degraded 응답)이긴 하나, 어떤 예외인지 로그에 남기지 않아 운영 시 원인 추적이 어렵다.
   - **수정 방향**: `except Exception as exc:` 로 받아 `_LOG.warning("ready check failed", exc_info=True, extra={...})` 한 줄 남기고 하위 흐름 진행.

---

## 방향 판단

[현재 방향 유지]

전체 설계(레이어드, Protocol 기반 DI, 결정적 임베딩/LLM, 트랜잭션 단위 재색인) 가 SPEC 의 의도와 충실히 정합한다. 92개 테스트가 SPEC 의 거의 모든 검증 기준을 1:1 로 검증하며, 외부 네트워크 의존 없이 1.5 초 내에 완주한다. 위 4개 항목은 리팩터링 수준의 개선이며, 합격 후 후속 작업으로 처리해도 무방하다.
