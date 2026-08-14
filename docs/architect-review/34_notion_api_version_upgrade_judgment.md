# 34. Notion API 버전 업그레이드 판단 (2022-06-28 → 2026-03-11)

- 대상: `app/services/documents/sources/notion_source.py`, `app/core/config.py`
- 질문: 지금 최신 버전(2026-03-11, data source 분리 모델)으로 올려야 하는가
- **권고: 지금은 올리지 않는다. 대신 진단 로그 ~6줄만 넣고, 아래 명시한 트리거가 발생하면 그때 올린다.**

## 1. 지금 당장 올려야 할 절박한 이유 — 없음

Notion 공식 문서 확인 결과:

- **구버전 지원 종료 계획이 없다.** "We don't currently have any plans to stop supporting older API versions."
  중단하게 되면 사전 공지 + 마이그레이션 기간을 준다고 명시.
- **종료 예정일이 공고된 적 없다.** "minimum versioning" 프로그램도 아직 도입되지 않았다.
- 단일 data source 데이터베이스에 대해서는 2022-06-28 요청이 **그대로 동작한다.**

즉 달력에 박힌 마감은 없다. 지금 올리는 비용과 6개월 뒤 올리는 비용이 같다.

주의: 공식 JS SDK 는 v5.0.0 에서 2022-06-28 지원을 끊었다. 우리는 SDK 를 쓰지 않고
`httpx` 로 REST 를 직접 호출하므로(`notion_source.py:15`) 이 제약을 받지 않는다.
SDK 를 도입하는 순간 얘기가 달라진다.

## 2. 안 올리면 쌓이는 리스크 — 마감이 아니라 지뢰

진짜 리스크는 일정이 아니라 **트리거**다.

> 2025년 9월 3일 이후, 우리 integration 이 연결된 워크스페이스에서 **누군가 데이터베이스에
> 두 번째 data source 를 추가하면**, 그 데이터베이스에 대해 `database_id` 는 더 이상 요청을
> 특정하기에 충분하지 않게 되고 `/databases/{id}/query` 가 실패한다.

성격:

- **우리가 통제할 수 없다.** 워크스페이스 사용자가 Notion UI 에서 클릭 한 번이면 발생한다.
- **전면 장애가 아니라 소스 1개짜리 부분 장애**다. 해당 프로젝트의 Notion 검색만 조용히 빈다.
- **현재 오류 메시지로는 진단이 불가능하다.** `_notion_error_message()`(`notion_source.py:275-287`)는
  400 을 별도 처리하지 않아 `notion request failed for /databases/xxx/query (status 400)` 만 남는다.
  Notion 이 응답 본문 `additional_data.child_data_source_ids` 로 정답을 알려주는데도 우리는 버린다.
- 시간이 갈수록 트리거 확률만 단조 증가한다(multi data source 는 신기능이라 사용률이 올라간다).

부수 리스크 하나 더 — **`DOCS_MCP_NOTION_VERSION` env 노브가 현재 함정이다.** 운영자가 이 값만
`2026-03-11` 로 바꾸면 헤더만 바뀌고 코드는 여전히 `/databases/` 를 호출해 오히려 멀쩡하던 것이 깨진다.
"버전은 env 로 조절 가능"이라는 외형이 실제로는 성립하지 않는다.

## 3. 올린다면 작업 범위 — 생각보다 작다 (반나절)

우리 코드가 실제로 쓰는 Notion 엔드포인트는 4개뿐이고, 그중 **깨지는 건 2곳**이다.

| 호출 | 위치 | 2026-03-11 영향 |
| --- | --- | --- |
| `POST /databases/{id}/query` | `notion_source.py:140` | **변경 필요** → `/data_sources/{id}/query` |
| `POST /databases/{id}/query` (child_database 순회) | `notion_source.py:218` | **변경 필요** (동일) |
| `POST /search` | `notion_source.py:141` | 영향 없음(page 필터 유지) |
| `GET /blocks/{id}/children` | `notion_source.py:245` | 영향 없음(변경된 건 append(POST) 쪽) |

작업 항목:

1. `_resolve_data_source_id(client, database_id)` 신규 — `GET /databases/{id}` 응답의 `data_sources[]`
   에서 id 를 꺼내고 인스턴스 dict 에 캐시. data source 가 2개 이상이면 첫 번째를 쓰고 warning.
   (진짜 멀티 data source fan-out 은 YAGNI — 필요해지면 그때.) **~20줄**
2. 위 2개 호출부 경로 교체. **2줄**
3. `DEFAULT_NOTION_VERSION`(`notion_source.py:26`) + `config.py:80` 기본값 + `.env.example` + `README`. **4곳**
4. 테스트: 기존 목이 `/databases/db-1/query` 경로를 하드코딩 중 —
   `tests/unit/test_document_sources.py:645`, `tests/unit/test_notion_page_source.py:312,339,383`
   4개 지점 수정 + 해석/캐시 테스트 신규. **이번 작업의 절반 이상이 여기다.**

DB 스키마 변경은 **불필요**하다. `project_source.location` 에 database_id 를 그대로 두고 런타임에
해석하면 된다. data_source_id 를 저장하는 쪽은 마이그레이션이 붙으므로 택하지 않는다.

버전을 올린다면 2025-09-03 을 경유하지 말고 **2026-03-11 로 직행**한다. 두 버전 사이의 차이
(markdown 지원, append block children 의 position 객체)는 전부 우리가 안 쓰는 쓰기 경로라
읽기 전용인 우리 표면에서는 동일하다. 마이그레이션을 두 번 할 이유가 없다.

## 4. 권고안

**지금 하는 것(작다):** `_notion_error_message()` 에 `/databases/` 경로의 400 분기 하나를 추가해
"data source 가 여러 개일 수 있다 + 34번 문서 참조"를 메시지에 남긴다. **~6줄 + 테스트 1개.**
지뢰를 밟았을 때 5분 만에 원인을 알게 하는 것이 목적이고, 그 이상은 하지 않는다.

**지금 안 하는 것:** 버전 업그레이드 자체. 공고된 마감이 없고, 지금 올리는 비용과 나중 비용이
같으며, 지금 올리면 멀쩡히 돌아가는 검색 경로를 건드리는 리스크만 산다.

**아래 중 하나라도 발생하면 즉시 3절 작업을 착수한다:**

- 위 400 진단 로그가 실제로 한 번이라도 찍힘 (= 지뢰 밟음, 그 시점엔 이미 필요)
- 팀이 쓰는 Notion 데이터베이스에 data source 를 추가할 계획이 생김
- Notion 이 구버전 종료를 공지함
- 공식 SDK 도입을 검토하게 됨 (SDK v5+ 는 2022-06-28 을 이미 끊었다)

## 출처

- https://developers.notion.com/reference/versioning
- https://developers.notion.com/docs/upgrade-faqs-2025-09-03
