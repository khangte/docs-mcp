# ADR-0005: 엔드포인트 lexical 표현의 색인 시점 구조화와 가중 tsvector 채택

- 상태: accepted
- 일시: 2026-08-28
- 관련: `docs/architect-review/78_endpoint_index_structure_signal_design.md`,
  `docs/architect-review/74_p02_coverage_fix_failure_and_keyword_variant_stop_verdict.md`,
  ADR-0002

## 컨텍스트

엔드포인트 키워드 검색은 `chunk.text_tsv`(= `to_tsvector('simple', text)`) 단일 필드
위에서 `ts_rank` 로 채점한다. 이 표현에서는 target 자원을 지시하는 path leaf 토큰,
ancestor context 토큰, 300자로 잘린 설명 안의 우연한 반복이 **모두 같은 무게**를 갖는다.
그래서 짧고 정확한 정답 청크가 길고 부정확한 형제 청크의 term density 에 밀린다.

verdict 74 는 이 문제를 search-time 후처리(coverage 임계, 기여 예산, variant pool 억제)로
고치려던 네 가지 후보를 전부 반려했다. 평평한 표현이 구분하지 못하는 정보를 후처리로
복원할 수 없다는 것이 실 코퍼스 게이트의 결론이었다.

## 결정

엔드포인트 lexical 표현을 **색인 시점에 네 등급으로 구조화**하고, 가중 tsvector 로 채점한다.

1. `chunk` 에 결정적 파생 평문 컬럼 3개를 둔다 — `leaf_text`(A), `intent_text`(B),
   `context_text`(C). 값은 `method`·`path`·`summary`·`tags`·`operation_id` 에서만 만든다.
   LLM 생성물(`EndpointBusinessMetadata`)은 주입하지 않는다.
2. 그 셋과 기존 `text`(D)를 `setweight` 로 묶은 생성 컬럼 `search_tsv` 를 두고,
   `ts_rank('{0.1, 0.2, 0.4, 1.0}', search_tsv, tsquery)` 로 채점한다. 가중치 배열은
   Postgres 기본값 그대로 상수 고정하며 평가 결과를 보고 조정하지 않는다.
3. `method` × path shape(item/collection) → operation alias 표를 동결한다. 항목 추가·삭제는
   새 architect verdict 를 요구한다. 게이트에서 실패한 질의의 동사를 표에 더하는 것은
   verdict 74 가 반려한 과적합과 같은 경로다.
4. `text` 와 `embedding` 은 바꾸지 않는다. 따라서 재임베딩이 없고 벡터 arm 은 비트
   단위로 불변이며, 기존 색인 반영은 재색인이 아니라 백필 스크립트로 한다.
5. 롤백은 `DOCS_MCP_SEARCH_LEXICAL_FIELD` 설정 하나로 한다(기본 `text` = 현행 동작).
   기존 `text_tsv` 컬럼과 인덱스는 존치하며, 협업 문서(`chunk_type="section"`) 검색
   경로는 계속 그것을 쓴다.

## 결과

- 장점: term density 역전을 Postgres 내장 기능으로 직접 교정한다. 벡터 arm 이 불변이라
  순위 변화가 lexical arm 에 귀속되고, 같은 공유 인덱스 위에서 컬럼만 바꿔
  baseline/candidate 를 비교할 수 있다. 재임베딩 비용 0, 롤백은 설정 한 줄.
- 단점: `chunk` 테이블 rewrite 1회와 엔드포인트 행에 대한 두 번째 GIN 인덱스가 든다.
  lexical 표현이 두 벌(`text_tsv`/`search_tsv`)이 되어 승급 확정까지 공존한다.
- 한계: 한글 전용 질의는 여전히 이 경로로 풀리지 않는다. 영문 OpenAPI 원문에 한글
  원천이 없어 결정적 생성 계약을 지키면서 한글 신호를 만들 수 없다 — 클라이언트가
  넘기는 `query_variants` 와 벡터 arm 이 그 역할을 계속 맡는다.
- 후속 영향: `app/services/indexer/endpoint_structure.py`(신규),
  `app/models/chunk.py`, `app/repositories/chunk_repository.py`,
  `app/services/search/keyword_search.py`, `app/scripts/backfill_endpoint_structure.py`.
- ADR-0003(MCP 읽기 전용 경계)은 **개정 대상이 아니다.** 이 결정은 상류 API 를 호출하지
  않고 MCP 도구 표면도 바꾸지 않는다(78번 §9.1).
