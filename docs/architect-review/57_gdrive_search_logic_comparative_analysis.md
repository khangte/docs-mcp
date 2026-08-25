# 57. Google Drive 문서 검색 로직 — 권장 검색 아키텍처 대비 비교 분석

- 작성: architect
- 대상 커밋: `ebbc52b` 기준 워킹트리
- 범위: `search_documents` 의 Drive 문서 검색 경로 전체(진입 → 랭킹 → 응답).
  `refresh_index` 색인 경로는 검색 품질에 영향을 주는 범위(chunking·임베딩)까지만 다룬다.
- 비교 기준: 사용자가 제시한 13단계 "권장 검색 로직"(Intent parsing ~ Query rewrite/retry)
> **갱신 이력 (2026-08-25)**: 5절 Top 5 개선 #1(응답에 근거·메타 추가)이 구현·리뷰 완료되어
> 2절(MCP response schema / Search reason·evidence), 5절, 9절(항목 14·15)을 실제 코드 기준으로 갱신했다.
>
> **갱신 이력 (2026-08-26)**: 5절 Top 5 개선 #3(arm 가중 RRF + title arm 품질 게이트)이 구현·리뷰
> 완료되어 1절(항목 7·11), 2절(Hybrid), 5절, 7절 V1, 9절(항목 7)을 갱신했다. 구현 과정에서
> 원안의 전제 오류가 드러나 게이트 판정 기준을 바꿨다 — 5.2절 참조.
>
> **갱신 이력 (2026-08-26, 2)**: 5절 Top 5 개선 #2(메타데이터 hard filter — 날짜 + mimeType)가
> 구현·리뷰 완료되어 1절(항목 1·3·7·9·10), 2절(Metadata filtering / MCP response schema), 5절,
> 7절 V1, 9절(항목 3)을 갱신했다. `owner` 는 컬럼·수집만 하고 필터·노출은 후속이다 — 5.4절 참조.
>
> 4절 문제점 서술과 8절 65점 평가는 **구현 이전 시점의 진단**이며 이력 보존을 위해 원문 그대로 둔다.

---

## 1. 기존 로직 요약 (단계별)

### 1.0 전제: 색인 시점과 검색 시점의 분리

현재 구조에서 Drive API 는 **검색 시점에 호출되지 않는다**. `refresh_index` 가 미리
문서를 긁어 `document_meta`(메타) + `document`/`chunk`(본문·임베딩)에 넣어두고,
`search_documents` 는 전적으로 로컬 PostgreSQL 만 읽는다. 따라서 검색 품질은
"색인이 얼마나 잘 돼 있는가"에 종속된다.

색인 경로(요약):

1. `refresh_index` → `resolve_all()` 로 등록된 project 전체의 `GoogleDriveSource` 생성
2. `list_files()` — 폴더 BFS(상한 `MAX_FOLDERS=500`), `FileMeta(external_id, title, url, modified_at)` 수집
3. `document_meta` upsert/delete (`BATCH_SIZE` 마다 커밋)
4. `index_bodies=True`(도구 기본값)이면 문서마다 `fetch()` → `index_document_body`
   - `parse_document`(markdown 파서 고정) 로 헤딩(`#`) 단위 섹션 분리
   - `content_hash` 동일하면 스킵, 다르면 delete-and-insert 재색인
   - `build_chunks` → 섹션당 청크 1개, 480토큰(`TOKEN_WARNING_THRESHOLD`) 초과 시
     `section_splitter` 가 문단 → 문장 → 문자 하드컷 순으로 그리디 분할(**overlap 없음**)
   - `intfloat/multilingual-e5-small`(dim 384, CPU) 로 `passage: ` 접두사 임베딩 → `chunk.embedding`
   - `chunk.text_tsv` 는 DB generated 컬럼(`to_tsvector('simple', …)`, 한글/ASCII 경계 정규화 포함)

### 1.1 검색 시점 단계

| #   | 단계                                                                                                                                                                                                                                                                                                                                   | 구현 위치                                                     |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| 1   | 사용자 자연어 → MCP client LLM 이 `search_documents(query, top_k, source, project, query_variants, modified_after, modified_before, mime_types)` 호출                                                                                                                                                                                                                               | LLM 측                                                        |
| 2   | FastMCP 라우팅 → `run_bundle_tool` (워커 스레드 오프로드, 요청 스코프 세션·서비스 조립)                                                                                                                                                                                                                                                | `app/mcp/tools/documents.py`, `_common.py`                    |
| 3   | 질의 검증(`top_k` 1~50, `source` ∈ {drive, notion}, 날짜 ISO8601·범위 역전·`mime_types` 개수/길이), 소스 구성 여부 확인(미구성이면 `IntegrationError`). 검증 통과분으로 `DocumentMetaFilter` 조립(개선 #2)                                                                                                                                                                                                                                | `document_search_service.py:225`                              |
| 4   | 토큰화 — `[0-9A-Za-z_]+\|[가-힣]+` 정규식, 소문자화. `query_variants` 는 **필터 토큰에만** 합류(`filter_tokens`), 점수 토큰(`query_tokens`)에는 섞이지 않음                                                                                                                                                                            | `search_scorer.documents_tokenize`                            |
| 5   | 전략 분기 — `document_search_strategy="indexed"`(기본) → 3-arm RRF. 그 외/의존성 누락 시 `"fetch"`(라이브 fetch + 가중합)로 degrade                                                                                                                                                                                                    | `:255`                                                        |
| 6   | 후보 폭 결정 — `width = max(top_k*4, 50)`                                                                                                                                                                                                                                                                                              | `_RRF_CANDIDATE_WIDTH_MULTIPLIER`, `_RRF_MIN_CANDIDATE_WIDTH` |
| 7   | **title arm** — `document_meta.search_by_tokens`: 토큰별 `title/url ILIKE '%token%'` OR 질의 collapse(공백 제거) 패턴, GIN trgm 인덱스 사용. **날짜/mimeType hard filter 를 같은 SQL 의 WHERE 로 AND 결합**(개선 #2). **`_passes_title_gate` 로 토큰 경계를 지키지 않는 부분문자열 잡음 행을 제외**(개선 #3, 2026-08-26)한 뒤 `_title_score`(토큰 겹침 비율 vs collapse 매칭 1/토큰수 의 `max`) 계산·정렬 → 상위 `width` → `deterministic_document_id(project, source, external_id)` 로 문서 ID 리스트화 | `_title_arm`, `_passes_title_gate`                            |
| 8   | 본문 색인 존재 확인 — `has_endpoint_chunks(project, chunk_type="section")` 이 False 면 keyword/vector arm 을 통째로 생략                                                                                                                                                                                                               | `:512`                                                        |
| 9   | **keyword arm** — `chunk.text_tsv @@ to_tsquery('simple', t1 \| t2 \| …)`(OR), `ts_rank(text_tsv, 원본토큰 tsquery)` 내림차순, `chunk_type='section'` + `Document.doc_type ∈ {drive, notion}`(또는 지정 source) 필터, `limit width`, **메타 필터가 있으면 `document_meta` EXISTS 서브쿼리 추가**(개선 #2). 문서별 첫 등장만 남기는 dedupe → 문서 ID 순위 + 승자 청크 ID                                      | `_keyword_arm`, `chunk_repository.search_endpoint_by_text`    |
| 10  | **vector arm** — `embed_query`(요청당 1회, `query: ` 접두사, LRU 256) → pgvector `<=>` 코사인 거리, HNSW 인덱스(`SET LOCAL hnsw.ef_search = max(100, top_k)`, **메타 필터가 있으면 하한 200 + EXISTS 서브쿼리**, 개선 #2), 유사도 = 1 − 거리, `score > 0` 만 채택, 같은 dedupe. `vector_fallback_enabled=False`(해시 백엔드)면 arm 생략                                                            | `_vector_arm`, `chunk_repository.search_by_vector`            |
| 11  | **RRF 융합** — `score(d) = Σ_arm w_arm · 1/(60 + rank_arm(d))`, **title 0.5 / keyword 1.0 / vector 1.0**(개선 #3, 2026-08-26), 동점은 ref_id 오름차순. `top_k=width` 로 컷                                                                                                                                                       | `app/services/search/rrf.py`                                  |
| 12  | 메타 보강 — title arm 에 없던 문서 ID 는 `document_meta.list_by_document_ids` 로 배치 조회                                                                                                                                                                                                                                             | `:522`                                                        |
| 13  | 스니펫 — 승자 청크 = `{**vector_chunk_by_doc, **keyword_chunk_by_doc}`(**키워드 승자가 벡터 승자를 덮어씀**), `get_texts_by_ids` 배치 조회 → `_build_snippet`(매치 위치 앞 60자부터 300자) 또는 제목 기반 fallback                                                                                                                     | `_build_indexed_item`, `snippet_generator.py`                 |
| 14  | 최종 컷 — 융합 순서대로 순회하며 `top_k` 개 채우면 중단(재정렬 없음)                                                                                                                                                                                                                                                                   | `:544`                                                        |
| 15  | 응답 — `{"items": [{title, source, project, url, snippet, score, version, snippet_as_of, external_id}]}`                                                                                                                                                                                                                               | `payloads._to_document_search_payload`                        |

---

## 2. 단계별 비교표

| 영역                      | 기존 로직                                                                                                                                                         | 권장 로직                                                                  | 차이점                                                                                                                                                                        | 영향도   | 개선 필요                |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ------------------------ |
| Intent parsing            | 없음. 도구 시그니처가 받는 구조화 슬롯은 `source`/`project`/`query_variants` 뿐. LLM 이 날짜·사람·타입을 추출해도 **넘길 곳이 없다**                              | LLM 이 query/people/date_range/doc type/folder 로 구조화해 MCP 에 전달     | 구조화 자체가 아니라 **수용 인터페이스**가 없음                                                                                                                               | 상       | **필요**                 |
| Query expansion           | 서버는 확장하지 않음. 호출 LLM 이 `query_variants` 로 제공, 후보 필터만 넓히고 점수에는 불참                                                                      | 동의어/약어/영문 일부 확장                                                 | 설계 의도는 일치(확장 주체 = LLM). 다만 variant 가 **title arm 의 SQL 필터에만** 반영되고 chunk FTS 의 `terms` 로도 전달되긴 하나 vector arm 에는 미반영                      | 중       | 부분(현행 유지 + 문서화) |
| Metadata filtering        | `project`, `source` + **`modified_after`/`modified_before`/`mime_types` hard filter**(3 arm 전부 SQL 적용). `document_meta` 에 `mime_type`/`created_at`/`owner` 컬럼 추가 | createdTime/modifiedTime/mimeType/folderId/owner/sharedWith 로 hard filter | 날짜·mimeType 은 해소. `owner` 는 수집만 하고 필터 미노출, `created_at` 은 컬럼만, folderId/sharedWith 는 미구현 | 상       | **날짜·mime 구현 완료 (2026-08-26)** / owner·created_at 후속 |
| Chunking                  | 헤딩(`#`) 기반 섹션 → 480토큰 초과 시 문단/문장/하드컷 그리디 분할, **overlap 0**, 각 sub 에 `# 제목` 앵커 부착                                                   | Heading/Section 우선, 500~1,000 token, 50~150 overlap                      | 방식은 권장안과 동일 계열. 크기(480)는 권장 하한보다 작고 overlap 이 없음. **PDF/DOCX/Docs 평문 export 는 마크다운 헤딩이 없어 문서 전체가 섹션 1개**로 묶인 뒤 기계적 분할됨 | 중       | 부분                     |
| Keyword/BM25              | PostgreSQL FTS(`simple` config) + `ts_rank`. term OR 결합, **질의 측 복합어 분해(concat term + `<->` 2분할 phrase)로 한글 띄어쓰기 변형 흡수**, IDF 없음, 문서 길이 정규화 없음(`ts_rank` 기본 normalization=0) | BM25 또는 FTS. 코드/고객사명/사람이름/오류코드에 강해야 함                 | 정확 토큰 매칭과 복합어 띄어쓰기 대칭은 해소. 남은 격차는 **희소어 가중(IDF) 부재** — 흔한 토큰이 점수를 지배                                                                 | 상       | **복합어 대칭 구현 완료 (2026-08-26)** / IDF 는 V2 |
| Vector/Semantic           | multilingual-e5-small(384d), passage/query 접두사 규약 준수, HNSW+cosine                                                                                          | Embedding 기반 vector search                                               | 구현 충실. 모델 크기가 작은 것 외 구조적 결함 없음                                                                                                                            | 하       | 불필요                   |
| Hybrid                    | RRF(k=60) 3-arm(title/keyword/vector), **title 0.5 · keyword 1.0 · vector 1.0 가중**                                                                              | weighted sum 또는 RRF, semantic+lexical+metadata                           | arm 가중은 `reciprocal_rank_fuse(weights=…)` 로 도입 완료. metadata arm 은 여전히 부재(평가셋 없어 보류, 6절)                                                                  | 중       | **가중 구현 완료 (2026-08-26)** / metadata arm 은 보류 |
| Candidate retrieval       | arm 당 `width = max(top_k*4, 50)` → 기본 top_k=5 면 arm 당 50건, 합집합 최대 150건                                                                                | BM25 Top30 + Vector Top30 → 30~50 candidate                                | 후보 폭은 권장안 이상. 문제 없음                                                                                                                                              | 하       | 불필요                   |
| Dedup                     | arm 별 "문서 첫 등장 등수만 채택"(`_dedupe_first_with_chunk`). 문서 1건이 섹션 수만큼 슬롯을 먹는 것을 차단                                                       | merge/dedup                                                                | 동등                                                                                                                                                                          | 하       | 불필요                   |
| Reranking                 | **없음**. 융합 순서가 곧 최종 순서                                                                                                                                | 상위 candidate 를 query 와 재비교해 정밀 정렬                              | 전면 부재                                                                                                                                                                     | 중       | V2                       |
| Document aggregation      | dedupe-first 가 사실상 "best chunk rank"만 반영. second-best·chunk 수·metadata score 없음                                                                         | best/second-best chunk + metadata 로 document score 산출                   | 증거량(같은 문서 안 여러 청크가 걸린 것)이 순위에 반영 안 됨                                                                                                                  | 중       | 부분                     |
| Permission/access control | **없음**. 서비스 계정 1개가 본 것 = 모든 MCP 호출자가 검색 가능. 최종 사용자 신원 개념 자체가 없음                                                                | owner/sharedWith 를 필터·시그널로 활용                                     | 권한 모델 부재(설계상 "폴더를 SA 에 공유" 전제)                                                                                                                               | 상(보안) | **필요(정책 명시 최소)** |
| Result scoring            | RRF 절대값(0.016~0.05 스케일). 전략에 따라 스케일이 달라 **순서 정보만 유효**(코드 주석에 명시)                                                                   | 0~1 정규화 score(예: 0.93)                                                 | LLM 이 임계값 판단 불가                                                                                                                                                       | 중       | 부분                     |
| MCP response schema       | title/source/project/url/snippet/score/version/snippet_as_of/external_id **+ `matched_chunks[]`·`match_reasons[]`·`modified_at`·`indexed`·`mime_type`** | document_id/title/url/score/matched_chunks[]/match_reasons[] | 권장안이 요구한 근거·메타 필드가 채워졌다. 문서 식별자는 `document_id` 대신 `external_id`(+`source`)로 노출한다 — `get_document(source, external_id)` 가 받는 키가 그것이라 클라이언트 변환이 불필요하다. 작성자(owner)는 여전히 부재(개선 #2 소관) | 상       | **구현 완료 (2026-08-25)** |
| Search reason/evidence    | 스니펫(300자) + `matched_chunks[{chunk_id, text, chunk_type, arm}]` + `match_reasons[]`(arm 기여 · 필터 일치 · 미색인 강등)                                          | match_reasons 로 근거 명시                                                 | 해소. arm 별 승자 청크를 각각 노출하고(같은 청크가 양쪽 arm 승자면 `arm="both"`), 근거 문자열은 서비스 모듈 상수라 LLM 이 안정적으로 대조할 수 있다                          | 상       | **구현 완료 (2026-08-25)** |
| Query rewrite/retry       | 서버에 없음. docstring 이 "결과 0건이면 `query_variants` 로 재호출"하도록 LLM 을 유도                                                                             | confidence 낮으면 재검색                                                   | 재시도 주체를 LLM 에 둔 것은 타당하나, **confidence 신호를 안 주므로** LLM 이 판단 근거가 없음                                                                                | 중       | 부분(신호만 제공)        |
| Latency                   | 검색 경로에 외부 API 0회. SQL 3~4회 + 로컬 임베딩 1회(CPU, LRU 캐시). 체감 수십~수백 ms                                                                           | 명시 없음                                                                  | 현행이 유리                                                                                                                                                                   | 하       | 불필요(유지)             |
| 검색 실패 처리            | `DomainError`/`IntegrationError` → `{error, code, message}`. 0건은 그냥 빈 리스트. 색인 누락/fetch 실패 문서는 **조용히** title-only 로 강등                      | 명시 없음                                                                  | 실패와 "결과 없음"과 "색인 안 됨"이 구별되지 않음                                                                                                                             | 중       | **필요**                 |

---

## 3. 기존 로직의 장점 (바꾸지 않아도 되는 부분)

1. **검색 경로에서 외부 API 를 제거한 것.** 사전 색인 + 로컬 DB 검색 구조라 Drive rate limit·
   네트워크 지연이 검색 latency 에 들어오지 않는다. 권장안이 요구하는 "BM25 Top30 + Vector Top30"
   같은 넓은 후보 스캔을 부담 없이 할 수 있는 것도 이 구조 덕이다. 라이브 fetch 전략(`"fetch"`)을
   롤백 스위치로만 남긴 판단은 옳다.
2. **RRF 채택.** `ts_rank` 와 코사인 유사도는 스케일이 달라 가중합이 왜곡되는데, 등수만 쓰는 RRF 로
   이를 회피했다. k=60 을 평가셋 없이 임의 튜닝하지 않고 표준값으로 고정한 것도 근거 있는 절제다.
3. **title 을 별도 arm 으로 편입한 것.** 본문 색인이 안 된 문서(fetch 실패·미지원 MIME)도 title arm
   단독으로 결과에 남는다. 별도 폴백 분기 없이 자연스럽게 degrade 하는 설계다.
4. **`deterministic_document_id` 로 arm 간 융합 키를 통일한 것.** 색인 여부와 무관하게 같은 키가
   나오므로 title arm 과 chunk arm 이 같은 ID 공간에서 만난다.
5. **질의 확장의 주체를 클라이언트 LLM 에 둔 것.** MCP 서버가 자체 LLM 을 호출해 동의어를 만드는
   설계를 피했다. 권장안 12번("최종 의미 판단은 LLM")과 같은 철학이고, 비용·지연·비결정성을 서버가
   지지 않는다. `query_variants` 가 후보 필터만 넓히고 점수에는 불참하는 규약도 정확하다.
6. **한글/ASCII 경계 정규화(`TEXT_TSV_EXPRESSION`)와 공백 변형 흡수(`collapse`).** `GET요청`,
   `트러블 슈팅` 같은 실제 한국어 문서에서 발생하는 매칭 실패를 겨냥한 구체적 대응이다.
7. **섹션 sub-chunking 상한을 임베딩 모델 실측 토큰수로 잡은 것.** SentenceTransformer 가 512토큰
   초과를 조용히 truncate 하는 문제를 480 상한 + 초과 경고 로그로 막았다.
8. **부분 실패 격리.** 개별 문서 fetch 실패, 개별 소스 갱신 실패가 전체를 죽이지 않고 재시도 가능한
   상태(document_id NULL)로 남는다.

---

## 4. 기존 로직의 문제점

### 4.1 Recall

- **본문 keyword arm 에는 collapse(공백 제거) 매칭이 없다.** title arm 만 `'트러블슈팅'` ↔
  `'트러블 슈팅'` 을 흡수한다. 본문 FTS 는 `to_tsvector('simple', …)` 라 복합어 분해가 없다.
  → 현상: 질의 `"결제장애"`, 본문 `"결제 장애가 발생"` → keyword arm 미히트. 벡터 arm 이
  건지지 못하면 그 문서는 제목에 '결제장애'가 없는 한 탈락.
- **`has_endpoint_chunks` 가 False 면 keyword/vector arm 전체가 꺼진다.** 특정 project 에 본문
  청크가 하나도 없으면(초기 동기화 직후, `index_bodies=False` 배치 운영) 검색이 제목 매칭
  단독으로 조용히 퇴화한다. 사용자에게 알리는 신호는 없다.
- **텍스트 추출 불가 문서는 영구히 title-only.** Google 그림/설문, 이미지 PDF(스캔본), 50MB 초과
  파일은 `fetch()` 가 `IntegrationError` 로 끝나고 warning 로그만 남는다.
  → 현상: 스캔한 회의록 PDF 는 제목에 키워드가 없으면 절대 안 나온다.
- **폴더 500개 상한 초과 시 조용한 누락.** `MAX_FOLDERS` 도달은 warning 로그일 뿐 `refresh_index`
  응답에 실리지 않는다. 사용자는 "그 문서는 검색 대상이 아니다"를 알 방법이 없다.
- **날짜·작성자 조건을 좁힐 수단이 없다.** `"김대리가 7월에 쓴 회고"` 는 그냥 전체 검색이 되고,
  '김대리'·'7월' 이 본문에 우연히 있는 문서가 잡힌다.

### 4.2 Precision / Ranking

- **`ts_rank` 에 IDF 가 없다.** OR 결합이라 `"결제 시스템 장애"` 질의에서 '시스템'만 다수 포함한
  일반 문서가 상위에 올 수 있다. BM25 의 희소어 가중·길이 정규화가 없다.
- **title arm 이 keyword/vector arm 과 동일 가중이다.** title arm 후보는 `ILIKE '%token%'` 이라
  1~2글자 한글 토큰이면 대량 매칭되고, `_title_score` 가 0.0 인 행조차(다른 조건으로 SQL 에 걸린 행)
  정렬 뒤쪽에 남아 `width` 안에 들면 RRF 점수를 받는다.
  → 현상: 제목에 'api' 만 겹친 무관 문서가 title arm 1위(1/61)를 받아, 본문이 정확히 맞는
  keyword arm 1위 문서와 동점이 된다.
- **문서 내 증거량이 순위에 반영되지 않는다.** 같은 문서에서 8개 청크가 걸려도 첫 등장 1건만
  카운트된다. 한 문단만 스치는 문서와 문서 전체가 주제인 문서가 동급으로 융합된다.
- **reranking 부재.** 융합 이후 질의-문서 재비교가 없어, arm 별 등수의 우연이 최종 순위에 그대로 남는다.
- **스니펫이 최선의 근거가 아닐 수 있다.** `{**vector, **keyword}` 병합이라 keyword 승자 청크가
  vector 승자를 무조건 덮는다. 벡터가 정확히 집어낸 문단이 있어도 키워드가 스친 문단이 노출된다.
- **overlap 0 인 chunking.** 문단 경계에서 잘린 개념(정의 문장과 결론 문장이 분리)은 어느 청크에서도
  온전한 문맥을 못 만든다. 특히 헤딩이 없는 PDF/DOCX 는 문서 전체가 섹션 1개라 분할이 순전히
  기계적이다.

### 4.3 Latency

- 현재 검색 경로는 빠르다(외부 호출 0). 다만 **벡터 arm 의 `embed_query` 가 프로세스 내 CPU 추론**
  이라, 동시 요청이 늘면 GIL·CPU 경합이 검색 지연으로 직결된다. LRU 256 캐시는 동일 문자열 질의만
  구제한다.
- `SET LOCAL hnsw.ef_search = max(100, top_k)` 는 후보 폭 50 기준 합리적이다. 문제 없음.
- 개선 후보 중 cross-encoder reranking 은 이 "외부 호출 0, 수십 ms" 특성을 무너뜨린다(6절 참조).

### 4.4 비용

- 검색 비용은 사실상 DB CPU 뿐이다. 색인 비용(문서마다 fetch + 파싱 + 임베딩)이 전부이며
  `content_hash` 게이트로 재색인은 변경분만 든다. **현 규모에서 비용은 문제가 아니다.**
- 오히려 비용을 걱정해 `index_bodies=False` 로 운영하면 검색이 조용히 title-only 로 퇴화한다 —
  비용 절감 옵션이 품질 절벽과 직결돼 있는데 그 사실이 응답에 드러나지 않는다.

### 4.5 유지보수성

- `DocumentSearchService` 가 700줄, 두 전략(`indexed`/`fetch`)을 한 클래스에 담고 있다. `fetch`
  전략은 롤백 스위치로만 남았는데 `_select_candidates`/`_rank_with_body`/`_fetch_and_score` 등
  절반 가까운 코드가 그것 전용이다.
- 랭킹 상수(`RRF_K`, `_RRF_MIN_CANDIDATE_WIDTH`, `TITLE_SCORE_WEIGHT`, `SNIPPET_MAX_CHARS`)가
  파일마다 흩어져 있고 평가셋이 없어 변경 영향을 측정할 방법이 코퍼스 평가 스크립트뿐이다.
- 반대로 좋은 점: 순수 함수 분리(`search_scorer`, `snippet_generator`, `rrf`, `section_splitter`)가
  잘 돼 있어 랭킹 실험 자체는 붙이기 쉽다.

### 4.6 LLM 의존성

- 질의 확장·재시도·최종 판단을 모두 LLM 에 맡긴 것은 방향이 옳다. 문제는 **LLM 에게 판단 재료를
  안 준다**는 점이다.
  - `score` 는 RRF 절대값이라 "이게 좋은 결과인가"를 판정할 수 없다.
  - 어느 arm 이 왜 이 문서를 뽑았는지(title 만? 벡터만?) 응답에 없다.
  - 문서 수정일이 응답에 없어 "최신 자료인가" 판단이 불가하다.
    → 현상: LLM 이 결과 5건을 받고 전부 `get_document` 로 원문을 열어보는 낭비, 또는 무관한
    title-only 히트를 근거로 답변하는 환각.

### 4.7 MCP 책임 과다 / 과소

- **과다**: 없음. 서버가 LLM 을 호출하거나 의미 판단을 하는 지점이 없다. 이 경계는 잘 지켜졌다.
- **과소**: 검색 엔진이 당연히 져야 할 책임(메타데이터 필터, 근거 제시, 신뢰도 신호)까지 LLM 에
  떠넘겼다. LLM 은 서버가 노출하지 않은 필드를 스스로 만들어낼 수 없다.

### 4.8 보안 / 권한

- **문서 접근 권한이 검색에 반영되지 않는다.** 서비스 계정에 공유된 폴더의 모든 문서는 MCP 를
  호출할 수 있는 **모든** 클라이언트에게 검색·조회된다. 원본 Drive 에서 특정 사용자에게만 공유된
  문서라도 마찬가지다.
  → 현상: 인사·평가 문서가 팀 폴더 하위에 있으면 누구나 스니펫과 원문을 얻는다.
- 권한 회수는 다음 `refresh_index` 때 `list_files()` 결과에서 빠져야 반영된다(즉시성 없음).
- `get_document` 는 `document_meta` 조회 없이도 **임의의 Drive file ID** 로 fetch 를 시도한다.
  서비스 계정이 볼 수 있는 파일이면 색인 범위 밖이어도 반환된다.
- 이 셋은 "SA 에 공유된 것만 검색 대상"이라는 전제를 문서화하고 폴더 위생으로 막는 것이 현실적
  대응이지만, **현재 그 전제가 어디에도 명시돼 있지 않다.**

---

## 5. 가장 중요한 개선사항 Top 5

| #   | 변경 내용                                                                                                                                                                                                    | 이유                                                                                                                                | 구현 난이도                                | 품질개선 효과                 | latency 영향                     | V1 포함                                     |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ | ----------------------------- | -------------------------------- | ------------------------------------------- |
| 1   | **응답에 근거·메타 추가 — 구현 완료 (2026-08-25)** — `matched_chunks[{chunk_id, text, chunk_type, arm}]`, `match_reasons[]`, `modified_at`, `indexed` 를 `search_documents` 응답 item 에 추가. `rrf.FusedResult.contributing_arms` 로 기여 arm 을 그대로 실어 나른다 | LLM 이 최종 판단을 하려면 근거가 필요한데 현재 스니펫 300자가 전부. `match_type`·`modified_at` 은 **이미 손에 있는 값을 버리는 중** | Low                                        | High                          | ~0 (추가 SQL 없음 — `get_texts_by_ids` 한 번의 id 집합만 확대) | **구현 완료**                                    |
| 2   | **메타데이터 hard filter 도입 — 날짜·mimeType 구현 완료 (2026-08-26)** — `document_meta` 에 `mime_type`/`created_at`/`owner` 컬럼 추가(Drive `files.list` fields 확장) + `search_documents(modified_after, modified_before, mime_types)` 파라미터. `owner` 필터·`created_at` 필터는 후속(5.4절) | 권장안 1·3단계의 전제. LLM 이 추출한 날짜/타입 조건을 받을 그릇이 없어 intent parsing 이 통째로 무력화됨                            | Medium (마이그레이션 + 전량 재동기화 필요) | High                          | ~0 (SQL WHERE 추가, 오히려 감소) | **날짜·mime 구현 완료** / owner 후속 |
| 3   | **arm 가중 RRF + title arm 품질 게이트 — 구현 완료 (2026-08-26)** — `reciprocal_rank_fuse(weights=…)` 로 title 0.5 / keyword 1.0 / vector 1.0, `_title_arm` 에서 `_passes_title_gate` 로 토큰 경계 미준수 행 제외(판정 기준은 원안에서 수정, 5.2절) | 제목에 흔한 토큰 하나 겹친 문서가 본문 정답 문서와 동점이 되는 구조적 오류. 코드 변경량 대비 순위 개선 폭이 가장 크다               | Low                                        | Medium~High                   | ~0                               | **구현 완료**                                    |
| 4   | **keyword arm 의 한글 복합어 대칭 확보 — 구현 완료 (2026-08-26)** — 질의 측 분해 채택: 인접 토큰 concat term + 순수 한글 토큰의 2분할을 tsquery 구문 연산자 `<->` 로 묶은 phrase term 을 본문 FTS 에 OR 로 추가. 생성 컬럼 `text_collapsed` + trgm 은 미채택(5.6절). 5.3절 파생 항목(`_collapse_match_score` 토큰 경계)도 같이 해소 | recall 구멍 중 가장 자주 발생. 한국어 문서 기반 시스템에서 `"결제장애"` ↔ `"결제 장애"` 미스는 치명적                               | Medium                                     | High                          | ~0 (새 인덱스·마이그레이션 없음, tsquery operand 만 증가·상한 32) | **구현 완료**                                    |
| 5   | **색인 커버리지 가시화** — `refresh_index` 응답에 `unindexed`(본문 없음)/`unsupported`(MIME 미지원)/`folder_limit_reached` 노출, `search_documents` 결과 항목에 `indexed` 플래그(**이 항목만 개선 #1 로 구현 완료**) | "검색에 안 나오는 이유"가 현재 전부 서버 로그에만 있다. 운영자·LLM 모두 조용한 퇴화를 감지할 수 없다                                | Low                                        | Medium (품질보다 신뢰도·운영) | ~0                               | **포함**                                    |

### 5.1 개선 #1 구현 결과 (2026-08-25)

`search_documents` 응답 item 의 실제 계약:

```jsonc
{
  "title": "...", "source": "gdrive", "project": "...", "url": "...",
  "snippet": "...", "score": 0.031, "version": "...",
  "snippet_as_of": "2026-08-24T...Z",   // 본문 청크 없으면 null
  "external_id": "1AbC...",             // get_document(source, external_id) 에 그대로 전달
  "matched_chunks": [                   // 본문 근거 없으면 빈 리스트
    {"chunk_id": "...", "text": "...", "chunk_type": "section",
     "arm": "keyword" | "vector" | "both"}
  ],
  "match_reasons": ["제목·URL 매칭", "본문 의미 유사", "프로젝트 필터 일치: payments"],
  "modified_at": "2026-08-20T09:11:00+00:00",  // 원본 최종 수정 시각, 없으면 null
  "indexed": true                       // false = 본문 미색인, 제목 매칭만으로 히트
}
```

- `match_reasons` 는 `document_search_service` 의 모듈 상수라 문자열이 고정이다 —
  arm 근거(`제목·URL 매칭` / `본문 키워드 일치` / `본문 의미 유사`) → 필터 근거
  (`프로젝트 필터 일치: {project}` / `출처 필터 일치: {source}`) → 강등 근거
  (`본문 미색인 — 제목 매칭만으로 검색됨`) 순서로 쌓인다.
  `strategy="fetch"` 경로는 색인 arm 이 없으므로 `실시간 본문 매칭` + 필터 근거만 붙는다.
- 기여 arm 은 `rrf.FusedResult.contributing_arms`(항상 title→keyword→vector 순)로 전달한다.
  `match_type` 의 기존 의미는 그대로라 `endpoint_candidate_search` 는 손대지 않았다.
- 추가 SQL 없음: 기존 `get_texts_by_ids` 호출의 id 집합만 넓혔다.
  랭킹·정렬·스니펫 선택 로직은 변경하지 않았다(노출 전용).

### 5.2 개선 #3 구현 결과 (2026-08-26)

**arm 가중.** `reciprocal_rank_fuse` 에 `weights: Mapping[str, float] | None` 키워드를 추가해
`score(d) = Σ_arm w_arm · 1/(k + rank_arm(d))` 로 바꿨다. `weights=None`(기본값)이면 전 arm 1.0 이라
가중치를 넘기지 않는 엔드포인트 검색(`endpoint_candidate_search`)은 점수·순서가 무변경이다.
문서 검색만 `weights={ARM_TITLE: TITLE_ARM_WEIGHT}`(`TITLE_ARM_WEIGHT = 0.5`)를 넘긴다.
가중치는 **점수에만** 적용되고 `match_type`/`contributing_arms` 는 기존대로 존재 여부로만 계산한다.

0.5 의 근거: title 후보는 permissive 한 `ILIKE '%token%'` 게이트에서 나오는데다, `k=60` 이라
arm 내 등수 차이가 거의 소멸한다(rank 1 = 1/61 = 0.0164, rank 50 = 1/110 = 0.0091). 즉 title arm 은
사실상 "존재 보너스"이므로 본문 신호 arm 의 절반으로 둔다. 평가셋이 없어 튜닝 근거가 없으므로
`RRF_K` 와 같은 방침으로 모듈 상수 고정, env 미노출이다.

**품질 게이트 — 원안 대비 판정 기준 수정.** 원안은 "`_title_score <= 0` 행 제외"였으나 구현 중
전제가 두 군데 틀린 것이 확인돼 기준을 바꿨다.

1. `_title_score` 의 점수용 토큰은 원본 질의 토큰뿐이라, **variant 로만 걸린 정상 매칭 행도 0.0** 이다.
   원안대로 하드컷하면 본문 미색인 문서(= title arm 이 유일한 경로)에서 `query_variants` 기능이
   통째로 무력화된다.
2. 더 결정적으로, `_title_score` 는 애초에 그 잡음을 걸러내지 못한다. `_collapse_match_score` 가
   `collapse(query) in collapsed_haystack` 부분문자열 판정이라 토큰 경계를 모른다 —
   질의 `'api'` 는 제목 `'Rapid Onboarding Guide'` 에 대해 0.0 이 아니라 **1.0**(단일 토큰 질의라
   `1/1`)을 반환한다. SQL `ILIKE` 와 정확히 같은 수준의 permissive 매칭이라 게이트로 쓸 수 없다.

그래서 게이트를 `search_scorer.py` 의 별도 헬퍼로 만들었다.

- `_token_aligned_concat_match(query, haystack_tokens)`: 양쪽 다 `documents_tokenize` 로 토큰화한 뒤,
  질의 토큰 concat 이 haystack 토큰들의 **연속 부분열** concat 과 정확히 일치할 때만 True
  (토큰 시작·끝 오프셋 집합으로 경계 정렬을 확인).
- `_passes_title_gate(row, filter_tokens, queries)`: (원본 ∪ variant 토큰)이 title/url 토큰과
  완전 일치하거나, 어떤 질의가 위 연속 부분열 매치에 걸리면 통과. title 토큰열과 url 토큰열은 따로 본다.

판정 결과: `'api'` / `'Rapid Onboarding Guide'` → **제외**, `'결제장애'` / `'결제 장애 대응'` →
**통과**(`'결제'+'장애'` 연속 부분열), variant `'payment failure'` 로만 걸린 영문 제목 → **통과**,
경계가 어긋난 절단 질의 → 제외. `_title_arm` 은 이 게이트로 필터만 하고 정렬 키·점수식은 무변경이다.

**동작 변화(의도된 것).** 부분문자열로만 걸렸고 본문 색인도 없는 문서는 결과에서 완전히 사라진다.
precision 을 위해 감수한 recall 손실이다.

**범위 밖으로 남긴 것.** fetch 전략의 `_title_candidates` 에는 게이트를 넣지 않았다(2단계에서
본문 `_body_score` 로 재정렬되며 잡음이 강등되고, 롤백 스위치 경로라 표면적을 넓히지 않는다).
`RRF_K`·스니펫 선택·`endpoint_candidate_search` 무변경.

### 5.3 개선 #3 에서 파생된 후속 항목 (개선 #4 와 함께 해소, 2026-08-26)

**`_collapse_match_score` 의 토큰 경계 무시는 점수 계산에도 그대로 남아 있다.** 게이트를 통과한
행이라도, 단일 토큰 질의 `'api'` 가 제목 `'Rapid …'` 에 대해 `1/1 = 1.0`, 즉 title 만점을 받는다
(겹치는 다른 토큰이 하나라도 있어 게이트를 통과한 경우). 짧은 질의일수록 분모(`token_count`)가
작아 과대평가 폭이 커진다.

- 영향 범위: `_title_score`·`_body_score` 양쪽 + fetch 전략의 후보 순서(`_title_candidates`)까지.
- 수정 방향: `_collapse_match_score` 도 `_token_aligned_concat_match` 와 같은 토큰 경계 기준으로
  바꾸거나, collapse 매치 점수 상한을 `1/token_count` 보다 낮게(예: 토큰 1개 겹침의 절반) 잡는다.
- 개선 #3 과 분리한 이유: 게이트는 후보 집합만 건드리지만 이건 **순위 자체**를 바꾸고 두 전략
  모두에 걸린다. 개선 #4(본문 collapse 대칭 확보)와 같은 함수를 손대므로 그때 함께 다루는 편이 낫다.
- **해소(2026-08-26).** 개선 #4 의 T1 로 처리했다 — 수정 방향 두 가지 중 "`_token_aligned_concat_match` 와
  같은 토큰 경계 기준으로 바꾼다"를 택했다(상한을 낮추는 안은 미채택: 경계를 존중하는 순간
  그 점수는 "토큰 1개가 실제로 겹친 것"과 동등한 신호라 더 깎을 근거가 없다). 상세는 5.6절.

### 5.4 개선 #2 구현 결과 (2026-08-26)

**스키마.** `document_meta` 에 `mime_type`(String 128) / `created_at`(DateTime) / `owner`(String 320)
nullable 컬럼과 `ix_document_meta_document_id` 인덱스를 추가했다(마이그레이션 `47fe51335c37`).
인덱스는 선택이 아니라 필수다 — keyword/vector arm 에 붙는 EXISTS 서브쿼리가
`document_meta.document_id` 로 조회하는데, PostgreSQL 은 FK 에 인덱스를 자동 생성하지 않는다.
날짜·mime 전용 인덱스는 만들지 않았다(title arm 은 trgm 후보로 이미 좁혀진 행에, chunk arm 은
document_id 로 집은 1행에 필터를 걸기 때문).

**수집.** Drive `files.list` fields 에 `createdTime`, `owners(displayName, emailAddress)` 를 더했다
(`mimeType` 은 원래 있었다). `owner` 는 이메일 우선·표시 이름 폴백이다. Notion 은 `created_at` 만
채우고 `mime_type`/`owner` 는 NULL 이다 — Notion `created_by` 는 user id 뿐이라 이메일/이름을
얻으려면 문서마다 users API 를 한 번 더 호출해야 해서 동기화 비용 대비 가치가 없다.

**필터 적용 지점.** `app/repositories/document_filters.py` 의 `DocumentMetaFilter` 를 3 arm 이 공유한다.

- title arm(indexed·fetch 전략 모두): `search_by_tokens` 의 WHERE 에 AND 결합.
- keyword/vector arm: `document_meta` **EXISTS 서브쿼리**. JOIN 을 쓰지 않은 이유는 같은
  `document_id` 행이 둘 이상일 때 청크 행이 증식해 조용히 순위를 망가뜨리기 때문이다.
- 융합 후 파이썬 후처리는 쓰지 않았다 — arm 당 후보 폭이 `width` 로 고정돼 있어 후처리하면
  필터가 셀수록 결과가 조용히 빈다.
- 벡터 arm 한정으로, 필터가 있으면 `hnsw.ef_search` 하한을 100 → 200 으로 올린다. HNSW 는 ANN 이
  먼저 top-N 을 뽑고 그 뒤에 필터가 걸리는 post-filtering 구조라 유효 후보가 줄기 때문이다.
  평가셋이 없어 근거 없는 값이므로 휴리스틱으로 주석에 명시하고 env 로 노출하지 않았다.
- `meta_filter` 는 전부 기본값 `None` 이라 엔드포인트 검색(`endpoint_candidate_search`)은 무변경이다.

**의미론(반드시 문서화된 대로).**

- 날짜 경계는 양끝 포함(`>=`, `<=`). `modified_at` 이 NULL 인 문서는 날짜 필터가 하나라도 있으면
  제외된다(SQL 3값 논리).
- `mime_types` 는 정확 일치 OR 이며 접두 매칭이 아니다. Notion 문서는 `mime_type` 이 NULL 이라
  이 필터를 주면 **항상 제외**된다.
- 검증(`_validate_options`): ISO8601 파싱 실패, `modified_after > modified_before`, 빈 `mime_types`,
  원소 20개 초과, 원소 128자 초과 → `ValidationError`. 날짜만("2026-08-01") 표기도 허용된다.

**응답.** `mime_type` 만 추가로 노출한다(이미 적재된 `document_meta` 행에서 읽어 추가 SQL 0).
클라이언트가 다음 질의를 `mime_types` 로 좁히려면 각 결과의 타입을 알아야 하기 때문이다.
`created_at`·`owner` 는 노출하지 않는다.

**재동기화가 본문 재수집을 부르지 않게 한 것이 이 작업의 핵심 제약이다.**
`_apply_changes` 의 반환값은 `_stage_upsert` 에서 `needs_body_index` 로 쓰여 본문 재fetch·재색인을
트리거한다. 새 컬럼을 변경 판정에 넣었다면 백필 첫 실행에서 전 문서가 NULL → 값 으로 바뀌며
`updated` 로 잡혀 Drive 본문을 전량 다시 받았을 것이다(rate limit·시간 폭발). 그래서 새 필드는
**무조건 대입만 하고 `is_changed` 판정에서 뺐다** — 백필은 UPDATE 한 번으로 끝나고, `updated`
집계와 본문 색인 트리거는 기존 의미(제목·URL·수정시각이 실제로 바뀐 문서)를 유지한다.
같은 함정을 다시 밟지 않도록 이 규약을 `_apply_changes` docstring 에 남겼다.

**운영 순서(릴리스 노트 필수).** `refresh_index` docstring 에 런북으로 남겼다:
마이그레이션 → 코드 배포 → 프로젝트·소스별 `refresh_index(index_bodies=False)` 1회 →
`SELECT source, count(*) FILTER (WHERE mime_type IS NULL), count(*) FROM document_meta GROUP BY source;`
로 백필 확인. **백필 전에는 `mime_types` 필터가 Drive 문서까지 전부 걸러낸다**(값이 NULL 이므로) —
순서를 지키지 않으면 "검색이 갑자기 0건"으로 보인다.

**개인정보 유의.** Drive `owners[0].emailAddress` 를 저장하므로 문서 소유자 이메일이 DB 에 남는다.
사내 도구 전제로 진행했으며, 문제가 되면 `_owner_from_raw` 를 표시 이름만 반환하도록 바꾸고
컬럼을 재백필하면 된다.

### 5.5 개선 #2 에서 남긴 후속 항목 (미착수)

- `owner` 필터(`owner` 파라미터)와 응답 노출. 컬럼·수집은 이미 끝나 있어 재동기화 없이 배선만 하면 된다.
  컬럼을 미리 넣은 이유가 이것이다 — `files.list` fields 확장은 전량 재동기화를 다시 요구하므로,
  후속으로 미뤘다면 같은 비용을 두 번 치렀을 것이다.
- `created_after`/`created_before` 필터. 같은 이유로 값은 이미 쌓인다.
- folderId(폴더 경로) / sharedWith 필터는 여전히 미구현이며, 폴더 경로는 Drive 계층 순회 비용이
  걸려 별도 설계가 필요하다.

차순위(V1 제외): document aggregation 정교화(best + second-best 보너스), cross-encoder reranking,
score 정규화(0~1 매핑).

### 5.6 개선 #4 구현 결과 (2026-08-26)

설계·판정 문서: `docs/architect-review/58_keyword_arm_compound_symmetry_design.md`,
`docs/architect-review/59_keyword_arm_compound_symmetry_code_verdict.md`. 커밋 `985e44d`.

**수단 선택 — 생성 컬럼이 아니라 질의 측 분해.** 원안이 제시한 두 후보 중 `text_collapsed`
생성 컬럼 + trgm 은 채택하지 않았다. (1) `chunk.text` 사본을 하나 더 들게 돼 시스템에서 가장
큰 테이블이 최대 2배가 되고 긴 텍스트 trgm GIN 의 크기·빌드·INSERT 비용이 붙는다.
(2) 본문에 대한 부분문자열 매칭은 5.3절이 지적한 경계 무시 왜곡의 확대 재생산이다 — 제목에서
그 성질을 없애는 T1 과 방향이 정반대다. (3) trgm 히트는 `ts_rank` 가 없어 arm 안에 두 번째
점수 경로와 근거 없는 상수가 또 생긴다. (4) 되돌리기 비용이 마이그레이션 + 전량 재기록이다.
평가셋이 없어 효과를 사전 계측할 수 없는 상황에서는 되돌리기가 싼 쪽을 먼저 넣는 것이 맞다.

**T1 — `_collapse_match_score` 토큰 경계 정렬(5.3절 해소).** 판정을
`_token_aligned_concat_match`(개선 #3 에서 게이트용으로 만든 함수) 위임으로 바꿨다. 점수 값
`1/token_count` 와 `max(token_score, collapsed_score)` 합성 규칙은 그대로다. 함께
`_title_score` 의 `collapse(title) + collapse(url)` 이어붙이기를 title 토큰열·url 토큰열 개별
판정 후 `max` 로 분리했다 — 이어붙인 경계를 걸쳐 매치되던 유령 매치를 없애고
`_passes_title_gate` 와 판단 기준을 일치시킨다. `_match_positions`(스니펫 위치)는 그대로 뒀다:
점수가 엄격해지는 방향이라 "점수는 매치인데 스니펫 위치가 없다"는 불일치가 생기지 않는다
(느슨한 쪽이 항상 상위집합).

부수 효과로 **fetch 전략이 양방향 복합어 대칭을 자동으로 얻었다.** `_body_score` 가
`_token_aligned_concat_match(query, documents_tokenize(body))` 를 쓰게 되면서 질의 `'결제장애'`
↔ 본문 `['결제','장애','대응']`, 질의 `'결제 장애'` ↔ 본문 `['결제장애']` 가 모두 잡힌다.
즉 T1 = 파이썬 경로의 대칭, T2 = SQL 경로의 대칭이다.

**T2 — keyword arm 질의 측 분해.** 두 방향을 각각 다른 수단으로 덮는다.

| 미스 방향 | 예 | 수단 |
| --- | --- | --- |
| 질의 띄어씀 / 본문 붙여씀 | 질의 `'결제 장애'`, 본문 `'결제장애'` | **concat term** — 인접 토큰의 연속 run 을 이어붙인 lexeme 을 OR 로 추가 |
| 질의 붙여씀 / 본문 띄어씀 | 질의 `'결제장애'`, 본문 `'결제 장애'` | **split phrase term** — 순수 한글 토큰의 2분할을 tsquery 구문 연산자 `<->` 로 묶어 OR 로 추가 |

- 분할은 사전 없이 경계를 알 수 없어 **가능한 2분할을 전부** 넣는다(양쪽 조각 2음절 이상).
  엉뚱한 분할은 `<->` 인접성 강제로 사실상 걸러진다 — 매치된다면 본문에 실제로 그 두 조각이
  붙어 있다는 뜻이다.
- concat run 은 **스크립트 경계를 넘지 않을 때만** 만든다. `TEXT_TSV_EXPRESSION` 이 ASCII ↔ 한글
  경계에 공백을 넣으므로 `'get요청'` 같은 혼합 lexeme 은 본문에 존재할 수 없고, 만들어봐야
  죽은 term 이다.
- `COMPOUND_TERM_LIMIT = 32` 로 파생 term 총량을 캡한다(`query` 길이·`query_variants` 개수에
  검증이 없어서다). 생성 단계에서 상한 도달 즉시 멈추고, 원본 질의 파생이 예산을 먼저 쓰며
  variant 는 남은 예산만 나눠 쓴다 — variant 수가 늘어도 전체 상한은 고정이다. 평가셋이 없어
  근거 있는 값이 아니므로 `RRF_K`·`TITLE_ARM_WEIGHT` 와 같은 방침으로 모듈 상수 고정, env 미노출.
- **필터와 점수의 분리 규약은 유지된다.** 필터 측(`terms`/`phrase_terms`)은 원본 + 각 variant
  문자열에서 개별 파생하고(variant 끼리·원본과 variant 를 가로질러 concat 하지 않는다), 점수
  측(`score_terms`/`score_phrase_terms`)은 **원본 질의 파생만** 쓴다. 파생 term 을 점수에 넣는
  것은 기존 "variant 는 점수에서 제외" 규약과 충돌하지 않는다 — variant 는 호출자가 넣은 다른
  표현이지만 concat/split term 은 같은 표층 문자열의 띄어쓰기 변형이다. 여기서 점수를 빼면
  복합어로만 걸린 문서가 keyword arm 최하위로 밀려 개선 #4 자체가 무력해진다.
- 저장소는 `search_endpoint_by_text(..., phrase_terms=None, score_phrase_terms=None)` 로 받는다.
  **두 인자가 `None` 이면 생성되는 tsquery 문자열이 기존과 완전히 동일**하므로 엔드포인트 검색
  (`endpoint_candidate_search`)은 무변경이다(개선 #2·#3 과 같은 규약). phrase 조각도
  `_quote_tsquery_lexeme` 를 통과시켜 tsquery 연산자로 오인되지 않게 한다.

**비용.** 마이그레이션·재색인·새 인덱스 없음. 기존 `ix_chunk_text_tsv`(GIN)를 그대로 쓰고
증가분은 tsquery operand 수뿐이다(상한 32). 5절 표의 "소폭 증가(인덱스 1개 추가)" 예상치는
생성 컬럼 안 기준이었으므로 실제는 그보다 낮다.

**동작 변화(의도된 것).** 부분문자열로만 걸리던 collapse 매치는 점수 0 이 된다 — 개선 #3
게이트와 같은 성격의 precision 교환이다. `'api'` 가 제목 `'Rapid Onboarding Guide'` 에 대해
받던 1.0 만점이 사라진다.

**남긴 것.**

- ASCII 복합어(`'apikey'` ↔ `'api key'`)의 분할은 v1 범위 밖이다. 한국어가 문제의 축이고,
  ASCII 를 열면 잡음 분할이 급증한다. 필요해지면 상수 하나로 연다.
- 3분할 이상 복합어(`'결제장애대응'` 질의 → 본문 `'결제 장애 대응'`)는 2분할만으로는 못 잡는다.
  계측 수단이 생기기 전에는 투기적 확장이라 미룬다.
- 스크립트가 번갈아 나오는 병적인 질의는 concat 생성이 전부 게이트에 걸려 조기 중단이 발동하지
  않으므로 run 열거가 O(n^2) 남는다(문자열은 만들지 않아 메모리 폭증은 없다). 실사용 질의
  규모에서는 무시할 수준이라 후속 선택 항목으로 둔다(59절 F6).
- IDF·문서 길이 정규화는 여전히 부재다(9절 항목 5, V2).

---

## 6. 과도한 설계 검토 (현재 규모 기준)

현재 규모 전제: 프로젝트 단위 Drive 폴더 몇 개, 문서 수백~수천 건, 동시 사용자 소수(팀 내부 MCP).

| 권장 항목                                                        | 판정          | 근거                                                                                                                                                                                                                                                                                                     |
| ---------------------------------------------------------------- | ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Query expansion (서버 내장)**                                  | **과도**      | 이미 `query_variants` 로 클라이언트 LLM 이 담당한다. 서버가 동의어 사전을 갖거나 LLM 을 호출하면 비용·지연·비결정성을 서버가 지고, "MCP 는 어떻게 검색할지만" 이라는 경계도 깨진다. **현행 유지가 정답.** 단 variant 를 벡터 arm 에도 쓸지는 별도 판단(현재 미반영, 다중 임베딩 = 비용 증가라 보류 타당) |
| **Cross-encoder reranker**                                       | **과도(V2)**  | 후보 30~50건 × CPU 추론 20~50ms = 0.6~2.5초. 지금은 검색 전체가 수십 ms 다. 문서 수백 건 규모에서 RRF 상위 5건의 오정렬은 LLM 이 스니펫 보고 걸러낼 수 있는 수준이고, 무엇보다 **근거 노출(개선 1번)이 안 된 상태에서 reranker 를 붙이는 건 순서가 틀렸다**                                              |
| **LLM reranker**                                                 | **과도**      | 권장안 12단계가 이미 "최종 의미 판단은 LLM"이라고 정해뒀다. MCP 안에서 또 LLM 을 부르면 같은 판단을 두 번 하고 비용도 두 번 낸다. 명백한 중복                                                                                                                                                            |
| **Document-level aggregation (best+second-best+metadata score)** | **부분 과도** | 현재의 dedupe-first(= best chunk rank)만으로도 문서 단위 순위는 성립한다. 다만 헤딩 없는 PDF 는 문서당 청크가 수십 개라 "몇 개나 걸렸는가"가 실제 신호가 된다. **weight 튜닝이 필요한 metadata score 는 과도, second-best 보너스 같은 단순 규칙은 저비용** — V1.5 로                                     |
| **Query rewrite / retry (서버 내장)**                            | **과도**      | 재검색 판단은 결과를 보는 쪽(LLM)이 해야 한다. 서버가 내부에서 재시도하면 latency 가 2배가 되고, 서버는 "결과가 나쁘다"를 판정할 근거(정답 개념)가 없다. **대신 재시도 판단 재료(신뢰도 신호·arm 기여)를 응답에 주는 것이 옳은 대응** — 개선 1번이 이걸 커버한다                                         |
| **Metadata score (랭킹 신호로 혼합)**                            | **부분 과도** | 최신성 가중 같은 건 그럴듯하지만 평가셋 없이 가중치를 정하면 근거 없는 튜닝이다. **hard filter 가 먼저**(개선 2번), 랭킹 혼합은 코퍼스 평가로 효과를 잰 뒤                                                                                                                                               |
| **별도 BM25 엔진(Elasticsearch/OpenSearch)**                     | **과도**      | 문서 수천 건에 검색 인프라를 하나 더 운영하는 건 명백한 초과 설계다. PostgreSQL FTS 의 진짜 약점은 엔진이 아니라 **IDF 부재**인데, 이건 `ts_rank_cd` 조정이나 term 별 문서빈도 가중을 SQL 로 얹어 상당 부분 완화 가능하다. 문서 10만 건, 동시 질의 수십 QPS 를 넘어설 때 재검토                          |

---

## 7. 최종 추천 아키텍처

### V1 — 최소 변경, 충분한 품질 (지금 착수 권장)

```
User
 └─ 자연어 요청
LLM (MCP client)
 └─ intent 추출 → search_documents(
        query="결제 시스템 장애",
        query_variants=["결제 오류","PG 장애","payment incident"],
        project="payments",
        modified_after="2026-07-01",           # [신규] 개선 2
        mime_types=["application/pdf","application/vnd.google-apps.document"],  # [신규] 개선 2
        top_k=5)
MCP Server
 ├─ 1. 검증 + 토큰화 (현행)
 ├─ 2. hard filter 적용 — project/source/modified_at 범위/mime_type   [구현 완료, 개선 2]
 ├─ 3. title arm  — trgm ILIKE 후보 → 토큰 경계 게이트 → _title_score   [구현 완료, 개선 3]
 ├─ 4. keyword arm — chunk FTS(OR) + collapse 대칭 매칭               [collapse 신규, 개선 4]
 ├─ 5. vector arm  — e5-small 질의 임베딩 → HNSW cosine
 ├─ 6. weighted RRF (title 0.5 / keyword 1.0 / vector 1.0, k=60)      [구현 완료, 개선 3]
 ├─ 7. dedupe-first → 문서 단위 순위, 승자 청크 = 최상위 arm 기준     [병합 규칙 수정]
 └─ 8. 응답 조립 — matched_chunks[], match_reasons(arm 기여+필터 일치),
        modified_at, indexed 플래그                                   [신규, 개선 1·5]
LLM
 └─ 근거를 보고 판단 → 부족하면 query_variants 바꿔 재호출,
    확신 서면 get_document(source, external_id)로 원문 확인 → 최종 답변
```

V1 이 권장안 13단계에 대응하는 방식:
1(intent) LLM + 확장된 파라미터 / 2(expansion) `query_variants` / 3(metadata) hard filter /
4(chunking) 현행 유지 / 5(keyword) FTS+collapse / 6(semantic) 현행 / 7(hybrid) 가중 RRF /
8(candidate) width 50 현행 / 9(rerank) **생략** / 10(aggregation) dedupe-first 현행 /
11(response) 근거 포함 / 12(final judgment) LLM / 13(retry) LLM 이 근거 보고 재호출.

### V2 — 데이터·트래픽 증가 후 (문서 1만 건 이상 또는 품질 불만이 계측된 뒤)

```
User → LLM (intent 구조화)
MCP Server
 ├─ hard filter (+ owner/sharedWith, folder path)
 ├─ 3-arm 후보 확장 (arm 당 100건)
 ├─ weighted RRF → 상위 30~50 candidate
 ├─ [신규] rerank — cross-encoder(다국어 소형, ONNX CPU) 또는
 │         경량 feature 랭커(제목매치·최신성·arm합의·청크수)
 ├─ [신규] document aggregation — best + second-best chunk + 청크 히트수
 ├─ [신규] score 정규화 (0~1) + confidence 밴드(high/medium/low)
 └─ 응답 — matched_chunks 복수 + match_reasons + confidence
LLM → 최종 답변 (confidence low 면 자체 재질의)
```

V2 착수 조건(먼저 갖춰야 할 것): **평가 코퍼스와 회귀 지표**. 지금
`tests/fixtures/corpus_eval/` 이 있으므로 여기에 Drive 문서 질의셋을 붙여
nDCG/recall@k 를 재는 게 V2 의 실제 선행 작업이다. 이것 없이 reranker 를 붙이면
개선 여부를 증명할 수 없다.

---

## 8. 최종 결론

### (1) 현재 로직 그대로 유지 가능한가

**뼈대는 유지 가능하다. 계약(응답 스키마)과 필터는 유지 불가하다.**

- 유지: 사전 색인 + 로컬 하이브리드 검색, RRF 융합, title arm 편입, 질의 확장을 클라이언트 LLM 에
  위임한 경계. 이 네 가지는 현재 규모에 정확히 맞는 선택이고 바꿀 이유가 없다.
- 유지 불가: (a) LLM 이 추출한 intent(날짜·타입·작성자)를 받을 파라미터가 없다는 것,
  (b) 응답에 근거·수정일·색인 여부가 없어 LLM 이 최종 판단을 할 수 없다는 것.
  이 둘은 "LLM 이 무엇을 검색할지, MCP 가 어떻게 검색할지"라는 전제 자체를 깨고 있다.
- 별도 트랙: 권한 모델 부재는 품질이 아니라 보안 사안이다. 코드 수정 전이라도
  "서비스 계정에 공유된 폴더 = 전체 공개"라는 전제를 운영 문서에 명시해야 한다.

### (2) 가장 먼저 바꿀 것

**MCP 응답에 근거를 싣는 것(개선 1번).** 이유는 셋이다.

1. 이미 계산해놓고 버리는 값들이다 — `match_type`(어느 arm 기여), `row.modified_at`,
   `row.document_id`(색인 여부). 추가 쿼리 없이 필드만 채우면 된다.
2. 나머지 모든 개선의 검증 수단이다. 근거가 노출돼야 "왜 이 문서가 1위인지"를 사람이 확인할 수
   있고, 가중치 조정·collapse 대칭·reranker 의 효과를 판단할 수 있다.
3. LLM 의 재질의(권장안 13단계)와 최종 판단(12단계)이 여기에 직접 달려 있다.

그 다음 순서: 개선 3(title arm 게이트 + 가중 RRF, 저비용 고효과) → 개선 2(메타데이터 필터,
마이그레이션 동반) → 개선 4(한글 복합어 대칭) → 개선 5(커버리지 가시화).

**진행 상황(2026-08-26): 개선 1·3·2·4 구현 완료(5.1·5.2·5.4·5.6절). 남은 것은 개선 5**
(커버리지 가시화 — `indexed` 플래그만 개선 #1 로 반영됐고 `refresh_index` 응답 노출은 미착수)
**와 후속 항목**(개선 #2 의 `owner`/`created_at` 필터 — 5.5절).

### (3) 완성도 평가 — **65 / 100**

| 항목                                  | 배점    | 점수   | 근거                                                                                               |
| ------------------------------------- | ------- | ------ | -------------------------------------------------------------------------------------------------- |
| 검색 구조(하이브리드·RRF·인덱스 설계) | 20      | 17     | HNSW+GIN, 3-arm RRF, 융합 키 통일까지 교과서적. arm 가중 조절 수단 부재로 -3                       |
| Chunking                              | 15      | 11     | 헤딩 기반 + 토큰 상한 + 계층적 분할은 우수. overlap 0, 헤딩 없는 PDF 의 단일 섹션 문제로 -4        |
| Recall                                | 15      | 10     | 넓은 후보 폭·title arm 폴백은 좋음. 본문 collapse 부재, 미지원 MIME 영구 누락, 폴더 상한으로 -5    |
| Precision / Ranking                   | 15      | 8      | IDF 없음, title arm 동일 가중, 증거량 미반영, reranking 부재                                       |
| 응답 계약(근거·설명가능성)            | 10      | 5      | 스니펫·`snippet_as_of`·`external_id` 는 좋은 설계. matched_chunks·match_reasons·수정일·신뢰도 전무 |
| 메타데이터 / 필터                     | 10      | 3      | project/source 뿐. `modified_at` 은 저장만 하고 미사용                                             |
| 권한 / 보안                           | 10      | 4      | 최종 사용자 단위 접근제어 없음, 전제도 미문서화. 다만 SA 스코프가 폴더로 한정되긴 함               |
| 운영 / 실패 처리                      | 10      | 7      | 부분 실패 격리·재시도 가능 상태 보존은 견고. 조용한 퇴화(색인 누락·폴더 상한)를 노출 안 해 -3      |
| **합계**                              | **100** | **65** |                                                                                                    |

해석: **"검색 엔진으로서의 뼈대는 잘 만들었고, LLM 에게 넘기는 계약이 미완성인 상태"** 다.
65점의 감점 대부분(응답 계약 5/10, 메타데이터 3/10)은 랭킹 알고리즘이 아니라 인터페이스에서 나왔고,
그 둘은 개선 1·2번으로 대부분 회수된다. 이 두 개만 반영해도 실질 체감은 80점대에 들어간다.

---

## 9. 항목별 종합 평가표 (순차 / 기존 / 제안 / 평가)

2절의 18개 항목을 그대로 두고, 3절(장점)·6절(과도 설계 판정)·8절(결론)의 판단을 종합해
항목마다 **평가** 한 줄을 붙인다. 새 조사 없이 이 문서 안의 판단만 합친 것이다.
우선순위 번호는 8절 (2)의 착수 순서(개선 1 → 3 → 2 → 4 → 5)를 따른다.

| #   | 항목                        | 기존 로직                                                                                  | 제안(권장) 로직                                                         | 평가                                                                                                                                                              |
| --- | --------------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Intent parsing              | 구조화 슬롯이 `source`/`project`/`query_variants` 뿐                                       | LLM 이 query/people/date_range/type/folder 로 구조화해 전달             | **제안 채택 필요(우선순위 3위)** — 추출은 LLM 이 이미 할 수 있고 받을 그릇만 없다. 항목 3(메타데이터 필터)과 같은 작업으로 함께 처리                              |
| 2   | Query expansion             | 서버 확장 없음, 호출 LLM 이 `query_variants` 제공(후보 필터만, 점수 불참)                  | 동의어·약어·영문 일부 확장                                              | **기존 유지가 적절** — 확장 주체를 LLM 에 둔 경계가 옳다(3절 장점 5, 6절 과도 판정). 서버 내장은 비용·지연·비결정성을 서버가 지는 퇴보                            |
| 3   | Metadata filtering          | `project`/`source` + `modified_after`/`modified_before`/`mime_types` hard filter            | createdTime/modifiedTime/mimeType/folderId/owner/sharedWith hard filter | **날짜·mimeType 구현 완료 (2026-08-26)** — 개선 #2 로 반영. `owner`/`created_at` 은 컬럼·수집까지만(재동기화를 두 번 하지 않으려고 미리 채웠다), folderId/sharedWith 는 미구현 |
| 4   | Chunking                    | 헤딩 기반 섹션 → 480토큰 초과 시 문단/문장/하드컷 그리디 분할, overlap 0                   | Heading 우선, 500~1,000 token, 50~150 overlap                           | **절충(부분만 채택)** — 480 상한은 임베딩 모델 실측 제약이라 유지가 맞다(3절 장점 7). overlap 도입과 헤딩 없는 PDF/DOCX 의 단일 섹션 문제만 별도 과제             |
| 5   | Keyword/BM25                | PostgreSQL FTS(`simple`) + `ts_rank`, OR 결합, **질의 측 복합어 분해(concat + `<->` phrase)**, IDF·길이정규화 없음 | BM25 또는 FTS, 코드·고유명사에 강할 것                                  | **절충(부분만 채택) — 복합어 대칭은 구현 완료 (2026-08-26)** — 별도 BM25 엔진 도입은 과도(6절). 한글 복합어 collapse 대칭은 개선 #4 로 반영(질의 측 분해 채택, 생성 컬럼 미채택 — 5.6절), IDF 보정은 V2 |
| 6   | Vector/Semantic             | multilingual-e5-small(384d), passage/query 접두사 규약 준수, HNSW cosine                   | Embedding 기반 vector search                                            | **기존 유지가 적절** — 구조적 결함 없음. 모델 교체는 평가셋이 생긴 뒤의 논의                                                                                      |
| 7   | Hybrid retrieval            | RRF(k=60), title 0.5 / keyword 1.0 / vector 1.0 가중                                       | semantic+lexical+metadata weighted sum 또는 RRF                         | **구현 완료 (2026-08-26)** — 개선 #3 로 arm 가중 도입. metadata score 혼합은 평가셋 없이는 근거 없는 튜닝이라 계속 보류(6절)                                       |
| 8   | Candidate retrieval         | arm 당 `width = max(top_k*4, 50)`, 합집합 최대 150건                                       | BM25 Top30 + Vector Top30 → 30~50                                       | **기존 유지가 적절** — 후보 폭이 이미 권장안 이상. 외부 호출 0 구조라 넓은 스캔 부담도 없다                                                                       |
| 9   | Dedup                       | arm 별 문서 첫 등장 등수만 채택                                                            | merge/dedup                                                             | **기존 유지가 적절** — 권장안과 동등하며 문서 1건이 슬롯을 독식하는 것도 막힌다                                                                                   |
| 10  | Reranking                   | 없음. 융합 순서가 곧 최종 순서                                                             | 상위 candidate 를 query 와 재비교해 정밀 정렬                           | **절충(V2 로 연기)** — cross-encoder 는 현 규모에 과도(6절, 0.6~2.5초 추가). 근거 노출(항목 14)이 먼저이며, 착수 조건은 평가 코퍼스 확보                          |
| 11  | Document aggregation        | dedupe-first = best chunk rank 만 반영                                                     | best + second-best chunk + metadata score                               | **절충(부분만 채택)** — second-best 보너스 같은 단순 규칙은 저비용이라 V1.5. metadata score 는 과도(6절)                                                          |
| 12  | Permission / access control | 없음. 서비스 계정이 본 것 = 모든 호출자가 검색 가능                                        | owner/sharedWith 를 필터·시그널로 활용                                  | **제안 채택 필요(별도 트랙)** — 품질이 아니라 보안 사안(8절). 코드 변경 전이라도 "SA 공유 폴더 = 전체 공개" 전제를 운영 문서에 즉시 명시                          |
| 13  | Result scoring              | RRF 절대값(0.016~0.05), 순서 정보만 유효                                                   | 0~1 정규화 score                                                        | **절충(부분만 채택)** — 정규화·confidence 밴드는 V2. V1 은 항목 14 의 arm 기여 노출로 "왜 뽑혔는가"를 먼저 준다                                                   |
| 14  | MCP response schema         | title/source/project/url/snippet/score/version/snippet_as_of/external_id **+ matched_chunks·match_reasons·modified_at·indexed** | document_id/title/url/score/matched_chunks[]/match_reasons[]            | **구현 완료 (2026-08-25)** — 개선 #1 로 반영. 추가 쿼리 0 으로 근거·최신성·색인 여부를 노출한다. 문서 키는 `get_document` 와 맞물리는 `external_id` 를 유지했다 |
| 15  | Search reason / evidence    | 스니펫(300자) + matched_chunks(arm 별 승자 청크) + match_reasons(고정 문구)               | match_reasons 로 근거 명시                                              | **구현 완료 (2026-08-25)** — 항목 14 와 같은 작업으로 반영. title-only 히트는 `indexed=false` 와 `본문 미색인 — 제목 매칭만으로 검색됨` 근거로 구분된다 |
| 16  | Query rewrite / retry       | 서버에 없음. docstring 이 LLM 의 재호출을 유도                                             | confidence 낮으면 재검색                                                | **기존 유지가 적절** — 서버 내장 재시도는 과도(6절, latency 2배 + 판정 근거 없음). 대신 재시도 판단 재료(신뢰도·arm 기여)를 항목 14 로 제공                       |
| 17  | Latency                     | 검색 경로 외부 호출 0, SQL 3~4회 + 로컬 임베딩 1회                                         | 명시 없음                                                               | **기존 유지가 적절** — 현행이 권장안보다 유리하다. 이후 개선은 이 특성을 깨지 않는 선에서만(reranker 를 V2 로 미룬 이유)                                          |
| 18  | 검색 실패 처리              | `{error, code, message}`, 0건은 빈 리스트. 색인 누락·MIME 미지원·폴더 상한은 서버 로그에만 | 명시 없음                                                               | **제안 채택 필요(우선순위 5위)** — 실패/결과없음/미색인이 구별되지 않는다. `refresh_index` 응답과 검색 결과에 커버리지 신호를 노출                                |

**요약**: 18개 중 기존 유지 6건(2·6·8·9·16·17), 제안 채택 6건(1·3·12·14·15·18),
절충 6건(4·5·7·10·11·13). 채택분의 착수 순서는 14·15 → 7 → 1·3 → 5 → 18 이고,
**14·15 는 개선 #1 로 2026-08-25, 7 은 개선 #3, 3 은 개선 #2, 5 는 개선 #4 로 2026-08-26 구현 완료**
(5.3절 파생 항목은 개선 #4 의 T1 로 함께 해소) — 남은 채택분은 1(intent parsing 계열)·12·18 이다.
12(권한 전제 문서화)는 코드와 무관하게 지금 처리 가능하다.
