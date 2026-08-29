# 81. structured lexical v2 sealed holdout 프리즈 판정

- 설계: `docs/architect-review/80_structured_lexical_v2_sealed_holdout_freeze_design.md`
- lead 승인: V2-D1~D9 전항 승인
- rules commit: `cef9214acc6ea8037d26a9e395a6ec44a2e34ef1`
- product source: `468ffafad253b112f59917b9b9c703786078be83`
- 상태: **architect F3/F6 감사 PASS — fixture commit 대기, v2 검색 결과 미개봉**

## 1. 판정

`queries_gate_v2.json`, `gate_manifest_v2.json`, runner의 v2 정적 검증과 단위 테스트를
감사했다. 80번 §3~§7의 신규성·분포·split·manifest 계약을 충족한다.

최초 의미 감사에서 `v2g095`가 Customer Session과 portal configuration 사이에 실제로
존재하지 않는 연결을 전제해 반려했다. 검색 실행 전에 이 레코드를 다음의 같은 리소스
생명주기로 교체했다.

- query: `register a webhook endpoint and read back the configuration it was created with`
- accepted 1: `POST /v1/webhook_endpoints` — webhook endpoint 생성
- accepted 2: `GET /v1/webhook_endpoints/{webhook_endpoint}` — 생성된 endpoint 재조회

두 operation은 Stripe 프리즈 corpus의 실제 endpoint이며 POST의 `url`, `enabled_events` 등
생성 구성을 GET 응답으로 다시 읽는 의도가 성립한다. Stripe/en/C6/gate quota는 바뀌지
않았고 두 accepted tuple은 v1 및 다른 v2 레코드와 겹치지 않는다.

**최종 판정: 프리즈 승인.** lead가 이 문서와 fixture 묶음을 커밋한 뒤 그 commit SHA를
프리즈 승인 기록에 결합한다. commit 전까지 gate96을 실행하지 않는다.

## 2. 의미 검토 범위

80번 §7 F3보다 넓게 다음을 검토했다.

| 대상 | 검토량 | 결과 |
|---|---:|---|
| root/child pair | 12쌍/24질의 전량 | PASS |
| C6 all-of | 12건 전량 | 1건 수정 후 PASS |
| diagnostic | 4건 전량 | PASS |
| 나머지 scored | 84건 중 category/domain/language 층화 24건(28.6%) | PASS |
| accepted 실재 | 136 tuple 전량 자동 검증 | PASS |

pair는 같은 domain/language/split에서 root path가 child path의 세그먼트 경계 prefix이고,
각 accepted는 정확히 1건이다. C6는 두 endpoint가 각각 query의 두 의도를 완전히 충족한다.
variant 표본은 원문의 operation·target·scope를 보존하며 새 method/path/리소스를 추가하지
않는다.

## 3. 분포·split 프리즈

| 축 | 프리즈 값 |
|---|---|
| 레코드 | total 124 / scored 120 / diagnostic 4 |
| split | gate 96 / holdout 24 |
| category | C1 12 / C2 24 / C3 18 / C4 12 / C5 24 / C6 12 / C7 18 |
| domain | Stripe 60 / GitHub 60 |
| language | ko 58 / en 58 / code 4 |
| domain별 language | 각 ko 29 / en 29 / code 2 |
| holdout domain | Stripe 12 / GitHub 12 |
| holdout language | ko 11 / en 11 / code 2 |
| pairs | `v2p01`~`v2p12`, 각 2건 |
| pair category | C2 2쌍 / C3 2쌍 / C5 8쌍 |
| pair split | gate 10쌍 / holdout 2쌍 |

holdout pair는 `v2p08`(Stripe/en)과 `v2p09`(GitHub/ko)다. gate pair는 domain 5/5,
language 5/5이며 holdout은 domain 1/1, language 1/1이다.

## 4. strict novelty 감사

runner의 `_validate_v2_novelty`와 독립 집계로 다음을 확인했다.

1. `v2g001`~`v2g124`, `v2p01`~`v2p12` 정확 집합
2. Unicode NFKC → trim → whitespace collapse → casefold 기준 query/variant가
   legacy·v1 및 v2 내부와 불일치
3. v2 accepted 136 tuple 전부 v1 accepted와 불교집합
4. v2 내부 accepted tuple 공유 0(C6 두 endpoint와 diagnostic 포함)
5. endpoint/query 조합 legacy·v1 재사용 0
6. v1 pair ID·accepted endpoint·route family 재사용 0
7. C6 endpoint 집합 재사용 0

## 5. 프리즈 SHA

아래 값은 architect가 fixture raw bytes와 80번 §5.4 split 직렬화에서 독립 재계산했다.

| 대상 | SHA-256 / git SHA |
|---|---|
| query raw `queries_gate_v2.json` | `a325583905a624c4e8293b7abff49e65741bc4aa6d0e09e48d5ed74bfa0346e5` |
| scored split mapping | `a53c1ab7eb7ce21b2afc4ea8cc0b28ae6809a236cc9d08061d5f42b5448b9a9a` |
| manifest raw `gate_manifest_v2.json` | `53023d663054520fdccfe3474a5d6ba18d55fa5394e1bb418248e6e6129b6865` |
| Stripe corpus raw | `3653ad45bbec54fcbe461c541c908355b715018bdf455a0e11b27bedb2cbdee5` |
| GitHub corpus raw | `80850db290cde4eb487e0efb587cf27f305e77b6bef96933ed8a09b5169d5b1d` |
| legacy query raw | `8f61cb99006e0d07923111fc919aaaa7489b486b0fffca15928efce75355441f` |
| v1 query raw | `6eb897d24d681d1389963007a184ded043d3ae914cf862f6ffd8aba7f75838d8` |
| rules git commit | `cef9214acc6ea8037d26a9e395a6ec44a2e34ef1` |
| product source git commit | `468ffafad253b112f59917b9b9c703786078be83` |

split SHA 직렬화는 scored 120건을 ID 오름차순으로 정렬한 UTF-8
`<id><TAB><split><LF>` 120줄이다. manifest의 `rules_git_sha="cef9214"`는 위 full commit의
lead 지정 short SHA다.

freeze commit SHA는 manifest나 이 문서에 자기참조로 넣지 않는다. lead가 commit 생성 뒤
`say` 승인 기록에 남겨 위 raw 지문 묶음과 결합한다(80번 §6).

## 6. runner·검증 판정

기존 runner 하나를 유지하면서 다음만 보강됐다.

- query basename별 v1/v2 manifest 명시 선택
- 등록되지 않은 gate schema query 파일 즉시 거부(v1 fallback 없음)
- 로더 단계에서 query raw SHA와 scored split SHA 재계산·manifest 대조
- v2 strict novelty 7개 축 검증
- v2 pair ID exact 집합과 pair당 2건 검증
- 기존 v1 loader 회귀 가드

정적 테스트 결과:

- `tests/unit/test_corpus_eval_v2_novelty.py`: **14 passed**
- `pytest -k 'corpus_eval or gate or manifest'`: **38 passed**
- `git diff --check`: PASS
- ruff: 이번 diff에서 신규 위반 0. runner 전체 검사에는 기존 E402/E501/F541 21건이 남아
  있으나 이번 추가 구간 밖이며 이 프리즈에서 기계적으로 정리하지 않는다.

두 번째 pytest slice는 저장소의 기존 DB 기반 테스트도 선택해 test DB teardown 시
`AdminShutdown` pool-reset 로그를 냈지만 38건은 모두 PASS했다. `run_corpus_eval.py`의
index/eval/search mode나 v2 query rank 실행은 호출하지 않았다. 따라서 v2 gate/holdout
검색 결과는 열리지 않았다.

## 7. 봉인 상태와 다음 단계

- query·accepted·variant·pair·split·quota와 80번 §8 기준은 이 판정 뒤 변경 금지다.
- lead freeze commit 전후에는 raw SHA 재검산만 허용한다.
- gate96 실행은 별도 lead 지시 전 금지한다.
- gate96은 80번 §8.1 HARD + EFFECTIVENESS 전항 PASS 전에는 holdout24를 열 수 없다.
- holdout은 lead-only one-shot이며 FAIL 뒤 같은 v2로 candidate나 기준을 조정하지 않는다.

현재 `structured`는 계속 dark이고 기본 `text`를 유지한다.
