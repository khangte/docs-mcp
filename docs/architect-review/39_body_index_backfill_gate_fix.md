# 기존 문서 본문 소급 백필 — 게이트 조건 수정으로 닫는다 (별도 스크립트·force 플래그 반려)

- 일시: 2026-08-15
- 작성: architect
- 선행: `docs/architect-review/38_doc36_step13_legacy_fetch_removal_gate.md`,
  `docs/architect-review/35_drive_notion_embedding_migration_and_refresh_strategy.md` §1-7
- 증상: `--index-bodies` 백필 실행 결과 `synced=274 added=0 updated=0 removed=0`,
  `app.chunk` 0건 그대로.

---

## 1. 원인 — 게이트가 "본문 유무"가 아니라 "메타 변경"만 본다

`_stage_upsert` (`document_index_service.py:339`):

```python
if index_bodies and needs_body_index:
    self._index_body(...)
```

`needs_body_index` 는 신규 행이거나 `_apply_changes` 가 True 를 준 경우다. `_apply_changes`
는 `modified_at`/`title`/`url` 이 하나라도 바뀐 경우만 True 다(`:428`). 274건은 doc35
Phase1+2 시점에 이미 메타 동기화가 끝나 있어 원본에서 아무것도 안 바뀌었고, 그래서
`index_bodies=True` 를 줘도 `_index_body` 가 한 번도 호출되지 않는다.

## 2. 이것은 1회성 백필 문제가 아니다

같은 구멍으로 빠지는 경로가 계속 생긴다:

- `index_bodies=False` 로 돈 refresh 에서 새로 추가된 문서 — 그 run 이 `added` 를 소비해
  버려서, 다음 run 에서는 메타가 안 바뀌었으므로 영원히 미색인.
- 본문 색인이 실패한 문서(권한 오류·rate limit) — 실패 후 메타는 그대로라 재시도 기회가
  다시 오지 않는다.
- 임베딩 모델 교체 등으로 청크를 비운 경우 — 메타는 멀쩡하니 재색인 안 됨.

**따라서 1회성 스크립트로 닫으면 다음 번에 같은 구멍이 다시 열린다.** 판정 대상 3안 중
"별도 백필 스크립트"는 이 이유로 반려한다.

## 3. `--force` 재사용도 반려

`refresh_documents.py` 의 `--force` 는 `resync_registered_documents(force=...)` 로 가는
**등록형 재동기화 축** 인자다(doc31 의 축 A/B 분리). 여기에 본문 백필을 얹으면 (a) 의미가
다른 두 축이 한 플래그에 묶이고, (b) 백필이 "운영자가 기억해서 켜야 하는 의식"이 된다 —
§2 의 구멍은 운영자가 켜는 걸 잊는 순간 그대로 남는다. (c) 매 실행마다 274건 전량
재fetch 라 게이트의 존재 이유(rate limit 방어)를 정면으로 없앤다.

## 4. 채택안 — 게이트 조건을 "메타 변경 **또는** 본문 미색인"으로 넓힌다

`row.document_id is None` 이 곧 "이 문서는 본문이 색인된 적 없다"는 신호이고, 이미 행에
들어 있다. 조건 한 줄만 고치면 된다:

```python
if index_bodies and (needs_body_index or row.document_id is None):
    self._index_body(...)
```

성질:

- **자기 치유.** §2 의 모든 경로가 다음 `--index-bodies` 실행에서 자동으로 회수된다.
- **비용 유한·자기 종료.** 색인에 성공하면 `_index_body` 가 `row.document_id` 를 채우므로
  (`:376`) 다음 실행부터는 기존 게이트로 되돌아간다. 추가 fetch 는 "아직 색인 안 된 문서
  수"만큼이고, 그건 어떤 백필 방식으로도 피할 수 없는 하한이다.
- **`index_bodies=False` 경로는 무영향.** 기본 동작은 그대로다.
- 새 플래그·새 스크립트·새 개념이 없다.

## 5. 함께 고쳐야 하는 것 — 문서 1건 실패가 소스 전체를 막는다

지금 `_index_body` 의 예외는 `_stage_upsert` 를 그대로 통과해 `_refresh_source` 의 try 로
올라가고, 거기서 **롤백 + 그 소스 중단**이다(`:286`). 배치 커밋 덕에 직전 배치까지는
남지만, 실패 문서는 매 실행마다 같은 자리에서 다시 걸리므로 **그 뒤 문서들은 영구히 백필
불가**다. §4 를 켜면 이 벽이 곧바로 드러난다(미색인 274건 전량을 fetch 하므로 권한 오류
하나만 있어도 걸린다).

수정: `document_source.fetch()` 만 `try/except IntegrationError` 로 감싸 경고 로그 후
그 문서만 건너뛴다. 검색 경로가 이미 쓰는 규약과 동일하다(`_fetch_and_score` — "한 건의
권한 오류가 검색 전체를 죽이지 않게 한다").

**fetch 만 감싸고 `index_document_body` 는 감싸지 않는다.** fetch 는 DB 쓰기 이전 지점이라
건너뛰어도 세션이 깨끗하지만, `index_document_body` 는 청크 삭제·삽입을 세션에 올린 뒤라
중간 실패를 삼키면 반쯤 쓰인 상태가 다음 `_commit_batch` 에 실려 커밋된다. 파서·임베딩
실패는 그 실행 전체가 어차피 성립하지 않으므로 지금처럼 중단시키는 것이 맞다.

건너뛴 문서는 `document_id` 가 NULL 로 남아 §4 게이트가 다음 실행에서 자동 재시도한다.

## 6. 하지 않는 것

- `_SourceCounts` 에 본문 색인 카운터 추가 — 검증은 doc38 §6-2 대로 SQL(`app.chunk` 의
  `section` 건수, `document_meta.document_id` 채워진 비율)로 한다. 집계 필드를 늘리면
  `RefreshResult`·MCP 도구 응답까지 파급된다. 필요해지면 그때 추가한다.
- `--force` 의미 변경, 신규 CLI 플래그, 별도 백필 스크립트 (§2·§3).

## 7. doc35 13번 게이트에 미치는 영향

doc38 §6 의 순서는 유지된다. (1) 이 수정 → (2) `--index-bodies` 재실행으로 274건 백필 →
색인률 확인 → (3) `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY` 기본값 전환 → (4) 구경로 삭제.
