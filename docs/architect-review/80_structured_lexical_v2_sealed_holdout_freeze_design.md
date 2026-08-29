# 80. structured lexical v2 sealed holdout 프리즈 설계

- 대상 candidate: main `468ffafad253b112f59917b9b9c703786078be83`
  (`weighted-tsvector-track` dark merge)
- 비교 축: `DOCS_MCP_SEARCH_LEXICAL_FIELD=text`(baseline) vs
  `DOCS_MCP_SEARCH_LEXICAL_FIELD=structured`(candidate)
- 선행 증거: p02 개발 게이트 PASS, v1 exposed regression의 적용 가능한 HARD 8/8 PASS
- 상태: **프리즈 설계안 — lead 승인 대기. 이 문서로 v2 파일을 생성하거나 검색을 실행하지 않는다.**

## 1. 판정 요약

1. v2는 `queries_gate_v2.json` 124건(scored 120 + diagnostic 4)을 새로 저작한다.
   v1·legacy의 query, accepted label, variant, route pair를 한 건도 재사용하지 않는다.
2. verdict 69의 category/domain/language quota, gate 96/holdout 24 split, C6 all-of,
   root/child pair guard를 그대로 유지한다. 분포를 candidate 결과에 맞춰 바꾸지 않는다.
3. query 원문 파일 SHA, scored split mapping SHA, 두 corpus 원문 SHA, 평가 규칙 commit,
   candidate source SHA를 `gate_manifest_v2.json`에 함께 고정한다.
4. holdout 개봉 전 gate96이 이 문서의 HARD와 EFFECTIVENESS pre-open 조건을 모두
   통과해야 한다. 최종 승급은 scored 120 전체의 HARD와 EFFECTIVENESS 전항 PASS일 때만
   가능하다.
5. EFFECTIVENESS 최소치는 verdict 69 §7.2를 낮추지 않는다. v1에서 관측한
   `targeted C2+C3+C5 +2`, `한국어58 +1`에 맞춰 기준을 낮추거나 표본을 고르는 행위를
   금지한다.
6. PASS 시 승급 행위는 candidate 코드를 다시 고치는 것이 아니라 배포 환경의
   `DOCS_MCP_SEARCH_LEXICAL_FIELD=structured` 활성화다. 하나라도 FAIL이면 기본
   `text` dark 상태를 유지한다.

## 2. 프리즈 범위와 비범위

### 2.1 프리즈하는 것

| 대상 | 프리즈 값/방법 |
|---|---|
| query/label/variant/pair | `tests/fixtures/corpus_eval/queries_gate_v2.json` raw bytes |
| split | 위 파일의 scored `id -> gate|holdout` mapping을 별도 SHA-256으로 기록 |
| manifest | `tests/fixtures/corpus_eval/gate_manifest_v2.json` |
| query SHA | query 파일 raw bytes의 SHA-256 |
| corpus SHA | Stripe/GitHub fixture raw bytes의 SHA-256 |
| 판정 규칙 | 이 문서가 들어간 git commit SHA와 문서 경로 |
| candidate | product source `468ffafad253b112f59917b9b9c703786078be83`, field `structured` |
| baseline | 같은 source·인덱스·evaluator, field만 `text` |

현재 고정할 corpus SHA는 다음과 같다. 프리즈 시 raw bytes에서 다시 계산해 같은지
검증하며, 다르면 v2를 프리즈하지 않는다.

| corpus | SHA-256 |
|---|---|
| `stripe_spec3.json` | `3653ad45bbec54fcbe461c541c908355b715018bdf455a0e11b27bedb2cbdee5` |
| `github_api.json` | `80850db290cde4eb487e0efb587cf27f305e77b6bef96933ed8a09b5169d5b1d` |

### 2.2 이번 설계의 비범위

- 실제 v2 query·label·variant·split 저작과 파일 생성
- `OPERATION_ALIASES`, `_STRUCTURED_RANK_WEIGHTS`, `ts_rank` 함수·RRF 파라미터 변경
- path specificity/route-family rerank, 한글 alias, 구조 텍스트 임베딩 반영
- v1 query·label·variant·pair 수정 또는 v1 결과의 승급 근거 사용
- v2 결과를 본 뒤 accepted 완화, split 이동, 기준 완화

위 항목이 필요하면 현재 v2 candidate를 반려하고 새 설계·새 dataset version으로 시작한다.

## 3. v2 신규성 계약

v2의 “전량 신규 endpoint/query pair”는 단순히 ID만 바꾸는 것이 아니다. 정적 검증기가
legacy `queries.json`과 v1 `queries_gate_v1.json`을 함께 읽어 다음을 모두 검사한다.

| 축 | v2 계약 |
|---|---|
| ID | `v2g001`~`v2g124`; 기존 ID와 불일치 |
| query | Unicode NFKC, trim, whitespace collapse, casefold 정규화 값이 legacy/v1과 불일치 |
| variant | 같은 정규화로 legacy/v1 query·variant 및 v2의 다른 query·variant와 불일치 |
| accepted label | v2의 모든 `(doc, method, path)`가 v1 scored/diagnostic의 accepted tuple에 없음 |
| endpoint/query pair | 정규화 query와 정렬된 accepted tuple 집합의 조합이 legacy/v1에 없음 |
| route pair | `v2p01`~`v2p12`; v1 pair ID·두 accepted endpoint·route family를 재사용하지 않음 |
| C6 | 두 accepted endpoint 모두 v1 accepted와 불일치하며 v2 내부에서도 다른 C6와 중복 없음 |

같은 endpoint에 표현만 바꾼 query를 붙이면 label을 재사용한 것이므로 허용하지 않는다.
v2 내부에서도 scored 레코드끼리 accepted tuple을 공유하지 않는다. `answer_mode="all"`의
두 endpoint가 다른 레코드에 다시 나오는 것도 금지한다. 이렇게 해야 v1에서 이미 확인한
endpoint의 동의어만 다시 묻는 우회 재사용을 막을 수 있다.

질의 작성자는 corpus의 endpoint 명세만 보고 사용자 의도를 먼저 작성한 뒤 label을 붙인다.
v1 실패 목록, v1 arm trace, candidate 순위는 표본 선택이나 문구 작성에 사용하지 않는다.

## 4. 레코드·분포 계약 (verdict 69 승계)

스키마와 라벨 규칙은 verdict 69 §2~§4를 유지한다. `id`와 파일 version만 v2로 바꾼다.
scored 120건, diagnostic 4건이며 diagnostic은 headline과 판정에서 제외한다.

### 4.1 category/domain quota

| category | 합계 | Stripe | GitHub | gate | holdout |
|---|---:|---:|---:|---:|---:|
| C1 직접키워드 | 12 | 6 | 6 | 10 | 2 |
| C2 한국어 패러프레이즈 | 24 | 12 | 12 | 19 | 5 |
| C3 영어 의역 | 18 | 9 | 9 | 14 | 4 |
| C4 흔한 토큰 범람 | 12 | 6 | 6 | 10 | 2 |
| C5 decoy·specificity | 24 | 12 | 12 | 19 | 5 |
| C6 다개념 | 12 | 6 | 6 | 10 | 2 |
| C7 endpoint 세부 | 18 | 9 | 9 | 14 | 4 |
| **합계** | **120** | **60** | **60** | **96** | **24** |

각 domain은 `ko 29 / en 29 / code 2`, 전체는 `ko 58 / en 58 / code 4`다.
holdout은 Stripe/GitHub `12/12`, `ko/en/code = 11/11/2`; 따라서 gate는
Stripe/GitHub `48/48`, `ko/en/code = 47/47/2`다. 한국어 scored 58건은 자연스러운
영어 variant 정확히 1개, 영어·code는 variant가 없어야 한다. diagnostic 4건은 verdict
69와 같이 Stripe/GitHub `2/2`, 한국어/영어 `2/2`로 둔다.

### 4.2 C6와 root/child guard

- C6 12건은 `answer_mode="all"`, accepted 정확히 2건이다. 나머지는 기본 `any`다.
- route pair는 C2 2쌍, C3 2쌍, C5 8쌍으로 총 12쌍/24질의다.
- domain은 Stripe/GitHub 6쌍씩, language는 한국어/영어 6쌍씩이다.
- gate 10쌍은 domain 5/5·language 5/5, holdout 2쌍은 domain 1/1·language 1/1이다.
- 한 pair의 root/child는 같은 domain·language·query style·split이고 accepted는 각각
  1건이다. root path는 child path의 세그먼트 경계 prefix이며 서로 다른 endpoint다.
- root/child 어느 하나도 candidate에서 baseline보다 1칸이라도 나빠질 수 없다.
  미검출은 11위로 cap하고 verdict 69 §3.4의 `delta` 산식을 그대로 쓴다.

## 5. split과 sealed holdout 계약

1. 124건의 의미 검토가 끝날 때까지 scored 레코드의 split은 확정하지 않는다.
2. query 문구·accepted·variant·pair가 완성된 뒤 §4 quota로 gate/holdout을 층화 배정한다.
   pair 두 레코드는 하나의 block으로 이동한다.
3. split 배정 뒤 query 내용을 다시 고치지 않는다. 오류가 있으면 split만 옮겨 덮지 않고
   초안 상태로 되돌려 의미 검토와 split 배정을 다시 한다.
4. 최종 scored mapping을 ID 오름차순의 UTF-8
   `<id><TAB><split><LF>` 120줄로 직렬화해 `split_sha256`을 계산한다. query raw SHA도
   split 필드를 포함하므로 두 지문이 함께 바뀐다.
5. holdout query가 저장소에 있다는 사실은 숨김(secrecy)을 뜻하지 않는다. sealed의 계약은
   **프리즈 뒤 candidate 결과를 최초 1회만 열고, 그 결과로 candidate·label·split·기준을
   고치지 않는 운영 통제**다.
6. developer는 gate만 실행한다. lead가 §8.1 pre-open PASS와 실행 SHA를 확인한 뒤 동일
   candidate에 대해서만 holdout 실행을 지시한다. raw holdout log는 판정 문서 작성 전까지
   lead가 관리하며 tuning 입력으로 전달하지 않는다.
7. holdout FAIL 뒤 원인 분석은 가능하지만 candidate는 이미 반려된 상태다. 후속 후보는
   v2 holdout을 재사용하지 않고 v3 전량 신규 프리즈로 간다.

## 6. manifest 계약

`gate_manifest_v2.json`은 최소 다음 구조를 가진다.

```json
{
  "schema_version": 2,
  "dataset_version": "v2",
  "status": "frozen",
  "query_file": "queries_gate_v2.json",
  "query_sha256": "...",
  "query_sha256_method": "raw file bytes",
  "split_sha256": "...",
  "split_sha256_method": "scored rows sorted by id: <id><TAB><split><LF>",
  "counts": {"total": 124, "scored": 120, "diagnostic": 4,
             "gate": 96, "holdout": 24},
  "corpus_sha256": {"stripe": "3653...", "github": "8085..."},
  "novelty_against": {
    "legacy_query_sha256": "8f61cb99006e0d07923111fc919aaaa7489b486b0fffca15928efce75355441f",
    "v1_query_sha256": "6eb897d24d681d1389963007a184ded043d3ae914cf862f6ffd8aba7f75838d8"
  },
  "product_source_sha": "468ffafad253b112f59917b9b9c703786078be83",
  "baseline_lexical_field": "text",
  "candidate_lexical_field": "structured",
  "rules": "docs/architect-review/80_structured_lexical_v2_sealed_holdout_freeze_design.md",
  "rules_git_sha": "..."
}
```

manifest 자기 자신의 commit SHA 순환은 만들지 않는다. freeze commit SHA는 commit 생성
뒤 별도 freeze 결과 문서와 lead 승인 기록에 남겨 manifest와 결합한다. query/corpus/rules/
candidate SHA는 프리즈 commit 전에 반드시 실값이다.

현재 러너의 `gate_manifest_v1.json` 하드코딩은 v2 프리즈 때 제거한다. 새 러너를 만들지
않고 기존 러너가 `--queries-file`에 대응하는 manifest를 명시적으로 선택하게 하며, v1/v2
각각 자기 manifest·quota·SHA를 검증한다. v2 novelty 검증도 DB 검색을 시작하기 전 로더
단계에서 수행한다.

## 7. 프리즈 절차

| 단계 | 실행자 | 산출/게이트 |
|---|---|---|
| F0 | lead | §10 승인표 전항 승인, 이 문서 commit SHA 확정 |
| F1 | developer | 검색 미실행 상태로 v2 124건 초안 작성 |
| F2 | developer | 신규성·schema·quota·accepted 실재·pair/C6 자동 검증 PASS |
| F3 | lead | pair 12쌍, C6 12건, diagnostic 4건 전량 + 나머지 scored 층화 20% 의미 검토 |
| F4 | developer | 최종 split 배정, split/query/corpus SHA 계산 |
| F5 | developer | frozen manifest 작성; v2 fixture와 최소 러너 선택 변경만 한 commit 생성 |
| F6 | lead | product diff 없음, rules/candidate/query/split/corpus SHA 재검산 후 freeze 승인 |

F1~F6 동안 baseline과 candidate를 포함한 검색 실행은 금지한다. schema/label 정적 검증과
corpus endpoint 실재 확인만 허용한다. F6 뒤 query, accepted, variant, pair, split, quota,
판정 기준 어느 하나를 고치면 현재 v2 freeze는 폐기하고 새 query SHA로 F1부터 다시 한다.

## 8. 결과를 보기 전에 고정하는 판정 기준

### 8.1 holdout pre-open — gate96

아래 전항 PASS 전에는 holdout을 열지 않는다.

**HARD**

| 항목 | gate PASS |
|---|---|
| 프리즈 무결성 | query/split/corpus/rules/candidate SHA 일치, 정적 검증 오류 0 |
| 실행 동등성 | text/structured × OFF/ON 네 실행의 fixture commit·DB·index fingerprint 동일 |
| C1 exact/direct | candidate top-10 hit loss 0 |
| category 회귀 | C1~C7 각각 R@10 hit 순감소 최대 1건, MRR 하락 최대 0.02 |
| C6 all-of | coverage@10·complete@10 모두 baseline 이상 |
| route pair | gate 10/10 root·child non-regression |
| empty result | OFF/ON 모두 baseline보다 증가하지 않음 |
| 구조 후보 불변식 | §8.3 다섯 항목 전부 PASS |

**EFFECTIVENESS**

| 항목 | gate PASS |
|---|---|
| OFF Recall@10 | baseline 대비 `>= +3.0%p` (96건에서는 순증 최소 3건) |
| ON Recall@10 | baseline 대비 `>= +3.0%p` (96건에서는 순증 최소 3건) |
| OFF/ON MRR | 각각 baseline 이상, 둘 중 하나 `>= +0.02` |
| OFF/ON nDCG@10 | 각각 baseline 이상 |
| targeted C2+C3+C5 | OFF 또는 ON top-10 순증 최소 3건, 다른 조건 순감소 없음 |
| 한국어 gate 47건 ON | top-10 hit 순증 최소 2건 |
| route pair | gate 10쌍 중 effective 최소 2쌍 |

gate 기준에도 v1 미달 항목의 최소치 3건·2건을 그대로 적용한다. 96건으로 분모가 줄었다는
이유로 각각 2건·1건으로 낮추지 않는다.

### 8.2 최종 승급 — scored 120

holdout 개봉 뒤에는 gate+holdout을 합친 scored 120에 verdict 69 §7.1~§7.2를 그대로
적용한다. 다음 전항이 동시에 PASS여야 한다.

**HARD**

| 항목 | 최종 PASS |
|---|---|
| 프리즈·실행 무결성 | §8.1과 동일, holdout 실행도 같은 identity |
| C1 exact/direct | 전체 candidate top-10 hit loss 0 |
| category 회귀 | 각 C1~C7 hit 순감소 최대 1건, MRR 하락 최대 0.02 |
| C6 all-of | 전체 coverage@10·complete@10 baseline 이상 |
| route pair | gate 10/10, holdout 2/2, 전체 12/12 non-regression |
| empty result | OFF/ON baseline보다 증가하지 않음 |
| sealed holdout | OFF/ON 각각 R@10 baseline 이상, MRR 하락 최대 0.01 |
| 구조 후보 불변식 | §8.3 다섯 항목 전부 PASS |

**EFFECTIVENESS**

| 항목 | 최종 PASS |
|---|---|
| OFF Recall@10 | candidate - baseline `>= +3.0%p` (순증 최소 4건) |
| ON Recall@10 | candidate - baseline `>= +3.0%p` (순증 최소 4건) |
| OFF/ON MRR | 각각 baseline 이상, 둘 중 하나 `>= +0.02` |
| OFF/ON nDCG@10 | 각각 baseline 이상 |
| targeted C2+C3+C5 | OFF 또는 ON top-10 순증 최소 3건, 다른 조건 순감소 없음 |
| 한국어 58건 ON | top-10 hit 순증 최소 2건 |
| route pair | gate effective 최소 2쌍, holdout 최소 1쌍, 전체 최소 3쌍 |
| holdout 방향성 | OFF/ON 합산 top-10 win > loss, win 최소 1건 |

HARD PASS + EFFECTIVENESS FAIL은 “안전하지만 실익 미확증”으로 승급 보류가 아니라 이번
candidate의 **최종 활성화 반려**다. v2 holdout이 이미 열렸기 때문에 같은 candidate를
조정해 재시험하지 않는다. HARD 하나라도 FAIL도 즉시 반려다.

### 8.3 structured lexical 후보 전용 HARD 불변식

| 항목 | PASS |
|---|---|
| lexeme 상위집합 | 모든 endpoint에서 `text_tsv` lexeme이 `search_tsv`에 포함 |
| 벡터 arm 불변 | 비교 전후 `chunk.text`·`chunk.embedding` 정렬 해시 동일 |
| 파생 결정성 | 백필 산출과 재색인 산출 문자열 동일; 반복 실행 rank 동일 |
| 문서 검색 무변경 | `chunk_type="section"` capped rank가 baseline과 완전 동일 |
| exact control | `_search_exact` 결과 무변경 |

verdict 69의 fallback-control 동일성은 이 후보에 적용하지 않는다. fallback 자체가 lexical
field를 읽으므로 text와 structured의 결과 차이는 의도된 변경이다. 이는 v1 결과를 본 뒤
만든 예외가 아니라 78번 설계와 v1 실행 전에 확정된 component 경계다. 대신 위 불변식과
동일 source에서 `text` 즉시 복귀 가능성을 HARD로 유지한다. 미설정·미인식 설정이 `text`로
degrade하는 설정 단위 테스트도 freeze/eval commit에서 PASS해야 한다.

## 9. 실행·최종 승급 절차

1. 하나의 신규 shared index를 만들고 index fingerprint, fixture commit, product source,
   query/split/corpus SHA를 기록한다.
2. gate96에서 baseline `text`와 candidate `structured`를 variants OFF/ON으로 각각 실행한다.
   field 외 evaluator, source, corpus, row, text, embedding, DB 조건은 동일하다.
3. §8.1을 blind하게 계산한다. 하나라도 FAIL이면 holdout 미개봉, candidate는 dark 유지다.
4. lead 승인 뒤 같은 index·candidate identity로 holdout24의 네 짝 실행을 최초 1회 수행한다.
5. 먼저 §8.2 HARD를 판정하고 전항 PASS일 때만 EFFECTIVENESS를 판정한다. 실패 질의
   진단은 최종 verdict를 고친 뒤에만 한다.
6. 전항 PASS면 architect가 최종 verdict를 `docs/architect-review/`에 남기고 lead가
   `DOCS_MCP_SEARCH_LEXICAL_FIELD=structured` 활성화를 별도 승인한다.
7. 활성화 뒤 smoke에서 이상이 있으면 환경값을 `text`로 되돌린다. 이 롤백은 v2 PASS
   판정을 소급 취소하지 않지만 운영 활성화는 중단한다.
8. 어느 평가 항목이든 FAIL이면 `structured`를 활성화하지 않는다. 임계값, alias, rank
   weight, label, split을 고쳐 같은 v2로 재시험하지 않는다.

## 10. 실제 프리즈 착수 전 lead 승인 지점

아래는 모두 blocking 승인이다. 한 항목이라도 미승인이면 F1 query 저작을 시작하지 않는다.

| ID | lead가 승인할 결정 | architect 권고 | 미승인 시 |
|---|---|---|---|
| **V2-D1** | 124건/120 scored, verdict 69 분포와 96/24 split 유지 | 그대로 승인 | quota 재설계 후 문서 개정 |
| **V2-D2** | strict novelty: v1 accepted endpoint까지 전량 불교집합 | 승인 | “전량 신규” 정의를 lead가 다시 확정 |
| **V2-D3** | root/child 12쌍과 split별 non-regression/effective guard 유지 | 승인 | pair 계약 재설계 |
| **V2-D4** | query raw SHA + scored split SHA + 두 corpus raw SHA 동시 프리즈 | 승인 | 프리즈 방식 재설계 |
| **V2-D5** | candidate `468ffaf…`, 동일 source의 `text` vs `structured` 비교 | 승인 | candidate identity 재지정 |
| **V2-D6** | §8.1 gate pre-open 최소치(3건/2건 포함) | 승인 | 결과 열람 전에만 기준 개정 가능 |
| **V2-D7** | §8.2 최종 HARD + verdict 69 EFFECTIVENESS 최소치 불변 | 승인 | 결과 열람 전에만 기준 개정 가능 |
| **V2-D8** | lead-only one-shot holdout, FAIL 시 v3 전량 신규 | 승인 | sealed 운영 주체 재지정 |
| **V2-D9** | PASS 시 env `structured` 활성화, FAIL 시 `text` dark 유지 | 승인 | 승급 행위·롤백 계약 재설계 |

이 승인표와 이 문서가 commit된 뒤에만 별도 지시로 실제 프리즈를 시작한다. 승인 뒤 기준을
바꾸려면 아직 검색을 한 번도 실행하지 않았더라도 문서 revision과 새 lead 승인을 남긴다.
검색 결과를 한 번이라도 연 뒤에는 기준 revision이 아니라 dataset v3가 필요하다.

## 11. 최종 판정

v1은 structured lexical 후보가 회귀 가드를 통과할 가능성과 일반 개선 신호를 보여줬지만,
노출 코퍼스이고 EFFECTIVENESS의 두 최소치도 넘지 못했다. 따라서 승급 근거가 아니다.
v2는 그 두 미달값을 목표로 문구나 표본을 고르는 보충 시험이 아니라, 동일 분포에서 전량
새 endpoint와 사용자 질의로 일반화 여부를 묻는 one-shot 승급 게이트다.

현재 판정은 **설계 완료·프리즈 미착수**다. lead가 §10 전항을 승인한 뒤 별도 작업에서만
v2 파일을 생성한다.
