# 62. `DocumentMetaFilter` 확장 설계 — owner / created_at / folderId / sharedWith

- 대상: `docs/architect-review/57_gdrive_search_logic_comparative_analysis.md` **5.5절 후속 항목**
- 선행 문서: 57번 5.4절(개선 #2 구현 결과 — 현행 필터 구조·규약이 전부 여기 있다)
- 상태: 설계(미구현). 구현 착수 전 8절의 승인 필요 결정 3건에 대한 lead 판단이 필요하다.

## 1. 범위와 결론 요약

5.5절이 남긴 네 항목을 셋으로 나눠 설계한다. 나누는 기준은 **필요한 운영 비용**이다 —
마이그레이션이 필요한가, 재동기화가 필요한가, 외부 API 를 더 호출해야 하는가.

| 단계 | 항목                                                | 마이그레이션 | 메타 재동기화                  | Drive API 추가 호출               | 권고                                                |
| ---- | --------------------------------------------------- | ------------ | ------------------------------ | --------------------------------- | --------------------------------------------------- |
| A    | `owner` 필터, `created_after`/`created_before` 필터 | 불필요       | 불필요                         | 없음                              | 즉시 착수                                           |
| B    | `folder_ids` 필터                                   | 컬럼 2개     | 필요(`index_bodies=False` 1회) | **없음**                          | A 직후 착수                                         |
| C    | `sharedWith` 필터                                   | 테이블 1개   | 필요                           | 조사 필요(옵션에 따라 문서당 1회) | 착수 전 수집 가능성 probe 필수, 항목12 와 통합 권고 |

핵심 판단 세 가지:

1. **Stage A 는 배선만 하면 된다.** 컬럼(`owner`/`created_at`)과 수집은 개선 #2 에서 이미 끝났다.
   단, 필터를 넣으면 **`owner` 를 응답에도 노출해야 한다** — `mime_type` 을 노출한 것과 똑같은
   이유다(호출자가 값을 모르면 필터를 쓸 수 없다). 개인정보 판단이 걸리므로 8절 결정 사항이다.
2. **Stage B 의 폴더 경로는 Drive 계층 순회 비용이 들지 않는다.** 5.5절은 "폴더 경로는 Drive
   계층 순회 비용이 걸려 별도 설계가 필요하다"고 적었지만, `GoogleDriveSource.list_files` 는
   **이미 BFS 로 전체 폴더 트리를 순회하고 있다**(`app/services/documents/sources/google_drive_source.py:202`).
   각 파일을 수집하는 시점에 그 파일의 조상 폴더 체인이 이미 손 안에 있다. 추가 API 호출 0,
   추가 지연 0 으로 조상 id 배열과 이름 경로를 채울 수 있다. 이 발견이 5.5절의 유보 사유를 없앤다.
3. **Stage C(sharedWith)만 진짜로 막혀 있다.** 막는 것은 설계가 아니라 **수집 가능성**이다 —
   Drive `files.list` 의 `permissions` 필드는 요청자가 그 파일을 공유할 수 있을 때만 반환되는데,
   이 서버의 서비스 계정은 뷰어로 공유받는 전제다(`google_drive_source.py:1` 모듈 docstring).
   설계는 5절에 다 적었으나, **probe 결과 없이 착수하면 안 된다**.

## 2. 현행 구조와 반드시 지켜야 할 규약

`app/repositories/document_filters.py` 의 `DocumentMetaFilter` 를 3 arm 이 공유한다.
확장하면서 깨면 안 되는 규약이 넷이다(전부 57번 5.4절에서 나온 것).

- **R1. 청크 조회문에는 JOIN 이 아니라 EXISTS.** `document_meta` 에 같은 `document_id` 행이 둘
  이상일 때 JOIN 은 청크 행을 증식시켜 순위를 조용히 망가뜨린다.
- **R2. `meta_filter=None` 또는 `is_empty()` 면 SQL 이 기존과 완전히 동일해야 한다.**
  엔드포인트 검색(`endpoint_candidate_search`)이 같은 저장소 메서드를 쓰기 때문이다.
- **R3. 새 컬럼은 `_apply_changes` 의 `is_changed` 판정에 절대 넣지 않는다.**
  (`app/services/documents/document_index_service.py:532` docstring 에 규약이 명시돼 있다.)
  넣으면 백필 첫 실행에서 전 문서가 NULL → 값 으로 바뀌며 `updated` 로 잡혀 본문을 전량 다시 받는다.
- **R4. 융합 후 파이썬 후처리로 필터를 걸지 않는다.** arm 당 후보 폭이 `width` 로 고정돼 있어
  후처리하면 필터가 셀수록 결과가 조용히 빈다.

추가로 이번 확장에서 새로 생기는 규약:

- **R5. NULL 행은 해당 필터가 지정되면 제외된다.** 날짜(3값 논리)·`mime_types`(Notion 은 NULL)와
  같은 규칙을 `owner`/`created_at`/폴더에도 그대로 적용한다. 소스별로 예외를 두면 "왜 이 필터만
  Notion 이 남지?"를 문서 없이는 아무도 설명할 수 없게 된다.

## 3. Stage A — `owner` / `created_at` 필터

### 3.1 필터 파라미터

`DocumentSearchOptions`(`app/services/documents/document_search_service.py:240`)에 3개 추가한다.

| 파라미터         | 타입                | 의미                                                                       |
| ---------------- | ------------------- | -------------------------------------------------------------------------- |
| `created_after`  | `str \| None`       | ISO8601. 생성 시각 >= (포함). `created_at` NULL 이면 제외                  |
| `created_before` | `str \| None`       | ISO8601. 생성 시각 <= (포함). `created_at` NULL 이면 제외                  |
| `owners`         | `list[str] \| None` | 소유자 정확 일치 OR(대소문자 무시). `owner` NULL(Notion·백필 전) 이면 제외 |

`DocumentMetaFilter` 는 대응 필드를 갖는다 — `created_after: datetime | None`,
`created_before: datetime | None`, `owners: tuple[str, ...]`.

파라미터 이름을 `owner`(단수)가 아니라 `owners`(복수 목록)로 잡은 이유: `mime_types` 와 형태를
맞춘다. "A 또는 B 가 쓴 문서"는 실제로 자주 나오는 질의고, 단수로 냈다가 복수로 넓히는 것은
MCP 도구 시그니처 변경이라 나중에 더 비싸다. 목록 1개짜리가 곧 단수다.

### 3.2 `owner` 매칭 규칙 — 대소문자 무시 정확 일치

`owner` 컬럼에는 Drive `owners[0].emailAddress` 가 우선 들어가고, 없을 때만 표시 이름이
들어간다(`_owner_from_raw`, `google_drive_source.py:414`). 세 가지 후보를 검토했다.

| 안                                                   | 판정                                                                                                                                                                                |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 정확 일치(`IN`)                                      | 이메일 대소문자가 다르면 조용히 0건. 외부 시스템에서 온 값이라 표기를 보장할 수 없다                                                                                                |
| **대소문자 무시 정확 일치(`lower(owner) IN (...)`)** | **채택**                                                                                                                                                                            |
| 부분 문자열(`ILIKE '%v%'`)                           | **반려**. 57번 5.3절이 제목에서 없앤 "경계 무시 매칭"을 필터에 다시 들이는 것이다. `owners=["kim"]` 이 `kimberly@`·`kim.j@`·`joakim@` 을 함께 무는 것은 hard filter 로서 부적절하다 |

`lower()` 를 씌우면 인덱스를 못 쓰지만 이 필터에 쓸 인덱스는 애초에 없고(3.6절), 필터가 걸리는
행 수는 title arm 은 trgm 후보로 이미 좁혀진 집합, keyword/vector arm 은 `document_id` 로 집은
1행이다. 비용은 무시 가능하다.

표시 이름으로 저장된 행은 이메일로 못 찾고 그 반대도 마찬가지다 — 데이터가 그렇게 생겼기
때문이며(한 컬럼에 두 종류의 값), 여기서 해결할 문제가 아니다. 도구 docstring 에 "응답의
`owner` 값을 그대로 복사해 넣으라"고 적어 우회한다.

### 3.3 응답 노출 — `owner` 를 내보내야 필터가 쓰인다

57번 5.4절은 `owner`/`created_at` 을 응답에 노출하지 않기로 했다. **Stage A 는 그 결정을
`owner` 에 한해 뒤집어야 한다.** 근거는 `mime_type` 을 노출한 것과 동일하다 — 호출자(Claude)가
결과에서 값을 보고 다음 질의를 좁히는 것이 이 필터들의 유일한 사용 경로인데, 값을 모르면
LLM 은 이메일을 추측해서 넣거나 필터를 아예 못 쓴다. 추가 SQL 은 0 이다(이미 읽어 온
`document_meta` 행에서 꺼낸다, `document_search_service.py:649`·`:806` 두 조립 지점).

- `owner`: **노출 권고**(8절 결정 1). 문서 소유자 이메일이 MCP 응답으로 나간다.
- `created_at`: **노출하지 않는다.** 필터는 절대 시각을 그대로 주면 되고, `modified_at` 이 이미
  최신성 판단용으로 나가고 있어 추가 노출의 실익이 없다. 필드가 늘면 응답 토큰만 는다.

개인정보 대안은 5.4절이 이미 적어 뒀다 — 문제가 되면 `_owner_from_raw` 를 표시 이름만 반환하게
바꾸고 컬럼을 재백필한다. 노출을 끄는 env 스위치는 만들지 않는다(YAGNI, 6절).

### 3.4 `is_empty()` 구조 수정 — 필드를 늘려도 어긋나지 않게

현행 `is_empty()` 는 필드를 나열해 검사한다. 여기에 필드를 추가하면서 한 줄이라도 빠뜨리면
**필터가 조용히 무시된다**(R2 게이트가 `is_empty()` 참이면 WHERE 를 아예 안 붙인다). 조건 목록을
만드는 함수가 이미 있으므로 거기서 파생시킨다.

```python
def is_empty(self) -> bool:
    """조건이 하나도 없으면 True(조건 생성 함수와 항상 일치한다)."""
    return not document_meta_conditions(self)
```

`document_meta_conditions` 가 `DocumentMeta` 컬럼 표현식만 만들고 DB 를 건드리지 않으므로 비용은
객체 몇 개다. 이후 필터 필드를 추가할 때 손댈 곳이 `document_meta_conditions` 한 곳으로 줄고,
"조건은 만드는데 `is_empty` 가 True" 같은 어긋남이 구조적으로 불가능해진다.
`document_meta_exists` 는 `document_id` 조건을 뒤에 덧붙이므로 영향 없다.

### 3.5 검증 규칙 (`_validate_meta_filter_options`)

기존 규칙에 다음을 더한다. 상수는 `mime_types` 와 같은 자리(모듈 상수)에 둔다.

- `created_after`/`created_before`: `_parse_filter_datetime` 재사용(ISO8601 파싱 실패 시
  `ValidationError`). `created_after > created_before` → `ValidationError`.
- `owners`: `None` 이면 통과. 빈 목록이면 `ValidationError`("must not be empty when provided").
  원소 수 `_MAX_OWNERS = 20` 초과 → 에러. 원소를 `strip()` 한 결과가 비었거나
  `_MAX_OWNER_LENGTH = 320`(컬럼 폭과 동일) 초과 → 에러.
- **날짜 축끼리 교차 검증은 하지 않는다.** `created_after` 와 `modified_before` 의 조합은
  "8월에 만들어졌고 9월 이전에 수정된" 처럼 정상 질의가 될 수 있다.

`_build_meta_filter` 는 `owners` 를 `tuple(o.strip() for o in ...)` 으로 정규화한다.
소문자 변환은 저장하지 않고 조건 생성 시점에 한다 — 필터 객체가 입력 표기를 그대로 들고 있어야
로그·에러 메시지가 사용자가 준 값과 일치한다.

### 3.6 인덱스 — 만들지 않는다

5.4절이 날짜·mime 전용 인덱스를 만들지 않은 근거가 그대로 적용된다. title arm 은 trgm 후보로
이미 좁혀진 행에, keyword/vector arm 은 `ix_document_meta_document_id` 로 집은 1행에 필터를
건다. `owner`·`created_at` 도 같은 자리에 걸리므로 전용 인덱스의 이득이 없다.

벡터 arm 의 `hnsw.ef_search` 하한 상향(100 → 200)은 `is_empty()` 기준으로 이미 걸려 있어
새 필터에도 자동 적용된다 — 추가 작업 없음.

### 3.7 Stage A 태스크

- **A1.** `DocumentMetaFilter` 에 `created_after`/`created_before`/`owners` 필드 추가,
  `document_meta_conditions` 에 조건 3개 추가(`owners` 는 `func.lower(DocumentMeta.owner).in_(...)`,
  값도 소문자로 변환해서 넘긴다), `is_empty()` 를 3.4절 형태로 교체.
- **A2.** `DocumentSearchOptions` 에 파라미터 3개 추가(주석에 NULL 제외 규칙 명시).
- **A3.** `_validate_meta_filter_options` 에 3.5절 검증 추가, `_MAX_OWNERS`/`_MAX_OWNER_LENGTH` 상수 추가.
- **A4.** `_build_meta_filter` 에 3개 매핑 추가.
- **A5.** `DocumentSearchItem.owner` 필드 추가 + 두 조립 지점(`:649`, `:806`)에서 `row.owner` 대입.
  `app/mcp/payloads.py` 의 `_to_document_search_payload` 와 `app/mcp/types.py` 의
  `DocumentSearchItemPayload` 에 `owner` 추가.
- **A6.** `search_documents` MCP 도구에 인자 3개 + docstring(Args/Returns) 갱신.
  Returns 절에 "owner 는 Drive 소유자 이메일 또는 표시 이름이며, 다음 질의를 owners 로 좁힐 때
  **응답 값을 그대로 복사해 넣으라**"를 명시한다(3.2절 매칭 규칙의 사용자 측 대응).
- **A7.** 테스트: (a) `owners` 대소문자 다른 입력이 매치, (b) `owner` NULL(Notion) 행이 `owners`
  지정 시 제외, (c) `created_at` NULL 행이 날짜 필터 지정 시 제외, (d) 경계 포함(>=/<=),
  (e) 검증 에러 4종, (f) **`meta_filter` 미지정 시 생성 SQL 이 기존과 동일**(R2 회귀).

Stage A 는 마이그레이션도 재동기화도 없다. 배포 즉시 동작한다(백필이 끝난 환경 기준 —
57번 5.4절 운영 순서를 아직 안 돌린 환경이면 `owner`/`created_at` 이 NULL 이라 필터가 전부
0건을 낸다. 이 전제는 도구 docstring 이 아니라 릴리스 노트에 적는다).

## 4. Stage B — `folder_ids` 필터

### 4.1 전제 재검토: 순회 비용은 이미 지불돼 있다

`list_files()` 는 `DOCS_MCP_DRIVE_FOLDER_ID` 를 루트로 BFS 하며, 자식이 폴더면 큐에 넣고 파일이면
수집한다. 즉 **파일 하나를 `collected` 에 넣는 순간, 그 파일이 어느 폴더에서 나왔는지와 그 폴더가
루트에서 어떤 경로로 도달됐는지를 이미 알고 있다.** 큐에 `(folder_id, ancestor_ids, name_path)` 를
함께 넣어 두면 되고, Drive API 호출은 1건도 늘지 않는다.

예외 1건: 루트 폴더 자신의 **이름**은 BFS 가 모른다(자식만 나열하므로). 이름을 얻으려면
`files.get(folderId, fields=name)` 1회가 더 필요한데, **그 호출을 하지 않기로 한다.** 이유는
비용이 아니라 테스트 표면이다 — 기존 `list_files` 테스트의 MockTransport 핸들러가
`request.url.params["q"]` 로 분기하고 있어(`tests/unit/test_document_sources.py:169`) 폴더 목록이
아닌 요청이 하나라도 늘면 관련 테스트가 전부 깨진다. 루트 이름은 사용자가 이미 아는 값
(`DOCS_MCP_DRIVE_FOLDER_ID` 로 직접 지정한 폴더)이라 경로에 넣을 실익도 낮다.

따라서 `folder_path` 는 **동기화 루트 기준 상대 경로**다. 루트 직속 파일은 빈 문자열(`""`),
루트/설계/2026 아래 파일은 `"설계/2026"`. `None` 과 `""` 는 다른 뜻이다 — `None` 은
"Notion 이거나 아직 백필 안 됨", `""` 는 "루트 직속"이다. `folder_ancestor_ids` 에는 루트 id 를
포함하므로 루트 id 로 필터하면 전체가 잡힌다(범위 전체 = 무필터와 같은 결과).

### 4.2 스키마 — 조상 id 배열 + 이름 경로 2컬럼

`document_meta` 에 nullable 컬럼 2개를 추가한다.

| 컬럼                  | 타입                 | 내용                                                                               | 용도                     |
| --------------------- | -------------------- | ---------------------------------------------------------------------------------- | ------------------------ |
| `folder_ancestor_ids` | `ARRAY(String(256))` | 루트부터 직계 부모까지의 폴더 id 목록(순서 유지)                                   | **필터 키**              |
| `folder_path`         | `String(2048)`       | 같은 체인의 폴더 **이름**을 `/` 로 이은 문자열(동기화 루트 제외, 루트 직속은 `""`) | 응답 노출·사람이 읽는 값 |

두 컬럼인 이유: 필터는 id 로 걸어야 안전하고(이름은 바뀌고 중복된다), 화면과 LLM 추론에는
이름이 필요하다. 직계 부모 id 는 `folder_ancestor_ids[-1]` 이므로 별도 컬럼을 두지 않는다.

**필터 표현식은 배열 중첩 연산자(`&&`)를 쓴다.**

```python
DocumentMeta.folder_ancestor_ids.overlap(list(f.folder_ids))
```

= "이 문서의 조상 폴더 중 하나라도 지정된 id 에 속하면 매치" = **자손 포함(descendant-inclusive)**
의미론. 사용자가 "설계 폴더 안 문서"라고 할 때 기대하는 것이 하위 폴더 포함이므로 이것이 기본값이다.
직계 부모만 거르는 변형은 만들지 않는다(6절).

대안으로 검토하고 **반려한 안: 구분자로 감싼 id 경로 문자열 + `LIKE '%/id/%'`.**

- Drive 폴더 id 는 `-` 와 **`_` 를 포함**한다. `_` 는 LIKE 의 단일 문자 와일드카드라 이스케이프를
  빠뜨리면 **엉뚱한 폴더가 조용히 매치된다**. 이스케이프를 정확히 하더라도 hard filter 를
  패턴 매칭으로 구현하는 것 자체가 57번 5.3절이 없앤 방향이다.
- 선행 와일드카드라 어차피 인덱스를 못 쓴다 — 배열 대비 이점이 없다.
- PostgreSQL 전용이 걸림돌이 아니다: 이 프로젝트는 이미 trgm·tsvector·pgvector 로 PostgreSQL 에
  고정돼 있다(`app/models/chunk.py` 가 `sqlalchemy.dialects.postgresql` 을 직접 import 한다).

인덱스는 만들지 않는다(3.6절과 같은 근거). GIN(`array_ops`)은 폴더 필터가 실제로 느리다는 측정이
나온 뒤에 붙인다.

### 4.3 소스별 값과 NULL 의미

- **Drive**: 위 규칙대로 채운다.
- **Notion**: 두 컬럼 모두 **NULL**. Notion 도 부모 페이지 체인을 순회하지만("폴더"가 아니라
  페이지다) 의미가 달라 같은 필드에 섞으면 `folder_path` 가 폴더 이름과 페이지 제목이 뒤섞인
  값이 된다. 배선 자체는 동일 구조로 가능하므로 필요해지면 그때 확장한다(6절).
- R5 에 따라 `folder_ids` 를 지정하면 Notion 문서와 백필 전 Drive 문서는 **항상 제외**된다.
  `mime_types` 와 같은 함정이므로 도구 docstring 과 릴리스 노트 양쪽에 적는다.

### 4.4 이 작업에서 드러나는 기존 결함 — 다중 부모 파일

Drive 파일은 (레거시 다중 부모·바로가기로) BFS 중 두 폴더에서 나올 수 있다. 그러면
`collected` 에 같은 `external_id` 가 두 번 들어간다. **이것은 폴더 작업 이전부터 있던 결함이다** —
`_stage_upsert` 는 루프 전에 읽어 둔 `existing` dict 만 보므로, 첫 번째 출현에서 신규 행을
`add()` 하고 두 번째 출현에서도 `existing` 에 없으니 또 `add()` 한다 →
`uq_document_meta_project_source_external` 위반으로 커밋이 통째로 실패한다.

폴더 컬럼을 넣으면 "어느 경로가 저장됐나"까지 비결정적이 되므로, 이번 작업에서 함께 고친다.

- `GoogleDriveSource.list_files` 에서 `external_id` 기준 **최초 방문 승리**로 중복을 제거한다.
  BFS 순서가 결정적이므로 결과도 결정적이다(루트에 가까운 경로가 이긴다).
- 로그: 중복이 실제로 있었으면 `_LOG.info` 로 건수만 남긴다(문서마다 warning 은 소음).
- 회귀 테스트: 같은 파일이 두 폴더에 있는 페이크 응답으로 `list_files` 가 1건만 반환하는지.

### 4.5 응답 노출

`DocumentSearchItem`/응답 payload 에 `folder_path`(이름 경로)만 추가한다. `folder_ancestor_ids` 는
노출하지 않는다 — LLM 이 폴더로 좁히려면 id 가 필요하지만, 결과마다 조상 id 배열을 통째로
실으면 응답 토큰이 크게 는다. **직계 부모 id 1개만** `folder_id` 필드로 파생해 내보낸다
(`folder_ancestor_ids[-1]`, 추가 SQL 0). 이 값으로 필터를 걸면 그 폴더와 그 하위가 잡힌다.

"이름으로 폴더를 지목하는" 경로(예: "설계 폴더에서 찾아줘")는 **여기서 풀지 않는다.**
그것은 다음 작업인 **항목1(people/folder intent)** 의 몫이고, 이 설계는 거기에 필요한 재료를
남겨 둔다 — `SELECT DISTINCT folder_path, folder_ancestor_ids FROM document_meta WHERE ...` 한 번이면
이름 → id 해석표가 나온다. 지금 이름 접두 필터를 만들면 항목1 에서 다시 갈아엎게 된다.

### 4.6 수집·저장 배선

- `FileMeta` 에 `folder_ancestor_ids: tuple[str, ...] = ()`, `folder_path: str | None = None` 추가
  (기본값을 줘서 Notion 어댑터는 무변경).
- `list_files` BFS 큐 원소를 `str` 에서 `(folder_id, ancestor_ids, name_path)` 로 바꾼다.
  `MAX_FOLDERS` 상한 로직은 그대로.
- `_new_row`/`_apply_changes` 에 두 필드 대입 추가. **R3 준수 — `is_changed` 판정에 넣지 않는다.**
  (파일이 폴더 간 이동해도 본문 재색인이 필요하지 않다. 폴더 이동은 본문을 바꾸지 않는다.)
- 마이그레이션: 컬럼 2개 추가만. 백필 UPDATE 없음(재동기화가 채운다).

### 4.7 운영 순서 (릴리스 노트 필수)

57번 5.4절과 같은 순서다: 마이그레이션 → 코드 배포 → 프로젝트·소스별
`refresh_index(index_bodies=False)` 1회 → 확인 질의
`SELECT source, count(*) FILTER (WHERE folder_ancestor_ids IS NULL), count(*) FROM document_meta GROUP BY source;`
(Drive 행의 NULL 이 0 이면 백필 완료. Notion 행은 전부 NULL 이 정상).
**백필 전에는 `folder_ids` 필터가 Drive 문서까지 전부 걸러낸다.**

R3 을 지키면 이 재동기화는 UPDATE 만 돌고 본문 재fetch 는 일어나지 않는다 — 그것이 R3 의 목적이다.

### 4.8 Stage B 태스크

- **B1.** 마이그레이션: `document_meta.folder_ancestor_ids`(`ARRAY(String(256))`, nullable),
  `document_meta.folder_path`(`String(2048)`, nullable).
- **B2.** `DocumentMeta` 모델에 두 컬럼 + Attributes docstring.
- **B3.** `FileMeta` 에 두 필드(기본값 포함) 추가.
- **B4.** `GoogleDriveSource.list_files` BFS 큐를 경로 동반 튜플로 변경, `_to_file_meta` 에 경로
  인자 전달(루트 이름은 조회하지 않는다 — 4.1절).
- **B5.** 4.4절 중복 제거(최초 방문 승리) + 회귀 테스트.
- **B6.** `_new_row`/`_apply_changes` 대입 추가(R3 준수 — `is_changed` 에 넣지 말 것).
- **B7.** `DocumentMetaFilter.folder_ids: tuple[str, ...]` + `overlap()` 조건 추가
  (`is_empty` 는 3.4절 수정으로 자동 반영).
- **B8.** `DocumentSearchOptions.folder_ids` + 검증(빈 목록 금지, 최대 20개, 원소 256자 이하)
  - `_build_meta_filter` 매핑.
- **B9.** 응답: `DocumentSearchItem.folder_path`/`folder_id`(파생) + payload/types + MCP 도구 인자·docstring.
- **B10.** 테스트: 자손 포함 매치(손자 문서가 조부모 id 로 걸림), Notion 제외, 백필 전 NULL 제외,
  `folder_ids` 미지정 시 SQL 무변경(R2), BFS 경로 조립 단위 테스트.

## 5. Stage C — `sharedWith` 필터 (착수 게이트 있음)

### 5.1 먼저 확인할 것 — 수집이 가능한가

Drive API v3 `files` 리소스의 `permissions` 필드는 **요청자가 그 파일을 공유할 수 있을 때만**
반환된다. 이 서버의 서비스 계정은 대상 폴더를 **뷰어로 공유**받는 전제이므로(모듈 docstring),
`permissions` 가 빈 값으로 오는 것이 유력하다. 이 한 가지 사실에 따라 설계가 갈리므로,
**구현 태스크가 아니라 probe 태스크가 먼저다.**

- **C0(probe).** 현재 자격증명으로 대상 폴더의 파일 1건에 대해
  `files.get?fields=id,permissions,shared,capabilities(canShare,canReadRevisions)` 를 호출해
  `permissions` 가 실제로 채워지는지 확인하고 결과를 기록한다. 코드 변경 없는 일회성 확인이다.
- 채워지면 옵션 A, 비면 옵션 B 로 간다. 둘 다 불가하면 **sharedWith 는 폐기**하고 그 사실을
  57번 5.5절에 적는다(계정 권한을 올리는 것은 별개 결정이다 — 편집자 권한을 요구하는 순간
  이 서버는 읽기 전용 도구가 아니게 된다).

**C0 결과(2026-08-27).** 지정 테스트 폴더의 15개 파일 모두 `permissions` 키가 반환되지 않았고,
`canShare=false`, `canReadRevisions=false`, `shared=true`, `ownedByMe=false` 였다. 따라서 이 서버의
서비스 계정=뷰어 전제에서는 **옵션 A 가 불가능함을 확인했다**. 먼저 보고된 반대 결과는 지정
코퍼스가 아니라 `anyoneWithLink=writer` 가 걸린 별도 코퍼스에서 나온 것으로 게이트 근거에서
제외한다. 후속 판정은 63번 문서에 따른다.

### 5.2 수집 옵션

| 옵션 | 방법                                                           | 비용                                                                                                                             | 판정                                   |
| ---- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| A    | `_LIST_FIELDS` 에 `permissions(emailAddress, role, type)` 추가 | API 호출 증가 0(응답 크기만 증가), 재동기화 1회                                                                                  | probe 가 통과하면 이것                 |
| B    | 문서마다 `permissions.list` 호출                               | **문서 수 N 만큼 호출 증가** — 지금까지 폴더 수에 비례하던 동기화가 문서 수에 비례하게 된다. rate limit 위험이 질적으로 달라진다 | 게이트 분기상 유일하지만 Stage C 단독 구현은 63번에서 반려 |

옵션 B 를 굳이 한다면 전체 동기화가 아니라 **명시적 옵트인**(예: `refresh_index(collect_permissions=True)`)
으로 분리해 기본 동기화 비용을 지금 그대로 둬야 한다.

### 5.3 데이터 모델과 필터

권한은 문서 1 : 다 N 이므로 컬럼이 아니라 테이블이다.

- `document_permission(id, document_meta_id FK ON DELETE CASCADE, principal String(320),
role String(32), principal_type String(16))`,
  `UNIQUE(document_meta_id, principal)`, `Index(principal)`.
- 필터 `shared_with: tuple[str, ...]` 는 `DocumentMeta` 기준 **EXISTS 서브쿼리**로 건다
  (`document_meta_conditions` 안에서 만든다). keyword/vector arm 에서는 기존
  `document_meta_exists` 안에 이 EXISTS 가 한 겹 더 들어가는 형태가 되는데, 안쪽은
  `document_meta_id` + `principal` 인덱스를 타는 포인트 조회라 문제되지 않는다. R1 에 따라
  여기서도 JOIN 은 쓰지 않는다.
- 매칭은 3.2절과 동일하게 대소문자 무시 정확 일치.
- 동기화 시 권한 집합 갱신은 문서당 delete-then-insert 가 아니라 **차집합 반영**으로 한다 —
  전량 삭제·재삽입은 매 refresh 마다 테이블을 통째로 다시 쓴다.
- `domain`/`anyone` 타입 권한(도메인 전체 공개)은 특정 이메일과 매칭되지 않는다.
  "전사 공개 문서"를 `shared_with` 로 찾을 수 없다는 뜻이며, 의미론으로 명시한다.

### 5.4 권고 — 항목12(permission/access)와 합쳐 처리

`sharedWith` 는 "누구와 공유됐는가"이고 항목12 는 "이 사용자가 접근할 수 있는가"다. **같은 데이터
(문서별 권한 주체 목록)를 쓰며, 항목12 는 그 데이터에 더 강한 요구(정확성·최신성)를 건다.**
필터용으로 먼저 얕게 만들어 두면 항목12 에서 스키마를 다시 뒤집을 가능성이 높다.

또 하나: 문서별 전체 ACL 을 DB 에 적재하는 것은 5.4절이 받아들인 "소유자 이메일 1건" 과는
**개인정보 규모가 다르다**. 전 직원의 문서 접근 관계가 이 DB 에 복제된다.

따라서 권고는 **C0 probe 만 지금 수행하고, 구현은 항목12 착수 시점으로 이관**이다(8절 결정 3).
C0 결과 후에는 Stage C 단독 옵션 B 도 반려했다. `sharedWith` 메타 필터는 이번 범위에서 종료하고,
항목12는 호출자 신원과 실효 권한을 함께 설계하는 별도 보안 트랙으로 다룬다(63번).

## 6. 하지 않기로 한 것 (YAGNI)

- `owner` 부분 문자열/접두 매칭 (3.2절 반려)
- `owner` 응답 노출을 끄는 env 스위치 — 노출할지 말지는 지금 한 번 결정할 문제지 런타임 옵션이 아니다
- 폴더 **직계 부모만** 거르는 변형 필터 — 자손 포함이 기대 동작이고, 직계 한정 요구가 실제로 나온 적 없다
- 폴더 **이름 접두** 필터 — 항목1(folder intent)의 몫이다(4.5절)
- Notion 부모 페이지 체인의 `folder_*` 채움 (4.3절)
- `created_at` 응답 노출 (3.3절)
- `owner`/`created_at`/`folder_ancestor_ids` 전용 인덱스 (3.6절·4.2절)

## 7. 순서와 의존성

```
A (배선만, 마이그레이션·재동기화 없음)
└─ A1 필터 → A2~A4 옵션·검증 → A5 응답 → A6 도구 → A7 테스트

B (마이그레이션 + 메타 재동기화 1회, Drive API 추가 호출 0)
└─ B1~B2 스키마 → B3~B6 수집·저장(B5 중복 제거 포함) → B7~B9 필터·응답 → B10 테스트
   * A1 의 is_empty 구조 수정에 의존한다(A 선행)

C (게이트 종료)
└─ C0 probe → 옵션 A 실패 → 옵션 B 단독 구현 반려 → sharedWith 범위 종료
```

Stage A 와 B 는 배포 단위를 분리한다. B 는 재동기화가 완료돼야 필터가 의미를 갖는데, A 는
그렇지 않으므로 묶으면 A 까지 재동기화 대기에 잡힌다.

## 8. lead 승인이 필요한 결정

1. **`owner` 응답 노출**(3.3절). 문서 소유자 이메일이 MCP 응답으로 LLM 클라이언트에 나간다.
   노출하지 않으면 `owners` 필터는 사실상 쓸 수 없다. 권고: 노출.
   반대 시 대안은 표시 이름만 저장하도록 `_owner_from_raw` 를 바꾸고 재백필(5.4절 기재 대안).
2. **Stage B 의 재동기화 1회 수행 시점**(4.7절). Drive 본문 재fetch 는 없으나 전 문서 UPDATE 가
   돈다. 운영 창이 필요하면 알려 달라.
3. **Stage C 를 항목12 로 이관**(5.4절). 지금은 C0 probe 만 수행. 이견 있으면 Stage B 직후
   단독 착수도 가능하지만 스키마 재작업 위험을 안는다.

## 9. 승인 기록 (2026-08-26)

lead 승인 완료.

1. `owner` 응답 노출 = **예**.
2. Stage B 재동기화 = **구현 직후 즉시** 수행.
3. `sharedWith` = **항목12 로 이관**, 이번 범위에서는 C0 probe 만. C0 결과 옵션 A 실패,
   옵션 B 단독 구현도 반려하여 Stage C 는 종료한다. 항목12는 별도 보안 설계로 재개한다(63번).

구현 계획: `docs/superpowers/plans/2026-08-26-meta-filter-owner-created-folder.md`
(Stage A/B 만 포함. Stage C 는 계획에서 제외).
