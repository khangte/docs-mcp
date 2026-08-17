# Parent-Child 청킹 — 그린필드 설계 + 현 코드베이스 부수효과 구조 비교

- 상태: **설계/비교 only** — 코드 미수정.
- 일시: 2026-08-13
- 작성: architect
- 목적: (1) parent-child 청킹을 "지금 아무것도 없다" 가정하고 표준 패턴으로 처음부터 설계. (2) 이 코드베이스에 **부수효과로 이미 존재하는** 유사 구조와 차이 정리.
- 참고: `app/models/chunk.py`, `app/models/document.py`, `app/services/indexer/chunk_builder.py`, `app/services/search/rrf.py`, `docs/architect-review/23-long-section-sub-chunking-phase2-design.md`

---

## Part A. 그린필드 Parent-Child 설계 (기존 구조 무전제)

### A-0. 왜 parent-child인가

검색 최적 청크 크기와 답변 최적 청크 크기가 다르다. **작은 청크**는 임베딩이 한 주제에 집중돼 벡터 매칭 정밀도가 높다. **큰 블록**은 LLM이 답을 구성할 문맥이 온전하다. parent-child는 이 둘을 분리한다:

- **child** = 작은 검색용 청크. 임베딩된다. 매칭 정밀도 담당.
- **parent** = 큰 반환용 블록. 임베딩 안 한다. 문맥 담당.
- child N개 → parent 1개 (`N:1`).

### A-1. 저장 스키마

```
parent
  id            PK
  document_id   FK
  content       TEXT      -- 반환용 full 블록 (임베딩 안 함)
  order_index   INT
  (title 등 메타)

child
  id            PK
  parent_id     FK -> parent.id   -- ★ 명시적 매핑
  document_id   FK
  text          TEXT      -- 작은 검색용 텍스트 (parent의 슬라이스/요약)
  embedding     VECTOR    -- child만 임베딩
  text_tsv      TSVECTOR  -- (하이브리드면) 키워드 FTS도 child 텍스트 기준
```

핵심: **매핑은 `child.parent_id` FK 한 컬럼**. child는 자기 텍스트만 저장(작음), parent는 full 본문을 **한 번만** 저장. 텍스트 중복 없음.

### A-2. 인덱싱 흐름

```
document → split into parents (페이지/헤딩/윈도우 등 큰 단위)
  for each parent:
    persist parent(content=full)
    children = split_small(parent.content)      -- 문단/문장/고정윈도우
    for each child:
      persist child(parent_id=parent.id, text=child_text, embedding=encode(child_text))
```

### A-3. 검색 흐름 (child로 매칭 → parent 반환)

```
1. query → embed → child.embedding ANN 검색 → top-k child 히트
   (하이브리드면 child.text_tsv 키워드 검색도 → RRF 융합, 여전히 child 단위 순위)
2. 히트 child들의 parent_id 수집
3. dedupe: 같은 parent_id는 하나로 접기 (child 여러 개가 같은 parent 가리킴)
   - 랭킹 보존: parent 점수 = 그 parent에 속한 child 중 최고 순위/점수
4. parent_id로 parent.content 조회 → full 블록 반환
```

포인트:

- **매칭 단위 = child, 반환·정체성 단위 = parent.** 사용자는 parent(문맥 온전)를 받는다.
- **dedupe는 parent_id 기준.** 한 parent의 여러 child가 상위에 들면 중복 반환하지 않는다.
- parent는 검색에 안 걸린다(임베딩 없음). 순수 반환 저장소.

### A-4. 설계 변수

| 변수          | 표준 선택                                                       |
| ------------- | --------------------------------------------------------------- |
| parent 경계   | 헤딩/페이지/문서, 또는 큰 고정 윈도우                           |
| child 경계    | 문단/문장/작은 고정 윈도우(+overlap)                            |
| child overlap | 경계 miss 방지용 관례적으로 있음(50~150토큰)                    |
| 매핑          | `child.parent_id` FK (역방향 조회 `parent → children`도 관계로) |
| dedupe 지점   | 검색 후 parent 접기(child 최고점 승계)                          |

---

## Part B. 현 코드베이스의 부수효과 구조

명시적으로 "parent-child를 하자"고 만든 적 없지만, 두 갈래에서 사실상 같은 모양이 나와 있다.

### B-1. 섹션 갈래 (`DocumentSection` + `Chunk`)

| parent-child 역할 | 이 코드                                         | 근거                                           |
| ----------------- | ----------------------------------------------- | ---------------------------------------------- |
| parent(반환 본문) | `DocumentSection.content` = full 본문           | document.py:71                                 |
| child(검색 청크)  | `Chunk`(embedding+text+text_tsv)                | chunk.py:58-67                                 |
| child→parent 매핑 | `Chunk.ref_id` = `section_id` (**문자열 규약**) | chunk_builder.py:29·133, indexer_service.py:91·104 |
| parent 접기       | RRF `_dedupe_first`가 ref_id 중복 병합          | rrf.py:30                                      |

→ ref_id를 parent_id처럼 쓰는 **암묵적 parent-child**.

### B-2. 엔드포인트 갈래 (실제로 배선돼 동작 중)

| 역할         | 이 코드                                                          |
| ------------ | ---------------------------------------------------------------- |
| child(검색)  | `Chunk`(chunk_type=endpoint, embedding)                          |
| parent(반환) | `ApiEndpoint` + 파라미터/응답 (`get_endpoint_details` full 반환) |
| 매핑         | `Chunk.ref_id` = endpoint_id                                     |
| 접기         | RRF `_dedupe_first`                                              |

→ **엔드포인트 갈래가 이 프로젝트에서 실제로 도는 parent-child다.** child=엔드포인트 요약 청크, parent=엔드포인트 상세.

### B-3. doc/23 섹션 sub-chunking을 얹으면

지금 섹션은 child=parent 텍스트가 사실상 같다(1:1). doc/23을 적용하면 한 섹션이 sub-chunk N개가 되고 각 sub가 `ref_id=section_id`를 공유 → **비로소 child(작은 sub) N개 : parent(DocumentSection) 1개** 가 성립. 즉 doc/23은 섹션 갈래를 진짜 parent-child(N:1)로 밀어 올리는 조각이다.

### B-4. 스키마 갈래 (세 번째 갈래, 섹션과 동형)

| 역할         | 이 코드                                                          |
| ------------ | ---------------------------------------------------------------- |
| child(검색)  | `Chunk`(chunk_type=schema, embedding)                           |
| parent(반환) | `ApiSchema`(openapi.py:165, `api_schema` 테이블) full 스키마    |
| 매핑         | `Chunk.ref_id` = `schema_name` (**id 아닌 이름 문자열** — 섹션·엔드포인트보다 더 약한 규약) |
| 접기         | RRF `_dedupe_first`                                              |

→ 구조는 섹션 갈래와 **동형**: parent 저장소(`ApiSchema`)는 있으나 검색 경로에 미배선(위 두 하드필터가 `endpoint`만 통과시켜 벡터·키워드 둘 다 안 탐). 섹션과 같은 상태라 아래 C-2 판단(재색인 게이트 대상, 실이득 없이는 YAGNI)이 그대로 적용된다.

---

## Part C. 차이 정리 (그린필드 A vs 현행 B)

| 축                 | 그린필드(A)                            | 현행(B)                                                                                                                                        | 격차                                                                       |
| ------------------ | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| **매핑 방식**      | `child.parent_id` **FK**               | `Chunk.ref_id` **문자열 규약**(FK 아님, 타입도 endpoint/schema/section 혼용)                                                                   | 스키마상 무결성 제약·CASCADE 보장이 ref_id엔 없음. 정합은 코드 규약에 의존 |
| **child 크기**     | parent보다 작음(N:1)                   | **섹션 갈래는 1:1**(child.text ≈ parent.content) — child가 안 작음. 엔드포인트 갈래는 N=1이지만 요약이라 작음                                  | 섹션 정밀도 이득 미실현. doc/23 적용 시 해소                               |
| **텍스트 중복**    | child=슬라이스만, parent=full 1회      | 섹션 갈래는 `Chunk.text`(full) + `DocumentSection.content`(full) **이중 저장**                                                                 | 저장 중복. doc/23이 sub로 쪼개면 child.text는 슬라이스라 완화              |
| **반환 경로 배선** | child 매칭 → parent 조회·반환          | **엔드포인트만 배선**. `search_by_vector`(chunk_repository.py:291)·`search_endpoint_by_text`(chunk_repository.py:184) 둘 다 `chunk_type=="endpoint"` 하드 제한 → **섹션·스키마 child는 벡터·키워드 검색 둘 다 안 탐** | 섹션·스키마 parent-child는 반환 단계가 미배선(설계상 준비만 됨)                   |
| **dedupe 지점**    | 검색 후 parent 접기(child 최고점 승계) | RRF **랭킹 전** `_dedupe_first`로 ref_id 접기                                                                                                  | 결과는 유사(첫 등장=최고 순위 승계). 위치만 다름                           |
| **parent 임베딩**  | parent 임베딩 안 함(순수 반환)         | `DocumentSection` 임베딩 없음 ✅ 일치 / 단 `Chunk`(child)가 full이라 parent를 임베딩하는 셈(섹션 1:1일 때)                                     | 1:1인 동안은 "parent를 임베딩"에 가까움. sub 분할 시 정상화                |
| **overlap**        | child 간 관례적 overlap                | 없음(doc/23도 비권장 유지)                                                                                                                     | 의도적 선택. 이 구조(발견=벡터/반환=별도저장)에선 이득 얇음(doc/16 §2-3)   |
| **parent 경계**    | 페이지/윈도우 등 자유                  | 헤딩 섹션 고정(markdown_parser)                                                                                                                | 헤딩 희소 입력(PDF/DOCX)에서 parent가 비대 → doc/16 맹점의 뿌리            |

### C-1. 요지

- 이 코드베이스는 **이름만 없을 뿐 parent-child를 두 번 재발명**했다. 엔드포인트 갈래는 완성형(child=요약청크, parent=상세, 배선 완료). 섹션 갈래는 **뼈대(parent=DocumentSection, child=Chunk, 매핑=ref_id, 접기=\_dedupe_first)는 있으나** ① child가 안 작고(1:1) ② 벡터 검색에 미배선 ③ 매핑이 FK 아닌 문자열.
- **그린필드와의 본질적 격차는 셋**: (a) `parent_id` FK 부재(무결성/CASCADE를 규약에 의존), (b) 섹션 child가 아직 parent만큼 큼(→ doc/23이 메움), (c) 섹션 반환 경로 미배선.
- doc/23 sub-chunking은 (b)를 정확히 겨눈 조각 — 적용하면 섹션 갈래가 표준 parent-child(N:1)에 근접한다. 다만 (a)(c)는 doc/23 범위 밖이다.

### C-2. 판단 (참고용, 착수 아님)

- 현행은 **그린필드로 갈아엎을 대상이 아니다.** 엔드포인트 갈래는 이미 표준형이고, 섹션 갈래도 ref_id 규약 = de-facto parent_id로 동작한다. FK로 승격(`Chunk.parent_section_id`)은 무결성 이점은 있으나 **재색인 동반 스키마 변경**이라 docs/09·15 게이트 대상 — 실이득(고아 청크가 실제 문제로 나타남) 없이는 YAGNI.
- 섹션·스키마 검색 미배선(C 표 4행)은 별개 결정 사안: 두 갈래는 벡터·키워드 **둘 다** 안 탄다(`endpoint` 하드필터). "섹션·스키마를 검색으로도 찾게 할 것인가"는 검색 스코프 정책 문제라 doc/23(색인측)과 분리해 판단해야 한다.

---

## Part D. reviewer 지적 판정 (doc/27 흡수, 2026-08-13)

- 상태: **판정 완료 + 위 본문(A~C) 수정 반영 완료**
- 계기: reviewer가 이 문서 검토 후 3건 지적 → 설계 판단 필요분으로 architect에 회부.

### D-0. 판정 요약

3건 모두 **타당 → 수정 확정**(위 Part A~C 본문에 이미 반영됨). 셋 다 **서술 정확도** 문제이고, 결론(C-2: "그린필드로 갈아엎지 않는다")은 **불변**.

| # | 유형 | 판정 | 근거 |
| --- | --- | --- | --- |
| 1 | 인용 오류 | 수정 | `chunk_builder.py:20`은 토큰상한 주석(무관). 실제 근거는 field 정의 `:29`와 섹션 ref_id 할당 `:133`. |
| 2 | 서술 범위 | 수정 | 섹션 child는 벡터만 미배선이 아니라 **벡터·키워드 둘 다** 미배선. `search_endpoint_by_text`(chunk_repository.py:184)도 `chunk_type=="endpoint"` 하드필터. |
| 3 | 누락 축 | 수정(추가) | `chunk_type=schema`도 parent 저장소(`ApiSchema`, openapi.py:165) 있는 **세 번째 갈래**. 섹션과 동형으로 검색 미배선. Part B-4로 반영. |

상세 근거:

1. **인용 오류**: B-1 표 "child→parent 매핑 = ref_id" 근거로 `chunk_builder.py:20`을 인용했으나 그 줄은 `DEFAULT_SECTION_TOKEN_LIMIT` 주석이라 무관. `:29` = `BuiltChunk.ref_id` field 정의(주석 "endpoint_id, schema_name 또는 section_id"), `:133` = `BuiltChunk(chunk_type="section", ref_id=sid, ...)` 할당이 실제 근거. → B-1 표 인용을 `:20` → `:29·133`으로 교정 완료.
2. **서술 범위**: `search_endpoint_by_text`(chunk_repository.py:184) `.where(Chunk.chunk_type == "endpoint")`. `search_by_vector`(chunk_repository.py:291)와 동일한 하드필터. 결론 영향 없음 — "섹션을 검색으로 찾게 할 것인가"는 검색 스코프 정책 문제(C-2)라 벡터만이든 벡터+키워드든 동일한 정책 결정 대상. → C 표 4행·C-2를 "벡터·키워드 둘 다"로 확장 완료.
3. **누락 축**: Part B가 원래 섹션/엔드포인트 두 갈래만 다뤘으나 `chunk_type=schema`도 같은 하드필터로 검색 미배선인 세 번째 갈래임을 확인 → Part B-4로 추가 완료.

### D-1. 결론

- 3건 모두 서술 정확도 교정. **설계 결론(C-2) 불변** — 현행 parent-child 구조는 그린필드로 갈아엎을 대상 아님, FK 승격·검색 배선은 실사례 트리거 대기(YAGNI/게이트).
- 코드 변경 없음.

### D-2. 후속 결정 — 섹션·스키마 검색 미배선 처리 (사용자 확정, 2026-08-13)

C-2가 남긴 열린 질문("섹션·스키마 갈래가 벡터·키워드 둘 다 미배선인 현 상태를 어떻게 할 것인가")에 대한 결정.

**상태 재확인**

- 색인측(`indexer_service.py:112`, `embed_documents(texts)`)은 built_chunks **전체**(endpoint+schema+section)에 임베딩을 만들어 저장한다 — 타입 필터 없음.
- 검색측(`chunk_repository.py:184` `search_endpoint_by_text`, `:291` `search_by_vector`)은 `chunk_type=="endpoint"` 하드필터라 섹션·스키마 임베딩을 **절대 읽지 않는다**.
- 즉 "쓰기는 하고 읽기는 안 하는" 상태. '공짜 미사용 인덱스'가 아니라 읽지 않는 임베딩을 쓰기·저장 비용 내며 생성 중.

**검토한 안**

- **(a) 그대로/유예**: 읽기측 배선 안 함(YAGNI). 하위 갈래 (a-hedge) 임베딩 생성 유지 vs (a-trim) 섹션·스키마 임베딩 생성 중단.
- **(b) 지금 배선**: 하드필터 제거. → **반대.** 필터 한 줄 문제가 아님. 섹션·스키마는 반환 경로가 엔드포인트와 달라(엔드포인트=`get_endpoint_details`, 섹션=`DocumentSection.content`, 스키마=`ApiSchema`) 필터만 풀면 엔드포인트 RRF 랭킹에 섞여 핵심 검색 품질을 희석. 제대로 하려면 타입별 별도 검색·반환 레인 + 관련성 평가 필요 → 실사용 요청 시 게이트.

**결정: (a-hedge) — 헤지 유지**

- 섹션·스키마 임베딩 생성을 **지금대로 유지**. 이는 '방치'가 아니라 **의도된 헤지**다: 나중에 섹션·스키마 검색 수요가 오면 **재색인 없이 읽기측 배선만으로** 켤 수 있다.
- **읽기측(검색) 배선은 지금 안 함.**
- **코드 변경 없음** — `indexer_service.py:112`의 현 동작이 이미 헤지 상태라 그대로 두면 성립.

**재검토 트리거**

- **켜기**: 섹션·스키마를 검색 결과로 찾고 싶다는 실사용 요청 → 타입별 레인 설계 + 관련성 평가 후 배선(별도 게이트, docs/12 후보 정체성 축 준수).
- **끄기((a-trim)로 전환)**: 코퍼스가 커져 섹션·스키마 임베딩 생성·저장 비용이 무시 못 할 수준이 되고 검색 수요는 여전히 없을 때 → build 단계에서 해당 타입 embed 스킵(전환 시 재색인 동반).
