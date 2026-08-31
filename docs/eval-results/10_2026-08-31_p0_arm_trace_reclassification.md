# P0 동일 실행 arm/base-wide/final trace 및 miss 재분류 2026-08-31

- commit SHA: b0aa1a8 (HEAD, main) — `09_2026-08-31_corpus_eval.md` 와 동일 대상
- 근거: `docs/architect-review/92_corpus_eval_search_logic_improvement_review.md` §4.1(P0 rank-coordinate contract), §11(P1/P2 분기 규칙)
- 코퍼스 content_sha256: stripe=3653ad45bbec, github=80850db290cd (`tests/fixtures/corpus_eval/corpus_manifest.json`, 09 와 동일)
- 임베딩: intfloat/multilingual-e5-small (dim 384) / is_semantic: True / with_variants: False
- 등록: stripe -> endpoints=589 / github -> endpoints=1220
- 전략: `rrf` (운영 기본), top_k=10 → arm width=50 (모든 질의 exact prefix ≤1 → `max((10-exact)*4, 50) == 50`)
- 결정성: **PASS** — 계측 OFF 2회 + 계측 ON 1회의 final top-10(method/path/match_type) 완전 일치. 계측이 결과 리스트를 변형하지 않음을 확인.

---

## 1. 방법 — top_k 재호출 없는 read-only sink

doc 92 §4.1 요구: "운영 `search(top_k=10)` 단일 호출 안에서 중간 리스트를 read-only 로 기록. search 재호출·더 큰 top_k 금지, 결과 리스트 변형 금지."

계측 스크립트(`scratchpad/trace_p0.py`, 커밋 안 함)가 살아있는 `EndpointCandidateSearch` 인스턴스의 협력자 4개를 래핑한다. 각 래퍼는 실제 함수를 그대로 호출하고 반환값을 **변형 없이** 돌려주며, 인자·결과만 부수 기록한다:

| 래핑 대상 | 기록 좌표 |
| --- | --- |
| `_keyword_search.search(query, top_k=50)` | keyword arm top-50 (ref_id, ts_rank score) |
| `_vector_search.search(query, top_k=50)` | vector arm top-50 (ref_id, cosine score) |
| `_search_exact(...)` | exact prefix 매치 목록 + 개수 |
| `endpoint_candidate_search.reciprocal_rank_fuse(kw_ids, vec_ids, top_k=50)` | base-wide RRF top-50 (ref_id, RRF score, match_type) |

`cs.search(query, CandidateSearchOptions(top_k=10))` 를 질의당 정확히 1회 호출. final top-10 은 이 호출의 반환값. arm width·RRF_K(60)·arm 가중치는 전부 운영 좌표 그대로다.

실행 명령:

```bash
cd /home/kang/projects/docs-mcp
nohup rtk proxy uv run python \
  "$SCRATCH/trace_p0.py" "$SCRATCH/trace_p0.json" \
  > "$SCRATCH/trace_p0.stdout" 2>&1 &
# SCRATCH = 세션 스크래치패드. 모델 로드 + 1809 endpoint 색인으로 약 7분 소요(foreground 2분 타임아웃 회피).
```

`trace_p0.py` 는 `run_corpus_eval` 헬퍼(`_load_manifest`/`_load_corpus_texts`/`_load_and_validate_queries`/`_make_temp_db`/`_drop_temp_db`)를 재사용해 `tests/fixtures/corpus_eval/corpus_manifest.json` 의 동결 코퍼스로 임시 DB 를 세워 색인한다. 질의는 `tests/fixtures/corpus_eval/queries.json`(레거시 20건).

---

## 2. 탈락 단계 정의 (doc 92 §4.1 point 6 어휘)

정답 endpoint (method+path) 기준으로, 어느 단계에서 사라졌는지:

| 분류 | doc 92 용어 | 조건 |
| --- | --- | --- |
| `hit` | — | final top-10 안에 있음 (1-based rank ≤ 10) |
| `generation_miss` | `not-in-arm-width` | keyword arm top-50 에도, vector arm top-50 에도 없음 |
| `fusion_cut` | `fusion-cut` | 한쪽 arm top-50 에는 있으나 base-wide RRF top-50 에서 밀림 |
| `final_cut` | `final-cut` | base-wide RRF top-50 안에 있으나 최종 `base_wide[:10]` slice 밖 |

정답이 복수면 질의 대표 분류는 가장 늦은 단계 탈락(=최선)을 취한다.

---

## 3. 재분류 집계 — 09 의 rrf miss 11건

09 의 rrf 미검출 = q04–q12, q17, q18.

| 분류 | 건수 | 질의 |
| --- | --- | --- |
| `generation_miss` | **3** | q04, q07, q10 |
| `fusion_cut` | **0** | — |
| `final_cut` | **8** | q05, q06, q08, q09, q11, q12, q17, q18 |
| `hit` | 0 | — |

**generation miss : arm-hit→final-miss = 3 : 8 (of 11)**

카테고리별:

| 카테고리 | generation_miss | final_cut |
| --- | --- | --- |
| C2-한글패러프레이즈 | q04, q07 | q05, q06 |
| C3-영문의역 | q10 | q08, q09 |
| C4-흔한토큰범람 | — | q11, q12 |
| C6-다개념(복수정답) | — | q17 |
| C7-대형엔드포인트세부 | — | q18 |

doc 92 §2.2 가 잠정적으로 generation 측으로 의심했던 q06·q11 은 trace 결과 **final_cut** 이다(정답이 vector arm 안에 존재: q06 vec rank 16, q11 vec rank 8). 진짜 generation miss 는 q04·q07·q10 3건뿐.

---

## 4. 좌표표 — 전체 20질의

정답별 arm rank / base-wide rank / final rank. `None` = 해당 리스트에 없음.

| # | 질의 | 정답 | kw arm | vec arm | base-wide | final | 분류 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| q01 | 고객 생성 | POST /v1/customers | 2 | 1 | 1 | 1 | hit |
| q02 | 저장소 조회 | GET /repos/{owner}/{repo} | 33 | 4 | 8 | 1 | hit |
| q03 | create a checkout session | POST /v1/checkout/sessions | 1 | 3 | 1 | 1 | hit |
| q04 | 고객 새로 등록하고 싶어 | POST /v1/customers | None | None | None | None | **generation_miss** |
| q05 | 결제 환불 처리해줘 | POST /v1/refunds | None | 35 | 35 | None | **final_cut** |
| q06 | 이슈 새로 만들기 | POST /repos/{owner}/{repo}/issues | None | 16 | 16 | None | **final_cut** |
| q07 | 저장소 삭제해줘 | DELETE /repos/{owner}/{repo} | None | None | None | None | **generation_miss** |
| q08 | cancel my recurring payment | DELETE /v1/subscriptions/{subscription_exposed_id} | None | 15 | 38 | None | **final_cut** |
| q09 | shut down a repository | DELETE /repos/{owner}/{repo} | None | 10 | 25 | None | **final_cut** |
| q10 | show my billing history | GET /v1/invoices | None | None | None | None | **generation_miss** |
| q11 | customer | GET /v1/customers | None | 8 | 38 | None | **final_cut** |
| q12 | pull request | GET /repos/{owner}/{repo}/pulls | None | 4 | 40 | None | **final_cut** |
| q13 | subscription 취소 decoy | DELETE /v1/subscriptions/{subscription_exposed_id} | 9 | 4 | 6 | 6 | hit |
| q14 | 사용자 프로필 decoy | GET /users/{username} | 3 | 1 | 1 | 1 | hit |
| q15 | 커밋 목록 decoy | GET /repos/{owner}/{repo}/commits | 2 | 5 | 2 | 2 | hit |
| q16 | 구독 취소 + 환불 | DELETE /v1/subscriptions/{subscription_exposed_id} | None | 1 | 1 | 1 | hit |
| q16 | (2번째 정답) | POST /v1/refunds | None | None | None | None | generation_miss |
| q17 | 이슈 목록 조회 + 새 이슈 생성 | GET /repos/{owner}/{repo}/issues | None | 12 | 12 | None | **final_cut** |
| q17 | (2번째 정답) | POST /repos/{owner}/{repo}/issues | None | 25 | 25 | None | **final_cut** |
| q18 | 결제 생성 시 통화 단위 지정 | POST /v1/charges | None | 42 | 42 | None | **final_cut** |
| q19 | payment intent 세부 | POST /v1/payment_intents | None | 5 | 5 | 5 | hit |
| q20 | pull 생성 세부 | POST /repos/{owner}/{repo}/pulls | 5 | 1 | 2 | 2 | hit |

arm 길이 특이: q04·q05·q06·q07·q16·q17·q18(전부 한글 질의)의 keyword arm 길이 = **0** (`text_tsv` 가 한글 형태소를 못 잡음). q20 keyword arm 길이 = 19.

---

## 5. final_cut 8건 — 두 갈래 메커니즘

### 5a. keyword arm 공백 → base-wide = vector arm rank 그대로 (q05, q06, q17, q18)

한글 질의라 keyword arm 이 비어 RRF 가 vector 단일 arm fusion 이 된다. 교집합 경쟁이 없으므로 **base-wide rank == vector arm rank** (q05 35==35, q06 16==16, q17 12/25==12/25, q18 42==42). 정답 root endpoint 가 vector arm 안에서 이미 12~42위 — route-family 형제(자식/세부 endpoint)에게 vector 유사도 자체로 밀린다.

예 q06 "이슈 새로 만들기" base-wide top-10: `GET .../issues/{issue_number}/suggestions`, `PATCH /orgs/{org}`, `POST /orgs/{org}/issue-fields`, `POST .../suggestions/{suggestion_id}/approve`, `PATCH .../issues/{issue_number}`, … — 정답 `POST /repos/{owner}/{repo}/issues` 는 16위.

→ 이 4건은 doc 92 §3.4 의 "양-arm 교집합 편향" 메커니즘이 **아니다**. arm 이 하나뿐이라 교집합 경쟁이 없다. vector arm 랭킹 약점(짧은 root chunk vs 장황한 자식 chunk) 자체가 원인.

### 5b. keyword arm 존재(50) + final top-10 전부 `[both]` (q08, q09, q11, q12)

keyword arm 이 50건 다 차 있고, 정답은 vector-exclusive(kw rank None), vector arm 랭킹은 강함(q12 vec=4, q11 vec=8, q09 vec=10, q08 vec=15). 그런데 base-wide top-10 이 **전부 `[both]`** route-family decoy 로 채워진다. 등가중 RRF 에서 양-arm 점수 `1/(60+r_kw) + 1/(60+r_vec)` 가 단일 vector 점수 `1/(60+r_vec)` 를 항상 이기기 때문. 결과: vec 4위인 q12 정답이 base-wide 40위로 매장.

예 q12 "pull request" base-wide top-10: 전부 `[both]`, `POST .../stacks`, `POST .../pulls/{pull_number}/reviews/{review_id}/events`, `POST .../stacks/{stack_number}/add`, … — 정답 `GET /repos/{owner}/{repo}/pulls` 40위.

→ 이 4건이 doc 92 §3.4 메커니즘의 **직접 확인 사례**. P2(arm-exclusive rescue/quota)의 정확한 타깃.

---

## 6. generation_miss 3건 — vector arm 이 root 를 못 만든다

| # | 질의 | 정답 | vector arm 이 대신 반환한 것 |
| --- | --- | --- | --- |
| q04 | 고객 새로 등록하고 싶어 | POST /v1/customers | `.../customers/{customer}/subscriptions/.../discount`, `.../customers/{customer}/sources/{id}`, `.../customers/{customer}/cards/{id}` … 전부 자식 리소스, bare collection POST 는 top-50 밖 |
| q07 | 저장소 삭제해줘 | DELETE /repos/{owner}/{repo} | `DELETE /orgs/{org}/security-managers/teams/{team_slug}`, `DELETE /teams/{team_id}`, `DELETE /teams/{team_id}/repos/{owner}/{repo}` … org/team scope 변형만 |
| q10 | show my billing history | GET /v1/invoices | `POST /v1/billing/meter_events`, `POST /v1/billing/meters`, `GET /v1/balance/history` … `billing`↔`invoices` 어휘 간극. 영문 질의인데도 top-50 밖 |

q04·q07 은 한글 질의(KO→EN 간극) + 짧은 root chunk 가 장황한 자식 chunk 에 밀린 것. q10 은 영문인데도 실패 — `billing history` 와 `invoices` 사이 순수 어휘 간극. doc 92 §2.2 가 q10 을 "candidate-generation 강한 후보"로 예측한 것과 일치.

q16 의 2번째 정답 `POST /v1/refunds`("결제 환불" 개념)도 generation_miss — q05 와 동일 대상, 동일 실패.

---

## 7. P0 → P1/P2/P3 라우팅 함의

doc 92 §11 분기 규칙:

- generation miss 우세 → **P1** (bounded vector-only 질의 재구성)
- arm-hit/final-miss 우세 → **P2** (bounded arm-exclusive rescue/quota)
- 2–10위 shallow-rank 우세 → **P3** (cross-encoder rerank)

이번 trace:

| 단계 | 건수 | 대상 |
| --- | --- | --- |
| generation miss | 3 | q04, q07, q10 |
| arm-hit → final-miss (final_cut) | **8** | q05, q06, q08, q09, q11, q12, q17, q18 |
| shallow-rank hit (final rank 2–10) | 4 | q13(6), q15(2), q19(5), q20(2) |

**arm-hit/final-miss 가 8건으로 우세 → doc 92 §11 대로 P2 를 단독 product 후보로.** P1 은 generation miss 3건(q04·q07·q10)에 대해 여전히 필요 — P2 로는 arm 에 없는 정답을 살릴 수 없다.

P2 설계 시 주의(§5 근거):

- final_cut 8건 중 4건(q08·q09·q11·q12)만 양-arm 교집합 편향. 나머지 4건(q05·q06·q17·q18)은 keyword arm 공백이라 base-wide == vector rank — "RRF 교집합에 밀림"이 아니라 vector arm 자체 랭킹이 12~42위. P2 quota 를 vector-exclusive rank 기준으로 걸면 두 갈래 다 기계적으로 커버되지만, 후자는 vector rank 가 얕지 않아(q05 35, q18 42, q17 25) 좁은 quota(예: vector-exclusive top-3~5)로는 q12(4)·q11(8) 정도만 건짐. P2 상한이 bounded 라는 doc 92 경고와 일치.
- P2 는 product 랭킹을 바꾸지 않는 P0 범위를 벗어난다. 이 문서는 진단만 제공, 채택 판정은 architect.

---

## 8. 비고

- 하네스 종료 시점 stdout 에 `psycopg.errors.AdminShutdown: terminating connection due to administrator command` — 집계 출력·`trace_p0.json`(499KB) 기록 완료 후 커넥션 풀 teardown(`_drop_temp_db`) 중 postgres 컨테이너 외부 재시작과 겹친 것. 결정성 PASS 로 결과 유효성 확인됨. 09 와 동일 노이즈.
- 임베딩 로드 시 `sentence-transformers` 의 시퀀스 절단 경고 다수 — 코퍼스 chunk 가 512 토큰 초과분을 자르는 정상 동작, 09 기준선과 동일 조건.
- raw per-query arm top-50 전체 덤프(keyword/vector 각 50행 × 20질의)는 `scratchpad/trace_p0.json` 에 있다. 위 표는 결정 관련 좌표(정답 rank + base-wide top-10 이웃)를 발췌한 것. 스크립트 재실행으로 재생성 가능.
