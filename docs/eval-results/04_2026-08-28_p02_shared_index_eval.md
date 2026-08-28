# p02 shared-index 평가 2026-08-28

- 평가 범위: verdict 73 coverage-aware variant admission + merged `top_k` cap의 p02 개발 회귀 재현
- candidate source-state SHA-256:
  `36d2e5473b2fdfbee8013561dc71e6914f20fbc5d8e859f07321c9e90ffd112d`
- 비교 기준: repository HEAD `17686f7cd981b930a020d0470625730501cbfc29`, 제품 검색 기점
  `75fa5f3f98bcd119f1f7bf3645b018cc0db4996d`
- shared DB: `rrfeval_ed5b97f0`
- shared-index fingerprint:
  `da3952f144ebf8d3b45e65c14318c54f01bcb1bf0ad1d4023422d1907fc02faa`
- query SHA-256: `6eb897d24d681d1389963007a184ded043d3ae914cf862f6ffd8aba7f75838d8`
- corpus SHA-256: stripe
  `3653ad45bbec54fcbe461c541c908355b715018bdf455a0e11b27bedb2cbdee5`, github
  `80850db290cde4eb487e0efb587cf27f305e77b6bef96933ed8a09b5169d5b1d`
- 실행: `run_corpus_eval.py --mode eval --db-url …/rrfeval_ed5b97f0 --queries-file
queries_gate_v1.json --split holdout --strategy rrf` (OFF / candidate ON)

## candidate source-state preimage

다음 표는 위 candidate source-state SHA-256 `36d2e547…ffd112d`의 preimage다. 여섯 줄을
논리 경로 오름차순으로 `<file_sha256><두 칸><logical_path><LF>` 형태로 직렬화한 bytes의
SHA-256이 그 집계 해시이며, 미커밋 6파일 상태는 developer session
`4a84df97-a1cf-4c1c-a2ed-4ef4a293f5a1`의 file-history snapshot
(`/home/kang/.claude/file-history/4a84df97-a1cf-4c1c-a2ed-4ef4a293f5a1/`, message UUID
`ede4f9d9-b61a-44e1-94a5-07847c473250`, `2026-08-28T05:00:18.838Z`)으로 복원한다.

| 논리 경로 | snapshot file | SHA-256 |
|---|---|---|
| `app/repositories/chunk_repository.py` | `2cd4771de6dc3f96@v2` | `b577640726bfe07c6cbcc2492a19b58e7714b96d933d1eb6bc2a21f6e89806ac` |
| `app/services/search/endpoint_candidate_search.py` | `2a283f546b85f4ac@v3` | `8bcf1093d246f58955375730c6e735460edbd6d3f614c87051ca784430733cc3` |
| `app/services/search/keyword_search.py` | `c2c1e461faa82536@v2` | `62bac33aa5c4936bc1dfabbcdd1608b43e04e24bfdca1625bf0ef22bca73a5a1` |
| `tests/unit/test_chunk_repository.py` | `c0022c5ac2827e64@v2` | `4377bd36df126b522a993be94ea78d04f736c0f836abbd8c6f68429a01f11cff` |
| `tests/unit/test_endpoint_candidate_search.py` | `891da8b15540feb1@v3` | `1f7ba2ee7821c5add107fe76b11ff0bde4200841ea5f63805b32b8862debb808` |
| `tests/unit/test_keyword_search.py` | `7c3d5518e51d4783@v2` | `1ad77f1a6c97219f65611b6b7480aae3ba7dc30690146c16bb25388f764abf87` |

## p02 route pair 순위

미검출 또는 top-10 밖은 cap `11`이다. 다음 값은 raw runner 출력의 동일 shared index
paired run에서 전사했다.

| pair | role  | accepted                           | OFF | candidate ON |
| ---- | ----- | ---------------------------------- | --: | -----------: |
| p02  | root  | `GET /repos/{owner}/{repo}`        |   4 |           11 |
| p02  | child | `GET /repos/{owner}/{repo}/topics` |  11 |            9 |

## Raw 실행 trace

- developer transcript session: `4a84df97-a1cf-4c1c-a2ed-4ef4a293f5a1`
- OFF holdout runner output: `88e7cb59-d495-470b-adb6-42f0bf5bd306`
- candidate ON p02 pair output: `53fb663f-f5ba-442f-9fce-631b2377df77`
- arm trace output: `8f0b73fe-6a56-41dc-ae3a-c0c237a01625`
- reproduction script SHA-256:
  `975e20ad40b43db66c38836e6a7a8c71ab5fbe6dcc98f3fd9006cc328d0010b1`

## g003/g004 arm trace

| query      | target variant-quality                      | admitted pool | fused rank (OFF → ON) |
| ---------- | ------------------------------------------- | ------------- | --------------------: |
| g003 root  | rank 29/475, coverage 0.25, matched count 1 | no            |                4 → 11 |
| g004 child | rank 79/817, coverage 0.67, matched count 2 | no            |                11 → 9 |

g004의 broad parent는 variant-quality rank 154, coverage 0.33, matched count 1이었다.
Raw arm trace는 g004 target coverage가 broad parent coverage 이상임도 기록한다.
