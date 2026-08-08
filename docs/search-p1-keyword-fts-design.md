# P1 설계 확정 — 키워드 검색 Postgres FTS 이관

- 상태: **확정**(한글 방침 A안 lead 승인 — 2026-08-08). 구현 대기.
- 일시: 2026-08-08
- 작성: architect
- 관련: `docs/search-performance-improvements.md` P1/P2, ADR-0002
- 대상: `app/services/search/keyword_search.py`, `app/repositories/chunk_repository.py`, `app/models/openapi.py`, `alembic/versions/`, `app/services/search/endpoint_candidate_search.py`

## 확인된 사실(설계 전제)
1. **한글은 현재 키워드 매칭에서 완전히 버려진다.** `app/services/search/tokenize.py` 의 `[A-Za-z0-9_]+` 는 ASCII 영숫자만 잡는다. 엔드포인트 청크 텍스트(`build_endpoint_chunk_text`)의 summary/description 에 한글이 있어도 키워드로는 못 잡고, 한글 질의는 토큰 0개 → 키워드 0건 → **벡터 fallback 으로 넘어간다.** 즉 lead 지적대로 "형태소 차이"가 아니라 **"현재 무시 → FTS 후 새로 매칭 시작"** 동작 변경 이슈가 맞다.
2. **`tokenize.py` 는 키워드 검색 전용이 아니다.** `HashEmbeddingProvider`(`app/services/indexer/embedding_provider.py`)도 이 함수를 쓴다. **전역으로 고치면 해시 임베딩 벡터가 바뀌어 재색인이 필요**해진다 → P1 은 `tokenize.py` 를 건드리지 않는다.
3. **`KeywordSearch` 소비자는 `EndpointCandidateSearch` 하나뿐**(composition.py 에서 1회 조립). 계약 변경 파급이 좁다.
4. **테스트는 이미 Postgres 에서 돈다.** `tests/conftest.py` 가 `postgresql+psycopg` 로 테스트별 DB 를 만든다. `keyword_search.py` 상단 "SQLite 에서도 동작하도록" 주석은 **실제 인프라와 어긋난 stale 주석** — FTS 전환이 테스트 인프라를 깨지 않는다(그 주석은 이번에 삭제/정정 대상).
5. 청크 텍스트에서 method/path/param/tag/response 는 ASCII, summary/description 만 한글 가능. `ref_id` 는 `api_chunk` 자체 컬럼이라 별도 조인 없이 얻을 수 있다.

---

## 1. Text search config: **`simple` 채택**
- **근거**: 현재 점수식은 어간 처리 없는 **정확 토큰 겹침**이다. `english` config 는 어간 추출(users→user, 불용어 제거)로 API 식별자·경로 토큰을 왜곡하고 현재 순위 의미를 바꾼다. 기술 문서/식별자 검색은 어간 없는 정확 매칭이 안전하다.
- `simple` 은 소문자화 + 유니코드 단어 경계 분할만 한다 → 현재 ASCII 동작을 가장 근접하게 보존하면서, 부수적으로 한글 단어 토큰까지 인덱싱한다.
- config 는 컬럼 생성식에 **리터럴로 고정**(`to_tsvector('simple', text)`)한다. `regconfig` 를 런타임 파라미터로 두지 않는다(생성 컬럼은 IMMUTABLE 식만 허용).

## 2. 한글 처리 방침 — ✅ **A안 확정(한글 매칭 포함, lead 승인 2026-08-08)**
FTS 전환은 필연적으로 한글 동작을 바꾼다. `to_tsvector('simple', …)` 의 기본 파서는 한글 연속 구간을 **공백/구두점 단위 "단어" 토큰**으로 인덱싱한다(형태소 분해 아님). 예: `'주문 목록'` → `주문`, `목록` 2토큰 / `'주문목록'` → `주문목록` 1토큰.

- **선택지 A — 한글도 매칭 대상에 포함(✅ 채택).**
  - 근거: (a) 문서 검색 경로(`documents_tokenize`)는 **이미 한글을 토큰화**한다. 엔드포인트만 한글을 버리는 현재 상태가 오히려 비일관. (b) 한글 summary/description 을 가진 API 를 한글 질의로 찾는 것은 자연스러운 기대. (c) 기능이 순증(늘어남)이지 기존 ASCII 매칭을 잃지 않음.
  - **관측 가능한 변화**: 한글 질의가 지금은 항상 벡터 fallback 을 타는데, FTS 후엔 키워드에서 잡히면 **벡터 fallback 을 안 타게 된다**(검색 전략 분기 변경). 결과 품질이 달라질 수 있어 테스트로 명시 고정 필요(5번).
  - **알려진 한계(P1 범위 밖, 문서화만)**: 공백 변형(`주문목록` vs `주문 목록`)은 FTS 가 교차 매칭 못 함. 문서 검색 쪽은 `collapse()` 로 흡수하지만, 엔드포인트에 같은 장치를 붙이는 것은 **후속 과제로 분리**(P1 을 비대하게 만들지 않음).
- **선택지 B — 한글 계속 무시(현행 유지). ❌ 미채택.**
  - (기록용) `to_tsvector` 대신 질의·청크에서 ASCII 토큰만 뽑아 FTS 에 넣도록 강제. FTS 이점(인덱스 가속)은 얻되 동작 변화 0. 단 문서 검색과의 비일관은 유지, 한글 API 검색은 계속 벡터에만 의존.
- **결정: A 채택(lead 승인 2026-08-08).** 비일관 해소 + 순기능이며, "한글 질의가 벡터 fallback 을 덜 타게 되는" 전략 변화는 4번 term 토큰화(`simple` 규칙, 한글 포함)와 5번 4항 한글 동작 고정 테스트로 계약화한다. 공백 변형 한계는 후속 과제로 트래킹.

## 3. 생성 컬럼 · GIN 인덱스 · 마이그레이션
- **컬럼**: `api_chunk.text_tsv TSVECTOR`, **STORED generated column**.
  - ⚠️ **구분자 정규화(2026-08-08 보완, developer 실측 반영)**: `to_tsvector('simple', text)` 를 raw text 에 직접 걸면 기본 파서가 `/`·`{`·`}`·`.` 등을 단어 경계로 보지 않아 path 가 컴파운드 lexeme 로 남는다(`'/orders/{orderId}'` → `'/orders'`,`'orderid'` / `'/pet/findByStatus'` → 단일 `'/pet/findbystatus'`). 옛 정규식 `[A-Za-z0-9_]+` 은 이들을 구분자로 봐 세그먼트를 bare 토큰으로 쪼갰다. 정규화 없이 가면 **정확 경로 질의의 recall 이 빠져 5.1 recall 동치 게이트를 위반**한다(ADR-0002: 벡터는 정확 경로에 약함 → 경로 recall 은 키워드가 지켜야 할 강점). 따라서 to_tsvector 입력을 먼저 정규화한다:
    - **구두점 정규화**: `regexp_replace(text, '[^0-9A-Za-z_가-힣]', ' ', 'g')` — [영숫자·언더스코어·한글]을 제외한 모든 문자를 공백으로 치환.
    - **스크립트 경계 정규화(2026-08-08 2차 보완, reviewer 실측 반영)**: 위 구두점 치환만으로는 **ASCII↔한글이 공백 없이 붙은 복합어**(`GET요청`, `API키`)를 못 쪼갠다. `to_tsvector('simple', ...)` 의 기본 파서는 스크립트 전환 지점을 단어 경계로 보지 않아 `GET요청` → 단일 lexeme `'get요청'` 으로 뭉친다. 반면 질의측 토크나이저(`[0-9A-Za-z_]+|[가-힣]+`)는 `['get','요청']` 2토큰으로 쪼개므로, `GET`/`요청`/`GET요청` **어느 질의로도 매칭 실패** — 게다가 옛 스코어러는 `GET요청` 에서 `get` 을 뽑아 질의 `GET` 과 매칭됐으므로 이는 **순수 ASCII recall 회귀**(2번 A(c) '기존 ASCII 매칭을 잃지 않음' 위반). 따라서 스크립트 경계에 공백을 먼저 삽입한다(양방향, ASCII→한글 및 한글→ASCII):
      ```
      regexp_replace(
        regexp_replace(text, '([0-9A-Za-z_])([가-힣])', '\1 \2', 'g'),
        '([가-힣])([0-9A-Za-z_])', '\1 \2', 'g')
      ```
      두 패턴을 순차 적용하면 `a가b` 같은 연속 전환도 `a 가 b` 로 분해된다(첫 패턴이 `a가`, 둘째 패턴이 `가b` 를 각각 처리). 최종 식은 **경계 삽입 → 구두점 치환** 순서로 중첩한다(모두 IMMUTABLE 유지).
    - 효과: (a) ASCII·한글 토큰 경계가 질의측 정규식(`[0-9A-Za-z_]+|[가-힣]+`)과 **정확히 일치** → 5.1 게이트가 구성상 보장(혼합 복합어 포함). (b) 한글은 keep-set 에 있어 A안(한글 매칭) 유지. (c) `.`(v1.2)·`-`(find-by-status)·`:`(Tags:) 구두점 컴파운드 + `GET요청` 스크립트 컴파운드까지 일괄 해소. (d) 문서 검색 토크나이저(`documents_tokenize`)와 경계 규칙이 **정확히 일치** → 두 경로 일관성 확보.
  - 모델(`app/models/openapi.py`): `from sqlalchemy.dialects.postgresql import TSVECTOR`, `from sqlalchemy import Computed`.
    ```
    # 가독성을 위해 상수로 뽑아 재사용(모델·마이그레이션 동일 식). 순서:
    # 스크립트 경계 공백삽입(ASCII↔한글) → 구두점 공백치환 → to_tsvector.
    _TSV_EXPR = (
        "to_tsvector('simple', "
        "regexp_replace("
        "  regexp_replace("
        "    regexp_replace(text, '([0-9A-Za-z_])([가-힣])', '\\1 \\2', 'g'),"
        "    '([가-힣])([0-9A-Za-z_])', '\\1 \\2', 'g'),"
        "  '[^0-9A-Za-z_가-힣]', ' ', 'g'))"
    )
    text_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(_TSV_EXPR, persisted=True),
        nullable=True,
    )
    ```
    - 주의: Python 문자열에서 `\1`·`\2` 역참조가 리터럴로 DB 에 가야 하므로 `\\1`·`\\2` 로 이스케이프한다(또는 raw 문자열). 모델과 마이그레이션이 **바이트 동일한 식**을 써야 alembic autogenerate 가 불필요한 diff 를 만들지 않는다.
  - `Computed(persisted=True)` → Postgres `GENERATED ALWAYS AS (...) STORED`. **인덱서 코드 변경 불필요**: insert/update 시 DB 가 자동 채운다(트리거 불요). 기존 행도 컬럼 추가 시 자동 backfill.
  - ORM 이 write 하지 않도록 `Computed` 가 보장. 일반 select 성능을 위해 이 컬럼은 **명시적으로 select 하지 않는다**(필터 전용).
- **인덱스**: `__table_args__` 에 추가
  ```
  Index("ix_api_chunk_text_tsv", "text_tsv", postgresql_using="gin")
  ```
- **alembic 리비전**(신규 1개, 기존 컨벤션 `schema='app'`·`postgresql_using` 사용 — `dfbe6143212a` HNSW 인덱스 정의 방식과 동일 패턴):
  - `op.add_column('api_chunk', sa.Column('text_tsv', postgresql.TSVECTOR(), sa.Computed(_TSV_EXPR, persisted=True), nullable=True), schema='app')` — `_TSV_EXPR` 는 모델과 **동일한** 중첩 식(스크립트 경계 삽입 → 구두점 치환 → to_tsvector). 두 곳이 어긋나면 재색인/diff 사고가 나므로 한 곳에서 정의해 공유하거나 문자 그대로 복제한다.
  - `op.create_index('ix_api_chunk_text_tsv', 'api_chunk', ['text_tsv'], schema='app', postgresql_using='gin')`
  - downgrade: drop index → drop column.
  - 적용 후 `uv run alembic upgrade head`(CLAUDE.md 2.3).

## 4. KeywordSearch → 저장소 to_tsquery 인터페이스
- **반환 계약(`KeywordHit`) 유지**: 반환 타입 `list[KeywordHit]` 는 그대로 둔다. 다만 `EndpointCandidateSearch` 가 in-memory 청크 없이 `ref_id` 를 얻어야 하므로 **`KeywordHit` 에 `ref_id: str` 필드를 additive 로 추가**(chunk_id·score 는 불변). 이게 두 번째 라운드트립(별도 ref 조회)을 피하는 가장 단순한 길이다. → "반환 dataclass 유지 + 필드 1개 추가", 기존 필드 계약은 깨지 않음.
- **입력 계약은 변경**(이게 P1·P2 의 목적): 현재 `search(query, top_k, candidates=None, chunks=None)` 의 in-memory `chunks`/`candidates` 를 **제거**하고 스코프를 SQL 로 내린다:
  ```
  KeywordSearch.search(query, top_k, *, document_id=None, project=None) -> list[KeywordHit]
  ```
- **저장소 신규 메서드**(`ChunkRepository`):
  ```
  search_endpoint_by_text(terms: Sequence[str], top_k: int,
                          document_id: str|None, project: str|None) -> list[ChunkTextHit]
  # ChunkTextHit(chunk_id, ref_id, score)
  ```
  SQL 골자:
  ```
  tsq = func.to_tsquery('simple', ' | '.join(<정규화된 terms>))   # OR 매칭
  SELECT id, ref_id, ts_rank(text_tsv, tsq) AS score
  FROM api_chunk
  WHERE chunk_type='endpoint'
    [AND document_id=:doc]                    # 조건부
    [AND EXISTS/JOIN ApiDocument.project=:p]  # 기존 list_endpoint_chunks 와 동일 필터
    AND text_tsv @@ tsq
  ORDER BY score DESC
  LIMIT :top_k
  ```
  - **OR(`|`) 매칭 이유**: 현재 점수식은 "질의 토큰 중 하나라도 겹치면 후보, 많이 겹칠수록 상위"다. `plainto_tsquery`(AND)는 너무 엄격해 recall 을 떨어뜨린다. term 을 우리가 토큰화해 `|` 로 결합하고, **각 term 은 lexeme 로 escape/quote** 해서 사용자 입력이 tsquery 문법으로 해석되지 않게 한다(`websearch_to_tsquery` 는 기본 AND 라 부적합).
  - **term 소스(대칭 정규화 필수)**: 질의 term 추출도 **인덱스와 동일한 경계 규칙** — `[0-9A-Za-z_가-힣]+` 로 토큰화 — 을 써야 한다. 인덱스는 구분자를 공백화(3번)하는데 질의만 옛 ASCII 정규식이나 raw `to_tsquery` 를 쓰면, `/orders/{orderId}` 같은 경로 질의가 양쪽에서 다르게 쪼개져 매칭이 어긋난다. 즉 질의 토크나이저 = `documents_tokenize` 와 같은 `[0-9A-Za-z_]+|[가-힣]+` 계열 규칙(한글 포함). 이렇게 추출한 term 들을 lexeme escape 후 `|` 로 결합해 `to_tsquery('simple', …)` 에 넣는다.
  - `ts_rank` 는 매칭 lexeme 수가 많을수록 큰 값 → 현재 "겹침 많을수록 상위" 의도와 방향 일치(값 자체는 다름 → 5번에서 순위로만 검증).
- **`EndpointCandidateSearch` 흐름 조정**:
  - `_search_by_keyword` 가 in-memory `chunks` 대신 `KeywordSearch.search(query, top_k, document_id, project)` 를 호출, 결과의 `ref_id` 로 바로 `_to_candidates`.
  - **벡터 fallback 의 candidate 스코프**: 현재는 `_endpoint_chunks()` 로 미리 적재한 청크 id 집합을 쓴다. P1 후엔 키워드가 SQL 로 가므로, **fallback 진입 시에만** 후보 스코프를 만들면 된다. 두 가지 중 택1:
    - (권장) `search_by_vector` 에 `chunk_type/document_id/project` 필터를 SQL 로 직접 추가 → in-memory 청크 적재를 아예 없앰(P2 완성까지 한 번에).
    - (최소) fallback 진입 시에만 `list_endpoint_chunks` 로 id 집합 조회(현행 재사용). 단 키워드 히트가 흔하면 대부분 이 경로를 안 타므로 손해가 작다.
  - **`_endpoint_chunks` 빈 스코프 early-return**(문서에 endpoint 청크 0건이면 `[]`) 은 가벼운 `exists`/`count` 질의로 대체하거나, keyword 0건 + vector 0건이면 자연히 `[]` 이므로 생략 가능 — 동작 동일성만 테스트로 확인.

## 5. 순위 회귀 검증 방법
ts_rank 점수값은 현재 겹침비율과 **다를 수밖에 없다**. 따라서 "점수 동일"이 아니라 **불변식 + 특성(characterization) 비교**로 검증한다.

1. **ASCII recall 동치 테스트(회귀 게이트)**: 한글 없는 질의 집합에 대해, **레거시 파이썬 스코어러 결과의 후보 집합(set)** 과 FTS 결과 후보 집합이 **동일**함을 assert. 순서가 아니라 집합 동치로 "ASCII 매칭을 잃지 않았다"를 못박는다. (레거시 스코어러를 전환기 동안 테스트 참조용으로 임시 보존 후 제거.)
   - ⚠️ **픽스처 보강(2026-08-08, reviewer 지적)**: 이 게이트가 petstore(한글 없음) fixture 만 쓰면 `GET요청`·`API키` 같은 **ASCII↔한글 혼합 복합어**의 순수 ASCII recall 회귀를 못 잡는다. 혼합 복합어를 summary/description 에 담은 청크를 fixture 에 추가하고, 질의 `GET`(ASCII 단독)이 그 청크를 매칭함을 assert 하는 케이스를 게이트에 포함한다. 이 케이스가 3번 스크립트 경계 정규화의 회귀 방지선이다.
2. **불변식 테스트**(점수값 비의존):
   - 더 많은 distinct 질의 토큰을 포함한 청크가 더 적게 포함한 청크보다 순위가 낮지 않다(단조성).
   - 질의 토큰 0개 → `[]`(현행 유지).
   - `top_k` 상한 준수, 결과 결정성(동점 시 tie-break 규칙 명시 — 예: score desc, id asc).
3. **순위 상관 진단(비-실패 리포트)**: 동일 시드 DB 에서 레거시 vs FTS 순위의 Spearman 상관을 계산해 로그로만 남기는 파라메트릭 테스트. 급락하는 질의를 사람이 검토(경고용, CI fail 아님).
4. **한글 동작 고정 테스트(A안 확정 — 필수)**:
   - 한글 summary 를 가진 endpoint 를 한글 질의로 검색하면 **키워드에서 잡히고 `match_type='keyword'`** 임을 assert(= 벡터 fallback 을 안 탐). 동작 변경을 의도된 계약으로 못박는다.
   - 공백 변형 한계(`주문목록` vs `주문 목록`)를 **현재는 매칭 안 됨**으로 명시하는 테스트(후속 과제 트래킹용).
5. **인프라**: 위 테스트는 tsvector/GIN 이 필요하므로 **Postgres 픽스처**(이미 `conftest.py` 가 Postgres) 위에서만 돈다. SQLite 폴백 가정 제거를 코드/주석에서 함께 정리.

---

## lead 결정 이력
- **한글 처리 방침(2번)**: **A안(한글 매칭 포함) 채택 — lead 승인 2026-08-08.** term 토큰화는 `simple` 규칙(한글 포함)으로 진행하며, 5번 4항 한글 동작 고정 테스트로 계약을 못박는다. 공백 변형(`주문목록` vs `주문 목록`) 교차 매칭은 P1 범위 밖 후속 과제.

## 착수 순서(확정 — 구현 대기)
1. 마이그레이션(3) + 모델 컬럼/인덱스 → `alembic upgrade head`.
2. 저장소 `search_endpoint_by_text`(4) + 벡터 fallback 스코프 SQL 화(P2 흡수).
3. `KeywordSearch`·`EndpointCandidateSearch` 배선 교체, 레거시 스코어러 임시 보존.
4. 회귀 테스트(5) 통과 확인 후 레거시 스코어러·stale SQLite 주석 제거.
