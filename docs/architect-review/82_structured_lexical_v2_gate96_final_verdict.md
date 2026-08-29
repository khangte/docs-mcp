# 82. structured lexical v2 gate96 최종 처리 판정

- 설계·고정 기준: `docs/architect-review/80_structured_lexical_v2_sealed_holdout_freeze_design.md`
- freeze 판정: `docs/architect-review/81_structured_lexical_v2_freeze_verdict.md`
- 실행 결과: `docs/eval-results/08_2026-08-29_structured_lexical_v2_gate96.md`
- fixture commit: `d5a79c96558b8d68accb58146614d2616e15ab4d`
- result commit: `faaf3b5`
- 상태: **최종 활성화 반려 — holdout 미개봉, `structured` dark 유지**

## 1. 최종 판정

`DOCS_MCP_SEARCH_LEXICAL_FIELD=structured` 후보는 v2 gate96의 HARD route-pair 기준을
통과하지 못했다.

| 조건 | 기준 | 결과 | 판정 |
|---|---|---|---|
| route pair OFF | gate 10/10 root·child non-regression | 8/10 | **FAIL** |
| route pair ON | gate 10/10 root·child non-regression | 7/10 | **FAIL** |

나머지 HARD 7항목과 구조 후보 불변식 5/5가 PASS했어도 pair loss를 상쇄하지 않는다.
80번 §8.1은 root 또는 child가 한 칸이라도 악화된 pair를 막도록 결과 전에 고정됐다.

따라서 80번 §9-3을 그대로 적용한다.

1. sealed holdout24를 열지 않는다.
2. `DOCS_MCP_SEARCH_LEXICAL_FIELD` 기본값과 운영값은 `text`로 유지한다.
3. `structured` 활성화를 최종 반려한다.
4. alias·rank weight·label·split·임계값을 조정해 같은 v2를 재시험하지 않는다.
5. EFFECTIVENESS는 판정하지 않는다. 실행값은 진단 기록일 뿐 승급 근거가 아니다.

## 2. 회귀 증거

미검출을 11위로 cap한 `r_s`, baseline `text` → candidate `structured`의 RRF 순위다.

| pair/query | OFF | ON | 판정 |
|---|---:|---:|---|
| v2p03 child `GET /v1/payment_links/{payment_link}/line_items` | 1→10 | 1→10 | child 중대 회귀 |
| v2p07 root `GET /v1/tax/calculations/{calculation}` | 1→4 | 1→4 | root 회귀 |
| v2p01 root `GET /v1/quotes/{quote}` | 동일 | 5→6 | ON root 회귀 |

원시 runner 로그의 fallback(키워드 우선) 순위도 원인을 좁힌다.

| query | text fallback | structured fallback | RRF 변화 |
|---|---:|---:|---:|
| v2p03 root `payment_links/{id}` | 미검출 | 3 | 4→2 |
| v2p03 child `payment_links/{id}/line_items` | 1 | 미검출 | 1→10 |
| v2p07 root `tax/calculations/{id}` | 2 | 미검출 | 1→4 |
| v2p07 child `tax/calculations/{id}/line_items` | 1 | 1 | 1→1 |

v2p03은 root를 올린 대가로 child의 lexical top-10 정답을 제거했고, v2p07은 반대로
child를 유지하면서 root 정답을 제거했다. 한쪽 방향의 짧은-path boost 문제만이 아니다.
가중 단일 lexical document가 leaf·operation alias·ancestor context·free text의 경쟁을
질의마다 다르게 재배열하면서 **hierarchy 안의 어느 쪽도 단조 보존하지 못한 것**이다.

v2p01 ON은 text와 structured fallback 모두 정답을 못 찾는 가운데 RRF 5→6이다. 벡터 arm은
불변이므로 structured keyword 후보의 상대 순위 변화가 fusion 경쟁을 바꾼 2차 회귀다.

## 3. verdict 72/74 B 트랙과 같은 계열인가

**판정: gate 위반 계열은 같고, 즉시 원인은 다르다.**

| 축 | B-only / verdict 72·74 | structured lexical v2 |
|---|---|---|
| 공통 안전 실패 | root/child pair 중 한쪽 개선·유지 대가로 다른 쪽 회귀 | 동일 |
| activation | keyword variants ON에서 발생 | v2p03·v2p07은 OFF/ON 공통 |
| 직접 기전 | variant keyword pool이 동일 route-family sibling을 주입·경쟁시킴 | `text_tsv`를 weighted `search_tsv`로 전면 교체해 기존 lexical 정답을 top-10 밖으로 밀어냄 |
| 대표 증거 | p02 child 3→miss, coverage 수정 뒤 root 4→miss | v2p03 child 1→10, v2p07 root 1→4 |
| 결론 | variant admission/coverage로 target hierarchy 식별 실패 | 구조 필드를 넣었어도 하나의 가중 rank가 hierarchy 양쪽을 단조 보존하지 못함 |

더 상위의 공통 원인은 같다. lexical 신호가 target resource와 ancestor/context/sibling을
경쟁시킬 때 aggregate 개선만으로 root와 child의 동시 안전을 보장하지 못한다. verdict
74가 “target token과 context token의 구분 부재”를 진단했고 78번은 이를 index field로
보강했다. v2는 그 보강이 일부 query의 구조적 착지를 개선해도 **기존 text lexical 강점의
보존 계약까지 자동으로 주지는 않는다**는 새 반증이다.

따라서 “74번과 완전히 같은 variant flood 버그가 재발했다”는 서술은 부정확하고,
“동일한 hierarchy pair 안전 실패가 다른 lexical 교체 기전으로 재발했다”가 정확하다.

## 4. EFFECTIVENESS 기록의 해석

HARD FAIL이므로 정식 판정은 하지 않는다. 다만 다음 실행값은 pair 세 건만 국소 수정하는
후속이 충분하지 않음을 보여주는 진단 근거다.

- OFF Recall@10: +1%p(요구 +3%p) — 미달
- targeted C2+C3+C5: OFF/ON 모두 net −1, 순감 발생 — 미달
- ON Recall@10, MRR, nDCG, 한국어 gate는 최소치 충족 기록

즉 pair veto만 우회해도 candidate가 §8.1 전항을 통과하는 상태가 아니다. 결과를 보고
세 pair에 예외를 넣거나 OFF 최소치를 낮추는 것은 71번과 80번이 금지한 소급 완화다.

## 5. 다음 레버 판정

### 5.1 즉시 승급 가능한 레버는 없다

다음 변경은 반려한다.

- v2p01·v2p03·v2p07 token을 alias에 추가
- `_STRUCTURED_RANK_WEIGHTS` 조정
- pair별/path별 boost 또는 예외
- v2 label·variant·split 수정
- pair veto를 category/aggregate 순증으로 대체
- 현 candidate를 고쳐 v2 gate/holdout에 재진입

이들은 모두 노출된 gate96 결과를 상수나 규칙으로 옮기는 작업이다.

### 5.2 허용할 다음 단계 — gate-only 원인 분해

새 product 후보를 만들기 전에 이미 노출된 gate query만으로 다음을 진단할 수 있다.

1. v2p03·v2p07·v2p01 6질의에 대해 text/structured의 keyword top-10, vector top-10,
   최종 RRF contribution을 나란히 기록한다.
2. structured query가 맞힌 A/B/C/D field lexeme과 탈락한 정답·상승한 decoy endpoint를
   기록한다.
3. root/child 양쪽에서 “구조 신호 신규 검출”과 “기존 text 정답 보존 실패”를 분리한다.

이는 postmortem이지 승급 재시험이 아니다. holdout은 사용하지 않는다.

### 5.3 후속 설계 후보 — 승인 아님

진단이 같은 기전을 확인할 때만 다음 아키텍처를 별도 설계할 수 있다.

- `text_tsv`를 없애거나 전면 대체하지 않고 primary lexical rank로 보존한다.
- structured 신호는 별도 rank list 또는 **text lexical miss에만 작동하는 bounded
  augmentation**으로 분리한다.
- 기존 text hit의 keyword 순서를 보존하는 불변식과, structured-only 후보의 기여 상한을
  결과 열람 전에 정의한다.
- path specificity/hierarchy 신호가 필요하면 78번 D7 비범위를 뒤집는 새 근거와 별도
  verdict가 먼저 필요하다.

이 방향은 현재 candidate의 “단일 weighted field 전면 교체”와 component가 다르다.
그러나 효과성은 아직 증명되지 않았으며 구현 승인도 아니다. p02와 v1/v2 exposed set은
개발 회귀·진단용으로만 쓸 수 있고, 최종 승급에는 전량 신규 v3가 필요하다.

## 6. v2와 holdout 처리

- v2 gate96은 이제 exposed development/diagnostic corpus다.
- v2 holdout24는 실행하지 않았지만 80번 §9-8의 “어느 항목이든 FAIL이면 같은 v2로
  재시험하지 않는다”를 적용한다. 새 candidate의 승급 holdout으로 재사용하지 않는다.
- 후속 candidate가 생기면 verdict 69 분포와 pair guard를 유지한 전량 신규 v3를
  프리즈한다.
- 현재 v2의 query·label·variant·pair·split과 결과 문서는 변경하지 않는다.

## 7. shared index cleanup 판정

**cleanup을 지시한다.** `rrfeval_1b75828f`는 ephemeral 평가 DB이고 holdout을 열지 않으므로
더 유지할 목적이 없다. 보존 근거는 fixture/result commit과 fingerprint, query/corpus SHA,
raw gate 로그로 충분하다.

developer는 holdout 명령을 실행하지 말고 다음 cleanup만 수행한다.

```bash
cd /home/kang/projects/docs-mcp && uv run python tests/fixtures/corpus_eval/run_corpus_eval.py \
  --mode cleanup --db-url 'postgresql+psycopg://postgres:postgres@localhost:5432/rrfeval_1b75828f'
```

cleanup 뒤 DB 부재를 확인하고 lead/architect에 보고한다. raw gate 로그는 scratch 수명에
의존하므로 필요한 pair 근거는 이미 commit된 eval 결과와 이 verdict에 남겼다.

## 8. 최종 상태

| 항목 | 판정 |
|---|---|
| gate96 | **FAIL** |
| holdout24 | **미개봉, 실행 금지** |
| `structured` 활성화 | **최종 반려** |
| 운영 lexical field | **`text` 유지** |
| 같은 v2 재시험 | **금지** |
| shared DB | **cleanup 지시** |
| 후속 | gate-only 기전 진단 후 별도 candidate 설계 여부 판단; 승급은 신규 v3 |

weighted structured lexical 표현은 v1 exposed에서 HARD를 통과했지만 v2 신규 endpoint/query
pair에 일반화되지 않았다. ON aggregate 개선은 남길 가치가 있는 연구 신호이나, 제품
활성화의 root/child 안전 근거는 아니다.
