# v3 comparator verdict 89 blocker 최종 재확인

- 선행 판정: `docs/architect-review/88_text_primary_augmentation_implementation_verification.md`,
  `docs/architect-review/89_v3_comparator_fail_closed_reverification.md`
- 검토 구현 commit: `b0786bd3027ed57b78cfad0449b9a2b780984022`
- 판정: **PASS — verdict 88/89 종결, v3 gate96 실행 승인**
- 승인 범위: 신규 shared DB preflight/index와 gate96 baseline/candidate × variants OFF/ON 네 실행 및 comparator 판정
- 비승인 범위: sealed holdout 개봉, final120 실행, 후보 활성화
- search/eval/preflight/holdout 실행: 없음

## 1. R1~R4 재확인

### R1 — PASS

`check_execution_roles()`는 `variants_enabled`와 `augmentation_enabled` 각각에 대해 key 존재,
`type(value) is bool`, 역할표의 exact value를 순서대로 검사한다. `strategy`는 runner와 동일하게
정확히 `both`만 허용한다. False 축의 필드 삭제·`None`·문자열/비-bool과 `fallback` strategy
변조가 모두 fail-closed로 고정됐다.

### R2 — PASS

frozen query metadata에 `accepted_count`가 포함됐고 `_compare()`의 HARD 선행 구간에서
`check_row_schema()`가 호출된다. 모든 row의 `result_empty` 실제 bool, `per_accepted_ranks` list 및
frozen accepted count와의 exact length, 각 원소와 `answer_rank`의 `None | int[1,10]`을 검사한다.
`_valid_rank()`는 `type(x) is int`를 사용해 bool을 배제한다. fallback full-scope map의 각 값도 같은
raw-rank schema를 통과해야 하므로 누락·타입·길이·범위 변조가 HARD를 우회하지 못한다.

### R3 — PASS

category floor는 OFF/ON별 `baseline hit count - candidate hit count`를 `net_loss`로 계산하고
`net_loss <= 1`을 적용한다. loss 2/gain 2 PASS와 loss 2/gain 0 FAIL 경계가 테스트로 고정됐다.
동시에 C1은 별도 `_hit_loss()`로 gross loss zero를 유지하므로 두 계약이 섞이지 않는다.

### R4 — PASS

C6는 OFF/ON 각각에서 전체 all-of row의 mean coverage와 complete count를 집계해 candidate가
baseline 이상인지 판정한다. 개별 query loss를 HARD로 승격하지 않는다. query 간 loss/gain 상쇄
PASS, aggregate mean coverage 하락 FAIL, aggregate complete count 하락 FAIL 경계가 모두 고정됐다.

## 2. 변경 범위와 frozen 무변경

`b0786bd^..b0786bd`의 tracked 변경은 아래 두 파일뿐이다.

- `tests/fixtures/corpus_eval/compare_v3_candidate.py`
- `tests/unit/test_corpus_eval_v3_candidate_gates.py`

freeze commit `7c8de3386dce514466a0779742af338a4a26bef4`부터 검토 commit까지 query fixture,
manifest, 설계 85 threshold 문서의 diff는 없다. split은 query fixture에 봉인된 scored
`id<TAB>split<LF>` 직렬화이므로 fixture 무변경과 독립 재계산으로 함께 확인했다.

```text
query_sha256 = 1da41901a225990492ead8215eb6a5bfde8afde987cafb60f0f74d03cbd84fdf
split_sha256 = 701c43479425848c7af8f74360b88adb8f375d8bc801986ea2f684b5d45541e6
product SHA  = 961bccad9d7d7f169ea5ee17c81581782c441bec
rules SHA    = dbc29008aa9803fd708bf619d263f76925e4d2a6
manifest     = status `frozen`, candidate contract exact 유지
```

검토 시작 시 `HEAD == origin/main == b0786bd3027ed57b78cfad0449b9a2b780984022`였고 tracked
worktree는 clean이었다. 기존 untracked `scratchpad/`는 평가 identity와 frozen tracked 자산에
포함되지 않는다.

## 3. 검증 증빙

- architect 직접: comparator gate suite `77 passed`
- architect 직접: v3 novelty/integrity + comparator gate suite `136 passed`
- architect 직접: 변경 파일 `ruff check` PASS, `compileall` PASS
- reviewer 재검토: R1~R4와 경계 mutation 25건 PASS, 최종 승인
- 구현 commit 증빙: 관련 회귀 186건 및 당시 전체 suite `1259 passed`, build PASS

현재 architect runtime에서 전체 suite를 추가 재실행했을 때 PostgreSQL
`localhost:5432` 미기동으로 DB fixture가 setup되지 않아 `614 passed, 1 failed, 644 errors`로
종료됐다. 단일 failure도 DB를 요구하는 Alembic subprocess 검사이고 나머지는 connection-refused
setup error다. 이는 검토 commit에서 정상 DB 환경으로 완료된 전체 회귀 증빙을 뒤집는 제품/평가
회귀가 아니며, DB 비의존 대상 suite는 위와 같이 다시 PASS했다.

## 4. 착수 판정과 실행 잠금

verdict 89의 R1~R4 blocker와 architect 재확인 조건은 해소됐다. 따라서 verdict 88과 89를
**종결**하고 v3 gate96 실행을 **승인**한다.

lead는 이 판정 문서를 commit·push한 뒤 실행할 실제 HEAD full SHA를 네 report의
`implementation_git_sha`로 고정하고 tracked worktree clean을 확인한다. 그 다음 구현 계획 87의
순서대로 신규 v3 전용 shared DB를 한 번만 preflight/index하고, 같은 DB·SHA·`lexical-field=text`·
`strategy=both`로 네 gate report를 생성한다. 일반 HARD와 candidate-specific HARD 전항 PASS 뒤에만
EFFECTIVENESS를 판정한다.

gate HARD/EFFECTIVENESS 중 하나라도 FAIL이면 같은 v3 재시험과 holdout 개봉은 금지한다. gate 전항
PASS 후에도 sealed holdout은 lead의 별도 명시 승인 전까지 봉인한다.
