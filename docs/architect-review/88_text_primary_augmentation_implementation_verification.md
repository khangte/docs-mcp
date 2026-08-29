# Text-primary bounded structured augmentation 구현 확인 판정

- 검토 기준: 설계 84, freeze 설계 85, verdict 86, 구현 계획 87
- 검토 HEAD: `4adb8b5d4392f68f2aef32008fb1f7af2b350466`
- 판정: **수정 필요 — v3 gate 실행 착수 보류**
- search/eval 실행: 없음

## 1. 확인 결과

제품 검색 구현(Task 1~4)은 계획 87의 승인 계약과 일치한다.

- `DOCS_MCP_STRUCTURED_AUGMENTATION_ENABLED`는 기본 OFF이고 `1/true/yes`만 True다.
- `search_lexical_field == "text"` conjunction으로 structured lexical full swap과 배타다.
- base-wide RRF를 `width`까지 완성한 뒤, text keyword top-width ref 전체를 protected로 고정한다.
- base-wide 내부 vector-only ref만 original-query term으로 A/B/C 점수화하며 D weight는 0이다.
- scorer는 ref `IN` 한 번으로 실행되고 빈 term/ref에는 SQL을 실행하지 않는다.
- postprocessor는 strict-greater, top-down, non-overlap adjacent swap이며 ref당 변위는 최대 1이다.
- 기존 `FusedResult`의 RRF score, match type, contributing arms와 ref multiset은 보존된다.
- exact가 `top_k`를 채우면 조기 반환하고, fallback은 `_search_rrf()`를 통과하지 않으며,
  document search에는 setting이 전달되지 않는다.
- request-scoped trace는 final `top_k` 절단 전에 한 번 생성되고 latency 반복에는 sink를 넘기지 않는다.

설계 84 §6.1의 역방향 scan 문구와 계획 87의 top-down scan은 문면상 다르다. 그러나 계획 87
I6가 top-down을 명시했고 lead가 I1~I10을 전부 승인했으므로, 구현의 top-down scan을 후행 승인된
구체화로 판정한다. protected absolute slot, displacement 1, strict-greater, non-overlap, no-injection
안전계약은 그대로 보존된다.

커밋 경계와 메시지는 Task 1~5 계획과 일치한다. Task 1에서
`app/services/search/endpoint_candidate_search.py`가 함께 커밋된 것은 계획의 Files/git-add 목록에는
빠졌지만 Step 3~4가 생성자 변경을 직접 요구하므로 정합한 보정이다. Task 5 뒤 evaluator/code review
fix가 추가됐으므로 이전 `f649f8d`가 아니라 최종 HEAD `4adb8b5d...`를 implementation SHA로 잡은
판단도 계획 87의 identity 규칙과 일치한다.

직접 재실행한 Task 6 회귀는 `232 passed in 38.31s`로 PASS했고 skip/xfail은 없다. 다음 frozen
파일은 freeze commit 이후 변경이 없으며 query raw SHA도 일치한다.

```text
queries_gate_v3.json = 1da41901a225990492ead8215eb6a5bfde8afde987cafb60f0f74d03cbd84fdf
split_sha256         = 701c43479425848c7af8f74360b88adb8f375d8bc801986ea2f684b5d45541e6
product_source_sha   = 961bccad9d7d7f169ea5ee17c81581782c441bec
rules_git_sha        = dbc29008aa9803fd708bf619d263f76925e4d2a6
```

## 2. 수정 필요 항목

### A1. comparator가 freeze 85의 전체 HARD/EFFECTIVENESS를 기계 판정하지 않는다 — blocker

현재 `compare_v3_candidate.py`의 top-level 판정은 다음만 수행한다.

1. frozen identity 형식·네 report 간 동일성
2. candidate-specific HARD 9항목
3. boundary crossing net과 recall paired net의 동일성

따라서 다음 freeze 85/계획 87 §5 계약이 누락되어도 comparator가 PASS할 수 있다.

- gate row가 정확히 96건인지와 네 report의 query ID 집합 완전 동일성
- C1 loss zero
- C1~C7별 hit 순손실 최대 1, MRR 하락 최대 0.02
- C6 coverage/complete baseline 이상
- empty-result 증가 0
- Recall@10 OFF/ON `+3pp` 및 hit 순증 `+3`
- MRR OFF/ON non-decline 및 한 activation `+0.02`
- nDCG@10 OFF/ON non-decline
- C2+C3+C5, Korean ON, effective-pair 하한
- gate crossing net 자체의 `+3` 하한
- final/holdout HARD와 final EFFECTIVENESS, crossing `+4` 하한

runner JSON도 C6 개별 accepted rank, language, empty-result 수 등 위 판정에 필요한 값을 모두 담지
않는다. 현재 `PASS (gate) — HARD 전항 ...` 메시지는 실제 검사 범위보다 강하다. 계획 87의 파일
구조 설명과 I8은 comparator가 freeze 85 HARD/EFFECTIVENESS를 기계 판정하도록 요구하므로,
Task 5는 이 상태로 완료 판정할 수 없다.

필요 수정:

- report schema에 전체 threshold 판정 입력을 추가한다. 원시 per-query 자료를 우선하며 comparator가
  지표를 재계산해야 한다.
- gate/final 각각 freeze 85 §6~§8의 모든 하한을 fail-closed로 검사한다.
- 각 누락/경계값을 하나씩 변조하는 RED→GREEN 단위 테스트를 추가한다.
- HARD 전항을 먼저 끝낸 뒤에만 EFFECTIVENESS 함수를 호출하는 순서를 테스트로 고정한다.

### A2. HARD item 8의 fallback parity가 계획 §5 명령에서는 공집합 비교다 — blocker

lead가 승인한 해석, 즉 “fallback rank parity + augmentation의 `_search_rrf()` 내부 격리”는
타당하다. exact/document는 구조적으로 augmentation 입력을 받지 않고 제품 단위 회귀가 이를
보조하므로 별도 gate search를 추가하지 않아도 된다.

그러나 현재 계획 §5의 네 실행은 `--strategy rrf`다. 이 경우 runner의 `fallback_run`은 `None`이고
report에는 `unaffected_paths.fallback = {}`가 기록된다. 네 report의 `{}` 동일성은 fallback rank
parity를 실측하지 않는다.

필요 수정은 둘 중 하나다.

- 네 gate 실행을 `--strategy both`로 고정해 같은 query 96건의 fallback rank를 report에 채우거나,
- rrf 실행과 별도의 동등한 fallback control을 report에 반드시 채우고 comparator가 비어 있으면
  FAIL하게 한다.

후자를 택하지 않는 한 가장 작은 변경은 §5 실행축을 `both`로 좁히는 것이다. exact/document는
승인된 structural-isolation 해석을 유지하되, comparator는 해당 필드가 의도적으로 structural
proof임을 구분해야 하며 세 필드가 모두 빈 것만으로 HARD 8을 PASS해서는 안 된다.

## 3. v3 gate 착수 조건

현재는 착수 조건 미충족이다. 아래를 모두 만족한 뒤 architect 재검토와 lead 명시 승인으로 gate를
시작한다.

1. A1/A2를 TDD로 수정하고 developer가 변경 파일·RED/GREEN·전체 회귀를 보고한다.
2. lead가 review-fix를 커밋·push하고 그 새 final full SHA를 `implementation_git_sha`로 확정한다.
   현재 `4adb8b5d...`는 수정이 생기는 즉시 평가 identity로 사용할 수 없다.
3. Task 6 전체 회귀, evaluator 정적 회귀, ruff/build를 다시 통과하고 tracked worktree가 clean이어야 한다.
4. query/split/product/rules/corpus/candidate contract와 v3 fixture/manifest/threshold 무변경을 다시 확인한다.
5. final SHA에서 신규 v3 전용 shared DB를 한 번만 preflight/index하고 fingerprint를 고정한다.
6. 같은 shared DB·final SHA·`lexical-field=text`·gate96으로 baseline/candidate × variants OFF/ON
   네 report를 만든다. fallback parity를 실측하는 승인된 실행축을 사용한다.
7. comparator가 일반 HARD와 candidate-specific HARD 전항을 먼저 PASS한 뒤에만 EFFECTIVENESS를
   판정한다. 어느 하나라도 FAIL이면 같은 v3 재시험과 holdout 개봉을 금지한다.
8. gate HARD/EFFECTIVENESS 전항 PASS 후에도 holdout은 lead의 별도 명시 지시 전까지 봉인한다.

## 4. 최종 판정

제품 검색 구현의 bounded augmentation 계약은 승인한다. 그러나 evaluator/comparator가 freeze 85의
전체 gate를 집행하지 못하므로 구현 전체와 v3 gate 실행 착수는 **수정 필요**다. reviewer 최종
승인은 이 누락을 해소하지 않으며, A1/A2 수정과 재검토 전에는 search/eval을 실행하지 않는다.

## 5. Developer 착수 전 해석 잠금

### C1. OFF/ON 축

OFF/ON은 augmentation 축이 아니라 **query variants 축**이다. 네 run은 다음 의미로 고정한다.

| report | variants | augmentation |
|---|---:|---:|
| baseline_off | OFF | OFF |
| candidate_off | OFF | ON |
| baseline_on | ON | OFF |
| candidate_on | ON | ON |

따라서 candidate 두 run 모두 augmentation이 active여야 한다. comparator의 happy-path synthetic
fixture도 두 candidate run이 각각 gate effectiveness를 충족하도록 고친다. report root의 arm,
variants, augmentation 조합이 위 표와 다르면 positional CLI 인자가 맞더라도 FAIL해야 한다.

### C2. MRR `+0.02`

freeze 85 §7.3을 문면 그대로 적용한다. OFF/ON 각각 candidate MRR은 baseline 이상이고 둘 중
적어도 하나는 `+0.02` 이상이어야 한다. rank 11→10 crossing 한 건의 RR 이득이 약 0.009라는
사실은 이 threshold를 완화하지 않는다. adjacent swap은 상위 rank에서도 일어날 수 있어 전체
MRR 개선은 boundary crossing만으로 결정되지 않는다. 실제 gate가 이 조건에서 FAIL하면 정상적인
후보 반려이며, 같은 v3에서 조정·재시험하지 않는다.

### C3. `compare_final` 범위

이번 A1 수정에서 gate와 final을 모두 완성한다. `compare_final`은 freeze 85 §8의 final HARD,
holdout safety, final EFFECTIVENESS, crossing `+4`, effective pair gate/holdout/all 하한과 holdout
combined 방향성을 전부 기계 판정해야 한다. 구현·단위 테스트는 synthetic report만 사용하며 실제
holdout 파일 개봉이나 search/eval은 하지 않는다.

### C4. report schema

제안한 per-row `language`, `per_accepted_ranks`, `result_empty`, raw rank는 승인한다. 여기에
`answer_mode`를 추가해 C6 coverage/complete를 재계산한다. comparator는 headline Recall/MRR/nDCG를
기존 `tests/fixtures/rrf_eval/metrics.py`의 any-hit `answer_rank` 정의로 재계산하고, C6만
`per_accepted_ranks`로 보조 HARD를 계산한다.

다음 execution identity 필드도 report root에 포함하고 comparator가 네 run 역할을 fail-closed로
검증한다.

- `variants_enabled`
- `augmentation_enabled`
- `arm` (`baseline`/`candidate`)
- `lexical_field == "text"`
- 실행 strategy가 fallback parity를 포함한다는 값
- `top_k == 10`

gate에서는 중복 없는 query ID 집합이 frozen gate96과 정확히 같아야 하고, final에서는 frozen
scored120과 정확히 같아야 한다. 네 report의 ID·split·category·language·answer_mode·pair metadata도
서로 및 frozen fixture와 일치해야 한다.

`unaffected_paths.fallback`은 단순 non-empty가 아니라 해당 scope의 **전체 query ID 집합**을 갖는
`id -> raw rank` map이어야 하며 baseline/candidate가 exact parity여야 한다. exact/document의 빈
map은 lead가 승인한 structural-isolation 증명의 표현으로 유지할 수 있다. 다만 HARD 8의 동적
PASS 근거는 full-coverage fallback map이고, exact/document는 pinned implementation SHA의 source
경계와 회귀 테스트가 근거임을 판정 코드·문서에서 분리해 명시한다. 세 map의 공집합 동일성만으로
HARD 8을 PASS시키는 경로는 금지한다.
