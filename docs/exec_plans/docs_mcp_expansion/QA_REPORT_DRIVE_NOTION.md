# QA 검수 보고서 — Drive/Notion 문서 검색 (SPEC 기능 5~8 + 기능 9 도구 등록)

- 검수 대상: 검수 당시 `/home/kang/projects/docs-mcp-expansion` (브랜치
  `feat/docs-mcp-expansion`). 이후 해당 워크트리는 제거됐고 작업분은
  `refactor/260727`에 머지됐다.
- 관련 커밋: `a2f3b71`, `eeb3afd`, `d9c2a24`, `7c61017` (+ `46e70f8` 에 섞여 들어간 MCP 배선분)
- 검수 범위: SPEC 기능 5~8, 기능 9 중 Drive/Notion 도구 등록
- 범위 외: 기능 1~4 (OpenAPI 트랙, `QA_REPORT.md` 에서 이미 검수 완료)
- 검수 방식: 코드 정독 + 정적 검사 + 전체 테스트 실행 + **동적 프로브 3건 직접 작성·실행**

---

**전체 판정**: 조건부 합격
**가중 점수**: 6.9 / 10.0

**항목별 점수**:
- 기능 정확성: 7/10 — 기능 5·7·8·9 는 충실히 구현됐으나, **기능 6의 "부분 실패 허용" 검증 기준을 실제로는 충족하지 못한다**(프로브로 증명).
- 코드 품질: 8/10 — 신규 파일 lint/type 위반 0건, 책임 분리와 한국어 docstring 양호. 다만 미구성 상태의 사용자 피드백 설계가 부실하고 README 가 실제 동작과 어긋난다.
- 성능: 6/10 — `top_k` 절단을 1단계에서 수행한 점은 정확하나, 1단계 후보 압축이 **`document_meta` 전체 행을 Python 으로 적재**하는 O(N) 구조다.
- 테스트 커버리지: 7/10 — 345건 전체 통과, 공허 참(vacuous truth) 결함 없음. 그러나 **가장 중요한 SPEC 검증 기준(부분 실패)을 검증하지 않는 테스트**가 통과하고 있다.

> 판정 근거: 가중 점수 6.9 는 조건부 합격 구간(5.0~6.9)이다. 기능 정확성·테스트
> 커버리지 모두 4점 초과이므로 무조건 불합격 조건에는 해당하지 않는다.

---

## 1. SPEC 기능 체크

### [PASS] 기능 5: Drive/Notion 소스 어댑터

| SPEC 검증 기준 | 결과 | 확인 내용 |
|---|---|---|
| 지정 폴더(및 하위)의 파일만 `list_files()` 에 나타남 | PASS | `google_drive_source.py:159` `list_files()` 가 `q="'{folder_id}' in parents"` 로 BFS 재귀 탐색. `MAX_FOLDERS=500` 상한과 `visited` 집합으로 순환 방지. `test_drive_list_files_recurses_into_subfolders` 가 폴더 자체 제외까지 단언. |
| 공유되지 않은 파일은 응답에 없음(Google 측 보장) | PASS | 서버가 별도 필터링하지 않음 — SPEC 의도와 일치. |
| Notion 은 지정 워크스페이스/DB 하위만 | PASS | `notion_source.py:114` `_list_request_spec()` 가 `database_id` 유무로 `/databases/{id}/query` 와 `/search` 를 분기. |
| 없는 `external_id` → `IntegrationError` | PASS | 404 를 `_notion_error_message`/`_drive_error_message` 가 "document not found" 로 변환. |
| 인증 실패는 스택트레이스 없이 `IntegrationError` | PASS | **프로브 3으로 직접 검증** (아래 §3 참조). 401/403/429 모두 매핑됨. |
| 동일 `external_id` 반복 fetch 결정성 | PASS | 무상태 구현. |

추가 확인:
- **`google-api-python-client` 미사용 확인**: `grep` 결과 `google.oauth2.service_account` 와 `google.auth.transport.requests` 만 사용(둘 다 `google-auth` 패키지). Drive REST 호출은 전부 `httpx`. **SPEC 제약 준수.**
- **신규 의존성**: `pyproject.toml` 에 `google-auth>=2.30` **하나만** 추가됨. 그 외 신규 의존성 없음. **준수.**
- **`DocumentSource` Protocol 만 참조**: 서비스 계층(`document_search_service.py`, `document_index_service.py`)이 구체 SDK 를 import 하지 않음. **준수.**
- 지연 import(`_load_credentials` 내부)로 Drive 미사용 환경의 로딩 비용을 회피한 점은 적절.

### [FAIL] 기능 6: 메타데이터 캐시 및 갱신

| SPEC 검증 기준 | 결과 | 확인 내용 |
|---|---|---|
| 신규 파일 → `added` 집계 + 신규 행 | PASS | `test_new_files_are_counted_as_added` 가 집계와 실제 행 존재를 함께 단언. |
| 삭제된 파일 → 캐시 제거 + `removed` 집계 | PASS | `_refresh_source` 의 `seen` 집합 차집합으로 삭제. |
| `modified_at` 동일 시 `updated` 미포함 | PASS | `_apply_changes()` 가 변경 없으면 `last_synced_at` 만 갱신 후 `False` 반환. **타임존 문제 없음**(아래 상세). |
| **갱신 중 예외가 나도 이미 처리된 행은 커밋** | **FAIL** | **프로브 2로 반증** — 소스 내부 중간 실패 시 **커밋된 행이 0건**. |

#### FAIL 상세: 부분 실패 허용이 source 경계에서만 성립한다

Generator 는 자기보고 2번에서 "부분 실패 허용을 source별 커밋 경계로 구현"
했다고 밝혔다. 이 구현은 **source 가 2개 이상일 때만** SPEC 을 충족한다.
source 가 1개(예: Drive 만 설정한 팀)이거나, 한 source 를 처리하는 **도중**
실패하면 SPEC 문구를 충족하지 못한다.

`document_index_service.py:135` `_refresh_source()` 는 remote 파일 전체를 루프
돌며 `add`/`_apply_changes`/`delete` 를 수행한 뒤 **맨 마지막 줄(`:160`)에서
단 한 번 `self._session.commit()`** 을 호출한다. 루프 도중 예외가 나면 커밋에
도달하지 못하고, `refresh()` 의 `except IntegrationError` 절(`:98`)이
`self._session.rollback()` 을 호출해 **그 source 에서 처리한 모든 행이 통째로
사라진다.**

직접 작성한 프로브(파일 5건 중 3번째 `add` 에서 `IntegrationError` 주입)의 실행 결과:

```
PROBE2 raised='failed to refresh every document source: drive' committed_rows=0 ids=[]
```

SPEC 기능 6 은 "갱신 중 예외가 나도 **이미 처리된 행은 커밋되어 있고**, 실패한
항목만 다음 갱신에서 재시도 가능하다(부분 실패 허용)"라고 명시한다. 처리에
성공한 2개 행조차 남지 않으므로 **SPEC 위반**이다.

기존 테스트 `test_partial_failure_commits_already_processed_source`(`:178`)는
`fake_notion_source.list_should_fail = True` 로 **`list_files()` 단계에서**
실패시킨다. 이 시점에는 해당 source 가 아직 아무 행도 건드리지 않았으므로,
이 테스트는 "source 간 격리"만 검증할 뿐 **"한 source 내부의 부분 실패"는
전혀 검증하지 않는다.** 테스트가 통과하는데 SPEC 은 위반인 상태다.

#### 안전 확인: 교차 소스 삭제 버그는 없음

지시받은 위험 시나리오(특정 source 갱신 시 다른 source 행까지 삭제)를
프로브 1로 검증했다.

```
PROBE1 removed=1 notion_alive=True
```

`_refresh_source()` 가 `self._meta_repo.list_by_source(source_name)` 로
**해당 source 로 한정한 행만** 로드해 차집합을 계산하므로 안전하다.
`document_meta_repository.py:32` `list_by_source()` 에 `WHERE source = :source`
가 정확히 걸려 있다. **버그 없음.**

다만 이 안전성을 고정하는 회귀 테스트가 없다. `test_source_filter_refreshes_only_that_source`
(`:147`)는 "notion 이 아직 갱신된 적 없는" 상태만 확인하며, **"notion 이 이미
캐시된 상태에서 drive 만 갱신"** 하는 위험 시나리오를 다루지 않는다.

#### 안전 확인: `modified_at` 타임존 처리는 정확함

naive/aware 혼용으로 비교가 항상 참/거짓이 되는 결함을 우려했으나, 처리가 정확하다.
`time_parsing.py:16` `parse_rfc3339()` 가 `Z`/오프셋을 모두 UTC 로 정규화한 뒤
`.replace(tzinfo=None)` 으로 **tz-naive 로 통일**한다. `DocumentMeta.modified_at`
컬럼도 `DateTime`(timezone 미포함)이고, `_refresh_source` 의 `now` 역시
`datetime.now(timezone.utc).replace(tzinfo=None)` 로 동일 규약을 지킨다.
`test_parse_rfc3339_normalizes_offset` 이 `+09:00` → UTC 변환을 단언한다.
**결함 없음.**

### [PASS] 기능 7: Drive/Notion 문서 검색 (2단계 후보 압축)

| SPEC 검증 기준 | 결과 | 확인 내용 |
|---|---|---|
| 제목에 쿼리 단어 포함 문서는 1단계 후보에 반드시 포함 | PASS | `test_title_match_document_is_included` 가 결과 리스트를 정확히 단언(`== ["로그인 인증 설계서"]`). |
| 1단계 후보 0건 → 본문 fetch 없이 빈 리스트 | PASS | **양방향 모두 검증됨** (아래 상세). |
| 한 번의 검색에서 fetch 수 ≤ `top_k` | PASS | **후보 초과 상황을 실제로 만들어 검증** (아래 상세). |
| `source` 필터 시 모든 항목이 해당 source | PASS | 비어있지 않은 결과에 대해 검증(아래 상세). |
| 캐시에 없는 신규 문서는 미검색(제약 문서화) | PASS | README + docstring 명시, `test_search_documents_before_refresh_returns_empty` 로 계약 고정. |

- **`top_k` 절단 위치(자기보고 1번) — 타당함.** `_select_candidates()`(`:156`)가
  `scored[: options.top_k]` 로 **1단계에서** 자른 뒤 `_rank_with_body()` 에
  넘긴다. 2단계에서 잘랐다면 fetch-then-discard 가 되어 "fetch 수 ≤ top_k"
  불변식이 깨졌을 것이다. 판단이 정확하다.
- 동점 시 `external_id` 2차 정렬로 후보 집합의 결정성을 확보한 점도 적절
  (`test_results_are_deterministic` 이 점수까지 단언).
- 개별 문서 fetch 실패를 건너뛰는 처리(`:193`)로 한 건의 권한 오류가 검색
  전체를 죽이지 않게 한 것은 좋은 설계.

### [PASS] 기능 8: Drive/Notion 문서 원문 조회

| SPEC 검증 기준 | 결과 | 확인 내용 |
|---|---|---|
| 없는 `external_id` → `IntegrationError` 페이로드 | PASS | `test_get_document_unknown_id_returns_error_payload` 가 `code == "integration_error"` 와 `"Traceback" not in message` 를 함께 단언. |
| `content` 는 fetch 시점 최신 원문(캐시 아님) | PASS | `test_get_document_returns_latest_content` 가 본문을 바꿔가며 v1/v2 를 각각 단언. |

### [PASS] 기능 9 (Drive/Notion 범위): MCP 도구 3개 등록

- `search_documents` / `get_document` / `refresh_index` 3개 모두 `@mcp.tool()` 등록 확인.
- 기존 OpenAPI 도구가 밀려나지 않음(`test_openapi_tools_are_not_broken`).
- `query_rag` 는 도구 목록에서 제외되고 구현은 보존 + 미사용 주석 유지(`mcp_server.py:533`).
- 모든 도구가 `except (DomainError, IntegrationError)` → `to_error_payload()` 로
  표준 포맷 `{"error", "code", "message"}` 반환. **계약 통일 확인.**

---

## 2. 특별 점검 항목 결과

### 2.1 테스트 실효성 — 공허 참(vacuous truth) 전수 점검 → **결함 없음**

직전 OpenAPI 트랙에서 발견된 "빈 리스트에 `all()`" 결함이 이 트랙에도 있는지
Drive/Notion 관련 테스트 6개 파일을 전수 조사했다.

`assert all(...)` / `assert any(...)` / `assert not [...]` 패턴을 grep 한 결과
**해당 패턴이 하나도 사용되지 않았다.** 대신 집합/리스트 동등 비교(`==`)와
정확한 카운트 단언을 쓴다. 동등 비교는 원소가 비어 있으면 실패하므로 공허 참이
성립하지 않는다.

지시받은 4개 항목을 개별 확인한 결과:

| 점검 항목 | 판정 | 근거 |
|---|---|---|
| "후보 0이면 fetch 0" — 양쪽 다 테스트하는가 | **양쪽 다 함** | 음성: `test_returns_empty_without_fetch_when_no_candidate` 가 `fetch_call_count == 0`. 추가로 `test_no_candidate_never_touches_source` 가 호출 시 `AssertionError` 를 던지는 `ExplodingDocumentSource` 로 이중 방어. 양성: `test_fetch_count_never_exceeds_top_k` 가 `fetch_call_count == 3`(0 아님)으로 후보가 있으면 실제 fetch 가 일어남을 단언. |
| "fetch 수 ≤ top_k" — 후보 초과 상황을 실제로 만드는가 | **만듦** | `test_fetch_count_never_exceeds_top_k` 가 문서 **10건**을 심고 `top_k=3` 로 호출해 `fetch_call_count == 3` 을 단언. 통합 테스트도 8건 심고 `top_k=2` → `== 2`. 부등호(`<=`)가 아닌 **정확한 등호**라 자동 통과가 불가능하다. |
| "source 필터" — 비어있지 않은 결과에서 검증하는가 | **검증함** | `test_source_filter_restricts_results` 가 `[i.source for i in items] == [SOURCE_NOTION]` — 리스트 동등 비교라 빈 결과면 실패. 대조군으로 drive 문서도 함께 심고 `fake_drive_source.fetch_call_count == 0` 까지 단언. |
| 비어있지 않음 보장 단언이 앞에 있는가 | **있음** | 통합 테스트 `test_search_documents_returns_expected_fields` 는 `for` 루프 앞에 **`assert items`** 를 명시적으로 배치. |

**결론: 이 트랙에는 직전 트랙과 같은 공허 참 결함이 없다.** 페이크의 호출
카운터를 정확한 등호로 단언하는 방식은 적절하다.

단, 실효성이 **없지는 않으나 SPEC 검증에 실패한** 테스트가 1건 있다
(`test_partial_failure_commits_already_processed_source` — §1 기능 6 참조).
공허 참은 아니지만 **검증 대상을 잘못 잡은** 경우다.

### 2.2 자격증명 없이 서버 기동 → **기동은 정상, 그러나 사용자 피드백 결함**

미설정 환경을 실제로 구성해 실행했다.

```
sources when unconfigured: {}
search_documents unconfigured -> []
get_document unconfigured -> IntegrationError: document source is not configured: drive
```

- 서버 기동: **정상**. `build_document_sources()` 가 빈 dict 를 반환하고 기존
  OpenAPI 도구는 영향받지 않는다(`test_openapi_tools_are_not_broken`).
- `get_document` / `refresh_index`: **명확한 `IntegrationError`**. 원인 파악 가능. 양호.
- **`search_documents`: 빈 리스트 `[]` 를 조용히 반환** — 문제.

`search()` 는 소스 구성 여부를 확인하지 않고 곧장 `_select_candidates()` 로
간다. 캐시가 비어 있으니 후보 0건 → 빈 리스트. 호출 LLM 입장에서 이 응답은
**"관련 문서가 없음"과 "서버에 Drive/Notion 이 아예 설정되지 않음"이 완전히
구별되지 않는다.** 사용자는 "우리 팀 문서가 검색이 안 되네" 하고 원인을
영영 알 수 없다. `refresh_index` 를 호출해야만 비로소 원인을 알 수 있는데,
LLM 이 그 진단 경로를 스스로 밟으리라 기대하기 어렵다.

**README 는 이와 다르게 서술하고 있어 사실과 어긋난다.** `README.md:164`:

> "Drive/Notion 자격증명이 없으면 이 세 도구는 등록은 되지만 호출 시 "미구성"
> `IntegrationError` 를 반환하고"

실제로는 **세 도구 중 `search_documents` 만 IntegrationError 를 반환하지 않는다.**
문서와 구현의 불일치다.

### 2.3 에러 매핑 및 보안(토큰 유출) → **유출 없음**

로그·에러 메시지에 API 토큰이나 서비스 계정 키가 찍히는지 프로브 3으로 직접 검증했다.
`SUPER_SECRET_TOKEN_abc123` 을 토큰으로 주입하고 403 응답을 유발한 결과:

```
PROBE3 drive_error_msg='google drive access denied for /files (status 403):
        check the service account credentials and folder sharing'
       leaks_secret=False
```

코드 수준 확인:
- `_drive_error_message()` / `_notion_error_message()` 는 **경로와 상태코드만**
  메시지에 넣는다. 응답 본문(`response.text`)이나 요청 헤더를 넣지 않는다. 적절.
- 토큰은 `_client()` 안에서 `headers` 로만 쓰이고 로그에 남지 않는다.
- `to_error_payload()`(`mcp_server.py:77`)가 `exc_info` 를 **서버 로그에만**
  남기고 클라이언트에는 `code`/`message` 만 전달. 스택트레이스 유출 없음.
- `ServiceAccountTokenProvider._load_credentials()` 의 `except` 절이
  `IntegrationError(f"...: {exc}")` 로 감싸는데, `json.JSONDecodeError` 메시지에는
  파싱 실패 위치만 담기고 키 본문은 담기지 않는다.

**보안 결함 없음.** 401/403/404/429 매핑도 SPEC 요구대로 전부 `IntegrationError` 로 통일.

한 가지 지적: `google_drive_source.py:117` 의 `except Exception` 은 광범위하지만,
google-auth 가 다양한 예외 타입을 던지는 현실을 감안하면 수용 가능하다. 주석으로
사유가 명시돼 있고 예외를 삼키지 않고 변환 후 `raise ... from exc` 한다.

### 2.4 테스트 격리(hermetic) → **완전히 격리됨**

- 어댑터 테스트는 전부 `httpx.MockTransport` 사용. 실제 네트워크로 나가는
  테스트 **0건**.
- `conftest.py:121` `app_state` 픽스처가 `document_sources=fake_document_sources`
  를 **명시 주입**한다. 주석에도 "실행 환경에 Drive/Notion 자격증명이 설정돼
  있어도 테스트가 실제 외부 API 를 호출하지 않게 한다"고 사유를 밝혔다.
  개발자 환경에 실제 자격증명이 있어도 안전하다. **적절한 설계.**
- DB 는 테스트마다 `test_{uuid}` 로 별도 database 를 만들고 종료 시 DROP.

### 2.5 `.env` 미수정 → **준수**

`git diff cb9efbd..HEAD --name-only` 에 `.env` 없음. `.env.example` 만 31줄 추가.
추가된 변수 8개가 `config.py` 의 신규 필드와 정확히 일치하며, 각 변수에 한국어
주석으로 용도·기본값·비활성화 조건이 설명돼 있다. 양호.

### 2.6 마이그레이션 (자기보고 3번) → **타당한 판단, 다만 검증 흔적 없음**

Generator 는 개발자 로컬 `docs_mcp` DB 에 마이그레이션을 적용하지 않았다고
보고했다. 사유("`create_all` 로 만든 테이블에 alembic stamp 가 없어 오류")는
타당하며, 남의 DB 상태를 함부로 바꾸지 않은 판단은 옳다.

마이그레이션 자체를 검토한 결과 **모델과 정합한다**:
- `UniqueConstraint('source','external_id', name='uq_document_meta_source_external')` — 모델과 이름까지 일치
- `Index('ix_document_meta_source')` — 일치
- 컬럼 타입/길이(`String(16)`, `String(256)`, `String(1024)`, `String(2048)`) — 전부 일치
- `schema='app'` 지정, `down_revision='b336d80334c8'` 체인 정상
- `downgrade()` 가 인덱스 → 테이블 순으로 정확히 역순 수행

다만 "임시 DB 에서 왕복 검증 후 삭제"는 **자기보고일 뿐 재현 가능한 흔적이
없다.** 검수자가 독립적으로 재확인할 수 없다는 점은 한계로 기록한다.

### 2.7 실 API 미검증 (자기보고 4번) → **정직한 보고, 리스크 잔존**

Generator 가 "실제 Drive/Notion API 로 한 번도 검증된 적 없음"을 SELF_CHECK 의
"알려진 제약"에 명시한 것은 정직하다. 실제로 다음 필드 가정이 실환경에서
깨질 수 있다:

- Drive `webViewLink`: `files.list` 에서 항상 오지 않을 수 있음.
  `_to_file_meta()` 가 `https://drive.google.com/file/d/{id}/view` 폴백을 두어
  방어함. **적절.**
- Notion `_page_title()`: `properties` 안에서 `type == "title"` 인 속성을 찾는데,
  DB 하위가 아닌 일반 페이지는 `properties` 구조가 다를 수 있음.
  `UNTITLED` 폴백 존재. **적절.**
- Drive `export` 의 크기 제한(10MB)이나 429 재시도(백오프)는 미구현.

폴백 설계가 되어 있어 크래시로 이어지지는 않겠으나, 실환경 첫 연동 시 조정이
필요하다는 점은 리스크로 남는다.

---

## 3. 동적 프로브 (검수자가 직접 작성·실행)

기존 테스트만으로는 판정할 수 없는 3개 쟁점을 검증하기 위해 임시 프로브를
작성해 실행한 뒤 **삭제했다**(워크트리 상태 원복 완료, `git status` 로 확인).

| 프로브 | 검증 대상 | 결과 |
|---|---|---|
| PROBE1 | 특정 source 갱신 시 다른 source 행 삭제 여부 | `removed=1 notion_alive=True` → **버그 없음** |
| PROBE2 | source 1개일 때 내부 중간 실패 시 커밋 여부 | `committed_rows=0` → **SPEC 위반 확인** |
| PROBE3 | 에러 메시지 토큰 유출 여부 | `leaks_secret=False` → **유출 없음** |

---

## 4. 정적 검사 및 테스트 실행 결과

### `uv run ruff check app/`

```
12건 위반 (F401 x2, E501 x6, I001 x4)
main.py, openapi.py, request_example_service.py, chunk_builder.py,
indexer_service.py, sync_service.py, openapi_parser.py,
schema_normalizer.py, keyword_search.py, search_service.py
```

**전부 이번 트랙 범위 밖의 기존 위반이다.** 신규 Drive/Notion 파일만 대상으로
재실행한 결과 **위반 0건**:

```
$ uv run ruff check app/services/documents/ app/models/document_meta.py \
    app/repositories/document_meta_repository.py app/mcp_server.py \
    app/mcp_types.py app/api/dependencies.py app/core/config.py
[]
```

### `uv run mypy app/`

```
6 errors in 4 files
  indexer_service.py (3) — arg-type
  embedding_provider.py (1) — misc
  chunk_repository.py:41 (1) — attr-defined (Result has no rowcount)
  openapi_parser.py (1) — import-untyped (types-PyYAML 미설치)
```

**전부 기존 오류다.** `chunk_repository.py:41` 은 이번 브랜치가 손대지 않은
`delete_by_document()` 안에 있다(브랜치는 `:50` 이후에 새 메서드만 추가).
신규 파일만 대상으로 재실행하면:

```
$ uv run mypy app/services/documents/ app/models/document_meta.py \
    app/repositories/document_meta_repository.py
mypy: No issues found
```

> 참고: 기존 위반 12건 + 6건은 이번 트랙의 책임이 아니므로 감점 사유에서
> 제외했다. 다만 리포지토리 전체 게이트는 여전히 red 이며, 별도 정리 태스크가 필요하다.

### `uv run pytest tests/ -v`

```
345 passed in 64.40s
```

실패 0건. 출력 말미의 `psycopg.errors.AdminShutdown` 로그는 테스트용 임시
database 를 `DROP DATABASE ... WITH (FORCE)` 로 정리할 때 커넥션 풀이 끊기며
나는 것으로, 테스트 결과에 영향이 없다(전건 통과). 다만 로그가 시끄러워
실제 오류를 가릴 수 있으므로 정리 시 `engine.dispose()` 순서를 조정하면 좋다.

Drive/Notion 관련 신규 테스트 122건 내역:
- `test_document_sources.py` 39건, `test_document_source_factory.py` 9건
- `test_document_index_service.py` 17건, `test_document_meta_repository.py` 9건
- `test_document_search_service.py` 28건 (기능 8 포함)
- `test_mcp_documents.py` 21건

---

## 5. 성능 검토

### [문제] 1단계 후보 압축이 테이블 전체를 Python 으로 적재한다

`document_search_service.py:164` `_select_candidates()`:

```python
rows = self._meta_repo.list_all(source=options.source)
scored = [... for row in rows if score > 0.0]
```

`list_all()` 은 `WHERE source = ?` 외에 아무 필터가 없다. **`document_meta` 의
모든 행을 ORM 객체로 만들어 Python 에서 토큰 매칭**한다. 문서 1만 건이면 매
검색마다 1만 개 ORM 객체를 생성한다.

- SPEC 은 1단계를 "가벼움·빠름"으로 규정했는데, 이 구현은 문서 수에 선형 비례한다.
- 아이러니하게도 같은 브랜치의 OpenAPI 트랙에서는 정확히 이 문제를 고쳤다.
  `chunk_repository.list_endpoint_chunks()` 신규 메서드의 docstring 은
  "전체 청크를 적재한 뒤 Python 에서 버리면 쓰이지도 않을 임베딩 벡터 컬럼까지
  매 검색마다 전송된다"라고 적고 있다. **같은 원칙이 Drive/Notion 경로에는
  적용되지 않았다.** 일관성 결여.

완화 요인: `document_meta` 는 본문이 없어 행이 가볍고(제목·URL·시각만),
협업 문서 수는 보통 수천 건 규모라 즉각적 장애는 아니다. 그래서 성능 6점으로
감점하되 기능 정확성에서는 감점하지 않았다.

### 양호한 점

- `top_k` 절단을 1단계에서 수행해 fetch-then-discard 를 회피(자기보고 1번 — 타당).
- `document_fetch_max_chars`(기본 200,000)로 과대 응답 메모리 폭증 방지.
- `MAX_FOLDERS=500`, `MAX_BLOCK_DEPTH=4`, `MAX_BLOCKS=2000` 상한으로 API 폭주 방지.
- `httpx.Client` 를 `with` 블록으로 감싸 커넥션 누수 없음. 확인 완료.
- `visited` 집합으로 폴더 순환 참조 방지.

---

## 6. 구체적 개선 지시

### 필수 (합격 전환 조건)

1. **`document_index_service.py:_refresh_source()` — 소스 내부 부분 실패 시 커밋 보장**
   현재 루프 종료 후 단 한 번 커밋하므로 중간 실패 시 전량 롤백된다(프로브 2로
   증명). SPEC 기능 6 의 "이미 처리된 행은 커밋되어 있고, 실패한 항목만 다음
   갱신에서 재시도 가능"을 충족하려면 **커밋 경계를 배치 단위로 낮춰라.**
   예: `BATCH_SIZE = 100` 상수를 두고 N건 처리마다 `self._session.commit()` 을
   호출한 뒤, 실패 시 마지막 성공 배치까지는 남게 한다. 삭제 처리는 remote
   목록 조회가 성공한 뒤에만 수행하므로 현재 위치를 유지해도 무방하다.
   *또는* SPEC 문구를 "source 단위 부분 실패 허용"으로 개정하고 그 근거를
   SPEC 에 명시하라. **둘 중 하나를 반드시 택하라 — 현재는 SPEC 과 구현이 불일치한 상태다.**

2. **`test_document_index_service.py` — 소스 내부 중간 실패 테스트 추가**
   기존 `test_partial_failure_commits_already_processed_source` 는 `list_files()`
   단계 실패만 다뤄 "source 간 격리"를 검증할 뿐이다. `list_files()` 는 성공하되
   **행 저장 도중** 실패하는 페이크(예: N번째 `add` 에서 `IntegrationError`)를
   추가하고, 실패 후 커밋된 행 수가 **0보다 큰지** 단언하라. 1번 수정의 회귀
   방지 장치가 된다.

3. **`document_search_service.py:search()` — 미구성 상태를 빈 결과와 구별하라**
   소스가 하나도 구성되지 않았을 때(`not self._sources`) 빈 리스트 대신
   `IntegrationError("no document source is configured: set google drive or notion credentials")`
   를 던져라. `DocumentIndexService._resolve_targets()`(`:122`)가 이미 같은
   메시지로 처리하고 있으니 **동일 문구를 재사용해 일관성을 확보하라.**
   현재는 "검색 결과 없음"과 "서버 미설정"이 구별되지 않아 사용자가 원인을
   알 수 없다.

4. **`README.md:164` — 실제 동작과 일치시켜라**
   "이 세 도구는 ... 호출 시 미구성 `IntegrationError` 를 반환하고"는 사실과
   다르다(`search_documents` 는 `[]` 반환). 3번을 수정하면 문서가 사실이 되므로
   **3번과 함께 처리하라.** 3번을 채택하지 않는다면 README 를 실제 동작에 맞게
   고쳐라.

### 권장

5. **`document_meta_repository.py` — 1단계 후보 조회를 SQL 로 내려라**
   `list_all()` 전량 적재 대신 `search_by_title_tokens(tokens, source, limit)`
   메서드를 추가해 `WHERE title ILIKE ANY(...) OR url ILIKE ANY(...)` 로 DB 에서
   1차 필터링하라. 같은 브랜치의 `chunk_repository.list_endpoint_chunks()` 가
   이미 동일 원칙을 적용했으므로 **일관성을 위해서도 맞추는 편이 좋다.**

6. **`test_document_index_service.py` — 교차 소스 삭제 회귀 테스트 추가**
   현재 구현은 안전하나(프로브 1) 이를 고정하는 테스트가 없다. "notion 이 이미
   캐시된 상태에서 drive 만 갱신하고 drive 파일을 삭제" 시나리오를 추가해
   `meta_repo.find(SOURCE_NOTION, "n1") is not None` 을 단언하라. 향후
   `list_by_source` → `list_all` 같은 실수로 데이터가 날아가는 것을 막는다.

7. **`conftest.py:pg_engine` — 테스트 종료 로그 소음 제거**
   `DROP DATABASE ... WITH (FORCE)` 가 풀 커넥션을 끊으며 `AdminShutdown`
   스택트레이스를 다량 출력한다. DROP 이전에 해당 엔진의 `dispose()` 가 완료되도록
   순서를 조정하면 실제 오류가 로그에 묻히는 것을 막을 수 있다.

8. **Drive/Notion 429 재시도 부재 명시**
   `_drive_error_message` 가 "retry later" 라고 안내하지만 자동 백오프는 없다.
   SPEC 이 요구하지 않았으므로 결함은 아니나, SELF_CHECK "알려진 제약"에
   추가해 두면 실환경 연동 시 혼선을 줄인다.

---

## 7. Generator 자기보고 사항 판정

| # | 자기보고 내용 | 판정 | 근거 |
|---|---|---|---|
| 1 | `top_k` 절단을 1단계에서 수행 | **타당** | `_select_candidates()` 가 `scored[:top_k]` 로 자른 뒤 2단계에 전달. 불변식 준수. `test_fetch_count_never_exceeds_top_k` 가 10건→top_k=3→`fetch_call_count == 3` 으로 정확히 단언. |
| 2 | 부분 실패 허용을 "source별 커밋 경계"로 구현 | **불충분** | source 간 격리는 성립하나, **source 내부 중간 실패 시 전량 롤백**된다(프로브 2: `committed_rows=0`). SPEC 기능 6 문구 미충족. §1·개선지시 1번 참조. |
| 3 | 마이그레이션을 로컬 DB 에 미적용, 임시 DB 왕복 검증 | **타당(단 검증 흔적 없음)** | 남의 DB 를 건드리지 않은 판단은 옳다. 마이그레이션 내용을 검토한 결과 모델과 완전 정합(제약명·인덱스명·타입·길이·schema·revision 체인). 다만 왕복 검증은 재현 가능한 흔적이 없어 독립 확인 불가. |
| 4 | 어댑터가 실제 API 로 미검증 | **타당(정직한 보고)** | SELF_CHECK "알려진 제약"에 명시. `webViewLink` 부재, Notion `properties` 구조 차이 등에 폴백이 있어 크래시로는 이어지지 않으나 실환경 조정 리스크는 잔존. |

---

## 8. 최종 판정

**조건부 합격 (6.9 / 10.0)**

Drive/Notion 트랙의 전반적 구조 — `DocumentSource` Protocol 추상화, 2단계 후보
압축, `top_k` 를 1단계에서 절단하는 불변식 준수, `httpx` 직접 호출, 토큰 미유출,
hermetic 테스트 — 는 SPEC 의 설계 의도를 정확히 구현했다. 직전 OpenAPI 트랙에서
문제가 됐던 공허 참 테스트 결함도 이 트랙에는 없으며, 페이크 호출 카운터를
정확한 등호로 단언하는 방식은 오히려 모범적이다.

그러나 **SPEC 기능 6 의 명시적 검증 기준 하나("갱신 중 예외가 나도 이미 처리된
행은 커밋")를 실제로는 충족하지 못하며, 이를 검증한다고 표시된 테스트가 실은
다른 것을 검증하고 있어 통과 상태로 위장돼 있다.** 이것이 조건부 판정의 결정적
사유다. 여기에 미구성 상태에서 `search_documents` 가 침묵하는 UX 결함과 그와
어긋나는 README 서술이 더해진다.

**방향 판단**: **현재 방향 유지.** 구조 재설계는 불필요하다. 개선 지시 1~4번
(커밋 경계 조정, 중간 실패 테스트 추가, 미구성 에러 구별, README 정정)만
반영하면 합격 수준에 도달한다. 5~8번은 후속 처리 가능하다.

**재검수 시 확인할 것**:
- 개선 지시 1번 수정 후 프로브 2와 동일한 시나리오(source 1개, 저장 도중 실패)에서
  `committed_rows > 0` 인지
- 개선 지시 3번 수정 후에도 **캐시가 비었을 뿐 소스는 구성된** 정상 케이스가
  여전히 `[]` 를 반환하는지(과잉 교정으로 `test_search_documents_before_refresh_returns_empty`
  가 깨지지 않아야 한다)
- 기존 345건이 계속 전건 통과하는지(회귀 방지)
