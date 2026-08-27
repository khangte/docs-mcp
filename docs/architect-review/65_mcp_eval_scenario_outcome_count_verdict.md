# 65. MCP eval 시나리오 outcome 개수 불일치 판정

- 상태: reviewer 지적 타당, **문서 산술만 수정**
- 대상: `64_mcp_layer_eval_harness_design.md` §5.2,
  `tests/fixtures/mcp_eval/scenarios.json`

## 판정

success↔error로 바꿀 시나리오는 **없다**. 21개 표와 구현의
실제 구성인 **success 14개 + expected error 7개**가 설계값이다.

64번의 “정상 13개 + 의도한 오류 8개” 문구는 표를 세는 과정에서 난
단순 산술 오류다. 원 요청은 tool별 정상·edge와 `expected` 계약을 요구했지,
success/error 시나리오를 13:8로 할당하지 않았다.

## 근거

- 미존재 project 목록과 no-match 검색의 빈 목록은 제품 계약상 유효한
  정상 payload다. 개수를 맞추려고 이를 expected error로 바꾸면 현재 서버
  계약과 반대가 된다.
- 표의 expected error는 공백 query 2건, 미존재 협업 문서 1건, 미존재
  endpoint 1건, schema ref 오류 2건, 미존재 tag document 1건으로 총 7건이다.
- expected error는 올바른 `ErrorPayload(code)`가 오면 Tool Success Rate의 success
  bucket에 든다. 따라서 정상/error 픽스처 비율은 목표치 판정을 조절하는
  노브가 아니다.

## 조치

1. 64번 §5.2의 설명을 `정상 14개 + 의도한 오류 7개`로 정정했다.
2. `scenarios.json`과 runner 구현은 변경하지 않는다.
3. developer는 시나리오 의미를 바꾸는 후속 수정을 하지 않는다.
