# 자체 점검

## SPEC 기능 체크

- [x] 기능 1 (OpenAPI 문서 등록): `src/services/ingestor/sync_service.py::SyncService.register` + `src/api/routes/documents.py` — `tests/unit/test_sync_service.py`, `tests/integration/test_api_documents.py`
- [x] 기능 2 (파싱·정규화): `src/services/parser/openapi_parser.py`, `src/services/parser/schema_normalizer.py` — `tests/unit/test_parser.py`, `tests/unit/test_schema_normalizer.py`
- [x] 기능 3 (청크 빌드·임베딩): `src/services/indexer/chunk_builder.py`, `src/services/indexer/embedding_provider.py`, `src/services/indexer/indexer_service.py` — `tests/unit/test_chunk_builder.py`, `tests/unit/test_embedding_provider.py`, `tests/unit/test_indexer_service.py`
- [x] 기능 4 (하이브리드 검색): `src/services/search/search_service.py` — `tests/unit/test_search_service.py`, `tests/integration/test_api_search.py`
- [x] 기능 5 (엔드포인트 상세): `src/api/routes/endpoints.py::get_endpoint` — `tests/integration/test_api_endpoints.py::test_get_endpoint_detail`
- [x] 기능 6 (요청 예시 생성): `src/services/examples/request_example_service.py` — `tests/unit/test_request_example_service.py`, `tests/integration/test_api_endpoints.py::test_get_endpoint_example_curl`
- [x] 기능 7 (RAG 자연어 질의): `src/services/rag/rag_service.py`, `src/services/rag/llm_provider.py` — `tests/unit/test_rag_service.py`, `tests/integration/test_api_query.py`
- [x] 기능 8 (재색인 / 동기화): `src/services/ingestor/sync_service.py::SyncService.resync` + `src/api/routes/sync.py` — `tests/unit/test_indexer_service.py`, `tests/integration/test_api_sync.py`
- [x] 기능 9 (문서 목록/상세/삭제): `src/api/routes/documents.py` + `SyncService.delete` — `tests/integration/test_api_documents.py`, `tests/unit/test_sync_service.py::test_delete_document_removes_chunks`
- [x] 기능 10 (키워드/벡터 전용 검색): `src/api/routes/search.py` (mode 파라미터), `src/services/search/{keyword,vector}_search.py` — `tests/unit/test_keyword_search.py`, `tests/unit/test_vector_search.py`, `tests/integration/test_api_search.py::test_search_keyword_mode_only_keyword_scored`
- [x] 기능 11 (헬스체크/레디니스): `src/api/routes/health.py` — `tests/integration/test_api_health.py`
- [x] 기능 12 (에러 응답 포맷): `src/main.py::_register_exception_handlers`, `TraceIdMiddleware`, `src/core/logging.py` — `tests/integration/test_api_documents.py::test_register_duplicate_source_url_conflict`, `tests/integration/test_api_endpoints.py::test_get_endpoint_not_found`, `tests/integration/test_api_sync.py::test_sync_document_not_found` (모두 `error.type/message/trace_id` 스키마 확인)
- [x] 기능 13 (인터페이스 교체 가능성): `EmbeddingProvider` / `LLMProvider` / `OpenAPIFetcher` Protocol + `InMemoryVectorIndex`, 각 Repository — `tests/unit/test_embedding_provider.py`, `tests/unit/test_vector_search.py`, `tests/unit/test_sync_service.py::test_register_with_source_url_uses_fetcher`

## 코드 자체 평가

- 금지 패턴 사용 여부: 없음 (전역 가변 상태·빈 except·하드코딩 경로 없음; 설정은 `src/core/config.py` 통해 주입).
- 타입 힌트 적용률: 약 98% (모든 public 함수/클래스 메서드 시그니처에 타입 힌트, 테스트 fixture 는 pytest 관례상 일부 생략).
- 테스트 케이스 수: 총 92개 (`pytest tests/ -v` → 92 passed). 유닛 65개 + 통합 27개.
- 주요 설계 결정:
  - SQLite + `StaticPool` 을 통한 in-memory DB 공유로 통합 테스트에서 단일 프로세스 내 모든 FastAPI 요청이 동일 DB 를 보게 했다.
  - 임베딩은 `HashEmbeddingProvider` 로 토큰 해시 버킷 + L2 정규화 → 외부 API 없이 결정적, 재현 가능.
  - 하이브리드 검색은 `alpha * keyword + (1 - alpha) * vector`, 기본 α=0.4. 테스트에서는 α=0.4 로 키워드 신호를 보존.
  - RAG 환각 방지: 검색 결과 0건 시 고정 문자열 `"해당 API 를 찾을 수 없습니다."` 만 반환하고 `is_grounded=False`; 1건 이상이면 답변 본문에 반드시 `METHOD PATH` 가 등장하도록 템플릿 조립.
  - 서비스 레이어는 Protocol (`OpenAPIFetcher`, `EmbeddingProvider`, `LLMProvider`, `VectorIndex`) 에만 의존. 테스트는 `InMemoryFetcher` 주입으로 네트워크 없이 전체 파이프라인을 검증한다.

## 테스트 실행 결과

```
======================== 92 passed in 1.30s ========================
```

## 수정한 src 파일
- 없음. `tests/conftest.py` 의 `sqlite_engine` 픽스처만 `StaticPool` 을 쓰도록 수정(통합 테스트에서 세션 간 in-memory DB 공유 목적).
