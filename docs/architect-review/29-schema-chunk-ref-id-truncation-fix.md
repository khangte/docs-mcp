# 29. schema 청크 ref_id 트렁케이션 크래시 — 근본 수정 판정

- 상태: 판정 확정 — 수정은 developer(app 코드), reviewer 검토.
- 계기: developer가 doc/28 실 코퍼스 색인 중 발견. Stripe `spec3.json` 등록 시 `sync_service.register` 크래시 — `StringDataRightTruncation`. schema 컴포넌트 1440개 중 106개 이름이 64자 초과(최대 135자), `chunk.ref_id`(String(64)) INSERT 실패.
- 참고: `app/services/indexer/chunk_builder.py:114-121`, `app/services/indexer/indexer_service.py:79-87`, `app/models/chunk.py:57`, `app/models/openapi.py:171-173`, `app/repositories/chunk_repository.py:221`

## 0. 결론

developer가 제시한 A(컬럼 확장 마이그레이션)/B(해시·트렁케이트)/C(eval에서 schema 청크 스킵) **모두 채택 안 함.** 근본 원인은 컬럼 폭이 아니라 **schema 청크만 ref_id 규약을 이탈**한 것이다.

**수정: `chunk_builder`가 schema 청크 ref_id로 `schema.name`(String(256)) 대신 이미 계산돼 있는 바운드 schema id(`f"{document.id}:schema:{idx}"`, `ApiSchema.id`와 동일값, ≤~40자)를 쓴다.** endpoint/section 청크와 동일 패턴. 마이그레이션·해시·스킵 불필요.

## 1. 진단 — 규약 이탈 지점

`build_chunks(document: ParsedDocument, ...)`는 **파싱 결과**를 받아 청크를 만든다. DB id는 외부에서 맵으로 주입한다:

| chunk_type | ref_id 소스 | 값 | 바운드 |
|---|---|---|---|
| endpoint | `endpoint_ids[(method,path)]` 맵 | `f"{document.id}:endpoint:..."`(해시) | ✅ ≤64 |
| section | `section_ids[idx]` 맵 | `f"{document.id}:section:{idx}"` | ✅ ≤64 |
| **schema** | **`schema.name` 직접** | **원본 스키마명** | ❌ **최대 135자(Stripe)** |

- endpoint/section은 `indexer_service`가 만든 바운드 id를 맵으로 받아 쓴다. schema만 맵 없이 파싱된 `schema.name`을 그대로 ref_id에 넣어 **유일하게 이탈** → 긴 이름에서 트렁케이션 크래시.
- 게다가 endpoint 청크는 `Chunk.ref_id == ApiEndpoint.id`로 조인한다(`chunk_repository.py:221`). 규약상 ref_id는 **엔티티 id**여야 하는데 schema만 name을 넣어 조인 규약과도 어긋난다.
- `indexer_service.py:79-87`이 이미 `ApiSchema.id = f"{document.id}:schema:{idx}"`를 만들어 저장한다 — 필요한 바운드 id가 **이미 존재**한다. 안 넘겨줬을 뿐.

## 2. 왜 A/B/C가 아닌가

- **A(컬럼 확장 마이그레이션)**: 잘못된 계층. ref_id는 설계상 바운드 엔티티 id를 담아야 한다(endpoint/section이 그렇게 함). 컬럼을 넓히면 "schema만 이름을 담는" 설계 불일치를 **고정·은폐**한다. 데이터모델 비대화 + 스코프 밖 마이그레이션 비용, 이득 없음.
- **B(해시/트렁케이트)**: 트렁케이트는 64자 프리픽스 충돌 위험, 임의 해시는 이미 있는 결정적 id를 버리는 것. 조인 대상(`ApiSchema.id`)과 어긋나 향후 schema 검색 배선(doc/24 C-2) 때 깨진다.
- **C(eval에서 schema 청크 스킵)**: doc/28은 안 막지만 **프로덕션 버그를 방치**한다. `register_document`는 문서화된 1급 경로(README §C)라 실사용자가 Stripe급 실 스펙을 넣으면 동일 크래시 — HIGH 심각도. eval 전용 스킵 플래그는 버려질 코드.

근본 수정이 **더 작고**(맵 하나 배선) 세 문제를 한 번에 없앤다: 크래시 + 조인 규약 이탈 + doc/28 차단.

## 3. 수정 지시 (developer)

1. `chunk_builder.build_chunks` 시그니처에 `schema_ids: dict[int, str] | None = None` 추가(endpoint_ids/section_ids와 동형).
2. schema 루프를 `for idx, schema in enumerate(document.schemas):`로 바꾸고 `ref_id = schema_ids[idx]`(없으면 endpoint처럼 skip) 사용. `schema.name`은 **ref_id에서만** 제거 — `build_schema_chunk_text`의 본문 텍스트(`Schema: {name}`)는 그대로 둔다(검색 텍스트엔 이름 필요).
3. `indexer_service`에서 schema 저장 루프의 `f"{document.id}:schema:{idx}"`를 `schema_ids` 맵으로 모아 `build_chunks(..., schema_ids=schema_ids)`로 전달(section_ids 배선과 동일 패턴).
4. `BuiltChunk.ref_id` 주석(`chunk_builder.py:29`)을 "schema_name" → "schema_id"로 정정.
5. 테스트(RED→GREEN): 스키마명 64자 초과(예: 100자) 문서 등록 → 크래시 없음 + schema 청크 `ref_id == f"{doc_id}:schema:{idx}"`(== `ApiSchema.id`) 단언. 기존 색인 데이터 없음(DB 공백)이라 재색인 이슈 없음.

## 4. doc/28 영향

- 이 수정으로 Stripe/GitHub 스펙 등록 차단 해제 → doc/28 코퍼스 색인 정상 진행. eval은 endpoint 청크만 채점하므로 schema ref_id 변경과 **무관**(결과 불변).
- doc/28 스코프·정답 단위·질의셋 변경 없음.

## 5. 심각도 / lead 보고 사항

- **프로덕션 HIGH**: 대형 실 API 스펙 등록 시 결정론적 크래시(문서화된 1급 경로). 별도 이슈가 아니라 이 판정으로 즉시 근본 수정.
- 스코프: app 코드 소폭 수정(chunk_builder + indexer 배선 + 테스트). 마이그레이션 없음.
