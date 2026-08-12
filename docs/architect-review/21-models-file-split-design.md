# 21. `app/models/openapi.py` 파일 분리 설계

> 분석·설계 리포트. 코드 수정 없음. 병행 중인 project_source 마이그레이션과는
> 별개 트랙(단 같은 파일을 건드리므로 §4 순서 주의).

사용자 지적: `openapi.py` 에 범용 모델과 openapi 전용 모델이 섞여 파일명과
내용이 안 맞는다. 실제 openapi 전용은 `ApiEndpoint/ApiParameter/ApiRequestBody/
ApiResponse/ApiSchema` 5개뿐이고, `ApiDocument`(전 포맷 루트)·`ApiSection`
(md/csv)·`ApiChunk`(검색코어)·`DocumentSyncHistory`·`Base`·공용 상수는 범용.

**결론 요약**: 분리 타당. 단 **행위/성능 이득 0, 순수 응집도·명명 개선**이고
비용은 **import 팬아웃(~30 사이트) + alembic 메타데이터 등록 리스크**다.
분리 자체보다 **모델 등록 허브(`app/models/__init__.py`) 도입**이 본질적 이득
(env.py 모듈 누락 버그류를 구조적으로 제거). project_source 마이그레이션이
착지한 뒤 독립 커밋으로 하는 걸 권고.

---

## 1. 분리 기준과 대상 파일

기준: **(a) 결합 방향이 아래로만 흐르는 레이어**(base ← 나머지) +
**(b) 도메인 성격**(범용 문서 vs 검색코어 vs openapi 전용).

| 새 파일 | 담는 것 | 성격 |
|---|---|---|
| `app/models/base.py` | `Base(DeclarativeBase)`, `SCHEMA`, `PROJECT_MAX_LENGTH`, `DEFAULT_PROJECT`, `_utcnow` | 전 모델의 declarative base + 크로스컷 상수. **어떤 모델도 import 안 함(리프)** |
| `app/models/document.py` | `ApiDocument`(전 포맷 루트), `ApiSection`(md/csv), `DocumentSyncHistory` | 범용 문서 루트 + 비-openapi 부품 + 동기화 이력 |
| `app/models/chunk.py` | `ApiChunk`, `EMBEDDING_DIM`, `TEXT_TSV_EXPRESSION` | 검색 코어(포맷무관, pgvector/FTS 특화 상수 동거) |
| `app/models/openapi.py` | `ApiEndpoint/ApiParameter/ApiRequestBody/ApiResponse/ApiSchema` + `_decode_json_dict`/`_decode_json_any` | **진짜 openapi 전용만** 잔류 |
| `app/models/__init__.py`(신규 내용) | 전 모델 모듈 import + `Base`·`create_all`·상수 re-export | **단일 등록 허브**(§3 핵심) |
| `document_meta.py`/`project_source.py` | 현행 유지 | 단 import 출처를 `openapi`→`base`로 교체 |

배치 판단 메모:
- `ApiSection` 은 md/csv 전용이나 **ApiDocument 의 자식 부품**이라 별도 파일보다
  `document.py` 동거가 응집적(파일 수 억제, ponytail). 굳이 `section.py` 안 뺌.
- `ApiChunk` 는 `EMBEDDING_DIM`·`TEXT_TSV_EXPRESSION`(임베딩 dim·FTS 표현식)과
  강결합이라 `chunk.py` 로 함께 뺀다 — 이 상수들은 openapi 무관.
- `_utcnow` 는 document/openapi 양쪽이 쓰므로 `base.py` 로 승격(중복 제거).
- JSON 디코드 헬퍼는 param/body/response/schema 전용 → `openapi.py` 잔류.

---

## 2. Base 이동 시 순환 임포트 리스크와 회피

### 결합 그래프(분리 후) — 비순환(DAG) 유지 가능

```
base.py            (리프: 아무 모델도 import 안 함)
  ▲   ▲   ▲   ▲
  │   │   │   └── document_meta.py  (base만)
  │   │   └────── project_source.py (base만)
  │   └────────── document.py       (base만)
  └── chunk.py    (base + document)      ← FK/관계 대상 ApiDocument
      openapi.py  (base + document)      ← FK/관계 대상 ApiDocument
```

**핵심: 부모(`ApiDocument`)는 자식(`ApiEndpoint`/`ApiChunk`…)을 import 하지
않는다.** 따라서 `document.py` 는 `chunk.py`/`openapi.py` 를 참조하지 않아
사이클이 안 생긴다.

### 왜 부모가 자식을 import 안 해도 되나 (SQLAlchemy 메커니즘)

- `ApiDocument.chunks: Mapped[list["ApiChunk"]] = relationship(back_populates=...)`
  의 `"ApiChunk"` 는 **문자열 forward-ref** — import 타임이 아니라 **매퍼 설정
  시점에 `Base.registry` 에서 클래스명으로 해석**된다.
- 자식의 `document: Mapped[ApiDocument] = relationship(...)` 도
  `from __future__ import annotations`(현행 존재)로 애노테이션이 문자열이라
  **정의 시점에 `ApiDocument` 를 평가하지 않는다.**
- 자식의 `ForeignKey("api_document.id")` 는 **테이블명 문자열** — 클래스 import 불요.

즉 자식→부모 import 는 **가독성/명시성 목적**일 뿐 필수가 아니다. 그래도
`chunk.py`/`openapi.py` 가 `document.py` 를 import 하는 편을 권고(타입힌트 실체화
+ 등록 보장). 이 방향은 단방향이라 안전.

### 남는 실제 위험 — "매퍼 설정 시점에 전 클래스가 등록됐나"

문자열 관계 해석은 **모든 모델 클래스가 같은 registry 에 등록된 뒤** 첫 쿼리
전에 성립해야 한다. 어떤 모델 모듈을 아무도 import 안 하면
`"expression 'ApiChunk' failed to locate a name"` 로 **런타임에 터진다.**
→ 이게 분리의 진짜 함정이며, §3 등록 허브로 해소한다.

---

## 3. 분리 시 깨질 수 있는 지점과 대응

### 3-1. `create_all()` — 등록 누락 시 테이블 미생성

현행 `create_all` 은 `openapi.py` 안에서 `document_meta`/`project_source` 를
명시 import 해 등록을 보장한다. 분리하면 `document.py`/`chunk.py`/`openapi.py`
도 같은 대상이 되어, **한 곳이라도 빠지면 그 테이블이 안 생긴다.**

**대응**: `create_all` 을 `app/models/__init__.py` 로 옮기고, 거기서 **전 모델
모듈을 import**(등록) 후 `Base.metadata.create_all` 호출. `__init__` 이 유일한
등록 지점이 된다.

### 3-2. `alembic/env.py` — 등록 누락 시 autogenerate 오탐(DROP TABLE)

`env.py:10` 은 `from app.models.openapi import SCHEMA, Base` + 명시
`import app.models.document_meta`/`project_source`(주석: 누락 시 `alembic check`
가 `remove_table` 오탐). 분리하면 **모든 새 모듈을 env.py 가 import** 해야 한다.

**대응**: env.py 를 `import app.models` + `from app.models import SCHEMA, Base`
한 줄로 단순화 — `__init__`(§3-1)이 전 모듈을 import 하므로 **여기서
개별 모듈 나열이 사라지고, 향후 모델 추가 시 env.py 를 안 건드려도 된다.**

### 3-3. 안전망: `tests/unit/test_alembic_env_metadata.py`

이 테스트는 서브프로세스로 `alembic check`(compare_metadata)를 돌려 pending
diff 0 을 단언한다 — **env.py 가 모델을 빠뜨리면 즉시 실패**. 즉 §3-1/3-2
누락은 이 테스트가 자동으로 잡는다. 분리 작업의 검증 게이트로 그대로 활용.
(로컬 Postgres 가 head 까지 올라와 있어야 함.)

### 3-4. import 팬아웃 — ~30개 사이트 수정 (최대 비용)

`from app.models.openapi import ...` 가 app·tests·scripts 약 30곳에 흩어져
있고(Base/EMBEDDING_DIM/DEFAULT_PROJECT/PROJECT_MAX_LENGTH/ApiChunk/ApiDocument
/ApiSection/DocumentSyncHistory/create_all 등 대부분이 openapi.py 밖으로 이동).
심볼이 옮겨가면 **해당 import 문이 전부 깨진다.**

**대응(권고)**: `__init__` 이 전 심볼을 re-export 하게 만들고, 호출부를
**`from app.models import X`** 로 통일. 이렇게 하면:
- 물리 파일이 어디든 import 경로가 안정(파일 재배치에 강건).
- 팬아웃 수정이 "출처를 `.openapi`→`app.models` 로 바꾸는" **단일 기계적 치환**
  1패스로 끝남(심볼별로 파일을 추적할 필요 없음).
- **back-compat 재-export 껍데기를 openapi.py 에 남기지 않는다**(그건 "openapi 가
  비-openapi 를 export" 냄새를 되살리는 크루프트 — 금지).

### 3-5. 병행 project_source 마이그레이션과의 충돌

방금 물결에서 `openapi.py`(operation_id/example 제거, create_all import 교체)
와 env.py 를 이미 건드린다. 이 분리도 같은 두 파일을 크게 건드리므로 **동시
진행 시 충돌**. → **project_source 물결이 머지된 뒤** 착수(§4).

---

## 4. 실행 순서 권고 (developer 착수 시)

1. project_source 마이그레이션 물결(문서 19) 머지 확인 — `openapi.py`/env.py 안정화.
2. `base.py`/`document.py`/`chunk.py` 신설 + 심볼 이관, `openapi.py` 는 5개
   openapi 모델만 잔류.
3. `app/models/__init__.py`: 전 모델 모듈 import + `Base`/`create_all`/상수·
   모델 클래스 re-export + `create_all` 이관.
4. `alembic/env.py`: `import app.models` + `from app.models import SCHEMA, Base`
   로 축약(개별 모듈 나열 제거).
5. `document_meta.py`/`project_source.py`: `from app.models.base import ...` 로 교체.
6. import 팬아웃: 전 사이트를 `from app.models import ...` 로 기계 치환.
7. 검증: `uv run alembic upgrade head` 후 `alembic check`(=위 회귀 테스트) 통과,
   전체 테스트 그린. **DB 스키마·마이그레이션은 무변경**(순수 코드 재배치)임을
   확인(autogenerate diff 0 이어야 정상).

---

## 5. 판정·권고

- **분리 타당**: 파일명↔내용 불일치는 실재하고, 레이어(base) + 도메인(document
  /chunk/openapi) 기준 분리는 응집도를 올린다.
- **단 순수 리팩터**(행위·스키마·성능 무변경)라 **긴급도 낮음**. 얻는 건 명명
  정합·탐색성, 잃는 건 30-사이트 팬아웃 diff.
- **본질 이득은 등록 허브(`__init__`)** — env.py/create_all 의 "모듈 나열 누락"
  버그류(과거 project_source 누락 사건과 동종)를 구조적으로 제거. 분리를 하든
  안 하든 이 허브화만 따로 해도 가치 있음.
- **권고**: 분리 진행하되 (1)project_source 물결 착지 후 (2)독립 커밋으로
  (3)`__init__` 허브 + `from app.models import` 통일 방식으로. back-compat
  껍데기는 두지 않는다.
