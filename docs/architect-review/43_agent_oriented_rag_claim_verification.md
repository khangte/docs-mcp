# "Agent-oriented RAG" 대외 주장 vs 실제 구현 — 검증 및 판정

- 일시: 2026-08-15
- 작성: architect
- 선행: `docs/architect-review/07_search_rrf_reevaluation.md`(RRF 채택),
  `34_drive_notion_no_embedding_rationale.md`, `35_drive_notion_embedding_migration_and_refresh_strategy.md`,
  `36_user_rag_proposal_vs_our_design_diff.md`(범용 RAG 제안 대조),
  `37_document_search_phase3_rrf_verdict.md`(3-arm RRF 확정),
  `41_backfill_result_verification_and_indexed_default_gate.md`(indexed 기본 전환),
  `42_snippet_as_of_mcp_exposure_verdict.md`
- 질문: "docs-mcp는 Candidate/Evidence 분리 + LLM 중심 Retrieval Planning +
  Lazy Document Fetch + Agentic Retrieval 구조라서 일반 MCP/RAG보다 유리하다"는
  주장이 실제 코드와 맞는가. 일반 MCP/RAG 쪽 로직이 더 적절한 항목이 있는가.

---

## 판정 요약

**설계 방향 판정: 7개 비교 항목 어디에서도 일반 MCP/RAG 쪽 로직이 더 적절하지 않다. 방향 전환 불필요.**

**단, 주장의 사실관계 2건이 현재 코드와 어긋나고, 주장이 전제하는 동작을 실제로는
지탱하지 못하는 구현 갭 4건이 있다.** 주장 문구를 대외 문서에 그대로 옮기면 안 되고,
갭 1번은 "Agentic Retrieval" 주장 자체가 실전에서 성립하지 못하게 만드는 수준이다.

| 원문 주장 | 코드 대조 | 판정 |
|---|---|---|
| 검색 방식 Title+Keyword+Vector 3-arm | 일치 (`_search_indexed`) | 정확 |
| 랭킹 RRF 통합 | 일치 (`search/rrf.py`) | 정확, 일반 RAG보다 우월 |
| 검색 단계 원문 조회 안 함 | 기본값에서만 참, **원인 귀속이 틀림** | **부정확** |
| 원문 조회 시점 = LLM 필요 시 | `get_document`는 일치, **"본문 미캐시"는 거짓** | **부정확** |
| Query 확장 = LLM 수행 | 일치 (`query_variants`) | 정확, 유지 |
| 재검색 = LLM 루프 | 일치 (서버 retry 없음) | 정확, 유지 |
| 서버 역할 = 후보 제공 | 부분 참 — **랭킹 계획은 서버가 갖고 있다** | 과장 |

---

## 1. 정확한 항목 — 방향 유지

### 1.1 3-arm + RRF (정확)

`app/services/documents/document_search_service.py:474` `_search_indexed`가
title(`document_meta` 토큰 매칭) / keyword(`chunk` FTS) / vector(pgvector) 3-arm을
돌리고 `reciprocal_rank_fuse(..., title_ref_ids=...)`로 융합한다. 융합 키는
`deterministic_document_id(project, source, external_id)`라 미색인 문서도 title arm
단독으로 결과에 남는다(별도 폴백 분기 없음).

**"단순 점수 합산"보다 RRF가 맞다.** `ts_rank`(FTS)와 코사인 유사도는 스케일이 달라
직접 더하면 가중치가 암묵적으로 왜곡된다. doc07에서 이미 판정한 건이고, 재론할 근거가
새로 생기지 않았다.

**단 대가가 있다(원문은 언급하지 않음).** RRF 점수는 등수의 역수 합이라 **절대값이
무의미**하다(`app/mcp/types.py:162` docstring이 명시). Agentic 루프에서 LLM이
"이 결과가 충분히 좋은가 → 재질의할까"를 판정하려면 임계값이 필요한데, 그 신호가 없다.
현재는 LLM이 스니펫 내용을 읽고 판정할 수 있으므로 실사용에 막히지 않는다 — 관측되지
않은 문제를 위해 점수 체계를 바꾸지 않는다(YAGNI). 재질의 실패가 실측되면 그때
arm별 기여(`match_type`) 노출을 검토한다.

### 1.2 Query 확장·재검색을 LLM에 위임 (정확, 유지)

`DocumentSearchOptions.query_variants`가 호출자에게서 오고, 서버는 동의어 사전도 LLM
API도 갖지 않는다. 규율도 정확하다: variant 토큰은 **SQL 후보 게이트만 넓히고**
점수 계산은 원본 질의 토큰만 쓴다(`_select_candidates`, `_title_arm`,
`ChunkRepository.search_endpoint_by_text`의 `score_terms` 분리). variant-only 매치가
원본 매치보다 상위로 올라오는 사고를 구조적으로 막는다.

**이 방향이 맞다.** MCP 서버의 호출자는 이미 LLM이다. 서버가 질의 확장을 위해 별도
LLM API를 부르면 같은 추론을 두 번 사고 지연·비용·모델 버전 불일치를 떠안는다
(기존 판단과 동일 — 메모리 `mcp-delegate-reasoning-to-client-llm`).
서버 내부 자동 retry도 같은 이유로 반대다: "무엇으로 재질의할지"를 서버가 정하려면
결국 서버 안에 또 하나의 LLM이 필요하다. `search_documents` docstring이 0건/부족 시
`query_variants` 재호출을 명시적으로 지시해 루프를 안내하는 현재 방식이 옳다.

---

## 2. 부정확한 항목 — 대외 문서에 그대로 쓰면 안 됨

### 2.1 "검색 단계에서 원문 조회를 하지 않는다" — 기본값에서만 참, 원인 귀속이 틀렸다

두 전략이 이 항목에서 **정반대**다.

- `document_search_strategy="fetch"`(롤백 스위치, `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY=fetch`):
  검색 도중 후보 본문을 **실시간으로 최대 20건 fetch한다**
  (`_body_fetch_budget`, `MAX_BODY_FETCH_CANDIDATES=20`, `MAX_CONCURRENT_BODY_FETCHES=5`).
  "검색 중 원문 조회 안 함"의 정확한 반대다. 이게 doc41 이전의 기본값이었다.
- `document_search_strategy="indexed"`(현재 기본): 검색 경로에 외부 API 호출이 0회다.

따라서 주장은 **현재 기본값에 한해서만** 참이다. 그리고 참인 이유가 "원문을 lazy하게
미룬 설계" 때문이 아니라 **동기화 시점에 본문을 미리 색인해 뒀기 때문**이다
(`refresh_index(index_bodies=True)` → `index_document_body`). 즉 검색 시점 API 호출이
사라진 공은 Lazy Fetch가 아니라 **사전 색인**에 있다. 이는 일반 RAG의 인제스트-색인
구조와 같은 것이지, 그것과 다른 차별점이 아니다.

### 2.2 "본문은 캐시하지 않는다" — 절반은 거짓

`index_document_body`(`app/services/documents/document_body_indexer.py:87`)가
`Document.raw_text = raw`로 **전문을 DB에 저장**하고, 같은 본문이 `chunk.text`에
섹션 단위로 한 번 더 저장된다. 캐시가 없는 것은 `get_document` 경로 하나뿐이고,
그것도 "캐시가 없어서"가 아니라 **최신성을 위해 캐시를 의도적으로 쓰지 않는**
선택이다(`get_document`는 항상 live fetch).

이 선택 자체는 타당하다 — LLM이 원문을 읽고 답변을 만드는 순간이 최신성이 가장
중요한 지점이다. 다만 근거를 "캐시가 없다"로 쓰면 사실과 다르고, 실제로는
**`raw_text` 캐시를 놔두고도 안 쓰는** 상태라 "API 호출 감소" 주장과 긴장 관계다.
바꾸지 않는다(freshness 우선이 맞다). 서술만 정정한다.

### 2.3 "Candidate와 Evidence의 분리" — 정도 차이지 구조 차이가 아니다

현재 구조도 검색 응답에 본문 발췌를 담는다: 승자 청크에서 뽑은 **300자 스니펫**
(`SNIPPET_MAX_CHARS = 300`). 일반 RAG가 청크 전문(≈480토큰)을 주는 것과의 차이는
"주느냐 마느냐"가 아니라 **분량**이다. top_k=5 기준 약 1.5KB로, 청크 전문 반환 대비
대략 1/4~1/5 수준이다.

이 기본값은 합리적이다 — 스니펫은 "이 문서를 열어볼 가치가 있는가" 판정에 충분하고,
판정에 실패하면 `get_document`가 있다. 유지한다. 다만 "증거를 아예 안 준다"는 서술은
과장이다.

### 2.4 "Server = Search Engine, LLM = Retrieval Planner" — 과장

서버가 실제로 갖고 있는 retrieval 계획: 3-arm 구성과 arm별 후보폭
(`width = max(top_k*4, 50)`), RRF 융합·정렬, section 청크 존재 여부 게이트
(`has_endpoint_chunks`), 벡터 arm on/off(`vector_fallback_enabled`),
전략 미인식 시 `fetch`로의 degrade, fetch 전략의 오버스캔 예산 산정.

LLM에 위임된 것은 정확히 세 가지다: **동의어 확장, 재질의 여부, 원문 열람 여부.**
즉 **질의 계획은 LLM, 랭킹 계획은 서버**다. 이 분담이 옳다(랭킹은 결정적이고 재현
가능해야 골든 회귀 테스트가 성립한다). 서술을 이 선으로 정정한다.

---

## 3. 구현 갭 — 주장을 실제로 지탱하지 못하는 지점

### 3.1 [HIGH] `search_documents` 응답에 `external_id`가 없다

`get_document(source, external_id)`가 요구하는 `external_id`가 검색 응답
(`DocumentSearchItemPayload`, `app/mcp/types.py:162`)에 **없다**. 필드는 title/source/
project/url/snippet/score/version/snippet_as_of뿐이다.

즉 LLM이 `search → get_document` 핸드오프를 하려면 `url`에서 ID를 직접 파싱해야 한다:
Drive는 `https://drive.google.com/file/d/{id}/view` 또는 `webViewLink`의 임의 형태,
Notion은 `https://www.notion.so/{slug}-{32hex}`(대시 제거됨)
(`google_drive_source.py:399`, `notion_source.py:359`). 이 파싱 규칙은 어느 docstring
에도 없다.

**주장 4번("Agentic Retrieval: search → get_document → answer")의 유일한 연결
고리가, 문서화되지 않은 URL 파싱 휴리스틱에 걸려 있다.** Lazy Fetch의 이점 전체가
이 한 필드에 의존한다.

→ **수정 방향**: `DocumentSearchItemPayload`에 `external_id: str` 추가.
`DocumentSearchItem`에도 필드를 올리고(`_build_indexed_item`/`_fetch_and_score` 둘 다
`row.external_id`를 이미 갖고 있다), `_to_document_search_payload`에서 매핑.
`search_documents` docstring에 "get_document 에 그대로 넘기는 값"이라고 명시.
추가 조회 없음, 순수 필드 노출이다. doc44와 같은 성격(계약 경계를 못 넘은 필드).

### 3.2 [MEDIUM] indexed arm이 협업 문서가 아닌 등록형 문서 청크까지 긁는다

`chunk_type="section"`은 협업 문서 전용이 아니다. `register_document` 경로
(markdown/csv/pdf/docx)도 같은 타입의 청크를 만든다(`chunk_builder.build_chunks:143`).
`_keyword_arm`/`_vector_arm`은 `project`만 필터하므로, 등록형 문서의 section 청크가
그대로 후보에 섞인다.

결과: 그 문서 ID들이 fused 슬롯(`width` ≥ 50)을 차지하고,
`document_meta`에 대응 행이 없어 `_search_indexed:528`에서
`"융합 결과가 참조하는 문서 메타를 찾을 수 없음"` WARNING을 찍고 버려진다.
**협업 문서의 recall이 조용히 깎이고 로그가 오염된다.** 등록형 문서와 협업 문서가
같은 project에 공존하는 환경에서 항상 발생한다.

→ **수정 방향**: `Document.doc_type`이 협업 색인 문서에 대해 이미 `drive`/`notion`으로
세팅돼 있다(`document_body_indexer.py:93` `doc_type=source_name`). 이걸 arm에
푸시다운한다 — `ChunkRepository.search_endpoint_by_text`/`search_by_vector`에
`doc_types: Sequence[str] | None = None` 인자를 추가하고
`Document.doc_type.in_(doc_types)` 조건을 기존 `project` 조인에 얹는다
(조인은 이미 있으므로 조건 한 줄). 문서 검색 경로만 `["drive", "notion"]`을 넘기고
엔드포인트 경로는 기본 `None`이라 무변경이다.

### 3.3 [MEDIUM] `source` 필터가 arm에 푸시다운되지 않고 융합 후에 걸린다

`_search_indexed`는 `source`를 title arm에만 넘기고(`_title_arm(..., source, ...)`),
keyword/vector arm은 필터 없이 돌린 뒤 융합 결과에서
`if source is not None and row.source != source: continue`로 버린다
(`document_search_service.py:530`).

`source="notion"`으로 검색했는데 상위 융합 슬롯을 drive 문서가 차지하면
**top_k를 채우지 못한 채 결과가 잘린다.** 필터가 좁을수록 결과가 나빠지는,
방향이 거꾸로인 동작이다.

→ 3.2와 **같은 푸시다운으로 동시에 해결**된다: `doc_types=[source]`(source 지정 시)
또는 `["drive", "notion"]`(미지정 시)을 넘기면 된다. 별도 작업이 아니다.

### 3.4 [LOW] 검색 기본값과 색인 기본값이 어긋나 조용히 약화된다

검색 전략 기본은 `indexed`(`config.py:44`)인데, `refresh_index` 도구와
`refresh_documents` 스크립트의 `index_bodies` 기본은 **False**
(`app/mcp/tools/sources.py:33`, `app/scripts/refresh_documents.py:60`).

본문 색인 없이 refresh만 돌리는 운영이 계속되면 section 청크가 없거나 낡은 채로
남고, 검색은 **예외도 경고도 없이** title arm 단독으로 퇴화한다
(`has_endpoint_chunks`가 False면 두 arm을 통째로 건너뛴다). 실패가 침묵한다.

→ **수정 방향(택1, 가벼운 쪽 권장)**: `index_bodies=False`로 refresh가 끝났을 때
"본문 색인을 건너뜀 — 검색은 제목 매칭만 사용" WARNING 로그 1줄.
기본값 자체를 True로 뒤집는 것은 refresh 비용 성격을 바꾸는 변경이라 별건으로 다룬다.

---

## 4. 결론

1. **일반 MCP/RAG 쪽 로직으로 바꿔야 할 항목은 없다.** RRF, LLM 위임 질의확장,
   서버 retry 부재, lazy get_document 모두 근거가 유효하고 선행 판정
   (doc07/35/37/39/43)과 정합적이다.
2. **주장 서술 2건은 정정 대상이다** — (a) 검색 중 원문 미조회의 원인은 Lazy Fetch가
   아니라 사전 색인이며 `fetch` 전략에서는 반대로 최대 20건을 fetch한다,
   (b) 본문은 `Document.raw_text`/`chunk.text`에 캐시된다. 포트폴리오·아키텍처 문서에
   원문 문구를 그대로 옮기지 않는다.
3. **구현 갭 4건 중 3.1이 우선순위 1이다.** `external_id` 미노출은 주장의 핵심인
   Agentic Retrieval 루프를 실제로 끊는다. 3.2/3.3은 같은 푸시다운 한 번으로 함께
   해결된다. 3.4는 로그 1줄.

권고 착수 순서: **3.1 → 3.2+3.3 → 3.4.**
