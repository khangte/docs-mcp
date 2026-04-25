# 하네스 엔지니어링 오케스트레이터 지시서

3-Agent 하네스 구조(Planner → Generator → Evaluator).
백엔드/데이터 처리 프로젝트를 설계·구현·검수한다.

에이전트 목록은 `@AGENTS.md` 를 참고하라.
`@.claude/harness_workflow.md`의 실행 흐름을 **필수**로 진행한다.

## 절대 규칙: 코드 작업 전 반드시 수행할 것

**기능 개발, 버그 수정, 리팩터링 등 src/ 코드를 변경하는 모든 작업에 적용된다.**

### worktree 에서 구현
- 모든 구현은 전용 worktree 에서 수행한다. `master`/`develop` 브랜치의 `src/` 를 직접 수정하지 않는다.
- worktree 생성은 `bash scripts/start_task.sh <task-name> <type>` 로만 한다. 이 스크립트가 worktree + EXEC_PLAN + 포트 + 로그 디렉터리를 한 번에 만든다.
- 생성된 `EXEC_PLAN.md` 의 목표 / 접근법 / 단계별 계획 / 완료 기준을 채우기 전에는 구현을 시작하지 않는다.
- worktree 안에서는 `AGENTS.md` → `ARCHITECTURE.md` → 관련 문서 순서로 읽는다.
- 테스트 통과 및 Evaluator 합격 후 `develop` 브랜치로 병합한다. 상세 절차는 `@.claude/harness_workflow.md` 참고.

## 코드 규칙
- 함수명: snake_case / 클래스명: PascalCase
- 한 함수는 하나의 역할만 수행한다
- 타입 힌트 필수
- 클래스나 함수에 간단 설명 docstring을 한글로 작성

## 커밋 규칙
- Conventional Commits 형식 사용: `feat(scope): 설명`
- 타입: `feat`, `fix`, `refactor`, `docs`, `test`, `chore` 등
- scope는 변경 대상 모듈명 사용: `feat(planner): 기능 추가`
- 한 커밋에 하나의 변경만 (atomic commit)
  + 변경 내용이 다르면 파일별로 작성
- 커밋 메시지는 한국어로 일관되게 작성

## PR 규칙
- PR 하나에 하나의 변경만
- 테스트 없는 PR은 올리지 않는다
- PR 제목도 Conventional Commits 형식으로 작성
- 리뷰어 지정 필수
- main 브랜치 직접 push 금지

## 자동 검사
- 커밋 전 `pre-commit` 자동 실행
- `ruff`, `mypy` 검사 통과 필수
- 검사 실패 시 커밋 불가

## 정리 규칙
- 임시 파일은 작업 완료 즉시 삭제한다
- `temp_`, `_new`, `_old`, `_backup` 이름의 파일을 만들지 않는다
- 사용하지 않는 import는 즉시 제거한다
- 작업 중 생성한 디버그용 코드는 PR 전에 삭제한다
