# 검색 품질 평가 2026-08-28 — 색인 시점 구조 신호(가중 tsvector) · v1 exposed regression

`docs/architect-review/78_endpoint_index_structure_signal_design.md` §8.2 실행 순서의
두 번째 단계. 계획 79 Task 12. 전제(Task 11 p02 개발 회귀 게이트 PASS)는
`docs/eval-results/06_2026-08-28_structured_lexical_p02_gate.md` 에서 충족.

HARD 판정 기준은 `docs/architect-review/69_search_quality_expanded_gate_set_design.md`
§7.1, route pair 산식은 §3.4. EFFECTIVENESS(§7.2)는 **기록만** 한다 — v1 은 노출된
개발 코퍼스이므로 이 수치로 승급하지 않는다(verdict 74 §6.2).

## 대상 상태

- 측정 대상 commit SHA: `29d4534` (branch `weighted-tsvector-track` HEAD, 워킹트리
  clean — `git status --porcelain` 비어 있음). 계획 79 Task 1~10 커밋 완료 상태.
- 임베딩: `intfloat/multilingual-e5-small` (dim 384), is_semantic: true (전체 실행).
- baseline = `--lexical-field text` (현행 `chunk.text_tsv`),
  candidate = `--lexical-field structured` (가중 `chunk.search_tsv`, 78번 설계).
  같은 물리 공유 인덱스 위에서 이 플래그만 바꾼 짝 실행이다(78번 §8.1).

## 공유 인덱스 · 실행 목록

- shared DB: `rrfeval_56b1a4d1` (Task 11 Step 1 preflight 로 생성, 재사용)
- `(doc, method, path, chunk_id)` sorted SHA-256:
  `7b794a65eca626f7428134cafc2a841e89e2e9063166f4480901d72fe805f20a`
- query SHA-256: `6eb897d24d681d1389963007a184ded043d3ae914cf862f6ffd8aba7f75838d8`
- corpus content_sha256: stripe
  `3653ad45bbec54fcbe461c541c908355b715018bdf455a0e11b27bedb2cbdee5`,
  github `80850db290cde4eb487e0efb587cf27f305e77b6bef96933ed8a09b5169d5b1d`
- fixture commit: `29d4534e63c784510c2626ec327a0f307cbc401a`

| 실행 | mode | strategy | split | field | variants | 로그 |
|---|---|---|---|---|---|---|
| A | eval | rrf | all | text | OFF | `scratchpad/t11_eval_text_off.log` |
| B | eval | rrf | all | text | ON | `scratchpad/t11_eval_text_on.log` |
| C | eval | rrf | all | structured | OFF | `scratchpad/t11_eval_structured_off.log` |
| D | eval | rrf | all | structured | ON | `scratchpad/t11_eval_structured_on.log` |
| E | determinism | — | all | structured | — | `scratchpad/t12_determinism.log` |
| F1~F4 | eval | fallback | all | text/structured × OFF/ON | | `scratchpad/t12_fb_*.log` |
| H1~H4 | eval | rrf | holdout | text/structured × OFF/ON | | `scratchpad/t12_hold_*.log` |

모든 실행이 위 세 지문(인덱스 SHA · query SHA · fixture commit)을 동일하게 출력했다.

## HARD 판정표 (69번 §7.1)

| 항목 | 결과 | 근거 |
|---|---|---|
| 프리즈 무결성 | **PASS** | 실행 query SHA-256 = `gate_manifest_v1.json` `query_sha256`, corpus stripe/github SHA-256 = manifest `corpus_sha256`, split 수(scored 120 / gate 96 / holdout 24) = manifest `counts`. 러너 자동 검증 오류 0. |
| 실행 동등성 | **PASS** | 네 실행(A~D)과 보조 실행(E, F, H) 모두 fixture commit `29d4534` · DB `rrfeval_56b1a4d1` · 인덱스 지문 `7b794a65…20a` 동일. |
| fallback control | **N/A (architect 승인 2026-08-28)** | 이 트랙은 lexical arm 자체를 바꾸므로 "candidate 는 lexical arm 무변경" 전제가 성립하지 않는다. 아래 별도 절 참조. |
| C1 exact/direct control | **PASS** | C1(g025~g036) 12건 중 baseline hit → candidate miss 전환 0건 (OFF·ON). OFF: g035 미검출→1 획득. ON: g030 미검출→7, g035 미검출→1 획득. |
| category 회귀 | **PASS** | C1~C7 각 카테고리 R@10 hit 순감소 0건(≤1), MRR 하락 0건(≤0.02). 개별 hit→miss 2건(C3 g068, C7 g115)은 같은 카테고리 내 신규 검출(C3 g007·g065, C7 g110)로 상쇄되어 순증감 0. 상세 아래 표. |
| C6 all-of | **PASS** | OFF coverage 0.583→0.583, complete 41.7%→41.7%. ON coverage 0.667→0.667, complete 50.0%→50.0%. 모두 baseline 이상. |
| route pair | **PASS** | §3.4 산식, cap 11. OFF·ON 각각 gate 10/10 · holdout 2/2 · 전체 12/12 non-regression. 상세 아래 표. |
| empty result | **PASS** | empty_result_rate: A·B·C·D 모두 0/120. baseline 대비 증가 없음. |
| sealed holdout | **PASS** | holdout split(n=24). OFF R@10 75%→75%(≥), MRR 0.414→0.549(하락 0 ≤ 0.01). ON R@10 88%→88%(≥), MRR 0.491→0.627(하락 0). |
| 추가 불변식 (78번 §8.3) | **PASS (4/4)** | 아래 별도 절 참조. |

**집계 결과: 8 PASS / 1 N/A. 적용 가능한 HARD 8/8 PASS 확정.** fallback control 은
아래 사유로 이 트랙에 적용 불가(N/A) — architect 승인(2026-08-28). aggregate
지표(EFFECTIVENESS)는 이 8개 항목 통과를 전제로 아래에 기록만 한다.

### fallback control — N/A 사유 (architect 승인 2026-08-28)

69번 §6.2 의 "fallback control" 은 fallback 이 **candidate 수정 대상이 아니라는
전제**의 롤백 컨트롤이다(route-family rerank 등 lexical arm 무변경 계열). 그 전제에서만
`--strategy fallback`(키워드 우선, 0건일 때만 벡터 — `endpoint_candidate_search.py`
§`_search_fallback`)의 per-query 순위가 baseline/candidate 간 완전히 같다.

78번 트랙은 lexical arm 자체를 `text_tsv` → 가중 `search_tsv` 로 바꾸므로 전제가
성립하지 않는다. `--strategy fallback` 은 키워드 결과를 그대로 쓰므로 `--lexical-field`
를 직접 탄다. F1~F4 대조에서 text↔structured per-query capped rank 가 다수 상이한 것
(예: OFF g007 미검출→1, g033 미검출→1, g036 미검출→2, g081 미검출→2, g117 4→미검출)은
회귀가 아니라 이 변경의 **의도된 효과**다. 문자 그대로 FAIL 로 채점하지 않는다.

**rollback 증거** — N/A 로 두어도 롤백 안전성은 별도 확보된다:

- `app/core/config.py:51` `search_lexical_field` 기본값 `"text"`.
- `app/services/search/keyword_search.py:42`
  `self._lexical_field = "structured" if lexical_field == "structured" else "text"`
  — `"structured"` 정확히 일치할 때만 가중 `search_tsv` 경로, 기본값·미인식 값은
  전부 기존 `text_tsv` 로 degrade. baseline(`--lexical-field text`) 실행이 곧
  현행 운영 SQL 경로 그대로임을 이 분기가 보장한다.
- 즉 `DOCS_MCP_SEARCH_LEXICAL_FIELD` 미설정/오타 시 색인 시점 구조 신호가 붙어
  있어도 검색은 `text_tsv` 만 조회 — 배포 후 즉시 무중단 롤백 가능.

`--strategy rrf`(운영 기본 전략)에서 route pair·category·holdout HARD 전항 통과.

### category 회귀 상세 (rrf, R@10 hit 수 · MRR)

hit = accepted 순위 1~10. MRR 은 러너 `카테고리별 분해` 표에서 전사.

| 카테고리 | n | OFF text hit | OFF struct hit | OFF ΔMRR | ON text hit | ON struct hit | ON ΔMRR |
|---|--:|--:|--:|--:|--:|--:|--:|
| C1-직접키워드 | 12 | 10 | 11 | 0.602→0.727 | 10 | 12 | 0.602→0.741 |
| C2-한글패러프레이즈 | 24 | 9 | 9 | 0.118→0.118 | 20 | 20 | 0.282→0.282 |
| C3-영문의역 | 18 | 10 | 11 | 0.339→0.343 | 10 | 11 | 0.339→0.343 |
| C4-흔한토큰범람 | 12 | 6 | 8 | 0.239→0.446 | 7 | 9 | 0.257→0.463 |
| C5-decoy구분 | 24 | 14 | 15 | 0.323→0.418 | 16 | 17 | 0.331→0.426 |
| C6-다개념 | 12 | 9 | 9 | 0.634→0.676 | 10 | 10 | 0.602→0.644 |
| C7-대형엔드포인트세부 | 18 | 14 | 14 | 0.328→0.381 | 15 | 15 | 0.349→0.403 |

모든 카테고리에서 R@10 hit 순감소 ≤ 1, MRR 하락 없음. 개별 미검출 전환:
C3 g068(`POST …/dispatches`) OFF·ON 1→미검출, C7 g115(`GET …/issues`) OFF·ON 10→미검출.
각각 같은 카테고리에서 C3 g007 미검출→3·g065 미검출→2·g007…, C7 g110 미검출→4 로
신규 검출이 나 순증감은 0이다. 69번 §7.1 은 순감소(net) 기준이며 paired child
regression 만 별도 금지 — route pair 표에서 child 악화 0건이다.

### route pair 상세 (69번 §3.4, cap 11)

`delta(q) = r_candidate(q) − r_baseline(q)` (baseline=text, candidate=structured).
`pair_nonregression = [delta(root) ≤ 0 이고 delta(child) ≤ 0]`.
`pair_effective = pair_nonregression 이고 [delta(root) < 0 또는 delta(child) < 0]`.

#### variants OFF (A vs C)

| pair | split | root text→struct (Δ) | child text→struct (Δ) | non-reg | effective |
|---|---|---|---|:--:|:--:|
| p01 | gate | 11→11 (0) | 4→4 (0) | O | · |
| p02 | holdout | 4→4 (0) | 11→11 (0) | O | · |
| p03 | gate | 10→2 (−8) | 2→1 (−1) | O | O |
| p04 | gate | 11→3 (−8) | 11→11 (0) | O | O |
| p05 | gate | 3→3 (0) | 11→11 (0) | O | · |
| p06 | gate | 11→11 (0) | 11→11 (0) | O | · |
| p07 | gate | 11→11 (0) | 11→11 (0) | O | · |
| p08 | gate | 11→11 (0) | 11→11 (0) | O | · |
| p09 | gate | 3→2 (−1) | 1→1 (0) | O | O |
| p10 | gate | 1→1 (0) | 11→2 (−9) | O | O |
| p11 | gate | 2→2 (0) | 1→1 (0) | O | · |
| p12 | holdout | 3→1 (−2) | 2→1 (−1) | O | O |

- gate 10쌍: non-regression 10/10, effective 4 (p03·p04·p09·p10) ≥ 2 ✓
- holdout 2쌍: non-regression 2/2, effective 1 (p12) ≥ 1 ✓
- 전체 12쌍: non-regression 12/12, effective 5 ≥ 3 ✓

#### variants ON (B vs D)

| pair | split | root text→struct (Δ) | child text→struct (Δ) | non-reg | effective |
|---|---|---|---|:--:|:--:|
| p01 | gate | 7→7 (0) | 6→6 (0) | O | · |
| p02 | holdout | 11→11 (0) | 6→6 (0) | O | · |
| p03 | gate | 10→2 (−8) | 2→1 (−1) | O | O |
| p04 | gate | 11→3 (−8) | 11→11 (0) | O | O |
| p05 | gate | 7→7 (0) | 11→11 (0) | O | · |
| p06 | gate | 11→11 (0) | 4→4 (0) | O | · |
| p07 | gate | 11→11 (0) | 11→11 (0) | O | · |
| p08 | gate | 11→11 (0) | 11→11 (0) | O | · |
| p09 | gate | 3→2 (−1) | 1→1 (0) | O | O |
| p10 | gate | 1→1 (0) | 11→2 (−9) | O | O |
| p11 | gate | 2→2 (0) | 1→1 (0) | O | · |
| p12 | holdout | 3→1 (−2) | 2→1 (−1) | O | O |

- gate 10쌍: non-regression 10/10, effective 4 ≥ 2 ✓
- holdout 2쌍: non-regression 2/2, effective 1 ≥ 1 ✓
- 전체 12쌍: non-regression 12/12, effective 5 ≥ 3 ✓

child 순위가 1칸이라도 악화된 pair 0건 (OFF·ON).

### 추가 불변식 (78번 §8.3)

| 불변식 | 결과 | 근거 |
|---|---|---|
| lexeme 상위집합 | PASS | `tests/unit/test_chunk_repository.py::test_search_tsv_lexemes_are_superset_of_text_tsv` — Task 1~10 단위 스위트(1074건) 전량 통과. |
| 벡터 arm 불변 | PASS | Task 11 Step 3: `md5(string_agg(id||':'||text))` 백필 전후 `5dc075e98a930aa02fc576f7e5c31466` 완전 일치, endpoint `embedding IS NULL` 0건. |
| 파생 결정성 | PASS | 실행 E(`--mode determinism`): OFF 2회 per-query capped rank 완전 동일, variant 없는 질의 OFF/ON 동일. `test_backfill_is_idempotent` 통과. |
| 문서 검색 무변경 | PASS | Task 11 Step 3: `chunk_type<>'endpoint' AND search_tsv IS NOT NULL` 0건. `lexical_field` 분기는 `endpoint_candidate_search` 에만 있고 section/document 검색 경로 코드는 무변경. |

## EFFECTIVENESS (69번 §7.2) — 기록만, 승급 판단 아님

전체 scored 120건 `rrf`. baseline=text, candidate=structured.

| 항목 | 기준 | OFF | ON | 비고 |
|---|---|---|---|---|
| Recall@10 Δ | ≥ +3.0%p | 60%→64% (+4%p) | 73%→78% (+5%p) | 충족 |
| MRR | 각 baseline 이상, 하나는 ≥ +0.02 | 0.336→0.401 (+0.065) | 0.372→0.438 (+0.066) | 충족 |
| nDCG@10 | 각 baseline 이상 | 0.398→0.458 | 0.457→0.520 | 충족 |
| targeted C2+C3+C5 top-10 순증가 | ≥ 3건 | +2건 (C3 +1, C5 +1) | +2건 (C3 +1, C5 +1) | **미달** (순감소는 없음) |
| 한국어 58건 ON top-10 순증가 | ≥ 2건 | — | 41→42 (+1건) | **미달** |
| route pair effective (전체 12쌍) | ≥ 3쌍 | 5쌍 | 5쌍 | 충족 |
| holdout 방향성 | win > loss, win ≥ 1 | p12 win, loss 0 | p12 win, loss 0 | 충족 |

`answer_miss@10`(= 1 − Recall@10): OFF 40.0%→35.8%, ON 26.7%→21.7%.

EFFECTIVENESS 는 정보 기록이며 v1 승급 판단에 쓰지 않는다(verdict 74 §6.2). 승급
판단은 v2 프리즈 이후 architect 설계로 별도 진행한다.

## 산출물

- Task 11 재사용 로그: `scratchpad/t11_preflight.log`,
  `scratchpad/t11_eval_{text,structured}_{off,on}.log`
- Task 12 추가 로그: `scratchpad/t12_determinism.log`,
  `scratchpad/t12_fb_{text,structured}_{off,on}.log`,
  `scratchpad/t12_hold_{text,structured}_{off,on}.log`
- 인덱스 정리: 계획 79 Task 12 Step 5 완료 —
  `run_corpus_eval.py --mode cleanup --db-url …/rrfeval_56b1a4d1` 로 shared DB
  `rrfeval_56b1a4d1` drop (2026-08-28).
