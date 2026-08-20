# 검색 성능 개선 방안 — 라운드 2 (P1~P6 이후 잔여 병목)

- 상태: Quick win(Q1~Q3) **구현 완료** (6절 참조) · 구조적 개선(S1~S3)은 **분석/제안 only**(코드 미수정)
- 일시: 2026-08-11
- 작성: architect
- 선행 문서: `docs/architect-review/03_search_performance_improvements.md`(P1~P6·RRF 전부 구현완료), `docs/search-flow.md`(현재 흐름)
- 대상 코드: `app/services/search/endpoint_candidate_search.py`, `app/services/documents/document_search_service.py`,
  `app/repositories/chunk_repository.py`, `app/repositories/document_meta_repository.py`,
  `app/repositories/endpoint_repository.py`, `app/services/search/vector_search.py`

> P1~P6(FTS 이관, embedding 컬럼 defer, 본문 fetch 병렬화, meta trgm 인덱스, 쿼리 임베딩 LRU,
> HNSW ef_search) + RRF 융합은 이미 반영됨. 이 문서는 **그 이후에도 코드에 남아 있는 병목**만 다룬다.

---

## 1. 현재 파이프라인 (단계 요약)

### A. 엔드포인트 검색 (`EndpointCandidateSearch._search_rrf`)
1. `has_endpoint_chunks` EXISTS 체크 (가벼움)
2. 후보 폭 `width = max(top_k*4, 50)`
3. **키워드 arm** — FTS(`to_tsquery`+`ts_rank`, `text_tsv` GIN) — DB 왕복 1회
4. **벡터 arm** — (a) `list_endpoint_chunk_ids`로 스코프 내 청크 ID 전체 로드 → (b) `embed_query`(로컬 e5-small CPU 추론) → (c) `search_by_vector`(HNSW `<=>`, `id IN (...)`)
5. `reciprocal_rank_fuse` (in-memory)
6. `_to_candidates_from_fused` — 결과당 `endpoint_repo.get(ref_id)` DB 왕복

### B. 협업문서 검색 (`DocumentSearchService`)
1. `_require_configured` — resolver 호출
2. 1단계 `search_by_tokens` — ILIKE(trgm GIN) 후보 필터, Python 채점, fetch 예산 컷
3. 2단계 `_rank_with_body` — project별 resolve → `ThreadPoolExecutor(≤5)` 본문 병렬 fetch → 결합 점수 top_k 컷

---

## 2. 잔여 병목 (근거·영향)

### B1. RRF 두 arm이 **직렬 실행** — 엔드포인트 검색 latency 핵심 ★
- **근거**: `_search_rrf`(`endpoint_candidate_search.py:144`)가 키워드 arm 완료 후 벡터 arm을 **순차** 호출한다.
  문서·docstring은 "항상 병렬 실행"이라 서술하지만 **실제 코드는 직렬**이다. 총 지연 =
  `keyword_db + embed_inference + vector_db` 합산.
- **지배 비용**: 로컬 `multilingual-e5-small` CPU 추론(`embed_query`)이 세 항 중 가장 크다. LRU 캐시는
  **동일 질의 반복**에만 듣고, 신규 질의는 매번 추론 비용을 전부 문다(RRF는 벡터 arm을 항상 켠다).
- **영향**: 질의당 수십~수백 ms 고정 오버헤드. 캐시 미스가 대부분인 실사용에서 체감.

### B2. 벡터 arm의 `candidate_ids IN (...)` — 스코프 없을 때 순수 낭비 ★
- **근거**: `_search_rrf`가 `list_endpoint_chunk_ids`로 스코프 내 endpoint 청크 ID를 **Python set으로 전량 로드**한 뒤
  `search_by_vector`에 `ApiChunk.id.in_(candidate_ids)`로 넘긴다(`chunk_repository.py:271`). `search_by_vector`는
  `chunk_type='endpoint'` 조건을 **SQL에 갖고 있지 않아**, 이 거대한 IN 리스트가 "endpoint만 남기는 필터"를 겸한다.
- **문제**: document_id/project 스코프가 **없는 전역 검색**에서는 candidate_ids = 전체 endpoint 청크 →
  (1) 전 ID를 앱 메모리로 왕복, (2) 수천~수만 원소 `IN (...)` 파라미터 바인딩, (3) HNSW ANN 순회 후
  **post-filter**라 넓은 후보에서 recall 저하(P6 `ef_search` 상향으로 완화했으나 근본 원인은 남음).
- **영향**: 코퍼스가 커질수록 벡터 arm 자체가 무거워짐. 스코프가 좁을 때는 IN이 유효하지만, 전역/광범위 스코프에서 역효과.

### B3. `get_document`의 `_find_meta_row` — 포인트 조회를 O(N) 풀로드로 (quick win) ★
- **근거**: `_find_meta_row`(`document_search_service.py:234`)가 `meta_repo.list_all(source=source)`로 **그 source의
  메타 행 전체를 적재**한 뒤 Python 리스트 컴프리헨션으로 `external_id` 일치를 거른다.
- **문제**: `(source, external_id)`는 사실상 키 조회인데 전 행을 앱으로 끌어온다. 메타 캐시가 커질수록 `get_document`가 선형 악화.
- **개선**: repo에 `WHERE source=? AND external_id=? ORDER BY last_synced_at DESC LIMIT 1` 전용 메서드 추가.
  (검색 자체는 아니나 `search_documents`와 짝을 이루는 조회 경로라 함께 다룸.)

### B4. `endpoint_repo.get(ref_id)` 결과당 반복 — 경미한 N+1
- **근거**: `_to_candidates_from_fused`/`_to_candidates`가 결과 ref_id마다 `session.get(ApiEndpoint, id)` 호출.
- **영향**: top_k(≤50)만큼 개별 조회. `session.get`은 identity map 캐시가 있어 대개 가볍지만, 콜드 상태에선 N 왕복.
  `WHERE id IN (ref_ids)` 배치 1회로 접을 수 있음. **우선순위 낮음**(top_k 작음).

### B5. resolver 중복 호출 — 경미
- **근거**: `search()`에서 `_require_configured`가 resolve한 뒤, `_rank_with_body`가 project별 `resolve_for_project`를
  다시 호출. 결과 재사용 없음.
- **영향**: resolve 비용이 작으면 무시 가능. resolve가 DB/설정을 읽으면 사소한 중복. **우선순위 낮음.**

---

## 3. 개선 방안 (우선순위)

### Quick win (낮은 리스크, 즉효)
| # | 항목 | 대상 | 효과 | 난이도 | 리스크 |
|---|------|------|------|--------|--------|
| **Q1** | **B3**: `_find_meta_row`를 인덱스 포인트 조회로 교체 | `document_meta_repository`에 전용 조회 메서드 + `_find_meta_row` | 중(메타 규모↑) | 낮음 | 낮음. `(source, external_id)` 인덱스 유무만 확인 |
| **Q2** | **B2 부분**: `search_by_vector`에 `chunk_type='endpoint'` SQL 조건 추가하고, 전역 스코프면 `candidate_ids=None` 전달 | `chunk_repository.search_by_vector`, `endpoint_candidate_search._search_rrf` | 중~큼(전역 검색) | 낮음 | 낮음. 스코프 좁을 땐 기존 IN 유지 |
| **Q3** | **B4**: `endpoint_repo`에 `get_many(ids)` 배치 조회 추가 | `endpoint_repository`, `_to_candidates*` | 낮음 | 낮음 | 낮음 |

### 구조적 개선 (설계 판단 필요)
| # | 항목 | 내용 | 효과 | 난이도 | 리스크/트레이드오프 |
|---|------|------|------|--------|---------------------|
| **S1** | **B1**: RRF arm 부분 병렬화 | 벡터 arm의 **임베딩 추론(순수 CPU, Session 무관)** 을 워커 스레드로 띄워 키워드 FTS DB 왕복과 **겹친다**. 임베딩 완료 후 벡터 DB 쿼리는 기존 Session에서 순차 수행. 총 지연 ≈ `max(embed_infer, keyword_db) + vector_db` | 큼(지배 비용을 겹침) | 중 | **Session 스레드 세이프 아님** → DB 왕복은 병렬화 금지, 오직 임베딩 추론만 오프로드. 스레드 1개면 GIL 영향 적음(추론은 C 확장에서 GIL 해제) |
| **S2** | **B2 근본**: 벡터 스코프를 IN이 아닌 JOIN으로 | `search_by_vector`가 `ApiDocument` JOIN + `chunk_type='endpoint'` 조건을 직접 걸어 스코프 필터를 SQL로 내림. `list_endpoint_chunk_ids` 왕복 제거 | 중~큼 | 중 | HNSW+필터는 여전히 post-filter라 recall 관리 필요(ef_search 유지). 부분 인덱스 검토 여지 |
| **S3** | 문서 서술 정정 | `00-search-flow.md`/`endpoint_candidate_search` docstring의 "항상 병렬 실행" → 현행 직렬 반영 (S1 착수 전이라면) | — | 낮음 | 문서-코드 정합성. **살아있는 문서 규칙상 별건이라도 정정 필요** |

---

## 4. 권장 착수 순서
1. **Q1 + Q2** — 리스크 최저, 규모 확장 대비 즉효(메타 조회 O(N) 제거 + 전역 벡터검색 낭비 제거).
2. **S1** — 엔드포인트 검색 체감 지연의 핵심(임베딩·키워드 겹침). Session 제약을 지키는 **부분** 병렬화라 안전.
3. **S2** — S1 이후 벡터 arm이 병목으로 남으면 착수. HNSW recall 회귀 테스트 동반.
4. **Q3 / B5** — 여유 시 정리(우선순위 낮음).
5. **S3** — S1을 하지 않기로 하면 그 즉시 문서 서술을 직렬로 정정.

## 5. 스코프 밖(의도적 비개선)
- **본문 캐시**: 협업문서 본문은 신선도 때문에 캐시 안 함 — 설계 결정(변경 대상 아님).
- **질의 확장 LLM 호출**: 서버가 자체 LLM로 질의 확장하지 않음 — 판단은 호출측 모델 몫(설계 결정).
- **임베딩 모델 교체/GPU**: 인프라 판단 영역. 본 분석은 코드 레벨 병목만 다룸.

---

## 6. 적용 결과 (Q1~Q3)

- 상태: **구현 완료**(Q1/Q2/Q3), 전체 테스트 641건 통과
- 담당: developer
- 일시: 2026-08-11
- S1/S2/S3(구조적 개선)는 이번 라운드에 포함하지 않음 — 4절 계속 유효.

### 6.1 구현 요약

| # | 변경 |
|---|------|
| **Q1** | `document_meta_repository.find_latest_by_source_and_external_id` 추가 + `(source, external_id)` 복합 인덱스 마이그레이션(`e2bd26b83408`). `document_search_service._find_meta_row`가 `list_all()` 전량로드 대신 이 포인트 조회로 교체됨. |
| **Q2** | `chunk_repository.search_by_vector`에 `chunk_type='endpoint'` SQL 조건 추가. `endpoint_candidate_search._search_rrf`가 전역 스코프(document_id·project 둘 다 None)면 `candidate_ids=None` 전달, 스코프가 좁을 때는 기존 `IN` 목록을 그대로 유지. |
| **Q3** | `endpoint_repository.get_many(ids)` 배치 조회 추가. `_to_candidates`/`_to_candidates_from_fused`가 결과당 `get()` 을 반복하던 N+1 을 배치 조회 한 번으로 대체. |

### 6.2 측정 방법

- 스크립트: `scripts/bench_search_perf.py` (1회성 임시 스크립트가 아니라, 이후 라운드에서도 재사용할 수 있도록 정식 위치에 유지)
- 실행마다 임시 postgres DB(pgvector/pg_trgm 확장 포함)를 새로 만들어 시딩하고, 측정이 끝나면 자동 삭제
- 코퍼스: 문서 40개 × 엔드포인트 15개(endpoint 청크 총 600개), `document_meta` 800행
- 케이스당 20회 반복(워밍업 2회 제외)해 latency 평균(mean)·p95(ms) 산출
- 실행 방법: `uv run python scripts/bench_search_perf.py` — Q1~Q3 구현 전/후 **동일한 스크립트**를 그대로 재실행해 콘솔 출력을 비교(별도 diff 도구 없이 수동 비교)

### 6.3 결과

| 케이스 | before (mean) | after (mean) | 개선율 |
|---|---|---|---|
| `endpoint_search_global`(전역 스코프) | 21.6ms | 8.9ms | 약 59% ↓ |
| `endpoint_search_narrow`(좁은 스코프) | 16.3ms | 9.5ms | 약 42% ↓ |
| `get_document` | 9.8ms | 4.1ms | 약 58% ↓ |

- before는 2회 실행 평균, after는 3회 실행 평균(각 실행값 자체가 케이스당 20회 반복의 mean).
- `endpoint_search_global` 개선폭이 가장 큰 이유는 Q2(전역 스코프에서 `candidate_ids` 전량 로드를 없앤 것)가 정확히 이 경로를 겨냥했기 때문이다.
- `endpoint_search_narrow`도 개선되는 이유는 Q3(N+1 → 배치 조회)가 스코프 좁고 넓음과 무관하게 항상 적용되기 때문이다(narrow는 Q2 대상이 아니라 기존 `IN` 방식을 유지).
- `get_document` 개선은 Q1(메타 800행 전량 스캔 → 인덱스 포인트 조회) 효과다.
- 코퍼스 규모가 작아(600청크/800행) 개선폭이 보수적으로 잡힌 수치다 — 2절 B2·B3에서 지적한 대로 코퍼스가 커질수록 격차는 더 벌어질 것으로 예상된다.
