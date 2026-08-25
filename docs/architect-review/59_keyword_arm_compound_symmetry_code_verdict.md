# 59. 개선 #4(T1+T2) 구현 검토 판정

- 작성: architect, 2026-08-26
- 대상: 워킹트리 변경분 — `app/services/documents/search_scorer.py`,
  `app/repositories/chunk_repository.py`, `app/services/documents/document_search_service.py`
  (+ 테스트 3종)
- 설계 기준: `docs/architect-review/58_keyword_arm_compound_symmetry_design.md`

**판정: 수정필요 (must 1건 + should 3건 + minor 1건). 수정 후 reviewer 배정.**

---

## 1. 설계 준수 확인 (통과 항목)

- **T1** — `_collapse_match_score` 가 `_token_aligned_concat_match` 위임으로 교체됨(58 §3.1).
  `_title_score` 의 `collapse(title) + collapse(url)` 이어붙이기가 title/url 개별 판정 후
  `max` 로 분리됨(§3.2). `_body_score` 는 토큰 리스트를 한 번만 만들어 재사용(§3.4).
  `_match_positions` 무변경(§3.3). `collapse` import 유지.
- **T2 저장소** — `phrase_terms`/`score_phrase_terms` 기본값 `None`, `None` 이면 tsquery 문자열이
  기존과 동일(§4.3). `_quote_tsquery_lexeme` 를 phrase 조각에도 적용. 빈 원소 그룹 폐기.
  `terms` 가 비고 phrase 만 있어도 동작하도록 조기 반환 조건을 함께 고침. 저장소 테스트는
  회귀·인용 이스케이프·`score_phrase_terms=[]` 억제까지 덮는다.
- **T2 서비스** — `filter_tokens`/`score_tokens` 집합은 그대로 두고 순서가 필요한 파생만
  `_keyword_arm` 안에서 만든다(§4.4). variant 는 문자열별로 개별 토큰화해 파생한다
  (원본↔variant 가로지르는 concat 없음). title/vector arm 무변경.
- 마이그레이션·재색인·새 인덱스 없음 — 설계대로다.

---

## 2. 수정 요청

### F1 (must) — variant 파생 phrase 가 점수 계산에 샌다

`document_search_service._keyword_arm`:

```python
score_phrase_terms=phrase_terms or None,
```

`phrase_terms`(원본 질의 파생)가 **빈 리스트면 `or None` 이 `None` 으로 바꾼다.** 저장소는
`score_phrase_terms is None` 을 "필터용 `phrase_terms` 를 그대로 점수에도 쓴다"로 해석하므로,
그 순간 **variant 파생 phrase 가 `ts_rank` 에 섞인다.**

재현 조건이 드물지 않다: 원본 질의 `'결제 장애'` → 토큰 `['결제','장애']` 는 둘 다 길이 2 라
`compound_split_phrases` 산출이 빈 리스트. variant `'결제장애'` → `('결제','장애')` 생성.
결과적으로 variant 로만 걸린 문서가 원본 매칭 문서보다 높은 keyword arm 순위를 받을 수 있다 —
58 §4.4 와 기존 `score_terms` 규약(variant 는 후보만 넓히고 점수엔 넣지 않는다)의 정면 위반이다.

**수정:** `or None` 을 떼고 빈 리스트를 그대로 넘긴다.

```python
score_phrase_terms=phrase_terms,
```

저장소는 `[]` 를 `None` 과 구분해 "phrase 없음"으로 처리하며, 그 경로는 이미
`test_search_endpoint_by_text_score_phrase_terms_scores_independently` 가 덮고 있다.
`phrase_terms=filter_phrase_terms or None` 쪽은 `None` 과 `[]` 의 의미가 같아 그대로 둬도 된다.

**테스트 추가:** 서비스 레벨에서 "원본 질의는 split 을 못 만들고 variant 만 만드는" 케이스로
`score_phrase_terms` 가 비어 전달되는지 확인할 것.

### F2 (should) — 스크립트 경계를 넘는 concat term 은 매치될 수 없는 죽은 term 이다

`compound_concat_terms` 는 인접 토큰이면 무조건 이어붙인다. 그런데 `TEXT_TSV_EXPRESSION` 은
ASCII/언더스코어 ↔ 한글 경계에 **공백을 삽입한 뒤** tsvector 를 만든다. 즉 본문 lexeme 은
언제나 순수 ASCII 계열이거나 순수 한글이며, `'get요청'` 같은 혼합 lexeme 은 **존재할 수 없다.**
질의 `'GET 요청'` 이 만드는 concat term `'get요청'` 은 어떤 문서와도 매치되지 않는다.

해가 되진 않지만 tsquery operand 와 `COMPOUND_TERM_LIMIT` 예산을 확실히 낭비한다.

**수정:** run 안의 모든 토큰이 같은 스크립트 부류(전부 순수 한글 / 전부 그 외)일 때만 concat 한다.
`_PURE_HANGUL_RE` 를 그대로 재사용하면 된다.

### F3 (should) — 캡이 variant 마다 따로 걸려 전체 상한이 없다

`compound_terms_for_tokens` 를 원본과 각 variant 에 **개별 호출**하므로 실제 상한은
`COMPOUND_TERM_LIMIT × (1 + variant 수)` 다. `query_variants` 는 개수·길이 검증이 없어
(`_validate` 는 top_k 만 본다) 호출자가 넉넉히 넣으면 tsquery operand 가 그만큼 늘어난다.
캡을 둔 이유 자체가 "질의 길이에 상한이 없어서"였으므로 지금 상태는 그 의도를 만족하지 못한다.

**수정:** 파생 term 을 원본 → variant 순으로 누적하면서 **합계 기준**으로
`COMPOUND_TERM_LIMIT` 를 적용한다(원본 파생이 먼저 예산을 가져간다).

### F4 (should, F3 과 같은 자리) — variant 간 중복 phrase 그룹이 제거되지 않는다

`filter_phrase_terms.extend(variant_phrase)` 는 중복을 보지 않는다. variant 두 개가 같은 조각을
내면 같은 `('a' <-> 'b')` 절이 tsquery 에 두 번 들어간다. 결과는 같지만 절 수만 늘어난다.
F3 수정 시 순서를 보존하는 중복 제거를 함께 넣을 것.

### F5 (minor) — concat 생성이 캡보다 먼저 전량 전개된다

`compound_concat_terms` 는 길이 2..n 의 모든 run 을 만든 뒤 `compound_terms_for_tokens` 가
자른다. 토큰 수 n 에 대해 run 수는 O(n²), 총 문자량은 O(n³) 이라 질의가 비정상적으로 길면
캡을 걸기 전에 이미 메모리·CPU 를 쓴다. 실사용 질의(수~수십 토큰)에선 무시할 수준이지만,
캡의 목적이 "상한 없는 질의 길이 방어"인 만큼 생성 단계에서 끊는 편이 일관적이다.

**수정(선택):** 생성 함수에 상한 인자를 받아 도달 즉시 중단한다. F3 수정과 함께 처리하면 자연스럽다.

---

## 3. 판정 요약

| 항목 | 판정 |
| --- | --- |
| T1 (`_collapse_match_score` 토큰 경계 정렬) | 승인 — 설계대로 |
| T2 저장소(`phrase_terms` 배선·무변경 규약) | 승인 — 설계대로 |
| T2 서비스(점수/필터 분리) | **수정필요 — F1** |
| T2 파생 term 생성 규칙 | 수정필요 — F2·F3·F4·F5 |

F1 만이 동작(순위) 결함이고 나머지는 낭비·상한 문제다. 다섯 건 모두
`_keyword_arm` 과 `search_scorer.py` 의 파생 term 생성부에 국한되며, 저장소·T1 은 손대지 않는다.
수정 후 커밋 분리 방침(T1 / T2)은 그대로 유지한다.

---

## 4. 재검토 결과 (2026-08-26, developer 수정 후)

**판정: 승인. reviewer 배정 가능.**

| 항목 | 확인 |
| --- | --- |
| F1 | `score_phrase_terms=phrase_terms`(빈 리스트 그대로 전달). 저장소는 `[]` 를 `None` 과 구분해 "phrase 없음"으로 처리하므로 variant 파생 phrase 가 `ts_rank` 에 섞이지 않는다. 수정 전 재현 → 실패 확인 후 복구까지 밟았다. |
| F2 | `_same_script_run` 게이트로 스크립트 경계를 넘는 run 을 만들지 않는다. |
| F3 | `compound_terms_for_tokens(tokens, limit)` + `_keyword_arm` 의 `remaining_budget` 누적 배분. 원본이 먼저 예산을 쓰고 variant 는 남은 만큼만 쓰며, 소진되면 루프를 빠져나간다 — variant 수와 무관하게 전체 상한이 `COMPOUND_TERM_LIMIT` 로 고정된다. |
| F4 | `seen_phrase_terms` 로 순서 보존 dedupe. |
| F5 | 생성 함수가 `limit` 도달 즉시 반환한다(전량 전개 후 자르기 폐기). |
| 범위 | `chunk_repository.py` 와 T1 부분은 무변경 — 지시대로 손대지 않았다. |

`filter_terms |= set(variant_concat)` 은 이미 있던 term 과 겹쳐도 예산을 소비한 것으로
계산한다 — 상한 방향으로 보수적이라 그대로 둔다.

### F6 (잔여, 선택 후속 — 블로커 아님)

F5 의 조기 중단은 **term 이 실제로 생성될 때만** 발동한다. 스크립트가 번갈아 나오는 병적인
질의(`'가 a 가 a …'`)는 모든 run 이 `_same_script_run` 에서 걸러져 `terms` 가 차지 않으므로
조기 중단이 걸리지 않고, run 열거 자체가 토큰 수에 대해 O(n^2)(스크립트 판정까지 합치면
O(n^3) 비교) 남는다. 문자열을 만들지 않으므로 F5 가 막으려던 메모리 폭증은 사라졌고,
실사용 질의(수~수십 토큰)에서는 무시할 수준이다.

`query` 길이 검증이 여전히 없다는 점만 남으므로, 정리한다면 토큰별 스크립트 판정을 한 번만
계산해 두고(또는 파생에 넣는 토큰 수 자체에 상한을 두고) run 검사를 O(1) 로 만드는 정도면
충분하다. reviewer 지적 사항이 생기면 그때 함께 처리하는 것을 권한다 — 이것만으로 별도
수정 라운드를 돌릴 가치는 없다.
