# 하네스 실행 워크플로우

> 해당 워크플로우는 선택이 아니라 **필수**다.

## 주의사항

- Generator와 Evaluator는 반드시 다른 서브에이전트로 호출(분리가 핵심).
- 각 단계 완료 후 결과 파일 존재 여부 확인

## 서브에이전트 호출 방법

각 단계에서 Task 도구를 사용하여 서브에이전트를 호출한다.
서브에이전트에게 전달할 프롬프트는 아래 "단계별 실행 지시"를 따른다.

중요: 각 서브에이전트는 독립된 컨텍스트에서 실행된다.
이것이 "만드는 AI와 평가하는 AI를 분리"하는 핵심.

## 단계별 실행 지시

### 0 단계: EXEC_PLAN 및 worktree 생성 (필수 선행)

코드에 손대기 전에 반드시 수행한다. 계획 없이 구현을 시작하지 않는다.

1. 저장소 루트에서 아래를 실행한다.

   ```
   bash scripts/start_task.sh <task-name> <type>
   ```

   이 스크립트가 한 번에 생성한다:
   - 새 worktree (`.worktrees/<type>-<task-name>`, 브랜치 `<type>/<task-name>`, base는 `develop`)
   - `docs/exec-plans/active/<type>-<task-name>/EXEC_PLAN.md` 템플릿 (목표 / 접근법 / 단계별 계획 / 완료 기준)
   - 포트 파일 `.ports`
   - 로그 디렉터리 `output/logs/<type>-<task-name>`

2. 생성된 worktree 디렉터리로 이동한다. 이후 모든 작업은 이 worktree 안에서 수행한다.
   `master`/ `main`/`develop` 브랜치에서 `src/` 코드를 직접 수정하지 않는다.

3. worktree 안에서 문서를 아래 순서로 먼저 읽는다.
   `AGENTS.md` → `ARCHITECTURE.md` → 관련 문서(<!-- `docs/product-specs/plan.md`,--> `agents/*.md`).

4. `docs/exec-plans/active/<type>-<task-name>/EXEC_PLAN.md` 의 네 항목(목표 / 접근법 / 단계별 계획 / 완료 기준)을 모두 채운 뒤 다음 단계로 넘어간다.
   이 파일이 비어 있으면 1 단계 Planner 호출을 금지한다.

### 1 단계: Planner 호출

서브에이전트에게 아래 내용을 전달합니다:

```
`agents/planner.md` 파일을 읽고, 그 지시를 따라라.
`agents/evaluation_criteria.md` 파일도 읽고 참고하라.

사용자 요청: [사용자가 준 프롬프트]
작업 계획: `docs/exec-plans/active/<type>-<task-name>/EXEC_PLAN.md`

결과를 `docs/exec-plans/active/<type>-<task-name>/SPEC.md` 파일로 저장하라.
```

Planner 서브에이전트가 `SPEC.md`를 생성하면, 다음 단계로 진행합니다.

### 2 단계: Generator 호출

서브에이전트에게 아래 내용을 전달합니다:

최초 실행 시:
```
`agents/generator.md` 파일을 읽고, 그 지시를 따라라.
`agents/evaluation_criteria.md` 파일도 읽고 참고하라.
`docs/exec-plans/active/<type>-<task-name>/SPEC.md` 파일을 읽고, 전체 기능을 한 번에 구현하라.

구현 코드는 `src/` 디렉토리에, 테스트 코드는 `tests/` 디렉토리에 저장하라.
완료 후 `docs/exec-plans/active/<type>-<task-name>/SELF_CHECK.md`를 작성하라.
```

피드백 반영 시 (2회차 이상):
```
`agents/generator.md` 파일을 읽고, 그 지시를 따라라.
`agents/evaluation_criteria.md` 파일도 읽고 참고하라.
`docs/exec-plans/active/<type>-<task-name>/SPEC.md` 파일을 읽어라.
`src/` 디렉토리의 코드를 읽어라. 이것이 현재 코드다.
`docs/exec-plans/active/<type>-<task-name>/QA_REPORT.md` 파일을 읽어라. 이것이 QA 피드백이다.

QA 피드백의 "구체적 개선 지시"를 모두 반영하여 코드를 수정하라.
"방향 판단"이 "완전히 다른 접근 시도"이면 아키텍처 자체를 재설계하라.
완료 후 `docs/exec-plans/active/<type>-<task-name>/SELF_CHECK.md`를 업데이트하라.
```

### 3 단계: Evaluator 호출

서브에이전트에게 아래 내용을 전달합니다:

```
`agents/evaluator.md` 파일을 읽고, 그 지시를 따라라.
`agents/evaluation_criteria.md` 파일을 읽어라. 이것이 채점 기준이다.
`docs/exec-plans/active/<type>-<task-name>/SPEC.md` 파일을 읽어라. 이것이 설계서다.
`src/` 디렉토리의 코드를 읽어라. 이것이 검수 대상이다.

검수 절차:
1. `src/` 코드를 분석하라
2. `docs/exec-plans/active/<type>-<task-name>/SPEC.md`의 기능이 구현되었는지 확인하라
3. `python -m pytest tests/ -v`를 실행하고 결과를 기록하라
4. `evaluation_criteria.md`에 따라 4개 항목을 채점하라
5. 최종 판정(합격/조건부/불합격)을 내려라
6. 불합격 또는 조건부 시, 구체적 개선 지시를 작성하라

결과를 `docs/exec-plans/active/<type>-<task-name>/QA_REPORT.md` 파일로 저장하라.
```

### 단계 4: 판정 확인

`docs/exec-plans/active/<type>-<task-name>/QA_REPORT.md`를 읽고 판정을 확인합니다.

- "합격" → 단계 5(병합)로 진행.
- "조건부 합격" 또는 "불합격" → 단계 2로 돌아가 피드백 반영.
- 최대 반복 횟수: 3회. 3회 후에도 불합격이면 현재 상태로 전달하고 이슈를 보고.

### 5 단계: develop 병합

합격 판정 후에만 진행한다.

1. worktree 안에서 `python -m pytest tests/ -v` 가 통과하는지 최종 확인한다.
2. 작업 산출물 디렉토리를 `docs/exec-plans/completed/` 로 이동한다.
   `active/` 에는 남기지 않는다.

   ```
   mkdir -p docs/exec-plans/completed/
   git mv docs/exec-plans/active/<type>-<task-name> docs/exec-plans/completed/
   ```

3. 변경 사항을 커밋한다 (Conventional Commits: `<type>(scope): 설명`).
4. 저장소 루트로 이동해 `develop` 로 체크아웃하고 worktree 브랜치를 병합한다.

   ```
   git checkout develop
   git merge --no-ff <type>/<task-name>
   ```

5. 병합 후 worktree 를 정리한다.

   ```
   git worktree remove .worktrees/<type>-<task-name>
   git branch -d <type>/<task-name>
   ```

6. 사용자에게 완료 보고. 병합된 브랜치, `src/` 변경 범위, 아카이브 경로(`docs/exec-plans/completed/<type>-<task-name>/`)를 안내한다.
