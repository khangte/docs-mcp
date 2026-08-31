# P2 bounded arm-exclusive rescue — paired corpus eval (quota 0/1/2/3) 2026-08-31

- commit SHA: b0aa1a8 (HEAD, main) — `09_2026-08-31_corpus_eval.md` 기준선과 동일 대상
- 근거: `docs/architect-review/92_corpus_eval_search_logic_improvement_review.md` §6(P2 설계·리스크·측정 게이트), `docs/eval-results/10_2026-08-31_p0_arm_trace_reclassification.md`(진단: final_cut 8건이 P2 1차 후보)
- 코퍼스 content_sha256: stripe=3653ad45bbec, github=80850db290cd (`tests/fixtures/corpus_eval/corpus_manifest.json`, 09·10 과 동일)
- 임베딩: intfloat/multilingual-e5-small (dim 384) / is_semantic: True / with_variants: False
- 등록: stripe -> endpoints=589 / github -> endpoints=1220
- 전략: `rrf` (운영 기본), top_k=10 → arm width=50
- 질의: 레거시 `tests/fixtures/corpus_eval/queries.json` 20건 (split 없음, 09 와 동일 집합)
- 결정성: **PASS** — quota=0 을 두 번 실행해 final top-10(method/path) 완전 일치. quota 미설정 시 기존 `base_wide[:top_k]` 와 동일함을 확인.
- 비고: 결과 출력·JSON 기록 이후 psycopg 커넥션 풀 teardown 중 `AdminShutdown`(postgres 재시작과 겹침). 지표·순위 산출은 그 전에 완료되어 영향 없음(09 와 동일 성질의 노이즈).

실행 명령:

```bash
cd /home/kang/projects/docs-mcp && \
  rtk proxy uv run python scratchpad/eval_p2.py scratchpad/eval_p2.json
# scratchpad/eval_p2.py, eval_p2.json — 커밋 안 함
```

---

## 1. 구현 요약

`docs/architect-review/92` §6.1 설계 그대로:

- `EndpointCandidateSearch._search_rrf` 에서 `reciprocal_rank_fuse(...)` 융합이 끝난 **뒤**, `base_wide[:top_k]` 슬라이스 직전에 rescue 를 적용한다. arm 순위·RRF 순위·RRF_K(60)·arm 가중치는 건드리지 않는다.
- rescue 대상 = `base_wide[top_k:]` 중 `match_type != "both"` (단일 arm 후보). 선택 우선순위는 **base_wide(RRF 점수) 순서** — route-family, path 길이, A/B/C/D structured 점수는 쓰지 않는다.
- 삽입 = final 의 최하위 RRF 슬롯을 뒤에서부터 밀어낸다(tail-slot displacement). §6.3 대비 최소 1건의 순수 RRF hit 는 보존(`n = min(len(rescued), top_k - 1)`).
- exact prefix 는 `search()` 에서 `_search_rrf` 바깥, `remaining_top_k` 계산 전에 처리되므로 구조적으로 무관.

플래그 / 상한:

| 항목 | 값 |
| --- | --- |
| env 플래그 | `DOCS_MCP_SEARCH_ARM_RESCUE_QUOTA` |
| 기본값 | `"0"` — 완전 비활성, `base_wide[:top_k]` 와 바이트 동일 |
| 하드 상한 | `_MAX_ARM_RESCUE_QUOTA = 3` (`endpoint_candidate_search.py`) — legacy F 건수(8)에 맞춰 늘리면 §6.3 RRF `k` 튜닝식 과적합이 되므로 상한 고정 |
| degrade | 미인식 값(`"abc"`, `"-5"`, `""`, 음수) → `0` |
| 배선 | `Settings.search_arm_rescue_quota` → `AppState.search_arm_rescue_quota` → `EndpointCandidateSearch(arm_rescue_quota=...)` → `_coerce_arm_rescue_quota()` 로 `[0, 3]` 클램프 |

변경 파일: `app/core/config.py`, `app/composition.py`, `app/services/search/endpoint_candidate_search.py`, `tests/unit/test_endpoint_candidate_search.py`.

단위 테스트 9건(전부 통과, DB 불필요 stub):

- `test_arm_rescue_disabled_by_default_keeps_rrf_cut` — quota=0 → 기존 컷 불변
- `test_arm_rescue_promotes_one_arm_exclusive_below_cut` — 컷 밖 단일 arm 후보 1건 승격
- `test_arm_rescue_respects_quota_cap` — quota 상한 준수
- `test_arm_rescue_only_rescues_arm_exclusive_not_both` — `both` 는 구제 안 함
- `test_arm_rescue_keeps_at_least_one_rrf_hit_when_top_k_is_one` — top_k=1 에서 순수 RRF hit 최소 1건 보존
- `test_arm_rescue_pool_follows_base_wide_order_deterministic` — 구제 풀은 base_wide 순서 결정적
- `test_arm_rescue_quota_coerces_unrecognized_to_zero` — degrade
- `test_arm_rescue_wired_from_app_state_default_off` / `test_arm_rescue_wired_when_app_state_sets_quota` — 배선

`uv run pytest tests/unit/test_endpoint_candidate_search.py tests/unit/test_rrf.py -q` → **67 passed**. `uv run ruff check` (변경 4파일) → **clean**.

---

## 2. paired 지표 — 09 기준선 대비

같은 bundle·같은 색인 위에서 `_arm_rescue_quota` 만 0→3 으로 바꿔 20건 재실행. quota=0 행이 09 기준선과 일치(대조 검증).

| quota | Recall@1 | Recall@3 | Recall@10 | MRR | nDCG@10 | answer_miss@10 |
| --- | --- | --- | --- | --- | --- | --- |
| **0 (=09 기준선)** | 0.25 | 0.35 | 0.45 | 0.318 | 0.350 | 11 |
| 1 | 0.25 | 0.35 | 0.45 | 0.318 | 0.350 | 11 |
| 2 | 0.25 | 0.35 | 0.50 | 0.323 | 0.365 | 10 |
| 3 | 0.25 | 0.35 | 0.55 | 0.329 | 0.380 | 9 |

교란·회귀 계측 (vs quota=0):

| quota | rescued(=displaced) | 그중 baseline `both` 슬롯 축출 | regressed_accepted | final_cut 회복 |
| --- | --- | --- | --- | --- |
| 1 | 20 | 11 | **0** | 없음 |
| 2 | 39 | 22 | **0** | q17 |
| 3 | 59 | 34 | **0** | q12, q17 |

- **Recall@1 / Recall@3: 전 quota에서 변화 없음.** §6 설계 의도대로(effect target = Recall@10 + answer_miss, Recall@1 아님).
- **regressed_accepted = [] (전 quota).** 어떤 accepted 정답도 순위가 나빠지거나 top-10 에서 빠지지 않음 → §6.4 C1(gross loss 0), route-pair root/child 비회귀 HARD 게이트 **PASS**.

---

## 3. final_cut 8건 (doc 10) 개별 추적

doc 10 이 P2 1차 후보로 지목한 8건. accepted 가 base_wide 에서 앉아 있는 rank(doc 10) 와 quota 별 final rank:

| 질의 | category | accepted base_wide rank (doc 10) | q0 | q1 | q2 | q3 |
| --- | --- | --- | --- | --- | --- | --- |
| q05 | C2-한글패러프레이즈 | bw35 | — | — | — | — |
| q06 | C2-한글패러프레이즈 | bw16 | — | — | — | — |
| q08 | C3-영문의역 | bw38 | — | — | — | — |
| q09 | C3-영문의역 | bw25 | — | — | — | — |
| q11 | C4-흔한토큰범람 | bw38 | — | — | — | — |
| q12 | C4-흔한토큰범람 | bw40 (vector arm rank 4) | — | — | — | **10** |
| q17 | C6-다개념(복수정답) | bw12/25 | — | — | **10** | **9** |
| q18 | C7-대형엔드포인트세부 | bw42 | — | — | — | — |

**8건 중 quota 상한(3) 안에서 회복된 것은 q17(quota≥2), q12(quota=3) 두 건.** 나머지 6건은 accepted 가 base_wide rank 16~42 로, quota≤3 이 끌어올릴 수 있는 `base_wide[top_k:top_k+3]` 창을 벗어난다. 그 창은 대부분 정답이 아닌 단일 arm decoy 로 채워진다 (예: q05 quota=1 은 `DELETE /v1/customers/{customer}` 를 구제, 정답 `POST /v1/refunds` 는 bw35 로 손대지 못함).

---

## 4. §6.4 측정 게이트 대조

| 게이트 | 결과 |
| --- | --- |
| exact prefix 를 final 좌표에 포함 | 충족 — `search()` 에서 exact 처리 후 남은 슬롯에만 rrf/rescue 적용, 계측도 동일 |
| baseline·candidate paired | 충족 — 동일 bundle·색인, quota 만 변경 |
| 구제분·축출분 분리 집계 | §2 표: quota=1 은 20 구제 / 20 축출(11 은 baseline `both`), quota=3 은 59 / 59(34 `both`) |
| C1 gross loss 0 | **PASS** — regressed_accepted = [] 전 quota |
| route-pair root/child 비회귀 | **PASS** — accepted 회귀 0, 표시 변화는 전부 비정답 슬롯 간 교체 |
| category MRR drop 한도 (사전 고정) | 위반 없음 — MRR 전 quota 비감소(0.318→0.318→0.323→0.329) |
| boundary-crossing trace | §2·§3 표 + `scratchpad/eval_p2.json` `rescued`/`displaced`/`final_by_quota` 에 질의별 교체 기록 |
| 신규 sealed split 에서 Recall@10 순증 + MRR/nDCG 비회귀 | **미실시** — 신규 봉인 split 없음. 레거시 20건에서는 quota=2 부터 Recall@10 +0.05, MRR/nDCG 비회귀. |

---

## 5. 해석 — dose-response 와 §6.2/§6.3

- **quota=1(최소·최소교란 설정)은 지표를 전혀 움직이지 않는다.** 질의당 1개 슬롯이 열리지만 그 슬롯은 항상 base_wide rank 11 근처의 비정답 단일 arm 후보가 차지한다. final_cut 정답은 모두 그보다 깊다(bw12~42).
- 회복은 quota=2(q17), quota=3(q12) 에서만 시작되고, 그 대가로 질의 전반에서 baseline `both` 슬롯을 quota=2 에 22개, quota=3 에 34개 축출한다. accepted 회귀는 0 이지만, 이는 레거시 20건에 한정된 관측이다.
- 이 결과는 §6.2("실제 기대치는 quota 와 decoy 경쟁 때문에 더 낮다")·§6.3(단일 arm false positive 가 both-arm 정답을 밀어냄, quota 를 legacy F 수에 맞추면 RRF `k` 튜닝식 과적합) 경고와 정합한다. bounded 설계는 레거시 집합에서 Recall@1 을 못 움직이고 Recall@10 도 상한 근처에서만 소폭 움직인다.
- 구현은 §6.1 설계대로다(설계 이탈 아님). 다만 "arm-exclusive **상위** 후보" 의 선택 키를 **base_wide(RRF) 순서**로 동결했는데, doc 10 은 q12 정답이 vector arm rank 4(자기 arm 최상위)임을 보였다. 선택 키를 자기 arm rank 로 바꾸면 quota=1 에서 q09/q11/q12 를 직접 끌어올릴 여지가 있으나, 결과를 본 뒤 선택 키를 바꾸는 것은 §6.3 이 경고한 과적합에 해당한다. P2 진행 여부·선택 키 재정의는 architect 판단 사항.

---

## 6. 아티팩트

- `scratchpad/eval_p2.py` — paired eval 스크립트 (커밋 안 함)
- `scratchpad/eval_p2.json` — quota 0/1/2/3 질의별 rank·final·rescued·displaced 전량
- `scratchpad/eval_p2.stdout` — 실행 로그
