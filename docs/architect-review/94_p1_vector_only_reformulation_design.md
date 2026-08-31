# 94. P1 vector-only reformulation/decomposition 설계

- 선행 근거: `docs/architect-review/92_corpus_eval_search_logic_improvement_review.md` §5,
  `docs/architect-review/93_p2_arm_rescue_effectiveness_verdict.md`,
  `docs/eval-results/10_2026-08-31_p0_arm_trace_reclassification.md`
- 제품 기준: HEAD `3f698ba` — exact prelude + keyword/vector wide RRF,
  P2 arm rescue는 기본 quota `0`
- 상태: **설계 승인. 구현·fixture 저작·평가 실행은 별도 승인 대상**
- 목적: P0에서 확인된 generation miss q04/q07/q10처럼 원문 벡터 arm top-50에도
  정답이 없는 질의에, client LLM이 제공한 소수의 대체 검색 표현을 vector arm에만 추가한다.

## 1. 결정 요약

P1은 새 MCP 입력 `vector_reformulations: list[str] | None`을 추가하는 bounded query
representation candidate다. client LLM이 최대 두 개의 영문 reformulation 또는 atomic
decomposition을 제공하고, 서버는 NFKC/공백 정규화·중복 제거·상한 검증만 한다. 서버는
번역, synonym/alias 생성, LLM/MT API 호출, endpoint에서의 pseudo-relevance expansion을 하지
않는다.

P1의 핵심 경계는 다음과 같다.

1. **새 입력만 vector arm에 사용한다.** 기존 `query_variants`의 keyword+vector 계약은
   바꾸지 않는다.
2. **keyword/exact/fallback은 P1 전후 byte-identical이다.** P1이 켜져도 fallback은
   reformulation을 읽거나 vector 재질의하지 않는다.
3. **기존 RRF만 사용한다.** arm weight, `RRF_K=60`, candidate width, RRF 식을 바꾸지 않고
   P2 rescue quota도 평가·운영에서 `0`으로 고정한다.
4. **최종 tail-slot 치환이 없다.** P2처럼 base `both` 후보를 임의로 축출하는
   postprocessor를 만들지 않는다. enhanced vector arm을 넣은 정상 RRF 결과만 반환한다.

이 설계는 P2의 실패를 보정하려는 변형이 아니다. P2가 처리하지 못한 arm 밖의
generation miss를 먼저 vector arm에 넣는 별도 candidate다. P1과 P2/P3를 한 실행에
결합하지 않는다.

## 2. 배제한 대안

| 대안 | 판정 | 사유 |
|---|---|---|
| 서버 alias/번역 사전 | 반려 | q04/q07/q10을 본 뒤 특정 한국어·영어 표현을 제품 상수로 옮길 위험이 크며, client LLM 위임 원칙을 깬다. |
| 서버 LLM/외부 MT API 호출 | 반려 | 검색 경로의 $0 비용·지연·결정성·비밀정보 경계를 바꾸며, 기존 결정인 “판단은 client LLM”과 맞지 않는다. |
| 임베딩 nearest-neighbor로 자동 확장 | 반려 | 현재 generation miss의 decoy를 feedback해 반복할 수 있고 새 의미를 만들지 못한다. |
| 기존 `query_variants`를 vector-only로 변경 | 반려 | 현재 MCP 계약은 해당 입력을 keyword와 vector 양쪽에 전달한다. 이 계약을 바꾸면 legacy 호출의 keyword 후보군을 바꾼다. |
| 새 reformulation을 keyword arm에도 전달 | 반려 | 72/74의 keyword-variant flood와 동일한 위험이다. 원문 keyword arm 비간섭을 증명할 수 없다. |
| P2 quota와 결합 | 반려 | arm admission 효과와 tail replacement 효과를 분리 측정할 수 없고, P2는 93에서 비승급됐다. |

## 3. API 및 생성 책임

### 3.1 새 MCP 입력

`search_endpoints`에 다음 선택 인자를 추가한다.

```text
vector_reformulations: list[str] | None
```

의미는 “원본 query와 같은 API retrieval 의도를 영문으로 표현한 독립 vector subquery”다.
일반 paraphrase 하나 또는 복수 의도의 atomic clause 둘을 담을 수 있다. 이는 결과 필터나
정답 endpoint를 지정하는 입력이 아니다.

기존 `query_variants`는 그대로 유지한다.

| 입력 | keyword arm | vector arm | 호환성 |
|---|---|---|---|
| `query_variants` | 기존과 동일하게 후보 filter 확장 | 기존과 동일하게 원본과 병합 | 기존 contract 보존 |
| `vector_reformulations` | **전달 금지** | P1 ON일 때만 원본과 병합 | 새 contract |

두 입력은 동시에 줄 수 없다. 둘 다 정규화 뒤 nonempty이면 `ValidationError`다. 그래야
`query_variants`를 사용한 legacy 호출은 P1 flag 유무와 무관하게 기존 vector 후보폭·순서를
그대로 유지하고, P1 요청은 raw `query`만 keyword arm에 전달한다. P1 평가는
`query_variants=None`으로 고정해 keyword 비간섭을 독립 검증한다.

### 3.2 client LLM 책임

client LLM은 query의 언어·API 관용어·복수 의도를 해석해 최대 두 표현을 제출한다.
서버는 입력이 정확한 번역인지, 의도가 보존됐는지, 어느 endpoint가 맞는지 판단하지 않는다.

- 비영문 query: 영문 API action+resource 표현을 우선 제출한다.
- 영문 의역: 문서 resource vocabulary에 가까운 일반 API 표현을 제출할 수 있다.
- 다개념 query: 각 clause를 하나씩 분리할 수 있다. 이 경우 vector merge는 OR 후보 회수이지
  all-of 답을 보장하지 않는다.
- 직접 method+path/operationId query: reformulation을 제출하지 않는다. exact prelude가 담당한다.

### 3.3 서버가 하는 일과 하지 않는 일

서버의 syntactic normalization만 허용한다.

1. 각 문자열에 NFKC를 적용하고 앞뒤 공백을 제거하며 내부 공백 run을 하나로 접는다.
2. 정규화 key(NFKC·공백 접기·casefold)가 원문 또는 앞선 reformulation과 같으면 버린다.
3. 빈 문자열을 버린 뒤 서로 다른 값이 둘을 초과하면 `ValidationError`로 실패한다.
4. 남은 순서는 client 입력 순서로 보존한다.

서버는 token 치환, 단어 번역, Korean morphology, operation alias, path token, embedding
nearest neighbor, query splitting을 수행하지 않는다. 따라서 server-side 규칙만으로 할 수 있는
것은 malformed/duplicate input 억제와 예산 제한뿐이다. 이 규칙만으로 KO→EN 번역이나
`billing history`→`invoices` 같은 의미 연결을 만들 수는 없다.

## 4. P1 데이터 흐름

### 4.1 RRF 전략

P1은 `EndpointCandidateSearch.search()`의 existing exact prelude 뒤, `search_strategy="rrf"`
분기 안에서만 동작한다.

```text
MCP search_endpoints(query, query_variants, vector_reformulations)
  │
  ├─ exact prelude(method+path | operationId) ─────────────── unchanged
  │
  ├─ keyword arm(query) ───────────────────────────────────── P1 request에서 unchanged
  │     text_tsv filter/rank, width=50
  │
  ├─ vector arm
  │    ├─ original query                              (always)
  │    └─ P1 vector_reformulations                    (P1 ON only; max 2)
  │         each: vector search top-50, same scope
  │         -> ref_id별 best rank 병합 -> vector list top-50 재절단
  │
  ├─ unchanged reciprocal_rank_fuse(keyword_ids, vector_ids, top_k=50)
  │
  └─ base_wide[:remaining_top_k]
       (P2 `DOCS_MCP_SEARCH_ARM_RESCUE_QUOTA=0`; no tail replacement)
```

`vector_reformulations`가 없거나 P1 flag가 OFF이면 vector arm 호출은 기존 호출 순서·입력·결과와
정확히 같아야 한다. P1 branch가 disabled input을 정규화하거나 빈 임베딩 호출을 해서는 안 된다.
`query_variants`만 든 legacy 요청은 현행 keyword+vector 경로를 그대로 사용한다.

### 4.2 fallback 전략

`search_strategy="fallback"`은 P1 범위 밖이다.

```text
keyword(query, query_variants) -> nonempty면 반환
                           └-> empty일 때 기존 original-query vector fallback
```

P1은 fallback에 `vector_reformulations`를 전달하지 않는다. 따라서 candidate OFF/ON, 입력
유무와 무관하게 fallback 결과는 byte-identical한 rollback control이다. RRF 제품 경로의
candidate recall 개선을 fallback 품질 개선으로 잘못 귀속하지 않는다.

### 4.3 vector 병합과 예산

각 vector subquery는 현재 production scope와 동일하게 `width=50`을 조회한다. 원본 하나와
P1 reformulation 둘까지이므로 P1이 추가하는 vector 검색은 최대 두 번이다. P1 요청은
`query_variants`와 상호배타이므로 legacy variant 수가 P1 vector budget을 우회하지 않는다.

병합은 ref별로 다음을 보존한다.

1. 양수 cosine score hit만 고려한다.
2. 가장 작은 arm-local rank를 `best_rank`로 둔다.
3. 같은 best rank면 더 큰 score를 trace용으로 보관한다. RRF score 계산에는 쓰지 않는다.
4. `(best_rank, ref_id)`로 결정적으로 정렬한 뒤 **최대 width=50**만 vector arm에 넘긴다.
5. trace에는 best hit를 낸 source(`original`, `legacy_variant`, `p1_reformulation[i]`)와
   source별 rank를 함께 남긴다.

마지막 재절단은 중요하다. 여러 subquery의 top-50 union을 그대로 RRF arm에 넘기면 vector
arm이 50보다 커져 query 수가 RRF candidate budget을 우회한다. P1은 새 의미 표현을 두 개까지
허용하되, RRF에 기여하는 vector arm의 폭은 기존 50으로 고정한다.

## 5. P0 generation miss와 P1의 역할

P0에서 q04/q07/q10은 keyword arm과 vector arm 모두 top-50 밖인 `generation_miss`였다.
P1은 이들을 바로 final top-10으로 “보장”하지 않고, 먼저 영문 vector arm top-50에 정답
endpoint가 진입할 기회를 만든다. final RRF 순위는 unchanged fusion과 안전 게이트가 판정한다.

| 질의 | P0 실패 원인 | client가 제공할 수 있는 reformulation 예 | P1이 바꾸는 것 | P1의 한계 |
|---|---|---|---|---|
| q04 `고객 새로 등록하고 싶어` | 한국어 원문은 영문 endpoint lexical 신호가 0이고, KO→EN vector가 customer child에 밀려 root POST가 top-50 밖 | `create a customer` | EN query와 EN endpoint embedding을 직접 비교해 root create 후보를 vector arm에 재입장시킨다 | 이 예시는 runtime client 입력이지 서버 alias가 아니다. client 미제공이면 기준선 그대로다. |
| q07 `저장소 삭제해줘` | KO→EN vector가 org/team delete decoy를 반환하고 repository root delete가 top-50 밖 | `delete a repository` | action+resource를 명시한 EN subquery로 root repository delete의 vector candidate admission을 시도한다 | 과거 broad variant가 top-50에만 들어온 적이 있어 final top-10 회복은 별도 측정 대상이다. |
| q10 `show my billing history` | 영문인데도 `billing history`와 invoice resource 사이 어휘/의도 갭 때문에 양 arm top-50 밖 | `list invoices` | client가 문서 resource vocabulary를 아는 경우 invoices 계열을 vector arm에 입장시킨다 | 서버는 billing→invoice를 추론하지 않는다. baseline `both` 슬롯을 강제로 치우지 않으므로 final top-10 진입은 보장되지 않는다. |

위 문자열은 input 형식의 예시일 뿐 P1 코드·fixture의 상수 목록이 아니다. 특히 q04/q07/q10에
맞춘 번역표나 alias map을 서버에 넣지 않는다. evaluation fixture의 reformulation 값은
candidate 실행 전 별도 manifest에 고정하고 SHA로 봉인한다.

## 6. 안전 계약과 측정 게이트

### 6.1 HARD: 비간섭·rollback

| 항목 | PASS 조건 |
|---|---|
| disabled parity | P1 flag OFF이면 `vector_reformulations`가 있어도 RRF final `(endpoint_id, match_type)`가 baseline과 완전 동일 |
| empty parity | flag ON이더라도 입력이 `None`/정규화 뒤 빈 목록이면 baseline과 완전 동일 |
| exact parity | exact prelude 결과와 final prefix가 candidate OFF/ON 완전 동일 |
| keyword parity | 같은 `query`·`query_variants`에서 keyword query/filter terms/score terms/top-50 ref/rank/score가 candidate OFF/ON 완전 동일 |
| original-vector parity | original query의 vector top-50 ref/rank/score가 P1 유무와 완전 동일 |
| fallback parity | `strategy=fallback`의 per-query final output이 candidate OFF/ON 완전 동일 |
| vector budget | P1 reformulation은 최대 2개, 병합 후 vector RRF list 길이는 최대 50 |
| P2 isolation | `DOCS_MCP_SEARCH_ARM_RESCUE_QUOTA=0`; P2 rescue 함수가 final output에 관여하지 않음 |
| determinism | 같은 shared index·같은 frozen inputs에서 candidate ON 2회 final/trace 완전 동일 |

### 6.2 both-arm slot 보호

P2에서 드러난 위험은 “정답 근거가 없는 단일-arm 후보를 위해 existing `both` 슬롯을 tail에서
기계적으로 축출하는 것”이었다. P1은 그런 postprocessor를 금지한다. enhanced vector arm의
rank 변화는 unchanged RRF가 처리하며, P1 코드가 base-wide final tail을 교체해서 `both` 결과를
내보내면 HARD FAIL이다.

추가로 gate에서는 query별 baseline final top-k의 `match_type="both"` ref 집합이 candidate
final top-k의 부분집합인지 검사한다.

```text
baseline_final_both_ref_ids ⊆ candidate_final_ref_ids
```

하나라도 빠지면 aggregate Recall/MRR 개선과 무관하게 FAIL이다. 이는 의도치 않은 vector
candidate가 strong two-arm evidence를 밀어내는 것을 막는다. 이 조건 때문에 q10처럼 baseline
top-k가 `both`로 포화된 질의는 vector admission이 성공해도 final top-k로는 못 들어갈 수 있다.
그 경우 P1은 candidate-generation 진단에는 성공해도 제품 후보로는 효과성 FAIL이며, guard를
완화해 같은 P1을 재시험하지 않는다.

### 6.3 품질 회귀 가드

- C1 direct/exact: baseline hit→candidate miss 0
- route pair: root와 child 각각 capped final rank가 baseline보다 나빠지지 않음
- C6: aggregate coverage@10과 complete@10이 baseline 이상
- category: C1~C7 각각 R@10 hit 순감 최대 1, MRR 하락 최대 0.02 이하
- empty result: candidate가 baseline보다 증가시키지 않음
- 기존 accepted: any `regressed_accepted`는 0. aggregate gain으로 상쇄하지 않음

P1은 RRF arm을 바꾸므로 fallback parity 외에 RRF OFF parity를 요구하지 않는다. 대신 위
keyword/original-vector parity와 candidate-specific both protection이 P1의 비간섭 경계다.

### 6.4 09/10 legacy diagnostic success 조건

legacy 20은 이미 노출됐으므로 승급 판단이 아니라 방향 검증만 한다. P1 reformulation input을
실행 전에 JSON manifest로 고정한 뒤 다음을 함께 만족해야 새 sealed split 준비를 승인한다.

1. q04/q07/q10 세 generation-miss 정답이 모두 vector arm top-50에 들어온다.
2. 셋 중 적어도 두 질의가 final top-10으로 새로 회복된다.
3. 09 baseline 대비 R@10이 최소 2건(+10%p) 증가하고, MRR/nDCG@10은 비감소한다.
4. §6.1~§6.3 HARD 전항 PASS.

이 조건은 P1이 단지 arm trace를 바꾸는 후보가 아니라 실제 endpoint 반환을 개선하는지
판정하기 위한 것이다. q04/q07/q10 중 하나라도 source 표현을 바꿔가며 재시도하지 않는다.
legacy 결과가 이 기준에 못 미치면 P1 candidate는 반려하고 새 P1 alias/reformulation rule을
같은 legacy 결과에 맞춰 조정하지 않는다.

### 6.5 신규 sealed split

legacy 방향 검증을 통과한 하나의 implementation SHA에만 새 P1 fixture를 작성한다.

- 96 gate + 24 sealed holdout의 scored 120건으로 분리한다.
- Korean cross-language, English paraphrase/resource-vocabulary gap, multi-clause query를 포함하고
  C1 direct, C5 decoy, root/child route pair를 필수 대조군으로 둔다.
- 각 query의 `vector_reformulations` byte, candidate flag, query/split/corpus SHA를 manifest에
  고정한다. client LLM의 실시간 비결정성을 평가 실행에 남기지 않는다.
- legacy/v1/v2/v3 query·accepted endpoint·route-pair family와 신규성 검사를 한다. v3 sealed
  holdout은 verdict 91에 따라 열거나 재사용하지 않는다.
- gate에서 HARD PASS 뒤에만 효과성을 판정한다. HOLDOUT은 lead의 별도 명시 승인 전까지 봉인한다.

sealed effectiveness는 baseline 대비 R@10 순증, MRR/nDCG 비감소, generation-miss cohort의
top-50 admission 및 top-10 recovery, both-arm preservation을 함께 요구한다. 정확한 최소
상승값은 fixture freeze 문서에 결과를 보기 전에 고정한다.

## 7. 리스크와 롤백

| 리스크 | 완화 |
|---|---|
| client가 잘못된 번역/의도 확장을 제공 | max2, syntactic dedup, original query 유지, both-arm/C1/route-pair gate. 서버가 correctness를 추측하지 않는다. |
| reformulation 수로 vector arm 폭을 우회 | 각 subquery top-50 후 best-rank merge, 최종 vector list를 50으로 재절단한다. |
| 새 vector hit가 `both` evidence를 밀어냄 | P2-style tail replacement 금지 및 baseline final `both` subset HARD. |
| latency·메모리 증가 | 추가 vector 검색 최대 2회; benchmark에 embedding/ANN 전체 시간을 포함한다. P1 input 없으면 추가 호출 0. |
| API 혼동 (`query_variants`와 새 입력) | tool docstring에서 두 입력의 arm 범위를 표로 명시하고, integration schema test로 두 필드의 의미를 고정한다. |
| client가 입력을 생략 | flag ON이어도 missing/empty input은 byte-identical baseline이다. 품질 저하는 없지만 P1 이득도 없다. |
| 운영 문제 | `DOCS_MCP_SEARCH_VECTOR_REFORMULATION_ENABLED=false`가 즉시 rollback이다. flag OFF는 supplied input까지 무시하고 baseline과 완전 동일해야 한다. |

## 8. 구현 범위와 비범위

구현 시 변경이 허용되는 범위는 MCP tool input/docstring, `CandidateSearchOptions`, P1
normalizer, RRF vector branch, feature flag/composition, P1 trace와 테스트다. 기존
`query_variants`, keyword search, RRF implementation, fallback vector path, exact prelude, P2
implementation은 동작을 바꾸지 않는다.

다음은 명시적 비범위다.

- server LLM/translation API, alias dictionary, operation/path rule
- keyword `query_variants` policy 변경 또는 keyword reformulation
- RRF weight/k 조정, third RRF arm, score fusion
- P2 quota 활성화·확대·결합
- final tail rescue, protected-slot permutation, structured lexical/text-primary v3 재시도
- P3 cross-encoder

## 9. 최종 설계 판정

P1은 client LLM이 이미 맡아야 할 semantic reformulation을 새 vector-only input으로 전달하고,
서버에는 후보 예산·결정성·rollback만 남긴다. q04/q07/q10 같은 generation miss에 새 vector
admission 기회를 만들되, keyword signal이나 fallback을 건드리지 않고 P2식 `both` slot 축출을
허용하지 않는다.

이 보수적 경계 안에서 legacy 09/10의 three-target admission과 two-target final recovery를
동시에 보여야만 새 sealed split으로 진행한다. 보여주지 못하면 P1을 더 많은 alias·quota·slot
규칙으로 튜닝하지 않고 별도 architecture를 다시 설계한다.
