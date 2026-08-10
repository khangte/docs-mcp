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
- 원인: `scored[: options.top_k]`가 본문을 열어보기 전에 제목 점수만으로 컷.
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
