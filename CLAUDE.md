# Claude Code 지시서

## 하네스 엔지니어링 오케스트레이터 지시서

3-Agent 하네스 구조(Planner → Generator → Evaluator)
백엔드/데이터 처리 프로젝트를 설계·구현·검수한다

에이전트 목록은 `harness/AGENTS.md` 를 참고하라
`harness/docs/harness_workflow.md`의 실행 흐름을 **필수**로 진행한다

## 코드 작업 전 필수 절차

- **기능 개발, 버그 수정, 리팩터링 등 src/ 코드를 변경하는 모든 작업에 적용된다.**
- 코드 작업 전 태스크 규모를 먼저 판단한다: **소규모**(버그 수정·유틸 추가·단일 모듈 수정) vs **중대형**(신규 모듈·파이프라인·다중 모듈 연동).
- **중대형만**: Planner를 호출하여 `harness/docs/exec-plans/<type>-<task-name>/SPEC.md`를 생성한다. 호출 시 사용자 요청과 핵심 의도(방향·완료 기준)를 함께 전달한다.
- 테스트 통과 및 Evaluator 합격 후 커밋한다. 상세 절차는 `harness/docs/harness_workflow.md` 참고.

## 절약 규칙
- **이미 읽은 파일은 다시 확인하지 않는다.**
- 불필요한 도구 호출은 하지 않는다.
- 간으한 도구 호출은 동시에 실행한다.
- 20줄 이상의 불필요한 출력은 서브에이전트에 위임한다.
- **사용자가 이미 설명한 내용을 다시 반복하지 않는다.**

## 코드 규칙
- 함수명: snake_case / 클래스명: PascalCase
- 한 함수는 하나의 역할만 수행
- 타입 힌트 필수
- 클래스나 함수에 간단 설명 docstring을 한글로 작성
- 로그&로깅에는 `logging` 사용, `print()` 사용 금지
- 불필요한 `---` 사용 자제

## 커밋 규칙
- Conventional Commits 형식 사용: `feat(scope): 설명`
- 타입: `feat`, `fix`, `refactor`, `docs`, `test`, `chore` 등
- scope는 변경 대상 모듈명 사용: `feat(planner): 기능 추가`
- 한 커밋에 하나의 변경만 (atomic commit)
  + 변경 내용이 다르면 파일별로 작성
- 커밋 메시지는 한국어로 일관되게 작성

## 정리 규칙
- 임시 파일은 작업 완료 즉시 삭제한다
- `temp_`, `_new`, `_old`, `_backup` 이름의 파일을 만들지 않는다
- 사용하지 않는 import는 즉시 제거한다
- 작업 중 생성한 디버그용 코드는 PR 전에 삭제한다

## 완료 보고 템플릿

하네스 실행이 완료되면 아래 형식으로 보고한다.

### 하네스 실행 완료

**결과물**: output/index.html
**Planner 설계 기능 수**: X개
**QA 반복 횟수**: X회
**최종 점수**: 디자인 X/10, 독창성 X/10, 기술 X/10, 기능 X/10 (가중 X.X/10)

**실행 흐름**:
1. Planner: [설계한 기능 한 줄 요약]
2. Generator R1: [첫 구현 결과 한 줄]
3. Evaluator R1: [판정 결과 + 핵심 피드백 한 줄]
4. Generator R2: [수정 내용 한 줄] ← 해당 시만 포함
5. Evaluator R2: [판정 결과] ← 해당 시만 포함

## 파일 경로 표기    
- 항상 백틱으로 감싸라: `harness/agents/planner.md`                        
- LLM 지시문에서는 "파일을 읽어라" 동사와 함께 쓴다  
