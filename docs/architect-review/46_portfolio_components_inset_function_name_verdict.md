# 46. portfolio-components.html 계층 경계 예외 인셋 함수명 판정

- 대상: `docs/portfolio-components.html` (신규 컴포넌트 구조 문서)
- 리뷰 출처: reviewer
- 판정: **수정 필요 인정 — 지적 1건 전부 수용, architect가 직접 문서 정정**

## 1. 지적 사항

인셋 "boundary exceptions" 첫 번째 항목의 서브 라벨을
`get_endpoint_details 내부`라고 적었으나, 실제로 `DocumentRepository`를 직접
생성하는 코드는 `get_endpoint_details`가 아니라 `get_raw_document` 안에 있다.

## 2. 검증

`app/mcp/tools/endpoints.py`의 등록 지점:

| 행 | 데코레이터 | 함수 |
|----|-----------|------|
| 29 | `@mcp.tool()` | `search_endpoints` |
| 90 | `@mcp.tool()` | `get_endpoint_details` |
| 127 | `@mcp.tool()` | `resolve_ref` |
| 162 | `@mcp.tool()` | `list_tags` |
| 190 | `@mcp.resource("document://{document_id}/raw")` | `get_raw_document` |

`DocumentRepository` 직접 생성은 195행이며, 이는 190행에서 시작하는
`get_raw_document` 본문에 속한다. `get_endpoint_details`(90~126행) 범위 밖이다.
**지적은 정확하다.**

## 3. 판정

지적을 수용한다. 다이어그램의 다른 부분(의존 방향, 계층 구성, 두 번째 예외
항목)은 영향을 받지 않는다 — 잘못된 것은 예외가 발생한 **함수 이름 하나**이고,
"MCP 계층이 서비스 계층을 건너뛰고 리포지토리를 직접 만든다"는 예외 자체의
성립 여부는 그대로다.

## 4. 조치

문서 편집만으로 끝나는 건이라 developer 수정 지시 없이 architect가 직접 반영했다.

1. 인셋 서브 라벨: `get_endpoint_details 내부` → `get_raw_document — @mcp.resource`
2. 부수 정정: `tools/endpoints` 박스 서브 라벨 `4 tools · API 명세 질의` →
   `4 tools + 1 resource`

2번은 리뷰 지적 사항은 아니지만, 인셋이 `@mcp.resource`를 지목하게 된 이상
다이어그램 본체에 resource 등록이 아예 없는 것처럼 보이면 두 표기가 서로
모순되어 보인다. 같은 사실을 가리키도록 맞췄다.

## 5. 부산물 — 기존 문서의 "17 도구" 표기 해소

앞선 보고에서 `docs/portfolio-architecture.html`의 `17 도구` 표기와 실제
등록 수 16개가 불일치한다고 올렸는데, 이번 확인으로 원인이 드러났다.

- 실제 `@mcp.tool()` 등록: **16개** (documents 4 · endpoints 4 · sources 8)
- 추가로 `@mcp.resource` 등록: **1개** (`get_raw_document`)

즉 기존 문서의 17은 tool 16 + resource 1을 합산한 수치로 보인다. 틀린 수는
아니지만 라벨이 `MCP 도구`라 tool 만 세는 것으로 읽힌다. 신규 문서는
`16 MCP 도구 (4·4·8)`로 tool 만 세고, resource 는 `tools/endpoints` 박스에
별도로 표기하는 방식을 택했다.

기존 `portfolio-architecture.html` 수정 여부는 lead 판단 사항이다 — 선택지는
(a) 그대로 두기, (b) 라벨을 `MCP 도구 + 리소스`로 바꾸기, (c) `16`으로 낮추고
resource 를 별도 표기. 신규 문서와의 정합만 놓고 보면 (b)를 권한다.

## 6. 검증 결과 (reviewer 보고 기준, 재실행 없음)

- `registered_resync.py`의 `app.composition` · `app.mcp.types` 역참조: 정확
- MCP 도구 16개 구성: 정확
- 리포지토리 6개: 정확
- SVG 렌더링 및 named entity 사용: 기존 문서와 동일 관례, 문제 없음
- 스타일·톤: 기존 `portfolio-architecture.html`과 일관
