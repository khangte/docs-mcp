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
| 6 | P3 | score 0 결과가 필터 없이 반환됨 | 대기 |

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

- 위치: `app/models/document_meta.py`
- 원인: 제목의 버전 접미사를 파싱하는 로직이 아예 없음.
- 수정: 완료 대기

### 4. get_document가 title/url을 빈 문자열로 반환 (P2)

- 위치: `app/services/documents/document_search_service.py` `get_document`
- 원인: 메타 캐시에 행이 없으면 조용히 빈 문자열 반환, docstring에도 미기재.
- 수정: 완료 대기

### 5. 본문 절단 시 truncated 플래그 없음 (P2)

- 위치: `app/services/documents/sources/{notion_source,google_drive_source}.py`
- 원인: `[:max_chars]`로 조용히 자르고 절단 여부를 반환하지 않음.
- 수정: 완료 대기

### 6. score 0 결과가 필터 없이 반환됨 (P3)

- 위치: `app/services/documents/document_search_service.py`
- 원인: 의도적 설계로 무관 문서도 후보에서 제외하지 않음.
- 수정: 완료 대기
