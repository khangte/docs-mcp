# 83. structured lexical v2 route-pair 회귀 postmortem

- 문서 성격: **원인 분석**. 승급·활성화 판정이 아니며 82번의 최종 반려 상태를 변경하지 않는다.
- 사건: v2 gate96 HARD route-pair non-regression 실패 — variants OFF 8/10, ON 7/10
- 대상 candidate: `DOCS_MCP_SEARCH_LEXICAL_FIELD=structured`, product source `468ffaf`
- 원본 실행: `docs/eval-results/08_2026-08-29_structured_lexical_v2_gate96.md`
- 최종 처리: `docs/architect-review/82_structured_lexical_v2_gate96_final_verdict.md`
- 분석 범위: exposed gate의 `v2p01`·`v2p03`·`v2p07` 여섯 질의만 사용. v2 holdout은
  실행하거나 조회하지 않았다.

## 1. 원인 요약

원인은 variant flood가 아니라 **기존 `text_tsv` lexical arm을 weighted
`search_tsv` arm으로 전면 교체한 것**이다. 더 정확히는 정답 문서의 절대 `ts_rank`가
낮아진 것이 아니다.

`search_tsv`의 D에는 기존 `text` 전체가 그대로 들어 있고, baseline의 2인자 `ts_rank`와
candidate의 `_STRUCTURED_RANK_WEIGHTS='{0.1,0.2,0.4,1.0}'`는 모두 PostgreSQL 기본
`{D,C,B,A}` 가중치를 쓴다. 따라서 기존 D 신호는 사라지지 않는다. 세 회귀에서 발생한
일은 다음과 같다.

1. 정답도 A/B/C 신호를 받아 절대 점수가 올랐다.
2. 그러나 일부 root·sibling·decoy가 질의의 흔한 토큰을 A/B에서 더 많이 받아 훨씬 크게
   올랐다.
3. OR tsquery에는 필수 target lexeme이나 최소 coverage 계약이 없어서, 높은 등급의 부분
   매치가 낮은 등급의 더 완전한 매치를 이겼다.
4. 이 상대순위 역전이 RRF의 keyword rank를 바꿨다. RRF는 `ts_rank` 크기가 아니라 arm
   내 순위만 보므로 작은 의미 차이를 보존하지 않고 역전을 그대로 fusion에 전달했다.

핵심 실측은 다음과 같다. 값은 동일 corpus·동일 물리 인덱스에서 `text`/`structured`만
바꾼 postmortem 재현의 raw `ts_rank`다.

| query/문서 | text `ts_rank` | structured `ts_rank` | 변화 | keyword rank |
|---|---:|---:|---:|---:|
| v2p03 child 정답 `/payment_links/{id}/line_items` | 0.0347006 | 0.1035927 | 2.99배 | 1→18 |
| v2p03 root decoy `GET /payment_links` | 0.0176374 | 0.2136047 | 12.11배 | top-50 밖→1 |
| v2p07 root 정답 `/tax/calculations/{id}` | 0.0502919 | 0.2340026 | 4.65배 | 2→15 |
| v2p07 decoy `GET /tax_ids/{id}` | 0.0484794 | 0.3688652 | 7.61배 | 7→2 |

즉 `_STRUCTURED_RANK_WEIGHTS`가 정답의 raw 점수를 깎은 것이 아니라 **경쟁 문서의
점수 증가율을 정답보다 크게 만들어 정답의 상대 `ts_rank` 순위를 낮췄다.** v2p01은
원문 한글 score lexeme이 0개라 가중치 크기조차 관여하지 않았다. `search_tsv`가 늘린
variant-only 후보 한 건이 0점 tie와 top-50 cutoff를 거쳐 RRF 경쟁자를 추가한 별도
2차 경로다.

## 2. 증거와 격리 방법

### 2.1 원본 결과 — 최종 순위의 기준

원본 gate96 네 실행은 shared DB `rrfeval_1b75828f`, index fingerprint
`126210e9bc264e7a511cc2b7847407ca605049c30ea8a4f6904c809709b07d33`, query SHA-256
`a325583905a624c4e8293b7abff49e65741bc4aa6d0e09e48d5ed74bfa0346e5`를 공유했다.
따라서 아래 최종 RRF 변화는 재색인 드리프트가 아니라 lexical field 교체에 귀속된다.

| pair/role | variants | text fallback | structured fallback | text RRF | structured RRF |
|---|---|---:|---:|---:|---:|
| v2p01 root `v2g023` | OFF | miss | miss | miss | miss |
| v2p01 root `v2g023` | ON | miss | miss | 5 | **6** |
| v2p01 child `v2g024` | OFF | miss | miss | miss | miss |
| v2p01 child `v2g024` | ON | miss | miss | 9 | 6 |
| v2p03 root `v2g044` | OFF/ON 동일 | miss | 3 | 4 | 2 |
| v2p03 child `v2g045` | OFF/ON 동일 | 1 | miss | 1 | **10** |
| v2p07 root `v2g079` | OFF/ON 동일 | 2 | miss | 1 | **4** |
| v2p07 child `v2g080` | OFF/ON 동일 | 1 | 1 | 1 | 1 |

여기서 fallback `miss`는 top-10 밖을 뜻한다. keyword arm 자체의 후보 폭은 50이다.

### 2.2 postmortem 재현 — arm과 lexeme의 기준

82번 처리에서 원본 DB를 정리했으므로, 같은 product bytes와 query/corpus bytes로 별도
ephemeral shared DB를 한 번 만들고 여섯 gate 질의만 읽었다.

- product diff: `git diff 468ffaf HEAD -- app alembic` 비어 있음
- query SHA-256: 원본과 같은 `a3255839…46e5`
- corpus SHA-256: stripe `3653ad45…`, github `80850db2…`
- endpoint chunk: stripe 589 + github 1220 = 1809
- diagnostic index fingerprint: `cced3f9524b707570625038d21634feb2054d7edf6b2897652ca0261364c5c4d`
- 실행 후 ephemeral DB `rrfeval_b02fa6c7` drop 완료

새 fingerprint는 endpoint ID 재해시와 embedding 재생성 때문에 원본과 다르다. verdict 70·77의
귀속 한계에 따라 **최종 RRF incident rank는 §2.1 원본 로그만 사용**하고, 재현 DB는 동일
인덱스 안의 text↔structured keyword rank·lexeme·field score 분해에만 사용했다.
v2p03·v2p07의 keyword/RRF 변화는 원본과 정확히 재현됐다. v2p01의 0점 tie 최종 위치는
재색인 ID 순서에 따라 달라졌으며, 이 비결정성 자체가 §5의 원인 일부다.

진단의 field-only `ts_rank`는 각 A/B/C 필드가 가진 매치 강도를 보여주는 보조값이다.
`ts_rank`는 빈도 포화가 있는 비선형 함수이므로 A+B+C+D field-only 값을 합산해 full
`ts_rank`로 해석하지 않는다.

## 3. gate-only arm 격리 결과

variants가 없는 v2p03·v2p07에서 벡터 arm은 field 교체와 무관하게 동일했다.

| query | keyword text→structured | vector rank | diagnostic RRF text→structured | 원본 RRF |
|---|---:|---:|---:|---:|
| v2g044 p03 root | 37→3 | 1 | 4→2 | 4→2 |
| v2g045 p03 child | 1→18 | 3 | 1→10 | 1→10 |
| v2g079 p07 root | 2→15 | 1 | 1→4 | 1→4 |
| v2g080 p07 child | 1→1 | 1 | 1→1 | 1→1 |

RRF 식 `1/(60+r_keyword) + 1/(60+r_vector)`를 적용하면 회귀 경로가 직접 보인다.

- v2g045 정답: `(kw=1, vec=3) = 0.03226646` → `(kw=18, vec=3) = 0.02869353`.
  parent·payment sibling 여러 건이 양 arm에 들어오면서 최종 1→10이 됐다.
- v2g079 정답: `(kw=2, vec=1) = 0.03252247` → `(kw=15, vec=1) = 0.02972678`.
  `/tax_ids` 두 GET item이 `(kw=1, vec=3)`·`(kw=2, vec=2)`로 정답을 앞섰고 최종
  1→4가 됐다.

RRF는 target의 vector rank 3·1을 보존했지만 keyword rank 손실을 상쇄하지 못했다.
반대로 decoy가 structured keyword와 vector 양쪽에 있으면 두 항이 더해져 target의
vector-only 강점까지 넘어섰다.

## 4. v2p03 child 1→10 — parent를 A로 소유한 문서가 이겼다

질의는 `list what is being sold through that payment link`이고 정답은
`GET /v1/payment_links/{payment_link}/line_items`다. 질의는 child leaf인 `line_items`를
직접 말하지 않고 parent인 `payment link`와 operation인 `list`로 child를 우회 지시한다.

### 4.1 정답의 field 배치

| field | 정답에서 매치한 score lexeme | field-only `ts_rank` | 의미 |
|---|---|---:|---|
| A leaf (1.0) | 없음 | 0 | 질의에 `line`·`items`가 없음 |
| B intent (0.4) | `list`, `payment`, `link` | 0.0810569 | collection alias + summary |
| C context (0.2) | `payment`, `link` | 0.0352748 | parent path가 context로 강등 |
| D text (0.1) | `is`, `list`, `payment`, `link` | baseline full 0.0347006 | 기존 정답 근거 |
| A+B+C+D | — | 0.1035927 | 절대 점수는 상승 |

### 4.2 위로 올라온 문서

| structured keyword rank | endpoint | 핵심 매치 | full `ts_rank` |
|---:|---|---|---:|
| 1 | `GET /v1/payment_links` | A=`payment`,`link`; B=`list`,`payment` | 0.2136047 |
| 2 | `POST /v1/payment_links/{payment_link}` | A=`payment`,`link`; B=`payment`,`link` | 0.1830774 |
| 3 | `GET /v1/payment_links/{payment_link}` | A=`payment`,`link`; B=`payment`,`link` | 0.1828672 |
| 4 | `POST /v1/payment_links` | A=`payment`,`link`; B=`payment`,`link` | 0.1826650 |
| 5~8 | payment records/configurations/domains siblings | A=`payment`; B=`list`,`payment` | 0.1287~0.1294 |
| 18 | **정답 child** | A 없음; B=`list`,`payment`,`link`; C=`payment`,`link` | 0.1035927 |

정답이 D의 완전한 문맥으로 baseline 1위였어도, structured rank에서는 질의가 말한 parent
명사 두 개를 leaf A에 가진 root가 이겼다. A:D의 명목 가중비는 10:1이고 A:C는 5:1이다.
document-local한 “leaf가 중요하다”는 규칙이 query-local한 “parent를 말해 child를 찾는다”는
의도를 식별하지 못했다.

같은 원리로 v2g044 root는 text keyword 37위에서 structured 3위로 개선됐다. 즉 하나의
weight 배치가 p03 root에는 맞고 child에는 반대로 작동했다. 이것이 root gain과 child loss가
같은 pair에서 동시에 난 직접 이유다.

## 5. v2p07 root 1→4 — `tax_ids`가 `tax + id` 두 A lexeme을 얻었다

질의는 `retrieve a tax calculation by id`, 정답은
`GET /v1/tax/calculations/{calculation}`이다.

### 5.1 정답과 대표 decoy 비교

| field | 정답 `/tax/calculations/{calculation}` | decoy `GET /tax_ids/{id}` |
|---|---|---|
| A leaf (1.0) | `calculation` — 0.1013212 | `tax`,`id` — 0.2645609 |
| B intent (0.4) | `retrieve`,`a`,`calculation` — 0.1317175 | `retrieve`,`a`,`tax`,`id` — 0.1722460 |
| C context (0.2) | `tax` — 0.0202642 | 없음 — 0 |
| D text baseline | 0.0502919 | 0.0484794 |
| full structured | **0.2340026** | **0.3688652** |
| keyword rank | 2→15 | 7→2 |

`_PARAM_NOISE_SUBWORDS={'id'}`는 trailing path parameter의 `id`만 leaf 승격에서 제외한다.
literal segment `tax_ids`는 `_split_subwords`와 단수화 결과 `tax_ids tax_id tax ids id`가
되므로 `tax`와 `id`를 둘 다 A에서 받는다. GET-item alias는 모든 item endpoint에
`retrieve`를 B로 준다. summary도 `Retrieve a tax ID`라 `tax`·`id`가 B에 반복된다.

따라서 target-specific `calculation` 한 개를 A에서 맞힌 정답보다, 질의의 generic locator
`id`와 domain token `tax`를 A에서 맞힌 `tax_ids`가 더 강해졌다. `tax calculation`을 함께
요구하는 coverage나 field 간 필수 조합이 없으므로 OR rank는 이 partial match를 허용했다.
`/customers/{customer}/tax_ids/{id}`도 같은 0.3688652로 1위, 다른 `tax_ids` method·collection
endpoint들도 3~8위를 차지했다.

child 질의 `list the line items of that tax calculation`은 정답 leaf `line_items`를 직접
말하므로 A 신호가 target과 정렬되어 keyword 1→1, RRF 1→1을 유지했다. p07은 p03과 반대로
root만 손실한 사례이며, weight가 root나 child 어느 한쪽을 일관되게 선호한 문제가 아님을
보여준다.

## 6. v2p01 root ON 5→6 — 0점 structured-only 후보의 RRF 진입

원문은 `예전에 보낸 견적서 하나를 열어봐줘`, variant는
`open one quote we sent earlier`다. 구현 계약상 variant term은 boolean 후보 필터만 넓히고
`ts_rank`는 원문 term만으로 계산한다.

- score terms: `예전에`, `보낸`, `견적서`, `하나를`, `열어봐줘`
- filter-only 추가 terms: `open`, `one`, `quote`, `we`, `sent`, `earlier`
- 영문 endpoint corpus와 원문 score lexeme 교집합: 0
- filter match 수: `text_tsv` 101건, `search_tsv` 102건
- structured-only 후보: `GET /v1/quotes`

`GET /v1/quotes`의 기존 text에는 `quotes`만 있어 `simple` config가 variant의 단수 `quote`를
매치하지 못한다. structured leaf 파생은 `quotes quote`를 A에 넣으므로 이 한 건이
boolean 후보 집합에 추가된다. 하지만 점수는 원문 한글로만 계산하므로 text 후보 101건과
structured 후보 102건의 keyword `ts_rank`는 전부 0이다.

SQL 정렬은 `score DESC, chunk.id ASC LIMIT 50`이다. 원본 shared index에서 structured-only
`GET /v1/quotes`가 0점 tie의 top-50에 들어와 vector arm에도 있던 이 endpoint에 keyword
RRF 항을 추가했고, accepted root는 두 field 모두 keyword miss인 vector-only 상태에서
5→6으로 한 칸 밀렸다. 재생성 index에서는 endpoint ID 순서가 달라 이 한 칸을 재현하지
않았다. 이는 원본 동일-index A/B/C/D 실행의 5→6 귀속을 뒤집지 않고, 오히려 이 회귀가
weight 크기가 아니라 **lexeme superset + zero-score tie + top-50 cutoff + RRF**에 의존함을
확인한다.

같은 pair의 child가 원본 ON에서 9→6으로 개선된 것도 target score 상승 증거가 아니다.
양쪽 모두 keyword miss였으므로 0점 후보 membership이 vector fusion 경쟁자를 다르게 만든
결과다. 이 pair는 aggregate 순증으로 안전성을 말할 수 없는 가장 작은 예다.

## 7. A/B/C/D 배치가 만든 일반 실패 기전

| 설계 가정 | gate에서 확인된 반례 |
|---|---|
| leaf A는 target resource를 뜻한다 | child 질의가 parent로 child를 지시하면 parent/root가 A를 소유하고 정답은 C만 가진다(v2p03) |
| operation alias B는 의도를 강화한다 | `list`·`retrieve`는 많은 collection/item이 공유해 decoy에도 동일 보너스를 준다 |
| `id`는 noise로 제거된다 | param `id`만 제거되고 literal `tax_ids`의 `id`는 A로 승격된다(v2p07) |
| D에 text를 보존하면 기존 정답도 보존된다 | lexeme 집합은 보존되지만 top-50/순위는 보존되지 않는다 |
| default weights는 중립적인 출발점이다 | A:B:C:D=1:.4:.2:.1은 문서 구조의 우선순위이지 query target의 우선순위가 아니다 |
| RRF가 score scale 차이를 흡수한다 | scale은 흡수하지만 잘못 바뀐 keyword 순위와 both-arm 보너스는 그대로 증폭한다 |

lexeme superset 불변식은 “정답이 boolean 후보 전체에서 사라지지 않는다”만 보장한다.
운영 검색에는 ranker별 width 50과 최종 top-10 cutoff가 있으므로, 정답보다 많은 문서가 더
큰 boost를 받으면 실질적으로는 keyword arm에서 탈락한다. v2p03 child 1→18과 v2p07 root
2→15가 바로 그 차이다.

## 8. verdict 72/74 계열과의 공통점·차이

| 축 | verdict 72/74 keyword-variant 계열 | structured lexical v2 |
|---|---|---|
| 공통 안전 실패 | root 개선 대가로 child 회귀; aggregate gain이 pair loss를 숨김 | root/child 중 어느 쪽도 단조 보존되지 않음; aggregate MRR·nDCG gain이 pair loss를 숨김 |
| 공통 상위 원인 | target·ancestor·sibling을 같은 lexical 경쟁 안에서 구분하지 못함 | A/B/C로 표현을 늘렸지만 query가 어느 field를 target으로 삼는지 구분하지 못함 |
| 변경 위치 | query-time variant pool admission/merge | index-time field 파생 + search-time lexical arm 전면 교체 |
| activation | variants ON에서만 | p03·p07은 variants 없이 OFF/ON 공통; p01만 ON |
| 경쟁 신호 | flat text에서 variant coverage가 높은 sibling | A/B 고가중 부분 매치를 가진 root·sibling·generic-ID endpoint |
| 대표 실패 | p02 root gain, child 3→miss; coverage 수정 뒤 child 회복과 root 4→miss | p03 child kw 1→18/RRF 1→10; p07 root kw 2→15/RRF 1→4 |
| 직접 레버의 실패 | threshold·budget을 바꿔도 target selection 정보가 없음 | weight를 바꿔도 query별 leaf/context 역할이 뒤집혀 단일 전역 순서를 만들 수 없음 |
| 비결정성 경계 | variant pool과 재색인 drift | p01의 0점 tie가 endpoint ID/top-50 cutoff에 의존 |

공통점은 “lexical 후보를 더 잘 찾았다”와 “기존 정답을 보존했다”가 별개 계약이라는 것이다.
차이는 verdict 72/74가 **추가 variant 후보**로 기존 arm을 오염시켰다면, v2는 variants가
없어도 **모든 기존 lexical 후보를 새 전역 점수로 재정렬**했다는 데 있다. 따라서 v2를
“같은 variant flood 재발”로 부르면 원인 범위를 잘못 좁힌다.

또한 v1 p02 개발 게이트의 green은 structured weight 기전을 강하게 검증하지 못했다.
p02 원문은 한글이고 영어 variant는 filter-only라 target `ts_rank`가 0이었다. 당시 결과가
text↔structured 동일하고 vector-dominated였다는 사실은 06번 eval 문서에도 기록돼 있다.
v2p03·v2p07의 영어 원문이 처음으로 A/B/C score lexeme을 pair 양쪽에서 직접 행사했고,
그때 전역 weight의 비단조성이 드러났다.

## 9. 다음 후보에 넘길 제약과 교훈

아래는 구현 승인이나 수치 설계가 아니라 `text-primary + bounded structured augmentation`
후보가 만족해야 할 입력 제약이다.

1. **text primary 보존**
   - `text_tsv`의 keyword list와 기존 hit의 arm rank를 primary로 유지한다.
   - structured 신호로 기존 text hit를 재채점하거나 순서를 바꾸지 않는다.
   - “D를 포함했다”가 아니라 text rank 자체의 순서 보존을 불변식으로 둔다.

2. **augmentation과 replacement 분리**
   - structured는 별도 후보 목록에서 text miss를 보충하는 용도로만 둔다.
   - structured-only 후보가 기존 text 후보를 밀 수 있는 수·위치·RRF 기여 상한을 결과 열람
     전에 고정한다.
   - 무제한 제3 RRF arm은 기존 text-only hit를 다시 밀 수 있으므로 bounded의 충분조건이
     아니다.

3. **0점 후보 금지**
   - original score lexeme 기여가 0인 후보를 structured augmentation에 넣지 않는다.
   - cross-language variant를 structured에서 점수화하려면 filter-only 경로에 섞지 말고,
     별도 variant-scored arm·quota·pair gate를 정의한다.

4. **field match와 query target을 구분**
   - A leaf match 하나를 무조건 C context match보다 우선하지 않는다.
   - parent를 말해 child를 찾는 질의와 generic locator(`id`)를 명시적으로 다룰 근거가 없으면
     high-weight boost를 주지 않는다.
   - `list`·`retrieve` 같은 operation class token은 단독 admission 근거가 될 수 없다.

5. **pair 안전을 arm 단계에서 검사**
   - exposed v1/v2 root·child에 대해 text keyword rank, augmentation membership, vector rank,
     최종 rank를 각각 기록한다.
   - `delta(root)<=0 and delta(child)<=0`를 최종 RRF뿐 아니라 primary text hit 보존에도
     적용한다.
   - aggregate Recall/MRR/nDCG나 pair 반대편의 개선으로 한쪽 loss를 상쇄하지 않는다.

6. **계측을 승급 프로토콜 일부로 고정**
   - filter terms와 score terms, keyword/vector top-50, final RRF contribution, A/B/C/D 매치
     lexeme을 보존한다.
   - shared index는 postmortem arm trace가 끝나기 전에 cleanup하지 않는다.
   - score 0 tie 수와 cutoff 경계의 후보 ID를 별도 기록한다.

7. **노출셋과 sealed 승급셋 분리**
   - p02, v1, v2는 개발·회귀·원인 진단에만 사용한다.
   - 다음 제품 후보는 위 불변식을 먼저 exposed set에서 통과한 뒤 전량 신규 v3를 프리즈한다.
   - v2p01/p03/p07 token alias, weight, path 예외를 추가해 v2를 다시 맞추지 않는다.

## 10. 확정된 causal chain

```text
text_tsv 무가중 순위
  -> search_tsv(A/B/C/D)로 lexical arm 전면 교체
  -> query와 무관한 전역 field 우선순위 적용
  -> root/sibling의 A·generic B 부분 매치가 정답의 C·D 문맥보다 큰 증가
  -> keyword arm 상대순위 역전(1->18, 2->15)
  -> decoy가 vector와 양 arm을 차지
  -> RRF가 역전을 top-10에 전달
  -> p03 child 1->10, p07 root 1->4
```

v2p01은 같은 교체의 보조 경로다.

```text
structured singularization으로 `quote` 후보 1건 추가
  -> original 한글 score는 전 후보 0
  -> chunk ID tie-break + width 50 membership 변화
  -> structured-only `/quotes`가 vector+keyword 양 arm 획득
  -> vector-only accepted root 5->6
```

따라서 incident의 기전은 “구조 신호가 부족했다”가 아니라 **구조 신호를 기존 text rank의
보존 장치 없이 단일 전역 rank로 합쳐 primary lexical arm을 교체했다**는 것이다. 다음
후보의 출발점은 weight 재튜닝이 아니라 text primary의 비간섭 보존과 structured 기여의
명시적 경계여야 한다.
