# 93. P2 bounded arm-exclusive rescue 효과성 판정

- 검토 대상: `DOCS_MCP_SEARCH_ARM_RESCUE_QUOTA` candidate 구현 워킹트리
- 측정 근거: `docs/eval-results/10_2026-08-31_p0_arm_trace_reclassification.md`,
  `docs/eval-results/11_2026-08-31_p2_arm_rescue_eval.md`
- 설계 기준: `docs/architect-review/92_corpus_eval_search_logic_improvement_review.md` §6
- 판정: **구현 정합성 PASS, 제품 후보 P2는 보류·비승급. 기본값 0 유지, P1 설계로 전환.**
- 비승인: quota 활성화, quota 상한 확대, 선택 키의 의미 있는 변경, 신규 sealed split 개봉

## 1. 검토 결과

구현은 92 §6의 frozen P2 형태를 지켰다.

- RRF arm rank, RRF score, `RRF_K`, arm weight는 그대로 두었다.
- RRF base-wide의 컷 밖 `match_type != "both"`만 대상으로 삼았다.
- base-wide 순서로 정하고 final tail slot만 치환했으며, 최소 한 개의 base RRF 슬롯을 남긴다.
- env 기본값은 `0`이고, 잘못된 값은 0으로 degrade하며, 상한은 3이다.
- P2 관련 단위/RRF 테스트 67개와 변경 파일 ruff 검사를 이번 검토에서 재실행해 통과했다.

따라서 이는 구현 결함이나 설계 이탈 반려가 아니다. 그러나 구현이 안전장치를 통과했다는
사실과 제품 후보로서 충분한 효과가 있다는 것은 별개다.

## 2. 효과성 판정 — 승급하지 않는다

| quota | R@1 | R@3 | R@10 | MRR | nDCG@10 | miss@10 | legacy에서 회복한 질의 |
|---:|---:|---:|---:|---:|---:|---:|---|
| 0 | .25 | .35 | .45 | .318 | .350 | 11 | — |
| 1 | .25 | .35 | .45 | .318 | .350 | 11 | — |
| 2 | .25 | .35 | .50 | .323 | .365 | 10 | q17 |
| 3 | .25 | .35 | .55 | .329 | .380 | 9 | q12, q17 |

quota 3은 legacy 20건에서 R@10을 2건(+10%p) 높이고 MRR/nDCG도 비감소시킨다. 하지만
이 효과는 다음 이유로 승급 근거가 되지 않는다.

1. 최소 교란 설정인 quota 1은 20개 질의 모두에서 출력만 바꾸고 어떤 품질 지표도 바꾸지
   못했다. 효과는 quota 2부터 생기며, bounded rescue의 가장 좁은 activation이 정답을
   선택하지 못한다는 뜻이다.
2. quota 3의 회복은 final-cut 8건 중 q12와 q17 두 건뿐이다. 나머지 여섯 정답은
   base-wide 16~42위여서 이 bounded window의 밖이다. 이 후보의 legacy 효과 상한이 작다.
3. R@1과 R@3은 전 quota에서 불변이다. 현재 제품의 주 갭인 early precision/nDCG를 직접
   고치지 못하며, P3 이전의 recall 확보 레버로도 coverage가 좁다.
4. quota 3은 20 질의에서 59개 slot을 치환하고, 그중 34개는 기존 `both` 결과를 축출한다.
   accepted regression 0은 좋은 safety 신호이지만, 20개 exposed 질의에서의 무회귀가 이
   넓은 slot 교체의 일반화를 보장하지 않는다.
5. 92 §6.4가 요구한 신규 sealed split의 paired Recall@10 순증 검증은 아직 없다. 이 단계에서
   새 split을 만들어 비용을 더 쓰기에는 legacy 효과가 두 질의에 국한된다.

따라서 quota 3을 운영 활성화하거나 sealed 평가 후보로 승격하지 않는다. 구현은 기본값 0의
dark candidate로만 남길 수 있으나, P2를 계속 다듬거나 활성화하기 위한 구현·측정은 승인하지
않는다.

## 3. “base-wide 순서 → 자기 arm rank” 제안 판정

### 3.1 literal 변경은 효과가 없는 동치 변환

P2 rescue 대상은 모두 `match_type != "both"`, 즉 keyword 또는 vector 중 정확히 한 arm에만
존재한다. 이 후보 `d`의 RRF 점수는 다음 한 항뿐이다.

```text
score(d) = 1 / (60 + rank_of_the_only_arm(d))
```

분모가 작을수록 점수가 크므로, 현재 `base_wide`의 RRF 내림차순은 rescue 대상 안에서는 이미
그 후보의 **자기 arm rank 오름차순**이다. 동점은 현재도 `ref_id`로 결정적 처리된다.

그러므로 “단일-arm 후보를 base-wide 순서 대신 자기 arm rank 순서로 고른다”는 literal 변경은
결과가 같아야 한다. q12가 vector arm 4위이지만 quota 3에서야 rescue된 이유는 그보다 앞선
단일-arm 후보가 있었기 때문이지, base-wide가 자기 arm rank를 무시했기 때문이 아니다.

이 literal 변경은 과적합이라기보다 **no-op**이므로 구현할 이유가 없다.

### 3.2 q12 등을 quota 1로 우대하는 의미 있는 변경은 새 후보이며, 지금은 반려한다

만약 제안의 뜻이 다음 중 하나라면, 이는 단순 선택 키 치환이 아니다.

- keyword/vector 각각에 별도 quota를 배정
- 특정 arm을 먼저 고르거나 arm별 rank를 다시 정규화
- base top-k에 든 후보를 제외한 뒤 새로운 arm-local 순서를 만들기
- 특정 vector rank 범위, query 유형, route family에 우선권을 주기

이들은 P2 candidate의 selection function과 competition contract를 바꾸는 **새 architecture**다.
q12/q09/q11의 P0·P2 결과를 본 뒤 quota 1의 승자를 바꾸려는 목적이라면, 92 §6.3이 금지한
결과-관측 후 quota/선택 규칙 튜닝에 해당한다. legacy 20과 P0 trace는 원인 진단용으로만
남겨야 하며, 이 데이터를 기준으로 새 arm-local 규칙을 계속 조정하지 않는다.

결론적으로 현 제안은 두 경우 모두 진행하지 않는다.

| 해석 | 판정 | 이유 |
|---|---|---|
| 단일-arm의 자기 arm rank로만 재정렬 | **불필요** | 현 RRF/base-wide 순서와 동치라 효과 없음 |
| q12 등을 더 앞세우는 추가 arm-local 규칙 | **반려** | 결과를 본 뒤 P2 selection contract를 바꾸는 새 후보·과적합 |

## 4. 다음 단계

### 4.1 P1 vector-only reformulation/decomposition으로 전환

다음 제품 후보는 92 §5의 **P1**이다. P0 trace에서 P2가 건드릴 수 없는
generation miss가 q04, q07, q10 세 건으로 확인됐고, P2를 이어서 확대해도 이 세 건은
회수할 수 없다.

P1은 P2와 결합하지 않고 별도 candidate identity로 설계한다. 설계에서 다음을 먼저 고정한다.

1. vector-only subquery의 생성 방식과 최대 개수
2. supplied variant, deterministic reformulation, clause decomposition의 출처별 계약
3. keyword arm이 완전히 불변임을 보이는 trace
4. source language/영문 의역/다개념별 효과와 route-pair 회귀 게이트
5. legacy 방향 진단 뒤 사용할 신규 sealed split 및 사전 효과성 하한

q04/q07은 KO→EN root 표현, q10은 `billing history`↔`invoices` 영문 표현 갭이라는 서로 다른
generation miss이므로, 단순 q04/q07 전용 번역 사전이나 q10 전용 alias를 바로 제품 상수로
넣지 않는다.

### 4.2 P3는 후속 보류

P3 cross-encoder는 현재 top-10 안의 q13/q15/q19/q20을 1위로 올려 R@1, MRR, nDCG를 높일 수
있지만, P2와 동일하게 generation miss를 해결하지 못한다. P1이 production-wide recall을
어느 정도 회복한 뒤에 P3의 candidate-set parity와 oracle upper bound를 다시 평가한다.

## 5. 최종 판정표

| 쟁점 | 판정 |
|---|---|
| P2 구현의 설계 정합성 | **PASS** |
| P2 quota 1~3 운영 활성화 | **불승인** |
| P2 신규 sealed split·추가 튜닝 | **보류/미승인** |
| base-wide → 자기 arm rank literal 변경 | **불필요(no-op)** |
| q12 등을 우대하는 의미 있는 키 변경 | **반려 — 새 후보·결과후 과적합** |
| 다음 제품 후보 | **P1 vector-only reformulation/decomposition 설계** |
| P3 | **P1 뒤 후속 보류** |

이 판정은 P2 구현을 실패한 코드로 취급하지 않는다. 다만 bounded rescue가 legacy 20의
경계에서 두 건만 회수하고 @1/@3을 움직이지 못했으므로, 더 큰 quota나 결과-맞춤 선택 규칙으로
연장하지 않고 후보를 여기서 멈춘다.
