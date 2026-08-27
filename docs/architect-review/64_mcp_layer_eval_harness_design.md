# 64. MCP 계층 평가 하네스(B 트랙) 설계

- 상태: 설계 확정(구현은 developer)
- 관련: `27_search_quality_eval_real_corpus_design.md` §7, `docs/eval-results/README.md`,
  `tests/fixtures/corpus_eval/run_corpus_eval.py`, ADR-0003
- 측정 대상: 서버가 관측·통제하는 **도구 실행 계약**. 클라이언트 LLM의 도구 선택,
  인자 선택, 답변 생성은 범위 밖이다.

## 1. 판정 요약

| 결정 | 확정값 |
|---|---|
| 1. 호출 레이어 | **A — 인프로세스 FastMCP 디스패치**. `create_mcp_server(state).call_tool()`을 사용한다. stdio 프로토콜은 이번 분모에서 제외한다. |
| 2. Success / Error / Timeout | 시나리오의 기대 결과와 일치하면 success. 기대한 `ErrorPayload(code)`는 처리된 거절로서 success다. 실패는 상호배타적으로 error 또는 timeout에 넣고 두 비율의 합을 `1 - Tool Success Rate`로 고정한다. |
| 2-1. timeout | `search_endpoints`, `search_documents`는 **5.0초**, 나머지 read-only tool은 **2.0초**. `perf_counter` 벽시계로 tool 진입 직전부터 구조화 결과 반환까지 잰다. |
| 3. 시나리오 | read-only tool 9개, 시나리오 21개. 기본 5회 반복하여 **총 105회**를 채점한다. write tool과 setup 호출은 분모에서 제외한다. |
| 4. 코퍼스 | 27번 대형 Stripe/GitHub 코퍼스가 아니라 **최소 결정론 시드**. 기존 Petstore 샘플과 `FakeDocumentSource`를 재사용한다. 외부 HTTP·자격증명은 금지한다. |
| 5. 러너 | `tests/fixtures/mcp_eval/run_mcp_eval.py` 독립 스크립트. pytest 비수집, 임시 DB, Markdown stdout, 종료 코드 계약을 사용한다. |

## 2. 목표와 비목표

### 2.1 목표

1. 준비 완료된 MCP 서버가 유효한 인자를 받아, 정해진 시간 안에 계약에 맞는
   구조화 응답 또는 계약에 맞는 오류 응답을 내는지 재현 가능하게 측정한다.
2. `Tool Success Rate >= 99%`, `MCP Error / Timeout Rate < 1%`를 같은 실행의
   상보 지표로 산출한다.
3. 도구별 success/error/timeout 카운트와 실패 원인을 남겨 회귀 위치를 바로 찾게 한다.
4. 27번 하네스의 임시 DB 및 독립 러너 관례를 재사용한다.

### 2.2 비목표

- Tool Selection Accuracy, Parameter Accuracy, Tool Calls per Query: 클라이언트 LLM 영역이다.
- stdio 프레이밍, MCP 클라이언트 SDK 호환성, 프로세스 기동 실패: 이번 A 트랙의
  분모가 아니다. 필요하면 소수의 B 프로토콜 smoke test로 별도 설계한다.
- 검색 정답 순위·Recall/MRR: 27번 하네스의 책임이다.
- 운영 트래픽의 장기 SLO 추정: 105회 결정론 실행은 회귀 게이트이지 통계적 신뢰구간을
  갖춘 가용성 증명이 아니다.
- 실제 Drive/Notion API 안정성: 페이크 어댑터를 사용하므로 측정하지 않는다.

## 3. 결정 1 — 호출 레이어

### 3.1 확정: `FastMCP.call_tool()` 인프로세스 디스패치

러너는 다음 경로로 호출한다.

```text
scenario
  -> create_mcp_server(AppState)
  -> FastMCP.call_tool(name, arguments)
  -> 등록된 tool callable
  -> run_bundle_tool
  -> ServiceBundle / DB
  -> ToolResult.structured_content
```

요청서의 A안을 단순히 `app/mcp/tools/*.py`에서 함수를 import해 await하는 것으로
구현하면 안 된다. 현재 tool callable은 `register_*_tools()` 안에 중첩되어 공개
심벌이 아니다. 이를 평가 때문에 밖으로 빼면 제품 구조를 하네스에 맞춰 바꾸는 셈이다.

`FastMCP.call_tool()`은 기존 MCP 통합 테스트가 사용하는 검증된 경로이며, 프로세스와
stdio만 생략한다. 그러면서도 다음은 실제 제품 계약을 통과한다.

- 도구명 디스패치와 인자 바인딩
- FastMCP 반환 직렬화 및 `{"result": ...}` 구조화 래핑
- tool return annotation에서 생성된 `output_schema`
- `run_bundle_tool`의 스레드 오프로딩, 번들 수명, Domain/Integration 오류 변환

반대로 stdio B안은 서버 핸들러의 회귀와 프로세스/SDK/프레이밍 문제를 한 지표에
섞는다. 현재 목표는 `docs/eval-results`의 서버 관측 지표이고 CI에 가까운 반복 측정이므로
비용 대비 분리도가 낮다. stdio 호환성은 후속 smoke track의 독립 결과로 남겨야 한다.

### 3.2 평가 대상 9개

| 영역 | 포함 tool |
|---|---|
| OpenAPI 문서 | `list_documents` |
| 엔드포인트 | `search_endpoints`, `get_endpoint_details`, `resolve_ref`, `list_tags` |
| 협업 문서 | `search_documents`, `get_document` |
| source 조회 | `list_drive_sources`, `list_notion_sources` |

다음 write tool은 평가 분모에서 제외한다.

- `register_document`
- `submit_endpoint_metadata`
- `refresh_index`
- `register_drive_source`, `remove_drive_source`
- `register_notion_source`, `register_notion_page`, `remove_notion_source`

ADR-0003은 실제 API Execute를 제외하는 제품 경계를 정하지만, 위 도구 중 일부는 로컬
DB와 외부 색인 상태를 변경한다. 반복 횟수가 결과를 바꾸고 롤백·외부 어댑터 격리가
지표 자체보다 커지므로 read-only B 트랙에 섞지 않는다. write 안정성이 필요해지는 시점에
별도의 mutation track으로 설계한다.

### 3.3 `get_raw_document`는 보조 resource 게이트

`get_raw_document`는 이름과 달리 `@mcp.tool()`이 아니라
`@mcp.resource("document://{document_id}/raw")`이다. 따라서 Tool Success Rate의
분모에 넣으면 지표 정의가 거짓이 된다.

- 정상 document URI 1건과 미존재 document URI 1건을 `read_resource`로 실행한다.
- 정상 원문 일치와 미존재 `ResourceError`를 각각 기대값으로 채점한다.
- 결과는 `MCP resource conformance` 보조 표에 기록하고 tool 105회 분모에서는 제외한다.
- resource 게이트 실패는 러너 전체 종료 코드를 실패로 만들되, 두 tool 비율을 고쳐 쓰지 않는다.

## 4. 결정 2 — Success / Error / Timeout 정의

### 4.1 두 축을 분리한다

의도한 잘못된 ref나 없는 id는 서버의 정상적인 계약 응답이다. 따라서 관측 결과와
시나리오 판정을 분리한다.

관측 결과(`observed`):

- `payload`: 예외 없이 `ToolResult.structured_content` 반환
- `exception`: 호출이 예외를 raise
- `over_deadline`: 완료 여부와 무관하게 경과시간이 tool 임계 이상

시나리오 기대(`expected`):

- `success`: 비-error payload + 출력 스키마 유효 + assertions 전부 참
- `error`: `error is true` + 지정한 `code` 일치 + 출력 스키마 유효

최종 verdict는 기대와 관측의 일치 여부다. 예를 들어 `resolve_ref("Pet")`가
`validation_error`를 반환하는 것은 **expected rejection / success**다. 반면 같은
시나리오가 일반 payload를 반환하거나 다른 code를 반환하면 **error / fail**이다.

### 4.2 유효 응답

tool별 `output_schema`는 러너 시작 시 `await mcp.list_tools()`에서 읽는다. 반환 전체
`ToolResult.structured_content`를 해당 JSON Schema로 검증한다. 현재 스키마의 최상위
`{"result": ...}`까지 검증해야 하며 내부 payload만 검증하면 안 된다.

- 검증기: `jsonschema.Draft202012Validator`
- `output_schema is None`, 스키마 자체 오류, `structured_content is None`, `result` 누락은
  preflight 또는 실행 error다.
- 하네스가 `jsonschema`를 직접 import하므로 developer는 이를 `pyproject.toml`의 직접
  dependency로 승격하고 lock을 갱신한다. transitive dependency에 기대지 않는다.
- `ErrorPayload` 판정은 `payload.get("error") is True`로 한다. key 존재만으로 판정하지 않는다.

FastMCP 출력 스키마는 형태만 검증한다. 정상 payload의 의미는 시나리오 `assertions`로
추가 검증한다. assertion DSL은 `equals`, `contains`, `length_gte`, `path_exists` 네 종류만
허용하며 임의 Python/eval은 금지한다.

### 4.3 상호배타 실패 분류와 우선순위

각 실행은 정확히 하나의 bucket에 들어간다.

1. 경과시간 `>= timeout_s`이면 `timeout`이다. 늦게 유효 응답을 반환해도 timeout으로 우선 분류한다.
2. 임계 안에서 예외, JSON Schema 불일치, 기대 outcome/code/assertion 불일치는 `error`다.
3. 그 외는 `success`다.

예외와 스키마 불일치가 모두 있는 것처럼 보이는 중복 집계는 금지한다. timeout 우선 규칙으로
전체 분모와 bucket 합을 항상 같게 유지한다.

### 4.4 timeout 확정값

| timeout class | tool | 임계 |
|---|---|---:|
| `search` | `search_endpoints`, `search_documents` | 5.0 s |
| `read` | 나머지 7개 tool | 2.0 s |
| `resource` | `get_raw_document` 보조 게이트 | 2.0 s |

측정 구간은 `mcp.call_tool()` 직전 `time.perf_counter()`부터 구조화 결과 또는 예외가
돌아온 직후까지다. DB·시드·AppState 생성은 제외한다. fake embedding을 쓰므로 5초는
검색 알고리즘의 품질 목표가 아니라 dead/blocked 회귀를 찾는 넉넉한 기능 게이트다.

개별 호출을 강제 취소하는 장치로 이 값을 오해하면 안 된다. 현재 tool 본문은
`anyio.to_thread.run_sync`를 사용하므로 취소해도 이미 실행 중인 동기 DB 작업을 안전하게
중단한다는 보장이 없다. 러너는 완료 후 실제 벽시계를 분류하고, 전체 프로세스 hang 보호는
운영자가 명령에 외부 watchdog을 적용하는 별도 안전장치로 둔다.

### 4.5 지표 공식과 게이트

```text
N = success_count + error_count + timeout_count
Tool Success Rate = success_count / N
Error Rate = error_count / N
Timeout Rate = timeout_count / N
MCP Error / Timeout Rate = (error_count + timeout_count) / N
                         = 1 - Tool Success Rate
```

전체 판정은 다음 둘을 **모두** 만족해야 PASS다.

```text
Tool Success Rate >= 0.99
MCP Error / Timeout Rate < 0.01
```

두 지표가 상보이므로 실패율이 정확히 1.00%이면 첫 조건은 만족하지만 두 번째의 strict
inequality는 실패한다. 러너는 임계값을 반올림 문자열로 비교하지 않고 정수 카운트의
교차곱으로 비교한다. 출력 반올림은 소수점 둘째 자리 백분율로 하되 판정에는 쓰지 않는다.

expected rejection은 success bucket 안에서 별도 카운트만 보여준다. 이를 Error Rate에
다시 더하면 상보 관계가 깨지므로 금지한다.

## 5. 결정 3 — 시나리오셋

### 5.1 파일 계약

위치: `tests/fixtures/mcp_eval/scenarios.json`

```json
{
  "schema_version": 1,
  "default_repeat": 5,
  "scenarios": [
    {
      "id": "endpoint.search.hit",
      "tool": "search_endpoints",
      "arguments": {"query": "find pet by id"},
      "expected": {
        "outcome": "success",
        "assertions": [
          {"op": "contains", "path": "items", "value": {"path": "/pet/{petId}"}}
        ]
      }
    },
    {
      "id": "endpoint.search.blank",
      "tool": "search_endpoints",
      "arguments": {"query": "   "},
      "expected": {"outcome": "error", "code": "validation_error"}
    }
  ]
}
```

- scenario id는 유일해야 한다.
- timeout은 scenario 작성자가 임의 완화하지 못하게 §4.4 tool mapping에서만 정한다.
- 인자 문자열의 `${openapi_document_id}`, `${pet_endpoint_id}` 같은 placeholder는 setup이
  만든 handle map으로 치환한다. 미정의 placeholder는 preflight 실패다.
- 기대 error에는 `code`가 필수고 success에는 `code`를 허용하지 않는다.
- fixture 로더는 필수/허용 key를 엄격히 검사한다. 알 수 없는 key를 묵살하지 않는다.

### 5.2 고정 21개

| # | id | tool | 입력 요지 | expected |
|---:|---|---|---|---|
| 1 | `document.list.seeded` | `list_documents` | 전체 | success, Petstore document 포함 |
| 2 | `document.list.project_miss` | `list_documents` | 미존재 project | success, 빈 list |
| 3 | `collab.search.hit` | `search_documents` | `로그인` | success, Drive seed 포함 |
| 4 | `collab.search.no_match` | `search_documents` | 고정 nonsense query | success, `items=[]` |
| 5 | `collab.search.blank` | `search_documents` | 공백 query | error `validation_error` |
| 6 | `collab.get.drive_hit` | `get_document` | Drive seed source/id | success, source/content 일치 |
| 7 | `collab.get.missing` | `get_document` | Drive 미존재 id | error `integration_error` |
| 8 | `endpoint.search.hit` | `search_endpoints` | `find pet by id` | success, `/pet/{petId}` 포함 |
| 9 | `endpoint.search.no_match` | `search_endpoints` | 고정 nonsense query | success, `items=[]` |
| 10 | `endpoint.search.blank` | `search_endpoints` | 공백 query | error `validation_error` |
| 11 | `endpoint.details.hit` | `get_endpoint_details` | seed endpoint id | success, method/path 일치 |
| 12 | `endpoint.details.missing` | `get_endpoint_details` | 미존재 endpoint id | error `endpoint_not_found` |
| 13 | `schema.resolve.hit` | `resolve_ref` | `#/components/schemas/Pet` + doc id | success, name=`Pet` |
| 14 | `schema.resolve.missing` | `resolve_ref` | 미존재 schema | error `schema_ref_not_found` |
| 15 | `schema.resolve.bad_format` | `resolve_ref` | `Pet` | error `validation_error` |
| 16 | `tag.list.seeded` | `list_tags` | seed doc id | success, `pet` count >= 1 |
| 17 | `tag.list.missing_doc` | `list_tags` | 미존재 doc id | error `document_not_found` |
| 18 | `source.drive.seeded` | `list_drive_sources` | seed project | success, folder mapping 1건 |
| 19 | `source.drive.project_miss` | `list_drive_sources` | 미존재 project | success, `items=[]` |
| 20 | `source.notion.seeded` | `list_notion_sources` | seed project | success, database mapping 1건 |
| 21 | `source.notion.project_miss` | `list_notion_sources` | 미존재 project | success, `items=[]` |

정상 14개 + 의도한 오류 7개다. 기본 반복은 scenario별 5회, 총 105회다. 반복은
read-only 호출에만 적용하므로 상태 누적이 없어야 한다. 각 repeat를 round-robin으로
실행하고 순서 셔플은 하지 않는다. 동일 입력·동일 순서가 회귀 비교에 유리하다.

기본값보다 작은 `--repeat`는 디버깅에는 허용하되 총 실행이 100 미만이면 목표치 판정을
`INSUFFICIENT`로 출력하고 종료 코드 2를 반환한다. 작은 분모에서 99% 숫자만 표시해
신뢰도를 과장하지 않는다. 도구별 비율은 진단용이고 목표치 PASS/FAIL은 전체 분모에만 적용한다.

## 6. 결정 4 — 최소 결정론 시드

27번 Stripe/GitHub 전체 코퍼스는 검색 순위·노이즈 분포를 측정하기 위해 필요했다. 이번
하네스의 질문은 응답 계약과 오류 변환이므로 대형 색인은 측정값을 더 정확하게 하지 않고
setup 시간과 변동만 늘린다. 따라서 별도 최소 시드로 확정한다.

### 6.1 재사용 자산

- OpenAPI: `tests/fixtures/samples.py`의 `openapi_3_json()` Petstore 문서
- 협업 소스 어댑터: `tests/fixtures/document_sources.py`의 `FakeDocumentSource`
- embedding: `HashEmbeddingProvider(dim=EMBEDDING_DIM)`, `is_semantic=false`
- DB 수명: `tests/fixtures/rrf_eval/compare_strategies.py`의
  `_make_temp_db` / `_drop_temp_db`

semantic embedding을 쓰지 않는 이유는 검색 품질을 채점하지 않기 때문이다. hit scenario의
질의와 title/path는 keyword로 결정적으로 잡히게 고정한다. `is_semantic=false`를 stdout에
반드시 출력하여 27번 품질 결과와 혼동하지 않게 한다.

### 6.2 새 seed 파일

위치: `tests/fixtures/mcp_eval/seed.json`

- project `eval-openapi`: Petstore OpenAPI 1건
- project `eval-collab`: Drive mapping 1건, Notion mapping 1건
- Drive 문서 1건: title에 `로그인`, 고정 external_id/body/url
- Notion 문서 1건: Drive와 다른 고정 title/external_id/body/url

Petstore 원문 자체는 `seed.json`에 복제하지 않고 기존 fixture 함수를 참조한다. 협업 문서
리터럴만 seed 파일에 둔다. seed 파일 전체의 SHA-256을 실행 메타데이터에 출력한다.

### 6.3 setup 순서와 실패 처리

1. 임시 DB 생성, 확장 및 `create_all`.
2. fake source builder와 hash embedding을 주입해 `AppState` 생성.
3. Petstore 등록, Drive/Notion mapping 등록, fake source refresh 및 본문 색인.
4. 실제 생성된 document/endpoint/external id를 handle map에 바인딩.
5. scenario/tool/output schema/placeholder preflight.
6. 21개 시나리오 채점.
7. `finally`에서 임시 DB drop.

setup용 mutation은 service bundle 또는 기존 MCP 호출을 사용할 수 있지만 **절대 분모와 latency에
넣지 않는다**. setup이나 preflight가 실패하면 부분 지표를 출력하지 않고 `SETUP_ERROR`와 원인,
종료 코드 2를 반환한다. 준비 실패를 tool error인 것처럼 분모에 섞으면 제품 실행과 fixture 결함을
구분할 수 없다.

실제 Google/Notion builder, 네트워크 fetch, 환경 자격증명 사용은 금지한다. developer는 환경에
실자격증명이 있어도 fake builder가 고정됨을 테스트해야 한다.

## 7. 결정 5 — 러너 계약

### 7.1 위치와 실행

`tests/fixtures/mcp_eval/run_mcp_eval.py`는 pytest가 수집하지 않는 독립 스크립트다.

```bash
docker compose up -d postgres
uv run python tests/fixtures/mcp_eval/run_mcp_eval.py [--repeat 5]
```

필수 CLI:

- `--repeat N`: scenario별 반복 수, 기본 `scenarios.json.default_repeat`(5), 양의 정수
- `--scenarios PATH`: 기본은 같은 디렉터리의 `scenarios.json`; 로컬 진단용 override
- `--seed PATH`: 기본은 같은 디렉터리의 `seed.json`; 로컬 진단용 override

timeout override CLI는 두지 않는다. 목표치의 의미가 실행자마다 바뀌면 결과 비교가 불가능하다.

### 7.2 ponytail 재사용 원칙

- `_make_temp_db` / `_drop_temp_db`를 27번이 사용하는 기존 helper에서 import한다.
- `create_db_engine`, `create_all`, `AppState.from_engine`, `create_mcp_server`를 그대로 사용한다.
- `FakeDocumentSource`, `openapi_3_json`, `HashEmbeddingProvider`를 기존 위치에서 import한다.
- 임시 DB 생성/drop, fake source, 샘플 OpenAPI, embedding stub을 새 파일에 복사하지 않는다.
- 새 코드는 scenario/seed strict loader, placeholder binder, schema/expectation validator,
  elapsed classifier, aggregation/formatter에 한정한다.

검색 품질용 `recall_at`/MRR 함수는 이 비율에 억지로 재사용하지 않는다. 재사용 대상은 같은
책임의 DB/fixture/runner 골격이며, 다른 의미의 metric 함수를 공유하면 오히려 결합이다.

### 7.3 preflight 불변식

실행 전 다음을 모두 검사하고 하나라도 어기면 채점하지 않는다.

1. scenario/seed `schema_version == 1`.
2. scenario id 유일, 고정 9개 tool 외 이름 없음.
3. 서버의 실제 tool 목록에 9개가 모두 존재.
4. 각 tool의 `output_schema`가 존재하고 JSON Schema 자체가 유효.
5. 모든 placeholder 해소.
6. expected outcome/code/assertion DSL 구조 유효.
7. repeat 양수; PASS/FAIL을 내는 실행은 총 호출 100 이상.
8. seed SHA-256 계산 및 출력.

### 7.4 출력 계약

stdout은 그대로 `docs/eval-results/` 기록에 붙일 수 있는 Markdown이어야 한다.

```text
# MCP 계층 평가 YYYY-MM-DD

- commit SHA: ...
- layer: in-process FastMCP.call_tool (no stdio)
- seed_sha256: ...
- is_semantic: false
- scenarios: 21
- repeat: 5
- measured calls: 105
- timeout: search=5.0s, read=2.0s

## 도구별 결과
| tool | n | success | expected rejection | error | timeout |
...

## MCP 계층
| 지표 | 측정값 | 목표치 | 판정 |
| Tool Success Rate | 105/105 (100.00%) | >= 99% | PASS |
| MCP Error / Timeout Rate | 0/105 (0.00%) | < 1% | PASS |
| - Error Rate | 0/105 (0.00%) | 분해 | INFO |
| - Timeout Rate | 0/105 (0.00%) | 분해 | INFO |

## 실패 상세
| scenario | repeat | tool | elapsed_ms | class | expected | observed | detail |
...

## MCP resource conformance (분모 밖)
| resource | n | success | error | timeout | 판정 |
...
```

실패 상세에는 exception class와 message를 기록하되 stack trace는 기본 stdout 표에 넣지 않는다.
비밀이나 원문 전체 payload도 출력하지 않는다. 디버그 로그에서만 traceback을 허용한다.

러너는 `docs/eval-results`를 자동 수정하지 않는다. 실제 실행자가 stdout을
`YYYY-MM-DD_mcp_eval.md`로 새로 저장한다. `docs/eval-results/README.md` 구현 변경에는
이 파일명과 B 트랙 명령, `서버 로그 산출` 문구를 `MCP 평가 하네스 산출`로 바꾸는 작업을
포함한다. 기존 결과 파일은 수정하지 않는다.

### 7.5 종료 코드

| code | 의미 |
|---:|---|
| 0 | 두 metric 목표 통과 + 보조 resource 게이트 통과 |
| 1 | metric FAIL 또는 resource conformance FAIL |
| 2 | 설정/seed/preflight/setup 오류 또는 표본 100 미만으로 판정 불가 |

실패가 발생해도 21개 scenario 실행은 계속하여 분해 표를 완성한다. 단, timeout 뒤 제품 코드의
동기 작업이 계속 실행 중일 수 있으므로 DB 무결성 오류가 이어지면 후속 오류도 그대로 기록하고
최종 drop을 시도한다.

## 8. 구현 파일과 검증 계약

developer 변경 범위:

1. `tests/fixtures/mcp_eval/scenarios.json`
2. `tests/fixtures/mcp_eval/seed.json`
3. `tests/fixtures/mcp_eval/run_mcp_eval.py`
4. runner 순수 로직 단위 테스트(예: `tests/unit/test_mcp_eval_runner.py`)
5. `pyproject.toml` / `uv.lock`의 `jsonschema` 직접 dependency
6. `docs/eval-results/README.md`의 B 트랙 실행·결과 파일 계약

필수 테스트:

- expected ErrorPayload가 success + expected rejection으로 집계됨
- 잘못된 error code, 정상/오류 outcome 역전, assertion 실패가 error로 집계됨
- schema-invalid structured payload가 error로 집계됨
- 임계와 같은 elapsed는 timeout, 늦은 정상 payload도 timeout 우선
- `success + error + timeout == N` 및 combined rate가 정확히 `1-success rate`
- 정확히 1% 실패는 strict `<1%` 게이트 FAIL
- tool별 output schema가 full `structured_content`를 검증함
- get_raw_document 결과가 tool 분모를 바꾸지 않음
- setup 실패·미정의 placeholder·미등록 tool·100 미만 표본이 종료 코드 2
- runner가 실제 외부 source builder나 HTTP를 호출하지 않음

## 9. 열린 항목

구현을 막는 열린 설계 판단은 없다. stdio B안, write tool, 운영 장기 SLO는 모두 이번 지표에
조용히 섞지 않고 별도 트랙으로만 연다.
