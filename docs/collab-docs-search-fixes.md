# 협업 문서(Drive/Notion) 검색 수정사항 — Notion 검색 실패 사례 검토 후속

- 상태: **착수**
- 일시: 2026-08-10
- 작성: lead
- 배경: uhok-sonata 프로젝트에서 `search_documents`로 유사상품 추천 로직 문서를 찾을 때,
  top_k=10 안에 정답 버전(v_1.0) 페이지가 전혀 없어 구버전 통합 문서를 최종본으로 오인한 사례.
  코드 대조 결과 서버 쪽 결함 다수 확인.
- 대상 코드: `app/services/documents/document_search_service.py`,
  `app/repositories/document_meta_repository.py`,
  `app/services/documents/sources/{notion_source,google_drive_source}.py`,
  `app/models/document_meta.py`, `app/mcp/payloads.py`, `app/mcp/tools/documents.py`

## 항목 목록

| # | 우선순위 | 내용 | 상태 |
|---|---|---|---|
| 1 | P0 | `collapse` 매칭이 `query_variants`를 반영하지 않음 | 대기 |
| 2 | P1 | top_k 컷이 제목 점수만으로 2단계 이전에 발생 | 대기 |
| 3 | P1 | `version` 필드/개념 부재 | 대기 |
| 4 | P2 | `get_document`가 title/url을 빈 문자열로 반환 | 대기 |
| 5 | P2 | 본문 절단 시 `truncated` 플래그 없음 | 대기 |
| 6 | P3 | score 0 결과가 필터 없이 반환됨 | **기각(전제 불성립)** |

## 항목별 상세

### 1. collapse 매칭이 query_variants를 반영하지 않음 (P0)

- 위치: `app/repositories/document_meta_repository.py` `search_by_tokens`
- 원인: `collapsed_query = collapse(query)`가 원본 질의 문자열만 collapse하고,
  `query_variants`는 토큰 OR 매칭에만 섞여 공백-무관 매칭 경로를 안 탐.
- 설계 확정 (architect):
  - **인터페이스**: `query: str` 단일 인자를 `queries: Sequence[str] = ()`로 교체.
    근거 — 저장소 메서드 안에서 `query`는 오직 collapse-OR 확장에만 쓰이고(점수 계산은
    서비스 계층 몫), 원본/variant를 구분할 의미가 없다. `query + extra_queries` 분리안은
    내부 처리가 완전히 동일한 두 인자를 만들어 비대칭·DRY 위반이 되므로 기각.
  - **동작**: `queries`의 각 문자열을 `collapse()`한 뒤, 빈 문자열이 아니고 아직 안 본
    collapse 값만 OR 조건으로 추가(중복 collapse 값은 dedup — 원본과 variant가 같은
    문자열로 collapse될 수 있음). `collapsed_title`/`collapsed_url` func 표현식은
    루프 밖에서 한 번만 생성하고 패턴만 질의별로 갈아끼운다. `queries=()`면 collapse
    조건 전체 생략(기존 `query=""` 계약과 동일).
  - **호출부**: `_select_candidates`가
    `queries=[query, *(options.query_variants or [])]`로 호출. variant는 토큰이 아닌
    **원문 그대로** 넘긴다(collapse가 구절 전체 공백을 제거해야 하므로).
  - **영향 범위**: 프로덕션 호출부는 `_select_candidates` 하나뿐. 나머지는 저장소 단위
    테스트(`test_document_meta_repository.py`의 `query=` kwarg 사용부)로, 구현 시
    `queries=[...]`로 함께 갱신. docstring의 `query` 인자 계약도 `queries`로 개정.
- 수정: 완료 대기

### 2. top_k 컷이 제목 점수만으로 2단계 이전에 발생 (P1)

- 위치: `app/services/documents/document_search_service.py` `_select_candidates`
  (현 254~256행), `_rank_with_body`, `search`.
- 원인: `_select_candidates`가 `scored[: options.top_k]`로 **1단계 title_score
  만으로** 후보를 top_k 건까지 자르고, 2단계 본문 fetch는 그 잘린 집합에만
  일어난다. 제목에 원본 질의어가 없어(title_score 낮거나 0) 정렬 하위로 밀린
  문서는 본문이 아무리 강하게 매칭돼도 애초에 fetch 대상에 못 들어가
  body_score를 계산할 기회조차 없다. 근본 원인은 top_k가 **"2단계 fetch
  상한(rate limit 보호)"과 "최종 결과 개수"를 동시에** 맡는 이중 역할.
  (uhok-sonata: 정답 v_1.0 페이지 제목에 질의어가 없어 top_k=10 밖으로 밀려
  본문 미조회.)
- 설계 확정 (architect):
  - **두 역할 분리**: top_k는 이제 **최종 결과 개수**만 의미한다. "2단계 본문
    fetch 상한"은 top_k에서 파생하되 top_k보다 넉넉한 **별도 fetch 예산**으로
    분리한다.
  - **fetch 예산 공식** — 신설 헬퍼 `_body_fetch_budget(top_k, candidate_count)`:
    ```
    overscan = min(top_k * BODY_FETCH_OVERSCAN, MAX_BODY_FETCH_CANDIDATES)
    budget   = min(max(top_k, overscan), candidate_count)
    ```
    - `BODY_FETCH_OVERSCAN = 3`: top_k의 몇 배까지 본문을 열어볼지.
    - `MAX_BODY_FETCH_CANDIDATES = 20`: 2단계 총 fetch 하드캡(rate limit 보호).
      단 `max(top_k, ...)`로 감싸 사용자가 top_k>20을 명시하면 그 값이 우선한다
      (기존 계약 유지 — top_k=50이면 오늘도 50건 fetch). 기존
      `MAX_CONCURRENT_BODY_FETCHES=5`(동시성)와는 별개 상수다.
    - 예: top_k=1→3, top_k=5→15, top_k=10→20, top_k=50→50.
  - **`_select_candidates`**: `scored[: options.top_k]` → `scored[: budget]`.
    정렬 키(원본매치 내림차순, title_score 내림차순, external_id)는 **그대로
    유지** — 한정된 fetch 예산을 원본 신호가 강한 행부터 배분하는 원칙은 옳다.
    달라지는 건 slice 깊이뿐이다.
  - **최종 컷은 body_score 반영 후**: `_rank_with_body`에 `top_k` 인자를
    추가하고, 결합 점수(`TITLE_SCORE_WEIGHT*title + BODY_SCORE_WEIGHT*body`)로
    정렬한 **뒤** `items[:top_k]`로 자른다. `search`는
    `_rank_with_body(..., options.top_k)`로 호출. 이로써 title_score=0·body_score
    강한 문서가 결합 점수(=0.6×body_score)로 title-only 문서(0.4×title_score)를
    제치고 최종 top_k에 진입할 수 있다 — 항목 2가 노리는 바로 그 경로.
  - **동시성 불변**: `MAX_CONCURRENT_BODY_FETCHES=5`는 그대로. 예산 20이면
    최대 4웨이브(지연 ~4×)로, 검색 응답 지연 상한 내 허용 비용으로 판단.
  - **한계(honest)**: 후보 수가 fetch 예산을 초과하고 그 초과분이 전부
    title_score=0인 극단에서는 여전히 정답이 예산 밖일 수 있다. overscan은
    위험을 줄이는 실용적 완화이며, "제목 신호 자체가 없는" 근본 문제의 정답은
    항목 1(variant collapse 매칭으로 후보 진입 보장)·항목 3(version 인식)과
    층으로 함께 동작한다.
- **영향 범위**:
  - `document_search_service.py`: 상수 2개 추가(`BODY_FETCH_OVERSCAN`,
    `MAX_BODY_FETCH_CANDIDATES`), `_body_fetch_budget` 헬퍼 신설,
    `_select_candidates` slice 변경, `_rank_with_body` 시그니처(+top_k)·말미
    slice 추가, `search` 호출부 1곳, 모듈/`_select_candidates`/`_rank_with_body`
    docstring 개정("top_k 건만 fetch" → "fetch 예산만큼 fetch 후 top_k 컷").
  - 외부 인터페이스(`search` 시그니처, `DocumentSearchOptions`, MCP payload)
    **불변** — 반환은 여전히 최대 top_k 건.
  - 테스트: fetch 예산 경계(top_k=5→15, top_k=10→20 cap, top_k>cap→top_k),
    "title_score=0·body 강함" 문서가 최종 top_k에 진입하는 회귀 케이스,
    후보<top_k일 때 budget=candidate_count 확인.
- 수정: 완료 대기

### 3. version 필드/개념 부재 (P1)

- 위치: `app/services/documents/document_search_service.py`
  (`_fetch_and_score`·`get_document`), `app/mcp/types.py`·`app/mcp/payloads.py`
  (payload), 신설 `app/services/documents/version_parser.py`. `document_meta.py`
  모델은 **변경 없음**(아래 설계 확정 2번 참조).
- 원인: 제목의 버전 접미사(`v_1.0` 등)를 파싱·노출하는 개념이 아예 없어,
  검색 결과에서 정답 버전과 구버전 통합 문서를 구분할 단서가 호출자에게
  전달되지 않는다. (uhok-sonata: 정답 `v_1.0` 페이지와 버전 표기 없는 구버전
  통합 문서가 섞여 후자를 최종본으로 오인.)
- 설계 확정 (architect):
  1. **파싱 규칙** — 순수 함수 `parse_version(title: str) -> str | None`:
     - 정규식 `(?i)(?<![0-9A-Za-z])v[\s_]?(\d+(?:[._]\d+)*)` 로 `v`-접두 **숫자
       버전**을 잡는다. 여러 개면 **마지막 매치**를 쓴다(버전은 제목 접미사
       관례). 캡처값의 `_`를 `.`로 정규화해 `"v" + 정규화값` 반환
       (예: `v_1.0`→`v1.0`, `V 2`→`v2`, `...로직_v_1.0`→`v1.0`).
     - 부정 룩비하인드 `(?<![0-9A-Za-z])`로 `rev2`·`level2` 같은 단어 내부 v를
       배제(직전 문자가 영숫자면 불매치, `_`·공백·`(`·시작은 허용).
     - **버전 표기 없으면 `None`**(에러 아님). 파싱 실패가 정상 경로.
     - **상태 마커(`(최종)`/`final`/`draft` 등)는 `version`에 넣지 않는다** —
       버전 번호와 성격이 다르고(비교 불가) 한 필드에 섞으면 의미가 흐려진다
       (KISS). 이런 마커는 이미 노출되는 `title` 문자열에 그대로 남아 호출자
       판단에 쓰인다.
  2. **DB 컬럼 불필요 — 응답 시점 동적 파싱**: `version`은 `title`의 순수
     파생값이라 컬럼 저장은 파생 데이터 중복·규칙 변경 시 재동기화 부담만
     생긴다. 4번(순위 무영향)에 따라 SQL 필터/정렬도 불필요하므로 **alembic
     마이그레이션 없이** 결과 조립 시점(최종 ≤top_k 건에 대해서만)에 파싱한다.
     항목 2와 같은 최소변경·YAGNI 기조.
  3. **노출 위치**: `DocumentSearchItem`·`DocumentContent` 데이터클래스에
     `version: str | None` 추가, `DocumentSearchItemPayload`·
     `DocumentContentPayload`(TypedDict)에 `version: str | None`(항상 emit,
     버전 없으면 `null`) 추가, `_to_document_search_payload`·
     `_to_document_content_payload` 매핑 추가. `_fetch_and_score`와
     `get_document`가 `parse_version(title)`을 호출해 채운다.
  4. **순위 무영향 — 단순 메타데이터**: 점수 계산(`search_scorer`)은 **손대지
     않는다**. "최신판=정답"은 사용자 의도에 달린 의미 판단이고(구버전을
     원할 수도 있음), 버전 비교 의미론도 지역·표기별로 모호하다. 항목 1의
     "질의확장 판단은 호출자 모델 몫" 원칙과 일관되게, 서버는 `version`을
     **노출만** 하고 최신판 선택은 호출자(Claude)에 맡긴다. 점수의 의미는
     "질의 정합성"으로 유지된다.
  5. **하위호환**: 컬럼이 없어 기존 행은 그대로. 버전 표기 없는 문서는
     `version=null`. payload에 키가 추가되나 **가산적 변경**(기존 필드·순위·
     계약 불변)이라 기존 클라이언트에 무해하다. uhok 해결은 층으로 동작 —
     항목 1(후보 진입)+항목 2(본문 fetch)가 정답 버전을 결과에 올리고, 항목
     3이 `version` 문자열로 호출자가 구별하게 한다.
- **영향 범위**:
  - 신설 `version_parser.py`(순수 함수 1개), `document_search_service.py`
    2곳(`_fetch_and_score`·`get_document`)에서 호출, `DocumentSearchItem`·
    `DocumentContent` 필드 추가, `mcp/types.py`·`mcp/payloads.py` payload 2곳.
  - **모델·마이그레이션·SQL·점수 계산 불변.** `search`/`get_document` 시그니처
    불변.
  - 테스트: `parse_version` 단위(정상 `v_1.0`/`v2`/`V 3.1`, 단어 내부 v 배제,
    다중 매치 시 마지막, 표기 없음→None), 검색/조회 payload에 `version` 키가
    실리고 버전 없는 문서는 null인지, 순위가 version에 영향받지 않는지 회귀.
- 수정: 완료 대기

### 4. get_document가 title/url을 빈 문자열로 반환 (P2)

- 위치: `app/services/documents/document_search_service.py` `get_document`,
  `DocumentContent` docstring, `app/mcp/types.py` `DocumentContentPayload`
  docstring.
- 원인: `(source, external_id)`로 메타 캐시에 행이 없으면 title/url을 조용히
  `""`로 채워 반환한다. **본문(content)은 정상**이다 — `DEFAULT_PROJECT`
  폴백으로 fetch는 성공하기 때문. 문제는 (1)`""`의 의미가 계약에 명시되지
  않았고, (2)docstring이 "없으면 빈 문자열/**식별자 기반 기본값**"이라 적어
  실제(둘 다 `""`)와 어긋난다 — url에 식별자 기반 기본값을 주는 코드는 없다.
- 설계 확정 (architect):
  1. **명시적 실패로 바꾸지 않는다(계약 유지)**: 메타 없음은 정상 경로다 —
     `get_document`의 존재 이유가 "캐시에 없어도 최신 원문을 가져온다"이고,
     그 폴백은 `DEFAULT_PROJECT`로 이미 설계돼 있다. content fetch가 성공한
     응답을 메타데이터가 없다는 이유로 `IntegrationError`로 던지면, "본문
     조회 실패"(fetch가 이미 던지는 진짜 오류)와 "메타 미캐시"(무해)를
     뒤섞고 계약을 깬다. **raise 하지 않는다.**
  2. **빈 문자열을 유지하되 계약을 정직하게 기재**: title/url은 메타 캐시에
     있으면 그 값, **없으면 빈 문자열 `""`** 로 확정(코드 동작 그대로).
     docstring의 잘못된 "식별자 기반 기본값" 문구를 제거하고 "메타 캐시에
     없으면 `""`"로 정정한다. content는 항상 fetch 시점의 최신 원문.
  3. **url 폴백은 하지 않는다(범위 밖)**: 식별자로 canonical url을 만들 수는
     있으나(어댑터가 이미 하는 일), 서비스 계층이 url 패턴을 알면 `DocumentSource`
     추상화가 깨지고, 깨끗이 하려면 Protocol에 `canonical_url()`을 추가해 두
     어댑터를 고쳐야 한다 — P2·YAGNI 범위 초과. 게다가 호출자는 보통
     `search_documents` 결과에서 온 external_id를 쓰므로 url을 이미 갖고 있어
     실익이 작다. 후속 과제로만 남긴다.
  4. **호출자 해석 계약(질문 c)**: `title`/`url`이 `""`이면 "서버에 이 문서의
     **메타데이터가 캐시돼 있지 않다**"는 뜻이며, `content`는 여전히 방금
     fetch한 authoritative 최신 원문이다. 호출자는 `""`를 "제목/링크 미상 —
     필요하면 `refresh_index` 후 재조회"로 해석한다. content 유무와 메타 유무는
     독립임을 payload docstring에 명시한다. (별도 `metadata_cached: bool` 플래그도
     검토했으나 `""`가 이미 그 신호이고 필드 추가는 speculative라 기각 — YAGNI.)
- **영향 범위**:
  - **동작 변경 없음.** 코드는 `get_document`의 반환문 그대로. 변경은
    **docstring/계약 3곳**(`get_document`, `DocumentContent`,
    `DocumentContentPayload`)의 정정뿐. 항목 5에서 `DocumentContent`에
    `truncated`가 추가되므로 편집이 겹치는 점만 developer가 함께 처리.
  - 테스트: 메타 행 없음 → `content`는 fetch 값, `title`/`url`은 `""`,
    예외 없이 반환됨을 명시하는 회귀 테스트(계약 고정).
- 수정: 완료 대기

### 5. 본문 절단 시 truncated 플래그 없음 (P2)

- 위치: `app/services/documents/sources/document_source.py`(Protocol·신설
  `FetchedDocument`), `notion_source.py`·`google_drive_source.py`(`fetch` 반환),
  `document_search_service.py`(`get_document`·`_fetch_and_score` 호출부,
  `DocumentContent`), `app/mcp/types.py`·`app/mcp/payloads.py`(payload).
- 원인: 두 어댑터가 `text[: self._max_chars]`로 조용히 자르고 절단 여부를
  버린다. `DocumentSource.fetch()`가 `str`만 반환해 절단 정보를 실을 자리가
  없다 — 최대 문자 수(`max_chars`)를 아는 곳은 어댑터뿐인데 그 신호가 위로
  전파되지 않는다.
- 설계 확정 (architect):
  1. **절단 판정은 어댑터에서, 반환 타입으로 전파(질문 a)**: `max_chars`를 알고
     실제로 자르는 주체가 어댑터이므로, 서비스 계층에서 `len==max_chars` 같은
     휴리스틱으로 역추정하지 않는다(정확히 max_chars 길이인 문서를 오탐).
     `document_source.py`에 `FileMeta`와 같은 스타일의 frozen dataclass
     `FetchedDocument(text: str, truncated: bool)`를 신설하고, Protocol을
     `fetch(self, external_id: str) -> FetchedDocument`로 바꾼다.
     각 어댑터: `truncated = len(text) > self._max_chars` 계산 후
     `FetchedDocument(text[: self._max_chars], truncated)` 반환(둘 다 단일
     반환 지점 — notion은 `"\n".join(lines)`, drive는 `text[:max_chars]` 한 곳).
  2. **search 경로에는 truncated를 노출하지 않는다(질문 b)**: 스니펫은 본래
     본문의 작은 발췌라 "절단" 개념이 무의미하고, `DocumentSearchItem`은 전문을
     싣지 않는다. `_fetch_and_score`는 `document_source.fetch(...).text`로 텍스트만
     쓰고 `.truncated`는 버린다(점수·스니펫 계산 불변). truncated는 **원문 조회
     경로(`get_document`)에만** 의미가 있으므로 거기에만 노출한다.
  3. **get_document·payload에 전파**: `get_document`는
     `fetched = document_source.fetch(normalized_id)` 후
     `DocumentContent(..., content=fetched.text, truncated=fetched.truncated)`.
     `DocumentContent` 데이터클래스와 `DocumentContentPayload`(TypedDict)에
     `truncated: bool`(항상 emit) 추가, `_to_document_content_payload` 매핑 추가.
  4. **하위호환**: payload에 `truncated: bool`이 추가되나 가산적 변경.
     기존 계약(반환 형태·시그니처는 서비스 공개 API 기준 불변, `fetch`는 내부
     Protocol이라 외부 MCP 계약과 무관).
- **영향 범위(질문 c — 정확히)**:
  - `document_source.py`: `FetchedDocument` 신설 + Protocol `fetch` 반환 타입 변경.
  - **두 어댑터 구현체 모두 변경**(`notion_source.fetch`, `google_drive_source.fetch`)
    — 반환을 `FetchedDocument`로. 각 단일 반환 지점만 수정.
  - `document_search_service.py` **fetch 호출부 2곳**: `get_document`(라인 210 부근,
    `.text`+`.truncated` 사용), `_fetch_and_score`(라인 359 부근, `.text`만 사용).
    `DocumentContent`에 `truncated` 필드 추가.
  - `mcp/types.py`·`mcp/payloads.py`: `DocumentContentPayload`에 `truncated` +
    매핑.
  - **테스트 페이크 변경 필수**: `tests/fixtures/document_sources.py`와
    `tests/unit/test_document_search_service.py`의 `def fetch` 페이크가 이제
    `FetchedDocument`를 반환해야 한다.
  - 테스트: 어댑터가 `max_chars` 초과 시 `truncated=True`·경계(정확히 max_chars면
    False), `get_document` payload에 `truncated` 전파, search 결과는 truncated에
    영향받지 않음(회귀).
- 수정: 완료 대기

### 6. score 0 결과가 필터 없이 반환됨 (P3)

- 위치: `app/services/documents/document_search_service.py` `_rank_with_body`
  (최종 조립부), `search` docstring.
- 원인 (재확인): score=0 결과는 **실제로 나갈 수 있다**. `_fetch_and_score`가
  `score = round(TITLE_SCORE_WEIGHT*title_score + BODY_SCORE_WEIGHT*body_score, 4)`
  를 계산하는데, variant 토큰으로만 SQL 후보에 걸려(title_score=0) 본문에도
  원본 토큰이 전혀 없는(body_score=0) 문서는 정확히 `0.0`을 받는다. 정렬상
  맨 뒤에 붙어 top_k 여유가 있으면 그대로 반환된다 — 원본 질의 관점에서
  겹침이 0인, 사실상 무관한 문서다.
- **설계 재검토 (architect, 2026-08-11) — 필터 기각. 구현 되돌림.**
  - **번복 사유**: 구현 중 항목 6 필터가 항목 2 회귀 테스트 3건
    (`test_query_variants_widen_sql_candidate_gate`,
    `test_top_k_two_includes_both_but_original_match_ranks_first`,
    `test_query_variant_whitespace_difference_reaches_collapse_match`)과
    정면 충돌했다. 검토 결과 **충돌하는 쪽은 테스트가 아니라 항목 6의 전제**다.
  - **전제 붕괴**: 점수는 `test_query_variants_do_not_affect_score`로 **원본
    토큰만** 반영하도록 고정돼 있다. 따라서 variant/collapse로만 후보에 든
    문서 — 항목 1·2가 존재하는 **바로 그 이유** — 는 원본 토큰 겹침이 없어
    **설계상 반드시 score=0.0**이 된다. `0.0 ⟺ 진짜 무관` 이라는 항목 6의
    핵심 전제는 여기서 거짓이다: 0.0은 "무관"이 아니라 "토큰 스코어러가
    점수화하지 않는 경로(호출자 지정 variant, 공백 collapse)로 매칭됐다"는
    뜻일 뿐이다. 후보 게이트를 통과한 문서는 모두 원본-or-variant를
    토큰-or-collapse로 매칭했으므로, **score=0.0으로 노이즈를 증명할 수 있는
    후보는 존재하지 않는다.**
  - **원칙 정합**: variant는 호출자(Claude)가 동의어로 고른 관련성 신호다.
    이를 서버가 "원본 토큰 미겹침"을 근거로 삭제하는 것은 항목 3에서 못박은
    "질의확장 판단은 호출자 몫" 원칙과 `MCP는 판단을 클라 LLM에 위임` 기조에
    정면으로 반한다. 서버는 score를 실어 반환하고, 약한 신호(0.0)를 버릴지는
    호출자가 판단한다.
  - **옵션 (a)(테스트 3건에 원본 토큰 바디 겹침 주입) 반려**: 그 수정은
    테스트를 "고치는" 게 아니라 **variant-only/collapse-only 경로의 유일한
    회귀 커버리지**(P0·P1 전체 작업의 존재 이유)를 삭제해 결함을 은폐한다.
  - **조치**: 항목 6 구현을 되돌린다 — `_rank_with_body`의
    `item.score > 0.0` 필터 1줄과 관련 docstring 추가분 제거, 항목 6 전용
    신규 테스트 4건 삭제, 항목 2 테스트 3건은 **그대로 유지**. `search`/
    `_rank_with_body` 반환 계약은 항목 2 확정본(결합 점수 정렬 후 `[:top_k]`)
    그대로. score=0 결과는 정렬상 최하위→top_k 경쟁 시 자연히 밀리고, 여유가
    있을 때만 노출돼 호출자 판단에 맡겨진다(항목 2 랭킹과 무모순).
  - _아래 원안(취소됨)은 이력 보존용._

- ~~설계 확정 (architect) — **필터링으로 변경(의도 유지 아님)**~~:
  - **판단**: 유지할 의도가 아니라 고칠 값이다. 항목 2가 "variant-only
    (title_score=0) 후보도 일단 본문을 열어보되, **최종 관련성 판단은
    body_score가 맡는다**"로 확정했다. body_score가 0을 매겼다는 건 그 최종
    판단이 "무관"이라는 뜻이므로, 0점을 결과에 남기는 것은 항목 2 자신의
    로직과 모순이다. score=0 제거는 "판단은 호출자 몫" 원칙 위반이 아니다 —
    그 원칙은 동의어·최신판 같은 *의미* 판단에 관한 것이고, 여기서 하는 건
    서버 고유의 *검색 관련성* 필터를 완성하는 것이다(서버는 이미 후보 0건이면
    `[]`, top_k 컷, score 정렬로 관련성을 거른다).
  - **임계값은 정확히 0.0만**: 별도 threshold(예: `<0.1`)를 두지 않는다.
    "얼마나 관련돼야 충분한가"는 임의적·취약한 매직넘버이자 진짜 호출자 몫의
    의미 판단이다. 반면 0.0은 "원본 토큰 겹침 전무"라는 명확한 사실이라
    튜닝이 필요 없다. **양(+)의 점수는 원본 신호가 조금이라도 있다는 뜻이라
    남겨 호출자에게 넘기고, 서버는 증명 가능한 노이즈(0.0)만 제거**한다.
    (계산상 실제 겹침이 있으면 rounding 후에도 0.0이 될 수 없음 — 최소 양의
    점수 ≈ `TITLE_SCORE_WEIGHT/토큰수` ≫ `1e-4`. 즉 `0.0 ⟺ 진짜 무관`이라
    양의 신호를 오탐 제거할 위험이 없다.)
  - **위치**: `_rank_with_body` 최종 조립부에서 `it.score > 0.0` 인 항목만
    남긴 뒤 정렬·`[:top_k]` 컷(항목 2가 추가한 컷보다 **앞**에서 걸러 0점이
    top_k 자리를 차지하지 못하게). `search()` 상위가 아니라 여기에 둬 랭킹
    로직을 한곳에 유지.
  - **계약**: top_k는 이미 상한(후보 부족·fetch 실패로 이하 가능)이라 반환
    개수가 top_k보다 적을 수 있는 계약은 **신규가 아니다**. 다만 "빈 리스트는
    후보 0건뿐 아니라 '후보는 있었으나 관련도 0'인 경우도 포함"이라는 의미를
    `search`/`_rank_with_body` docstring에 명시한다.
  - **옵션 추가 안 함**: `DocumentSearchOptions`에 `include_zero_score` 같은
    토글은 YAGNI — 증명 가능한 노이즈를 되살릴 유스케이스가 없다. API 표면
    불변.
  - **uhok 회귀 없음**: 정답 문서는 항목 1·2로 본문이 fetch되면 body_score>0
    (실제로 매칭됨)이라 score>0 → 필터 대상 아님. 필터는 title·body 둘 다 0인
    문서만 제거하므로 항목 1~3이 복원한 신호를 건드리지 않는다.
- **영향 범위**:
  - `document_search_service.py` `_rank_with_body` 한 곳(필터 1줄 + docstring),
    `search` docstring. **모델·payload·옵션·점수식·시그니처 전부 불변.**
  - 테스트: title=0·body=0 문서가 결과에서 제외됨, 양의 점수 문서는 유지됨,
    모든 후보가 0점이면 `[]` 반환, 0점이 top_k 자리를 차지하지 않는지(컷과의
    순서) 회귀.
- 수정: 완료 대기
