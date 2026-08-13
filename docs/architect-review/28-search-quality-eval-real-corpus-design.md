# 28. 검색 품질 평가 설계 — 실 코퍼스 기반 질의셋 + recall@k / MRR 방법론

- 상태: 설계 확정 + 코퍼스/질의 리터럴 확정(구현은 developer)
- 관련: `tests/fixtures/rrf_eval/`(기존 synthetic 하네스), `07-search-rrf-reevaluation.md`, `09-search-quality-post-rrf.md`, `26-pdf-docx-...`(번들 검토 대상)
- 선행 결정: 사용자가 **실 코퍼스 = 공개 API 문서(Stripe / GitHub OpenAPI spec)** 로 확정.

## 1. 배경과 현황

### 1.1 이미 있는 것 (재사용)

- `tests/fixtures/rrf_eval/metrics.py` — `reciprocal_rank` / `dcg_at` / `recall_at` 순수 함수. **그대로 재사용. 지표 코드 신규 작성 금지.**
- `tests/fixtures/rrf_eval/compare_strategies.py` — 임시 DB 생성 → 문서 등록 → 질의 실행 → 정답 순위 → Recall@k/MRR/nDCG 요약. **드라이버 골격 그대로 포팅.** 정답이 이미 `(method, path)` 단위라 이번 코퍼스와 정답 단위가 일치한다(§3).
- `queries.json` 스키마.

### 1.2 기존 하네스의 한계 (이번 작업이 바꾸는 것)

- 코퍼스가 **손으로 만든 synthetic `openapi.json`(엔드포인트 ~20개)** → 실제 운영 문서의 규모·분포·노이즈 미반영. 20개짜리 토이 코퍼스에선 Recall@k가 쉽게 포화된다.
- 이번엔 **실제 공개 API 스펙(수백~수천 엔드포인트)** 으로 교체 → 노이즈·decoy 밀도가 현실적이 되어 지표가 변별력을 갖는다.

### 1.3 검색면이 둘이라는 사실 (반드시 인지 — 번들 판단의 근거)

| 검색면 | 서비스 | 대상 데이터 | 검색 필터 | 반환 식별자 |
|---|---|---|---|---|
| **A. 엔드포인트/API 검색** | `endpoint_candidate_search`(vector+keyword+RRF) | `chunk` 테이블 | **`chunk_type == "endpoint"`** (`chunk_repository.search_by_vector:291`, `search_endpoint_by_text:184`) | `chunk_id` + `ref_id` + (method,path) |
| **B. 협업 문서 검색** | `document_search_service` | `document_meta` 캐시 + 라이브 fetch | — | title / url / snippet (chunk ID 없음) |

**결정적 사실**: 벡터/키워드 검색은 `chunk_type == "endpoint"` 만 반환한다. Markdown/CSV/**PDF/DOCX**는 `chunk_type == "section"` 청크를 만들지만(`chunk_builder.py:133`) **검색 필터가 이들을 제외한다 → 현재 벡터 검색으로 조회 불가.** 즉 **이 프로젝트의 검색 품질 평가가 성립하는 유일한 면 = A(엔드포인트) = OpenAPI 문서.** → 코퍼스가 공개 API 스펙이어야 하는 건 선택이 아니라 구조적 귀결이다.

> 파생 발견(§7.3, lead 확인 요망): section 청크는 **빌드·임베딩까지 되지만 어떤 검색 경로에서도 반환되지 않는다.** doc/23 sub-chunking(섹션 전용)은 현재 **검색에 걸리는 게 없어 실질 효과 0** — section 검색 배선(doc/24 parent-child)이 열리기 전까지는 미측정 자산이다.

## 2. 목표 / 비목표

**목표**
1. 실제 공개 API 스펙 코퍼스에 대해 재현 가능한 검색 품질 측정(Recall@k, MRR, nDCG@10).
2. 청킹 전략이 바뀌어도 안 깨지는 안정적 ground truth 단위 정의.
3. developer가 그대로 구현할 리터럴 코퍼스 + 20질의 + 태깅 절차 + 스크립트 계약.

**비목표**
- 검색면 B(협업 문서), section 청크 검색 평가 — 검색 자체가 미배선이라 이번 범위 밖(§7.3).
- CI 상시 실행 — 임베딩 로딩·대형 스펙 색인이 무거워 **수동 회귀 재실행** 도구.
- graded relevance — 단일-관련(binary) 근사 유지(기존 `dcg_at` 전제).

## 3. Ground truth 매핑 기준

### 3.1 정답 단위 = 엔드포인트 `(method, path)` (≡ ref_id)

- 검색 반환 단위가 엔드포인트 청크이고, 코퍼스가 문서 2건뿐이라 **document 단위 채점은 무의미**(recall 자명 포화). → 정답은 **엔드포인트 식별자** 단위.
- **청킹 불변성**: 엔드포인트는 sub-chunking 대상이 아니다(sub-chunking은 섹션 전용, `chunk_builder.py`). 엔드포인트 = 정확히 청크 1개, `ref_id = endpoint_id` 고정. → `(method, path)` 라벨은 재청킹·재색인에도 안 깨진다. `chunk_id`는 라벨에 절대 기록하지 않는다(생성 id, 불안정).
- 문서 2건 이상이라 동일 path 충돌 대비 `document` 키를 병기(Stripe는 `/v1/...` 프리픽스라 GitHub와 충돌 거의 없음).

> 참고: doc/28 초안의 "document 1차 / section locator 2차"는 **다-문서 markdown 코퍼스**를 위한 설계였다. 실 코퍼스가 대형 OpenAPI 2건으로 확정되며 정답 단위는 기존 하네스와 동일한 `(method, path)`로 수렴 — 재사용이 오히려 커졌다.

### 3.2 정답 라벨 스키마 (`queries.json`)

```json
{
  "id": "q08",
  "query": "cancel my recurring payment",
  "category": "C3-영문의역",
  "accepted": [
    {"doc": "stripe", "method": "DELETE", "path": "/v1/subscriptions/{subscription_exposed_id}"}
  ]
}
```

- 복수 정답 허용(C6). 하나라도 top-k에 들면 recall hit, 최상위 정답 순위로 MRR.
- `doc` = §4 매니페스트의 소스 키(`stripe`|`github`). developer가 등록 후 실제 `document_id`로 바인딩.

### 3.3 라벨 검증 게이트 (기존 `_validate_labels` 확장, 필수)

스크립트 초입에서 모든 `accepted`의 `(method, path)`가 **프리즈된 스펙에 실재**하는지 확인, 오타/추정 라벨은 즉시 에러로 죽인다(조용한 rank=None 집계 방지). **아래 §6 리터럴 라벨은 architect가 저작한 best-known 값 — Stripe의 파라미터명(예: `{subscription_exposed_id}`) 등 정확 표기는 developer가 이 게이트로 검증·확정한다.**

## 4. 코퍼스 (리터럴 확정)

공개 API OpenAPI 스펙 2건. 도메인이 달라(결제 vs 개발자 플랫폼) 교차 decoy가 현실적이다.

| 소스 키 | 문서 | doc_type | 프리즈 URL(원문) | 비고 |
|---|---|---|---|---|
| `stripe` | Stripe API | `openapi` | `https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json` | 결제/고객/구독/환불. 대형(~수백 op) |
| `github` | GitHub REST API | `openapi` | `https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json` | repo/issue/PR/user/org. 초대형 |

**프리즈 절차(재현성 필수)**
- 위 원문을 **특정 commit SHA로 핀**해 받아 `tests/fixtures/corpus_eval/`에 스냅샷 커밋(라이브 fetch 금지 — 스펙이 바뀌면 재현·라벨 무효). `.env`·비밀 미포함.
- `corpus_manifest.json`에 `{source_key, doc_type, frozen_url, commit_sha, content_sha256, document_id}` 기록. `document_id`는 등록 후 developer가 채워 고정(재색인에도 라벨 유효하도록).
- 크기 주의: 두 스펙 모두 수 MB. 수동 회귀 도구라 허용하나, 색인 시간이 부담이면 **핀한 SHA에서 태그/도메인 서브셋으로 축소**하는 것도 가능(단 서브셋도 프리즈·해시 고정). 1차는 전체로 간다.

## 5. 태깅 절차

1. 프리즈 스펙을 임시 DB에 색인(§7 드라이버).
2. §6 리터럴 질의·정답을 로드, §3.3 게이트로 `(method, path)` 실재 검증. 실패 라벨은 developer가 스펙 확인해 정정.
3. `accepted`의 `doc` 키를 실제 `document_id`로 바인딩.
4. 채점: `_rank_of_answer`가 반환 후보에서 `(method, path)` 최초 일치 1-based 순위 산출(기존 로직 그대로).

## 6. 질의셋 20개 (리터럴 확정)

카테고리 배분 고정(총 20). 질의문은 확정, `(method, path)`는 best-known(§3.3 게이트로 확정).

| id | 카테고리 | query | 정답 doc / method / path |
|---|---|---|---|
| q01 | C1-직접키워드 | `POST /v1/customers` | stripe · POST · /v1/customers |
| q02 | C1-직접키워드 | `GET /repos/{owner}/{repo}` | github · GET · /repos/{owner}/{repo} |
| q03 | C1-직접키워드 | `create a checkout session` | stripe · POST · /v1/checkout/sessions |
| q04 | C2-한글패러프레이즈 | `고객 새로 등록하고 싶어` | stripe · POST · /v1/customers |
| q05 | C2-한글패러프레이즈 | `결제 환불 처리해줘` | stripe · POST · /v1/refunds |
| q06 | C2-한글패러프레이즈 | `이슈 새로 만들기` | github · POST · /repos/{owner}/{repo}/issues |
| q07 | C2-한글패러프레이즈 | `저장소 삭제해줘` | github · DELETE · /repos/{owner}/{repo} |
| q08 | C3-영문의역 | `cancel my recurring payment` | stripe · DELETE · /v1/subscriptions/{subscription_exposed_id} |
| q09 | C3-영문의역 | `shut down a repository` | github · DELETE · /repos/{owner}/{repo} |
| q10 | C3-영문의역 | `show my billing history` | stripe · GET · /v1/invoices |
| q11 | C4-흔한토큰범람 | `customer` | stripe · GET · /v1/customers |
| q12 | C4-흔한토큰범람 | `pull request` | github · GET · /repos/{owner}/{repo}/pulls |
| q13 | C5-decoy구분 | `delete a subscription` | stripe · DELETE · /v1/subscriptions/{subscription_exposed_id} |
| q14 | C5-decoy구분 | `get user information` | github · GET · /users/{username} |
| q15 | C5-decoy구분 | `list commits of a repo` | github · GET · /repos/{owner}/{repo}/commits |
| q16 | C6-다개념(복수정답) | `구독 취소하고 환불까지` | stripe · DELETE · /v1/subscriptions/{subscription_exposed_id} **and** stripe · POST · /v1/refunds |
| q17 | C6-다개념(복수정답) | `이슈 목록 조회하고 새 이슈 생성` | github · GET · /repos/{owner}/{repo}/issues **and** github · POST · /repos/{owner}/{repo}/issues |
| q18 | C7-대형엔드포인트세부 | `결제 생성 시 통화 단위 지정` | stripe · POST · /v1/charges |
| q19 | C7-대형엔드포인트세부 | `결제 인텐트에 자동 결제수단 설정` | stripe · POST · /v1/payment_intents |
| q20 | C7-대형엔드포인트세부 | `풀리퀘스트를 draft로 생성` | github · POST · /repos/{owner}/{repo}/pulls |

**카테고리 의도**
- C1(3): baseline sanity(경로/명칭 정확 일치).
- C2(4): 한글 질의 → 영문 문서 **교차언어 recall**(multilingual-e5 핵심 검증).
- C3(3): 영문 동의어/의역(질의 어휘 ≠ 문서 어휘, 예 "recurring payment"↔"subscription").
- C4(2): 흔한 bare word 노이즈 범람 견딤.
- C5(3): 유사 개념 decoy 변별(두 스펙 교차 — "delete/user/list"가 양쪽에 산재).
- C6(2): 복수 정답 다개념 질의.
- C7(3): **대형 엔드포인트 세부 파라미터 타겟.** Stripe `charges`/`payment_intents`처럼 파라미터 문서가 방대해 엔드포인트 청크가 임베딩 예산(512)을 넘는 경우, 깊이 묻힌 세부(통화·자동결제수단·draft)를 질의해도 그 엔드포인트가 잡히는지 확인 — 엔드포인트는 sub-chunking되지 않으므로 **truncation 무방비 구간**이다(doc/26 truncation 논지가 엔드포인트에도 적용됨을 측정으로 노출). 정답 단위는 여전히 엔드포인트 ref_id.

> C7이 초안의 "sub-chunk 내부 타겟"에서 바뀐 이유: sub-chunking은 섹션 전용이고 섹션은 검색 불가(§1.3). 엔드포인트 코퍼스에서 sub-chunking은 측정 대상이 아니다. 대신 "대형 단일 엔드포인트 청크의 truncation 노출"로 재정의 — 측정 가능하고 doc/26 truncation 테마와 직접 연결된다.

## 7. 재현 스크립트 인터페이스 스펙

`compare_strategies.py`를 실 코퍼스용으로 포팅한 `tests/fixtures/corpus_eval/run_corpus_eval.py`.

**실행**
```bash
docker compose up -d postgres
uv run python tests/fixtures/corpus_eval/run_corpus_eval.py [--strategy rrf|fallback|both] [--top-k 10]
```

**계약**
- 입력: `corpus_manifest.json`(§4), `queries.json`(§3.2/§6). 같은 디렉터리 로드.
- 임시 DB 생성 → vector/pg_trgm 확장 → `create_all` → 매니페스트 스펙 색인 → 종료 시 임시 DB drop(`_make_temp_db`/`_drop_temp_db` 그대로).
- 상수 `TOP_K = 10`, `RECALL_KS = (1,3,5,10)`(기존값 유지).
- 각 질의: 검색면 A로 top-k 조회 → `_rank_of_answer`로 `(method,path)` 최초 일치 순위(없으면 None). 지표는 `metrics.py` 재사용, `_summarize` 골격 그대로.
- 출력(stdout markdown): 질의별 순위 표 + 지표 요약 + 카테고리별 분해(Recall@3/MRR) + 회귀 목록(기존 포맷).
- 결정성: 임베딩 `intfloat/multilingual-e5-small`(dim 384) 고정, `content_sha256` 검증, `is_semantic` 첫 줄 출력(fake-embedding 오실행 구분).

### 7.1 재사용 원칙(ponytail)
`_make_temp_db`/`_drop_temp_db`/`_rank_of_answer`/`_summarize`/`_format_summary_line`은 `compare_strategies.py`에서 그대로 가져온다. 신규 코드는 (1) 매니페스트 로더(URL/SHA 프리즈 스냅샷 색인) (2) `_validate_labels`의 다-문서 버전뿐. metric·DB·순위·요약 재작성 금지.

### 7.2 검색면 B(협업 문서) — 트랙 분리(범위 밖)
정답 단위가 url뿐이라 청크 지표 불성립. 별도 설계 사안.

### 7.3 파생 발견 (lead 확인 요망)
`chunk_type == "section"` 청크는 빌드·임베딩되나 어떤 검색 경로에서도 반환되지 않는다(vector/keyword 모두 endpoint 필터). → markdown/pdf/docx 본문은 현재 **검색 대상이 아니다.** doc/23 sub-chunking은 검색에 걸리는 게 없어 실효 0. **의도된 미배선인지(doc/24 parent-child 검색 배선 대기), 아니면 누락인지 확인 필요.** 이번 평가 설계와 별개 트랙이나 검색 커버리지의 큰 공백이라 명시.

## 8. doc/26 번들 검토 — 결론: **분리(번들하지 않음)**

사용자 요청: 공개 API 문서 중 multi-page PDF/DOCX가 있으면 코퍼스에 넣어 doc/28 질의 다양성 + doc/26 게이트 확증을 한 번에.

**판정: 분리한다.** 근거(구조적, 취향 아님):

1. **검색면 불일치.** doc/28 평가면 A는 `chunk_type=="endpoint"`만 반환. PDF/DOCX는 `section` 청크만 만들고 **검색에서 제외**(§1.3). → 코퍼스에 PDF를 넣어도 doc/28의 Recall/MRR에 **기여 0**(조회 자체가 안 됨). "질의 다양성 확보" 효과가 원리적으로 없다.
2. **측정 종류가 다르다.** doc/26 확증은 *검색 품질*이 아니라 *섹션 길이 truncation* 진단이다 — 긴 PDF 1건 등록 후 `app/scripts/diagnose_long_sections.py`(Phase0 재실행)로 섹션 토큰 길이가 결정론적으로 512 초과함을 보이는 색인단 진단. 질의셋·정답·recall이 필요 없다.
3. **결합이 제약만 는다.** doc/26은 "공개 API 문서"일 필요가 전혀 없다 — 아무 실무급 다중페이지 PDF면 된다. 공개 API 스펙을 굳이 PDF로 구하려는 건(Stripe/GitHub는 canonical PDF 스펙을 배포하지도 않음) 이득 없는 제약이다.

**따라서**: doc/28 코퍼스 = Stripe/GitHub OpenAPI(§4)로 확정. doc/26 cheap 확증은 **독립 트랙**으로 진행 — developer가 임의의 실무급 다중페이지 PDF 1건 등록 + `diagnose_long_sections.py` 재실행. 두 작업은 산출물·측정·정답체계가 겹치지 않는다.

## 9. 열린 의존성 / lead 확인

1. 코퍼스·질의 리터럴 확정(§4·§6) — 완료. `document_id`·정확 path 표기만 developer가 등록/게이트로 바인딩.
2. §3.1 정답 단위 = `(method, path)` 채택에 이견 없는지(초안 document-1차에서 코퍼스 확정에 따라 수렴).
3. **§7.3 발견**: section 청크 미검색이 의도인지 — doc/24 parent-child 검색 배선과 함께 판단할 사안.
4. doc/26 분리(§8)에 이견 없는지.

## 10. developer 착수 순서

1. §4 두 스펙을 핀 SHA로 받아 `tests/fixtures/corpus_eval/`에 프리즈 커밋 + `corpus_manifest.json` 작성.
2. `compare_strategies.py`에서 재사용 함수 포팅해 `run_corpus_eval.py` 구현(§7 계약, 다-문서 `_validate_labels` 포함).
3. §6 20질의를 `queries.json`으로 저작(리터럴 제공됨). 게이트로 `(method,path)` 검증·정정, `document_id` 바인딩.
4. `--strategy both` 1회 측정, 결과를 후속 리뷰 문서(29번대)에 기록.
5. (독립) doc/26: 실무급 다중페이지 PDF 1건 등록 + `diagnose_long_sections.py` 재실행 → truncation 결정론 확증, 결과 doc/26 하단 또는 후속에 기록.
