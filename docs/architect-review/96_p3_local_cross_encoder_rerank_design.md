# 96. P3 local cross-encoder rerank 상세 설계

- 선행 근거: `docs/architect-review/92_corpus_eval_search_logic_improvement_review.md` §7,
  `docs/architect-review/93_p2_arm_rescue_effectiveness_verdict.md`,
  `docs/architect-review/95_p1_vector_reformulation_rejection_verdict.md`,
  `docs/eval-results/10_2026-08-31_p0_arm_trace_reclassification.md`
- 기준선: HEAD의 exact prelude + original-query keyword/vector wide RRF + fallback.
  P2 quota는 `0`, P1은 반려·제거 상태로 고정한다.
- 상태: **설계 승인. 구현, 모델 반입, fixture 저작, 평가 실행은 별도 승인 대상.**

## 설계 요약 — 구현 전에 보고할 효과 상한

P3는 RRF가 이미 만든 상위 50개를 재정렬할 뿐, 후보를 생성하거나 주입하지 않는다. 더구나
P1 q10의 재발을 막기 위해 baseline 최종 반환 안의 `both` 후보는 **원래 slot에 HARD lock**한다.
따라서 P0 final-cut 8건 중 다음 네 건만 P3 단독의 final top-10 회복 후보이고, 나머지 네 건은
모델이 정답의 의미를 알아도 lock을 지키는 한 절대로 회복할 수 없다.

| P3 단독 회복 가능성 | 질의 | 이유 |
| --- | --- | --- |
| 가능 후보 | q05, q06, q17, q18 | keyword arm이 비어 있고 정답이 base-wide 12~42위에 있다. lock 없는 반환 slot을 query–endpoint 점수로 재배열할 수 있다. |
| **불가 (HARD lock)** | q08, q09, q11, q12 | baseline final top-10이 모두 `both`이고 정답은 vector-exclusive다. `both` 10개 slot을 하나도 바꾸지 않는 계약에서는 넣을 빈 slot이 없다. |

즉 P3는 P2/P1과 같은 **효과 캡이 있는 단독 candidate**다. q08/q09/q11/q12와
generation-miss q04/q07/q10의 Recall@10에는 P3만으로 변화가 없으며, 위 cap을 lead가 구현
착수 전에 사용자에게 먼저 보고한다. P3의 주 목표는 lock 밖의 final-cut 및 이미 top-10인
shallow-rank(q13/q15/q19/q20)의 Recall@1·MRR·nDCG 개선이다.

## 1. 범위와 비목표

P3는 query와 endpoint를 함께 넣는 local cross-encoder의 learned relevance score로 **기존 RRF
후보 집합 안에서만** 순서를 바꾸는 후보다. RRF arm, score 식, arm weight, candidate 생성 폭,
exact prelude, fallback에는 관여하지 않는다.

- P1 `vector_reformulations` 입력·best-rank 병합과 결합하지 않는다. P3 요청은 original query만
  사용한다.
- P2 arm-rescue는 `DOCS_MCP_SEARCH_ARM_RESCUE_QUOTA=0`으로 고정하며, tail-slot replacement를
  재사용하지 않는다.
- `search_tsv`, 구조화 lexical augmentation, text-primary, path/family boost, 서버 alias/번역,
  query expansion, 후보 injection은 범위 밖이다.
- client LLM은 score·후보 id·순서를 전달하지 않는다. 이 경계는 P1의 “client가 질의 표현을
  제공” 원칙과 충돌하지 않는다. P3는 서버 안에서 재현 가능하게 실행하는 판별 모델이며, 서버가
  별도 LLM API를 호출하는 구조가 아니다.

## 2. 실행 위치와 데이터 흐름

### 2.1 고정된 입력·출력 관계

`top_k`를 API가 허용하는 반환 수, exact prelude가 점유한 수를 `E`, RRF가 채울 반환 수를
`K = max(top_k - E, 0)`로 둔다. 기존 검색이 만든 wide pool을 `base_wide`라 하면 P3의 입력은
다음으로 고정한다.

```text
exact_prelude (그대로) ───────────────────────────────┐
original query ── keyword arm ─┐                      │
                                ├─ reciprocal_rank_fuse ─ base_wide[:50]
original query ── vector arm ──┘                         │
                                                          ├─ P3 local rerank + both lock
                                                          └─ first K RRF-return slots + exact_prelude
fallback ───────────────────────────────────────────────── baseline 그대로, P3 미실행
```

- rerank 폭 `N`은 **항상 `min(50, len(base_wide))`**이다. production `top_k=10`의 기존
  wide 폭 50과 같은 좌표이며, `top_k`가 더 작아도 50개를 비교한다. `top_k=50`도 같은 첫
  50개만 재정렬하므로 후보 범위는 넓어지지 않는다.
- 후보 id·`match_type`·원 RRF rank는 `base_wide`에서 읽기만 한다. P3 앞의 pool은 P0 trace와
  baseline 사이 byte-for-byte 동일해야 한다.
- P3의 출력은 후보를 추가/삭제하지 않은 `base_wide[:N]`의 순열 중 앞 `K`개다. exact prelude는
  모델 점수 계산·교체 대상 모두에서 제외한다. fallback 진입 조건과 fallback 결과도 읽거나 바꾸지
  않는다.
- RRF 결과가 50개보다 적으면 있는 수만 점수화한다. 모델 오류·모델 asset 부재는 요청 중 원격
  다운로드를 시도하지 않고 P3를 fail-closed하여 baseline 순서를 반환한다. 해당 호출은 관측 로그로
  구분하며, 이 상태는 승급 평가에서 PASS가 될 수 없다.

### 2.2 endpoint pair 표현과 결정성

각 후보에는 query 원문과 아래 고정 순서의 `rerank_document`를 한 pair로 준다.

```text
METHOD <path>
summary: <summary>
operation_id: <operation id>
description: <description>
parameters: <name[: short description] ...>
request_body_fields: <property name ...>
response_fields: <property name ...>
```

존재하는 현재 endpoint/chunk 필드만 사용하며, 누락 필드는 빈 행을 만들지 않고 생략한다. 새
의미어, endpoint별 상수, 필드 가중치, 질의별 template 분기는 만들지 않는다. route/summary/field
name을 앞에 둬 q18 같은 parameter-detail이 긴 설명의 뒤에서 조용히 잘리지 않게 하되, 이는 모든
후보에 같은 직렬화 순서를 적용하는 입력 계약이지 structured augmentation이 아니다.

토크나이저·입력 포맷 버전은 candidate identity다. query는 최대 64 tokenizer tokens로 제한하고,
pair 전체는 `max_length=512`, `truncation=only_second`로 고정한다. 50 pair는 한 batch(메모리
불가 시 고정된 sub-batch 크기)에서 inference mode로 점수화한다. 동점은 원 `base_wide` rank
오름차순, 그 다음 안정 endpoint id 오름차순으로만 푼다. 부동소수 score를 임의 반올림하여 tie를
만들지 않는다.

## 3. both-arm subset HARD slot lock

P1 q10은 reformulated vector score가 올라가면서 baseline final의 `both` 두 ref가 top-10에서
사라진 HARD FAIL이었다. P3는 subset만 사후 비교하는 약한 guard가 아니라 다음 slot lock을
출력 알고리즘에 넣는다.

1. rerank 전 `base_final_rrf = base_wide[:K]`를 snapshot 한다.
2. 이 안에서 `match_type == "both"`인 각 ref의 **0-based RRF-return slot**을 `locked_slots`로
   기록한다.
3. top `N`의 모든 query–document pair를 score하되, locked ref는 점수와 무관하게 그 snapshot
   slot에 그대로 둔다.
4. `base_wide[:N]`에서 locked ref를 제외한 후보만 score 내림차순(동점 규칙은 §2.2)으로 정렬해,
   비어 있는 RRF-return slot을 앞에서부터 채운다.
5. exact prelude를 앞에 붙여 `top_k`를 자른다.

따라서 baseline final `both` ref의 **id·존재·slot·상대 순서가 모두 불변**이다. P3 score가 높아도
locked slot을 탈취할 수 없고, base-final이 전부 `both`인 q08/q09/q11/q12는 결과가 baseline과
동일하다. 이 보호를 완화하거나 locked ref를 tail로 모으는 방식은 P1/P2의 결과-후 정책 변경을
되풀이하므로 금지한다.

## 4. 실행 위치의 선택 — local 권고와 배제 대안

| 방식 | 비용·지연·의존성 | 판정 |
| --- | --- | --- |
| **local cross-encoder (권고)** | 모델 asset을 배포 이미지/로컬 cache에 사전 반입한다. 요청당 외부 호출·키·egress 비용은 없고, CPU batch inference의 지연·RSS만 추가된다. | `LocalEmbeddingProvider`와 동형의 provider protocol + composition 배선으로 채택. |
| client LLM이 score 반환 | 검색 server가 후보 50개를 먼저 client에 노출한 뒤 다시 score를 받아야 하는 두 단계 MCP 왕복이다. client/model 변경마다 비결정적이며 timeout·조작·관측 불가능성에 의존한다. | 반려. P1의 reformulation 위임과 달리 단일 search response의 server-side ordering 계약을 성립시키지 못한다. |
| 원격 reranker/LLM API | 요청마다 API 비용·비밀키·네트워크 지연·endpoint text egress가 생기고 provider 장애가 검색 순위에 직접 들어온다. | 반려. “서버 별도 LLM API 호출 금지” 및 local provider 경계를 위반한다. |

### 4.1 권고 model과 후보 비교

초기 구현 후보는 **`BAAI/bge-reranker-v2-m3` commit
`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`**로 pin한다. 이름만 또는 `main` tag를 설정에
두지 않고 model repo revision, tokenizer revision, 파일 SHA-256 manifest를 image/build artifact에
함께 동결한다. 모델 card는 이 모델을 multilingual reranker, Apache-2.0, 약 0.6B parameter로
표기하며 query–passage relevance score를 직접 출력하는 사용법을 제공한다.

| 후보 | 크기 | 라이선스·다국어 지원 | 의존성·판정 |
| --- | ---: | --- | --- |
| **BAAI/bge-reranker-v2-m3** (권고 pin) | 약 0.6B params | Apache-2.0, multilingual | 현 `sentence-transformers`의 전이 의존성인 `transformers`/`torch`로 local sequence-classification 실행. 새 PyPI runtime 의존성은 추가하지 않는다. 가장 큰 CPU/RSS·p95 위험은 §6 gate로 판정한다. [model card](https://huggingface.co/BAAI/bge-reranker-v2-m3) |
| Alibaba-NLP/gte-multilingual-reranker-base | 306M params | Apache-2.0, 75개 언어(카드상 70+ 지원) | 자원 대안이지만 `trust_remote_code=True`를 요구한다. source review·hash pin·보안 승인 전에는 도입하지 않는다. [model card](https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base) |
| jinaai/jina-reranker-v2-base-multilingual | card에 parameter 수 미표기 | CC-BY-NC-4.0, multilingual | commercial/release 경계와 `trust_remote_code`가 맞지 않아 후보에서 제외한다. [model card](https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual) |

모델 card의 multilingual 표기는 한국어 API query 품질을 보장하지 않는다. q05/q06/q17/q18과 새
sealed split의 Korean cases가 반드시 실제 gate를 통과해야 한다. 이 표의 model 교체는 phrase나
fixture를 본 뒤 고르는 재튜닝이 될 수 있으므로, **candidate 간 비교는 legacy diagnostic 한 번으로
끝내고 하나의 model/revision/input spec을 sealed split 개봉 전에 freeze**한다.

### 4.2 배선과 설정 경계

새 `CrossEncoderReranker` protocol은 `score_pairs(query, documents) -> list[float]`만 노출한다.
`LocalCrossEncoderReranker`는 `LocalEmbeddingProvider`처럼 composition에서 주입 가능하고 테스트
fake를 허용하지만, embedding provider를 재사용하거나 embedding score를 바꾸지 않는다.

- `DOCS_MCP_SEARCH_CROSS_ENCODER_ENABLED=false`가 기본이다. `true`일 때만 composition이
  pinned local asset을 load한다.
- model id·revision·token budget·batch size·device(CPU)·document format version은 설정/trace에
  기록하고, 운영 중 임의 변경하지 않는다.
- asset은 배포 전 cache/image에 존재해야 한다. startup과 request 중 network fetch는 금지한다.
  의도적으로 local embedding backend가 hash여도 P3는 local reranker asset을 별도로 명시적으로
  준비해야 하며, 없으면 feature-on 실험은 fail-closed baseline이다.
- flag가 false면 reranker를 생성·load·score·log하지 않는다. 그때 exact/RRF/fallback 결과와 순서는
  baseline과 **byte-identical**이어야 한다.

## 5. final-cut 8건 대조

| query | P0 관측 | P3 판정 | 왜 회복 가능/불가인가 |
| --- | --- | --- | --- |
| q05 `결제 환불 처리해줘` → `POST /v1/refunds` | keyword 없음, vec/base-wide 35 | 가능 후보 | 50 안에 있으므로 Korean action·resource와 refund endpoint를 joint score로 sibling decoy보다 올릴 수 있다. 모델의 cross-language 품질 실패 시 그대로 miss다. |
| q06 `이슈 새로 만들기` → `POST /repos/{owner}/{repo}/issues` | keyword 없음, 16 | 가능 후보 | `create`와 `POST`/root `issues` 관계를 long child/issue-field decoy와 직접 비교할 수 있다. 후보 생성은 이미 됐으므로 lock 밖 순위 문제다. |
| q17 `이슈 목록 조회 + 새 이슈 생성` → GET+POST issues | keyword 없음, 12/25 | 가능 후보 (all-of 별도 guard) | 두 endpoint 모두 50 안이다. 각 pair가 list/create clause에 맞으면 두 비locked slot에 올라갈 수 있으나, independent score는 두 답의 coverage를 보장하지 않는다. C6 pair completeness가 필요하다. |
| q18 `결제 생성 시 통화 단위 지정` → `POST /v1/charges` | keyword 없음, 42 | 가능 후보 | action/resource와 `currency` parameter name이 고정 passage 앞부분에 있어야 score가 상승할 여지가 있다. 해당 field가 source chunk에 없거나 Korean relevance가 약하면 P3로는 못 고친다. |
| q08 cancel recurring payment → DELETE subscription | keyword 50, vec 15, final top-10 all `both` | **불가** | semantic score는 cancellation/deletion을 구별할 수 있어도 final 10 slot이 모두 locked다. vector-exclusive 정답을 넣을 공간이 없다. |
| q09 shut down repository → DELETE repo | keyword 50, vec 10, final all `both` | **불가** | 의미상 root repository delete를 sibling과 비교할 수 있어도 same lock으로 출력은 baseline 불변이다. |
| q11 customer → GET customers | keyword 50, vec 8, final all `both` | **불가** | root/child 구분은 model의 잠재 장점이나 protected both slots를 축출하면 P1 q10 HARD failure를 재현한다. |
| q12 pull request → GET pulls | keyword 50, vec 4, final all `both` | **불가** | 정답 vector rank가 높아도 RRF both saturation 문제다. P3가 candidate injection/slot replacement를 하지 않는 이상 회복 불가다. |

이 구분은 “8건 final-cut 모두 P3 target”이라는 느슨한 해석을 금지한다. q08/q09/q11/q12는
P2가 보류된 범주의 재개가 아니라, 별도 retrieval architecture가 필요하다는 증거로만 기록한다.

## 6. 측정·승급 gate

### 6.1 사전 parity와 legacy diagnostic

`09`/`10`의 production-wide trace를 기준선으로 고정한다. flag off와 asset 부재 fail-closed 결과는
baseline에 byte-identical이어야 하며, flag on의 P3 전 `base_wide[:50]` id/rank/match_type은
기준선과 완전히 같아야 한다. trace에는 model revision, asset manifest digest, format version,
N, K, locked slot/id, raw score, final rank를 남긴다.

| gate | 성공 조건 | 성격 |
| --- | --- | --- |
| candidate parity | rerank 전 first-50 및 exact/fallback이 baseline 동일 | HARD |
| both-arm subset/slot | baseline final `both`의 id·slot·순서 전부 보존; q08/q09/q11/q12는 baseline final byte-identical | **HARD** |
| C1 | direct keyword C1 gross hit loss 0 | HARD |
| route-pair | 모든 root/child pair non-regression | HARD |
| C6 | aggregate non-regression 및 q17의 두 정답 coverage를 별도 기록 | HARD (aggregate), diagnostic (q17) |
| deterministic | 같은 revision/asset/device에서 3회 final id/rank 완전 동일 | HARD |
| recall attribution | Recall@10 변화가 있으면 lock 밖 후보만의 순열 효과인지 명시; P3가 candidate generation 개선으로 표기되지 않음 | HARD 기록 |

P3는 permutation이므로 `base_wide[:50]` 바깥의 miss를 회복할 수 없다. non-locked top-k slot에
후보를 올리는 경우만 Recall@10이 변할 수 있고, locked-only 질의에는 변화가 없다는 것을 query별
trace로 증명한다.

### 6.2 효과 및 지연 gate

legacy exposed set은 model 후보를 고르는 diagnostic일 뿐 승급 판정에 쓰지 않는다. one-time
legacy 결과를 보고 model/revision/input format/N/lock을 freeze한 뒤, 새로 저작·미개봉한 sealed
split(최소 96 query + holdout 24 query, C1/C6·Korean·paraphrase·root/child를 층화)에서만 아래를
판정한다. sealed 정답/문구를 본 뒤 후보·token cap·lock을 다시 조절하지 않는다.

| 측정 | sealed split PASS 기준 |
| --- | --- |
| ranking 효과 | baseline 대비 Recall@1 **+5 percentage points 이상**, MRR **+0.030 이상**, nDCG@10 **+0.030 이상**. Recall@10은 하락 0. |
| final-cut target | lock 밖 q05/q06/q17/q18류 strata에서 final top-10 accepted hit 순증이 있어야 한다. q17은 2-answer coverage를 따로 보고한다. |
| 회귀 | §6.1 모든 HARD PASS; `regressed_accepted=[]`; exact prelude/fallback parity PASS. |
| warm latency | P3 flag-on end-to-end warm **p50 ≤ 200 ms, p95 ≤ 500 ms**, 그리고 baseline 대비 rerank 추가 p95 **≤ 250 ms**. 50-pair batch 기준이다. |
| cold/resource | cold p95, model load time, peak RSS, model asset/package size를 기록하고, warm 상한을 넘거나 메모리 pressure/timeout이 있으면 FAIL. |

정량 ranking 조건만 통과하고 final-cut target 순증이 없으면, shallow-rank만의 효과와 이 문서 맨 앞
effect cap을 분리해 lead가 재판정을 요청해야 한다. HARD 하나라도 실패하면 sealed 결과가 좋아도
승급하지 않는다.

## 7. 리스크와 롤백

| 리스크 | 통제/판정 |
| --- | --- |
| 0.6B CPU model의 지연·RSS·cold load | pre-bundled asset, batch inference, §6 p50/p95/RSS 기록. warm latency gate FAIL이면 default-on 승급 불가. |
| Korean query–English endpoint relevance가 충분하지 않음 | q05/q06/q17/q18 strata와 sealed Korean strata로 검증한다. 외부 번역·phrase 상수로 보정하지 않는다. |
| 긴 endpoint의 parameter 정보 truncation | format v1에서 method/path/summary/field name을 앞에 고정하고 tokenizer-level 512 cap을 trace한다. 결과 뒤 field order나 cap을 재튜닝하지 않는다. |
| learned reranker가 direct/root-child 결과를 바꿈 | exact prelude 제외, C1/route-pair HARD, both slot lock으로 차단한다. |
| model revision drift·silent network download | revision + file digest pin, offline asset only. `main`/runtime download 금지. |
| model/asset runtime failure | feature-on 요청도 baseline 순서 fail-closed; 운영 알림. 평가에서는 failure를 PASS로 취급하지 않는다. |

롤백은 `DOCS_MCP_SEARCH_CROSS_ENCODER_ENABLED=false` 하나다. flag off에서는 provider construction,
model load, score/trace와 P3 postprocess가 전부 발생하지 않아 기존 exact/RRF/fallback 호출 결과가
byte-identical이다. P3 code는 flag-off 검증을 통과하기 전에는 커밋/승급하지 않는다.

## 8. 구현 전 handoff 조건

developer는 이 문서를 code 변경 지시로 해석하지 않는다. 구현을 시작하려면 lead가 다음을 별도로
승인해야 한다.

1. `BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` asset의 licence/digest를
   build manifest에 고정할 것.
2. §3 slot lock을 unit/property test로 먼저 증명할 것(locked ref가 하나라도 이동하면 HARD FAIL).
3. P1 code/input 미포함, P2 quota=0, flag-off byte-identical fixture를 만들 것.
4. legacy diagnostic을 한 번만 실행하고 candidate identity를 freeze한 뒤 sealed split을 생성·평가할 것.

