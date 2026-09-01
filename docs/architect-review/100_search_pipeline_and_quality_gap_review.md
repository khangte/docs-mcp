# 100. 현행 검색 파이프라인과 품질 목표 격차 검토

- 기준 코드: `41f59bb`의 P3 flag-off 상태, P2 quota `0`, P1/B1/B2 미승급 상태
- 기준 평가: `docs/eval-results/09_2026-08-31_corpus_eval.md` (legacy 20, `top_k=10`),
  `docs/eval-results/14_2026-08-31_p0_reaudit_arm_wide_final_coordinates.md`
- 관련 판정: 93(P2 dark), 95(P1 반려), 98(B1/B2 반려), 99(P3 promotion 불가)
- 상태: **참조·격차 검토 문서. 구현 지시가 아니다.**

## 1. 요약

현행은 “exact prelude + keyword/vector wide RRF”가 제품 경로이고, P2/P3는 코드가 있어도
기본 비활성이다. `query_variants`는 client가 제공할 때만 두 arm에 전달된다. 서버는 자연어의
의도·언어·route family를 해석해 query를 새로 만들지 않는다.

09의 RRF는 Recall@1 `25%`, @3 `35%`, @10 `45%`, MRR `0.318`, nDCG@10 `0.350`이다. 목표
(aspirational)는 각각 `70%`, `85%`, `95%`, `0.75`, `0.80`이므로, 현 20-query 좌표에서
R@10 hit 9건을 적어도 19건으로 만들어야 한다. P0 miss 11건 중 3건은 후보 생성 자체가 없고,
4건은 keyword가 비어 vector rank가 12~42위이며, 나머지 4건은 both-arm RRF 포화로 final에서
잘린다. 하나의 현행 hook으로 이 세 종류를 모두 줄일 수 없다.

P2/P1/B1/B2/P3의 실험 공간은 이미 소진됐다. 다음은 quota·phrase·RRF k/alpha·P3 lock을
재조정하는 일이 아니라, **endpoint-level candidate generation을 새로 만들고 그 위에서 fusion과
ranking을 분리하는 retrieval architecture 재설계**여야 한다. 이것이 generation 3과
keyword-blank final-cut 4를 동시에 건드릴 수 있는 첫 지점이며, both-saturation 4는 그 뒤 새
fusion/ranker가 별도로 해결해야 한다.

## 2. 현행 실행 순서

### 2.1 MCP 입력에서 후보 DTO까지

아래는 `search_endpoints`의 실제 제어 흐름이다. tool 기본 `top_k`는 5지만, corpus eval은
명시적으로 10을 준다. `top_k` 허용 범위는 1~50이다.

```text
MCP search_endpoints
  → CandidateSearchOptions / validate / document-project scope
  → exact prelude
  → rrf 전략: keyword arm + vector arm → wide RRF → P2 hook → P3 hook
     또는 fallback 전략: keyword 우선, 0건일 때만 vector
  → exact + 중복 제거된 rest → EndpointCandidate DTO → MCP items
```

| 순서 | 호출자 → 구성요소 | 입력 | 출력·현재 파라미터 | 현재 동작 |
| ---: | --- | --- | --- | --- |
| 1 | MCP `search_endpoints` → `CandidateSearchOptions` | `query`, `top_k=5`, `document_id?`, `project?`, `query_variants?` | options | 반환 항목은 id/method/path/summary/match_type뿐; 상세는 후속 `get_endpoint_details`가 담당 |
| 2 | `EndpointCandidateSearch.search` → `_validate` | options | trim된 non-empty query, resolved scope | `top_k=1..50` 검증. `document_id`가 있으면 project와의 소속을 검증하고 document가 우선 |
| 3 | candidate search → chunk repo | resolved scope | endpoint chunk 존재 여부 | 해당 scope에 endpoint chunk가 없으면 즉시 `[]` |
| 4 | candidate search → `_search_exact` | 원 query, scope | 0개 이상 `match_type=exact` | `METHOD /path` 정확 일치면 method+path lookup, 아니면 query 전체 operationId 정확 lookup. RRF보다 앞서고 남은 `top_k`를 계산 |
| 5a | `search_strategy=rrf` (기본) | remaining top_k | §2.2 wide RRF 경로 | keyword와 vector를 항상 시도. semantic vector provider가 아니면 vector만 생략 |
| 5b | `search_strategy=fallback` | remaining top_k | keyword 결과 또는 vector 결과 | keyword가 1건 이상이면 vector 호출 없이 keyword만 반환; P2/P3 hook은 이 경로에 없음 |
| 6 | DTO 조립 | exact + rest ref id | 최대 requested `top_k` | exact id와 겹치는 rest 제거, batch endpoint 조회 후 `EndpointCandidate`로 반환 |

**route 판정의 경계:** 현 runtime에는 “root/child route family”를 분류해 점수화하는 로직이 없다.
route 관련 분기는 4번의 exact method+path/operationId lookup뿐이다. `route-pair`는 candidate가
root 또는 child의 capped rank를 악화시키지 않았는지 보는 평가 guard이지 검색 입력이 아니다.

### 2.2 기본 RRF 후보 생성 상세

| 단계 | 호출자 → 구성요소 | 입력 | 출력·현재 파라미터 | 주의점 |
| ---: | --- | --- | --- | --- |
| A | `_search_rrf` | remaining `top_k` | `width=max(top_k×4, 50)` | eval의 exact prefix≤1, `top_k=10`에서는 width=50 |
| B | `_keyword_search.search` | 원 query, scope, `top_k=width`, variants | keyword endpoint ref-id top-50 | Postgres FTS `text_tsv`, original query terms로 `ts_rank`. variants는 OR **filter**만 넓히고 score는 원문 terms만 사용 |
| C | `_search_vector_with_variants` → `VectorSearch.search` | 원 query 및 variants 각각, width, optional scope candidate ids | vector ref-id top-50 | local embedding→pgvector cosine. 각 subquery 결과를 best rank(동률이면 큰 score)로 병합해 50개 재절단; score≤0은 제거 |
| D | `reciprocal_rank_fuse` | keyword/vector id lists | `base_wide` top-width | endpoint 호출은 title arm/weights 없이 2-arm. `RRF_K=60`, `score=Σ 1/(60+arm_rank)`, ref-id 동점 정렬, match type=`keyword`/`vector`/`both` |
| E | `_apply_arm_rescue` (P2) | `base_wide`, remaining top_k | final candidate list | `DOCS_MCP_SEARCH_ARM_RESCUE_QUOTA=0` 기본. 0이면 `base_wide[:top_k]`와 동일. >0은 tail을 최대 3개 arm-exclusive로 치환하고 pure RRF 1개 보존 |
| F | `_apply_cross_encoder_rerank` (P3) | original query, `base_wide[:50]`, P2 결과, top_k | P3 final 또는 fallback | `DOCS_MCP_SEARCH_CROSS_ENCODER_ENABLED=false` 기본. enabled면 P2 quota를 0으로 강제. asset/error/score mismatch는 baseline fallback |

### 2.3 P3 enabled일 때만 추가되는 순서

P3는 wide 후보를 만들지 않는다. `base_wide[:min(50,len(base_wide))]`의 endpoint를 deterministic
format v1(method/path/summary/operationId/description/field names)으로 직렬화해 original query와
함께 local cross-encoder에 준다. 모델은 BAAI pinned revision, query 64 tokens/전체 512 tokens,
CPU offline load로 설계됐으나 99에서 promotion 보류됐다.

| P3 내부 순서 | 입력 | 출력·보호 규칙 |
| --- | --- | --- |
| 1 | base-wide top N=50 | query-document score 50개; candidate set과 arm rank/RRF score는 불변 |
| 2 | baseline `base_wide[:top_k]` | 이 안의 `match_type=both` ref를 original 0-based slot에 HARD lock |
| 3 | 50개 중 lock 아닌 ref | score 내림차순, 동점은 base rank/ref-id로 정렬 |
| 4 | locked slots + non-locked ordering | 빈 final slots만 채움. baseline final `both`의 id·slot·상대 순서 불변 |

이 때문에 q08/q09/q11/q12처럼 final 10개가 모두 both인 질의는 enabled여도 byte-identical이다.
q05/q06/q17/q18은 both lock이 없어 legacy에서 회복됐지만 q16 rank 1→2 HARD와 CPU p95 76초가
발생해 P3는 미승격이다.

## 3. 목표 대비 정량 격차

`docs/eval-results/README.md`의 품질 목표는 small legacy set의 PASS/FAIL gate가 아니라
aspirational target이다. 20건에서 1건은 5 percentage points이므로, 아래 숫자는 제품 승격선이
아닌 구조적 격차의 크기를 보여 준다.

| 지표 | 09 RRF | 목표 | 차이 | 20-query 환산 |
| --- | ---: | ---: | ---: | --- |
| Recall@1 | 0.25 | ≥0.70 | **−0.45** | 5 hit → 최소 14 hit, +9 |
| Recall@3 | 0.35 | ≥0.85 | **−0.50** | 7 hit → 최소 17 hit, +10 |
| Recall@10 | 0.45 | ≥0.95 | **−0.50** | 9 hit → 최소 19 hit, +10; answer_miss 11 → 최대 1 |
| MRR | 0.318 | ≥0.75 | **−0.432** | top-rank quality 대폭 개선 필요 |
| nDCG@10 | 0.350 | ≥0.80 | **−0.450** | multiple answer/상위 순위 모두 개선 필요 |
| empty result rate | 0.00 | ≤0.02 | +0.02 여유 | 09의 55%는 진짜 빈 응답이 아니라 answer_miss@10으로 이미 정정됨 |

R@10 목표만 보아도 11 miss 가운데 적어도 10 query-level hit를 새로 얻어야 한다. P3 legacy
효과(+4 hit)를 안전·지연 문제 없이 실현해도 R@10은 0.65로 목표보다 0.30 낮다. 현재 hook의
부분 최적화로 0.95를 약속할 수 없는 이유다.

## 4. P0 11 miss와 현재 candidate의 도달 한계

| 실패군 | qid | P0 실제 단계 | 무엇이 필요함 | 현 실험 결과 |
| --- | --- | --- | --- | --- |
| generation | q04, q07, q10 | keyword/vector arm width 50 밖 | 새 후보 생성 또는 query/index representation bridge | P1은 3건 vector admission 후 final 안전 HARD 실패로 반려; P3/RRF는 후보가 없어 무력 |
| keyword blank final-cut | q05, q06, q17, q18 | vector top-50 12~42위, keyword arm 0 | endpoint semantic ranking을 root/action/parameter까지 개선 | P2 quota≤3은 거의 못 건짐. P3는 4건 모두 회복했지만 q16 HARD·CPU latency로 미승격 |
| both saturation final-cut | q08, q09, q11, q12 | vector 4~15위이나 RRF wide 25~40위, final all both | overlap-aware fusion 또는 protected output을 바꾸는 새 architecture | P2는 q12만 quota3에서 회복. B1/B2 고정식은 효과 0, P3 both lock은 의도적으로 불가 |

P2는 quota 2/3에서 R@10 일부 순증이 있어 default-off dark candidate로 남았지만 R@1/@3은
불변이었다. P1은 q10 both subset HARD FAIL로 제거됐다. B1/B2는 효과 0이라 제거됐다. P3는
효과가 있으나 q16/CPU HARD 미해결의 flag-off dark diagnostic candidate다. 따라서 이들의
threshold·quota·phrase·lock·k/alpha를 넓히는 것은 새 증거 없이 같은 실패 공간을 재탐색하는
일이며 금지한다.

## 5. 구조적으로 남은 방향

### A. endpoint-level 다중 표현 후보생성 arm — **최우선 권고**

endpoint마다 현재 long chunk 하나에만 의존하지 않고, method/path/operationId/summary/tags,
request/response field names를 deterministic canonical endpoint document로 별도 색인한다. 이
index는 현 `text_tsv`를 바꾸지 않는 독립 candidate generator이며, multilingual dense와 lexical
retrieval을 endpoint 수준에서 수행한다. 원 RRF candidate와 새 arm candidate의 union은 trace로
분리한다.

- **기대 효과:** q04/q07/q10의 action+resource/어휘 gap을 candidate width 안으로 넣고, 짧은 root가
  long child에 밀린 q05/q06/q17/q18의 vector rank도 개선할 수 있다. 7개 miss에 직접 닿는다.
- **장점:** P1의 client reformulation이나 서버 LLM 없이 source OpenAPI의 결정적 field만 사용한다.
  v3의 weighted `search_tsv` primary swap과 달리 기존 keyword arm 순서를 바꾸지 않는다.
- **리스크:** 새 index/reindex, canonical text의 field coverage·truncation, duplicate endpoint
  representation, 새 arm decoy, storage/latency 비용. q08~q12의 final fusion은 별도로 남는다.
- **필수 측정:** arm-level Recall@50/union Recall@50, source field coverage, C1/route-pair/C6,
  base-arm byte parity, candidate attribution. 기존 20은 diagnostic만 쓰고 새 sealed split을 만든다.

### B. 후보 union 뒤의 학습된 fusion/ranking — A 다음의 권고

RRF의 arm-rank 합산을 final decision으로 두지 말고, 원 keyword/vector과 A의 endpoint arm이 만든
고정 union을 query-conditioned ranker가 재정렬한다. 이는 raw `ts_rank` 가중, fixed route boost,
`search_tsv` swap이 아니라 arm rank·method/path·canonical endpoint text를 입력한 새 ranking
architecture다. 모델 학습/score calibration이 필요하면 20 legacy가 아닌 분리된 labeled dev set만
사용한다.

- **기대 효과:** q08/q09/q11/q12의 “both라서 자동 승리”를 제거하고, A가 넣은 generation candidate를
  final top-10으로 보낼 수 있다. 이론상 11 miss 모두의 final stage에 관여한다.
- **장점:** RRF k/alpha 한 숫자를 재튜닝하지 않고, candidate availability와 ranking을 별도 trace로
  측정한다. route family를 상수로 boost하지 않아 root/child 기준을 label/feature 검증으로 드러낼 수 있다.
- **리스크:** 학습 데이터 품질·leakage, score drift, C1/route-pair/q16형 rank regression, model
  serving 비용. “learned”라는 이유로 both 보호를 임의로 완화할 수 없다.
- **필수 측정:** train/calibration/sealed 완전 분리, per-query candidate parity, C1 gross loss 0,
  route-pair 100% non-regression, C6 complete, all accepted rank deltas, latency/RSS. A+B는 처음부터
  하나의 새 candidate identity로 설계하고 ablation으로 A-only와 ranking-only 기여를 분리한다.

### C. serving 가능한 reranker 재설계 — A/B의 ranking component 대안

P3가 보인 q05/q06/q17/q18 회복 신호는 query-endpoint joint relevance 자체는 가치가 있음을
보인다. 이를 계속 쓰려면 GPU batch serving, quantized/ONNX inference, 또는 smaller multilingual
cross-encoder를 **새 candidate identity**로 선택해야 한다. CPU 0.6B F32/N=50 조합은 현재
p95 contract에서 탈락했다.

- **기대 효과:** A의 union 또는 현 lock 밖 후보의 semantic ordering, shallow-rank R@1 개선.
- **장점:** P3 diagnostic의 +4 R@10 hit를 재현할 가능성.
- **리스크:** GPU capacity/장애 fallback/비용, model-license·digest, q16처럼 base winner를 낮추는
  회귀, model 변경 후 legacy 결과 재사용 금지.
- **판정 경계:** production CPU benchmark가 현 identity의 p95를 통과하지 못하면 CPU P3는 종료한다.
  GPU·quantization·model 축소는 설정 변경이 아니라 새 설계와 새 sealed가 필요하다.

### D. goal 재보정과 평가 체계 확장 — 병행 필수

목표를 지금 낮추면 20-query 실패를 숨길 뿐이다. 먼저 96 scored + 24 unopened holdout 이상의
새 sealed set을 Korean/English, generation, root/child, both saturation, multi-answer, parameter
detail로 층화하고 실제 product query distribution을 확보한다. 그 결과로 aspirational 0.70/0.85/
0.95가 제품 SLA인지 장기 north-star인지 구분해 phased target을 정한다.

- **장점:** 1 query=5pp인 legacy 과적합을 막고, A/B/C 선택을 반증 가능하게 만든다.
- **리스크:** corpus/label 저작 비용과 시간이 들며, 평가 확장은 retrieval defect를 직접 고치지 않는다.
- **금지선:** 새 split 전 target 수치만 낮춰 현 baseline 또는 dark candidate를 승격하지 않는다.

## 6. architect 판단 — 어디를 먼저 건드릴 것인가

다음 제품 후보의 출발점은 **A: deterministic endpoint-level 다중 표현 candidate generator**다.
이유는 목표 R@10 격차의 대부분이 final 순위보다 earlier candidate quality에 있고, A만이
generation 3과 keyword-blank root/child 4를 같은 source-of-truth(OpenAPI field)에서 다룰 수
있기 때문이다. B가 다루는 both saturation 4는 A만으로 해결되지 않으므로 A의 arm/union trace를
고정한 뒤 B의 learned fusion 또는 C의 serving-capable reranker를 붙이는 순서가 맞다.

권장 순서는 다음과 같다.

1. A의 candidate-recall 전용 설계와 P0-style arm/union trace를 먼저 승인한다.
2. A-only로 generation/keyword-blank cohort의 Recall@50과 R@10 safety를 검증한다.
3. A가 고정한 union에 B 또는 C 중 하나를 독립적으로 붙여 both saturation과 final ordering을
   측정한다. P2/P1/B1/B2/P3 기존 parameters를 결합하지 않는다.
4. 모든 HARD를 통과한 단일 identity에만 새 sealed split을 architect/lead가 동결한다.

이 순서는 8 final-cut miss를 “후처리 quota”로 밀어 넣는 대신, 후보 생성과 final ranking을
각각 관찰 가능하게 만든다. generation 3을 포함한 목표 R@10 0.95의 나머지 격차는 A의 실제
Recall@50 결과 없이는 예측할 수 없으므로, 지금 수치 목표를 보장하거나 낮추는 판정은 하지 않는다.

