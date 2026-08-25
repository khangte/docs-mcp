# 54. 비즈니스 메타데이터 1b 실험 판정 및 2단계 승인

- 판정일: 2026-08-25
- 대상: [53](53_business_metadata_stage1_gate_verdict.md) §4가 지시한 1b 실험(arm A = HTML strip only, arm B = strip + 300자 절단) 결과
- 측정 수행: developer (`chunk_builder.py` 단독 수정, 각 arm 측정 후 원복, 워킹트리 clean)
- **판정: arm B 채택. 게이트 통과. 2단계(스키마 마이그레이션 + 옵셔널 주입) 착수 승인.**
- 부수 판정: 53 §4 판정표는 폐기한다(아래 §1 사유). fallback 갈래 회귀는 별건 결함으로 분리한다.

---

## 1. 53 §4 판정표가 세 행 어디에도 맞지 않은 이유 — 판정표가 틀렸다

판정표는 `fallback` 과 `rrf` 를 동등 가중으로 놓고 "두 전략 모두에서 회복"을 요구했다. 이것이 오류다.

- `app/core/config.py:37` / `app/composition.py:59` — **운영 기본값은 `search_strategy="rrf"` 다.**
- `app/services/search/endpoint_candidate_search.py:8,108` — `fallback` 은 스스로 문서에
  "**롤백 스위치**"라고 적혀 있다. 키워드를 먼저 돌리고 결과가 0건일 때만 벡터를 부른다.

즉 `fallback` 은 운영 경로가 아니라 벡터 검색을 끄는 비상 경로다. 두 갈래를 동등 가중으로 놓고
"둘 다 회복해야 통과"를 요구한 것은 **비상 경로의 회귀로 운영 경로의 개선을 막는** 기준이었다.
판정표를 폐기하고 rrf 기준으로 재채점한다.

부수 소득: `fallback` ≈ 렉시컬 단독, `rrf` = 렉시컬+벡터 융합이므로, 이 두 축은
52 §(a) 조건 2가 요구한 "벡터 갈래 / 키워드 갈래 분리 관측"을 근사적으로 충족한다.
53 §5에서 "조건 2 미충족"이라고 적은 것은 정정한다 — 하네스 추가 없이 이미 관측되고 있었다.

## 2. rrf(운영 경로) 기준 재채점 — 통과

| 지표 | 기준선 | arm B | 판정 |
|------|--------|-------|------|
| 집계 R@3 (variants off) | 30% | 35% | 개선 |
| 집계 R@3 (variants on) | 35% | 40% | 개선 |
| 집계 MRR (variants off) | .303 | .318 | 개선 |
| 집계 MRR (variants on) | .367 | .352 | -.015 |
| C5 R@3 / MRR | 67% / .556대 | 67% / .556 | 회복 |
| C7 R@3 | 0% | 33% | 개선 |

**recall@3은 두 조건 모두 상승, MRR은 한쪽 상승·한쪽 -.015.** n=20에서 MRR .015 차이는 질의 한 건이
한 칸 움직인 정도이며, 53 §2(b)에서 세운 기준("집계값만으로 판정하지 않는다, 건별로 본다")을 그대로
적용하면 이것을 근거로 반려할 수 없다. 건별로는 q14가 미검출 → rank 1, q20이 4~5위 → 2위,
q19가 미검출 → 5위(variants off)로 올라왔다. 하락으로 뒤집힌 건은 q13 하나다.

arm A는 arm B에 모든 축에서 열세다. **HTML strip 단독으로는 부족하고 길이 절단이 실제 이득의 원천이다.**
53 §2(c)의 설명(대형 청크에서 장문 산문이 구조 필드를 480토큰 밖으로 밀어내고 있었다)과 일치한다.

## 3. fallback 갈래 회귀는 회귀가 아니다 — `ts_rank` 정규화 미설정 때문이다

fallback 은 두 arm 모두, 4개 조건 모두에서 기준선 대비 하락했다. 원인을 코퍼스에서 직접 확인했다.

`app/repositories/chunk_repository.py:180` 은 `ts_rank(Chunk.text_tsv, score_tsq)` 를
**normalization 인자 없이** 호출한다. Postgres 기본값은 0 = **문서 길이 정규화 없음**이다.
즉 점수가 매칭 term 의 절대 출현 횟수에 비례한다. 텍스트를 줄이면 정답 청크의 점수가 기계적으로 내려간다.

q13(`delete a subscription`, 정답 `DELETE /v1/subscriptions/{subscription_exposed_id}`)에서 실측:

| 청크 | 전체 desc: delete/a/subscription 출현 (총 토큰) | 300자 절단 후 |
|------|--------------------------------------------|--------------|
| 정답 `DELETE /v1/subscriptions/{...}` | 1 / 3 / 10 (177) | 1 / 2 / 4 (54) |
| 오답 `DELETE /v1/subscription_items/{item}` | 2 / 3 / 5 (26) | 2 / 3 / 5 (26) — **불변** |

오답은 description 이 짧아 절단의 영향을 받지 않고 출현 횟수를 그대로 유지한다. 정답만
`subscription` 10회 → 4회로 깎인다. 정규화가 없으므로 **절단은 긴 정답만 처벌한다.**
q13이 arm A·B 어디에서도 부활하지 않고 rank 6~9를 오간 것이 이것으로 설명된다.

역으로, **기준선에서 q13이 rank 1이었던 것도 상당 부분 이 편향의 산물이다** — 정답이 의미적으로
가장 잘 맞아서가 아니라 매칭 term 을 가장 많이 담은 긴 문서였기 때문이다.
`fallback` 갈래는 현재 "질의어를 가장 많이 반복하는 긴 청크"를 상위에 올리는 성질을 갖고 있고,
따라서 **청크 텍스트를 줄이는 어떤 실험도 이 갈래에서는 구조적으로 나쁘게 나온다.**

결론: fallback 하락은 arm B의 결함이 아니라 측정 편향이다. 이 갈래 수치로 1b를 반려하지 않는다.
다만 정규화 미설정 자체는 실재하는 검색 품질 결함이므로 별건으로 분리한다(§5).

참고로 `to_tsvector('simple', ...)` 는 불용어를 제거하지 않아 `a` 같은 term 도 lexeme 이 된다
(q13 정답 청크에서 `a` 3회가 점수에 그대로 들어간다). 지금은 지배적 요인이 아니어서 지시하지 않는다.
§5 측정에서 정규화만으로 해결되지 않으면 그때 다룬다.

## 4. 2단계 승인 — 53 §3의 보류 사유가 해소됐다

53 §3에서 2단계를 막은 이유는 하나였다: 대형 엔드포인트 청크가 이미 480토큰에서 잘리는데
`Keywords:` / `Phrases:` 를 header 직후에 넣으면 C7을 살린 `Body:` 줄이 밀려난다는 것.

**arm B가 description 을 300자로 묶으면서 이 충돌이 사라진다.** 절단 예산에 여유가 생겼고,
실제로 C7이 arm B에서 0% → 33% 로 올라온 것이 그 여유가 구조 필드에 돌아갔다는 증거다.
따라서 52 §(3)이 확정했던 배치를 **잠정 기본값으로 복원한다** — 최종 확인은 4단계 A/B에서 한다.

### 2단계 범위 (developer 착수 가능)

1. `build_endpoint_chunk_text` 에 arm B 정리 로직 정식 반영 — HTML 태그 제거 + description 300자 절단.
   실험용 원복분을 코드로 확정하는 것이다.
2. `endpoint_business_metadata` 테이블 마이그레이션 — 스키마·키·FK 조건은 52 §(b) 보완 지시 그대로다
   (`api_endpoint` 에 FK 금지, `(document_id, method, path)` 키, `document_id` 만 FK+cascade).
3. `build_endpoint_chunk_text` / `build_chunks` 옵셔널 주입 — 52 §(2) 그대로.
   `ParsedEndpoint` 필드 추가는 반려 상태 유지.
4. 이 시점에 메타는 비어 있으므로 **측정치가 arm B와 동일해야 한다.** 회귀 확인용으로 4조건 재측정하고
   arm B 수치와 대조해 보고한다. 달라지면 주입 경로에 버그가 있는 것이다.

LLM SDK 는 2단계에 들어오지 않는다(52 §(c) 조건 — 3단계 CLI에서 optional extra 로).

## 5. 별건 분리: `ts_rank` 정규화 A/B

2단계와 병렬로 진행 가능하며, **4단계 최종 A/B 전에는 결론이 나야 한다** — 최종 성공 기준이
두 전략을 함께 보기 때문이다.

- 대상: `app/repositories/chunk_repository.py:180` 의 `func.ts_rank(...)` 에 normalization 인자 추가.
- 측정: normalization 0(현행) / 1(`1+log(length)` 로 나눔) / 32(`rank/(rank+1)`) / 1|32 를
  코퍼스 하네스로 비교. 두 전략 4조건 + 카테고리별 + q13 건별 rank.
- 주의: 정규화 2(길이로 직접 나눔)는 짧고 조밀한 오답(`DELETE /v1/subscription_items` 는 26토큰에
  매칭 10회)을 오히려 밀어올린다. 후보에서 제외한다.
- 이 과제는 business metadata 범위가 아니다. README 검색 품질 수치의 근거가 되는 랭킹 자체의 문제다.

## 6. q13은 business metadata 의 타깃 케이스로 넘긴다

q13이 정리만으로 살아나지 않은 것은 §3의 측정 편향 외에 실질적 이유가 하나 더 있다 —
**정답의 summary 는 "Cancel a subscription" 인데 사용자는 "delete a subscription" 이라고 묻는다.**
어휘가 다르고, 청크 텍스트 어디에도 "delete a subscription" 이라는 표현이 없다
(`[DELETE]` 는 메서드 토큰일 뿐이다).

이것이 정확히 `user_phrases` 가 메우려는 갭이다. 청크 텍스트를 어떻게 정리하든 없는 표현은
만들어지지 않는다. q13은 1b의 실패 사례가 아니라 **3단계에서 검증할 대표 케이스**로 이관한다.
3단계 메타데이터 생성 프롬프트는 summary 의 동의 표현(cancel/delete/remove 류)을 `user_phrases` 에
포함하도록 지시한다.
