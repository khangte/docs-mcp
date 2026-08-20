# 사용자 범용 RAG 제안 vs 우리 설계 — 항목별 대조

- 일시: 2026-08-14
- 작성: architect
- 선행: `docs/architect-review/35_*`(임베딩 미사용 이유), `36_*`(도입 시 변경범위·refresh 전략)
- 질문: 사용자가 제시한 구조(Connectors→Ingestion→Postgres+pgvector→Hybrid+Rerank,
  documents/chunks 공통 스키마, BGE-M3, RRF, reranker, delete-old-chunks-then-reinsert)와
  우리 Phase0~3 설계가 같은 것인가.

## 한 줄 결론

**뼈대는 같다. 다른 곳이 셋인데, 그중 둘은 우리가 이미 실험해서 반대로 판정한 건이다.**

| 항목 | 판정 |
|---|---|
| 1. 공통 스키마(documents/chunks) | **같다** — 이미 구현돼 있음 |
| 2. 임베딩 BGE-M3 / OpenAI(1024dim) | **다르다** — 호환 불가 + 실험 후 HOLD 확정 |
| 3. cross-encoder reranker | **다르다** — 의도적으로 없음(3회 판정) |
| 4. delete-and-insert 증분갱신 | **층에 따라 갈린다** — 청크층은 같고 문서층은 반대 |
| 5. 메타데이터 필터 / RRF / MCP 툴 | **같다** — 이미 있음 |
| 5b. operationId·path exact match | **진짜 갭** — 유일하게 새로 할 일 |

---

## 1. 공통 스키마 정규화 — 같다

제안의 "documents / chunks 범용 테이블"은 우리 `document` + `chunk` 와 **실질적으로
동일**하다. `app/models/chunk.py` 파일 docstring이 이미 "검색 코어(포맷 무관)"이고,
컬럼 구성도 `text` + `embedding`(pgvector) + `text_tsv`(FTS) 로 제안과 일치한다.

차이는 "문서 타입을 테이블로 나눌 것인가, 컬럼으로 구분할 것인가" 하나뿐인데 우리는
이미 후자다 — `chunk.chunk_type ∈ {endpoint, schema, section}`. 포맷별 부속 테이블
(`api_endpoint`/`api_schema`/`document_section`)이 옆에 있지만 **검색 경로는 그것들을
타지 않는다**(청크만 탄다). 즉 제안이 "만들자"고 하는 정규화 층은 이미 서 있고,
36번 문서 Phase1이 하는 일은 거기에 Drive/Notion을 얹는 것뿐이다.

**새 테이블을 만들면 안 된다** — 지금 것을 두 번째로 만드는 셈이 된다.

## 2. 임베딩 모델 — 다르다(호환 불가, 그리고 이미 판정됨)

- 현행: `intfloat/multilingual-e5-small`, **384dim**, 로컬 CPU (ADR-0004).
- 제안: BGE-M3 **1024dim** 또는 OpenAI 임베딩.

**차원이 다르면 드롭인이 아니다.** pgvector 컬럼 dim은 테이블 생성 시 고정이라
컬럼 재생성 마이그레이션 + 전량 재임베딩이 필요하다(256→384 때 실제로 그렇게 했다 —
`alembic/versions/ff8aa8f36266_*`, `app/scripts/reembed.py`).

그런데 이건 **이미 실험한 건이다**(`docs/architect-review/15_embedding_model_swap_experiment.md`):

- bge-m3는 애초에 "후보 2순위, 조건부"였고 **미착수 확정**으로 종결됐다(§5-확정).
- 이유 (a) dense가 e5의 `query:`/`passage:` 접두사 규약을 안 써서 provider 변형이 필요,
  (b) 568M·2.2GB로 CPU 추론이 e5-small의 ~5배 무거워 **지연 게이트(G4) 확정 실패**,
  (c) 1차 후보 e5-base(768dim)가 교차언어 recall 66.7%로 게이트 미달 → "CPU 임베딩
  제약 위에서 G2와 G4를 동시에 만족하는 모델이 없다"로 **후보 공간 소진** 판정.
- OpenAI 임베딩은 ADR-0004(관리형 API 의존 제거)를 정면으로 되돌린다 — 키·비용·
  네트워크 의존이 되살아난다.

**결론: 교체 불필요. 재개 조건은 GPU 등 하드웨어 변경, 또는 e5-base int8/ONNX
양자화 실험**(docs/15 §트리거 2·4). 그 전엔 밑지는 지출이다.

## 3. reranker — 우리 설계엔 없다. 의도적이다

cross-encoder 리랭킹은 **세 번 검토돼 세 번 다 보류**됐다:

- `docs/09` P3 — 보류(2026-08-10)
- `docs/12` 후보1 — 신규 착수 비권장(이미 HOLD)
- `docs/14` 레버 표 — HOLD

핵심 근거는 지연이 아니라 **지표가 다르다**는 것이다: 이 서버는 최종 답변을 만들지
않는 **후보 피더**라 구속 지표가 recall@k(이미 88~95%)지 top-1이 아니다. 순위 정밀화의
마지막 한 뼘은 호출측 LLM이 이미 하고 있다 — 서버가 CPU cross-encoder로 매 검색마다
top-20을 재채점하는 건 같은 일을 두 번 하면서 지연만 얹는 것이다.

**필요하다고 보지 않는다.** 단 Drive/Notion 본문 청크가 들어오면 코퍼스 성격이
바뀌므로(짧은 구조적 endpoint 청크 → 긴 산문) 재평가 트리거는 될 수 있다. 그때도
순서는 recall 측정 먼저, 리랭커는 그 다음이다.

## 4. 증분갱신 — 층이 다르면 정답도 다르다

제안이 말하는 "delete-old-chunks-then-reinsert"가 **문서 1건의 청크 집합**을 뜻한다면
**우리 추천안과 완전히 같다.** 등록형 `sync_service.resync`가 이미 그렇게 한다
(`sync_service.py:185` `delete_by_document` → 재색인). 36번 문서의 추천안도 그것이다.

우리가 반대한 것은 **문서 집합 전체(목록 전량)를 지우고 다시 넣는 것**이다. 구분선:

| 층 | 방식 | 이유 |
|---|---|---|
| 문서 집합(`document_meta` 행) | **diff upsert 유지** | `modified_at`이 "이 문서는 안 바뀌었다"를 증명하는 값비싼 정보다. 전량 삭제는 매 회 그 정보를 스스로 파괴해, 임베딩 도입 후엔 refresh마다 전 문서 fetch+재파싱+재임베딩이 된다. 부분 실패 시 캐시가 빈 채 남아 검색이 0건이 되는 문제도 있다(SPEC 기능 6 위반). |
| 문서 1건의 파생물(청크·벡터) | **delete-and-insert** | 재파싱하면 청크 개수·경계가 통째로 달라져 diff가 무의미하다. 전량 교체가 정답이고 stale 벡터도 자동으로 닫힌다. |

즉 **"delete-and-insert가 맞다"와 "upsert가 맞다"는 서로 모순이 아니라 서로 다른 층의
답**이다. 제안이 이 둘을 한 규칙으로 묶었다면 그 부분만 갈라야 한다.

## 5. 제안에는 있고 우리에겐 "없어 보이는" 것 — 있는 것과 진짜 갭 구분

### 이미 있는 것 (추가 작업 없음)

| 제안 항목 | 우리 코드 |
|---|---|
| Connectors | `DocumentSource` Protocol(drive/notion) + `parser/document_router` 7개 포맷 |
| Hybrid + RRF | 기본 전략이 `rrf`. k 스윕 실험까지 완료(`docs/exec_plans/search_p4_rrf_k_sweep_plan.md`) |
| 메타데이터 필터링 | `CandidateSearchOptions(document_id, project)`, `DocumentSearchOptions(source, project)` — SQL WHERE + `Document` JOIN으로 내려간다 |
| MCP tool 스펙 | 16개 등록: `search_endpoints`/`get_endpoint_details`/`resolve_ref`/`list_tags`/`search_documents`/`get_document`/`list_documents`/`register_document`/`refresh_index` + drive·notion 소스 CRUD 7종 |
| pgvector + HNSW | `ix_chunk_embedding_hnsw`(vector_cosine_ops), FTS는 `ix_chunk_text_tsv` GIN |

### 진짜 갭 (새로 할 일 — 둘 다 값싸다)

1. **operationId로 검색이 안 된다.** 파서는 `operation_id`를 뽑지만
   (`openapi_parser.py:61,185`, `swagger2_parser.py:117`) `ApiEndpoint` 모델에 컬럼이
   없고 청크 텍스트에도 안 들어간다 — 파싱 직후 버려진다. → 컬럼 1개 추가 +
   `build_endpoint_chunk_text` 헤더에 포함. 재색인 필요.
2. **exact match 우선 단계가 없다.** path는 청크 헤더에 들어가고 `text_tsv`가 경로
   세그먼트를 토큰화하므로 "검색은 된다". 하지만 질의가 `GET /pet/{petId}`처럼 정확히
   일치할 때 **확정적으로 1위를 주는 경로가 없다** — RRF 융합 안에서 다른 신호와
   섞인다. `endpoint_repository`에 method+path 조회 메서드조차 없다(`get`은
   endpoint_id 전용). → RRF 앞단에 결정적 lookup 단계를 두는 게 정공법.

### 부분 갭 (판단 필요)

3. **태그를 검색 필터로 못 쓴다.** `tags_json`으로 저장되고 `list_tags`로 조회는
   되지만 `search_endpoints`에 tag 인자가 없다. 카탈로그 조회용으로만 산다.

---

## 권고

- 1·5(있는 것)은 그대로 두고, **새로 만들지 않는다.**
- 2·3은 **판정 유지**(임베딩 교체 HOLD, 리랭커 HOLD). 뒤집으려면 재개 조건
  (GPU / 양자화 실험 / 코퍼스 성격 변화 후 recall 재측정)이 먼저다.
- 4는 층을 갈라 적용 — 36번 추천안 그대로.
- **5b(operationId + exact match)만 별도 건으로 착수 가치가 있다.** 제안 전체에서
  우리가 실제로 얻을 게 있는 유일한 항목이다.
