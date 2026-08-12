# 18. openapi 테이블군 과설계 감사 (project_source 착수 전)

## 범위·방법

대상: `api_document / api_endpoint / api_parameter / api_request_body /
api_response / api_schema / api_section` (+ `api_chunk`, `document_sync_history`).

방법: `app/models`(정의) ↔ `app/repositories`·`app/services`·`app/mcp`(read/write
사용처) 3자 대조. "쓰는데 안 읽는" 컬럼/테이블(write-only), 과잉 정규화,
쿼리 패턴 대비 과한 인덱스/제약을 근거 기반으로 판정.

**결론 요약**: 과설계는 있다. 단 사용자가 의심한 형태(1:1 과잉 정규화)가
아니라 **write-only 죽은 무게** — 파싱·저장은 하는데 아무도 읽지 않는
테이블 1개 + 컬럼 2개다.

---

## 1) Dead fields/tables — 쓰기만 하고 읽지 않음 (제거 후보)

### ★ A. `document_sync_history` 테이블 전체 — write-only

- **쓰기**: `sync_service` 3곳(`_sync_history_repo.add(...)`, 라인 118/159/205)에서
  동기화 시도마다 append.
- **읽기**: **없음.** 저장소에 `list_by_document()` 메서드는 있으나
  **호출하는 곳이 전무**하고, 이 이력을 노출하는 MCP 도구도 없다.
- 결과: append-only 로 **무한 증식**하는데 조회 경로가 0. 전형적 YAGNI 산물
  ("나중에 이력 볼 일 있겠지").
- **판정**: 제거 1순위. 유지하려면 "이력 조회 도구를 실제로 붙일 계획"이
  전제여야 한다. 현재로선 죽은 무게.

### B. `api_endpoint.operation_id` 컬럼 — write-only

- **쓰기**: `indexer_service:131` (`operation_id=parsed.operation_id`), 파서가 채움.
- **읽기**: **없음.** payloads/types/EndpointDetails DTO/chunk_builder/검색 어디서도
  참조 안 함. (`grep operation_id` → indexer·parser 외 0건)
- **판정**: 제거 후보. 파서에서 뽑기만 하고 끝.

### C. `api_response.example_json` 컬럼 — write-only

- **쓰기**: `indexer_service:180` (`_to_response_entity` 에서 `entity.example = parsed.example`).
- **읽기**: **없음.** `_to_response` DTO 변환(라인 235~)이 example 을 매핑하지 않고,
  `ResponseDetails` 에 example 필드 자체가 없다. 응답 예시는 저장만 되고
  어디에도 안 나온다.
- **주의(대칭 아님)**: `api_request_body.example_json` 은 **살아있다** —
  `request_example_service._body_sample(body)` 가 `body.example` 를 읽어(라인 94)
  요청 예시 코드 생성에 쓴다. 죽은 건 **response 쪽 example 만.**
- **판정**: `api_response.example_json` 제거 후보. request_body 쪽은 유지.

### 살아있음 확인 (오탐 방지 차 기록)

- `raw_text`(sync diff + get_raw_document 도구), `version`/`content_hash`(문서목록·
  동기화 해시비교), `operation_id` 외 endpoint 필드 전부, `parameter.location`
  (예시 생성 path/query/header 분기), `schema.description`/`json_schema`
  (chunk_builder·schema_ref_resolver) — 전부 read 경로 있음.

---

## 2) 과잉 정규화 여부 — `api_request_body` 1:1 분리는 **정당** (유지)

`api_request_body` 는 endpoint 와 1:1(PK=endpoint_id)로 별도 테이블. 사용자가
"그냥 api_endpoint 컬럼으로 합쳐도 되지 않냐" 지적한 지점.

**판정: 분리 유지.** 근거:

- **nullable 1:1** — GET/DELETE 등 상당수 엔드포인트는 request_body 가 없다.
- **wide TEXT 컬럼** — `schema_json`(+ 현행 `example_json`, ①C 로 제거 대상)이
  큰 JSON 텍스트. 이를 endpoint 로 합치면 **body 없는 엔드포인트 행마다 NULL
  wide 컬럼 4개**가 붙어 행이 뚱뚱해진다(수직 분할의 교과서적 반대 사례).
- 합쳐서 얻는 건 "테이블 1개 감소 + 조인 1회 절약"뿐인데, 엔드포인트 상세는
  어차피 relationship 으로 한 번에 로드하므로 실이익 작다.

나머지(`api_parameter`/`api_response`/`api_schema`/`api_section`)는 전부 1:N —
정상 정규화, 과분할 아님.

---

## 3) 인덱스/제약 YAGNI — 과한 것 없음

| 인덱스/제약 | 쿼리 근거 | 판정 |
|---|---|---|
| `api_chunk` HNSW(vector) | `vector_search` 코어 | 필수 유지 |
| `api_chunk` GIN(text_tsv) | `keyword_search` FTS 코어 | 필수 유지 |
| `api_document` ix_project | `list_all`/`list_resyncable` project 필터 | 유지(저비용) |
| UNIQUE(document_id,method,path) | 재sync 멱등 upsert 불변식 | 유지(실제 제약) |
| UNIQUE(document_id,name) 스키마 | 스키마 이름 유일성 | 유지 |

- **과잉 인덱스 없음.** 벡터/FTS 인덱스는 검색 코어라 비용 정당.
- 참고(과설계 아님, 반대 방향): `document_sync_history.document_id` 엔 인덱스가
  없어 `list_by_document` 는 풀스캔+정렬이 될 수 있으나 — ①A 대로 읽는 곳이
  없어 무의미. 테이블을 남긴다면 인덱스 필요, 없앤다면 논점 소멸.

---

## 권고 (lead 결정용)

**정규화 구조는 손댈 것 없음**(1:1 분리 정당, 과분할·과인덱스 없음).
과설계의 실체는 write-only 죽은 무게 3건:

1. **`document_sync_history` 테이블 제거** — 조회 경로 0, 무한 증식. (유지하려면
   이력 조회 도구 추가가 전제)
2. **`api_endpoint.operation_id` 컬럼 제거**
3. **`api_response.example_json` 컬럼 제거** (request_body.example 은 유지)

셋 다 파서/indexer/sync_service 의 write 쪽 소량 수정 + 마이그레이션(drop).
**이미 예정된 `project_source` 마이그레이션 물결에 함께 태우면** 추가 비용 최소.
단 §각 항목은 독립적이므로 원하는 것만 취사선택 가능.

죽은 무게 3건 모두 "제거"가 기본 권고이나, `document_sync_history` 는
**향후 이력 노출 계획 여부**만 lead 가 확인해 주면 존치/제거 확정 가능.

---

## 4) `document_meta` ↔ `project_source` 통합 검토 — **통합 반대** (1:N, 별개 관심사)

질문: 둘 다 협업 문서 소스 관련이니 용도가 겹치는 것 아니냐, 합치는 게 낫냐.

코드로 확인한 실제 역할·데이터 흐름:

| | `project_source`(=현 project_drive/notion_source) | `document_meta` |
|---|---|---|
| 의미 | "이 프로젝트가 **어느 폴더/DB를 볼지**" 소스 등록 | 그 소스에서 **발견된 개별 문서**의 메타 캐시 |
| 카디널리티 | 프로젝트당 소수 (PK=project, 소스당 1행) | 프로젝트당 다수 (UNIQUE(project,source,external_id)) |
| 쓰기 트리거 | `register_drive/notion_source` MCP 도구 (사용자 수동 등록) | `refresh_index` 크롤 재조정 (`document_index_service`: 폴더 나열 → `list_by_project_source` 대조 → `add`/`delete`) |
| 읽기 소비처 | `project_source_resolver` — `folder_id`/`database_id`+`kind` 로 Drive/Notion 클라이언트 구성 | `document_search_service` — `search_by_tokens`(1단계 후보 필터), `find_latest_by_source_and_external_id`(get_document 포인트조회) |
| 생명주기 | **설정(config)** — 사용자가 지우기 전 영속 | **재생성 가능 캐시** — refresh 로 언제든 재구축, 본문 미보유 |

**판정: lead 의 1차 판단(카디널리티 다름, 1:N)이 코드로 정확히 확인됨. 통합 반대.**

- 관계가 명확한 **1:N (등록된 소스 1 → 그 소스에서 발견된 문서 N)**. 하나로
  합치면 "등록행 1 + 발견문서행 N"을 한 테이블에 섞어, 행 역할별로
  의미 없는 NULL 컬럼(등록행엔 external_id/title/url 무의미, 문서행엔
  folder_id 무의미)이 생긴다 — §1~2에서 반대한 것과 같은 유형의 실수.
- **관심사 분리도 깨진다**: 소스 등록은 사용자 설정(영속), meta 는 재생성 가능
  캐시. 성격·수명·쓰기 주체가 전부 다르다.
- **project_source 확장과도 상충**: project_source 엔 `source_type=openapi` 행이
  들어오는데(§project_source 설계), openapi 는 `document_meta` 대응물이 아예
  없다(api_document 트리로 감). 억지 통합 시 openapi 소스행만 meta 컬럼이
  통째로 뜨는 기형이 된다.

결론: `project_source`(등록/config) 와 `document_meta`(발견 문서 캐시)는
**별개 테이블 유지**가 맞다. 통합은 하지 않는다.
