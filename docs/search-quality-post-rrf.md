# 검색 품질 추가 개선 검토 — RRF 도입 이후 (분석)

- 상태: **P1 구현완료**(2026-08-10, 커밋 `4ff1f5a`·`6c2236a`·`97f3c2d`·`731d43c`, 실행계획 `docs/exec_plans/eval-set-expansion-plan.md`) — **P2~P5는 제안 상태 유지**(lead 판단 대기).
- 일시: 2026-08-10
- 작성: architect
- 관련: `docs/search-rrf-reevaluation.md`(RRF 도입·실측), `docs/search-performance-improvements.md`(P1~P6), `docs/vector-store-qdrant-vs-pgvector.md`
- 대상 코드: `app/services/search/`, `app/models/openapi.py`(text_tsv 식), `app/repositories/chunk_repository.py`, `tests/fixtures/rrf_eval/`

## 출발점(현 상태)

- 엔드포인트 검색 = 키워드 FTS + 벡터를 **RRF 상시 융합**(기본 `rrf`, 롤백용 `fallback` 병존).
- 실측(20질의): **top-1 80%(불변), top-3 recall 80%→95%(개선), 회귀 0.**
- 임베딩: 로컬 CPU `multilingual-e5-small`(384차원), e5 접두사(`query:`/`passage:`) **정상 적용됨**(버그 아님).
- HNSW 코사인 인덱스 존재, `hnsw.ef_search` **미설정(기본 40)**.
- `text_tsv` 는 `to_tsvector('simple', ...)` **평면 단일 가중치**(setweight 없음).

핵심 관찰: **RRF는 top-3 recall은 끌어올렸지만 top-1(80%)은 못 움직였다.** 남은 상방은
(a) top-1 정확도, (b) 아직 측정 안 되는 precision/MRR 이다. 아래 제안은 이 두 축을 겨눈다.

## 개선 방안 (우선순위)

### P1 — 평가셋 확장 + 지표 보강 (★기반, 저비용, 나머지의 전제) — ✅ 구현완료(커밋 `4ff1f5a`·`6c2236a`·`97f3c2d`·`731d43c`, 실측은 아래 'P1 실측 결과' 절)
- **현 문제**: 평가셋 20질의는 **회귀 방어엔 되지만 튜닝·리랭킹 검증엔 통계적으로 얇다**(과적합 위험).
  `compare_strategies.py` 는 **top-1·top-3 recall만** 계산 — 순위 2~3위 내 미세 이동(RRF가 실제로 한 일)을 못 잡는다.
- **개선**: (a) 라벨 질의 20→50~100개로 확장(실패 taxonomy: 동의어·패러프레이즈·한글·경로·흔한토큰·필드희석 균형 배분).
  (b) **MRR·nDCG·precision@k** 추가 — top-1 flat 뒤에 숨은 순위 이동을 정량화.
- **효과**: 간접적이지만 **아래 모든 레버의 개선/회귀 판정 근거**. 없으면 P3·P4가 20질의에 과적합.
- **난이도**: 낮음~중(라벨링 수작업 + 스크립트 지표 몇 줄). **런타임 비용 0.** **리스크**: 낮음.

#### P1 실측 결과 (2026-08-10, developer)

`tests/fixtures/rrf_eval/queries.json` 20→84질의(8개 실패 taxonomy 각 8질의 이상)로 확장,
`compare_strategies.py` 에 Recall@{1,3,5,10}·MRR·nDCG@10 추가 후 `uv run python
tests/fixtures/rrf_eval/compare_strategies.py` 로 재실측(로컬 postgres, `multilingual-e5-small`).

| 지표 | fallback | rrf | 델타 |
|---|---|---|---|
| Recall@1 | 60% | 69% | **+9pt** |
| Recall@3 | 79% | 88% | +9pt |
| Recall@5 | 82% | 88% | +6pt |
| Recall@10 | 89% | 95% | +6pt |
| MRR | 0.704 | 0.788 | +0.084 |
| nDCG@10 | 0.749 | 0.827 | +0.078 |

20질의 실측(top-1 80% 불변)과 달리 **84질의 확장셋에서는 top-1(Recall@1)도 60%→69%로 개선**됐다
— 이전 결론("RRF는 top-3만 움직이고 top-1은 못 움직인다")은 **20질의 표본 편향**이었다.
확장셋 기준 RRF는 모든 지표에서 fallback을 상회한다.

회귀(rrf가 fallback보다 나빠진 케이스)는 2건, 둘 다 top-1→top-2 수준의 경미한 하락:
- `PATCH /users/{userId} 호출법`: 1위→2위
- `orders 목록 엔드포인트`: 1위→2위

카테고리별로는 "동의어/패러프레이즈-한글"(MRR 0.429→0.525)과 "흔한 토큰 오탐 범람"
(MRR 0.552→0.812)에서 개선폭이 가장 컸다. "교차언어" 카테고리는 fallback·rrf 모두 낮음
(Recall@3 50%, MRR ~0.3~0.5) — P3(리랭킹) 착수 시 우선 검증 대상 후보.

**시사점**: top-1 상방이 이미 RRF로 상당 부분 닫혔으므로, P3(cross-encoder 리랭킹) 착수 여부는
이 확장 실측치를 기준으로 재판단 필요(20질의 기준 "recall-top1 격차 크다"는 전제가 약해짐).
P2(필드 가중 tsvector)는 "필드 희석" 카테고리가 이미 88~100%로 양호해 시급성이 낮아졌을 수 있음
— 두 항목 모두 착수 전 architect 재검토 권장.

### P2 — 필드 가중 tsvector(`setweight`) (★키워드 arm 저비용 고효율)
- **현 문제**: `text_tsv` 가 summary·path·description·params·responses 를 **동일 가중**으로 뭉친다.
  → `docs/search-rrf-reevaluation.md` 1절이 지목한 **"필드 희석"**(질의 토큰이 파라미터 이름에 매칭돼 무관 엔드포인트 히트)의 직접 원인. `ts_rank` 가 요약/경로 매칭과 파라미터 매칭을 구분 못 한다.
- **개선**: `TEXT_TSV_EXPRESSION` 을 필드별 `setweight` 로 재구성 — A=summary+path, B=description, C=tags, D=params+responses. `ts_rank(weights, tsv, query)` 로 요약/경로 매칭을 상위로.
- **효과**: 중~큼. 필드 희석 오탐 억제 → **키워드 arm 정밀도↑ = top-1 직접 개선 여지**(RRF가 못 움직인 지표).
- **난이도**: 중(생성 컬럼식 변경 → text_tsv 재생성 마이그레이션, 모델·alembic 식 동기화, ts_rank 호출에 weights 전달). **리스크**: 중(순위 골든 기대값 갱신 필요, 회귀 재검증). Postgres 네이티브라 신규 의존성 0.

### P3 — Cross-encoder 리랭킹 (top-1 최대 레버, 고비용·조건부)
- **현 문제**: RRF는 두 arm의 **등수만** 융합 → 질의-후보 **상호작용**(교차 주의)을 못 본다. top-1이 80%에 갇힌 주 원인. 동의어/패러프레이즈에서 두 arm이 각자 약하면 융합도 약하다.
- **개선**: RRF 융합 top-N(예: 20)을 경량 cross-encoder(예: `bge-reranker-base`, ms-marco MiniLM)로 재정렬 → top_k 컷. 질의·후보 텍스트 쌍을 직접 채점.
- **효과**: 큼(top-1·precision@k 겨냥). 리랭킹은 recall이 이미 담은 정답을 **상위로 재배치**하는 데 최강 — P1에서 95% recall인데 top-1 80%면 **정확히 리랭커가 메우는 격차**.
- **난이도**: 중~높음. **비용(제동)**: (a) 신규 모델 의존성·메모리(수백MB), (b) CPU 리랭킹 지연(후보 20건 쌍 채점 ≈ 수십~수백ms — 현재 벡터 arm 13.7ms 대비 지연 델타 큼), (c) 해시 폴백 배포 게이팅 필요(벡터와 동일).
- **조건**: P1 확장 평가셋에서 **top-1 상방(recall-top1 격차)이 실측**되고 **지연 예산이 허용**될 때 착수. 측정 없이 먼저 도입 금지.

### P4 — RRF `K`/후보폭 `N` 스윕 (저비용 실험, 상방 제한)
- **현 상태**: `RRF_K=60`(표준), `N=max(top_k*4,50)` 하드코딩. 도입 시 "평가셋 없어 튜닝 무의미"로 고정했으나 **이제 평가셋 존재**.
- **개선**: `compare_strategies.py` 로 K∈{10,20,40,60,80}·N 스윕 → 지표 최적점 확인.
- **효과**: 낮음~중(K는 대체로 둔감, 상방 제한적). **난이도**: 낮음(파라미터 루프). **리스크**: **20질의 과적합** — 반드시 **P1 확장 후** 실행. 개선폭 미미하면 K=60 유지(설정 표면 안 늘림, YAGNI).

### P5 — HNSW `ef_search` GUC + 스코프 필터 over-filtering 점검 (규모 의존, 저비용)
- **현 문제**: `hnsw.ef_search` 세션 미설정(기본 40=낮은 recall). 또한 벡터 검색이 `candidate_ids IN (...)`(스코프 내 endpoint 청크)로 **post-filter** — HNSW가 ef_search개 후보를 먼저 뽑고 걸러서, 다문서 프로젝트에서 단일 문서로 좁히면 top_k 미만 반환(recall 저하) 가능.
- **개선**: `SET LOCAL hnsw.ef_search=100`(융합용 넓은 N에 맞춰), pgvector 0.8+면 `hnsw.iterative_scan` 로 over-filtering 해소 검토.
- **효과**: **현 규모(단일 문서·수백 청크)에선 낮음**(HNSW가 seq scan으로 폴백할 만큼 작음). **다문서/대규모 시 중.** **난이도**: 낮음. **리스크**: recall↔속도 트레이드오프. 지금은 "인지하고 규모 커지면 착수".

## 하지 않기를 권함 — LLM 기반 쿼리 확장
- 동의어/약어 확장은 매력적으로 보이나 **서버가 별도 LLM으로 질의를 재작성하는 것은 이 프로젝트 원칙(판단은 클라 LLM에 위임)에 반한다.** 자연어 이해·동의어 처리는 (a) 이미 호출측 LLM의 몫이고 (b) 벡터 arm의 강점 영역이다.
- 정적 동의어/약어 사전(비-LLM)은 대안이지만 **한/영 이중 사전 유지비가 크고 취약** — ROI 낮음. **벡터 arm 강화(P3 리랭킹)로 동의어를 흡수**하는 편이 옳다.

## 권장 착수 순서

1. **P1**(평가셋 확장 + MRR/nDCG) — 모든 후속 판정의 계측 기반. 먼저.
2. **P2**(필드 가중 tsvector) — 네이티브·저비용, 필드 희석 직격, top-1 개선 여지. P1 지표로 검증.
3. **P3**(cross-encoder 리랭킹) — top-1 최대 레버지만 지연·의존성 비용. **P1에서 recall-top1 격차·지연예산 확인 후 조건부.**
4. **P4**(K/N 스윕) — P1 확장 후 저비용 실험. 상방 미미하면 현행 유지.
5. **P5**(HNSW ef_search/iterative) — 규모 커질 때. 지금은 인지만.

## 한 줄 요약
RRF가 top-3 recall(95%)은 채웠으나 **top-1(80%)이 남은 상방**이다. 이를 겨누는 순서는
**평가셋·지표부터 세우고(P1) → 저비용 네이티브 개선(P2 필드가중) → 그다음 리랭킹(P3, 조건부)**.
LLM 쿼리확장은 원칙 위배로 배제, 동의어는 리랭킹으로 흡수한다.
