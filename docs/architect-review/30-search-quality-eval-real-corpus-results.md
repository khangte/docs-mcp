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
