# Evaluation trace rank-coordinate contract

## 1. 목적과 적용 범위

이 문서는 검색 evaluator가 서로 다른 rank 좌표를 혼용해 잘못된 HARD/EFFECTIVENESS 판정을
내리는 일을 막는 상설 설계 규약이다. 새 search architecture, 새 sealed split, 새 runner/report,
새 comparator를 설계하거나 기존 evaluator의 rank schema를 변경할 때 적용한다.

직접 근거는 `docs/architect-review/91_v3_gate96_candidate_rejection_verdict.md` §5다. v3 gate의
`v3g117`에서는 exact match가 최종 결과 rank 1에 먼저 붙었지만 RRF-local trace의 accepted ref는
rank 2로 기록됐다. report가 RRF-local `base/final` rank와 exact prefix를 포함한 `answer_rank`를
서로 다른 좌표라는 표시 없이 함께 보관했고, comparator도 그 cross-field 관계를 HARD로 검사하지
않았다. 이 문제는 verdict 91의 0-delta FAIL에는 영향을 주지 않았지만 차기 evaluator에서는 같은
schema 결함을 허용하지 않는다.

이 규약은 verdict 91로 폐기된 후보의 재시험을 허용하지 않는다. 새 evaluator는 별도 architecture와
새 sealed split 절차를 따라야 한다.

## 2. rank 좌표 정의

### 2.1 `rrf_local`

exact prelude를 제외하고 RRF 함수에 들어가거나 RRF에서 나온 list 안의 1-based rank다. wide
candidate, base RRF, postprocessor 전후 trace가 이 좌표를 사용할 수 있다. 이 값은 제품이 반환한
최종 rank가 아니다.

RRF-local trace를 report에 재사용하면 다음 중 하나를 반드시 택한다.

1. trace block에 `coordinate: "rrf_local"`과 `exact_prefix_count`를 명시한다.
2. exact 결과를 포함해 제품의 최종 출력 조립을 그대로 재현한 `final_output` 좌표로 정규화한다.

`exact_prefix_count`는 좌표 차이를 드러내는 필수 metadata이지, 그 값만 더해 final-output rank를
추론해도 된다는 뜻이 아니다. exact/RRF 중복 제거, 누락 endpoint 제거, top-k 절단이 있으면 단순
offset 변환이 틀릴 수 있다. 최종 지표와 crossing에는 직접 직렬화한 `final_output` rank를 사용한다.

### 2.2 `final_output`

exact prefix 추가, RRF/fallback remainder 결합, 중복 제거, 조회 실패 ref 제거, top-k 절단까지
제품 코드가 모두 끝낸 뒤 실제 반환 candidate list에서의 1-based rank다. 사용자가 관측하는 rank와
Recall/MRR/nDCG, accepted coverage의 기준 좌표는 이것뿐이다.

### 2.3 좌표 식별자

rank를 가진 report block은 좌표를 schema로 선언해야 한다. 필드명이나 코드 문맥만으로 좌표를
추정하지 않는다.

```json
{
  "rrf_trace": {
    "coordinate": "rrf_local",
    "exact_prefix_count": 1,
    "base_wide": [],
    "final_wide": []
  },
  "final_output": {
    "coordinate": "final_output",
    "items": []
  }
}
```

알 수 없거나 선언되지 않은 좌표는 comparator에서 fail-closed한다.

## 3. report 필수 schema

### 3.1 최종 출력 직접 직렬화

runner는 각 query에 대해 제품이 실제 반환한 candidate list를 `final_output.items`에 순서대로 직접
직렬화한다. 최소 필드는 `ref_id`와 1-based `rank`다. rank는 list 위치와 정확히 같아야 하고
`1..top_k` 범위의 실제 정수여야 한다.

accepted ref 각각의 final-output rank도 frozen accepted 순서와 같은 길이로 직접 직렬화한다.
권장 형태는 accepted identity와 rank를 함께 보존하는 것이다.

```json
{
  "accepted_final_output_ranks": [
    {
      "accepted": {"doc": "github", "method": "GET", "path": "/example"},
      "rank": 3
    },
    {
      "accepted": {"doc": "github", "method": "POST", "path": "/example"},
      "rank": null
    }
  ],
  "answer_rank": 3
}
```

`rank: null`은 해당 accepted ref가 final output에 없다는 뜻이다. accepted count, identity, 배열 순서는
frozen fixture와 exact match해야 한다. ref ID만으로 채점할 경우에도 frozen accepted tuple과 ref의
검증 가능한 매핑을 report 또는 pinned index identity로 제공해야 한다.

### 3.2 `answer_rank` cross-field invariant

`answer_rank`는 별도 검색이나 RRF trace에서 다시 계산하지 않는다. 아래 식으로
`accepted_final_output_ranks`에서 도출되는 값과 exact match해야 한다.

```text
valid = [rank for accepted rank if rank is not null]
expected_answer_rank = min(valid) if valid is non-empty else null
HARD PASS iff answer_rank == expected_answer_rank
```

즉 comparator는 다음을 모두 HARD로 검사한다.

- `accepted_final_output_ranks` 필드 존재
- frozen accepted count·identity·순서와 exact match
- 각 rank가 `null` 또는 bool이 아닌 `int`이며 `1..top_k`
- 각 non-null rank가 `final_output.items`의 같은 accepted ref 위치와 exact match
- `answer_rank == min(valid accepted final-output ranks)`, valid rank가 없으면 `null`

all-of query의 coverage/complete 계산도 같은 배열을 사용한다. headline any-hit `answer_rank`와
all-of coverage가 서로 다른 원천에서 계산되면 안 된다.

## 4. boundary crossing 좌표 규약

서로 다른 좌표의 rank를 boundary crossing 산식에 직접 혼용하는 것을 금지한다. 특히
`rrf_local base_answer_rank`와 `final_output answer_rank`를 비교해 `11→10` 또는 `10→11`로
세면 안 된다.

crossing을 계산하려면 비교하는 두 operand가 모두 다음 조건을 만족해야 한다.

- 둘 다 `coordinate: "final_output"`
- 같은 query, accepted identity, `top_k`, exact/dedupe/filter 규칙
- baseline과 candidate 각각 실제 제품 출력 조립을 끝낸 뒤의 rank
- 같은 sealed identity와 실행축

postprocessor 전후 crossing이 필요하면 runner가 두 list 각각에 대해 제품의 final-output 조립
규칙을 적용해 `base_final_output_rank`와 `candidate_final_output_rank`를 별도로 직렬화한다. comparator는
두 field의 coordinate tag가 같지 않거나 누락되면 HARD FAIL한다.

진단 목적으로 RRF-local displacement를 계산할 수는 있다. 다만 이름에 `rrf_local`을 명시하고
제품 Recall crossing/EFFECTIVENESS와 분리한다.

## 5. comparator HARD 요구사항

새 comparator는 EFFECTIVENESS 전에 전 query에 대해 다음 순서로 검사한다.

1. report identity, query ID 집합, frozen metadata를 검사한다.
2. 모든 rank block의 `coordinate`와 `top_k`를 검사한다.
3. RRF-local trace가 있으면 `exact_prefix_count`의 존재와 bool이 아닌 0 이상 정수 타입을 검사한다.
4. `final_output.items`의 rank 연속성, ref 중복 없음, `len <= top_k`를 검사한다.
5. accepted identity/count/순서와 `accepted_final_output_ranks`를 frozen fixture에 대조한다.
6. accepted rank를 `final_output.items`에서 독립 재계산해 report 값과 대조한다.
7. `answer_rank == min(valid accepted final-output ranks)`를 검사한다.
8. all-of coverage/complete가 같은 accepted rank 배열에서 재계산되는지 검사한다.
9. boundary crossing operand의 coordinate tag와 조립 조건이 exact match하는지 검사한다.
10. 위 HARD 전항 PASS 뒤에만 Recall/MRR/nDCG, crossing, category/pair effectiveness를 계산한다.

cross-field invariant는 진단 warning이 아니라 HARD다. 한 query라도 불일치하면 전체 report 비교를
거부한다.

## 6. 필수 mutation tests

runner/comparator TDD에는 최소한 다음 변조를 포함한다.

- `coordinate` 삭제·알 수 없는 값·`rrf_local`/`final_output` 바꿔치기
- RRF-local trace의 `exact_prefix_count` 삭제, `null`, bool, 음수, 문자열
- exact prefix가 1건 이상인 query에서 RRF-local rank와 final-output rank가 다른 positive fixture
- exact와 RRF remainder에 같은 ref가 있어 dedupe되는 fixture
- endpoint lookup 실패로 RRF ref가 final output에서 빠지는 fixture
- accepted final-output rank 필드 삭제·잘못된 길이·frozen identity 불일치
- accepted rank의 문자열/bool/0/`top_k+1` 변조
- `final_output.items` 순서와 accepted rank 불일치
- accepted가 하나도 나오지 않는데 `answer_rank`가 non-null인 변조
- 복수 accepted 중 최소 rank가 아닌 값으로 `answer_rank`를 변조
- all-of coverage/complete를 다른 rank source에서 계산한 변조
- crossing의 한 operand만 `rrf_local`로 바꾼 mixed-coordinate 변조

각 mutation은 comparator HARD에서 FAIL해야 한다. 단순 happy-path synthetic만으로 좌표 계약 통과를
주장할 수 없다.

## 7. 설계·리뷰 체크리스트

### Runner / report author

- [ ] 제품 반환 직전의 실제 candidate list를 `final_output`으로 직접 직렬화했다.
- [ ] 모든 rank block에 coordinate tag가 있다.
- [ ] RRF-local trace를 남기면 `exact_prefix_count`를 명시했다.
- [ ] accepted final-output rank를 frozen accepted 순서대로 직접 기록했다.
- [ ] exact, dedupe, missing-ref, top-k cut 이후 좌표를 사용했다.
- [ ] `answer_rank`와 all-of 지표가 같은 accepted rank 배열에서 나온다.

### Comparator author

- [ ] accepted rank와 final output의 cross-field invariant를 HARD로 검사한다.
- [ ] `answer_rank == min(valid accepted final-output ranks)`를 fail-closed한다.
- [ ] rank 타입에서 bool을 배제하고 범위를 검사한다.
- [ ] 좌표가 다른 operand의 산술·비교를 거부한다.
- [ ] boundary crossing 전에 coordinate와 실행 identity를 exact match한다.
- [ ] HARD 전항을 EFFECTIVENESS보다 먼저 실행한다.

### Architect / reviewer

- [ ] report schema에 rank 좌표가 자연어가 아니라 기계 판독 필드로 잠겼다.
- [ ] exact-prefix query가 positive fixture에 포함됐다.
- [ ] dedupe·누락 ref 때문에 단순 offset이 깨지는 경계를 검토했다.
- [ ] mutation suite가 cross-field와 mixed-coordinate 우회를 실제로 막는다.
- [ ] sealed 결과를 보기 전에 schema, invariant, threshold가 freeze됐다.

## 8. 변경 통제

새 evaluator 설계 문서는 이 규약을 normative dependency로 인용한다. 예외가 필요하면 evaluator 구현
전에 architect가 새 좌표 모델, 변환 규칙, mutation proof를 별도 승인해야 한다. 결과를 본 뒤
좌표 정의나 HARD invariant를 완화할 수 없다.

이 문서의 체크리스트를 통과해도 품질 후보가 승인되는 것은 아니다. 좌표 무결성은 evaluator가
효과를 올바르게 재기 위한 선행 HARD일 뿐이며, candidate의 별도 HARD/EFFECTIVENESS threshold를
대체하지 않는다.
