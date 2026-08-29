# v3 comparator verdict 88 수정 재확인

- 선행 판정: `docs/architect-review/88_text_primary_augmentation_implementation_verification.md`
- 검토 HEAD: `bd81f294e737bd2085a252c39a933c25c0750ec7`
- 판정: **수정 필요 — v3 gate 실행 착수 보류**
- search/eval 실행: 없음

## 1. 통과 확인

verdict 88 A1/A2의 큰 구조는 반영됐다.

- OFF/ON을 variants 축으로 고정하고 두 candidate run에서 augmentation을 활성화한다.
- report JSON은 `--strategy both`를 강제하며 fallback full-scope rank map을 기록한다.
- gate96/final120 frozen ID·metadata와 네 실행축을 검사한다.
- candidate-specific HARD 9항목, 공통 HARD, final holdout HARD, gate/final EFFECTIVENESS를
  별도 함수로 구현했다.
- top-level comparator는 모든 HARD를 먼저 호출한 뒤 EFFECTIVENESS를 호출한다.
- gate/final crossing, MRR, nDCG, Recall, targeted category, Korean, effective pair,
  holdout combined 하한이 코드에 존재한다.
- 대상 mutation suite 재실행 결과는 `58 passed in 3.98s`다.
- HEAD full SHA와 보고된 새 implementation SHA가 일치한다.
- query SHA는 `1da41901a225990492ead8215eb6a5bfde8afde987cafb60f0f74d03cbd84fdf`이고,
  freeze commit 이후 query/manifest/설계 85 변경 diff는 없다.

## 2. 남은 수정 필요 항목

### R1. False 실행축 필드 누락과 `strategy=fallback`이 execution identity를 통과한다 — blocker

`check_execution_roles()`가 boolean 필드를 `bool(rep.get(...))`로 비교하므로 False가 기대되는
`baseline_off`/`candidate_off`의 `variants_enabled`를 삭제해도 `bool(None) == False`로 PASS한다.
문자열 등 bool이 아닌 값도 truthiness로 해석된다. verdict 88 §5 C4는 필드 존재와 정확한 타입·값을
fail-closed로 요구한다.

또한 comparator는 strategy 허용 집합에 `fallback`을 포함하지만 runner는 report JSON에
`--strategy both`만 허용한다. 네 trace와 fallback parity를 함께 생성한 실행축이라는 identity를
잠그려면 comparator도 `strategy == "both"`만 받아야 한다.

필요 수정:

- `variants_enabled`와 `augmentation_enabled`는 key 존재, `type(value) is bool`, expected value
  exact match를 모두 검사한다.
- report comparator의 strategy는 정확히 `both`만 허용한다.
- 각 필드 삭제, `None`, 문자열 boolean, `fallback` 변조 테스트를 추가한다.

### R2. `result_empty`와 C6 `per_accepted_ranks` 누락이 HARD를 통과한다 — blocker

현재 `result_empty`는 `row.get()`의 falsy 기본값으로, `per_accepted_ranks`는 `or []`로 흡수된다.
그 결과 네 report 모든 row에서 `result_empty`를 삭제하거나 모든 C6 row에서
`per_accepted_ranks`를 삭제해도 synthetic gate가 PASS한다. 이는 report schema와 HARD를
fail-closed로 판정한다는 A1/C4 계약에 어긋난다.

필요 수정:

- frozen metadata에 accepted count를 포함한다.
- 모든 row에서 `result_empty`가 실제 bool인지 검사한다.
- 모든 row에서 `per_accepted_ranks`가 frozen accepted count와 같은 길이인지, 각 값이
  `None` 또는 유효한 raw rank 정수인지 검사한다.
- `answer_rank`도 `None` 또는 `1..10` 정수인지 검사한다.
- fallback map value도 `None` 또는 `1..10` raw rank 정수인지 검사한다.
- 누락·잘못된 길이·잘못된 타입·범위 밖 rank mutation 테스트를 추가한다.

직접 probe 결과:

```text
missing_false_variants      UNEXPECTED_PASS
missing_result_empty        UNEXPECTED_PASS
missing_c6_per_accepted     UNEXPECTED_PASS
strategy_fallback           UNEXPECTED_PASS
```

### R3. per-category hit floor가 frozen의 순손실이 아니라 gross loss를 센다 — blocker

freeze 85 §6.1.5는 category별 Top-10 hit **순손실** 최대 1건이다. 현재 `_hit_loss()`는 같은
category에서 candidate gain이 있어도 baseline hit가 빠진 ID 수만 센다. 예를 들어 loss 2, gain 2로
hit count 순변화가 0인 category도 FAIL한다. 이는 frozen threshold를 사후 강화하는 결과다.

C1 loss zero는 개별 loss 0을 유지하되, per-category floor는 다음 집계 의미로 고친다.

```text
net_loss(category) = baseline_hit_count - candidate_hit_count
PASS iff net_loss <= 1
```

loss 2/gain 2와 loss 2/gain 0 경계 mutation을 각각 PASS/FAIL로 고정한다.

### R4. C6 HARD가 frozen의 aggregate coverage/complete가 아니라 per-query non-regression이다 — blocker

freeze 85 §6.1.6과 verdict 69 §3.3의 C6 판정 단위는 C6 전체의 평균 coverage와 complete count다.
현재 구현은 모든 C6 query가 개별적으로 coverage/complete non-regression이어야 한다. 이는 frozen
계약보다 강한 새 HARD다.

OFF/ON별로 C6 전체를 집계해 다음만 판정한다.

- candidate mean coverage@10 >= baseline mean coverage@10
- candidate complete@10 count >= baseline complete@10 count

개별 query 값은 진단으로 남길 수 있으나 HARD로 승격하지 않는다. 한 query loss가 다른 query gain으로
정확히 상쇄되는 aggregate PASS와, aggregate가 실제 하락하는 FAIL mutation을 모두 추가한다.

## 3. implementation identity와 gate 착수 조건

현재 HEAD `bd81f294...`는 위 수정이 생기면 즉시 과거 implementation SHA가 된다. R1~R4 수정 후
lead의 새 review-fix commit full SHA를 최종 `implementation_git_sha`로 다시 확정해야 한다.

v3 gate 착수 조건은 다음과 같다.

1. R1~R4를 TDD로 수정하고 대상 mutation suite와 전체 회귀를 통과한다.
2. reviewer 재검토와 architect 재확인을 통과한다.
3. lead가 최종 commit/push 후 full SHA를 고정하고 tracked worktree clean을 확인한다.
4. frozen query/split/product/rules/corpus/candidate contract, fixture/manifest/threshold 무변경을
   다시 확인한다.
5. 그 전에는 preflight, search/eval, shared DB 생성, holdout 개봉을 실행하지 않는다.

## 4. 최종 판정

A1/A2의 기능 범위는 대부분 구현됐지만 fail-closed schema와 frozen 집계 의미에 blocker 4건이
남았다. 따라서 verdict 88은 아직 종결되지 않았고 v3 gate 실행은 승인하지 않는다.
