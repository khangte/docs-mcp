# P0 재감사 — arm / base-wide / final 3좌표 read-only trace 2026-08-31

- 지시: `docs/architect-review/98_b1_b2_verdict_and_p0_coordinate_reconciliation.md` §5.2 (B 구현과 독립된 read-only P0 재감사)
- 대상: HEAD `c20c01d` (B1/B2 변경분 원복 완료, `arm_rescue_quota=0`, `strategy=rrf` — 운영 기본)
- 기준 문서: `docs/eval-results/09_2026-08-31_corpus_eval.md` (miss 11건), `docs/eval-results/10_2026-08-31_p0_arm_trace_reclassification.md` (초판 재분류), `docs/eval-results/13_2026-08-31_b1_b2_rrf_ablation_eval.md` (좌표 정정 대상)
- 코퍼스 content_sha256: stripe=`3653ad45bbec`, github=`80850db290cd` (09/10 과 동일)
- 질의: `tests/fixtures/corpus_eval/queries.json` (레거시 20건, split 없음)
- 임베딩: intfloat/multilingual-e5-small (dim 384) / is_semantic: True / with_variants: False
- 결정성: **PASS** — 계측 OFF 2회 + 계측 ON 2회 모두 final top-10(method/path/match_type) 완전 일치. 계측이 결과 리스트를 변형하지 않음.

---

## 1. 방법 — ranking 무변경, 계측만 추가

`docs/architect-review/98` §2 요구: 운영 `search(top_k=10)` 단일 호출 안에서 keyword arm top-50 · vector arm top-50 · `reciprocal_rank_fuse` base-wide(top_k=50) · final top-10 을 read-only 로 함께 기록. search 재호출·더 큰 top_k·리스트 변형 금지.

`scratchpad/trace_p0_reaudit.py` (커밋 안 함) 가 살아있는 `EndpointCandidateSearch` 인스턴스의 협력자 4개를 감싼다. 각 래퍼는 실제 함수를 그대로 호출하고 반환값을 **변형 없이** 돌려주며 인자·결과만 부수 기록한다:

| 래핑 대상 | 기록 좌표 |
| --- | --- |
| `cs._keyword_search.search(query, top_k=50, ...)` | keyword arm top-50 (ref_id 순서) |
| `cs._vector_search.search(query, top_k=50, ...)` | vector arm top-50 (ref_id 순서, score 필터 전) |
| `cs._search_exact(...)` | exact prefix 매치 (method,path) |
| `endpoint_candidate_search.reciprocal_rank_fuse(kw_ids, vec_ids, top_k=50)` | base-wide RRF top-50 (ref_id 순서, match_type) |

`cs.search(query, CandidateSearchOptions(top_k=10))` 를 질의당 정확히 1회 호출. final top-10 은 그 반환값. arm width(50) · RRF_K(60) · arm 가중치는 전부 운영 좌표 그대로. arm/base-wide 의 ref_id 는 `cs._endpoint_repo.get_many()` 로 (method, path) 해소해 정답과 대조.

harness 는 `run_corpus_eval` 헬퍼(`_load_manifest` / `_load_corpus_texts` / `_load_and_validate_queries` / `_make_temp_db` / `_drop_temp_db`)를 재사용해 동결 코퍼스로 임시 DB 를 세워 색인한다.

실행 명령:

```bash
cd /home/kang/projects/docs-mcp
nohup rtk proxy uv run python \
  scratchpad/trace_p0_reaudit.py scratchpad/trace_p0_reaudit.json \
  > scratchpad/trace_p0_reaudit.stdout 2>&1 &
# 모델 로드 + 1809 endpoint 색인 + (clean 2회 + trace 2회) 로 약 6분.
```

### 탈락 단계 정의 (`docs/eval-results/10` §2 와 동일)

정답 endpoint (method+path) 기준으로 어느 단계에서 사라졌는지:

| 분류 | 조건 |
| --- | --- |
| `hit` | final top-10 안 (1-based rank ≤ 10) |
| `generation_miss` | keyword arm top-50 에도 vector arm top-50 에도 없음 |
| `fusion_cut` | 한쪽 arm top-50 에는 있으나 base-wide RRF top-50 에서 밀림 |
| `final_cut` | base-wide RRF top-50 안에 있으나 최종 `base_wide[:10]` 밖 |

정답이 복수면 질의 대표 분류는 가장 늦은 단계 탈락(=최선)을 취한다.

---

## 2. 좌표표 — 09 miss 11건

`None` = 해당 리스트에 없음. rank 는 정답 endpoint 의 1-based 등수.

| # | 질의 | 정답 | kw arm | vec arm | base-wide | final | 분류 |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| q04 | 고객 새로 등록하고 싶어 | POST /v1/customers | None | None | None | None | **generation_miss** |
| q05 | 결제 환불 처리해줘 | POST /v1/refunds | None | 35 | 35 | None | **final_cut** |
| q06 | 이슈 새로 만들기 | POST /repos/{owner}/{repo}/issues | None | 16 | 16 | None | **final_cut** |
| q07 | 저장소 삭제해줘 | DELETE /repos/{owner}/{repo} | None | None | None | None | **generation_miss** |
| q08 | cancel my recurring payment | DELETE /v1/subscriptions/{subscription_exposed_id} | None | 15 | 38 | None | **final_cut** |
| q09 | shut down a repository | DELETE /repos/{owner}/{repo} | None | 10 | 25 | None | **final_cut** |
| q10 | show my billing history | GET /v1/invoices | None | None | None | None | **generation_miss** |
| q11 | customer | GET /v1/customers | None | 8 | 38 | None | **final_cut** |
| q12 | pull request | GET /repos/{owner}/{repo}/pulls | None | 4 | 40 | None | **final_cut** |
| q17 | 이슈 목록 조회 + 새 이슈 생성 | GET /repos/{owner}/{repo}/issues | None | 12 | 12 | None | **final_cut** |
| q17 | (2번째 정답) | POST /repos/{owner}/{repo}/issues | None | 25 | 25 | None | **final_cut** |
| q18 | 결제 생성 시 통화 단위 지정 | POST /v1/charges | None | 42 | 42 | None | **final_cut** |

참고 — miss 아닌 질의 중 관련 좌표: q16 2번째 정답 `POST /v1/refunds` 는 kw/vec/wide/final 전부 `None` → `generation_miss` (q05 와 동일 대상·동일 실패). shallow-rank hit: q13(6), q15(2), q19(5), q20(2).

keyword arm 길이: q04·q05·q06·q07·q17·q18 (한글 질의) = **0** (`text_tsv` 가 한글 형태소 미포착). q08·q09·q11·q12 = 50 (영문 질의).

---

## 3. 재분류 확정 — `docs/eval-results/10` 과 대조

| 분류 | 건수 | 질의 | doc 10 | 변경 |
| --- | --- | --- | --- | --- |
| `generation_miss` | 3 | q04, q07, q10 | 동일 (3) | 없음 |
| `fusion_cut` | 0 | — | 동일 (0) | 없음 |
| `final_cut` | 8 | q05, q06, q08, q09, q11, q12, q17, q18 | 동일 (8) | 없음 |

**doc 10 재분류(generation 3 / fusion_cut 0 / final_cut 8)를 좌표 단위로 전부 재확인했다. 바뀌는 항목 없음.**

이번 재감사는 doc 10 이 측정하지 않았다는 지적을 받은 세 좌표(kw arm / vec arm / base-wide)를 B 구현과 무관한 HEAD `c20c01d` 에서 독립적으로 다시 계측했고, 정답 endpoint 의 arm rank·base-wide rank 가 doc 10 §4 와 동일함을 확인했다 (vec: 35/16/15/10/8/4/12/25/42, base-wide: 35/16/38/25/38/40/12/25/42).

### `docs/eval-results/13` 좌표 주장과의 관계

`13` 초판 §3.3/§6 의 "q08/q09/q11/q12 정답이 fused wide-list(width=50) rank `None`" 및 "candidate-generation miss" 서술은 틀렸다. `scratchpad/eval_b.py` 는 `cs.search(top_k=10)` 의 final 10개만 읽었고 arm/wide 계측이 없었으므로, 그 `None` 은 "final top-10 밖" 일 뿐이다. 이번 재감사가 해당 4건의 vec arm rank(15/10/8/4)와 base-wide rank(38/25/38/40)를 직접 기록했다 — 정답은 arm 과 base-wide 안에 있으며 실패 단계는 **final_cut** 이다. `13` 은 verdict 98 에 따라 좌표 정정 노트가 이미 추가돼 있다.

---

## 4. final_cut 8건 — 두 갈래 메커니즘 (좌표로 구분)

| 질의 | kw arm len | final top-10 match_type | 메커니즘 |
| --- | ---: | --- | --- |
| q05 | 0 | 10/10 `vector` | keyword arm 공백 → 단일 vector fusion |
| q06 | 0 | 10/10 `vector` | 〃 |
| q17 | 0 | 10/10 `vector` | 〃 |
| q18 | 0 | 10/10 `vector` | 〃 |
| q08 | 50 | 10/10 `both` | 양-arm 교집합 포화 |
| q09 | 50 | 10/10 `both` | 〃 |
| q11 | 50 | 10/10 `both` | 〃 |
| q12 | 50 | 10/10 `both` | 〃 |

### 4a. keyword arm 공백 → base-wide = vector arm rank 그대로 (q05, q06, q17, q18)

한글 질의라 keyword arm 이 비어 RRF 가 단일 vector arm fusion 이 된다. 교집합 경쟁이 없어 **base-wide rank == vector arm rank** (q05 35==35, q06 16==16, q17 12/25==12/25, q18 42==42). final top-10 은 전부 `vector`. 정답 root endpoint 가 vector 유사도 자체로 route-family 형제(자식·세부 endpoint)에 12~42위로 밀린 것 — RRF 교집합 편향이 원인이 아니다.

### 4b. keyword arm 50 + final top-10 전부 `both` (q08, q09, q11, q12)

keyword arm 이 꽉 차 있고 정답은 vector-exclusive(kw rank `None`), vector arm 랭킹은 강하다(q12 vec 4, q11 vec 8, q09 vec 10, q08 vec 15). 그런데 base-wide top-10 이 **전부 `both`** route-family decoy 로 채워진다. 등가중 RRF 에서 양-arm 점수 `1/(60+r_kw) + 1/(60+r_vec)` 가 단일 vector 점수 `1/(60+r_vec)` 를 항상 이기기 때문 (`docs/architect-review/97` §2.1 universal both dominance). 결과: vec 4위 q12 정답이 base-wide 40위로 매장. 이 4건이 `97` §2.1 수학의 직접 확인 사례다.

`docs/architect-review/98` §3: B1(`k=20`)·B2(`alpha=0.5`) 고정식은 이 4건의 base-wide rank(38/25/38/40)를 top-10 경계 위로 올리지 못했다. 재감사는 그 실패가 generation 단계가 아니라 이 both-포화 fusion 단계에서 일어남을 재확인한다.

---

## 5. generation_miss 3건

| # | 질의 | 정답 | 원인 |
| --- | --- | --- | --- |
| q04 | 고객 새로 등록하고 싶어 | POST /v1/customers | 한글(KO→EN 간극) + 짧은 root chunk 가 장황한 자식 chunk 에 밀려 vector arm top-50 밖 |
| q07 | 저장소 삭제해줘 | DELETE /repos/{owner}/{repo} | 〃 (org/team scope 변형만 arm 진입) |
| q10 | show my billing history | GET /v1/invoices | 영문인데도 `billing history`↔`invoices` 순수 어휘 간극으로 양 arm top-50 밖 |

q16 2번째 정답 `POST /v1/refunds` 도 동일 (양 arm 부재).

---

## 6. 결정성

| 검증 | 결과 |
| --- | --- |
| 계측 OFF final top-10 2회 일치 | PASS |
| 계측 ON trace 2회 (final match_type + per-accepted stage) 일치 | PASS |
| 계측 ON final == 계측 OFF final | PASS |

harness 종료 시 stdout 에 `psycopg.errors.AdminShutdown: terminating connection due to administrator command` — 집계·`trace_p0_reaudit.json` 기록 완료 후 커넥션 풀 teardown(`_drop_temp_db`) 노이즈. 09/10 과 동일. 결정성 PASS 로 결과 유효.

---

## 7. 결론

- 09 miss 11건의 실패 단계 재분류: **generation_miss 3 (q04·q07·q10) / fusion_cut 0 / final_cut 8 (q05·q06·q08·q09·q11·q12·q17·q18)**. `docs/eval-results/10` 과 **완전 동일 — 변경점 없음**.
- final_cut 8건은 두 갈래: keyword arm 공백 4건(q05·q06·q17·q18, base-wide=vector rank, 단일 arm), 양-arm 교집합 포화 4건(q08·q09·q11·q12, final 전부 `both`).
- `docs/eval-results/13` 의 "wide-list None / generation miss" 좌표 주장은 계측 부재로 인한 오해였고, 이번 독립 재감사로 doc 10 좌표가 유효함을 확정.
- 산출물: `scratchpad/trace_p0_reaudit.py`, `scratchpad/trace_p0_reaudit.json`, `scratchpad/trace_p0_reaudit.stdout` (전부 커밋 안 함). 본 문서만 `docs/eval-results/` 산출물.
