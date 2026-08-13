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
| child→parent 매핑 | `Chunk.ref_id` = `section_id` (**문자열 규약**) | chunk_builder.py:20, indexer_service.py:91·104 |
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

---

## Part C. 차이 정리 (그린필드 A vs 현행 B)

| 축                 | 그린필드(A)                            | 현행(B)                                                                                                                                        | 격차                                                                       |
| ------------------ | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| **매핑 방식**      | `child.parent_id` **FK**               | `Chunk.ref_id` **문자열 규약**(FK 아님, 타입도 endpoint/schema/section 혼용)                                                                   | 스키마상 무결성 제약·CASCADE 보장이 ref_id엔 없음. 정합은 코드 규약에 의존 |
| **child 크기**     | parent보다 작음(N:1)                   | **섹션 갈래는 1:1**(child.text ≈ parent.content) — child가 안 작음. 엔드포인트 갈래는 N=1이지만 요약이라 작음                                  | 섹션 정밀도 이득 미실현. doc/23 적용 시 해소                               |
| **텍스트 중복**    | child=슬라이스만, parent=full 1회      | 섹션 갈래는 `Chunk.text`(full) + `DocumentSection.content`(full) **이중 저장**                                                                 | 저장 중복. doc/23이 sub로 쪼개면 child.text는 슬라이스라 완화              |
| **반환 경로 배선** | child 매칭 → parent 조회·반환          | **엔드포인트만 배선**. `search_by_vector`가 `chunk_type=="endpoint"`로 하드 제한(chunk_repository.py:291) → **섹션 child는 벡터 검색에 안 탐** | 섹션 parent-child는 반환 단계가 미배선(설계상 준비만 됨)                   |
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
- 섹션 벡터 검색 미배선(C 표 4행)은 별개 결정 사안: "섹션을 벡터로도 찾게 할 것인가"는 검색 스코프 정책 문제라 doc/23(색인측)과 분리해 판단해야 한다.
