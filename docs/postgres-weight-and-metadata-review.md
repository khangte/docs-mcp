# PostgreSQL 무게 및 메타데이터 필요성 검토

- 상태: 결정 완료 — **배포 목표=개인/내부용(docker 전제 OK) 확정 → Postgres/pgvector 유지, 메타데이터 스키마 변경 없음.**
- 일시: 2026-08-08
- 작성: architect
- 지시: lead(사용자 질문 — "postgresql이 용도에 비해 무겁지 않은가", "메타데이터를 굳이 저장할 필요가 있나")
- 관련: `docs/vector-store-qdrant-vs-pgvector.md`, `docs/supabase-migration-review.md`, `docs/embedding-provider-local-model-design.md`(구현 완료 커밋 `be774dd`)
- 대상: `app/core/db.py`, `app/models/openapi.py`, `docker-compose.yml`, `alembic/versions/*`, `.env.example`, `app/services/ingestor/sync_service.py`

## 요약(결정 사항)
1. **Q1(무게) 결론 — 지금은 Postgres 유지, 단 "과provision"은 정직하게 인정.** 로컬 단일 사용자 MCP 규모에 풀 RDBMS + docker 컨테이너는 **런타임 부담은 아니나(sub-ms·수십 MB) 배포/운영 형태로는 다소 과하다.** 그럼에도 지금 옮기지 않는 이유는 "Postgres 가 맞아서"가 아니라 **(a) 방금 배포된 pgvector 구현(`be774dd`) 위로 떨어지는 마이그레이션 비용, (b) 관측된 병목 부재, (c) sqlite-vec 의 상대적 미성숙** 때문이다. 이건 Qdrant 검토와 **다른 종류의 결론** — Qdrant 는 "스토어를 추가"라 명백히 나빴지만, SQLite 는 "더 가벼운 것으로 교체"라 방향 자체는 타당하다.
2. **전환의 유일한 결정적 트리거 = 배포 모델.** MCP 서버는 클라이언트(Claude Desktop 등)가 **서브프로세스로 띄우는** 형태가 일반적이다. "docker 없이 pip 설치 후 즉시 실행"을 **배포 목표로 삼는다면**, docker+Postgres 의존은 실질 도입 장벽이고 **SQLite 가 곧바로 권장으로 뒤집힌다.** 반대로 "개인/내부용, docker 는 이미 워크플로에 있음"이면 현행 유지가 옳다. → **이 질문의 답을 lead/사용자에게 확인 요청**(6절).
3. **SQLite 대안은 기능적으로 충분히 성립.** `SQLite + sqlite-vec(벡터) + FTS5(키워드)` 는 이 규모(수천~수만 청크·단일 사용자)를 커버한다. 핵심: **한글 FTS 자산은 이식 가능**하다 — 튜닝의 본질이 DB 고유 토크나이저가 아니라 **정규식 전처리**(`TEXT_TSV_EXPRESSION`)라, 동일 로직을 Python `re` 로 재현해 FTS5 에 넣을 수 있다(3절). 벡터도 이 규모면 sqlite-vec 의 brute-force KNN 로 충분(HNSW 불필요). **단일 스토어 하이브리드**(RRF 전제)도 SQLite 안에서 그대로 유지된다.
3. **Q2(메타데이터) 결론 — 대부분 필요, 삭제 후보는 소수.** 저장 필드 대부분이 실제 쿼리/기능에 쓰인다(4절). 명확한 "저장하나 안 읽는" 필드는 없고, **재검토(trim) 후보는 (a) `document_sync_history`(감사 로그 — 검색/상세엔 불필요, 순수 이력), (b) `operation_id`(출력 경로에서 읽는 곳 미확인 — 검증 필요)** 둘뿐. `text_tsv` 는 "저장 불필요"가 아니라 **FTS 인덱스의 저장 substrate 자체**라 제거 대상이 아니다(4-2).

---

## 1. Postgres 가 이 용도에 무거운가 — 정직한 무게 측정
"무겁다"를 두 축으로 분리해야 정확하다.

### 1-1. 런타임 무게 — 부담 아님
- **왕복**: 앱과 DB 가 같은 로컬 docker → sub-ms(Supabase 검토에서 이미 확인). 원격 왕복 문제 없음.
- **메모리/디스크**: `pgvector/pgvector:pg16` 컨테이너 idle 수십~백 MB 수준, 데이터 수천~수만 청크면 디스크도 소소. 단일 사용자 로컬 머신에서 체감 부담 아님.
- **관측된 병목 없음**: 세 번째 검토에 걸쳐 확인된 사실 — 현재 성능 병목은 DB 엔진이 아니다.
→ **런타임 관점에선 "무겁다"가 성립하지 않는다.**

### 1-2. 배포/운영 무게 — 여기서는 다소 과함(정직한 인정)
- **별도 서버 프로세스 + 컨테이너 생명주기**: 앱을 쓰려면 `docker compose up -d postgres` 가 선행되어야 한다(현재 `CLAUDE.md`·`.env.example` 에 명시). MCP 서버가 클라이언트에 의해 서브프로세스로 기동되는 전형적 형태에서, **"먼저 docker 로 DB 를 띄워두라"는 전제는 배포 마찰**이다.
- **기동 의존성**: 앱이 외부 stateful 프로세스의 가용성에 의존. 임베디드 DB 라면 앱 프로세스가 곧 DB.
→ **로컬 단일 사용자 MCP 라는 형태에 한정하면, 풀 RDBMS 는 기능 대비 운영 표면이 다소 크다.** 이 점은 방어하지 않고 인정한다.

## 2. 대안 비교 — SQLite(+sqlite-vec+FTS5), DuckDB
| 항목 | Postgres(현행) | SQLite + sqlite-vec + FTS5 | DuckDB |
|---|---|---|---|
| 서버 프로세스 | 필요(docker) | **없음**(앱 내 파일 1개) | 없음(임베디드) |
| 벡터 | pgvector HNSW(ANN) | sqlite-vec — **brute-force KNN**(ANN 미제공, but 이 규모면 충분) | VSS 확장 있으나 성숙도 낮음, 벡터는 약함 |
| 키워드 FTS | tsvector+GIN, 한글 정규식 튜닝 완료 | **FTS5**(성숙), bm25 랭킹 | FTS 확장 존재하나 제한적 |
| 트랜잭션/JOIN | 완전 | 완전(단일 writer) | 분석형 OLAP 지향 |
| 동시성 | 다중 writer | 단일 writer 락(**단일 사용자라 무의미**) | 단일 프로세스 |
| 성숙도/유지보수 | 최상 | FTS5 최상 / **sqlite-vec 는 v0.1.x, 단일 메인테이너** | 코어 성숙, 벡터/FTS 확장은 미성숙 |

- **DuckDB 는 탈락**: OLAP 분석 지향이고 벡터/FTS 확장 성숙도가 이 용도에 못 미친다. 실현성 낮음.
- **현실적 후보는 SQLite 조합** 하나. 아래는 그 성립성 검증.

## 3. SQLite 조합이 현재 기능을 커버하는가 — 세부 검증
결론: **커버한다. 특히 한글 FTS 이식이 가능하다는 점이 핵심 발견.**

- **한글 FTS(가장 큰 걱정) — 이식 가능**: 현재 튜닝(`GET요청`→`get`,`요청`; 경로 분해; 비-영숫자-한글 strip)은 **DB 고유 토크나이저 마법이 아니라 `regexp_replace` 전처리**다(`TEXT_TSV_EXPRESSION`). Postgres 에선 STORED generated 컬럼식으로, SQLite 에선 **동일 정규식을 Python `re` 로 write 시점에 적용해 shadow 텍스트를 만들고 그걸 FTS5 에 인덱싱**하면 된다. 즉 **투자한 자산이 Postgres 에 락인돼 있지 않다.** (질의 측 토크나이저 `[0-9A-Za-z_]+|[가-힣]+` 도 그대로 재사용.) — 단, 랭킹이 `ts_rank`→FTS5 `bm25()` 로 바뀌어 **순위가 달라지므로 골든 기대값 재검증**이 필요(회귀 관리 대상).
- **벡터 — brute-force 로 충분**: sqlite-vec 는 ANN(HNSW) 미제공이나, 384dim × 수천~수만 = 수십 MB 선형 스캔 = 한 자릿~수십 ms. **이 규모에선 HNSW 빌드/튜닝이 오히려 불필요한 복잡도**였다. 코사인 지원.
- **단일 스토어 하이브리드 유지**: FTS5 와 sqlite-vec 가 **같은 파일·같은 커넥션**에 있어, RRF 융합(FTS5 MATCH 등수 + vec KNN 등수)을 한 곳에서 수행 가능. `search-rrf-reevaluation.md` 가 기댄 **"두 랭커가 한 스토어"** 전제가 pgvector 와 동일하게 보존된다(Qdrant 분리와 대조적으로 여기선 안 깨진다).
- **SQLAlchemy 이식성**: 세션/ORM 대부분은 dialect 중립이나, **PG 고유 요소는 전면 교체 대상** — `pgvector.Vector`·HNSW·`cosine_distance`·`TSVECTOR`·`Computed(generated)`·`schema="app"`·psycopg 드라이버·alembic 리비전 3개(`dfbe6143212a`/`ff8aa8f36266`/`a17165213545`)가 전부 PG-ism. → **스토리지 계층 광범위 재작성**(적은 일 아님).

## 4. 메타데이터 필요성 — 필드별 실사용 추적
`app/models/openapi.py` 전 필드를 grep 으로 실사용 확인한 결과.

### 4-1. 실제로 쓰이는(=삭제 시 기능 깨짐) 필드 — 대부분
- `ApiChunk.ref_id` — RRF/후보의 융합 단위(endpoint id). **필수.**
- `ApiChunk.document_id`·`chunk_type` — scope 필터·endpoint/schema 구분(`has_endpoint_chunks`, `list_endpoint_chunks`). **필수.**
- `ApiChunk.text` — FTS substrate·재임베딩(`reembed`)·표시. **필수.**
- `ApiChunk.embedding` — 벡터 검색. **필수.**
- `ApiDocument.project` — 프로젝트 scope JOIN(`ix_api_document_project`). **필수.**
- `ApiDocument.raw_text` — `endpoints.py:197`(원문 반환 도구)·`sync_service`(재파싱 비교)에서 **읽음**. 업로드 문서는 재fetch 소스가 없어 **유일 원본**이라 대체 불가. 크지만 필수.
- `ApiDocument.content_hash` — 변경 감지로 불필요 재색인 skip(`sync_service` 다수). **필수.**
- `ApiEndpoint.summary`/`method`/`path`/`description`/`tags_json` — 후보/상세 출력·필터. **필수.**
- `ApiParameter`/`ApiResponse`/`ApiRequestBody`/`ApiSchema` 의 `schema_json`/`example_json` 등 — `get_endpoint_details` 상세 구성. **필수.**
- `ApiSection.order_index` — 섹션 정렬(`endpoint_repository.py:73`). **필수.**

### 4-2. `text_tsv` — "저장 불필요"가 아니라 인덱스 substrate
질문의 "조회 시점 계산으로 대체 가능한가"에 대한 답: **아니다.** `text_tsv` 는 STORED generated 컬럼으로 **GIN 인덱스의 대상 자체**다. 조회 시점 계산(비저장)으로 바꾸면 매 질의가 풀스캔+실시간 tsvector 화가 되어 인덱스 이점을 잃는다. SQLite 로 가도 FTS5 shadow 테이블이라는 형태로 **저장은 여전히 필요**하다. → 제거 대상 아님.

### 4-3. Trim(재검토) 후보 — 소수
- **`document_sync_history`(테이블 전체)**: 동기화 시도 감사 로그. 검색·상세·RRF 어디에도 안 쓰이는 **순수 이력**. `sync_history_repository`+`sync_service`에서 기록/조회되나, 기능 관점 필수는 아님 → **감사 이력이 요구사항이 아니라면 삭제 가능**(단 삭제해도 무게 이득은 미미 — 판단은 "이 이력이 제품 요구인가"). 
- **`ApiEndpoint.operation_id`**: 파서→indexer 로 **쓰기**는 확인되나 **출력/검색 경로에서 읽는 곳이 grep 상 미확인**. → **"저장하나 안 읽음" 유력 후보. developer 가 payload 직렬화 경로까지 확인 후, 미사용이면 제거 가능**(단정 전 검증 필요).
- 그 외 필드는 모두 실사용 확인됨 → **"메타데이터 대부분 필요"가 결론.**

### 4-4. Trim 후보 2건 — 성능 관점 재확인
4-3 의 "무게이득 미미"는 스토리지·복잡도 기준이었다. 쿼리 성능·테이블 비대화 관점에서도 동일한지 재확인한 결과, **결론 동일(둘 다 유지, 지금 정리할 이유 없음).**
- **`document_sync_history`**: append-only 이고 TTL/prune 로직이 없어 **구조상 무한 누적**이나, 증가 단위가 "검색당"이 아니라 **"문서별 sync 시도당"**(sync_service 의 3개 `.add()` 지점)이라 로컬 단일 사용자 규모에선 수십~수백 행에 그쳐 무의미하다. FK `ON DELETE CASCADE` 로 문서 삭제 시 이력도 함께 제거되고, **검색 hot path 가 이 테이블을 전혀 건드리지 않는다** → 비대화·vacuum 부담 실질 없음. 단 정직한 latent 지적: `list_by_document` 는 `document_id` 필터 + `created_at DESC` 정렬인데 **이 테이블엔 PK(`id`) 외 인덱스가 없어** seq scan+sort 다. 지금 규모(`limit 10`, 소량)엔 무관하고, **한 문서에 이력이 수천 건 쌓이는 비현실적 시나리오에서만** 문제가 된다. 그때의 옳은 해법도 "삭제"가 아니라 "`(document_id, created_at)` 인덱스 추가"이며, 이 규모에선 불필요. → **지금 지울 성능적 이유 없음.**
- **`operation_id`**: `api_endpoint` 컬럼에 **인덱스가 없어**(엔드포인트 인덱스는 `uq_endpoint_doc` 유니크 제약뿐) 유지 비용이 제로다 — 드롭해도 인덱스 이득이 없다. `String(256)` nullable 이고 엔드포인트 수 자체가 수백~저수천이라 저장량도 미미. → **성능상 정리 유인 없음.**
- **종합**: 두 필드 모두 스토리지·복잡도뿐 아니라 **쿼리 성능·테이블 비대화 관점에서도** 정리 이득이 미미해, 4-3 판정을 그대로 유지한다.

## 5. 트레이드오프 종합
- **전환으로 얻음**: docker-compose·`docker compose up -d postgres` 단계 **소멸**, 서버 프로세스 제거(앱=파일 1개), 기동 의존성 제거, 콜드스타트 단축, HNSW 튜닝 부담 제거. 배포가 "설치 후 즉시 실행"에 가까워짐.
- **전환으로 잃음/치름**: 스토리지 계층 광범위 재작성(PG-ism 전면 교체, alembic 3리비전 폐기·SQLite 스키마 신설), **방금 배포된 `be774dd` 구현 재손질**, sqlite-vec 미성숙 리스크, `ts_rank`→bm25 순위 재검증(골든 갱신), 향후 다중 사용자 전환 시 되돌리기(그땐 Postgres 로 회귀).
- **단일 사용자라 SQLite 동시성 락은 비이슈**(확인). 이건 전환의 걸림돌이 아니다.

## 6. 최종 권장 및 결정 hinge
**지금은 Postgres 유지. 단 "SQLite 가 부적합해서"가 아니라 "지금 옮길 트리거가 아직 없어서".**

- Qdrant 검토와 **결론의 성격이 다름**: Qdrant 는 방향이 틀렸고(스토어 추가), SQLite 는 **방향은 맞고 타이밍/비용이 걸림돌**이다. "이미 pgvector 방어했으니 또 방어"가 아니라, 이번 질문(무게·이식성)엔 **SQLite 손을 상당 부분 들어준 판단**임을 명확히 한다 — 한글 FTS 이식 가능·단일스토어 하이브리드 유지·이 규모엔 brute-force 벡터로 충분.
- **미루는 근거(정직하게)**: (a) 관측된 병목 없음, (b) `be774dd` 방금 배포 — 즉시 재작성은 churn, (c) sqlite-vec v0.1.x 리스크, (d) 순위 재검증 비용.
- **뒤집는 단일 트리거 = 배포 모델**: **이 MCP 를 "docker 없이 누구나 설치·실행"으로 배포하는 것이 목표라면, SQLite 로 전환이 곧 권장**으로 바뀐다(그 목표에선 docker+Postgres 가 진짜 장벽). 개인/내부용이라 docker 가 이미 전제면 현행 유지.

### lead/사용자에게 확인 요청(결정 hinge)
> **이 MCP 서버의 배포 목표가 "외부 사용자가 docker 없이 손쉽게 설치·실행"인가, 아니면 "개인/내부용(docker 워크플로 전제 OK)"인가?**
> - 전자 → SQLite(+sqlite-vec+FTS5) 전환 설계 착수 권장(별도 spec).
> - 후자 → Postgres 유지, 이 문서는 "왜 유지하는지"의 근거로 보관.

**사용자 확인 결과: 개인/내부용, docker 전제 OK → Postgres 유지가 최종 결론.** (배포 hinge 가 "후자"로 확정되어, SQLite 전환은 착수하지 않는다. 향후 "docker 없는 외부 배포"가 목표가 되면 이 문서의 3·5절을 근거로 재론.)

### 메타데이터
- **대부분 필요** — 4절 근거대로 삭제 시 기능이 깨진다.
- **정리 가능(선택)**: `document_sync_history`(감사 이력이 제품 요구가 아니라면), `operation_id`(출력 경로 미사용 검증 후). 둘 다 **무게 이득은 미미**하므로, 스토리지 무게 때문이 아니라 "안 쓰는 걸 지운다"는 위생 차원에서만 판단하면 된다.
- **`text_tsv` 는 인덱스 substrate 라 비저장화 불가**(4-2).

**한 줄 요약**: 런타임상 Postgres 는 무겁지 않지만, 로컬 단일 사용자 MCP 의 배포 형태엔 다소 과하다. SQLite 조합은 기능적으로 성립하고 한글 FTS 자산도 이식 가능해 **greenfield 라면 더 맞는 선택**이나, 방금 배포된 구현·미관측 병목·sqlite-vec 미성숙 탓에 **지금 당장 옮길 이유는 부족**하다. **전환 여부의 실제 결정자는 "docker 없는 배포가 목표인가"** — 이 답에 따라 권장이 갈린다. 메타데이터는 대부분 필요하며, 삭제 후보는 감사 로그와 미사용 의심 필드 소수에 그친다.
