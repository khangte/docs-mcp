# 84. text-primary + bounded structured augmentation 설계

- 근거: `docs/architect-review/82_structured_lexical_v2_gate96_final_verdict.md`,
  `docs/architect-review/83_structured_lexical_v2_route_pair_regression_postmortem.md`
- 대상: `search_endpoints`의 기본 endpoint RRF 검색
- 상태: **설계 승인 — protected-slot postprocessor, runtime 안전계약 A**
- 후속: 별도 v3 freeze 설계 승인 후 구현 계획 작성. 이 문서로 구현을 바로 시작하지 않는다.

## 1. 목표와 범위

weighted `search_tsv`를 primary lexical field로 전면 교체한 후보는 v2 gate96 route-pair
HARD를 통과하지 못했다. postmortem 83은 정답 raw `ts_rank`가 낮아진 것이 아니라,
root·sibling·generic-ID decoy가 A/B 고가중 부분매치를 더 크게 받아 기존 text 정답의
상대순위를 역전한 것이 원인임을 확인했다. v2p01은 structured-only 0점 후보가 top-50
tie와 RRF both-arm을 거쳐 vector-only 정답을 한 칸 민 별도 경로였다.

이번 후보의 목표는 다음 두 문장을 동시에 만족하는 최소 변경이다.

1. 현행 `text_tsv` keyword arm과 vector arm이 만든 RRF 결과를 primary로 보존한다.
2. 이미 primary wide 후보 안에 있는 vector-only endpoint에 한해, original query가 A/B/C
   구조 필드에 양수 evidence를 줄 때 최대 한 칸만 개선할 수 있다.

다음은 범위 밖이다.

- `search_tsv` full swap 재활성화
- 세 번째 RRF arm 도입
- text `ts_rank` 재채점·bonus 가산
- 새 operation alias, path specificity, route-family 의미 규칙
- query/endpoint별 예외 또는 exposed v2 token 튜닝
- structured-only 후보를 corpus에서 새로 검색해 wide pool에 주입
- 임베딩·text·generated column·GIN index 변경
- v3 fixture나 수치 임계값의 최종 freeze — 부록 골격만 정의

## 2. 세 augmentation 형태 비교

### 2.1 별도 structured RRF arm — 반려

형태는 `text keyword + vector + structured` 세 rank list를 RRF로 융합하는 것이다. arm
경계는 명확하지만 hard non-regression bound가 없다.

- endpoint arm weight를 현행 1.0으로 유지하면 structured decoy가 세 번째 RRF 항을 얻는다.
  v2p03/v2p07에서 확인한 decoy both-arm 증폭을 줄이지 않고 더 강하게 만든다.
- structured arm weight를 낮춰도 어떤 query에서 몇 칸 움직일지 상한을 보장하지 못한다.
- 안전하게 만들려면 RRF 뒤에서 text hit를 다시 clamp해야 하므로 독립된 해법이 아니고,
  결국 §2.3 postprocessor가 필요하다.
- arm weight 변경은 이번 요청의 FROZEN 제약도 위반한다.

따라서 세 번째 arm은 쓰지 않는다.

### 2.2 text `ts_rank`에 bounded bonus 가산 — 반려

형태는 `text_score + min(structured_bonus, cap)` 또는 그 정규화 변형이다.

- `ts_rank` scale은 query term 수, 문서 내 빈도, field별 반복에 따라 달라진다. 한 개 score
  cap은 결과 rank의 최대 변위를 뜻하지 않는다.
- bonus는 text keyword arm 안의 기존 순서를 직접 바꾼다. 승인된 “text-backed endpoint의
  절대 base rank 불변”과 모순이다.
- query별 정규화·percentile·동적 cap을 더하면 새로운 tuning surface가 생긴다.
- score가 아니라 rank 이동을 clamp하면 §2.3의 후처리 순열과 같아진다.

따라서 text score에는 structured 값을 더하지 않는다.

### 2.3 protected-slot postprocessor — 채택

현행 text keyword + vector RRF를 먼저 완성한다. text keyword arm에 존재한 endpoint가
차지한 RRF slot을 protected anchor로 고정하고, 나머지 vector-only slot끼리만 structured
evidence로 최대 한 칸 교환한다.

이 형태를 택하는 이유는 bound가 score의 간접 효과가 아니라 **허용된 permutation 자체**로
표현되기 때문이다. 구현은 아래 네 사실을 직접 assert할 수 있다.

- text-backed endpoint의 absolute rank 변화 0
- vector-only endpoint의 absolute displacement 최대 1
- original-query structured score 0이면 전체 순서 변화 0
- base wide 밖 신규 endpoint 유입 0

## 3. 전체 데이터 흐름

```text
method+path exact -------------------------------------------> 기존 반환

text_tsv keyword top-width -----+
                                 +--> RRF(K=60, weights=None, top_k=width)
vector top-width ----------------+               |
                                                 v
                                  base wide order + protected slots
                                                 |
base-wide vector-only ref_id ----- A/B/C original-query score 1회 조회
                                                 |
                                  bounded adjacent-swap postprocessor
                                                 |
                                            final top_k

fallback ----------------------------------------------------> 기존 text_tsv 경로
```

`width = max(top_k * 4, 50)`은 현행 `_CANDIDATE_WIDTH_MULTIPLIER=4`,
`_MIN_CANDIDATE_WIDTH=50`을 그대로 사용한다. 현행 `_search_rrf`는 arm을 width만큼 가져온
뒤 RRF에서 곧바로 final `top_k`로 자른다. candidate는 RRF를 width까지 계산하고
postprocess 뒤 final `top_k`로 자른다.

RRF 정렬은 전체 union을 먼저 정렬한 뒤 자르므로, postprocessor가 no-op이면 wide RRF의
앞 `top_k`는 현행 final RRF와 완전히 같다. 이 동일성은 단위·통합 테스트의 baseline
control이다.

exact lookup은 지금처럼 RRF 전에 반환한다. fallback, document search, MCP request/response
schema는 이 흐름을 타지 않는다.

## 4. primary와 protected anchor 정의

### 4.1 primary rank

`base_wide`는 다음 두 기존 list만으로 만든 `reciprocal_rank_fuse(..., top_k=width)`다.

- `keyword_ref_ids`: `text_tsv` + 무가중 기존 `ts_rank`, top-width
- `vector_ref_ids`: 원문과 기존 query variants의 best-rank 병합, top-width

`RRF_K=60`, `weights=None`, tie-break, ref dedupe, `FusedResult.score`, `match_type`,
`contributing_arms`를 바꾸지 않는다. structured evidence는 RRF score나 contributing arm으로
표시하지 않는다. postprocess 뒤에도 각 `FusedResult`의 기존 metadata를 그대로 운반한다.

### 4.2 protected 집합

```text
protected_refs = set(keyword_ref_ids)
protected_positions = {
    position(base_wide, ref) for ref in protected_refs if ref in base_wide
}
```

같은 실행의 text keyword top-width에 존재한 모든 endpoint를 보호한다. query variants가
filter만 열어 raw `ts_rank=0`인 hit도 보수적으로 포함한다. protected endpoint는
postprocess 전후 같은 absolute position을 가져야 한다.

이 정의는 정답 label을 runtime에 요구하지 않는다. “text 정답만 선택 보호”하는 대신
primary keyword arm이 실제로 지지한 모든 문서를 동일하게 보호한다. 일부 decoy도 함께
고정되어 효과성은 줄 수 있지만, exposed 결과를 보고 보호 대상을 고르는 과적합 경로를
차단한다.

## 5. structured evidence 정의

### 5.1 A/B/C only

structured evidence는 기존 `search_tsv` full rank를 사용하지 않는다. D `text`는 primary
keyword arm이 이미 소비했으므로 A/B/C만 별도로 묶는다.

```text
structure_tsv(d) =
    setweight(to_tsvector('simple', NORM(d.leaf_text)),    'A') ||
    setweight(to_tsvector('simple', NORM(d.intent_text)),  'B') ||
    setweight(to_tsvector('simple', NORM(d.context_text)), 'C')

augmentation_score(d, q) = ts_rank(
    _STRUCTURED_RANK_WEIGHTS,
    structure_tsv(d),
    original_query_score_tsq(q),
)
```

`NORM`은 `text_tsv`/`search_tsv`와 같은 ASCII-한글 경계 처리와 문자 치환이다. 새 generated
column이나 index를 만들지 않는다. base wide 안의 소수 `ref_id`를 먼저 제한하므로
repository가 최대 width건에 대해 한 번 계산한다.

### 5.2 original query only

`augmentation_score`의 tsquery에는 `tokenize_terms(original_query)`만 넣는다.

- query variants를 score에 넣지 않는다.
- query variants를 structured 후보 filter로도 쓰지 않는다.
- endpoint candidate corpus를 `structure_tsv @@ variant_tsq`로 새로 검색하지 않는다.
- original term이 없거나 A/B/C 교집합이 없으면 score는 0이다.

기존 base의 text keyword/vector variants 동작은 그대로 유지한다. 이 구분은 v2p01의
variant-only `quote`가 structured-only 0점 후보로 RRF에 들어온 경로를 닫는다.

### 5.3 계산 대상

score 조회 대상은 다음 교집합뿐이다.

```text
eligible_input_refs = refs(base_wide) - protected_refs
```

즉 base wide에 vector arm으로 이미 들어온 후보만 score를 받는다. `search_tsv`나 A/B/C로
base 밖 후보를 검색하지 않는다. 결과 mapping에 ref가 없거나 score가 NULL/0 이하면 0으로
취급한다.

## 6. bounded permutation 알고리즘

### 6.1 규칙

입력은 `base_wide`, `protected_positions`, `augmentation_score_by_ref`다.

1. 결과 list를 `base_wide`의 복사본으로 시작한다.
2. 마지막 adjacent pair부터 첫 pair 방향으로 한 번만 순회한다.
3. pair의 두 position 중 하나라도 protected면 no-op한다.
4. pair의 endpoint 중 하나라도 이미 이번 요청에서 이동했다면 no-op한다.
5. 아래 endpoint의 score가 `> 0`이고 위 endpoint score보다 **엄격히 클 때만** swap한다.
6. swap한 두 endpoint를 moved set에 넣는다.
7. score tie는 base order를 유지한다.
8. 순회를 마친 뒤 앞 `top_k`만 반환한다.

의사코드:

```text
ranked = copy(base_wide)
moved = set()

for lower_pos from len(ranked)-1 down to 1:
    upper_pos = lower_pos - 1
    upper = ranked[upper_pos]
    lower = ranked[lower_pos]

    if upper_pos in protected_positions or lower_pos in protected_positions:
        continue
    if upper.ref_id or lower.ref_id is in moved:
        continue
    if score(lower) <= 0 or score(lower) <= score(upper):
        continue

    swap(ranked[upper_pos], ranked[lower_pos])
    moved.update({upper.ref_id, lower.ref_id})

return ranked  # orchestrator가 이 full-width 결과를 final top_k로 절단
```

역방향 순회는 final cutoff 바로 아래 후보도 위 slot과 비교할 기회를 준다. moved guard가
같은 endpoint의 연쇄 bubble-up/down을 막으므로 순회 방향과 무관하게 displacement 상한은
한 칸이다. 이 후보에서는 더 큰 window나 반복 pass를 허용하지 않는다.

### 6.2 runtime 안전계약 A

모든 요청에서 다음이 성립해야 한다.

```text
for d in base_wide:
    if d in protected_refs:
        final_rank(d) == base_rank(d)
    else:
        abs(final_rank(d) - base_rank(d)) <= 1

refs(final_wide) == refs(base_wide)

if max(augmentation_score_by_ref.values(), default=0) <= 0:
    final_wide == base_wide
```

모든 baseline top-k endpoint의 순위를 고정하는 계약 B는 채택하지 않는다. 같은 크기의
top-k에서 기존 k건을 모두 같은 absolute rank로 유지하면 새 endpoint의 top-k 진입이
불가능해 augmentation 실익이 0이기 때문이다.

계약 A는 text-backed endpoint를 hard anchor로 보호하고 vector-only endpoint끼리만 한 칸
교환한다. 따라서 rank 11의 vector-only 후보가 rank 10으로 들어올 수 있지만, rank 10이
text-backed면 그 crossing은 금지된다.

## 7. v2 세 회귀의 비재발 증명

### 7.1 v2p03 child `v2g045` — RRF 1→10

정답 `GET /v1/payment_links/{payment_link}/line_items`는 baseline text keyword rank 1이다.
그러므로 base wide의 해당 absolute position은 protected다.

- parent `/payment_links`가 A=`payment,link`로 더 큰 augmentation score를 받아도 정답
  position과 swap할 수 없다.
- structured evidence는 독립 RRF arm이 아니므로 parent가 새 RRF 항을 얻지 않는다.
- full structured rank 1→18 같은 전역 재정렬을 하지 않는다.

결과적으로 정답의 base/final rank는 candidate에서 동일하다. 이 pair의 root 개선을 위해
child를 희생하는 경로가 runtime invariant로 닫힌다.

### 7.2 v2p07 root `v2g079` — RRF 1→4

정답 `GET /v1/tax/calculations/{calculation}`는 baseline text keyword rank 2다. 역시
protected다.

- `/tax_ids/{id}`가 A에서 `tax,id`, B에서 `retrieve`를 가져 더 큰 score여도 protected
  root를 통과하지 못한다.
- literal `tax_ids`의 generic `id`를 새 예외로 제거하지 않는다. 전역 alias/field 규칙을
  고치는 대신 primary rank 보호로 blast radius를 제한한다.

정답의 absolute base/final rank는 동일하다. child `line_items`도 text-backed이면 같은
보호를 받는다.

### 7.3 v2p01 root `v2g023` ON — RRF 5→6

정답은 vector-only였으므로 protected anchor만으로는 충분하지 않다. 이 회귀는 original
한글 query가 아니라 영문 variant `open one quote...`가 A의 단수 `quote` 후보를 열고,
모든 keyword score가 0인 상태에서 ID tie와 RRF both-arm을 만든 경로였다.

이번 후보에서는:

- A/B/C score는 original 한글 query로만 계산한다.
- 영문 A/B/C와 original score term의 교집합이 없어 전 후보 score가 0이다.
- score 0이면 postprocessor 전체가 no-op한다.
- `/v1/quotes`를 structured-only 후보나 세 번째 arm으로 주입하지 않는다.

따라서 base RRF 5위는 candidate에서도 5위다. variant-filter 0점 tie의 chunk ID 순서도
postprocessor 입력을 바꾸지 못한다.

## 8. 기존 FROZEN 불변식

다음 상수·표현·동작을 변경하지 않는다.

| 항목 | 계약 |
|---|---|
| `_STRUCTURED_RANK_WEIGHTS` | `{0.1,0.2,0.4,1.0}` 그대로. A/B/C-only score에도 같은 배열 사용 |
| `OPERATION_ALIASES` | 항목 추가·삭제·등급 이동 없음 |
| `RRF_K` | 60 유지 |
| endpoint arm weights | `weights=None`, text/vector 각 1.0 유지 |
| candidate width | multiplier 4, minimum 50 유지 |
| text keyword | `text_tsv` + 기존 무가중 `ts_rank` + 기존 variants filter 계약 유지 |
| vector | embedding, variants best-rank merge, HNSW 설정 유지 |
| generated columns | `text_tsv`, `search_tsv` 표현 변경 없음 |
| index data | `text`, embedding, leaf/intent/context와 백필 산출 변경 없음 |
| exact/fallback | 기존 경로·순위 완전 동일 |

새 rank 상수는 `MAX_STRUCTURED_PROMOTION = 1` 하나다. 이는 RRF arm weight가 아니라
postprocess permutation의 최대 변위다. env/config로 노출하지 않으며 candidate source
identity와 v3 rules SHA에 포함한다.

`DOCS_MCP_SEARCH_LEXICAL_FIELD=structured` full swap은 dark 상태로 남지만 이 candidate가
사용하거나 활성화하지 않는다. augmentation은 `search_lexical_field=text`에서만 유효하다.

## 9. 구성요소 경계

### 9.1 endpoint candidate orchestration

endpoint RRF 경로의 책임:

- text keyword/vector width list를 현행대로 생성
- wide RRF 생성
- protected ref/position 계산
- unprotected base-wide ref score 조회
- 순수 postprocessor 호출
- final top_k 절단과 기존 DTO 변환

generic `rrf.py`는 endpoint 구조를 알지 못하므로 변경하지 않는다.

### 9.2 repository score query

repository는 `(ref_ids, original_score_terms) -> {ref_id: score}`의 bounded batch read만
제공한다.

- `ref_ids`가 비면 SQL을 실행하지 않는다.
- `chunk_type='endpoint'`와 ref ID 집합으로 먼저 제한한다.
- A/B/C 식은 모델의 기존 normalization과 weight expression을 재사용한다.
- corpus-wide `@@` 후보 검색이나 새 GIN index는 없다.
- 결과 순서가 아니라 ref_id mapping을 반환한다.

### 9.3 pure postprocessor

bounded permutation은 DB·embedding·endpoint metadata를 모르는 순수 함수로 분리한다.
입력 list와 protected refs, score mapping만으로 결과를 낸다. 이 경계가 max-one-step,
anchor 보존, tie no-op을 작은 단위 테스트로 증명하게 한다.

### 9.4 activation과 rollback

별도 boolean setting을 둔다.

```text
DOCS_MCP_SEARCH_STRUCTURED_AUGMENTATION=false  # default
```

- false: 현행 RRF 호출과 final top_k 절단을 그대로 사용한다.
- true + lexical field text: 이 설계의 candidate.
- true + lexical field structured: 잘못된 조합으로 startup/config validation에서 거부한다.

full swap과 augmentation을 한 실행에 중첩해 attribution을 잃지 않는다. rollback은 setting을
false로 되돌리는 한 동작이며 DB schema/data rollback이 없다.

## 10. 결정성·오류·성능

### 10.1 결정성

- base keyword/vector/RRF tie-break는 현행 그대로다.
- score tie는 swap하지 않는다.
- 순회 방향은 wide list의 끝→앞으로 고정한다.
- endpoint당 이동은 요청당 최대 한 번이다.
- 동일 base list와 score mapping은 항상 동일 output을 낸다.

### 10.2 no-op과 오류

다음은 정상 no-op이다.

- original query token 없음
- base wide의 unprotected ref 없음
- A/B/C가 빈 문자열 또는 모든 augmentation score 0
- adjacent positive inversion 없음
- final cutoff 주변이 protected anchor로 막힘

unexpected SQL/DB 오류를 숨기고 부분 rerank를 만들지 않는다. candidate flag가 켜진 요청은
기존 request error 처리로 실패시켜 migration/config 불일치를 드러낸다. default OFF 경로는
추가 SQL 자체를 실행하지 않는다.

### 10.3 비용 상한

- embedding 호출 추가 0
- DB write 0
- 구조 score SQL 요청당 최대 1회
- score row 최대 width건
- postprocess 시간 O(width), 메모리 O(width)
- corpus 전체 scan과 새 index 0

v3 freeze는 correctness와 별도로 candidate/base latency를 같은 shared DB에서 기록한다.
정확한 허용치는 freeze 문서에서 실행 전에 고정하되, 추가 SQL 1회의 비용을 숨기기 위해
headline 품질만으로 latency 회귀를 상쇄하지 않는다.

## 11. 테스트 계약 골격

### 11.1 pure postprocessor

- protected endpoint는 앞·중간·cutoff 위치 모두 absolute rank 불변
- unprotected positive inversion은 정확히 한 adjacent swap
- 세 후보 연쇄 inversion에서도 각 endpoint displacement 절대값 ≤1
- protected anchor를 가로지르는 swap 없음
- score 0, 음수/누락, tie는 no-op
- base-wide ref multiset 완전 동일
- empty/singleton/`top_k=1` 경계
- 같은 입력 반복 결과 동일

### 11.2 repository score

- A/B/C weight label과 `_STRUCTURED_RANK_WEIGHTS` 재사용
- D text lexeme만 맞으면 score 0
- original A/B/C lexeme은 양수
- variant-only lexeme은 original score query에 들어가지 않음
- ref ID 범위 밖 endpoint가 결과에 없음
- 빈 ref list SQL no-op
- 한글/ASCII 경계 normalization이 기존 generated expression과 동일

### 11.3 endpoint integration

- setting OFF는 기존 keyword/vector 호출 수·RRF/final 순서 완전 동일
- exact/fallback/document search 무변경
- setting ON은 wide RRF 뒤 한 번만 postprocess
- text-backed final rank 불변
- vector-only max-one-step
- `match_type`·contributing arms에 structured가 추가되지 않음
- invalid `structured full swap + augmentation` 조합 거부
- v2p03/v2p07/v2p01 최소 재현 fixture

## 12. rollout·관측 계약

candidate는 기본 OFF dark 배포만 허용한다. v3 최종 승급 전 운영 default를 true로 바꾸지
않는다.

평가/진단 로그는 질의별로 다음을 보존한다.

- text keyword top-width ref/rank/score
- vector top-width ref/rank
- base wide RRF ref/rank/contribution
- protected 여부
- original A/B/C augmentation score
- swap pair와 base→final rank
- final top-k
- index/query/corpus/source fingerprint

로그는 exposed 개발셋과 sealed 평가 실행 산출물에만 요구하며 MCP 응답 surface에는 노출하지
않는다. shared DB는 arm trace와 판정 문서 작성이 끝나기 전에 cleanup하지 않는다.

## 13. 승인 결정 D1..D14

| ID | 결정 | 승인 내용 |
|---|---|---|
| **D1** | augmentation 형태 | RRF 뒤 protected-slot postprocessor |
| **D2** | primary | 현행 `text_tsv` keyword + vector RRF 완전 보존 |
| **D3** | 구조 evidence | base-wide vector-only ref의 A/B/C-only `ts_rank`; D 제외 |
| **D4** | query 입력 | original query score terms만; variants 제외 |
| **D5** | protected 집합 | text keyword top-width의 모든 ref, raw score 0 hit 포함 |
| **D6** | candidate 집합 | base wide RRF 내부 unprotected만; 신규 후보 주입 금지 |
| **D7** | bound | deterministic non-overlap adjacent swap, endpoint당 최대 1칸 |
| **D8** | zero/tie | lower score `>0`이고 strict greater일 때만 swap; 나머지 no-op |
| **D9** | 적용 경로 | endpoint default RRF만; exact/fallback/document search 제외 |
| **D10** | frozen | weights, aliases, RRF_K, arm weight, width 전부 불변 |
| **D11** | 새 상수 | `MAX_STRUCTURED_PROMOTION=1`, env 비노출 |
| **D12** | rollout | 별도 기본-OFF setting; text primary와만 조합 |
| **D13** | 평가 | v1/v2 exposed regression 후 전량 신규 v3 gate96/holdout24 |
| **D14** | 단계 | 이 문서는 설계 승인; 별도 freeze 승인 후 구현 계획 |

## 부록 A. v3 sealed split freeze 골격

이 부록은 다음 freeze 문서의 범위와 하한을 정한다. fixture 내용, source SHA, query SHA,
수치표의 최종 승인은 별도 architect 문서에서 실행 전에 한다.

### A.1 novelty와 분포

- scored 120건: gate96 + sealed holdout24
- category 총수: C1 12, C2 24, C3 18, C4 12, C5 24, C6 12, C7 18
- Stripe/GitHub, 한국어/영어 균형은 69·80번 수준을 유지
- route pair 12쌍/24질의: gate 10쌍, holdout 2쌍
- pair 분포: C2 2쌍, C3 2쌍, C5 8쌍; Stripe/GitHub 각 6쌍; 한국어/영어 각 6쌍
- v1/v2 query 문장, accepted endpoint, pair endpoint, pair route family 재사용 금지
- v1/v2 failure token을 단순 치환한 near-duplicate 금지
- candidate 결과를 보지 않은 상태에서 query/label/variant/split을 commit하고 SHA freeze

v1·v2는 exposed development/regression corpus로만 쓴다. v3 authoring 중 p03/p07/p01과
형태가 같은 pair를 포함할 수는 있지만 endpoint·route family·표현은 신규여야 한다.

### A.2 candidate identity와 실행축

하나의 신규 shared index에서 다음 네 짝 실행을 한다.

| run | product | variants | primary |
|---|---|---|---|
| A | baseline, augmentation OFF | OFF | text+vector RRF |
| B | baseline, augmentation OFF | ON | text+vector RRF |
| C | candidate, augmentation ON | OFF | 같은 base + postprocess |
| D | candidate, augmentation ON | ON | 같은 base + postprocess |

fixture commit, product source, rules SHA, query/corpus SHA, index fingerprint, model/dim,
endpoint/chunk count를 네 실행에서 같게 고정한다. candidate는 lexical field `text`만 사용한다.

### A.3 pre-open 순서

1. p02, v1, v2 exposed set에서 단위 불변식과 route-pair non-regression을 실행한다.
2. v3 gate96에서 baseline/candidate × variants OFF/ON을 같은 shared index로 실행한다.
3. 일반 HARD와 후보 전용 HARD를 전부 판정한다.
4. HARD PASS일 때만 EFFECTIVENESS를 판정한다.
5. gate HARD + EFFECTIVENESS 전항 PASS일 때만 lead가 holdout24 최초 개봉을 지시한다.
6. holdout HARD/방향성 전항 PASS일 때만 activation verdict를 회부한다.

어느 단계든 FAIL이면 holdout 미개봉 또는 candidate 반려다. 결과를 보고 promotion bound,
score 식, protected 정의, aliases, fixture, threshold를 바꿔 같은 v3로 재시험하지 않는다.

### A.4 후보 전용 HARD

| 항목 | PASS 하한 |
|---|---|
| text primary | keyword top-width ref/score/rank baseline과 완전 동일 |
| vector primary | vector top-width ref/rank baseline과 완전 동일 |
| base RRF | postprocess 전 wide ref/rank/contribution baseline과 완전 동일 |
| protected slots | absolute rank 위반 0 |
| displacement | 모든 unprotected `abs(delta_rank) <= 1` |
| zero-score | augmentation score 0인 질의 candidate final이 baseline과 완전 동일 |
| membership | base wide 밖 endpoint 유입 0, ref multiset 변화 0 |
| controls | exact/fallback/document search baseline과 완전 동일 |
| route pair gate | 10/10 root·child non-regression |
| empty result | baseline 대비 증가 0 |

일반 C1, category, C6, determinism, latency, shared-index identity HARD는 69·80번을 계승한다.
category aggregate가 protected 또는 pair loss를 상쇄하지 못한다.

### A.5 EFFECTIVENESS 하한 방향

별도 freeze 문서는 최소한 80번보다 약하지 않게 다음을 숫자로 고정한다.

- OFF/ON Recall@10의 paired 순증과 +%p
- OFF/ON MRR·nDCG non-regression과 최소 개선
- targeted C2+C3+C5 순증과 다른 activation의 무순감
- 한국어 ON 순증
- route-pair effective 수
- base rank 11→10 structured crossing 수와 반대 방향 10→11 수
- latency p50/p95와 추가 SQL 비용

이 후보는 모든 text-backed rank를 고정하므로 개선 가능 영역이 의도적으로 좁다. 그 사실은
효과성 임계값을 결과 뒤에 낮출 이유가 아니다. v3에서 실익이 부족하면 bound를 풀지 않고
candidate를 반려하거나 별도 architecture로 다시 설계한다.

### A.6 holdout

- gate96 전항 PASS 전 실행 금지
- pair 2/2 non-regression
- protected/one-step/zero-score/membership 불변식 전부 재적용
- OFF/ON 각각 R@10 baseline 이상, MRR 허용 하락은 freeze에서 사전 고정
- top-10 win > loss, 최소 win 1
- 최초 개봉 뒤 v3는 exposed로 전환하며 수정 candidate의 sealed split으로 재사용 금지

## 14. 설계 결론

이번 후보는 structured rank를 더 약하게 전면 적용하는 설계가 아니다. text+vector RRF를
먼저 완결된 primary 결과로 만들고, 구조 신호는 그 결과가 남긴 vector-only 인접 slot
안에서만 한 번 작동한다.

이 경계 때문에 v2p03/v2p07의 text 정답은 구조 score와 무관하게 고정되고, v2p01의
variant-only 0점 경로는 완전 no-op한다. 반대로 hard bound 안에서 실익을 보이지 못하면
weight·alias·window를 풀어 같은 후보를 살리지 않는다. **안전성을 architecture로 먼저
고정하고, 남은 좁은 효과성만 신규 v3가 판단하게 하는 것**이 이 설계의 전부다.
