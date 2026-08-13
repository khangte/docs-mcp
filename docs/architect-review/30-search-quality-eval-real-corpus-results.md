# 30. 검색 품질 평가 — 실 코퍼스(Stripe/GitHub) 측정 결과

- 상태: 측정 완료(developer). 설계는 `28-search-quality-eval-real-corpus-design.md`, 색인 차단 버그 수정은 `29-schema-chunk-ref-id-truncation-fix.md`.
- 실행: `uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --strategy both`
- 코퍼스: `tests/fixtures/corpus_eval/`에 프리즈(§4 매니페스트, 핀 SHA) — Stripe 589 엔드포인트, GitHub 1220 엔드포인트.
- `is_semantic: True`(로컬 e5 모델 정상 동작 확인).

## 1. 지표 요약 (n=20, top_k=10)

| 전략 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|
| fallback | 10% | 20% | 30% | 40% | 0.183 | 0.235 |
| rrf | 10% | 30% | 35% | 45% | 0.200 | 0.259 |

synthetic 20-엔드포인트 하네스(`rrf_eval`) 대비 큰 폭 하락 — doc/28 §1.2가 예견한 대로, 589~1220개 규모 실 코퍼스에서 지표가 포화되지 않고 실제 변별력을 보인다.

## 2. 카테고리별 분해 (Recall@3 / MRR)

| 카테고리 | n | fallback R@3 | fallback MRR | rrf R@3 | rrf MRR |
|---|---|---|---|---|---|
| C1-직접키워드 | 3 | 33% | 0.389 | 67% | 0.667 |
| C2-한글패러프레이즈 | 4 | 0% | 0.042 | 0% | 0.042 |
| C3-영문의역 | 3 | 0% | 0.083 | 33% | 0.111 |
| C4-흔한토큰범람 | 2 | 0% | 0.000 | 0% | 0.000 |
| C5-decoy구분 | 3 | 67% | 0.528 | 67% | 0.264 |
| C6-다개념(복수정답) | 2 | 50% | 0.250 | 50% | 0.250 |
| C7-대형엔드포인트세부 | 3 | 0% | 0.000 | 0% | 0.067 |

**두드러진 실패 축**: C2(한글 패러프레이즈)와 C7(대형 엔드포인트 세부 truncation)이 두 전략 모두에서 사실상 0에 가깝다.

- **C2**: `고객 새로 등록하고 싶어`(MRR 낮음), `결제 환불 처리해줘`/`이슈 새로 만들기`/`저장소 삭제해줘`는 top-10에서 아예 미검출. 교차언어(한글 질의 → 영문 문서) recall이 synthetic 하네스보다 훨씬 약함 — 코퍼스 규모가 커지며 다른 언어권 decoy가 늘어난 영향으로 보임.
- **C7**: `POST /v1/charges`, `POST /v1/payment_intents`, `결제 생성 시 통화 단위 지정` 모두 미검출(rrf가 draft PR 질의 하나만 순위 5 회수). doc/26 truncation 논지가 엔드포인트 청크에도 실측으로 확인됨 — 대형 파라미터 문서를 가진 엔드포인트의 세부 질의는 임베딩 512토큰 상한에 걸려 발견되지 않는다.
- rrf가 fallback보다 나은 축은 C1(직접 키워드)·C3(영문 의역) — RRF 융합이 벡터 arm의 의미 유사도를 살리는 사례로 해석 가능.

## 3. 회귀 (rrf < fallback, MRR 기준)

- `delete a subscription`: fallback=1위 → rrf=3위 (MRR -0.667)
- `list commits of a repo`: fallback=4위 → rrf=8위 (MRR -0.125)

두 건 모두 C5(decoy구분) — 키워드 단독으로 이미 상위였던 사례에서 벡터 arm 융합이 오히려 순위를 밀어낸 경우. 표본 20건 규모에서 결론을 내리기엔 이르나, RRF가 "이미 잘 맞는 키워드 매치"를 약화시킬 수 있다는 신호.

## 4. 원 색인 차단 버그 (참고)

최초 실행 시 Stripe 스펙 등록이 `StringDataRightTruncation`으로 크래시(schema 컴포넌트명 최대 135자가 `chunk.ref_id`(64자)에 그대로 들어감). `29-schema-chunk-ref-id-truncation-fix.md` 판정에 따라 근본 수정(schema 청크 ref_id를 바운드 id로 교체) 후 재실행 — 위 결과는 수정 반영 후 값이다. endpoint 청크만 채점 대상이라 이 수정은 doc/28 결과 자체에는 영향 없음(§4, 판정문 그대로 확인).

## 5. 시사점 (lead/architect 판단 필요 항목)

1. **C2 교차언어 recall 붕괴**는 synthetic 하네스에서 보이지 않던 문제 — 실 코퍼스 규모에서만 드러남. multilingual-e5-small의 한/영 교차 임베딩 품질 재검토가 필요할 수 있음.
2. **C7 truncation 미검출**은 doc/26(긴 섹션 truncation) 논지가 엔드포인트 청크에도 적용됨을 실측으로 확인 — 엔드포인트는 sub-chunking 대상이 아니므로(doc/28 §6 카테고리 의도) 별도 대응이 필요하면 새 설계 검토 대상.
3. RRF 회귀 2건은 표본이 작아 결론 보류 — 향후 질의셋 확장 시 재확인 권장.

## 6. 재현

```bash
docker compose up -d postgres
uv run python tests/fixtures/corpus_eval/run_corpus_eval.py --strategy both
```

## 7. C2 교차언어 recall 붕괴 — 원인 분석 및 대응 설계 (§5-1 판단)

§5-1(C2 한글 패러프레이즈 top-10 미검출)에 대한 architect 판정이다. 대상 질의:
`고객 새로 등록하고 싶어`(q04) / `결제 환불 처리해줘`(q05) / `이슈 새로 만들기`(q06) /
`저장소 삭제해줘`(q07). 두 전략 모두 R@3 0%, MRR 0.042.

### 7.1. 원인 — 단일 실패가 아니라 두 신호가 동시에 죽는다

파이프라인을 arm별로 분해하면 C2는 **키워드 arm이 구조적으로 0건**이고
**벡터 arm이 홀로 약한** 두 원인의 곱이다.

1. **키워드 arm(FTS)은 교차언어에서 원리적으로 0건.**
   엔드포인트 청크 텍스트(`build_endpoint_chunk_text`, `app/services/indexer/chunk_builder.py:33`)는
   `[POST] /v1/customers — Create a customer / <description> / Tags / Params / Responses`로
   **전량 영문**이다. Postgres FTS는 어휘 매칭이므로 한글 토큰(`고객`, `등록`)은
   이 영문 청크와 겹치지 않는다. 즉 C2에서 키워드 arm은 후보 자체를 못 만든다.

2. **그 결과 RRF가 벡터 단독으로 붕괴 → §2에서 rrf==fallback(둘 다 0%)로 관측된 이유.**
   `_search_rrf`(`endpoint_candidate_search.py:160`)는 두 arm을 융합하는데,
   키워드 arm이 빈 리스트면 RRF 점수는 `1/(k+rank_vector)` 한 항만 남아
   **사실상 벡터 검색 순위 그대로**가 된다. `fallback` 전략도 키워드 0건 시
   벡터로 넘어가므로 결국 같은 벡터 순위를 본다 — 두 전략이 C2에서 동률인 것은
   우연이 아니라 구조적 귀결이다.

3. **유일하게 남은 벡터 arm이 약하다.**
   `intfloat/multilingual-e5-small`(`app/core/config.py:48`, 118M·384-dim)은 e5
   다국어 계열의 **최소 모델**로, KO→EN 교차언어 정렬 품질이 base/large 대비
   눈에 띄게 낮다. synthetic 20-엔드포인트 하네스에서는 경쟁 후보가 적어
   약한 정렬로도 상위에 들었지만, 589+1220개 실 코퍼스에서는 영어권 decoy가
   대량으로 끼어들며 약한 KO→EN 유사도가 top-10 밖으로 밀린다(§2 관측과 일치).

**측정 조건 주석**: 이번 하네스는 원본 질의만 넣고 `query_variants`를 비운
**콜드 싱글샷**이다. 즉 C2 0%는 "클라이언트가 아무 보정도 안 한 최악의 단발
질의" 값이다. 설계상 교차언어 회복 경로는 `query_variants`(클라 LLM이 영문
표현을 함께 제공)인데, 이 경로가 지금 **절반만 배선**돼 있다(7.2 참조).

### 7.2. 대응 설계 — 판정

세 대응 후보를 검토했다. 결론은 **1차(변형 라우팅) 즉시, 2차(모델 교체) 재측정
게이트, 3차(가중치 튜닝) 기각**이다.

#### 채택 — 1차: `query_variants`를 벡터 arm에도 라우팅 (저비용·구조 정합)

현재 `query_variants`는 키워드 arm만 넓히고 벡터 arm은 원본 질의만 임베딩한다
(`endpoint_candidate_search.py:68-71` 주석, `_search_rrf`가 `vector_search.search`에
원본 `query`만 전달). 이 계약을 바꿔 **영문 변형을 두 arm 모두에 태운다.**

- **키워드 arm 부활**: 클라가 준 영문 변형 `create customer`는 영문 엔드포인트
  청크와 어휘가 겹친다 → C2에서 키워드 arm이 실제 후보를 만든다 → RRF가 죽은
  단항이 아니라 두 신호 융합으로 복귀.
- **벡터 arm에 동일언어 기회 제공**: `create customer` 임베딩 대 영문 passage는
  KO→EN이 아니라 **EN→EN 단일언어** 비교라 e5-small로도 훨씬 강하다. 변형 벡터
  히트를 원본 질의 벡터 히트와 함께 융합한다.

이 방향은 `docs/12 후보4`(query_variants = 클라 LLM 확장)와 정합하고,
메모리 원칙 `MCP는 판단을 클라 LLM에 위임`(서버가 별도 번역 LLM/MT를 돌리지
않음)을 지킨다. 다만 doc/12에서 **명시적으로 "벡터 arm은 손대지 않는다"고
결정**했던 계약을 뒤집는 변경이므로, 이 판정문이 그 재결정 근거다:
교차언어에서는 벡터 arm이 유일 신호인데 원본 KO 질의만으로는 약하다는 것이
doc/12 당시엔 실측되지 않았고 이번 §2에서 드러났다.

부수 필수 조치(문서 변경, 저비용·고레버리지):
`search_endpoints` 도구 docstring(`app/mcp/tools/endpoints.py`)의 `query_variants`
설명을 **"비영문 질의는 영문 표현을 반드시 변형으로 함께 제공"**으로 강화한다.
현재는 "영한 혼용" 정도로 약하게 유도해 클라가 교차언어 변형을 안 넣을 여지가 크다.

#### 조건부 보류 — 2차: 재임베딩 모델 교체 (`e5-small` → `e5-base`)

가장 높은 천장을 주는 레버지만 **마이그레이션 비용이 실재**한다:
- 차원 384→768 → alembic 마이그레이션 필요(선례:
  `alembic/versions/ff8aa8f36266_embedding_dim_256_to_384...`), 전량 재임베딩
  (`app/scripts/reembed.py` 존재), CPU 인코딩 지연 약 2~3배·메모리 증가.
- KO→EN raw recall은 확실히 오르나, 1차(변형 라우팅)로 키워드 arm이 부활하고
  벡터가 EN→EN이 되면 base 승급 없이도 C2가 회복될 수 있다.

**판정: 1차 적용 후 C2를 재측정하고, 그래도 미흡할 때만 2차를 당긴다.**
콜드 KO 단발 recall 자체가 제품 요구인지(=클라가 변형을 못/안 주는 경로를
지원해야 하는지)가 base 승급의 진짜 트리거다. 그 요구가 확정되기 전 마이그레이션
+지연 비용을 선지불하지 않는다(YAGNI).

#### 기각 — 3차: RRF k / arm 가중치 튜닝

`RRF_K=60`은 doc/07 §5.2에서 평가셋 부재로 상수 고정했다. k나 arm 가중을 바꿔도
**없는 신호를 만들어내지 못한다** — C2의 병목은 키워드 arm이 0건이고 벡터가
약한 것이지 융합 스케일이 아니다. 근거 없는 튜닝은 다른 카테고리 회귀 위험만
키운다(§3 RRF 회귀 2건이 이미 튜닝 민감성을 시사). 기각.

### 7.3. 후속 조치 (developer 배정 대상)

1. `_search_rrf` 벡터 arm이 `query_variants`를 반영하도록 배선(원본+변형 벡터
   히트 융합). `endpoint_candidate_search.py:68-71` 계약 주석도 갱신.
2. `search_endpoints` docstring의 `query_variants` 유도문을 교차언어 필수로 강화.
3. 1·2 적용 후 `run_corpus_eval.py`를 **변형 포함 조건**으로 재측정 — C2 회복폭이
   base 승급 판단(2차)의 입력이다. 변형은 클라 LLM이 주는 값이므로, 하네스에
   질의별 영문 변형을 함께 넣는 옵션을 추가해 재현 가능한 측정으로 만든다.

> C7(대형 엔드포인트 세부 truncation)은 §5-2로 별도 축이며 이 절 범위 밖이다.

## 8. §7.3 배선 결과 및 C2 재측정 (developer)

§7.2 판정에 따라 세 가지를 배선했다.

1. **벡터 arm query_variants 라우팅**: `_search_rrf`(`app/services/search/endpoint_candidate_search.py`)가
   원본 질의와 각 `query_variants`를 개별로 벡터 검색해, ref_id별 최솟(최상위) 등수를
   기준으로 병합한 뒤 키워드 arm과 RRF 융합한다. `CandidateSearchOptions.query_variants`
   계약 주석도 "벡터 arm은 손대지 않는다"(doc/12 후보4)에서 "벡터 arm에도 라우팅된다"로
   갱신했다.
2. **`search_endpoints` docstring 강화**: `query_variants` 설명을 "비영문 질의는 영문
   표현을 반드시 변형으로 함께 제공"으로 바꿔(`app/mcp/tools/endpoints.py`), 클라가
   교차언어 변형을 빠뜨릴 여지를 줄였다.
3. **`run_corpus_eval.py` 변형 옵션**: `queries.json`에 질의별 `variants`(클라 LLM이
   제공했을 영문 표현)를 추가하고, `--with-variants` 플래그로 재현 가능한 측정을
   지원한다.

### 8.1. C2 재측정 (rrf, top_k=10, n=20)

C2 4건에 실제로 자연스러운 영문 변형을 부여해 재측정했다(`고객 새로 등록하고
싶어`→`create a new customer`, `결제 환불 처리해줘`→`refund a payment`,
`이슈 새로 만들기`→`create a new issue`, `저장소 삭제해줘`→`delete a repository`).

| 조건 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|
| 변형 없음(§1 재확인) | 10% | 30% | 35% | 45% | 0.202 | 0.262 |
| 변형 포함(`--with-variants`) | 10% | 35% | 40% | 50% | 0.224 | 0.290 |

C2 카테고리만 보면:

| 조건 | C2 Recall@3 | C2 MRR |
|---|---|---|
| 변형 없음 | 0% | 0.042 |
| 변형 포함 | 25% | 0.150 |

질의별 순위(변형 없음 → 변형 포함):

- q04 `고객 새로 등록하고 싶어`: 6위 → **2위** (top-3 진입)
- q05 `결제 환불 처리해줘`: 미검출 → 10위 (top-10 진입, top-3는 아직 미달)
- q06 `이슈 새로 만들기`: 미검출 → 미검출 (변화 없음)
- q07 `저장소 삭제해줘`: 미검출 → 미검출 (변화 없음)

다른 카테고리(C1·C3·C4·C5·C6·C7)는 변형을 넣지 않았으므로 값이 §1과 동일 — 회귀 없음.

### 8.2. 해석

- **부분 회복 확인**: C2 MRR이 0.042 → 0.150(3.6배), Recall@3 0% → 25%로 개선됐다.
  q04는 top-3 진입, q05는 top-10 진입 — §7.2 판정("변형 라우팅으로 EN→EN 단일언어
  비교가 가능해져 회복") 방향이 실측으로 확인된다.
- **완전 회복은 아니다**: q06·q07은 변형을 줘도 여전히 미검출이다. 두 질의는
  GitHub 코퍼스(1220 엔드포인트, decoy 밀도가 Stripe보다 높음) 소속이라 —
  `create a new issue`/`delete a repository`처럼 짧고 흔한 동사구는 동일 문서 내
  유사 경로(예: PR 생성/삭제류)와도 벡터 유사도가 근접해 단일언어 비교로도
  변별이 약할 수 있다는 가설이나, 표본 2건으로 결론 내리긴 이르다.
- **2차(모델 교체) 판단**: §7.2가 세운 게이트("1차 적용 후에도 미흡하면 2차를
  당긴다")에 비춰, 4건 중 2건만 회복한 이번 결과는 **e5-base 승급을 즉시 정당화할
  만큼은 아니되 완전히 기각할 근거도 아니다** — q06·q07의 실패가 모델 표현력
  한계인지 decoy 밀도 문제인지 원인 분리가 안 됐다. 질의셋을 늘려 GitHub
  카테고리 실패 패턴을 더 확인한 뒤 2차 여부를 재판단하는 편이 이 표본 크기에서는
  근거 있는 다음 스텝이다(YAGNI — 원인 불명 상태에서 마이그레이션 비용을
  선지불하지 않는다).

## 9. C7 대형 엔드포인트 세부 미검출 — 원인 재판정 및 대응 설계 (§5-2 판단)

§5-2(C7 대형 엔드포인트 세부 top-10 미검출)에 대한 architect 판정이다. 대상 질의:
`결제 생성 시 통화 단위 지정`(q18, → POST /v1/charges) / `결제 인텐트에 자동
결제수단 설정`(q19, → POST /v1/payment_intents) / `풀리퀘스트를 draft로 생성`
(q20, → POST /repos/{owner}/{repo}/pulls). 두 전략 모두 R@3 0%, rrf가 q20만 순위 5 회수.

### 9.1. 원인 — §2/§5-2의 "truncation" 진단은 틀렸다. 실제 병목은 request body 누락이다

§2·§5-2는 C7 실패를 "임베딩 512토큰 상한 truncation"(doc/26 논지의 엔드포인트
적용)으로 귀속했다. 프리즈 코퍼스를 직접 측정한 결과 **이 귀속은 반증된다.**

1. **문제 엔드포인트의 청크는 512에 근처도 못 간다.**
   프리즈 스펙 실측:
   - `POST /v1/charges`: `parameters` **0개**, `currency` 는 `requestBody` 프로퍼티(총 20개).
   - `POST /v1/payment_intents`: `parameters` **0개**, `automatic_payment_methods`·
     `currency` 는 `requestBody` 프로퍼티(총 38개).
   - `POST /repos/{owner}/{repo}/pulls`: `parameters` 2개(owner/repo 경로), `draft` 는
     `requestBody` 프로퍼티(총 8개).

   세 건 모두 검색 타겟 세부(통화·자동결제수단·draft)가 **request body 필드**다.

2. **`build_endpoint_chunk_text`(`chunk_builder.py:33`)는 `request_body`를 전혀 읽지 않는다.**
   청크 텍스트는 `header / description / Tags / Params(=endpoint.parameters) / Responses`
   로만 구성된다. `endpoint.request_body`(파서는 `ParsedRequestBody`로 정상 파싱)는
   포맷에서 누락. → charges/payment_intents는 파라미터가 0개라 `Params:` 줄조차 없고,
   청크는 헤더+요약+태그+응답코드 수준의 **수십 토큰짜리 소형 청크**다. 512 상한에
   걸릴 여지가 없다.

3. **따라서 currency/automatic_payment_methods/draft는 truncation된 게 아니라 애초에
   색인되지 않았다.** 질의가 겨냥한 필드명이 벡터·FTS 어느 텍스트에도 존재하지 않으니
   두 arm 모두 신호를 못 만든다. q20이 rrf에서 순위 5로 잡힌 것도 `draft` 때문이 아니라
   경로·요약(`pulls` / "Create a pull request")의 잔여 신호일 뿐이다 — `draft`는 이 청크에 없다.

4. **교차언어(C2) 오염 주의**: q18·q19·q20 질의문은 모두 한글이다. 즉 C7의 관측
   실패는 request body 누락과 §7의 KO→EN 교차언어 붕괴가 **곱해진** 값이다. 설계
   의도(doc/28 §6)는 C7로 "엔드포인트 청크 truncation"을 노출하려 했으나, 실제 질의가
   한글이라 truncation을 격리 측정하지 못했고 — 그리고 위 1~3으로 truncation은
   애초에 병목이 아니었다. doc/26 truncation 테마가 엔드포인트에 실측 확인됐다는
   §5-2 문장은 이 절로 정정한다.

### 9.2. 대응 설계 — 판정

네 대응 후보를 검토했다. 결론은 **1차(request body 필드명 방출) 즉시 채택,
2차(엔드포인트 sub-chunking) 재측정 게이트 보류, 3차(임베딩 컨텍스트 확장)·
4차(request body 전용 보조청크) 기각**이다.

#### 채택 — 1차: `build_endpoint_chunk_text`에 request body 필드명 방출 (근본 수정·최소 diff)

`build_endpoint_chunk_text`에 `Params:`와 동형의 `Body:` 줄을 추가한다 —
`endpoint.request_body.schema["properties"].keys()`를 읽어 `Body: currency, amount,
customer, ...`. `build_schema_chunk_text`(`chunk_builder.py:70-73`)가 이미 쓰는
프로퍼티 추출 패턴 그대로다.

- **누락된 신호 복구**: currency/automatic_payment_methods/draft가 청크에 실려
  FTS(영문 변형·영문 질의)와 벡터 양쪽에서 후보를 만든다. C7의 진짜 병목(색인 자체
  누락)을 없앤다.
- **필드명만, 설명은 넣지 않는다**: 기존 `Params:` 포맷과 동형으로 **이름만**
  나열한다. Stripe request body 프로퍼티 설명은 장문이라 이걸 넣으면 38-필드
  엔드포인트가 즉시 480/512를 넘겨 §9.1이 반증한 truncation 문제를 **이번엔 진짜로**
  만든다. 이름만이면 38필드도 ~수백 토큰 이내로 유계.
- **$ref-only body 한계**: 인라인 `properties`가 없고 `$ref`만 있는 body는 이름을
  못 뽑는다(빈 줄). Stripe(form-urlencoded 인라인)·GitHub pulls(application/json 인라인)는
  프로퍼티가 인라인이라 커버되므로 1차 범위에서 $ref 해소는 하지 않는다(YAGNI —
  필요 시 후속). 이 천장은 구현 시 주석으로 명시.

이 방향은 청크당 1개(=엔드포인트 ref_id 1개) 불변(doc/28 §3.1 ground-truth 안정성
근거)을 **깨지 않는다** — 기존 단일 청크 텍스트를 늘릴 뿐이다.

#### 조건부 보류 — 2차: 엔드포인트도 sub-chunking 대상으로 확장 (전제 뒤집기)

doc/28 §3.1·§6은 "엔드포인트는 sub-chunking 대상이 아니다(=정확히 청크 1개)"를
ground-truth 불변의 근거로 삼았다. **이 전제는 지금 뒤집지 않고, 1차 재측정 뒤로
게이트한다.** 근거:

- 1차 이전에는 뒤집을 이유가 없다 — 현재 문제 청크는 소형이라 분할할 것이 없다.
- 1차 적용 후에야 전제가 압박받는다: `Body:` 필드명을 실으면 payment_intents(38필드)
  급 대형 body 엔드포인트가 480 상한을 **처음으로** 넘길 수 있다. 그때 비로소
  "엔드포인트 청크도 480 초과 시 `build_section_chunks` 기계로 분할" 확장이
  정당해진다(섹션 sub-chunking 배선 재사용).
- 단 이 확장은 ref_id 1:N 청크가 되어 doc/28 §3.1 불변을 깬다. 채점은 `(method,path)`
  단위라 여전히 안전하나(ref_id 최초 일치), 그 트레이드오프를 지불할지는 실측
  overflow 건수를 본 뒤 결정한다.

**판정: 1차 적용 후 프리즈 코퍼스에서 엔드포인트 청크 토큰 분포를 측정
(`diagnose_long_sections.py`의 엔드포인트판 or 임시 카운트)하고, 480 초과 엔드포인트가
유의미하게 나올 때만 2차를 당긴다.** 안 나오면 청크당 1개 불변을 유지한다.

#### 기각 — 3차: 임베딩 모델 컨텍스트(512) 확장

병목이 truncation이 아니라 텍스트 미방출이므로(§9.1) 컨텍스트를 키워도 없는 필드가
생기지 않는다. 게다가 FTS(키워드 arm)는 stored text 기반이라 애초에 512 컨텍스트와
무관하다. 마이그레이션 비용만 크고 C7을 못 고친다. 기각.

#### 기각 — 4차: request body 전용 보조 청크 분리

body 필드를 별도 `chunk_type`으로 쪼개 색인하는 안. 1차(단일 청크에 인라인)가 같은
발견성을 더 싸게 달성한다:
- 청크 수·색인 비용 증가, 그리고 엔드포인트 ref_id 1:N → doc/28 §3.1 불변 파손을
  1차 없이도 즉시 유발.
- body 필드가 별도 청크로 분리되면 검색 필터(`chunk_type=="endpoint"`, doc/28 §1.3)에
  새 타입 추가 배선까지 파생 — 레버리지 대비 표면적이 크다.

인라인이 overflow할 만큼 커지는 경우는 2차(sub-chunking)로 흡수되므로 4차의 고유
효용이 없다. 기각.

### 9.3. 후속 조치 (developer 배정 대상)

1. `build_endpoint_chunk_text`(`chunk_builder.py`)에 `Body:` 줄 추가 —
   `request_body.schema["properties"]` 키를 `Params:`와 동형으로 이름만 나열.
   $ref-only body는 빈 줄(주석으로 천장 명시). 단위 테스트: request body 있는
   엔드포인트 청크에 필드명이 실리는지 assert 1개.
2. 재색인 후 C7 재측정 — q18·q19는 한글이므로 §8의 `--with-variants`(영문 변형)
   조건과 함께 측정해야 request body 복구 효과가 교차언어 붕괴에 가려지지 않는다.
   (q18→`specify currency when creating a charge`, q19→`set automatic payment methods
   on a payment intent` 급 영문 변형 부여.)
3. 재측정 시 엔드포인트 청크 토큰 분포도 함께 산출 — 480 초과 엔드포인트 건수가
   2차(sub-chunking 확장) 게이트의 입력이다.

> C2(교차언어)·C7(request body 누락)은 독립 축이나, C7 질의가 한글이라 2번 재측정은
> 반드시 §8 변형 배선과 겹쳐 수행한다. 그래야 두 축의 효과가 분리 관측된다.

## 10. §9.3 배선 결과 및 C7 재측정 (developer)

§9.2 판정(1차 채택)에 따라 세 가지를 배선했다.

### 10.1. `Body:` 줄 배선

`build_endpoint_chunk_text`(`chunk_builder.py`)에 `Params:`와 동형인 `Body:` 줄을
추가했다 — `endpoint.request_body.schema["properties"]` 키를 `build_schema_chunk_text`가
쓰는 것과 같은 패턴(`sorted(...)`)으로 이름만 나열한다. 인라인 `properties`가 없는
$ref-only body는 `Body:` 줄 자체를 생략한다(구현 docstring에 이 천장을 명시).
단위 테스트(`test_endpoint_chunk_text_includes_inline_request_body_field_names`)로
인라인 request body 필드명이 청크 텍스트에 실리는지 확인 — RED 확인 후 구현, GREEN.

```
tests/unit/test_chunk_builder.py -q → 8 passed (신규 1건 포함)
```

### 10.2. C7 재측정

프리즈 코퍼스(`run_corpus_eval.py --strategy both`, n=20)로 세 조건을 비교했다.
"수정 전"은 §1/§9.1 원 측정값, "수정 후·변형 없음"/"수정 후·변형 포함"은 이번
`Body:` 배선 반영 후 재실행 값이다(변형 포함은 q18→`specify currency when creating
a charge`, q19→`set automatic payment methods on a payment intent`을 `queries.json`에
추가하고 `--with-variants`로 실행).

| 조건 | fallback R@3 | fallback MRR | rrf R@3 | rrf MRR |
|---|---|---|---|---|
| 수정 전(§1) | 0% | 0.000 | 0% | 0.067 |
| 수정 후·변형 없음 | 0% | 0.167 | 33% | 0.194 |
| 수정 후·변형 포함 | 0% | 0.083 | 33% | 0.228 |

질의별 순위(fallback / rrf):

| # | 질의 | 정답 | 수정 전 | 수정 후·변형 없음 | 수정 후·변형 포함 |
|---|---|---|---|---|---|
| q18 | 결제 생성 시 통화 단위 지정 | POST /v1/charges | 미검출/미검출 | 미검출/미검출 | 미검출/**4** |
| q19 | 결제 인텐트에 자동 결제수단 설정 | POST /v1/payment_intents | 미검출/미검출 | **4/4** | 미검출/10 |
| q20 | 풀리퀘스트를 draft로 생성 | POST /repos/{owner}/{repo}/pulls | 미검출/5 | 4/3 | 4/3 |

**해석**:

- **`Body:` 줄 자체가 주 효과다.** 변형 없이(한글 질의 그대로) q19가 미검출 →
  rrf/fallback 모두 4위로 올라섰다 — request body 필드명 색인 복구가 C7의 진짜
  원인이었다는 §9.1 진단이 실측으로 확인된다. q20(GitHub, `draft`가 인라인
  프로퍼티)도 5위 → 3위로 개선.
- **q18은 한글 상태로는 여전히 미검출**이다 — 영문 변형을 줘야 rrf 4위로
  진입한다. `currency`가 20개 프로퍼티 중 하나로 묻히는 데다 C2(교차언어)
  붕괴가 겹쳐(§7) 한글 질의 단독으로는 신호가 약함.
- **q19는 변형을 주면 오히려 악화**된다(4/4 → 미검출/10). `automatic_payment_methods`
  변형 질의가 동일 엔드포인트(payment_intents, 38-필드 대형 body)의 다른 프로퍼티
  토큰과 벡터 유사도를 나눠 가지는 것으로 추정 — 원인 분리는 안 됐고 표본 1건이라
  결론 보류(§8.2와 같은 종류의 "완전 회복은 아님" 신호). 필드가 많은 대형 body일수록
  단일 필드 타겟 질의가 다른 필드 토큰에 흡수될 여지가 크다는 가설.
- **종합**: rrf MRR 기준 0.067 → 0.194(변형 없음) → 0.228(변형 포함)로 단조 개선,
  R@3는 0% → 33%(q20 진입) → 33%(정체, q18 진입이 q19 퇴보로 상쇄). C7은 truncation이
  아니라 색인 누락이었다는 §9.1 판정이 재측정으로 뒷받침된다.

### 10.3. 엔드포인트 청크 토큰 분포 (2차 sub-chunking 게이트 입력)

`Body:` 배선 반영 후 프리즈 코퍼스 전체 엔드포인트 청크를 임베딩 토크나이저
(`intfloat/multilingual-e5-small`)로 실측(DB 적재 없이 파서+`build_endpoint_chunk_text`
직결, 1회성 측정 — 커밋된 스크립트 아님).

| 코퍼스 | 엔드포인트 수 | 480토큰 초과 | 최대 토큰 |
|---|---|---|---|
| Stripe | 589 | 5건 | 1087 |
| GitHub | 1220 | 40건 | 1394 |

합계 45/1809건(≈2.5%)이 480토큰을 넘는다. 512 임베딩 상한 자체를 넘는 것도
다수(GitHub 최대 1394는 512의 2.7배) — §9.2 2차(엔드포인트 sub-chunking) 게이트
판단의 입력 수치이며, 2차 실행 여부는 이 절 범위 밖(architect/lead 판단).

## 11. 2차(엔드포인트 sub-chunking) 게이트 판정 (§9.2 · §10.3 후속)

§9.2가 세운 게이트 — "480 초과 엔드포인트가 **유의미하게** 나올 때만 2차를 당긴다" —
에 §10.3 실측(45/1809, 최대 1394)을 대입한 architect 판정이다.

**판정: 게이트 미달. 2차(엔드포인트 sub-chunking)는 보류 유지.** 대신 불변을
깨지 않는 저비용 하드닝(11.2)을 채택한다.

### 11.1. 보류 근거

1. **C7 플래그십 타겟은 1차 후 애초에 안 잘린다.** 프리즈 스펙에 `Body:` 배선을
   반영해 토큰 실측: `POST /v1/charges` 172, `POST /v1/payment_intents`(38-필드 대형
   body) **440**(+prefix 9 = 449 < 480). C7이 겨냥한 대형 body 엔드포인트 자체가
   512 상한 아래다 — 이들의 잔여 실패(§10.2 q18 교차언어·q19 벡터 희석)는
   truncation이 아니라 다른 축이며 sub-chunking으로 안 고쳐진다.
2. **45건은 소수(2.5%)이고, overflow 집합에 귀속되는 recall 실패가 하나도 측정되지
   않았다.** 실패 C7 질의의 타겟 엔드포인트는 전부 비-overflow다. 게이트 문구의
   "유의미하게"는 건수가 아니라 실제 검색 손실을 요구하는데, 그 증거가 없다.
3. **overflow 드라이버가 종종 body 필드가 아니라 free-text description이다.**
   `POST /repos/{owner}/{repo}/pulls`는 body 8필드뿐인데 656토큰 — 장문 description이
   상한을 넘긴다. body 필드 리스트를 쪼개는 sub-chunking은 description-driven
   overflow에 **틀린 도구**다(45건의 driver 분포는 §10.3에서 분리 안 됨).
4. **2차 비용이 실재한다.** 엔드포인트 청크 1:N은 `ref_id` = 정확히 청크 1개
   불변(doc/28 §3.1, ground-truth 안정성 근거)을 깨고 색인 청크 수·검색 융합
   복잡도를 늘린다. 측정된 이득 0인 상태에서 선지불 안 함(YAGNI).

### 11.2. 채택 — 저비용 하드닝: 청크 필드 순서 재배치

SentenceTransformer는 **입력 꼬리**를 잘라낸다. 현재 `build_endpoint_chunk_text`
순서는 `header / description / Tags / Params / Body / Responses`라, overflow 시
질의가 정확히 겨냥하는 **고신호 토큰(Params·Body 필드명)이 먼저 잘린다.**

`header`를 선두로 고정한 채 **구조 필드(Params·Body)를 free-text description보다
앞에** 놓도록 재배치한다 — 예: `header / Params / Body / Tags / description /
Responses`. overflow가 나면 꼬리의 저신호 산문(description)이 먼저 잘리고
필드명은 보존된다. 비-overflow 97.5%엔 무영향, overflow 45건엔 정확히 C7-급
필드 발견성을 지킨다. 단일 청크 내 재배치라 `ref_id` 1:1 불변을 **안 깬다**.

### 11.3. 게이트 재무장(2차를 다시 검토할 조건)

11.2 적용 후에도 **overflow 엔드포인트에서 recall 실패가 실제로 측정되면** 2차를
재검토한다. 그때 §10.3에 빠진 **overflow 드라이버 분포(body-필드수 vs
description-길이)**를 먼저 산출해 도구를 고른다:
- body-필드-driven이 지배적 → 엔드포인트 sub-chunking(`build_section_chunks` 재사용).
- description-driven이 지배적 → description 토큰 캡(재배치와 결합, 더 싼 단일-청크 수정).

> §10.2 q19 벡터 희석("대형 body에서 단일 필드 타겟이 형제 토큰에 유사도를 나눠
> 가짐")은 truncation과 **독립 축**이다 — payment_intents는 overflow도 아니다.
> 이 게이트를 움직이지 않으며, 필요 시 별도 검토 대상(질의셋 확장 후).

### 11.4. §11.2 배선 결과 (developer)

`build_endpoint_chunk_text`(`chunk_builder.py`) 필드 순서를
`header / description / Tags / Params / Body / Responses` →
`header / Params / Body / Tags / description / Responses`로 재배치했다.
docstring 예시·근거 설명도 순서에 맞게 갱신.

- 단일 청크 내 필드 재배치라 `ref_id` 1:1 불변은 그대로 유지(§9.2·doc/28 §3.1).
- 신규 단위 테스트(`test_endpoint_chunk_text_places_structured_fields_before_description`)로
  header 다음 Params→Body가 description보다 먼저 오는지 확인 — RED 확인 후 구현, GREEN.
- 기존 단위 테스트는 전부 `in text`(포함 여부) 방식이라 순서에 의존하지 않아 갱신 불필요했음.
  `build_endpoint_chunk_text` 호출부(`compare_chunking.py`)도 텍스트 앞에 접두만
  붙이는 래퍼라 순서 가정 없음 — 영향 없음.

```
tests/unit/test_chunk_builder.py -q → 9 passed (신규 1건 포함)
```
