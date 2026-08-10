# 구현 계획 — P4(pg_trgm)·P5(임베딩 LRU 캐시)·P6(HNSW ef_search)

- 상태: **착수 승인**(lead, 2026-08-10). developer 구현 대상.
- 작성: architect
- 근거: `docs/search-performance-improvements.md` P4/P5/P6
- 원칙: **셋 다 저효과·저리스크 → 최소 변경, 과설계 금지.** 새 env·설정 표면 늘리지 않는다
  (상수는 모듈 상수로, RRF_K 선례 따름). 검색 동작 계약은 바뀌지 않는다(성능/내부 최적화만).
- alembic head(작성 시점): `ff8aa8f36266`. P4 마이그레이션의 `down_revision` 은 이 값.

세 항목은 서로 독립이라 **어떤 순서로 해도 되고 개별 커밋**한다. 아래는 권장 순서(쉬운 것부터).

---

## P5 — 쿼리 임베딩 LRU 캐시 (가장 쉬움)

### 왜/어디에
- `VectorSearch.search`(`app/services/search/vector_search.py:41`)가 매 호출 `embedding_provider.embed_query(query)`.
- **캐시는 요청마다 새로 조립되는 `VectorSearch`가 아니라, 장수(long-lived) 객체인 임베딩
  프로바이더에 둬야 한다.** `embedding_provider`는 `AppState`에 1회 생성되어 요청 간 살아있다
  (`app/composition.py`). `build_services`는 요청마다 번들을 새로 만들므로 거기 캐시를 두면 매번 비워진다.
- 비싼 경로는 `LocalEmbeddingProvider`뿐. `HashEmbeddingProvider`는 순수 해시라 캐시 불필요 — **건드리지 않는다.**

### 변경 (최소)
- 대상 파일: `app/services/indexer/embedding_provider.py` (`LocalEmbeddingProvider`만).
- `LocalEmbeddingProvider.__init__`에서 `functools.lru_cache`로 감싼 내부 인코딩 함수를 만들어
  인스턴스에 보관하고, `embed_query`가 그걸 호출하게 한다. 캐시 키는 **원본 질의 문자열**
  (`query:` 접두사 부착 전). 반환 리스트는 캐시가 같은 객체를 재사용하므로, 호출측이 변형하지
  않는지만 주의(현재 소비자는 읽기만 함 — 안전).
  ```python
  # __init__ 안:
  self._embed_query_cached = functools.lru_cache(maxsize=_QUERY_CACHE_SIZE)(self._embed_query_uncached)
  # 모듈 상수: _QUERY_CACHE_SIZE = 256  (env 미노출, YAGNI)
  ```
  `_embed_query_uncached(text)`가 기존 `encode([_QUERY_PREFIX + text], ...)` 로직을 담고,
  `embed_query`는 `list(self._embed_query_cached(text))` 반환(캐시 공유 리스트를 방어적 복사).
- `embed_documents`는 캐시하지 않는다(색인은 배치·1회성, 반복 없음).

### 완료 기준
- 단위 테스트(신규 또는 기존 `tests/unit/test_embedding_provider*.py`에 추가):
  호출 카운트를 세는 fake 인코더를 주입해, **같은 질의로 `embed_query`를 2번 호출하면
  인코더의 `encode`는 1번만 불린다**(AAA). 다른 질의는 별도 인코딩됨을 확인.
- 반환 벡터가 캐시 전/후 동일값임을 확인(값 회귀 없음).
- `HashEmbeddingProvider` 경로·`embed_documents`는 변화 없음(기존 테스트 그대로 green).

---

## P6 — HNSW `ef_search` 설정 (쉬움)

### 왜/어디에
- 벡터 검색 SQL은 `ChunkRepository.search_by_vector`(`app/repositories/chunk_repository.py:241`).
- `hnsw.ef_search` 세션 GUC가 미설정이라 기본 40. RRF 후보 폭 N은 최대 200(top_k=50→width 200)까지
  요청되는데, `ef_search < 요청 top_k`면 HNSW가 요청 수보다 적게 반환해 recall이 깎인다.
- **현 규모(단일 문서·수백 청크)에선 영향 미미**(HNSW가 seq scan으로 폴백할 만큼 작음). 규모 대비 안전장치.

### 변경 (최소)
- 대상 파일: `app/repositories/chunk_repository.py` (`search_by_vector`만).
- 벡터 쿼리 실행 **직전**, 같은 세션(트랜잭션)에서 `SET LOCAL hnsw.ef_search = :ef` 실행.
  `SET LOCAL`은 현재 트랜잭션 스코프라 세션 전역을 오염시키지 않는다(SQLAlchemy 세션은 트랜잭션 안에서 돎).
  ```python
  # 모듈 상수: HNSW_EF_SEARCH = 100
  ef = max(HNSW_EF_SEARCH, top_k)   # 요청 수보다 작으면 안 됨
  self._session.execute(text("SET LOCAL hnsw.ef_search = :ef"), {"ef": ef})
  ```
  `sqlalchemy.text` import 추가. `top_k <= 0` 조기 반환 뒤(기존 로직)에 둔다.
- env·config 노출하지 않음(YAGNI, RRF_K 선례). 정수 바인딩이라 인젝션 여지 없음.

### 완료 기준
- 기존 벡터 검색 단위/통합 테스트가 그대로 green(동작 계약 불변, 순위 회귀 없음 확인).
  특히 `tests/unit/test_search_rrf_golden.py`·벡터 관련 테스트.
- 로컬 postgres에서 `SET LOCAL hnsw.ef_search` 가 에러 없이 실행되는지 스모크 확인
  (pgvector 확장이 GUC를 인식). 필요 시 `EXPLAIN`으로 인덱스 스캔 유지 확인(선택).
- **주의**: 이 설정이 결과 집합을 바꾸면(테스트가 깨지면) 골든 기대값을 임의로 고치지 말고
  architect(:0.1)에 보고 — ef_search는 recall만 넓혀야지 결정적 순위를 흔들면 안 된다.

---

## P4 — document_meta ILIKE → pg_trgm GIN 인덱스 (마이그레이션 포함)

### 왜/어디에
- 1단계 후보 필터 `DocumentMetaRepository.search_by_tokens`(`app/repositories/document_meta_repository.py:~90`)가
  `title`/`url`에 `ILIKE '%token%'`(선행 와일드카드)를 건다 → 일반 인덱스 미사용, seq scan.
- **pg_trgm GIN 인덱스**(`gin_trgm_ops`)는 `%token%` 형태 ILIKE를 인덱스로 처리할 수 있게 한다.
- `document_meta` 테이블은 **schema `app`**, 컬럼 `title`(String 1024)·`url`(String 2048).

### 변경 1 — alembic 마이그레이션 (신규 파일)
`uv run alembic revision -m "add pg_trgm gin index on document_meta title/url"` 로 생성, `down_revision='ff8aa8f36266'`.

```python
def upgrade() -> None:
    # 확장은 DB 전역(스키마 무관). IF NOT EXISTS로 멱등.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_document_meta_title_trgm", "document_meta", ["title"],
        unique=False, schema="app",
        postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_document_meta_url_trgm", "document_meta", ["url"],
        unique=False, schema="app",
        postgresql_using="gin", postgresql_ops={"url": "gin_trgm_ops"},
    )

def downgrade() -> None:
    op.drop_index("ix_document_meta_title_trgm", table_name="document_meta", schema="app")
    op.drop_index("ix_document_meta_url_trgm", table_name="document_meta", schema="app")
    # 확장은 다른 곳이 쓸 수 있으니 downgrade에서 DROP하지 않는다(보수적).
```

### 변경 2 — 모델 인덱스 선언 동기화
`app/models/document_meta.py`의 `__table_args__`(현재 라인 44~)에 위 두 GIN 인덱스를 선언 추가
해 **alembic autogenerate 스푸리어스 diff를 막는다**(마이그레이션과 모델이 어긋나면 다음 autogenerate가 인덱스 삭제/재생성 diff를 냄). `text_tsv` GIN 인덱스 선언(`app/models/openapi.py`) 형태를 참고:
```python
Index("ix_document_meta_title_trgm", "title", postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}),
Index("ix_document_meta_url_trgm", "url", postgresql_using="gin", postgresql_ops={"url": "gin_trgm_ops"}),
```
(테이블이 schema="app"이므로 `__table_args__`에 이미 스키마가 잡혀 있는지 확인 — 기존 인덱스 선언 방식과 동일하게 맞춘다.)

### 변경 3 — 리포지토리 코드
**변경 없음.** `title.ilike('%token%')`/`url.ilike('%token%')`는 인덱스가 생기면 플래너가 자동으로
GIN trgm 인덱스를 쓴다. 쿼리문 수정 불필요.
- 참고(스코프 밖, 손대지 말 것): `collapse()`(공백 제거) 표현식 조건은 함수 결과라 이 인덱스를 못 탄다.
  이를 인덱싱하려면 표현식 인덱스가 별도로 필요하나 **이번 최소 스코프에서 제외**(부차 조건이고 과설계).
- 참고: 트라이그램 특성상 **3자 미만 토큰**은 선택도가 낮아 인덱스 이득이 작다(정상, 허용).

### 완료 기준
- `uv run alembic upgrade head` 성공(로컬 postgres: `docker compose up -d postgres` 후).
- `uv run alembic downgrade -1` → `upgrade head` 왕복이 깨지지 않음(멱등성 확인).
- 모델↔마이그레이션 정합: `uv run alembic revision --autogenerate` 가 **빈 마이그레이션**을 내는지
  확인(스푸리어스 diff 없음). 확인 후 생성된 임시 리비전 파일은 **삭제**(커밋 금지).
- 인덱스 사용 확인(선택, 권장): `EXPLAIN (질의) SELECT ... WHERE title ILIKE '%주문%'` 가
  seq scan이 아닌 **bitmap index scan**(trgm 인덱스)을 타는지 확인. 데이터가 적으면 플래너가
  seq scan을 고를 수 있음 — 그건 정상(소규모라 인덱스 미사용이 더 빠름). "인덱스가 존재하고
  대용량에서 쓰일 준비가 됨"이 완료 기준이지 "항상 인덱스를 탄다"가 아니다.
- 기존 문서 검색 테스트(`tests/**/*document*search*`)가 그대로 green(결과·순위 불변 — 인덱스는
  같은 행을 더 빨리 찾을 뿐 결과 집합을 바꾸지 않는다).

---

## 커밋 분할 (원자적, 항목별)
- 커밋 A: `perf: 로컬 임베딩 provider에 쿼리 임베딩 LRU 캐시 추가` (P5)
- 커밋 B: `perf: 벡터 검색에 hnsw.ef_search 세션 설정 추가` (P6)
- 커밋 C: `perf: document_meta title/url에 pg_trgm GIN 인덱스 추가` (P4 — 마이그레이션+모델+테스트)

## 설계 이탈 시
- ef_search 설정이 검색 결과 집합/순위를 바꾸거나(P6), pg_trgm이 결과를 바꾸거나(P4), 캐시가
  값 회귀를 내면(P5) — **골든/기대값을 임의 수정하지 말고 architect(:0.1)에 문의.** 이 세 항목은
  전부 "결과 불변, 속도만 개선"이 원칙이라 결과가 바뀌면 그 자체가 설계 이탈 신호다.
- 리포지토리 쿼리 로직을 바꿔야 할 것 같으면(P4에서 collapse 인덱싱 등) 멈추고 문의 — 이번 스코프 밖.
