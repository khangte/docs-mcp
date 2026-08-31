# B1/B2 RRF 융합식 ablation 평가 2026-08-31

> **좌표 정정 (verdict 98, `docs/architect-review/98_b1_b2_verdict_and_p0_coordinate_reconciliation.md`):**
> 초판 §3.3/§6 의 "q08/q09/q11/q12 정답이 fused wide-list(width=50) rank `None`" · "candidate-generation miss" · "P0 반증" 서술은 틀렸다.
> 이 harness(`scratchpad/eval_b.py`)는 `cs.search(query, top_k=10)` 의 **최종 반환 10개만** `_rank()` 로 읽으며, keyword/vector arm top-50 도 `reciprocal_rank_fuse(..., top_k=50)` base-wide 도 계측하지 않는다.
> 따라서 그 `None` 은 "final top-10 밖" 일 뿐이고 wide/arm 부재의 근거가 아니다. 실패 단계는 P0(`docs/eval-results/10`)가 기록한 대로 **final_cut** 이다 (q08/q09/q11/q12 vector arm rank 15/10/8/4, base-wide rank 38/25/38/40).
> 측정 수치·B1/B2 top-10 무효과 결론은 그대로 유효하다. 아래 본문은 verdict 98 에 맞게 좌표 서술만 정정한 상태다.

- 스펙: `docs/architect-review/97_both_arm_saturation_remediation_options.md` (§2.1 dominance, §4.1 B1, §4.2 B2, §6.1 HARD, §6.2 sealed effectiveness)
- 근거 기준선: `docs/eval-results/09_2026-08-31_corpus_eval.md` (variant 없음), `docs/eval-results/10_2026-08-31_p0_arm_trace_reclassification.md` (final_cut 8건)
- code_head: `c20c01d`. 평가 시점엔 미커밋 B 구현(config / composition / rrf / endpoint_candidate_search + B 전용 tests)이 워킹트리에 있었으나, `docs/architect-review/98` 판정에 따라 6파일 모두 `c20c01d` 로 되돌렸다(비승급, dark 보존 안 함).
- 코퍼스 content_sha256: stripe=`3653ad45bbec`, github=`80850db290cd` (09 와 동일)
- 질의 파일 sha256: `8f61cb99006e0d07923111fc919aaaa7489b486b0fffca15928efce75355441f` (`tests/fixtures/corpus_eval/queries.json`, legacy 20질의, split 없음)
- 봉인 manifest: `scratchpad/b_ablation_manifest.json`, sha256 `73e958493d26d5bebd3f6580ae58a70c3c6f878021ead74037dfcdd0cd164476` (후보 실행 전 동결)
- 임베딩: intfloat/multilingual-e5-small (dim 384)

---

## 1. 구현 요약

RRF 융합식의 두 파라미터를 설정으로 노출했다. 기본값은 현행값이라 flag/설정 미지정 시 09 와 byte-identical 이다.
keyword/vector arm 내용·exact prelude·fallback 무변경, P2 arm-rescue quota 는 0 고정.

| 파라미터 | 설정 필드 | 환경변수 | 기본값 (= 현행) | B1 | B2 |
| --- | --- | --- | --- | --- | --- |
| RRF `k` | `settings.search_rrf_k` → `AppState.search_rrf_k` → `EndpointCandidateSearch(rrf_k=...)` | `DOCS_MCP_SEARCH_RRF_K` | `"60"` | `"20"` | `"60"` |
| second-arm 가중 α | `settings.search_rrf_second_arm_alpha` → `AppState.search_rrf_second_arm_alpha` → `EndpointCandidateSearch(rrf_second_arm_alpha=...)` | `DOCS_MCP_SEARCH_RRF_SECOND_ARM_ALPHA` | `"1.0"` | `"1.0"` | `"0.5"` |

- 융합식 v1: `score(d) = c_max + α · Σ(c_others)`, `c_arm = w_arm / (k + rank_arm(d))`.
  - α = 1.0 이면 분기 없이 기존 누적합(`Σ c_arm`)과 완전히 같다 — endpoint/doc 검색, 2-arm/3-arm 모든 호출부 byte-identical.
  - single-arm 히트는 `c_others` 가 비어 α 와 무관.
  - 첫 arm 기여(`c_max`)는 α 가 절대 건드리지 않음. `match_type` / `contributing_arms` 는 존재 여부 기준 그대로.
- `k` 와 α 는 서로 독립 — 각각 켤 수 있고 동시 적용도 가능하나, 이 평가는 각각 단독으로만 측정한다 (§4.3 / §8: B1+B2, B+P3, A+B 조합은 평가 대상 아님).
- coerce: `_coerce_rrf_k` (int, `>= 1` 아니면 60 으로 degrade), `_coerce_second_arm_alpha` (float, `0.0 <= α <= 1.0` 아니면 1.0 으로 degrade).

### 변경 파일

| 파일 | 변경 |
| --- | --- |
| `app/services/search/rrf.py` | `SECOND_ARM_ALPHA = 1.0` 상수, `reciprocal_rank_fuse(..., second_arm_alpha=SECOND_ARM_ALPHA)` 키워드 인자, α != 1.0 且 both 기여 2개 이상일 때만 `c_max + α·Σ(c_others)` 재계산 |
| `app/core/config.py` | `search_rrf_k`, `search_rrf_second_arm_alpha` raw 문자열 필드 (env 기본) |
| `app/composition.py` | `AppState` 필드 2개, `from_engine` 파라미터 2개, `build_services` 에서 `EndpointCandidateSearch` 로 배선 |
| `app/services/search/endpoint_candidate_search.py` | `_DEFAULT_RRF_K` / `_DEFAULT_SECOND_ARM_ALPHA`, `_coerce_rrf_k` / `_coerce_second_arm_alpha`, 생성자 인자 2개, `_search_rrf` 의 wide fuse 호출에 `k` / `second_arm_alpha` 전달 |
| `tests/unit/test_rrf.py` | B 단위 테스트 7건 |
| `tests/unit/test_endpoint_candidate_search.py` | B 배선/불변 테스트 9건 |

### 단위 테스트

`tests/unit/test_rrf.py` (7):
- `test_default_k_and_alpha_match_legacy_formula` — 생략 시 기존 합산식과 동일 객체
- `test_b1_lower_k_changes_rank_math` — `k=20` 이면 `1/(20+r)` 로 계산
- `test_b1_lower_k_lets_head_single_outrank_tail_both` — k=20 에서 vector 1위 single 이 말단 both(50/50) 를 앞선다
- `test_b2_alpha_halves_only_the_weaker_arm_contribution` — `c_max + 0.5·c_weak`
- `test_b2_alpha_does_not_touch_single_arm_scores` — single-arm 불변
- `test_b2_alpha_removes_universal_both_dominance` — α=0.5 에서 말단 both 가 head single 밑으로
- `test_b1_and_b2_independent_and_composable` — `k=20`, α=0.5 동시 적용 시 `1/21 + 0.5·(1/23)`

`tests/unit/test_endpoint_candidate_search.py` (9):
- `test_rrf_defaults_are_byte_identical_to_legacy_call`, `test_rrf_baseline_final_cut_is_all_both`
- `test_b1_lower_k_admits_vector_head_and_breaks_both_saturation`, `test_b2_alpha_admits_vector_head_and_breaks_both_saturation`
- `test_b1_b2_leave_keyword_and_exact_untouched`
- `test_rrf_k_coerces_unrecognized_or_nonpositive_to_sixty`, `test_second_arm_alpha_coerces_out_of_range_to_one`
- `test_rrf_params_wired_from_app_state_defaults`, `test_rrf_params_wired_when_app_state_sets_b1_or_b2`

### 상태

- `uv run pytest tests/unit/test_rrf.py tests/unit/test_endpoint_candidate_search.py tests/integration/test_mcp_server.py -q` → **129 passed**
- `uv run ruff check` (touched 파일 전체) → **All checks passed!**

---

## 2. 평가 방법

`scratchpad/eval_b.py` — `tests/fixtures/corpus_eval/run_corpus_eval.py` 헬퍼를 재사용해 임시 DB 에 코퍼스 1회 색인 후, 동일 `EndpointCandidateSearch` 인스턴스에 `_rrf_k` / `_rrf_second_arm_alpha` 만 바꿔가며 20질의를 `top_k=10` 으로 3-way paired 실행. 결정성 위해 각 arm 2회 실행해 동일 확인.

| arm | rrf_k | α |
| --- | --- | --- |
| base | 60 | 1.0 |
| B1 | 20 | 1.0 |
| B2 | 60 | 0.5 |

산출물: `scratchpad/eval_b.json`, `scratchpad/eval_b.stdout`.

---

## 3. 3-way 대조표

### 3.1 parity (base vs 09)

| 지표 | base (측정) | 09 기준선 | 일치 |
| --- | --- | --- | --- |
| Recall@1 | 0.25 | 0.25 | O |
| Recall@3 | 0.35 | 0.35 | O |
| Recall@10 | 0.45 | 0.45 | O |
| MRR | 0.31833 | 0.318 | O (< 5e-3) |
| nDCG@10 | 0.35025 | 0.350 | O (< 5e-3) |
| answer_miss@10 | 11 | 11 | O |

base arm 은 09 를 정확히 재현한다 — 기본값 배선이 no-op 임을 확인.

### 3.2 base / B1 / B2 지표

| arm | R@1 | R@3 | R@10 | MRR | nDCG@10 | miss@10 | R@10 hit Δ vs base |
| --- | --- | --- | --- | --- | --- | --- | --- |
| base | 0.25 | 0.35 | 0.45 | 0.31833 | 0.35025 | 11 | — |
| B1 (k=20) | 0.25 | 0.35 | 0.45 | 0.31833 | 0.35025 | 11 | 0 |
| B2 (α=0.5) | 0.25 | 0.35 | 0.45 | 0.31833 | 0.35025 | 11 | 0 |

**B1·B2 모두 legacy 20질의 지표에 측정 가능한 변화가 없다.** 모든 집계 지표가 base 와 동일.

### 3.3 final_cut 8건 + B 표적 추적

| 질의 | 카테고리 | base final rank | B1 final rank | B2 final rank |
| --- | --- | --- | --- | --- |
| q05 | C2-한글패러프레이즈 | 미검출 | 미검출 | 미검출 |
| q06 | C2-한글패러프레이즈 | 미검출 | 미검출 | 미검출 |
| **q08** | C3-영문의역 | 미검출 | 미검출 | 미검출 |
| **q09** | C3-영문의역 | 미검출 | 미검출 | 미검출 |
| **q11** | C4-엔드포인트의미 | 미검출 | 미검출 | 미검출 |
| **q12** | C4-엔드포인트의미 | 미검출 | 미검출 | 미검출 |
| q17 | C6-대형엔드포인트 | 미검출 | 미검출 | 미검출 |
| q18 | C6-대형엔드포인트 | 미검출 | 미검출 | 미검출 |

- **좌표 주의:** `scratchpad/eval_b.py` 는 `cs.search(query, top_k=10)` 의 **최종 반환 10개만** `_rank()` 로 읽는다. keyword/vector arm top-50, `reciprocal_rank_fuse(..., top_k=50)` 의 base-wide, accepted 의 wide rank 는 이 harness 가 **계측하지 않는다**. 아래 "미검출"/`None` 은 전부 **"final top-10 에 없음"** 이며 "wide/arm 에 없음" 이 아니다.
- final_cut 8건은 base/B1/B2 에서 전부 동일하게 final top-10 밖 (`['q05','q06','q08','q09','q11','q12','q17','q18']`).
- B 표적 q08/q09/q11/q12: 이 harness 가 읽은 final top-10 rank 는 3 arm 모두 `None`. arm/wide 좌표는 미측정이므로 이 문서만으로는 실패 단계를 특정할 수 없다. `docs/eval-results/10` (P0, 동일 코퍼스·동일 production `search(top_k=10)` 1회 내부에서 arm top-50·fused top-50·final 을 read-only 기록) 이 q08/q09/q11/q12 의 vector arm rank 15/10/8/4, base-wide rank 38/25/38/40, 실패 단계 = final_cut 으로 기록했고, 그 좌표가 유효하다.
- §2.1 무효과 예상군 q05/q06/q17/q18 (keyword-empty): 예상대로 3 arm 모두 final top-10 밖, 변화 없음.
- 유일한 미시 변화: `by_query` 기준 q08/q09 의 `both_in_final` 이 B1 에서 10 → 9 (final top-10 안의 low-rank `both` 1건이 밀림). 정답은 회복되지 않고 집계 지표도 불변. B2 는 미시 변화도 없음.

---

## 4. §6.1 HARD 게이트

| 게이트 | B1 | B2 |
| --- | --- | --- |
| stability (3× 동일 + flag-off byte-identical) | PASS | PASS |
| C1 gross hit loss = 0 | PASS (`[]`) | PASS (`[]`) |
| accepted 회귀 (`regressed_accepted=[]`) | PASS (`[]`) | PASS (`[]`) |
| route-pair (root·child 각 `delta <= 0`) | PASS (`[]`) | PASS (`[]`) |
| C6 (coverage & complete@10 >= baseline) | PASS (`[]`) | PASS (`[]`) |
| category (R@10 hit drop 0, MRR drop 0.000) | PASS (`[]`) | PASS (`[]`) |
| overlap accounting | RECORD only |

route family (accepted path segment-2 prefix): `/v1/customers` [q01,q04,q11], `/repos/{owner}` [q02,q06,q07,q09,q12,q15,q17,q20], `/v1/refunds` [q05,q16], `/v1/subscriptions` [q08,q13,q16].

**B1 hard_pass = True, B2 hard_pass = True.** 단, top-10 결과를 아무것도 바꾸지 않으므로 게이트를 trivially 통과한 것이다 (regression 도 improvement 도 없음).

---

## 5. §6.2 새 sealed split 유효성 산식 (미실행)

§6.2 / §8 에 따라 sealed effectiveness split 은 **B variant 가 동결되고 lead 승인을 받은 뒤에만** 작성한다. 본 평가는 legacy-20 진단 ablation 이며 B1/B2 어느 쪽도 top-10 에 효과가 없어 sealed split 을 트리거하지 않는다. 승격 시 요구 조건(기록만):

- 새 split: 채점 질의 >= 96 + 미개봉 holdout 24, strata 에 "keyword+vector overlap high, 정답 single-arm" 포함
- Recall@10 +5 퍼센트포인트 이상
- MRR & nDCG@10 비감소
- overlap-saturation stratum 의 accepted hit 순증
- empty result 증가 0
- §6.1 HARD 중 하나라도 FAIL → 승격 없음

---

## 6. 해석 및 처분

- 구현은 doc 97 B1/B2 에 충실했다 — 설계 이탈 없음. `reciprocal_rank_fuse` 단일 경로에 `second_arm_alpha` 키워드 인자를 더했고, α=1.0 에서 기존 누적을 그대로 두어 모든 호출부가 byte-identical.
- **B1(k=20)·B2(α=0.5) 모두 legacy 20질의의 accepted top-10·R@10·MRR·nDCG 를 전혀 개선하지 못했다.** B1 은 q08/q09 의 final `both` 수만 10→9 로 바꿨고 정답은 올리지 못했으며, B2 는 그 미시 변화도 없다. §6.1 HARD PASS 는 output 무변경/무회귀가 만든 trivial safety PASS 일 뿐 effectiveness 증거가 아니다.
- 이 harness 는 final top-10 만 읽었다. arm/wide 좌표는 미측정이므로 "candidate-generation miss" 나 "P0 반증" 은 이 문서가 주장할 수 없다. 실패 단계는 P0(`docs/eval-results/10`)가 기록한 대로 **final_cut** 이다.
- flag 기본값(60/1.0)은 no-op 이라 09 골든 회귀는 안전하나, B1/B2 는 enabled 상태에서도 accepted 효과가 0이고 공용 `reciprocal_rank_fuse` 의 global parameter surface 를 늘린다. P2 와 달리 보존할 실험 신호가 없다.
- 처분(`docs/architect-review/98`): B1·B2 비승급. dark candidate 로 보존하지 않고 6파일을 HEAD `c20c01d` 로 되돌린다. sealed split 미실행. 다음 후보 전 B 구현과 독립된 read-only P0 재감사를 수행한다.

---

## 7. 산출물

| 파일 | 커밋 여부 |
| --- | --- |
| `docs/eval-results/13_2026-08-31_b1_b2_rrf_ablation_eval.md` (본 문서) | 산출물 |
| `scratchpad/b_ablation_manifest.json` (sha256 `73e958493d26d5bebd3f6580ae58a70c3c6f878021ead74037dfcdd0cd164476`) | 커밋 안 함 |
| `scratchpad/eval_b.py`, `scratchpad/eval_b.json`, `scratchpad/eval_b.stdout` | 커밋 안 함 |
| B 구현 6파일 (§1 변경 파일) | `docs/architect-review/98` 에 따라 `c20c01d` 로 revert — 커밋 없음 |
