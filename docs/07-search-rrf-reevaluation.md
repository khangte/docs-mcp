# RRF(순위 융합) 적용 재검토 — P1 이후

- 상태: **구현+실측 완료**(RRF 구현·배포 커밋 `33d1dbe`, 평가셋 커밋 `c197ec9`). 5절 설계 확정 → 6절 실측 결과 참조.
- 일시: 2026-08-08
- 작성: architect
- 관련: `docs/search-performance-improvements.md`(상위 제안 RRF), `docs/search-p1-keyword-fts-design.md`, ADR-0002, SPEC Phase 0 결정 6
- 대상: `app/services/search/endpoint_candidate_search.py`, `keyword_search.py`, `vector_search.py`, `app/repositories/chunk_repository.py`

## 현재(P1 이후) 구조 재확인
`EndpointCandidateSearch.search` 흐름:
1. `has_endpoint_chunks` 로 스코프 내 endpoint 청크 존재 확인(없으면 `[]`).
2. **키워드 FTS**(`search_endpoint_by_text`: 질의 term 을 `|` OR 로 결합 → `to_tsquery('simple')` @@ `text_tsv` GIN → `ts_rank` 내림차순 top_k).
3. **키워드가 1건이라도 있으면 그대로 반환** — 벡터(임베딩 API)는 호출조차 안 함.
4. 키워드 0건일 때만 벡터 fallback(질의 임베딩 → HNSW 코사인).

즉 여전히 **OR-fallback**(배타적 분기)이지 순위 융합이 아니다. `match_type` 도 `keyword`/`vector` 배타값.

## P1이 바꾼 지점(재검토의 축)
P1은 키워드 recall 을 **양방향으로** 넓혔다: (a) 한글 단어 매칭, (b) 경로 세그먼트 분해(`/orders/{orderId}`→`orders`,`orderid`), (c) 혼합복합어(`GET요청`→`get`,`요청`). 그 결과:

- **예전**: 한글 질의·경로 질의가 자주 키워드 0건 → 벡터 fallback 을 탐 → 벡터 호출이 잦았다.
- **지금**: 같은 질의가 키워드에서 잡혀 **벡터를 안 탄다** → 벡터 호출(임베딩 비용) 자연 감소.

**핵심 반전(중요)**: 비용은 줄었지만, 동시에 **"키워드 단독으로 최종 결정되는 질의"의 비중이 커졌다.** OR-fallback 게이트는 "키워드 ≥1건이면 멈춘다"인데, P1으로 키워드가 ≥1건을 반환하기가 더 쉬워졌으므로 **게이트가 더 자주 발동 = 벡터가 개입할 기회가 더 줄었다.** 즉 lead 질문의 프레이밍을 한 겹 뒤집으면 — 의미검색을 놓치는 표면적은 P1 이후 **줄어든 게 아니라 늘었다.** 이게 재검토가 지금 의미 있는 이유다.

---

## 1. 지금도 의미검색을 놓치는 케이스가 있는가 — 있다(구체 패턴)
키워드는 term 중 **하나라도** 리터럴 매칭되면 히트를 낸다(OR). 따라서 "무언가는 잡혔지만 그게 최선이 아닌" 상황에서 벡터가 봉쇄된다. 구체 패턴:

1. **정답이 키워드에 존재하나 top_k 밖으로 밀림(가장 값어치 큼)**: 질의 토큰과 어휘 겹침이 약한 정답 엔드포인트가 `ts_rank` 하위(예: #12)로 밀려 top_k=5 에서 잘림. 벡터라면 의미 유사도로 상위에 올렸을 것. 키워드가 ≥1건이라 벡터는 실행 안 됨. **lead가 지목한 "매칭되지만 순위가 안 좋은 경우"가 이것.**
2. **동의어/패러프레이즈**: 질의 `로그인` 인데 정답 summary 는 `인증`/`authenticate`/`signin`. 어느 청크도 `로그인` 을 리터럴로 안 담으면 0건→벡터(정상). 하지만 **무관한 청크 하나가 description 에 우연히 `로그인` 을 담고 있으면** 그 우연 매칭이 키워드 히트가 되어 벡터를 봉쇄하고 정답은 못 찾음. 동의어는 정확히 벡터의 강점 영역.
3. **흔한 토큰의 오탐 범람**: `create user order` → `user` 가 무관한 다수 엔드포인트 description 에 매칭. `ts_rank` 는 이들을 채우고, `user` 를 안 쓰고 `POST /orders — 신규 주문 생성` 으로 표현된 정답은 누락. 벡터 봉쇄.
4. **다개념 질의에서 어휘·의미 신호 불일치**: `구독 해지하고 환불` → `환불` 이 refund 엔드포인트에 리터럴 매칭되어 히트. 의도 중심인 `구독 해지`(정답 summary 는 `terminate`/`해지`)는 어휘가 어긋나 밀림. 키워드가 refund 를 내놓고 멈춤.
5. **필드 희석**: 청크 텍스트는 method/path/tags/params/responses 를 연결한 것이라, 질의 토큰이 **파라미터 이름**에 매칭되어 무관 엔드포인트가 히트→벡터 봉쇄.

요지: 남은 손실은 주로 **(a) 정답이 키워드에 있으나 순위·top_k 컷으로 탈락**, **(b) 우연한 단일 토큰 리터럴 매칭이 벡터 경로를 통째로 봉쇄**하는 두 축. P1은 "0 토큰" 케이스를 줄인 대신 "무언가 우연히 잡혀 멈춤" 케이스를 늘려, **후자(더 은밀한 실패)를 오히려 확대**했다.

## 2. RRF 전환 시 임베딩 비용 — P1 이후 재추정
- **절대 단가는 불변**: RRF="상시 임베딩"이면 질의당 임베딩 호출 1회(짧은 **질의** 문자열, 문서 임베딩 아님). 로컬 CPU 모델(`intfloat/multilingual-e5-small`)은 질의 1건당 금전 비용이 아예 없다(API 호출 자체가 없음, CPU 추론 시간만 소요). 문서 임베딩은 색인 시점에 이미 발생하므로 RRF 와 무관.
- **상대 증가폭은 커졌다**: P1으로 현재 baseline 의 "임베딩을 실제로 호출하는 질의 비율"이 **떨어졌다**(한글·경로 질의가 키워드로 해소). 따라서 "상시 임베딩"으로 가는 **델타(현재 대비 증가분)는 예전 추정보다 크다.** 단 절대 상한은 예전과 같은 "질의당 1회"로 동일.
- **진짜 비용은 돈이 아니라 (a) 지연·(b) 의존성**:
  - **지연**: RRF 는 **모든** 검색에 임베딩 왕복(수십~수백 ms)을 추가한다. P1이 즉답(키워드만, sub-ms)으로 만든 다수 질의에 이 지연이 새로 얹힌다 — 흔한 케이스의 체감 저하. **이 지연 델타가 P1 때문에 오히려 커진 것**이 재추정의 핵심.
  - **provider 의존성**: 현재 임베딩 백엔드가 해시 폴백(`DOCS_MCP_EMBEDDING_BACKEND=hash`, 테스트/모델 로드 실패용)이면 벡터는 조용히 생략(`vector_fallback_enabled=False`)되고 키워드로 동작한다. RRF 는 벡터 arm 을 상시 요구 → **해시 폴백이면 `HashEmbeddingProvider`(결정적 해시, 의미 없음)** 가 벡터 자리를 채운다. 이걸 RRF 로 융합하면 **노이즈를 정답 신호와 섞는 셈**. 따라서 RRF 는 **로컬 모델(`is_semantic=True`)이 활성화된 배포에서만** 켜지고, 해시 폴백이면 키워드 단독으로 degrade 해야 한다(설계 제약).
  - **캐시 효과 제한**: 질의 임베딩 LRU 캐시(예전 P5)는 반복 질의만 상쇄. MCP 검색은 대개 고유 자연어라 캐시 적중률 낮음.

결론: 금전 비용은 무시 가능하나, **"모든 질의에 임베딩 지연 + provider 강결합"** 이 실질 비용이고 그 델타는 P1 이후 baseline 이 낮아진 만큼 상대적으로 커졌다.

## 3. 구현 난이도/개략 설계 (중간)
**RRF 채택 이유**: `ts_rank`(키워드)와 코사인 유사도(벡터)는 스케일이 달라 가중합이 지저분하다. RRF 는 **등수만** 쓰므로 스케일 불변 — 두 신호 융합에 자연스럽다.
- 공식: `score(d) = Σ_r 1/(K + rank_r(d))`, K≈60. 각 ranker(키워드/벡터)에서 d 의 등수로 합산.

바꿔야 할 것:
- **`EndpointCandidateSearch.search`**: "키워드→(0이면)벡터" 배타 분기를 **두 ranker 병렬 실행 → 융합**으로 교체. 각 ranker 는 top_k 가 아니라 **더 넓게**(예: `N = max(top_k*4, 50)`) 가져와야 융합이 의미 있다(정답이 한쪽 상위에만 있어도 건짐). 융합 후 top_k 로 컷.
- **벡터 쪽 ref_id 확보(P2 잔여 흡수)**: 융합은 endpoint(ref_id) 단위여야 한다. 키워드는 이미 `(chunk_id, ref_id, score)` 반환. 벡터 `search_by_vector` 는 `(chunk_id, score)` 뿐 → **SQL 프로젝션에 `ref_id` 추가**(api_chunk 컬럼이라 조인 불필요, P1이 키워드에 한 것과 동형). 이러면 벡터 fallback 이 전 청크를 메모리 적재하던 것(`_endpoint_chunks`)도 제거되어 **P2가 벡터 경로까지 완성**된다.
- **융합 로직**: 각 ranker 결과를 ref_id 기준 first-occurrence 로 dedupe→등수 부여→RRF 합산→정렬→top_k. 수십 줄 규모.
- **`match_type` 계약 변경**: 더는 배타적 `keyword`/`vector` 가 아님. 선택: `hybrid` 단일값, 또는 기여 ranker 표기(`keyword`/`vector`/`both`). **MCP 도구 출력 계약 변경**이므로 명시 필요(`MatchType` Literal 확장).
- **provider 게이팅**: 해시 폴백이면 벡터 arm 비활성 → 키워드 단독 순위로 degrade(기존 `vector_fallback_enabled` 플래그 재활용).
- **설정**: 전략 플래그(`fallback`|`rrf`)와 RRF 상수 `K`. 주의 — `.env.example` 의 `DOCS_MCP_HYBRID_ALPHA=0.4` 는 **가중합** 의미라 RRF(K 기반)와 다르다. 가중합을 쓰려면 ts_rank·코사인 정규화가 필요(지저분) → **RRF(스케일 불변) 권장**, alpha 는 이 경로에 미적용임을 문서화.
- **거버넌스**: 이 전환은 **SPEC Phase 0 결정 6(키워드 우선·임계값 없음)을 뒤집는다** → 코드가 아니라 결정의 번복이므로 그 결정 재승인 필요.

난이도 총평: **중.** 융합 코어는 단순하나, ranker 폭 확대·벡터 ref_id 추가·`match_type` 계약 확장·provider 게이팅·**순위 회귀 재검증(골든 기대값 갱신)** 이 실작업. 모든 질의가 임베딩 지연을 지는 성능 특성 변화도 수반.

## 4. 최종 권장
**"계속 보류"보다는 적기에 가까워졌다 — 단, 무조건 지금이 아니라 조건부 착수 권장.**

근거(저울):
- **찬(지금)**: P1은 키워드 recall 을 높인 동시에 **벡터가 개입 못 하는 질의 비중을 키웠다**(1항). 그중 값어치 큰 실패(정답이 키워드에 있으나 top_k 밖으로 밀림, 우연 토큰 매칭이 벡터 봉쇄)를 RRF 가 직접 교정. 금전 비용은 무시 가능(2항). 벡터 경로 ref_id 화로 P2 잔여까지 함께 정리(3항).
- **주의(제동)**:
  1. **지연**: 모든 검색에 임베딩 왕복 추가 — P1이 즉답으로 만든 흔한 케이스가 느려짐(체감 저하).
  2. **provider 조건**: 로컬 모델이 활성화된 배포에서만 이득. 해시 폴백 배포엔 노이즈 융합 위험 → RRF 는 로컬 모델 활성 배포로 한정, 해시 폴백이면 키워드 단독 degrade 필수.
  3. **거버넌스**: SPEC 결정 6 번복.
  4. **측정 부재(가장 큰 제동)**: 위 손실 패턴은 **추론**이지 **관측된 실패**가 아니다. RRF 는 품질 최적화인데, 품질 신호(평가셋/실사용 불만) 없이는 개선인지 회귀인지 검증 불가.

**권장 실행안(조건부):**
1. **전제 A — 대상 배포에서 로컬 모델(`is_semantic=True`)이 활성화되어 있는가** 확인. 아니라면(해시 폴백 운영) RRF 이득이 없으므로 **보류 유지**.
2. **전제 B — 최소 평가셋 구축**: 대표 질의 10~20개(정답 엔드포인트 라벨, 동의어·패러프레이즈·한글·경로 포함)로 현재 fallback vs RRF 를 before/after 측정. RRF 는 품질 변경이라 이 계측 없이 착수하면 회귀를 못 본다.
3. 전제 A 충족 + 평가셋에서 1항 손실이 **실측**되면 → **RRF 착수가 지금 적기**(P1으로 벡터 ref_id 화·P2 정리를 겸할 수 있어 타이밍도 좋음). 평가셋에서 키워드 단독이 이미 정답을 맞히면 → **cheaper 유지**(보류).
4. 착수 시 순서: 벡터 `search_by_vector` ref_id 프로젝션(P2 완성) → ranker 폭 확대 + RRF 융합 + provider 게이팅 → `match_type` 계약 확장 → 평가셋·순위 골든 회귀 통과 → SPEC 결정 6 재승인.

**한 줄 요약**: P1이 baseline 을 낮춰 RRF 의 상대 지연·상대 비용 델타는 커졌지만, 동시에 벡터가 봉쇄되는 질의를 늘려 **품질 공백도 커졌다.** 로컬 모델이 활성화된 배포라면 RRF 는 이제 "해볼 만한" 단계 — 다만 **평가셋으로 손실을 실측한 뒤** 켜는 것이 옳고, 측정 없이 지금 바로는 권하지 않는다.

---

## 5. 착수 확정 (architect, 2026-08-08)

lead 지시(`task-rrf-kickoff.md`)에 따라 4절 조건부 권장을 **착수 확정**으로 갱신한다. 갱신 근거는 문서 작성(4절) 이후 바뀐 두 사실이다.

### 5.0 전제 A/B 재체크 — 착수 타당

**전제 A(로컬 모델 `is_semantic=True` 활성) — ✅ 완전 충족.**
기본 백엔드가 `DOCS_MCP_EMBEDDING_BACKEND=local`(`multilingual-e5-small`)이고, `is_vector_fallback_available()`가 `backend != "hash"`로 판별한다(`app/composition.py`). 4절이 "Gemini 키 게이팅"으로 썼던 조건은 이미 `is_semantic` 기준으로 재정의돼 있으므로, 로컬 모델 배포에서 전제 A는 상시 참이다.

**전제 B(최소 평가셋 실측) — △ 부분 충족(정직하게).**
엄밀한 라벨셋(10~20개, 정답 라벨)은 아직 없다. developer의 10개 질의 스팟체크가 손실 패턴을 관측했으나, 그 성격을 정직하게 구분한다:
- 관측 사례("로그인"→로그아웃 근소 1위, "비밀번호를 잊어버렸어요" 정답 2위 밀림)는 **벡터 arm 자체의 혼동**이지, 1절이 주 손실축으로 지목한 "키워드가 벡터를 봉쇄"의 직접 실측은 아니다.
- 다만 이 사례들은 **RRF의 유효한 이득 방향에 부합**한다. RRF는 대칭 융합이라 어느 arm이 헷갈리든 다른 arm이 정답을 상위에 두면 융합 순위가 교정된다("로그인" 리터럴이 로그인 엔드포인트에 있으면 키워드 arm이 로그아웃 우위를 눌러준다).
- N=10·비형식 라벨이라 **회귀를 통계적으로 잡아낼 평가셋으로는 부족**하다. 이 한계는 숨기지 않는다.

**그럼에도 착수 타당**으로 판정하는 근거(저울이 4절 대비 크게 기운 지점):
1. **지연 제동 해소(결정적)**: 4절이 "가장 큰 체감 저하"로 꼽은 "모든 질의에 임베딩 왕복 추가"는 Gemini API(수십~수백ms) 전제였다. 로컬 실측 `embed_query` 단건 **avg 13.7ms / p95 16.8ms(네트워크 0)** — 4절 주의 1번(지연)이 사실상 무력화된다. 저울의 최대 반대추가 사라졌다.
2. **전제 A 완전 충족**: 해시 폴백 배포는 자동으로 벡터 arm이 비활성(`vector_fallback_enabled=False`)이라 "노이즈 융합" 위험이 구조적으로 차단된다(5.5).
3. **금전 비용 0**: 로컬 CPU 추론, API 호출 없음(2절).
4. **타이밍**: 벡터 경로 ref_id화로 P2 잔여를 함께 정리(5.4).

**측정 부재 리스크의 처리(중요)**: 품질 개선의 **정량 입증은 착수의 선결조건에서 후속 산출물로 격하**한다. 대신 회귀 방어를 두 겹으로 건다 — (a) **순위 골든 회귀 테스트**(fallback/rrf 두 경로 각각 결정적 기대 순위), (b) **롤백 스위치**(`fallback` 전략 상시 보존, env 한 줄로 즉시 복귀, 5.5). 즉 "RRF가 개선임을 지금 증명"하지 않고, "회귀 시 즉시 되돌릴 수 있고 새 경로는 결정적임을 보장"하는 방식으로 리스크를 관리한다. 정식 평가셋(전제 B 완전 충족)은 착수와 병행/후속으로 남긴다.

### 5.1 `match_type` 계약 — 기여 ranker 표기 채택

`hybrid` 단일값이 아니라 **기여 arm 표기**로 확정한다.

```python
MatchType = Literal["keyword", "vector", "both"]
```

의미 정의(계약):
- **`both`**: 후보가 키워드 arm·벡터 arm의 후보 폭 N 양쪽에 모두 등장(가장 강한 신호).
- **`keyword`**: 키워드 arm에만 등장(벡터 폭 밖).
- **`vector`**: 벡터 arm에만 등장(키워드 폭 밖).
- **fallback 전략 또는 해시 폴백 degrade**: 기존과 동일하게 `keyword`/`vector` 배타값(단일 arm이므로 `both` 불가).

채택 이유: RRF는 두 arm 융합이라 "어느 arm이 이 후보를 올렸나"가 진단·디버깅·후속 회귀분석에 유의미하다. `hybrid`는 이 정보를 버린다. 기존 `keyword`/`vector` 값을 **그대로 유지**하고 `both`만 추가하므로 하위호환(기존 소비자가 보던 값은 계속 나옴).

**계약 변경 반영 지점(3곳 동시 갱신)**:
- `app/services/search/endpoint_candidate_search.py` — `MatchType` Literal.
- `app/mcp/types.py:57` — `EndpointCandidateItem.match_type` Literal.
- `app/mcp/tools/endpoints.py` — 도구 docstring의 `match_type("keyword" 또는 "vector")` 설명을 `keyword`/`vector`/`both`로 갱신.

### 5.2 RRF 상수 K = 60 — 확정

표준값 `K = 60`(Cormack et al. 2009)을 채택한다. **모듈 상수로 하드코딩**(`RRF_K = 60`), `.env` 노출하지 않는다 — 평가셋 없이 K 튜닝은 무의미하므로 설정 표면을 늘리지 않는다(YAGNI). 후속 평가셋 구축 후 튜닝 필요가 실증되면 그때 노출.

### 5.3 후보 폭 N = max(top_k * 4, 50) — 확정

각 arm에서 융합 전에 가져올 후보 수. `top_k=5`(기본)→`N=50`, `top_k=50`(최대)→`N=200`. 각 arm의 SQL `limit`으로 전달(`search_endpoint_by_text` / `search_by_vector`의 top_k 인자 = N), 융합 후 top_k로 컷.

근거: endpoint 청크는 단일 문서 수백 규모라 N=200도 SQL top-N·메모리 융합(200×2 등수 계산) 모두 무시 가능한 비용. `max(_, 50)` 바닥은 top_k가 작아도 "정답이 한 arm의 #30에 있어도 건지는" 융합 유효폭을 확보하기 위함.

### 5.4 RRF 융합 명세 (developer 구현 계약)

```
입력: keyword_ref_ids(키워드 arm 순위 리스트), vector_ref_ids(벡터 arm 순위 리스트), K=60, top_k
1. 각 arm에서 ref_id first-occurrence 등수(1-based) 부여
   — 같은 ref_id의 여러 chunk는 첫 등장만 채택(현재 _to_candidates seen 로직과 동형).
2. score(ref) = Σ_arm 1/(K + rank_arm(ref))   # 해당 arm에 없으면 그 항은 0
3. match_type(ref) = both(양쪽) | keyword(키워드만) | vector(벡터만)
4. 정렬: score 내림차순, 동점이면 ref_id 오름차순(결정적 tie-break — 골든 안정성 필수)
5. 상위 top_k 컷 → [(ref_id, match_type)]
```

**tie-break를 ref_id asc로 못박는 것이 골든 회귀 테스트 안정성의 전제**다. 반드시 결정적으로.

### 5.4.1 벡터 arm ref_id 프로젝션 (P2 완성 — 선행 작업)

융합은 endpoint(ref_id) 단위여야 한다. 현재 벡터 경로는 `ChunkVectorHit(chunk_id, score)`뿐이고, `EndpointCandidateSearch._search_by_vector`가 전 endpoint 청크를 메모리 적재(`_endpoint_chunks`)해 chunk_id→ref_id를 역매핑한다. 이걸 SQL 프로젝션으로 대체한다(P1이 키워드에 한 것과 동형):
- `ChunkVectorHit`에 `ref_id` 추가, `search_by_vector` select에 `ApiChunk.ref_id` 추가.
- `VectorSearchHit`에 `ref_id` 전파.
- `EndpointCandidateSearch._endpoint_chunks` 메모리 적재 경로 제거.
이로써 **P2 잔여(벡터 경로 메모리 적재 제거)가 함께 완성**된다.

### 5.5 전략 플래그 — `DOCS_MCP_SEARCH_STRATEGY`, 기본값 `rrf`

- **env 키**: `DOCS_MCP_SEARCH_STRATEGY`, 값 `fallback | rrf`. `app/core/config.py` `Settings`에 `search_strategy` 필드 추가, `.env.example`에 주석과 함께 추가.
- **기본값**: **`rrf`**(바로 켠다).
- **롤백 스위치**: `fallback` 경로를 **삭제하지 않고 상시 보존**한다. RRF가 회귀를 내면 `DOCS_MCP_SEARCH_STRATEGY=fallback` 한 줄로 즉시 복귀.

기본을 `rrf`로 두는 근거(안전 롤아웃 관점):
1. 사용자가 명시적으로 착수 지시 → 기본 활성이 의도에 부합(플래그로만 두고 기본 fallback이면 실사용은 여전히 구경로, 신경로는 죽은 코드가 됨).
2. 지연 델타가 실측(p95 16.8ms)으로 무력화 → 기본 활성의 체감 리스크가 사라짐.
3. 해시 폴백 배포는 `vector_fallback_enabled=False`로 **자동 degrade**(키워드 단독) → 노이즈 융합 위험 없음. 즉 안전판이 코드에 이미 내장.
4. "기본 rrf + 롤백용 fallback"이 "기본 fallback + opt-in rrf"보다 안전 롤아웃에 유리 — 두 경로를 모두 살려 즉시 복귀 가능하면서 실사용은 신경로를 탐.

**게이트 조건**: developer는 (a) fallback 경로 보존 → (b) rrf 경로 + 골든 회귀 확립 → (c) 골든 통과 확인 후 기본을 `rrf`로 커밋. 골든 미통과 상태로 기본 rrf 커밋 금지.

**provider 게이팅(불변식)**: `vector_fallback_enabled=False`(해시 폴백)면 전략이 `rrf`여도 벡터 arm을 실행하지 않고 키워드 단독 순위로 degrade(match_type 전부 `keyword`). 기존 `_vector_fallback_enabled` 플래그 재활용.

**`DOCS_MCP_HYBRID_ALPHA`와의 관계**: alpha(가중합)는 RRF 경로에 **미적용**. RRF는 등수 기반 스케일 불변이라 alpha가 개념적으로 붙지 않는다. `.env.example`에 "alpha는 legacy `SearchService` 하이브리드 전용, `search_endpoints`의 rrf 경로엔 무관"을 명기.

### 5.6 거버넌스 — SPEC 결정 6 번복은 재승인으로 간주

SPEC Phase 0 결정 6("키워드 0건일 때만 벡터 트리거, 임계값 없음", `docs/exec_plans/docs_mcp_expansion/SPEC.md:377`)을 RRF가 정면으로 뒤집는다(항상 두 arm 실행).

**판정: 별도 명시 확인 불필요 — 사용자의 "RRF 착수" 지시를 재승인으로 간주.** 근거: 결정 6은 "벡터를 언제 트리거하나"에 대한 결정이고, "RRF 착수"는 정의상 "항상 두 arm 실행 후 융합"을 내포하므로 결정 6 트리거 조건의 폐기가 그 지시에 이미 담겨 있다.

단 **이력을 남긴다**(lead 판단·소유): SPEC 결정 6에 "RRF 전략(`DOCS_MCP_SEARCH_STRATEGY=rrf`) 도입으로 이 결정은 `fallback` 전략에 한해 유효, rrf 경로는 두 arm 상시 융합" 각주 추가를 권고. SPEC은 lead 소유 문서이므로 각주 반영 여부·시점은 lead가 결정한다.

### 5.7 developer 구현 순서 (요약 — 배분용)

1. **벡터 arm ref_id 프로젝션**(5.4.1, P2 완성): `ChunkVectorHit`·`VectorSearchHit`·`search_by_vector` select에 ref_id 추가, `_endpoint_chunks` 메모리 적재 제거.
2. **RRF 융합 코어**(5.4): 결정적 tie-break 포함. 각 arm 폭 N=max(top_k*4,50)로 조회.
3. **전략 분기**(5.5): `search_strategy`로 fallback/rrf 분기. rrf 경로에 provider 게이팅(해시면 키워드 단독 degrade).
4. **match_type 계약 확장**(5.1): 3개 파일 동시 갱신, `both` 추가.
5. **설정**(5.5): `DOCS_MCP_SEARCH_STRATEGY` 추가, `.env.example` 갱신(alpha 무관 명기).
6. **회귀 방어**: fallback/rrf 각 경로 순위 골든 회귀 테스트 + developer 10질의 before/after 스팟체크. 골든 통과 후 기본 rrf 커밋.

설계 불명확 시 architect(:0.1)에 즉시 문의.

---

## 6. 실측 결과 (구현 완료 후, 2026-08-08)

RRF가 구현·배포되고(커밋 `33d1dbe`), 5.0에서 후속으로 격하했던 **전제 B(최소 평가셋)가 충족됐다**(커밋 `c197ec9`, `tests/fixtures/rrf_eval/`). 이로써 4절 '최종 권장'이 착수의 최대 제동으로 꼽았던 "측정 부재 — 개선인지 회귀인지 검증 불가"가 해소된다.

**측정 결과(20질의 평가셋, fallback vs rrf)** — ⚠️ **20질의 표본편향으로 후속 번복됨, 아래 각주 참조**:
- **top-1 정확도**: fallback = rrf = 16/20(80%) — **동일**. RRF가 1위를 흔들지 않았다(회귀 없음의 핵심 지표).
- **top-3 recall**: 80% → **95% 개선**. 정답이 top_k 안에 들어오는 비율이 오른다 — 4절 1항이 예측한 "정답이 키워드에 있으나 순위·컷으로 탈락"을 RRF가 실제로 교정했다는 직접 증거.
- **회귀**: 0건.

**의미**: 4절은 "품질 신호 없이는 개선/회귀를 검증할 수 없다"며 착수를 조건부로 묶었고, 5.0은 그 정량 입증을 후속으로 미뤘다. 이제 그 measurement가 존재하며, RRF는 **top-1을 지키면서 top-3 recall만 끌어올리는**(순수 이득, 회귀 0) 결과로 확인됐다. 5.0의 "골든 회귀 + 롤백 스위치 2겹 방어" 전제 위에서 착수한 판단이 실측으로 정당화됐다.

> **후속 정정(2026-08-10)**: 위 "top-1 불변(80%)"은 **20질의 평가셋의 표본편향**이었다.
> `docs/search-quality-post-rrf.md`의 P1(커밋 `97f3c2d`·`731d43c`)이 평가셋을 84질의로
> 확장 재측정한 결과, **Recall@1도 60%→69%로 개선**되는 것으로 나타났다(P1 실측 결과 절
> 참조). 즉 이 절의 "top-1 동일"은 최종 수치가 아니라 초기 소표본 관측치이며, 최신
> 수치는 `search-quality-post-rrf.md`를 따른다.
