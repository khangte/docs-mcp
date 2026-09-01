# P3 local cross-encoder rerank — feature-ON legacy-20 diagnostic + asset audit 2026-09-01

- 지시: `docs/architect-review/96_p3_local_cross_encoder_rerank_design.md` §6.1 (HARD 게이트) + §6.2 (지연) — lead/architect 경계 판정으로 범위 축소
- 대상: HEAD `41f59bb` (P3 구현·단위 테스트 커밋됨 — `app/services/search/cross_encoder_reranker.py`, `app/services/search/endpoint_candidate_search.py`, `app/composition.py`, `app/core/config.py`). 평가 중에는 deps exact pin(`sentence-transformers==5.7.0` / `transformers==5.14.1` / `torch==2.13.0+cpu`)을 임시 적용했고 architect verdict 99 반려로 원복함 — HEAD 의 `pyproject.toml` / `uv.lock` 는 pin 없이 유지. `DOCS_MCP_SEARCH_CROSS_ENCODER_ENABLED` 로 on/off.
- 기준 문서: `docs/eval-results/09_2026-08-31_corpus_eval.md` (레거시 20건, miss 11건), `docs/eval-results/14_2026-08-31_p0_reaudit_arm_wide_final_coordinates.md` (좌표 기준)
- 코퍼스 content_sha256: stripe=`3653ad45bbec…`, github=`80850db290cd…` (09/14 와 동일 — 동결 코퍼스)
- 질의: `tests/fixtures/corpus_eval/queries.json` (레거시 20건, split 없음)
- 임베딩(색인): intfloat/multilingual-e5-small (dim 384) / is_semantic: True / with_variants: False
- rerank arm_rescue_quota: **0** (P3 ON 이 P2 quota 를 0 으로 강제 — P1/P2 결합 없음)
- 결정성: **PASS** — on / on_2 / on_3 3회 재실행에서 final top-10 (method/path/match_type) 및 정답 rank 완전 일치.
- 실행 박스: WSL2, 논리 CPU 8, RAM 7.6 GiB. `OMP_NUM_THREADS=4`, `torch.set_num_threads(4)`. cross-encoder 는 `local_files_only=True` offline load (startup/request 시점 network fetch 없음).

## 범위 한정 (중요)

> **sealed split NOT RUN — promotion 불가. 본 문서는 legacy-20 diagnostic + asset audit 한정이다.**
> sealed split 의 질의·accepted·strata·manifest 저작·동결·개봉은 architect/lead 범위이며 이번 작업에서 수행하지 않았다.
> 따라서 본 실행은 효과성 PASS 판정도 promotion 판정도 내리지 않는다. **promotion = no.**

- 본 문서가 기록하는 것: §6.1 HARD 게이트(parity / determinism / candidate N=50 parity / both-arm slot 보존 / q08·q09·q11·q12 byte-identical / C1 gross loss / route-pair / C6 / recall attribution), §6.2 warm·cold 지연 + RSS, asset digest.
- `hard_pass = false` (아래 §3 참조).

## asset 승인 식별자 (`docs/architect-review/96` §8.1)

```
manifest approval: lead, 2026-08-31, session 6933ec83, model BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e Apache-2.0, prep-time 1회 다운로드 승인
```

- 다운로드: `huggingface_hub.snapshot_download` pinned revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` 1회 (prep 단계, `scratchpad/p3_prep_download.py`). download_seconds ≈ 93.
- 이후 런타임(`LocalCrossEncoderReranker`)은 `local_files_only=True` 로만 load.

### 파일 SHA-256 digest

manifest 파일: `tests/fixtures/corpus_eval/cross_encoder_asset_manifest.json`
manifest 파일 SHA-256: `f4e6ba3bbe9f603728e2b14ff2ac083d62fb7df39e368323273757e186392a8b`
자산 총량: 6 파일 / 2187.0 MiB

| 파일 | SHA-256 |
| --- | --- |
| `config.json` | `13dcd6c31d9fec9d1d8e158702072f62d7fa7d312a64b9fe057bec9a08cfe41a` |
| `model.safetensors` | `d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286` |
| `sentencepiece.bpe.model` | `cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865` |
| `special_tokens_map.json` | `8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835` |
| `tokenizer.json` | `69564b696052886ed0ac63fa393e928384e0f8caada38c1f4864a9bfbf379c15` |
| `tokenizer_config.json` | `7e4c1cc848840aeccdd763458c18dd525eb0f795c992e00ebe9c28554e7db2d4` |

### 의존성 고정 버전 (`pyproject.toml`)

| 패키지 | 버전 |
| --- | --- |
| `sentence-transformers` | `5.7.0` |
| `transformers` | `5.14.1` (설치본 `5.14.1`) |
| `torch` | `2.13.0` (설치본 `2.13.0+cpu`, `[tool.uv.index] pytorch-cpu`) |

- `transformers` 는 이미 `sentence-transformers` 전이 의존이나 P3 `_load_offline_scorer` 가 직접 import 하므로 명시 고정했다(새 런타임 의존성 footprint 증가 없음).
- rerank_document_format_version: `v1`. rerank width N = `min(50, len(base_wide))` = 50 (전 질의).

---

## 1. 방법

`scratchpad/eval_p3.py` (커밋 안 함). `run_corpus_eval` 헬퍼(`_load_manifest` / `_load_corpus_texts` / `_load_and_validate_queries` / `_make_temp_db` / `_drop_temp_db`)로 동결 코퍼스를 임시 DB 에 색인하고, 살아있는 `EndpointCandidateSearch` 인스턴스에 `cs._cross_encoder_reranker` 를 주입/해제하며 4개 arm 을 돈다:

| arm | 설정 |
| --- | --- |
| `off` | `cs._cross_encoder_reranker = None` — 09 기준선 재현, parity 확인 |
| `on` | pinned local `BAAI/bge-reranker-v2-m3` offline load, flag ON |
| `on_2`, `on_3` | `on` 재실행 (결정성) |

- 계측: `reciprocal_rank_fuse` 와 `apply_slot_lock` 을 read-only 래핑해 `base_wide[:50]` (ref_id, match_type), slot-lock 입력(N/K/locked_slots/raw_scores)과 출력(final_ids)을 질의별로 기록. 반환값은 변형하지 않는다. 계측 유무로 결과 리스트가 바뀌지 않음(off parity + 결정성으로 확인).
- 질의당 `cs.search(eq.query, CandidateSearchOptions(top_k=10))` 정확히 1회. warm 지연은 앞 3건(cold, 모델 warmup) 제외.
- arm 단위 pickle checkpoint(`scratchpad/eval_p3_ckpt.pkl`): 실행 중 postgres 컨테이너가 외부에서 `docker compose stop` 으로 3회 중단되어(SIGINT, `execDuration` ≈ 3460 s, per-run teardown 추정 — developer eval 경로/훅에는 해당 호출 없음) 완료 arm 을 잃지 않도록 각 arm 종료 시 저장하고 재실행 시 재사용. off/on/on_2/on_3 는 모두 동일 프로세스·동일 코퍼스에서 산출.

---

## 2. diagnostic 대조 — off vs on (legacy-20)

| 지표 | off (=09) | on | Δ |
| --- | ---: | ---: | ---: |
| recall@1 | 0.250 | 0.200 | −0.050 |
| recall@3 | 0.350 | 0.500 | +0.150 |
| recall@10 | 0.450 | 0.650 | +0.200 |
| MRR | 0.318 | 0.348 | +0.030 |
| nDCG@10 | 0.350 | 0.421 | +0.071 |
| answer_miss@10 | 11 | 7 | −4 |

- on / on_2 / on_3 지표 완전 동일.
- recall@10 +0.200 = 정답 4건(q05·q06·q17·q18) 신규 회복. recall@1 −0.050 = q16 정답이 rank 1 → 2 로 밀림(§3 route-pair FAIL 과 동일 사건).

### 2.1 질의별 rank (off → on)

`None` = final top-10 밖.

| # | 카테고리 | 정답 수 | off | on | 비고 |
| --- | --- | ---: | ---: | ---: | --- |
| q01 | C1-직접키워드 | 1 | 1 | 1 | |
| q02 | C1-직접키워드 | 1 | 1 | 1 | |
| q03 | C1-직접키워드 | 1 | 1 | 1 | |
| q04 | C2-한글패러프레이즈 | 1 | None | None | generation_miss (base_wide[:50] 밖) |
| q05 | C2-한글패러프레이즈 | 1 | None | **3** | 회복 (표적) |
| q06 | C2-한글패러프레이즈 | 1 | None | **3** | 회복 (표적) |
| q07 | C2-한글패러프레이즈 | 1 | None | None | generation_miss |
| q08 | C3-영문의역 | 1 | None | None | HARD lock (final 전부 both) — byte-identical |
| q09 | C3-영문의역 | 1 | None | None | HARD lock — byte-identical |
| q10 | C3-영문의역 | 1 | None | None | generation_miss |
| q11 | C4-흔한토큰범람 | 1 | None | None | HARD lock — byte-identical |
| q12 | C4-흔한토큰범람 | 1 | None | None | HARD lock — byte-identical |
| q13 | C5-decoy구분 | 1 | 6 | 6 | |
| q14 | C5-decoy구분 | 1 | 1 | 1 | |
| q15 | C5-decoy구분 | 1 | 2 | 2 | |
| q16 | C6-다개념(복수정답) | 2 | **1** | **2** | 정답 rank 1 → 2 강등 (route-pair / category-MRR FAIL) |
| q17 | C6-다개념(복수정답) | 2 | None | **6** | 회복 (표적), 커버리지 0 → 2 |
| q18 | C7-대형엔드포인트세부 | 1 | None | **8** | 회복 (표적) |
| q19 | C7-대형엔드포인트세부 | 1 | 5 | **3** | 개선 (표적 외) |
| q20 | C7-대형엔드포인트세부 | 1 | 2 | 2 | |

- C1(직접키워드) q01–q03 rank 1 유지 — 직접키워드 경로 무손상.
- C5(decoy) q13/q14/q15 무변동 — decoy 오염 없음.
- 표적 q05·q06·q17·q18: 4건 전부 회복.
- generation_miss q04·q07·q10: 무변동 (permutation 으로 회복 불가 — 설계대로).

### 2.2 final_cut 8건 개별

| qid | off rank | on rank | off both-in-final | on both-in-final | byte-identical final |
| --- | ---: | ---: | ---: | ---: | --- |
| q05 | None | 3 | 0 | 0 | no |
| q06 | None | 3 | 0 | 0 | no |
| q08 | None | None | 10 | 10 | **yes** |
| q09 | None | None | 10 | 10 | **yes** |
| q11 | None | None | 10 | 10 | **yes** |
| q12 | None | None | 10 | 10 | **yes** |
| q17 | None | 6 | 0 | 0 | no |
| q18 | None | 8 | 0 | 0 | no |

- q08/q09/q11/q12: final 10 슬롯 전부 `both` → HARD lock 으로 baseline 과 byte-identical (무변동 확인).
- q05/q06/q17/q18: `both` 0건 → lock 밖 순열로 회복.

### 2.3 recall attribution

recall@10 hit 상태가 바뀐 4건 전부 lock 밖 순열이며 `base_wide[:50]` 안에 있었음:

| qid | base_rank | on_rank | locked_slots | in base_wide[:50] |
| --- | ---: | ---: | --- | --- |
| q05 | None | 3 | {} | yes |
| q06 | None | 3 | {} | yes |
| q17 | None | 6 | {} | yes |
| q18 | None | 8 | {} | yes |

lock 안(q08/q09/q11/q12) 또는 generation_miss(q04/q07/q10) 에서 recall 변화 없음 → 효과 상한 표(§96 §1)와 일치.

---

## 3. §6.1 HARD 게이트

| 게이트 | 결과 | 근거 |
| --- | --- | --- |
| OFF parity vs 09 | **PASS** | recall@1/3/10 = 0.250/0.350/0.450, MRR 0.318, nDCG@10 0.350 — 09 와 |Δ| < 5e-3 (전부 정확히 일치) |
| candidate parity `base_wide[:50]` off == on | **PASS** | 20/20 질의 (ref_id, match_type) 순서 동일 — mismatch [] |
| 결정성 on == on_2 == on_3 | **PASS** | final (method,path) mismatch [], rank mismatch [] |
| both-arm subset / slot 보존 | **PASS** | off 의 `both` 참조가 on final 에서 동일 slot·순서·`both` 유지 — violation [] |
| q08/q09/q11/q12 byte-identical | **PASS** | 4건 전부 final 리스트 baseline 과 완전 일치 |
| C1 직접키워드 gross hit loss | **PASS** | baseline top-10 → miss 전락 0건 |
| baseline top-10 accepted 유지 (regressed_accepted) | **PASS** | top-10 밖으로 탈락한 정답 0건 |
| **route-pair 비회귀** | **FAIL** | q16: 정답 rank 1 → 2. route family `/v1/refunds`, `/v1/subscriptions` 양쪽에서 `capped(on) > capped(off)` |
| C6 커버리지 count 비회귀 | **PASS** | 질의별 cov@10 감소 0건. q17 커버리지 0 → 2 (dual-answer 완전 커버) |
| **per-category R@10-hit / MRR 비회귀** | **FAIL** | `C6-다개념(복수정답)`: hit 1 → 2 (증가) 이나 MRR 0.500 → 0.333 (q16 강등이 원인) |
| recall attribution (R@10 변화 = lock 밖 순열) | **PASS** | 변화 4건 전부 lock 밖·base_wide[:50] 내 (§2.3) |

**hard_pass = false.** 두 FAIL 은 단일 질의 **q16** 에서 발생: baseline 에서 rank 1 이던 정답(`POST /v1/refunds`, `both` 아님)을 reranker 가 다른 후보보다 낮게 채점(−3.218 vs slot 1 후보 −2.344)해 rank 2 로 강등. top-10 은 유지(recall@10 hit)하나 rank 1 → 2 이므로 route-pair 및 category-MRR 비회귀 게이트를 위반. q16 은 C6 복수정답이라 `/v1/refunds`·`/v1/subscriptions` 두 route family 에 모두 속해 route-pair 위반이 2행으로 집계됨(사건은 1건).

---

## 4. §6.2 지연 / 자원

warm = 앞 3건 제외한 17건. 단위 ms.

| 항목 | 측정 | doc96 상한 | 판정 |
| --- | ---: | ---: | --- |
| on end-to-end warm p50 | 75190.0 | ≤ 200 | **FAIL** |
| on end-to-end warm p95 | 76119.4 | ≤ 500 | **FAIL** |
| rerank 추가분 warm p50 | 75163.2 | — | — |
| rerank 추가분 warm p95 | 76091.0 | ≤ 250 | **FAIL** |
| off end-to-end warm p50 / p95 | 26.2 / 31.0 | — | (기준선 정상) |
| cold 앞 3건 on | 61911 / 77018 / 76146 | — | 기록 |
| model load | 1.78 s | — | offline load |
| RSS after index | 1663.2 MiB | — | |
| RSS peak (on arm) | 3653.6 MiB | 메모리 압박/timeout 시 FAIL | 상주 한도 내 (박스 7.6 GiB) |
| asset 총량 | 2187.0 MiB / 6 파일 | ~2.3 GB | 일치 |

- **지연 §6.2 FAIL.** 본 박스(WSL2, CPU 8논리코어, GPU 없음)에서 50-pair(각 ≤512 토큰) cross-encoder 배치가 질의당 ≈ 75 s. doc96 상한(rerank p95 ≤ 250 ms)의 약 300배. 전 질의가 ≈ 75 s 로 평탄 — 특정 질의 이상치가 아니라 CPU 추론 자체의 처리량 한계.
- RSS peak 3.65 GiB 로 메모리 압박·timeout 은 없었음(느릴 뿐). `model_load` 1.78 s 는 offline `from_pretrained` 만의 시간이며 first-query warmup 이 cold 앞 3건에 포함됨.
- 이 수치는 **본 실행 박스 한정**이다. 프로덕션/다코어·GPU 급 하드웨어의 수치는 별도 계측이 필요하며, 본 문서는 상한 대조 결과(FAIL)만 사실대로 기록한다.

---

## 5. 결론

- **HARD PASS 아님 (`hard_pass = false`).** §6.1 효과성 게이트 2건 FAIL(route-pair 비회귀, per-category MRR 비회귀 — 둘 다 q16 rank 1→2 강등), §6.2 지연 게이트 FAIL(본 박스 CPU 추론 ≈ 75 s/질의).
- **PASS 항목:** OFF parity(09 와 정확히 일치), candidate N=50 parity, 결정성(3회), both-arm slot 보존, q08/q09/q11/q12 byte-identical, C1 gross loss 0, regressed_accepted 0, C6 커버리지 count 비회귀, recall attribution(변화 전부 lock 밖 순열).
- **diagnostic 관찰:** legacy-20 에서 recall@3 +0.15 / recall@10 +0.20 / MRR +0.03 / nDCG@10 +0.071, miss 11 → 7. 표적 q05·q06·q17·q18 4건 전부 회복(전부 lock 밖·base_wide[:50] 내), q17 dual-answer 커버리지 0→2, q19 개선(5→3). generation_miss(q04/q07/q10)·HARD lock(q08/q09/q11/q12) 무변동 — 설계 효과 상한과 일치.
- **회귀:** q16(C6 복수정답) 정답 rank 1→2. recall@1 −0.05 의 원인.
- **promotion = no. sealed split NOT RUN.** 승격 판정은 architect/lead 가 동결·개봉하는 sealed split 계측을 요구하며 본 문서 범위 밖이다. 본 문서는 legacy-20 diagnostic + asset audit 한정.
