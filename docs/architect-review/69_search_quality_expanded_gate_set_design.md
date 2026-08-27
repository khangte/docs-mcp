# 69. 검색 품질 확장 게이트셋 정답·분포·프리즈 설계

- 선행 조건: 67번 §2 1~2단계 완료
- 비교 대상: baseline `ecc3e7923e216bf8e6b72ed609d5990749b2f700`
  vs candidate `8b4e36a`
- candidate 20건 방향성: OFF R@10 45→50%, ON 70→75%, 회귀 0
- 정답 단위: 27번의 binary `(document, method, path)` 유지
- 상태: 확장셋 저작 승인 설계. 실제 라벨 저작·검색 실행은 developer/lead 후속 작업이다.

## 1. 판정 요약

1. **기존 C1~C7은 유지한다.** 이는 질의 작성 시점에 정할 수 있는 사용자 질의 유형이다.
   route-family 편향·candidate-gen 실패 같은 결과 기반 실패유형은 카테고리로 쪼개지 않고
   `diagnostic_tags`로 직교 표기한다.
2. **기존 20건은 확장셋에 넣지 않는다.** 이미 설계·튜닝에 사용했으므로 smoke/진단셋으로
   보존하고, 신규 scored 120건을 별도 holdout 성격으로 저작한다. 66번의 bare-word
   진단 질의 4건을 더해 파일 레코드는 총 124건이다.
3. **scored 120건은 gate 96 + sealed holdout 24로 층화 분할한다.** developer는 검색을
   실행하지 않고 양쪽을 저작·정적 검증한다. lead가 gate 결과 통과 후 holdout을 한 번
   열어 최종 판정한다.
4. **headline은 기존 any-hit binary Recall/MRR/nDCG를 유지한다.** C6의 conjunctive
   복수 의도는 `answer_mode="all"`과 보조 `coverage@10`·`complete@10`으로 따로
   게이트한다. headline 산식을 소급 변경하지 않는다.
5. **과거 “No-result Rate”는 `answer_miss@10`으로 이름을 바로잡는다.** R@10의 정확한
   보수(1-R@10)였으므로 결과 공집합과 다르다. 실제 빈 후보는 `empty_result_rate`로
   별도 기록한다.
6. **확장셋·검증 보강은 검색 구현과 별도 fixture commit으로 프리즈한다.** 동일 fixture
   commit을 baseline/candidate worktree 양쪽에 적용해 기존 러너로 비교한다.

## 2. 파일과 레코드 계약

### 2.1 파일 위치

- 기존 `tests/fixtures/corpus_eval/queries.json`: 20건 smoke/진단셋으로 그대로 보존
- 신규 `tests/fixtures/corpus_eval/queries_gate_v1.json`: scored 120 + diagnostic 4
- 신규 `tests/fixtures/corpus_eval/gate_manifest_v1.json`: 질의셋 해시·코퍼스 해시·분포·비교 SHA

기존 파일을 교체하지 않는다. 기존 결과 재현성과 새 holdout의 독립성을 동시에 지키기
위해서다. 새 러너를 만들지 않고 `run_corpus_eval.py`에 `--queries-file`과 `--split`
선택 인자만 추가한다. 기본값은 계속 기존 `queries.json` 전체다.

### 2.2 질의 레코드 스키마

```json
{
  "id": "g001",
  "query": "...",
  "category": "C5-decoy구분",
  "domain": "stripe",
  "language": "ko",
  "evaluation_role": "scored",
  "split": "gate",
  "answer_mode": "any",
  "accepted": [
    {"doc": "stripe", "method": "DELETE", "path": "/v1/..."}
  ],
  "variants": ["..."],
  "pair_id": "p01",
  "pair_role": "root",
  "diagnostic_tags": ["route_family_pair", "root_target", "cross_language"]
}
```

필드 규칙:

| 필드 | 계약 |
|---|---|
| `id` | `g001`~`g124`, 파일 내 유일 |
| `category` | C1~C7 중 하나. 실패 결과가 아니라 질의 작성 유형 |
| `domain` | `stripe` 또는 `github` |
| `language` | `ko`, `en`, `code` 중 하나 |
| `evaluation_role` | `scored` 또는 `diagnostic` |
| `split` | scored는 `gate`/`holdout`, diagnostic은 `diagnostic` |
| `answer_mode` | `any` 또는 `all` |
| `accepted` | 27번 binary `(doc, method, path)` 목록 |
| `variants` | 규칙에 해당할 때만 존재. 빈 배열 대신 필드 생략 |
| `pair_id/role` | 대조쌍에만 둘 다 존재. role은 `root`/`child` |
| `diagnostic_tags` | 결과를 보기 전에 정할 수 있는 구조 태그만 허용 |

허용 태그 초기 집합은 다음으로 닫아 둔다.

- `route_family_pair`, `root_target`, `child_target`
- `lexical_control`, `common_token`
- `cross_language`, `multi_intent`, `detail_field`

`rerank_failure`, `candidate_gen_failure`, `improved`처럼 실행 결과로 정해지는 태그는
프리즈셋에 쓰지 않는다. 결과를 본 뒤 표본 구성을 설명하는 누수를 막기 위해서다.

## 3. A — 정답 라벨링 계약

### 3.1 공통 정답 규칙

1. 정답은 프리즈된 Stripe/GitHub OpenAPI 안에 실제 존재하는 endpoint의
   `(doc, method, path)`만 허용한다.
2. endpoint가 질의의 요청을 **독립적으로 완전히 충족**할 때만 `accepted`에 넣는다.
   같은 route family, 유사 summary, 관련 리소스라는 이유만으로 추가하지 않는다.
3. root/child를 동시에 정답으로 완화하지 않는다. 질의가 collection root를 요구하면
   root만, 명시 child resource를 요구하면 child만 정답이다.
4. `method`는 대문자, `path`는 프리즈 스펙의 템플릿을 바이트 단위로 그대로 쓴다.
5. query/variant를 검색 결과에서 역으로 만들지 않는다. 먼저 자연어 질의를 작성하고,
   canonical spec을 읽어 라벨을 붙인다.
6. C1의 code 직접질의를 제외하면 query/variant에 method+path, operationId, 스펙 summary
   전문을 복사하지 않는다.

### 3.2 `answer_mode="any"`

C1~C5·C7의 기본값이다. `accepted`는 원칙적으로 1건이다. 실제 API가 동일 사용자 의도를
완전히 충족하는 대체 endpoint를 둘 이상 제공할 때만 최대 3건까지 허용한다.

headline 채점은 기존과 같다.

```text
rank_any(q) = accepted 중 top-k에 가장 먼저 나온 순위, 없으면 None
Recall@k(q) = 1[rank_any <= k]
RR(q) = 1/rank_any, 미검출은 0
nDCG@10(q) = binary 단일-관련 근사 유지
```

여러 accepted를 “관련 endpoint 모음”으로 넓혀 넣는 것은 금지한다. binary any-hit이
낙관 편향되지 않게 하기 위해서다.

### 3.3 `answer_mode="all"` — C6 conjunctive 질의

C6 scored 12건은 서로 다른 두 의도를 한 요청에 담으며, 각 의도마다 정답 endpoint를
정확히 1건 둔다. 즉 `accepted`는 정확히 2건이다. 하위 의도 하나에 대체 endpoint가
여럿 필요한 질의는 v1에서 저작하지 않는다. flat binary 목록으로 any/all 그룹을 동시에
표현할 수 없기 때문이다.

headline은 20건과의 비교를 위해 `rank_any`를 계속 쓴다. 대신 C6 보조 게이트를 추가한다.

```text
coverage@10(q) = top-10에서 찾은 accepted 수 / 2
complete@10(q) = 1[accepted 두 건이 모두 top-10에 존재]
```

C6 집계는 평균 coverage@10과 complete@10 비율을 모두 출력한다. “한 의도만 맞힌
top-1”이 headline hit가 되더라도 다의도 충족으로 오해하지 않는다. graded relevance는
도입하지 않는다.

### 3.4 root/child 대조쌍

scored 120건 안에 **12쌍/24질의**를 넣는다.

- C2 2쌍, C3 2쌍, C5 8쌍
- Stripe 6쌍, GitHub 6쌍
- 한국어 6쌍, 영어 6쌍
- gate 10쌍, holdout 2쌍

한 pair는 같은 domain·language·query style에서 같은 route family를 대상으로 한 root
질의 1개와 child 질의 1개다. 두 질의는 각각 `answer_mode="any"`, accepted 1건이어야
한다. root accepted path는 child accepted path의 세그먼트 경계 prefix여야 하고 endpoint는
서로 달라야 한다. 가능한 한 operation 표현은 같게 두고 target specificity만 바꾼다.

예를 들어 “repository를 삭제”와 “repository의 webhook을 삭제”처럼 child 질의가 child
리소스를 명시해야 한다. 단순히 path 문자열을 자연어에 복사해 정답을 누설하지 않는다.

비교 시 미검출을 11위로 cap한다.

```text
r_s(q) = 실제 accepted 순위(1~10), top-10 미검출은 11
delta(q) = r_candidate(q) - r_baseline(q)
pair_nonregression(p) = [delta(root) <= 0 and delta(child) <= 0]
pair_effective(p) = pair_nonregression(p) and
                    [delta(root) < 0 or delta(child) < 0]
```

승급 게이트는 다음과 같다.

- gate 10쌍: 10/10 non-regression, 2쌍 이상 effective
- sealed holdout 2쌍: 2/2 non-regression, 1쌍 이상 effective
- 전체 12쌍: 12/12 non-regression, 3쌍 이상 effective

이는 “짧은 path를 올린 대가로 명시 child를 떨어뜨리는” 구현을 직접 차단한다. root만
개선하고 child가 1칸이라도 나빠진 pair는 non-regression 실패다.

### 3.5 no-result 용어와 산식

20건에서 No-result Rate가 정확히 `1 - Recall@10`이었던 값은 검색 결과 공집합이 아니라
**정답 미검출률**이다. v1부터 다음 두 지표를 분리한다.

| 이름 | 정의 | 용도 |
|---|---|---|
| `answer_miss@10` | scored 질의 중 `rank_any=None` 비율 | 주 품질 게이트, `1-R@10` |
| `empty_result_rate` | 검색 반환 list 자체가 빈 질의 비율 | 색인/후보 경로 운영 진단 |

`answer_miss@10`을 계속 “no-result”라 부르지 않는다. 후보 10건이 나왔지만 정답이 없는
상태와 후보가 한 건도 없는 상태는 원인과 조치가 다르다.

### 3.6 variants 라벨링

scored 자연어는 한국어 58건, 영어 58건이며 code 질의는 4건이다.

- 한국어 58건: 자연스러운 영어 variant **정확히 1개** 필수
- 영어 58건: `variants` 필드 없음
- code 4건: `variants` 필드 없음
- diagnostic 한국어 2건도 영어 variant 1개, diagnostic 영어 2건은 없음

OFF는 러너가 필드를 전달하지 않으므로 모든 한국어 레코드에 variant가 있어도 순수 원문
측정의 의미가 유지된다. 일부 한국어 variant를 일부러 비워 ON 조건에 호출자 계약 준수와
누락을 섞는 안은 반려한다. variants 미제공 내성은 OFF가 이미 측정한다.

variant 작성 규칙:

1. 클라이언트 LLM이 낼 법한 짧고 자연스러운 영어 의역 1개만 쓴다.
2. 원문의 operation, target, scope, 단수/복수 의도를 보존한다.
3. 원문에 없는 child resource·파라미터·method·path·operationId를 추가하지 않는다.
4. accepted endpoint의 summary 문구를 그대로 복사하지 않는다.
5. C6는 두 의도를 모두 한 variant에 보존하며 둘 중 하나를 생략하지 않는다.
6. variant를 여러 개 시험해 가장 잘 검색되는 것을 고르는 행위는 금지한다.

## 4. 라벨 검증 게이트

`run_corpus_eval.py`의 기존 로더/`_validate_labels`를 보강한다. 새 검증 러너를 만들지
않는다. 실제 검색 전에 아래 검증이 전부 실행되고 하나라도 실패하면 DB 검색을 시작하지
않는다.

### 4.1 자동 검증

1. JSON schema: 필수 필드, enum, 타입, 알 수 없는 category/tag 거부
2. ID·정규화 query 중복 없음; scored와 기존 20건 사이 query 중복도 없음
3. 레코드 수: scored 120, diagnostic 4; split 96/24/4
4. §5의 category/domain/language quota 정확 일치
5. corpus manifest SHA가 Stripe `3653ad45bbec...`, GitHub `80850db290cd...`와 일치
6. 모든 accepted `(doc, method, path)`가 해당 프리즈 문서에 정확히 1건 존재
7. `answer_mode=all`은 C6·accepted 2건, 그 외 기본 any 계약 준수
8. 한국어 variants 1건, 영어/code variants 없음; blank·중복·원문 동일 variant 거부
9. pair_id는 정확히 두 레코드(root/child), 동일 domain/language, accepted 1건씩,
   path prefix·서로 다른 endpoint 조건 충족
10. diagnostic은 headline·category gate 입력에서 제외

현재 `_validate_labels`의 “accepted 실재 확인”은 규모와 무관하게 전량 순회하므로 120건에도
재사용 가능하다. 위 구조 검증만 같은 로더 앞단에 추가한다.

### 4.2 사람 검토

developer 자동 검증 후 lead가 검색 실행 전에 다음을 검토한다.

- 12개 root/child pair 전량
- C6 12건 전량
- diagnostic 4건 전량
- 나머지 scored에서 category/domain/language 층화 20% 표본

검토 항목은 질의가 accepted를 실제로 충족하는지, variant가 의미를 늘리거나 정답 문구를
누설하지 않는지다. 검색 순위는 이 단계에서 보지 않는다. 오류 수정이 끝난 뒤에만 해시를
계산하고 프리즈한다.

## 5. B — 카테고리·도메인·언어 분포

### 5.1 primary category는 7개 유지

route-family 편향과 순수 어휘 갭은 검색 구현에 따라 달라지는 **결과 분류**다. 이를 C2a,
C2b처럼 primary category로 만들면 candidate 결과를 본 뒤 분포를 정하는 셈이고, 다음
모델에서는 의미가 바뀐다. C1~C7은 사용자 입력 형태로 계속 유지하고 구조적 가설은
`diagnostic_tags`로 집계한다.

### 5.2 scored 120건 목표

| category | 건수 | 비율 | Stripe | GitHub | 설계 의도 |
|---|---:|---:|---:|---:|---|
| C1 직접키워드 | 12 | 10% | 6 | 6 | exact/direct control, code 4 포함 |
| C2 한국어 패러프레이즈 | 24 | 20% | 12 | 12 | cross-language variants 핵심 축 |
| C3 영어 의역 | 18 | 15% | 9 | 9 | synonym/operation 표현 일반화 |
| C4 흔한 토큰 범람 | 12 | 10% | 6 | 6 | scored는 의도가 판정 가능한 수준으로 작성 |
| C5 decoy·specificity | 24 | 20% | 12 | 12 | root/child와 오작동 회귀를 두껍게 검증 |
| C6 다개념 | 12 | 10% | 6 | 6 | any headline + all 보조 게이트 |
| C7 endpoint 세부 | 18 | 15% | 9 | 9 | 긴 본문/필드 단서 recall |
| **합계** | **120** | **100%** | **60** | **60** | |

C2·C5를 각각 20%로 두껍게 두되 과반을 차지시키지는 않는다. route-family 수정만을 위한
벤치가 아니라 실무 검색 전반의 승급 게이트여야 하기 때문이다.

추가 diagnostic 4건은 C4 bare noun/control이며 Stripe 2, GitHub 2, 한국어 2, 영어 2다.
headline 120건과 category threshold의 분모에는 넣지 않고 순위 덤프만 보존한다.

### 5.3 언어·도메인 교차 quota

각 domain 60건 안에서 `ko 29 / en 29 / code 2`로 맞춘다. 전체는 자연어
`ko 58 / en 58`, code direct 4다.

| category | 각 domain의 ko | 각 domain의 en | 각 domain의 code |
|---|---:|---:|---:|
| C1 | 2 | 2 | 2 |
| C2 | 12 | 0 | 0 |
| C3 | 0 | 9 | 0 |
| C4 | 3 | 3 | 0 |
| C5 | 6 | 6 | 0 |
| C6 | 3 | 3 | 0 |
| C7 | 3 | 6 | 0 |
| **domain별 합계** | **29** | **29** | **2** |

Stripe/GitHub 한쪽에 한국어나 root 질의를 몰지 않는다. domain 효과와 언어 효과가
겹치면 어느 축에서 회귀했는지 분리할 수 없기 때문이다.

### 5.4 gate/holdout split

scored 120건은 category·domain·language·pair 여부를 층화해 다음처럼 나눈다.

| category | gate | holdout | 합계 |
|---|---:|---:|---:|
| C1 | 10 | 2 | 12 |
| C2 | 19 | 5 | 24 |
| C3 | 14 | 4 | 18 |
| C4 | 10 | 2 | 12 |
| C5 | 19 | 5 | 24 |
| C6 | 10 | 2 | 12 |
| C7 | 14 | 4 | 18 |
| **합계** | **96** | **24** | **120** |

holdout은 Stripe/GitHub 12:12, 자연어 ko/en 11:11, code 2로 맞춘다. pair 12쌍 중
10쌍은 gate, 2쌍은 holdout이다. holdout 레코드도 저장소에서는 보이지만 developer는
검색 실행·튜닝·결과 열람을 하지 않는다. lead가 gate PASS 뒤 한 번만 실행하는 운영상
sealed set이다.

## 6. C — 프리즈 절차

### 6.1 manifest

`gate_manifest_v1.json`에는 최소 다음을 기록한다.

```json
{
  "schema_version": 1,
  "status": "frozen",
  "query_file": "queries_gate_v1.json",
  "query_sha256": "...",
  "counts": {"total": 124, "scored": 120, "diagnostic": 4,
             "gate": 96, "holdout": 24},
  "corpus_sha256": {
    "stripe": "3653ad45bbec...",
    "github": "80850db290cd..."
  },
  "baseline_search_sha": "ecc3e7923e216bf8e6b72ed609d5990749b2f700",
  "candidate_search_sha": "8b4e36a",
  "rules": "docs/architect-review/69_search_quality_expanded_gate_set_design.md"
}
```

프리즈 순서:

1. developer가 검색을 실행하지 않고 질의·라벨·variants를 작성한다.
2. 자동 검증과 lead의 의미 검토를 완료한다.
3. JSON canonical bytes의 SHA-256을 manifest에 기록하고 `status=frozen`으로 바꾼다.
4. 질의·manifest·기존 러너 보강만 하나의 **evaluation fixture commit**으로 커밋한다.
5. lead가 freeze commit SHA를 결과 문서에 기록한다.

프리즈 뒤 query/accepted/variant/pair/split을 수정하지 않는다. 실제 라벨 오류가 발견되면
v1 결과를 폐기하고 `queries_gate_v2.json`과 새 해시로 양 구현을 처음부터 다시 비교한다.
실패 질의를 candidate에 유리하게 고쳐 v1을 계속 쓰지 않는다.

### 6.2 동일 evaluator로 두 구현 비교

fixture commit은 검색 구현 변경과 분리해야 한다. 두 임시 worktree에 같은 fixture commit을
적용한다.

- baseline search: `ecc3e7923e216bf8e6b72ed609d5990749b2f700`
- candidate search: `8b4e36a`
- corpus/query manifest: 동일 SHA
- Python lockfile·embedding model·Postgres 조건: 동일

각 worktree에서 gate split을 먼저 실행한다.

```bash
uv run python tests/fixtures/corpus_eval/run_corpus_eval.py \
  --queries-file tests/fixtures/corpus_eval/queries_gate_v1.json \
  --split gate --strategy both
uv run python tests/fixtures/corpus_eval/run_corpus_eval.py \
  --queries-file tests/fixtures/corpus_eval/queries_gate_v1.json \
  --split gate --strategy both --with-variants
```

gate가 §7을 통과한 뒤 lead만 같은 두 명령의 `--split holdout`, 마지막으로
`--split all`을 실행한다. 새 러너·새 metric 구현을 만들지 않는다. 기존 runner에
파일/분할 선택, C6 보조 지표, pair 표만 추가한다. `diagnose_variants.py`는 최종 결과에서
실패한 scored ID를 원인 분류할 때 기존 로직을 재사용하도록 `--queries-file`과
`--query-ids` 선택 인자만 허용한다. 진단 결과로 v1 라벨을 수정하지 않는다.

fallback은 candidate 수정 대상이 아니므로 rollback control이다. baseline/candidate에서
per-query rank가 완전히 같아야 한다.

## 7. 승급 판정표

아래는 p-value 기반 연구 판정이 아니라 120건 규모의 실무 paired gate다. 모든 HARD와
EFFECTIVENESS 조건을 통과해야 candidate를 승급한다.

### 7.1 HARD — 무결성·회귀

| 항목 | PASS |
|---|---|
| 프리즈 무결성 | query/corpus SHA 일치, 자동 검증 오류 0 |
| 실행 동등성 | 두 구현의 evaluator fixture commit·환경 동일 |
| fallback control | OFF/ON 모두 per-query capped rank 완전 동일 |
| C1 exact/direct control | candidate의 top-10 hit loss 0 |
| category 회귀 | 각 C1~C7에서 R@10 hit 순감소 최대 1건, MRR 하락 최대 0.02 |
| C6 all-of | coverage@10·complete@10 모두 baseline 이상 |
| route pair | gate/holdout/전체의 §3.4 non-regression 조건 전부 충족 |
| empty result | OFF/ON empty-result count가 baseline보다 증가하지 않음 |
| sealed holdout | OFF/ON 각각 R@10 baseline 이상, MRR 하락 0.01 이하 |

category의 1건 허용은 C1 12~C7 24처럼 작은 분모에서 한 칸 이동만으로 비율이 크게
흔들리는 것을 감안한 것이다. 단 C1 top-10 loss와 paired child regression은 허용하지 않는다.

### 7.2 EFFECTIVENESS — 수정 실익

scored 120건 전체 `rrf`에 대해:

| 항목 | PASS |
|---|---|
| OFF Recall@10 | candidate - baseline **≥ 3.0%p** |
| ON Recall@10 | candidate - baseline **≥ 3.0%p** |
| OFF/ON MRR | 각각 baseline 이상, 둘 중 하나는 **≥ +0.02** |
| OFF/ON nDCG@10 | 각각 baseline 이상 |
| targeted C2+C3+C5 | OFF 또는 ON에서 top-10 순증가 **3건 이상**, 다른 조건도 순감소 없음 |
| 한국어 58건 ON | baseline ON 대비 top-10 hit 순증가 **2건 이상** |
| route pair | 전체 12쌍 중 effective **3쌍 이상** |
| holdout 방향성 | OFF/ON을 합쳐 top-10 win이 loss보다 많고 최소 win 1건 |

3.0%p는 scored 120건에서 최소 4건의 순증가를 뜻한다. 20건의 한 질의 개선(5%p)을
그대로 일반화했다고 주장하지 않으면서, 단순 무회귀만으로 승급하는 것도 막는 최소 실익
기준이다.

`answer_miss@10`은 Recall@10의 보수이므로 별도 중복 임계값을 두지 않는다. 표에는
반드시 함께 출력하되 OFF/ON에서 각각 최소 3.0%p 감소해야 Recall 조건을 통과한다.

### 7.3 판정 결과

- 항목 전부 PASS: candidate 승급 가능
- HARD 하나라도 FAIL: 승급 반려, 해당 회귀 원인 판정
- HARD PASS + EFFECTIVENESS FAIL: 안전하지만 실익 미확증 — 승급 보류
- holdout FAIL: holdout을 보고 추가 튜닝하지 않는다. v1 candidate 반려 후 새 설계/새
  candidate가 생기면 v2 프리즈 절차로 다시 시작한다.

## 8. developer 저작 순서

1. 기존 20건을 복사하지 않고 §5 quota대로 신규 124 레코드 초안 작성
2. 12개 root/child pair와 C6 all-of 레코드를 먼저 완성
3. 나머지 category/domain/language quota 충족
4. variants를 §3.6 규칙으로 한 번만 작성
5. 기존 runner 로더에 §4 정적 검증, 파일/split 선택, pair/C6 출력만 보강
6. 검색을 실행하지 않은 상태에서 lead 의미 검토 요청
7. 승인 수정 후 SHA manifest 작성·evaluation fixture commit 생성
8. lead가 baseline/candidate gate → holdout → all 순으로 실행·판정

이 단계에서 endpoint 검색 코드, chunk builder, corpus spec, accepted 완화, 새 평가 러너는
변경하지 않는다.
