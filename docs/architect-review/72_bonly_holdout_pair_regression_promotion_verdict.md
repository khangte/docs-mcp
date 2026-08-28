# 72. B-only holdout pair 회귀·최종 승급 판정

- 대상: B-only candidate `75fa5f3f98bcd119f1f7bf3645b018cc0db4996d`
- baseline: `ecc3e7923e216bf8e6b72ed609d5990749b2f700`
- fixture/query: `ed7852e`, query SHA `6eb897d2…` 불변
- 근거 계약: 69번 §3.4·§7.1, 71번 §5.1~§6
- 상태: **최종 승급 반려. sealed holdout HARD FAIL**

## 1. 판정 요약

`75fa5f3`는 gate96에서 71번 B-only 계약을 통과했고 ON headline 개선도 크다. 그러나
sealed holdout의 route-pair HARD를 통과하지 못했다.

- p02 root: 미검출(11 cap) → 2위, 개선
- p02 child: 3위 → 미검출(11 cap), **회귀**
- p12 root: 3위 → 3위, 불변
- p12 child: 1위 → 1위, 불변

따라서 holdout 2쌍의 실제 판정은 다음이다.

- pair non-regression: **1/2** — 요구 2/2 미달
- pair effective: **0/2** — 요구 1쌍 이상 미달

69번 §3.4에서 pair effective는 먼저 pair non-regression이어야 한다. p02는 root가
개선됐어도 child가 악화됐으므로 effective가 아니다. p12는 안전하지만 개선이 없다.

71번 §5.3은 holdout ON에 69번 sealed holdout HARD와 방향성 기준을 그대로 적용했다.
전체 R@10·MRR 개선이나 wins 8/losses 3은 이 명시적인 child 회귀 veto를 상쇄하지 못한다.
따라서 “all PASS → promote”에 해당하지 않으며 승급을 승인하지 않는다.

## 2. 누락된 holdout pair 재산출

### 2.1 p02

| role | query | accepted | baseline ON RRF | B-only ON RRF | delta |
|---|---|---|---:|---:|---:|
| root | 저장소 기본 정보를 가져와줘 | `GET /repos/{owner}/{repo}` | 11 | 2 | -9 |
| child | 저장소에 달린 토픽만 따로 조회해줘 | `GET /repos/{owner}/{repo}/topics` | 3 | 11 | +8 |

p02는 variants를 통한 root 회복과 동시에 명시 child를 top-10 밖으로 밀었다. 69번 pair
guard가 차단하려던 “root 개선 대가로 child 회귀”가 sealed holdout에서 그대로 재현됐다.

### 2.2 p12

| role | accepted | baseline ON RRF | B-only ON RRF | delta |
|---|---|---:|---:|---:|
| root | `GET /repos/{owner}/{repo}/actions/runs/{run_id}` | 3 | 3 | 0 |
| child | `GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs` | 1 | 1 | 0 |

p12는 non-regression이지만 개선 항목이 없어 effective가 아니다.

### 2.3 공식 산식 적용

```text
pair_nonregression(p02) = [-9 <= 0 and +8 <= 0] = false
pair_effective(p02) = false

pair_nonregression(p12) = [0 <= 0 and 0 <= 0] = true
pair_effective(p12) = false
```

회부문의 “holdout ON R@10 79→83%, 회귀 없음” 중 “회귀 없음”은 per-query 관점에서
사실이 아니다. 회부문 스스로 g004를 losses 3건 중 하나로 열거하고 있으며, g004가 바로
p02 child다. aggregate hit 순증과 개별 HARD 회귀는 동시에 존재할 수 있다.

## 3. gate96 효과성 집계 정규화

lead/reviewer 사이에 분모가 달랐던 두 항목은 프리즈 JSON의 `split=gate`,
`evaluation_role=scored`와 ON RRF rank로 재산출했다.

| cohort | n | baseline hit@10 | B-only hit@10 | gains | losses | 판정 |
|---|---:|---:|---:|---:|---:|---|
| C2+C3+C5 | 52 | 36 | 41 | 5 | 0 | PASS |
| 한국어 | 47 | 31 | 41 | 11 | 1(g072) | 순증 +10, PASS |
| 전체 gate | 96 | 66 | 76 | 11 | 1(g072) | headline PASS |

C2 R@3은 3/19→15/19, C2 hit@10은 14/19→18/19다. 따라서 gate96의 §5.2
EFFECTIVENESS PASS 자체는 유지된다.

다만 “한국어 cohort 순감 0”은 정확하지 않다. g072가 8위→미검출로 악화됐다. 71번의
한국어 조건은 순증 2건 이상이므로 이 항목은 여전히 PASS지만, 최종 결과 기록에는
canonical 집계를 사용해야 한다.

## 4. HARD와 aggregate 방향성의 우선순위

sealed holdout의 aggregate 지표는 긍정적이다.

- R@10 79.2→83.3%
- MRR .433→.580
- nDCG@10 .520→.645
- wins 8 / losses 3

그러나 pair guard는 일반 aggregate보다 좁고 강한 안전 조건이다. B-only가 route reranker가
아니어도 variant keyword rank 병합이 family 내부 후보를 바꾸므로 child 회귀가 가능하다.
그래서 71번 §5.1에서도 ON pair non-regression을 남겼고, §5.3에서 holdout에 69번 HARD를
재적용했다.

결과를 본 뒤 pair veto를 aggregate 순증으로 대체하면 sealed holdout을 프리즈한 의미가
없어진다. reviewer의 “승급 이의없음” 의견은 holdout pair 산식을 적용하지 않은 상태의
결론이므로 최종 승인 근거로 채택하지 않는다.

`all120` 지표도 gate96과 holdout24를 합쳐 p02 회귀를 희석하므로 별도 승급 우회 근거가
아니다.

## 5. 최종 상태

### 5.1 `75fa5f3`

**승급 반려.** gate96 PASS / sealed holdout FAIL로 기록한다. 제품 코드에 merge/promote하지
않는다.

### 5.2 manifest `6d5570b`

query SHA·labels 불변은 유효하다. 그러나 `result_records`의
`"gate/holdout/all all-PASS"` outcome은 사실과 다르므로 developer가 다음 의미로
정정해야 한다.

```text
gate PASS; sealed holdout FAIL — p02 child 3→miss,
holdout pair non-regression 1/2, effective 0/2; not promoted
```

query file, accepted, variants, pair, split은 수정하지 않는다.

### 5.3 결합 candidate `608731b`

71번 판정을 유지한다. 후속 실험 근거로만 보존하며 v1 승급 대상이 아니다. B-only 실패를
이유로 A+B를 다시 v1 holdout에 대입하지 않는다.

### 5.4 v1 holdout 재사용

24건은 `75fa5f3`에 대해 이미 개봉됐다. 이후 B 수정이나 route-family A 튜닝의 sealed
holdout으로 재사용하지 않는다.

- p02/g004와 나머지 holdout losses는 이제 개발·회귀진단 사례로 사용할 수 있다.
- 새 candidate를 최종 승급하려면 새 sealed split과 query SHA를 가진 v2가 필요하다.
- v1 수치를 보고 label/variant를 완화하거나 p02를 pair에서 빼지 않는다.

## 6. 후속 작업

1. developer가 manifest 결과기록을 정정한다.
2. B-only variant rank 병합에서 root gain이 child target을 밀어내는 원인을 별도 진단한다.
3. p02를 재현하는 단위/통합 회귀 테스트를 추가할 수정 설계를 다시 회부한다.
4. 수정 candidate가 생기면 69번 분포 원칙으로 v2 sealed holdout을 새로 프리즈한다.
5. 그 전까지 `75fa5f3`, `608731b` 모두 승급하지 않는다.

현재 결과는 “variants 대칭화가 평균적으로 강하게 개선한다”는 가설은 지지하지만,
“명시 child를 회귀시키지 않고 안전하게 승급 가능하다”는 게이트는 통과하지 못했다.
