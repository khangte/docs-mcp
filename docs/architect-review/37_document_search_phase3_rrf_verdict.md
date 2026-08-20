# doc35 Phase3 12번 — DocumentSearchService 2단계 교체 구조 판정

- 일시: 2026-08-14
- 작성: architect
- 선행: `docs/architect-review/35_drive_notion_embedding_migration_and_refresh_strategy.md` §1 Phase3,
  `docs/architect-review/07_search_rrf_reevaluation.md`,
  `docs/architect-review/34_drive_notion_no_embedding_rationale.md`
- 질문(developer): 2단계를 (A) 후보 문서별 스코프 조회 + 기존 가중합 유지로 갈지,
  (B) `EndpointCandidateSearch` 와 동형인 RRF 융합으로 갈지.

## 판정 요약

**(A) 반려, (B) 채택.** 단 (B) 를 "title 후보와 합집합 또는 대체"로 두지 않고,
**title 신호를 세 번째 RRF arm 으로 편입한 3-arm 융합**으로 확정한다. 그러면
질문에 있던 폴백 분기(`document_meta.document_id IS NOT NULL`)가 아예 필요 없어진다.

## 1. (A) 를 반려하는 이유

### 1.1 도입 목적이 무효화된다 (결정적)

(A) 는 1단계(`_select_candidates` → `search_by_tokens`, title/url ILIKE 게이트)를
**유일한 후보 공급원**으로 남긴다. 그 결과 본문 색인은 "이미 제목으로 찾은 문서의
재정렬"에만 쓰인다.

그런데 본문 임베딩 도입의 근거는 `34_drive_notion_no_embedding_rationale.md` 이후
줄곧 **"제목에는 안 걸리고 본문에만 강하게 걸리는 문서"** 를 찾는 것이었다
(`document_search_service.py:55-60` 의 오버스캔 주석도 같은 문제를 fetch 예산으로
완화하려던 흔적이다). (A) 에서 그런 문서는 여전히 1단계에서 탈락해 0건이다.
Phase1+2 로 만든 색인의 유일한 존재 이유가 사라진다.

### 1.2 fetch 비용을 임베딩·왕복 비용으로 옮길 뿐이다

`VectorSearch.search()` 는 호출마다 `embed_query` 를 수행한다
(`app/services/search/vector_search.py:41`). 후보 문서마다 벡터 1건을 조회하면
후보 수(최대 `MAX_BODY_FETCH_CANDIDATES`=20)만큼 임베딩 API 를 호출한다. 여기에
문서당 FTS 1회를 더해 SQL 왕복이 후보 수 × 2 로 늘어난다. 외부 API N+1 을 없애려는
변경이 다른 외부 API N+1 을 만든다. (질의 임베딩을 호출부에서 1회로 끌어올리면
완화되지만, 그건 `VectorSearch` 계약을 우회하는 별도 부채다.)

### 1.3 이미 폐기한 가중합 스케일 문제를 되살린다

`TITLE_SCORE_WEIGHT`(0.4) / `BODY_SCORE_WEIGHT`(0.6) 은 두 항이 모두 **토큰 겹침
비율 [0,1]** 이라 성립했던 공식이다. body 항을 `ts_rank`(코퍼스 의존·무경계)나
코사인 유사도로 바꾸면 정규화 기준이 없다 — 그 정규화 불가능성이
`07_search_rrf_reevaluation.md` 3·5절에서 RRF 를 채택한 근거다. 엔드포인트 경로에서
버린 구조를 문서 경로에 새로 심는 셈이다.

"per-document 1건 랭킹이라 RRF 의미가 약하다"는 관찰은 맞지만, 그건 (A) 구조가
만들어낸 결과이지 (A) 를 정당화하는 근거가 아니다.

## 2. 채택안 (B'): section 청크 3-arm RRF, 융합 키는 `Document.id`

### 2.1 arm 구성

| arm | 소스 | 순위 리스트 |
|---|---|---|
| title | `document_meta` (`search_by_tokens` + `_title_score`) | 기존 1단계 결과를 title_score 내림차순으로 |
| keyword | `chunk` (`chunk_type="section"`, project/source 스코프) FTS | `ts_rank` 내림차순 |
| vector | 같은 스코프 pgvector 코사인 | 유사도 내림차순 |

- 후보 폭은 엔드포인트 경로와 동일 규칙(`max(top_k * 4, 50)`) 을 쓴다.
- 벡터 arm 은 `is_semantic=False` 배포에서 조용히 생략하고 나머지 arm 으로 degrade
  한다(`EndpointCandidateSearch` 와 동일 규약).
- **title 을 "별도 부스트"나 "합집합"으로 두지 않는다.** 부스트는 1.3 의 스케일
  문제를 축소판으로 다시 들여오고, 합집합은 두 집합의 상대 순위를 정의하지 못한다.
  RRF 는 arm 을 몇 개 붙이든 등수만 쓰므로 title 을 arm 으로 넣는 게 가장 싸고
  일관된다.

### 2.2 융합 키

**반드시 `Chunk.document_id`(= `Document.id`) 로 접어서 융합한다.** `ref_id`
(`DocumentSection.id`) 로 융합하면 문서 하나가 섹션 수만큼 결과 슬롯을 먹는다.
각 arm 안에서 같은 문서의 첫 히트만 남기면 되고, 그 dedupe 는
`rrf._dedupe_first` 가 이미 한다 — `reciprocal_rank_fuse` 는 불투명 문자열 키를
받으므로 **수정 없이 재사용한다**(단 arm 이 3개이므로 3번째 리스트를 받는 인자
추가가 필요하다. 기존 두 인자는 유지해 엔드포인트 호출부를 건드리지 않는다).

title arm 의 키도 같은 공간이어야 하므로
`deterministic_document_id(project, source, external_id)`
(`document_body_indexer.py:31`) 로 계산한다 — 색인 여부와 무관하게 순수 함수로
동일 키가 나온다.

역방향(청크 arm 결과 → 표시용 메타)은 해시라 역산이 불가하므로
`DocumentMetaRepository.list_by_document_ids(ids)` 한 건을 추가해 배치 조회한다
(Phase1 에서 채운 `document_meta.document_id` FK 사용). 문서당 조회 금지.

### 2.3 폴백 — 별도 분기를 만들지 않는다

질문의 "`document_id IS NOT NULL` 이면 신규 경로, NULL 이면 기존 fetch 경로"는
불필요하다. title arm 이 색인 여부와 무관하게 모든 `document_meta` 행을 계속
커버하므로, 미색인 문서는 **title arm 단독 문서로 자연 degrade** 한다. 색인된
문서만 body arm 에서 추가 신호를 받는다. 즉 부분 색인 상태가 구조적으로 안전하다.

따라서 "실제 section 청크 존재까지 확인해야 하나"라는 걱정도 해소된다 —
`Document` 는 있는데 청크가 없는 문서(빈 본문, 색인 중단)도 title arm 으로 남는다.
다만 스코프 전체에 section 청크가 0건이면 임베딩 호출이 낭비이므로,
`has_endpoint_chunks` 를 `chunk_type` 인자화(11번)해 `has_chunks(chunk_type="section", ...)`
로 early-exit 하는 것만 붙인다.

### 2.4 스니펫

승자 청크 text 로 `_build_snippet` 재사용 — **동의**. 라이브 fetch 는 검색 경로에서
완전히 제거한다(`get_document` 에만 남긴다, doc35 13번). 세부:

- 청크 arm 에 등장한 문서: 그 문서의 **최상위 히트 청크 1건**의 text 로 스니펫.
  청크 text 는 chunk_id 배치 조회 1회로 가져온다(문서당 조회 금지).
- title arm 단독 문서: 기존 `_fallback_snippet(row, query)`. fetch 하지 않는다.
- doc35 Phase0-2 가 예고한 유일한 겉면 계약 변경이 여기서 발생한다(스니펫 출처가
  동기화 시점 본문). `DocumentSearchItem` 에 `snippet_as_of: datetime | None` 를
  추가해 명시한다(title arm 단독 문서는 None — 스니펫이 본문 발췌가 아니므로).

### 2.5 점수 필드

`DocumentSearchItem.score` 는 RRF 점수를 그대로 담는다(0.0x 스케일). 기존 가중합
점수와 절대값이 다르며 **순서 정보만 의미가 있다** — docstring 에 명시한다.
`match_type` 은 문서 검색 계약에 없던 필드이므로 추가하지 않는다.

### 2.6 스코프 거는 방식

엔드포인트 경로는 `list_endpoint_chunk_ids()` 로 후보 ID 집합을 만들어
`candidate_ids` 로 넘긴다. section 청크는 문서 수 × 섹션 수라 ID 집합이 훨씬 커져
`IN` 절이 부풀 수 있다. **신규 경로는 `search_by_vector` 에 `document_id`/`project`
스코프 인자를 넘겨 SQL 조인으로 거른다**(FTS 쪽은 이미 그렇게 한다). 조회 1회가
통째로 사라져 코드도 짧다. 엔드포인트 경로를 같은 방식으로 이관하는 건 이번 범위가
아니다 — 별건으로 남긴다.

### 2.7 플래그

**`config.py` Settings env 로 노출한다.** `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY`,
값은 `fetch`(기본) | `indexed`. 이유는 두 가지다.

1. 롤아웃/롤백 스위치는 운영자가 재배포 없이 되돌릴 수 있어야 한다 —
   `search_strategy` 가 이미 그 전례다(엔드포인트 `rrf`/`fallback`).
2. 생성자 bool 로 두면 composition 이 결국 Settings 를 읽어 넘기게 되므로 결과가
   같고, 이름만 비대칭해진다.

생성자는 `EndpointCandidateSearch` 처럼 **원시 문자열**을 받아 비교로 분기한다.
단 degrade 방향은 반대다: 인식 못 하는 값은 **`fetch`(기존 경로)로 degrade** 한다 —
롤아웃 중에는 검증된 경로가 안전한 쪽이다.

## 3. developer 지시 사항

1. 11번(`chunk_type` 인자 승격)은 doc35 대로. 기본값 `"endpoint"` 를 유지해 기존
   호출부는 무변경.
2. `reciprocal_rank_fuse` 에 3번째 arm 리스트 인자 추가(기본 빈 리스트). 기존
   시그니처·골든 테스트 무변경.
3. `ChunkTextHit`/`ChunkVectorHit` 에 `document_id` 프로젝션 추가.
4. `DocumentMetaRepository.list_by_document_ids` 추가.
5. `DocumentSearchService` 는 `document_search_strategy` 인자를 받고, `indexed`
   일 때만 신규 경로. 기존 fetch 경로 코드는 **이번 단계에서 지우지 않는다**
   (doc35 13번은 전환 완료 후).
6. 테스트: (a) 제목에 질의 토큰이 전혀 없고 본문에만 있는 문서가 상위에 오는 회귀
   테스트 — 이게 Phase3 의 존재 이유다. (b) 한 문서의 여러 섹션이 히트해도 결과가
   1건으로 접히는 테스트. (c) 미색인 문서(청크 없음)가 title arm 으로 살아남는 테스트.
   (d) `fetch` 전략에서 기존 동작 무변경.
