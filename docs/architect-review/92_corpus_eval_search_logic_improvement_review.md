# 92. corpus eval 기반 검색 로직 보완점 검토

- 요청 입력: `docs/eval-results/09_2026-08-31_corpus_eval.md`
- 대조 근거: `docs/eval-results/01`, `03`, `05`~`08`,
  `docs/architect-review/67`, `71`, `72`, `74`, `82`~`91`
- 제품 기준: HEAD `b0aa1a8`의 endpoint 검색 — exact prelude + text keyword/vector wide RRF,
  `fallback` 롤백 경로; `query_variants` 미제공
- 상태: **분석·후보 우선순위 판정 완료. 구현 승인 아님**
- 제외 범위: 반려된 structured lexical 전면 교체, text-primary bounded structured
  augmentation v3, keyword-variant rank 병합 재시도, 고정 route-family/path boost

## 1. 결론

09의 낮은 품질은 새 회귀가 아니라 01의 검색 토폴로지가 그대로 남은 결과다. RRF의
`Recall@1 25% / @10 45%, MRR 0.318, nDCG@10 0.350`은 01과 완전히 같고, 20건 중
정답 miss 11건도 동일하다. 진짜 빈 응답은 0건이므로 문제는 “결과가 없음”이 아니라
**관련 없는 endpoint가 정답보다 앞서거나 정답이 유효 후보폭에 들지 못하는 것**이다.

실패 11건 중 9건(81.8%)은 C2 한글 패러프레이즈 4건, C3 영문 의역 3건, C4 흔한 토큰
2건이다. 나머지는 C6 다개념 q17, C7 세부 필드 q18이다. 따라서 exact lookup이나 응답 수
보장보다 다음 세 병목이 우선이다.

1. 비영문·의역·다개념 질의를 endpoint 표현에 맞는 검색 질의로 바꾸는 신호가 부족하다.
2. flat endpoint chunk 안에서 root/child/sibling이 양 arm을 함께 점유하고, rank-only RRF가
   두 arm 교집합을 과도하게 우대한다.
3. 후보가 있어도 query와 endpoint를 직접 비교하는 최종 relevance 단계가 없어 2~10위
   decoy를 1위와 구분하지 못한다.

다만 03의 “FAMILY-RERANK 후보 5건 / CANDIDATE-GEN 실패 2건” 분류는 현재 증거로 그대로
사용하면 안 된다. 진단 스크립트가 `top_k=50`을 별도 호출하면서 내부 arm 폭을 200으로
늘리기 때문이다. 운영 `top_k=10`의 arm 폭은 50이다. 즉 03의 top50은 **운영 호출의
base-wide 50이 아니라 더 넓은 별도 검색의 최종 50**이다. P0 동일 실행 trace를 먼저
고치기 전에는 P1~P3의 투자 순서를 확정하지 않는다.

초기 우선순위는 다음과 같다.

| 순위 | 후보 | 판정 |
|---:|---|---|
| **P0** | 동일 실행 arm/production-wide trace 보정 | **필수 선행조건. 제품 순위 변경 금지** |
| **P1** | bounded vector-only query reformulation/decomposition | P0에서 candidate-generation miss가 우세하면 1순위 제품 실험 |
| **P2** | bounded arm-exclusive rescue/quota | P0에서 arm hit → RRF/top-10 miss가 우세하면 1순위 제품 실험 |
| **P3** | query-endpoint cross-encoder rerank | 후보 recall 확보 뒤 Recall@1/MRR/nDCG 개선용 2단계 후보 |

P1과 P2는 처음부터 결합하지 않는다. P0 결과에 따라 하나를 단독 candidate로 선택하고,
component별 효과와 회귀를 분리 측정한다. P3는 candidate-generation을 해결하지 못하므로
P1/P2보다 먼저 추진하지 않는다.

## 2. 09 지표가 말하는 실패 형태

### 2.1 지표 분해

RRF가 1위로 맞힌 것은 q01, q02, q03, q14, q16의 5건이다. 정답은 있으나 1위가 아닌
질의가 q13(6위), q15(2위), q19(5위), q20(2위) 4건이고, 나머지 11건은 top-10 miss다.

- 낮은 Recall@1은 **11건의 recall 실패 + 4건의 shallow-rank 실패**가 합쳐진 결과다.
- 낮은 nDCG@10은 11건이 0점이고, q13/q19가 각각 6위/5위에 머문 영향이 크다.
- empty result 0%는 검색기가 항상 무언가를 반환했다는 뜻일 뿐 정답 coverage 증거가 아니다.
- RRF와 fallback의 Recall@10은 둘 다 45%다. RRF는 새 정답을 top-10으로 들이지 못했고,
  이미 검출된 일부를 위로 올려 MRR만 `0.270 → 0.318` 개선했다.

현재 top-10 안의 2~10위 정답 4건을 어떤 완벽한 reranker가 전부 1위로 올려도, 새 후보를
회수하지 않는 한 Recall@1 상한은 Recall@10과 같은 45%다. 이때 이론상 지표 상한은 대략
`MRR 0.450`, `nDCG@10 0.450`이다. 따라서 목표 Recall@1 70%에 접근하려면 rerank 이전에
최소 5건 이상의 top-10 recall을 추가 확보해야 한다.

### 2.2 질의 유형별 09/03 대조

| 유형 | 09 결과 | 03 진단에서 확인된 것 | 현재 판정 |
|---|---|---|---|
| C1 직접 키워드 q01~q03 | 전부 1위 | 진단 대상 아님 | exact prelude가 method+path를, flat RRF가 직접 표현을 안정적으로 처리. 보호 대조군 |
| C2 한글 q04~q07 | 전부 miss | variants ON에서 q04 1위, q06 3위; q05 41위, q07 22위(03 좌표) | 교차언어 candidate 신호 부족이 확정적. q05/q07의 운영 width=50 포함 여부는 P0 전 미확정 |
| C3 의역 q08~q10 | 전부 miss | q08 39위, q09 24위, q10 top50 miss(03 좌표) | q08/q09도 production-wide 포함 여부 미확정. q10은 `billing history`↔`invoices` 표현 갭이 강하지만 arm별 generation miss는 아직 미입증 |
| C4 흔한 토큰 q11~q12 | 전부 miss | q11 top50 miss, q12 29위(03 좌표); q11 top-10은 customer child가 9/10 | flat chunk의 root/child 포화가 확정적. q11 root의 arm별 위치와 q12 production-wide 포함 여부는 미확정 |
| C5 decoy q13~q15 | 6/1/2위 | 03 대상 아님 | RRF가 fallback보다 q13·q14를 개선하지만 q13은 양 arm decoy를 못 이김. shallow-rank 문제 |
| C6 다개념 q16~q17 | 1위/miss | 03 대상 아님 | 단일 질의·단일 순위가 복수 의도 중 하나도 안정적으로 회수하지 못하는 경우 존재 |
| C7 endpoint 세부 q18~q20 | miss/5/2위 | 과거 29에서 긴 endpoint chunk의 detail truncation 지적 | q18은 query/parameter 표현·embedding truncation 후보. q19/q20은 최종 relevance 부족 |

03의 방향성 자체, 즉 “C2는 어휘 갭이 크고 root/child/sibling 경쟁도 있다”는 결론은
유효하다. 수정할 부분은 원인 명칭의 확정도다. P0 전에는 다음처럼 표현해야 한다.

- q04/q06: variants가 실제 top-3를 회복한 **확정된 query-representation 문제**
- q05/q07/q08/q09/q12: 더 넓은 재실행에서는 보였지만 운영 base-wide 포함 여부가
  미확정인 **candidate/fusion 경계 문제**
- q10/q11: 더 넓은 재실행의 최종 50에도 없지만 individual arm 내 위치가 없는
  **강한 candidate-generation 의심**

## 3. 현재 로직에서 손실이 발생하는 지점

### 3.1 exact prelude의 의도된 좁은 범위

`EndpointCandidateSearch.search()`는 먼저 method+path 또는 operationId 완전 일치를 조회한다.
이 때문에 C1은 보호되지만 자연어 paraphrase, 자원명 bare word, parameter detail에는 작동하지
않는다. exact 범위를 fuzzy lookup으로 넓히면 C1의 결정성을 훼손하므로 보완 지점이 아니다.

### 3.2 keyword arm: flat OR FTS와 표현 불일치

`KeywordSearch.search()`는 원본 term을 OR로 검색하고 무가중 `text_tsv`의 `ts_rank`로
정렬한다. endpoint chunk는 method/path/summary/operationId/params/body/tags/description을 한
텍스트에 합친다. 그 결과는 다음과 같다.

- 한글 원문과 영문 OpenAPI 사이에는 lexical 교집합이 없어 C2 keyword arm이 사실상 죽는다.
- bare `customer`처럼 흔한 토큰은 긴 child 설명 수십 건을 동시에 맞혀 root가 밀린다.
- path leaf, ancestor, operation, description이 query 관점의 역할 구분 없이 경쟁한다.

과거 keyword-variant 독립 rank 병합과 coverage/budget 조정은 이 flat 경쟁에 후보를 더 넣고
root/child 한쪽을 회귀시켜 72/74에서 반려됐다. 따라서 같은 방식의 keyword 확장은 후보에서
제외한다.

### 3.3 vector arm: 하나의 긴 endpoint 표현과 family 포화

vector arm도 같은 endpoint chunk를 하나의 embedding으로 사용한다. 짧은 root와 유사한 child,
sibling이 summary/description/parameter 문맥을 공유하므로 dense similarity가 route family
내 정답 역할을 구분하지 못한다. C7 q18처럼 질의가 endpoint의 후반 세부 필드를 가리키면
embedding 입력 절단 영향도 받을 수 있다. `multilingual-e5-small`의 KO→EN 신호가 1,809개
endpoint 경쟁에서 약하다는 29의 진단도 C2 전멸과 정합한다.

### 3.4 wide RRF: 넓은 조회와 최종 recall은 별개

운영 `top_k=10`에서 각 arm 폭은 `max(10×4, 50)=50`이고, RRF도 50개를 만든 뒤 즉시
top-10으로 자른다. `RRF_K=60`, 양 arm weight 1인 rank-only 식은 점수 크기를 버리고 arm
교집합을 강하게 보상한다.

```text
single-arm rank 1 = 1 / (60 + 1)       = 0.01639
both-arm rank 50  = 2 / (60 + 50)      = 0.01818
```

즉 양 arm의 최하위 50위인 endpoint도 다른 arm에 없는 1위 endpoint보다 앞선다. 두 arm이
같은 root/child/sibling decoy를 공유하면, 한 arm에서만 강한 정답은 final top-10 진입 전에
구조적으로 밀릴 수 있다. 현재 RRF가 fallback보다 MRR은 높이지만 Recall@10을 전혀 늘리지
못한 관측과 맞는다. 단, 실제 11개 miss 중 몇 건이 이 경로인지는 P0 arm trace로 확정해야 한다.

RRF `k`나 arm weight를 20질의에 맞춰 조정하는 것은 후보로 두지 않는다. 없는 후보를 만들지
못하고, 과거 C5 및 route-pair 회귀처럼 다른 질의의 순서를 광범위하게 바꿀 위험이 있다.

### 3.5 fallback: 품질 후보가 아니라 롤백 대조군

fallback은 keyword 결과가 한 건이라도 있으면 vector를 전혀 호출하지 않는다. OR FTS가 흔한
토큰으로 decoy 한 건만 반환해도 semantic recovery가 차단된다. 따라서 MRR 0.270은 설계상
예상 가능한 하한이며, fallback을 주 검색 품질 레버로 튜닝하지 않는다. rollback 동작과
exactness 대조군으로 유지한다.

## 4. P0 — 동일 실행 arm/production-wide trace 보정

### 4.1 판정

**P0은 P1~P3 우선순위 판단의 필수 선행조건이다.** 제품 검색 순위는 바꾸지 않고 평가·trace
계약만 고친다. 별도 `search(top_k=50)` 결과를 production-wide라고 부르지 않는다.

운영과 같은 단일 `search(top_k=10)` 실행 안에서 다음 좌표를 함께 기록한다.

1. exact prefix와 `exact_prefix_count`
2. keyword arm top-50의 ref/rank/score
3. vector arm top-50의 ref/rank/score와 사용한 원본/variant subquery
4. 그 두 목록으로 만든 base-wide RRF top-50의 ref/rank/arm 기여
5. 실제 final top-10과 accepted final-output rank
6. accepted가 각 단계에 없을 때 `not-in-arm-width`, `fusion-cut`, `final-cut`을 구분한 사유

v3에서 발견된 exact-prefix와 RRF-local rank 좌표 불일치를 되풀이하지 않도록, 최종 채점은
항상 exact를 포함한 full-output 1-based rank를 사용하고 arm/base-wide 좌표는 별도 필드로 둔다.
trace를 얻기 위해 검색을 다시 호출하거나 더 큰 top_k로 재실행하지 않고, 실제 호출 내부의
중간 리스트를 read-only sink로 직렬화해야 한다.

### 4.2 예상 효과

- 제품 지표 직접 효과: **0**. baseline 순위와 byte 수준 동등이 성공 조건이다.
- 판단 효과: 11개 miss를 최소 `generation miss / fusion miss / final shallow rank`로 분리해
  P1·P2·P3의 대상 건수와 이론상 상한을 계산할 수 있다.
- 03의 5개 FAMILY-RERANK와 2개 CANDIDATE-GEN 라벨을 운영 좌표에서 재판정한다.

### 4.3 리스크

- trace용 별도 쿼리나 정렬이 제품 실행과 다른 후보폭을 만들면 같은 오류가 재발한다.
- ref/score 전체 기록은 eval report 크기를 늘리고, 운영 로그에 노출하면 데이터·비용 문제가
  생긴다. eval 전용 sink로 제한해야 한다.
- instrumentation이 검색 함수의 결과 리스트를 mutate하면 baseline이 오염된다.

### 4.4 측정 방법과 PASS

- 동일 shared index에서 trace OFF/ON 각각 2회 결정성 확인
- trace 비활성/활성의 final endpoint id·순서·match_type 완전 동일
- `final_answer_rank == min(valid accepted final-output ranks)` fail-closed 검증
- keyword/vector/base-wide/final 각 rank 범위·필드 존재·타입 mutation test
- 20질의 전부에 배타적인 실패 사유 한 개를 부여하고, q04~q12를 03 라벨과 대조

P0 문서·하네스가 승인되기 전에는 P1~P3 구현 후보를 선택하지 않는다.

## 5. P1 — bounded vector-only query reformulation/decomposition

### 5.1 후보 형태와 기존 반려안과의 차이

원본 query를 유지하면서 vector arm에만 최대 소수의 reformulated subquery를 추가하고,
ref별 best rank로 병합한다. 대상은 다음 두 형태다.

- 비영문 질의의 bounded 영어 API 표현 정규화
- `A하고 B`, parameter/detail 표현처럼 다개념인 질의의 bounded clause decomposition

keyword arm의 후보·점수·순서는 baseline과 완전히 같아야 한다. 72/74에서 반려된
keyword-variant 독립 검색, coverage admission, merged cap을 재사용하지 않는다. 서버가 별도
LLM을 호출할지, 고정된 로컬 reformulator를 쓸지는 후보 설계에서 하나로 고정해야 하며,
결과를 본 뒤 provider나 alias를 바꾸지 않는다. 기존 caller `query_variants`와 자동 subquery의
출처도 trace에서 구분한다.

### 5.2 예상 효과

03에서 영어 variants가 q04를 miss→1위, q06을 miss→3위로 회복했다. 동일한 의미의 bounded
vector reformulation이 이 두 건을 재현하고 다른 질의가 불변이라는 낙관적 조건이면 09 대비:

- Recall@1 `25% → 30%`(+5%p)
- Recall@3/10 `35%/45% → 45%/55%`(+10%p)
- MRR 약 `+0.067`, nDCG@10 약 `+0.075`

이는 과거 실행이 keyword와 vector 경로를 함께 사용한 결과이므로 **효과 약속이 아니라 상한
가늠값**이다. q05/q07은 더 넓은 검색에 들어왔지만 top-10 회복은 못 했으므로 P1만으로
해결된다고 보지 않는다. q17 decomposition과 q18 detail reformulation은 P0에서 각 subquery의
vector arm hit가 확인될 때만 추가 기대효과로 계산한다.

### 5.3 리스크

- 잘못된 번역·분해가 vector family를 바꾸거나 root 대신 child를 밀어 올릴 수 있다.
- best-rank merge는 subquery 수가 늘수록 decoy 유입이 커진다. 최대 subquery 수와 빈/중복 제거를
  사전 고정해야 한다.
- 로컬 MT/모델을 넣으면 latency·메모리·모델 배포 비용이 늘고, 외부 LLM이면 현행 $0 search
  cost와 client-LLM 위임 원칙이 바뀐다.
- legacy q04/q06에 맞춘 alias 사전은 과적합이다. exposed set의 표현을 제품 상수로 옮기지 않는다.

### 5.4 측정 방법과 PASS

- P0 trace로 baseline/reformulation의 **keyword arm 완전 동일**과 vector arm 변화만 확인
- original-only / supplied-variant / automatic-reformulation을 분리한 ablation
- C1 gross hit loss 0, route-pair root·child 전쌍 non-regression, C6 aggregate
  coverage/complete non-regression
- category별 Recall@1/@3/@10, MRR, nDCG와 Korean/English cohort를 별도 집계
- reformulator latency를 포함한 end-to-end p50/p95, memory, 외부 호출 비용 기록
- legacy·v1·v2는 방향 진단만 사용하고, 승급은 신규 candidate identity와 신규 sealed split에서 판정

## 6. P2 — bounded arm-exclusive rescue/quota

### 6.1 후보 형태와 기존 반려안과의 차이

P0에서 accepted가 keyword 또는 vector top-50에는 있으나 equal-weight RRF 교집합에 밀리는
질의가 유의미하게 확인될 때만 연다. 기존 양 arm 순위와 RRF 순위를 바꾸지 않고, final top-k
구성에서 arm-exclusive 상위 후보를 위한 작은 사전 고정 quota를 두는 별도 candidate다.

구체 quota 수, 삽입 위치, 중복 처리, exact 보호는 결과를 보기 전에 freeze한다. route-family,
path 길이, A/B/C/D structured score를 쓰지 않는다. 따라서 v3의 structured scorer +
protected-slot swap이나 과거 route-family reranker와 신호·목적이 다르다.

### 6.2 예상 효과

- 주 효과는 Recall@10과 answer_miss 개선이며, Recall@1 개선은 직접 목표가 아니다.
- P0에서 `F`건의 accepted가 arm top-50에 있지만 final top-10에 없다면 이 후보의 legacy 20건
  이론상 최대 Recall@10 개선은 `F/20`이다. 실제 기대치는 quota와 decoy 경쟁 때문에 더 낮다.
- q05/q08/q09/q12는 현재 후보군이지만 P0이 production arm hit를 확인하기 전에는 효과 대상에
  포함하지 않는다.
- 두 arm 모두에서 accepted가 없는 q10/q11 유형에는 효과가 0이다.

### 6.3 리스크

- single-arm false positive를 위해 기존 both-arm 정답을 밀 수 있어 C1/C5와 route pair가 회귀한다.
- quota는 RRF의 교집합 우대를 반대로 과보정할 수 있다.
- top_k가 작을수록 한 슬롯의 영향이 커지고, exact prefix 뒤 remaining top_k와의 상호작용이 생긴다.
- legacy 20건의 F에 맞춰 quota 숫자를 고르면 RRF `k` 튜닝과 같은 과적합이 된다.

### 6.4 측정 방법과 PASS

- P0에서 arm-hit/final-miss 질의가 먼저 존재해야 함. 없으면 후보를 폐기
- exact prefix를 포함한 실제 final-output 좌표에서 baseline/candidate paired 비교
- rescued accepted 수와 displaced accepted 수를 따로 세고 net Recall만으로 개별 loss를 숨기지 않음
- C1 gross loss 0, 모든 route pair root·child non-regression, category MRR 하락 한도 사전 고정
- arm-exclusive rescue가 실제 final 10 경계를 넘었는지 boundary-crossing trace 필수
- 신규 sealed split에서 Recall@10 순증과 MRR/nDCG non-regression을 함께 요구

## 7. P3 — query-endpoint cross-encoder rerank

### 7.1 후보 형태와 기존 반려안과의 차이

P1/P2로 production-wide candidate recall을 확보한 뒤, query와 기존 endpoint 표현을 함께
입력하는 relevance model로 제한된 후보만 재정렬한다. `search_tsv`, A/B/C/D 전역 field weight,
route-family path boost, v3 protected-slot postprocess를 사용하지 않는다. 후보 injection이 아니라
**동일 후보 집합 안의 query-conditioned relevance 비교**다.

입력은 method/path/summary와 예산 내 기존 endpoint text처럼 재현 가능한 필드로 고정한다.
모델·버전·token budget·candidate count·동점 규칙을 candidate identity에 포함한다.

### 7.2 예상 효과

- 현재 top-10에 있으나 2~10위인 q13/q15/q19/q20이 직접 대상이다.
- 네 건을 전부 1위로 올리는 현재-pool 이론상 상한은 Recall@1 `25% → 45%`, MRR 약
  `0.318 → 0.450`, nDCG@10 약 `0.350 → 0.450`이다.
- candidate set이 그대로면 Recall@10은 변하지 않는다. 따라서 P3 단독으로 목표 Recall@1 70%에
  도달할 수 없다.
- family sibling의 summary/parameter 의미를 query와 직접 비교하므로 q13/q19 같은 decoy 구분에는
  P1/P2보다 높은 기대효과가 있다.

### 7.3 리스크

- CPU latency·메모리 증가, 모델 패키징/다운로드, 버전 재현성 비용이 가장 크다.
- 긴 endpoint text를 다시 자르면 C7 blind spot을 재현할 수 있다.
- cross-language relevance가 약한 모델이면 C2 개선 없이 영어 decoy만 재배열할 수 있다.
- learned ranker는 exact/direct와 route pair를 회귀시킬 수 있으므로 exact prefix는 모델 입력에서
  제외하고 절대 보존해야 한다.

### 7.4 측정 방법과 PASS

- P0 production-wide를 고정해 candidate-set parity를 먼저 증명
- rerank 전 candidate recall을 보고해 “생성 개선”으로 잘못 귀속하지 않음
- oracle MRR/nDCG와 실제 모델 개선의 비율을 함께 기록
- Recall@1, MRR, nDCG를 주 지표로 하고 Recall@10·C1·route-pair non-regression을 HARD로 둠
- cold/warm p50/p95, peak RSS, 모델 load time과 package size 기록
- 모델/입력 포맷을 freeze한 신규 sealed split에서만 승급 판정

## 8. 후보 선택 의사결정

P0 완료 뒤 질의별 병목 수로 다음처럼 한 갈래만 먼저 연다.

```text
accepted가 keyword/vector arm top-50 모두에 없음
  -> P1 query reformulation/decomposition

accepted가 한 arm top-50에는 있으나 base-wide/final top-10에서 탈락
  -> P2 arm-exclusive bounded rescue

accepted가 final top-10에는 있으나 rank 2~10
  -> P3 cross-encoder rerank
```

혼재하면 `generation miss 수 → fusion miss 수 → shallow-rank 수` 순으로 전체 metric ceiling을
계산한다. 09에서 11건 miss가 4건 shallow-rank보다 크므로 기본 순서는 P1/P2 후 P3다.

## 9. 공통 평가 계약

1. **동일 shared index·paired 실행:** baseline/candidate, reformulation OFF/ON을 같은 물리 인덱스와
   동일 product SHA에서 비교한다.
2. **실패 지표 분리:** `answer_miss@10`과 `empty_result`를 다시 합쳐 “no-result”로 부르지 않는다.
3. **회귀 보호:** C1 gross loss 0, route pair는 root/child 각각 non-regression, C6는 aggregate
   coverage/complete를 함께 본다.
4. **지표:** Recall@1/@3/@10, MRR, nDCG@10, category/language, boundary crossing, per-arm trace.
5. **component 단독 평가:** P1+P2+P3 결합 후보로 시작하지 않는다.
6. **노출셋 용도 제한:** legacy 20, v1, v2, 이미 개봉된 gate는 개발·원인 진단 전용이다.
7. **v3 재사용 금지:** verdict 91대로 v3 holdout은 영구 봉인하며 query/accepted/split/threshold를
   새 후보에 재사용하지 않는다.
8. **새 candidate, 새 freeze:** 제품 후보마다 identity에 맞는 신규 sealed split과 사전 threshold를
   만든다. 결과 후 quota·subquery 수·모델·gate를 조정해 같은 split을 재시험하지 않는다.

## 10. 명시적 비후보

이번 검토는 다음 접근을 재개하지 않는다.

- `lexical_field=structured`로 primary keyword arm 전면 교체
- A/B/C/D weight, operation alias, path leaf/context를 전역 `ts_rank`로 다시 튜닝
- text-primary + structured score + protected-slot adjacent swap(v3)
- keyword variant를 독립 ranker처럼 병합하거나 coverage/budget으로 admission
- 무조건적 짧은 path boost, 고정 route-family root/child permutation
- legacy 20질의만 보고 `RRF_K` 또는 arm weight 조정
- fallback을 운영 품질 후보로 승급

P1은 **vector-only query representation**, P2는 **기존 두 arm 사이의 bounded coverage**,
P3는 **query-conditioned learned relevance**를 각각 분리해서 다룬다. 이 경계가 무너지면 과거
반려 후보의 변형이므로 새 구현 설계로 승인하지 않는다.

## 11. 최종 판정

현재 가장 구체적인 로직 결함은 두 가지다. 첫째, 03의 넓은 후보 진단이 실제 production-wide와
다른 후보폭을 사용해 generation과 fusion 원인을 확정하지 못한다. 둘째, equal-weight
`RRF_K=60`은 arm 교집합의 최하위 후보도 single-arm 1위보다 앞세울 수 있어 family decoy
포화를 final top-10에 전달한다.

따라서 **P0 trace 보정이 무조건 첫 작업**이다. 그 결과 generation miss가 우세하면 P1,
arm-hit/final-miss가 우세하면 P2를 단독 후보로 설계한다. P3는 top-10 recall이 확보된 뒤
Recall@1/MRR/nDCG를 높이는 후속 단계다. 이 문서는 분석과 우선순위 판정만 승인하며,
구현·fixture freeze·새 모델 도입은 각각 별도 설계와 lead 승인을 받아야 한다.
