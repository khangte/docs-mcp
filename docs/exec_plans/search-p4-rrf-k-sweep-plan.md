# 구현 계획 — P4: RRF `K` 스윕 실험 (post-rrf 문서 기준 P4)

- 상태: **착수 승인**(lead, 2026-08-10). developer 구현 대상.
- 작성: architect
- 근거: `docs/search-quality-post-rrf.md` P4(RRF K/N 스윕). **P2(필드가중)는 보류**(한계이득 작음+재색인 비용), **P3(리랭킹)은 조건부 보류**.
- 대상: `tests/fixtures/rrf_eval/`(실험 스크립트). **프로덕션 검색 코드(`app/`)는 건드리지 않는다.**
- 성격: 이것은 **실험(측정) 태스크**다. K를 실제로 바꾸는 건 이 태스크가 아니라, 스윕 결과 해석 후 별도 후속(아래 6절).

## 0. 핵심 전제 — 이 스윕에서 관측 가능한 것은 K뿐 (N 아님)

평가 픽스처(`openapi.json`)는 **오퍼레이션 20개**뿐이다. RRF 후보폭 `N = max(top_k*4, 50)`은
`top_k=10`(평가 `TOP_K`)에서도 `50`이라 **이미 전체 코퍼스(20개)를 포함**한다. 즉 이 픽스처에서
**N을 키우든 줄이든(≥20이면) 두 arm의 후보 집합이 동일** → N 스윕은 지표에 **아무 변화도 못 준다**(관측 불가·포화).

따라서:
- **이 태스크의 스윕 대상은 `K` 하나로 한정**한다. N은 손대지 않는다.
- N 튜닝은 "엔드포인트가 후보폭보다 많은" 대규모 코퍼스에서만 의미가 있는데, 현 픽스처는 이를
  표현할 수 없다. **N은 스코프 밖**이며, 대규모 평가 코퍼스가 생기면 그때 별도로 본다(계획서에 명기).

## 1. 스윕 방식 — 프로덕션 무변경 in-memory 스윕

`reciprocal_rank_fuse(keyword_ref_ids, vector_ref_ids, *, top_k, k=RRF_K)`(`app/services/search/rrf.py:42`)는
이미 `k`를 인자로 받는다. 그리고 두 arm(키워드·벡터)의 순위 리스트는 K와 무관하게 결정된다
(K는 융합 단계에서만 쓰임). 따라서:

> **질의당 두 arm을 딱 한 번만 실행**해 순위 리스트를 얻고, 그 위에서 **K 그리드를 in-memory로
> 융합만 반복**한다. DB·임베딩 재실행 없음, 프로덕션 코드 변경 없음, 결과는 정확(근사 아님).

`app/`의 `RRF_K` 상수·`_search_rrf`는 **그대로 둔다.** 스윕은 순수 실험 스크립트 안에서만 K를 바꾼다.

## 2. 신규 스크립트 — `tests/fixtures/rrf_eval/sweep_rrf_k.py`

`compare_strategies.py`의 인프라를 최대한 재사용한다(DRY). 재사용 대상:
- `_load_queries()`, `_load_valid_endpoints()`, `_validate_labels()`, `_make_temp_db()`, `_drop_temp_db()`, `_rank_of_answer()`
- 지표: `metrics.py`의 `dcg_at`/`recall_at`/`reciprocal_rank`, 그리고 `compare_strategies.py`의
  `_summarize()`/`EvalSummary`/`_format_summary_line()`/`RECALL_KS`.

가져오기가 지저분하면 공용 헬퍼(`_load_queries` 등)를 `metrics.py` 옆의 작은 모듈로 추출해 두 스크립트가
공유해도 된다(선택 — 과설계 말 것. import로 충분하면 그대로).

### 2.1 스크립트 흐름
```
1. 임시 DB 생성 → openapi.json 등록(compare_strategies와 동일 셋업).
   state.search_strategy = "rrf"로 bundle 구성.
   is_semantic=True(로컬 모델) 확인 — 벡터 arm이 죽어 있으면 스윕이 무의미(경고 후 중단).
2. 두 arm 순위 리스트를 질의당 1회 수집:
   - cs = bundle.candidate_search
   - width = max(TOP_K * 4, 50)          # 프로덕션 _search_rrf와 동일. (픽스처에선 사실상 전 코퍼스)
   - keyword_ref_ids = [h.ref_id for h in cs._keyword_search.search(q, top_k=width, document_id=None, project=None)]
   - candidate_ids = cs._chunk_repo.list_endpoint_chunk_ids(document_id=None, project=None)
   - vector_ref_ids = [h.ref_id for h in cs._vector_search.search(q, top_k=width, candidates=candidate_ids) if h.score > 0.0]
   ※ cs 내부 속성(_keyword_search 등) 접근은 "프로덕션과 바이트 동일한 arm"을 쓰기 위한
     실험 스크립트 한정 지름길이다. 주석으로 명시. (arm 로직을 손으로 복제하면 프로덕션과
     어긋날 위험 → 재사용이 정확.)
3. K 그리드 스윕(융합만 반복):
   K_GRID = (10, 20, 30, 40, 60, 80, 120)   # 60은 현행 기준선
   각 K에 대해, 질의마다:
     fused = reciprocal_rank_fuse(keyword_ref_ids, vector_ref_ids, top_k=TOP_K, k=K)
     ref_id → (method, path) 매핑은 cs._endpoint_repo.get(ref)로. rank = _rank_of_answer(...)
   질의별 rank 리스트 → _summarize()로 Recall@{1,3,5,10}·MRR·nDCG@10.
4. 출력: K별 지표 표. K=60 행에 '(기준)', 최고 지표 K에 '(최고)' 표기.
   추가로 "K=60 대비 각 K의 델타" 한 줄과, "K 변경 권장 여부" 결론 문구(4절 가드 적용).
5. finally: 임시 DB 삭제.
```

### 2.2 실행
```bash
docker compose up -d postgres
uv run python tests/fixtures/rrf_eval/sweep_rrf_k.py
```
`compare_strategies.py`와 마찬가지로 **pytest 수집 대상 아님**(실모델 로딩·전용 임시 DB). 회귀 의심 시 수동 실행.

## 3. 대상 평가셋 — 확장된 84질의

P1에서 20→**84질의**로 확장된 현재 `queries.json`을 그대로 쓴다. 별도 확장 불필요.

## 4. 과적합 가드 (★lead 지시로 계획에 명시) — 결과 해석 규칙

84질의는 20질의보다 낫지만 여전히 **통계적으로 얇고**, RRF의 K는 원래 60 근방에서 **둔감한(robust)**
파라미터로 알려져 있다(Cormack 2009 표준값). 따라서 스윕 결과 해석은 아래 규칙을 **스크립트 출력과
후속 판단에 반드시 적용**한다:

**K를 60에서 바꾸는 것을 권장하려면 아래를 모두 만족해야 한다:**
1. **유의미한 크기**: 개선폭이 잡음 수준(예: Recall 0.5pt 미만, MRR/nDCG 0.01 미만)을 넘는다.
2. **지표 일관성**: Recall@1/3/5/10·MRR·nDCG가 **한 방향으로** 개선(하나 오르고 다른 게 내리면 기각).
3. **소수 질의 의존 아님**: 순위가 바뀐 질의 수를 세어, 개선이 1~2개 질의의 우연이 아님을 확인
   (카테고리별 분해가 있으면 특정 카테고리 쏠림도 점검).
4. **K 이웃 안정성**: 최고 K뿐 아니라 **그 이웃 K들도** 60보다 나아야 한다. 특정 K 한 점에서만
   튀는 스파이크는 과적합 신호 → 기각.

**위를 모두 만족하지 못하면 → `K=60` 유지가 정답이다.** "개선 없음(null result)"도 유효한 결론이며,
표준값을 근거 없이 흔들지 않는 것이 옳다. 설정 표면을 늘리지 않겠다는 기존 방침(YAGNI)도 이를 지지한다.

## 5. 완료 기준 (검증)

1. `sweep_rrf_k.py`가 84질의에 대해 **K별 지표 표**를 출력하고, K=60 기준선·최고 K·델타·권장결론을 명시한다.
2. 두 arm이 질의당 1회만 실행되고(효율), K는 in-memory로만 스윕된다 — **`app/`의 어떤 파일도 변경되지 않는다**
   (특히 `RRF_K`는 60 그대로). `git diff -- app/`가 비어 있어야 한다.
3. 기존 테스트 전부 그대로 green(프로덕션 무변경이므로 당연). `uv run pytest tests/unit/test_rrf.py tests/unit/test_search_rrf_golden.py -q`.
4. 스크립트 출력(K별 표 + 권장결론)을 **architect(:0.1)에게 보고**한다. 나는 4절 가드로 해석해
   "K 변경 vs 유지"를 판정하고 lead에 보고한다.

## 6. 후속(이 태스크 밖) — K를 실제로 바꾸기로 결정될 경우에만

스윕 결과가 4절 가드를 **모두 통과**해 K 변경이 정당화되면, 그건 **별도 후속 태스크**다:
`app/services/search/rrf.py`의 `RRF_K` 상수 변경 → **골든 회귀 테스트 기대값 갱신**(`test_search_rrf_golden.py`) →
회귀 재검증. 이 실험 태스크에서는 **절대 프로덕션 K를 바꾸지 않는다.** 스윕은 "측정"까지만.

## 커밋
- 단일 커밋: `test: RRF K 스윕 실험 스크립트 추가`(스크립트만, 프로덕션 무변경).

## 설계 이탈 시
- N도 스윕하고 싶어지면(픽스처에선 무의미) 멈추고 architect(:0.1) 문의 — 0절 참고.
- 스윕을 위해 `app/`을 고쳐야 할 것 같으면 그 순간 멈추고 문의 — 이 태스크는 프로덕션 무변경이 핵심.
