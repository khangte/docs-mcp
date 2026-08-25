# 52. 비즈니스 메타데이터 설계안 검토 (외부 세션 docs-mcp-b6)

- 검토일: 2026-08-25
- 대상: 외부 세션이 제출한 '비개발자 자연어 OpenAPI 검색' 설계안 (신규 MCP 도구 0개, 청크 텍스트 개선 단일 갈래)
- 판정: **조건부 승인** — 제안 5건 중 2건 승인, 2건 보완, 1건 범위 밖으로 연기

## 요약 판정표

| # | 제안 | 판정 | 사유 |
|---|------|------|------|
| 1 | `ApiEndpoint` 에 컬럼 3개 추가 | **보완** | 재색인이 행을 삭제해 메타데이터가 소실된다. 별도 테이블로 옮겨야 한다 |
| 2 | `ParsedEndpoint` 에 대응 필드 추가 | **반려** | 파서가 채우지 않는 필드를 파서 출력 타입에 넣는 것은 타입 계약 위반. 기존 옵셔널 주입 패턴으로 대체 |
| 3 | `build_endpoint_chunk_text` 수정 | **승인** | 배치 순서·fallback 유지 조건 충족 시 기존 설계 근거와 정합 |
| 4 | `generate_business_metadata` CLI 신규 | **승인(조건부)** | 파이프라인 분리는 옳다. LLM SDK 의존성 격리 조건 추가 |
| 5 | `get_endpoint_details` 에 metadata 노출 | **연기** | 현 단계에서 소비자가 없다(YAGNI). 1단계 범위에서 제외 |

---

## (a) description 완전 대체가 정보 손실 없다는 전제 — **성립. 단, 보고서가 짚지 못한 두 번째 경로가 있다**

### 상세 조회 경로: 손실 없음 (확인됨)

`get_endpoint_details` 는 청크 텍스트를 전혀 읽지 않는다.

- `app/services/endpoints/endpoint_details_service.py` 의 `EndpointDetailsResult.description` 은
  `ApiEndpoint.description` 컬럼에서 직접 온다.
- `app/mcp/types.py:105` `EndpointDetails` TypedDict 도 같은 값을 그대로 노출한다.

따라서 청크 텍스트에서 description 을 빼도 클라이언트 LLM 은 상세 조회에서 원문 description 을
그대로 받는다. **전제는 성립한다.**

문서 검색 스니펫도 무관하다 — `app/services/documents/snippet_generator.py` 의 스니펫은
`DocumentMeta` 본문에서 생성되며 엔드포인트 청크와 경로가 다르다.

### 조건 1: `ApiEndpoint.description` 컬럼은 유지한다

제안이 "description 원문을 대체"라고만 적혀 있어 컬럼 자체를 치환하는 것으로 읽힐 여지가 있다.
**대체 범위는 청크 텍스트(임베딩·키워드 검색 입력)에 한정한다.** DB 컬럼과 API 응답의
`description` 은 손대지 않는다. 이 조건이 깨지면 위 "손실 없음" 판단이 무효가 된다.

### 조건 2: 보고서가 누락한 경로 — 렉시컬 검색도 같이 잃는다

보고서는 임베딩 입력 관점에서만 description 제거를 논했다. 그러나 청크 텍스트는 두 곳에 쓰인다.

- 벡터 검색: 임베딩 입력
- 키워드 검색: `chunk.text_tsv` 생성 컬럼(마이그레이션 `a17165213545`)이
  `to_tsvector('simple', text)` 로 만들어진다 — `app/services/search/keyword_search.py:16`

즉 description 제거는 **하이브리드 검색의 렉시컬 갈래에서도 그 토큰들을 지운다.**
description 산문에만 등장하던 어휘(제품명, 도메인 용어)가 키워드 매칭을 담당하고 있었다면
그 경로가 먼저 죽는다. 임베딩 절단(1406토큰 잘림)과 달리 렉시컬 인덱스는 잘림 문제가 없었으므로,
description 제거는 렉시컬 쪽에는 순수 손실이다.

**따라서 A/B 측정은 하이브리드 최종 점수만이 아니라 벡터/키워드 두 갈래를 분리해 봐야 한다.**
전체 recall 이 유지돼도 렉시컬 갈래가 무너졌다면 다른 코퍼스에서 재발한다.

---

## (b) 스키마 변경 방식 — **직렬화 패턴은 일치. 컬럼 위치가 틀렸다 (치명적)**

### 일치하는 부분

`tags_json` 패턴(`app/models/openapi.py:53,66-77`)은 정확히 재사용 가능하다.

```python
tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

@property
def tags(self) -> list[str]:
    try:
        return list(json.loads(self.tags_json or "[]"))
    except (json.JSONDecodeError, TypeError):
        return []
```

`nullable=False` + 기본값 + 예외를 삼키는 `@property` 게터. `keywords_json` / `user_phrases_json`
은 이 형태를 그대로 따르면 된다.

### 치명적 결함: 재색인이 메타데이터를 전멸시킨다

`app/services/documents/document_body_indexer.py:101-105`:

```python
chunk_repo.delete_by_document(document_id)
for endpoint in list(endpoint_repo.list_by_document(document_id)):
    session.delete(endpoint)
```

재색인은 `ApiEndpoint` 행을 **전부 삭제하고 다시 만든다.** LLM 이 생성해 그 행에 써 둔
business metadata 는 재색인 한 번에 사라진다. 그리고 재색인은 예외 상황이 아니다 —
`app/scripts/refresh_documents.py` 배치가 주기적으로 돌고, 원본 해시가 바뀌면 매번 실행된다.
즉 "언젠가 문제가 될 수 있다"가 아니라 **정상 운영에서 확실히 일어난다.**

행 ID 로 복원하는 우회도 막혀 있다. `app/services/indexer/indexer_service.py:193-197`:

```python
key = f"{document_id}:{parsed.method}:{parsed.path}:{idx}"
```

`endpoint_id` 해시에 **엔드포인트 순서 인덱스 `idx` 가 들어간다.** OpenAPI 스펙에서 엔드포인트가
하나 추가/삭제돼 순서가 밀리면 같은 (method, path) 인데도 ID 가 통째로 바뀐다. ID 기준 재부착은
설계할 수 없다.

### 보완 지시: 별도 테이블로 분리한다

```
테이블: endpoint_business_metadata
키:     (document_id, method, path)   -- api_endpoint.id 에 FK 를 걸지 않는다
컬럼:   business_description  Text  NOT NULL DEFAULT ''
        keywords_json         Text  NOT NULL DEFAULT '[]'
        user_phrases_json     Text  NOT NULL DEFAULT '[]'
        generated_at          timestamptz
        model                 String  -- 어느 모델이 만든 값인지(재생성 판단용)
```

- `api_endpoint` 에 FK 를 걸지 않는 것이 핵심이다. 걸면 cascade 로 같이 지워져 문제가 그대로 남는다.
- (method, path) 는 재색인을 넘어 안정적인 유일한 키다 — `idx` 가 섞인 ID 와 달리 스펙 순서 변경에
  영향받지 않는다.
- `document` 삭제 시에는 같이 지워야 하므로 `document_id` 에는 FK + cascade 를 건다.
- `generated_at` / `model` 은 투기적 추가가 아니다. LLM 생성물은 재생성 대상이고
  (모델 교체, 프롬프트 개선), 무엇이 낡았는지 판단할 수단이 없으면 전량 재생성밖에 못 한다.

이 분리는 부수 효과로 원 제안의 약점 하나를 더 해소한다 — 스펙이 갱신돼도 메타데이터가 살아남으므로
문서 재색인마다 LLM 재호출 비용이 발생하지 않는다.

---

## (2) `ParsedEndpoint` 필드 추가 — **반려**

`ParsedEndpoint` 는 파서의 출력 계약이다. "파서는 안 채운다"는 필드를 여기 넣으면 타입이 거짓말을
하게 되고, 이후 파서 코드를 읽는 사람이 채우는 지점을 찾아 헤맨다.

이 코드베이스에는 이미 맞는 패턴이 있다 — `build_chunks` 의 옵셔널 주입
(`app/services/indexer/chunk_builder.py:99-107`):

```python
count_tokens: CountTokens | None = None,
token_limit: int = 480,
```

같은 방식으로 간다.

```python
def build_endpoint_chunk_text(
    endpoint: ParsedEndpoint,
    metadata: EndpointBusinessMetadata | None = None,
) -> str: ...

def build_chunks(
    document, endpoint_ids, ...,
    business_metadata: dict[tuple[str, str], EndpointBusinessMetadata] | None = None,
) -> list[BuiltChunk]: ...
```

`IndexerService.index_document` 가 `endpoint_business_metadata` 를 (method, path) 로 조회해 넘긴다.
`None` 이면 기존 동작과 완전히 동일하다 — 이것이 fallback 요구사항을 코드 구조 자체로 보장한다.

---

## (3) 청크 포맷 수정 — **승인. 배치 순서 확정**

현재 `build_endpoint_chunk_text` 의 docstring 이 이미 근거를 명시하고 있다
(`docs/architect-review/30` §11.2): SentenceTransformer 는 입력 꼬리를 자르므로
고신호 구조 필드를 저신호 free-text 앞에 둔다. 제안은 이 원칙의 연장선이며 정합한다.

확정 순서:

```
[METHOD] PATH — SUMMARY          (header, 선두 고정)
Keywords: k1, k2, k3             (신규, 고신호 — header 다음)
Phrases: p1; p2                  (신규)
OperationId: ...
Params: ...
Body: ...
Tags: ...
BUSINESS_DESCRIPTION or DESCRIPTION   (기존 description 자리)
Responses: ...
```

- Keywords/Phrases 를 header 직후에 두는 이유: 이 필드들이 자연어 질의와 직접 매칭되는 최고신호이고,
  절단 시 가장 먼저 살아남아야 한다.
- BusinessDesc 는 기존 description 자리(끝, Responses 앞)를 그대로 쓴다. 짧아진 산문이므로
  앞으로 당길 이유가 없고, 위치를 유지하면 A/B 에서 "산문 내용 교체" 효과만 분리해 볼 수 있다.
- fallback: 메타데이터가 없으면 기존 description 을 그대로 쓴다. 위 (2)의 옵셔널 주입 구조가
  이를 자동으로 보장한다.

---

## (c) CLI 를 색인 파이프라인과 분리 — **방향 승인. 의존성 격리 조건 추가**

### 분리 결정 자체는 옳다

색인은 결정적이고 빨라야 한다. LLM 호출은 느리고, 실패하고, 과금된다. 색인에 묶으면
문서 재색인 전체가 LLM 가용성에 종속된다. 분리가 맞다.

`mcp-delegate-reasoning-to-client-llm` 원칙의 예외 판단도 타당하다 — 그 원칙은 **런타임 질의 처리**를
겨냥한다(질의 확장을 서버가 별도 LLM 으로 재현하지 말 것). 색인 시점 1회성 전처리는 성격이 다르다.
클라이언트 LLM 은 색인 시점에 존재하지 않으므로 위임할 대상 자체가 없다.

### 조건: LLM SDK 가 서버 런타임에 들어오면 안 된다

현재 `pyproject.toml` 메인 dependencies 에 LLM SDK 가 하나도 없다. 이건 우연이 아니라 위 원칙의
물리적 표현이다. 이게 무너지면 원칙은 문서에만 남는다.

1. CLI 는 `app/scripts/` 에 둔다 — `refresh_documents.py`, `reembed.py`, `diagnose_long_sections.py`
   와 같은 자리다. 배치 CLI 의 기존 위치이며 새 관례를 만들 필요가 없다.
2. LLM SDK 는 메인 dependencies 가 아니라 optional extra 로 넣는다
   (`[project.optional-dependencies]` 에 `metadata` 등). 서버 배포 이미지에 들어가지 않게 한다.
3. `app/mcp/`, `app/services/search/`, `app/services/indexer/` 어디서도 LLM SDK 를 import 하지
   않는다. 이 경계는 리뷰에서 확인한다.

---

## (5) `get_endpoint_details` 메타데이터 노출 — **1단계 범위에서 제외**

현 시점에 소비자가 없다. `ApiEndpoint.description` 이 그대로 응답에 실리므로 클라이언트 LLM 은
이미 원문 설명을 받고 있다. `business_description` 은 **검색 매칭을 위한 임베딩 입력**이지
사람이나 클라이언트 LLM 이 읽을 응답 본문이 아니다. 둘을 함께 노출하면 같은 내용의 두 버전을
받게 되어 오히려 혼란스럽다.

검색 품질 개선이 측정으로 확인되고, 그 뒤 클라이언트가 실제로 이 필드를 필요로 한다는 근거가
생기면 그때 추가한다(YAGNI).

---

## 측정 계획 — **순서를 바꿔야 한다**

원 제안 순서: 스키마 → 파서 필드 → 기준선 → CLI → 청크 포맷 → 재색인/재측정

이 순서의 문제: **description 제거 효과와 business metadata 투입 효과가 한 번에 섞인다.**
결과가 나빠도 어느 쪽 탓인지 못 가른다. 그리고 스키마·CLI 작업(가장 무거운 단계)을 다 끝낸 뒤에야
"description 제거가 애초에 성립하는가"를 알게 된다 — 성립하지 않으면 그 작업 전부가 헛수고다.

### 수정 순서

**0단계 — 기준선 (변경 0)**
현재 상태로 `--with-variants` on/off 양쪽 측정. 카테고리별 recall@3 / MRR 기록.

**1단계 — description 제거 단독 A/B (가장 싸고, 가장 결정적)**
청크 텍스트에서 description 만 뺀다. 메타데이터 없음. 재색인 후 재측정.
- 벡터 갈래와 키워드 갈래를 분리해 본다 (위 (a) 조건 2).
- 관찰 지점: C5(decoy 구분) 3건, C7(대형 엔드포인트 세부) 3건.
- **이 단계가 게이트다.** C7 이 무너지면 "description 전면 대체" 전제 자체가 죽고,
  설계는 "description 을 줄이되 남긴다"로 바뀌어야 한다. 스키마 작업 전에 알아야 한다.
- 사전 가설(코드 근거): C7 정답 정보가 상당 부분 body 필드명에 있다 —
  q18 `currency`, q19 `automatic_payment_methods`, q20 `draft` 는 모두 request body 프로퍼티다.
  현재 `Body:` 줄이 이미 필드명을 싣고 있으므로 C7 은 살아남을 가능성이 있다. 가설일 뿐 측정으로 확인한다.

**2단계 — 스키마 마이그레이션 + 청크 빌더 옵셔널 주입**
`endpoint_business_metadata` 테이블 + `build_chunks` 파라미터. 이 시점에는 메타가 비어 있으므로
동작은 1단계와 동일해야 한다(회귀 확인용).

**3단계 — CLI 생성 및 메타데이터 적재**

**4단계 — 최종 A/B**
성공 기준(원 제안 그대로 승인): `--with-variants` **없이** 돌린 C2/C7 의 recall@3 + MRR 이
0단계의 variants 켠 값에 근접하는가.

하네스는 수정 불필요 — `tests/fixtures/corpus_eval/run_corpus_eval.py:121` 의 `--with-variants`
플래그와 카테고리별 breakdown 이 이미 있다. 확인함.

### 표본 크기 경고

`queries.json` 은 총 20건이고 C7 은 3건, C2 는 4건이다. C7 에서 한 건이 뒤집히면 recall@3 이
0.33 씩 움직인다. **이 표본으로 "근접했다"를 집계 수치만 보고 판정하면 노이즈를 신호로 읽는다.**
판정 시 어느 쿼리가 어떻게 뒤집혔는지 건별로 함께 본다. 집계값만으로는 승인하지 않는다.

---

## 미해결로 남기는 것

- 원 세션이 Docker/Postgres 부재로 기준선을 못 쟀다는 점은 우리 쪽에서 해소된다 — 0/1단계 측정은
  이 환경에서 수행한다.
- stripe description 의 HTML 태그(99.2%) 문제는 description 을 청크에서 빼면 자동 해소된다.
  1단계에서 description 을 남기는 결론이 나올 경우에만 별도 HTML strip 이 필요해진다.
  그 전에 미리 만들지 않는다.
