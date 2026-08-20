# 17. DB 스키마 결합 구조 재검토 — openapi FK CASCADE vs drive/notion 느슨결합

## 배경

사용자 지적:

1. openapi / drive / notion 은 전부 "검색 대상(수집 소스)"인데,
   openapi 만 `api_document` 아래로 FK CASCADE 강결합이고 drive/notion 은
   `project` 문자열로만 느슨결합돼 있어 **구조적으로 불일치**한다.
2. 전체를 느슨결합으로 **통일**하고 싶다.

developer 조사 결과(`.team/_runtime/db_schema_report.md`)를 근거로 현재 구조를
검토하고 설계 방향을 제시한다. (실제 마이그레이션 작성은 범위 밖.)

---

## 1. 핵심 진단 — "불일치"는 서로 다른 두 관계를 하나로 착각한 것

현재 스키마의 결합은 **한 종류가 아니라 두 축**이다. 사용자가 본 "불일치"는
같은 관계를 두 방식으로 처리한 게 아니라, **애초에 성격이 다른 두 관계**를
나란히 놓고 비교한 결과다.

### 축 A — 소스 → 프로젝트 (테넌시/그룹핑)

| 테이블 | project 참조 방식 | FK |
|---|---|---|
| `api_document` | `project` varchar 컬럼 | 없음 |
| `document_meta` | `project` varchar 컬럼 | 없음 |
| `project_drive_source` | `project` PK | 없음 |
| `project_notion_source` | `project` PK | 없음 |

**이 축은 이미 세 소스 전부 동일하게 느슨결합이다.** `project` 테이블 자체가
없고(확인함), 전부 문자열 참조·FK 없음. 여기엔 불일치가 없다.

### 축 B — 문서 → 그 문서를 분해한 하위 부품 (합성/애그리게잇)

`api_document` → `api_endpoint` / `api_parameter` / `api_request_body` /
`api_response` / `api_schema` / `api_section` / `api_chunk` /
`document_sync_history` 의 FK CASCADE.

이건 **"소스 연결"이 아니다.** 하나의 수집된 문서를 파싱해 쪼갠 **부품**이며,
부모 없이는 존재 의미가 없다:

- `api_parameter` 는 자기 `api_endpoint` 없이 무의미
- `api_chunk`(검색 코어)는 자기 `api_document` 없이 무의미

즉 축 B의 FK CASCADE는 **문서 내부 합성 구조(composition)**이지, 축 A의
소스-프로젝트 링크와 같은 층위가 아니다.

### 그래서 drive/notion 에 FK CASCADE 가 "없는" 진짜 이유

불일치가 아니라 **수집 전략이 근본적으로 다르기 때문**이다:

| | openapi | drive / notion |
|---|---|---|
| 본문 저장 | O (`raw_text`) | X (검색 시점 원본 실시간 fetch) |
| 구조 분해 | O (endpoint/param/response/schema/section) | X |
| 벡터화·FTS | O (`api_chunk`) | X (메타데이터만) |
| 하위 부품 존재 | 있음 | **없음** |

drive/notion 은 애초에 **쪼갤 부품이 없다.** `document_meta` 는 제목·URL·수정일
캐시일 뿐이다. FK CASCADE 로 묶을 대상 자체가 없으니, "openapi 처럼 강결합돼
있지 않다"가 아니라 **묶을 게 없어서 안 묶은 것**이다.

---

## 2. 사용자 요청안(전량 느슨결합 통일) 평가 — **반대**

"FK CASCADE 를 걷어내 openapi 하위 부품을 drive/notion 과 같은 층위로
재배치하고 문자열 참조로 통일한다"를 그대로 하면:

- **검색 코어(`api_chunk`) 참조 무결성 상실** — 고아 청크/엔드포인트가 앱 버그로
  언제든 생김. 지금은 DB가 막아준다.
- **삭제·재동기화 원자성 상실** — 문서 삭제/재sync 교체 시 지금은 CASCADE 한 방.
  느슨결합화하면 7개 자식 테이블을 앱 코드가 수동 순차 삭제 → 누락 시 유령 데이터.
- **얻는 이득 없음** — drive/notion 엔 대응 부품이 없으므로, 이 변경은
  "불일치를 없애는" 게 아니라 **한쪽에만 있는 올바른 합성 FK 를 강등**하는 것.

결론: 축 B의 FK CASCADE 는 제거 대상이 아니라 **유지해야 할 올바른 설계**다.
KISS/무결성 관점 모두에서 손대면 손해다.

---

## 3. 실재하는 진짜 비대칭 — 통일 가치가 있는 지점

그럼에도 사용자가 감지한 이질감의 **실체가 하나 있다**: **"소스 등록(source
registry)"이라는 통일된 개념이 없다.**

| 소스 | 등록 방식 | 수집 산출물 |
|---|---|---|
| drive | `project_drive_source`(folder 매핑) | `document_meta` 캐시 |
| notion | `project_notion_source`(db/page 매핑) | `document_meta` 캐시 |
| openapi | **전용 등록 테이블 없음** — 소스가 곧 `api_document`(`source_url` 컬럼) | 자기 자신(ingest 트리) |

- drive/notion 은 "소스 등록 테이블 + 메타 캐시"의 2층 구조인데,
- openapi 는 등록 테이블이 따로 없고 `api_document` 행 자체가 소스이자 산출물.

게다가 `project_drive_source` 와 `project_notion_source` 는 **거의 동일한
테이블**(project PK + 값 컬럼 1개 + 타임스탬프)이라, 저장소 코드가 이미
`ProjectSourceRepositoryBase` 제네릭+상속으로 중복을 우회하고 있다
(`app/repositories/project_source_repository.py`). 즉 **모델 층의 DRY 위반을
코드 층에서 메꾸는 중.**

---

## 4. 권고 옵션

### 옵션 C (권고 1순위) — 현행 유지

구체적 통증(버그·쿼리 난이도·성능 저하)이 보고된 바 없다. 두 축 모두 이미
각자 올바르다(축 A 느슨결합 일관, 축 B 합성 FK 정당). 사용자가 본 "불일치"는
표면적 명명·멘탈모델 문제이지 설계 결함이 아니다. **가장 싸고, 아마 옳다.**

이 문서 §1 을 `docs/adr/` 에 한 줄 근거로 남겨 "왜 두 결합이 공존하는가"를
못박아 두면, 같은 질문 재발을 막는다.

### 옵션 B (구조 통일을 굳이 원하면) — 소스 등록 층만 단일화

축 B(ingest 트리 + FK CASCADE)는 **손대지 않고**, 축 A의 소스 등록만 통일:

- `project_drive_source` + `project_notion_source` → 단일 `project_source` 테이블로 병합
  - 컬럼(안): `project`, `source_type`{openapi|drive|notion}, `location`(folder_id
    /database_id/page_id/source_url), `kind`(notion 의 database|page 등), `created_at`, `updated_at`
  - PK: `(project, source_type)` 또는 `(project, source_type, location)` — 한 프로젝트가
    같은 타입 소스를 복수 가질지에 따라 결정(현재 drive/notion 은 프로젝트당 1개 전제)
- openapi 소스도 이 테이블에 1급 등록 → 세 소스가 **동일 방식으로 등록**.
  `api_document` 트리는 그대로 "openapi 소스의 수집 산출물"로 남음.
- 저장소의 제네릭+상속 우회가 단일 테이블 CRUD 로 정리됨(진짜 DRY 이득).

**이득**: 소스 층 구조 일관 + 모델 중복 제거. **비용**: 마이그레이션(테이블 병합·
데이터 이전) + `project_source_repository`/`sources.py`/composition 개편.
**주의**: `document_meta`(메타 캐시)와 `api_document`(ingest 트리)의 이질성은
**그대로 남는다** — 수집 전략이 실제로 다르기 때문이며, 이걸 억지로 합치는 건
옵션 A 와 같은 실수다.

### 옵션 A (사용자 원안 그대로) — **비권고**

§2 사유로 반대. 무결성 상실 대비 이득 없음.

---

## 5. lead 결정 요청

1. **옵션 C(현행 유지 + ADR 한 줄)** 로 종결할지,
2. **옵션 B(소스 등록 층만 `project_source` 단일화)** 를 진행할지 —
   진행 시 "프로젝트당 소스 복수 허용 여부"만 확정해 주면 PK/스키마 확정 가능.

옵션 A(전량 FK 제거)는 아키텍트 판단상 반대이며, 그 근거는 §1–§2.
