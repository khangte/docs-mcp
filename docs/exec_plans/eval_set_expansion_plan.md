# 구현 계획 — 검색 평가셋 확장 + MRR/nDCG 지표 보강 (post-rrf P1)

- 상태: **착수 승인**(lead, 2026-08-10). developer 구현 대상.
- 작성: architect
- 근거 설계: `docs/architect-review/09_search_quality_post_rrf.md` P1
- 대상 파일: `tests/fixtures/rrf_eval/queries.json`, `tests/fixtures/rrf_eval/compare_strategies.py`,
  (선택) `tests/fixtures/rrf_eval/openapi.json`
- 원칙: 이 작업은 **계측 인프라 확장**이다. 검색 로직(`app/services/search/`)은 **건드리지 않는다.**
  런타임 코드 변경 0, 테스트/평가 픽스처만 수정.

## 배경 (왜 이 작업인가)

RRF 실측이 20질의로 top-1 80%·top-3 recall 95%를 냈으나, 20질의는 **회귀 방어엔 되지만
후속 튜닝·리랭킹 검증엔 통계적으로 얇다**(과적합 위험). 또 현 지표는 top-1·top-3 recall
**두 개뿐**이라 순위 2~3위 내 미세 이동을 못 잡는다. 이 계획은 (A) 질의를 50~100개로 늘리고
(B) MRR·nDCG를 추가해, 이후 P2(필드가중)·P3(리랭킹)·P4(K/N 스윕)의 개선/회귀를
정량 판정할 기반을 만든다.

---

## Task 1 — 평가셋 확장 (20 → 최소 60질의, 목표 80~100)

### 1.1 대상 파일
`tests/fixtures/rrf_eval/queries.json` (현재 20개). 스키마는 기존 그대로:
```json
{"query": "<질의>", "category": "<카테고리>", "accepted": [["METHOD", "/path"], ...]}
```

### 1.2 질의 추가 기준 (출처·방식)
- **정답 풀은 기존 `openapi.json`의 엔드포인트로 한정**한다(약 21개 오퍼레이션: auth/login·logout·
  password reset-request/confirm·session, users POST/GET, users/{userId} GET/PATCH/DELETE,
  orders POST/GET, orders/{orderId} GET/DELETE, subscriptions cancel, billing refund/invoices,
  health, products GET, products/{productId}). **새 질의는 이 엔드포인트를 정답으로 라벨링**한다.
  openapi.json 확장은 이번 스코프 밖(1.5 참고).
- **실패 taxonomy 균형 배분**(각 카테고리 최소 8~12질의). 카테고리는
  `docs/architect-review/07_search_rrf_reevaluation.md` 1절 손실 패턴에서 파생:
  1. `동의어/패러프레이즈-한글` (예: "회원 나가기", "계정 없애줘" → DELETE /users/{userId})
  2. `동의어/패러프레이즈-영문` (예: "register", "onboard a user" → POST /users)
  3. `한글질의 vs 영문문서` / `영문질의 vs 한글문서` (교차언어)
  4. `흔한 토큰 오탐 범람` (user, order, get 등 다수 엔드포인트에 퍼진 토큰)
  5. `필드 희석` (질의 토큰이 파라미터 이름·응답코드에만 매칭되는 decoy)
  6. `경로 기반 질의` (리터럴 경로, 경로 세그먼트 자연어)
  7. `decoy 구분` (세션종료 vs 세션조회, 로그인 vs 로그아웃 등 근접 혼동쌍)
  8. `다개념 질의(복수 정답)` (accepted에 2개 이상)
- **작성 방식**: 각 엔드포인트당 최소 3~4개 질의(직설/동의어/패러프레이즈/오타 혼용).
  자연스러운 사용자 발화로 쓰되, **정답이 자명하지 않게**(어휘 겹침이 약한 표현 우선 —
  그래야 벡터·리랭킹의 이득이 드러난다). 기존 20질의는 유지하고 추가만 한다.

### 1.3 라벨링 규칙 (`accepted`)
- `accepted`는 **정답으로 인정되는 (method, path) 집합**. 질의 의도에 부합하는 엔드포인트가
  여럿이면 모두 넣는다(예: "주문" → orders 3종). **단, 남발 금지** — "이것도 맞다고 볼 수 있음"
  수준은 제외하고, 사용자가 그 질의로 실제 원했을 엔드포인트만.
- path는 openapi.json의 표기와 **정확히 일치**(`{userId}` 등 중괄호 파라미터명 포함).
- 애매하면 라벨을 **좁게**(정답 1개) 유지 — recall/MRR이 낙관 편향되지 않도록.

### 1.4 완료 판단(Task 1)
- `queries.json` 총 **60질의 이상**(목표 80~100), 8개 카테고리 각 8질의 이상.
- 모든 `accepted`의 (method, path)가 `openapi.json`에 실재(1.6 검증 스크립트로 확인).

### 1.5 (선택, 스코프 밖) openapi.json 확장
현 21개 엔드포인트로 60~100질의 라벨링이 가능하므로 **이번엔 openapi.json을 늘리지 않는다.**
질의 다양성이 엔드포인트 수에 막히면(같은 정답에 질의가 과밀) 별도 태스크로 분리 제안.

### 1.6 라벨 정합성 검증(권장 산출물)
`compare_strategies.py` 실행 초입에 "queries.json의 모든 accepted가 openapi.json에 존재하는가"를
검사해 불일치 시 명확한 에러로 죽게 한다(오타 라벨이 조용히 미검출로 집계되는 것 방지). Task 2에 포함.

---

## Task 2 — MRR/nDCG 지표 추가 (`compare_strategies.py`)

### 2.1 기존 구조 (참고)
- `TOP_K = 10`으로 조회, `_rank_of_answer(candidates, accepted)`가 **정답 최초 등장 1-based 순위**
  또는 `None` 반환(현재 라인 74~79).
- 집계 루프(라인 106~132)가 `fb_top1/fb_top3/rrf_top1/rrf_top3` 카운트 → 표·요약 출력.
- 전략별로 `results[query][strategy] = rank|None` 구조.

### 2.2 추가할 지표 (전략별로 fallback·rrf 각각 계산)
기존 `_rank_of_answer`가 주는 "정답 최초 순위 r"만으로 전부 계산 가능(추가 조회 불필요):

- **MRR** (Mean Reciprocal Rank): 질의별 `1/r`(정답 미검출이면 0)의 평균.
  `mrr = mean(1/r_i for each query, else 0)`. top-1이 평평해도 2→3, 3→2 이동을 잡아낸다.
- **nDCG@k** (k=10, 이진 관련성): 정답이 순위 r에 있으면 `DCG = 1/log2(r+1)`, 미검출 0.
  이진·정답1건 기준 이상적 순위는 r=1이라 `IDCG = 1/log2(2) = 1`, 즉 **nDCG@10 = DCG@10**.
  **복수 정답(accepted ≥2) 질의**: `_rank_of_answer`는 최초 1건만 보므로, nDCG도
  **"최초 정답의 순위" 기준 단일 관련항으로 계산**한다(IDCG=1 유지). 복수 정답 전부를 gain에
  넣는 완전 nDCG는 이번 스코프 밖 — 문서에 "단일-관련 근사"임을 주석으로 명기.
- **Recall@k**: k=1,3,5,10 각각 `정답이 top-k 안에 있는 질의 비율`. 기존 top-1/top-3를
  이 일반형으로 흡수.

### 2.3 구현 방식 (최소 변경)
- 순수 함수로 분리(테스트 용이·회귀 안정):
  ```python
  def reciprocal_rank(rank: int | None) -> float: ...      # 1/rank, None→0.0
  def dcg_at(rank: int | None, k: int) -> float: ...       # rank<=k면 1/log2(rank+1), 아니면 0
  def recall_at(rank: int | None, k: int) -> int: ...      # rank is not None and rank<=k → 1
  ```
  `math.log2` 사용. 이 3개는 rank만 입력받는 결정적 함수라 단위테스트가 쉽다(Task 3).
- 집계 루프에서 전략별 리스트에 질의별 rank를 모으고, 끝에서 `mean`으로 MRR/nDCG,
  카운트로 Recall@{1,3,5,10} 산출.
- **출력 표에 열 추가**: 기존 per-query 표(질의·카테고리·정답·fallback순위·rrf순위·판정)는 유지,
  하단 `### 지표 요약`을 다음으로 확장(전략별 한 줄):
  ```
  - fallback: Recall@1 .. Recall@3 .. Recall@5 .. Recall@10 .. | MRR .. | nDCG@10 ..
  - rrf     : Recall@1 .. Recall@3 .. Recall@5 .. Recall@10 .. | MRR .. | nDCG@10 ..
  ```
- **카테고리별 분해(권장)**: 카테고리별 Recall@3·MRR 소계 표를 추가하면 어느 실패 패턴에서
  전략이 약한지 드러난다(P2/P3 착수 근거). 여력 없으면 전체 지표만으로도 완료 인정.
- 회귀 리스트(rrf가 fallback보다 나빠진 케이스, 라인 134~139)는 **MRR 기준으로도** 병행 출력
  (순위 하락은 top-k 이탈 없이도 MRR을 떨어뜨리므로 더 민감한 회귀 탐지).
- `TOP_K`는 10 유지(nDCG@10·Recall@10 조회 폭 충족).

### 2.4 라벨 정합성 검사(Task 1.6 반영)
`_load_queries()` 직후, openapi.json에서 (method, path) 집합을 만들어 모든 accepted가
그 안에 있는지 확인, 없으면 `raise ValueError(f"미존재 라벨: {...}")`.

---

## Task 3 — 완료 기준 (테스트/검증)

1. **단위 테스트 신규**(`tests/unit/test_eval_metrics.py`): 2.3의 순수 함수 3종을 AAA로 검증.
   - `reciprocal_rank`: rank=1→1.0, rank=2→0.5, None→0.0.
   - `dcg_at`: rank=1,k=10→1.0, rank=3,k=10→1/log2(4)=0.5, rank=11,k=10→0.0, None→0.0.
   - `recall_at`: (rank=3,k=3)→1, (rank=4,k=3)→0, (None,k=10)→0.
   - **주의**: compare_strategies.py는 pytest 수집 대상이 아니지만(실DB·실모델 로드),
     지표 순수 함수는 **DB/모델 의존 없는 모듈 함수**이므로 정상 수집·실행된다. 함수가
     import 가능하도록 스크립트 상단에 두거나 별도 `metrics.py`로 분리(분리 권장 — YAGNI 벗어나지 않는 선).
2. **라벨 정합성**: 잘못된 (method, path) 라벨을 일부러 넣은 임시 케이스로 2.4 검증이 죽는지 확인
   (확인 후 되돌린다 — 커밋에 남기지 않음).
3. **평가 스크립트 실측 재실행**:
   ```bash
   docker compose up -d postgres
   uv run python tests/fixtures/rrf_eval/compare_strategies.py
   ```
   - 확장 질의셋(60+)에서 fallback vs rrf의 Recall@{1,3,5,10}·MRR·nDCG@10이 출력되고,
     **회귀(rrf < fallback) 목록이 명시**될 것.
   - 이 실행 결과(지표 표)를 `docs/architect-review/09_search_quality_post_rrf.md` P1 절 하단 또는 별도
     "실측 결과" 절에 **기록**(RRF 재검토 문서 6절과 동일 포맷). 수치 해석은 architect가 검토.
4. **기존 골든 회귀 테스트 불변**: `tests/unit/test_search_rrf_golden.py`,
   `tests/unit/test_rrf.py`가 그대로 통과(이 작업은 검색 로직 무변경이므로 반드시 green).
   ```bash
   uv run pytest tests/unit/test_rrf.py tests/unit/test_search_rrf_golden.py tests/unit/test_eval_metrics.py -q
   ```

## 커밋 분할(원자적)
- 커밋 A: `test: RRF 평가셋 20→60+ 질의 확장`(queries.json)
- 커밋 B: `test: 평가 스크립트에 MRR/nDCG/Recall@k + 라벨 정합성 검사 추가`(compare_strategies.py, metrics.py, test_eval_metrics.py)
- 커밋 C(선택): `docs: 확장 평가셋 실측 결과 기록`(09_search_quality_post_rrf.md)

## 설계 이탈 시
질의 라벨 정답이 애매해 taxonomy 배분이 안 맞거나, nDCG 복수정답 처리를 단일근사 이상으로
가져가야 한다고 판단되면 **구현 전 architect(:0.1)에 문의**. 검색 로직(`app/services/search/`)
수정이 필요해 보이면 그 순간 멈추고 문의 — 이 작업 스코프가 아님.
