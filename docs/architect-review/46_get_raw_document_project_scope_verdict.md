# 46. `get_raw_document` project 스코프 검사 누락 판정

- 대상: `app/mcp/tools/endpoints.py:191-206` (`@mcp.resource("document://{document_id}/raw")`)
- 선행: `docs/architect-review/44_layer_boundary_exceptions_verdict.md` §3 (별건으로 보류했던 항목)
- 판정일 기준 커밋: `7bd579d`

## 1. 제기된 문제

`get_raw_document` 는 `project` 인자가 없고 `document_repo.get(document_id)` 가 project 를
보지 않으므로, `document_id` 만 알면 다른 project 소속 문서의 원문을 그대로 받을 수 있다.
`list_tags` / `resolve_ref` / `search_endpoints` 는 `document_id` 와 `project` 가 함께 오면
정합을 검사해 `document_not_found` 를 내는데, 이 리소스만 그 검사가 없다.

## 2. 판정

**현행 유지.** 리소스 URI 에 `project` 를 추가하지 않고, 소유 project 대조 가드도 넣지
않는다. 다만 이 리소스의 docstring 이 실제 노출 범위를 잘못 적고 있어 **그 한 줄만 수정**한다.

## 3. 근거

### 3-1. 호출 컨텍스트에 project 가 없다 — 대조 가드는 구현 자체가 불가능하다

지시의 고려사항 (1) 중 "document_repo 조회 후 소유 project 와 호출 컨텍스트의 project 를
비교"는 성립하지 않는다. 코드상 MCP 세션에 project 를 싣는 경로가 없다.

- `app/mcp/server.py:17-23` — `FastMCP("docs-mcp")` 에 도구만 등록한다. 인증·세션 미들웨어,
  초기화 파라미터, 컨텍스트 주입이 전혀 없다.
- `app/mcp/server.py:26-32` — `mcp_server.run()` 기본 전송(stdio)로 뜬다. 원격 다중 사용자
  진입점이 아니다.
- `app/mcp/tools/_common.py:23-51` — 모든 도구가 거치는 실행부는 `app_state` 로 매 호출마다
  DB 세션 번들을 새로 열 뿐이다. 호출자 신원이나 project 를 담는 필드가 없다.

따라서 서버가 "지금 호출자의 project" 로 삼을 수 있는 값은 **호출자가 인자로 준 값뿐**이다.
자기 신고 값과 소유 project 를 대조하는 것은 접근 통제가 아니라 인자 오타 검사다.

### 3-2. ID 로 문서 한 건을 특정하는 조회에는 project 를 붙이지 않는 것이 기존 규칙이다

지시의 고려사항 (2)에 따라 등록된 도구 16개를 분류했다.

| 분류 | 도구 | `project` |
|---|---|---|
| 목록·검색 (범위 지정형) | `list_documents`, `search_documents`, `search_endpoints`, `list_tags`, `resolve_ref`, `refresh_index`, `list_drive_sources`, `list_notion_sources` | optional |
| 쓰기 (소유 project 확정 필요) | `register_document`, `register_drive_source`, `remove_drive_source`, `register_notion_source`, `register_notion_page`, `remove_notion_source` | 필수 |
| 단건 상세 조회 (ID 가 이미 문서를 특정) | `get_endpoint_details`, `get_document`, `document://{document_id}/raw` | **없음** |

세 번째 분류는 세 개 전부 일관되게 `project` 가 없다. `get_raw_document` 만 예외인 것이
아니라, **이미 자기 분류와 일치**한다. 여기에 `project` 를 넣으면 같은 분류의 다른 두 도구와
어긋나 오히려 일관성이 깨진다.

이는 사후 합리화가 아니라 원설계의 명시적 결정이다 —
`docs/exec_plans/feat_project_scoped_documents/SPEC.md:206-218`:

> `get_endpoint_details` / `get_document` / raw 리소스에 project 를 넣지 않는 이유: 이미 단일
> 문서를 특정하는 식별자를 받고 있어 필터가 무의미하며, "project 를 넣었는데 불일치"
> 케이스만 추가로 만들어낸다. 이들은 프로젝트 격리의 관심사가 아니라 후속 상세 조회이고,
> 상세 조회 대상은 앞선 검색(이미 project 로 필터된)에서 얻은 ID 다.

`list_tags` 등이 하는 정합 검사는 "격리"가 아니라 **호출자가 스스로 준 두 인자(document_id,
project)가 서로 안 맞을 때 조용히 빈 결과를 주지 않기 위한 것**이다. 인자가 하나뿐인 리소스에는
검사할 짝이 없다.

### 3-3. URI 에 project 를 넣어도 격리 효과가 0 이다

`project` 를 URI 에 추가한다고 해서 "다른 project 문서를 못 읽게" 되지 않는다. 같은 서버의
다른 도구가 이미 전 project 를 무조건 열어주기 때문이다.

- `app/mcp/tools/documents.py:27-56` — `list_documents()` 를 인자 없이 부르면
  `list_all(project=None)` 이 되고(`app/repositories/document_repository.py:33-45`, `project`
  가 None 이면 WHERE 절 자체가 없음) **모든 project 의 문서 요약이 `document_id` 와 함께**
  반환된다.
- `app/mcp/tools/documents.py:115-164` — `search_documents()` 도 `project` 생략 시 전 project
  본문을 검색해 스니펫을 돌려준다.

즉 raw 리소스를 부를 수 있는 호출자는 이미 (a) 전 project 문서 목록과 ID 를 얻을 수 있고,
(b) 전 project 본문 스니펫을 얻을 수 있다. raw 리소스에만 자기 신고 `project` 를 요구하는 것은
잠기지 않은 문이 셋인 집에서 한 문에만 "본인 project 를 적어 주세요" 팻말을 붙이는 것이다.
비용(리소스 URI 파괴적 변경, 문서·테스트 갱신)만 남고 이득이 없다.

참고로 ID 추측으로 넘어가는 경로도 아니다. 등록형은 `uuid.uuid4().hex[:16]`
(`app/services/ingestor/sync_service.py:234-236`), Drive/Notion 형은
`sha256(project\0source\0external_id)[:16]`
(`app/services/documents/document_body_indexer.py:31-41`)라 열거·추측이 불가능하다. 유출
경로는 오직 위의 목록/검색 도구이고, 그게 이 판정의 핵심 근거다.

### 3-4. 결론적으로 이것은 신뢰 경계가 아니다

이 시스템의 `project` 는 접근 통제 주체가 아니라 **검색 범위를 좁히는 태그**다
(`docs/portfolio-architecture.html` 설계 결정 02). 단일 프로세스 stdio MCP 서버, 단일 DB,
인증 없음, 세션 신원 없음 — 격리를 걸 지점이 존재하지 않는다. 47번 문서의 "신뢰 경계가 아니라
태그 기반 범위 지정" 판단은 이번 재검토에서도 유지된다.

## 4. 다만 실제 결함 1건 — docstring 이 노출 범위를 잘못 적고 있다

`app/mcp/tools/endpoints.py:193` 의 docstring 은 이렇다.

```python
"""등록된 특정 OpenAPI 문서의 원문(JSON/YAML)을 반환한다."""
```

그러나 `Document.raw_text` 는 OpenAPI 전용이 아니다.
`app/services/documents/document_body_indexer.py:95` 와 `:108` 에서 **Drive/Notion 협업 문서의
본문도 같은 컬럼에 저장**된다. `register_document` 로 등록되는 markdown/csv/pdf/docx 도 마찬가지다
(`app/services/ingestor/sync_service.py:106`). 따라서 이 리소스는 등록된 모든 doc_type 의 원문을
반환한다.

MCP 리소스의 docstring 은 클라이언트 LLM 이 이 리소스를 쓸지 판단하는 계약 문구다. "OpenAPI"
로 한정해 두면 협업 문서 원문이 필요한 상황에서 이 리소스를 후보에서 빼게 된다. 이번 건에서
실제로 고칠 값어치가 있는 유일한 항목이다.

## 5. developer 지시

`app/mcp/tools/endpoints.py:193` docstring 한 줄만 교체한다. 그 외 시그니처·URI·가드는 손대지
않는다.

```python
"""등록된 문서 한 건의 원문을 반환한다.

doc_type 과 무관하게 저장된 원문(OpenAPI JSON/YAML, markdown/csv, Drive/Notion
동기화 본문 등)을 그대로 돌려준다. document_id 는 list_documents 또는
search_endpoints 에서 얻는다.
"""
```

- 테스트 추가·수정 없음(문서 문자열 변경). `tests/integration/test_mcp_server.py:453` 기존
  테스트는 그대로 통과해야 한다. 깨지면 이 판정의 전제가 틀린 것이니 고치지 말고 보고할 것.

## 6. 나중에 진짜 격리가 필요해지면 (지금은 하지 않는다)

이 서버를 여러 팀이 공유하는 원격 전송으로 옮기는 날이 오면, 손댈 곳은 raw 리소스가 아니라
**서버 기동 시점의 project 바인딩**이다.

1. 기동 설정(예: `DOCS_MCP_PROJECT`)이나 전송 계층 인증에서 project 를 확정해 `AppState` 에
   싣는다.
2. `run_bundle_tool` 에서 그 값을 강제 주입해, 인자로 온 `project` 는 그 범위 안에서만
   좁히는 용도로 쓴다(넓히지 못하게).
3. 그러면 `list_documents` / `search_documents` / raw 리소스가 **한 곳에서 동시에** 막힌다.

이 순서를 지키지 않고 리소스 URI 부터 고치면, 격리는 여전히 없는 채로 파괴적 변경만 남는다.
