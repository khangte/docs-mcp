# 74. p02 coverage 수정 실패·keyword variants 트랙 중단 판정

- 대상: 73번 구현 워킹트리와 p02 shared-index 재현
- 상태: **73번 구현 반려·커밋 금지. search-time keyword variants 수정 트랙 중단**

## 0. 실행·감사 근거

- candidate source-state: repository HEAD
  `17686f7cd981b930a020d0470625730501cbfc29`, 제품 검색 기점 B-only
  `75fa5f3f98bcd119f1f7bf3645b018cc0db4996d`, 미커밋 6파일 source-state SHA-256
  `36d2e5473b2fdfbee8013561dc71e6914f20fbc5d8e859f07321c9e90ffd112d`
- source snapshot: developer session `4a84df97-a1cf-4c1c-a2ed-4ef4a293f5a1`, message UUID
  `ede4f9d9-b61a-44e1-94a5-07847c473250`, `2026-08-28T05:00:18.838Z`; 여섯 파일 SHA를
  논리 경로 오름차순으로 `<file_sha256><두 칸><logical_path><LF>` 직렬화해 산출
- shared-index: `rrfeval_ed5b97f0`, fingerprint
  `da3952f144ebf8d3b45e65c14318c54f01bcb1bf0ad1d4023422d1907fc02faa`
  (endpoint/chunk `github=1220`, `stripe=589`; query SHA
  `6eb897d24d681d1389963007a184ded043d3ae914cf862f6ffd8aba7f75838d8`)
- raw 실행 trace UUID: OFF holdout runner `88e7cb59-d495-470b-adb6-42f0bf5bd306`, ON p02 pair
  `53fb663f-f5ba-442f-9fce-631b2377df77`, arm trace `8f0b73fe-6a56-41dc-ae3a-c0c237a01625`
- 재현 산출물: developer session `4a84df97-a1cf-4c1c-a2ed-4ef4a293f5a1`; 재현 스크립트
  `repro_p02_73.py` SHA-256 `975e20ad40b43db66c38836e6a7a8c71ab5fbe6dcc98f3fd9006cc328d0010b1`;
  결과 보고 `73_impl_and_p02_repro_report.md` SHA-256
  `99b831e0e2802de3354f34ed42bf27772396b2c23597ecace8cfa4bb33506fcd`

## 1. 판정 요약

73번의 핵심 가정은 실제 프리즈 코퍼스에서 성립하지 않았다.

- `/topics` child는 informative term 3개 중 2개를 매치했지만 동일 coverage sibling
  수십 개에 밀려 variant quality rank 79, width/cap 밖이었다.
- root는 informative term 4개 중 `repository` 하나만 매치해 coverage 0.25였고 pool에
  들어오지 못했다.
- admitted 10건은 target이 아니라 coverage가 높은 다른 `/repos/*` sibling이었다.
- 결과적으로 child는 11→9로 조금 회복했지만 root가 4→11로 회귀해 p02 pair는 여전히
  non-regression을 통과하지 못했다.

단위 942건 통과와 gate/holdout aggregate MRR 개선은 구현이 명세대로 동작한다는 증거이지,
명세가 옳다는 증거는 아니다. 73번 §6.2는 mock이 아닌 실 코퍼스에서 바로 이 전제를
검증하도록 둔 게이트이고, 그 게이트가 설계를 반증했다.

따라서 현재 워킹트리는 커밋하지 않는다. coverage threshold·budget 숫자를 p02에 맞춰
추가 조정하지 않는다.

## 2. 왜 coverage도 target을 식별하지 못했는가

OpenAPI endpoint chunk는 path·summary·description을 담지만 사용자 variant의 operation
표현과 target hierarchy를 정규화한 구조 필드는 없다.

- query: `list just the topics on the repository`
- target chunk: `GET .../topics — Get all repository topics`
- lexical 차이: query의 `list`와 chunk의 `Get all`은 같은 operation이지만 token이 다르다.

coverage는 bag-of-tokens 안에서 몇 개를 맞혔는지만 보므로 다음을 구분하지 못한다.

1. target leaf `topics`를 포함한 terse 정답
2. `repository`와 우연한 operation term을 더 많이 포함한 긴 sibling 설명
3. root query의 대상인 bare repository와 `fetch/info`를 설명에 포함한 다른 하위 route

즉 raw `ts_rank`의 density 문제를 coverage count 문제로 바꿨을 뿐, “어느 token이 target
resource이고 어느 token이 ancestor context인가”라는 정보 부재는 그대로다.

## 3. developer가 제시한 네 안 판정

### 3.1 (a) 원문 arm에 신호가 있을 때만 variant pool 주입

**반려한다.**

한국어 원문이 영문 OpenAPI에서 0 hit인 상황이 variants가 필요한 주 사례다. 이 조건에서
pool을 끄면 B의 C2 cross-language 실익을 구조적으로 제거한다. 원문 hit가 있는 질의만
variants를 쓰는 기능은 현재 문제를 풀지 않는다.

### 3.2 (b) coverage 1.0 같은 절대 하한

**반려한다.**

정답 `/topics`가 실제로 0.67이고 root가 0.25다. 완전 coverage만 허용하면 terse 정답은
제외되고 우연히 query 표현을 많이 반복한 off-target 설명만 남는다. threshold를 0.67이나
0.25로 내리는 것은 p02 관측값을 상수로 옮기는 것일 뿐 다른 query 길이에 일반화되지
않는다.

### 3.3 (c) budget 2~3으로 축소

**반려한다.**

blast radius는 줄지만 admitted 상위가 여전히 off-target sibling이다. target이 rank 79인
상태에서 cap을 줄일수록 정답은 더 확실히 배제된다. pair 중 어느 쪽이 밀리는지만 바뀌고
target selection 오류는 고쳐지지 않는다.

### 3.4 (d) 원문 keyword arm에 이미 있는 ref만 rank 보정

**안전장치로는 타당하지만 제품 후보로는 반려한다.**

이 규칙은 새로운 sibling flood를 막는다. 그러나 cross-language 원문 arm이 0건인 C2에서는
보정할 ref가 없어 B가 완전 no-op한다. variants ON의 실익은 baseline에 이미 있던 vector
variants에만 남는다.

안전하지만 새 기능 가치가 없으므로 이를 별도 candidate로 구현·승급하지 않는다. baseline
동작을 유지하는 것과 제품 결과가 같다.

## 4. search-time B 트랙 종료

다음 후보는 모두 승급하지 않는다.

- `75fa5f3` B-only: v1 sealed holdout p02 FAIL
- `608731b` A+B: 71번 효과성 미달
- 73번 coverage+cap 워킹트리: p02 개발 게이트 FAIL

developer는 73번의 미커밋 production/test 변경을 candidate commit으로 만들지 않는다.
진단 보고서와 architect verdict는 실패 근거로 보존한다.

현재 production 기준은 `ecc3e792`의 검색 동작이다. 여기에 이미 존재하는 vector variants
경로는 유지한다. keyword variants를 full ranking signal로 승격하는 변경만 중단한다.

## 5. 다음 레버 — 별도 index representation 트랙

p02가 요구하는 정보는 search-time rank weight가 아니라 색인 표현에 없다. 후속을 계속하려면
ADR-0003 read-only 범위를 벗어난 **별도 설계 승인**이 필요하다.

검토할 index-time 구조 신호:

1. **path leaf token 보존**
   - `line_items` → `line`, `items`
   - `/topics` → target leaf `topics`
   - ancestor resource와 leaf resource를 별도 필드/가중치로 구분
2. **method × path shape operation alias**
   - GET collection → `list`
   - GET item → `get`, `retrieve`
   - POST collection → `create`
   - DELETE item → `delete`
3. **가중 lexical field**
   - target leaf·operation alias는 높은 weight
   - ancestor path·free-text description은 낮은 weight
4. **결정적 생성**
   - LLM metadata 생성 없이 method/path/summary에서만 산출
   - 재색인 시 동일 입력은 동일 lexical document

이 신호가 있으면 p02 child는 `list + topics`를 구조적으로 매치하고, root는
`get + repository`를 매치할 수 있다. broad sibling의 description term density가 target
leaf보다 강해지는 현재 문제가 줄어든다.

구체적인 chunk text 추가와 weighted tsvector 중 어느 방식을 택할지는 별도 설계에서
코드·migration·재색인 비용을 확인한 뒤 결정한다. 이번 verdict로 구현을 선행하지 않는다.

## 6. 테스트·프리즈 처리

### 6.1 p02

p02는 앞으로 모든 lexical expansion 후보의 개발 회귀 테스트로 유지한다.

- root와 child 모두 baseline capped rank보다 악화되지 않아야 함
- aggregate 개선으로 pair loss를 상쇄하지 않음
- arm trace에서 target leaf/operation 신호가 실제로 기여했는지 확인

### 6.2 v1

v1 gate/holdout은 전부 exposed development corpus다. 새 candidate의 최종 승급에는 쓰지
않는다. query·label·variant·pair는 수정하지 않는다.

### 6.3 v2

새 index representation candidate가 p02와 v1 exposed regression을 통과한 뒤에만 v2를
저작한다. 69번의 domain/language/category 분포와 root/child guard를 유지하되 sealed
holdout은 전량 신규 endpoint/query pair로 만든다.

## 7. 후속 지시

1. developer: 73번 production/test 워킹트리를 커밋하지 말고 작업 실패로 종료 보고
2. lead: keyword variants search-time 트랙 종료 여부 확정
3. lead가 계속 추진할 경우 architect에게 index representation 별도 설계 요청
4. 그 전까지 v2 프리즈·holdout 저작 착수 금지

## 8. 최종 판정표

| 항목 | 판정 |
|---|---|
| 73번 구현 | **반려, 커밋 금지** |
| (a) 원문0 pool 억제 | **반려 — C2 기능 소멸** |
| (b) coverage 절대 하한 | **반려 — terse target 배제** |
| (c) budget 2~3 | **반려 — off-target 선택 유지** |
| (d) 기존 ref만 보정 | **안전하지만 신규 실익 없어 제품 후보 반려** |
| search-time keyword variants | **트랙 중단** |
| 다음 가능 레버 | **별도 index representation + 재색인 설계** |
| v2 | **새 candidate 전까지 착수 금지** |

현재 증거에서 안전한 선택은 평균 개선폭을 좇아 lexical rank 규칙을 더 복잡하게 만드는
것이 아니라, 정답 target을 표현할 구조 신호가 준비될 때까지 B를 승급하지 않는 것이다.
