# v3 gate96 text-primary bounded augmentation 최종 반려 판정

- 선행 설계·freeze: `docs/architect-review/84_text_primary_bounded_structured_augmentation_design.md`,
  `docs/architect-review/85_text_primary_augmentation_v3_freeze_design.md`
- 실행 승인: `docs/architect-review/90_v3_comparator_blockers_final_reverification.md`
- 실행 implementation SHA: `a3e8e23d4e2943c2a327cafbac682e1adfa55a45`
- shared-index fingerprint: `26b6cbed7b68070fc9687ff443c2a1413493c9b5de28209bc8c6f0e98ab3077e`
- query/split SHA: `1da41901a2259904...` / `701c43479425848c...`
- 판정: **FAIL — 현 후보 폐기·미활성화**
- holdout: **미개봉 상태로 영구 봉인**

## 1. 실행 무결성과 gate 결과

네 report는 같은 implementation SHA, shared-index fingerprint, query/split/corpus/rules/product
identity를 사용했다. comparator를 독립 재실행한 결과 일반 HARD와 candidate-specific HARD는
전항 PASS했고 EFFECTIVENESS에서 다음 항목이 FAIL했다.

- Recall@10 OFF/ON: 각각 `+0.00pp`, hit 순증 각각 `0`
- MRR activation: `[0.0, 0.0]`
- C2+C3+C5 hit 순증: `[0, 0]`
- Korean ON hit 순증: `0`
- effective route pair: OFF/ON 각각 `0 < 2`
- boundary crossing net: OFF/ON 각각 `0 < +3`

report raw-byte SHA-256은 다음과 같다.

```text
baseline_off  7f6e922790ee69dc267442a6d6345fa58440f8c659efd3e820827ee6f9cf88d4
candidate_off 112c0a87196000ae8750ece377ef1c619bcd612370e3652b577ac357b4a50b58
baseline_on   5487ec235cc2cfa0eed4be0332beba798dffa2eeb887db46fe176e34c6a65f56
candidate_on  60de5c737a030f5128db673f693319e5261b4c28899c5fac25bc11e3d926fef2
```

임시 shared DB는 결과 보존 뒤 drop됐고, 같은 v3 재시험과 holdout 개봉은 수행하지 않았다.

## 2. 원인 판정

### 2.1 구현 경로 미실행 또는 배선 결함이 아니다

candidate report에서 scorer와 postprocessor는 실제 실행됐다.

| 관측 | OFF | ON |
|---|---:|---:|
| structured-score 대상 ref | 2,055 | 1,688 |
| 양수 score가 있는 query | 74/96 | 74/96 |
| 양수 score ref | 805 | 772 |
| base-wide와 final-wide 순서가 다른 query | 62/96 | 63/96 |
| final Top-10 순서가 다른 query | 0/96 | 0/96 |
| 최초 이동 rank | 12 | 12 |

따라서 setting, scorer SQL, postprocessor 배선이 꺼져 생긴 완전 no-op이 아니다. HARD가 이를 놓친
것도 아니다. HARD는 primary·보호·bound의 안전성을 판정하고, 실제 Top-10 실익은 별도
EFFECTIVENESS가 판정하도록 freeze됐으며 이번 FAIL은 그 분리가 의도대로 동작한 결과다.

### 2.2 gate query가 augmentation 경로를 전혀 건드리지 않은 것도 아니다

74개 query에서 original-query A/B/C 점수가 양수였고 62/63개 query에서 wide 내부 swap이
발생했다. gate는 augmentation 경로를 충분히 실행했다. 다만 swap이 전부 반환 Top-10 아래에서만
발생해 제품 출력과 정답 순위에는 영향을 주지 못했다.

### 2.3 승인된 제약과 실제 retrieval topology의 결합으로 output-inert다

rank 10↔11 경계에서 OFF는 96개 중 83개, ON은 96개 중 95개 query에 protected ref가 적어도
하나 있었다. 둘 다 unprotected인 나머지 OFF 13개와 ON 1개도 lower score strict-greater 조건을
만족하지 않았다.

실제 base answer rank 11인 경우는 OFF 1개, ON 2개뿐이다. 두 activation에 공통인 `v3g096`은
rank 11 정답이 unprotected이고 양수 score를 가졌지만 rank 10이 protected라 승격할 수 없다.
ON의 다른 한 건 `v3g028`은 정답 자체가 protected다. 따라서 이 candidate contract의
text top-width 전량 absolute protection, base-wide vector-only 한정, 신규 injection 금지,
strict-greater adjacent max-one-swap을 모두 유지하면 gate96에서 11→10 crossing을 만들 수 없다.

이는 fixture 결함을 뜻하지 않는다. fixture와 threshold는 후보 결과를 보기 전에 봉인됐고,
candidate가 그 blinded effectiveness 하한을 만족하지 못했다. 실익을 만들려면 protected 정의,
candidate 집합/injection, 이동 bound, 또는 primary ranking 중 최소 하나를 바꿔야 하므로 현 후보의
수정이 아니라 별도 architecture다.

## 3. 후보 처분

lead 승인에 따라 다음을 확정한다.

1. 현 text-primary bounded structured augmentation 후보를 **폐기**한다.
2. `DOCS_MCP_STRUCTURED_AUGMENTATION_ENABLED`를 운영에서 활성화하지 않는다. cleanup 전까지도
   default OFF를 유지한다.
3. v3 gate FAIL은 최종 결과다. bound, score, protected 정의, fixture, split, threshold 또는
   implementation을 바꿔 같은 v3를 재시험하지 않는다.
4. v3 sealed holdout 24건은 개봉하지 않고 영구 봉인한다. final120도 실행하지 않는다.
5. 제품 요구가 다시 생기면 설계 84의 후보를 고치는 방식이 아니라 완전히 새로운 architecture를
   먼저 승인하고, 그 candidate identity에 맞는 완전히 새로운 sealed split을 만든다. v3 query,
   accepted endpoint, split, manifest, threshold를 새 후보 평가에 재사용하지 않는다.
6. diagnostic-only 재범위는 채택하지 않는다. Top-10 실익이 없는 production 경로를 유지할 근거가
   없고 별도 setting·SQL·trace·테스트의 유지비만 남긴다.

## 4. 별도 dead-code cleanup task

이 판정문에서 코드를 수정하지 않는다. lead는 developer에게 별도 cleanup task를 배정하고, 삭제
뒤 기존 text+vector RRF의 OFF 동작이 byte/순위 관점에서 유지되는지 회귀 검증한다.

### 4.1 production 경로

- `app/services/search/structured_augmentation.py`
  - 파일 전체: `MAX_STRUCTURED_PROMOTION`, `AugmentationTraceRow`, `AugmentationOutcome`,
    `RrfSearchTrace`, `apply_structured_augmentation`
- `app/repositories/chunk_repository.py`
  - `_STRUCTURED_AUGMENTATION_RANK_WEIGHTS`
  - `ChunkRepository.score_endpoint_structured_augmentation()`
- `app/services/search/endpoint_candidate_search.py`
  - augmentation module import
  - `CandidateSearchOptions.rrf_trace_sink`
  - constructor의 `structured_augmentation_enabled`와 내부
    `_structured_augmentation_enabled`
  - `_search_rrf()`의 scorer/postprocessor 분기와 `RrfSearchTrace` sink 생성
- `app/core/config.py`
  - `Settings.structured_augmentation_enabled`
  - env `DOCS_MCP_STRUCTURED_AUGMENTATION_ENABLED`
- `app/composition.py`
  - `AppState.structured_augmentation_enabled`
  - `AppState.from_engine(..., structured_augmentation_enabled=...)`
  - `build_services()`의 setting 전달

### 4.2 직접 대응 테스트

- 삭제: `tests/unit/test_structured_augmentation.py`
- 삭제: `tests/unit/test_structured_augmentation_repository.py`
- 삭제: `tests/unit/test_structured_augmentation_settings.py`
- 정리: `tests/unit/test_endpoint_candidate_search.py`의 augmentation fake/import/setting·wiring·
  trace-sink 테스트

삭제는 기존 RRF·keyword·vector·exact/fallback 동작의 테스트를 약화시키지 않는다. 후보와 무관한
기존 테스트는 유지하고, setting OFF 기준 baseline 순서가 cleanup 전 봉인 baseline report와
같음을 별도 regression으로 잠근다.

### 4.3 평가 전용 동적 실행 경로

- 삭제: `tests/fixtures/corpus_eval/compare_v3_candidate.py`
- 삭제: `tests/unit/test_corpus_eval_v3_candidate_gates.py`
- 정리: `tests/fixtures/corpus_eval/run_corpus_eval.py`의 아래 candidate 동적 경로
  - `--structured-augmentation`, `--report-json` 인자와 조합 guard
  - trace capture·serialization인 `_augmentation_trace_row()`
  - `_augmentation_identity_root()`, `_augmentation_effectiveness()`
  - candidate report JSON 생성 분기와 `AppState` augmentation 주입

반대로 아래 frozen 감사 자산은 실패 증거와 anti-retuning 기록이므로 삭제하지 않는다.

- `tests/fixtures/corpus_eval/queries_gate_v3.json`
- `tests/fixtures/corpus_eval/gate_manifest_v3.json`
- `tests/unit/test_corpus_eval_v3_novelty.py`와 manifest/freeze 정적 검증
- `docs/architect-review/84`~이 판정문까지의 설계·freeze·검증·FAIL 기록

## 5. 부수 발견: exact prelude와 trace rank 좌표 불일치

`v3g117` 한 건에서 report의 `base_answer_rank=2`, trace `final_wide`의 accepted ref rank도 2지만
실제 `answer_rank=1`이다. 원인은 `EndpointCandidateSearch.search()`가 exact match를 먼저 결과에
붙인 뒤 남은 `top_k`만 `_search_rrf()`에 넘기는 반면, `RrfSearchTrace.base_wide/final_wide`는
exact prefix를 제외한 RRF-local 1-based rank를 기록하고 `_augmentation_trace_row()`의
`answer_rank`는 exact prefix가 포함된 최종 반환 list에서 계산하기 때문이다.

이 좌표 불일치는 comparator가 cross-field invariant로 검사하지 않아 HARD를 통과했다. 그러나
baseline/candidate 모두 같은 exact rank 1이고 candidate Top-10 순서 변화가 0이므로 이번
`+0` delta와 EFFECTIVENESS FAIL에는 영향을 주지 않는다. 실패를 무효화하거나 같은 v3 재시험을
허용하는 사유가 아니다.

권고 후속 처리는 다음과 같다.

- 4장의 cleanup으로 v3 trace/report 경로를 제거하면 함께 제거한다.
- 향후 새 evaluator가 RRF-local trace를 재사용한다면 `exact_prefix_count`를 명시하거나 exact를
  포함한 full-output rank로 정규화한다.
- report에는 accepted ref의 final-output rank를 직접 직렬화하고,
  `answer_rank == min(valid accepted final-output ranks)`를 fail-closed invariant로 추가한다.
- 서로 다른 좌표의 `base_answer_rank`와 `answer_rank`를 boundary crossing 산식에 직접 섞지 않는다.

## 6. 최종 판정

v3 gate는 안전성에서는 PASS했으나 제품 출력에 대한 효과가 0이므로 후보 승급 조건을 명백히
충족하지 못했다. 현 후보는 폐기하고 미활성화하며, 같은 v3와 sealed holdout은 영구 봉인한다.
향후 시도는 별도 architecture와 새 sealed split으로만 가능하다.
