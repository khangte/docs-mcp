# 30. 검색 품질 평가 — 실 코퍼스(Stripe/GitHub) 측정 결과

- 상태: 측정 완료(developer). 설계는 `28-search-quality-eval-real-corpus-design.md`, 색인 차단 버그 수정은 `29-schema-chunk-ref-id-truncation-fix.md`.
- 실행: `uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --strategy both`
- 코퍼스: `tests/fixtures/corpus_eval/`에 프리즈(§4 매니페스트, 핀 SHA) — Stripe 589 엔드포인트, GitHub 1220 엔드포인트.
- `is_semantic: True`(로컬 e5 모델 정상 동작 확인).

## 1. 지표 요약 (n=20, top_k=10)

| 전략 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|
| fallback | 10% | 20% | 30% | 40% | 0.183 | 0.235 |
| rrf | 10% | 30% | 35% | 45% | 0.200 | 0.259 |

synthetic 20-엔드포인트 하네스(`rrf_eval`) 대비 큰 폭 하락 — doc/28 §1.2가 예견한 대로, 589~1220개 규모 실 코퍼스에서 지표가 포화되지 않고 실제 변별력을 보인다.

## 2. 카테고리별 분해 (Recall@3 / MRR)

| 카테고리 | n | fallback R@3 | fallback MRR | rrf R@3 | rrf MRR |
|---|---|---|---|---|---|
| C1-직접키워드 | 3 | 33% | 0.389 | 67% | 0.667 |
| C2-한글패러프레이즈 | 4 | 0% | 0.042 | 0% | 0.042 |
| C3-영문의역 | 3 | 0% | 0.083 | 33% | 0.111 |
| C4-흔한토큰범람 | 2 | 0% | 0.000 | 0% | 0.000 |
| C5-decoy구분 | 3 | 67% | 0.528 | 67% | 0.264 |
| C6-다개념(복수정답) | 2 | 50% | 0.250 | 50% | 0.250 |
| C7-대형엔드포인트세부 | 3 | 0% | 0.000 | 0% | 0.067 |

**두드러진 실패 축**: C2(한글 패러프레이즈)와 C7(대형 엔드포인트 세부 truncation)이 두 전략 모두에서 사실상 0에 가깝다.

- **C2**: `고객 새로 등록하고 싶어`(MRR 낮음), `결제 환불 처리해줘`/`이슈 새로 만들기`/`저장소 삭제해줘`는 top-10에서 아예 미검출. 교차언어(한글 질의 → 영문 문서) recall이 synthetic 하네스보다 훨씬 약함 — 코퍼스 규모가 커지며 다른 언어권 decoy가 늘어난 영향으로 보임.
- **C7**: `POST /v1/charges`, `POST /v1/payment_intents`, `결제 생성 시 통화 단위 지정` 모두 미검출(rrf가 draft PR 질의 하나만 순위 5 회수). doc/26 truncation 논지가 엔드포인트 청크에도 실측으로 확인됨 — 대형 파라미터 문서를 가진 엔드포인트의 세부 질의는 임베딩 512토큰 상한에 걸려 발견되지 않는다.
- rrf가 fallback보다 나은 축은 C1(직접 키워드)·C3(영문 의역) — RRF 융합이 벡터 arm의 의미 유사도를 살리는 사례로 해석 가능.

## 3. 회귀 (rrf < fallback, MRR 기준)

- `delete a subscription`: fallback=1위 → rrf=3위 (MRR -0.667)
- `list commits of a repo`: fallback=4위 → rrf=8위 (MRR -0.125)

두 건 모두 C5(decoy구분) — 키워드 단독으로 이미 상위였던 사례에서 벡터 arm 융합이 오히려 순위를 밀어낸 경우. 표본 20건 규모에서 결론을 내리기엔 이르나, RRF가 "이미 잘 맞는 키워드 매치"를 약화시킬 수 있다는 신호.

## 4. 원 색인 차단 버그 (참고)

최초 실행 시 Stripe 스펙 등록이 `StringDataRightTruncation`으로 크래시(schema 컴포넌트명 최대 135자가 `chunk.ref_id`(64자)에 그대로 들어감). `29-schema-chunk-ref-id-truncation-fix.md` 판정에 따라 근본 수정(schema 청크 ref_id를 바운드 id로 교체) 후 재실행 — 위 결과는 수정 반영 후 값이다. endpoint 청크만 채점 대상이라 이 수정은 doc/28 결과 자체에는 영향 없음(§4, 판정문 그대로 확인).

## 5. 시사점 (lead/architect 판단 필요 항목)

1. **C2 교차언어 recall 붕괴**는 synthetic 하네스에서 보이지 않던 문제 — 실 코퍼스 규모에서만 드러남. multilingual-e5-small의 한/영 교차 임베딩 품질 재검토가 필요할 수 있음.
2. **C7 truncation 미검출**은 doc/26(긴 섹션 truncation) 논지가 엔드포인트 청크에도 적용됨을 실측으로 확인 — 엔드포인트는 sub-chunking 대상이 아니므로(doc/28 §6 카테고리 의도) 별도 대응이 필요하면 새 설계 검토 대상.
3. RRF 회귀 2건은 표본이 작아 결론 보류 — 향후 질의셋 확장 시 재확인 권장.

## 6. 재현

```bash
docker compose up -d postgres
uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --strategy both
```
