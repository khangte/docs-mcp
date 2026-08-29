# Text-primary bounded structured augmentation v3 freeze audit verdict

- 판정: **PASS — v3 sealed split freeze 가능**
- 규칙 문서: `docs/architect-review/85_text_primary_augmentation_v3_freeze_design.md`
- rules commit: `dbc29008aa9803fd708bf619d263f76925e4d2a6`
- 감사 범위: fixture·manifest·정적 verifier·novelty unit test
- 비범위: 검색 실행, 후보 효과 판정, holdout 결과 개봉, 제품 구현

## 1. 결론

설계 85의 V3-D1~V3-D12와 F1~F6 계약에 따라 v3 산출물을 재감사했다. 두 차례 보강으로 schema v3 exact-lock과 pair linkage 검증의 누락을 제거했고, 최종 상태는 F1~F6 전항 PASS다.

이 판정은 v3 fixture와 평가 규칙을 freeze할 수 있다는 뜻이다. text-primary bounded structured augmentation 후보의 품질이나 활성화를 승인하는 판정은 아니다. 후보 구현과 gate 실행은 별도 구현 계획 및 실행 승인 뒤에만 가능하다.

## 2. 감사 대상

- `tests/fixtures/corpus_eval/queries_gate_v3.json`
- `tests/fixtures/corpus_eval/gate_manifest_v3.json`
- `tests/fixtures/corpus_eval/run_corpus_eval.py`의 v3 정적 검증 경로
- `tests/unit/test_corpus_eval_v3_novelty.py`
- `tests/unit/test_corpus_eval_v2_novelty.py`의 manifest map 회귀 확인

감사 중 query fixture 내용은 변경되지 않았다. search/eval은 실행하지 않았고 제품 코드는 변경하지 않았다.

## 3. F1~F6 결과

### F1 Distribution — PASS

- scored 120 = gate 96 + holdout 24
- diagnostic 4
- category gate/holdout:
  - C1 `10/2`
  - C2 `19/5`
  - C3 `14/4`
  - C4 `10/2`
  - C5 `19/5`
  - C6 `10/2`
  - C7 `14/4`
- corpus: Stripe 60 / GitHub 60
- language: Korean 58 / English 58 / code-like 4
- corpus별 language: Korean 29 / English 29 / code-like 2
- holdout: corpus 12/12, language 11/11/2
- diagnostic: corpus 2/2, language Korean 2 / English 2
- C6 12건 전부 `answer_mode=all`, accepted endpoint 정확히 2개
- route pair 12 = gate 10 + holdout 2
- pair category C2/C3/C5 = 2/2/8, corpus 6/6, language 6/6
- gate pair corpus·language 5/5, holdout pair corpus·language 1/1

fixture 행에서 독립 집계한 값과 manifest counts 및 validator의 exact quota가 일치했다.

### F2 Novelty — PASS

- query·variant NFKC 정규화 기준 legacy/v1/v2 충돌 0
- v3 내부 query·variant 교차 충돌 0
- accepted tuple 136개 전부 고유
- accepted tuple의 v1/v2 재사용 0
- C6 endpoint set 재사용 0
- ID는 `v3g001..v3g124`, pair ID는 `v3p01..v3p12`로 연속·고유

novelty verifier는 위반 fixture를 이용한 negative tests와 frozen fixture positive test를 모두 통과했다.

### F3 Route-family pair-block scope — PASS

- v3 pair family와 v1/v2 pair family 교집합 0
- 서로 다른 v3 pair family 교집합 0
- 동일 pair root/child family 일치
- root path가 child path의 segment-prefix
- 일반 scored single끼리의 family 재사용 허용 positive test PASS
- pair와 일반 single 사이의 family 재사용 허용 positive test PASS

따라서 family 불교집합은 승인 범위인 12개 pair block에만 적용되고, 일반 scored query에는 endpoint-level 불교집합만 적용된다.

### F4 SHA bundle — PASS

독립 재계산 및 git object 확인 결과는 다음과 같다.

```text
query_sha256   = 1da41901a225990492ead8215eb6a5bfde8afde987cafb60f0f74d03cbd84fdf
split_sha256   = 701c43479425848c7af8f74360b88adb8f375d8bc801986ea2f684b5d45541e6
stripe_sha256  = 3653ad45bbec54fcbe461c541c908355b715018bdf455a0e11b27bedb2cbdee5
github_sha256  = 80850db290cde4eb487e0efb587cf27f305e77b6bef96933ed8a09b5169d5b1d
product_source = 961bccad9d7d7f169ea5ee17c81581782c441bec
rules_git_sha  = dbc29008aa9803fd708bf619d263f76925e4d2a6
```

novelty 기준 raw SHA도 실제 legacy/v1/v2 query 파일과 manifest가 일치했다.

```text
legacy = 8f61cb99006e0d07923111fc919aaaa7489b486b0fffca15928efce75355441f
v1     = 6eb897d24d681d1389963007a184ded043d3ae914cf862f6ffd8aba7f75838d8
v2     = a325583905a624c4e8293b7abff49e65741bc4aa6d0e09e48d5ed74bfa0346e5
```

rules commit은 실제 commit object이며 그 tree에 설계 85 문서가 존재한다.

### F5 Pair semantic linkage — PASS

- architect가 gate pair 10쌍의 query intent, accepted endpoint, collection/root→specific child 관계, decoy 방향을 직접 검토해 모두 PASS했다.
- holdout pair 2쌍은 lead가 sealed 책임 하에 동일한 네 항목을 검토하고 PASS를 attestation했다.
- validator는 각 pair의 root/child 정확히 1건, 동일 domain/language/split/category, accepted 1건씩, 같은 route family, segment-prefix, 서로 다른 endpoint를 강제한다.

holdout query 본문과 endpoint는 이 판정 문서에 기록하지 않는다.

### F6 Manifest schema v3 — PASS

manifest와 verifier가 다음을 값 자체로 exact-lock한다.

- schema version, dataset version, frozen status, query filename
- baseline/candidate lexical field `text`
- 설계 85 경로와 rules full SHA
- product source full SHA
- 양 corpus raw SHA와 legacy/v1/v2 novelty raw SHA
- counts 전체 구조
- candidate contract 전체 구조

candidate contract에는 다음이 명시돼 있다.

- `structured_query_source=original_query_only`
- text lexical arm primary와 protected absolute slots
- structured evidence A/B/C-only
- D, query variant, alias expansion 제외
- base-wide에 이미 존재하는 vector-only candidate만 대상
- candidate injection 금지
- non-overlapping adjacent max-one-swap
- `MAX_STRUCTURED_PROMOTION=1`
- `RRF_K=60`, lexical/vector arm weight `1/1`
- `_STRUCTURED_RANK_WEIGHTS=[0.1,0.2,0.4,1.0]` 및 `OPERATION_ALIASES` 불변
- 설계 84 경로

잘못된 scalar/SHA/count/contract 값을 주입하는 negative tests가 모두 실패를 확인한다. query/split SHA 값은 실제 파일에서 재계산해 manifest와 비교한다.

## 4. 감사 중 발견·보강 내역

### 1차 보강

초기 schema v3 verifier가 일부 identity를 hex 형식 또는 key 존재만으로 확인해, 다른 유효 hex와 잘못된 contract 문구를 놓칠 수 있었다. 다음을 보강했다.

- manifest 고정 scalar·SHA·counts·candidate contract exact comparison
- `structured_query_source=original_query_only` 명시
- diagnostic 및 pair 세부 분포 lock
- pair-block 한정 scope positive tests
- identity·counts·contract 변조 negative tests

query fixture는 변경하지 않았다.

### 2차 보강

- 동일 이름으로 중복 선언된 query SHA mismatch test 1개 제거
- pair 대표행의 입력 순서 의존 제거: `pair_role=root`를 명시 선택
- root/child category 동일성 검증 추가
- member-category mismatch 및 pair domain/language 세부 분포 negative tests 추가

이 보강에서도 query fixture와 manifest 고정 SHA는 변경되지 않았다.

## 5. 정적 검증 결과

실행 명령:

```bash
uv run pytest tests/unit/test_corpus_eval_v3_novelty.py tests/unit/test_corpus_eval_v2_novelty.py -q
```

결과:

```text
73 passed, 0 skipped, 0 xfailed
```

추가로 `git diff --check`를 통과했다.

## 6. Freeze 이후 계약

1. lead가 fixture, manifest, verifier, tests와 이 verdict를 함께 commit한다.
2. 이후 query, accepted endpoint, split, candidate contract, threshold를 후보 결과에 맞춰 변경하지 않는다.
3. 후보 implementation full SHA는 구현 후 eval identity에 별도로 고정한다.
4. gate 96 HARD 전항을 먼저 판정하고, PASS 뒤 gate EFFECTIVENESS를 판정한다.
5. gate HARD와 EFFECTIVENESS 전항 PASS 전에는 holdout을 개봉하지 않는다.
6. 어느 항목이든 FAIL이면 같은 v3로 bound/score/fixture를 조정하거나 재시험하지 않는다.

현재 단계에서 허용되는 다음 작업은 승인된 설계 84와 freeze 85를 구현 계획으로 옮기는 별도 요청뿐이다.
