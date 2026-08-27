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

### 검색 품질 (27번 하네스 산출)

| 지표 | 정의 | 목표치 | 비고 |
|---|---|---|---|
| Recall@1 / @3 / @5 / @10 | 정답 `(method, path)`가 top-k에 하나라도 들면 hit. 20질의 평균 | 회귀 판단용(절대 임계값 미고정) | k=1/3/5/10 모두 기록 |
| MRR | 최상위 정답의 1-based 순위 역수, 20질의 평균 | 회귀 판단용 | |
| nDCG@10 | binary relevance 근사(graded 라벨 없음) | 회귀 판단용 | 27번 §2 비목표: graded 미도입 |
| No-result Rate | 20질의 중 top-k가 공집합인 비율 | 낮을수록 좋음. 상승 시 필터·색인 회귀 의심 | 27번 하네스에 추가 필요 |

기준선: `docs/architect-review/29_search_quality_eval_real_corpus_results.md`.

### MCP 계층 (서버 로그 산출)

| 지표 | 정의 | 목표치 |
|---|---|---|
| Tool Success Rate | 툴 호출이 예외·타임아웃 없이 유효 응답을 반환한 비율 | ≥ 99% |
| MCP Error / Timeout Rate | 위의 뒷면(1 − Tool Success Rate). 에러/타임아웃 분해 | < 1% |

End-to-end Success Rate는 "툴 호출 → 유효 응답" 비율로 정의하면 Tool
Success Rate와 동일하므로 별도 지표로 두지 않는다.

### 성능 (`scripts/bench_search_perf.py` 산출)

| 지표 | 정의 | 목표치 |
|---|---|---|
| P50 / P95 Latency (검색) | 검색 경로 단독 지연 | 기준선 대비 회귀 없을 것 |
| P50 / P95 Latency (end-to-end) | 툴 진입 → 응답 반환 전체 | 기준선 대비 회귀 없을 것 |

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
