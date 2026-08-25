# 58. keyword arm 한글 복합어 대칭 설계 (57번 §5 개선 #4 + §5.3 파생항목)

- 작성: architect, 2026-08-26
- 근거 문서: `docs/architect-review/57_gdrive_search_logic_comparative_analysis.md` §5 개선 #4, §5.3
- 대상 파일: `app/services/documents/search_scorer.py`,
  `app/repositories/chunk_repository.py`, `app/services/documents/document_search_service.py`

---

## 1. 문제

문서 검색의 세 arm 중 **본문 keyword arm 만 복합어 띄어쓰기 변형을 흡수하지 못한다.**

- title arm: `search_by_tokens` 가 `collapse(title)`/`collapse(url)` 에 대해 `collapse(query)`
  패턴을 ILIKE 로 추가 매칭한다 → `'결제장애'` ↔ `'결제 장애'` 양방향 매칭.
- keyword arm: `chunk.text_tsv` 는 `TEXT_TSV_EXPRESSION` 이 공백/기호를 경계로 잘라 만든
  lexeme 집합이고, 질의는 `documents_tokenize` 가 같은 규칙으로 자른 토큰이다.
  두 쪽 다 **공백을 경계로 인정**하므로:
  - 질의 `'결제장애'`(붙여씀) → lexeme `'결제장애'` 하나. 본문이 `'결제 장애'` 면 미스.
  - 질의 `'결제 장애'`(띄어씀) → lexeme `'결제'|'장애'`. 본문이 `'결제장애'` 면 미스.

한국어 문서 기반 시스템에서 recall 구멍 중 가장 자주 발생하는 축이다.

같은 뿌리의 두 번째 문제가 §5.3 에 미착수로 남아 있다. `_collapse_match_score` 는
`collapse(query) in collapsed_haystack` **부분문자열** 판정이라 토큰 경계를 모른다 —
질의 `'api'` 가 제목 `'Rapid Onboarding Guide'` 에 대해 `1/1 = 1.0`(만점)을 반환한다.
`_title_score`·`_body_score` 양쪽과 fetch 전략의 후보 순서에 그대로 걸린다.

---

## 2. 결론 (설계 확정)

두 항목을 **한 작업으로 묶되 커밋은 분리**한다.

1. **T1** — `_collapse_match_score` 를 토큰 경계 기준으로 교체 (§5.3 파생항목).
2. **T2** — keyword arm 에 **질의 측 복합어 분해**를 넣어 대칭을 확보 (개선 #4).

개선 #4 의 두 후보 중 **질의 측 분해**를 채택하고 `text_collapsed` 생성 컬럼 + trgm 은
채택하지 않는다. 근거는 §5.

묶는 이유: T1 을 먼저 고치면 fetch 전략(`_title_candidates`/`_body_score`)이 **공짜로**
양방향 복합어 대칭을 얻는다(§3.3). 즉 T1 = 파이썬 경로의 대칭, T2 = SQL 경로의 대칭이고
둘을 합쳐야 "복합어 매칭은 어느 경로로 들어와도 같은 기준"이 성립한다. T1 을 남겨두면
게이트(`_passes_title_gate`, 토큰 경계 존중)와 점수(`_collapse_match_score`, 부분문자열)가
같은 arm 안에서 서로 다른 기준으로 판정하는 상태가 유지된다.

커밋을 나누는 이유: T1 은 **순위**를 바꾸고 두 전략 모두에 걸리므로, T2 의 recall 변화와
섞이면 회귀 원인 추적이 어렵다.

---

## 3. T1 — `_collapse_match_score` 토큰 경계 정렬

### 3.1 변경

`_token_aligned_concat_match(query, haystack_tokens)` 는 이미 있고(개선 #3 에서 게이트용으로
도입) 테스트도 있다. 점수 함수를 이 판정 위로 옮긴다.

```python
def _collapse_match_score(query: str, haystack_tokens: Sequence[str], token_count: int) -> float:
    """공백 변형을 흡수하되 토큰 경계를 존중하는 보수적 점수."""
    if not _token_aligned_concat_match(query, haystack_tokens):
        return 0.0
    return 1 / token_count
```

- 시그니처가 `collapsed_haystack: str` → `haystack_tokens: Sequence[str]` 로 바뀐다.
- 점수 값(`1/token_count`)과 `max(token_score, collapsed_score)` 합성 규칙은 그대로다.
  상한을 더 낮추는 안(§5.3 의 대안)은 채택하지 않는다 — 경계를 존중하는 순간 그 점수는
  "토큰 1개가 실제로 겹친 것"과 동등한 신호라 낮출 근거가 없다.

### 3.2 `_title_score` 의 title+url 이어붙이기도 함께 고친다

현재 `collapse(row.title) + collapse(row.url)` 로 **두 문자열을 이어붙인 뒤** 부분문자열을 본다.
이러면 title 끝과 url 앞을 걸쳐 매치되는 유령 매치가 가능하다. `_passes_title_gate` 는 이미
title 토큰열과 url 토큰열을 따로 본다 — 점수도 같게 맞춘다.

```python
haystack_title = documents_tokenize(row.title)
haystack_url = documents_tokenize(row.url)
collapsed_score = max(
    _collapse_match_score(query, haystack_title, len(query_tokens)),
    _collapse_match_score(query, haystack_url, len(query_tokens)),
)
```

토큰 집합(`overlap` 계산)은 기존대로 title ∪ url 합집합을 쓴다(경계를 넘지 않는 판정이라 무해).

### 3.3 파급 (의도된 것)

- **`_title_arm`**: 게이트와 점수가 같은 기준이 된다. 게이트를 통과한 행이 다른 토큰
  때문에 통과했을 뿐인데 collapse 만점을 받던 왜곡이 사라진다.
- **fetch 전략(`_select_candidates`/`_rank_with_body`)**: `_body_score` 가
  `_token_aligned_concat_match(query, documents_tokenize(body))` 를 쓰게 되어
  **양방향 복합어 대칭을 자동으로 얻는다.**
  - 질의 `'결제장애'` / 본문 토큰 `['결제','장애','대응']` → 연속 부분열 일치 → 통과.
  - 질의 `'결제 장애'` / 본문 토큰 `['결제장애']` → target `'결제장애'`, 경계 {0,4} 일치 → 통과.
- **recall 손실**: 부분문자열로만 걸리던 매치는 점수 0 이 된다. 개선 #3 게이트와 같은 성격의
  의도된 precision 교환이다.
- **`_match_positions`(스니펫 위치)는 건드리지 않는다.** 이쪽은 토큰별 collapse `find` 라
  더 느슨하다 — 점수가 엄격해지는 방향이므로 "점수는 매치인데 스니펫 위치가 없다"는
  불일치는 생기지 않는다(느슨한 쪽이 항상 상위집합). 반대 방향(점수 0 인데 스니펫이 잡히는
  토큰)은 기존에도 있었고 무해하다.

### 3.4 성능

`_body_score` 는 이미 `documents_tokenize(body)` 와 `collapse(body)` 를 둘 다 만들고 있었다.
새 경로는 토큰 리스트 하나만 쓰고 그것으로 joined 문자열+경계 집합을 만든다 — 같은 O(len(body)).
토큰화 결과를 리스트로 한 번만 만들어 `set(...)` 과 concat 판정에 함께 쓴다.

---

## 4. T2 — keyword arm 질의 측 복합어 분해

### 4.1 두 방향, 두 수단

| 미스 방향 | 예 | 수단 |
| --- | --- | --- |
| 질의 띄어씀 / 본문 붙여씀 | 질의 `'결제 장애'`, 본문 `'결제장애'` | **concat term**: 인접 토큰 연속 run 을 이어붙인 lexeme `'결제장애'` 를 OR 로 추가 |
| 질의 붙여씀 / 본문 띄어씀 | 질의 `'결제장애'`, 본문 `'결제 장애'` | **split phrase term**: 2분할 후보를 tsquery 구문 연산자 `<->` 로 묶어 OR 로 추가 (`'결제' <-> '장애'`) |

분할은 사전 없이 정확한 경계를 알 수 없으므로 **가능한 2분할을 전부** 넣는다. 인접성을
`<->` 로 강제하므로 엉뚱한 분할(`'결' <-> '제장애'`)은 사실상 매치되지 않는다 — 매치된다면
그건 본문에 실제로 그 두 조각이 붙어 있다는 뜻이라 오탐이라 보기 어렵다.

### 4.2 순수 함수 (`search_scorer.py`)

```python
#: 질의 하나에서 파생할 수 있는 복합어 term 총 개수 상한(concat + split 합산).
#: 질의 길이에 상한이 없어(_validate 는 top_k 만 본다) 토큰이 많으면 tsquery 가
#: 폭증할 수 있어 캡을 둔다. 평가셋이 없어 근거 있는 값이 아니므로 모듈 상수 고정,
#: env 미노출(RRF_K·TITLE_ARM_WEIGHT 와 같은 방침).
COMPOUND_TERM_LIMIT = 32
#: 2분할 시 양쪽 조각의 최소 길이(음절 1개짜리 조각은 잡음이라 만들지 않는다).
_MIN_SPLIT_PART_LEN = 2

def compound_concat_terms(tokens: Sequence[str]) -> list[str]:
    """인접 토큰의 연속 run(길이 2 이상)을 이어붙인 term 목록."""

def compound_split_phrases(tokens: Sequence[str]) -> list[tuple[str, str]]:
    """순수 한글 토큰의 2분할 후보 목록(양쪽 조각 길이 >= _MIN_SPLIT_PART_LEN)."""
```

규칙:

- `compound_concat_terms`: 입력은 **순서 있는** 토큰 리스트(`documents_tokenize` 결과). 길이
  2 이상의 모든 연속 run 을 이어붙인다(`[a,b,c]` → `ab`, `bc`, `abc`). 원본 토큰과 같은 값은
  제외하고, 순서를 보존한 중복 제거를 한다.
- `compound_split_phrases`: 토큰이 **순수 한글**(`^[가-힣]+$`)이고 길이가
  `2 * _MIN_SPLIT_PART_LEN` 이상일 때만, 양쪽 조각이 각각 `_MIN_SPLIT_PART_LEN` 이상인
  모든 2분할을 낸다. ASCII 복합어(`'apikey'` ↔ `'api key'`)는 v1 범위 밖이다 — 한국어가
  문제의 축이고, ASCII 를 열면 잡음 분할이 급증한다. 필요해지면 그때 상수 하나로 연다(YAGNI).
- 두 함수의 산출 합계가 `COMPOUND_TERM_LIMIT` 를 넘으면 잘라내고 `logging` 으로 debug 기록.
  자를 때는 concat term 을 먼저 채운다(정확 매치라 split 보다 신호가 강하다).

### 4.3 저장소 인터페이스 (`chunk_repository.search_endpoint_by_text`)

phrase 는 lexeme OR 목록으로 표현할 수 없으므로 파라미터를 추가한다.

```python
def search_endpoint_by_text(
    self,
    terms: Sequence[str],
    top_k: int,
    ...,
    phrase_terms: Sequence[Sequence[str]] | None = None,
    score_phrase_terms: Sequence[Sequence[str]] | None = None,
) -> list[ChunkTextHit]:
```

- tsquery 문자열 = (`terms` 의 인용 lexeme) + (`phrase_terms` 의 각 그룹을
  `('a' <-> 'b')` 로 만든 것) 을 ` | ` 로 결합.
- 각 조각은 반드시 `_quote_tsquery_lexeme` 를 통과시킨다(연산자 오인 방지 — 기존 규약 유지).
- 빈 문자열 원소가 든 그룹은 통째로 버린다.
- `score_tsq` 도 같은 방식으로 `score_terms` + `score_phrase_terms` 에서 만든다.
- **두 파라미터의 기본값은 `None` 이고, `None` 이면 생성되는 tsquery 문자열이 기존과 완전히
  동일해야 한다.** 엔드포인트 검색(`endpoint_candidate_search`)은 인자를 넘기지 않으므로
  점수·순서 무변경이다(개선 #2·#3 과 같은 규약).
- `terms` 와 `phrase_terms` 가 모두 비면 기존대로 빈 리스트 반환.

### 4.4 서비스 배선 (`document_search_service._keyword_arm`)

`_keyword_arm` 에 `query: str`, `query_variants: list[str] | None` 을 추가로 넘긴다
(`_search_indexed` 는 이미 둘 다 손에 갖고 있다).

- **필터 측**(`terms`/`phrase_terms`): 원본 질의 토큰 + **각 variant 문자열을 개별
  토큰화한 결과**에서 파생한다. variant 끼리, 또는 원본과 variant 를 가로질러 concat 하지
  않는다 — 서로 다른 문장이라 이어붙일 근거가 없다.
- **점수 측**(`score_terms`/`score_phrase_terms`): **원본 질의 토큰에서 파생한 것만** 넣는다.
- 파생 term 을 점수에 포함하는 것은 기존 "variant 토큰은 점수에서 제외" 규약과 충돌하지 않는다.
  variant 는 호출자(Claude)가 넣은 **다른 표현**이지만, concat/split term 은 원본 질의와
  **같은 표층 문자열의 띄어쓰기 변형**이다. 여기서 점수를 빼면 복합어로만 걸린 문서가
  keyword arm 최하위 rank 로 밀려 RRF 기여가 사실상 사라지고, 개선 #4 자체가 무력해진다.

`filter_tokens`/`score_tokens`(집합)은 그대로 두고, 순서가 필요한 파생은 `_keyword_arm`
안에서 `documents_tokenize(query)` 를 다시 호출해 만든다(질의는 짧아 비용 무시 가능,
`search()` 시그니처를 넓히지 않는 쪽이 낫다).

title arm·vector arm 은 무변경이다.

### 4.5 성능

- 새 인덱스 없음, 마이그레이션 없음, 재색인 없음. 기존 `ix_chunk_text_tsv`(GIN) 를 그대로 쓴다.
- 비용 증가는 tsquery operand 수뿐이며 `COMPOUND_TERM_LIMIT = 32` 로 상한이 걸린다.
  57번 표의 "소폭 증가(인덱스 1개 추가)" 예상보다 낮다 — 표의 수치는 생성 컬럼 안 기준이다.
- phrase(`<->`) 는 GIN 이 lexeme 으로 후보를 좁힌 뒤 위치를 확인하는 방식이라 추가 인덱스가
  필요 없다.

---

## 5. `text_collapsed` 생성 컬럼 + trgm 을 채택하지 않은 이유

1. **저장 비용.** `chunk.text` 는 섹션 본문 전체다. STORED 생성 컬럼은 그 사본을 하나 더
   들고 있으므로 시스템에서 가장 큰 테이블이 최대 2배가 된다. 여기에 긴 텍스트 대상
   trgm GIN 인덱스가 얹히면 인덱스 크기·빌드 시간·INSERT 비용이 모두 크게 늘어난다.
2. **본문에 대한 부분문자열 매칭은 제목보다 훨씬 위험하다.** title arm 에서 `'api'` 가
   `'Rapid …'` 를 잡는 문제(개선 #3, §5.3)를 본문 길이만큼 확대 재생산한다. 지금 T1 로
   제목 쪽의 그 성질을 없애는 중인데, 같은 성질을 본문에 새로 도입하는 것은 방향이 반대다.
3. **arm 내 순위 규칙이 둘로 갈린다.** trgm ILIKE 히트는 `ts_rank` 점수가 없어 별도 점수
   경로가 필요하고, 그 둘을 한 arm 안에서 어떻게 섞을지에 또 근거 없는 상수가 붙는다.
4. **되돌리기 비용.** 생성 컬럼 안은 마이그레이션 + 전량 재기록이고, 질의 측 안은 코드
   되돌리기 한 번이다. 평가셋이 없어 효과를 사전 계측할 수 없는 상황에서는 되돌리기가
   싼 쪽을 먼저 넣는 것이 맞다.

**언제 재검토하나.** 질의 측 분해로도 못 잡는 미스가 계측되면(예: 3분할 이상 복합어
`'결제 장애 대응'` ↔ `'결제장애대응'` — concat 은 잡지만 반대 방향인 질의
`'결제장애대응'` → 본문 `'결제 장애 대응'` 은 2분할만으로는 못 잡는다) 그때
3분할 확장 또는 생성 컬럼을 다시 검토한다. 지금은 계측 수단이 없어 투기적 확장이다.

---

## 6. 작업 분해 (developer 용)

### T1. `_collapse_match_score` 토큰 경계 정렬

1. `search_scorer.py`: `_collapse_match_score` 시그니처를 `haystack_tokens: Sequence[str]` 로
   바꾸고 본문을 `_token_aligned_concat_match` 위임으로 교체.
2. `_title_score`: title 토큰열·url 토큰열을 따로 판정해 `max` (§3.2).
3. `_body_score`: `documents_tokenize(body)` 를 리스트로 한 번만 만들어
   `set(...)` 과 concat 판정에 함께 사용.
4. 더 이상 쓰이지 않는 `collapse` import 는 `_match_positions` 가 계속 쓰므로 유지.
5. 테스트: `tests/unit/test_search_scorer.py` 에
   - `'api'` / `'Rapid Onboarding Guide'` → collapse 점수 0.0 (기존 1.0)
   - `'결제장애'` / `'결제 장애 대응'` → `1/token_count`
   - `'결제 장애'` / `'결제장애'` → `1/token_count` (역방향)
   - title 끝 + url 앞을 걸친 유령 매치가 0.0 인지
   기존 테스트 중 부분문자열 매치를 기대하던 케이스가 있으면 **기대값을 바꾸는 것이 맞다**
   (의도된 동작 변경) — 다만 그런 케이스가 나오면 어떤 것이었는지 보고에 적을 것.
6. `tests/unit/test_document_search_service.py` 의 순위 기대값이 깨지면 같은 기준으로 갱신.
7. 커밋 1건으로 마감.

### T2. keyword arm 복합어 대칭

1. `search_scorer.py`: `COMPOUND_TERM_LIMIT`, `_MIN_SPLIT_PART_LEN`,
   `compound_concat_terms`, `compound_split_phrases` 추가 (§4.2). 한국어 docstring 필수.
2. `chunk_repository.search_endpoint_by_text`: `phrase_terms`/`score_phrase_terms`
   키워드 파라미터 추가와 tsquery 조립 (§4.3). docstring 에 "None 이면 기존과 동일한
   tsquery" 를 명시.
3. `document_search_service._keyword_arm`: `query`/`query_variants` 인자 추가 + 파생 term
   배선 (§4.4). 호출부(`_search_indexed`) 갱신.
4. 테스트:
   - `tests/unit/test_search_scorer.py`: 두 순수 함수의 산출·중복 제거·캡 동작.
   - `tests/unit/test_chunk_repository.py`: `phrase_terms` 미지정 시 tsquery 무변경(회귀),
     지정 시 `('a' <-> 'b')` 그룹이 OR 로 붙는지, 인용 이스케이프가 유지되는지.
   - `tests/unit/test_search_fts_regression.py`: 본문 `'결제 장애 대응'` 문서를 질의
     `'결제장애'` 로, 본문 `'결제장애 대응'` 문서를 질의 `'결제 장애'` 로 각각 찾는지.
   - `tests/unit/test_endpoint_candidate_search.py`: 엔드포인트 경로 무변경 확인.
5. 커밋 1건으로 마감(T1 과 분리).

### 공통

- 커밋은 하지 않는다 — 워킹트리에 두고 lead 에게 보고한다.
- 설계와 다르게 가야 할 지점이 나오면 그 자리에서 architect 에게 판단을 요청한다.
- 완료 보고에 담을 것: 변경 파일, 새 테스트 이름, 기대값을 바꾼 기존 테스트와 그 이유,
  `uv run pytest` 결과(실행 명령과 요약 줄 그대로).
