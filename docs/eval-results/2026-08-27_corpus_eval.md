# 평가 결과 2026-08-27

> 템플릿 초기화 상태. 수치는 `run_corpus_eval.py` 실제 실행 후 채운다.

- 실행자 / 커밋 SHA: _(미실행)_
- 코퍼스 매니페스트 content_sha256: _(미실행)_
- 임베딩: intfloat/multilingual-e5-small (dim 384)
- is_semantic: _(미실행)_
- 명령: `uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --strategy both --top-k 10`

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

- _(미실행)_

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

- 폴더 신설 커밋과 함께 생성된 초기 템플릿. 첫 측정 실행 시 이 파일을 채우거나 실행일자 파일을 새로 만든다.
