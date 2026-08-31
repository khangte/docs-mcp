# 95. P1 vector-only reformulation 최종 반려 판정

- 대상: `DOCS_MCP_SEARCH_VECTOR_REFORMULATION_ENABLED` P1 candidate 구현 워킹트리
- 설계: `docs/architect-review/94_p1_vector_only_reformulation_design.md`
- 실행 근거: `docs/eval-results/12_2026-08-31_p1_vector_reformulation_eval.md`
- 판정: **구현 정합성 PASS, P1 candidate FAIL·폐기. production P1 코드는 되돌린다.**
- 다음 설계: P3 cross-encoder rerank를 P1과 독립된 candidate로 검토한다.

## 1. 구현 정합성과 후보 판정을 분리한다

developer 구현은 94의 product boundary를 지켰다.

- `vector_reformulations`는 feature flag OFF가 기본이고, `query_variants`와 상호배타다.
- 서버는 NFKC/공백 정규화·dedup·max2 cap만 수행한다. 번역/alias/LLM 호출은 없다.
- P1 입력은 RRF vector arm에만 전달되고, original+reformulation best-rank 병합 뒤 width 50으로
  재절단된다.
- keyword, exact, fallback, RRF 식·weight·`RRF_K`, P2 quota 0은 변경하지 않는다.
- 단위/RRF/MCP integration 테스트와 대상 ruff 검사를 이번 검토에서 재실행해 통과했다.

따라서 반려 사유는 구현 품질이나 설계 이탈이 아니다. P1이 94에서 사전 고정한 legacy
effectiveness와 both-arm protection HARD를 통과하지 못했기 때문이다.

## 2. 실행 결과와 HARD FAIL

| 항목 | 94의 요구 | 12의 결과 | 판정 |
|---|---|---|---|
| generation admission | q04/q07/q10 모두 vector top-50 | 3/3 | PASS |
| targeted final recovery | 셋 중 2건 이상 top-10 회복 | q04만, 1/3 | FAIL |
| legacy R@10 | 09 대비 +2건 이상 | +1건, .45→.50 | FAIL |
| MRR/nDCG | 비감소 | .318→.343 / .350→.382 | PASS |
| C1·route pair·accepted | loss 0 | PASS | PASS |
| both-arm subset | baseline final `both`가 candidate final에 모두 남음 | q10에서 2 ref 탈락 | **HARD FAIL** |

q04의 final 2위 회복은 P1 mechanism이 client-supplied English expression으로 vector admission을
바꿀 수 있음을 보인다. 그러나 제품 후보는 세 target 중 하나만 top-10에 회복했고, R@1은 .25로
불변이다. 무엇보다 q10의 both-arm subset FAIL은 aggregate 지표 상승으로 상쇄할 수 없는
사전 계약 위반이다.

94 §6.4는 HARD 전항과 targeted recovery·R@10 하한을 모두 통과한 candidate만 새 sealed split을
만들도록 했다. 따라서 sealed split을 만들지 않은 12의 처리는 맞으며, P1을 같은 fixture에서
재실행하거나 input 표현을 교체해 통과시키지 않는다.

## 3. q10 FAIL은 P1 contract 안의 근본 한계다

q10 `show my billing history`에서 client의 `list invoices`는 accepted invoice를 vector top-50에
새로 입장시켰다. 즉 client-side semantic reformulation 자체가 고장 난 것이 아니다. 실패는 그
다음 RRF topology에서 발생했다.

```text
원문 keyword arm: billing 계열 후보 다수
원문/강화 vector arm: billing 계열 + invoice 후보
unchanged equal-weight RRF: billing 양-arm(both) 후보가 final top-10을 점유
invoice: vector-only 한 기여
```

invoice를 final top-10으로 올리려면 final `both` 후보 하나 이상이 빠져야 한다. 그러나 94 §6.2는
baseline final `both` ref의 부분집합 보존을 HARD로 고정했다. 실제 실행도 vector rank가 변하면서
기존 `both` 두 건이 빠졌고, invoice는 끝내 들어오지 못했다.

따라서 다음은 P1 안에서 허용되지 않는다.

- `list invoices`를 다른 q10 전용 표현으로 바꾸거나 추가 표현을 더 주는 것
- q07의 `delete a repository`를 더 구체적인 표현으로 바꾸는 것
- reformulation 수·vector width를 늘리는 것
- both-arm subset guard를 완화하거나 P2처럼 slot을 교체하는 것

첫 두 항목은 legacy 결과를 본 뒤 client instruction/fixture input을 조정하는 재튜닝이다. 뒤의 두
항목은 P1 budget·noninterference contract를 바꾸는 새 architecture다. 특히 q10은 표현을 바꿔
vector rank를 높여도 `both` 보호와 vector-only 단일 기여를 동시에 만족시킬 수 없으므로, semantic
표현 지침의 개선으로 해결할 문제가 아니다.

q07도 같은 결론을 바꾸지 않는다. q07은 vector top-50에 들어왔으나 root delete보다 sub-resource
delete decoy의 vector rank가 높았다. 더 좋은 client phrase가 있을 수 있다는 가능성은, 노출된
q07 결과를 보고 phrase를 바꿔 같은 P1 candidate를 시험할 권한이 아니다.

## 4. 코드 처분 — P2와 달리 되돌림

**P1 production code는 커밋하지 않고 워킹트리에서 되돌린다.** failure evidence 문서만 보존한다.

P2는 효과성이 부족했지만 자신이 고정한 HARD를 위반하지 않았고, lead가 기본 quota 0의 dark
candidate 보존을 선택했다. P1은 명시적인 candidate-specific HARD(`both-arm subset`)를 실제로
FAIL했다. 기본 OFF라도 실패한 MCP public input, setting, plumbing, 테스트를 코드베이스에 남기면
향후 사용자가 flag를 켜거나 API를 의존할 수 있어 반려 상태가 불명확해진다.

developer cleanup 범위:

1. `app/core/config.py`의 P1 flag
2. `app/composition.py`의 P1 state·wiring
3. `app/mcp/tools/endpoints.py`의 `vector_reformulations` public input·docstring
4. `app/services/search/endpoint_candidate_search.py`의 P1 option, normalizer, flag 분기,
   vector truncate branch
5. P1 전용 unit/integration 테스트

P2 `DOCS_MCP_SEARCH_ARM_RESCUE_QUOTA=0` dark candidate와 그 테스트는 유지한다. 94 설계,
12 실행 결과, 이 95 verdict는 실패·anti-retuning 증거이므로 삭제하지 않는다.

cleanup 뒤에는 다음만 확인한다.

- default RRF, fallback, exact, legacy `query_variants`, P2 quota 0의 per-query output이 P1 전
  baseline과 동일
- P1 public schema field가 사라짐
- P1 제거가 P2 implementation에 영향을 주지 않음

## 5. 다음 단계 — P3만 별도 설계로 진행

다음 후보는 **P3 cross-encoder rerank 설계**다. P3는 P1 code/input을 재사용하거나 P1 ON
candidate set에 붙이지 않는다. baseline production-wide candidate set을 고정한 뒤 query-endpoint
joint relevance만 재평가하는 새로운 candidate identity가 필요하다.

P3가 직접 다룰 수 있는 대상은 current top-10의 shallow-rank q13(6), q15(2), q19(5), q20(2)다.
따라서 R@1/MRR/nDCG 개선 후보이지 q07/q10 generation miss 해결책으로 부르지 않는다. P1이
남긴 generation-miss 문제를 다시 다루려면, both-arm 보호와 vector-only best-rank merger 중 어느
경계를 바꿀지 명시한 **별도 retrieval architecture**와 새 fixture를 먼저 설계해야 한다.

P3 설계의 선행 조건은 다음이다.

- candidate-set parity: reranker 전 pool은 baseline과 완전 동일
- exact prelude 보존, C1 gross loss 0, root/child non-regression
- P1/P2 없이 단독 평가
- legacy exposed set은 diagnostic만 사용하고 새 sealed split에서 승급 판단

## 6. 최종 판정표

| 쟁점 | 판정 |
|---|---|
| P1 구현의 설계 정합성 | PASS |
| P1 legacy diagnostic | FAIL |
| both-arm subset q10 | HARD FAIL |
| reformulation 지침/입력 재조정 | 반려 — 결과 후 재튜닝 |
| P1 code | 되돌림; dark candidate로 보존하지 않음 |
| sealed split | 미작성·미개봉 유지 |
| 다음 작업 | P3 독립 설계 |

P1은 candidate generation admission 자체는 입증했지만, 안전하게 endpoint 반환 품질로 연결하지
못했다. 이 후보를 더 많은 phrase, 더 넓은 vector arm, slot 정책 변경으로 연장하지 않고 종료한다.
