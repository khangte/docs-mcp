# 61. 색인 커버리지 가시화 구현 코드 판정 (개선 #5)

- 설계: `docs/architect-review/60_index_coverage_visibility_design.md`
- 검토: architect, 2026-08-26
- 판정: **설계 부합 — 조건부 승인**(미세 정리 2건 후 reviewer 배정 가능)

## 1. 설계 대비 대조

| 설계 항목 | 구현 | 판정 |
| --- | --- | --- |
| §2 응답 계약 `coverage{unindexed,unsupported,listing_truncated}` 중첩 | `RefreshCoverage` TypedDict + `_to_refresh_payload` | 부합 |
| §2 `coverage` 항상 존재(NotRequired 아님) | `RefreshIndexResult.coverage: RefreshCoverage` | 부합 |
| §2.1 `listing_truncated` 를 `"<project>/<source>"` 라벨 목록으로 | `refresh()` 의 `truncated_labels` | 부합 |
| §3.1 MIME 지원 여부를 목록 시점에 선판정 | `DocumentSource.supports_text_extraction()`, Drive 는 fetch 와 같은 상수 재사용, Notion 은 항상 True, 빈 값은 True | 부합 |
| §3.1 이미 색인된 행은 미지원으로 바뀌어도 건드리지 않음 | 분류가 `row.document_id is None` 조건 안에만 있음 | 부합 |
| §3.2 `FileListing(files, truncated)` 반환 | Drive `bool(pending)`, Notion `len(pages) >= MAX_PAGES` | 부합 |
| §3.3 `unindexed`/`unsupported` 서로소, `total_changes` 오염 없음 | `total_changes` 미변경, 분류가 if/else 배타 | 부합 |
| §3.3 커밋 경계 규칙(부분 실패 시 커밋분까지) | pending → `_commit_batch` 경로, `listing_truncated` 만 `committed` 초기값으로 별도 보관 | 부합 |
| §3.4 미지원이면 `_index_body` 미호출 | `index_bodies and supported and (...)` | 부합 |
| 범위 밖: `search_documents.indexed` | 미변경 | 부합 |
| 스키마·마이그레이션 없음 | 없음 | 부합 |

`listing_truncated` 를 `_SourceCounts` 에 두되 `_merge_counts` 에서 합치지 않고 소스별 라벨로만 쓰는
처리는 설계에 명시하지 않았던 부분인데, `list_files()` 성공 시점에 확정되는 값이라 배치 집계와 성질이
다르다 — 올바른 분리다.

부분 실패 경로에서 `exc.committed.listing_truncated` 도 라벨에 넣는다. 목록은 받았지만 도중에 실패한
소스도 "잘렸다"는 사실은 유효하므로 맞다.

## 2. 테스트

설계 §5 의 10건이 모두 있다(T1~T10). 특히 T2(2회 refresh 후에도 fetch 0회)와
T10(부분 실패 시 `unindexed == BATCH_SIZE`)이 각각 영구 재시도 제거와 커밋 경계 규칙을 실제로 고정한다.
T6 은 `MAX_FOLDERS` 를 1로 monkeypatch 해 상한 도달 분기를 탄다 — 상한값 자체에 의존하지 않는 좋은 형태.

## 3. 정리 요청 2건 (블로킹 아님)

1. **Protocol 드리프트 — 페이크 2개 미갱신.** 설계 §4 변경 파일표에 있었으나 반영되지 않았다.
   - `scripts/bench_search_perf.py:182` `_FakeDriveSource.list_files` → `[]` 반환
   - `tests/unit/test_document_search_service.py:73` `_SlowTrackingDocumentSource.list_files` → `[]` 반환
   둘 다 `supports_text_extraction` 이 없다. 현재 두 페이크의 `list_files` 는 호출되지 않아
   테스트는 통과하지만, `DocumentSource` 와 타입이 어긋난 페이크가 남으면 다음에 이 페이크를
   인덱스 서비스 경로에 붙이는 순간 조용히 깨진다. `FileListing(files=[])` 반환 + 메서드 추가로 정리할 것.
2. **CLI 경고 경로 미검증.** `app/scripts/refresh_documents.py` 의 `listing_truncated` warning 은
   테스트가 없다. `tests/unit/test_refresh_documents_script.py` 에 `listing_truncated` 가 있는
   `RefreshResult` 로 warning 1줄을 확인하는 케이스를 추가할 것(기존 케이스들은 기본값으로 통과 중이라
   회귀를 잡지 못한다).

## 4. 커밋 분리 주의

워킹트리의 `.gitignore` 와 `tests/fixtures/corpus_eval/run_corpus_eval.py` 변경은 개선 #5 이전부터
있던 것으로 이번 작업과 무관하다. 커밋 시 섞이지 않게 분리할 것.
