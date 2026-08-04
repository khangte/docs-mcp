# `services/documents/` 경계 정리 방안

## 배경

`app/services/` 는 문서타입별이 아니라 **처리 계층(파이프라인 단계)별**로 나뉜다.
그런데 `documents/` 라는 이름은 "문서 전반"을 담당하는 것처럼 읽혀, 실제 책임과
어긋난다. 이 문서는 그 어긋남을 세 가지로 분해하고, 각각의 정리안을 제시한다.

**전제: 경계 자체는 이미 깨끗하다.** `documents/` 내부 파일은 `ingestor`/`indexer`/
`search`/`parser` 를 하나도 import 하지 않는다(자기완결적). 따라서 이건 *결합을 끊는*
리팩터링이 아니라 *네이밍·배치를 책임에 맞추는* 리팩터링이다. 동작 변경은 없다.

## `documents/` 안에 섞여 있는 세 부류

| 부류 | 파일 | 실제 책임 |
|------|------|-----------|
| **A. 협업문서 검색/색인** | `document_index_service.py`, `document_search_service.py`, `search_scorer.py`, `snippet_generator.py` | Drive/Notion 메타캐시 갱신 + 실시간 fetch 검색 (`DocumentMeta`) |
| **B. project→source 매핑** | `project_source_resolver.py`, `project_source_service.py`, `sources/*` | project 를 Drive/Notion 어댑터로 해석 (SPEC 기능 5·6) |
| **C. 공용 project 유틸** | `project_scope.py` | project 값 정규화·검증 — **파이프라인 전반이 공유** |

A 와 B 는 같은 "협업문서(Drive/Notion)" 세계에 속해 응집이 높다. 문제는 **C** 다.

## 문제의 핵심: `project_scope.py` 는 협업문서 것이 아니다

`project_scope.py` 를 import 하는 곳:

- `ingestor/sync_service.py` (등록형 파이프라인)
- `schema_resolution/schema_ref_resolver.py`
- `search/endpoint_candidate_search.py`
- `tags/tag_catalog_service.py`

즉 **등록형 API 문서 파이프라인 전반이 쓰는 공용 유틸**인데 `documents/`(협업문서
전용 디렉토리) 밑에 있다. 이게 "`documents/` 가 문서 전반을 관장한다"는 착시의
가장 큰 원인이다. project 정규화는 협업문서와 무관하며, 특정 도메인에 속하지 않는다.

## 제안

### 제안 1 (핵심) — `project_scope.py` 를 공용 위치로 이동

`app/services/documents/project_scope.py` → `app/core/project_scope.py`
(또는 `app/services/project_scope.py`).

- **근거**: 6개 서로 다른 파이프라인이 공유하는, 도메인 중립 정규화 규칙.
  협업문서 디렉토리에 둘 이유가 없다. `app/core/` 에는 이미 `errors`, `logging`,
  `config` 같은 횡단 관심사가 모여 있어 자연스럽다.
- **영향**: import 경로 변경 5곳(사용처 4 + `documents/` 내부 사용처). 동작 변경 없음.
- **주의**: `project_scope` 는 `app.models.openapi.PROJECT_MAX_LENGTH` 와
  `DocumentRepository` 를 참조한다. `app/core/` 로 옮기면 core→models/repositories
  방향 의존이 생긴다. 이 방향이 프로젝트 레이어링상 허용되는지 확인 필요.
  꺼려지면 **`app/services/project_scope.py`**(services 최상위)가 무난한 절충.

### 제안 2 — `documents/` 를 도메인 이름으로 개명

남은 A+B 는 전부 **Drive/Notion 협업문서** 전용이다. 등록형 API 문서(`ApiDocument`)와
헷갈리지 않도록 디렉토리 이름을 도메인에 맞춘다.

`app/services/documents/` → `app/services/collab_docs/`
(대안: `external_docs/`, `drive_notion/`)

- **근거**: 지금 이름은 등록형 `ApiDocument` 파이프라인까지 포함하는 것처럼 읽힌다.
  실제 내용은 "외부 협업도구 문서" 하나뿐이므로 이름을 좁혀 오해를 없앤다.
- **영향**: import 경로 변경 다수(`git mv` + 일괄 치환). 순수 개명, 동작 변경 없음.
- **트레이드오프**: 변경 범위가 제안 1보다 크다. 제안 1만으로도 착시의 주원인
  (공용 유틸의 오배치)은 해소되므로, **제안 2는 선택 사항**으로 둔다.

### 제안 3 (즉시) — 빈 `schemas/` 디렉토리 제거

`app/services/schemas/` 는 비어 있다(`schema_resolution/` 로 개명한 잔재, 커밋 3dbcbce).
`services/` 목록에서 유령 디렉토리로 남아 혼란을 준다. 즉시 삭제 가능.

## 권고 실행 순서

1. **제안 3** — 빈 `schemas/` 삭제 (리스크 0, 즉시)
2. **제안 1** — `project_scope` 를 공용 위치로 이동 (착시의 주원인 해소, 저리스크)
3. **제안 2** — `documents/` → `collab_docs/` 개명 (원하면; 범위 큼, 순수 개명)

제안 1·3만으로 "`documents/` 가 문서 전반을 담당한다"는 오해의 실질적 뿌리는
제거된다. 제안 2는 이름의 정확성을 위한 선택적 마감이다.

## 하지 않는 것

- A(검색/색인)와 B(소스매핑)를 분리하지 않는다 — 같은 협업문서 도메인이고 응집이
  높다. 쪼개면 인위적 경계만 늘어난다(YAGNI).
- 문서타입별 재편은 하지 않는다 — 타입 분기는 `parser/` 에 이미 올바르게 격리돼 있다.
