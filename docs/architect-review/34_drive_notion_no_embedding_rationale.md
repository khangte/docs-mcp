# Drive/Notion 소스가 임베딩을 쓰지 않는 이유 (코드 대조)

- 일시: 2026-08-14
- 작성: architect
- 질문: Drive/Notion 커넥터/소스는 왜 임베딩 모델을 사용하지 않는가. 임베딩을 쓰는 다른 경로와 무엇이 다른가.

## 결론 한 줄

임베딩은 **본문을 영속화하는 경로에서만** 성립하는데, Drive/Notion 경로는 설계상
**본문을 저장하지 않는다**(메타 캐시만). 그래서 임베딩을 붙일 자리 자체가 없다.

## 1. 사실 확인 — 배선상 실제로 없다

- `app/services/documents/**`, `app/mcp/tools/documents.py` 전체에 `embedding`
  참조가 **0건**이다.
- `app/composition.py` 에서 `embedding_provider` 가 주입되는 곳은 두 군데뿐이다:
  `IndexerService`(`:184`)와 `VectorSearch`(`:196`). 둘 다 등록형(OpenAPI/PDF/DOCX…)
  파이프라인 소속이다.
- `DocumentSearchService`/`DocumentIndexService` 의 생성자 인자는
  `(meta_repo, resolver)` 뿐이다 — 임베딩 의존성이 애초에 없다.

## 2. 두 경로의 구조 차이

| | 등록형 문서(OpenAPI/PDF/DOCX…) | 협업 문서(Drive/Notion) |
|---|---|---|
| 진입 | 사용자가 문서를 **등록**(ingest) | `refresh_index` 가 **목록만** 동기화 |
| 저장 | `chunk` 테이블에 **본문 청크 영속** + `embedding`(pgvector 384dim, HNSW) | `document_meta` 에 **제목/URL/수정시각만** — 본문 미저장 |
| 본문 출처 | DB(색인 시점 스냅샷) | 매 요청마다 외부 API **실시간 fetch** |
| 랭킹 | 키워드 FTS + 벡터를 RRF 융합 | 제목 토큰 매칭(1단계) → 본문 fetch 후 결합 점수(2단계) |
| 어댑터 | `DocumentSource` 아님(파서/인제스터 경로) | `DocumentSource` Protocol(`list_files`/`fetch`) |

근거 코드: `app/services/indexer/indexer_service.py:105-128`(청크 임베딩 후
`Chunk.embedding` 저장), `app/services/documents/document_index_service.py:1-20`
("메타데이터만 upsert 하고, **본문은 가져오지 않는다**"),
`app/services/documents/document_search_service.py:16-17`("본문은 절대 캐시하지
않는다"), `docs/exec_plans/docs_mcp_expansion/SPEC.md:165`.

## 3. 임베딩을 못(안) 붙이는 이유 4가지

1. **벡터는 영속 없이는 인덱스가 안 된다.** ANN(HNSW) 검색은 사전 계산된 벡터가
   DB에 있어야 성립한다. 본문을 저장하지 않으면 문서 벡터도 저장할 수 없고,
   문서 벡터가 없으면 벡터 검색이라 부를 것이 남지 않는다.
2. **본문 미저장은 의도된 제약이다.** 외부 시스템이 SoT이고, `get_document` 는
   "항상 fetch 시점의 최신 원문"을 계약으로 갖는다. 본문 사본을 DB에 남기면
   신선도 계약이 깨지고, 원본 시스템의 접근 권한과 별개인 **사본 보관** 문제가 생긴다.
3. **비용 상환이 불가능하다.** 등록형은 색인 1회 임베딩 비용을 이후 모든 검색이
   나눠 갚는다. Drive/Notion 은 검색할 때마다 본문을 새로 받으므로, 임베딩하면
   **매 검색마다** 후보 전량(fetch 예산: `top_k*3`, 상한 20건 —
   `_body_fetch_budget`, `MAX_BODY_FETCH_CANDIDATES`)을 CPU 로컬 모델로 재임베딩해야
   하고 그 결과는 저장도 못 해 재사용되지 않는다. 순수 낭비다.
4. **2단계 압축 구조와 상충한다.** 이 경로의 핵심 비용은 임베딩이 아니라 외부 API
   fetch 지연/rate limit 이다. 그래서 1단계에서 SQL 토큰 매칭으로 후보를 좁히고
   fetch 를 예산 안에서만 병렬(`MAX_CONCURRENT_BODY_FETCHES=5`) 수행한다.
   벡터를 쓰려면 후보 압축 **이전에** 전량 임베딩이 필요한데, 그러면 압축 구조가
   무의미해진다.

## 4. 의미 검색 부재를 무엇으로 벌충하는가

- **`query_variants`**: 호출자(Claude)가 동의어/유사 표현을 함께 넘겨 1단계 SQL
  후보 게이트만 넓힌다. 서버가 자체 LLM을 호출해 질의를 확장하지 않는다는 원칙과
  일관된다. 점수 계산에는 원본 질의 토큰만 쓴다(`_select_candidates` 주석).
- **collapse 매칭**: 공백 변형('트러블슈팅' vs '트러블 슈팅')을 흡수하되 토큰 1개
  겹침 수준의 보수적 가중치만 준다(`_collapse_match_score`).
- **한글 인식 토크나이저**: `documents_tokenize` 가 `[0-9A-Za-z_]+|[가-힣]+` 로
  한글 덩어리를 함께 인식한다(협업 문서 제목에 한글이 흔하므로).

## 5. 판단

현 설계는 정합적이다. 임베딩을 이 경로에 넣고 싶다면 선결 조건은 하나뿐이다 —
**본문(또는 최소한 청크 텍스트)의 영속화를 허용할 것인가**. 그 결정이 뒤집히지
않는 한 임베딩 도입은 비용만 늘고 이득이 없다. 뒤집는다면 신선도 계약(`get_document`
최신 원문 보장)과 외부 문서 사본 보관 정책을 먼저 다시 써야 한다.
