# 98. B1/B2 RRF ablation 판정 및 P0 좌표 모순 해소

- 입력: `docs/eval-results/13_2026-08-31_b1_b2_rrf_ablation_eval.md`,
  `docs/eval-results/10_2026-08-31_p0_arm_trace_reclassification.md`,
  `docs/architect-review/97_both_arm_saturation_remediation_options.md`
- 판정: **B1/B2 비승급·코드 되돌림. P0 q08/q09/q11/q12 재분류는 유지. sealed split 미진행.**

## 1. 결론

`13`의 “q08/q09/q11/q12 accepted가 fused wide-list(width=50)에도 없다”는 결론은 측정 코드가
뒷받침하지 않는다. 해당 harness는 `cs.search(query, CandidateSearchOptions(top_k=10))`의
**최종 반환 10개만** `rank()`로 읽는다. arm top-50, `reciprocal_rank_fuse(..., top_k=50)`의
base-wide, accepted의 wide rank 어느 것도 계측하지 않는다. 그러므로 그 코드에서의 `None`은
“final top-10에 없음”일 뿐 “wide/arm에 없음”이 아니다.

P0는 같은 corpus와 production `search(top_k=10)` 한 번에서 keyword/vector 각각 top-50 및
fused top-50을 read-only로 기록했다. q08/q09/q11/q12의 vector rank는 각각 15/10/8/4,
base-wide rank는 38/25/38/40이었다. **실제 실패 단계는 계속 final_cut**이며, B harness의
coordinate omission이 P0를 generation miss로 보이게 한 것이다.

다만 B1/B2의 제품 판정은 별개다. 올바른 final 좌표에서 B1(`k=20`)과 B2(`alpha=.5`) 모두
accepted top-10·R@10·MRR·nDCG를 전혀 개선하지 못했다. B1은 q08/q09에서 final `both` 수만
10→9로 바꿨고 정답은 올리지 못했으며, B2는 그 미시 변화도 없다. HARD PASS는 output
무변경/무회귀가 만든 trivial PASS일 뿐 effectiveness 증거가 아니다.

## 2. 좌표 대조 — 어느 기록이 무엇을 측정했는가

| 항목 | P0 (`10`) | B eval (`13` / `scratchpad/eval_b.py`) | 판정 |
| --- | --- | --- | --- |
| 호출 | `search(top_k=10)` 질의당 1회 | `search(top_k=10)` 질의당 1회 | production final 호출은 같음 |
| keyword/vector arm | 실제 각 `top_k=50` 협력자 호출을 래핑해 ref/rank 기록 | 기록 없음 | B eval은 arm 부재를 주장할 수 없음 |
| fused wide | 실제 `reciprocal_rank_fuse(..., top_k=50)` 결과를 기록 | 기록 없음 | B eval의 `wide rank=None`은 허위 좌표 라벨 |
| final | 반환 final top-10 기록 | 반환 final top-10만 `_rank()`로 기록 | 두 문서 모두 final miss는 확인 |
| q08~q12 `None`의 의미 | P0 wide rank 38/25/38/40 뒤 final 밖 | B eval final top-10 밖 | 같은 `None`이 아님 |
| base parity | 계측 OFF/ON final id/path/match_type 완전 일치 | 09 headline의 5개 aggregate 수치 오차 <0.005 | B eval의 parity는 per-query/wide parity가 아님 |

`eval_b.py`의 `b_targets_rank`는 `runs[qid]["rank"]`이고, 이 값은 `_rank(cands, accepted)`에서
`cands = cs.search(... top_k=10)`으로만 계산된다. `base_wide` 변수·arm wrapper·wide slice가
코드에 없다. 따라서 `13` §3.3과 §6의 “fused wide-list rank None” 및 “candidate-generation
miss” 문장은 **측정 결과가 아니라 final-miss 값을 잘못 해석한 것**이다.

## 3. P0 유지 근거와 실제 실패 단계

P0의 commit `b0aa1a8`에서 B 평가 HEAD `c20c01d`까지 endpoint retrieval에 들어간 변화는 P2
arm-rescue뿐이며, 기본 quota `0`은 `base_wide[:top_k]` no-op이다. 나머지 endpoint chunk
변경은 formatting-only다. B의 base headline도 09와 일치하고 q08/q09/q11/q12의 base final
`both_in_final=10`은 P0 관측과 일치한다. 따라서 현 증거에는 vector arm이 P0 이후 달라졌다고
볼 근거가 없다.

| query | P0 vector arm | P0 base-wide | actual stage | B1/B2가 하지 못한 일 |
| --- | ---: | ---: | --- | --- |
| q08 DELETE subscription | 15 | 38 | final_cut | low-rank `both`를 하나 밀었어도 rank-38 target을 top-10까지 올리지 못함 |
| q09 DELETE repository | 10 | 25 | final_cut | 동일; B1의 single 진입은 accepted가 아니었음 |
| q11 GET customers | 8 | 38 | final_cut | B1/B2 모두 cutline 위로 올리지 못함 |
| q12 GET pulls | 4 | 40 | final_cut | B1/B2 모두 cutline 위로 올리지 못함 |

이는 `97` §2의 universal dominance 수학을 부정하지 않는다. 그 식은 top-50 안에서 poor-rank
`both`가 head single보다 앞서는 충분조건을 설명한다. 그러나 B1/B2의 고정된 두 식만으로 이
corpus의 actual competing ranks를 10위 경계 너머까지 움직일 수 있다는 보장은 없었고, 그
효과성 가설은 실패했다.

## 4. B1/B2 처분

| 항목 | B1 `k=20` | B2 `alpha=.5` | 처분 |
| --- | --- | --- | --- |
| 09/10 headline 및 accepted top-10 | 변화 0 | 변화 0 | effectiveness FAIL |
| B target | q08/q09 final `both` 10→9이나 accepted 회복 0 | 변화 없음 | target effectiveness FAIL |
| C1/route-pair/C6/accepted | PASS | PASS | 모두 no-op 또는 무해한 미시 변화의 trivial safety PASS |
| sealed split | 미실행 | 미실행 | 올바름 — trigger되지 않음 |
| 코드 | global RRF tuning surface 추가 | global RRF tuning surface 추가 | **dark candidate로 보존하지 않고 되돌림** |

P2는 quota 2/3에서 R@10·MRR·nDCG의 실제 순증이 있어 기본 off dark candidate로 남겼다. 반면
B1/B2는 enabled 상태에서도 accepted 효과가 0이고, endpoint뿐 아니라 공용
`reciprocal_rank_fuse`의 global parameter surface를 늘린다. P1처럼 HARD 실패는 아니지만 P2와
같이 보존할 실험 신호도 없다. 따라서 developer는 config/composition/endpoint search/rrf 및 B
전용 tests의 여섯 파일을 **HEAD `c20c01d` 상태로 되돌리고**, B flags를 커밋하지 않는다.

`13` 평가 문서는 유지하되, 다음만 정정한다.

1. §3.3의 “fused wide-list 등수 None”을 “이 harness가 측정한 final top-10 rank None”으로 고친다.
2. §3.3/§6의 “candidate-generation miss” 및 P0 반증 문장을 삭제하고, wide/arm 좌표는 미측정이라고
   명시한다.
3. B1/B2가 top-10 effectiveness를 보이지 못했다는 나머지 수치·HARD 결과는 그대로 둔다.

P0 `10`은 수정하지 않는다. 원 raw trace가 현재 scratchpad에 없으므로, 아래 재감사가 완료될
때까지 P0 coordinate를 새 candidate 설계의 유일한 근거로 확장하지 않는다.

## 5. 다음 단계

1. B code를 되돌리고 `13`의 coordinate claim을 바로잡는다. 신규 sealed split은 만들거나 열지
   않는다.
2. 다음 후보 전에는 B 구현과 독립된 read-only P0 재감사를 수행한다. `10`과 동일하게 한 번의
   production `search(top_k=10)` 내부에서 arm top-50, fused top-50, final을 함께 기록하고,
   q08/q09/q11/q12의 method/path/ref-id를 대조한다. 이 작업은 ranking 변경이 아닌 진단이다.
3. 재감사가 P0를 재확인하면, B1/B2 값의 재튜닝·k sweep·alpha sweep은 하지 않는다. q08~q12는
   final-cut이지만 두 fixed RRF formulas가 무효였다는 상태로 남긴다.
4. P3는 여전히 독립된 ranking candidate이나 current both lock 아래 q08~q12를 해결하지 못한다.
   P3 자체의 effect cap을 보고한 뒤 별도 승인된 평가로만 진행할 수 있다. q08~q12를 다시 직접
   다루려면 P2/P1/B1/B2의 값을 연장하지 않는 새 retrieval architecture와 새 sealed split이
   필요하다.

