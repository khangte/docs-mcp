# 99. P3 feature-ON legacy diagnostic 판정

- 입력: `docs/eval-results/15_2026-09-01_p3_cross_encoder_feature_on_diagnostic.md`
- 구현 기준: `41f59bb` (P3 code, flag default off)
- 판정: **promotion 불가·sealed split 미진행. 현 P3는 flag-off dark diagnostic candidate로만 보존한다. q16/top-1 lock 추가와 GPU 전환은 현 candidate에 대한 후속 패치가 아니라 별도 설계 대상이다.**

## 1. 판정 요약

P3는 legacy-20에서 lock 밖 final-cut q05/q06/q17/q18을 모두 회복해 R@10 `0.45→0.65`,
MRR `0.318→0.348`, nDCG@10 `0.350→0.421`이라는 유의미한 diagnostic 효과를 보였다. 그러나
아래 두 독립 gate를 모두 통과하지 못했다.

1. q16의 baseline rank-1 accepted가 2위로 강등되어 route-pair 및 C6 category-MRR HARD가
   실패했다.
2. 현재 설계의 CPU local reranker가 WSL2 CPU box에서 rerank 추가 p95 `76,091 ms`로
   계약 상한 `250 ms`를 약 300배 초과했다.

따라서 강한 legacy 효과는 safety/latency failure를 상쇄하지 않는다. `15`가 sealed NOT RUN 및
promotion=no를 선언한 처분은 맞으며, sealed fixture를 저작·개봉하거나 승격 근거로 사용하지
않는다.

## 2. q16 rank 1→2 — 현 계약의 근본 결함인가

### 2.1 관측과 현재 lock의 범위

q16의 `POST /v1/refunds`는 baseline final 1위, `match_type != both`였고 P3 score에서 2위가
되어 rank 2로 내려갔다. top-10에는 남아 `regressed_accepted=[]`는 PASS지만, route-pair의
capped rank와 C6 category MRR은 rank quality를 보므로 각각 FAIL이다. 두 route-pair 행은
`/v1/refunds`와 `/v1/subscriptions`에 속한 동일 q16 사건의 중복 집계일 뿐, 별도 회귀 두 건이
아니다.

96의 slot lock은 P1 q10에서 사라졌던 **baseline final `both` ref의 id·slot·순서**만 보존한다.
rank-1 single-arm ref의 절대 순위를 보장한 적은 없다. 그러므로 q16은 `both` lock 구현 결함이
아니라, learned reranker가 non-both base winner를 재평가할 때 본래 가능한 회귀를 실제로 드러낸
것이다.

### 2.2 top-1 lock을 이번 candidate에 넣지 않는 이유

`base_wide[0]`을 match type과 관계없이 고정하는 top-1 lock은 q16을 일반 규칙으로 막을 수 있다.
하지만 이는 both subset lock의 자연스러운 bug fix가 아니라 **새 P3-R1 contract**다.

- base rank-1이 decoy이고 accepted가 2~10위인 질의에서는 P3가 Recall@1/MRR을 개선할 경로를
  닫는다. 이는 96이 P3의 직접 대상으로 둔 shallow-rank ranking 개선과 충돌한다.
- q16을 본 뒤 “rank 1만 고정”을 추가하면, observed one-query regression을 없애는 방향으로
  candidate output 공간을 좁히는 결과-후 변경이다. top-1이라는 일반 규칙이라는 사실만으로
  calibration/holdout 경계를 면제받지 않는다.
- floor, score margin, `both` 외 다른 match-type 예외를 덧붙이는 방식도 동일하게 새로운 tuning
  surface를 만든다.

따라서 **현 P3에는 q16 교정 코드를 추가하지 않는다.** 제품상 base rank-1 보존이 별도 요구로
승인되면 P3-R1을 독립 architecture로 설계하고, base rank-1 decoy·C1·root/child·C6 all-of를
포함한 새 calibration 및 sealed split으로 처음부터 검증해야 한다. 그 전에는 q16 HARD FAIL이
현 candidate의 유효한 판정이다.

## 3. 지연 — CPU candidate의 hard blocker, GPU는 별도 설계

`15`의 50-pair×512-token warm rerank p95 약 76초는 특정 query, cold load, 또는 DB 이상치가
아니다. 17 warm 질의 전체가 약 75초로 평탄하고 baseline p95는 31ms이므로, pinned 0.6B F32
model의 local CPU inference throughput이 현재 병목이다. RSS 3.65GiB와 load 1.78초는 timeout
원인이 아니지만, p95 contract를 완화하지 않는다.

이 측정은 WSL2/8 logical CPU box에만 직접 적용된다. 따라서 production CPU의 절대 p95를
추정해 “모든 CPU에서 불가능”이라고 단정하지는 않는다. 다만 다음은 확정이다.

- **현 실행 환경에서는 §6.2 HARD FAIL**이므로 feature ON/ sealed/승급은 즉시 막힌다.
- 실제 배포를 고려하려면, feature-ON을 올릴 **명시된 production CPU hardware와 concurrency**에서
  동일 N=50, tokenizer cap, model revision으로 warm p50≤200ms, end-to-end p95≤500ms, rerank
  delta p95≤250ms를 먼저 재측정해야 한다. 그 측정 없이는 판정을 보류하는 것이 아니라
  **promotion이 보류**다.
- GPU, quantization, ONNX/TEI serving, smaller model, N 축소는 96의 `device(CPU)`, pinned model,
  local provider lifecycle, candidate identity 중 하나 이상을 바꾼다. GPU 수치가 좋더라도 현 P3의
  config toggle로 간주하지 않으며, 배포 GPU 의존성·capacity·fallback·새 asset digest를 정하는
  별도 설계/승인이 필요하다.

즉 production CPU 계측은 현 CPU P3를 계속 검토하기 위한 선행조건이고, GPU 계측은 현 실패의
간단한 재시도가 아니라 다른 candidate의 feasibility 탐색이다.

## 4. 코드·산출물 처분

| 대상 | 처분 | 이유 |
| --- | --- | --- |
| `41f59bb` P3 code/unit tests | **유지, flag default off** | P2와 달리 legacy 효과가 R@10 +4 hit 및 ranking 지표 개선으로 명확하며, OFF parity·asset-failure fail-closed·both slot lock도 통과했다. 단 dark diagnostic candidate일 뿐 enabled promotion 근거는 아니다. |
| q16/top-1 lock 등 추가 수정 | **하지 않음** | 현재 결과 후 P3-R1 contract를 암묵적으로 도입하는 재튜닝이므로 별도 설계 승인 없이는 금지. |
| `docs/eval-results/15_2026-09-01_p3_cross_encoder_feature_on_diagnostic.md` | **보존·커밋** | asset identity/digest, legacy 효과, HARD/latency FAIL, sealed NOT RUN을 재현 가능한 audit으로 남긴다. |
| `cross_encoder_asset_manifest.json` | **보존·커밋** | 15가 참조하는 pinned revision·Apache-2.0·파일 digest audit이다. 실제 2.19GiB asset 자체는 repository에 넣지 않는다. |
| 미커밋 `pyproject.toml`/`uv.lock` exact pin | **되돌림** | flag-off 제품 경로에 새 package-resolution lock을 강제할 필요가 없고, CPU P3는 gate를 통과하지 못했다. 향후 승인된 candidate가 실제 serving dependency를 다시 결정한다. |

이 처분은 asset cache를 삭제하라는 뜻이 아니다. 이미 승인된 prep-time asset은 local cache에 둘 수
있으나, feature ON 및 runtime download는 계속 금지다.

## 5. 다음 단계와 금지선

1. sealed split은 만들거나 열지 않는다. q16 HARD와 target-hardware latency gate가 모두 해소된
   **새로 승인된 candidate**에만 architect/lead가 새 fixture를 동결한다.
2. 현 CPU P3를 계속 검토하려면 production CPU benchmark를 먼저 제출한다. 같은 identity에서
   §6.2 p95를 통과하지 못하면 P3 CPU candidate는 최종 반려하고 code cleanup을 별도 지시한다.
3. top-1 preservation 또는 GPU/quantized/smaller reranker를 원하면, current P3 결과를 맞추는
   patch가 아니라 P3-R1 또는 P3 acceleration의 새 설계안으로 다시 회부한다. 기존 legacy/96
   sealed split은 재사용하지 않는다.
4. q08/q09/q11/q12는 both-arm lock 때문에 여전히 P3 범위 밖이고, q04/q07/q10은 generation
   miss다. P3 diagnostic 개선을 이 일곱 건의 해결로 과대해석하지 않는다.

