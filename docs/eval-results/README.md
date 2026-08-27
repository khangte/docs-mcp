# 평가 결과 (eval-results)

이 폴더는 **측정 결과만** 기록한다. 설계·방법론은
`docs/architect-review/27_search_quality_eval_real_corpus_design.md`,
배치 자동화는 `30_eval_batch_automation.md`를 참조한다.

- 한 파일 = 한 번의 측정 실행. 파일명 `YYYY-MM-DD_corpus_eval.md`.
- 실행마다 새 파일을 추가한다. 기존 결과 파일은 수정하지 않는다(회귀 이력 보존).
- 수치는 실제 실행 산출물만 붙여넣는다. 추정치·손계산 금지.

## 지표 셋 (7개)

LLM 계층 지표(Answer Correctness, Faithfulness, Citation Accuracy,
Hallucination Rate, "모른다" 판정 정확도)는 **이 프로젝트 범위 밖**이다.
docs-mcp는 MCP 서버이고 답변 생성·인용·"모른다" 판정은 클라이언트 LLM의
몫이다. 필요하면 "클라이언트 통합 평가"라는 별도 트랙으로 다룬다.

MCP 계층의 Tool Selection Accuracy / Parameter Accuracy / 평균 Tool
Calls per Query도 같은 이유로 제외한다 — 어느 툴을 어떤 인자로 부를지는
클라이언트 LLM 판단이다.

### 게이트 vs 목표

지표는 두 종류다.

- **게이트**: PASS/FAIL을 낸다. 회귀 방지선. 현재 값이 이미 통과 상태라
  "떨어지면 막는다"는 의미다. — Latency, MCP 계층.
- **목표(aspirational)**: 도달하고 싶은 값. **PASS/FAIL을 내지 않는다.**
  현재 측정이 목표에 한참 못 미치며(2026-08-27 기준 Recall@1 25% vs 목표
  70%), 이는 코드 튜닝이 아니라 **정답 라벨 검증 + 질의셋 확장**
  (`docs/exec_plans/eval_set_expansion_plan.md`)이 선행돼야 좁혀진다.
  그 작업 전까지 검색 품질 수치는 정보용 회귀 관찰값으로만 쓴다. —
  검색 품질(Recall/MRR/nDCG/No-result Rate).

### 검색 품질 (27번 하네스 산출) — 목표(aspirational), PASS/FAIL 아님

n=20 질의 평균. 표본이 작아(1건 = 5%p) 강제 게이트로 못 쓴다.
질의셋 확장·라벨 검증 후 게이트 승격을 재검토한다.

| 지표 | 정의 | 목표 | 2026-08-27 (rrf) |
|---|---|---|---|
| Recall@1 | 정답 `(method, path)`가 top-1에 들면 hit | ≥ 0.70 | 0.25 |
| Recall@3 | top-3 | ≥ 0.85 | 0.35 |
| Recall@5 | top-5 | ≥ 0.90 | 0.40 |
| Recall@10 | top-10 | ≥ 0.95 | 0.45 |
| MRR | 최상위 정답의 1-based 순위 역수 | ≥ 0.75 | 0.318 |
| nDCG@10 | binary relevance 근사(graded 라벨 없음, 27번 §2) | ≥ 0.80 | 0.350 |
| No-result Rate | 20질의 중 top-k가 공집합인 비율 | ≤ 2% | 55% |

현재 갭의 원인: C2(한글 패러프레이즈)·C3(영문 의역)·C4(bare word)
카테고리가 rrf/fallback 양쪽 0%. multilingual-e5 임베딩인데도 한글→영문
recall이 0인 것은 모델보다 **질의셋/정답 라벨** 문제일 가능성이 크다
(27번 §3.3 게이트는 `(method,path)` 실재만 검증, 의미 타당성 미검증).

기준선: `docs/architect-review/29_search_quality_eval_real_corpus_results.md`.

### MCP 계층 (MCP 평가 하네스 산출) — 게이트, PASS/FAIL

| 지표 | 정의 | 게이트 | 2026-08-27 |
|---|---|---|---|
| Tool Success Rate | 툴 호출이 예외·타임아웃 없이 유효 응답을 반환한 비율 | ≥ 99% | 100% |
| MCP Error / Timeout Rate | 위의 뒷면(1 − Tool Success Rate). 에러/타임아웃 분해 | < 1% | 0% |

End-to-end Success Rate는 "툴 호출 → 유효 응답" 비율로 정의하면 Tool
Success Rate와 동일하므로 별도 지표로 두지 않는다.

하네스는 인프로세스 `FastMCP.call_tool()` 로 read-only tool 9개 × 고정
시나리오 21개를 기본 5회 반복(총 105회) 채점한다. 설계는
`docs/architect-review/64_mcp_layer_eval_harness_design.md`.

- 한 파일 = 한 번의 측정 실행. 파일명 `YYYY-MM-DD_mcp_eval.md`.
- 러너 stdout 을 그대로 새 파일로 저장한다. 러너는 이 폴더를 자동 수정하지 않는다.
- 명령:

  ```bash
  docker compose up -d postgres
  uv run python tests/fixtures/mcp_eval/run_mcp_eval.py > docs/eval-results/$(date +%F)_mcp_eval.md
  ```

### 성능 — 게이트, PASS/FAIL

측정 조건: 로컬 CPU, 실 e5 임베딩, 27번 프리즈 코퍼스(2문서, ~1800
엔드포인트) 색인. 운영 코퍼스 규모가 커지면 이 게이트를 재산정한다.

| 지표 | 정의 | 게이트 | 2026-08-27 (rrf) |
|---|---|---|---|
| P50 Latency | 검색 경로 단독 지연(질의당 5회 반복 표본) | ≤ 200ms | 16.4ms |
| P95 Latency | 〃 | ≤ 500ms | 32.6ms |

`scripts/bench_search_perf.py`는 별도 벤치(부하 프로파일)로 유지. 위
게이트 값은 `run_corpus_eval.py`의 Latency 섹션에서 읽는다.

제외:
- LLM latency — 서버에 LLM 없음
- Cost / Query — 임베딩 로컬 모델(`intfloat/multilingual-e5-small`), API 비용 없음
- 동시 사용자 성능 저하율 — 현재 배포 형태(단일 클라이언트)에서 부하 대상 아님. 배포 형태 확정 시 재검토

## 결과 파일 템플릿

`YYYY-MM-DD_corpus_eval.md`:

```
# 평가 결과 YYYY-MM-DD

- 실행자 / 커밋 SHA:
- 코퍼스 매니페스트 content_sha256:
- 임베딩: intfloat/multilingual-e5-small (dim 384)
- is_semantic: true   # fake-embedding 오실행 아님을 확인
- 명령: uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --strategy both --top-k 10

## 검색 품질

| 지표 | rrf | fallback | 기준선 대비 |
|---|---|---|---|
| Recall@1 | | | |
| Recall@3 | | | |
| Recall@5 | | | |
| Recall@10 | | | |
| MRR | | | |
| nDCG@10 | | | |
| No-result Rate | | | |

### 카테고리별 분해 (Recall@3 / MRR)

| 카테고리 | Recall@3 | MRR |
|---|---|---|
| C1-직접키워드 | | |
| C2-한글패러프레이즈 | | |
| C3-영문의역 | | |
| C4-흔한토큰범람 | | |
| C5-decoy구분 | | |
| C6-다개념 | | |
| C7-대형엔드포인트세부 | | |

### 회귀 목록 (기준선 대비 순위 하락 질의)

- (없으면 "없음")

## MCP 계층

| 지표 | 값 | 목표 | 판정 |
|---|---|---|---|
| Tool Success Rate | | ≥ 99% | |
| MCP Error / Timeout Rate | | < 1% | |

## 성능

| 지표 | P50 | P95 | 기준선 대비 |
|---|---|---|---|
| 검색 latency | | | |
| end-to-end latency | | | |

## 비고

-
```
