# structured lexical v2 — sealed holdout pre-open gate (gate96)

design: `docs/architect-review/80_structured_lexical_v2_sealed_holdout_freeze_design.md`
(rules_git_sha `cef9214`). 판정 기준은 §8.1, route pair 산식은 §3.4, 구조 후보 불변식은
§8.3. HARD 전항 PASS 전에는 holdout(§8.2)을 열지 않는다. EFFECTIVENESS 는 HARD 전항
PASS 일 때만 판정한다.

작업: developer. 실행일 2026-08-29. 실제 실행값만 기재 — 추정·손계산 없음.
분석 스크립트 `scratchpad/v2g_analyze.py` (4개 eval 로그의 per-query rrf 순위·요약
지표만 파싱). 원본 로그 `scratchpad/v2g_{A_text_off,B_text_on,C_struct_off,D_struct_on,
E_determinism}.log`.

---

## 1. 실행 identity

하나의 신규 shared index 를 만들고(`run_corpus_eval.py --mode preflight`,
`queries_gate_v2.json`) 4개 eval 실행이 모두 재사용했다. 등록·재색인·drop 없음, read-only.

| 항목 | 값 |
|---|---|
| shared DB | `rrfeval_1b75828f` (유지 — holdout 판단용, cleanup 미실행) |
| index fingerprint `(doc, method, path, chunk_id)` sorted SHA-256 | `126210e9bc264e7a511cc2b7847407ca605049c30ea8a4f6904c809709b07d33` |
| query SHA-256 (raw file bytes) | `a325583905a624c4e8293b7abff49e65741bc4aa6d0e09e48d5ed74bfa0346e5` |
| split SHA-256 (manifest) | `a53c1ab7eb7ce21b2afc4ea8cc0b28ae6809a236cc9d08061d5f42b5448b9a9a` |
| corpus SHA-256 stripe | `3653ad45bbec54fcbe461c541c908355b715018bdf455a0e11b27bedb2cbdee5` |
| corpus SHA-256 github | `80850db290cde4eb487e0efb587cf27f305e77b6bef96933ed8a09b5169d5b1d` |
| rules_git_sha | `cef9214` |
| fixture commit | `d5a79c96558b8d68accb58146614d2616e15ab4d` (2026-08-29 11:28:13 +0900) |
| product source | `468ffafad253b112f59917b9b9c703786078be83` |
| baseline lexical field | `text` (`chunk.text_tsv`) |
| candidate lexical field | `structured` (weighted `chunk.search_tsv`) |
| endpoint chunks | github 1220, stripe 589 (합 1809) |
| schema chunks | 2409 |
| section chunks | 0 |
| embedding model | `intfloat/multilingual-e5-small` (dim 384, is_semantic) |

4개 eval 실행 + determinism 실행 5개 로그 전부 위 index fingerprint · query SHA-256 ·
fixture commit 이 동일하다. A/B = `lexical field: text`, C/D/E = `lexical field: structured`.

| run | field | variants | strategy |
|---|---|---|---|
| A | text | OFF | both (fallback+rrf) |
| B | text | ON | both |
| C | structured | OFF | both |
| D | structured | ON | both |
| E | structured | — | determinism (§4.4) |

field 외 evaluator·source·corpus·row·text·embedding·DB 조건은 4실행 동일. §8.1 route
pair·category·C1·C6 판정은 운영 전략인 `rrf` 기준(v1 선례).

---

## 2. §8.3 구조 후보 전용 HARD 불변식

`scratchpad/v2g_integrity.py` 로 백필 전 / 백필 1회 후 / 백필 2회 후 3회 스냅샷.
`app.scripts.backfill_endpoint_structure` 는 실행마다 endpoint chunk 1809건을 갱신했다.

| 스냅샷 | vec md5 (id:text:embedding, 전체 chunk) | struct md5 (endpoint leaf/intent/context) | search_tsv md5 (endpoint) | text_tsv md5 (endpoint) |
|---|---|---|---|---|
| 백필 전 | `f0e8e87184decd182af4e1bba1d35766` | `44cf578f81cfb05894d4f9610c6b1916` | `d91a45972d9daf575d472ae27d5e127a` | `8e43ec78041c33b7dcf55d5d85fa765e` |
| 백필 후 1 | 동일 | 동일 | 동일 | 동일 |
| 백필 후 2 | 동일 | 동일 | 동일 | 동일 |

부수 카운트: `lex_superset_violations` = 0, `endpoint_embedding_null` = 0,
`non_endpoint_search_tsv` = 0 (3회 모두).

| 불변식 | 판정 | 근거 |
|---|---|---|
| lexeme 상위집합 | **PASS** | endpoint 1809 전부 `tsvector_to_array(strip(text_tsv)) <@ tsvector_to_array(strip(search_tsv))`, 위반 0 |
| 벡터 arm 불변 | **PASS** | `chunk.text`·`chunk.embedding` 정렬 md5 `f0e8e871…` 백필 전/후1/후2 동일; endpoint embedding null 0. 백필은 `leaf_text/intent_text/context_text` 만 갱신 |
| 파생 결정성 | **PASS** | `search_tsv` 는 `GENERATED ALWAYS` — 백필 산출 md5 `d91a4597…` 3회 동일; determinism 실행 E: OFF 2회 per-query capped rank 완전 동일, variants 없는 질의 OFF/ON 동일 |
| 문서 검색 무변경 | **PASS** | corpus 에 `chunk_type='section'` 0개 (OpenAPI 스펙만); non-endpoint `search_tsv` 0. section rank 비교 대상 없음 — 공허 통과 |
| exact control | **PASS** | `_search_exact` 는 `list_by_method_path` / `list_by_operation_id` 경로, tsvector 미사용 → `lexical_field` 불변 (코드 검토 `endpoint_candidate_search.py:162`) |

verdict 69 의 fallback-control 동일성은 §8.3 단서대로 이 후보에 적용하지 않는다 — fallback
자체가 lexical field 를 읽으므로 text↔structured 결과 차이는 78번 설계·v1 실행 전 확정된
component 경계다. `text` 즉시 복귀 가능성은 `keyword_search.py:42`
(`"structured" if lexical_field == "structured" else "text"`, 미인식값 → text degrade) +
단위 테스트 14/14 PASS 로 유지.

---

## 3. 지표 요약 (rrf, gate96)

| run | field | variants | Recall@10 | MRR | nDCG@10 | answer_miss@10 | empty_result |
|---|---|---|---|---|---|---|---|
| A | text | OFF | 67% | 0.427 | 0.484 | 32/96 (33.3%) | 0/96 |
| B | text | ON | 80% | 0.416 | 0.506 | 19/96 (19.8%) | 0/96 |
| C | structured | OFF | 68% | 0.473 | 0.522 | 31/96 (32.3%) | 0/96 |
| D | structured | ON | 85% | 0.479 | 0.568 | 14/96 (14.6%) | 0/96 |

짝 비교: OFF = C − A, ON = D − B.

---

## 4. §8.1 HARD 판정

| 항목 | 판정 | 근거 |
|---|---|---|
| 프리즈 무결성 | **PASS** | §1 identity 의 query/split/corpus/rules/candidate SHA 전부 일치; v2 novelty 정적 검증 오류 0 (단위 테스트 14/14); ruff net-new 0 |
| 실행 동등성 | **PASS** | 4 실행 fixture commit `d5a79c9` · DB `rrfeval_1b75828f` · index fingerprint `126210e9…` 동일 |
| C1 exact/direct | **PASS** | candidate top-10 hit loss 0. OFF 순증감 0. ON +2/−0 (v2g009 미검출→10, v2g010 미검출→9 신규) |
| category 회귀 | **PASS** | C1~C7 각 R@10 hit 순감 ≤ 1 (최대 C3 순감 1: hit +1/−2, 동일 카테고리 내 상쇄 후 1). rrf MRR 하락 ≤ 0.02 (최소 C7 OFF 0.530→0.510 = −0.020, 경계 이내). 상세 §4.1 |
| C6 all-of | **PASS** | 집계 coverage@10·complete@10 모두 candidate ≥ baseline. OFF 0.550→0.700 / 30.0%→50.0%, ON 0.700→0.850 / 50.0%→70.0%. 개별 v2g100 하락(OFF·ON rrf cov 1.00→0.50) 관측되나 §8.1 집계 기준 충족 |
| route pair | **FAIL** | §3.4 non-regression: OFF 8/10, ON 7/10. 요구 gate 10/10. 상세 §4.2 |
| empty result | **PASS** | OFF 0→0, ON 0→0. baseline 대비 증가 없음 |
| 구조 후보 불변식 | **PASS** | §8.3 5/5 (위 §2) |

### 4.1 category 회귀 상세 (rrf, R@10 hit 수 · MRR)

| 카테고리 | n | OFF hitΔ (+/−) | OFF 순감 | OFF MRR A→C | ON hitΔ (+/−) | ON 순감 | ON MRR B→D |
|---|---|---|---|---|---|---|---|
| C1-직접키워드 | 10 | +0/−0 | 0 | 0.824→0.836 (+0.012) | +2/−0 | 0 | 0.625→0.659 (+0.034) |
| C2-한글패러프레이즈 | 19 | +0/−0 | 0 | 0.368→0.368 (+0.000) | +0/−0 | 0 | 0.414→0.415 (+0.001) |
| C3-영문의역 | 14 | +1/−2 | 1 | 0.324→0.491 (+0.167) | +1/−2 | 1 | 0.324→0.491 (+0.167) |
| C4-흔한토큰범람 | 10 | +1/−1 | 0 | 0.052→0.095 (+0.043) | +3/−1 | 0 | 0.052→0.140 (+0.088) |
| C5-decoy구분 | 19 | +0/−0 | 0 | 0.476→0.553 (+0.077) | +0/−0 | 0 | 0.539→0.617 (+0.078) |
| C6-다개념 | 10 | +1/−0 | 0 | 0.428→0.454 (+0.026) | +1/−0 | 0 | 0.347→0.422 (+0.075) |
| C7-대형엔드포인트세부 | 14 | +1/−0 | 0 | 0.530→0.510 (−0.020) | +1/−0 | 0 | 0.501→0.522 (+0.021) |

각 카테고리 R@10 hit 순감 ≤ 1, MRR 하락 ≤ 0.02 (C7 OFF −0.020 은 "하락 최대 0.02" 경계
이내). → category 회귀 PASS.

### 4.2 route pair 상세 (§3.4, cap = top_k+1 = 11, Δ = r_structured − r_text)

`pair_nonregression = [Δ(root) ≤ 0 이고 Δ(child) ≤ 0]`.
`pair_effective = pair_nonregression 이고 [Δ(root) < 0 또는 Δ(child) < 0]`.

**OFF (C − A)**

| pair | Δroot | Δchild | non-reg | effective |
|---|---|---|---|---|
| v2p01 | 0 | 0 | ✓ | |
| v2p02 | 0 | 0 | ✓ | |
| v2p03 | −2 | **+9** | ✗ | |
| v2p04 | −2 | −9 | ✓ | ✓ |
| v2p05 | 0 | 0 | ✓ | |
| v2p06 | 0 | 0 | ✓ | |
| v2p07 | **+3** | 0 | ✗ | |
| v2p10 | 0 | 0 | ✓ | |
| v2p11 | 0 | −5 | ✓ | ✓ |
| v2p12 | −1 | 0 | ✓ | ✓ |

non-regression 8/10, effective 3.

**ON (D − B)**

| pair | Δroot | Δchild | non-reg | effective |
|---|---|---|---|---|
| v2p01 | **+1** | −3 | ✗ | |
| v2p02 | 0 | 0 | ✓ | |
| v2p03 | −2 | **+9** | ✗ | |
| v2p04 | −2 | −9 | ✓ | ✓ |
| v2p05 | 0 | 0 | ✓ | |
| v2p06 | 0 | 0 | ✓ | |
| v2p07 | **+3** | 0 | ✗ | |
| v2p10 | 0 | −1 | ✓ | ✓ |
| v2p11 | 0 | −5 | ✓ | ✓ |
| v2p12 | −1 | 0 | ✓ | ✓ |

non-regression 7/10, effective 4.

회귀 근거 (raw r_s, text→structured):

- **v2p03 child** `GET /v1/payment_links/{payment_link}/line_items`: OFF 1→10, ON 1→10
- **v2p07 root** `GET /v1/tax/calculations/{calculation}`: OFF 1→4, ON 1→4
- **v2p01 root** `GET /v1/quotes/{quote}`: ON 5→6

gate 10/10 root·child non-regression 요건 미충족. → route pair **FAIL**.

---

## 5. §8.1 EFFECTIVENESS — 판정 대상 아님 (기록만)

HARD `route pair` FAIL 이므로 design 80 §8.1·§9-3 에 따라 EFFECTIVENESS 는 판정하지
않는다. 아래는 참고용 실행값이다.

| 항목 | 기준 | 실행값 | 기준 대비 |
|---|---|---|---|
| OFF Recall@10 | 순증 ≥ 3건, ≥ +3.0%p | 순증 +4/−3 net +1, 67%→68% (+1%p) | 미달 |
| ON Recall@10 | 순증 ≥ 3건, ≥ +3.0%p | 순증 +8/−3 net +5, 80%→85% (+5%p) | 충족 |
| OFF/ON MRR | 각 baseline 이상, 하나 ≥ +0.02 | OFF 0.427→0.473 (+0.046), ON 0.416→0.479 (+0.063) | 충족 |
| OFF/ON nDCG@10 | 각 baseline 이상 | OFF 0.484→0.522 (+0.038), ON 0.506→0.568 (+0.062) | 충족 |
| targeted C2+C3+C5 | OFF 또는 ON top-10 순증 ≥ 3, 다른 조건 순감 없음 | OFF net −1 (+1/−2), ON net −1 (+1/−2) | 미달 (순감 발생) |
| 한국어 gate 47 ON | top-10 hit 순증 ≥ 2 | 순증 +4/−0 net +4 | 충족 |
| route pair effective | gate 10쌍 중 ≥ 2쌍 | OFF 3쌍, ON 4쌍 | 충족 (단 non-regression 게이트 미통과) |

---

## 6. 종합 판정 — **FAIL**

- HARD `route pair` 미충족: OFF non-regression 8/10, ON 7/10 (< 요구 10/10).
  v2p03 child 이 text→structured 로 rank 1 → 10 으로 악화(OFF·ON 공통)한 것이 주 원인,
  v2p07 root (1→4), v2p01 root ON (5→6) 이 추가.
- design 80 §9-3: 하나라도 FAIL 이면 **holdout 미개봉, candidate 는 dark 유지**.
- 임계값·alias·weight 조정 재시도 없음 (§9-3, lead 지시 6). 같은 candidate 재시험 없음.
- 공유 인덱스 `rrfeval_1b75828f` 는 holdout 판단을 위해 유지 (`--mode cleanup` 미실행).
  `--split holdout` 은 실행하지 않았다.
