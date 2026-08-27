# 68. endpoint route-family 제한 rerank · variants 대칭화 설계

- 요청 근거: 66번 A~C, 67번 §1, `docs/eval-results/03_2026-08-27_variants_diagnosis.md`
- 대상 경로: `search_endpoints`의 기본 `rrf` 검색 전략
- 비대상: 색인 쓰기·청크 포맷 변경, 별도 LLM 호출, fallback 롤백 전략 변경,
  q10·q11 라벨/질의 개정
- 상태: 구현 승인 설계. developer가 구현·측정하고 lead가 결과 확인 후 커밋한다.

## 1. 판정 요약

1. **B를 A와 합쳐 숨기지 않는다.** endpoint keyword arm은 variant를 후보 필터에만
   쓰고 점수는 원문으로만 계산하는 반면, vector arm은 원문·variant를 각각 검색해
   최선 등수로 병합한다. 먼저 keyword arm도 질의별 독립 rank의 최선 등수 병합으로
   대칭화한다.
2. **A는 RRF 점수 boost가 아니라 RRF 이후의 제한된 순열(permutation)이다.** 넓은
   RRF 후보를 endpoint 메타데이터로 hydrate한 뒤, 동일 route family가 차지하던
   슬롯 안에서만 operation 의도와 path specificity가 맞는 후보를 앞으로 보낸다.
   family 사이의 상대 위치는 바꾸지 않는다.
3. **q05는 우선 B 대상, q07은 B로 후보를 유입한 뒤 A 대상이다.** q08·q09는
   명시적인 item-delete 의도가 있어 A 대상이다. q12는 operation이 없는 bare noun이라
   A가 의도적으로 no-op한다. 66번 B의 진단 질의 성격을 코드 휴리스틱으로 덮지 않는다.
4. **q10·q11과 `chunk_builder.py`는 이번 코드 변경에서 제외한다.** 컬렉션 루트의
   구조 표지가 약한 점은 별도 색인 실험 가설로만 기록한다.

## 2. 현재 코드에서 확인한 원인

### 2.1 variants arm 비대칭

`EndpointCandidateSearch._search_vector_with_variants`는 `[query, *variants]`를 각각
벡터 검색하고 ref_id별 최선 등수를 취한다. 반면 `KeywordSearch.search`는 원문과
variant term을 OR 후보 필터에 넣지만 `score_terms=terms`로 원문만 `ts_rank`에
반영한다. 한글 원문과 영문 색인만 만나는 경우 variant가 후보를 열어도 keyword
순위 신호는 0에 가깝다.

이 비대칭은 q05의 variants on wide 순위가 35→41로 오히려 밀리고, q07은 후보에는
유입되나 22위에 머문 현상과 정합한다. 따라서 B는 A와 독립된 검색-arm 결함이다.

### 2.2 top-k 절단 위치

현재 `_search_rrf`는 arm별 width를 최소 50으로 가져온 뒤
`reciprocal_rank_fuse(..., top_k=top_k)`에서 10건으로 먼저 자른다. q05·q07처럼
RRF 22~41위에 있는 endpoint는 그 뒤의 후처리로는 살릴 수 없다.

A를 적용할 때는 RRF를 `top_k=width`까지 계산·hydrate하고, 제한 rerank 후 최종
`top_k`로 자른다. rerank가 no-op이면 wide RRF의 앞 `top_k`는 기존 RRF 결과와
동일해야 한다.

## 3. B — endpoint keyword variants 대칭화

### 3.1 변경 위치와 알고리즘

`KeywordSearch`의 공용 계약은 바꾸지 않는다. 이 클래스는 문서 검색에서도
“variant는 필터만 넓히고 원문으로 점수 계산” 규약을 사용하므로 전역 변경 시 검색면 B에
예상하지 않은 순위 변경이 생긴다.

`EndpointCandidateSearch`의 RRF 경로에만 module-private helper
`_search_keyword_with_variants`를 둔다.

```text
queries = query + 공백 제거·중복 제거한 query_variants
각 q에 대해 KeywordSearch.search(q, width, ..., query_variants=None)
ref_id별 best_rank = 모든 q 검색 결과 중 최소 rank
(best_rank, ref_id) 오름차순으로 keyword_ref_ids 생성
```

- 원문 가중을 낮추지 않는다.
- variant 우선 boost도 두지 않는다.
- 여러 표현 중 하나에서 강하게 맞은 후보를 살리는 vector arm과 같은 규칙을 쓴다.
- 빈 문자열·원문과 동일한 variant·중복 variant는 호출 전에 제거한다.
- `query_variants=None`이면 keyword SQL 호출은 기존처럼 정확히 한 번이고 결과 순서도
  기존과 동일해야 한다.
- fallback 전략은 롤백 계약이므로 이번에 바꾸지 않는다. B는 기본 RRF 전용이다.

variant term 전체를 한 번의 `score_terms`로 합치는 안은 반려한다. variant 개수가
늘수록 더 많은 term이 점수에 들어가고, 여러 표현에 우연히 겹치는 긴 청크를 우대해
질의 수에 따른 점수 편향을 만든다. 독립 rank 병합은 점수 scale과 term 수에 무관하다.

### 3.2 B와 A의 역할 분리

- q04·q06: variants만으로 이미 1위·3위다. B 적용 뒤 이 순위를 보존해야 한다.
- q05: `refund a payment`에 명시적 CRUD operation이 없다. A가 억지로 HTTP POST를
  추론하지 않고 B가 영어 keyword rank를 정상 신호로 만드는 것이 우선이다.
- q07: variant가 endpoint를 wide 후보에 처음 유입한다. B가 arm 순위를 바로잡고,
  남은 동일-family item-delete 역전은 A가 처리할 수 있다.

구현·측정도 **B checkpoint → A+B** 순서로 한다. 영구 feature flag나 새 환경변수는
만들지 않는다. developer가 B까지만 적용한 작업트리에서 하네스를 한 번 기록한 뒤 A를
이어 구현한다.

### 3.3 클라이언트 variants 계약

MCP docstring은 비영문 질의에 영문 variant 제공을 강하게 안내하지만, tool schema가
그 의미를 강제하거나 서버가 누락을 검출하는 계약은 아니다. “항상 제공된다”를 correctness
전제로 두지 않는다.

- 서버가 variant 생성을 위해 별도 LLM API를 호출하지 않는다.
- variant가 있으면 B와 vector arm이 동등하게 활용한다.
- variant가 없으면 기존 원문 검색과 결정적 A만 동작한다.
- 검증은 variants off/on 양쪽을 계속 남긴다.

## 4. A — 제한적 route-family rerank

### 4.1 적용 지점

`rrf.py`의 일반 `reciprocal_rank_fuse` 공식과 가중치는 바꾸지 않는다. endpoint 전용
`_search_rrf`에서 다음 순서로 적용한다.

```text
keyword width rank + vector width rank
  → reciprocal_rank_fuse(top_k=width)
  → endpoint get_many 1회로 wide 후보 hydrate
  → 결정적 operation intent 추출
  → route-family별 constrained permutation
  → 최종 top_k 절단
  → EndpointCandidate 변환
```

후처리 패스를 택하는 이유는 세 가지다.

1. RRF는 서로 다른 ranker의 등수를 합치는 일반 모듈이고 path 의미를 알지 못한다.
2. 점수 boost는 family 경계를 넘어 후보를 이동시킬 수 있다.
3. 후처리 순열은 “각 family가 차지한 전역 슬롯은 그대로”라는 회귀 불변식을 직접
   보장할 수 있다.

exact lookup은 계속 RRF보다 먼저 반환하고, fallback은 그대로 둔다. `FusedResult.score`,
`match_type`, `contributing_arms`도 수정하지 않는다. 순위만 endpoint 전용 단계에서 바꾼다.

### 4.2 operation intent 추출

입력은 accepted label이나 endpoint 설명이 아니라 원문 query와 제공된 nonblank variants뿐이다.
별도 LLM 호출·모델 추론·DB 쓰기는 없다.

내부 enum은 다음 다섯 값이면 충분하다.

| intent | 대표 결정 토큰/구 | 기대 method | 기대 target shape |
|---|---|---|---|
| `LIST` | list, all, 목록, 리스트, 전체 | GET | collection |
| `CREATE` | create, add, register, new, 생성, 만들, 등록, 추가 | POST | collection |
| `DELETE` | delete, remove, cancel, shut down, terminate, 삭제, 제거, 취소, 해지, 종료 | DELETE | item |
| `GET_ONE` | get, retrieve, fetch, details, information, 상세, 정보 | GET | item |
| `NONE` | 명시 신호 없음 또는 서로 다른 intent가 둘 이상 | 없음 | 없음 |

구현 규칙:

- 영어는 소문자 단어/구, 한글은 현재 tokenizer와 같은 한글 덩어리 및 접두 일치로
  정규화한다. `shut down`처럼 두 단어 구를 단어보다 먼저 본다.
- 원문과 variants에서 같은 intent만 나오면 그 intent다.
- q17처럼 list와 create가 함께 나오거나 서로 충돌하면 `NONE`으로 전체 rerank를
  생략한다. 다의도 질의를 한 endpoint 의도로 축약하지 않는다.
- q05의 `refund`, q12의 bare `pull request`처럼 CRUD 의미가 확정되지 않는 도메인
  명사는 `NONE`이다. 평가 질의 하나를 맞히기 위한 도메인별 HTTP method 사전은 넣지 않는다.
- lexicon은 module 상수로 고정하고 env/config tuning surface로 노출하지 않는다.

### 4.3 route family와 path specificity

진단 러너의 “앞 두 세그먼트” helper를 운영 코드로 복사하지 않는다. 그 규칙은
`/repos/{owner}`처럼 지나치게 거친 표시용 근사이고 OpenAPI별 base path 깊이도 다르다.

wide 후보의 실제 path 집합에서 **세그먼트 경계 prefix tree**를 만들고, 어떤 path의
family root는 후보 집합 안에 실제 endpoint로 존재하는 가장 짧은 prefix path로 잡는다.
예시는 다음과 같다.

- `/v1/customers` → `/v1/customers/{id}` → `/v1/customers/{id}/...`
- `/repos/{owner}/{repo}` → `/repos/{owner}/{repo}/issues` →
  `/repos/{owner}/{repo}/issues/{number}`

문자열 `startswith`가 아니라 path segment 배열로 비교한다. 같은 path의 HTTP method가
달라도 family는 같고, 서로 prefix 관계가 없는 path는 별도 family다.

각 후보에서 계산할 값은 다음으로 제한한다.

- `relative_depth`: family root 대비 추가 세그먼트 수
- `param_count`: `{...}` path parameter 수
- `terminal_is_param`: 마지막 세그먼트가 path parameter인지
- `leaf_resource`: item이면 마지막 연속 parameter 앞의 literal, collection이면 마지막
  literal. 하이픈·언더스코어 분리, 소문자화, 단순 복수형 제거
- `target_match`: query의 비-operation resource token과 `leaf_resource` 일치 여부.
  최소 alias는 `repo|repository|repositories → repo`처럼 형태 정규화만 허용하고,
  `billing history → invoices` 같은 의미 사전은 넣지 않는다.

path 길이 자체를 점수로 boost하지 않는다. `relative_depth`는 같은 family·같은 intent
안에서 target shape와 resource 정합을 비교하는 마지막 tie-break에만 쓴다.

### 4.4 family 내부 정렬 키

intent가 `NONE`이면 입력 순서를 그대로 반환한다. intent가 하나일 때 family별로 다음
호환성 tuple을 계산한다.

1. `method_match`: intent의 기대 HTTP method와 일치
2. `target_match`: 질의 resource가 후보의 leaf resource와 일치
3. `shape_match`: LIST/CREATE는 terminal literal, DELETE/GET_ONE은 terminal parameter
4. `specificity_match`:
   - target_match가 있으면 그 leaf를 가진 가장 얕은 후보 우선
   - target_match가 전혀 없는 family에서는 method+shape가 맞는 가장 얕은 후보 우선
5. 원래 RRF rank, ref_id — 완전 동점 결정성

정렬 적용 자체도 보수적으로 제한한다.

- family에 후보가 2개 미만이면 no-op한다.
- 같은 family 안에 `method_match && shape_match` 후보가 없으면 no-op한다.
- 명시적인 child resource token이 질의에 있는데 어느 후보 leaf에도 맞지 않으면
  잘못된 root 승급을 피하기 위해 no-op한다.
- 그 외에는 위 tuple 내림차순, 원래 rank 오름차순으로 stable sort한다.

그 뒤 family가 원래 차지하던 전역 index들에 정렬된 family 후보를 다시 꽂는다. 따라서
전체 결과의 index별 family key 배열은 rerank 전후 완전히 같아야 한다. 이는
“cross-family 순위 불변”의 구현 가능한 정의다.

이 규칙의 의도된 결과:

- `delete a repository` / `shut down a repository`: DELETE + item + repo leaf가 맞는
  `/repos/{owner}/{repo}`를 같은 family의 더 깊은 child보다 우선
- `delete a subscription`: DELETE + item + subscription leaf를 보존 또는 개선
- `list commits of a repo`: LIST + collection + commits leaf가 맞으므로 repo root로
  끌어올리지 않고 `/commits` collection을 보존
- bare `pull request`: intent `NONE`, 순위 불변
- `list ... and create ...`: 다의도이므로 순위 불변

### 4.5 코드 배치

path parsing·intent extraction·constrained permutation은 순수 함수가 많고
`endpoint_candidate_search.py`가 이미 큰 편이므로
`app/services/search/endpoint_route_reranker.py`의 module-private 성격으로 분리한다.
외부 서비스·repository·설정 의존성을 넣지 않는다. public MCP DTO나 옵션은 추가하지 않는다.

`EndpointCandidateSearch`에는 아래만 남긴다.

- B의 endpoint 전용 keyword rank 병합 helper
- wide RRF hydrate 호출
- reranker 호출과 최종 `top_k` 절단

generic `rrf.py`, `KeywordSearch`, repository SQL, MCP schema는 변경하지 않는다.

## 5. 회귀 가드와 검증

### 5.1 단위 테스트

새 `tests/unit/test_endpoint_route_reranker.py`에서 최소 다음을 고정한다.

1. CREATE collection root가 같은 family child보다 앞선다.
2. DELETE item root가 더 깊은 child보다 앞선다.
3. `list commits of a repo`는 commits collection을 repo root보다 앞에 둔다.
4. operation 없는 bare noun은 완전 no-op한다.
5. list+create 다의도 질의는 완전 no-op한다.
6. 명시 child resource가 맞지 않으면 root를 추측 승급하지 않는다.
7. rerank 전후 index별 family 배열이 동일하다.
8. tie는 원래 RRF rank와 ref_id로 결정적이다.

기존 `tests/unit/test_endpoint_candidate_search.py`에는 다음을 추가·수정한다.

- variant가 keyword 독립 검색에서 얻은 높은 rank를 실제 RRF에 기여시킴
- variants 없음은 keyword 호출 1회·기존 순위 동일
- blank/duplicate variants는 추가 호출하지 않음
- wide 후보의 family member가 최종 top_k로 올라올 수 있음
- exact와 fallback 결과는 변경되지 않음

`test_keyword_search_query_variants_widen_filter_but_not_score`는 그대로 둔다. 공용
`KeywordSearch` 계약을 바꾸지 않았다는 가드다. `test_rrf.py`도 공식 변경이 없으므로
기존 테스트를 모두 통과해야 한다.

### 5.2 실 코퍼스 checkpoint

새 러너를 만들지 않는다. 각 checkpoint에서 아래 기존 명령을 재사용한다.

```bash
uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --strategy rrf
uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --strategy rrf --with-variants
uv run python tests/fixtures/corpus_eval/diagnose_variants.py --top-k 10 --wide 50
```

측정 순서:

1. B만 적용: q04·q06 보존, q05·q07의 variants on wide/top-10 변화 기록
2. A+B 적용: q05·q07·q08·q09·q12와 C5/C6 control의 전후 순위 기록

20건은 방향성 게이트이므로 다음을 통과 조건으로 한다.

- q04 variants on 1위, q06 variants on top-3를 악화시키지 않는다.
- q13(`delete a subscription`)과 q15(`list commits of a repo`)의 기존 순위를
  악화시키지 않는다.
- q12와 q17은 A 때문에 순위가 바뀌지 않는다.
- family-rerank 후보 q05·q07·q08·q09 중 적어도 2건이 top-10에 진입하고,
  나머지도 wide 순위가 악화되지 않는다.
- variants off/on 각각 전체 Recall@10·MRR이 현 기준선보다 낮아지지 않는다.
- latency p50/p95를 함께 기록한다. variants 수만큼 keyword SQL이 늘어나는 것은
  허용하되 query별/후보별 N+1은 금지한다.

이 조건은 최종 실무 승급이 아니다. 통과한 수정 후보만 67번 §2의 100~150건 프리즈
게이트로 올린다. 실패하면 점수 상수나 lexicon을 20건에 맞춰 추가 튜닝하지 않고,
B와 A 중 어느 checkpoint에서 실패했는지 분리해 판정을 다시 요청한다.

## 6. C — q10·q11 및 컬렉션 루트 청크 진단

q10 `billing history → invoices`는 top-50에 invoice 후보가 없는 순수 어휘 갭이고,
q11 `customer`는 bare noun + 단일 binary 정답 문제다. 둘 다 wide 후보를 재배열하는
A의 대상이 아니다.

이번 라운드에서 하지 않는 일:

- `billing`, `history`, `customer`를 코드 synonym 사전에 하드코딩
- q10·q11 accepted 완화
- `chunk_builder.py` 변경이나 재색인

진단 코멘트는 남긴다. 현재 endpoint chunk의 선두에는 `[METHOD] PATH — SUMMARY`가 있고
OperationId·Params·Body·Tags·description이 이어지지만, 컬렉션 루트를 “resource
collection / list / create entry point”로 명시하는 구조 표지는 없다. child endpoint가
더 많은 path·summary·description 토큰을 가져 vector와 keyword 양쪽에서 루트보다 강해질
수 있다.

다만 이를 고치려면 청크 포맷 변경, 전량 재색인, 기존 endpoint 검색 회귀 측정이 필요해
ADR-0003 read-only 검색 경계를 넘는다. 66번의 `list invoices` lexical control과
root/child 쌍이 100~150건 확장셋에 들어간 뒤 별도 색인 실험으로 판정한다.

## 7. developer 구현 순서

1. B의 endpoint RRF 전용 keyword variants 독립-rank 병합과 단위 테스트
2. 기존 하네스로 B checkpoint 기록
3. 순수 `endpoint_route_reranker` 구현과 단위 테스트
4. `_search_rrf`를 wide fuse → hydrate → constrained permutation → top-k로 배선
5. 전체 관련 단위 테스트 + 기존 두 하네스로 A+B 측정
6. 변경 파일·명령·질의별 전후 순위·headline·latency를 lead에게 보고

구현 중 영구 feature flag, 새 평가 러너, DB migration, 청크 재색인은 추가하지 않는다.
