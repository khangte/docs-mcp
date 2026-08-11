# 검색 성능 개선 방안 (분석)

- 상태: **전 항목 구현 완료**(2026-08-10 갱신) — 상위제안 RRF·P1~P6 모두 구현됨. 항목별 상태는 각 절 제목 옆 표기 참조.
- 일시: 2026-08-08
- 작성: architect
- 대상 코드: `app/services/search/`, `app/services/documents/`, `app/repositories/chunk_repository.py`, `app/repositories/document_meta_repository.py`

## 현재 구조 요약

두 개의 독립된 검색 경로가 있다.

### 1) OpenAPI 엔드포인트 검색 — `EndpointCandidateSearch`
- 전략(작성 당시, 2026-08-08): **키워드 우선 + 벡터 보조**. 키워드 결과가 정확히 0건일 때만 벡터 검색을 호출한다(SPEC Phase 0 결정 6).
  ⚠️ **이후 RRF가 기본 전략으로 전환돼 이 서술은 더 이상 현재 동작이 아니다** — 지금 기본값은
  **키워드·벡터 두 arm을 항상 병렬 실행해 RRF로 융합**(`search_strategy: str = "rrf"`,
  `endpoint_candidate_search.py:80`, 클래스 docstring `:69`)이며, 여기 서술된 "0건일 때만 벡터"는
  롤백 스위치인 `fallback` 전략으로 격하됐다(아래 "RRF" 절 및 `docs/07-search-rrf-reevaluation.md` 참조).
- `list_endpoint_chunks()` 가 `select(ApiChunk)` 로 endpoint 청크 **전 행을 적재**(256차원 `embedding` 벡터 컬럼 포함) → `KeywordSearch` 가 Python 레벨에서 **매 질의마다 모든 청크 텍스트를 재토큰화**해 겹침 비율 점수 계산.
- 벡터 검색: pgvector 코사인 거리(`<=>`). **HNSW 인덱스는 이미 존재**(`ix_api_chunk_embedding_hnsw`, `vector_cosine_ops`). `candidate_ids IN (...)` 로 후보를 제한.

### 2) 협업 문서 검색 — `DocumentSearchService`
- 2단계 후보 압축. **1단계**: `document_meta` 제목/URL 을 SQL `ILIKE '%token%'` 로 후보 필터 → Python 점수 계산. **2단계**: 상위 top_k 후보의 본문을 Drive/Notion 에서 **실시간 순차 fetch**(루프)해 스니펫·점수 재계산. 본문은 신선도 때문에 **캐시하지 않음**(설계 결정).

## 개선 방안 (우선순위)

### P1 — 키워드 검색을 Postgres FTS(tsvector + GIN)로 이관 ★핵심 — ✅ 구현완료(커밋 `aa4de84`)
- **근거**: ADR-0002 에 "Keyword Search는 아직 tsvector가 아닌 애플리케이션 레벨 토큰 매칭 유지(TODO)"로 이미 예정된 방향.
- **현 문제**: 매 질의마다 전 청크 적재 + 재토큰화 = O(N) 풀스캔. 문서/청크 수 증가에 선형 악화. 키워드 경로인데 임베딩 벡터까지 전송.
- **효과**: 큼. 인덱스 조회로 전환, 재토큰화·풀스캔 제거.
- **난이도**: 중. `tsvector` 생성 컬럼 + GIN 인덱스 마이그레이션, `KeywordSearch` 를 `to_tsquery` repo 쿼리로 교체.
- **리스크/트레이드오프**: 한글은 PG 기본 형태소 분석이 없어 `simple` config + 현행 정규식 토큰화를 유지해야 함 → 현재 점수식(토큰 겹침 비율)과 `ts_rank` 순위가 미세하게 달라질 수 있음. 순위 회귀 테스트 필요.

### P2 — 키워드 경로에서 embedding 컬럼 로딩 제거 (quick win) — ✅ 구현완료(커밋 `054fbfd`, 벡터 경로 ref_id 프로젝션은 RRF 커밋 `33d1dbe`로 완성)
- **현 문제**: `list_endpoint_chunks()` 가 256차원 벡터까지 전 행 적재. 키워드 점수엔 `id/text/ref_id` 만 필요.
- **효과**: 중(청크 많을수록 커짐, 대역폭·메모리 절감). **난이도**: 낮음(`load_only`/컬럼 프로젝션). **리스크**: 낮음.
- P1 을 하면 상당 부분 흡수되지만, P1 전 즉시 적용 가능한 독립 개선.

### P3 — 문서 검색 2단계 본문 fetch 병렬화 ★문서 검색 지연 핵심 — ✅ 구현완료(커밋 `78e0851`, `MAX_CONCURRENT_BODY_FETCHES=5` 상한)
- **현 문제**: top_k 개 외부(Drive/Notion) fetch 를 순차 루프 → 지연이 **합산**됨(top_k=5면 5회 왕복 직렬).
- **효과**: 큼. 벽시계 지연이 sum → max 로. **난이도**: 중(ThreadPool/async, 개별 실패 격리 유지).
- **리스크/트레이드오프**: 동시 요청이 rate limit 을 칠 수 있음 → **동시성 상한** 필수. 에러 격리(한 건 실패가 전체를 죽이지 않음) 현행 동작 보존.
- **후속 수정(커밋 `3d8297a`)**: 2단계로 넘기는 후보 수를 top_k 그대로 쓰면 title_score만으로 조기 컷돼
  본문에서만 강하게 매칭되는 문서가 fetch 기회조차 못 받는 결함이 있었다. `_body_fetch_budget(top_k,
  candidate_count)`(`document_search_service.py:52-76`)가 top_k보다 넓은 **fetch 예산**
  (`overscan = min(top_k*3, 20)`, `budget = min(max(top_k, overscan), candidate_count)`)을 계산해
  1단계 컷을 대신하고, 최종 top_k 컷은 본문 점수까지 반영한 2단계 뒤로 미뤄졌다(`docs/11-collab-docs-search-fixes.md`
  항목 2 참조). P3의 병렬화 자체(동시성 상한 5)는 그대로다.

### P4 — document_meta 1단계 ILIKE를 pg_trgm GIN 인덱스로 — ✅ 구현완료(커밋 `21522e8`, `ix_document_meta_title_trgm`·`ix_document_meta_url_trgm` `gin_trgm_ops`)
- **현 문제**: `title/url ILIKE '%token%'` 선행 와일드카드 → 인덱스 미사용, seq scan.
- **효과**: 중(메타 캐시 규모 커질 때). **난이도**: 낮음(`pg_trgm` 확장 + GIN 인덱스). **리스크**: 낮음. `collapse`(공백 제거) 패턴 매칭도 함께 인덱싱하려면 표현식 인덱스 추가 검토.

### P5 — 쿼리 임베딩 LRU 캐시 — ✅ 구현완료(커밋 `3ca1aa1`, `LocalEmbeddingProvider` `functools.lru_cache`)
- **현 문제**: 벡터 경로마다 `embed([query])` 외부 API 호출.
- **효과**: 낮음~중(반복 질의에서 지연·비용 절감). 단 벡터는 fallback 경로라 호출 빈도 낮음. **난이도**: 낮음. **리스크**: 낮음.

### P6 — HNSW `ef_search` 튜닝 / `candidate IN` 후처리 재검토 — ✅ 구현완료(커밋 `aae5728`, `search_by_vector`에 `SET LOCAL hnsw.ef_search = max(100, top_k)`; `docs/09-search-quality-post-rrf.md` P5와 동일 건)
- HNSW 인덱스는 존재하나 `hnsw.ef_search` 세션 파라미터 미설정(기본 recall). `candidate_ids IN (...)` 는 후보가 많으면 ANN 순회 후 post-filter 로 recall 저하 가능.
- **효과**: 낮음(현재 벡터가 fallback 이라 영향 제한적). **난이도**: 낮음. **리스크**: recall↔속도 트레이드오프.

## 상위(아키텍처) 제안 — 진짜 하이브리드(RRF) 통합 — ✅ 구현완료(커밋 `33d1dbe`, 실측은 `docs/07-search-rrf-reevaluation.md` 6절)
- 현 엔드포인트 검색은 "키워드 0건일 때만 벡터" = **OR-fallback** 이지, ADR-0002 가 말한 가중합/융합 하이브리드가 아니다. 키워드가 1건이라도 나오면 벡터를 아예 안 써서 **의미 검색 이점을 상실**한다.
- Reciprocal Rank Fusion(RRF)으로 키워드·벡터 순위를 **항상 융합**하면 관련성(품질) 개선 여지.
- **트레이드오프**: 매 질의 임베딩 비용 발생. 현 설계는 비용 절감을 위해 의도적으로 회피(SPEC 결정). **품질 vs 비용**은 제품 판단이라 lead 결정 필요.

## 권장 착수 순서
1. **P2 + P3** (낮은 리스크, 즉효): 키워드 경로 벡터 컬럼 제거 + 문서 fetch 병렬화.
2. **P1** (구조 개선, ADR 예정 항목): 키워드 FTS 이관.
3. **P4** (규모 대비): 메타 trgm 인덱스.
4. **P5/P6/RRF**: 데이터·비용 판단 후 선택 적용.
