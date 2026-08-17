# 47. Notion nested block 색인 갭 조사 및 색인·검색 설계안

- 상태: 설계안 (lead 승인 대기)
- 요청: notion mcp 없이 docs-mcp 자체 검색만으로 Notion 검색 수준 재현.
  `'트러블슈팅 내역 찾아줘'` 질의에서 (1) 페이지 내 하위 데이터베이스 행
  (2) 페이지 내 하위 페이지 (3) 본문 텍스트 일부까지 모두 검색되어야 함.
- 관련: `39`(3-arm RRF), `43`(indexed 기본 전환), `45`(agent-oriented RAG 검증),
  `33`/`34`(Notion page id / API 버전)

## 1. 결론 먼저

**검색 로직(3-arm RRF)은 이미 요구를 만족한다. 구멍은 전부 수집 단계
(`NotionSource.list_pages` / `NotionSource.fetch`)에 있다.**

`document_search_service._search_indexed` 는 title arm(`document_meta`) +
keyword arm(FTS) + vector arm(pgvector)을 `Document.id` 로 융합한다. 하위
페이지·DB 행이 각각 독립 `document_meta` 행 + `Document` + section 청크로
색인되기만 하면, 검색 코드를 한 줄도 고치지 않고 세 요구가 모두 충족된다.

따라서 이 문서의 설계안은 **어댑터(`notion_source.py`) 한 파일 + 운영 기본값
1개**에 집중한다. 검색/색인 파이프라인 구조 변경은 제안하지 않는다.

## 2. 현행 동작 조사

### 2.1 파이프라인 전체 흐름

```
refresh_index(index_bodies=True)
  → DocumentIndexService._refresh_source
      → NotionSource.list_files()        # 색인 "단위"를 정하는 곳
      → document_meta upsert
      → NotionSource.fetch(external_id)  # 본문 평문
      → index_document_body
          → markdown_parser.parse_document(raw, title_hint)   # 헤딩 단위 섹션 분리
          → IndexerService.index_document → chunk_builder → section 청크(+임베딩)
검색: DocumentSearchService._search_indexed  # title + FTS + vector 3-arm RRF
```

### 2.2 요구 (1) 하위 데이터베이스 행

| 설정 | 동작 |
|---|---|
| `kind="page"` (허브) | `_collect_child_pages` 가 `child_database` 를 만나면 `/databases/{id}/query` 로 행을 얻어 **독립 문서로 목록화**. 행 하위도 재귀. → **커버됨** |
| `kind="database"` | `list_pages()` 가 `/databases/{id}/query` 결과만 `_to_file_meta` 로 변환하고 **끝**. 각 행 페이지 안에 있는 child_database 는 목록화되지 않음. → **미커버** |

본문 경로로도 안 잡힌다: `_collect_block_text` 는 `block["has_children"]` 일
때만 재귀하는데 `child_database` 블록은 `has_children=false` 이고,
`_block_plain_text` 는 `child_database` payload 에서 `rich_text` 를 찾다
실패해 `""` 를 낸다 — **DB 제목조차 본문에 안 들어간다.**

### 2.3 요구 (2) 하위 페이지

| 설정 | 동작 |
|---|---|
| `kind="page"` | `_collect_child_pages` 로 depth 4 / 500건 상한까지 재귀 목록화. 단 **toggle·column·callout 안에 중첩된 `child_page` 는 누락**(현행 docstring이 스코프 밖이라 명시) |
| `kind="database"` | 미커버(2.2 와 동일 이유) |

본문 텍스트는 반대로 **과하게** 섞인다: `child_page` 블록은 `has_children=true`
이므로 `_collect_block_text` 가 하위 페이지 본문까지 부모 문서 본문으로
빨아들인다. 결과적으로

- 하위 페이지 텍스트가 부모/자식 두 문서에 **중복 색인**되고,
- `MAX_BLOCKS=2000` 예산을 하위 페이지가 잠식하며,
- 부모 문서가 히트해도 스니펫·URL 은 부모를 가리켜 **출처가 어긋난다**.

`child_page` 블록 자체의 제목(`child_page.title`)은 `rich_text` 가 아니라
역시 유실된다.

### 2.4 요구 (3) 본문 텍스트

`_block_plain_text` 는 `block[block_type]["rich_text"]` 하나만 본다. 그래서
`paragraph`/`heading_*`/`bulleted_list_item`/`numbered_list_item`/`to_do`/
`toggle`/`quote`/`callout`/`code` 는 잡히지만 **다음이 전부 유실**된다.

| 유실 대상 | 이유 | 영향 |
|---|---|---|
| `table` / `table_row` | 셀이 `cells: [[rich_text]]` 구조라 `rich_text` 키 없음 | **가장 큰 구멍.** 트러블슈팅 내역은 Notion 단순 표로 쓰는 경우가 지배적 |
| DB 행 페이지의 `properties` | `fetch()` 가 `/blocks/{id}/children` 만 보고 `/pages/{id}` 를 안 읽음 | 상태·태그·담당자·날짜 등 DB 행 정보의 절반이 색인 안 됨 |
| `child_page.title`, `child_database.title` | `rich_text` 아님 | 하위 문서 이름으로 부모가 안 잡힘 |
| `image`/`file`/`video`/`pdf` 의 `caption` | payload 에 `rich_text` 대신 `caption` | 캡션 검색 불가 |
| `bookmark`/`embed`/`link_preview` 의 `url`, `caption` | 동일 | 링크 텍스트 검색 불가 |
| `equation.expression` | `rich_text` 아님 | 경미 |

추가로 **헤딩 마커가 소실**된다. `heading_1` 은 평문만 남으므로
`markdown_parser.parse_document` 의 `^#{1,6}\s+` 정규식에 걸리지 않는다.
→ Notion 문서는 **항상 "개요" 섹션 1개**로 파싱되고, `section_splitter` 가
480토큰 그리디로 임의 지점에서 자른다. 모든 청크 머리에 붙는 앵커가
`# 개요` 뿐이라, 섹션 제목이라는 최고신호 문맥이 청크에 실리지 않는다.
FTS·벡터 양쪽 정확도가 여기서 크게 깎인다.

### 2.5 운영 기본값

`refresh_index(index_bodies=False)` 가 기본이다(`app/mcp/tools/sources.py:33`).
이 상태로 갱신하면 keyword/vector arm 이 비어 검색이 **제목 매칭만**으로
조용히 퇴화한다. 코드에 경고 로그는 있으나(`document_index_service.py:188`)
기본값 자체는 그대로다.

### 2.6 문제 없는 것 (변경 불필요)

- 3-arm RRF 융합 키(`deterministic_document_id`)는 미색인 문서도 title arm
  단독으로 남긴다 — 하위 문서가 늘어도 구조 변경 불필요.
- `doc_types` 필터가 등록형 문서 청크 혼입을 SQL 단에서 막는다(45번).
- `text_tsv` 는 한글/ASCII 경계 분리를 이미 처리한다(`chunk.py:27`).
- `_collapse_match_score` 가 `'트러블슈팅'` vs `'트러블 슈팅'` 공백 변형을 흡수한다.

## 3. 설계안

### P0 — 어댑터 본문 추출 보강 (`notion_source.py`, 효과 대비 최소 diff)

**P0-1. `_block_plain_text` 를 블록 타입별 추출로 확장**

`rich_text` 단일 경로 대신, payload 에서 아래를 순서대로 이어 붙인다.

```
rich_text (기존)  +  caption  +  cells(평탄화, " | " 조인)
+ child_page.title / child_database.title
+ bookmark|embed|link_preview 의 url
+ equation.expression
```

표는 `table_row.cells` 를 `" | "` 로 조인해 한 줄로 만든다 — 행 단위 한 줄이
FTS·임베딩 양쪽에서 가장 자연스럽다.

**P0-2. 헤딩·리스트 마커 복원**

`heading_1|2|3` → `"# "`/`"## "`/`"### "` 접두, `bulleted_list_item` → `"- "`,
`numbered_list_item` → `"1. "`, `to_do` → `"- [ ] "`/`"- [x] "` 를 붙인다.
이 한 줄짜리 변경이 `markdown_parser` 를 실제로 동작시켜 문서를 **헤딩 단위
섹션**으로 쪼개고, 각 청크가 `# {섹션 제목}` 앵커를 갖게 한다. 본문 검색
정확도 개선폭이 P0 중 가장 크다.

**P0-3. 페이지 properties 를 본문 머리에 첨부**

`fetch()` 시작 시 `GET /pages/{id}` 1회를 추가하고, `properties` 를
`"{속성명}: {값}"` 줄들로 평문화해 블록 텍스트 앞에 둔다. 지원 타입:
`title`/`rich_text`/`select`/`multi_select`/`status`/`date`/`people`/`number`/
`url`/`checkbox`/`formula`(평문 결과 — 값이 페이지 응답에 이미 있어 추가 호출
0회). 미지원 타입은 건너뛴다. **`relation`/`rollup` 은 제외한다** — 상세 근거는
아래 §6 판정 참고.

- 비용: 문서당 API 호출 +1. `MAX_BODY_FETCH_CANDIDATES` 상한이 이미 있고,
  본문 색인은 `content_hash` 게이트가 걸린 배치 작업이라 수용 가능하다.
- 실패는 삼키고 블록 텍스트만으로 진행한다(문서 1건 색인이 통째로 실패하지
  않게).

**P0-4. 본문 재귀에서 `child_page` 중단**

`_collect_block_text` 가 `child_page` 를 만나면 제목만 남기고 **재귀하지
않는다**(P1-1 이 하위 페이지를 독립 문서로 목록화하므로 중복 제거).
`MAX_BLOCKS` 예산이 실제 본문에만 쓰이고, 스니펫 출처가 정확해진다.

### P1 — 목록 커버리지 (`notion_source.py`)

**P1-1. `kind="database"` 에서도 행 하위를 재귀 목록화**

`list_pages()` 의 database 분기에서, 얻은 각 행에 대해 기존
`_collect_child_pages` 를 그대로 호출한다. 코드 재사용만으로 2.2/2.3 의
`kind="database"` 미커버가 사라진다.

**P1-2. `_collect_child_pages` 가 중첩 컨테이너를 통과**

현재는 페이지 직속 자식만 본다. `child_page`/`child_database` 가 아니면서
`has_children=true` 인 블록(toggle, column_list, column, callout, synced_block,
list item)은 **하위 페이지 수집 목적으로 계속 내려간다**. 이때
`MAX_PAGE_DEPTH` 는 "페이지 중첩 깊이"로만 세고 컨테이너 하강은 별도
카운터(예: `MAX_CONTAINER_DEPTH = 3`)로 제한해, 페이지 깊이 예산이 토글
때문에 소진되지 않게 한다.

**P1-3. 상한 재검토**

P0-4 로 본문 재귀에서 하위 페이지가 빠지면 `MAX_BLOCK_DEPTH=4` 가 순수 본문
중첩에만 쓰인다. 현행 유지로 충분하다고 본다 — 조정은 실측 후에.

### P2 — 운영 기본값 (lead 결정 필요)

**P2-1. `refresh_index` 의 `index_bodies` 기본값을 `True` 로 전환**

`document_search_strategy="indexed"` 가 이미 기본(43번)인데 본문 색인이
옵트인이면, 기본 경로끼리 어긋나 title-only 로 조용히 퇴화한다. 비용
증가(문서당 fetch 1~2회)를 감수하고 기본값을 맞추자는 제안이며, **비용
정책 판단이라 lead 승인 대상**이다. 반대면 최소한 `index_bodies=False` 를
`refresh_index` 응답에 경고 필드로 노출해 호출자(Claude)가 알 수 있게 한다.

### 하지 않을 것 (YAGNI)

- 검색 로직·RRF 가중치·arm 추가 변경 — 2.6 참고, 수집만 고치면 된다.
- Notion `/search` 엔드포인트 병용 — `project_source.location` 이 필수라
  워크스페이스 전역 스코프는 현 데이터 모델과 맞지 않는다.
- 본문 캐시 정책 변경 — `document`/`chunk` 가 이미 캐시다.
- 블록 트리를 그대로 보존하는 별도 계층 색인 — 평문 + 헤딩 마커(P0-2)로
  markdown 파이프라인을 태우는 쪽이 기존 자산을 그대로 쓴다.

## 4. 검증 기준

`'트러블슈팅 내역 찾아줘'` 질의 기준으로:

1. 허브 페이지 하위 DB 의 "트러블슈팅" 행이 **독립 결과**로 나온다 (P1-1).
2. 토글 안에 중첩된 하위 페이지가 결과에 나온다 (P1-2).
3. 표 셀에만 "트러블슈팅" 이 있는 페이지가 결과에 나오고, 스니펫이 그 표
   행을 보여준다 (P0-1).
4. DB 행의 `상태: 해결` 같은 property 값으로도 검색된다 (P0-3).
5. 같은 텍스트가 부모/자식 두 결과로 중복 노출되지 않는다 (P0-4).
6. 섹션 헤딩이 있는 문서의 청크가 `# 개요` 가 아닌 실제 헤딩을 앵커로 갖는다
   (P0-2) — `chunk.text` 표본 확인.

단위 테스트는 `tests/unit/test_notion_page_source.py` 에 블록 JSON 픽스처로
P0-1/P0-2/P0-4, `list_pages` 픽스처로 P1-1/P1-2 를 덮는다.

## 5. 작업 순서 제안

P0-2 → P0-1 → P0-4 → P1-1 → P1-2 → P0-3 → (승인 시) P2-1.

P0-2 를 먼저 하는 이유: 나머지 변경의 효과가 모두 "섹션이 제대로 쪼개진다"는
전제 위에서 측정되기 때문이다.

## 6. P0-3 속성 색인 범위 판정 — relation/formula (doc/51 흡수)

- 상태: 판정 완료 (부분 승인)
- 발단: reviewer 리뷰 지적 — `property_plain_text` 가 `relation`/`formula` 를 빈
  문자열로 버리는데, 위 §3 P0-3 최초안은 둘 다 지원 타입으로 명시했고 축소에 대한
  이탈 표시·승인 기록이 없었다.
- 관련: `docs/superpowers/plans/2026-08-15-notion-nested-block-indexing.md` Task 7

### 6.1 지적의 타당성

**타당하다.** 그리고 이탈을 만든 쪽은 developer 가 아니라 **architect** 다.

이 문서 §3 P0-3 최초안은 지원 타입으로 `relation(id 만)`·`formula(평문 결과)` 를 적었는데,
구현 계획(`2026-08-15-notion-nested-block-indexing.md`) Task 7 을 쓰면서 "비용 대비 이득이
작다"는 사유로 두 타입을 조용히 뺐다. developer 는 계획서를 그대로 따랐을 뿐이므로
developer 측 설계 이탈이 아니다. 계획서가 설계 문서를 축소할 때 그 사실을 표시하지 않은
것이 절차 결함이다.

reviewer 의 기술적 지적도 맞다 — **둘 다 추가 API 호출이 필요 없다.** 두 값 모두 이미
`GET /pages/{id}` 응답의 `properties` 안에 들어 있다. 계획서에 적힌 "비용 대비 이득" 사유는
`relation` 에 대해서만 절반 맞고(대상 페이지 **제목**을 얻으려면 추가 호출이 필요하다),
`formula` 에 대해서는 **틀렸다**.

### 6.2 판정

**`formula` — 반영 (스펙대로).** 승인한다. 결과값이 페이지 응답 안에 이미 있어 추가
호출이 0회이고, "경과일", "우선순위 점수" 같은 계산 결과는 실제 검색 신호가 된다. 값이
`{"type": "string"|"number"|"boolean"|"date", ...}` 로 한 겹 더 감싸여 있으므로
`property_plain_text` 를 그 안쪽에 재귀 적용하면 기존 분기를 그대로 재사용할 수 있다
(신규 로직 최소).

**`relation` — 반려 (§3 P0-3 최초안 쪽을 정정한다).** `relation(id 만)` 은 **스펙이 틀렸다.**
구현이 아니라 스펙을 고친다. 사유는 "비용"이 아니라 **색인 품질 훼손**이다.

1. `relation` 값은 `[{"id": "<uuid>"}]` 뿐이다. 사람이 검색하는 문자열은 대상 페이지의
   **제목**이지 UUID 가 아니다 — UUID 로 검색하는 이용자는 없다.
2. `chunk.text_tsv` 생성식(`app/models/chunk.py:27`)은 영숫자·한글이 아닌 문자를 공백으로
   치환한다. UUID 는 하이픈에서 쪼개져 `8f3a`, `4b2c` 같은 의미 없는 lexeme 5개로
   색인된다. **recall 이득 0, 인덱스 부피와 노이즈만 증가.**
3. 임베딩 쪽은 더 나쁘다. 의미 없는 hex 토큰이 청크 벡터를 희석해 같은 청크의 실제 본문
   신호를 약화시킨다.
4. 유용한 형태(대상 페이지 제목)를 얻으려면 relation 개수만큼 추가 호출이 필요한데, 이는
   이 문서가 명시적으로 범위 밖으로 둔 항목이다.

즉 `relation` 은 "지금 넣기엔 비싸다"가 아니라 **id 형태로는 넣지 않는 것이 맞다.** 후속으로
넣는다면 반드시 대상 페이지 제목 해소(추가 호출 + 캐시)를 동반해야 하며, 그때 별도 설계
판단 대상이다.

**`rollup` — 현행 유지 (범위 밖).** 이 문서 최초 스펙에 없었고 이번 지적 대상도 아니다.
값이 중첩 배열이라 평문화 규칙을 따로 정해야 하므로 실제 수요가 확인되면 그때 다룬다.

### 6.3 developer 조치 지시 (반영 완료)

`app/services/documents/sources/notion_blocks.py` `property_plain_text` 만 수정.

1. `formula` 분기를 추가한다 — 값 안쪽에 자기 자신을 재귀 적용한다.
2. 재귀가 닿는 스칼라 타입을 받게 한다: `"string"` 을 기존 `number/url/email/phone_number`
   튜플에 추가하고, `"boolean"` 을 `checkbox` 분기에서 함께 처리한다. 이때 값이 `None` 이면
   `"false"` 가 아니라 `""` 를 낸다(수식 결과 없음을 `false` 로 색인하면 없는 신호가 생긴다).
3. docstring 을 판정에 맞게 고친다 — `relation`/`rollup` 제외 사유를 "비용 대비 이득"이
   아니라 **"UUID 는 검색 신호가 아니고 tsvector·임베딩을 오염시킨다"** 로 정확히 적는다.
4. 테스트를 추가한다: formula string/number/date/boolean 각 1건, formula 결과가 비었을 때
   `""`, `relation` 은 계속 `""`(의도적 제외를 고정하는 가드).

### 6.4 절차 개선 (architect 자책 항목)

구현 계획이 설계 문서의 범위를 **좁힐 때**는 계획서에 그 사실과 사유를 명시하고, 설계
문서 쪽도 같이 고쳐 두 문서가 어긋난 채로 남지 않게 한다. 이번처럼 계획서만 조용히
줄이면 리뷰 단계에서야 불일치가 드러나고, 그 사이 구현은 이미 끝나 있다.
