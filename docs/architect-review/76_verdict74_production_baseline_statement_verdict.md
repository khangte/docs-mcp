# 76. verdict 74 production 기준 기술 정정 판정

- 대상: verdict 74 §4 "현재 production 기준은 `ecc3e792`의 검색 동작이다"
- 제기: developer 검색품질 eval 부수 지적 (main HEAD와 불일치)
- 상태: **정정 필요. 단 74의 판정 내용은 불변**

## 1. 사실 확인

developer 지적의 전제는 절반만 맞다.

`git diff --stat ecc3e79 HEAD -- app/` 결과는 다음 한 파일이다.

```text
app/services/search/endpoint_candidate_search.py | 53 ++++++++++++++++++++----
1 file changed, 46 insertions(+), 7 deletions(-)
```

- **component A는 HEAD에 없다.** `8b4e36a`(route-family constrained rerank)와
  `608731b`(deepest matched leaf)는 커밋 이력에는 남아 있지만, 그 위의
  `75fa5f3`가 `app/services/search/endpoint_route_reranker.py`(284줄)와 해당
  테스트를 삭제하고 wide-hydrate fusion 경로를 baseline으로 되돌렸다. HEAD 작업
  트리에 `endpoint_route_reranker.py`는 존재하지 않는다.
- **component B는 HEAD에 있다.** 위 46줄 추가분이 `75fa5f3`의
  `_search_keyword_with_variants` — verdict 72가 승급을 반려한 바로 그 후보다.

즉 "3커밋이 얹혀 있어 불일치"가 아니라, **한 커밋(`75fa5f3`)이 얹혀 있어
불일치**다.

## 2. 74 §4 기술은 틀렸는가

틀렸다. 단 "시점 스냅샷이라 낡았다"가 아니라 **작성 시점에 이미 사실과 달랐다**.

`75fa5f3`는 verdict 72(`17686f7`)보다 먼저 main에 올라갔고, 반려 판정 이후에도
revert가 없었다. 따라서 74를 쓸 때 main HEAD의 검색 동작은 이미
`ecc3e792 + component B`였다.

`75fa5f3` 커밋 메시지는 "variant가 없으면 keyword SQL은 정확히 한 번 실행되고
OFF 경로는 `ecc3e792`와 byte-identical"이라고 기록한다. 이는 맞지만 면책이
아니다. `query_variants`는 `app/mcp/tools/endpoints.py:38`,
`app/mcp/tools/documents.py:120`의 공개 파라미터이고, docstring이 클라이언트
LLM에게 변형 표현을 담아 재호출하라고 지시한다. 즉 실사용 경로에서 variants는
채워지며, 그때 HEAD 랭킹은 `ecc3e792`와 다르다.

**판단**: 스냅샷 면책은 "그 시점 증거로 내린 판정"에 적용되지 스펙 밖 사실
오기에는 적용되지 않는다. 74의 반려·트랙 종료 판정은 p02 게이트 결과에 근거하며
그대로 유효하다. 정정 대상은 §4의 저장소 상태 서술 한 문단뿐이다.

## 3. 정정 방식

74의 판정 문장(§4 승급 반려 목록, §8 판정표)은 **고치지 않는다**. §4 말미의
production 기준 서술에 날짜를 명시한 정정 문단을 덧붙인다. 원문을 조용히
바꿔치기하지 않는다 — 75번 이관 때와 같이 보강 형태로 남긴다.

정정 문구 취지:

> (2026-08-28 정정) 이 문단은 저장소 상태를 잘못 기술했다. `75fa5f3`는 verdict 72
> 반려 이후에도 revert되지 않아 main HEAD에 남아 있다. 따라서 HEAD의 실제 검색
> 동작은 `ecc3e792 + keyword-variant symmetrization`이며, `query_variants`가 빈
> 경우에만 `ecc3e792`와 동일하다. component A(`8b4e36a`, `608731b`)는 `75fa5f3`가
> reranker 모듈을 삭제하며 되돌렸으므로 HEAD에 없다. 승급 반려 판정 자체는 불변이다.

## 4. 더 큰 미결 항목 (lead 결정 필요)

문서 정정보다 중요한 것은 **코드와 판정의 불일치**다.

verdict 72는 `75fa5f3` 승급을 반려했고, verdict 74 §4는 "keyword variants를 full
ranking signal로 승격하는 변경만 중단한다"고 못 박았다. 그런데 그 변경이 main에
살아 있다. 선택지는 둘뿐이다.

1. **`75fa5f3` revert** — 72·74 판정과 트리를 일치시킨다. 판정에 부합하는 선택.
2. **B를 정식 채택으로 재분류** — 72·74를 뒤집는 새 판정이 필요하며, sealed
   holdout p02 FAIL을 감수한다는 명시적 근거가 있어야 한다.

architect 권고는 **1번**이다. 74가 종료시킨 트랙의 코드를 근거 없이 트리에
남겨두면 이후 모든 baseline 측정의 기준점이 판정 문서와 어긋난다.

## 5. developer eval 결과에 대한 함의

`429302c` → HEAD 비교(OK 2→3, FAMILY-RERANK 5→4, CANDIDATE-GEN 실패 2 불변)는
component B가 포함된 트리에서 측정됐다. q05 refund 회복분의 귀속을 B와 분리하지
않으면, 반려된 후보의 효과를 baseline 개선으로 오독할 수 있다.

- `FAMILY-RERANK`/`CANDIDATE-GEN`은 doc 03의 실패 분류 라벨이지 기능 이름이
  아니다. component A가 HEAD에 없으므로 `FAMILY-RERANK 5→4`를 A의 효과로 읽어선
  안 된다.
- `75fa5f3` revert가 결정되면 이 eval은 revert 후 재측정해야 baseline으로 쓸 수
  있다.
- `CANDIDATE-GEN 실패 2건 불변`은 74 §5의 결론 — 필요한 정보가 search-time
  weight가 아니라 색인 표현에 없다 — 을 재확인한다.

## 6. 판정표

| 항목 | 판정 |
|---|---|
| 74 §4 production 기준 서술 | **정정 필요 (사실 오기)** |
| 74 반려·트랙 종료 판정 | **불변** |
| 정정 방식 | 원문 유지 + 날짜 명시 정정 문단 추가 |
| "3커밋 불일치" 전제 | **부정확 — 실제는 `75fa5f3` 1건** |
| component A HEAD 잔존 | **없음 — `75fa5f3`가 reranker 삭제** |
| `75fa5f3` 처리 | **revert 권고, lead 결정 사항** |
| developer eval baseline | **revert 시 재측정 필요** |

## 7. revert 방법 판정 (2026-08-28 추가)

developer가 `git revert --no-commit 75fa5f3` 대신 ecc3e792 checkout을 적용한 이탈을
**승인한다**.

`75fa5f3`의 diff는 component B 추가와 component A 삭제를 함께 담는다. 이를 그대로
revert하면 A 삭제가 취소되어 `endpoint_route_reranker.py`가 되살아나고 워킹트리가
`608731b`(A+B) 상태가 된다. A는 verdict 71에서 효과성 미달로 반려됐으므로, 이는
반려 후보 하나를 지우려다 다른 반려 후보를 되살리는 결과다. 목표는 endpoint RRF를
`ecc3e792` fusion 경로로 되돌리는 것이지 `75fa5f3` 커밋의 역연산이 아니다.

검증 결과:

- `git diff ecc3e792 -- app/ tests/unit/` = 비어 있음. 두 트리가 동일하다.
- `app/services/search/endpoint_route_reranker.py`,
  `tests/unit/test_endpoint_route_reranker.py` 부재 유지 — A는 되살아나지 않았다.
- `git diff --stat HEAD -- app/ tests/` = 2파일 8+/166-.
- `tests/fixtures/` 는 되돌리지 않았다(`ecc3e792` 대비 3394+/73-). gate set v1,
  `run_corpus_eval.py --mode preflight/eval/determinism/cleanup`, manifest는 평가
  인프라이며 제품 검색 경로가 아니므로 보존이 맞다.
- `gate_manifest_v1.json` 의 `baseline_search_sha = ecc3e7923e216bf8e6b72ed609d5990749b2f700`
  가 이제 HEAD 검색 동작과 일치한다. `75fa5f3` 참조는 모두 historical result
  record이므로 정정 불필요.

커밋 시 유의:

1. 커밋 메시지에 `git revert`가 아니라 `ecc3e792` checkout으로 되돌렸음과 그 이유
   (A 부활 회피)를 남긴다. 이력만 보면 revert 커밋을 기대하게 된다.
2. `8b4e36a`·`608731b`는 이력에 남되 그 A 코드는 `75fa5f3` 이후 HEAD에 존재한 적이
   없다. 별도 조치 불요.
3. 이 커밋 이후 `docs/eval-results/05_2026-08-28_keyword_variant_p02_eval.md` §2 진단은
   반려된 B가 포함된 트리의 측정이 된다. baseline으로 쓰려면 재측정해야 한다(§5).
