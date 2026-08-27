# 63. `sharedWith` Stage C0 게이트 판정

작성일: 2026-08-27  
대상: `62_meta_filter_owner_created_folder_shared_design.md` 5절 Stage C

## 1. 최종 probe 사실

사용자가 지정한 테스트 폴더의 파일 15건을 현재 서비스 계정으로 조회한 결과는 다음과 같다.

- `permissions`: 키 자체가 미반환, 15/15
- `capabilities.canShare`: `false`, 15/15
- `capabilities.canReadRevisions`: `false`, 15/15
- `shared=true`, `ownedByMe=false`, 15/15

이는 "대상 폴더를 뷰어로 공유받는 서비스 계정은 `files.get` 응답에서 ACL을 수집할 수 없다"는
62번 5.1절의 가정과 일치한다. 먼저 보고된 `permissions` 15/15 반환 결과는 지정 폴더가 아닌 별도
코퍼스의 결과이며, 그 코퍼스에는 `anyoneWithLink=writer` 권한이 걸려 서비스 계정의
`canShare=true`가 성립했다. 따라서 그 결과는 일반 수집 가능성의 근거로 사용하지 않는다.

## 2. 판정

1. **옵션 A 반려.** 이 서버의 기본 운영 전제인 서비스 계정=뷰어에서 동작하지 않는다.
2. **옵션 B의 Stage C 단독 구현도 반려.** `permissions.list`를 문서마다 호출하는 구현을 지금
   착수하지 않는다.
3. **`sharedWith` 메타 필터는 이번 범위에서 종료한다.** `DocumentMetaFilter.shared_with`,
   `document_permission` 테이블, `collect_permissions` 옵션과 관련 마이그레이션을 만들지 않는다.
4. **항목12(permission/access control)는 폐기하지 않는다.** 다만 Stage C의 연장이 아니라,
   호출자 신원과 실효 권한을 함께 다루는 별도 보안 설계로 재착수한다.

## 3. 옵션 B를 채택하지 않는 이유

- 동기화 비용이 폴더 수 중심에서 문서 수 N회 API 호출로 바뀌어 quota/rate-limit 및 완료시간
  특성이 질적으로 달라진다.
- 전체 ACL은 이메일을 포함한 접근 관계 PII다. 메타 필터 하나를 위해 DB에 이를 복제하고
  장기 보관하는 것은 소유자 이메일 1건 저장과 규모·위험이 다르다.
- `sharedWith=email`은 "이 사용자가 지금 접근 가능한가"와 같지 않다. 그룹 멤버십, 도메인 권한,
  링크 권한, 상속 권한, 소유권 및 권한 회수의 최신성을 함께 해석해야 한다.
- 서버에는 현재 MCP 호출자의 검증된 신원 개념이 없다. 호출자가 임의 이메일을 필터로 넘기는
  구조는 접근제어가 아니며 보안 경계를 만들지 못한다.
- `permissions.list`가 뷰어 서비스 계정에서 호출 가능하더라도 위 의미·보안 문제는 해소되지
  않는다. 반대로 수집 누락을 빈 ACL로 취급하면 조용한 빈 결과 또는 잘못된 허용/거부가 된다.

## 4. 항목12 재착수 게이트

다음 결정을 먼저 승인받은 뒤 데이터 원천과 스키마를 정한다.

1. MCP 호출자의 인증된 principal을 무엇으로 정의할지
2. 사용자·그룹·도메인·링크·상속을 포함한 실효 권한을 어느 시스템에서 판정할지
3. 권한 회수 반영 지연, 동기화 실패, 미수집 상태에서의 fail-closed 정책
4. ACL PII의 최소 저장 범위, 보존 기간, 암호화/접근 제한 및 로그 마스킹 정책
5. 전량/증분 수집 비용과 Drive API quota를 만족하는 갱신 전략

이 게이트 전에는 옵션 B probe나 구현을 Stage C 작업으로 확대하지 않는다.

## 5. developer 지시

- Stage A/B의 승인 범위는 그대로 진행한다.
- Stage C 관련 스키마·API·필터·마이그레이션은 구현하지 않는다.
- C0 원본 보고서는 재현 근거로 스크래치패드에 보존한다.
