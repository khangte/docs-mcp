# 78. 엔드포인트 색인 시점 구조 신호 설계 (verdict 74 §5 트랙)

- 대상: verdict 74 §5 "별도 index representation 트랙"
- 선행: `docs/architect-review/74_p02_coverage_fix_failure_and_keyword_variant_stop_verdict.md`,
  `docs/architect-review/76_verdict74_production_baseline_statement_verdict.md`,
  `docs/architect-review/69_search_quality_expanded_gate_set_design.md`
- 상태: **설계안 — lead 승인 대기. 이 문서로 구현을 착수하지 않는다** (verdict 74 §5 마지막 문장)
- 산출 범위: 설계·비용 비교·ADR 판단·케이스 매핑. 구현 계획 수립과 코드 작성은 승인 후 별도 산출물.

## 1. 요약

verdict 74 §5가 지목한 네 개의 index-time 구조 신호를 다음과 같이 구체화한다.

1. `chunk` 테이블에 **평문 파생 컬럼 3개**(`leaf_text` / `intent_text` / `context_text`)를
   추가한다. 값은 `method`·`path`·`summary`·`tags`·`operation_id`에서만 결정적으로 산출한다.
2. 그 3개 컬럼과 기존 `text`를 `setweight` A/B/C/D로 묶은 **가중 tsvector 생성 컬럼**
   `search_tsv`를 추가하고, 엔드포인트 키워드 arm의 `@@`와 `ts_rank`를 이 컬럼으로 옮긴다.
3. `text`·`embedding` 컬럼은 **한 바이트도 바꾸지 않는다**. 따라서 재임베딩이 없고
   벡터 arm은 비트 단위로 불변이며, 관측되는 순위 변화는 전부 lexical arm에 귀속된다.
4. 기존 `text_tsv` 컬럼은 남긴다. 협업 문서(`chunk_type="section"`) 검색 경로는
   그대로 `text_tsv`를 쓰므로 blast radius가 엔드포인트 검색에 갇힌다.
5. 롤백은 설정 스위치 하나(`search_lexical_field`)로 컬럼을 되돌리는 것으로 충분하다.

채택하지 않는 대안은 **explicit chunk text 추가**(청크 `text`에 `Resource:`/`Operation:`
줄을 덧붙이는 방식)다. 근거는 §3.

## 2. 문제 재정의 — 왜 색인 표현인가

verdict 74 §2의 진단을 그대로 승계한다. 현재 endpoint 청크의 lexical 표현은
`to_tsvector('simple', text)` 단일 필드이며, 다음 세 종류의 token이 **완전히 같은 무게**를
가진다.

- target resource를 지시하는 path leaf (`topics`)
- ancestor context (`repos`, `{owner}`, `{repo}`)
- 300자로 잘린 free-text description 안의 우연한 반복 (`repository`가 여러 번)

`ts_rank`는 이 평평한 표현 위에서 density를 본다. 그래서 짧고 정확한 정답 청크가
길고 부정확한 형제 청크에 밀린다. verdict 74에서 반려한 (a)~(d) 네 안은 전부 **search-time에
이 평평한 표현을 후처리**하려는 시도였고, 표현 자체가 구분하지 못하는 정보를 후처리로
복원할 수 없다는 것이 게이트 결과였다.

이 설계의 명제는 하나다. **어느 token이 target resource이고 어느 token이 ancestor
context이며 어느 token이 free text인지를 색인 시점에 필드로 분리해 두면, `ts_rank`의
내장 가중치가 density 역전을 직접 고친다.**

## 3. 방식 택일 — explicit chunk text vs weighted tsvector

lead 지시의 핵심 택일 항목이다.

### 3.1 방식 1 — explicit chunk text 추가

`build_endpoint_chunk_text`의 출력에 줄을 덧붙인다.

```text
[GET] /repos/{owner}/{repo}/topics — Get all repository topics
Resource: topics topic
Operation: list index all browse
Ancestors: repos repo owner
...
```

| 항목 | 평가 |
|---|---|
| 코드 변경 | `chunk_builder.py` 1개 함수. 가장 작다 |
| 마이그레이션 | 없음 (`text`는 기존 컬럼, `text_tsv`는 generated라 자동 재계산) |
| 재색인 | **전 엔드포인트 청크 재임베딩 필요** — `text`가 바뀌면 `embedding`도 바꿔야 표현이 일치한다 |
| 벡터 arm | 전 코퍼스에서 이동. 기존 baseline(`ecc3e792`) 수치와 비교 불가 |
| 귀속 | 순위 변화가 lexical 신호 때문인지 임베딩 이동 때문인지 **분리 불가** |
| density 문제 | **해결하지 못한다.** 새 token이 description token과 같은 무게로 들어가므로, 300자 설명에 3~8개 token을 더하는 것에 불과하다. 형제 청크에도 똑같이 더해지므로 상대 순위는 거의 그대로다 |
| 롤백 | 재색인 되돌리기 = 다시 전량 재임베딩 |

마지막 두 줄이 결정적이다. 이 방식은 verdict 74가 반증한 실패 모드(평평한 bag-of-tokens
안에서 density로 경쟁)를 **그대로 재현한다**. 신호를 추가하되 신호를 구분할 수단이 없다.

### 3.2 방식 2 — weighted tsvector (채택)

파생 필드를 별도 컬럼에 두고 `setweight`로 등급을 붙인 tsvector를 별도 생성 컬럼으로
만든다.

| 항목 | 평가 |
|---|---|
| 코드 변경 | 신규 파생 모듈 1개 + 기존 6개 파일 소폭 (§6) |
| 마이그레이션 | 컬럼 3개 추가 + generated 컬럼 1개 + 부분 GIN 인덱스 1개. 1개 revision |
| 재색인 | **재임베딩 없음.** 파생 3개 컬럼만 백필 스크립트로 채운다 (§5.3) |
| 벡터 arm | `text`·`embedding` 불변 → 비트 단위 동일 |
| 귀속 | 같은 인덱스 위에서 컬럼만 바꿔 baseline/candidate를 비교할 수 있다 (§8) |
| density 문제 | `ts_rank(weights, ...)`의 A/B/C/D가 **정확히 이 문제를 위한 Postgres 내장 기능**이다 |
| 롤백 | 설정 스위치로 `text_tsv`로 되돌림. 데이터 삭제·재색인 불필요 |
| 비용 | `chunk` 테이블 rewrite 1회, 엔드포인트 행에 대한 두 번째 GIN 인덱스 |

**방식 2를 채택한다.** 방식 1보다 코드·마이그레이션 비용은 크지만, 재색인 비용은 오히려
작고(재임베딩 0), 무엇보다 방식 1은 문제를 풀지 못한다.

### 3.3 비용 요약

| 비용 항목 | 방식 1 | 방식 2 |
|---|---|---|
| 마이그레이션 revision | 0 | 1 |
| 변경 파일 수 | 1 | 7 (+ 신규 2) |
| 엔드포인트 청크 재임베딩 | 전량 | 0 |
| 파생 컬럼 백필 | 해당 없음 | 전량 (임베딩 호출 없음, 단일 SQL 조인 + Python 파생) |
| 추가 디스크 | 0 | endpoint 행 tsvector 1개 + 부분 GIN 인덱스 1개 |
| 기존 평가 baseline 재사용 | 불가 | 가능 |

## 4. 설계

### 4.1 path 파싱 계약 (결정적)

신규 모듈 `app/services/indexer/endpoint_structure.py`.

```text
segments(path)   = "/"로 split 후 빈 문자열 제외
is_param(s)      = s가 "{"로 시작하고 "}"로 끝남
param_name(s)    = s[1:-1]
is_version(s)    = 정규식 ^v[0-9]+(\.[0-9]+)*$ (대소문자 무시)
literals         = segments 중 is_param도 is_version도 아닌 것
params           = segments 중 is_param인 것의 param_name
shape            = segments가 비지 않고 마지막이 is_param이면 "item", 아니면 "collection"
leaf_segment     = literals의 마지막 (없으면 빈 문자열)
ancestor_segments= literals의 마지막을 제외한 나머지
```

버전 세그먼트(`v1`)를 버리는 이유는 Stripe 코퍼스 589건 전부가 `v1`을 공유해 판별력이
0이기 때문이다. `context_text`에도 넣지 않는다.

### 4.2 subword 분해와 단수화

```text
split_subwords(seg):
  "_", "-", "." 및 camelCase 경계에서 분해, 전부 소문자
  결과 = [소문자 전체] + (조각이 2개 이상일 때만) 각 조각
  예) "line_items" -> ["line_items", "line", "items"]
      "topics"     -> ["topics"]

singularize(t):
  길이 3 초과이고 "ies"로 끝나면 -> "ies"를 "y"로   (categories -> category)
  "ss"로 끝나면 -> 그대로                            (address -> address)
  "ses|xes|zes|ches|shes"로 끝나면 -> "es" 제거      (boxes -> box)
  길이 2 초과이고 "s"로 끝나면 -> "s" 제거           (topics -> topic, repos -> repo)
  그 외 -> 그대로
```

단수화는 **영어 굴절 규칙만** 적용한다. `repos -> repository` 같은 약어 확장은
결정적으로 유도할 수 없으므로 넣지 않는다(§11 비범위).

### 4.3 네 신호의 필드 배치

| 컬럼 | 가중치 | 내용 |
|---|---|---|
| `leaf_text` | **A** | `split_subwords(leaf_segment)` + 각각의 `singularize`. `shape == "item"`이면 마지막 param 이름의 subword도 추가하되, 그 중 `id` 토큰은 제외 |
| `intent_text` | **B** | operation alias 토큰(§4.4) + `summary` 원문 |
| `context_text` | **C** | `ancestor_segments`의 subword + 단수형, `params` 이름의 subword, `tags`, `operation_id`의 subword. `leaf_text`에 이미 있는 토큰은 제외 |
| `text` (기존) | **D** | 기존 청크 텍스트 전체 (변경 없음) |

D가 기존 `text` 전체이므로 **`search_tsv`의 lexeme 집합은 항상 `text_tsv`의 상위집합**이다.
즉 이 변경으로 후보 집합이 줄어드는 일은 구조적으로 불가능하다. 이것을 HARD 게이트의
불변식으로 쓴다(§8.3).

`operation_id`는 subword 분해 전에 `/`로 먼저 쪼갠다(`repos/get-all-topics` ->
`repos`, `get-all-topics`).

예시 — `GET /repos/{owner}/{repo}/topics`, summary `Get all repository topics`,
tags `["repos"]`, operationId `repos/get-all-topics`:

```text
leaf_text    = "topics topic"
intent_text  = "list index all browse Get all repository topics"
context_text = "repos repo owner get-all-topics get-all-topic get all"
```

`topics`/`topic`은 `leaf_text`에 이미 있으므로 `context_text`에서 뺀다.

예시 — `GET /repos/{owner}/{repo}`, summary `Get a repository`,
tags `["repos"]`, operationId `repos/get`:

```text
leaf_text    = "repos repo"
intent_text  = "get retrieve fetch read show detail Get a repository"
context_text = "owner get"
```

### 4.4 operation alias 표 (동결)

`method` × `shape`만으로 결정한다. **이 표는 이 문서에서 동결한다. 항목 추가·삭제는
새 architect verdict를 요구한다.**

| method | shape | alias 토큰 |
|---|---|---|
| GET | collection | `list` `index` `all` `browse` |
| GET | item | `get` `retrieve` `fetch` `read` `show` `detail` |
| POST | collection | `create` `add` `new` `register` |
| POST | item | `create` `submit` `send` |
| PUT | (무관) | `replace` `update` `set` |
| PATCH | (무관) | `update` `modify` `edit` `change` |
| DELETE | item | `delete` `remove` `destroy` |
| DELETE | collection | `delete` `remove` `clear` |
| HEAD, OPTIONS, TRACE | (무관) | (없음) |

**과적합 방지 규칙.** 이 표는 HTTP/REST 관용 표현에서만 뽑았고 게이트 질의를 보고
채우지 않았다. 게이트 실행 후 실패한 질의의 동사를 이 표에 추가하는 것은
verdict 74가 (b)·(c)를 반려한 것과 같은 오류(관측값을 상수로 옮기기)이므로 금지한다.

이 규칙이 실제로 작동하는 예가 01 eval q10 `show my billing history`
(정답 `GET /v1/invoices`)다. GET collection alias에 `show`를 넣으면 q10이 잡히지만,
그것은 q10을 보고 표를 고치는 것이므로 하지 않는다. q10은 미해결로 남긴다(§7.2).

### 4.5 결정적 생성 계약

- 입력은 `(method, path, summary, tags, operation_id)` 다섯 개뿐이다. LLM 호출 없음,
  난수 없음, 색인 순서·시각 의존 없음.
- 같은 입력 -> 같은 세 문자열(공백·순서까지 동일). 토큰 순서는 위 정의의 산출 순서로
  고정하고, 중복 제거는 최초 등장 순서를 유지한다.
- 색인 경로와 백필 경로가 **같은 함수**를 호출한다. 두 경로의 산출이 다르면 그것은 버그다.
- `EndpointBusinessMetadata`(LLM 생성)는 이 필드에 **주입하지 않는다**. metadata는 지금처럼
  `text`(D)에만 들어간다. 구조 신호는 결정적 원천만 쓴다는 verdict 74 §5.4 요구이자,
  write-back으로 metadata가 바뀌어도 A/B/C 필드는 흔들리지 않게 하는 안정성 요구다.

## 5. 스키마·마이그레이션·백필

### 5.1 컬럼

```text
chunk.leaf_text     TEXT NOT NULL DEFAULT ''
chunk.intent_text   TEXT NOT NULL DEFAULT ''
chunk.context_text  TEXT NOT NULL DEFAULT ''
chunk.search_tsv    TSVECTOR GENERATED ALWAYS AS (<식>) STORED
```

생성 식(모델과 마이그레이션이 `app/models/chunk.py`의 상수 하나를 공유한다):

```text
CASE WHEN chunk_type = 'endpoint' THEN
    setweight(to_tsvector('simple', NORM(leaf_text)),    'A') ||
    setweight(to_tsvector('simple', NORM(intent_text)),  'B') ||
    setweight(to_tsvector('simple', NORM(context_text)), 'C') ||
    setweight(to_tsvector('simple', NORM(text)),         'D')
ELSE NULL END
```

`NORM(col)`은 기존 `TEXT_TSV_EXPRESSION`이 `text`에 적용하는 3단 `regexp_replace` 체인과
**동일한 정규화**다(ASCII/한글 경계 공백 삽입 2회 + 나머지 문자 공백 치환). 질의 측
`tokenize_terms`(`[0-9A-Za-z_]+|[가-힣]+`)와의 토큰 경계 대칭을 유지해야 하기 때문이다.

> **구현 제약.** 기존 `TEXT_TSV_EXPRESSION` 문자열은 리팩터링하지 않는다. 헬퍼로 뽑아
> 재조립하면 한 바이트라도 달라졌을 때 alembic autogenerate가 기존 컬럼에 스푸리어스
> diff를 낸다(`app/models/chunk.py` 기존 주석의 경고). `SEARCH_TSV_EXPRESSION`은
> 별도로 작성하고, `TEXT_TSV_EXPRESSION`이 현재 리터럴과 같음을 단위 테스트로 못박는다.

`chunk_type <> 'endpoint'`인 행에서 `search_tsv`가 NULL이 되므로 section·schema 청크는
저장 비용이 0이다.

### 5.2 인덱스

```text
CREATE INDEX ix_chunk_search_tsv ON chunk USING gin (search_tsv)
    WHERE chunk_type = 'endpoint';
```

부분 인덱스로 엔드포인트 행에만 건다. 기존 `ix_chunk_text_tsv`는 유지한다(협업 문서
검색 경로가 계속 쓴다).

### 5.3 백필

신규 스크립트 `app/scripts/backfill_endpoint_structure.py`.

- `chunk`(`chunk_type='endpoint'`) `JOIN api_endpoint ON chunk.ref_id = api_endpoint.id`
- 각 행에 대해 §4의 파생 함수를 호출해 세 컬럼을 UPDATE
- 임베딩 호출 없음, `text` UPDATE 없음
- 문서 단위로 커밋, `--document-id` / `--project` 로 범위 제한 가능
- 멱등: 다시 돌려도 같은 값

전체 재색인으로도 같은 값이 나오지만(§4.5 결정성 계약) 백필을 권고한다. 재색인은
재임베딩 비용을 물고, verdict 70이 기록한 `api_endpoint.id` 재해시(`idx` 포함 키)
비결정성을 다시 건드린다.

### 5.4 write-back 경로

`refresh_endpoint_chunk`(metadata write-back)는 `text`와 `embedding`만 갱신한다.
A/B/C 필드는 metadata에 의존하지 않으므로 갱신 대상이 아니다. 다만 갱신 경로가 세 컬럼을
**빈 문자열로 덮지 않는지**를 회귀 테스트로 고정한다(§8.4).

## 6. 코드 변경 지점

| # | 파일 | 변경 |
|---|---|---|
| 1 | `app/services/indexer/endpoint_structure.py` (신규) | path 파싱, subword/단수화, alias 표, `derive_endpoint_structure()` |
| 2 | `app/services/indexer/chunk_builder.py` | `BuiltChunk`에 `leaf_text`/`intent_text`/`context_text` 추가. endpoint 청크에만 채우고 schema·section은 빈 문자열 |
| 3 | `app/models/chunk.py` | 컬럼 3개, `SEARCH_TSV_EXPRESSION`, 부분 GIN 인덱스. `TEXT_TSV_EXPRESSION` 불변 |
| 4 | `app/services/indexer/indexer_service.py` | `Chunk(...)` 생성 시 세 필드 전달 |
| 5 | `app/repositories/chunk_repository.py` | `search_endpoint_by_text`에 `lexical_field` 인자 추가 — `"text"`면 현행 `text_tsv` + 무가중 `ts_rank`, `"structured"`면 `search_tsv` + 가중 `ts_rank`. 기본값은 `"text"`(무변경) |
| 6 | `app/services/search/keyword_search.py` | `lexical_field`를 생성자 주입으로 받아 전달 |
| 7 | `app/core/config.py` | `search_lexical_field` 설정(`search_strategy`와 동일한 env 문자열 패턴, 미인식 값은 `"text"`로 degrade) + `app/composition.py` 배선 |
| 8 | `alembic/versions/<new>` | 컬럼 3개 + generated 컬럼 + 부분 인덱스 |
| 9 | `app/scripts/backfill_endpoint_structure.py` (신규) | §5.3 |
| 10 | `tests/fixtures/corpus_eval/run_corpus_eval.py` | lexical field 축을 실행 옵션으로 노출(§8.1) |

`app/services/search/endpoint_candidate_search.py`와 RRF는 **변경하지 않는다**.
벡터 arm, exact lookup, variants 배선 전부 현행 그대로다.

### 6.1 ts_rank 가중치

`ts_rank('{0.1, 0.2, 0.4, 1.0}', search_tsv, tsq)` — Postgres 기본 `{D,C,B,A}` 배열을
그대로 쓴다. 상수로 고정하고 env로 노출하지 않는다. 게이트 결과를 보고 이 네 숫자를
조정하는 것은 §4.4의 과적합 방지 규칙과 같은 이유로 금지한다. 기본값으로 게이트를
통과하지 못하면 그것은 튜닝 대상이 아니라 설계의 결과다.

`ts_rank_cd`로의 전환은 하지 않는다. 가중치와 랭크 함수를 동시에 바꾸면 귀속이 깨진다.

## 7. 케이스 매핑

### 7.1 p02 (gate v1 `g003` root / `g004` child)

| | root `g003` | child `g004` |
|---|---|---|
| 원문 질의 | `저장소 기본 정보를 가져와줘` (ko) | `저장소에 달린 토픽만 따로 조회해줘` (ko) |
| variant | `fetch the basic repository info` | `list just the topics on the repository` |
| 정답 | `GET /repos/{owner}/{repo}` | `GET /repos/{owner}/{repo}/topics` |

정답 청크의 신호:

| 필드 | root 정답 | child 정답 |
|---|---|---|
| A `leaf_text` | `repos repo` | `topics topic` |
| B `intent_text` | `get retrieve fetch read show detail` + `Get a repository` | `list index all browse` + `Get all repository topics` |
| C `context_text` | `owner get` | `repos repo owner get-all-topics get-all-topic get all` |

- **child**: variant term `topics`가 **A**에서 정답에만 걸린다. 형제 `/repos/{owner}/{repo}/*`
  중 leaf가 `topics`인 것은 정답뿐이다. 추가로 `list`가 **B**에서 collection-shape 형제에만
  걸린다. verdict 74가 기록한 "동일 coverage 형제 수십 개" 상황이 A 등급에서 1건으로 붕괴한다.
- **root**: variant term `fetch`가 **B**의 GET-item alias에 걸린다. `/repos/{owner}/{repo}/*`
  형제 대부분은 collection-shape여서 이 토큰을 갖지 않는다. 동시에 `repository`가
  summary(**B**)에 걸린다. item-shape 형제(`.../issues/{issue_number}` 등)는 `fetch`는
  갖지만 summary에 `repository`가 없다. 두 B 토큰을 모두 가진 것은 정답이다.
  verdict 74가 기록한 root coverage 0.25 / pool 미진입은 "informative term이 전부
  description density 경쟁에 던져졌기 때문"이었고, 그 경쟁이 B 등급으로 격리된다.
- pair 판정은 §69 3.4 산식 그대로: `delta(root) <= 0 and delta(child) <= 0`.
  이 설계가 root와 child를 **서로 다른 등급의 서로 다른 토큰으로** 분리하므로,
  "root를 올린 대가로 child를 떨어뜨리는" 73번의 실패 모드가 구조적으로 재현되지 않는다.

**단 이것은 설계 예측이지 측정이 아니다.** 73번도 같은 자리에서 예측이 반증됐다.
승인 시 p02 개발 게이트를 최우선 검증 항목으로 둔다(§8.2).

### 7.2 01 eval C2~C4 미검출 케이스

| # | 질의 | 정답 | 판정 | 근거 |
|---|---|---|---|---|
| q04 | 고객 새로 등록하고 싶어 | `POST /v1/customers` | **미해결** | 한글 전용 질의. lexeme이 전부 한글이라 영문 OpenAPI 청크와 교집합 0. 구조 신호는 영문이므로 무관 |
| q05 | 결제 환불 처리해줘 | `POST /v1/refunds` | **미해결** | 동일 |
| q06 | 이슈 새로 만들기 | `POST /repos/{owner}/{repo}/issues` | **미해결** | 동일 |
| q07 | 저장소 삭제해줘 | `DELETE /repos/{owner}/{repo}` | **미해결** | 동일 |
| q08 | cancel my recurring payment | `DELETE /v1/subscriptions/{...}` | **개선 기대** | summary `Cancel a subscription`의 `cancel`이 D(설명 범람)에서 **B**로 승격. `recurring`·`payment`는 여전히 D |
| q09 | shut down a repository | `DELETE /repos/{owner}/{repo}` | **부분** | A `repos repo`, B summary `repository` 확보. 그러나 `shut down` -> delete 매핑은 없음(§4.4 alias는 `delete remove destroy`). root/child 분리는 되지만 동사 매칭은 안 됨 |
| q10 | show my billing history | `GET /v1/invoices` | **미해결** | A `invoices invoice`, B alias `list index all browse` + summary `List all invoices`. 질의의 `show`·`billing`·`history` 어디에도 안 걸림. GET-collection alias에 `show`를 넣으면 잡히지만 §4.4가 금지 |
| q11 | customer | `GET /v1/customers` | **부분 해결** | A에서 `customer`(단수화)가 걸린다. 다만 `GET /v1/customers/{customer}`도 A에 `customer`를 가져 동률. tie-break가 B/D로 내려가 확정적이지 않음 |
| q12 | pull request | `GET /repos/{owner}/{repo}/pulls` | **부분 해결** | A에서 `pull`(단수화)이 걸린다. summary `List pull requests`의 `requests`는 `simple` config라 질의 `request`와 별개 lexeme이라 B 기여 없음 |

**정직한 결론.** 이 트랙은 C2(한글 패러프레이즈) 4건을 keyword arm 단독으로 풀지 못한다.
한글 질의에 대한 lexical 경로의 유일한 다리는 클라이언트가 넘기는 `query_variants`이고,
그 다리는 이미 존재한다(벡터 arm + FTS 후보 확장). 이 설계의 C2 기여는
**"variant term이 형제 홍수 대신 정답에 착지하게 만드는 것"**이며, 그것이 정확히
p02의 형태다. C2 미검출을 직접 없애는 것이 아니다.

C4는 부분 해결(단수/복수 굴절 다리 확보), C3는 1건 개선 기대·1건 부분·1건 미해결이다.
`GET /v1/customers` 대 `GET /v1/customers/{customer}` 같은 collection/item 동률은
path specificity 신호가 있어야 확정적으로 갈리는데, 그 신호는 이번 범위 밖이다(§11).

## 8. 평가 프로토콜

### 8.1 단일 인덱스 이중 컬럼 비교

이번 후보는 색인을 바꾸므로 종래의 "하나의 shared index 위에서 코드만 바꿔 비교"가
성립하지 않을 것처럼 보인다. 그러나 이 설계에서는 성립한다.

- 인덱스는 **하나만** 만든다(신규 컬럼 포함).
- baseline 실행: `lexical_field="text"` -> `text_tsv` + 무가중 `ts_rank`.
  이는 현행 production(`ecc3e792` + revert 완료 상태)의 lexical 동작과 동일하다.
- candidate 실행: `lexical_field="structured"` -> `search_tsv` + 가중 `ts_rank`.
- 두 실행이 같은 행·같은 `text`·같은 `embedding`을 본다. 벡터 arm은 비트 단위 동일하다.

따라서 §69 6.2의 "동일 evaluator로 두 구현 비교" 요구와 04·05번 eval의 shared-index
지문 프로토콜을 그대로 유지할 수 있다. 지문은 인덱스 1개에 대해 1회 기록한다.

### 8.2 실행 순서 (verdict 74 §6 승계)

1. **p02 개발 회귀 게이트 우선.** OFF/ON 각각 root·child capped rank를 baseline과 비교.
   `delta(root) <= 0 and delta(child) <= 0`을 만족하지 못하면 그 자리에서 중단하고
   aggregate 지표를 근거로 진행하지 않는다.
2. p02 통과 시 **v1 exposed regression** 전량 실행 — §69 7.1 HARD 전 항목, split별
   route-pair 산식 포함. v1은 노출된 개발 코퍼스이므로 최종 승급 근거가 아니다.
3. HARD 통과 후에만 aggregate·EFFECTIVENESS를 읽는다.
4. **v2 프리즈 저작은 1~3 통과 후에만 착수한다.** sealed holdout은 전량 신규
   endpoint/query pair(§69 5.x 분포, root/child guard 유지).

### 8.3 추가 HARD 불변식 (이 후보 전용)

| 항목 | PASS |
|---|---|
| lexeme 상위집합 | 모든 endpoint 청크에서 `text_tsv`의 lexeme이 전부 `search_tsv`에 존재 (후보 집합 축소 불가) |
| 벡터 arm 불변 | 마이그레이션·백필 전후 `chunk.text`·`chunk.embedding`의 정렬 해시 동일 |
| 파생 결정성 | 백필 산출 == 재색인 산출 (문자열 동일) |
| 문서 검색 무변경 | `chunk_type="section"` 경로의 capped rank가 baseline과 완전 동일 |
| exact control | `_search_exact` 경로 결과 무변경(C1 top-10 loss 0) |

### 8.4 단위 테스트 계약

- `derive_endpoint_structure` 골든 픽스처: `/repos/{owner}/{repo}`, `/repos/{owner}/{repo}/topics`,
  `/v1/customers`, `/v1/customers/{customer}`, `/v1/checkout/sessions`,
  `/v1/subscriptions/{subscription_exposed_id}`, `/repos/{owner}/{repo}/pulls`,
  버전 세그먼트 없는 path, param만 있는 path, 빈 path
- 단수화 경계: `address`(불변), `categories -> category`, `boxes -> box`, `topics -> topic`,
  `repos -> repo`, 길이 2 이하 토큰 불변
- subword: `line_items`, `payment_intents`, camelCase, 하이픈
- alias 표 전수: method × shape 조합
- `TEXT_TSV_EXPRESSION` 리터럴 고정 테스트
- `search_tsv` NULL 여부: `chunk_type` 별
- write-back(`refresh_endpoint_chunk`) 후 A/B/C 컬럼 보존
- `lexical_field="text"`일 때 생성되는 SQL이 현행과 동일

## 9. ADR 판단

### 9.1 ADR-0003은 개정 대상이 아니다

verdict 74 §5는 이 트랙이 "ADR-0003 read-only 범위를 벗어난다"고 썼다. **이 서술은
부정확하며 이 문서로 정정한다.**

ADR-0003의 결정 내용은 "MCP 도구는 OpenAPI 문서의 검색·상세 조회만 제공하고 실제 API
호출(Execute)은 범위에서 제외한다"이다. 이 설계는

- 상류 API를 호출하지 않는다
- MCP 도구 표면(`search_endpoints`, `get_endpoint_details` 등)을 바꾸지 않는다
- 새로운 쓰기 도구를 등록하지 않는다

색인 시점에 자체 DB에 파생 컬럼을 쓰는 것은 ADR-0001이 이미 채택한 저장형 구조가
매 색인마다 하는 일이다. **ADR-0003 개정 불필요.**

### 9.2 실제로 걸리는 결정은 ADR-0002

ADR-0002는 "tsvector full-text search + 벡터 유사도 하이브리드"를 채택했고, 후속 영향에
"Keyword Search는 아직 tsvector가 아닌 애플리케이션 레벨 토큰 매칭 유지(TODO)"까지
기록돼 있다. 이 설계는 keyword arm의 lexical 표현을 **단일 필드 `to_tsvector('simple', text)`
에서 가중 다중 필드로** 바꾼다. ADR-0002의 결정 자체를 뒤집지는 않지만, 그 결정의
구체적 실현 방식을 바꾸는 새 결정이다.

**권고: 신규 ADR-0005 저작 + ADR-0002에 후속 영향 1줄 추가.**

신규 ADR을 권고하는 이유는 다음 세 가지가 ADR-0002의 후속 영향 한 줄에 들어가지 않기
때문이다.

1. 가중 필드 배치(A/B/C/D)와 그 근거
2. operation alias 표의 동결 및 과적합 방지 규칙 — 이것은 "결정"이지 구현 세부가 아니다
3. 롤백 계약(`search_lexical_field` 스위치, `text_tsv` 존치)

ADR-0005 초안 제목: **"엔드포인트 lexical 표현의 색인 시점 구조화와 가중 tsvector 채택"**.
본문 골자는 §2(컨텍스트) / §3.2·§4(결정) / §5·§11(결과와 비범위)다.
ADR-0001은 정합, ADR-0004(local embedding)는 임베딩 불변이므로 무관.

## 10. lead 승인 지점

| ID | 결정 | architect 권고 |
|---|---|---|
| **D1** | 방식 택일: weighted tsvector vs explicit chunk text | **weighted tsvector**. explicit chunk text는 verdict 74가 반증한 density 실패 모드를 재현하고 전량 재임베딩까지 요구한다(§3) |
| **D2** | §4.4 operation alias 표를 이 문서에서 동결. 이후 항목 추가는 새 verdict 필요 | **동결 승인**. 게이트 실패 질의를 보고 표를 늘리는 것이 verdict 74가 반려한 (b)(c)와 같은 오류다 |
| **D3** | alias 토큰의 가중 등급: A(leaf와 동급) vs B(summary와 동급) | **B**. alias는 코퍼스 절반이 공유하는 저판별 클래스 토큰이라 A에 두면 희소한 leaf 명사를 눌러 density 역전을 재생산한다 |
| **D4** | `ts_rank` 가중치를 Postgres 기본값 `{0.1,0.2,0.4,1.0}`으로 고정, 게이트 결과 기반 튜닝 금지 | **고정 승인**. 튜닝 허용 시 D2와 같은 과적합 경로가 열린다 |
| **D5** | 기존 색인 반영: 백필 스크립트 vs 전체 재색인 | **백필**. 재임베딩 0, verdict 70의 endpoint id 재해시 비결정성 회피 |
| **D6** | ADR 처리: 신규 ADR-0005 + ADR-0002 후속 영향 추가, ADR-0003 미개정 | **권고대로**. verdict 74 §5의 ADR-0003 서술은 §9.1로 정정 |
| **D7** | path specificity(collection/item 특이도) 신호를 이번 범위에 포함할지 | **제외**. C4 동률(q11)이 게이트에서 실패로 확인되면 그때 별도 설계. 지금 넣으면 68번에서 되돌린 route-family rerank를 근거 없이 재도입하는 것이 된다 |

D1·D2·D3가 갈리면 설계 본문이 바뀐다. D4~D7은 승인 없이도 문서대로 진행 가능하나
명시적 확인을 권한다.

## 11. 비범위 (YAGNI)

- **약어 사전** (`repos -> repository`, `orgs -> organization`, `pulls -> pull request`).
  결정적으로 유도할 수 없는 손수 저작 도메인 어휘이고, 드리프트 지점이 된다.
- **path specificity / route-family rerank 재도입** (D7).
- **구조 텍스트의 임베딩 반영.** 벡터 arm 불변이 이 후보의 귀속 가능성을 만든다.
  구조 텍스트를 임베딩에 넣는 것은 별도 후보로 분리한다.
- **`ts_rank_cd` 전환**, `RRF_K` 조정, RRF arm 가중치 도입.
- **`text_tsv` 통합·삭제.** 승급이 확정되고 문서 검색 경로까지 옮길 근거가 생긴 뒤의 정리 작업.
- **한글 alias / 한글 구조 신호.** C2를 직접 풀려면 필요하지만, OpenAPI 원문에 한글 원천이
  없어 결정적 생성 계약을 위반한다. 클라이언트 variants가 담당한다.
- **`$ref` 해소를 통한 body 필드 확장** (30번 §9.2 결정 유지).

## 12. 판정표

| 항목 | 판정 |
|---|---|
| 방식 | **weighted tsvector 채택, explicit chunk text 반려** |
| 재임베딩 | **불필요** (`text`·`embedding` 불변) |
| 마이그레이션 | revision 1개 (컬럼 3 + generated 1 + 부분 GIN 1) |
| 재색인 | 백필 스크립트 (임베딩 호출 0) |
| 벡터 arm | **무변경** |
| 문서(section) 검색 | **무변경** (`text_tsv` 존치) |
| 롤백 | 설정 스위치 `search_lexical_field` |
| ADR-0003 | **개정 불필요** — verdict 74 §5 서술 정정 |
| ADR-0002 | 후속 영향 1줄 추가 |
| 신규 ADR-0005 | **필요** (초안 골자 §9.2) |
| p02 | root/child가 서로 다른 등급의 토큰으로 분리 — 해결 기대, **측정 전 예측** |
| 01 eval C2 4건 | **keyword arm 단독 미해결** — variants 착지 개선으로만 기여 |
| 01 eval C3 3건 | q08 개선 기대 / q09 부분 / q10 미해결 |
| 01 eval C4 2건 | 둘 다 부분 해결 (단수/복수 다리 확보, collection/item 동률 잔존) |
| 구현 | **이 문서로 착수하지 않음.** lead 승인 후 별도 구현 계획 산출물 |

이 설계의 가치는 미검출 케이스를 몇 건 없앤다는 데 있지 않다. verdict 74가 확인한
"target token과 context token을 구분할 정보가 색인에 없다"는 결함을, search-time 규칙을
더 얹지 않고 색인 표현에서 직접 제거한다는 데 있다. §7.2를 낙관적으로 쓰지 않은 것은
73번이 예측을 근거로 승급을 시도해 실패한 전례 때문이다.
