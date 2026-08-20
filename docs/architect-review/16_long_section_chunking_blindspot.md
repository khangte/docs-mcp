# 긴 섹션의 무상한 단일 청크화 + 512토큰 조용한 truncation — 맹점 검토

- 상태: **방향 검토 only** — 코드 미수정. 착수 여부 lead/사용자 판단 대기.
- 일시: 2026-08-12
- 작성: architect
- 요청 경위: 사용자 "청크 토큰 단위" 질문 → lead가 `chunk_builder.py`/`markdown_parser.py` 직접 확인 → 긴 섹션 무상한 청크화 + 512 조용한 truncation 맹점 발견 → architect에 해결 방향 검토 위임.
- 선행 규율: `docs/09`(재색인 비용 게이트·측정 우선), `docs/12`(서버 자동판단 배제·클라 위임 원칙), `docs/13`(작은 픽스처 가짜개선 교훈), `docs/15`(측정 없이 착수 금지)
- 대상 코드(검토 근거): `app/services/parser/markdown_parser.py`, `app/services/parser/document_router.py`, `app/services/indexer/chunk_builder.py`, `app/services/indexer/indexer_service.py`, `app/services/indexer/embedding_provider.py`, `app/models/openapi.py`

---

## 0. 결론 먼저

1. **맹점은 실재한다** — 헤딩 단위 섹션에 길이 상한이 없어(`markdown_parser`·`chunk_builder`), 긴 섹션이 임베딩 모델 `max_seq_length=512`를 넘으면 `SentenceTransformer.encode`가 **뒷부분을 조용히 잘라** 임베딩한다(경고·에러 없음).
2. **단, lead의 "순수 Markdown 전용" 프레이밍은 정정 필요** — `document_router`가 **pdf/docx도 markdown 파서로 섹션화**한다(`:79-80`). 추출 텍스트는 헤딩이 없거나 드물어 **문서 전체가 "개요" 섹션 1개**가 되기 쉽다 → **긴 PDF/DOCX가 순수 Markdown보다 더 위험**하다. 맹점의 진짜 이름은 "긴 Markdown 섹션"이 아니라 **"헤딩으로만 쪼개는 파서 + 헤딩 희소 입력"**이다.
3. **그러나 심각도는 생각보다 한정적** — truncation은 **임베딩 시점에만** 일어난다. stored `text`와 `text_tsv`(키워드 FTS 생성 컬럼)는 **full 텍스트로 온전**하다(`indexer_service:103-111`). 따라서 잘리는 건 **벡터 arm의 발견 가능성뿐**이고, ①키워드 arm은 섹션 전체를 여전히 색인하며 ②`get_endpoint_details`/섹션 조회로 반환되는 실제 본문도 온전하다. 손실 = "512토큰 이후 내용을 **벡터 검색으로 못 찾는다**"에 국한.
4. **판단**: 이건 **투기 수정 금지 대상**(docs/13·15 규율). "현재 코퍼스에 512 넘는 섹션이 실제로 있는가"를 **먼저 읽어 확인(Phase 0)** 하고, 없으면 값비싼 sub-chunking은 YAGNI. 다만 **truncation을 조용히 두는 것 자체가 별개 결함**이라, 측정 결과와 무관하게 **관측성(경고 로깅) 추가는 저비용·상시 정당**하다(Phase 1). sub-chunking(Phase 2)은 측정이 실사례를 보이면 그때 게이트 통과 후 착수.
5. **원칙 정합**: 토큰 상한 분할은 **기계적 길이 처리**지 의미 판단이 아니므로 "서버 자동판단 배제" 원칙과 **무충돌**(그 원칙은 질의재작성·자동 멀티홉을 겨눔). overlap 도입은 이 프로젝트 구조상 이득이 얇아 **초기 비권장**.

---

## 1. 맹점의 정확한 해부

### 1-1. 청킹은 구조 단위, 길이 상한 없음 (확인)

- `markdown_parser.parse_document`: `^#{1,6}\s+` 헤딩마다 `_flush()` → 헤딩 사이 전부를 한 `ParsedSection.content`로 묶는다. **길이 상한·분할 없음.** 첫 헤딩 이전은 "개요" 섹션.
- `chunk_builder.build_section_chunk_text`: `f"# {title}\n{content}"` — content를 **그대로** 청크 텍스트로. 자르지 않음.
- `build_chunks`: 섹션 1개 = `BuiltChunk` 1개. 슬라이딩윈도우·overlap 개념 자체 없음(lead 확인과 일치).

### 1-2. 조용한 truncation 지점 (확인)

- `indexer_service:103` `texts = [c.text for c in built_chunks]` → `:104` `embed_documents(texts)`.
- `LocalEmbeddingProvider.embed_documents`가 `SentenceTransformer.encode(prefixed, normalize_embeddings=True)` 호출. ST는 `max_seq_length`(로컬 실측 512) 초과 입력을 **기본 truncation=True로 조용히 절단** — 에러·경고 없음. 이후 토큰은 임베딩에 **전혀 기여 안 함**.

### 1-3. 왜 pdf/docx가 더 위험한가 (lead 프레이밍 정정)

- `document_router.parse_document:79-80`: `doc_type in {"pdf","docx"}` → **`markdown_parser.parse_document(raw)`**. 즉 pdf/docx 추출 텍스트도 **동일 헤딩 파서**로 섹션화된다.
- 추출 텍스트는 `#` 헤딩이 **없거나 드물다**(PDF 텍스트 추출은 마크다운 헤딩을 보존하지 않음). 헤딩이 하나도 없으면 **문서 전체가 "개요" 단일 섹션** → 여러 페이지가 청크 1개 → 512 훌쩍 초과. **순수 Markdown(보통 규칙적 헤딩 존재)보다 pdf/docx가 이 맹점의 최악 케이스.**
- CSV는 `csv_parser`가 **행 단위** 섹션(짧음), 엔드포인트/스키마도 짧음(실측 avg 40토큰) — 해당 없음(lead 확인 일치).

### 1-4. 심각도 경계 — 벡터 arm 발견성만 손실 (중요)

동일 `built.text`가 두 갈래로 쓰인다(`indexer_service:103-111`):

| 소비처 | 입력 | truncation? |
|---|---|---|
| 임베딩 벡터(`embedding` 컬럼) | `embed_documents(texts)` → `encode()` | **512에서 절단** |
| stored `text` 컬럼 | `text=built.text` | full(무손상) |
| `text_tsv`(키워드 FTS) | `text` 컬럼 기반 생성 컬럼 | full(무손상) |
| 섹션 본문 반환(`ApiSection.content`) | 파서 원본 | full(무손상) |

→ 결과: **512토큰 이후 내용은 "벡터 검색으로 못 찾을 뿐"**, 키워드 FTS로는 검색되고, 일단 찾으면 반환 본문은 온전하다. RRF 하이브리드라 **키워드 arm이 부분 보완**한다. 이 경계가 "즉시 큰 사고"가 아니라 "긴 문서 벡터 recall의 조용한 저하"로 심각도를 낮춘다 — sub-chunking을 서두를 이유를 줄이고, 측정·관측성 우선을 정당화한다.

---

## 2. 방향 검토 (lead의 3개 질문에 대응)

### 2-1. 실제로 문제인가 — Phase 0: 측정 우선 (docs/13·15 규율)

코드만으로는 **알 수 없다** — 로컬 단일 사용자 코퍼스라 실제 등록 문서에 의존한다. 투기 수정 금지 원칙(docs/13 문맥청킹이 픽스처 아티팩트로 소멸, docs/15가 "측정 없이 착수 금지")을 그대로 적용한다.

**Phase 0 진단(읽기 전용, 프로덕션 무변경)**: 현재 DB의 `api_chunk` 중 `chunk_type='section'` 행을 훑어, 각 `text`를 임베딩 토크나이저(`intfloat/multilingual-e5-small`)로 토큰화해 **512 초과 건수·최대 토큰·초과 문서(doc_type별)**를 집계하는 일회성 스크립트. 산출:
- 512 초과 섹션 **0건** → 맹점은 현재 코퍼스에서 **비활성**. sub-chunking은 **YAGNI**(Phase 2 보류). Phase 1(관측성)만 저비용으로 넣어 미래 방어.
- 초과 **존재** → 몇 건이 얼마나 초과하는지(꼬리 길이)와 doc_type 분포로 Phase 2 착수·설계를 정량 판단. pdf/docx에 몰리면 1-3절 예측 확증.

이 측정 자체가 값싸고(읽기 전용, 재색인 0), **어떤 방향을 고르든 선행 근거**가 된다.

### 2-2. 고칠 가치가 있다면 — 3방향 비교

| 방향 | 내용 | 비용 | 이 프로젝트 적합성 |
|---|---|---|---|
| **(A) 관측성만**(로깅/경고) | 임베딩 입력이 `max_seq_length` 초과 시 **경고 로그**(doc_id·section·토큰수). 청킹·재색인 무변경 | **최저**(로깅 1개, 재색인 0) | ✅ **상시 정당**. "조용한 truncation"을 "알려진 truncation"으로. 결함의 절반(가시성 부재)을 즉시 해소 |
| **(B) sub-chunking**(토큰 상한 분할) | 섹션이 상한 초과 시 문단/문장 경계로 **복수 sub-청크**(`section_id:0/:1…`)로 분할. 각 sub가 독립 임베딩 | **중~큼**(청킹 로직 + ref_id 스킴 + 섹션 청크 전면 재색인) | 🔎 **조건부**. Phase 0가 실사례 보이면. 벡터 recall 손실을 근본 해소 |
| **(C) overlap 도입** | sub-청크 간 50~150토큰 겹침(일반 RAG 컨벤션) | **큼**(B + 중복 저장·청크수 증가) | ⚠️ **초기 비권장**(아래) |

### 2-3. overlap이 이 프로젝트에 맞는가 — 초기 비권장

일반 RAG(300~800+50~150 overlap)는 **의미 단위가 청크 경계에 잘려 문맥이 끊기는 것**을 overlap으로 잇는다. 이 프로젝트는 다르다:
- 청크는 **구조(섹션) 단위**이고, **반환 본문은 stored `ApiSection.content`에서 온전히** 나온다(1-4절). 즉 "경계에서 문맥이 잘려 답이 불완전"해지는 일반 RAG 문제는 **여기선 반환 단계에 없다** — 벡터는 발견용, 본문은 별도 저장.
- overlap의 유일한 이득은 "질의가 sub-청크 경계 부근 텍스트에 매칭될 때 두 sub 모두 후보로 잡히는" 경계 recall인데, 그건 **후보 피더+키워드 arm 보완** 구조에서 한계이득이 작다.
- 비용은 확실(중복 저장·청크수·재색인 증가). **docs/09 P4(K스윕 null result)·docs/13(문맥청킹 무효)과 같은 교훈**: 컨벤션이라고 넣지 말고, 경계 miss 실패가 **실측되면** 그때. → **B를 overlap 없이 먼저**, overlap은 측정 트리거 시 조건부.

### 2-4. sub-chunking(B) 설계 시 유의점 (착수하게 되면)

- **분할 경계**: 문단(`\n\n`)/문장 우선, 그래도 초과하면 토큰 하드컷. LLM으로 분할점 판단 금지(원칙 위배·불필요).
- **ref_id 스킴**: `{document.id}:section:{idx}:{sub}` 식. 복수 sub가 **같은 `ApiSection`을 가리키도록** 매핑 유지 → 검색이 여러 sub를 잡아도 **반환·dedupe는 섹션 단위**(후보 피더 정체성 유지, docs/12 축2와 정합).
- **재색인**: 섹션 청크 전면 재색인(docs/09 P2·docs/12-B·docs/15와 동일 규모). Phase 0 측정이 이 비용의 전제.
- **상한 값**: `max_seq_length`(512)를 상수로 두되 토크나이저 실측 기반 안전 마진(예: 480). **설정 표면은 늘리지 않는다**(YAGNI, docs/09 P4 정신).

### 2-5. 원칙 정합성 점검

- **서버 자동판단 배제(docs/12)**: 토큰 상한 분할은 **기계적 길이 처리**, 질의재작성·자동 멀티홉 같은 **의미 판단 아님** → 무충돌. (경계 판단에 LLM을 쓰면 위배 — 안 쓴다.)
- **재색인 비용 게이트(docs/09·15)**: B/C는 재색인 동반 → Phase 0 측정으로 실사례 확인 후 착수. A는 재색인 0이라 게이트 불필요.
- **docs/13 교훈**: 작은 근거로 큰 변경 금지 → Phase 0 선행이 이 규율의 직접 적용.
- **MCP 판단 위임(mcp-delegate)**: 색인 시점 처리라 질의 LLM 위임과 무관.

---

## 3. 권장안 — 단계적

| 단계 | 항목 | 조건 | 재색인 | 성격 |
|---|---|---|---|---|
| **Phase 0** | 512 초과 섹션 진단(읽기 전용 스크립트) | 지금 | 0 | 측정 — 방향 판단 근거 |
| **Phase 1** | 임베딩 입력 초과 시 **경고 로깅** | Phase 0 결과 무관, 저비용이라 상시 | 0 | 관측성 — 조용한 truncation을 가시화 |
| **Phase 2** | sub-chunking(overlap 없이, 토큰 상한 분할) | **Phase 0가 512 초과 실사례를 보일 때만** | 섹션 청크 전면 | 벡터 recall 근본 해소 |
| — | overlap | Phase 2 후 경계 miss 실패가 실측될 때만 | — | **초기 비권장** |

- **Phase 0 + Phase 1은 저비용이라 지금 묶어 처리 가능**(측정 + 관측성). 재색인 0.
- **Phase 2는 Phase 0 수치에 종속** — 초과 0건이면 착수 안 함(YAGNI). 초과가 pdf/docx에 몰리면 우선순위↑.

> 핵심: 이 맹점은 **실재하나 심각도가 벡터 arm 발견성에 국한**되고(키워드 arm·반환 본문 온전), **현 코퍼스에 실제로 512 초과 섹션이 있는지 미확인**이다. 따라서 정답은 "일반 RAG 컨벤션(sub-chunk+overlap)을 바로 도입"이 아니라 **①먼저 측정(Phase 0) ②조용한 truncation을 가시화(Phase 1, 상시) ③측정이 실사례를 보이면 overlap 없는 sub-chunking(Phase 2)**. overlap은 이 프로젝트의 "발견용 벡터 / 별도 저장 본문" 구조에서 이득이 얇아 초기 비권장.
