# 검색 품질 평가 2026-08-28 — keyword-variant 로직 · p02 route-pair 회귀

- 측정 대상 commit SHA: `6f9e244` (측정 시점 HEAD). 이후 커밋(`74c83f2`, `6f5727f`)은
  docs 전용 — `6f9e244` 이후 `app/` 변경 없어 측정 유효.
- 대상 로직 (doc `03_2026-08-27_variants_diagnosis.md` baseline `429302c` 이후 검색 경로 변경):
  - **HEAD 실효 변경은 `75fa5f3` (endpoint RRF keyword-variant symmetrization, B-only) 1건뿐.**
    `git diff --stat ecc3e792 HEAD -- app/` = `app/services/search/endpoint_candidate_search.py`
    1파일 46+/7-. `429302c`의 app 검색 코드는 `ecc3e792`와 동일하므로 측정 대상 delta =
    `75fa5f3` symmetrization.
  - component A — `8b4e36a` (route-family constrained rerank), `608731b` (최심 matched leaf
    우선) — 는 커밋 이력엔 있으나 `75fa5f3`가 `app/services/search/endpoint_route_reranker.py`를
    삭제하며 되돌려 **HEAD에 없다** (verdict 76).
- 근거 문서: `docs/architect-review/74_p02_coverage_fix_failure_and_keyword_variant_stop_verdict.md`,
  `docs/architect-review/76_verdict74_production_baseline_statement_verdict.md` (74 §4 production 기준 정정)
- 코퍼스 content_sha256: stripe=`3653ad45bbec`, github=`80850db290cd`
  (full: stripe `3653ad45bbec54fcbe461c541c908355b715018bdf455a0e11b27bedb2cbdee5`,
  github `80850db290cde4eb487e0efb587cf27f305e77b6bef96933ed8a09b5169d5b1d`)

이 문서는 두 개의 독립 실행을 담는다.

1. **p02 route-pair 개발 게이트** — verdict 74가 반증한 73번 coverage-aware
   variant admission + merged `top_k` cap 후보의 shared-index holdout 재현.
2. **C2~C4 variant 진단** — 현재 HEAD(`6f9e244` = `ecc3e792` + `75fa5f3`
   keyword-variant symmetrization) 상태의 `diagnose_variants.py` 재측정.
   q05·q07 영향 확인용.

---

## 1. p02 route-pair 개발 게이트 (verdict 74 근거)

### 1.1 실행

- 대상 후보: 73번 coverage-aware variant admission + merged `top_k` cap
  (미커밋 워킹트리)
  - `candidate_base_sha` = `17686f7cd981b930a020d0470625730501cbfc29`
  - `candidate_product_base_sha` = `75fa5f3f98bcd119f1f7bf3645b018cc0db4996d`
  - `candidate_source_state_sha256` =
    `36d2e5473b2fdfbee8013561dc71e6914f20fbc5d8e859f07321c9e90ffd112d`
    (미커밋 6파일 상태; developer session
    `4a84df97-a1cf-4c1c-a2ed-4ef4a293f5a1` file-history snapshot,
    message UUID `ede4f9d9-b61a-44e1-94a5-07847c473250`,
    `2026-08-28T05:00:18.838Z`; 논리 경로 오름차순
    `<file_sha256><두 칸><logical_path><LF>` 직렬화 bytes의 SHA-256.
    파일별 preimage 표는 `04_2026-08-28_p02_shared_index_eval.md`)
- shared DB: `rrfeval_ed5b97f0`
- shared-index fingerprint:
  `da3952f144ebf8d3b45e65c14318c54f01bcb1bf0ad1d4023422d1907fc02faa`
  (endpoint/chunk github=1220, stripe=589)
- query SHA-256: `6eb897d24d681d1389963007a184ded043d3ae914cf862f6ffd8aba7f75838d8`
  (`queries_gate_v1.json`)
- 명령: `uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --mode eval
  --db-url <...>/rrfeval_ed5b97f0 --queries-file queries_gate_v1.json
  --split holdout --strategy rrf` (variants OFF / candidate ON 짝 실행)
- raw 실행 trace (developer transcript
  `4a84df97-a1cf-4c1c-a2ed-4ef4a293f5a1`):
  OFF holdout runner `88e7cb59-d495-470b-adb6-42f0bf5bd306`,
  ON p02 pair `53fb663f-f5ba-442f-9fce-631b2377df77`,
  arm trace `8f0b73fe-6a56-41dc-ae3a-c0c237a01625`
- 재현 스크립트 SHA-256:
  `975e20ad40b43db66c38836e6a7a8c71ab5fbe6dcc98f3fd9006cc328d0010b1`
  (스크립트·중간 보고는 scratch 정리로 소멸; 감사 기록은
  `04_2026-08-28_p02_shared_index_eval.md`)

### 1.2 핵심 지표 — p02 route pair (holdout, github)

미검출 또는 top-10 밖은 cap `11`. 값은 동일 shared index 짝 실행의 raw runner
출력에서 전사.

| pair | role  | accepted                           | OFF | candidate ON | 판정 |
| ---- | ----- | ---------------------------------- | --: | -----------: | ---- |
| p02  | root  | `GET /repos/{owner}/{repo}`        |   4 |       **11** | 회귀 |
| p02  | child | `GET /repos/{owner}/{repo}/topics` |  11 |        **9** | 개선 |

- child 11 → 9: 소폭 회복
- root 4 → 11: 회귀 (top-10 밖으로 이탈)
- **pair non-regression: FAIL** — root/child 모두 baseline capped rank 이하를
  유지해야 하나 root가 악화

### 1.3 arm trace (g003 root / g004 child)

| query      | target variant-quality                      | admitted pool | fused rank (OFF -> ON) |
| ---------- | ------------------------------------------- | ------------- | --------------------: |
| g003 root  | rank 29/475, coverage 0.25, matched count 1 | no            |                4 -> 11 |
| g004 child | rank 79/817, coverage 0.67, matched count 2 | no            |                11 -> 9 |

g004 broad parent는 variant-quality rank 154, coverage 0.33, matched count 1.
target coverage(0.67)가 broad parent coverage(0.33) 이상인데도 admitted pool
밖이었다 — coverage count만으로는 target leaf(`topics`)와 토큰이 많은 sibling
설명을 구분하지 못한다(verdict 74 §2).

### 1.4 단위 테스트

73번 구현 단위 테스트 942건 PASS. 명세대로 동작한다는 증거이지 명세가 옳다는
증거는 아니다(verdict 74 §1).

---

## 2. C2~C4 variant 진단 (HEAD `6f9e244` = `ecc3e792` + `75fa5f3`)

### 2.1 실행

- 명령: `uv run python tests/fixtures/corpus_eval/diagnose_variants.py
  --top-k 10 --wide 50`
- 임시 DB(스크립트가 생성·삭제), corpus stripe=589 / github=1220 endpoints
  (`corpus_manifest.json` content_sha256 검증 통과)
- 임베딩: `intfloat/multilingual-e5-small` (dim 384), is_semantic: true
- 전략: rrf 고정
- exit 0 (종료 시 임시 DB teardown의 `AdminShutdown` sqlalchemy 트레이스는
  결과와 무관한 정리 노이즈)
- 산출물: 아래 §4

### 2.2 분류 규칙 (doc 03 = 67번 §1 해석 규칙 고정)

- **OK**: best(top10) ≤ 3
- **FAMILY-RERANK 후보**: top10엔 없으나 넓은 후보군 top50엔 있음
- **CANDIDATE-GEN 실패**: top50에도 accepted 없음

### 2.3 결과 (doc 03 baseline `429302c` -> HEAD `6f9e244`)

`accepted 순위` = variants 있는 질의는 on, 없는 질의는 off. `미` = 미검출.

| 질의                            | 카테고리 | variants              | doc03 (top10 / top50) | HEAD (top10 / top50) | doc03 분류         | HEAD 분류          |
| ------------------------------- | -------- | --------------------- | --------------------- | -------------------- | ------------------ | ------------------ |
| q04 고객 새로 등록하고 싶어     | C2       | create a new customer | 1 / 1                 | 3 / 3                | OK                 | OK                 |
| q05 결제 환불 처리해줘          | C2       | refund a payment      | 미 / 41               | **3 / 3**            | FAMILY-RERANK 후보 | **OK**             |
| q06 이슈 새로 만들기            | C2       | create a new issue    | 3 / 3                 | 3 / 3                | OK                 | OK                 |
| q07 저장소 삭제해줘             | C2       | delete a repository   | 미 / 22               | **6 / 6**            | FAMILY-RERANK 후보 | FAMILY-RERANK 후보 |
| q08 cancel my recurring payment | C3       | (없음)                | 미 / 39               | 미 / 39              | FAMILY-RERANK 후보 | FAMILY-RERANK 후보 |
| q09 shut down a repository       | C3       | (없음)                | 미 / 24               | 미 / 24              | FAMILY-RERANK 후보 | FAMILY-RERANK 후보 |
| q10 show my billing history      | C3       | (없음)                | 미 / 미               | 미 / 미              | CANDIDATE-GEN 실패 | CANDIDATE-GEN 실패 |
| q11 customer                     | C4       | (없음)                | 미 / 미               | 미 / 미              | CANDIDATE-GEN 실패 | CANDIDATE-GEN 실패 |
| q12 pull request                 | C4       | (없음)                | 미 / 29               | 미 / 29              | FAMILY-RERANK 후보 | FAMILY-RERANK 후보 |

### 2.4 유형별 집계

| 유형               | doc03 | HEAD | 질의 (HEAD)             |
| ------------------ | ----: | ---: | ---------------------- |
| OK                 |     2 |    3 | q04, q05, q06          |
| FAMILY-RERANK 후보 |     5 |    4 | q07, q08, q09, q12     |
| CANDIDATE-GEN 실패 |     2 |    2 | q10, q11               |

`OK`/`FAMILY-RERANK 후보`/`CANDIDATE-GEN 실패`는 doc 03의 실패 분류 라벨이지 기능
이름이 아니다. component A(route-family rerank) 코드는 HEAD에 없으므로
`FAMILY-RERANK 후보 5→4`를 A의 효과로 읽지 않는다(verdict 76 §5). 이 delta는
반려된 component B(`75fa5f3`)가 포함된 트리에서 측정됐다.

---

## 3. 판정 및 symmetrization 영향 (q04·q05·q07)

### 3.1 판정 (verdict 74)

- **p02 개발 게이트 FAIL** — 73번 coverage-aware variant admission + merged
  `top_k` cap 구현 **반려, 커밋 보류**. coverage threshold·budget 숫자를 p02에
  맞춰 추가 조정하지 않는다.
- developer 제시 후보 a~d 전부 반려 (verdict 74 §3):
  a 원문 hit 있을 때만 pool 주입 → C2 cross-language 소멸,
  b coverage 절대 하한 → terse 정답 배제,
  c budget 2~3 축소 → off-target 선택 유지,
  d 기존 ref만 보정 → KO arm 0건에서 무효.
- **search-time keyword-variants 트랙 중단**. `75fa5f3` B-only,
  `608731b` A+B, 73번 워킹트리 모두 승급하지 않는다. verdict 74 §4는 production
  기준을 `ecc3e792`의 검색 동작 + 기존 vector variants 경로로 명시했으나,
  76번 정정: `75fa5f3`가 verdict 72 반려 후에도 revert되지 않아 main HEAD에 남아
  실제 HEAD 검색 동작은 `ecc3e792` + keyword-variant symmetrization이다
  (`query_variants`가 빈 경우에만 `ecc3e792`와 동일). `75fa5f3` revert 여부는
  lead 결정 사항.
- v2 프리즈·holdout 저작 착수 금지 (새 candidate가 p02와 v1 exposed
  regression 통과 전까지).

### 3.2 symmetrization 영향 (q04 회귀 · q05·q07 개선)

현재 HEAD(`6f9e244` = `ecc3e792` + `75fa5f3`)의 C2~C4 진단에서 두 건을 밀어올린
것은 **keyword-variant symmetrization 단독 효과**다. component A(route-family
rerank)는 HEAD에 없으므로(verdict 76) 이 개선의 귀속 대상이 아니다. 측정 delta는
baseline `429302c`(= `ecc3e792` 검색 코드) 대비 `75fa5f3` 한 커밋뿐이다.

- **q05** `결제 환불 처리해줘` / `refund a payment`: FAMILY-RERANK 후보 → OK.
  accepted `POST /v1/refunds`가 variants on top10 미검출 → 3위
  (top50 41 → 3).
- **q07** `저장소 삭제해줘` / `delete a repository`: FAMILY-RERANK 후보 유지,
  단 accepted `DELETE /repos/{owner}/{repo}`가 top10에 진입(6위).
  doc03 on top50 22 → HEAD on top10 6. off는 여전히 미검출.
- **q04** `고객 새로 등록하고 싶어` / `create a new customer` (회귀): 분류는 OK
  유지이나 accepted `POST /v1/customers` on top10 순위 1 → 3 (2계단 하락).
  delta 귀속이 `75fa5f3` 단독이므로 이 악화도 symmetrization의 비용이다 —
  B를 순개선으로만 읽지 않는다.

그러나 이 C2~C4 진단 개선은 p02 route-pair 개발 게이트 FAIL을 상쇄하지 않는다
(verdict 74 §1 — aggregate·개별 진단 개선으로 pair loss를 덮지 않는다).
p02 root regression(4→11)은 그대로다.

### 3.3 후속 조치 (verdict 74 §5·§7)

1. developer: 73번 production/test 워킹트리를 candidate commit으로 만들지
   않는다. 진단·verdict는 실패 근거로 보존.
2. lead: search-time keyword-variants 트랙 종료 여부 확정.
3. 계속 추진 시 architect에게 **별도 index representation + 재색인 설계** 요청
   (ADR-0003 read-only 범위 밖, 별도 설계 승인 필요). 검토 대상: path leaf
   token 보존, method × path shape operation alias, 가중 lexical field,
   결정적 생성.
4. 그 전까지 v2 프리즈·holdout 저작 착수 금지.
5. §2 진단(`429302c`→HEAD)은 반려된 component B(`75fa5f3`)가 포함된 트리에서
   측정됐다. `75fa5f3` revert가 결정되면 이 eval은 revert 후 재측정해야 baseline으로
   쓸 수 있다(verdict 76 §5).

---

## 4. 산출물

- p02 shared-index 감사 기록: `docs/eval-results/04_2026-08-28_p02_shared_index_eval.md`
- C2~C4 진단 raw 로그 (질의별 top-10 덤프 포함):
  `scratchpad/eval_variants_6f9e244_run2.log`
  (전체 경로: `/tmp/claude-1000/-home-kang-projects-docs-mcp--team-developer/9ebe7e5a-32fd-4a2d-9429-1c97113d6032/scratchpad/eval_variants_6f9e244_run2.log`)
- 재현: §1.1 / §2.1 명령 그대로.
