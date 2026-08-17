# 53. 데이터 흐름 시나리오별 정리 (발표 다이어그램 보완 자료)

- 작성: architect
- 대상: `docs/architecture-presentation-diagram.html` 보완 설명
- 근거: 코드 기준(2026-08-17, `main` @ 023e1a9)

## 0. 전제 — 모든 시나리오에 공통으로 깔리는 3가지

1. **MCP 도구는 전부 `async def` 이지만 본문은 동기 실행이다.**
   `run_bundle_tool` 이 `anyio.to_thread.run_sync` 로 워커 스레드에 넘겨 실행하고,
   호출자는 그 결과를 기다린다(`app/mcp/tools/_common.py:36-51`).
   **백그라운드 작업 큐가 없다** — 색인이 필요한 도구는 색인이 끝나야 응답한다.
   진짜 비동기(호출자가 기다리지 않는) 경로는 OS 스케줄러가 도는 별도 프로세스
   (`app/scripts/refresh_documents.py`) 하나뿐이다.
2. **DB 세션은 도구 호출 1회당 1개다.** `build_services` 가 세션·저장소·서비스·
   `ProjectSourceResolver` 를 매 호출마다 새로 만들고 끝나면 닫는다
   (`app/composition.py:178-277`). 그래서 `register_drive_source` 로 매핑을 바꾸면
   서버 재시작 없이 다음 호출부터 반영된다.
3. **임베딩은 외부 API 가 아니다.** 로컬 CPU SentenceTransformer 를 쓴다
   (`app/services/indexer/embedding_provider.py:141-222`). 발표에서 "임베딩 =
   OpenAI 호출" 로 오해받기 쉬운 지점 — 외부 호출은 Drive/Notion/OpenAPI URL 뿐이다.

외부 API 호출 지점은 딱 3곳이다.

| 호출처 | 대상 | 코드 |
| --- | --- | --- |
| `GoogleDriveSource.list_files/fetch` | Drive REST v3 (httpx) | `google_drive_source.py:195,225` |
| `NotionSource.list_pages/fetch` | Notion API v1 (httpx) | `notion_source.py:109,149` |
| `OpenAPIFetcher.fetch` | 등록 문서 `source_url` | `sync_service.py:88,153` |

---

## 케이스 1 — 검색 요청, 대상 문서가 이미 색인돼 있음

```
Claude → search_documents
      → run_bundle_tool(스레드) → build_services(세션 1개)
      → DocumentSearchService.search  [전략 = indexed]
         ├ title arm   : document_meta 토큰 매칭 (SQL)
         ├ keyword arm : chunk FTS  (chunk_type='section', doc_types=[drive,notion])
         └ vector arm  : embed_query(로컬) → pgvector 검색
      → RRF 융합 → 승자 청크 text → 스니펫 → top_k
```

| 항목 | 값 |
| --- | --- |
| 컴포넌트 | `mcp/tools/documents.py:114-164` → `document_search_service.py:225-269` → `_search_indexed:478-547` |
| DB | **전부 히트**. `document_meta`(`_title_arm:579-611`), `chunk` FTS(`_keyword_arm:613-631`), `chunk` 벡터(`_vector_arm:633-648`), `project_source`(`_require_configured:677-697`) |
| 외부 API | **0회** — 이 경로엔 라이브 fetch 자체가 없다 |
| 동기/비동기 | 동기(호출자 대기), 서버 내부는 워커 스레드 1개 |

세부 근거:

- 벡터 arm 의 질의 임베딩은 **요청당 1회**다(`_vector_arm:643`). 후보마다 부르는 N+1 은
  doc39 §1.2 에서 반려됐다. `LocalEmbeddingProvider.embed_query` 는 LRU 캐시(256)까지 탄다
  (`embedding_provider.py:156,215-217`).
- keyword/vector arm 은 `has_endpoint_chunks(chunk_type='section')` 가 True 일 때만 돈다
  (`document_search_service.py:513`) — 청크가 하나도 없는 DB 에서 헛 SQL 을 안 쏜다.
- 스니펫은 승자 청크 text 에서 만들고, `snippet_as_of = document_meta.last_synced_at` 로
  "언제 시점의 본문인가"를 응답에 실어 보낸다(`_build_indexed_item:549-577`).
- `search_endpoints`(OpenAPI 경로)도 같은 성질이다 — 전부 DB, 외부 호출 0
  (`services/search/endpoint_candidate_search.py`).

발표용 한 줄: **가장 흔한 경로이자 가장 싼 경로. 네트워크 0회, DB 3-arm.**

---

## 케이스 2 — 검색/조회 요청인데 DB에 아직 없음

이 케이스는 **세 갈래로 갈리며, 결과가 서로 다르다.** 발표에서 뭉뚱그리면 안 되는 지점.

### 2-a. 메타도 본문도 없음 (`search_documents`)

- title arm 0건 + keyword/vector arm 0건 → `fused` 가 비어 **즉시 빈 리스트**
  (`document_search_service.py:520-522`).
- **외부 API 0회.** 서버는 "없으니 지금 가서 가져오자"를 하지 않는다 —
  on-demand 색인 경로가 존재하지 않는다.
- 해소 수단은 `refresh_index` 실행(케이스 5) 뿐이다. 도구 docstring 에도 그렇게 적혀 있다
  (`mcp/tools/documents.py:126-128`).
- 단, 소스가 아예 구성되지 않았으면 빈 리스트가 아니라 `IntegrationError` 다 —
  "결과 없음"과 "서버 미설정"을 일부러 구분한다(`_require_configured:677-697`).

### 2-b. 메타는 있는데 본문 미색인 (`document_meta.document_id IS NULL`)

- title arm 단독으로 결과에 남는다. 융합 키가 `deterministic_document_id` 라
  색인 여부와 무관하게 같은 키 공간에서 만나기 때문에 **별도 폴백 분기가 없다**
  (`_title_arm:596-611`, doc39 §2.3).
- 스니펫은 청크가 없으니 `_fallback_snippet`, `snippet_as_of=None`
  (`_build_indexed_item:564-566`). 응답만 봐도 "제목만 걸린 문서"임을 알 수 있다.
- 외부 API 0회.

### 2-c. 롤백 스위치 `document_search_strategy="fetch"` 인 경우

기본값이 아니지만 다이어그램의 "점선 = fetch degrade" 가 가리키는 경로라 정리한다.

- 1단계: `document_meta` 토큰 매칭으로 후보 압축(`_select_candidates:339-381`).
  **후보 0건이면 외부 API 를 한 번도 부르지 않고 종료**(`:264-267`).
- 2단계: 후보 상위 `_body_fetch_budget` 건만 본문 라이브 fetch
  (`:66-77`, `top_k*3`, 상한 20) → `ThreadPoolExecutor(max_workers≤5)` 병렬
  (`_rank_with_body:385-427`).
- 개별 fetch 실패는 그 문서만 건너뛴다(`_fetch_and_score:454-462`).
- 주의할 설계점: resolver 는 요청 스코프 Session 을 읽으므로 **메인 스레드에서 미리
  resolve** 하고 워커에는 순수 I/O 인 `fetch()` 만 넘긴다(`:413-424`).

발표용 한 줄: **"DB에 없으면 서버가 대신 가져다준다"가 아니다. 기본 경로는 없으면 없다고 답한다.**

---

## 케이스 3 — `get_document` 로 원문 실시간 조회

```
Claude → get_document(source, external_id)
      → document_meta 포인트 조회 (title/url/project 확보용, 1행)
      → ProjectSourceResolver → 어댑터
      → 어댑터.fetch()  ← 외부 API (여기가 유일한 목적)
      → 본문 그대로 반환 (DB 쓰기 없음, 캐시 없음)
```

| 항목 | 값 |
| --- | --- |
| 컴포넌트 | `mcp/tools/documents.py:166-185` → `document_search_service.get_document:271-315` |
| DB | **읽기 2회**: `document_meta` 포인트 조회(`_find_meta_row:317-325`), `project_source`(resolver). **쓰기 0회** |
| 외부 API | **필수 1건 이상.** Drive: `GET /files/{id}`(mimeType) + export 또는 `alt=media`(`google_drive_source.py:254-266`). Notion: `GET /pages/{id}`(속성) + `/blocks/{id}/children` 재귀·페이지네이션(`notion_source.py:170-176`) |
| 동기/비동기 | 동기. 문서가 크면 이 도구가 세션에서 가장 느린 호출이 된다 |

세부:

- **본문은 절대 캐시하지 않는다.** 항상 호출 시점의 최신 원문이다
  (`document_search_service.py:16-17`).
- 메타 캐시 미스여도 실패가 아니다 — `project` 를 `DEFAULT_PROJECT` 로 폴백해 fetch 하고,
  `title`/`url` 만 `""` 로 나간다(`:304,308-315`). `content` 는 그래도 최신 원문이다.
- 절단 상한: Drive/Notion 모두 `max_chars`(기본 200,000) 로 자르고 `truncated` 를 실어 보낸다
  (`google_drive_source.py:267-268`, `notion_source.py:175-176`).
- Notion 재귀 상한: 블록 깊이 4 / 블록 2000 / 컨테이너 깊이 3
  (`notion_source.py:35-45`) — 무한 재귀·과금 폭주 방지.

---

## 케이스 4 — 색인이 도는 중에 동시에 요청이 들어옴

핵심: **읽기와 쓰기는 서로 다른 세션(대부분 다른 프로세스)이고, 커밋 경계가 100건이다.**

| 항목 | 값 |
| --- | --- |
| 읽기 측 | 도구 호출마다 새 세션(`composition.py:183`), READ COMMITTED |
| 쓰기 측 | 배치 프로세스 또는 `refresh_index` 도구, `BATCH_SIZE=100` 마다 커밋(`document_index_service.py:52,448-464`) |
| 결과 | 검색은 **커밋된 배치까지만** 본다. 색인 중간 상태(일부 문서만 최신)가 정상적으로 보인다 |

지켜지는 것:

- **문서 1건의 재색인은 리더에게 원자적으로 보인다.** `index_document_body` 는 기존 청크
  delete → 새 청크 insert 를 같은 트랜잭션에서 하고(`document_body_indexer.py:100-112`),
  커밋 경계 판정은 문서 1건 처리가 끝난 **뒤에만** 일어난다
  (`document_index_service.py:291-292`). 그래서 "청크가 지워졌는데 아직 안 채워진" 상태가
  커밋돼 검색에 노출되는 일은 없다.
- **부분 실패가 남는다.** 도중에 터져도 직전 배치까지는 DB 에 남고, 미완성 배치만 롤백된다
  (`_refresh_source:300-312`). 집계도 **실제 커밋된 것만** 센다.
- **커밋 경계 카운터에 `fetched_bodies` 가 포함된다**(`_SourceCounts.total_changes:91-101`).
  이게 없으면 메타 무변경 백필에서 카운터가 영영 0 이라 소스 하나를 다 끝내야 커밋되고,
  마지막에 1건 실패하면 본문 색인 전량이 롤백된다.

리스크로 남는 것(발표에서 "지금은 이렇게 막고 있다"로 말할 지점):

- **advisory lock 은 배치 CLI 에만 있다.** `refresh_documents.py:75-79` 가
  `pg_try_advisory_lock` 으로 축 A(733100501)/축 B(733100502) 를 따로 잠근다.
  **MCP `refresh_index` 도구에는 락이 없다**(`mcp/tools/sources.py:27-88`).
  즉 배치가 도는 중에 사용자가 `refresh_index` 를 부르면 두 writer 가 같은
  `document_meta` 행에 동시에 붙을 수 있다(UniqueConstraint `(project, source, external_id)`,
  `models/document_meta.py:48-51`). 현재는 "사람이 동시에 부르지 않는다"에 기대고 있다.
- 축 A/B 를 다른 락 키로 가른 이유는 주기 차이다(1시간 vs 1일) — 무거운 축 B 가 가벼운 축 A 의
  틱을 굶기지 않게 한다(`refresh_documents.py:20-23`).

---

## 케이스 5 — OS 스케줄러 주기 갱신 (축 A / B / C)

```
systemd timer(cron) → uv run python -m app.scripts.refresh_documents [옵션]
   → bootstrap_app_state → build_services (자체 세션)
   → pg_try_advisory_lock  ── 실패 시 INFO 로그 + exit 0 (겹치면 그냥 건너뜀)
   → document_index_service.refresh(source, project, index_bodies)   … 축 A(+C)
   → [--include-registered] resync_registered_documents(...)          … 축 B
   → exit 0 / 1
```

진입점: `app/scripts/refresh_documents.py:127-141`(`run`), `:82-124`(`_execute`).
종료코드: 전 대상 실패만 `1`, 부분 실패·락 미획득은 `0`(`:43-44,86-96`).
MCP 도구 `refresh_index` 와 **같은 서비스 함수**를 부르므로 즉시 실행 경로와 정기 실행 경로의
동작이 항상 일치한다.

### 축 A — 메타 캐시 동기화 (기본, 잦게)

| 항목 | 값 |
| --- | --- |
| 코드 | `document_index_service.refresh:151-222` → `_refresh_source:251-326` |
| 외부 API | (project, source) 쌍마다 `list_files()` 1세트(페이지네이션 포함). Drive 는 하위 폴더 BFS(상한 500, `google_drive_source.py:203-223`), Notion 은 DB 행 + 하위 페이지 재귀(상한 500, `notion_source.py:109-143`) |
| DB | `document_meta` diff upsert(`_stage_upsert:328-359`), 원본에서 사라진 행 삭제(`_delete_removed:436-446`) |
| 판정 | `modified_at`/title/url 이 모두 같으면 `last_synced_at` 만 갱신하고 `updated` 로 세지 않는다(`_apply_changes:489-506`) |

삭제 감지 기준 집합은 **`(project, source)` 로 좁힌 기존 행**이다
(`:274-277`). 여러 프로젝트가 같은 `source_name` 을 공유하므로, 이걸 안 좁히면
다른 프로젝트 행이 "원본에서 사라진 것"으로 오인돼 지워진다 — 기능 6 의 핵심 위험.

### 축 C — 협업 문서 본문 색인 (`--index-bodies`, 기본 켜짐)

축 A 와 **같은 실행에 얹히는 플래그**다. 별도 축이 아니라 축 A 의 옵션.

| 항목 | 값 |
| --- | --- |
| 코드 | `_index_body:361-434` → `document_body_indexer.index_document_body:56-113` |
| 1차 게이트 | `needs_body_index or row.document_id is None`(`:357`) — 메타가 안 바뀌었고 이미 색인됐으면 **fetch 자체를 안 한다** |
| 2차 게이트 | fetch 한 문서에 한해 `content_hash` 비교(`document_body_indexer.py:80-81`) — 같으면 청크를 안 건드린다 |
| 외부 API | 게이트를 통과한 문서마다 `fetch()` 1건 이상 (문서당 비용이 축 A 보다 훨씬 크다) |
| DB | `document` upsert + 청크 delete-and-insert + 로컬 임베딩 저장(`indexer_service.index_document:53-130`) |
| 실패 처리 | fetch 실패는 그 문서만 건너뛰고 `document_id` 를 NULL 로 남겨 **다음 실행에서 자동 재시도**(`:394-405`) |
| 빈 본문 | 오류가 아니라 정상 스킵. 전에 색인돼 있었으면 그 `Document` 를 지워 옛 스니펫이 계속 나가는 걸 막는다(`:408-419`) |

`--no-index-bodies` 로 끄면 keyword/vector arm 이 비어 검색이 제목 매칭만으로 조용히 퇴화한다
— 그래서 끌 때 경고 로그를 남긴다(`document_index_service.py:185-190`).

### 축 B — 등록 문서 재색인 (`--include-registered`, 드물게)

| 항목 | 값 |
| --- | --- |
| 코드 | `registered_resync.resync_registered_documents:33-67` → `sync_service.resync:137-223` |
| 대상 | `source_url` 이 있는 `Document` 만(`document_repo.list_resyncable`). `raw_document` 로 등록한 문서는 원본 재fetch가 불가능해 자동 제외 |
| 외부 API | 문서마다 `OpenAPIFetcher.fetch(source_url)` 1회 |
| DB | 해시 동일 + `force=False` → `skipped` 기록만. 다르면 청크/엔드포인트/스키마/섹션 전량 삭제 후 재색인 |
| 격리 | 문서마다 자체 커밋. 한 문서 실패는 `session.rollback()` 후 `failed` 에 담고 계속 진행 |

---

## 케이스 6 — 신규 소스/문서 등록

**여기서 반드시 갈라 말해야 하는 두 부류가 있다.**

### 6-a. `register_drive_source` / `register_notion_source` / `register_notion_page`

| 항목 | 값 |
| --- | --- |
| 코드 | `mcp/tools/sources.py:90-110,152-208` → `project_source_service.register:33-50` |
| DB | `project_source` upsert + commit. **그게 전부다** |
| 외부 API | **0회.** 폴더/DB 존재 확인조차 하지 않는다 |
| 색인 | **일어나지 않는다.** 등록 직후 `search_documents` 는 여전히 0건이다 |
| 동기/비동기 | 동기, 즉시 반환(`created`/`updated`) |

즉 이 도구들은 **"어디를 볼지"만 적어두는 포인터 등록**이다. 실제 적재는 다음
`refresh_index`(또는 스케줄러 틱)에서 일어난다. 매핑 변경은 서버 재시작 없이 바로 반영된다 —
resolver 가 요청마다 새로 조회하기 때문(`composition.py:230-239`).
한 project 는 Notion database 매핑과 page 매핑을 동시에 가질 수 없다(나중 호출이 덮어씀).

### 6-b. `register_document`

| 항목 | 값 |
| --- | --- |
| 코드 | `mcp/tools/documents.py:59-112` → `sync_service.register:66-135` |
| 흐름 | 중복 검사(`find_by_source_url`) → [source_url 이면] 외부 fetch → doc_type 판별 → 파싱 → `Document` insert+flush → `IndexerService.index_document`(엔드포인트/스키마/섹션/청크 + 로컬 임베딩) → sync history → **커밋 1회** |
| DB | 쓰기 다수, 단일 트랜잭션 |
| 외부 API | `source_url` 이면 1회(`OpenAPIFetcher`). `raw_document` 면 0회 |
| 동기/비동기 | **완전 동기.** 파싱·임베딩까지 끝나야 응답한다 — 큰 문서면 체감 지연이 여기 몰린다 |
| 제약 | pdf/docx 는 base64 `raw_document` + `doc_type` 명시 필수. `source_url`/`raw_document` 는 정확히 하나만 |

6-a 와 6-b 는 저장소도 다르다: 6-a 는 `project_source`(포인터), 6-b 는
`document`/`chunk`(본문). 협업 문서 본문이 최종적으로 들어가는 곳은 6-b 와 **같은**
`document`/`chunk` 테이블이며(`document_body_indexer.py:1-12`), 다만 `Document.id` 가
`deterministic_document_id(project, source, external_id)` 로 고정된다는 점만 다르다
(`:31-41`).

---

## 부록 A. 케이스 요약표

| # | 트리거 | DB | 외부 API | 동기성 | 한 줄 |
| --- | --- | --- | --- | --- | --- |
| 1 | `search_documents`(색인됨) | 히트(meta+chunk×2) | 0 | 동기 | 가장 싼 경로 |
| 2a | `search_documents`(미등록) | 미스 | 0 | 동기 | 없으면 없다고 답한다 |
| 2b | `search_documents`(메타만) | 부분 히트 | 0 | 동기 | 제목만 걸림, `snippet_as_of=null` |
| 2c | `fetch` 전략(롤백) | meta 히트 | 후보 ≤20건 병렬 | 동기 | 점선 경로의 정체 |
| 3 | `get_document` | 읽기만 | 1건 이상 | 동기 | 캐시 없음, 항상 최신 |
| 4 | 색인 중 동시 요청 | 커밋된 배치까지 | — | — | 100건 커밋 경계 |
| 5A | 스케줄러 축 A | meta upsert | 소스마다 list | 별도 프로세스 | 싸고 잦게 |
| 5C | 스케줄러 축 C | document/chunk | 변경 문서마다 fetch | 별도 프로세스 | 비싸다, 2단 게이트 |
| 5B | 스케줄러 축 B | document/chunk | 문서마다 fetch | 별도 프로세스 | URL 등록 문서만 |
| 6a | `register_*_source` | project_source 1행 | 0 | 동기 | 포인터만, 색인 없음 |
| 6b | `register_document` | 다수 | 0~1 | 동기 | 파이프라인 전체를 기다림 |

## 부록 B. 발표 다이어그램에 반영하면 좋을 3가지

1. **점선("실시간 조회/갱신 시에만")에 케이스 번호를 달 것.** 지금 점선 하나에
   `get_document`(케이스 3)·`fetch` degrade(2c)·배치(5)가 겹쳐 있는데, 셋은 빈도도 비용도
   다르다. 실제로 도는 건 3 과 5 이고, 2c 는 롤백 스위치를 켰을 때만이다.
2. **`register_*_source` 에서 DB 로 가는 화살표는 "포인터"라고 명시할 것.** 현재 그림에서
   등록 = 적재로 읽힐 여지가 있다. 등록 → (스케줄러/refresh_index) → 적재의 2단 구조가 이
   시스템의 성격을 가장 잘 보여준다.
3. **"임베딩(로컬 CPU)" 를 계속 유지할 것.** 외부 LLM/임베딩 API 호출이 0 이라는 점이
   이 아키텍처의 세일즈 포인트다(판단은 클라이언트 LLM 이, 서버는 근거만).
