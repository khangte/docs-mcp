# 55. 비즈니스 메타데이터 3단계 설계 — 생성 CLI

- 설계일: 2026-08-25
- 대상: [52](52_business_metadata_design_verdict.md) §(c) 4번 항목, [54](54_business_metadata_stage1b_verdict.md) 에서 승인된 3단계
- 전제: 2단계 완료(`66a2d9a`) — `endpoint_business_metadata` 테이블, `build_endpoint_chunk_text` 옵셔널 주입, description HTML strip + 300자 절단 반영됨
- 미정 4건(LLM provider / 프롬프트 / 재생성 판단 / 실행 단위)에 대한 결정을 담는다

---

## 1. LLM provider — **SDK 를 도입하지 않는다. `httpx` 로 Anthropic Messages API 를 직접 호출한다**

프로젝트 현황을 확인했다. `app/` 전체에 LLM SDK import 도, API 키 참조도 없다(grep 결과 0건).
따라서 "이미 쓰는 것"은 없고, 새로 정해야 한다.

이 코드베이스에는 이미 같은 상황에 대한 선례와 그 근거가 `pyproject.toml` 에 적혀 있다.

```toml
# Drive 서비스 계정 JWT 서명/토큰 발급 전용. Drive REST 호출 자체는 httpx 로 직접 수행하므로
# google-api-python-client 는 도입하지 않는다.
```

같은 판단을 적용한다. **`httpx>=0.27` 이 이미 메인 dependency 이므로 신규 의존성은 0개다.**

이 결정은 52 §(c) 조건("LLM SDK 는 optional extra 로 넣어 서버 배포 이미지에서 격리한다")을
완화하는 것이 아니라 **더 강하게 충족한다** — 격리해야 할 SDK 자체가 없으므로 extra 선언,
uv 동기화 분기, CI 설치 조건이 전부 불필요해진다. `[project.optional-dependencies]` 에
`metadata` extra 를 만들지 않는다.

우리가 쓰는 API 표면은 작다.

```
POST https://api.anthropic.com/v1/messages
헤더: x-api-key, anthropic-version: 2023-06-01, content-type: application/json
바디: {model, max_tokens, temperature, system, messages}
응답: content[0].text
```

재시도만 직접 다루면 된다 — 429/500/529 는 지수 백오프(최대 5회), `retry-after` 헤더가 있으면 우선한다.
그 외 4xx 는 즉시 실패로 처리하고 해당 엔드포인트만 건너뛴다.

### 모델

`temperature=0` 고정(재실행 재현성). 기본 모델은 `--model` 로 덮을 수 있게 하되,
**기본값을 확정하기 전에 파일럿을 돌린다**(§5 T5). 후보는 `claude-sonnet-5` 와
`claude-haiku-4-5-20251001` 이다. 대상이 1809건(stripe 589 + github 1220)이라 모델 선택이
비용에 그대로 곱해진다. 품질 차이가 실제로 있는지 50건으로 먼저 보고 정한다.

### 설정 (`app/core/config.py` 에 필드 3개 추가)

| 필드                | 환경변수                                                  | 기본값                      |
| ------------------- | --------------------------------------------------------- | --------------------------- |
| `metadata_api_key`  | `DOCS_MCP_ANTHROPIC_API_KEY` → 없으면 `ANTHROPIC_API_KEY` | `None`                      |
| `metadata_model`    | `DOCS_MCP_METADATA_MODEL`                                 | §5 T5 결과로 확정           |
| `metadata_api_base` | `DOCS_MCP_METADATA_API_BASE`                              | `https://api.anthropic.com` |

`Settings` 는 서버도 로드하지만 값을 읽기만 하고 아무것도 import 하지 않으므로 경계를 깨지 않는다.
키가 없으면 CLI 는 시작 시점에 명확한 메시지와 함께 종료한다(호출 도중에 실패하지 않게).

### 모듈 배치와 경계

| 파일                                        | 역할                                                             |
| ------------------------------------------- | ---------------------------------------------------------------- |
| `app/services/metadata/llm_client.py`       | httpx 호출 + 재시도 + JSON 파싱. Anthropic 고유 부분은 전부 여기 |
| `app/services/metadata/prompt.py`           | 시스템/유저 프롬프트 조립, `PROMPT_VERSION`, 입력 payload 직렬화 |
| `app/services/metadata/generator.py`        | 대상 선별 → 호출 → 검증/절단 → upsert. 순수 로직, 테스트 대상    |
| `app/scripts/generate_business_metadata.py` | argparse + `bootstrap_app_state()` + 종료코드. 얇게              |

`refresh_documents.py` → `app/services/documents/registered_resync.py` 와 같은 구조다.
CLI 는 얇고 로직은 서비스에 둔다.

**경계 테스트를 함께 넣는다.** 52 §(c) 조건 3은 "리뷰에서 확인한다"였는데, 사람 리뷰는 잊힌다.
`tests/unit/test_metadata_boundary.py` 에서 `app/mcp/`, `app/services/search/`,
`app/services/indexer/` 소스를 읽어 `app.services.metadata` import 가 없음을 단언한다.

## 2. 프롬프트 설계

### 2.1 가장 큰 결정: **출력은 한국어·영어 양쪽을 담는다**

이것이 이번 설계의 핵심이고, 측정에서 직접 나온 요구다.

- C7 3건(`결제 생성 시 통화 단위 지정`, `결제 인텐트에 자동 결제수단 설정`, `풀리퀘스트를 draft로 생성`)은
  **전부 한국어 질의**다. 그리고 청크 텍스트에는 한국어가 한 글자도 없다.
- 키워드 갈래는 `to_tsvector('simple', text)` 라 한국어 토큰을 인덱싱한다
  (`app/services/search/keyword_search.py` docstring). 청크에 한국어가 없으면
  **한국어 질의는 렉시컬로 매칭될 가능성이 0이다.** C7이 fallback 갈래에서 계속 0%인 것이 이것이다.
- 벡터 갈래는 `multilingual-e5-small` 이라 교차언어 매칭이 되긴 하지만, API 도메인 용어에서는 약하다.

즉 C7 실패는 "설명이 부실해서"가 아니라 **질의 언어의 표면이 색인에 아예 없어서**다.
한국어 표현을 청크에 넣는 것이 이 문제에 대한 직접적인 처방이다.

- `business_description`: **한국어** 1문장. 이미 영어 description 이 병기돼 있으므로 한국어로 써야
  중복이 아니라 추가 표면이 된다.
- `user_phrases`: **한국어 2 + 영어 2**.
- `keywords`: 영어(필드명·도메인 용어) + 한국어 혼합.

### 2.2 54 §6 반영 — q13 케이스는 프롬프트에 명시적으로 넣는다

54 §6에서 이관한 q13은 정답 summary 가 `Cancel a subscription` 인데 사용자는
`delete a subscription` 이라고 묻는 케이스다. 청크 어디에도 그 표현이 없어 어떤 텍스트 정리로도
살릴 수 없다.

프롬프트에 규칙으로 넣는다: **summary 의 동사와 다른, 사용자가 실제로 쓸 법한 동사 표현을
`user_phrases` 에 반드시 포함한다** (cancel ↔ delete/remove/terminate, create ↔ add/register,
list ↔ get all/fetch 류). 이 규칙 하나가 q13 유형 전체를 겨냥한다.
4단계 측정에서 q13이 살아나는지가 이 규칙의 검증이다.

### 2.3 개수·길이 상한 (토큰 예산 때문에 타이트하게 간다)

임베딩 모델 입력 상한은 512 토큰이고, 대형 엔드포인트 청크는 이미 그 근처에서 잘린다.
1b가 얻은 이득(C7 0% → 33%)은 description 을 300자로 줄여 구조 필드 `Body:` 를 살린 데서 나왔다.
**메타데이터를 넉넉하게 넣으면 그 이득을 그대로 반납한다.**

| 필드                   | 상한                      | 근사 토큰     |
| ---------------------- | ------------------------- | ------------- |
| `keywords`             | 5개, 각 30자              | ~25           |
| `user_phrases`         | 4개(한 2 + 영 2), 각 40자 | ~60           |
| `business_description` | 1문장, 120자              | ~70           |
| 합계                   |                           | **~155 토큰** |

상한은 프롬프트에만 적지 않는다 — **`generator.py` 가 저장 직전에 강제로 자르고, 잘렸으면
WARNING 을 남긴다.** 프롬프트 준수에 예산을 맡기면 언젠가 넘친다.
느슨하게 풀어도 되는지는 4단계 A/B 에서 정한다. 지금은 조인다.

### 2.4 입력 payload 와 환각 금지

호출당 입력은 엔드포인트 1건이다.

```
method, path, summary, description(HTML strip 후 앞 600자), operation_id,
param 이름 목록, request body 필드명 목록, tags
```

description 을 600자로 주는 것은 청크의 300자 절단과 다른 값이다 — 청크는 색인 예산 문제고,
프롬프트는 요약 근거를 확보하는 문제라 더 넓게 준다.

**규칙: 주어진 입력에 없는 사실을 만들지 않는다.** 근거가 부족하면 짧게 쓰거나 필드를 비운다.
빈 값은 허용한다 — 옵셔널 주입 구조상 빈 필드는 그냥 그 줄이 빠질 뿐이다.

### 2.5 출력 형식

strict JSON 만 반환하게 하고, assistant 턴을 `{` 로 prefill 해 서두 산문을 원천 차단한다.

```json
{ "business_description": "...", "keywords": ["..."], "user_phrases": ["..."] }
```

파싱 실패 시 1회 재시도, 그래도 실패하면 해당 엔드포인트를 건너뛰고 ERROR 로그를 남긴다
(전체 실행을 중단하지 않는다). 건너뛴 건은 `source_hash` 가 저장되지 않으므로 다음 실행에서
자동으로 재시도 대상이 된다.

## 3. 재생성 판단 — **`source_hash` 컬럼을 추가한다. 시간 기반 TTL 은 넣지 않는다**

`generated_at` / `model` 만으로는 **원본 스펙이 바뀐 경우를 감지할 수 없다.** 스펙의 description 이
갱신되면 기존 메타데이터는 조용히 틀린 값이 되고, 아무것도 그것을 알려주지 않는다.
이는 52에서 별도 테이블을 요구했던 것과 같은 종류의 문제다 — 재색인은 정상 운영에서 주기적으로
일어난다(`app/scripts/refresh_documents.py`).

3단계 마이그레이션에 컬럼 하나를 더한다.

```
source_hash  String(64)  NOT NULL DEFAULT ''
```

값은 **프롬프트에 실제로 들어간 입력 payload 문자열 + `PROMPT_VERSION` 의 sha256** 이다.
`PROMPT_VERSION` 을 해시에 넣는 것이 핵심이다 — 프롬프트를 개선하면 해시가 전부 바뀌어
재생성 대상이 자동으로 잡힌다. 별도 컬럼이나 수동 `--force` 가 필요 없다.

### skip / 재생성 규칙

다음 중 하나라도 참이면 생성, 아니면 건너뛴다.

1. 행이 없다
2. `row.source_hash != 계산된 해시` (스펙 변경 또는 프롬프트 변경)
3. `row.model != 대상 모델` (모델 교체)
4. `--force`

`generated_at` 은 **판단에 쓰지 않는다.** 관측·보고용이다. "N일 지나면 재생성" 같은 TTL 은 넣지 않는다 —
LLM 출력이 시간이 지난다고 낡지 않고, 낡게 만드는 요인(스펙 변경·프롬프트 변경·모델 변경)은
위 1~3이 이미 전부 덮는다. 없는 요구에 스위치를 만들지 않는다.

## 4. 실행 단위 — **기본 증분. 전체 재생성은 `--force`**

§3의 skip 규칙이 곧 증분이다. 별도 모드를 만들 필요가 없다.

```
uv run python -m app.scripts.generate_business_metadata [옵션]

  --document-id ID    특정 문서만 (반복 지정 가능)
  --project P         프로젝트 스코프
  --force             스코프 내 전건 재생성
  --limit N           최대 N건만 처리 (파일럿·비용 통제)
  --dry-run           대상 건수와 payload 만 출력, API 호출·DB 쓰기 없음
  --concurrency N     기본 4
  --model M           설정 기본값 덮어쓰기
```

- 스코프 기본값은 **OpenAPI 문서 전체**다.
- 동시성은 `concurrent.futures.ThreadPoolExecutor` + `httpx.Client` 로 한다. 스크립트들이 전부
  동기 코드이므로(`reembed.py`, `refresh_documents.py`) 여기만 async 로 가르지 않는다.
- **20건마다 커밋한다.** 1809건 단일 트랜잭션은 실패 시 전부를 잃는다. 중단돼도 증분 규칙 때문에
  재실행이 이어서 진행된다.
- 종료코드는 `refresh_documents.py` 규약을 따른다 — `0` 정상/부분 실패, `1` 전건 실패.
- 진행 로그는 `reembed.py` 처럼 `logger.info("생성 진행: %d/%d", ...)`.

### 이 CLI 는 재색인을 하지 않는다

메타데이터는 `IndexerService` 가 색인 시점에 주입하므로, 생성만으로는 청크에 반영되지 않는다.
CLI 에 `--reindex` 를 붙이지 않는다 — 52 §(c)가 분리한 이유(색인은 결정적·빠르게, LLM 은 분리)를
다시 무너뜨린다. 대신 **완료 로그에 다음 실행 명령을 출력한다.**

## 5. 태스크 분해 (developer)

| #   | 태스크                                                                                                                                                               | 산출                                      |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| T1  | `source_hash` 컬럼 마이그레이션 + 모델 필드                                                                                                                          | alembic revision, `app/models/openapi.py` |
| T2  | `app/core/config.py` 설정 필드 3개                                                                                                                                   | §1 표                                     |
| T3  | `app/services/metadata/prompt.py` — `PROMPT_VERSION`, payload 직렬화, 시스템/유저 프롬프트, 해시 함수                                                                | §2, §3                                    |
| T4  | `app/services/metadata/llm_client.py` — httpx 호출, 백오프 재시도, JSON prefill 파싱                                                                                 | §1                                        |
| T5  | **파일럿**: `--limit 50 --dry-run` 으로 payload 확인 → 같은 50건을 `claude-sonnet-5` / `claude-haiku-4-5-20251001` 로 각 1회 생성해 출력 품질 비교 후 기본 모델 확정 | 비교 결과 보고                            |
| T6  | `app/services/metadata/generator.py` — 대상 선별(skip 규칙), 상한 강제 절단, upsert, 20건 커밋                                                                       | §2.3, §3                                  |
| T7  | `app/scripts/generate_business_metadata.py` — argparse, 종료코드, 진행/완료 로그                                                                                     | §4                                        |
| T8  | 단위 테스트 — skip 규칙 4분기, 상한 절단, JSON 파싱 실패 폴백, 경계 테스트(`test_metadata_boundary.py`)                                                              | §1                                        |
| T9  | 전체 생성 실행 → 재색인 → 4단계 A/B 4조건 측정                                                                                                                       | **T5 보고 후 lead 승인을 받고 착수**      |

### T9 에 승인 게이트를 두는 이유

과금 호출이고 규모가 1809건(stripe 589 + github 1220)이다. 건당 입력 ~400 / 출력 ~200 토큰으로
잡으면 **입력 약 72만 토큰, 출력 약 36만 토큰**이다. 되돌릴 수 없는 지출이므로 architect 가
단독으로 실행 지시하지 않는다. T5 파일럿(50건, 두 모델)까지만 진행하고, 실제 품질과 모델 선택을
보고한 뒤 lead 판단으로 전량 실행한다.

## 6. 4단계 성공 기준 (미리 확정)

52 §측정 계획 4단계는 "variants 없이 돌린 C2/C7 이 0단계 variants 켠 값에 근접"이었다.
54 §2에서 재채점 축을 rrf 로 정했으므로 여기에 맞춰 다시 적는다.

- 1차 기준: **rrf · variants off** 에서 C7 recall@3 이 arm B 의 33% 를 넘고, C5 가 67% 이상 유지,
  집계 MRR 이 arm B(.318) 이상.
- q13 이 top3 에 들어오는지 건별로 확인한다 — §2.2 규칙의 직접 검증이다.
- fallback 갈래 수치는 54 §3의 `ts_rank` 정규화 결함이 해소되기 전까지 판정 근거로 쓰지 않는다.
  다만 한국어 표면이 추가되면 fallback 의 C7 이 0% 를 벗어날 수 있다 — 그렇게 되면 §2.1 가설의
  강한 확증이므로 관측은 계속한다.
