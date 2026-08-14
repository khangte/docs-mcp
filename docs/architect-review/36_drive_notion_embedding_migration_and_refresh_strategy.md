# Drive/Notion 임베딩 도입 변경범위 · refresh_index upsert vs delete-and-insert

- 일시: 2026-08-14
- 작성: architect
- 선행: `docs/architect-review/35_drive_notion_no_embedding_rationale.md`
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
   - `Document.id` 를 결정적으로 고정: ~~`f"{project}:{source}:{external_id}"`~~ →
     **정정(doc/38)**: 원문 결합은 `String(64)` 파생 ID 예산(실효 상한 ~40자)을
     넘겨 `StringDataRightTruncation` 을 유발한다. 실제 채택안은
     `f"{source}:{sha256(project\x00source\x00external_id)[:16]}"`(최대 23자).
     상세: `docs/architect-review/38_drive_notion_document_id_length_verdict.md`.
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
