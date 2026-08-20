# 22. 테이블명 스코프 정합 리네임 검토 (`api_` 접두사)

> 분석·설계 리포트. 코드/마이그레이션 무작성. 문서 21(모델 파일 분리)의
> 후속 성격 — 같은 "이름↔스코프 불일치" 문제를 DB 테이블명 층에서 본다.

배경: 문서 21 에서 `openapi.py` 를 base/document/chunk/openapi 로 분리했다(이미
착지). 같은 불일치가 **테이블명**에도 있다 — `api_document`/`api_section`/
`api_chunk` 는 `api_` 접두사가 붙었지만 실제로는 openapi 전용이 아니라
전 포맷 공용이다.

| 현재 테이블 | 실제 스코프 | 접두사 정당? |
|---|---|---|
| `api_document` | 전 포맷 루트(doc_type 로 openapi/md/csv 구분) | ✗ 오해유발 |
| `api_section` | md/csv 전용 | ✗ (api 아님) |
| `api_chunk` | 검색 코어, 포맷무관 | ✗ 오해유발 |
| `document_sync_history` | 포맷무관 동기화 이력 | ✓ (이미 api_ 없음) |
| `api_endpoint`/`api_parameter`/`api_request_body`/`api_response`/`api_schema` | **진짜 openapi 전용** | ✓ 정당 |

**결론 요약**: 리네임 타당(문서 21 논리의 DB 층 연장). 비용은 **국소적**이다 —
실행 SQL 에 테이블명 하드코딩이 **전무**(전부 ORM)라 팬아웃이 없다. 단
**순수 코스메틱**(행위·성능 무변경)이라 긴급도 낮음. project_source 물결과
**묶지 말고 별도 후속**으로, 그 물결 착지 후 독립 리네임 마이그레이션 권고.

---

## 1. 리네임 대상과 명명 (권고)

| 현재 | → 권고 | 비고 |
|---|---|---|
| `api_document` | `document` | 전 포맷 루트 |
| `api_section` | `document_section` | 문서의 섹션임을 명시(단순 `section` 보다 소속 분명) |
| `api_chunk` | `chunk` | 검색 코어 |
| `document_sync_history` | **유지** | 이미 `api_` 없고 `document_` 한정어가 유의미 — 바꿀 이유 없음 |
| openapi 5종 | **유지** | 접두사 정당(진짜 openapi 전용) |

→ 실제 rename 은 **3개 테이블만**(`api_document`/`api_section`/`api_chunk`).
`document_sync_history` 와 openapi 5종은 손대지 않아 범위가 좁아진다.

> 스코프 한정: 이 검토는 **테이블명**만 다룬다. Python 클래스명(`ApiDocument`/
> `ApiChunk` 등)은 그대로 둔다 — 클래스명≠테이블명은 표준이고, 클래스 리네임은
> 문서 21 과 같은 ~30사이트 import 팬아웃을 다시 유발하므로 별개 사안(§4).

---

## 2. 비용 평가 (근거 확인 완료)

### 2-1. 실행 SQL 의 테이블명 하드코딩 — **없음** (최대 호재)

- 리포지토리·검색(FTS/벡터) 쿼리는 전부 **ORM**(`select(ApiChunk)…join(ApiDocument)`)
  으로, 테이블명은 `__tablename__` 에서 나온다. **raw `FROM/JOIN` 문자열 없음.**
- `chunk_repository` 의 유일한 `text()` 는 `SET LOCAL hnsw.ef_search = {ef}` —
  **테이블명 미포함**.
- `diagnose_long_sections` 는 `inspect(engine).has_table(ApiChunk.__tablename__,
  schema=SCHEMA)` 로 **모델에서 테이블명을 파생** → 리네임 자동 추종.
- 따라서 리네임 팬아웃이 **모델·마이그레이션·인덱스명에 국한**되고, 흩어진
  쿼리 문자열 수정이 **0**이다. (문서 21 의 30사이트 팬아웃과 대조적으로 훨씬 쌈.)

### 2-2. SQLAlchemy `__tablename__` + FK 타깃 문자열

- `__tablename__` 변경: `document.py`(ApiDocument, ApiSection), `chunk.py`(ApiChunk).
- **FK 타깃 문자열** `ForeignKey("api_document.id")` 5곳 → `"document.id"`:
  `chunk.py`(1), `document.py`(ApiSection·DocumentSyncHistory 2), `openapi.py`
  (ApiEndpoint·ApiSchema 2). ※ `ForeignKey("api_endpoint.id")` 3곳은 **불변**.
- `api_section`/`api_chunk` 는 **FK 피참조 없음(리프)** → 타깃 문자열 수정 불요,
  `__tablename__` 만.

### 2-3. 인덱스명 / 제약명

- 테이블명이 박힌 인덱스명: `ix_api_document_project`(document.py),
  `ix_api_chunk_embedding_hnsw`·`ix_api_chunk_text_tsv`(chunk.py) → `ix_document_*`
  /`ix_chunk_*` 로 리네임(정합).
- FK/PK 제약은 모델에 `name=` 미지정 → **Postgres 자동명**(`api_document_pkey`,
  `api_endpoint_document_id_fkey` 등). `MetaData` 에 `naming_convention` **없음**
  (base.py 확인). Postgres `ALTER TABLE ... RENAME` 은 **데이터·FK·인덱스를
  자동 보존**하되 제약 **이름**은 옛 접두사를 유지 → 기능 무해, 이름만 드리프트.
  이름 정합까지 원하면 `ALTER ... RENAME CONSTRAINT/INDEX` 를 추가(선택).
- **HNSW/GIN 인덱스 리네임은 메타데이터 연산**(`ALTER INDEX ... RENAME`) — **벡터
  재색인·재빌드 없음**. 리네임 비용의 핵심 우려(벡터 인덱스 재구축)는 **해당 없음**.

### 2-4. Alembic 마이그레이션

- 신규 리비전: `op.rename_table("api_document","document", schema="app")` ×3.
  Postgres rename_table 은 데이터·FK·의존 인덱스를 자동 승계 → **무손실·저비용**.
- 이름 정합 원하면 인덱스/제약 `RENAME` `op.execute` 추가(선택).
- downgrade 는 역방향 rename — 완전 가역.
- **과거 마이그레이션 파일은 불변**(과거 리비전이 `api_document` 를 만든 기록은
  그대로 두고, 새 리비전이 그 위에서 rename). `create_all` 경로(테스트)는
  `__tablename__` 을 따르므로 리네임 후 새 이름으로 생성 → 마이그레이션 종단
  상태와 일치(드리프트 없음, 단 모델·마이그레이션 동시 반영 필수).

### 2-5. 안전망·잔여 확인

- `tests/unit/test_alembic_env_metadata.py`(`alembic check` 서브프로세스)가
  모델 `__tablename__` ↔ 실제 스키마 정합을 자동 검증 → 리네임 마이그레이션
  누락/불일치를 즉시 포착.
- developer 실행 시 확인: `grep -rn "api_document\|api_section\|api_chunk" tests`
  로 **테스트의 테이블명 문자열 단정**(있다면 raw SQL/스키마 assert) 정리.
  코드 본체엔 실행 SQL 하드코딩이 없으나 테스트/픽스처는 별도 확인.
- 코스메틱: 주석·docstring 의 `api_chunk`/`api_document` 언급(keyword_search,
  diagnose 주석, 모델 docstring)은 정확성 위해 함께 갱신(비파괴).

---

## 3. 리네임을 "할 가치"가 있나 (판정)

- **찬**: 문서 21 과 동일 논리 — 이름이 스코프를 오도(`api_chunk` 가 openapi
  전용처럼 보임). DB 스키마 가독성·신규 합류자 오해 방지. 게다가 §2-1 덕에
  **비용이 예상보다 훨씬 쌈**(쿼리 팬아웃 0, 벡터 재빌드 0).
- **반**: **순수 코스메틱** — 행위·성능·API 계약 무변경. rename 마이그레이션은
  본질적으로 리뷰·롤백이 조심스러운 종류(운영 DB 에 `ALTER TABLE RENAME`).
- **판정**: 타당하나 **저긴급**. 하면 싸지만 안 해도 기능 무손. lead 재량.

---

## 4. 순서 — project_source/모델분리 물결과 **묶지 말 것**

**별도 후속 권고.** 근거:

1. **비의존·비중첩**: rename 대상(document/section/chunk)은 project_source 물결의
   대상 테이블(project_*_source 병합, api_endpoint/api_response 컬럼 drop)과
   **겹치지 않는다** — 묶어야 할 기술적 이유가 없다.
2. **리뷰·롤백 격리**: 기능 변경(project_source)과 코스메틱 rename 을 한
   마이그레이션에 섞으면, rename 롤백이 기능 변경까지 엮어 위험을 키운다.
   독립 리비전이면 rename 만 깔끔히 되돌린다.
3. **선착지 원칙**: project_source 물결이 먼저 착지·안정화된 뒤, 조용한
   네이밍 정리 배치로 단독 진행.
4. **문서 21(모델 분리)과의 관계**: 모델 분리는 이미 착지 → rename 은 그 위에서
   `__tablename__`/FK 문자열만 손대면 됨. 선행 의존 없음.

권고 시퀀스: **project_source 물결 머지 → (안정화) → 테이블 rename 단독 리비전**.
클래스명 리네임(§1 주석)까지 원하면 그때 별도 판단 — 이번 rename 과도 분리.

---

## 5. 요약 판정

- 3개 테이블(`api_document`→`document`, `api_section`→`document_section`,
  `api_chunk`→`chunk`) 리네임 **타당**. `document_sync_history`·openapi 5종 **유지**.
- 비용 **국소·저렴**: 실행 SQL 하드코딩 0, 벡터 재빌드 0. 모델 `__tablename__`
  ×3 + FK 타깃 문자열 5 + 인덱스명 3 + rename 마이그레이션 1리비전.
- **저긴급 코스메틱** → project_source 물결과 분리, **후속 단독 리비전**으로.
- lead 결정 요청: (1)리네임 진행 여부, (2)진행 시 제약/인덱스 이름 정합까지
  갈지(선택적 `RENAME CONSTRAINT`) — 기능엔 무영향, 이름 청결도 문제.
