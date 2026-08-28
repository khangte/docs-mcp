# 검색 품질 진단 2026-08-27 — variants off/on · route-family trace

`docs/architect-review/67_search_quality_2026_08_27_next_step_verdict.md` §1이
요구한 진단. headline 재측정이 아니라 **C2·C3·C4 실패 9건의 유형 분류**가 목적이다.

- 실행자 / commit SHA: 429302c
- 코퍼스 content_sha256: stripe=3653ad45bbec, github=80850db290cd
- 색인: stripe 589 endpoints + github 1220 endpoints
- 임베딩: intfloat/multilingual-e5-small (dim 384), is_semantic: true
- 전략: rrf 고정
- 명령: `uv run python tests/fixtures/corpus_eval/diagnose_variants.py --top-k 10 --wide 50`
- route family 키: path 첫 두 세그먼트

## 결과 요약

| 질의                            | 카테고리 | variants              | off top10 | on top10 | off top50 | on top50 | 분류                                        |
| ------------------------------- | -------- | --------------------- | --------- | -------- | --------- | -------- | ------------------------------------------- |
| q04 고객 새로 등록하고 싶어     | C2       | create a new customer | 미검출    | **1**    | 미검출    | 1        | OK — variants 해결                          |
| q05 결제 환불 처리해줘          | C2       | refund a payment      | 미검출    | 미검출   | 35        | 41       | FAMILY-RERANK 후보                          |
| q06 이슈 새로 만들기            | C2       | create a new issue    | 미검출    | **3**    | 16        | 9        | OK — variants 해결                          |
| q07 저장소 삭제해줘             | C2       | delete a repository   | 미검출    | 미검출   | 미검출    | 22       | FAMILY-RERANK 후보 (variants가 후보군 유입) |
| q08 cancel my recurring payment | C3       | (없음)                | 미검출    | —        | 39        | —        | FAMILY-RERANK 후보                          |
| q09 shut down a repository      | C3       | (없음)                | 미검출    | —        | 24        | —        | FAMILY-RERANK 후보                          |
| q10 show my billing history     | C3       | (없음)                | 미검출    | —        | 미검출    | —        | **CANDIDATE-GEN 실패** (어휘 갭)            |
| q11 customer                    | C4       | (없음)                | 미검출    | —        | 미검출    | —        | **CANDIDATE-GEN 실패**                      |
| q12 pull request                | C4       | (없음)                | 미검출    | —        | 29        | —        | FAMILY-RERANK 후보                          |

분류 규칙(67번 §1 해석 규칙 고정):

- **OK**: best(top10) ≤ 3
- **FAMILY-RERANK 후보**: top10엔 없으나 넓은 후보군 top50엔 있음 → 최종 융합/랭킹에서만 밀림
- **CANDIDATE-GEN 실패**: top50 넓은 후보군에도 accepted 없음 → rerank 대상 아님, 색인 표현/후보 생성 문제

## 유형별 집계 (9건)

| 유형                              | 건수 | 질의                    |
| --------------------------------- | ---- | ----------------------- |
| OK (variants가 해결)              | 2    | q04, q06                |
| FAMILY-RERANK 후보                | 5    | q05, q07, q08, q09, q12 |
| CANDIDATE-GEN 실패 (순수 어휘 갭) | 2    | q10, q11                |

## 핵심 발견

### 1. route-family 편향이 주범 — 9건 중 7건이 편향에 기인

q04 off top-10은 전부 `/v1/customers/{customer}/...` **child** 리소스로 채워지고
루트 `POST /v1/customers`가 한 자리도 없다. variants on을 주면 같은 질의가 1위로
올라온다. q11(`customer`)도 off top-10 10자리 중 9자리가 `/v1/customers/{customer}/...`
child, 루트 `GET /v1/customers`는 top50에도 없다.

짧은 정확 경로가 토큰이 많은 child 경로에 vector·keyword 양 arm에서 함께 밀린다.
66번이 지목한 route-family 내부 랭킹 편향이 Stripe·GitHub 양쪽에서 재현됐다.

### 2. variants가 C2의 절반을 즉시 해결

- q04: off 미검출 → on **1위**
- q06: off 미검출 → on **3위**
- q05·q07: on top10 회복은 못 했지만 후보군 유입은 시킴 (q07 top50 미검출 → 22위)

즉 C2 붕괴의 1차 원인 일부는 한글↔영문 **어휘 갭**이고, variants 경로가 그 부분을
메운다. 남은 q05·q07은 어휘 갭을 메운 뒤에도 family 내부에서 밀리는 잔여 건이다.

### 3. 순수 어휘 갭은 2건 — rerank로 못 고침

- q10 `show my billing history` ↔ `GET /v1/invoices`: top50에 invoice 계열 endpoint가
  하나도 없다. off top-10은 전부 `billing/usage`·`billing/meters` 계열. "billing
  history"라는 표현이 `invoices` 리소스와 색인 표현상 연결되지 않는다.
- q11 `customer` bare word: 루트 `GET /v1/customers`가 top50 밖. bare word + 단일
  루트 정답이라 66번 B가 이미 진단/게이트 분리 대상으로 판정한 건.

이 2건은 후보 생성/색인 표현 문제이지 최종 랭킹 문제가 아니다.

## 66번·67번 판정에 따른 다음 단계 매핑

| 유형               | 건수 | 다음 레버                                                                                                                                |
| ------------------ | ---- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| FAMILY-RERANK 후보 | 5    | 67번 §1 → 66번 A: 질의 operation/list/create 의도 × path specificity 제한적 rerank 실험 대상. 무조건적 짧은 path boost 아님              |
| variants가 해결    | 2    | variants 경로 우선 개선. 클라 LLM이 variants를 항상 제공한다는 계약이면 C2 회복 대부분 여기서 남                                         |
| CANDIDATE-GEN 실패 | 2    | rerank 대상 아님. q11은 66번 B의 diagnostic 질의로 분리. q10은 66번 C의 lexical control(`list invoices`)과 순위 비교로 어휘 갭 크기 측정 |

## 원본

전체 질의별 top-10 덤프(off/on)는 러너 stdout에 있다. 재현: 위 명령 그대로.
