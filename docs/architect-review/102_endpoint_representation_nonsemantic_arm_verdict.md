# 102. endpoint_repr non-semantic FTS-only 동작 판정

## 판정: 반려 — non-semantic provider에서는 arm 전체를 빈 list로 반환

`101` §3.2의 계약은 feature ON이어도 semantic embedding이 비가용이면
`endpoint_repr`가 빈 rank list여서 keyword/vector-only RRF로 돌아간다는 것이다.
현 구현 `endpoint_representation_search.py`의 FTS-only merge와 그 unit test 기대값은
이 계약에서 이탈했다. **v1은 strict empty를 사용한다.**

`embedding_provider.is_semantic == false`이면 `EndpointRepresentationSearch.search()`는
다음을 모두 만족해야 한다.

- canonical FTS lookup과 vector lookup을 모두 호출하지 않는다.
- `ordered_endpoint_ids`, `trace`, `fts_hit_ids`, `vector_hit_ids`는 모두 빈 list이고
  `dense_enabled`는 false다.
- outer RRF에는 빈 third-arm list만 전달된다. feature flag가 ON이어도 response, legacy
  keyword/vector rank, base-wide, final은 baseline과 같다.

따라서 FTS-only 결과를 기대하는 test는 strict-empty expectation으로 바꾸고, non-semantic
provider에서 projection lookup이 없음을 call-count로 검증해야 한다.

## 근거

canonical FTS는 target Korean query에서 대체로 비어 있고, 영문 query에서는 기존 keyword
arm과 다른 corpus·field를 다시 lexical RRF에 더하는 효과를 낸다. dense 없이 그것을 세 번째
arm으로 허용하면 “짧은 canonical endpoint semantic representation”을 검증하는 후보가 아니라
새 lexical weighting/field-primary 후보가 된다. 이는 `101`의 attribution, P0의
generation/keyword-blank 표적, 그리고 이미 반려한 structured lexical primary 계열과의
경계를 흐린다.

FTS 자체는 semantic provider가 있을 때 canonical vector와 endpoint best-rank로 합쳐지는
보조 신호로만 유지한다. FTS가 standalone fallback이 되거나 non-semantic 환경에서 product
degrade 경로를 바꾸는 것은 v1 범위 밖이다.

## 영향과 회귀 확인

이 수정은 flag OFF 경로에는 영향이 없고, non-semantic+flag ON을 baseline-equivalent로
만든다. semantic provider+flag ON 동작, width 50, tie rule, both-arm slot lock, P2=0/P3
배타의 설계는 바뀌지 않는다. 다음은 HARD regression test다.

1. non-semantic provider에서 projection text/vector repository call count가 모두 0이다.
2. non-semantic+flag ON의 keyword/vector lists, RRF/base-wide/final이 same configuration의
   flag OFF와 byte-identical이다.
3. semantic provider+flag ON에서는 기존처럼 FTS와 dense를 각각 width 50으로 조회해
   endpoint best-rank merge를 수행한다.

이것은 feature 의미와 degrade contract의 정정이며, exposed eval 결과를 보고 alias·rank·width를
조정하는 재튜닝이 아니다.
