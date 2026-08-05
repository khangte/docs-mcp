# EXEC_PLAN

> **[2026-08-05]** 이 문서에 등장하는 FastAPI 관련 내용은 코드베이스에서 제거되었습니다. 현재는 MCP 서버 단일 진입점 구조입니다.

## 목표
여러 Swagger/OpenAPI 문서를 수집·정규화해 단일 검색 저장소로 색인하고, 사용자의 자연어 질의에 대해 **근거(엔드포인트·메서드·파라미터·요청/응답 예시)를 인용하며** 답하는 FastAPI 서버를 MVP 수준으로 완성한다.
산출물은 실행 가능한 HTTP API 서버 + pytest 통과하는 테스트 + 하네스 산출물 4종(EXEC_PLAN/SPEC/SELF_CHECK/QA_REPORT).

## 접근법
- ARCHITECTURE.md 의 "저장형 검색 구조"를 그대로 따른다: 요청 시점에 외부 OpenAPI 로 프록시하지 않는다.
- 저장소는 MVP 한정으로 **SQLite + 인메모리 임베딩 인덱스**를 채택해 외부 인프라 의존성을 제거한다. 인터페이스 계층(repository, vector_index)을 두어 추후 PostgreSQL+pgvector 로 교체 가능하게 한다.
- 임베딩/LLM 은 `EmbeddingProvider` / `LLMProvider` 인터페이스를 두고, 기본 구현으로 **해시 기반 결정적 의사-임베딩** + **템플릿 기반 응답 생성기**를 제공한다. 외부 API 키 없이도 전체 파이프라인이 결정적으로 동작한다. 실 LLM 연동은 어댑터 자리만 마련.
- RAG 파이프라인: 질의 → 검색(키워드+벡터 하이브리드) → 컨텍스트 조립(엔드포인트 상세 + 예시) → 근거 인용형 응답 생성. 응답 스키마에 `citations` 필드 필수.
- FastAPI 진입점 분리: 관리 API(`/documents`, `/sync/*`, `/health`, `/ready`)와 질의 API(`/query`, `/search`, `/endpoints/{id}`, `/endpoints/{id}/example`).
- 모든 구현은 본 worktree(`feat/openapi-rag-server`) 안에서만 수행한다.

## 단계별 계획
1. **Planner 단계**: `agents/planner.md` 에 따라 `SPEC.md` 작성. 기능 최소 8개(문서 등록/정규화/청킹/임베딩/키워드검색/벡터검색/하이브리드 검색/엔드포인트 상세/예시 생성/RAG 질의/재색인/헬스체크 등) + 각 기능의 입·출력 계약과 검증 기준 명시.
2. **Generator 단계**: `agents/generator.md` 에 따라 `src/` 에 레이어별 모듈 작성(core/models/schemas/repositories/services/api), `tests/` 에 단위·통합 테스트 작성, `SELF_CHECK.md` 기록. 외부 네트워크/DB 의존 없이 pytest 가 전부 통과하도록 설계.
3. **Evaluator 단계**: `agents/evaluator.md` 에 따라 코드 리뷰 + `pytest tests/ -v` 실행 + 4개 항목 채점 + `QA_REPORT.md` 작성.
4. **반복 단계**: 조건부/불합격 시 피드백 반영하여 Generator 재호출. 최대 3회.
5. **병합 단계**: 합격 후 산출물 4종을 `docs/exec-plans/completed/feat-openapi-rag-server/` 로 이동, Conventional Commits 로 커밋, `develop` 브랜치(없으면 생성) 로 `--no-ff` 병합, worktree 정리.

## 완료 기준
- [ ] `SPEC.md` 에 기능 8개 이상 + 각 기능의 입출력·검증기준 기재
- [ ] `src/` 아래 `core / models / schemas / repositories / services(ingestor|parser|indexer|search|examples|rag) / api` 레이어 구현 (ARCHITECTURE.md 금지 규칙 준수)
- [ ] `python -m pytest tests/ -v` 전체 통과 (외부 의존성 없음)
- [ ] `uvicorn app.main:app` 로 기동되며 `/health` 200, `/query` 가 자연어 질의에 대해 `answer` + `citations`(endpoint_id, method, path, snippet) 구조로 응답
- [ ] `QA_REPORT.md` 최종 판정 "합격"
- [ ] 산출물 4종이 `docs/exec-plans/completed/feat-openapi-rag-server/` 로 아카이브
- [ ] 금지 패턴 없음(전역 변수/빈 except/하드코딩 경로/100줄 초과 함수/타입 힌트 누락 없음)
