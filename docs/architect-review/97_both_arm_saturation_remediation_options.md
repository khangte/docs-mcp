# 97. both-arm 포화 직접 보정 — A/B 대안 검토

- 선행 근거: `docs/architect-review/96_p3_local_cross_encoder_rerank_design.md`, `docs/architect-review/93_p2_arm_rescue_effectiveness_verdict.md`, `docs/architect-review/95_p1_vector_reformulation_rejection_verdict.md`, `docs/eval-results/10_2026-08-31_p0_arm_trace_reclassification.md`
- 기준선: original-query keyword/vector wide RRF (`RRF_K=60`, equal arm weights), exact prelude 보존, P1 제거, P2 quota `0`.
- 상태: **검토 설계. 구현·모델 반입·fixture 저작·평가 실행은 별도 승인 대상.**

## 1. 결론과 우선순위

P0의 q08/q09/q11/q12는 route 산식이 아니라 **two-arm RRF의 overlap bonus** 때문에 final top-10이 `both`로 포화된 사례다. 현 `k=60`과 arm width 50에서는 top-50 안의 최하위 `both`조차 vector 1위 single보다 높은 점수를 받는다. 이는 P3가 의미 점수를 잘 내도 96번의 both slot lock 밖으로 정답을 올릴 수 없는 근본 원인이다.

1. **B를 우선 검토한다.** keyword/vector retrieval 내용은 유지한 채 RRF의 “두 번째 arm이 주는 존재 보너스”만 바꾸는 B1/B2를 각각 독립 ablation으로 평가한다.
2. **A는 비권고다.** P3의 lock을 B-1 floor로 완화하면 q08~q12의 빈 slot 하나는 만들 수 있지만, P1 q10에서 금지했던 both subset 손실을 계약상 허용하는 새 architecture다.
3. B와 P3는 첫 평가에서 결합하지 않는다. B가 새 base에서 모든 HARD 및 sealed gate를 통과한 경우에만, 그 **새 base를 baseline으로 다시 고정**해 P3를 별도 candidate로 올린다.

B가 q08/q09/q11/q12의 upstream saturation을, P3가 keyword-empty q05/q06/q17/q18의 learned vector-ranking 약점을 맡는 것이 final-cut 8건을 모두 다룰 수 있는 **유일한 조합 범위**다. 이는 효과 보장이 아니라 각 후보의 기계적 도달 범위다.

## 2. 원인 — current RRF에 route 점수는 없다

endpoint RRF의 현 식은 다음뿐이다. `r_kw`, `r_vec`는 각 arm의 1-based rank이고, arm에 없는 항은 0이다.

```text
s_kw(d)   = 1 / (60 + r_kw(d))
s_vec(d)  = 1 / (60 + r_vec(d))
s_both(d) = s_kw(d) + s_vec(d)
```

`route-pair`는 `delta(q) = rank_candidate(q) - rank_baseline(q)`로 root/child 비회귀를 판정하는 **평가 산식**이지, 현 검색 score에 들어가는 path length·family·root/child boost가 아니다. 편향은 두 arm이 공통 resource token을 가진 sibling endpoint를 각각 top-50에 많이 넣고, 그 교집합이 무가중 합산을 두 번 받는 데서 생긴다.

### 2.1 top-50 overlap 포화가 산식상 확정되는 이유

| 비교 | 계산 | 점수 | 결론 |
| --- | --- | ---: | --- |
| 두 arm에서 모두 50위인 최하위 `both` | `2 / (60 + 50)` | **0.01818** | top-50 overlap의 최저점 |
| vector arm 1위 single | `1 / (60 + 1)` | **0.01639** | single의 최고점 |
| 차이 | `0.01818 - 0.01639` | **+0.00179** | 최하위 `both`가 최고 single보다 높음 |

따라서 두 arm의 top-50 교집합이 충분하면, rank 품질과 무관하게 `both`가 single보다 앞선다. q12의 정답이 vector 4위인데 final 40위인 것은 이 성질의 직접 사례다. 이는 P2처럼 결과 뒤 tail에 후보를 주입하는 문제가 아니라 RRF가 두 evidence를 더하는 **upstream order** 문제다.

## 3. A — P3 both slot lock 조건부 완화 (비권고)

### 3.1 가능한 가장 좁은 완화 형태

현 P3의 `B`(rerank 전 final RRF 안 `both` 수)를 유지하되, 다음 두 조건을 모두 만족하는 경우에만 `both` 한 건을 강등하는 별도 candidate를 상정할 수 있다.

```text
both_floor = max(B - 1, 0)       # query당 both 최다 1건만 강등
demotion_budget = 1              # exact prelude 제외, top_k 전체에서 1
```

1. cross-encoder가 `base_wide[:50]`의 vector-exclusive 후보 하나를 highest relevance로 정하고, 잠재적으로 강등될 `both` 중 lowest relevance보다 사전 고정 margin 이상 높게 score한다.
2. 정답 여부·route family·path/method·query id에는 의존하지 않는다. 두 후보 모두 같은 pinned model/revision/input format에서 나온 score만 사용한다.
3. exact prelude는 absolute lock이고, 나머지 `both` `B-1`의 id·slot·상대순서는 그대로 남는다. allowed demotion도 final tail로 임의 이동시키지 않고, 사전 고정한 score ordering의 유일한 교체 slot만 쓴다.
4. score tie·margin 미달·asset failure·후보 부족이면 no-op baseline이다. P1/P2와 결합하지 않는다.

`both`가 top-10을 모두 채운 q08/q09/q11/q12에서는 이 한 slot으로 single-answer 정답 한 건을 넣을 가능성이 생긴다. 그러나 q별로 한 개뿐이며 all-of에는 coverage 보장이 없다.

### 3.2 왜 비권고인가

P1 q10은 final `both` 두 ref를 잃어 HARD FAIL이었다. A는 두 건 손실은 막지만, **한 건 손실을 허용하는 순간** 96 §3의 id·slot·순서 전체 보존 계약을 명시적으로 폐기한다. cross-encoder가 좋은 semantic signal을 내더라도 displaced `both`가 C1 direct answer 또는 route-pair child일 수 있다. `both` 수 floor는 relevance 안전성을 증명하지 못한다.

특히 09/10의 q08~q12를 본 뒤 raw-score `margin`, minimum score, 어떤 `both`를 고를지, 혹은 budget을 정하면 “이 four queries에서만 boundary crossing을 만들도록” decision surface를 맞추는 것이다. cross-encoder logit은 model revision·tokenizer·passage format에 종속되고 calibration되지 않았으므로, `0.12` 같은 수치를 현 결과에서 골라 일반화 근거라고 부를 수 없다. 이는 94 §6.3의 결과-본-뒤 변경 금지와 같은 과적합이다.

따라서 A를 굳이 진행하려면 아래 모두가 선행해야 한다.

- A를 P3 수정이 아닌 **독립 candidate identity**로 선언한다. 96의 sealed split·score·lock 결과를 재사용하지 않는다.
- 09/10 및 새 sealed와 겹치지 않는 pre-registered calibration split에서만 model revision, document format, `margin`, `both_floor=B-1`, budget=1을 한 번 freeze한다. calibration query의 endpoint/phrase에 맞춘 예외는 금지한다.
- freeze 뒤에는 전혀 새로 저작·미개봉한 sealed split을 만들고, 모든 결과가 A의 HARD와 효과 기준을 통과해야 한다. calibration·sealed 모두에서 margin/budget/floor 재튜닝은 없다.

### 3.3 A 보존·회귀 gate

| gate | 사전 고정 조건 | 성격 |
| --- | --- | --- |
| exact/fallback/candidate parity | A 전 pool, exact prelude, fallback은 baseline byte-identical | HARD |
| demotion bound | query당 final `both` 손실 ≤1, `both_count_final ≥ B-1`, exact 손실 0 | HARD |
| demotion trace | displaced/refill id, rank, score, margin, why-no-op을 전 query에 기록 | HARD |
| C1·accepted | C1 gross loss 0, `regressed_accepted=[]` | HARD |
| route-pair | root와 child 각각 capped rank delta ≤0, 모든 pair non-regression | HARD |
| C6 | coverage·complete@10 baseline 이상; all-of 한 답만 올린 경우를 별도 실패로 숨기지 않음 | HARD |
| effectiveness | sealed에서 Recall@10 순증, MRR/nDCG 비감소, demotion이 실제 accepted gain으로 연결됨 | 승급 |

A가 이 gate를 통과해도 “both evidence 하나는 버릴 수 있다”는 제품 정책 비용이 남는다. B가 overlap score 자체를 바로 검증할 수 있으므로 A를 선행하거나 P3와 병합하지 않는다.

## 4. B — RRF overlap 보정 (우선 권고)

B는 keyword arm의 `text_tsv` query, vector query, top-50 width, endpoint fields, exact prelude, fallback을 그대로 둔다. 바꾸는 것은 **동일한 두 ranked lists를 합치는 RRF 식 하나**다. 따라서 fixed route boost, route-family postprocessor, weighted `search_tsv`가 아니며, P1/P2처럼 후보를 추가·치환하지도 않는다.

모든 B variant는 다음 공통 불변식을 갖는다.

- arm별 ref-id/rank와 union candidate set은 baseline과 완전히 같다.
- `match_type`은 arm 존재 표기라 score 정책과 독립적으로 그대로다.
- exact prelude·fallback은 byte-identical이고, flag off는 기존 RRF와 byte-identical이다.
- 후보별 route/path/summary/accepted-label을 score 식에 넣지 않는다.

### 4.1 B1 — `RRF_K` 하향, 단독 ablation

**B1 후보 식:** 기존 합산식은 유지하고 `k=60`만 사전 고정 `k=20`으로 바꾼다.

```text
s_B1(d) = 1 / (20 + r_kw(d)) + 1 / (20 + r_vec(d))
```

낮은 `k`는 arm 존재 자체보다 arm 내부 rank 차이를 크게 만든다. top-50 최하위 `both`는 `2/70=0.02857`, vector 1위 single은 `1/21=0.04762`가 되어 §2.1의 universal dominance가 사라진다. 이는 q08/q09/q11/q12의 vector rank 4~15 single이 poor-rank `both` decoy를 앞설 통로를 만든다. 단, 특정 query의 실제 both ranks에 따라 top-10 회복은 보장되지 않는다.

`k=20`은 “q12를 회복시키는 값”으로 09 결과 뒤 고른 것이 아니라, 50-wide 안 lower-tail overlap보다 head single의 rank evidence를 우선한다는 architecture-level 선택으로 이 문서에서 한 번 고정한다. `k=10/30/40/48` sweep, env 노출, query별 k, legacy 결과 뒤 k 재선택은 금지한다.

### 4.2 B2 — second-arm contribution cap, 단독 ablation

**B2 권고 후보 식:** stronger arm의 full evidence에 weaker arm의 절반만 더한다. `k=60`은 유지하고 `alpha=0.5`를 아래처럼 사전 고정한다.

```text
s_kw  = 1 / (60 + r_kw)
s_vec = 1 / (60 + r_vec)
s_B2  = max(s_kw, s_vec) + 0.5 * min(s_kw, s_vec)
```

single은 기존 한 항과 동일하고, `both`만 두 번째 evidence가 half-credit이 된다. top-50 최하위 `both`는 `1.5/110=0.01364`로 vector 1위 single `1/61=0.01639`보다 낮아져 universal dominance를 제거한다. B1과 달리 strong arm의 rank 곡선은 `k=60` 그대로지만, agreement bonus의 상한을 직접 제한한다.

**B2 대안(이번 우선 후보 아님): keyword arm weight 하향**은 `s = w_kw/(60+r_kw) + 1/(60+r_vec)`, 예를 들어 `w_kw=0.5`로 같은 목적을 낼 수 있다. 그러나 keyword-only도 함께 낮춰 C1/direct lexical 결과를 불필요하게 위험에 노출한다. 이 형태는 B2 cap과 결합하지 않으며, 별도 architecture로 다시 승인·freeze하지 않는 한 구현/평가하지 않는다.

### 4.3 B1과 B2의 관계·선택 금지

B1은 rank sensitivity 전체를, B2는 overlap bonus만 바꾼다. 둘을 합치면 두 change가 어느 boundary crossing을 만들었는지 알 수 없으므로 **B1 vs B2 cap은 각각 baseline 단독 ablation**이다. 둘 중 하나의 legacy diagnostic을 보고 다른 쪽의 `k`/`alpha`/weight를 조정하지 않는다. 선택은 이 문서의 pre-registered candidate identity(B1 `k=20`, B2 `k=60, alpha=.5`) 그대로 이루어져야 하며, 어떤 variant도 새 sealed split 성공 전 승급하지 않는다.

## 5. final-cut 8건 대조와 P3 관계

| query | P0 메커니즘 | A B-1 | B1/B2 단독 | 현 P3 lock | B 통과 뒤 별도 P3 |
| --- | --- | --- | --- | --- | --- |
| q05 refund | keyword 공백, vector/base-wide 35 | 영향 없음 | **효과 0** — one-arm 순서는 식이 바뀌지 않음 | 회복 후보 | 회복 후보 |
| q06 create issue | keyword 공백, 16 | 영향 없음 | **효과 0** | 회복 후보 | 회복 후보 |
| q17 list + create issues | keyword 공백, 12/25 | 영향 없음 | **효과 0** | 회복 후보, C6 필요 | 회복 후보, C6 필요 |
| q18 charge currency | keyword 공백, 42 | 영향 없음 | **효과 0** | 회복 후보 | 회복 후보 |
| q08 cancel subscription | top-10 all `both`, vector-only 15 | 가능성: 1 slot | **직접 표적** | 불가 | B가 single을 final에 올린 뒤 P3가 그 slot 재정렬 가능 |
| q09 delete repository | top-10 all `both`, vector-only 10 | 가능성: 1 slot | **직접 표적** | 불가 | 동일 |
| q11 customer root | top-10 all `both`, vector-only 8 | 가능성: 1 slot | **직접 표적** | 불가 | 동일 |
| q12 list pulls | top-10 all `both`, vector-only 4 | 가능성: 1 slot | **직접 표적** | 불가 | 동일 |

B에는 keyword가 없는 q05/q06/q17/q18의 RRF order를 바꿀 항이 하나도 없다. 반대로 P3는 current lock 아래 q08~q12에 slot을 만들 수 없다. 따라서 B와 P3의 순차 조합만이 두 4건군을 동시에 겨냥한다. 하지만 B의 새 final에 있는 `both` set을 기준으로 P3 lock과 effect cap이 다시 정의되므로, 96의 결과를 재사용해 결합 효과라고 주장할 수 없다.

## 6. 전역 RRF 변경의 리스크와 정량 gate

B는 RRF union의 모든 질의·모든 slot 순서를 바꿀 수 있다. 4개의 observed saturation query만 움직이는 local patch가 아니므로, aggregate gain은 C1 direct hit·root/child family pair·기존 accepted answer의 손실을 상쇄할 수 없다. 특히 B1은 keyword high-rank와 vector high-rank의 상대 영향 전부를, B2 keyword-weight 대안은 keyword-only 후보까지 바꾼다.

### 6.1 보존·회귀 HARD

| 측정 | 수치/조건 | 성격 |
| --- | --- | --- |
| parity | B 전 arm top-50 ref/rank, union, exact/fallback은 baseline byte-identical | HARD |
| C1 | direct/exact C1 gross hit loss **0** | HARD |
| accepted | `regressed_accepted=[]`; baseline top-10 accepted가 candidate에서 하나라도 top-10 밖이면 FAIL | HARD |
| route-pair | 전 route pair에서 root와 child 각각 `delta ≤ 0`; non-regression **100%** | HARD |
| C6 | all-of coverage 및 complete@10이 baseline 이상 | HARD |
| category | C1~C7 각각 R@10 hit 순감소 **0**, category MRR 하락 **0.000** | HARD |
| stability | 같은 config 3회 final id/rank/match_type 동일, flag-off baseline byte-identical | HARD |
| overlap accounting | query별 final `both` count, final에 새 진입한 single, final에서 빠진 both, accepted 여부를 전량 trace | HARD 기록 |

이 gate는 “both를 몇 개 줄였는가”를 성공으로 삼지 않는다. B의 목적은 overlap decoy를 낮추는 것이지 both evidence를 기계적으로 제거하는 것이 아니다.

### 6.2 효과·sealed gate

09/10은 원인 진단과 legacy ablation만 한다. q08/q09/q11/q12 recovery 수, k/alpha, fixture를 그 결과 뒤 바꾸지 않는다. B1/B2 중 별도 승인된 한 candidate identity를 freeze한 뒤에만 아래 신규 split을 만든다.

- 새 sealed split은 기존 09/10, P1/P2/P3 legacy, v3 sealed와 query·accepted endpoint·route-pair family가 겹치지 않게 한다. 최소 scored 96 + unopened holdout 24이며, Korean/English, C1 direct, root/child, all-of, “keyword+vector overlap이 높고 answer가 single-arm” strata를 사전 층화한다.
- sealed effectiveness는 baseline 대비 Recall@10 **+5 percentage points 이상**, MRR 및 nDCG@10 각각 **비감소**, overlap-saturation stratum accepted hit **순증**, empty result 증가 0을 요구한다. §6.1 HARD 하나라도 FAIL이면 aggregate 효과와 무관하게 승급하지 않는다.
- raw query/split/corpus SHA, `k` 또는 `alpha`, arm width, RRF formula version, code SHA를 manifest에 고정한다. one sealed run 뒤 파라미터 변경·동일 split 재시험은 금지한다.

## 7. 반려 이력과의 경계

과거 fixed route boost는 root/path/family 같은 endpoint 구조를 사전 우대하고, weighted `search_tsv`/text-primary는 lexical field와 `ts_rank` 자체를 바꿔 route-pair sibling의 arm 내 순위를 흔들었다. B는 이 어느 것도 하지 않는다. text `ts_rank`, vector cosine, arm top-50 및 endpoint 표현은 그대로이고, 두 ordered list의 **공통 등장에 더해지는 두 번째 RRF 항만** uniform하게 조정한다. 그렇다고 안전하다는 뜻은 아니다. 영향 범위가 route-specific이 아니라 전역이므로 §6의 C1/accepted/route-pair HARD가 더 강하게 필요하다.

## 8. P3와의 순서·롤백

1. B1 또는 B2 cap 하나를 baseline 단독으로 평가한다. B1+B2, B+P3, A+B, A+P3 결합은 첫 평가에서 전부 금지한다.
2. 선택된 B가 새 sealed split의 §6 gate를 통과하고 lead가 새 RRF base로 승급을 승인한 경우에만, 그 상태에서 새 P0 trace를 만들고 96의 P3를 **새 candidate identity**로 다시 설계·평가한다.
3. B flag off는 exact/RRF/fallback의 기존 결과가 byte-identical이어야 한다. A/P3 flag도 기본 off 유지다. 어느 후보든 one-setting rollback으로 baseline RRF를 복원할 수 있어야 한다.

이 문서는 구현 순서 지시가 아니다. B1/B2 중 무엇을 developer에게 구현시킬지, 혹은 B 자체를 보류할지는 lead의 별도 승인 대상이다.

