# endpoint-representation arm — feature-ON legacy-20 diagnostic + backfill 단위검증 2026-09-01

- 지시: `docs/architect-review/101_deterministic_endpoint_representation_candidate_generator_design.md` §6 (사전 HARD gate) + §7.5 (diagnostic harness). `docs/architect-review/102` 정정(non-semantic strict-empty) 반영본.
- 대상: HEAD `bf828bc` (`feat(search): deterministic endpoint-representation candidate arm (flag-off dark candidate)`). 구현·단위 테스트 커밋됨 — `app/services/search/endpoint_representation_search.py`, `app/services/search/endpoint_candidate_search.py`, `app/repositories/endpoint_projection_repository.py`, `app/services/indexer/endpoint_projection.py`, `app/services/indexer/indexer_service.py`, `app/scripts/backfill_endpoint_projection.py`, alembic `d5f1a3c8b920`, `app/composition.py`, `app/core/config.py`. `DOCS_MCP_SEARCH_ENDPOINT_REPRESENTATION_ENABLED` 로 on/off. 코드/`pyproject.toml`/`uv.lock` 변경 없음.
- 기준 문서: `docs/eval-results/09_2026-08-31_corpus_eval.md` (레거시 20건, miss 11건), `docs/eval-results/14_2026-08-31_p0_reaudit_arm_wide_final_coordinates.md` (좌표 기준).
- 코퍼스 content_sha256: stripe=`3653ad45bbec…`, github=`80850db290cd…` (09/14/15 와 동일 — 동결 코퍼스 2건).
- 질의: `tests/fixtures/corpus_eval/queries.json` (레거시 20건, split 없음).
- 임베딩(색인 + projection dense + 질의): intfloat/multilingual-e5-small (dim 384) / `is_semantic: True` / `with_variants: False`. projection dense 표현형은 §2.1 대로 canonical_text 로 생성.
- 결정성: **PASS** — on / on_2 / on_3 3회 재실행에서 final top-10 (method/path/match_type), 정답 rank, repr arm trace (fts_rank/vector_rank/merged_rank/winning_source) 완전 일치.
- 실행 박스: WSL2, 논리 CPU 8, RAM 7.6 GiB. postgres 는 `docs-mcp-postgres-1` (pgvector pg16) healthy. eval 은 `_make_temp_db` 로 매 실행 임시 DB 를 새로 만들어 동결 코퍼스를 색인.

## 범위 한정 (중요)

> **sealed split NOT RUN — promotion 불가. 본 문서는 §6 HARD gate + legacy-20 diagnostic + §5.1 backfill 단위검증 한정이다.**
> sealed split 의 질의·accepted·strata·manifest 저작·동결·개봉은 architect/lead 범위이며 이번 작업에서 수행하지 않았다.
> developer 는 사실만 기록한다 — 효과성 PASS 판정도 promotion 권고도 내리지 않는다. **promotion 판정 = architect 몫.**

- 본 문서가 기록하는 것: §6 HARD gate 전항(OFF parity / 결정성 / source coverage / both-arm slot 보존 / C1·route-pair / attribution / isolation / performance), legacy-20 off↔on diagnostic 대조, §4 표적 7건 + saturation 4건 좌표, §5.1 backfill 단위검증(건수·소요·실패·digest).
- **`hard_pass = true`** (아래 §3).

---

## 1. §5.1 backfill 단위검증

`scratchpad/backfill_endpoint_repr_exercise.py` (커밋 안 함). 임시 DB 에 동결 코퍼스 2건을 색인(색인 경로가 projection 을 inline 생성)한 뒤 `app.scripts.backfill_endpoint_projection.backfill_endpoint_projection` 을 두 번 돌린다.

| 단계 | 건수 | 소요 | 실패 | dense_nonnull | audit digest |
| --- | ---: | ---: | ---: | ---: | --- |
| inline 색인 (색인 경로가 생성) | 1809 | 214.6–226.4 s | 0 | 1809 | `d4ac9b82e364…` |
| Phase A — 기존 projection 위에 재실행 (idempotent 재생성, dense 재임베드) | 1809 | 103.4 s | 0 | 1809 | `d4ac9b82e364…` |
| Phase B — projection 전량 삭제(1809행) 후 재빌드 (마이그레이션 직후 시나리오) | 1809 | 95.8 s | 0 | 1809 | `d4ac9b82e364…` |

- 문서별 내역: github(`46ddea31…`) 589행, stripe(`d6183a05…`) 1220행 = 2건 합계 1809행. 엔드포인트 없는 문서 없음.
- `is_semantic = True` 이므로 §5.2 대로 endpoint 당 dense vector 1개 생성 — dense_nonnull 이 전 단계에서 projection 행 수와 일치(1809/1809).
- inline / Phase A / Phase B **digest 3자 동일** → `(document_id, method, path, representation_version, source_hash)` 집계가 재생성·전삭제후재빌드에도 불변. `representation_version` 은 전행 `v1`.
- 실패 0: 두 phase 모두 예외 없이 완주, 문서 단위 커밋(§6 원자성)으로 반쪽 projection 없음.

> backfill digest(`d4ac9b82…`)와 §2 eval harness 의 `audit_digest`(`1a043beef00e…`)는 다르다. 둘은 별개 임시 DB 라 `document_id` 네임스페이스가 달라 집계 해시가 갈린다. 결정성 판정은 `source_hash`/`representation_version`/rank-merge/final-order 로 하며, 그 축은 각 harness 내부에서 불변(§3 결정성 PASS, backfill digest 3자 동일).

---

## 2. 방법 (eval)

`scratchpad/eval_endpoint_repr.py` (커밋 안 함). `run_corpus_eval` 헬퍼(`_load_manifest` / `_load_corpus_texts` / `_load_and_validate_queries` / `_make_temp_db` / `_drop_temp_db`)로 동결 코퍼스를 임시 DB 에 색인하고, 같은 `AppState` 에서 flag 만 바꿔 두 `EndpointCandidateSearch` 번들을 만들어 arm 을 돈다.

| arm | 설정 |
| --- | --- |
| `off` | `search_endpoint_representation_enabled="false"` — 09 기준선 재현, parity 확인 |
| `on` | `search_endpoint_representation_enabled="true"`, flag ON |
| `on_2`, `on_3` | `on` 재실행 (결정성) |
| `fallback` | flag ON + `search_strategy="fallback"` — arm 비호출 격리 확인 |

- 계측: `reciprocal_rank_fuse`, `_lock_both_slots`, `EndpointRepresentationSearch.search` 를 read-only 래핑해 질의별 `legacy_base_wide[:50]`·`tentative_wide[:50]` (ref_id, match_type), lock 입력(top_k / locked_slots / tentative_order)·출력(final_ids), repr arm trace(endpoint_id, fts_rank, vector_rank, merged_rank, winning_source, dense_enabled)를 기록. 반환값은 변형하지 않음 — OFF parity + 결정성으로 계측 무해 확인.
- 질의당 `cs.search(eq.query, CandidateSearchOptions(top_k=10))` 정확히 1회. warm 지연은 앞 3건(cold) 제외.
- config dump: `search_strategy=rrf`, `repr_arm_width=50`, `arm_rescue_quota=0` (P2 배타), `cross_encoder_enabled=false` (P3 배타).

---

## 3. §6 HARD gate

| gate | 결과 | 근거 |
| --- | --- | --- |
| OFF parity vs 09 | **PASS** | recall@1/3/10 = 0.250/0.350/0.450, MRR 0.318, nDCG@10 0.350 — 09 published headline 과 |Δ| < 5e-3 (전부 정확히 일치) |
| legacy base_wide parity (OFF fuse == ON 이 재계산한 legacy fuse) | **PASS** | 20/20 질의 `legacy_base_wide[:50]` (ref_id, match_type) 동일 — mismatch [] |
| 결정성 on == on_2 == on_3 | **PASS** | final (method,path) mismatch [], rank mismatch [], repr arm trace mismatch [] |
| source coverage | **PASS** | endpoint 1809 / projection 행 1809 / projection endpoint 1809, missing [] · orphan [] · duplicate [] · non-v1 [] |
| both-arm slot 보존 | **PASS** | OFF 의 `both` 참조가 ON final 에서 동일 slot·순서·`both` 유지 — violation [] |
| q08/q09/q11/q12 byte-identical | **PASS** | 4건 전부 final 리스트 baseline 과 완전 일치 (repr arm 이 후보를 반환해도 legacy both-slot HARD lock) |
| C1 직접키워드 gross hit loss | **PASS** | baseline top-10 → miss 전락 0건 |
| baseline top-10 accepted 유지 (regressed_accepted) | **PASS** | top-10 밖으로 탈락한 정답 0건 |
| route-pair 비회귀 | **PASS** | `capped(on) > capped(off)` 인 (route family, qid) 0건. route family: `/v1/customers`{q01,q04,q11}, `/repos/{owner}`{q02,q06,q07,q09,q12,q15,q17,q20}, `/v1/refunds`{q05,q16}, `/v1/subscriptions`{q08,q13,q16} |
| C6 커버리지 count 비회귀 | **PASS** | 질의별 cov@10 감소 0건 |
| per-category R@10-hit / MRR 비회귀 | **PASS** | 카테고리별 hit·MRR 감소 0건 (category_regress []) |
| attribution 기록 | **PASS** | qid별 legacy kw/vec wide rank, repr fts/vector/merged rank, winning source, 3-arm RRF rank, lock 사유 남김 (§4 표) |
| isolation | **PASS** | `arm_rescue_quota=0`, `cross_encoder_reranker is None`, fallback arm 호출 0, on arm 호출 20 (= 질의 수) |
| performance | **PASS (기록)** | 아래 §5 |

**hard_pass = true.** §6 전 gate 통과.

---

## 4. legacy-20 diagnostic — off vs on

| 지표 | off (=09) | on | Δ |
| --- | ---: | ---: | ---: |
| recall@1 | 0.250 | 0.250 | 0 |
| recall@3 | 0.350 | 0.400 | +0.050 |
| recall@10 | 0.450 | 0.550 | +0.100 |
| MRR | 0.318 | 0.338 | +0.020 |
| nDCG@10 | 0.350 | 0.388 | +0.038 |
| answer_miss@10 | 11 | 9 | −2 |

- on / on_2 / on_3 지표 완전 동일.
- recall@10 +0.100 = 정답 2건(q06·q17) 신규 회복. recall@1 무변동 — 상위 강등 없음.

### 4.1 질의별 rank (off → on)

`None` = final top-10 밖.

| # | 카테고리 | 정답 수 | off | on | 비고 |
| --- | --- | ---: | ---: | ---: | --- |
| q01 | C1-직접키워드 | 1 | 1 | 1 | |
| q02 | C1-직접키워드 | 1 | 1 | 1 | |
| q03 | C1-직접키워드 | 1 | 1 | 1 | |
| q04 | C2-한글패러프레이즈 | 1 | None | None | generation miss — repr arm 미진입 |
| q05 | C2-한글패러프레이즈 | 1 | None | None | repr arm 진입(vec 35), 3-arm RRF 29 — final 미도달 |
| q06 | C2-한글패러프레이즈 | 1 | None | **9** | **회복** (표적) |
| q07 | C2-한글패러프레이즈 | 1 | None | None | generation miss — repr arm 미진입 |
| q08 | C3-영문의역 | 1 | None | None | HARD lock (final 전부 both) — byte-identical |
| q09 | C3-영문의역 | 1 | None | None | HARD lock — byte-identical |
| q10 | C3-영문의역 | 1 | None | None | generation miss — repr arm 미진입 |
| q11 | C4-흔한토큰범람 | 1 | None | None | HARD lock — byte-identical |
| q12 | C4-흔한토큰범람 | 1 | None | None | HARD lock — byte-identical |
| q13 | C5-decoy구분 | 1 | 6 | 6 | |
| q14 | C5-decoy구분 | 1 | 1 | 1 | |
| q15 | C5-decoy구분 | 1 | 2 | 2 | |
| q16 | C6-다개념(복수정답) | 2 | 1 | 1 | 무변동 (repr vec 1) — 강등 없음 |
| q17 | C6-다개념(복수정답) | 2 | None | **7** | **회복** (표적) |
| q18 | C7-대형엔드포인트세부 | 1 | None | None | repr arm 미진입 |
| q19 | C7-대형엔드포인트세부 | 1 | 5 | **3** | 개선 (표적 외) |
| q20 | C7-대형엔드포인트세부 | 1 | 2 | 2 | |

- C1(직접키워드) q01–q03 rank 1 유지 — 직접키워드 경로 무손상.
- C5(decoy) q13/q14/q15 무변동 — decoy 오염 없음.
- q16(회귀 guard) 무변동 — 강등 없음.

### 4.2 §4 표적 7건 좌표

| qid | 정답 | legacy wide rank(mt) | repr fts | repr vec | repr merged | 3-arm RRF | final off→on | lock 사유 | 판정 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| q04 | `POST /v1/customers` | None | None | None | None | None | None→None | not_in_final | **미회복** — repr arm 미진입 |
| q05 | `POST /v1/refunds` | 35 (vector) | None | 35 | 35 | 29 | None→None | not_in_final | **미회복** — arm 진입·기여(35→29)했으나 final 미도달 |
| q06 | `POST /repos/{owner}/{repo}/issues` | 16 (vector) | None | 8 | 8 | 9 | None→**9** | fill_from_tentative | **회복** |
| q07 | `DELETE /repos/{owner}/{repo}` | None | None | None | None | None | None→None | not_in_final | **미회복** — repr arm 미진입 |
| q10 | `GET /v1/invoices` | None | None | None | None | None | None→None | not_in_final | **미회복** — repr arm 미진입 |
| q17 | `GET`/`POST /repos/{owner}/{repo}/issues` | 12 (vector) | None | 2 | 2 | 7 | None→**7** | fill_from_tentative | **회복** |
| q18 | `POST /v1/charges` | 42 (vector) | None | None | None | None | None→None | not_in_final | **미회복** — repr arm 미진입 |

- **회복 2/7**: q06, q17. 둘 다 repr canonical vector 가 legacy vector wide rank(16, 12)보다 얕은 rank(8, 2)로 arm 에 진입, 3-arm RRF 에서 top-10 안(9, 7)으로 밀어올림. lock 밖 순열(`fill_from_tentative`).
- **arm 진입·final 미도달 1/7**: q05. canonical vector rank 35 로 진입, 3-arm RRF 를 35→29 로 개선했으나 top-10 밖. 설계 §4 의 "canonical vector 가 현 vector rank 35보다 얕은 rank로 들어와 3-arm RRF 에 기여" 는 관측됨(기여 O), 다만 회복에는 미달.
- **repr arm 미진입 4/7**: q04, q07, q10, q18. `repr_dense_enabled=true` 이나 canonical vector top-50 에 정답 endpoint 없음. 설계 §2.3·§4 가 명시한 미보장 구간 — 한국어 action-resource↔영어 root 정렬 실패(q04/q07), `billing history → invoices` 는 deterministic rule 아님(q10), currency 세부조건 무시(q18).
- FTS 기여: 전 표적에서 `repr_fts_rank = None`. 한국어 원질의에 영문 canonical FTS 는 비어 있음 — 설계 §2.3·verdict 102 예측대로. 회복은 전부 canonical vector 경로.

### 4.3 saturation q08/q09/q11/q12 — 상한 0 확인

| qid | 정답 | repr vec | repr merged | 3-arm RRF | final off→on | byte-identical |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| q08 | `DELETE /v1/subscriptions/{…}` | 11 | 18 | 27 | None→None | **yes** |
| q09 | `DELETE /repos/{owner}/{repo}` | 15 | 25 | 24 | None→None | **yes** |
| q11 | `GET /v1/customers` | 22 | 36 | 38 | None→None | **yes** |
| q12 | `GET /repos/{owner}/{repo}/pulls` | 4 | 7 | 33 | None→None | **yes** |

- 4건 모두 repr arm 은 정답 후보를 **반환**(merged rank 7–36)하나, legacy final 10 slot 이 전부 `both` → §3.3 HARD lock → final byte-identical. **회복 상한 0 확인**, 설계 효과 상한(§1·§4)과 일치. 숨기지 않음.

---

## 5. §6 performance / 자원

warm = 앞 3건 제외한 17건. 단위 ms.

| 항목 | 측정 | 비고 |
| --- | ---: | --- |
| index_seconds | 214.62 | 임시 DB 에 동결 코퍼스 2건 색인 (endpoint chunk + projection dense inline) |
| projection rows / endpoint rows | 1809 / 1809 | 1:1 |
| RSS after index / peak | 1643.6 / 1643.6 MiB | 박스 7.6 GiB 내 |
| on end-to-end warm p50 / p95 | 27.6 / 41.1 | |
| off end-to-end warm p50 / p95 | 30.1 / 33.5 | |
| arm 추가분 warm p50 / p95 | −2.5 / +8.5 | p50 음수 = 측정 노이즈 대역. 질의 임베딩을 vector arm 과 공유(§5.2)해 ON 추가 비용은 FTS/HNSW 2 lookup + legacy baseline RRF snapshot 뿐 |
| cold 앞 3건 on | 31.8 / 48.0 / 37.8 | |

- ON 요청 지연은 OFF 와 사실상 동일 대역(p95 +8.5 ms). P3(요청당 ≈75 s, doc15)와 달리 이 arm 은 요청 경로에서 무거운 신규 추론이 없다.
- index_seconds 는 dense projection 1809건 임베딩 포함. §5.2 대로 재색인 시 endpoint 수만큼 local embedding 1회 추가. 프로덕션 하드웨어 예산은 별도 계측 필요 — 본 수치는 본 박스 한정.

---

## 6. 결론 (사실 기록)

- **`hard_pass = true`.** §6 HARD gate 전항 통과: OFF parity(09 정확히 일치), legacy base_wide parity, 결정성(3회 — final/rank/arm trace), source coverage(1809/1809, orphan·missing·dup·non-v1 0), both-arm slot 보존, q08/q09/q11/q12 byte-identical, C1 gross loss 0, regressed_accepted 0, route-pair 비회귀, C6 커버리지 비회귀, per-category 비회귀, attribution 기록, isolation(P2 quota 0 / P3 None / fallback 비호출), performance 기록.
- **§5.1 backfill 단위검증 PASS:** legacy 코퍼스 1809 projection(stripe 1220 / github 589), dense_nonnull 전건, `v1` 전행, inline/재실행/전삭제후재빌드 digest 3자 동일, 실패 0.
- **diagnostic 관찰(legacy-20):** recall@3 +0.05 / recall@10 +0.10 / MRR +0.02 / nDCG@10 +0.038, miss 11 → 9. §4 표적 7건 중 회복 2건(q06→9, q17→7, 둘 다 canonical vector 경로·lock 밖 순열), arm 진입하나 final 미도달 1건(q05: 3-arm RRF 35→29), repr arm 미진입 4건(q04/q07/q10/q18 — 설계가 명시한 미보장 구간). 표적 외 q19 개선(5→3). saturation q08/q09/q11/q12 회복 상한 0 확인(legacy both-slot HARD lock).
- **회귀:** 없음. C1/C5/q16 무변동, top-10 accepted 탈락 0.
- **promotion 판정 = architect 몫. sealed split NOT RUN.** 본 문서는 §6 HARD gate + legacy-20 diagnostic + §5.1 backfill 단위검증 한정이며, 승격 여부는 architect/lead 가 동결·개봉하는 Korean strata 포함 sealed split 계측이 결정한다.
