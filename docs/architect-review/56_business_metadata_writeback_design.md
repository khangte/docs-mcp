# 56. 비즈니스 메타데이터 재설계 — 호출 LLM write-back

- 설계일: 2026-08-25
- 대상: [55](55_business_metadata_stage3_cli_design.md) 에서 확정한 3단계(생성 CLI, `dd41b86`)의 대체 경로
- 폐기 사유: 설계 결함이 아니라 운영 제약 — 유료 API 크레딧 없이 운영해야 한다(T5 파일럿에서 크레딧 부족 400 확인)
- 전제: 2단계(`66a2d9a`) 산출물은 전부 유지한다 — `endpoint_business_metadata` 테이블, 청크 포맷,
  description HTML strip + 300자 절단
- 사용자와 합의된 트레이드오프: 콜드스타트(아무도 안 물어본 엔드포인트는 메타데이터 없음),
  비균일 커버리지(실사용 빈도에 비례)

---

## 0. 요약 — 무엇을 만들고 무엇을 버리는가

| 항목 | 결정 |
| --- | --- |
| 새 MCP 도구 | `submit_endpoint_metadata` 1개 신설(쓰기 전용, 엔드포인트 1건/호출) |
| 트리거 위치 | `get_endpoint_details` 응답에 `metadata_request` 키를 **없거나 낡았을 때만** 추가 |
| `search_endpoints` | 변경 없음 |
| 즉시 반영 | 해당 엔드포인트 청크 1건만 재조립 + 재임베딩(로컬 모델, 무료) |
| stage3 CLI | **유지(강등)** — LLM 호출부(`llm_client.py`/`generator.py`/`prompt.py`/CLI)는 그대로 두고, 순수 로직만 분리해 write-back 과 공유 |
| 스키마 변경 | **없음** — `source_hash`/`generated_at`/`model` 컬럼을 그대로 재사용 |

---

## 1. `mcp-delegate-reasoning-to-client-llm` 정합성 — stage3 CLI 는 지우지 않고 강등한다

### 1.1 원칙 관점에서 이번 방향이 맞다

[52](52_business_metadata_design_verdict.md) 에서 배치 사전생성을 고른 근거는 "색인 시점에는 위임할 호출 LLM 이
존재하지 않는다" 였다. write-back 은 생성 시점을 **질의 시점**으로 옮긴다. 질의 시점에는 위임 대상이 실재하므로
그 근거가 성립하지 않는다. 즉 이번 방향은 원칙을 우회하는 것이 아니라 원칙이 원래 가리키던 자리로 돌아오는 것이다.

동시에 원칙의 경계도 분명히 해 둔다. 서버는 **판단을 하지 않는다**(요약·키워드·표현 생성은 전부 호출 LLM 몫).
서버가 하는 일은 검증·정규화·저장·색인 반영뿐이다. 서버가 별도 LLM API 를 호출하는 경로는 이 설계에 없다.

### 1.2 그럼에도 CLI 를 삭제하지 않는 이유

삭제안과 유지안의 차이는 "지금 크레딧이 없다"가 아니라 "**균일한 커버리지를 강제할 수단이 있느냐**"다.
write-back 은 구조적으로 콜드스타트를 없애지 못한다. 크레딧이 생기는 순간 CLI 는 그 구멍을 한 번에 메우는
유일한 수단이 된다. 삭제 비용(코드 ~625줄 제거)과 유지 비용을 비교하면:

- 유지 비용은 거의 0 이다. `app/services/metadata/` 는 이미 `app/mcp`·검색·색인에서 import 불가하도록
  경계 테스트로 격리돼 있고(`tests/unit/test_metadata_boundary.py`), 실행하지 않으면 아무 비용도 없다.
- write-back 이 CLI 자산의 절반을 **그대로 재사용**한다 — 입력 payload 조립, `source_hash` 계산,
  출력 상한 검증/절단. 삭제하면 이 로직을 다시 쓰게 된다.
- 프롬프트 규칙(한/영 병기, summary 와 다른 동사 강제)은 55 에서 실측 근거(C7 한국어 질의 0%, q13 표현 갭)로
  얻은 자산이다. write-back 의 지시문은 이 규칙의 축약본이어야 하므로 `SYSTEM_PROMPT` 는 사양서로서 계속 산다.

**결정: 유지. 단 "기본 경로"에서 "옵션 운영 도구"로 강등한다.** README/모듈 docstring 에 "크레딧이 있을 때만
쓰는 백필 도구이며 기본 경로는 write-back" 을 명시한다. 삭제 판단 기준도 함께 못박는다 —
**2026-11-30 까지 한 번도 실행되지 않으면 그때 삭제한다**(코드는 `dd41b86` 에서 복원 가능).

### 1.3 패키지 재배치 (경계 테스트 수정 포함)

현재 경계 테스트는 `app/mcp`, `app/services/search`, `app/services/indexer` 가 `app.services.metadata` **패키지
전체**를 import 하지 못하게 막는다. 그 규칙의 진짜 이유는 "LLM 호출 비용·지연이 요청 경로로 새지 않게" 다.
write-back 서비스는 LLM 을 호출하지 않으므로 규칙을 모듈 단위로 좁힌다.

```
app/services/metadata/
  spec_payload.py     (신설) build_payload_json / compute_source_hash / METADATA_INSTRUCTION_VERSION
  validation.py       (신설) sanitize_and_clip — 상한·정규화. LLM 무관
  writeback_service.py(신설) 검증 → upsert → 청크 갱신. app/mcp 에서 import 허용
  prompt.py           SYSTEM_PROMPT 등 CLI 프롬프트만 남기고 payload/hash 는 spec_payload 재수출
  generator.py        CLI 전용(유지). _truncate_and_validate 는 validation.py 위임으로 교체
  llm_client.py       CLI 전용(유지)
```

경계 테스트 갱신:

- `app/services/search`, `app/services/indexer` → `app.services.metadata` 패키지 전체 금지(현행 유지).
- `app/mcp` → `llm_client` / `generator` / `prompt` 모듈만 금지. `spec_payload` / `validation` /
  `writeback_service` 는 허용.

`METADATA_INSTRUCTION_VERSION` 은 `PROMPT_VERSION` 의 개명이다(값 `"1"` 유지 — 파일럿이 400 으로 실패해
운영 데이터가 없으므로 무효화할 대상이 없다). **CLI 프롬프트 또는 write-back 지시문 중 하나라도 의미가 바뀌면
올린다.** 두 생산자가 버전을 공유하므로 한쪽 변경이 반대쪽 재생성도 유발하지만, 커버리지 규모를 감안하면
분리해서 얻는 이득보다 규칙이 하나인 편이 낫다.

---

## 2. MCP 도구 설계 — 신규 쓰기 도구 1개

### 2.1 기존 도구 확장이 아니라 신규 도구

`get_endpoint_details` 에 쓰기 파라미터를 얹는 안은 반려한다. (a) 읽기 도구가 부작용을 갖게 되고,
(b) 호출 LLM 이 "상세 조회"와 "메타데이터 기여"를 한 호출에 묶으면 아직 읽지도 않은 내용을 근거로 쓰게 되며,
(c) 쓰기 경로만 끄는 운영 스위치를 만들 수 없다.

### 2.2 시그니처

```python
@mcp.tool()
async def submit_endpoint_metadata(
    endpoint_id: str,
    business_description: str,
    keywords: list[str],
    user_phrases: list[str],
) -> MetadataSubmitResult | ErrorPayload
```

- **1 호출 = 1 엔드포인트.** 배치(list[dict])는 넣지 않는다 — 도구 스키마가 복잡해지면 호출 LLM 의 인자 조립
  실패율이 오르고, 실제 흐름은 `get_endpoint_details` 1건 뒤 1건 기여라 배치 이득이 없다(YAGNI).
- `overwrite` / `force` 같은 플래그를 노출하지 않는다 — 호출 LLM 이 덮어쓰기를 스스로 결정하게 두면
  오염 복구가 아니라 오염 증폭이 된다. 덮어쓰기 판단은 §4.3 규칙으로 서버가 한다.
- 반환:

```json
{"status": "stored | already_current | rejected",
 "endpoint_id": "...",
 "reindexed": true,
 "truncated": false,
 "reason": "..."}
```

`reason` 은 `status != "stored"` 일 때만 채운다. `reindexed=false` 는 저장은 됐지만 즉시 색인 반영에 실패했다는
뜻이다(다음 재색인 때 반영됨 — §3.3).

### 2.3 운영 스위치

`Settings.business_metadata_writeback_enabled: bool = True`
(`DOCS_MCP_METADATA_WRITEBACK_ENABLED`). 꺼지면 도구는 등록하되 `code="writeback_disabled"` 를 반환한다.
쓰기 경로를 LLM 에게 여는 설계이므로 코드 수정 없이 닫을 수단은 반드시 있어야 한다.

---

## 3. 트리거 로직 — `get_endpoint_details` 응답의 조건부 힌트

### 3.1 왜 `search_endpoints` 가 아니라 `get_endpoint_details` 인가

`search_endpoints` 결과에는 `method`/`path`/`summary` 밖에 없다. 그 상태에서 메타데이터를 쓰게 하면 근거가
빈약한 문장이 저장된다(원칙상 "판단은 클라 LLM" 이지만, **근거 없는 판단을 유도하는 것은 서버 설계 잘못**이다).
`get_endpoint_details` 는 파라미터·요청/응답 스펙까지 본 뒤라 근거가 충분하다.

부작용으로 커버리지 증가 속도는 "검색당 1건" 이 아니라 "상세조회당 1건" 이 된다. 이는 손실이 아니라
**실제로 쓰인 엔드포인트만 채워진다**는 뜻이고, write-back 방식이 원래 겨냥한 분포와 일치한다.

### 3.2 힌트 페이로드

`get_endpoint_details` 응답에 `metadata_request` 키를 **없거나 낡았을 때만** 추가한다(있으면 키 자체가 없어
토큰 오버헤드 0).

```json
"metadata_request": {
  "reason": "missing | stale",
  "instruction": "이 엔드포인트에는 검색용 비즈니스 메타데이터가 없다. 위 상세 정보를 근거로 다음을 만들어 submit_endpoint_metadata 로 보내면 이후 검색 품질이 개선된다. business_description: 한국어 1문장 최대 120자. user_phrases: 한국어 2 + 영어 2, 각 최대 40자, summary 의 동사와 다른 표현을 최소 1개 포함(cancel<->delete/remove, create<->add/register, list<->get all/fetch). keywords: 영/한 혼합 최대 5개, 각 최대 30자. 상세에 없는 사실은 만들지 않는다."
}
```

`instruction` 문안은 `SYSTEM_PROMPT`(55 §2) 규칙의 축약본이다 — 두 생산자가 같은 규칙을 따라야 청크 포맷이
일관된다. 문안을 바꾸면 `METADATA_INSTRUCTION_VERSION` 을 올린다(§1.3).

`reason` 판정:

- `missing` — `endpoint_business_metadata` 행 없음
- `stale` — 행은 있으나 `source_hash` 가 현재 스펙+버전으로 계산한 값과 다름(스펙이 바뀐 것)
- 그 외 → 키 없음

### 3.3 콜드스타트 보조 (선택, Phase 2)

크레딧 없이 콜드스타트를 줄이는 credit-free 수단으로 조회 도구 1개를 추가할 수 있다.

```python
list_endpoints_missing_metadata(document_id: str | None, project: str | None, limit: int = 20)
  -> {"items": [{"endpoint_id", "method", "path", "summary"}], "remaining": int}
```

사용자가 자기 세션에 "이 문서 메타데이터 백필해줘" 라고 지시하면 클라 LLM 이
`list → get_details → submit` 루프를 돌 수 있다. 과금은 사용자가 이미 쓰는 세션 안에서 발생하므로 별도 크레딧이
필요 없다. **Phase 1 과 분리 가능하므로 승인도 따로 받는다.**

---

## 4. 신뢰성·오염 방지

### 4.1 정규화 (거부보다 정규화 우선)

`validation.py::sanitize_and_clip` 이 저장 직전에 강제한다. 상한은 55 §2.3 과 동일하게 간다
(description 120자 / keywords 5×30자 / user_phrases 4×40자 — 청크 480토큰 예산 안에서 약 155토큰).

추가로 write-back 에서만 필요한 정규화:

1. **개행·제어문자 제거** (가장 중요). 청크 텍스트는 줄 단위 포맷이라, 저장값에 `\n` 이 들어가면
   `BusinessDesc:` 아래에 가짜 `Responses:` 줄 같은 것을 심을 수 있다. 모든 `\r\n\t` 와 제어문자를
   공백으로 치환하고 연속 공백을 접는다.
2. **HTML 태그 strip** — `chunk_builder`/`prompt` 와 동일한 정규식 재사용.
3. 항목 단위 공백 제거 후 빈 문자열 드롭, 대소문자 무시 중복 제거.
4. 세 필드가 정규화 후 전부 비면 `rejected` (`reason="empty_after_sanitize"`).
   `business_description` 만 비고 나머지가 있으면 저장한다(부분 기여 허용).

내용의 사실성은 서버가 검증할 수 없다. 방어선은 "형식·주입 차단 + 덮어쓰기 규칙 + 끄는 스위치" 까지이며,
이는 설계상 수용하는 한계다(호출 LLM 은 사용자 자신의 세션이고 MCP 서버는 로컬 신뢰 경계 안에 있다).

### 4.2 provenance 컬럼 채우기

| 컬럼 | write-back 에서의 값 |
| --- | --- |
| `generated_at` | 서버 시각 `datetime.now(UTC)` (관측용, 판단에 쓰지 않음 — 55 §3 유지) |
| `model` | 상수 `"client-writeback"` |
| `source_hash` | 서버가 `ApiEndpoint` 에서 payload 를 조립해 계산(CLI 와 동일 함수) |

`model` 을 호출 LLM 이 자기 신고하게 만들지 않는다 — 검증 불가능한 값이고, 55 의 "model 불일치 시 재생성" 규칙과
맞물리면 클라 모델이 바뀔 때마다 전량 재생성이 도는 사고가 난다. 상수 1개면 "이 행은 write-back 산" 을
구분하는 목적을 충족한다.

`source_hash` 를 **호출 LLM 이 아니라 서버가** 계산하는 점이 핵심이다. 호출 LLM 은 무엇을 근거로 썼는지
증명할 수 없고, 서버는 그 시점 스펙을 안다. 이렇게 하면 스펙이 바뀌는 순간 자동으로 `stale` 이 되어
힌트가 다시 뜬다 — 별도 TTL 없이 신선도가 유지된다.

### 4.3 중복 생성 / 덮어쓰기 규칙

행은 `(document_id, method, path)` 유니크 1건만 유지한다(누적 없음). upsert 판정:

| 기존 행 | 판정 |
| --- | --- |
| 없음 | 저장 (`stored`) |
| 있음 + `source_hash` 불일치(스펙 변경) | 덮어쓰기 (`stored`) |
| 있음 + 해시 일치 + `model == "client-writeback"` | **덮어쓰지 않음** (`already_current`) |
| 있음 + 해시 일치 + CLI 생성분 | **덮어쓰지 않음** (`already_current`) |

해시가 같은데 덮어쓰기를 허용하면 세션마다 문구가 흔들리고 그때마다 재임베딩이 돌아 검색 결과가
비결정적으로 움직인다(`fb61dc9` 에서 없앤 종류의 문제를 다시 만든다). 대신 **잘못 들어간 값을 고치는 경로는
운영자에게만 준다** — stage3 CLI `--force`, 또는 해당 행 DELETE 후 재기여. 이 제약은 의도적이며
도구 docstring 에 명시한다.

### 4.4 즉시 색인 반영

메타데이터를 저장만 하면 다음 재색인 전까지 검색에 아무 영향이 없다. 그러면 힌트는 사라지는데 품질은
그대로인 조용한 구멍이 생긴다. 따라서 저장과 함께 **해당 엔드포인트 청크 1건만** 갱신한다.

`app/services/indexer/endpoint_chunk_refresher.py` (신설, metadata 패키지 import 없음):

1. `Document.raw_text` + `doc_type` 으로 `parse_document` 재파싱 → `(method, path)` 로 `ParsedEndpoint` 조회
2. `build_endpoint_chunk_text(parsed_ep, metadata=row)` 로 텍스트 재조립 (색인 경로와 **같은 함수** — 포맷 드리프트 없음)
3. `embedding_provider.embed_documents([text])` — 로컬 SentenceTransformer 라 **비용 0**
4. `chunk` 행(`document_id`, `chunk_type="endpoint"`, `ref_id=endpoint_id`) 의 `text`/`embedding` UPDATE.
   `text_tsv` 는 STORED generated 컬럼이라 FTS 는 자동 갱신된다

ORM 에서 `ParsedEndpoint` 를 역조립하지 않고 재파싱하는 이유는 청크 텍스트 생성 경로를 하나로 유지하기
위해서다(역조립은 조용한 포맷 드리프트를 만든다). 비용은 문서 1건 파싱이고, 엔드포인트당 평생 한 번,
읽기 경로 밖에서 발생하므로 수용한다.

실패 시(파싱 오류, 청크 행 없음 등) **메타데이터 행은 롤백하지 않는다** — 다음 재색인에서 기존
`list_business_metadata_by_document` 주입 경로로 반영된다. 응답의 `reindexed=false` 로 알린다.

---

## 5. 기존 자산 확인 — 2단계/1b 는 전부 그대로다

| 자산 | 상태 |
| --- | --- |
| `endpoint_business_metadata` 테이블·마이그레이션 2건 | **변경 없음.** 컬럼 추가/삭제 없음 |
| `(document_id, method, path)` 키 설계(재색인 생존) | **더 중요해졌다** — write-back 산 데이터가 재색인에 살아남아야 한다 |
| 청크 포맷(헤더 직후 `Keywords`/`Phrases`, description 뒤 `BusinessDesc`) | **변경 없음** |
| description HTML strip + 300자 절단 (53/54) | **변경 없음** |
| `IndexerService` 의 옵셔널 주입 | **변경 없음** — write-back 은 같은 테이블을 채울 뿐 |
| `EndpointRepository.list_business_metadata_by_document` | **변경 없음.** 조회 메서드 1개 추가만 |

즉 이번 재설계는 **생산자만 교체**하는 변경이다. 소비자(청크 조립·검색)는 손대지 않는다.

---

## 6. 구현 범위

**Phase 1 (승인 요청 대상)**

1. `spec_payload.py` / `validation.py` 분리, `prompt.py`·`generator.py` 를 위임으로 교체
2. 경계 테스트를 모듈 단위 규칙으로 수정
3. `EndpointRepository`: `get_business_metadata(document_id, method, path)` 추가
4. `ChunkRepository`: `update_endpoint_chunk(document_id, ref_id, text, embedding)` 추가
5. `endpoint_chunk_refresher.py` 신설
6. `writeback_service.py` 신설 + `ServiceBundle` 배선
7. `submit_endpoint_metadata` MCP 도구 + `MetadataSubmitResult` TypedDict
8. `get_endpoint_details` 응답에 조건부 `metadata_request` (`EndpointDetails` TypedDict 에 `total=False` 키 추가)
9. `Settings.business_metadata_writeback_enabled`
10. 테스트: 정규화(개행 주입 포함), 덮어쓰기 4분기, 청크 재조립 텍스트 일치, 힌트 조건부 노출, 스위치 off

**Phase 2 (분리 승인)** — §3.3 `list_endpoints_missing_metadata`

**변경 없음** — 마이그레이션, 청크 포맷, 검색 경로, stage3 CLI 의 동작
