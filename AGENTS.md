# Agents

이 프로젝트는 3개의 서브에이전트로 구성된 하네스 구조로 동작한다.
각 에이전트의 상세 지시는 아래 파일을 직접 읽어라.

## 에이전트 목록

| 에이전트 | 파일 | 역할 | 결과물 |
|---------|------|------|--------|
| Planner | @agents/planner.md | 사용자 요청 분석 & 기술 설계 | SPEC.md |
| Generator | @agents/generator.md | SPEC 기반 구현 | src/, tests/ |
| Evaluator | @agents/evaluator.md | 코드 리뷰 & 테스트 실행 & 판정 | QA_REPORT.md |

## 핵심 원칙

- Generator와 Evaluator는 반드시 별개의 서브에이전트로 호출한다.
- 에이전트 간 소통은 파일로만 한다. 직접 호출하지 않는다.
- 각 에이전트는 독립된 컨텍스트에서 실행된다.
