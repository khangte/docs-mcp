# 본문 백필 크래시 3종 판정 — NUL 정규화 위치·빈 본문·커밋 경계

- 일시: 2026-08-15
- 작성: architect
- 선행: `docs/architect-review/39_body_index_backfill_gate_fix.md`,
  `docs/architect-review/38_doc36_step13_legacy_fetch_removal_gate.md`
- 증상: doc39 수정 후 재백필이 여전히 0건, 이번엔 exit 1.
  (a) drive PDF 본문에 NUL(0x00) → `PostgreSQL text fields cannot contain NUL bytes`
  (`drive:2239e73bded1694c`, `AI로개발을가속하기.pdf`).
  (b) notion 쪽 `empty document`(`markdown_parser.py:21` `ParserError`).
  (c) 두 소스 다 실패 → `refresh` 가 "모든 소스 실패" 로 `IntegrationError`, exit 1.
  (d) 커밋 경계가 added/updated/removed 기준이라 소급 색인 중에는 중간 커밋이 없다.

---

## 판정 요약

| 건 | 판정 |
|---|---|
| (a) NUL 정규화 | **어댑터 경계 = `FetchedDocument.__post_init__`**. 파서 단·`index_document_body` 단 모두 반려. |
| (b) 빈 본문 | 오류 아님. `_index_body` 진입 시 선검사로 건너뛰고, 이미 색인돼 있었다면 그 본문을 지운다. |
| (c) exit 1 | (a)(b) 를 고치면 소스 실패 자체가 사라진다. `refresh` 의 전체실패 판정 로직은 그대로 둔다. |
| (d) 커밋 경계 | `_SourceCounts` 에 `indexed_bodies` 를 추가하고 `total_changes` 에 포함한다. |

---

## 1. NUL 정규화는 어댑터 경계에서 한다 (a)

### 왜 파서 단이 아닌가

`parse_document` 는 등록형 파이프라인(openapi/csv/markdown)과 공유되는 순수 변환 계층이고,
**`get_document` 경로는 파서를 타지 않는다.** 파서에서 씻으면 MCP `get_document` 응답에는
NUL 이 그대로 나간다. 같은 오염원에 정화 지점이 둘로 갈라진다.

### 왜 `index_document_body` 단이 아닌가

색인 경로만 고치는 것이고, 역시 `get_document` 를 못 덮는다. "DB 에 넣기 직전에 씻는다"는
증상 지점 패치다 — 오염은 외부 API 응답에서 들어왔다.

### 채택: `FetchedDocument.__post_init__`

`FetchedDocument` 는 두 어댑터가 본문을 내보내는 **유일한 통로**다
(`google_drive_source.py:268`, `notion_source.py:133`). 여기서 씻으면 색인·검색·
`get_document` 세 소비자가 한 번에 덮이고, 앞으로 추가될 어댑터도 우회할 수 없다.
프로젝트 규약("외부 데이터는 신뢰하지 않는다 — 경계에서 검증")과도 일치한다.

```python
@dataclass(frozen=True)
class FetchedDocument:
    text: str
    truncated: bool

    def __post_init__(self) -> None:
        # PostgreSQL text 컬럼은 NUL(0x00)을 저장할 수 없다. PDF/DOCX 텍스트
        # 추출이 NUL 을 섞어 내보내므로 소스 경계에서 제거한다.
        if "\x00" in self.text:
            object.__setattr__(self, "text", self.text.replace("\x00", ""))
```

**제거 대상은 NUL 하나뿐이다.** 다른 제어문자는 PostgreSQL 이 저장할 수 있으므로 건드리지
않는다 — 일반 제어문자 스크러버는 지금 없는 문제를 위한 코드다.

## 2. 빈 본문은 오류가 아니다 (b)

Notion 페이지는 하위 페이지만 있거나 본문이 비어 있는 경우가 정상적으로 존재한다.
`parse_document` 의 `ParserError("empty document")` 는 등록형(사용자가 문서를 등록했는데
내용이 없음 = 사용자 오류)에서는 맞는 계약이지만, 목록 전체를 훑는 소급 색인에서는
"색인할 것이 없는 정상 문서"다.

`_index_body` 에서 **fetch 직후, `index_document_body` 호출 전에** 선검사한다:

```python
if not fetched.text.strip():
    # 본문이 빈 문서는 색인 대상이 아니다(하위 페이지만 있는 Notion 페이지 등).
    # 이전에 색인돼 있었다면 그 본문은 더 이상 유효하지 않으므로 지운다.
    self._drop_indexed_body(project, source_name, meta.external_id, row)
    return
```

`ParserError` 를 `try/except` 로 잡지 않고 선검사로 거르는 이유: 예외 포획은
`index_document_body` 내부에서 파싱이 쓰기보다 먼저 일어난다는 **현재 구현 순서에 의존**한다
(지금은 `:83` 파싱 → `:86` 이후 쓰기라 우연히 안전하다). 선검사는 그 순서와 무관하게
성립한다. doc39 §5 와 같은 원칙 — 세션에 쓰기가 시작되기 전에 판정한다.

**빈 본문으로 바뀐 문서의 기존 색인은 지운다.** `document_repo.get(deterministic_document_id(...))`
로 찾아 있으면 삭제(청크·벡터는 CASCADE)하고 `row.document_id = None` 으로 되돌린다.
안 지우면 원문이 비워졌는데 검색은 옛 본문 스니펫을 계속 내보낸다 — doc35 §0-1 이
필수 요건으로 못박은 삭제 전파와 같은 부류의 구멍이다. `_delete_removed` 는 메타 행까지
지우므로 재사용하지 않는다(여기서는 문서가 원본에 여전히 존재한다).

## 3. 커밋 경계에 본문 색인 건수를 포함한다 (d)

`total_changes = added + updated + removed` (`:93`) 는 메타 diff 만 센다. 소급 색인은
정의상 메타 변경이 0건이므로 `pending.total_changes` 가 영원히 0이고, `BATCH_SIZE` 경계가
한 번도 걸리지 않아 **소스 하나가 끝날 때까지 단 한 번도 커밋되지 않는다**(`:300` 최종
커밋이 유일). 274건 중 마지막 한 건이 깨지면 앞의 273건 색인이 통째로 롤백된다.
developer 지적이 정확하다 — 이건 doc39 의 자기 치유 게이트가 커버하지 못하는 별개 결함이다.

수정:

- `_SourceCounts` 에 `indexed_bodies: int = 0` 추가, `total_changes` 에 합산.
- `_index_body` 성공 시 `pending.indexed_bodies += 1`.
- `_commit_batch`·`_merge_counts` 의 누적에 같은 필드 추가.
- `_refresh_source` 완료 로그에 `indexed_bodies` 한 항목 추가(백필이 실제로 돌았는지 보는
  운영 신호).

**`RefreshResult`·MCP 도구 응답에는 노출하지 않는다** — doc39 §6 의 판단은 유지된다.
`RefreshResult` 는 필드를 명시적으로 나열해 만들므로(`:202`) 자동 누출은 없다.

배치 크기는 `BATCH_SIZE=100` 그대로 둔다. 본문 색인 전용 경계 상수를 따로 두면 최악 손실이
100건에서 10건으로 줄지만, doc39 §5 와 §1·§2 를 적용하고 나면 남는 크래시 요인은 임베딩
제공자·DB 장애처럼 "그 실행 전체가 어차피 성립하지 않는" 부류다. 실제 백필에서 부분 손실이
문제로 드러나면 그때 낮춘다.

## 4. exit 1 자체는 손대지 않는다 (c)

`refresh` 가 모든 소스 실패 시 `IntegrationError` 를 올리는 것은 정상 계약이다(doc31).
이번 exit 1 은 그 계약이 잘못돼서가 아니라 소스 두 개가 실제로 다 죽어서 나온 결과다.
§1·§2 로 문서 1건짜리 원인이 제거되면 소스 실패가 사라진다. 부분 실패 관용은 doc39 §5 의
per-document skip 이 이미 담당한다.

## 5. 이후 순서

doc38 §6 순서 유지: 이 수정 → `--index-bodies` 재백필 → `app.chunk` 의 `section` 건수와
`document_meta.document_id` 채움 비율로 색인률 확인 → `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY`
기본값 전환 → doc35 13번(구경로 삭제).
