# 101. Deterministic endpoint-level multi-representation candidate generator 설계

## 1. 결정 요약

`100`의 1순위 후보는 현 keyword/vector를 재가중하거나 질의를 서버에서 고쳐 쓰는
방식이 아니다. OpenAPI endpoint마다 **짧고 구조적인 canonical projection**을 하나 만들고,
그 projection을 검색하는 독립 `endpoint_repr` arm을 추가한다. 이 arm은 현 keyword arm과
vector arm의 입력·순위·`match_type`을 바꾸지 않는다.

이 설계가 직접 표적하는 것은 P0의 generation miss `q04/q07/q10` 및 keyword-blank
final-cut `q05/q06/q17/q18`, 총 7건이다. `q08/q09/q11/q12`의 both-arm saturation은
legacy both slot을 보존하는 HARD 제약 때문에 이 후보만으로는 움직이지 않는다. 그 4건은
후속의 별도 fusion/ranking 후보 범위다.

서버는 LLM API를 호출하거나 번역·동의어 확장·자연어 reformulation을 만들지 않는다.
여기서 말하는 “deterministic paraphrase”는 HTTP method/path와 OpenAPI 원문을 고정
template으로 재배열하는 것뿐이다. `billing -> invoice`, `저장소 -> repository` 같은
어휘 사전을 exposed eval 결과에 맞춰 추가하는 일은 포함하지 않는다.

## 2. 표현형과 색인

### 2.1 새 projection의 소유와 형태

문서마다 기존 `chunk`와 별도로 endpoint당 정확히 한 `endpoint_search_projection` 행을
둔다. physical identity는 `endpoint_id -> api_endpoint.id` foreign key (`ON DELETE CASCADE`)로
잡고, `(document_id, method, path)`에는 unique constraint를 둔다. 후자는 재색인 전후 같은
API operation을 식별하는 안정 natural key와 audit key이며, endpoint row를 delete/recreate하는
현 reindex에서는 cascade 삭제 뒤 새 endpoint와 함께 projection도 재생성한다.
`document_id`/project filter는 기존 endpoint search와 같이 적용한다.

행은 다음을 보유한다.

| 필드 | 원천 | 정규화/용도 |
| --- | --- | --- |
| `canonical_text` | 아래 2.2의 모든 결정적 표현 | local embedding과 FTS의 공통 입력 |
| `canonical_tsv` | `canonical_text` | simple FTS + GIN, 영문 직접어와 identifier lookup 보조 |
| `embedding` | `canonical_text` | 현 local embedding provider가 만드는 endpoint-level vector + HNSW |
| `representation_version`, `source_hash` | projection format과 원천 payload | 재색인/백필 판단 및 trace 재현 |

`EndpointBusinessMetadata`의 LLM 생성 문구·keywords·user phrases는 이 projection에 넣지
않는다. 이 후보의 source-of-truth는 업로드된 OpenAPI이고, metadata 유무가 candidate
availability를 바꾸지 않아야 한다.

### 2.2 canonical endpoint 표현형

모든 문자열은 NFKC, leading/trailing whitespace 제거, 내부 whitespace 한 칸화 후
비어 있으면 생략한다. field label과 field 순서는 고정하며, tag·body property는 정렬·중복
제거하고 parameter는 `(location, name)` 정렬한다. HTML 제거 뒤 description은 현 endpoint
chunk와 같은 300자 상한을 쓴다. 총 text도 사전에 고정한 상한(예: 1,024 Unicode code
point)을 넘으면 **아래 표의 순서대로** 자른다. 이 상한·format version은 env tuning 대상이
아니다.

| 표현형 | 생성 규칙 | 예시 (`POST /repos/{owner}/{repo}/issues`) | 의도 |
| --- | --- | --- | --- |
| method+path | 대문자 method와 원 path를 그대로 기록 | `MethodPath: POST /repos/{owner}/{repo}/issues` | 정확 route, root/child 구분 |
| resource path | version을 뺀 literal path segment의 원형·결정적 단수형·subword, ancestor와 leaf를 구분 | `Ancestor: repos repo`; `Resource: issues issue` | root resource가 장황한 child에 묻히는 문제 완화 |
| operation template | `(HTTP method, collection/item shape)`에 대한 **사전 동결** `OPERATION_ALIASES`와 resource를 한 template으로 기록 | `Action: create add new register`; `Phrase: create issue` | 자연어 action-resource와 짧은 endpoint를 직접 정렬 |
| operationId | 원문 및 camelCase/`_`/`-`/`/` subword | `OperationId: issues create issue` | API 고유 naming 신호 |
| summary/description | OpenAPI 원문만 | `Summary: Create an issue` | specification이 이미 제공한 의미 신호 |
| parameter names | path/query/header/cookie parameter 이름·위치와 request body property 이름 | `Params: query state`; `Body: title body assignees` | `q18` 같은 세부 요구와 child decoy 구분 |
| tags | OpenAPI tags 원문/subword | `Tags: issues` | 도메인 문맥 |

`OPERATION_ALIASES`는 이미 `endpoint_structure.py`에 있는 method/shape 전역표를
projection format v1에 복사해 동결한다. 명사형 template은 v1에서 **제거**한다.
`create -> creation`처럼 불규칙 영어 형태를 새 고정표로 만드는 것은 P0 표적에 필요한
근거가 없고, 별도 어휘 정책을 만들어야 하므로 YAGNI다. 새 동사, 한국어/영어 번역,
resource synonym을 legacy qid 결과를 보고 더하지 않는다. alias 표 또는 format version을
바꾸려면 새 calibration corpus와 architect verdict가 필요하다.

이러한 형식은 서버가 query를 생성하는 것이 아니다. 검색 시에는 사용자가 준 원 `query`
한 개만 현 local embedding provider와 FTS에 넣는다. `query_variants`는 기존 keyword/vector
arm에만 전달되고 `endpoint_repr` v1에는 전달하지 않는다. 따라서 client LLM이 variant를
준 경우에도 이 새 arm은 그것을 번역·확장·재사용하지 않으며, client-LLM 위임 원칙과
후보 attribution이 보존된다.

### 2.3 가능한 범위와 명시적 한계

local multilingual embedding은 한국어 질의와 위의 영어 canonical text를 같은 공간에서
비교할 수 있으므로, 원래의 길고 잡음 많은 chunk보다 root endpoint를 유리하게 만들 수 있다.
그러나 이는 pretrained model의 의미 일반화이지 서버의 번역 기능이 아니다. OpenAPI가
`invoice` 외에 billing/history 단서를 전혀 제공하지 않고 embedding이 그 관계를 일반화하지
못하면 `q10`은 여전히 후보에 들지 않을 수 있다. 이 설계는 그런 경우를 alias 상수로
덮지 않는다.

## 3. 실행 위치와 후보 결합

### 3.1 선택: 전처리 확장이 아닌 독립 병렬 arm

선택지는 keyword/vector의 query를 바꾸어 넓히는 전처리 확장이 아니라, **독립
`endpoint_repr` arm**이다. 기존에는 exact pre-lookup 뒤 keyword와 vector 두 rank list가
RRF에 들어간다. feature ON일 때만 다음 순서를 쓴다.

```text
MCP query ── exact(method+path / operationId) ──> exact candidates
       └── original query ┬─ keyword arm (unchanged, width 50)
                           ├─ vector arm  (unchanged, width 50; variants는 현행만)
                           └─ endpoint_repr arm (new, width 50)
                                      ├─ canonical FTS top 50
                                      └─ canonical vector top 50
                                          -> endpoint별 best-rank list top 50

keyword + vector + endpoint_repr -- RRF(k=60, equal rank contribution) --> tentative wide 50
legacy keyword+vector -- RRF(k=60) --> legacy base final / both-slot snapshot
tentative wide + locked slots --> final top_k (기본 10)
```

새 arm의 내부 lexical/dense 두 검색은 endpoint id별 최소 rank로 접고, 동점은
`repr_vector` 우선, 그 다음 endpoint id 오름차순으로 정한다. 이 결합은 raw score scale을
섞지 않고 rank만 쓰며 trace에는 양쪽 rank와 winning source를 모두 남긴다. 두 내부 검색을
RRF의 두 개 새 arm으로 세지 않는다. 외부 RRF에는 endpoint당 하나의 `endpoint_repr` rank
list만 추가된다.

`endpoint_repr`는 현 용어로 “다섯 번째 arm”이라 부르지 않는다. exact는 pre-lookup이고
fallback은 별도 전략이지 rank arm이 아니기 때문이다. 정확한 정의는 **세 번째 RRF arm**,
또는 exact/keyword/vector/fallback까지 후보원을 세는 표현이라면 다섯 번째 후보원이다.

fallback 전략은 이 arm을 호출하지 않는다. `search_strategy=fallback`의 keyword-first,
zero-hit일 때만 vector 호출이라는 계약은 byte-identical로 남는다.

### 3.2 RRF 진입과 public contract

새 rank list는 기존 `reciprocal_rank_fuse`에 `k=60`, weight 1로 넣는다. k·weight·width를
legacy 20 결과에 맞춰 추가 설정으로 노출하지 않는다. `match_type` public contract는 계속
`keyword`/`vector`/`both`(exact는 `exact`)만 표시한다. `endpoint_repr` hit 여부는 response에
새 enum을 추가하지 않고 evaluation/debug trace의 별도 field로만 기록한다. 즉 `both`는
언제나 **legacy keyword와 vector 양쪽 hit**라는 뜻이다.

feature OFF, semantic embedding 비가용, projection이 아직 없는 scope에서는 새 list는 빈
list이며, 기존 keyword/vector-only RRF 경로를 그대로 탄다. 특히 non-semantic/hash provider면
`endpoint_repr`는 canonical FTS도 실행하지 않고 빈 trace를 반환한다. OFF에서는 projection
repository lookup조차 하지 않아야 한다.

### 3.3 both-arm slot 보존과 P2/P3 관계

새 third-arm RRF는 기존 `both` 후보의 score도 바꿀 수 있다. 그러므로 feature ON의 final
assembly는 같은 query의 legacy keyword+vector-only RRF `base_wide[:remaining_top_k]`를 함께
계산해, 그 안의 모든 `match_type=both` endpoint를 기존 relative slot에 HARD lock한다. 나머지
slot만 tentative 3-arm wide 순서로 채운다. exact가 먼저 차지한 수만큼 `remaining_top_k`을
동일하게 계산한다.

그 결과:

- `q08/q09/q11/q12`처럼 legacy final 10 slot이 모두 both이면 feature ON도 final은
  byte-identical이다. 이 설계의 효과 상한이며, 숨기지 않는다.
- `q04/q05/q06/q07/q10/q17/q18`에는 legacy final both lock이 없거나 빈 slot이 있으므로
  representation arm이 root endpoint를 올릴 기회가 있다.
- P3의 reranker도 같은 lock 개념을 쓰지만 **P3와 이 후보는 결합하지 않는다**. composition은
  `DOCS_MCP_SEARCH_ENDPOINT_REPRESENTATION_ENABLED=true`와 P3 enabled의 동시 설정을
  invalid configuration으로 fail-closed 한다.
- P2는 quota가 0인 기준선에서만 이 후보를 측정한다. quota>0와의 동시 설정도 invalid로
  처리한다. P2의 tail replacement가 새 arm의 rank 효과를 가리는 것을 막기 위함이다.

이는 P3 q10의 both-subset HARD FAIL을 완화하거나 재정의하는 것이 아니라, 새 candidate
identity의 처음부터 적용하는 output safety contract다.

## 4. P0 표적 검증 매핑

아래 “기대 진입”은 exposed 20 결과에 맞춘 상수 규칙이 아니라, §2의 모든 OpenAPI에 공통인
format으로부터 사전에 도출한 검증 가설이다. legacy 20은 diagnostic으로만 사용하고, 승격은
새 sealed split에서 한다.

| qid | P0 실패와 정답 | 기대 representation 신호 | 기대 진입 | 이 설계가 보장하지 않는 부분 |
| --- | --- | --- | --- | --- |
| q04 | generation miss, `POST /v1/customers` | `POST` collection의 create/add/new/register template + `customers/customer` root | canonical vector top-50. Korean FTS hit는 기대하지 않음 | embedding이 한국어 action-resource를 영어 root와 정렬하지 못하면 여전히 miss |
| q07 | generation miss, `DELETE /repos/{owner}/{repo}` | delete/remove/destroy template + `repos/repo` resource, operationId/summary | canonical vector top-50 | `repository` 번역/동의어를 서버가 추가하지 않음; source가 빈약하고 embedding이 약하면 miss |
| q10 | generation miss, `GET /v1/invoices` | GET collection list/index/browse template + `invoice`; source summary/description/tag의 billing 단서가 있으면 함께 반영 | canonical vector top-50이 유일한 합리적 경로 | `billing history -> invoices`는 deterministic rule이 아님. source/model이 연결하지 못하면 이 candidate로도 회복 불가 |
| q05 | keyword-blank final-cut, `POST /v1/refunds` (vec 35) | create/submit + `refund`의 짧은 root projection | canonical vector가 현 vector rank 35보다 얕은 rank로 들어와 3-arm RRF에 기여 | Korean FTS는 비어도 정상; payment/refund 의미 연결은 model에 의존 |
| q06 | keyword-blank final-cut, `POST .../issues` (vec 16) | create/add/new/register + issue root, child path·응답 잡음 제거 | canonical vector; root POST가 child decoy보다 앞설지 측정 | 기존 vector arm의 정답이 있다고 해서 canonical arm rank 향상이 자동 보장되지는 않음 |
| q17 | keyword-blank final-cut, `GET .../issues` 또는 `POST .../issues` (vec 12/25) | GET list/browse issue와 POST create issue가 **각각의 endpoint projection**에 존재 | canonical vector가 적어도 한 accepted endpoint를 top-50에 넣는지, 두 endpoint의 rank를 별도 trace | 단일 복합 질의를 서버가 분해하지 않는다. 두 정답의 동시 회복은 보장하지 않으며 C6 count로 평가 |
| q18 | keyword-blank final-cut, `POST /v1/charges` (vec 42) | create/submit + charge, request parameter/body의 `currency`와 source summary/description | canonical vector (영문 canonical FTS는 한국어 원질의에 기대하지 않음) | OpenAPI에 currency field가 없거나 embedding이 세부 조건을 무시하면 root/child 구분은 실패 가능 |

`q08/q09/q11/q12`는 `endpoint_repr`가 후보를 반환해도 legacy both-slot lock으로 final top-10
회복이 0인 비표적군이다. `q16`의 두 번째 `POST /v1/refunds`도 우연히 candidate가 될 수
있지만, C6 accepted-count를 바꾸는 별도 목표로 선언하지 않는다. q13–q15/q19/q20 및 C1은
회귀 guard의 대상이지 format을 이들에 맞춰 조정할 근거가 아니다.

## 5. 색인·운영 범위와 flag

### 5.1 변경 범위

1. Alembic migration: projection table, FK/cascade, `canonical_tsv` GIN, `embedding` HNSW,
   document scope index를 추가한다. 기존 `chunk.text`, `text_tsv`, `search_tsv`, embedding은
   수정하지 않는다.
2. pure projection builder: ParsedEndpoint에서 §2.2 format v1과 `source_hash`를 만든다.
   parser·OpenAPI 재색인과 write-back refresh 양쪽에서 같은 builder를 호출한다.
3. indexer/backfill: document reindex의 endpoint lifecycle에 projection 생성·삭제를 포함한다.
   기존 문서는 migration만으로 검색 가능해지지 않으므로 명시적, 재시도 가능한 backfill이
   필요하다. local embedding 실패 시 해당 문서 transaction은 현 endpoint chunk indexing의
   원자성 규약과 같은 방식으로 실패해야 하며, 반쪽 projection을 남기지 않는다.
4. repository/search: scope-filtered projection FTS/vector lookup과 rank trace를 추가한다.
5. composition/config: default-off flag와 mutual-exclusion validation을 배선한다.

`DOCS_MCP_SEARCH_ENDPOINT_REPRESENTATION_ENABLED=false`가 유일한 product switch다. arm width
50, RRF k=60, format version, field cap은 code constant로 고정한다. flag OFF이면 final response,
fallback, exact, RRF 순서뿐 아니라 임베딩 호출과 DB projection query도 기준선과
byte-identical이다. flag rollback은 OFF로 바꾸는 것뿐이며 새 projection data는 inert audit
data로 남겨도 기존 검색에 영향을 주지 않는다.

### 5.2 비용과 운영 리스크

endpoint당 vector 1개와 canonical text/FTS/두 index가 추가된다. 따라서 재색인 시 endpoint
수만큼 local embedding 1회가 더 들고, 저장 공간은 vector dimension·HNSW graph·GIN 크기에
비례해 늘어난다. 실제 byte/latency 예산은 corpus 크기와 production hardware를 계측하기 전
숫자로 약속하지 않는다. request ON 경로는 local embedding 1회(동일 query embedding은
vector arm과 공유 가능) + FTS/HNSW 두 lookup + legacy baseline RRF snapshot을 추가한다.

공유 embedding은 query vector 값이 완전히 같은 경우에만 허용하고, provider 호출 순서나
fallback 의미를 바꾸지 않는다. non-semantic/hash provider에서는 `endpoint_repr` arm 전체를
비활성화한다. canonical FTS만 단독으로 RRF에 넣는 것은 새 lexical weighting 후보가 되어
이 설계의 semantic candidate-generation attribution을 흐리므로 허용하지 않는다. 이 배포는
feature ON 평가 대상이 아니다.

주요 리스크는 canonical format이 source의 중요 field를 잃는 것, root template이 generic
decoy를 과도하게 올리는 것, 새 RRF contribution이 non-both accepted를 내리는 것, migration/
backfill 동안 projection coverage가 부분적인 것이다. 각각 source-hash/coverage trace,
legacy-slot lock, C1/route-pair guard, per-document atomic index lifecycle로 통제한다.

## 6. 사전 HARD gate와 측정

| gate | 요구 | 검증 방식 |
| --- | --- | --- |
| OFF parity | flag OFF에서 09 baseline과 response bytes·arm calls·RRF/base-wide/final이 완전 동일 | 09 legacy trace diff, fallback/exact/query_variants 경로 포함 |
| 결정성 | 같은 source와 query는 projection hash, FTS/vector rank merge, final order가 동일 | fresh rebuild + 3회 query/restart 비교; 모든 tie rule 기록 |
| source coverage | active document의 endpoint마다 v1 projection 정확히 한 행, hash/version 일치 | index/reindex/backfill count 및 orphan/missing audit |
| both-arm slot preservation | feature ON에서도 legacy keyword+vector base final의 `both` endpoint id·slot·상대순서가 불변 | query별 locked-slot trace diff. 하나라도 이탈하면 HARD FAIL |
| C1 / route-pair | C1 gross loss 0, accepted regression 0, route-pair non-regression 100% | legacy diagnostic에서 전 후보 rank delta와 pair 결과를 모두 출력 |
| attribution | qid별 legacy kw/vec rank, repr FTS/vector rank, repr merged rank, 3-arm RRF rank, lock 사유를 남김 | P0과 같은 좌표 정의로 `generation`/`final` 변화를 분리 |
| isolation | P2 quota=0, P3 disabled, fallback non-invocation | composition test와 eval config dump |
| performance | index/backfill duration·rows, ON request p50/p95/RSS를 OFF와 비교 | production-like hardware에서 계측; threshold는 sealed split protocol과 함께 사전 동결 |

legacy 20의 `q04/q07/q10` admission과 7개 표적 final rank는 diagnostic 증거다. 그것을 본 뒤
alias, template, width, lock을 고치지 않는다. 위 HARD가 통과하고 projection format이 동결된
후 architect/lead가 Korean strata를 포함한 새 sealed split을 저작·미개봉으로 보관한다. 그
sealed 결과가 candidate 승격 여부를 결정한다.

## 7. developer 착수 단위

1. **Projection format과 persistence.** pure builder v1, normalization/ordering/cap/hash unit
   tests, ORM/migration/repository CRUD와 document/project scope tests를 만든다. qid·정답 문자열을
   production constant나 builder test fixture에 넣지 않는다.
2. **Index lifecycle.** initial index, full reindex, endpoint metadata write-back refresh,
   delete cascade, backfill/rollback behavior를 endpoint chunk lifecycle와 원자적으로 연결한다.
   projection count/hash audit command 또는 eval fixture를 제공한다.
3. **Independent arm.** projection FTS/vector lookup, endpoint-level best-rank merge, width 50,
   deterministic ties, per-arm trace를 구현한다. keyword/vector code path와 `query_variants`
   routing은 변경하지 않는 unit/integration parity test를 포함한다.
4. **RRF composition과 safety.** third RRF list, legacy baseline snapshot, both-slot lock,
   exact-relative slot 처리, default-off config, P2/P3 mutual exclusion, fallback bypass를
   배선한다. public `match_type` contract test를 갱신한다.
5. **Diagnostic harness.** P0-style coordinates와 §6 gates, ON/OFF latency/RSS/index cost를
   출력하는 corpus evaluation을 작성한다. 먼저 legacy 20 diagnostic만 수행하고, 결과에 맞춘
   format 수정 없이 새 sealed split fixture를 architect/lead가 별도 동결한 뒤 promotion
   evaluation을 수행한다.

각 단위는 독립적으로 review 가능하되, product flag는 1–4가 모두 완료되기 전까지 ON으로
승격하지 않는다. 이 문서는 설계 승인용이며 구현 착수 지시는 별도다.
