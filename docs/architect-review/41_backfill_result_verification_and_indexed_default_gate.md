# 백필 결과 검증(오차 3건 규명) 및 `indexed` 기본값 전환 게이트 판정

- 일시: 2026-08-15
- 작성: architect
- 선행: `docs/architect-review/38_doc36_step13_legacy_fetch_removal_gate.md` §4,
  `docs/architect-review/39_body_index_backfill_gate_fix.md`,
  `docs/architect-review/40_body_backfill_content_normalization_and_commit_boundary.md`
- 질문: (1) 보고치와 DB 사이 3건 오차가 버그인가. (2) 지금 상태로 기본값을 `indexed` 로
  전환해도 되는가.

---

## 1. 오차 3건 — 데이터 버그 아님, 카운터 의미가 잘못 붙었다

DB 실측(`app` 스키마):

| 항목 | drive | notion | 합 |
|---|---|---|---|
| `document_meta` 행 | 164 | 110 | 274 |
| `document_meta.document_id` NOT NULL | 28 | 108 | 136 |
| `document` 행 | 28 | 108 | 136 |
| `chunk`(`section`) | — | — | 5056 |

`document` 행 수와 `document_id` 채워진 메타 행 수가 **정확히 일치**한다(28/28, 108/108).
고아 `Document` 도, 색인됐는데 메타에 기록 안 된 행도 없다. 데이터는 정합적이다.

오차의 출처는 코드다. `_index_body` 는 **fetch 가 성공하면 무조건 `True`** 를 돌려주고
(`document_index_service.py:409` 빈 본문 스킵 경로도 `return True`), 호출자가 그걸로
`pending.indexed_bodies` 를 올린다(`:349`). 즉 이 카운터가 실제로 세는 값은
**"본문 fetch 성공 건수"** 이지 "색인 건수"가 아니다. 빈 본문 문서는 fetch 는 성공하고
색인은 안 되므로 둘의 차이만큼 벌어진다.

따라서 오차 3건 = **본문이 빈 문서 3건**(drive 1 + notion 2). 보고치 29/110 은
`fetch 성공`, DB 28/108 은 `색인 완료`. 둘 다 맞는 값이다. 재확인 작업 불필요.

### 그래도 고칠 것 — 이름만 바꾼다

카운터 자체는 지금 계산이 맞다. 커밋 경계(doc40 §3) 관점에서는 빈 본문 경로도 세는 게
옳다 — 그 경로도 기존 `Document` 를 지우는 DB 쓰기를 한다(`:406-409`). 문제는 이름과
로그가 "색인했다"고 말한다는 것뿐이고, 이번에 그 오해로 한 라운드를 썼다.

→ `_SourceCounts.indexed_bodies` 를 **`fetched_bodies`** 로 개명하고 완료 로그 항목명도
맞춘다. 카운터를 둘로 쪼개지 않는다(보고용 색인 건수는 SQL 로 보는 것이 doc39 §6 판단이고
그대로 유효하다).

## 2. 기본값 `indexed` 전환 — 승인

### 색인률은 이미 상한이다

의미 있는 분모는 전체 274건이 아니라 **본문 텍스트를 뽑을 수 있는 문서**다.

- drive 164 = 색인 28 + 빈 본문 1 + fetch 실패 135(png/mp4/hwp/50MB 초과 등 바이너리)
- notion 110 = 색인 108 + 빈 본문 2

→ **텍스트가 있는 문서의 색인률 100%(136/136).** 미설명 잔여분은 0건이다. doc38 §4(b) 가
말한 "색인률 목표치"는 이 정의로 충족됐다. 전체 대비 49.6% 라는 숫자는 분모에 애초에
색인 불가능한 바이너리가 들어간 값이라 게이트 지표로 쓰면 안 된다.

### 완전 색인이 전제조건이 아니라는 판단은 맞다 — 그리고 더 강하다

doc37 §2.3 대로 title arm 은 `deterministic_document_id` 를 순수 계산하므로 미색인 문서도
융합 결과에 남는다. 여기까지가 질문자의 근거이고 옳다. 여기에 하나 더 있다:

**현행 `fetch` 전략에서 그 135건 바이너리는 검색 결과에 아예 안 나온다.**
`_rank_with_body` → `_fetch_and_score` 가 `IntegrationError` 를 잡고 `None` 을 반환해
결과에서 탈락시킨다(`document_search_service.py:450-458`). 즉 지금은 사용자가 파일명으로
PNG/동영상을 찾아도 0건이다. `indexed` 로 가면 title arm 을 타고 `_fallback_snippet` 과
함께 정상 노출된다.

정리하면 전환은 (a) 텍스트 문서 136건에는 3-arm RRF 라는 설계상 개선이고, (b) 바이너리
135건에는 **없던 결과가 생기는 순개선**이다. 회귀 방향의 케이스가 남아 있지 않다.

### 전환 조건과 롤백

- 코드 삭제는 하지 않는다. `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY` 기본값만
  `"fetch"` → `"indexed"` 로 바꾼다(`app/core/config.py:45`). 문제가 나면 환경변수 한 줄로
  되돌린다(doc37 §2.7 degrade 규약 유지).
- 겉면 계약 변경은 이미 명시돼 있다: `score` 스케일이 RRF 로 바뀌어 절대값 비교 불가·
  순서만 유의미(doc37 §2.5), 스니펫 출처가 동기화 시점 캐시가 될 수 있어
  `snippet_as_of` 노출(doc35 §0-2). 추가 계약 변경 없다.
- doc35 13번(구경로 삭제)은 이 전환 후 실사용 확인까지 계속 보류다(doc38 §4(c)).

## 3. 남는 비용 — 매 실행 138건 재fetch (지금은 고치지 않음)

doc39 의 자기 치유 게이트(`document_id is None` 이면 재시도)에는 실패 기억이 없다. 그래서
**영구히 텍스트를 못 뽑는 135건 + 빈 본문 3건이 `--index-bodies` 실행마다 다시 fetch 된다.**
1회성 백필에서는 무해하지만, 이 배치를 정기 실행으로 돌리면 매번 138건의 무의미한 Drive
API 호출이 깔린다.

지금 고치지 않는 이유: 백필 주기가 아직 안 정해졌고, 1회성이면 비용이 0에 가깝다.
정기 실행으로 갈 때의 수정 방향은 **`FileMeta` 에 `mime_type` 을 실어 `_index_body` 에서
텍스트 계열이 아니면 fetch 전에 건너뛰는 것**이다. `document_meta` 행 자체는 남겨야 한다 —
지우면 파일명으로 이미지를 찾는 title arm 매치가 사라져 §2 에서 얻은 개선이 도로 없어진다.

## 4. 실행 순서

1. `_SourceCounts.indexed_bodies` → `fetched_bodies` 개명(§1).
2. `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY` 기본값 `indexed` 로 전환(§2).
3. 실사용에서 검색 품질 확인 → 이상 없으면 doc35 13번(플래그·fetch 브랜치 동시 삭제) 착수.
4. 배치 정기 실행이 결정되면 §3 의 mime 게이트.
