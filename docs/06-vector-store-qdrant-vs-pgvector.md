# 벡터 스토어 검토 — pgvector 유지 vs Qdrant 전환

- 상태: 결정 완료 — **pgvector 유지 확정(사용자 승인).** 로컬 임베딩 전환에 커밋 `be774dd`로 반영됨.
- 일시: 2026-08-08 (초판) / 2026-08-08 갱신 — 7절 "전제 완화 재검토" 추가(사용자가 "하이브리드 로직을 Qdrant 에 맞춰 바꿔도 된다" 전제를 열어 재판단). **초판 결론(pgvector 유지)은 유지되나 근거가 바뀜** — 상세는 7절.
- 작성: architect
- 지시: lead(로컬 CPU 임베딩 전환과 함께 벡터 DB 를 Qdrant 로 옮길지 판단)
- 관련: `docs/05-embedding-provider-local-model-design.md`(로컬 임베딩 전환 설계), `docs/07-search-rrf-reevaluation.md`(RRF 융합), `docs/supabase-migration-review.md`(원격/추가 서비스 정당성 판단 선례)
- 대상(전환 시): `app/services/search/vector_search.py`, `app/repositories/chunk_repository.py`, `app/models/openapi.py`, `app/services/indexer/indexer_service.py`, `app/services/sync/*`, `app/composition.py`, `docker-compose.yml`, `alembic/versions/`, `pyproject.toml`

## 요약(결정 사항)
1. **결론: pgvector 유지. Qdrant 전환은 현 시점 비권장.** 로컬 CPU 임베딩(384dim) 전환은 진행하되 벡터 스토어는 pgvector HNSW 그대로 둔다.
2. **핵심 근거 — "한 행 분리(row split)"**: `api_chunk` 는 **하나의 행**에 `text`·`ref_id`·`embedding`(벡터)·`text_tsv`(FTS)를 모두 담는다. 키워드 FTS 와 벡터 검색이 **같은 테이블·같은 행·같은 스코프 필터**(`chunk_type='endpoint'` + document/project JOIN)를 공유한다. Qdrant 로 벡터만 떼면 이 단일 행이 두 스토어로 쪼개져 **이중쓰기·동기화 책임·크로스스토어 병합**이 새로 생긴다.
3. **스케일 부적합**: Qdrant 의 실이득(분산 샤딩, 양자화 RAM 절감, 고급 HNSW 튜닝)은 **로컬·단일 사용자·수천~수만 벡터** 규모에서 무의미하다. pgvector HNSW 가 이 규모를 이미 충분히 감당한다(정상 동작은 수백만 벡터까지).
4. **RRF 설계에 역행**: `07-search-rrf-reevaluation.md` 의 융합 설계는 "벡터 `search_by_vector` 에 `ref_id` 프로젝션만 추가하면 조인 불필요"(동일 테이블이라 가능)라는 단순화에 기대어 있다. Qdrant 분리는 이 단순화를 파괴하고 `ref_id`·scope 메타데이터를 Qdrant payload 로 **중복**시켜 융합을 크로스스토어로 만든다 — RRF 를 더 복잡하게만 한다.
5. **선례 일치**: `supabase-migration-review.md` 는 "로컬 단일 사용자 MCP 에 원격/추가 서비스를 얹는 정당성"을 물어 비권장했다. Qdrant 를 로컬 docker 로 띄우면 **원격 왕복 문제는 없지만**, "별도 서비스 하나 추가 + 이중 데이터스토어 동기화"라는 운영 복잡도 축은 오히려 Supabase 검토 때보다 **악화**된다(그때는 전부 한 Postgres 안이었다).
6. **05-embedding-provider-local-model-design.md 개정 불필요**: pgvector 유지가 결론이므로 그 문서 3절(컬럼 dim 256→384 마이그레이션)·5절(composition 배선)은 **그대로 유효**하다. 개정 방향 없음(8절에서 확인).

---

## 1. Qdrant 채택 시 실이득 — 현 스케일에서 의미가 있는가
결론: **거의 없다.** Qdrant 가 pgvector 대비 갖는 강점은 모두 이 프로젝트의 규모·배포 형태와 어긋난다.

| Qdrant 강점 | 현 스케일에서의 실효성 |
|---|---|
| **HNSW 파라미터 튜닝**(`m`, `ef_construct`, `ef_search`) | pgvector **이미 HNSW 사용**(`ix_api_chunk_embedding_hnsw`, `vector_cosine_ops`). pgvector 도 `m`/`ef_construction` 인덱스 옵션, `hnsw.ef_search` 런타임 GUC 를 제공한다. 튜닝 여지는 동등하며, 현재 기본값으로도 병목 없음. |
| **payload 필터링**(메타데이터 조건 검색) | 현재 scope 필터(`document_id`/`project`/`chunk_type`)는 **SQL WHERE + ApiDocument JOIN** 으로 관계형으로 처리된다. Qdrant 로 옮기면 이 조건들을 payload 로 **복제**해야 한다 — 관계형 조인을 payload 필터로 다운그레이드하는 셈. 이득이 아니라 손실. |
| **양자화(scalar/product quantization)로 RAM 절감** | 384dim × 수천~수만 벡터 = 수십 MB. 양자화가 풀어야 할 RAM 압력 자체가 없다. |
| **분산 샤딩·복제·수평 확장** | 단일 사용자 로컬 서버. 샤딩 대상 아님. |
| **named vectors / 멀티벡터** | 청크당 임베딩 1개. 불필요. |
| **전용 벡터 API·gRPC 성능** | pgvector 는 앱과 **같은 로컬 docker**라 왕복 sub-ms. Qdrant 도 로컬이면 동급이지 우위 아님. |

**규모 추정**: 벡터 개수 = 색인된 API 문서의 endpoint/schema/section 청크 총합 — 단일 사용자 로컬 MCP 에서 현실적으로 수천~수만 건. pgvector HNSW 의 실용 한계(수백만~천만)와 두세 자릿수 차이가 난다. **스케일 압력이 존재하지 않으므로 Qdrant 의 스케일 강점은 사표(死票)다.**

## 2. pgvector 대비 운영 복잡도 — 이중 데이터스토어의 비용
전환의 진짜 비용은 성능이 아니라 **"메타데이터는 Postgres, 벡터는 Qdrant"로 갈라진 데이터의 정합성 책임**이다.

### 2-1. 이중쓰기(dual-write)와 부분 실패
현재 색인/삭제는 **단일 트랜잭션**이다:
- 색인: `ChunkRepository.bulk_add(chunks)` — 한 번의 커밋으로 text·ref_id·embedding·text_tsv 가 함께 들어간다.
- 문서 교체: `delete_by_document(document_id)` — 한 번의 SQL `DELETE`.

벡터를 Qdrant 로 떼면 **모든 쓰기 경로가 두 갈래**가 된다:
- 색인 = Postgres INSERT(메타+text_tsv) **+** Qdrant upsert(벡터+payload).
- 삭제/교체 = Postgres DELETE **+** Qdrant delete-by-filter.
- **크로스스토어 트랜잭션이 없다**: Postgres 커밋은 됐는데 Qdrant upsert 가 실패하면 → 검색은 되는데 벡터 없는 청크(벡터 검색에서 유실). 반대면 → 삭제된 문서의 유령 벡터(orphan). 정합성 복구용 **재조정(reconciliation) 배치**를 새로 만들어 운영해야 한다.

이건 로컬 docker 로 왕복 지연을 없애도 **사라지지 않는** 종류의 비용이다. 원격이냐 로컬이냐와 무관하게, **데이터가 두 시스템에 나뉘어 있다는 사실 자체**에서 나온다.

### 2-2. 운영 표면 증가
- **컨테이너 추가**: `docker-compose.yml` 에 Qdrant 서비스·볼륨·healthcheck 추가. 개발자는 이제 두 개의 stateful 서비스를 띄우고 관리한다.
- **백업 이원화**: 현재 `pg_dump` 하나로 메타+벡터가 전부 백업된다. 분리 후 Postgres 덤프 **와** Qdrant 스냅샷을 **시점 정합**하게 떠야 한다(두 백업의 시점이 어긋나면 복원 후 드리프트).
- **기동 순서/헬스 의존성**: 앱이 두 백엔드의 가용성에 의존. Qdrant 만 죽으면 벡터 fallback 이 조용히 비는 게 아니라 upsert 실패로 색인이 깨질 수 있어 에러 처리 경로가 늘어난다.

### 2-3. pgvector 가 "공짜로" 주는 것
`ApiChunk.embedding.is_not(None)` 같은 조건, `candidate_ids` 집합 필터(`ApiChunk.id.in_(...)`), scope JOIN 이 **전부 하나의 SQL 문**에서 조합된다. 벡터·메타·FTS 가 한 테이블에 있으니 트랜잭션·백업·스코프 필터가 **자동으로 정합**하다. 이 정합성은 Qdrant 전환 시 **직접 코드로 재구현·유지**해야 하는 대상이 된다.

## 3. 하이브리드 검색(키워드 FTS + 벡터) 병합 — 두 스토어 분리 시 변화
`07-search-rrf-reevaluation.md` 의 RRF 설계는 **벡터가 FTS 와 같은 테이블에 있다는 전제**에 최적화되어 있다. 분리하면 그 전제가 무너진다.

### 3-1. RRF 재검토 문서가 기대는 단순화(동일 테이블 전제)
그 문서 3절 핵심 항목:
- **"벡터 쪽 ref_id 확보(P2 잔여 흡수)"**: `search_by_vector` 의 SQL 프로젝션에 `ref_id` 를 **추가만** 하면 된다 — "api_chunk 컬럼이라 **조인 불필요**". 융합 단위(endpoint=ref_id)를 벡터 arm 도 바로 낼 수 있다.
- **스코프 정합**: 키워드·벡터가 같은 `document_id`/`project` JOIN 을 공유하므로 두 arm 의 후보 모집단이 자동으로 같다.

### 3-2. Qdrant 분리 시 융합 로직이 어떻게 나빠지는가
- **`ref_id` 중복**: Qdrant 는 청크 벡터만 안다. 융합을 endpoint 단위로 하려면 `ref_id` 를 **Qdrant payload 에 복제**하거나, Qdrant 가 준 `chunk_id` 를 들고 **Postgres 로 되돌아가 chunk_id→ref_id 해석**(추가 왕복). 어느 쪽이든 "조인 불필요" 단순화가 사라진다.
- **scope 필터 이원화**: 벡터 arm 의 후보를 `document_id`/`project` 로 좁히려면 그 값들을 **Qdrant payload 로 복제**해 payload 필터로 걸러야 한다. 키워드 arm(SQL JOIN)과 벡터 arm(Qdrant payload)이 **서로 다른 메커니즘**으로 같은 스코프를 구현하게 되어, 둘이 어긋날 여지가 생긴다.
- **크로스스토어 병합**: RRF 자체는 등수만 쓰므로 스토어가 달라도 수학적으로는 가능하다. 하지만 "SQL 한 방으로 두 ranker"가 될 수 있었던 미래(동일 테이블)가, "Postgres FTS 쿼리 + Qdrant 검색 요청 + Python 병합 + ref_id 재해석"으로 **확정적으로 분산**된다.

**요지**: Qdrant 전환은 RRF 를 **불가능하게 만들지는 않지만, 재검토 문서가 세운 "최소 변경으로 융합" 경로를 무효화**하고 융합을 더 복잡한 크로스스토어 문제로 바꾼다. RRF 착수 관점에서도 pgvector 유지가 유리하다.

## 4. 마이그레이션 난이도 — 전환한다고 가정하면
참고용으로 전환 작업량을 적어둔다(권장은 아님). 총평 **중상(中上)** — pgvector 내부 dim 변경보다 훨씬 무겁다.

- **스키마 폐기**: `api_chunk.embedding`(Vector 컬럼) + `ix_api_chunk_embedding_hnsw` HNSW 인덱스를 **드롭**하는 alembic 리비전. `pgvector.sqlalchemy.Vector` import·`EMBEDDING_DIM` 상수·모델 필드 제거. (로컬 임베딩 설계의 "256→384 컬럼 재생성"은 이 경우 **불필요** — 컬럼 자체가 사라지므로. 대신 Qdrant 컬렉션을 384dim·cosine 으로 생성.)
- **추상화 신설**: `VectorStore` 인터페이스(upsert/delete/search) + `QdrantVectorStore` 구현. `qdrant-client` 의존성 추가.
- **쓰기 경로 개조**: `IndexerService.index_document` 색인 후 Qdrant upsert 추가, `SyncService.resync`/문서 삭제 경로에 Qdrant delete 미러링, 부분 실패 처리·재조정 배치(2-1).
- **읽기 경로 개조**: `VectorSearch.search` 를 Qdrant 호출로, `EndpointCandidateSearch._search_by_vector` 의 `candidate_ids`/scope 를 Qdrant payload 필터로 재작성. `ChunkVectorHit`/`search_by_vector`(pgvector SQL) 제거.
- **docker-compose**: Qdrant 서비스·볼륨·healthcheck 추가.
- **백필**: 로컬 임베딩 설계의 `reembed.py` 를 "컬럼 갱신"이 아니라 "Qdrant 컬렉션 적재"로 작성. 청크 순회는 동일.
- **테스트**: 벡터 경로 테스트 전반이 Qdrant(임베디드/도커) 의존으로 바뀜. CI 에서 Qdrant 기동 or 페이크 스토어 필요.

대조: **pgvector 유지 시** 로컬 임베딩 전환은 이미 설계된 "컬럼 dim 256→384 재생성 alembic 1개 + reembed 배치"로 끝난다(05-embedding-provider-local-model-design.md 3·4절). 추상화 신설·이중쓰기·컨테이너 추가가 **전부 불필요**하다.

## 5. 최종 권장
**pgvector 유지.** Qdrant 전환은 현 시점 비권장.

근거 정리(저울):
- **이득 측(Qdrant)**: 현 스케일(로컬·단일 사용자·수천~수만 벡터)에서 Qdrant 의 차별 기능(샤딩·양자화·고급 HNSW·payload 필터)은 **전부 무의미하거나 이미 pgvector 로 충족**된다(1절). 순 이득 ≈ 0.
- **비용 측(Qdrant)**: 단일 행(api_chunk)이 두 스토어로 갈라지며 **이중쓰기·부분 실패·재조정·백업 이원화·컨테이너 추가**가 새로 생긴다(2절). 로컬 docker 로 띄워 원격 왕복은 없애도 이 복잡도는 남는다.
- **RRF 역행**: 벡터를 떼면 RRF 재검토 문서의 "최소 변경 융합" 경로가 무효화되고 융합이 크로스스토어로 복잡해진다(3절).
- **선례 일치**: 로컬 단일 사용자 MCP 에 서비스를 더하는 정당성은 supabase-review 에서 이미 부정됐고, Qdrant 는 "이중 데이터스토어"라 그때보다 정합성 부담이 크다(2절).

**전환이 정당화되는 조건(그때 재검토)**:
- 벡터 규모가 **수백만~천만**으로 커져 pgvector HNSW 빌드/메모리(`maintenance_work_mem`)가 실제 병목이 될 때.
- 다중 사용자/공유 배포로 가서 벡터 검색 QPS 가 관계형 DB 를 압박하고, 벡터 워크로드를 **전용 서비스로 격리**하는 이득이 정합성 비용을 넘어설 때.
- 그 시점엔 **키워드 FTS 도 함께 벡터 스토어로 갈지**(단일 스토어 유지) 재설계해 "한 행 분리"를 피하는 편이 나을 수 있다.

**한 줄 요약**: Qdrant 의 강점은 이 프로젝트가 갖지 않은 규모에서만 켜지고, 비용(단일 행을 두 스토어로 쪼개는 정합성 부담)은 규모와 무관하게 즉시 발생한다. 로컬 CPU 임베딩 전환은 pgvector 컬럼 dim 변경만으로 끝나므로, **벡터 스토어는 pgvector 로 유지하고 로컬 임베딩 전환을 그대로 진행**하는 것이 옳다.

## 6. 05-embedding-provider-local-model-design.md 개정 방향
**개정 불필요.** pgvector 유지가 결론이므로 그 문서의 다음 절은 **그대로 유효**하다:
- **3절(차원 256→384 마이그레이션)**: pgvector 컬럼 재생성(DROP INDEX → DROP/ADD COLUMN vector(384) → HNSW 재생성) 그대로 진행. 변경 없음.
- **5절(composition 배선)**: `_build_embedding_provider` 의 gemini 제거·로컬 provider 기본화·`is_vector_fallback_available` 을 `is_semantic` 으로 재정의 — 벡터 스토어 선택과 **직교**하며 그대로 유효.

즉 이번 Qdrant 검토 결과로 로컬 임베딩 설계에 되돌릴 내용은 없다. developer 는 05-embedding-provider-local-model-design.md 의 착수 순서를 **수정 없이** 따르면 된다.

---

# 7. 전제 완화 재검토 — "하이브리드 로직을 Qdrant 에 맞춰 재구성해도 된다"

- 추가 일시: 2026-08-08
- 계기: 사용자 재고 요청 — "그러면 Qdrant 를 도입하고 그에 맞춰서 로직을 변경하면 안돼?"
- 참조 지시: `.claude-logs/task-qdrant-reconsider.md`

## 7-0. 무엇이 달라졌나 — 되짚기
초판(1~6절)의 결정적 반대 근거였던 **"이중쓰기·크로스스토어 병합·two-mechanism scope"(2·3절)** 는 **"키워드 FTS 는 Postgres 에 남기고 벡터만 Qdrant 로 뗀다"**는 전제 위에서 성립한 것이다. 사용자는 이 전제를 열었다 — **검색 로직 전체를 Qdrant 중심으로 재구성**해도 된다는 뜻. 그렇다면 "두 스토어에 걸친 병합"이라는 문제 자체가 재구성으로 사라질 수 있으므로, 그 축은 **정직하게 재검토해야 한다.** 아래는 전제를 연 상태의 재판단이다.

## 7-1. Qdrant 중심으로 재구성하면 어떤 아키텍처가 가능한가
두 가지 현실적 형태가 있다.

### 아키텍처 A — Qdrant 단일 검색 소스 + payload 풀텍스트 필터
- 청크 `text`·`ref_id`·`document_id`·`project`·`chunk_type` 를 **Qdrant payload 로 복제**하고, `text` 에 full-text payload index 를 건다. Postgres 는 시스템-오브-레코드(엔드포인트/문서 메타·원문 저장, `get_endpoint_details` 용)만 담당.
- 검색 = Qdrant 한 곳: 벡터 검색 + payload 필터(scope) + `MatchText`(키워드).
- **한계(핵심)**: Qdrant 의 `MatchText` 는 **랭커가 아니라 불리언 필터**다("이 토큰을 포함하는가" 예/아니오). 현재 키워드 검색의 **`ts_rank` 상대순위**(어휘 겹침 많을수록 상위)를 재현하지 못한다. 즉 A 는 키워드를 "필터"로 격하시켜, RRF 융합에 넣을 **키워드 등수**를 못 만든다. → RRF 재검토 문서의 융합에는 부적합.

### 아키텍처 B — Qdrant 네이티브 하이브리드(dense + sparse/BM25, 서버사이드 RRF)
- Qdrant Query API(prefetch + fusion)는 **dense 벡터 + sparse 벡터를 서버에서 RRF/DBSF 로 융합**하는 것을 네이티브 지원한다. 키워드 신호를 **sparse 벡터(BM25/SPLADE)** 로 표현하면, "키워드 등수 + 벡터 등수"의 RRF 를 **Qdrant 한 요청**으로 처리 가능하다.
- 이것이 사용자가 기대하는 "로직을 Qdrant 에 맞춰 재구성" 의 이상형이다. **크로스스토어 병합·two-mechanism scope 문제는 여기서 실제로 사라진다** — 융합이 서버사이드 단일 요청이고, scope 도 Qdrant payload 필터 하나로 통일된다.
- **대가**: 키워드 신호를 sparse 벡터로 만들려면 **인덱싱·질의 양쪽에서 BM25/SPLADE sparse 임베딩 생성**이 필요하다(FastEmbed 의 BM25 sparse 모델 등, corpus 통계 포함). 즉 지금 Postgres FTS 가 하는 일을 **sparse-vector 파이프라인으로 새로 구축**해야 한다.

**요지**: 전제를 열면 **B 는 기술적으로 성립**하고, 초판이 지적한 "크로스스토어 병합/이중 scope 메커니즘"은 B 에서 **정말로 해소된다.** 이 점은 초판보다 Qdrant 에 유리하게 재평가되어야 한다.

## 7-2. 이 재구성이 RRF 융합에 실제로 이득인가
반반이다 — **융합 계층은 단순해지고, 키워드 계층은 복잡해진다.**
- **단순해지는 쪽**: 융합이 Qdrant 서버사이드 RRF 로 내려간다. `07-search-rrf-reevaluation.md` 3절이 앱에서 짜려던 "두 ranker 병렬 실행→ref_id dedupe→RRF 합산" 코어가 상당 부분 Qdrant Query API 로 대체된다.
- **복잡해지는 쪽(결정적)**: 현재 키워드 recall 은 **이미 구축·배포된 자산**이다 — 최근 커밋 `aa4de84 (perf: 엔드포인트 키워드 검색을 Postgres FTS(tsvector+GIN)로 이관)`, 그리고 P1 이 넣은 **한글·혼합 스크립트 토큰화**(`GET요청`→`get`,`요청`; 경로 분해 `/orders/{orderId}`→`orders`,`orderid`)가 `TEXT_TSV_EXPRESSION` 정규식으로 정밀 튜닝되어 있다. B 로 가면 이 로직을 **sparse-vector 토크나이저 위에서 재현**해야 하는데, Qdrant/FastEmbed 의 BM25 토크나이저·`multilingual` full-text 토크나이저(charabia 계열)의 **한글 처리 품질이 이 커스텀 FTS 와 동등하다는 보장이 없다.** → **이미 튜닝된 키워드 recall 을 미검증 경로로 재구축하는 회귀 리스크**가 생긴다. (이 한글 토큰화 동등성은 researcher 가 실측 검증할 항목 — 단, 아래 결론은 이 값에 의존하지 않는다.)
- **결정적 대비점**: **pgvector 도 "단일 스토어 네이티브 하이브리드"를 이미 제공한다.** RRF 재검토 문서의 전제가 바로 그것 — 키워드 FTS 와 벡터가 **한 테이블·한 SQL** 안에 있어 RRF 를 한 쿼리로 표현 가능. 즉 사용자가 Qdrant 에서 얻으려는 "네이티브 하이브리드"는 **pgvector 에서 마이그레이션 없이 이미 가능**하며, 그쪽 키워드 계층은 **이미 완성·튜닝**되어 있다.

## 7-3. 재구성 후에도 남는 리스크 (초판 대비 얼마나 줄었나)
| 리스크 축 | 초판(벡터만 분리) | 재구성 B(Qdrant 중심) | 판정 |
|---|---|---|---|
| 크로스스토어 병합 | 큼(앱에서 두 스토어 결과 병합) | **해소**(서버사이드 RRF) | ✅ 개선 |
| two-mechanism scope | 큼(SQL JOIN vs payload) | **해소**(payload 단일) | ✅ 개선 |
| 이중쓰기/정합성 | 큼 | **남음**(완화). SoR=Postgres, 검색인덱스=Qdrant 는 그대로 → outbox/재시도 큐로 "은밀한 발산"을 "지속가능한 최종일관성"으로 낮출 수 있으나 **재조정 책임은 존속** | 🔸 완화(불소멸) |
| 키워드 recall 품질 | 영향 없음(FTS 유지) | **신규 리스크**(튜닝된 한글 FTS→sparse BM25 재구축, 동등성 미검증) | 🔺 악화 |
| 새 이동부품 | 없음 | **sparse 임베딩 파이프라인**(BM25/SPLADE, corpus 통계) 추가 | 🔺 증가 |
| 운영 표면(컨테이너·백업) | 큼 | **동일하게 큼**(Qdrant 컨테이너·스냅샷·기동의존 그대로) | ➖ 불변 |
| 로컬 개발 워크플로 | 두 stateful 서비스 | 두 stateful 서비스 | ➖ 불변 |
| 스케일 정당성 | 없음 | **여전히 없음**(로컬·단일사용자·수천~수만 벡터) | ➖ 불변 |

핵심: 재구성은 **"검색 병합"쪽 리스크(2개)를 실제로 없애지만**, 대신 **"키워드 품질·sparse 파이프라인"쪽 리스크(2개)를 새로 만들고**, **운영·정합성·스케일 축은 그대로** 남긴다. 순감이 아니라 **리스크의 이동**에 가깝다.

## 7-4. 최종 재권장 — 여전히 pgvector 유지 (근거는 교체)
**전제를 열어도 결론은 바뀌지 않는다: pgvector 유지. 단 근거가 초판과 다르다.**

- 초판 근거("이중스토어 병합이 결정적")는 **전제 완화로 약해졌다** — 이 점을 정직하게 인정한다. B 아키텍처는 그 문제를 실제로 푼다.
- 그러나 새 저울이 여전히 pgvector 로 기운다:
  1. **네이티브 하이브리드는 Qdrant 만의 이점이 아니다.** pgvector 는 FTS+벡터를 한 테이블·한 SQL 로 이미 제공하고, RRF 도 한 쿼리로 표현 가능하다(RRF 재검토 문서의 전제 그 자체). 사용자가 Qdrant 에서 원하는 것을 **pgvector 는 마이그레이션 0 으로 이미 준다.**
  2. **키워드 계층의 자산 격차.** Postgres 쪽 키워드(한글·혼합스크립트·경로분해 FTS)는 **이미 완성·배포·튜닝**됐다(`aa4de84`, P1). Qdrant B 는 이걸 **sparse BM25 + 미검증 한글 토크나이저**로 새로 지어야 한다 — 작동하는 자산을 버리고 회귀 리스크를 사는 교환.
  3. **동기·스케일의 부재는 불변.** 재구성을 해도 Qdrant 를 **지금 도입할 적극적 이유**(스케일 압력, 관계형으로 안 되는 검색 요구)는 여전히 없다. B 는 "더 깨끗한 아키텍처"라는 미학적 동기이지 **관측된 병목의 해소가 아니다.**
  4. **비용의 즉시성 대 이득의 잠재성.** 컨테이너·outbox·sparse 파이프라인·한글 토큰화 재구축·재조정 배치는 **지금 확정 발생**하고, 이득(서버사이드 융합의 깔끔함)은 **pgvector 로도 얻을 수 있는 것**이라 순증분이 작다.

**한 줄 요약(재검토)**: 전제를 열면 Qdrant 네이티브 하이브리드(B)는 초판이 지적한 병합 문제를 실제로 해소한다 — 이건 인정. 그러나 **그 "네이티브 하이브리드"는 pgvector 가 마이그레이션 없이 이미 제공하고, 그쪽 키워드 계층은 이미 튜닝 완료**다. 따라서 재구성은 *작동하는 Postgres FTS 자산을 버리고, 스케일 이득도 없는 새 스토어로 lateral move* 하는 것 — **여전히 비권장.**

> **최종 확정(사용자 승인)**: 초판 검토 → 전제 완화 재검토(7절)를 모두 거친 뒤, 사용자가 **pgvector 유지**를 최종 승인했다. 벡터 스토어 변경 없음으로 확정되며, 로컬 임베딩 전환(커밋 `be774dd`)이 pgvector 위에서 그대로 진행·반영되었다.

## 7-5. 그래도 Qdrant 로 간다면 (조건부 실행 개요)
lead/사용자가 아키텍처 미학 또는 향후 스케일 대비로 **B 채택을 결정**할 경우에 한한 개요(권장 아님, 결정 시 참조용):
- **하이브리드**: 아키텍처 B(dense e5-small + sparse BM25, Qdrant Query API 서버사이드 RRF). A(풀텍스트 필터)는 키워드 랭킹 부재로 배제.
- **SoR 경계**: Postgres = 엔드포인트/문서 메타·원문(`get_endpoint_details`). Qdrant = 검색 인덱스(dense+sparse 벡터 + payload: `ref_id`/`document_id`/`project`/`chunk_type`/`text`).
- **쓰기 일관성**: **outbox 패턴** — Postgres 트랜잭션에 outbox 행 기록 → 워커가 Qdrant upsert/delete 반영, 실패 시 재시도. 부팅 시·주기적 **재조정 배치**(Postgres 청크 ↔ Qdrant 포인트 드리프트 감지)로 보강.
- **05-embedding-provider-local-model-design.md 개정 방향(이 경우에만)**:
  - **3절**: pgvector 컬럼 dim 256→384 재생성 → **`api_chunk.embedding` 컬럼·HNSW 인덱스 폐기(DROP)** 로 교체. dim 은 Qdrant 컬렉션(384, cosine) 생성으로 이관. `EMBEDDING_DIM` 은 Qdrant 컬렉션 파라미터로 이동.
  - **5절**: `VectorSearch`/`search_by_vector`(pgvector SQL) 제거, `QdrantVectorStore`(upsert/delete/hybrid-query) 신설, `is_vector_fallback_available` 은 Qdrant 가용성으로 재정의. sparse 임베딩 provider(BM25) 배선 추가.
  - **선행 검증(researcher)**: Qdrant `multilingual`/BM25 토크나이저의 **한글·혼합스크립트·경로세그먼트** recall 이 현행 `TEXT_TSV_EXPRESSION` 대비 동등한지 실측 — 회귀 없음이 확인돼야 착수.
- **선결(거버넌스)**: 이는 최근 커밋 `aa4de84`(키워드 FTS 이관) 결정을 사실상 되돌리는 것 → 결정 번복 승인 필요.
