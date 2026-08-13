# doc/24 parent-child 비교문서 — reviewer 지적 판정

- 상태: **판정 완료 + 문서 수정 반영**
- 일시: 2026-08-13
- 작성: architect
- 대상: `docs/architect-review/24-parent-child-chunking-greenfield-vs-actual.md`, `docs/architect-review/23-long-section-sub-chunking-phase2-design.md`
- 계기: reviewer가 doc/24 검토 후 3건 지적 → 설계 판단 필요분으로 architect에 회부.

---

## 판정 요약

3건 모두 **타당 → 수정 확정**. 셋 다 **서술 정확도** 문제이고, doc/24의 결론(C-2: "그린필드로 갈아엎지 않는다")은 **불변**. 수정 대상이 architect 산출물(review 문서)이라 developer 위임 없이 architect가 직접 반영함.

| #   | 유형      | 판정       | 근거                                                                                                                                                      |
| --- | --------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | 인용 오류 | 수정       | `chunk_builder.py:20`은 토큰상한 주석(무관). 실제 근거는 field 정의 `:29`와 섹션 ref_id 할당 `:133`.                                                      |
| 2   | 서술 범위 | 수정       | 섹션 child는 벡터만 미배선이 아니라 **벡터·키워드 둘 다** 미배선. `search_endpoint_by_text`(chunk_repository.py:184)도 `chunk_type=="endpoint"` 하드필터. |
| 3   | 누락 축   | 수정(추가) | `chunk_type=schema`도 parent 저장소(`ApiSchema`, openapi.py:165) 있는 **세 번째 갈래**. 섹션과 동형으로 검색 미배선. Part B에 명시.                       |

---

## 상세

### 1. 인용 오류 (B-1 표)

- 지적: B-1 표 "child→parent 매핑 = ref_id" 근거로 `chunk_builder.py:20`을 인용했으나 그 줄은 `DEFAULT_SECTION_TOKEN_LIMIT` 주석이라 무관.
- 확인: `:29` = `BuiltChunk.ref_id` field 정의(주석 "endpoint_id, schema_name 또는 section_id"), `:133` = `BuiltChunk(chunk_type="section", ref_id=sid, ...)` 할당. 이 둘이 실제 근거.
- 반영: doc/24 B-1 표 인용을 `:20` → `:29·133`으로 교정.

### 2. 서술 범위 — 벡터·키워드 둘 다 미배선

- 지적: doc/24는 "섹션 child는 벡터 검색에 안 탐(chunk_repository.py:291)"만 서술. 키워드 arm도 동일하게 endpoint 하드필터라 섹션은 키워드에도 안 탐.
- 확인: `search_endpoint_by_text`(chunk_repository.py:184) `.where(Chunk.chunk_type == "endpoint")`. `search_by_vector`(chunk_repository.py:291)와 동일한 하드필터.
- 결론 영향 없음: "섹션을 검색으로 찾게 할 것인가"는 검색 스코프 정책 문제(C-2). 벡터만이든 벡터+키워드든 동일한 정책 결정 대상이라 C-2 결론 불변.
- 반영: doc/24 C 표 4행·C-2를 "벡터·키워드 둘 다"로 확장. doc/23 §4도 동일 사실을 1줄 보강(섹션이 두 arm 모두 안 탄다 → "무변경" 근거를 오히려 강화).

### 3. 누락 축 — 스키마 갈래

- 지적: Part B가 섹션/엔드포인트 두 갈래만 다룸. `chunk_type=schema`도 같은 하드필터로 검색 미배선인 세 번째 갈래.
- 확인: `ApiSchema`(openapi.py:165, `api_schema` 테이블)가 parent 저장소로 존재 → schema는 섹션과 **동형** parent-child(child=`Chunk`(type=schema), parent=`ApiSchema`, 매핑=`ref_id`=schema_name). 매핑이 id 아닌 이름 문자열이라 섹션·엔드포인트보다 규약이 더 약함.
- 판정: 스코프 밖 배제가 아니라 **Part B에 추가**가 맞다. 섹션과 상태가 완전히 같은데 하나만 다루면 비교문서로서 불완전. 다만 별도 판단이 붙는 갈래가 아니므로(섹션과 동일 결론) 압축 형태(B-4 미니표 + 1줄)로 추가, C-2 결론은 그대로 적용.
- 반영: doc/24에 §B-4 신설.

---

## 결론

- 3건 모두 서술 정확도 교정. **설계 결론(C-2) 불변** — 현행 parent-child 구조는 그린필드로 갈아엎을 대상 아님, FK 승격·검색 배선은 실사례 트리거 대기(YAGNI/게이트).
- 수정은 doc/24·doc/23 문서에 직접 반영 완료. 코드 변경 없음.

---

## 후속 결정 — 섹션·스키마 검색 미배선 처리 (사용자 확정, 2026-08-13)

C-2가 남긴 열린 질문("섹션·스키마 갈래가 벡터·키워드 둘 다 미배선인 현 상태를 어떻게 할 것인가")에 대한 결정.

### 상태 재확인

- 색인측(`indexer_service.py:112`, `embed_documents(texts)`)은 built_chunks **전체**(endpoint+schema+section)에 임베딩을 만들어 저장한다 — 타입 필터 없음.
- 검색측(`chunk_repository.py:184` `search_endpoint_by_text`, `:291` `search_by_vector`)은 `chunk_type=="endpoint"` 하드필터라 섹션·스키마 임베딩을 **절대 읽지 않는다**.
- 즉 "쓰기는 하고 읽기는 안 하는" 상태. '공짜 미사용 인덱스'가 아니라 읽지 않는 임베딩을 쓰기·저장 비용 내며 생성 중.

### 검토한 안

- **(a) 그대로/유예**: 읽기측 배선 안 함(YAGNI). 하위 갈래 (a-hedge) 임베딩 생성 유지 vs (a-trim) 섹션·스키마 임베딩 생성 중단.
- **(b) 지금 배선**: 하드필터 제거. → **반대.** 필터 한 줄 문제가 아님. 섹션·스키마는 반환 경로가 엔드포인트와 달라(엔드포인트=`get_endpoint_details`, 섹션=`DocumentSection.content`, 스키마=`ApiSchema`) 필터만 풀면 엔드포인트 RRF 랭킹에 섞여 핵심 검색 품질을 희석. 제대로 하려면 타입별 별도 검색·반환 레인 + 관련성 평가 필요 → 실사용 요청 시 게이트.

### 결정: **(a-hedge) — 헤지 유지**

- 섹션·스키마 임베딩 생성을 **지금대로 유지**. 이는 '방치'가 아니라 **의도된 헤지**다: 나중에 섹션·스키마 검색 수요가 오면 **재색인 없이 읽기측 배선만으로** 켤 수 있다.
- **읽기측(검색) 배선은 지금 안 함.**
- **코드 변경 없음** — `indexer_service.py:112`의 현 동작이 이미 헤지 상태라 그대로 두면 성립.

### 재검토 트리거

- **켜기**: 섹션·스키마를 검색 결과로 찾고 싶다는 실사용 요청 → 타입별 레인 설계 + 관련성 평가 후 배선(별도 게이트, docs/12 후보 정체성 축 준수).
- **끄기((a-trim)로 전환)**: 코퍼스가 커져 섹션·스키마 임베딩 생성·저장 비용이 무시 못 할 수준이 되고 검색 수요는 여전히 없을 때 → build 단계에서 해당 타입 embed 스킵(전환 시 재색인 동반).
