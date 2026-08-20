# 긴 섹션 sub-chunking (docs/16 Phase 2) — 설계안

- 상태: **설계 only** — 코드 미수정. 구현 착수 판단 lead/사용자 대기.
- 일시: 2026-08-13
- 작성: architect
- 선행: `docs/architect-review/16_long_section_chunking_blindspot.md` (Phase 0 진단 + Phase 1 경고 로깅 완료). Phase 0 측정은 현 코퍼스에서 512 초과 섹션 **0건** → Phase 2는 YAGNI 게이트로 보류 상태였음. 본 문서는 lead 지시로 Phase 2 **설계만** 선착수.
- 대상 코드: `app/services/parser/markdown_parser.py`, `app/services/indexer/chunk_builder.py`, `app/services/indexer/indexer_service.py`, `app/services/indexer/embedding_provider.py`, `app/repositories/chunk_repository.py`, `app/services/search/rrf.py`

---

## 0. 결론 먼저

1. **sub-chunking은 파서가 아니라 chunk 빌드 시점에 넣는다.** `markdown_parser`는 "구조(헤딩) 분리" 단일 책임 유지 — 길이는 모른다. `ParsedSection` ↔ `DocumentSection`은 계속 1:1(저장 본문 = full, 무손상). 한 섹션이 임베딩 토큰 상한을 넘을 때만 **build 단계에서 복수 임베딩 청크**로 쪼갠다.
2. **ref_id = section_id를 모든 sub가 공유** → 검색·dedupe·반환이 전부 섹션 단위로 유지된다. RRF `_dedupe_first`(rrf.py:30)가 같은 ref_id 중복을 **첫 등장만 남겨 자동 병합** → 검색/반환 코드 **무변경**. `Chunk.id`는 이미 문서 내 전역 인덱스라 sub가 늘어도 유일. **스키마 마이그레이션 불필요.**
3. **분할 기준: 계층적 그리디** — 문단(`\n\n`) 경계 우선 팩킹 → 초과 문단은 문장/줄 경계 → 그래도 초과면 토큰 하드컷. LLM 판단 없음(기계적 길이 처리, docs/12 원칙 무충돌).
4. **overlap 없음** (docs/16 §2-3 결론 유지: 발견용 벡터 / 별도 저장 본문 구조라 경계 recall 이득 얇음). 업그레이드 경로만 표기.
5. **게이트 주의**: Phase 0 측정이 0건이므로 **구현+재색인 착수는 여전히 실사례 트리거 대기**가 원칙(docs/09·15). 설계는 값싸고 되돌릴 수 있어 선착수 정당하나, **재색인을 동반하는 구현 병합은 별도 게이트**다. lead 판단 필요(§6).

---

## 1. 통합 지점 — 왜 파서가 아니라 chunk_builder인가

| 계층 | 현재 책임 | sub-chunking 후 |
|---|---|---|
| `markdown_parser` | `^#{1,6}` 헤딩 분리, 첫 헤딩 이전 = "개요" | **무변경**. 구조만 안다, 토큰 모른다 |
| `DocumentSection`(저장) | 섹션 1개 = 행 1개, `content`=full | **무변경**. 반환 본문·FTS는 계속 full |
| `chunk_builder` | 섹션 1개 = `BuiltChunk` 1개 | **여기서 분할**. 섹션 1개 → sub `BuiltChunk` N개 |
| `Chunk`(임베딩 행) | `id=doc:chunk:{전역idx}`, `ref_id=section_id` | id 그대로 유일, **ref_id 그대로 section_id** |

핵심: **저장/반환 경로는 손대지 않는다.** truncation은 임베딩 벡터에만 생기던 손실이므로(docs/16 §1-4), 고칠 곳도 임베딩 청크 생성 한 곳뿐. `DocumentSection.content`는 full로 남아 `ApiSection.content` 반환·`text_tsv` FTS는 지금처럼 온전하다.

---

## 2. 분할 알고리즘 (계층적 그리디, overlap 없음)

토큰 상한 `T`(= `_TOKEN_WARNING_THRESHOLD`, 현재 480 재사용). 안전마진 32(512−480)가 encode 시 붙는 `"passage: "` 접두사 + special 토큰을 흡수한다.

```
build_section_chunks(section, count_tokens, T):
    title_prefix = f"# {section.title}\n"  (title 있을 때만)
    budget = T - count_tokens(title_prefix)          # sub마다 title 반복되므로 예산에서 차감
    full = build_section_chunk_text(section)          # 기존 포맷 재사용
    if count_tokens(full) <= T:
        return [full]                                # 상한 이하 → 현재 동작 그대로 (1청크)
    parts = split_by_paragraph(section.content, budget, count_tokens)
    return [title_prefix + p for p in parts]

split_by_paragraph(content, budget, count_tokens):
    paras = content.split("\n\n")
    buf, out = [], []
    for para in paras:
        if count_tokens(para) > budget:              # 문단 자체가 초과
            flush(buf) ; out += split_by_sentence(para, budget, count_tokens)
            continue
        if count_tokens(join(buf+[para])) > budget:  # 넣으면 초과 → 현재까지 확정
            flush(buf) ; buf = [para]
        else:
            buf.append(para)
    flush(buf)
    return out

split_by_sentence(para, budget, count_tokens):
    # 문장 경계(정규식 [.!?。]\s+ 또는 줄바꿈)로 재그리디 팩킹.
    # 단일 문장도 budget 초과 시 → hard_cut(문장, budget) (토큰 단위 슬라이스)
```

**경계 우선순위**: 문단(`\n\n`) → 문장/줄 → 토큰 하드컷. 상위 경계에서 예산 안에 들어가면 하위로 안 내려간다. 대부분 문단 팩킹에서 끝난다.

**설계 의도**:
- 각 sub는 `# {title}` 을 머리에 달아 **문맥 앵커 유지**(제목이 임베딩에 기여). 대신 title 토큰을 예산에서 뺀다.
- 그리디라 sub 개수 최소화(청크 폭증 억제). overlap 0이라 중복 저장 없음.
- 순수 함수 + 토큰 카운터 주입 → 페이크 카운터로 단위테스트 용이.

---

## 3. 토큰 카운터 주입 (chunk_builder를 모델에서 분리)

`chunk_builder`는 모델 의존이 없다(순수). 토큰 카운트는 임베딩 프로바이더만 안다. → **카운터를 콜러블로 주입**한다.

- `EmbeddingProvider` 프로토콜에 선택 메서드 `count_tokens(text) -> int` 추가.
  - `LocalEmbeddingProvider`: `len(self._encoder.tokenizer.encode(text, add_special_tokens=True))`. **기존 `_warn_if_exceeds_threshold`도 이걸 호출하도록 정리**(DRY — 현재 인라인 중복).
  - `HashEmbeddingProvider`: 길이 상한 없음 → `count_tokens` 미구현(또는 `None` 반환).
- `build_chunks(..., count_tokens: Callable[[str], int] | None = None)`:
  - `None`이면 **분할 안 함**(현재 동작 = 하위호환, 테스트·해시 프로바이더 경로).
  - 있으면 §2 알고리즘 적용.
- `IndexerService.index_document`: `count_tokens = getattr(self._embedding_provider, "count_tokens", None)` 를 `build_chunks`에 전달. 나머지(`texts`/`labels`/`embeddings` zip 루프)는 sub 개수만큼 자연히 늘어날 뿐 **로직 무변경**.

토크나이저 없는 인코더(일부 페이크)는 `_warn_if_exceeds_threshold`처럼 조용히 skip.

---

## 4. ref_id / dedupe 정합 (변경 없음이 핵심)

- sub `BuiltChunk`들은 **모두 `chunk_type="section"`, `ref_id=section_id`** 로 동일.
- `Chunk.id`: `indexer_service`의 `f"{document.id}:chunk:{idx}"` (전역 enumerate) → sub가 늘어도 유일. 스키마 변경 0.
- **검색측 무변경 근거**:
  - 벡터 arm: `search_by_vector`는 `chunk_type=="endpoint"`만 조회(chunk_repository.py:291) → **section sub-chunk는 현재 벡터 검색 경로에 아예 안 탄다**. (키워드 arm `search_endpoint_by_text`(chunk_repository.py:184)도 동일하게 `endpoint`만 통과시켜 섹션은 키워드 검색에도 안 탄다 — 즉 섹션은 벡터·키워드 둘 다 미배선이며, 이는 "무변경" 근거를 오히려 강화한다.) 즉 이 설계는 섹션 검색이 배선될 때를 대비한 **색인측 준비**다. 배선 시 여러 sub가 같은 ref_id로 잡혀도 RRF `_dedupe_first`가 첫 등장만 남겨 섹션 단위로 접힌다.
  - dedupe: `rrf.py:30 _dedupe_first`가 ref_id 기준 → 같은 섹션의 sub 중복은 자동 1건화. **추가 dedupe 코드 불필요.**
- 반환·정체성: 후보는 섹션(=`DocumentSection`) 단위 그대로. docs/12 축2(후보 피더 정체성 유지)와 정합.

---

## 5. 재색인 / 상수 / 관측성

- **재색인**: 섹션 청크 전면 재색인(docs/09 P2·docs/12-B 규모). Phase 0 측정이 이 비용의 전제 — §6 게이트.
- **상수**: `_TOKEN_WARNING_THRESHOLD`(480) 를 분할 상한으로 **재사용**(경고 임계와 분할 임계 동일 → 정합). 설정 표면 신설 안 함(YAGNI).
- **관측성**: 분할 발생 시 `_LOG.info`로 "section {id} split into N sub-chunks" 1줄. Phase 1 경고 로깅은 분할 후엔 정상적으로 안 뜬다(각 sub가 상한 이하) → 경고 소멸 자체가 수정 검증 신호.
- **테스트**: `section_splitter`(신규, 순수) 단위테스트 — 문단 팩킹/문장 폴백/하드컷/title 예산 차감/상한 이하 무분할. 페이크 `count_tokens`(단어수 등)로 모델 없이. 골든 회귀(rrf/검색)는 ref_id 불변이라 영향 없어야 함(확인 포인트).

---

## 6. 권장 — 게이트와 착수 순서

| 항목 | 조건 | 재색인 | 판단 |
|---|---|---|---|
| 본 설계 확정 | 지금 | 0 | lead/사용자 승인 |
| `section_splitter` + 카운터 주입 + 단위테스트 | 설계 승인 후 | 0 | **재색인 없이 코드+테스트만 먼저 병합 가능**(순수 로직, 기존 경로는 count_tokens=None로 무영향) |
| 실색인 배선(build_chunks에 카운터 전달) + 전면 재색인 | **Phase 0가 512 초과 실사례를 보일 때** | 섹션 청크 전면 | docs/09·15 게이트. 현 측정 0건이라 **트리거 대기**가 원칙 |
| overlap | 경계 miss 실측 시 | — | 초기 비권장 유지 |

**핵심 결정 포인트(lead)**: Phase 0가 0건인 상태다. 두 갈래 —
- (A) **설계만 확정하고 구현 보류** — 게이트 원칙(docs/15) 준수. 새 데이터에서 초과 나오면 즉시 구현.
- (B) **순수 로직(splitter+테스트)까지 미리 병합, 배선/재색인만 트리거 대기** — 재색인 0이라 게이트 무침해, 나중 사례 발생 시 배선 한 줄로 활성. 방어 코드 미리 확보.

architect 권장: **(B)**. splitter는 재색인을 안 건드리는 순수 함수라 게이트(재색인 비용)를 침해하지 않고, docs/16 Phase 1(경고 로깅)과 짝이 맞는 방어선을 완성한다. 실배선만 실사례에 게이트한다.
