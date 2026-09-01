# 103. endpoint-representation feature-ON promotion 판정

- 평가 근거: `docs/eval-results/16_2026-09-01_endpoint_representation_feature_on_eval.md`
- 설계 근거: `docs/architect-review/101_deterministic_endpoint_representation_candidate_generator_design.md`
- 정정 근거: `docs/architect-review/102_endpoint_representation_nonsemantic_arm_verdict.md`
- 평가 candidate: `bf828bc`, projection format `v1`
- 판정: **기본 ON 승격 보류, flag-default-off dark candidate 유지, 반려하지 않음**
- 다음 단계: **endpoint-representation 전용 신규 sealed split 저작 진행 승인**

## 1. 결론

이번 legacy-20 feature-ON 결과만으로 product default를 ON으로 바꾸지 않는다. `101`은 legacy
20을 diagnostic으로만 사용하고, 새 sealed split이 승격 여부를 결정한다고 명시했다. 아직
sealed split을 저작·동결·개봉하지 않았으므로 **promotion=default ON은 현재 불가**다.

반면 candidate를 반려하거나 코드를 되돌릴 근거도 없다. 사전 HARD gate 전항과 backfill
단위검증을 통과했고, baseline accepted regression 없이 Recall@10 hit가 2건 순증했다. 이는
projection arm이 lock 밖 final-cut miss를 실제로 회복할 수 있다는 방향 증거다. 따라서 현
candidate identity를 default-off로 보존하고, 과적합되지 않은 신규 sealed split을 저작하는
단계로 진행한다.

세 선택지의 처분은 다음과 같다.

| 선택지 | 판정 | 이유 |
| --- | --- | --- |
| 승격(기본 ON) | **보류** | exposed legacy diagnostic뿐이며 sealed 효과성·production-like 운영 gate가 없음 |
| dark candidate 유지 | **승인** | HARD 전항 PASS, 회귀 0, Recall@10 `+0.10`, rollback이 단일 flag로 격리됨 |
| 반려 | **아님** | 표적 2건 회복과 q19 개선이 있고 설계가 금지한 재튜닝·안전 계약 완화가 없었음 |
| 신규 sealed split 저작 | **진행 승인** | `101` §6의 sealed 진입 전제인 HARD PASS와 projection format 동결 조건을 충족 |

## 2. 평가 증거 판정

### 2.1 사전 HARD와 운영 기초 — PASS

다음 결과를 sealed split 저작의 선행 조건 충족으로 인정한다.

- flag OFF가 09 baseline과 일치하고, ON이 재계산한 legacy base-wide도 20/20 동일하다.
- ON 3회에서 final, accepted rank, representation arm trace가 모두 동일하다.
- endpoint/projection이 `1809:1809`이고 missing, orphan, duplicate, non-v1이 없다.
- legacy `both` endpoint의 id·slot·상대순서가 보존됐고 q08/q09/q11/q12가 byte-identical이다.
- C1 gross loss, accepted regression, route-pair regression, C6 coverage regression,
  per-category regression이 모두 0이다.
- P2 quota 0, P3 disabled, fallback arm 미호출로 candidate attribution이 격리됐다.
- backfill은 inline/재실행/전삭제 후 재빌드 모두 1809 dense projection, 실패 0,
  동일 audit digest를 보였다.

요청 경로의 WSL2 계측은 ON warm p50 `27.6 ms`, p95 `41.1 ms`이고 OFF 대비 추가 p95는
`8.5 ms`였다. 이는 sealed 저작을 막을 성능 신호는 아니다. 다만 `101`이 production-like
hardware threshold의 사전 동결을 요구했으므로 이 수치만으로 제품 운영 gate를 PASS 처리하지
않는다.

### 2.2 효과성 — sealed 진행에 충분, 승격에는 불충분

legacy-20에서 Recall@10은 `0.45 -> 0.55`, miss는 `11 -> 9`, MRR은 `+0.020`, nDCG@10은
`+0.038`이었다. q06은 rank 9, q17은 rank 7로 새로 회복됐고 둘 다 canonical vector가
legacy vector보다 얕은 rank로 진입한 뒤 lock 밖 slot을 채웠다. q19도 rank `5 -> 3`으로
개선됐다. baseline hit의 탈락은 없다.

표적 7건 중 2건만 회복한 사실은 숨기지 않는다. q05는 arm에 진입했지만 final-cut에 들지
못했고, q04/q07/q10/q18은 arm에 진입하지 못했다. 그러나 `101` §2.3과 §4는 model/source가
한국어 action-resource, billing-invoice, parameter detail을 정렬하지 못하는 경우를 명시적
미보장으로 두었다. legacy 결과를 보고 alias, width, RRF weight, lock을 조절하지 않은 현재
상태에서 이는 HARD 실패가 아니라 sealed split이 일반화 여부를 반증해야 할 효과 한계다.

q08/q09/q11/q12의 회복 상한 0도 설계된 both-slot safety contract와 일치한다. 이 네 건을
움직이기 위해 lock을 완화하거나 별도 fusion/reranker를 현 candidate에 결합해서는 안 된다.

## 3. sealed split 저작 승인 범위

이번 승인은 **fixture·manifest·verifier·threshold를 저작하고 정적 감사 가능한 상태로 만드는
것까지**다. 결과 실행, gate 개봉, holdout 개봉, product promotion을 자동 승인하지 않는다.

1. 기존 legacy 20과 과거 v1/v2/v3 또는 다른 candidate가 사용한 exposed split을 재사용하지
   않는다. query/variant와 accepted endpoint tuple의 novelty를 기계 검증하고, route-pair
   block은 기존 pair family와 겹치지 않게 한다.
2. 최소 scored 96건과 unopened holdout 24건으로 구성한다. Korean/English, C1 direct,
   generation miss, keyword-blank final-cut, root/child, both saturation, C6 multi-answer,
   parameter-detail strata를 manifest quota로 사전 고정한다.
3. `docs/eval_trace_coordinate_contract.md`를 normative dependency로 두고 `final_output` rank,
   accepted rank, exact prefix, dedupe, missing ref, top-k cut의 invariant와 mutation test를
   freeze 전에 통과시킨다.
4. baseline/candidate 구현 SHA, corpus SHA, query/split SHA, embedding model revision·asset
   digest, projection version, config dump와 모든 threshold를 결과 실행 전에 manifest에
   exact-lock한다.
5. scored gate가 HARD와 EFFECTIVENESS를 모두 통과한 뒤에만 holdout을 개봉한다. holdout
   개봉 권한은 lead의 별도 승인으로 남긴다.
6. query, accepted label, strata, threshold를 candidate 결과에 맞춰 바꾸지 않는다. 최초
   개봉 뒤 split은 exposed이며 수정 candidate에 재사용하지 않는다.

## 4. candidate identity 동결

현재 legacy 증거가 sealed 저작을 승인하는 identity는 다음과 같다.

- implementation `bf828bc`, projection format `v1`, endpoint당 projection 정확히 1행
- original query only, semantic provider일 때 canonical FTS/vector를 endpoint best-rank로 결합
- arm width 50, outer RRF `k=60`, equal contribution, `query_variants` 미사용
- legacy keyword/vector arm과 public `match_type` 계약 불변
- legacy final `both` id·slot·상대순서 HARD lock
- P2 quota 0, P3 disabled, fallback bypass
- non-semantic provider에서는 verdict 102의 strict-empty 동작
- product switch `DOCS_MCP_SEARCH_ENDPOINT_REPRESENTATION_ENABLED`, default OFF

sealed 결과를 보기 전후로 alias/template, canonical field order/cap, model/revision, width,
RRF k/weight, lock, mutual exclusion, strict-empty를 바꾸면 새 candidate identity다. 그런 변경에는
이 판정의 sealed 진행 승인을 승계하지 않고 새 calibration과 architect verdict가 필요하다.

## 5. promotion 재판정의 최소 조건

sealed protocol의 상세 수치와 production hardware budget은 fixture 개봉 전에 별도 freeze
감사에서 확정한다. 최소한 다음 조건은 완화할 수 없다.

- `101` §6의 HARD 전항과 coordinate-contract HARD 전항 PASS
- baseline 대비 paired Recall@10 순증
- MRR과 nDCG@10 비회귀, `regressed_accepted=[]`, route-pair/C6/per-category 비회귀
- generation/keyword-blank target strata에서 accepted top-10 hit 순증
- production-like hardware에서 사전 동결한 request p50/p95/RSS와 index/backfill budget PASS
- 전 active corpus projection coverage 100%, 실패 시 document 단위 원자성, flag OFF rollback 확인

전항을 scored gate와 승인된 holdout 절차에서 통과한 뒤에만 기본 ON promotion을 다시 판정한다.
하나라도 실패하면 default OFF를 유지하며, 결과를 보고 같은 split에서 alias·rank·lock을
재튜닝하지 않는다.

## 6. 최종 처분

1. `bf828bc` endpoint-representation v1 코드는 **flag-default-off dark candidate로 유지**한다.
2. 지금은 **기본 ON으로 승격하지 않는다**.
3. candidate를 **반려하거나 되돌리지 않는다**.
4. 위 §3~§5 계약에 따른 **신규 sealed split 저작을 진행한다**.
5. fixture·manifest·verifier·threshold freeze 감사 전에는 sealed execution을 시작하지 않고,
   scored gate PASS 전에는 holdout을 개봉하지 않는다.
