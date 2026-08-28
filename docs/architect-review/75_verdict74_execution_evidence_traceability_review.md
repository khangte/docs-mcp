# 75. verdict 74 실행 근거·추적성 리뷰 판정

- 대상: 74번의 verdict-73 p02 shared-index 재현 기록과
  `tests/fixtures/corpus_eval/gate_manifest_v1.json` result record
- 리뷰: candidate 소스 상태 및 shared-index/실행 trace 식별자 누락
- 상태: **수정 필요. 73·74·manifest staged commit 보류**

## 1. 판정

리뷰 지적을 승인한다.

74번의 `root 4→11`, `child 11→9`는 판정 방향을 바꾸지 않지만, 현재 기록만으로는
다음 두 질문에 답할 수 없다.

1. 정확히 어느 미커밋 6파일 상태를 ON candidate로 실행했는가?
2. 72번과 다른 `rrfeval_ed5b97f0` 물리 DB가 어떤 index fingerprint였고, 어느 raw
   실행 출력이 4→11·11→9를 산출했는가?

manifest의 `candidate_search_sha = "73-coverage-fix-worktree (uncommitted)"`는 사람이
읽는 설명이지 상태 식별자가 아니다. 74번의 “shared-index 재현”도 DB 이름만 있고 전체
fingerprint와 raw trace 식별자가 없다. 따라서 72번 result record와 동등한 감사 가능성을
충족하지 않는다.

## 2. 복구 가능한 source-state 식별자

실행 당시 repository HEAD는
`17686f7cd981b930a020d0470625730501cbfc29`, 제품 검색 기점은 B-only
`75fa5f3f98bcd119f1f7bf3645b018cc0db4996d`였다.

미커밋 candidate의 정확한 6파일 상태는 Claude developer session
`4a84df97-a1cf-4c1c-a2ed-4ef4a293f5a1`의 file-history snapshot으로 남아 있다.

- snapshot message: `ede4f9d9-b61a-44e1-94a5-07847c473250`
- snapshot time: `2026-08-28T05:00:18.838Z`
- file-history root:
  `/home/kang/.claude/file-history/4a84df97-a1cf-4c1c-a2ed-4ef4a293f5a1/`

snapshot의 논리 경로별 file SHA-256은 다음과 같다.

| 논리 경로 | snapshot file | SHA-256 |
|---|---|---|
| `app/repositories/chunk_repository.py` | `2cd4771de6dc3f96@v2` | `b577640726bfe07c6cbcc2492a19b58e7714b96d933d1eb6bc2a21f6e89806ac` |
| `app/services/search/endpoint_candidate_search.py` | `2a283f546b85f4ac@v3` | `8bcf1093d246f58955375730c6e735460edbd6d3f614c87051ca784430733cc3` |
| `app/services/search/keyword_search.py` | `c2c1e461faa82536@v2` | `62bac33aa5c4936bc1dfabbcdd1608b43e04e24bfdca1625bf0ef22bca73a5a1` |
| `tests/unit/test_chunk_repository.py` | `c0022c5ac2827e64@v2` | `4377bd36df126b522a993be94ea78d04f736c0f836abbd8c6f68429a01f11cff` |
| `tests/unit/test_endpoint_candidate_search.py` | `891da8b15540feb1@v3` | `1f7ba2ee7821c5add107fe76b11ff0bde4200841ea5f63805b32b8862debb808` |
| `tests/unit/test_keyword_search.py` | `7c3d5518e51d4783@v2` | `1ad77f1a6c97219f65611b6b7480aae3ba7dc30690146c16bb25388f764abf87` |

candidate source-state fingerprint는 위 여섯 줄을 **논리 경로 오름차순**으로
`<file_sha256><두 칸><logical_path><LF>` 형태로 직렬화한 bytes의 SHA-256으로 정의한다.

```text
36d2e5473b2fdfbee8013561dc71e6914f20fbc5d8e859f07321c9e90ffd112d
```

74번과 manifest에는 설명형 `candidate_search_sha` 대신 최소한 다음을 함께 기록한다.

- `candidate_base_sha = 17686f7cd981b930a020d0470625730501cbfc29`
- `candidate_product_base_sha = 75fa5f3f98bcd119f1f7bf3645b018cc0db4996d`
- `candidate_source_state_sha256 = 36d2e547...ffd112d`
- snapshot session/message/time와 위 직렬화 규칙

manifest schema를 불필요하게 일반화하지 않는다. 이 실패 record 하나에 필요한 명시적
필드를 추가한다.

## 3. shared-index와 실행 trace 식별자

재현에 사용한 물리 DB와 index fingerprint는 기존 preflight 기록에서 확인된다.

- shared DB: `rrfeval_ed5b97f0`
- shared-index fingerprint:
  `da3952f144ebf8d3b45e65c14318c54f01bcb1bf0ad1d4023422d1907fc02faa`
- query SHA-256:
  `6eb897d24d681d1389963007a184ded043d3ae914cf862f6ffd8aba7f75838d8`
- corpus SHA-256: Stripe `3653ad45bbec54fcbe461c541c908355b715018bdf455a0e11b27bedb2cbdee5`,
  GitHub `80850db290cde4eb487e0efb587cf27f305e77b6bef96933ed8a09b5169d5b1d`

raw 실행 출력은 developer Claude transcript에 남아 있다.

- transcript:
  `/home/kang/.claude/projects/-home-kang-projects-docs-mcp--team-developer/4a84df97-a1cf-4c1c-a2ed-4ef4a293f5a1.jsonl`
- OFF holdout runner tool result: message UUID
  `88e7cb59-d495-470b-adb6-42f0bf5bd306`
- ON holdout p02 pair tool result: message UUID
  `53fb663f-f5ba-442f-9fce-631b2377df77`
- arm trace tool result: message UUID
  `8f0b73fe-6a56-41dc-ae3a-c0c237a01625`
- repro script SHA-256:
  `975e20ad40b43db66c38836e6a7a8c71ab5fbe6dcc98f3fd9006cc328d0010b1`

74번에는 위 UUID를 동등한 실행 trace 식별자로 기록한다. 임시 scratch report 경로만
기록하는 것은 충분하지 않다. manifest에는 최소한 full shared-index fingerprint와
trace UUID 묶음을 넣는다.

## 4. 수정 범위

1. developer는 source snapshot 해시·실행 fingerprint·trace UUID를 독립 재확인한다.
2. 74번에 “실행·감사 근거” 절을 추가한다.
3. manifest의 설명형 candidate 식별자를 full base SHA + source-state SHA-256으로
   대체·보강하고 full shared-index fingerprint 및 trace UUID를 추가한다.
4. JSON parse, `git diff --cached --check`, 74번 수치와 raw trace 일치를 확인한다.
5. reviewer 재검토 전에는 73·74·75·manifest를 커밋하지 않는다.

이번 수정은 74번의 **반려·트랙 종료 판정 자체를 바꾸지 않는다**. 실패 기록이 어떤
소스와 인덱스에서 나온 것인지 72번 수준으로 추적 가능하게 만드는 감사 보강이다.
