# P1 vector-only reformulation — legacy diagnostic (09/10 기준선 대비 paired) 2026-08-31

- 코드 기준: `55828f7` (HEAD, main) + 미커밋 P1 구현 (working tree, 커밋 안 함)
- 스펙: `docs/architect-review/94_p1_vector_only_reformulation_design.md` 전항
- 근거: `docs/eval-results/09_2026-08-31_corpus_eval.md`(기준선), `docs/eval-results/10_2026-08-31_p0_arm_trace_reclassification.md`(generation_miss 3건 q04/q07/q10)
- 코퍼스 content_sha256: stripe=`3653ad45bbec`, github=`80850db290cd` (`tests/fixtures/corpus_eval/corpus_manifest.json`, 09·10·11 과 동일)
- 임베딩: intfloat/multilingual-e5-small (dim 384) / is_semantic: True
- 등록: stripe → endpoints=589 / github → endpoints=1220
- 전략: `rrf` (운영 기본), top_k=10 → arm width=50, P2 `DOCS_MCP_SEARCH_ARM_RESCUE_QUOTA=0` 고정
- 질의: 레거시 `tests/fixtures/corpus_eval/queries.json` 20건 (split 없음, 09 와 동일 집합)
- reformulation manifest: `scratchpad/p1_reformulations_manifest.json`, **sha256 `b733247088f8baee77ef3a2035467f55cd596aeb4db7a42b3f5969fa054b48b7`** (실행 전 봉인, queries.json sha256 `8f61cb9900…441f`)
- 결정성: **PASS** — p1_on 2회 실행 final `(method,path,match_type)` + vector arm 길이 완전 일치
- 비고: 결과 출력·JSON 기록 이후 psycopg 커넥션 풀 teardown 중 `AdminShutdown`(postgres 재시작과 겹침). 지표·순위 산출은 그 전에 완료돼 영향 없음(09·11 과 동일 성질의 노이즈).

실행 명령:

```bash
cd /home/kang/projects/docs-mcp && \
  rtk proxy uv run python scratchpad/eval_p1.py scratchpad/eval_p1.json
# scratchpad/eval_p1.py, eval_p1.json, p1_reformulations_manifest.json — 커밋 안 함
```

---

## 1. 구현 요약

`docs/architect-review/94` §3·§4·§8 설계 그대로 (설계 이탈 없음):

- 새 MCP 입력 `search_endpoints(..., vector_reformulations: list[str] | None = None)`.
  의미는 "원본 query 와 같은 retrieval 의도를 영문으로 다시 표현한 독립 vector subquery" (최대 2개).
- 서버 정규화는 syntactic 만: `_normalize_vector_reformulations` 가 각 항목에 NFKC + strip + 내부
  공백 접기, 정규화 key(+ casefold)가 원본 query 또는 앞선 항목과 같으면 drop, 빈 문자열 drop,
  서로 다른 값 2개 초과 시 `ValidationError`, client 입력 순서 보존. 번역·alias·형태소·embedding
  확장·질의 분해 없음.
- `query_variants` 와 상호배타 — 둘 다 정규화 뒤 nonempty 이면 `ValidationError`
  ("vector_reformulations and query_variants are mutually exclusive"). P1 평가는 `query_variants=None` 고정.
- `_search_rrf` 의 벡터 arm 에서만 `_search_vector_with_variants(query, reformulations, width,
  candidate_ids, truncate_to=width)` 로 원본 + reformulation 을 각 top-50 임베딩 → ref별 best-rank
  병합 → **병합 리스트를 width=50 으로 재절단**. keyword arm 은 raw `query` 만 받는다. legacy
  `query_variants` 벡터 경로는 `truncate_to=None` 으로 기존 무절단 계약 유지.
- keyword / exact / fallback / RRF 식 / arm 가중치 / `RRF_K=60` / P2 rescue quota 는 불변.
- P2-식 tail-slot 치환 없음 — enhanced vector arm 을 넣은 정상 RRF 결과만 반환.

플래그 / 입력:

| 항목 | 값 |
| --- | --- |
| env 플래그 | `DOCS_MCP_SEARCH_VECTOR_REFORMULATION_ENABLED` |
| 활성 조건 | `"1"`/`"true"`/`"yes"` (대소문자 무시). 그 외·미설정 = 완전 비활성 |
| 기본값 | 비활성 — client 가 `vector_reformulations` 를 줘도 무시, baseline 과 byte-identical (§7 롤백 스위치) |
| MCP 입력 | `vector_reformulations: list[str] | None` (최대 2, `query_variants` 와 상호배타) |
| 배선 | `Settings.search_vector_reformulation_enabled` → `AppState.search_vector_reformulation_enabled` → `EndpointCandidateSearch(vector_reformulation_enabled=...)` |

변경 파일: `app/core/config.py`, `app/composition.py`, `app/services/search/endpoint_candidate_search.py`,
`app/mcp/tools/endpoints.py`, `tests/unit/test_endpoint_candidate_search.py`.

단위 테스트 12건 추가(전부 통과, DB 불필요 stub):

- `test_reformulation_mutually_exclusive_with_query_variants` — 동시 입력 시 `ValidationError`
- `test_reformulation_rejects_more_than_two_distinct` — cap 초과 `ValidationError`
- `test_reformulation_dedup_and_normalization_collapse_to_within_cap` — NFKC·공백·casefold dedup 으로 3→2
- `test_reformulation_equal_to_query_is_dropped` — 원본 query 와 같은 값 drop
- `test_reformulation_does_not_touch_keyword_arm` — reformulation 은 벡터 arm 에만
- `test_reformulation_absent_is_byte_identical_to_baseline` — `None`/`[]`/`[""]`/`["   "]` = baseline
- `test_reformulation_flag_off_ignores_input_no_error` — flag OFF 면 입력+`query_variants` 동시라도 무시, 오류 없음
- `test_reformulation_merged_vector_list_capped_at_width` — 병합 벡터 리스트 `truncate_to` 로 width 재절단
- `test_reformulation_preserves_baseline_both_arm_slots` — single-arm rank-1 도 both-arm tail 못 밀어냄
- `test_reformulation_admits_new_ref_into_vector_arm_and_final` — reformulation 으로만 닿는 후보 진입(positive)
- `test_reformulation_wired_from_app_state_default_off` / `test_reformulation_wired_when_app_state_enables` — 배선

`uv run pytest tests/unit/test_endpoint_candidate_search.py tests/unit/test_rrf.py -q` → **79 passed**.
`uv run ruff check` (변경 5파일 + eval 스크립트) → **clean**.

---

## 2. arm 구성 (paired, 같은 bundle·같은 색인)

| arm | `query_variants` | `vector_reformulations` | flag | 목적 |
| --- | --- | --- | --- | --- |
| **base** | None | 없음 | OFF | 09 기준선 재현 |
| **p1_off** | None | manifest (q04/q07/q10) | OFF | disabled parity 대상 |
| **p1_on** | None | manifest (q04/q07/q10) | ON | 후보 |
| **p1_on_2** | None | manifest | ON | 결정성 재실행 |

manifest reformulation (doc 94 §5 예시, 의미 기반, 정답 method+path 에 맞춘 상수 아님):

| 질의 | 원문 | reformulation | accepted |
| --- | --- | --- | --- |
| q04 | `고객 새로 등록하고 싶어` | `create a customer` | `POST /v1/customers` |
| q07 | `저장소 삭제해줘` | `delete a repository` | `DELETE /repos/{owner}/{repo}` |
| q10 | `show my billing history` | `list invoices` | `GET /v1/invoices` |

나머지 17건은 전 arm 에서 `vector_reformulations=None`.

---

## 3. §6.1–§6.3 HARD 게이트

| 게이트 | 결과 |
| --- | --- |
| disabled parity (flag OFF + 입력 존재 → baseline 동일) | **PASS** — p1_off 20/20 질의 final `(method,path,match_type)` = base |
| empty parity (flag ON + 입력 없는 17건 → baseline 동일) | **PASS** — 17/17 질의 p1_on final = base |
| determinism (p1_on 2회) | **PASS** — final + vector arm 길이 완전 일치 |
| vector budget (reformulation ≤2, 병합 vector RRF 리스트 ≤50) | **PASS** — p1_on 전 질의 vector arm 길이 = 50 |
| P2 isolation (`arm_rescue_quota=0`) | **PASS** — quota 0, rescue 함수 no-op |
| keyword / original-vector parity | **PASS** — p1_off = base 로 간접 확인(같은 raw `query`, `query_variants=None`) |
| fallback parity | **해당 없음** — `strategy=rrf` 로만 실행. P1 은 fallback 에 `vector_reformulations` 전달 안 함(코드상 무관) |
| **§6.2 both-arm subset** (`baseline_final_both_ref_ids ⊆ candidate_final_ref_ids`) | **FAIL — q10** |
| §6.3 regressed_accepted | **PASS** — `[]` (accepted 순위가 나빠지거나 top-10 이탈한 질의 0) |
| §6.3 C1 direct/exact (baseline hit → candidate miss) | **PASS** — C1 4건(q01·q02·q03 rank 1, q14 rank 1) 불변 |
| §6.3 route-pair root/child capped rank 비회귀 | **PASS** — accepted 회귀 0, C5 decoy(q13·q14·q15) 순위 불변 |
| §6.3 category R@10 순감 ≤1 / MRR 하락 ≤0.02 | **PASS** — C2 R@10 hit 0→1(증가), 다른 category 불변, MRR 전 category 비감소 |

### §6.2 both-arm subset FAIL 상세 (q10 `show my billing history`)

`list invoices` 는 accepted `GET /v1/invoices` 를 **벡터 arm top-50 에 새로 입장시켰다**
(`accepted_in_vec_top50`: base False → p1_on True). 그러나:

- p1_on final top-10 은 **전부 `both`** 이고 `GET /v1/invoices` 는 없다(rank None → None, 미회복).
- reformulation 이 billing/meter/subscription 계열의 **벡터 등수를 흔들어** base final 의 `both`
  후보 2건이 top-10 밖으로 밀렸다:
  - `GET /organizations/{org}/settings/billing/usage`
  - `POST /v1/subscriptions`
  대신 다른 `both` 후보(`POST /v1/billing/meters/{id}`, `GET /users/{username}/settings/billing/usage` 등)가 올라왔다.

즉 P2-식 기계적 tail 축출이 아니라 **unchanged RRF 가 enhanced vector arm 으로 재계산한 결과**
`both` 집합 순서가 바뀌었고, doc 94 §6.2 는 이 경우("의도치 않은 vector candidate 가 strong
two-arm evidence 를 밀어냄")를 aggregate 개선과 무관하게 HARD FAIL 로 규정한다. doc 94 §6.2
말미가 예측한 시나리오와 정확히 일치한다 — "q10처럼 baseline top-k 가 `both` 로 포화된 질의는
vector admission 이 성공해도 final top-k 로는 못 들어갈 수 있다. 그 경우 P1 은 candidate-generation
진단에는 성공해도 제품 후보로는 효과성 FAIL."

---

## 4. 지표 — 09 기준선 대비

| arm | Recall@1 | Recall@3 | Recall@10 | MRR | nDCG@10 | answer_miss@10 |
| --- | --- | --- | --- | --- | --- | --- |
| **base (=09 기준선)** | 0.25 | 0.35 | 0.45 | 0.318 | 0.350 | 11 |
| **p1_on** | 0.25 | 0.40 | 0.50 | 0.343 | 0.382 | 10 |
| Δ | 0 | +0.05 (+1건) | +0.05 (+1건) | +0.025 | +0.032 | −1 |

- Recall@10 은 **+1건**(q04)만 증가. MRR·nDCG@10 은 비감소(상승).
- 전량 변화는 q04 단독. q07·q10 은 base·p1_on 모두 miss.

---

## 5. generation_miss cohort (q04/q07/q10) 추적

| 질의 | reformulation | accepted vec top-50 진입 (p1_on) | base final top-10 | p1_on final top-10 | 신규 회복 |
| --- | --- | --- | --- | --- | --- |
| q04 `고객 새로 등록…` | `create a customer` | ✅ (base ✗) | ✗ | **✅ rank 2** | ✅ |
| q07 `저장소 삭제해줘` | `delete a repository` | ✅ (base ✗) | ✗ | ✗ (rank None) | ✗ |
| q10 `show my billing history` | `list invoices` | ✅ (base ✗) | ✗ | ✗ (rank None) | ✗ |

- **q04**: `create a customer` 가 EN query ↔ EN endpoint 임베딩을 직접 비교해 `POST /v1/customers`
  를 벡터 arm 상위로 올렸고, keyword 신호가 0인 한글 원문이라 벡터 arm 이 사실상 유일 신호가 돼
  final rank 2 로 회복. 설계가 노린 그대로.
- **q07**: `DELETE /repos/{owner}/{repo}` 가 벡터 arm top-50 엔 들어왔으나, 병합 벡터 arm 상위를
  `DELETE /orgs/.../security-managers/...`, `DELETE /repos/.../code-scanning/analyses/...`,
  `DELETE /user/repository_invitations/...` 등 sub-resource/decoy delete 가 차지해 root delete
  가 벡터 arm top-10 밖 → RRF 융합 후 final 밖. doc 94 §5 가 예고한 "broad variant 가 top-50 에만
  들어온 적이 있어 final top-10 회복은 별도 측정 대상" 그대로.
- **q10**: §3 참조 — 벡터 admission 성공, final 미회복 + §6.2 both-subset FAIL.

---

## 6. §6.4 legacy diagnostic 판정

doc 94 §6.4 는 새 sealed split 준비 승인을 위해 **네 조건 전항**을 요구한다.

| 조건 | 요구 | 실측 | 판정 |
| --- | --- | --- | --- |
| 1 | q04/q07/q10 정답이 모두 vector arm top-50 진입 | 3/3 진입 | **PASS** |
| 2 | 셋 중 ≥2 가 final top-10 으로 신규 회복 | 1/3 (q04만) | **FAIL** |
| 3 | 09 대비 R@10 ≥+2건(+10%p), MRR/nDCG@10 비감소 | R@10 **+1건**, MRR/nDCG 비감소 | **FAIL** (R@10 부족) |
| 4 | §6.1–§6.3 HARD 전항 PASS | §6.2 both-subset FAIL (q10) | **FAIL** |

**§6.4 종합: FAIL (PASS = False).** 조건 1(candidate-generation 메커니즘)만 충족.

---

## 7. §6.5 신규 sealed split — 미실시

doc 94 §6.5 는 "legacy 방향 검증을 통과한 하나의 implementation SHA 에만 새 P1 fixture 를
작성한다" 로 규정한다. §6.4 가 FAIL 이므로 **신규 sealed split 은 작성하지 않았다.**

참고로 §6.5 가 규정하는 sealed effectiveness 산식(승인 시 적용 대상)은 다음과 같다:

```text
scored 120건 = 96 gate + 24 sealed holdout
  · Korean cross-language / English paraphrase·resource-vocabulary gap / multi-clause 포함
  · C1 direct, C5 decoy, root/child route pair 를 필수 대조군
  · query별 vector_reformulations byte + candidate flag + query/split/corpus SHA 를 manifest 봉인

gate HARD PASS (= §6.1–§6.3 전항) 후에만 효과성 판정:
  effective  ⟺  R@10(cand) − R@10(base) ≥ Δ_min           (Δ_min 은 freeze 문서에 결과 전 고정)
           ∧  MRR(cand)   ≥ MRR(base)
           ∧  nDCG@10(cand) ≥ nDCG@10(base)
           ∧  ∀ q ∈ generation_miss_cohort:
                  accepted ∈ vector_top50(cand, q)          (admission)
              ∧  |{q : recovered_to_top10(cand, q)}| ≥ 2    (recovery)
           ∧  ∀ q: baseline_final_both_ref_ids(q) ⊆ candidate_final_ref_ids(q)   (both preservation)

HOLDOUT 은 lead 의 별도 명시 승인 전까지 봉인. v3 sealed holdout 은 verdict 91 에 따라 열지 않음.
```

---

## 8. 해석

- **메커니즘은 동작한다.** flag ON·`vector_reformulations` 제공 시 세 generation_miss 정답이
  모두 벡터 arm top-50 에 새로 입장했다(§6.4 조건 1 PASS). client LLM 이 문서 어휘를 아는
  영문 표현을 주면 KO→EN / paraphrase gap 을 벡터 admission 단계에서 좁힐 수 있다는 설계 가정은
  legacy 20 에서 재현됐다.
- **제품 효과는 부족하다.** unchanged RRF 가 병합 벡터 arm 을 융합하면 q07 은 sub-resource
  delete decoy 에, q10 은 both-arm billing 포화에 막혀 final top-10 에 못 든다. q04 만 회복하고
  R@10 은 +1 건에 그친다(§6.4 조건 2·3 FAIL). q10 에서는 reformulation 이 `both` 순서를 흔들어
  §6.2 subset HARD FAIL 까지 발생한다(조건 4 FAIL).
- doc 94 §6.4·§9 는 이 결과에 대한 처리를 명시한다 — **"legacy 결과가 기준에 못 미치면 P1
  candidate 는 반려하고 새 P1 alias/reformulation rule 을 같은 legacy 결과에 맞춰 조정하지
  않는다", "q04/q07/q10 중 하나라도 source 표현을 바꿔가며 재시도하지 않는다", "보여주지 못하면
  P1 을 더 많은 alias·quota·slot 규칙으로 튜닝하지 않고 별도 architecture 를 다시 설계한다."**
- 구현은 설계대로이며(설계 이탈 아님) HARD 비간섭 게이트(disabled/empty/determinism/budget/
  keyword·original-vector parity/P2 isolation)는 전부 PASS 다. flag 기본값이 OFF 이므로 코드가
  머물러도 운영 동작은 baseline 과 byte-identical(no-op) — P2(doc 11/93)와 같은 disposition.
- 승급/반려·flag 활성 여부·별도 architecture 재설계는 architect·lead 판단 사항.

---

## 9. 아티팩트

- `scratchpad/p1_reformulations_manifest.json` — 봉인된 reformulation manifest (sha256 §헤더)
- `scratchpad/eval_p1.py` — paired eval 스크립트 (커밋 안 함)
- `scratchpad/eval_p1.json` — arm별 질의별 rank·final·vector arm 길이·admission·게이트 전량
- `scratchpad/eval_p1.stdout` — 실행 로그
