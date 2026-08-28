# 73. p02 회귀 — variant keyword 품질·기여 예산 수정 설계

- 대상: B-only `75fa5f3`의 p02 child 회귀
- 근거: 72번 §6.2~§6.3, developer arm trace, reviewer 기전 검토
- 범위: endpoint RRF의 keyword variants 경로만
- 상태: 원인 진단 승인, 수정 candidate 구현 승인

## 1. 판정 요약

1. **원인 진단을 승인한다.** 다른 물리 DB의 exact rank를 verdict 재현값으로 쓰지는
   않지만, child 2~3위→21위·root 미검출→2~4위라는 방향과 실 코드 arm trace는
   72번 holdout의 child 3위→미검출·root 미검출→2위와 같은 기전이다.
2. **수정은 score-aware variant admission과 bounded contribution을 함께 적용한다.**
   variant keyword 결과를 distinct informative-term coverage로 우선 정렬하고, 한 질의의
   variant 전체가 keyword arm에 새로 넣을 수 있는 후보 수를 최종 `top_k`로 제한한다.
3. 원문 keyword 0건이면 baseline filter-only로 되돌리는 안은 반려한다. cross-language
   C2에서 variants 기능을 사실상 끈다.
4. raw `ts_rank`를 query 간 직접 가중합하는 안도 반려한다. query 길이·term 구성에 따라
   scale이 달라 원문/variant 사이의 신뢰도로 해석할 수 없다.
5. cap/감쇠만 두는 안은 단독으로 채택하지 않는다. flood 크기는 줄여도 broad parent가
   child보다 먼저 들어오는 의미 오류를 고치지 못한다.
6. p02는 이미 개봉된 개발 회귀 사례다. 수정 candidate가 p02와 v1 전체 회귀검사를
   통과한 뒤에만 v2 sealed split을 저작·프리즈한다.

## 2. 원인 진단 승인 범위

### 2.1 승인하는 사실

p02의 한국어 원문은 영문 OpenAPI keyword arm에서 hit가 없고, 영어 variant
`list just the topics on the repository`는 broad `repository` term 때문에 많은 GitHub
route를 OR 매치한다.

baseline은 variant를 후보 필터에만 쓰고 원문 term으로 점수를 계산해 이 lexical arm이
실질적으로 순위를 만들지 못했다. 그 결과 vector rank 1인 `/topics` child가 RRF 상위에
남았다.

B-only는 원문/variant를 별도 검색한 뒤 min-rank로 합치므로 원문 hit가 0일 때 영어
variant의 조밀한 1..50 순위가 keyword arm 전체가 된다. broad parent와 sibling route가
각각 새 keyword RRF 항을 얻고, variant top-50 밖인 `/topics`는 vector 항만 남아
top-10 밖으로 밀렸다.

근본 문제는 “영어 variant를 썼다”가 아니다. **OR lexical 결과의 품질 차이를 보지 않고
한 variant가 만든 width개 후보 모두에 full keyword-rank 자격을 준 것**이다.

### 2.2 유보하는 부분

진단 DB `rrfeval_ed5b97f0`은 verdict DB `rrfeval_4c1f336b`와 다르므로 2위·21위 같은
exact rank는 판정 근거로 고정하지 않는다. 원인 기전과 방향만 승인한다. 수정 전후의
정확한 순위 판정은 다시 같은 shared index에서 수행한다.

## 3. 검토안 판정

### 3.1 raw score/weight 도입

**그대로는 반려, query-normalized coverage로 대체한다.**

PostgreSQL `ts_rank`는 한 query 안의 문서 정렬에는 쓸 수 있지만, 한국어 원문과 영어
variant처럼 term 수·빈도·길이가 다른 query 사이의 절대 점수는 신뢰도 weight가 아니다.
`0.7 * original_ts_rank + 0.3 * variant_ts_rank` 같은 식은 근거 없는 tuning surface를
만든다.

대신 한 variant 안에서 “질의의 서로 다른 정보 term을 몇 개 설명하는가”를 0~1 coverage로
정규화한다. p02에서는 parent의 `repository` 1개 매치보다 child의 `topics + repository`
매치가 앞서야 한다.

### 3.2 원문 arm 0건 fallback

**반려한다.**

원문 hit 0은 variant가 약하다는 증거가 아니라 cross-language variants가 필요한 바로 그
조건이다. 이때 baseline filter-only 동작으로 되돌리면 gate96의 C2 개선을 구조적으로
포기한다. 원문 부재를 fallback trigger로 사용하지 않는다.

### 3.3 목록 cap·감쇠

**cap은 보조 안전장치로 채택, 단독 수정은 반려한다.**

variant 후보를 width=50 전부 keyword arm에 넣지 않고 한 user query 전체에서 최대
`top_k`개만 새로 admit한다. 이로써 variant 하나가 수십 sibling에 RRF keyword 항을
부여하는 현상을 제한한다.

그러나 broad parent가 cap 안에서 child보다 먼저면 p02 pair는 여전히 악화될 수 있다.
따라서 cap 전에 coverage-based 품질 정렬이 필수다.

## 4. 채택 설계

### 4.1 원칙

`query_variants`는 원문의 대체 정답 목록이 아니라 **후보 확장 표현**이다.

- 원문 keyword 순위는 기존 계약 그대로 보존한다.
- variant keyword 후보는 query-normalized lexical coverage로 품질을 매긴다.
- 품질 상위의 제한된 후보만 원문 keyword list와 min-rank 병합한다.
- vector variants 경로는 변경하지 않는다.
- variants가 없으면 SQL 호출·순위·응답이 baseline과 완전히 같아야 한다.

### 4.2 informative term

variant마다 기존 endpoint tokenizer로 distinct term을 만든 뒤 닫힌 function-word
stoplist만 제거한다.

초기 영어 stoplist:

```text
a, an, the, this, that, these, those,
my, your, our, their,
of, on, in, at, to, for, from, with, inside,
just, please
```

- `list`, `get`, `create`, `delete` 같은 operation term은 남긴다.
- `repository`, `customer`, `topic` 같은 domain/resource term도 남긴다.
- stemming은 현재 tokenizer 수준의 단순 소문자화만 사용한다. 새 NLP/LLM 의존성을
  넣지 않는다.
- informative term이 0개면 그 variant는 keyword 후보를 추가하지 않는다. vector
  variants에는 계속 전달한다.

stoplist를 corpus 통계나 p02 결과에 따라 자동 확장하지 않는다. 언어 기능어만 두며
`repository`를 p02 때문에 stopword로 넣는 식의 domain 하드코딩은 금지한다.

### 4.3 coverage

variant `v`의 distinct informative term 집합을 `T_v`, endpoint chunk `d`가 실제로
매치한 term 집합을 `M(v,d)`라 한다.

```text
matched_count(v,d) = |M(v,d)|
coverage(v,d) = matched_count(v,d) / |T_v|
```

variant 검색은 기존 OR recall을 유지하되 아래 순서로 정렬한다.

```text
coverage DESC,
matched_count DESC,
기존 ts_rank DESC,
Chunk.id ASC
```

`coverage`와 `matched_count`는 query 안에서만 비교하므로 raw `ts_rank`의 query 간
scale 문제를 만들지 않는다. `ts_rank`는 coverage 동점의 기존 lexical 밀도 tie-break로만
남는다.

repository layer는 기존 기본 검색을 바꾸지 않도록 opt-in variant-quality mode 또는
별도 endpoint 전용 메서드로 이 값을 반환한다. 문서 검색과 원문 endpoint keyword
검색의 정렬 계약은 변경하지 않는다.

### 4.4 여러 variant의 단일 후보 pool

각 nonblank·중복 제거 variant를 §4.3으로 width만큼 조회한 뒤 ref_id별 최선 품질을
다음 key로 하나만 남긴다.

```text
coverage DESC,
matched_count DESC,
variant 내부 rank ASC,
variant 입력 순서 ASC,
ref_id ASC
```

그 전역 pool의 앞 `top_k`개만 `admitted_variant_ref_ids`로 둔다. cap은 variant별
`top_k`가 아니라 **모든 variants 합산 `top_k`**다. variants 개수를 늘려 keyword arm
기여량을 선형으로 키울 수 없게 하기 위해서다.

### 4.5 원문 list와 병합

원문 query는 현재 `KeywordSearch.search(query, top_k=width, query_variants=None)`로
그대로 검색한다. variant pool은 §4.4의 admitted 후보만 사용한다.

최종 keyword ref list는 기존 B-only의 min-rank 의미를 유지하되 variant 쪽 rank는
admitted pool 안의 품질 순위만 쓴다.

```text
best_rank(ref) = min(
    original keyword rank,          # 있으면
    admitted variant quality rank   # 있으면, 최대 top_k
)
```

동점은 ref_id로 결정한다. 원문이 없으면 품질 상위 variant 후보가 lexical arm을 구성하므로
cross-language 개선을 유지할 수 있다. 반대로 한 variant가 최대 width개 sibling을 full
rank로 주입하던 경로는 사라진다.

이 설계는 p02 target을 production 코드에서 알지 못한다. `/topics`가 parent보다 많은
informative term을 실제로 설명할 때만 품질 순위가 올라간다.

### 4.6 변경하지 않는 것

- generic `reciprocal_rank_fuse` 공식·RRF_K·arm weight
- `_search_vector_with_variants`
- fallback 전략
- MCP request schema와 optional variants 계약
- chunk text/index schema
- route-family reranker A

ADR-0003 read-only 경계를 유지한다.

## 5. 구현 경계

예상 변경점:

1. `ChunkRepository`: opt-in coverage projection/order 지원
2. `KeywordSearch` 또는 endpoint 전용 adapter: variant-quality hit DTO
3. `EndpointCandidateSearch._search_keyword_with_variants`: original list + bounded
   quality variant pool 병합

공용 `KeywordSearch.search`의 기본 반환·정렬은 그대로 둔다. 검색면 B(document search)가
같은 변경을 암묵적으로 받지 않게 한다.

상수는 코드에 고정한다.

- function-word stoplist
- total variant admission budget = request `top_k`

env/config/실험용 weight를 새로 노출하지 않는다.

## 6. 테스트 계약

### 6.1 단위 테스트

1. coverage: `topics + repository`를 매치한 child가 `repository`만 매치한 parent보다 앞섬
2. coverage 동점은 matched_count → ts_rank → id 순으로 결정
3. 여러 variants 합산 admitted 수가 `top_k`를 넘지 않음
4. duplicate/blank variants가 budget·SQL 호출 수를 늘리지 않음
5. variants 없음은 baseline keyword 호출 1회·순위 완전 동일
6. original hit는 variant 병합 뒤에도 min-rank 계약에 따라 보존
7. informative term 0개 variant는 keyword 기여 없음
8. fallback·vector variants는 무변경

### 6.2 p02 개발 회귀 테스트

p02는 v1 holdout이 개봉돼 더 이상 sealed 평가가 아니다. 수정 candidate와 함께 다음을
같은 physical index에서 고정한다.

- g003 root와 g004 child를 baseline/수정 candidate ON으로 실행
- child의 capped rank가 baseline보다 나빠지지 않음
- root 개선이 child 악화를 동반하지 않음
- arm trace에서 `/topics`가 admitted variant pool에 들어가고 broad parent보다 coverage가
  낮지 않음을 기록

mock만으로 끝내지 않고 프리즈 corpus를 쓰는 기존 shared-index runner/진단 경로로 한 번
검증한다. 새 runner 스켈레톤은 만들지 않는다.

### 6.3 v1 exposed regression

수정 candidate는 v1 gate/holdout/all을 개발 회귀셋으로 다시 실행할 수 있다. 승급 판정이
아니며 다음만 본다.

- OFF per-query baseline 완전 동일
- p02 포함 12 pair non-regression
- 기존 B-only gate96의 ON 실익이 붕괴하지 않음
- g072/g073/g108 등 알려진 losses 기록

이 결과를 보고 v1 label·variant·pair를 고치지 않는다.

## 7. v2 프리즈 순서

질문 3의 답은 **맞다**. p02 재현 테스트는 수정 candidate에 포함하고 v2 프리즈 전에
통과해야 한다.

순서:

1. §4 구현 + §6.1 단위 테스트
2. 같은 shared index에서 §6.2 p02 재현 통과
3. v1 exposed regression으로 명백한 새 회귀·효과 붕괴 확인
4. 여기까지 통과한 candidate SHA를 고정
5. 69번 분포 원칙에 따라 새 v2 sealed holdout을 저작·검증·프리즈
6. v2를 보기 전에 candidate-specific 승급 임계값 확정
7. 새 shared index paired 실행 후 최종 승급 판정

v2 holdout은 v1의 24건을 재분할하거나 문구만 바꾼 복제본이면 안 된다. p02와 v1 losses는
개발 회귀셋에 남기고, 새 endpoint/route-family 쌍으로 sealed 표본을 만든다.

## 8. 최종 판정표

| 회부 질의 | 판정 |
|---|---|
| 1. 원인 진단 승인 | **승인. exact rank가 아닌 기전·방향 승인** |
| 2. 수정 방향 | **coverage 기반 score-aware admission + variants 합산 top_k cap** |
| 원문 0건 fallback | **반려** |
| raw ts_rank query 간 가중 | **반려** |
| cap 단독 | **반려** |
| 3. p02 테스트 시점 | **수정 candidate와 함께, v2 프리즈 전** |
| 수정설계 회부 | **승인** |

목표는 variants의 cross-language 회복력을 버리지 않으면서, broad OR match가 sibling
route 전체에 full keyword arm 자격을 주는 구조를 제거하는 것이다.
