#!/usr/bin/env bash
# 하네스 작업 시작 스크립트.
# EXEC_PLAN, worktree, 포트 파일, 로그 디렉터리를 한 번에 준비한다.
#
# 사용법:
#   bash scripts/start_task.sh <task-name> <type>
#   <type> 예: feat | fix | refactor | docs | test | chore

set -euo pipefail

TASK_NAME="${1:-}"
TASK_TYPE="${2:-}"

if [[ -z "$TASK_NAME" || -z "$TASK_TYPE" ]]; then
    echo "Usage: bash scripts/start_task.sh <task-name> <type>" >&2
    echo "  <type>: feat | fix | refactor | docs | test | chore" >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
WORKTREE_ROOT="${REPO_ROOT}/.worktrees"
WORKTREE_DIR="${WORKTREE_ROOT}/${TASK_TYPE}-${TASK_NAME}"
BRANCH="${TASK_TYPE}/${TASK_NAME}"
LOG_DIR="${REPO_ROOT}/output/logs/${TASK_TYPE}-${TASK_NAME}"
EXEC_PLAN_DIR="docs/exec-plans/active/${TASK_TYPE}-${TASK_NAME}"
EXEC_PLAN="${WORKTREE_DIR}/${EXEC_PLAN_DIR}/EXEC_PLAN.md"
PORT_FILE="${WORKTREE_DIR}/.ports"

# base branch: develop 우선, 없으면 현재 HEAD
BASE_BRANCH="develop"
if ! git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/${BASE_BRANCH}"; then
    BASE_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
fi

mkdir -p "$WORKTREE_ROOT"

if [[ -d "$WORKTREE_DIR" ]]; then
    echo "[start_task] worktree 이미 존재: $WORKTREE_DIR" >&2
    exit 1
fi

if git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    git -C "$REPO_ROOT" worktree add "$WORKTREE_DIR" "$BRANCH"
else
    git -C "$REPO_ROOT" worktree add -b "$BRANCH" "$WORKTREE_DIR" "$BASE_BRANCH"
fi

mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$EXEC_PLAN")"

# 포트: task-name 해시 기반 오프셋 (3000~3899, 3개 연속 할당)
HASH="$(printf '%s' "$TASK_NAME" | cksum | awk '{print $1}')"
BASE_PORT=$(( 3000 + (HASH % 900) ))
cat > "$PORT_FILE" <<PORTS
API_PORT=${BASE_PORT}
DB_PORT=$((BASE_PORT + 1))
MCP_PORT=$((BASE_PORT + 2))
PORTS

cat > "$EXEC_PLAN" <<'PLAN'
# EXEC_PLAN

## 목표
<!-- 이 작업으로 해결할 문제와 산출물을 1-2 문장으로 -->

## 접근법
<!-- 어떤 전략/구조로 해결할지 -->

## 단계별 계획
1.
2.
3.

## 완료 기준
- [ ]
- [ ]
- [ ]
PLAN

cat <<DONE
[start_task] 준비 완료
  branch      : ${BRANCH}
  base        : ${BASE_BRANCH}
  worktree    : ${WORKTREE_DIR}
  exec plan   : ${EXEC_PLAN}
  logs        : ${LOG_DIR}
  ports       : ${PORT_FILE}

다음 단계:
  cd "${WORKTREE_DIR}"
  EXEC_PLAN.md 의 목표/접근법/단계별 계획/완료 기준을 먼저 채운다.
  그 뒤 harness_workflow 의 Planner 단계부터 진행한다.
DONE
