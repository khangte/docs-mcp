# 38. Drive/Notion 결정적 `Document.id` 길이 초과 — 판정

- 상태: 판정 확정 — 수정은 developer(app 코드 + 테스트), reviewer 재검토.
- 계기: reviewer의 Drive/Notion Phase1+2 리뷰. `deterministic_document_id(project, source, external_id)` 가 `f"{project}:{source}:{external_id}"` 원문 결합이라 `Document.id`(`String(64)`)를 쉽게 넘긴다. `index_bodies=True` 실사용 시 `StringDataRightTruncation` 으로 색인 자체가 깨짐.
- 참고: `app/services/documents/document_body_indexer.py:30-32`, `app/models/document.py:30`, `app/services/ingestor/sync_service.py:234-236`, `app/services/indexer/indexer_service.py:81,94,121,193-197`, `docs/architect-review/29-schema-chunk-ref-id-truncation-fix.md`

## 0. 결론

reviewer 지적은 **타당하고 심각도 HIGH**다. 다만 제시된 두 선택지 중 **컬럼 확장(A)은 반려**, **해시 고정길이(B)를 채택**한다.

**수정: `deterministic_document_id` 가 `f"{source}:{sha256(project\x00source\x00external_id)[:16]}"` 를 반환한다.** 마이그레이션 없음(`String(64)` 그대로), 파생 ID 예산 유지, 기존 `_new_id()` 16-hex 규약과 동형.

이건 doc/29(schema 청크 `ref_id` 트렁케이션)와 **같은 계열의 버그**다 — ID 컬럼에 외부 원문 문자열을 그대로 넣어 바운드를 깬 것. 판정 방향도 같다: 컬럼을 넓히지 말고 규약으로 되돌린다.

## 1. 진단 — 왜 반드시 터지는가

- `Document.id` 는 `String(64)`, 기존 등록형은 `_new_id() = uuid4().hex[:16]` → **16자**가 사실상의 규약이다.
- 그런데 `project` 컬럼만 해도 `PROJECT_MAX_LENGTH = 128` 이다. **external_id 가 0자라도 project 하나로 64를 넘길 수 있다.** Drive file_id(≈44) / Notion page_id(36) 는 그 위에 얹힌다.
- 테스트가 못 잡은 이유: `external_id="d1"`, project 도 짧은 기본값이라 합계 20자 남짓. 실 데이터에서만 터진다.

### 진짜 제약은 64가 아니라 파생 ID 예산이다

`Document.id` 는 **자기 컬럼 폭만의 문제가 아니다.** 하위 엔티티 ID가 전부 여기서 파생되고, 그 컬럼들도 모두 `String(64)` 다:

| 파생 ID | 생성 위치 | 형태 | 컬럼 |
|---|---|---|---|
| `ApiSchema.id` | `indexer_service.py:81` | `{doc_id}:schema:{idx}` | `String(64)` |
| `DocumentSection.id` | `indexer_service.py:94` | `{doc_id}:section:{idx}` | `String(64)` |
| `Chunk.id` | `indexer_service.py:121` | `{doc_id}:chunk:{idx}` | `String(64)` |
| `ApiEndpoint.id` | `indexer_service.py:197` | `{doc_id}:ep:{16-hex}` | `String(64)` |

가장 빡빡한 게 endpoint(`+4+16 = 20자` 오버헤드)다. 즉 **`Document.id` 실효 예산은 64가 아니라 약 40자**이고, 등록형이 16자를 쓰는 건 우연이 아니다.

## 2. A(컬럼 확장) 반려 근거

1. **한 컬럼이 아니라 5개 컬럼 + FK 5개를 같이 넓혀야 한다.** `document.id` 만 늘리면 `chunk.id`/`api_schema.id`/`document_section.id`/`api_endpoint.id` 가 그대로 터진다. FK 컬럼(`chunk.document_id` 등)까지 폭을 맞춰야 하니 마이그레이션이 테이블 5개+인덱스 재생성으로 번진다. **가장 큰 디프.**
2. **상한이 없다.** project(128) + external_id(256) 이면 이론상 400자 초과다. 넓혀도 "얼마나"에 근거가 없고, 다음 소스가 붙으면 또 넓혀야 한다. 해시는 폭이 입력과 무관하게 고정된다.
3. **PK 폭은 공짜가 아니다.** `chunk` 는 HNSW/GIN 인덱스를 얹은 최대 테이블이고, 모든 조인 키가 이 문자열이다. 400자 varchar PK 로 가는 건 성능·저장 양쪽에서 손해다.
4. **doc/29에서 이미 같은 이유로 A를 반려**했다. 여기서 뒤집으면 ID 규약이 둘로 갈라진다.

읽기 편한 ID(`myproject:drive:1a2b...`)라는 유일한 이점은, `document_meta` 가 project/source/external_id 를 **평문 컬럼으로 그대로 들고 있어** 조인 한 번이면 복원되므로 실익이 없다.

## 3. 채택안 (B) — 명세

```python
def deterministic_document_id(project: str, source: str, external_id: str) -> str:
    """project/source/external_id 로 Drive/Notion 문서의 결정적 `Document.id` 를 만든다.

    `Document.id` 는 `String(64)` 이고 하위 엔티티 ID가 여기서 파생되므로
    (`{doc_id}:ep:{16-hex}` 가 20자를 더 쓴다), 입력 길이와 무관하게 고정
    길이를 낸다. 등록형 `_new_id()`(uuid4 16-hex)와 같은 폭이다.
    """
    key = f"{project}\x00{source}\x00{external_id}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{digest}"
```

- **길이**: `source`("drive"/"notion") + 1 + 16 → **최대 23자**. 최악의 파생 ID(endpoint)도 43자로 64 안에 든다.
- **구분자 `\x00`**: project 명에 `:` 가 섞여도 `("a:b", "drive", "x")` 와 `("a", "b:drive", "x")` 가 같은 키로 뭉치지 않는다. 공짜로 얻는 방어.
- **`source:` 프리픽스 유지**: 로그·DB 육안 조회에서 출처를 바로 읽을 수 있고, 소스별 스캔이 프리픽스로 가능하다. 코드가 이 ID를 파싱해 되돌리는 곳은 없다(확인함) — 프리픽스는 사람용이다.
- **충돌**: 16-hex = 64비트. 기존 `_new_id()` 의 uuid4 16-hex 와 **동일한 충돌 예산**이다. 여기서 새 위험을 들여오는 게 아니다.
- **결정성 유지**: 같은 (project, source, external_id) → 항상 같은 ID. `document_meta.document_id` 조인, 재색인 시 같은 행 갱신, 삭제 전파 모두 그대로 성립한다. doc/36 §4~§5 의 계약은 유지되고 **ID 생성식만 바뀐다.**

### 마이그레이션 / 호환성

**불필요.** `index_bodies` 는 기본 `False` 이고 Phase 2 는 아직 커밋 전이라 이 규칙으로 생성된 행이 존재하지 않는다. `8a8db5f9c592`(document_meta.document_id FK) 는 `document.id` 타입을 따라가므로 **수정 없음.**

## 4. 수정 지시 (developer)

1. `app/services/documents/document_body_indexer.py:30-32` 의 `deterministic_document_id` 를 §3 명세대로 교체. docstring 에 "왜 고정 길이인가"(파생 ID 예산) 한 줄 남길 것.
2. `app/models/document_meta.py:41-43` 의 `document_id` docstring 이 `f"{project}:{source}:{external_id}"` 를 명시하고 있다 — 새 규칙으로 정정.
3. **테스트(RED→GREEN)** — 지금 테스트가 못 잡은 게 이 버그의 본질이므로 여기가 핵심이다:
   - `project="p" * 128`(컬럼 상한), `external_id=` Drive file_id 급 44자 실측 형태로 `deterministic_document_id` 호출 → **`len(result) <= 40`** 단언(파생 ID 예산 기준. 단순히 64 이하로 두지 말 것).
   - 같은 입력 → 같은 값(결정성), 입력 하나만 달라지면 다른 값(구분).
   - `index_document_body` 통합 테스트 1건을 **긴 project/external_id 로** 태워 `Document`/`Chunk`/`DocumentSection` insert 가 통과하는지 확인 — 파생 ID까지 실제로 들어가는 경로를 밟아야 이 계열 버그가 다시 안 샌다.
   - 기존 테스트의 `external_id="d1"` 은 그대로 둬도 된다(짧은 값 회귀 커버).
4. 스코프는 위 3개 파일 + 테스트. 마이그레이션·모델 컬럼 변경 **금지**.

## 5. 설계 문서 정정

`docs/architect-review/36_drive_notion_embedding_migration_and_refresh_strategy.md` §4 의 "`Document.id` 를 결정적으로 고정: `f"{project}:{source}:{external_id}"`" 가 **이 버그의 출처**다(설계 오류, 내 몫). 해당 줄에 본 문서(38번) 참조와 함께 해시 규칙으로 정정 표기할 것 — developer 수정과 같은 커밋에 포함.

## 6. 나머지 리뷰 항목

reviewer가 확인한 fetch 게이트 / delete-insert / 삭제 전파 CASCADE / `source_url` NULL 격리 / `index_bodies` 기본 False / alembic up-down 대칭 — **모두 doc/36 설계대로 동작 확인, 승인.** 별도 조치 없음.
