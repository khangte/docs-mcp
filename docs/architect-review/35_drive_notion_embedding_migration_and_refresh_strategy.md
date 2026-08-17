# Drive/Notion 임베딩 도입 변경범위 · refresh_index upsert vs delete-and-insert

- 일시: 2026-08-14
- 작성: architect
- 선행: `docs/architect-review/34_drive_notion_no_embedding_rationale.md`
- 질문: (1) 본문 영속화 외에 구체적으로 무엇을 어떤 순서로 바꿔야 하나. (2) refresh_index 를
  delete-and-insert 로 바꾸는 게 나은가.

---

## 0. 조사에서 나온 가장 중요한 사실

**임베딩용 인프라를 새로 만들 필요가 없다.** 등록형 파이프라인이 이미 필요한 부품을
전부 갖고 있고, `chunk` 스키마는 **변경 없이** Drive/Notion 본문을 수용한다.

- `chunk.chunk_type` 은 이미 `endpoint | schema | section` 3종이다
  (`chunk_builder.py:23`, `:150`).
- 엔드포인트 검색 SQL 은 **전부** `Chunk.chunk_type == "endpoint"` 로 하드 필터한다
  (`chunk_repository.py:81, 99, 153, 197`). 즉 Drive/Notion 본문을 `section` 청크로
  넣어도 `search_endpoints` 결과를 오염시키지 않는다.
- `Document.raw_text`(본문) + `content_hash`(변경 감지) + `sync_service.resync`
  (해시 게이트 → 청크 전량 삭제 → 재색인) + `reembed.py`(저장된 청크 텍스트만
  재임베딩)가 이미 있다.

따라서 권고 방향은 "`document_meta` 옆에 벡터 저장소를 새로 세우기"가 아니라
**Drive/Notion 문서를 기존 등록형 파이프라인에 태우기**다. 어댑터만 HTTP fetcher 대신
`DocumentSource.fetch()` 로 갈아끼우면 된다.

---

## 1. 변경 범위와 순서

### Phase 0 — 정책 결정 (코드 아님, 선행 필수)

1. **외부 문서 사본 보관 허용 여부.** 지금은 본문을 저장하지 않아 "DB에 남의 문서
   사본이 없다"가 성립한다. 저장하는 순간 원본에서 권한이 회수되거나 문서가
   삭제돼도 우리 DB에 본문과 벡터가 남는다. → **삭제 전파를 필수 요건으로 못박아야
   한다**(원본 목록에서 사라진 문서는 청크·본문까지 즉시 제거). 다행히 현행
   `refresh` 의 `removed` 감지 로직이 그 훅 자리다.
2. **`get_document` 신선도 계약은 바꿀 필요 없다.** 벡터는 랭킹용이고 원문 조회는
   계속 실시간 fetch 하면 된다. 대신 **검색 스니펫의 출처가 캐시 본문으로 바뀐다** —
   "스니펫은 마지막 동기화 시점, 원문은 최신"이라는 불일치가 새로 생기므로
   `DocumentSearchItem` 에 `last_synced_at`(또는 `snippet_as_of`)을 노출해 계약을
   명시할 것을 권고한다. 이게 이번 변경에서 유일하게 **깨지는 겉면 계약**이다.

### Phase 1 — 스키마 (마이그레이션 1개)

3. **`chunk` 테이블: 변경 없음.** (위 §0 근거)
4. **`Document` 재사용, 컬럼 2개만 손댄다.**
   - `doc_type` 에 `drive`/`notion` 값 추가(문자열 컬럼이라 DDL 불필요, 검증 상수만 추가).
   - `Document.id` 를 결정적으로 고정한다. **`f"{project}:{source}:{external_id}"` 원문
     결합은 채택하지 않는다** — `String(64)` 파생 ID 예산(실효 상한 ~40자, 근거는
     본 절 끝의 "Document.id 길이 판정" 참고)을 넘겨 `StringDataRightTruncation` 을
     유발하기 때문이다. 채택안은 `f"{source}:{sha256(project\x00source\x00external_id)[:16]}"`
     (최대 23자)이다.
   - **`source_url` 은 NULL 로 둔다.** 이 컬럼은 `unique` 라, 같은 문서가 두 프로젝트에
     매핑된 경우(현행 `document_meta` 는 2행을 허용) URL 이 충돌해 삽입이 깨진다.
     URL 은 `document_meta.url` 에 이미 있으므로 중복 저장할 이유도 없다. — **이게
     설계상 유일한 함정이다.**
   - 부수 효과: `refresh_index(include_registered=True)` 가 도는 `list_resyncable`
     대상은 `source_url` 이 있는 문서라, NULL 로 두면 Drive/Notion 문서가 등록형
     재동기화 경로에 섞여 들어가지 않는다(원하는 격리가 공짜로 따라온다).
5. **`document_meta` ↔ `Document` 연결.** 결정적 ID 규칙(4번)으로 조인 키가
   생기므로 FK 컬럼 추가는 선택이다. 추가한다면 `document_meta.document_id`
   nullable FK + `ondelete="SET NULL"`.

### Phase 2 — 색인 파이프라인

6. **본문 색인은 옵트인 플래그로 시작.** `refresh_index(index_bodies: bool = False)`.
   `include_registered` 와 동형 — 비용 큰 경로는 기본 off 라는 기존 규약을 따른다.
7. **fetch 게이트가 핵심이다.** 등록형은 `content_hash` 비교를 위해 일단 fetch 해야
   하지만, Drive/Notion 은 `list_files()` 가 이미 `modified_at` 을 준다.
   `document_meta.modified_at` 이 그대로면 **fetch 자체를 건너뛴다.** rate limit 방어의
   전부가 여기 달려 있다. hash 는 fetch 한 문서에 한해 2차 게이트로 쓴다.
8. **문서 1건 색인은 `sync_service.resync` 패턴을 그대로 복제**한다:
   `chunk_repo.delete_by_document(id)` → `build_chunks` → `embed_documents` → insert.
   파서는 `parse_document(raw, "markdown")` 재사용(어댑터가 이미 평문을 준다).
9. **삭제 전파**: `_refresh_source` 의 `removed` 분기에서 `document_meta` 행뿐 아니라
   대응 `Document` 도 삭제한다(청크·벡터는 `ondelete="CASCADE"` 로 따라 사라진다).
10. **재임베딩 배치**: `app/scripts/reembed.py` 는 저장된 청크 텍스트만 다시 임베딩하므로
    **수정 없이** Drive/Notion 청크까지 커버한다.

### Phase 3 — 검색 경로

11. `chunk_repository` 에 `chunk_type == "section"` + project 스코프용 조회를 추가
    (기존 endpoint 전용 메서드를 복제하되 상수만 교체 — 하드코딩된 `"endpoint"` 5곳을
    인자로 승격하는 편이 깔끔하다).
12. `DocumentSearchService` 의 2단계(본문 실시간 fetch + 점수)를 FTS+벡터 RRF 로 교체.
    **전환은 플래그 뒤에서** 하고, 색인 안 된 문서가 남아 있는 동안은 기존 fetch 경로가
    폴백으로 살아 있어야 한다.
13. 전환 완료 후 실시간 fetch 는 `get_document` 에만 남는다 — `_body_fetch_budget`,
    `MAX_CONCURRENT_BODY_FETCHES` 등 2단계 예산 장치는 그때 삭제 대상이 된다.

### 순서 요약

Phase 0(정책) → 4·5(스키마) → 7·8(게이트+색인) → 9(삭제 전파) → 6(플래그 노출) →
11·12(검색 전환) → 13(구경로 제거). **9번을 6번보다 먼저** 하는 게 중요하다 —
삭제 전파 없이 색인을 켜면 지워진 문서의 벡터가 DB에 영구히 남는다.

---

## 2. refresh_index: upsert vs delete-and-insert

### 현행

`(project, source)` 단위 diff upsert. `list_by_project_source` 로 기존 행 집합을 잡고
목록과 비교해 added/updated/removed 를 판정, `BATCH_SIZE=100` 마다 커밋해 도중 실패해도
직전 배치까지 보존한다(SPEC 기능 6 "부분 실패 허용").

### 전량 delete-and-insert 로 바꿀 때

| 관점 | 판정 |
|---|---|
| 코드 단순성 | **유리.** diff 판정·`_SourceCounts`·`_PartialRefreshError` 가 통째로 사라진다. |
| stale 정리 | 동률. 현행 `removed` 감지가 이미 같은 일을 한다. |
| 부분 실패 정합성 | **치명적.** 삭제 커밋 후 재삽입 중 API 가 실패하면 캐시가 빈 채로 남아 검색이 0건이 된다. 한 트랜잭션으로 묶으면 배치 커밋의 부분 실패 허용이 사라지고, 대량 문서에서 긴 트랜잭션·잠금이 생긴다. SPEC 기능 6 요구사항 정면 위반. |
| 검색 가용성 | **불리.** 삭제 커밋과 재삽입 커밋 사이에 들어온 검색 요청이 빈 캐시를 본다. |
| rate limit | **최악(임베딩 시나리오).** 전량 재삽입은 "변경 없음" 판정 근거인 기존 `modified_at` 을 스스로 지워버린다. 그러면 §1-7 의 fetch 게이트가 성립하지 않아 **매 refresh 마다 전 문서 fetch + 재파싱 + 재임베딩**이 된다. |
| 벡터 비용 | **최악.** 위와 같은 이유로 안 바뀐 문서의 벡터까지 전부 버리고 다시 만든다. |

즉 임베딩 도입 시나리오는 delete-and-insert 를 **더 나쁘게** 만든다. 임베딩이 붙는
순간 "무엇이 안 바뀌었는지 아는 것"이 가장 값비싼 정보가 되는데, 전량 삭제는 그
정보를 매번 스스로 파괴한다.

### 단, delete-and-insert 가 옳은 층이 따로 있다

**문서 1건 내부의 청크 집합.** 재파싱하면 청크 개수·경계·순서가 통째로 달라져 diff 가
무의미하다. 등록형 `resync` 가 이미 그렇게 한다(`sync_service.py:185`
`chunk_repo.delete_by_document` → 재색인). 여기서는 delete-and-insert 가 정답이다.

### 추천안 — 층을 나눈다

- **문서 집합(`document_meta` 행) = 현행 diff upsert 유지. 변경 없음.**
- **문서 1건의 파생물(청크·벡터) = 게이트(`modified_at` → `content_hash`) 통과 시에만
  delete-and-insert.**

이 조합이면 stale 벡터 문제는 자동으로 닫힌다(청크는 문서 단위 전량 교체, 문서 삭제
시 CASCADE). 그러면서 미변경 문서는 fetch·임베딩을 아예 건너뛰고, 부분 실패 허용도
현행 배치 커밋 구조 그대로 유지된다. 새로 만들 개념이 없다 — 등록형이 이미 쓰는
패턴을 한 층 아래에 그대로 적용하는 것뿐이다.

---

## 3. `Document.id` 길이 초과 판정 (doc/38 흡수, reviewer 리뷰 계기)

- 상태: 판정 확정 — 수정은 developer(app 코드 + 테스트), reviewer 재검토 완료.
- 계기: reviewer의 Drive/Notion Phase1+2 리뷰. `deterministic_document_id(project, source, external_id)`
  가 `f"{project}:{source}:{external_id}"` 원문 결합이라 `Document.id`(`String(64)`)를 쉽게
  넘긴다. `index_bodies=True` 실사용 시 `StringDataRightTruncation` 으로 색인 자체가 깨짐.
- 참고: `app/services/documents/document_body_indexer.py:30-32`, `app/models/document.py:30`,
  `app/services/ingestor/sync_service.py:234-236`, `app/services/indexer/indexer_service.py:81,94,121,193-197`,
  `docs/architect-review/28-schema-chunk-ref-id-truncation-fix.md`

### 3.0 결론

reviewer 지적은 **타당하고 심각도 HIGH**다. 다만 제시된 두 선택지 중 **컬럼 확장(A)은 반려**,
**해시 고정길이(B)를 채택**한다. 위 §1-4의 `Document.id` 규칙이 이 결정을 반영한 최종안이다.

이건 doc/28(schema 청크 `ref_id` 트렁케이션)와 **같은 계열의 버그**다 — ID 컬럼에 외부
원문 문자열을 그대로 넣어 바운드를 깬 것. 판정 방향도 같다: 컬럼을 넓히지 말고 규약으로
되돌린다.

### 3.1 진단 — 왜 반드시 터지는가

- `Document.id` 는 `String(64)`, 기존 등록형은 `_new_id() = uuid4().hex[:16]` → **16자**가
  사실상의 규약이다.
- 그런데 `project` 컬럼만 해도 `PROJECT_MAX_LENGTH = 128` 이다. **external_id 가 0자라도
  project 하나로 64를 넘길 수 있다.** Drive file_id(≈44) / Notion page_id(36) 는 그 위에 얹힌다.
- 테스트가 못 잡은 이유: `external_id="d1"`, project 도 짧은 기본값이라 합계 20자 남짓.
  실 데이터에서만 터진다.

**진짜 제약은 64가 아니라 파생 ID 예산이다.** `Document.id` 는 자기 컬럼 폭만의 문제가
아니다. 하위 엔티티 ID가 전부 여기서 파생되고, 그 컬럼들도 모두 `String(64)` 다:

| 파생 ID | 생성 위치 | 형태 | 컬럼 |
|---|---|---|---|
| `ApiSchema.id` | `indexer_service.py:81` | `{doc_id}:schema:{idx}` | `String(64)` |
| `DocumentSection.id` | `indexer_service.py:94` | `{doc_id}:section:{idx}` | `String(64)` |
| `Chunk.id` | `indexer_service.py:121` | `{doc_id}:chunk:{idx}` | `String(64)` |
| `ApiEndpoint.id` | `indexer_service.py:197` | `{doc_id}:ep:{16-hex}` | `String(64)` |

가장 빡빡한 게 endpoint(`+4+16 = 20자` 오버헤드)다. 즉 **`Document.id` 실효 예산은 64가
아니라 약 40자**이고, 등록형이 16자를 쓰는 건 우연이 아니다.

### 3.2 A(컬럼 확장) 반려 근거

1. **한 컬럼이 아니라 5개 컬럼 + FK 5개를 같이 넓혀야 한다.** `document.id` 만 늘리면
   `chunk.id`/`api_schema.id`/`document_section.id`/`api_endpoint.id` 가 그대로 터진다.
   FK 컬럼(`chunk.document_id` 등)까지 폭을 맞춰야 하니 마이그레이션이 테이블 5개+인덱스
   재생성으로 번진다. **가장 큰 디프.**
2. **상한이 없다.** project(128) + external_id(256) 이면 이론상 400자 초과다. 넓혀도
   "얼마나"에 근거가 없고, 다음 소스가 붙으면 또 넓혀야 한다. 해시는 폭이 입력과 무관하게
   고정된다.
3. **PK 폭은 공짜가 아니다.** `chunk` 는 HNSW/GIN 인덱스를 얹은 최대 테이블이고, 모든
   조인 키가 이 문자열이다. 400자 varchar PK 로 가는 건 성능·저장 양쪽에서 손해다.
4. **doc/29에서 이미 같은 이유로 A를 반려**했다. 여기서 뒤집으면 ID 규약이 둘로 갈라진다.

읽기 편한 ID(`myproject:drive:1a2b...`)라는 유일한 이점은, `document_meta` 가
project/source/external_id 를 **평문 컬럼으로 그대로 들고 있어** 조인 한 번이면
복원되므로 실익이 없다.

### 3.3 채택안(B) 명세

```python
def deterministic_document_id(project: str, source: str, external_id: str) -> str:
    """project/source/external_id 로 Drive/Notion 문서의 결정적 `Document.id` 를 만든다.

    `Document.id` 는 `String(64)` 이고 하위 엔티티 ID가 여기서 파생되므로
    (`{doc_id}:ep:{16-hex}` 가 20자를 더 쓴다), 입력 길이와 무관하게 고정
    길이를 낸다. 등록형 `_new_id()`(uuid4 16-hex)와 같은 폭이다.
    """
    key = f"{project}\x00{source}\x00{external_id}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{digest}"
```

- **길이**: `source`("drive"/"notion") + 1 + 16 → **최대 23자**. 최악의 파생 ID(endpoint)도
  43자로 64 안에 든다.
- **구분자 `\x00`**: project 명에 `:` 가 섞여도 `("a:b", "drive", "x")` 와 `("a", "b:drive", "x")`
  가 같은 키로 뭉치지 않는다. 공짜로 얻는 방어.
- **`source:` 프리픽스 유지**: 로그·DB 육안 조회에서 출처를 바로 읽을 수 있고, 소스별
  스캔이 프리픽스로 가능하다. 코드가 이 ID를 파싱해 되돌리는 곳은 없다(확인함) — 프리픽스는
  사람용이다.
- **충돌**: 16-hex = 64비트. 기존 `_new_id()` 의 uuid4 16-hex 와 **동일한 충돌 예산**이다.
  여기서 새 위험을 들여오는 게 아니다.
- **결정성 유지**: 같은 (project, source, external_id) → 항상 같은 ID. `document_meta.document_id`
  조인, 재색인 시 같은 행 갱신, 삭제 전파 모두 그대로 성립한다. §1-4~§1-5 의 계약은
  유지되고 **ID 생성식만 바뀌었다.**

**마이그레이션/호환성: 불필요.** `index_bodies` 는 기본 `False` 이고 Phase 2 는 이 판정 시점에
아직 커밋 전이라 이 규칙 변경 전 규칙으로 생성된 행이 존재하지 않았다. `document_meta.document_id`
FK 는 `document.id` 타입을 따라가므로 수정 없음.

### 3.4 수정 지시 (developer, 반영 완료)

1. `app/services/documents/document_body_indexer.py:30-32` 의 `deterministic_document_id` 를
   §3.3 명세대로 교체. docstring 에 "왜 고정 길이인가"(파생 ID 예산) 한 줄 남길 것.
2. `app/models/document_meta.py:41-43` 의 `document_id` docstring 이 이전 규칙을 명시하고
   있었다면 새 규칙으로 정정.
3. **테스트(RED→GREEN)** — 지금 테스트가 못 잡은 게 이 버그의 본질이므로 여기가 핵심:
   - `project="p" * 128`(컬럼 상한), `external_id=` Drive file_id 급 44자 실측 형태로
     `deterministic_document_id` 호출 → **`len(result) <= 40`** 단언(파생 ID 예산 기준.
     단순히 64 이하로 두지 말 것).
   - 같은 입력 → 같은 값(결정성), 입력 하나만 달라지면 다른 값(구분).
   - `index_document_body` 통합 테스트 1건을 **긴 project/external_id 로** 태워
     `Document`/`Chunk`/`DocumentSection` insert 가 통과하는지 확인 — 파생 ID까지 실제로
     들어가는 경로를 밟아야 이 계열 버그가 다시 안 샌다.
   - 기존 테스트의 `external_id="d1"` 은 그대로 둬도 된다(짧은 값 회귀 커버).
4. 스코프는 위 3개 파일 + 테스트. 마이그레이션·모델 컬럼 변경 **금지**.

### 3.5 나머지 리뷰 항목

reviewer가 확인한 fetch 게이트 / delete-insert / 삭제 전파 CASCADE / `source_url` NULL 격리 /
`index_bodies` 기본 False / alembic up-down 대칭 — **모두 본 문서 §1~§2 설계대로 동작 확인,
승인.** 별도 조치 없음.
