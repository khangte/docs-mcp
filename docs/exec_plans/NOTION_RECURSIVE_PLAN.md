# Notion 페이지 하위 트리 재귀 색인 설계안

- 목표: `register_notion_page` 로 등록한 허브 페이지의 하위 트리 **전체**를 색인(현재는 바로 아래 1단계만 → 3~4단계 아래 트러블슈팅 문서가 0건).
- 원칙(ponytail): `list_pages` 의 page 분기(`_list_child_pages`) 내부만 재귀화. 도구 시그니처·스키마(kind 컬럼)·`fetch`·database 분기 전부 무변경.

## 배경 확인 (실코드)

- `_list_child_pages`(`notion_source.py:177`) 는 `_list_children(page_id)` 로 바로 아래 블록을 받아 `type=="child_page"` 만 FileMeta 화, **재귀 없음**(라인 180 주석이 "후속 스코프" 라 명시).
- `child_page` 블록의 `id` = 그 하위 페이지의 page id → 다시 `_list_children` 대상이 될 수 있다. 즉 "child_page 발견 → FileMeta 1건 수집 + 그 id 로 재귀" 가 자연스럽다.
- 본문용 `_collect_block_text`(라인 161) 가 이미 `MAX_BLOCK_DEPTH=4`/`MAX_BLOCKS=2000` 상한 + depth 증가 재귀 패턴을 보유 → 목록화도 동일 패턴 복제.

## 핵심 결정

**결정 1 — `_list_child_pages` 를 깊이 우선 재귀로 교체. child_page 만 재귀 대상.**
- 발견한 모든 child_page 를 **평탄한 리스트**로 수집(트리 구조 보존 안 함 — 각 하위 페이지는 독립 문서 1건). 각 child_page 는 "FileMeta 1건이면서 동시에 더 깊이 탐색할 부모".
- 재귀는 `child_page` 를 통해서만 내려간다(page→subpage→subsubpage). 텍스트/토글 블록 안에 중첩된 child_page 까지 쫓지 않는다 → API 호출 폭증 방지. 그 극단 케이스는 미스코프(아래).

**결정 2 — 상한 2개 신설: `MAX_PAGE_DEPTH`·`MAX_PAGES`.**
- `MAX_PAGE_DEPTH = 4` (본문 `MAX_BLOCK_DEPTH` 와 동일값 채택 — 허브 3~4단계 요구를 덮음). depth 0 = 등록한 허브 자신의 직속 자식.
- `MAX_PAGES = 500` (신설). 한 허브가 끌어오는 문서 수 상한. 상한 도달 시 `_collect_block_text` 와 동일하게 `_LOG.warning` 후 조기 중단(부분 결과 반환, 예외 아님 — 기존 부분 실패 허용 정책과 일관).
- depth 초과·MAX_PAGES 도달 둘 다 조용히 그 가지만 잘라낸다(수집된 것은 유지).

**결정 3 — 순환/중복 방어: `visited: set[str]` page id.**
- child_page id 를 visited 에 넣고, 이미 본 id 는 FileMeta 수집도 재귀도 건너뛴다. Notion 에서 하위 페이지 순환은 드물지만, 같은 페이지가 두 부모에 링크된 중복 수집도 함께 막아 `document_meta` UNIQUE(project,source,external_id) 중복 upsert 낭비를 줄인다.

**결정 4 — 1단계-only 모드는 두지 않는다.**
- 사용자 요구가 "허브 하나로 트리 전체 검색". 1단계만 원하는 유스케이스 없음 → 플래그·파라미터 추가 없이 그냥 재귀로 교체(YAGNI). depth 상한이 안전판. 하위호환은 상한 안에서 1단계 결과가 재귀 결과의 부분집합이라 자연 보존.

**결정 5 — 성능: 재귀 페이지 수만큼 `/blocks/{id}/children` 호출.**
- N개 하위 페이지 = 최대 N회 children 호출(+페이지네이션). `MAX_PAGES` 가 절대 상한이자 호출 상한. depth·visited 로 폭증/무한 차단. `_client()` 는 `list_pages` 진입 시 1회 열어 재귀 전체가 공유(현재도 라인 94 에서 1개 client 로 감쌈 — 유지).

## 변경 지점 / developer 파일 목록

- 수정 `app/services/documents/notion_source.py`:
  - 상수 추가: `MAX_PAGE_DEPTH = 4`, `MAX_PAGES = 500`(라인 30~32 상한 블록 옆).
  - `_list_child_pages(client, page_id)` 를 재귀 시그니처로 교체 →
    `_collect_child_pages(client, page_id, acc: list[FileMeta], visited: set[str], depth: int)`.
    로직: depth > MAX_PAGE_DEPTH or len(acc) >= MAX_PAGES 면 return(후자는 warning);
    `_list_children(page_id)` 순회하며 `type=="child_page"` 이고 id 가 visited 에 없으면 →
    visited.add(id), acc.append(_child_page_to_file_meta(block)), 상한 확인, `_collect_child_pages(id, acc, visited, depth+1)` 재귀.
  - `list_pages` 의 page 분기(라인 93~95): `acc=[]; visited=set(); self._collect_child_pages(client, self._page_id, acc, visited, 0); return acc`. 허브 자신은 문서로 넣지 않음(현행 유지).
  - `_child_page_to_file_meta`(라인 277) 재사용, 변경 없음.
- 신규/보강 테스트 `tests/unit/test_notion_page_source.py`:
  - 3~4단계 중첩 child_page 트리(페이크 `_list_children` 응답) → 전 하위 페이지가 평탄 목록으로 수집.
  - `MAX_PAGE_DEPTH` 초과 가지는 잘리고 상한 내 페이지는 유지.
  - `MAX_PAGES` 도달 시 부분 결과 + warning, 예외 아님.
  - 순환(A→B→A) 이 무한 루프 없이 A,B 각 1건.
  - 기존 1단계 트리 회귀(하위호환): 자식이 child_page 뿐이면 결과 동일.

## 미스코프(후속)
- 텍스트/토글/컬럼 등 비-child_page 블록 안에 중첩된 child_page 추적.
- 트리 계층 메타(부모 경로)를 FileMeta 에 보존.
- 데이터베이스 안의 페이지를 다시 허브로 재귀.
