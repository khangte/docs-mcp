# 66. 컬렉션 루트 랭킹 편향·평가 라벨 판정

- 대상: 27번 `queries.json` 라벨 재검증 후 미검출 8건(q04~q12, q17) 진단
- 근거: 모든 `accepted`가 프리즈 스펙의 `(method, path)`와 일치하며, 미검출 시
  컬렉션 루트 대신 같은 route family의 하위 리소스가 top-10을 점유함
- 상태: architect 판정 완료. 현재 프리즈 라벨 파일은 수정하지 않는다.

## A. 컬렉션 루트가 하위 리소스에 밀리는 현상

**판정: 29번 이후 검색 품질 후속 레버로 채택한다.** 8건에 걸쳐 Stripe와 GitHub
양쪽에서 같은 형태로 재현됐으므로 단일 질의 오라클 문제가 아니라 route-family 내부
랭킹 편향 신호다. 단, 29번의 완료된 측정값·종결 판정을 소급 수정하지 않고 다음
평가셋 확장/튜닝 라운드에 이월한다.

구현 방향은 무조건적인 짧은 path boost가 아니다. 그것은 명시적으로 하위 리소스를
묻는 질의를 회귀시킨다. 다음 라운드는 아래 순서로 진행한다.

1. arm별 후보 폭을 top-10보다 넓혀 컬렉션 루트가 후보군에는 있는지 먼저 확인한다.
2. 컬렉션 질의와 하위 리소스 질의를 route-family별 쌍으로 확장해 회귀 감시셋을 만든다.
3. 루트가 넓은 후보군에는 있으나 최종 top-10에서만 밀릴 때, 질의의 operation/list/create
   의도와 path specificity를 함께 쓰는 제한적 rerank를 실험한다.

즉 레버의 대상은 `path 길이` 자체가 아니라 **질의 의도와 endpoint specificity의 정합**이다.

## B. q11 `customer` / q12 `pull request`

**판정: `accepted`에 하위 변형을 일괄 추가하는 안은 반려한다.** bare word에 여러
엔드포인트가 관련될 수 있다는 지적은 타당하지만, 이를 모두 binary 정답으로 넣으면
관련도 차이를 없애고 Recall/MRR을 낙관 편향시킨다.

현재 binary 지표에서 의미 타당성을 확보하는 최소 변경은 scored 질의를
`list customers` / `list pull requests`처럼 operation이 명확한 표현으로 바꾸고,
기존 bare-word 두 건은 C4의 **비게이트 진단 질의**로 보존하는 것이다. graded qrels는
관련도 등급과 route-family 전체 judgment 규칙까지 함께 설계할 때만 도입한다. q11/q12
두 건만을 위해 현재 하네스의 단일-관련 근사를 깨지는 않는다.

따라서 현재 프리즈 파일은 그대로 두되, 다음 질의셋 개정에서 scored/diagnostic 분리를
반영한다. 그때까지 C4 0%는 검색 품질 실패 신호로 관찰하되 headline binary 품질을
확정하는 근거로 사용하지 않는다.

## C. q10 `show my billing history`

**판정: 질의와 `GET /v1/invoices` 정답을 모두 유지한다.** `list invoices`로 바꾸면
C3 영문 의역 테스트를 직접 키워드 테스트로 바꿔 어휘 갭을 측정하지 못한다. billing
계열 endpoint를 `accepted`에 추가하는 것도 실제 의도를 충족한다는 근거 없이 실패를
가리는 라벨 완화다.

대신 다음 질의셋에 `list invoices` → `GET /v1/invoices`를 별도 lexical control로
추가한다. 두 질의의 순위 차이로 `billing history`↔`invoices` 어휘 갭과 컬렉션 루트
편향을 분리해 측정한다.

## 후속 우선순위

1. 프리즈 라벨은 변경하지 않고 이번 재검증 결과를 기준선 근거로 보존한다.
2. 질의셋 확장 때 root/child 쌍과 q10 lexical control을 먼저 추가한다.
3. C4 scored/diagnostic 분리 후에만 headline 지표를 다시 계산한다.
4. 확장된 평가셋에서 route-family 편향이 재현되면 A의 제한적 rerank를 실험한다.

