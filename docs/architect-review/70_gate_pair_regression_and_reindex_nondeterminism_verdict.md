# 70. 확장 gate pair 회귀·재색인 비결정성 판정

- 대상: 69번 gate split, baseline/candidate × variants OFF/ON 4회 결과
- fixture: `ed7852e`, query SHA `6eb897d2…`
- baseline: `ecc3e792`, candidate: `8b4e36a`
- 상태: **수정 필요. candidate 승급 반려, holdout 계속 봉인**

## 1. 종합 판정

69번 §7.3을 그대로 적용한다. candidate `8b4e36a`는 현재 승급하지 않는다.

다만 두 HARD FAIL의 성격은 다르다.

1. **p09 child 1→2는 실제 reranker 결함이다.** query가 명시한 child resource보다
   문맥상 함께 언급된 ancestor resource를 우선하는 알고리즘 오류가 순수 함수에서
   재현된다. candidate를 수정해야 한다.
2. **p03 ON 9→10과 fallback 불일치는 별도 재색인 간 비교가 만든 측정 비결정성이다.**
   현 증거로 product 회귀라고 판정할 수 없다. 반대로 “1칸은 잡음”으로 HARD 기준을
   완화해서도 안 된다. 같은 물리 인덱스를 공유하는 paired 실행으로 비교를 다시
   유효하게 만들어야 한다.

ON R@10 +12%p, C2 R@3 +63%p 등 EFFECTIVENESS는 수정 방향의 가치가 크다는 근거지만,
HARD 예외 승인 근거는 아니다. p09를 고치고 실험 조건을 교정한 뒤 gate 4회를 전량
재실행한다. gate가 PASS하기 전 holdout/all은 실행하지 않는다.

## 2. p09 child 회귀 — 실제 코드 결함

### 2.1 원인

질의는 `list the line items inside that checkout session`, 정답은
`GET /v1/checkout/sessions/{session}/line_items`다.

현재 reranker는 다음과 같이 동작한다.

1. intent를 `LIST`로 올바르게 추출한다.
2. resource token에서 `item`과 `session`을 모두 얻는다.
3. 같은 family 안에서 `/line_items`의 leaf `item`과 상위 `/sessions`의 leaf
   `session`을 모두 `target_match=True`로 본다.
4. target match 후보 중 **최소 `relative_depth`**를 `specificity_match`로 택한다.
5. 따라서 query의 주 target인 깊은 `line_items`보다 ancestor context인 얕은
   `sessions` collection을 먼저 놓는다.

순수 함수에 아래 세 후보를 넣어 재현한 결과도 동일하다.

```text
입력: line_items child, sessions collection, session item
출력: sessions collection, line_items child, session item
```

즉 wide RRF/top-k cut이나 pgvector 잡음이 원인이 아니다. reranker 자체가 1위 child를
2위로 내린다. 68번의 “명시 child resource를 root로 끌어올리지 않는다”와 69번 pair
HARD가 잡도록 설계한 바로 그 회귀다.

### 2.2 수정 계약

target match가 있는 family에서는 `specificity_match`의 depth 방향을 바꾼다.

```text
target_match 후보가 있음:
    preferred_depth = max(relative_depth)  # 가장 구체적인 명시 leaf
target_match 후보가 없음:
    preferred_depth = min(relative_depth)  # 기존 보수적 root/item fallback
```

정렬 우선순위 `method_match → target_match → shape_match → specificity_match → original rank`
자체는 유지한다. path 전체 길이 boost나 “원래 1위는 고정” 예외는 넣지 않는다.

이 규칙이면:

- `line items ... checkout session`: item과 session이 함께 언급돼도 더 깊은
  `line_items` leaf가 우선한다.
- `pull up one checkout session`: 같은 `session` leaf의 collection/item 사이에서는
  `GET_ONE` shape가 item을 고른다.
- `delete customer discount`: customer는 ancestor context, discount는 깊은 명시 target이다.
- root만 언급된 질의는 기존 root 선택을 유지한다.

평가 질의 문자열이나 `pair_id`를 production reranker에 전달하는 수정은 금지한다.

### 2.3 필수 단위 테스트

`tests/unit/test_endpoint_route_reranker.py`에 다음을 추가한다.

1. p09 축소 재현: `line_items`가 이미 1위이면 rerank 뒤에도 1위
2. parent와 child resource가 질의에 함께 있을 때 deepest matched leaf 우선
3. root-only GET_ONE은 기존 item root 선택 유지
4. 명시 child 없는 경우의 기존 shallow fallback 유지
5. index별 family 배열 불변 유지

수정 뒤 기존 reranker/endpoint candidate 테스트 전부를 통과해야 한다.

## 3. p03 ON 회귀 — variants 문제가 아니라 실행 비결정성

p03 root 질의 `wipe a customer from the account for good`는 영어이며 variants 필드가
없다. 같은 구현·같은 query라면 OFF와 ON의 검색 입력은 동일해야 한다. 그런데 별도
재색인 실행에서 다음과 같이 뒤집혔다.

- baseline: OFF 10위, ON 9위
- candidate: OFF 9위, ON 10위

candidate에서만 variants가 family 판정을 흔든 현상이 아니다. variants가 아예 없는데
두 구현 모두 재색인마다 9/10위가 바뀌었다. p03의 현재 결과는 product regression을
증명하지 못하며 gate 비교 표본으로도 유효하지 않다.

같은 물리 인덱스에서 재실행한 뒤에도 candidate가 baseline보다 뒤에 있을 때만 genuine
p03 회귀로 판정한다. 그 경우 HARD는 그대로 FAIL이고 별도 reranker 수정이 필요하다.
지금 단계에서 p03 전용 lexicon·예외를 코드에 넣지 않는다.

## 4. fallback control — 기준 완화 반려, 실험 교정

### 4.1 비결정성 증거

fallback은 `8b4e36a`에서 수정되지 않았다. per-query 표에는 이미 원인 판정에 충분한
증거가 있다.

`g058 pull up my past invoices`는 영어이고 variants가 없다.

- baseline fallback: OFF 5위, ON 6위
- candidate fallback: OFF 6위, ON 5위

동일 코드·동일 입력이 재색인마다 5/6위를 오간다. 따라서 candidate 경로 누수보다
별도 인덱스 구축 차이가 먼저 입증됐다.

코드에도 그 원인이 가능한 구조가 있다.

- 매 등록마다 `Document.id`가 UUID로 새로 생성된다.
- endpoint/chunk id는 이 document id를 포함하므로 재색인마다 달라진다.
- keyword 동점은 `Chunk.id`, vector 동점은 `Chunk.id`, RRF 동점은 ref_id로 깨므로
  의미 점수가 같아도 새 ID가 순서를 바꿀 수 있다.
- vector arm은 HNSW 근사 인덱스이므로 별도 물리 인덱스 구축 자체도 cutoff 부근 후보를
  달리 만들 수 있다.

### 4.2 HARD 임계값은 유지

fallback exact-equality를 `±1건`이나 `±1 rank`로 완화하는 안은 반려한다. fallback은
candidate 수정 비대상인 rollback control이므로, 동일 조건에서는 완전 동일해야 한다.
이 control이 깨진 것은 기준이 지나치게 엄격해서가 아니라 비교 조건이 동일하지 않았음을
보여 준다.

집계 지표만 같으면 PASS로 바꾸는 것도 반려한다. 서로 다른 질의의 1 gain/1 loss가
상쇄될 수 있어 product 회귀를 숨긴다. capped per-query rank 완전 동일을 유지한다.

### 4.3 동일 물리 인덱스 paired 실행

공식 재실행은 baseline/candidate가 **같은 PostgreSQL DB와 같은 HNSW 인덱스**를 읽어야
한다. 두 worktree에서 각각 재색인하지 않는다.

새 러너를 만들지 않고 기존 `run_corpus_eval.py`에 shared-index 모드만 보강한다.

1. 한 번만 임시 DB를 생성하고 frozen corpus를 등록한다.
2. DB를 drop하지 않고 URL/DB 식별자와 index fingerprint를 출력한다.
3. baseline/candidate worktree의 기존 runner가 그 DB URL을 받아 등록·재색인·drop을
   생략하고 read-only 평가만 수행한다.
4. 네 실행이 끝난 뒤 명시적으로 임시 DB를 정리한다.

shared-index preflight 출력에는 최소 다음이 필요하다.

- DB 식별자
- Stripe/GitHub content SHA와 document id
- endpoint 수 589/1220, endpoint chunk 수
- sorted `(doc, method, path, chunk_id)` 기반 fingerprint
- query SHA와 fixture commit

검색 중 DB 쓰기는 하지 않는다. `SET LOCAL hnsw.ef_search` 같은 세션 설정은 허용하되
인덱스·row는 네 실행 동안 불변이어야 한다. 이 방식은 ADR-0003 read-only 경계와도
정합한다.

고정 document id만 주고 DB를 네 번 재구축하는 대안은 채택하지 않는다. ID tie-break는
줄일 수 있어도 별도 HNSW graph 차이를 제거하지 못한다. 같은 물리 인덱스 공유가 판정에
필요한 더 강한 조건이다.

### 4.4 결정성 preflight

공식 비교 전에 같은 shared index에서 다음을 확인한다.

1. baseline OFF를 2회 실행해 fallback/rrf per-query capped rank가 완전 동일
2. candidate OFF를 2회 실행해 fallback/rrf per-query capped rank가 완전 동일
3. variants가 없는 질의는 같은 구현의 OFF/ON rank가 동일

하나라도 다르면 gate를 실행하지 않고 하네스/검색 결정성 문제로 다시 회부한다.

## 5. 재실행과 프리즈 처리

query/accepted/variants/pair/split에는 오류가 없다. 다음은 그대로 유지한다.

- `queries_gate_v1.json` bytes와 query SHA
- corpus SHA
- gate/holdout split
- 69번 HARD/EFFECTIVENESS 임계값

candidate 수정 때문에 질의셋 v2를 만들지 않는다. holdout은 아직 실행하지 않았으므로
sealed 성격도 보존됐다. manifest의 candidate SHA는 비교 대상 메타데이터이므로 새
candidate SHA를 결과 기록에 추가하되 query SHA를 다시 만들거나 라벨을 수정하지 않는다.

재실행 순서:

1. developer가 §2 reranker 수정과 shared-index runner 보강
2. 관련 단위 테스트와 결정성 preflight
3. 같은 shared index에서 baseline/candidate × OFF/ON gate 4회 전량 재실행
4. 69번 §7을 처음부터 다시 판정 — 기존 EFFECTIVENESS PASS를 이월하지 않음
5. gate 전항 PASS일 때만 lead가 holdout, 이후 all 실행

## 6. 최종 판정표

| 사안 | 판정 | 후속 |
|---|---|---|
| p09 child 1→2 | genuine candidate 결함 | deepest matched leaf 수정 후 재실행 |
| p03 ON 9→10 | 현재 측정 무효, product 회귀 미확정 | shared index에서 재판정 |
| fallback 불일치 | 재색인 비결정성으로 control 무효 | exact HARD 유지, shared index 사용 |
| ON 대폭 개선 | 수정 실익의 강한 방향성 근거 | HARD 예외 승인에는 사용하지 않음 |
| candidate `8b4e36a` | 승급 반려 | 수정 candidate SHA로 gate 재시작 |
| holdout/all | 계속 봉인 | gate 전항 PASS 뒤에만 실행 |

데이터셋이나 HARD 규칙을 결과에 맞춰 바꾸지 않는다. candidate와 실험 조건을 고쳐 이미
프리즈한 v1 gate에 다시 답하게 한다.
