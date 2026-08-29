# Text-primary bounded structured augmentation v3 sealed-split freeze design

- 상태: **FROZEN — fixture 저작 전 규칙 고정**
- 선행 설계: `docs/architect-review/84_text_primary_bounded_structured_augmentation_design.md`
- 근거 판정: verdict 69, verdict 80, verdict 82, postmortem 83
- 적용 후보: text-primary 검색 결과에 protected-slot postprocess로 bounded structured augmentation을 적용하는 후보
- 적용 데이터셋: `queries_gate_v3.json`의 scored 120건과 별도 diagnostic 4건
- 금지: 이 문서가 포함된 commit 이후 fixture 내용이나 판정식을 후보 결과에 맞춰 조정하는 행위

## 1. 목적과 freeze 순서

v3는 structured `search_tsv` 전면 교체 후보를 다시 시험하는 데이터셋이 아니다. text lexical arm과 vector arm으로 만든 기존 wide RRF 결과를 primary로 유지하고, 설계 84가 정한 protected-slot postprocess 후보만 처음 평가한다.

freeze 순서는 다음과 같다.

1. 이 규칙 문서를 먼저 commit한다.
2. 그 commit의 full SHA를 `gate_manifest_v3.json.rules_git_sha`로 고정한다.
3. developer가 query fixture, manifest, 정적 novelty 검사를 저작한다.
4. architect가 F1~F6 상당의 독립 재감사를 수행한다.
5. fixture와 manifest를 commit한 뒤에만 구현 계획을 별도 승인 절차로 시작한다.

이 과정에서는 holdout query를 열거나 검색을 실행하지 않는다. fixture 저작과 freeze 감사는 정적 데이터 검사만 허용한다.

## 2. Candidate identity

### 2.1 제품 기준선

`product_source_sha`는 다음 full SHA로 고정한다.

```text
961bccad9d7d7f169ea5ee17c81581782c441bec
```

이 SHA는 후보 구현 SHA가 아니다. 후보 구현 후 실제 평가 실행에 사용한 implementation full SHA는 eval identity에 별도로 고정하며, freeze manifest를 수정해서 대체하지 않는다.

### 2.2 Candidate contract

manifest의 `candidate_contract`는 최소한 다음 의미를 기계 판독 가능한 필드로 담아야 한다.

- `design_path`: `docs/architect-review/84_text_primary_bounded_structured_augmentation_design.md`
- text lexical arm이 primary이고 기존 text 순위를 보호한다.
- structured evidence는 original query의 A/B/C weight만 사용한다.
- D weight, query variant, alias expansion은 structured score에 사용하지 않는다.
- structured evidence는 base-wide에 이미 존재하는 vector-only candidate에만 계산한다.
- 새 candidate injection은 금지한다.
- text keyword-backed 결과의 absolute slot은 protected다.
- 허용 이동은 서로 겹치지 않는 adjacent swap뿐이다.
- 한 문서의 최대 승격 폭은 `MAX_STRUCTURED_PROMOTION=1`이다.
- 기존 `RRF_K=60`, lexical arm weight `1`, vector arm weight `1`을 유지한다.

다음 FROZEN 값은 후보가 수정하거나 재튜닝하지 않는다.

- `_STRUCTURED_RANK_WEIGHTS = {0.1, 0.2, 0.4, 1.0}`
- `OPERATION_ALIASES`
- `RRF_K`
- 기존 lexical/vector arm weight

구조 가중치 상수 자체는 보존하지만, 이 후보의 postprocess evidence에는 A/B/C만 허용한다. 이는 상수 변경이 아니라 설계 84의 적용 범위 제한이다.

## 3. v3 fixture 구성

### 3.1 파일과 ID

- query fixture: `tests/fixtures/corpus_eval/queries_gate_v3.json`
- manifest: `tests/fixtures/corpus_eval/gate_manifest_v3.json`
- scored query ID: `v3g001`부터 `v3g120`
- diagnostic query ID: `v3g121`부터 `v3g124`
- route-pair ID: `v3p01`부터 `v3p12`

scored query는 총 120건이며 gate 96건, sealed holdout 24건으로 나눈다. diagnostic 4건은 scored 분모와 effectiveness 계산에서 제외한다.

### 3.2 Category 분포

| Category | 의미 | Gate | Holdout | Total |
|---|---|---:|---:|---:|
| C1 | exact/name 중심 | 10 | 2 | 12 |
| C2 | operation 중심 | 19 | 5 | 24 |
| C3 | resource·parameter 중심 | 14 | 4 | 18 |
| C4 | 설명형 일반 검색 | 10 | 2 | 12 |
| C5 | 혼합·부분 lexical 경쟁 | 19 | 5 | 24 |
| C6 | all-of 복수 정답 | 10 | 2 | 12 |
| C7 | 경계·decoy 경쟁 | 14 | 4 | 18 |
| **합계** |  | **96** | **24** | **120** |

C6 12건은 모두 all-of이며 query마다 accepted endpoint가 정확히 2개다. 두 endpoint를 모두 만족해야 complete로 계산한다.

### 3.3 Corpus·언어 분포

| 축 | Gate | Holdout | Total |
|---|---:|---:|---:|
| Stripe | 48 | 12 | 60 |
| GitHub | 48 | 12 | 60 |
| Korean | 47 | 11 | 58 |
| English | 47 | 11 | 58 |
| Code-like | 2 | 2 | 4 |

각 corpus 내부 총 60건은 Korean 29, English 29, code-like 2로 구성한다. diagnostic 4건은 Stripe/GitHub 2/2, Korean/English 2/2로 구성한다.

Korean scored query는 정확히 하나의 English variant를 갖는다. English와 code-like scored query는 variant가 없다. variant는 structured evidence 입력이 아니며, novelty 검사의 입력으로만 포함한다.

### 3.4 Route pair 분포

route pair는 12쌍이며 gate 10쌍, holdout 2쌍이다.

| 축 | Gate | Holdout | Total |
|---|---:|---:|---:|
| C2 | - | - | 2 pairs |
| C3 | - | - | 2 pairs |
| C5 | - | - | 8 pairs |
| Stripe | 5 | 1 | 6 pairs |
| GitHub | 5 | 1 | 6 pairs |
| Korean | 5 | 1 | 6 pairs |
| English | 5 | 1 | 6 pairs |

각 pair는 동일 route family의 root/child query 두 건으로 이루어진다. 같은 pair 안의 root와 child가 family를 공유하는 것은 필수 예외다.

## 4. Novelty와 누수 방지

### 4.1 Query novelty

query와 variant를 NFKC 정규화한 뒤 비교한다. 다음 충돌은 모두 0이어야 한다.

- legacy fixture와 v3의 query/variant 충돌
- v1 fixture와 v3의 query/variant 충돌
- v2 fixture와 v3의 query/variant 충돌
- v3 내부 query/variant 상호 충돌

대소문자·공백 등 기존 novelty verifier가 정규화하는 항목은 v2와 동일한 규칙을 계승한다. query와 variant 사이의 교차 충돌도 검사한다.

### 4.2 Accepted endpoint novelty

accepted endpoint identity는 corpus/source, HTTP method, normalized path의 tuple로 비교한다. 다음 충돌은 모두 0이어야 한다.

- v1 accepted tuple과 v3 accepted tuple
- v2 accepted tuple과 v3 accepted tuple
- v3 내부 accepted tuple 재사용

검사 대상은 scored query의 기본 accepted endpoint 120개, C6의 추가 endpoint 12개, diagnostic 4개를 합한 136 accepted tuple 전부다.

### 4.3 Route-family novelty의 한정 범위

route-family 불교집합은 선언된 12개 pair block에만 적용한다.

- v3 pair family와 v1/v2 pair family의 교집합은 0이다.
- 서로 다른 v3 pair의 family 교집합은 0이다.
- 동일 v3 pair의 root/child는 같은 family를 공유한다.

일반 scored query에는 route-family 불교집합을 요구하지 않는다. 일반 scored query는 4.2의 endpoint-level 불교집합만 유지한다. 이 한정은 Stripe corpus에서 60개의 전역 신규 family를 확보할 수 없다는 사전 feasibility 결과를 반영한 의도적 계약이다.

## 5. Manifest schema v3와 SHA bundle

`gate_manifest_v3.json`은 `schema_version: 3`, dataset `v3`, frozen 상태를 선언하고 최소한 다음 항목을 포함한다.

- query fixture 경로
- query 파일 raw-byte SHA-256 (`query_sha256`)
- scored split SHA-256 (`split_sha256`)
- gate/holdout/diagnostic 및 category/corpus/language/pair counts
- Stripe corpus raw SHA-256
- GitHub corpus raw SHA-256
- legacy/v1/v2 novelty 기준 query SHA 목록
- `product_source_sha`
- 2.2의 `candidate_contract`
- 이 규칙 문서 경로
- 이 규칙 문서를 포함한 commit의 full 40자 `rules_git_sha`

고정 corpus raw SHA는 다음과 같다.

```text
stripe = 3653ad45bbec54fcbe461c541c908355b715018bdf455a0e11b27bedb2cbdee5
github = 80850db290cde4eb487e0efb587cf27f305e77b6bef96933ed8a09b5169d5b1d
```

novelty 기준 query raw SHA는 다음과 같다.

```text
legacy = 8f61cb99006e0d07923111fc919aaaa7489b486b0fffca15928efce75355441f
v1     = 6eb897d24d681d1389963007a184ded043d3ae914cf862f6ffd8aba7f75838d8
v2     = a325583905a624c4e8293b7abff49e65741bc4aa6d0e09e48d5ed74bfa0346e5
```

query raw SHA와 scored split SHA 계산은 기존 evaluator의 v2 manifest verifier 방식을 v3로 확장한다. manifest 자기 자신은 query SHA 계산 입력에 포함하지 않는다.

## 6. Gate 96 HARD 계약

HARD는 effectiveness보다 먼저 판정한다. 아래 항목 중 하나라도 FAIL이면 effectiveness 수치가 좋아도 후보는 gate FAIL이다.

### 6.1 공통 HARD

1. **Integrity:** query raw SHA, split SHA, 양 corpus raw SHA, product source SHA, rules full SHA, counts, novelty, candidate contract가 모두 일치한다.
2. **Execution identity:** baseline/candidate 각각 OFF/ON을 동등 조건으로 실행하고, candidate 실행 전체가 하나의 implementation full SHA를 사용한다.
3. **Fallback exactness:** structured postprocess를 적용할 수 없는 경로의 결과는 baseline과 정확히 같다.
4. **C1 loss zero:** C1 Top-10 hit 손실은 OFF와 ON 각각 0이다.
5. **Per-category floor:** 각 C1~C7, 각 OFF/ON에서 Top-10 hit 순손실은 최대 1건이고 MRR 하락은 최대 0.02다.
6. **C6 all-of:** coverage와 complete count가 OFF/ON 모두 baseline 이상이다.
7. **Route-pair non-regression:** gate route pair 10쌍 전부가 OFF와 ON에서 pair-safety를 통과한다.
8. **Empty-result safety:** empty-result 수가 OFF/ON 모두 baseline보다 증가하지 않는다.
9. **Candidate-specific invariants:** 6.2의 9개 항목을 전부 통과한다.

### 6.2 Candidate-specific HARD 9항목

1. **Text-arm parity:** text lexical arm의 top-width document reference, score, rank가 baseline과 정확히 같다.
2. **Vector-arm parity:** vector arm의 document reference, score, rank가 baseline과 정확히 같다.
3. **Base-wide RRF parity:** postprocess 직전 wide RRF의 reference, rank, arm contribution이 baseline과 정확히 같다.
4. **Protected absolute-slot preservation:** text keyword-backed protected document의 absolute slot 위반이 0이다.
5. **Bounded displacement:** unprotected document의 absolute displacement가 1을 넘는 사례가 0이다.
6. **Zero-score no-op:** original-query A/B/C structured score의 최대값이 0인 query는 final Top-k reference와 순서가 base-wide와 정확히 같다.
7. **No injection/drop:** base-wide reference multiset과 postprocess 대상 multiset이 같고 base-wide 밖 candidate injection이 0이다.
8. **Unaffected-path parity:** exact search, fallback, document search 결과가 baseline과 정확히 같다.
9. **Pair gate:** gate route pair 10/10이 pair-safety를 통과한다.

4번은 `MAX_STRUCTURED_PROMOTION=1`만으로 대체되지 않는다. protected result는 1칸조차 내려갈 수 없다. 5번은 모든 비보호 result에 대해 adjacent max-one-swap 경계를 직접 검사한다.

## 7. Gate 96 EFFECTIVENESS 계약

HARD 전항 PASS 후에만 다음 effectiveness를 판정한다. verdict 69/80에서 고정한 수준을 낮추지 않는다.

1. **Recall@10 OFF:** baseline 대비 최소 `+3 percentage points`이고, Top-10 hit win-loss 순증이 최소 `+3`이다.
2. **Recall@10 ON:** baseline 대비 최소 `+3 percentage points`이고, Top-10 hit win-loss 순증이 최소 `+3`이다.
3. **MRR:** OFF와 ON 모두 baseline 이상이며, 둘 중 적어도 하나는 최소 `+0.02`다.
4. **nDCG@10:** OFF와 ON 모두 baseline 이상이다.
5. **Targeted categories:** C2+C3+C5 합산 Top-10 hit 순증이 한 activation에서 최소 `+3`, 다른 activation에서 최소 `0`이다.
6. **Korean ON:** Korean gate 47건에서 Top-10 hit 순증이 최소 `+2`다.
7. **Effective route pairs:** verdict 69/80의 기존 effective-pair 계산식으로 gate에서 최소 `2 pairs`다.
8. **Boundary crossing OFF:** `base rank 11 → final rank 10` gain에서 `base rank 10 → final rank 11` loss를 뺀 값이 최소 `+3`이며, OFF Recall@10의 paired win-loss 순증과 정확히 같다.
9. **Boundary crossing ON:** 같은 crossing net이 최소 `+3`이며, ON Recall@10의 paired win-loss 순증과 정확히 같다.

protected non-regression, zero-score no-op, unchanged query 수는 안전성 진단값일 뿐 effectiveness gain으로 계산하지 않는다. max-one-swap 후보의 Top-10 gain은 반드시 11→10 crossing으로 설명되어야 한다.

## 8. Sealed holdout 개봉과 final 120 계약

### 8.1 개봉 조건

holdout은 lead만 개봉할 수 있다. 다음 순서를 위반하면 v3 평가는 무효다.

1. rules commit과 fixture freeze/audit/commit 완료
2. gate 96 HARD 전항 PASS
3. gate 96 EFFECTIVENESS 전항 PASS
4. lead의 명시적 holdout 개봉

### 8.2 Final HARD

final 120에서도 6장의 공통 HARD와 candidate-specific 9항목을 동일하게 적용한다. 추가로 다음을 모두 만족해야 한다.

- gate pair 10/10, holdout pair 2/2, 전체 pair 12/12 pair-safety PASS
- sealed holdout Recall@10이 OFF와 ON 모두 baseline 이상
- sealed holdout MRR 하락이 OFF와 ON 각각 최대 0.01

### 8.3 Final EFFECTIVENESS

1. **Recall@10 OFF:** 최소 `+3 percentage points`, Top-10 hit 순증 최소 `+4`.
2. **Recall@10 ON:** 최소 `+3 percentage points`, Top-10 hit 순증 최소 `+4`.
3. **MRR:** OFF와 ON 모두 baseline 이상이며, 둘 중 적어도 하나는 최소 `+0.02`.
4. **nDCG@10:** OFF와 ON 모두 baseline 이상.
5. **Targeted categories:** C2+C3+C5 합산 순증이 한 activation에서 최소 `+3`, 다른 activation에서 최소 `0`.
6. **Korean ON:** Korean scored 58건에서 순증 최소 `+2`.
7. **Effective route pairs:** gate 최소 2, holdout 최소 1, 전체 최소 3 pairs.
8. **Holdout combined:** OFF/ON 합산 holdout win이 loss보다 많고, 최소 1건의 win이 존재.
9. **Boundary crossing OFF:** crossing net 최소 `+4`, OFF Recall@10 paired 순증과 정확히 일치.
10. **Boundary crossing ON:** crossing net 최소 `+4`, ON Recall@10 paired 순증과 정확히 일치.

## 9. 실패·재시험 금지 규칙

다음 중 하나라도 발생하면 같은 v3로 재시험하지 않는다.

- gate HARD 또는 gate EFFECTIVENESS FAIL
- holdout 개봉 뒤 final HARD 또는 final EFFECTIVENESS FAIL
- bound, score, candidate contract, query, accepted endpoint, split, threshold의 사후 조정
- 일부 query만 교체하거나 manifest SHA를 다시 만들어 동일 후보를 재시험

다음 후보를 시험하려면 원인 분석과 별도 승인을 거쳐 완전히 새로운 sealed split을 만들어야 한다. latency p50/p95는 OFF/ON별로 기록하지만 verdict 69/80에 없던 새 latency gate는 이번 freeze에 추가하지 않는다.

## 10. Freeze 재감사 F1~F6

fixture 저작 뒤 architect는 검색 실행 없이 다음을 독립 재감사한다.

- **F1 Distribution:** scored 120, gate96/holdout24, diagnostic4, C1~C7, corpus, language, C6 all-of, pair 분포가 3장과 정확히 일치한다.
- **F2 Novelty:** query/variant NFKC 및 accepted tuple이 legacy/v1/v2와 v3 내부에서 충돌 0이다.
- **F3 Pair-family scope:** pair block에만 family 불교집합을 적용하며 prior pair family 충돌 0, v3 pair 상호 충돌 0, pair 내부 root/child family 동일이다.
- **F4 SHA bundle:** query raw SHA, scored split SHA, 양 corpus raw SHA, product source SHA, rules full SHA가 실제 파일·commit과 일치한다.
- **F5 Pair semantic linkage:** 12 pair의 root/child가 같은 operation/resource 흐름의 일반 route와 구체 route로 의미상 연결되고 accepted endpoint가 각 의도에 맞는다.
- **F6 Manifest/schema:** schema v3 필수 필드, counts, novelty 기준, candidate contract가 빠짐없이 있고 evaluator/test가 이를 검증한다.

F1~F6 중 하나라도 실패하면 developer에게 수정 요청하고, 수정 후 전체 정적 감사를 다시 수행한다. 이 단계에서도 holdout query 내용은 architect에게 노출하지 않는다. holdout의 정적 검사는 sealed metadata와 lead가 통제하는 검증 경로로 수행한다.

## 11. 승인 결정 V3-D1~V3-D12

- **V3-D1:** 120 scored, gate96/holdout24와 고정 category/corpus/language/C6 분포를 승인한다.
- **V3-D2:** query/variant 및 136 accepted tuple의 strict novelty를 승인한다.
- **V3-D3:** route-family 불교집합을 12 pair block에만 한정하는 범위를 승인한다.
- **V3-D4:** product source와 bounded postprocess candidate identity를 승인한다.
- **V3-D5:** query/split/corpus/product/rules SHA bundle과 implementation SHA 분리를 승인한다.
- **V3-D6:** candidate-specific HARD 9항목을 승인한다.
- **V3-D7:** gate HARD/EFFECTIVENESS와 crossing net `+3`을 승인한다.
- **V3-D8:** final HARD/EFFECTIVENESS와 crossing net `+4`를 승인한다.
- **V3-D9:** protected/no-op을 effectiveness가 아닌 HARD·진단으로만 계산한다.
- **V3-D10:** gate 전항 통과 전 holdout 미개봉과 lead-only 개봉을 승인한다.
- **V3-D11:** 어느 단계든 FAIL 시 동일 v3 조정·재시험 금지를 승인한다.
- **V3-D12:** rules commit → fixture 저작 → F1~F6 감사/fixture commit → 구현 계획의 순서를 승인한다.

V3-D1~V3-D12는 전부 승인되었으며, 이 문서는 그 승인 내용을 변경 없이 freeze한다.
