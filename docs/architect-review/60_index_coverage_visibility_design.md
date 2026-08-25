# 60. 색인 커버리지 가시화 설계 (개선 #5)

- 대상: `docs/architect-review/57_gdrive_search_logic_comparative_analysis.md` §5 Top5 의 5번
- 작성: architect, 2026-08-26
- 상태: 설계 확정 — developer 구현 대기

## 0. 범위

57번 §5 #5 는 두 갈래다.

| 항목 | 상태 |
| --- | --- |
| `search_documents` 결과 항목의 `indexed` 플래그 | **이미 완료**(개선 #1, 57번 §5.1). `app/mcp/payloads.py:147`. 이번 작업 범위 아님 |
| `refresh_index` 응답의 `unindexed` / `unsupported` / `folder_limit_reached` | **이번 작업 범위** |

## 1. 문제

"검색에 왜 안 나오는가"의 근거가 전부 서버 로그에만 있다.

1. **본문 미색인(`unindexed`)** — `document_meta.document_id` 가 NULL 이면 keyword/vector arm 이
   비어 title 매칭만으로 조용히 퇴화한다. 현재 이 사실은 `index_bodies=False` 경고 로그
   (`document_index_service.py` `refresh()`)와 fetch 실패 경고
   (`_index_body`)에만 남는다. 운영자는 `refresh_index` 응답만 보면 "정상 동기화"로 읽는다.
2. **MIME 미지원(`unsupported`)** — 이미지/영상/그림·폼 같은 네이티브 타입은
   `_fetch_native_export` / `_fetch_binary_text` 가 `IntegrationError` 를 던지고,
   `_index_body` 가 그걸 warning 으로 삼킨 뒤 `False` 를 반환한다. `document_id` 는 NULL 로
   남으므로 `_stage_upsert` 의 `row.document_id is None` 게이트에 매번 걸려
   **매 refresh 마다 같은 파일을 다시 fetch 한다**(영구 재시도 — 가시성 문제이자 비용 문제).
3. **탐색 상한 도달** — Drive `list_files` 의 `MAX_FOLDERS = 500`
   (`google_drive_source.py:224` 경고), Notion `MAX_PAGES = 500`
   (`notion_source.py:140` 등)에 걸려 목록이 잘려도 응답에는 흔적이 없다. `synced` 만 줄어들 뿐이라
   "문서가 원래 그만큼"인지 "잘린 것"인지 구별 불가.

## 2. 응답 계약

`RefreshIndexResult` 에 `coverage` 키를 추가한다. 기존 4개 카운터는 **이번 실행의 변화량(delta)**
이고 커버리지는 **갱신 대상 범위의 현재 상태(state)** 라, 의미가 다른 값을 평평하게 섞지 않기 위해
중첩한다.

```jsonc
{
  "synced": 120, "added": 3, "updated": 5, "removed": 1,
  "failed_sources": [],
  "coverage": {
    "unindexed": 4,          // 본문 색인 없음(document_id NULL) — MIME 미지원 제외
    "unsupported": 7,        // 텍스트 추출 불가 MIME 이라 fetch 자체를 건너뜀
    "listing_truncated": ["payments/drive"]   // 탐색 상한에 걸려 목록이 잘린 "<project>/<source>"
  },
  "registered": { }        // include_registered=True 일 때만(기존 그대로)
}
```

- `coverage` 는 항상 존재한다(`NotRequired` 아님). `include_registered` 와 달리 하위호환 분기 없음 —
  키 추가는 MCP 클라이언트에 파괴적이지 않다.
- `unindexed` 와 `unsupported` 는 **서로소**다. 둘의 합 = 이번 범위에서 `document_id` 가 NULL 인 문서 수.
  `unsupported` 는 "원래 색인될 수 없는 것"(정상), `unindexed` 는 "색인돼야 하는데 안 된 것"(조치 대상)
  이라 운영자가 나눠 볼 수 있어야 의미가 있다.
- `registered`(URL 기반 Document 재동기화)는 `document_meta` 축이 아니므로 커버리지 집계에 넣지 않는다.

### 2.1 `folder_limit_reached` → `listing_truncated` (원안에서 이름 변경)

57번 원안의 키 이름은 `folder_limit_reached` 였다. 그러나 같은 조건을 Notion 도 `MAX_PAGES`
로 트립하는데 Notion 에는 폴더 개념이 자체가 없다. 소스별로 키를 둘로 나누면 호출자가 소스마다 다른
키를 봐야 하므로, 소스 중립 이름 `listing_truncated` 로 통일한다.

값을 bool 이 아니라 `"<project>/<source>"` 문자열 목록으로 두는 이유: 여러 프로젝트·소스를 한 번에
갱신하는 호출에서 bool 은 "어느 트리가 잘렸는지"를 지운다. `failed_sources` 와 같은 라벨 형식이라
호출자가 이미 아는 포맷이다.

## 3. 설계

### 3.1 MIME 지원 여부를 목록 시점에 판정한다 (`unsupported`)

fetch 실패로 사후 분류하지 않고, `list_files()` 가 이미 들고 있는 `FileMeta.mime_type` 으로
**fetch 전에** 판정한다. 왕복 1회를 아끼는 것보다 §1-2 의 영구 재시도를 끊는 게 본질이다.

`DocumentSource` Protocol 에 메서드를 추가한다:

```python
def supports_text_extraction(self, mime_type: str | None) -> bool:
    """이 MIME 타입에서 본문 텍스트를 추출할 수 있으면 True."""
```

- **Drive**: 네이티브 접두사(`GOOGLE_NATIVE_MIME_PREFIX`)면 `NATIVE_EXPORT_MIME_TYPES` 에 있는지,
  `text/` 로 시작하면 True, 그 밖이면 `BINARY_TEXT_EXTRACTORS` 에 있는지. 판정 근거를 fetch 경로와
  **같은 상수**에서 가져와야 두 곳이 갈라지지 않는다.
- **Notion**: 항상 True(`mime_type` 이 항상 None).
- `mime_type` 이 None/빈 문자열이면 **True**(모른다고 미리 건너뛰지 않는다 — 판정 실패로 색인을
  누락시키느니 fetch 가 실패하게 둔다).

`fetch()` 안의 기존 `IntegrationError` 는 그대로 둔다. `get_document` 는 색인 범위 밖 임의 file ID 도
fetch 하므로 그쪽 방어선이 여전히 필요하다.

**이미 색인된 행은 건드리지 않는다.** `document_id` 가 NULL 이 아닌데 MIME 이 미지원으로 바뀐 경우
(Drive 에서 사실상 발생하지 않음 — 타입이 바뀌면 file ID 가 새로 생긴다)는 기존 본문을 그대로 두고
`unsupported` 에도 넣지 않는다. 이 케이스를 위한 Document 삭제 로직은 넣지 않는다(YAGNI).

### 3.2 목록 절단을 반환값에 싣는다 (`listing_truncated`)

`list_files()` 의 반환 타입을 바꾼다:

```python
@dataclass(frozen=True)
class FileListing:
    """`list_files()` 한 번의 결과.

    truncated: 탐색 상한(Drive MAX_FOLDERS / Notion MAX_PAGES)에 걸려 목록이 불완전하면 True.
    """
    files: list[FileMeta]
    truncated: bool = False
```

어댑터 상태 플래그(`source.listing_truncated` 를 나중에 읽는 방식)는 쓰지 않는다 — 호출 순서에
의존하고 타입으로 강제되지 않는다. 호출자는 `DocumentIndexService._refresh_source` 한 곳뿐이라
반환 타입 변경 비용이 낮다.

기존 경고 로그는 유지한다(배치 CLI 운영자가 보는 신호).

### 3.3 커버리지 집계 위치

`_SourceCounts` 에 `unindexed` / `unsupported` 를 추가하고, `_stage_upsert` 가 문서 한 건 처리를
끝낸 직후 `row.document_id` 기준으로 분류한다:

| 조건 | 집계 |
| --- | --- |
| `row.document_id is not None` | 없음(색인됨) |
| NULL + `supports_text_extraction(meta.mime_type)` False | `unsupported += 1` |
| NULL + 그 밖 | `unindexed += 1` |

- 원본에서 사라져 삭제된 행(`_delete_removed`)은 범위를 떠났으므로 세지 않는다.
- **`total_changes` 에는 절대 넣지 말 것.** 이 둘은 DB 쓰기가 아니라 상태 관찰값이라, 커밋 경계
  판정에 섞이면 변경 없는 문서만으로 커밋이 유발된다.
- pending → committed 이동은 기존 카운터와 같은 경로(`_commit_batch`)를 탄다. 즉 부분 실패 시
  커버리지도 **커밋된 배치까지만** 반영된다 — `added`/`updated` 와 같은 규칙이라 응답 안에서
  일관된다. 이 사실을 `RefreshResult` docstring 에 명시할 것.
- `index_bodies=False` 로 호출하면 범위 안 NULL 행이 전부 `unindexed` 로 잡힌다. 이게 의도한
  동작이다 — "본문 없이 메타만 돌린 결과 검색이 title arm 만 남았다"를 응답으로 보여주는 게 목적.

`unsupported` 판정은 `_stage_upsert` 안에서 이미 계산한 값을 재사용한다(fetch 게이트와 집계가
같은 판정을 두 번 하지 않게 할 것).

### 3.4 fetch 게이트 변경

`_stage_upsert` 의 본문 색인 조건:

```python
if index_bodies and (needs_body_index or row.document_id is None):
```

앞에 지원 여부를 곱한다 — 미지원이면 `_index_body` 를 호출하지 않는다. 이걸로 §1-2 영구 재시도가
사라진다(파일 1건당 매 refresh 마다 metadata GET + export/다운로드 시도 1회씩 절감).

## 4. 변경 파일

| 파일 | 변경 |
| --- | --- |
| `app/services/documents/sources/document_source.py` | `FileListing` 추가, Protocol 에 `list_files() -> FileListing`, `supports_text_extraction()` 추가 |
| `app/services/documents/sources/google_drive_source.py` | `list_files` 가 `FileListing(collected, truncated=bool(pending))` 반환, `supports_text_extraction` 구현 |
| `app/services/documents/sources/notion_source.py` | `list_files` 가 `FileListing` 반환(`list_pages()` 는 시그니처 유지, 절단 여부는 `list_files` 에서 판정), `supports_text_extraction` 은 항상 True |
| `app/services/documents/document_index_service.py` | `RefreshResult`/`_SourceCounts` 확장, `_refresh_source` 가 절단 라벨 수집, `_stage_upsert` 분류·게이트 |
| `app/mcp/types.py` | `RefreshCoverage` TypedDict 추가, `RefreshIndexResult.coverage` |
| `app/mcp/payloads.py` | `_to_refresh_payload` 에 coverage 매핑 |
| `app/mcp/tools/sources.py` | `refresh_index` docstring Returns 절에 coverage 설명 |
| `app/scripts/refresh_documents.py` | 완료 로그에 `unindexed=%d unsupported=%d`, 절단 시 warning 1줄 |
| `tests/fixtures/document_sources.py` | 페이크 2종의 `list_files` 반환 타입 + `supports_text_extraction` |
| `tests/unit/test_document_search_service.py`, `scripts/bench_search_perf.py` | 페이크 시그니처만 맞춤(호출되지 않는 경로) |

### Notion `list_pages()` 절단 판정

Notion 은 `MAX_PAGES` 도달 시 여러 지점에서 `return`/경고한다. `list_pages()` 내부를 다 고치지
말고, **`len(acc) >= MAX_PAGES`** 를 `list_files()` 에서 판정해 `truncated` 로 싣는다(상한에 도달한
목록은 정의상 잘린 것이다). 내부 재귀 구조는 손대지 않는다.

## 5. 테스트

`tests/unit/test_document_index_service.py`, `tests/unit/test_document_sources.py` 기준.

1. 미지원 MIME(예: `image/png`) 1건 + 지원 문서 1건 → `coverage.unsupported == 1`,
   `coverage.unindexed == 0`, **페이크의 fetch 호출 대상에 미지원 문서가 없을 것**(게이트 검증).
2. 같은 입력으로 refresh 를 2회 연속 실행 → 2회차에도 미지원 문서 fetch 0회(영구 재시도 제거 회귀 가드).
3. `index_bodies=False` → 범위 전체가 `coverage.unindexed`, `unsupported` 는 미지원 건만.
4. fetch 가 `IntegrationError` 를 던지는 문서(지원 MIME) → `unindexed` 에 잡힌다.
5. 본문이 공백뿐인 문서 → `document_id` 가 NULL 로 정리되므로 `unindexed`.
6. Drive `list_files`: 폴더 수가 `MAX_FOLDERS` 를 넘는 페이크 응답 → `FileListing.truncated is True`,
   이하이면 False.
7. Notion `list_files`: `MAX_PAGES` 도달 → `truncated is True`.
8. 서비스: 절단된 소스 1개 + 정상 소스 1개 → `coverage.listing_truncated == ["<project>/<source>"]`.
9. MCP 페이로드: `refresh_index` 응답에 `coverage` 3키가 모두 있고, 기존 4카운터·`failed_sources`·
   `registered` 계약이 그대로일 것.
10. 부분 실패(중간 문서에서 예외) → 응답의 coverage 가 커밋된 배치 기준으로만 집계될 것.

## 6. 하지 않는 것

- 소스별 커버리지 분해(`coverage.by_source`) — 지금 필요한 신호는 "전체에 구멍이 있는가"와
  "어느 트리가 잘렸는가"뿐이다. 라벨 목록으로 후자는 이미 답한다.
- 미지원 문서 목록(external_id 배열) 노출 — 수천 건 규모에서 응답이 커진다. 필요해지면 그때
  전용 도구로 뺀다.
- `document_meta` 스키마 변경 없음. 마이그레이션 없음.
- 랭킹·검색 경로 변경 없음.
