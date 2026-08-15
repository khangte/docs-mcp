# 47. 계층 경계 예외 2건 판정 — 고칠 것과 남길 것

- 작성: architect
- 요청: lead — "`docs/portfolio-components.html` 인셋에 표시한 계층 경계 예외 2건,
  각각 (a) 실제로 문제인지 (b) 고친다면 방향과 영향범위 (c) 고치지 않는다면 이유를 판단하라"
- 대상 코드:
  - `app/mcp/tools/endpoints.py:190` (`get_raw_document`, `@mcp.resource`)
  - `app/services/documents/registered_resync.py`
- 선행 판정: `docs/architect-review/32-refresh-index-batch-automation.md` §4

---

## 0. 결론 요약

| # | 대상 | 판정 | 근거 한 줄 |
|---|------|------|-----------|
| 1 | `get_raw_document` 의 세션·리포지토리 직접 생성 | **수정** | 오류 로깅이 이 경로만 빠져 있고, 세션 수명주기가 조립 지점 밖에 있다 |
| 2-a | `registered_resync` 의 `ServiceBundle` 인자 | **수정** | 이 저장소에서 유일하게 서비스가 컴포지션 루트를 임포트한다(잠재 순환) |
| 2-b | `registered_resync` 의 `app.mcp.types` 임포트 | **유지** | 타입 전용 임포트이고, 되돌리면 스키마 leaf 모듈이 서비스 계층을 끌어온다 |

수정 범위는 **2개 파일 + 호출부 2곳**이고, **기존 테스트 수정은 불필요**하다(§3.3, §4.3).

---

## 1. 예외 1 — `get_raw_document` (`app/mcp/tools/endpoints.py:190`)

```python
@mcp.resource("document://{document_id}/raw")
async def get_raw_document(document_id: str) -> str:
    def _sync() -> str:
        with managed_session(session_factory) as session:
            repo = DocumentRepository(session)
            doc = repo.get(document_id)
            if not doc:
                raise DocumentNotFoundError(document_id)
            return doc.raw_text
    return await anyio.to_thread.run_sync(_sync)
```

### 1.1 (a) 실제 문제인가 — 문제다. 단, 인셋에 적은 이유 때문은 아니다

"서비스 계층을 건너뛴다"는 것 자체는 이 건에서 결정적이지 않다. 원문(`raw_text`) 한 필드를
읽는 데 서비스를 새로 만드는 것은 오히려 과잉이다 — 지금 어떤 서비스도 이 데이터를
노출하지 않고, 3줄짜리 읽기를 위해 `RawDocumentService` 를 만들 이유는 없다.

진짜 문제는 그 옆에 있다.

**(1) 오류 처리 규약이 이 경로만 다르다.** 나머지 16개 도구는 전부
`run_bundle_tool` 을 거치고, 거기서 `DomainError`/`IntegrationError` 가
`to_error_payload()` 로 변환되며 **서버 로그에 `_LOG.error(..., exc_info=e)` 가 남는다**
(`app/mcp/tools/_common.py:54-61`). `get_raw_document` 의
`DocumentNotFoundError` 는 그 경로를 타지 않으므로 **서버 측 실패 기록이 아예 남지 않는다.**
운영 문서(`docs/operations.md`)가 로그로 장애를 추적하는 구조인데 이 진입점만 로그에
보이지 않는다.

**(2) 세션 수명주기가 조립 지점 밖에 있다.** `app_state.session_factory` 를 직접 열어
쓰는 곳은 이 함수가 유일하다. 앞으로 `build_services()` 에 세션 단위 설정(statement
timeout, search_path, project 스코프 주입 등)을 추가하면 **이 경로만 조용히 누락된다.**
"조립은 `app/composition.py` 한 곳에서만"이라는 이 저장소의 규칙이 실제로 깨지는 지점이다.

**(3) 테스트가 없다.** `tests/` 전체에서 `get_raw_document` / `document://` 를 참조하는
파일이 0개다. 위 두 특성이 회귀해도 잡히지 않는다.

### 1.2 (b) 수정 방향

`_common._run_bundle` 을 재사용해 번들 안의 리포지토리를 쓴다. 새 헬퍼·새 서비스·새
추상화를 만들지 않는다.

```python
@mcp.resource("document://{document_id}/raw")
async def get_raw_document(document_id: str) -> str:
    def _inner(bundle: ServiceBundle) -> str:
        doc = bundle.document_repo.get(document_id)
        if not doc:
            raise DocumentNotFoundError(document_id)
        return doc.raw_text
    return await anyio.to_thread.run_sync(lambda: _run_bundle(app_state, _inner))
```

**`run_bundle_tool` 을 쓰지 않는 이유는 유지한다.** 그 헬퍼의 반환 타입은
`_T | ErrorPayload` 인데, `@mcp.resource` 핸들러는 `str` 을 반환해야 하므로 에러
페이로드 dict 를 돌려줄 수 없다. MCP 규약상 리소스는 예외를 올려 실패를 알린다 —
따라서 `raise` 유지가 맞고, 바꿔야 할 것은 **세션 조립 경로**다.

로깅 누락은 위 수정만으로는 해소되지 않는다(`_run_bundle` 은 오류를 변환하지 않는다).
`except (DomainError, IntegrationError)` 로 감싸 `to_error_payload()` 를 호출하고
**다시 raise** 하면 로그는 남기고 예외 전파는 유지된다. `to_error_payload` 는 로깅 +
페이로드 생성을 함께 하므로 반환값을 버리는 형태가 어색하다 — developer 판단에 맡기되,
**"실패가 서버 로그에 남는다"는 결과만은 반드시 충족**해야 한다(전용 로거 한 줄이면 충분).

### 1.3 (c) 영향 범위

- 수정 파일: `app/mcp/tools/endpoints.py` 1개
- 삭제되는 임포트: `app.core.db.managed_session`, `app.repositories.document_repository.DocumentRepository`,
  그리고 `register_endpoint_tools` 지역변수 `session_factory`(다른 사용처 없음 — 확인함)
- 추가되는 임포트: `app.mcp.tools._common._run_bundle`
- 호출부: 없음(MCP 클라이언트가 URI 로만 접근)
- 기존 테스트: 이 경로를 덮는 테스트가 0건이라 깨질 것이 없다.
  **대신 회귀 테스트 1건을 새로 요구한다** — 존재하는 문서는 원문을 돌려주고,
  없는 `document_id` 는 `DocumentNotFoundError` 를 올린다.

### 1.4 수정 후에도 남는 것

수정 후에도 MCP 계층이 `bundle.document_repo` 를 직접 읽는 구조는 그대로다. 이것은
**의도적으로 허용한다** — 번들이 이미 노출하는 리포지토리를 통과 읽기하는 것과,
조립 지점 밖에서 세션을 새로 여는 것은 위험도가 다르다. 다이어그램 인셋도 이 뉘앙스로
문구를 조정한다(§5).

---

## 2. 예외 2 — `registered_resync.py`

```python
from app.composition import ServiceBundle          # (2-a)
from app.mcp.types import RegisteredResyncResult   # (2-b)

def resync_registered_documents(
    bundle: ServiceBundle, *, project: str | None, force: bool
) -> RegisteredResyncResult:
```

이 모듈은 doc/32 §4의 결정으로 **MCP 계층에서 서비스 계층으로 내려온** 코드다. 당시
"동작·반환 스키마는 무변경"을 조건으로 순수 이동만 했고, 두 임포트는 그 이동의 잔여물이다.
즉 **더 큰 역전(스크립트 → MCP 도구 → 서비스)을 없앤 결과물**이지, 방치된 실수가 아니다.
그래도 둘의 성격은 다르므로 나눠 판단한다.

### 2-a. `ServiceBundle` 인자 — **수정**

**(a) 문제인가.** 그렇다. 두 가지가 겹친다.

1. **잠재 순환 임포트.** `app.composition` 은 서비스 계층을 임포트한다. 그 서비스 계층의
   모듈이 다시 `app.composition` 을 임포트하고 있다. 오늘 터지지 않는 유일한 이유는
   `composition.py` 가 마침 `registered_resync` 를 임포트하지 않기 때문이다. 이 함수를
   `ServiceBundle` 에 넣거나 컴포지션이 참조하는 순간 기동 시점 `ImportError` 가 된다.
   "언젠가 터질 수 있다"가 아니라 **"지금 안 터지는 게 우연"**인 상태다.
2. **의존이 시그니처에 안 보인다.** 이 함수가 실제로 쓰는 것은 `session`, `document_repo`,
   `sync_service` 셋뿐인데 번들 16개 필드를 통째로 받는다. 이 저장소의 나머지 서비스는
   전부 생성자 명시 주입을 쓴다(`composition.py:190-256`) — **컴포지션 루트를 임포트하는
   서비스 모듈은 이 파일 하나뿐**이라, 자기 저장소 관례에서 유일하게 벗어나 있다.

**(b) 방향.** 명시 의존으로 바꾼다.

```python
def resync_registered_documents(
    session: Session,
    document_repo: DocumentRepository,
    sync_service: SyncService,
    *, project: str | None, force: bool,
) -> RegisteredResyncResult:
```

`app.composition` 임포트가 사라지고, 새로 붙는 임포트는 전부 하위 방향
(`sqlalchemy.orm.Session`, `app.repositories.*`, `app.services.ingestor.*`)이다.

**영향 범위 — 호출부 2곳, 테스트 0곳.**

- `app/mcp/tools/sources.py:80` → `resync_registered_documents(bundle.session, bundle.document_repo, bundle.sync_service, project=project, force=force)`
- `app/scripts/refresh_documents.py:94` → 동일 형태
- `tests/unit/test_refresh_documents_script.py` 의 `_FakeBundle` 은 `session`/`document_repo`/
  `sync_service` 를 이미 모두 갖고 있고(38-53행), 테스트는 `_execute(bundle, ...)` 를
  부르므로 **테스트 파일은 손댈 필요가 없다.** 통합 테스트도 MCP 도구를 통해 호출하므로 무관.

### 2-b. `app.mcp.types.RegisteredResyncResult` 임포트 — **유지**

**(c) 고치지 않는 이유.**

1. **타입 전용, 런타임 결합 0.** `app/mcp/types.py` 는 `typing` 만 임포트하는 leaf 모듈이라
   서비스·배치가 이를 끌어와도 MCP 런타임(`fastmcp`)이 딸려오지 않는다.
2. **되돌리면 더 나빠진다.** 타입을 서비스 모듈로 옮기고 `app/mcp/types.py` 가 그것을
   임포트하게 하면, 순수 스키마 모듈이 서비스 계층 전체를 임포트 시점에 끌어온다.
   양쪽에 같은 4필드 TypedDict 를 중복 선언하는 대안은 DRY 위반이고, 이 값은
   `RefreshIndexResult.registered` 에 **그대로 중첩되는 응답 스키마**라 어차피 한 곳에서
   정의되는 편이 맞다.
3. **선행 결정과 일치.** doc/32 §4가 "반환 스키마 무변경"을 이동 조건으로 명시했고,
   그 조건은 지금도 유효하다.
4. **강제되지도 않는다.** `pyproject.toml` 에 mypy 가 없어 타입 검사가 CI 에서 돌지
   않는다 — 이 임포트는 사실상 문서 수준의 결합이다. 여기에 리팩터링을 들이는 것은
   비용 대비 이득이 없다.

결론: 2-b 는 **의도적 예외로 명시 유지**한다. 대신 모듈 docstring 에 "이 임포트는 응답
스키마 공유를 위한 타입 전용이며 의도된 예외"라는 한 줄을 남겨, 다음 사람이 같은 판단을
반복하지 않게 한다.

---

## 3. 검토했지만 범위에 넣지 않은 것

- `get_raw_document` 는 `project` 스코프 검사가 없다(`repo.get()` 은 project 를 보지 않음).
  `list_tags` 등은 document_id ↔ project 정합을 검사한다. 다만 리소스 URI 템플릿
  (`document://{document_id}/raw`)에 project 가 없어 **검사할 인자 자체가 없고**, 이
  시스템의 프로젝트 격리는 애초에 신뢰 경계가 아니라 태그 기반 범위 지정이다
  (`docs/portfolio-architecture.html` 설계 결정 02). 이번 지시 범위(계층 경계) 밖이므로
  건드리지 않되, **별건으로 판단이 필요하면 lead 에 올린다.**

---

## 4. developer 지시 사항

1. `app/mcp/tools/endpoints.py` — `get_raw_document` 를 `_run_bundle` + `bundle.document_repo`
   경로로 바꾸고, 실패가 서버 로그에 남게 한다. `managed_session`/`DocumentRepository`
   임포트와 `session_factory` 지역변수를 제거한다(다른 사용처 없음).
2. 위 경로 회귀 테스트 1건 추가 — 정상 조회 / 없는 id 시 `DocumentNotFoundError`.
3. `app/services/documents/registered_resync.py` — 시그니처를 명시 의존
   (`session`, `document_repo`, `sync_service`)으로 바꾸고 `app.composition` 임포트를 제거한다.
   **`app.mcp.types` 임포트는 그대로 두고**, 의도된 예외임을 docstring 한 줄로 남긴다.
4. 호출부 2곳(`app/mcp/tools/sources.py:80`, `app/scripts/refresh_documents.py:94`) 갱신.
5. 기존 테스트는 수정 대상이 아니다 — 깨진다면 그것은 이 판정의 전제가 틀렸다는 뜻이니
   고치지 말고 architect 에 보고할 것.

---

## 5. 문서 반영

수정 완료 후 `docs/portfolio-components.html` 인셋을 갱신한다.

- 예외 1: 문구를 "세션을 조립 지점 밖에서 직접 연다" → **삭제**(해소됨). MCP 계층이
  번들의 리포지토리를 통과 읽기하는 구조만 남으므로, 표시하더라도 등급을 낮춘다.
- 예외 2: `app.composition` 역참조 **삭제**(해소됨), `app.mcp.types` 는
  "의도적으로 허용한 타입 전용 예외"로 문구를 바꿔 남긴다.
- 결과적으로 인셋은 "예외 2건"에서 **"의도적 예외 1건"**으로 줄어든다. `stats` 의
  `2 계층 경계 예외` 수치도 함께 고친다.
