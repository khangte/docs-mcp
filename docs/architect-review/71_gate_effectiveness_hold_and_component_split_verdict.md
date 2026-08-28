# 71. shared-index gate 효과성 미달·component 분리 판정

- 대상: 수정 candidate `608731b`, 70번 shared-index gate 재실행
- fixture/query: `ed7852e`, query SHA `6eb897d2…` 불변
- 상태: **결합 candidate 승급 보류. holdout 계속 봉인. variants 대칭화 단독 후보로 재검증**

## 1. 결과 요약

70번에서 요구한 안전성·측정 무결성 문제는 모두 해소됐다.

- 같은 물리 인덱스 fingerprint로 4회 실행
- 결정성 preflight PASS
- fallback control 완전 동일
- p09 child 회귀 해소
- p03 root baseline/candidate 동일
- 69번 §7.1 HARD 8/8 PASS

그러나 69번 §7.2 EFFECTIVENESS는 다음 두 항목이 미달이다.

- OFF Recall@10: 56→57%, 요구 +3%p 미달
- OFF gate route pair effective: 1쌍, 요구 2쌍 미달

ON은 R@10 68→80%, MRR .331→.575, C2 R@3 32→74%로 관련 효과성 항목을
모두 통과했다.

## 2. 쟁점 1 — 현재 candidate 상태

**판정: 69번 §7.3대로 보류한다.**

HARD PASS는 “안전성·회귀 가드를 통과했다”는 뜻이고 EFFECTIVENESS PASS를 대신하지
않는다. OFF 미달을 ON 대폭 개선으로 상쇄해 결합 candidate를 승급하면, 프리즈 전에
정한 “안전하면서 실익이 있는가”의 두 번째 절반을 결과를 본 뒤 제거하게 된다.

`608731b`를 실패 폐기할 필요는 없지만 현재 SHA 그대로 main 승급 대상으로 승인하지
않는다. holdout/all도 실행하지 않는다.

## 3. 쟁점 2 — 결합 candidate에 OFF 게이트를 계속 적용하는가

**판정: 계속 적용한다. 임계값 소급 완화는 반려한다.**

결합 candidate의 제품 범위는 두 가지다.

1. query variants가 있을 때 keyword arm을 대칭화하는 B
2. variants 없이도 operation intent와 path specificity로 family를 재배열하는 A

A는 68번부터 명시적으로 OFF 경로의 route-family 문제를 고치기 위한 레버였다. 결합
candidate에서 OFF +3%p와 pair effectiveness를 제거하면 A의 일반화 실익을 검증할
항목이 사라진다.

“프로덕션 기본은 ON에 가깝다”는 주장도 현재 계약상 게이트 변경 근거가 아니다.

- `query_variants`는 MCP optional 입력이다.
- 서버는 별도 LLM으로 variants를 생성하지 않는다.
- docstring 안내는 호출자의 semantic compliance를 강제하지 못한다.
- 영어 질의와 variants 누락 호출은 계속 유효하다.

따라서 결합 후보에서 OFF는 실제 지원 경로이며 효과성 게이트를 통과해야 한다.

## 4. 쟁점 3 — 다음 액션

### 4.1 판정: B-only 후보를 분리한다

route-family rerank를 gate96에 맞춰 추가 튜닝하는 안보다 **keyword variants 대칭화만
분리한 새 candidate**를 먼저 검증한다.

근거:

- OFF pair 10/10 비회귀로 A의 안전성은 확인됐지만 effective는 1/10뿐이다.
- OFF headline 실익도 +1%p로 사전 최소치에 못 미친다.
- 반면 ON 개선은 B의 activation 조건과 정확히 일치하고 폭이 크다.
- intent lexicon·specificity tie-break를 gate 질의에 맞춰 더 늘리면 gate96을 튜닝셋으로
  과사용하고 child 회귀 면적을 다시 키울 위험이 있다.

B-only 제품 diff는 baseline 검색 경로에 다음만 더한다.

- endpoint RRF의 `_search_keyword_with_variants`
- 원문과 각 nonblank variant를 독립 keyword 검색
- ref_id별 최소 rank 병합

제외할 것:

- `rerank_endpoints_by_route_family` 호출
- route-family용 wide hydrate·constrained permutation
- A 전용 intent/path specificity production 코드

generic vector variants 배선은 baseline에 이미 있던 기능이므로 유지한다. shared-index
runner와 결정성 preflight는 제품 검색 변경이 아니라 평가 fixture로 유지한다.

### 4.2 A-only/결합 후보 처리

A를 삭제 판정하는 것은 아니다. `608731b`의 route-family 구현과 테스트는 후속 실험
브랜치 근거로 보존할 수 있다. 다만 v1 승급 후보에서는 제외한다.

A를 다시 추진하려면 다음 중 하나가 먼저 필요하다.

- gate 결과를 보지 않고 설명 가능한 새 intent/resource 일반 규칙
- 실제 호출 로그에서 variants 없는 root/child 실패의 유의미한 빈도
- A-only ablation에서 69번 OFF 효과성 기준을 넘는 증거

현재 gate의 특정 pair를 맞히기 위한 token alias·예외·boost 추가는 하지 않는다.

### 4.3 B-only 분리는 임계값 완화가 아니다

B-only에서 OFF는 기능 비활성 조건이다. variants가 없으면 새 helper가 원문을 한 번
검색하므로 baseline과 완전히 같아야 한다. 이 후보에 OFF +3%p를 요구하면 “기능이
꺼졌을 때 효과를 내라”는 모순이 된다.

따라서 후보 범위를 먼저 B-only로 바꾸고, 실행 전에 아래 component-specific 게이트를
고정한다. 이미 측정한 결합 candidate 수치를 B-only PASS로 대체 사용하지 않는다.

## 5. B-only gate 계약

### 5.1 HARD

| 항목 | PASS |
|---|---|
| fixture/index 무결성 | 70번 shared-index fingerprint·결정성 preflight 유지 |
| fallback OFF/ON | baseline과 per-query capped rank 완전 동일 |
| RRF OFF | baseline과 96건 per-query capped rank **완전 동일** |
| RRF ON category | 69번 category 회귀 기준 유지 |
| C1/C6 | C1 hit loss 0, C6 coverage/complete baseline 이상 |
| route pair OFF | 10쌍 root/child 모두 baseline과 완전 동일 |
| route pair ON | 10/10 non-regression; effective 최소치는 요구하지 않음 |
| empty result | OFF/ON baseline보다 증가하지 않음 |

pair effectiveness를 B-only에 요구하지 않는 이유는 이 component가 route specificity
수정이 아니라 variants의 keyword arm 전달 대칭성을 고치는 것이기 때문이다. 다만
variants 때문에 child가 밀리는 회귀는 ON pair non-regression이 계속 막는다.

### 5.2 EFFECTIVENESS

OFF에는 개선 임계값을 두지 않고 §5.1의 완전 동일을 요구한다. ON scored gate96에서:

| 항목 | PASS |
|---|---|
| Recall@10 | baseline ON 대비 **≥ +3.0%p** |
| MRR | baseline ON 대비 **≥ +0.02** |
| nDCG@10 | baseline ON 이상 |
| targeted C2+C3+C5 | top-10 순증 3건 이상, 순감소 없음 |
| 한국어 cohort | baseline ON 대비 top-10 hit 순증 2건 이상 |
| C2 | Recall@3 baseline ON 이상, hit 순감소 없음 |

이는 69번의 ON 관련 최소치를 낮추지 않고 activation 조건에 맞게 분리한 것이다.

### 5.3 실행 순서

1. developer가 B-only candidate SHA를 만든다.
2. 같은 shared index에서 baseline/B-only × OFF/ON gate 4회를 새로 실행한다.
3. §5.1·§5.2를 처음부터 판정한다.
4. 전항 PASS일 때만 lead가 B-only candidate로 holdout을 연다.
5. holdout에서도 OFF per-query 완전 동일, ON은 69번 sealed holdout HARD와 방향성
   기준을 적용한다.

현재 결합 candidate의 ON 결과를 B-only 결과로 간주하지 않는다. A가 ON 순위에 기여했을
가능성이 있으므로 ablation 실행이 필수다.

## 6. 쟁점 4 — holdout

**판정: 계속 봉인한다.**

현재 gate는 HARD는 통과했지만 결합 candidate EFFECTIVENESS가 미달했고, B-only는 아직
실행 전이다. 어느 후보도 holdout 개봉 조건을 충족하지 않았다.

- `queries_gate_v1.json`·query SHA·split 불변
- holdout 실행 금지
- B-only gate PASS 뒤 그 SHA 하나에 대해서만 최초 개봉

B-only로 holdout을 개봉하면 그 24건은 이후 A 튜닝의 sealed holdout으로 재사용하지
않는다. route-family A를 후속 승급하려면 새 candidate와 새 sealed split/version이
필요하다.

## 7. 최종 판정표

| 쟁점 | 판정 |
|---|---|
| 1. §7.3 보류인가 | **예. `608731b` 결합 candidate 보류** |
| 2. OFF 게이트 유지 | **결합 candidate에는 유지. 소급 완화 반려** |
| 3. 다음 액션 | **B-only 분리 → component-specific gate 재실행** |
| 4. holdout | **계속 봉인** |

큰 ON 개선은 버리지 않되, 효과가 검증되지 않은 A를 함께 통과시키거나 프리즈한 기준을
낮추지 않는다. component 경계와 activation 조건에 맞는 새 후보로 같은 gate에 다시
답하게 한다.
