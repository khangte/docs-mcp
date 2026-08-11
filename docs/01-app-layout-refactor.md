# app/ 디렉터리 계층 분리 설계안

> **[2026-08-05]** 이 문서에 등장하는 FastAPI 관련 내용은 코드베이스에서 제거되었습니다. 현재는 MCP 서버 단일 진입점 구조입니다.

## 1. 배경과 문제

`app/` 최상위에 3계층이 뒤섞여 있다.

- **(A) 웹 전용**: `main.py`, `api/` (`routes/`, `dependencies.py`, `dependency_providers.py`)
- **(B) MCP 전용**: `mcp_server.py`, `mcp_types.py`
- **(C) 공유 코어**: `bootstrap.py`, `core/`, `models/`, `repositories/`, `schemas/`, `services/`

핵심 문제는 **경계 오배치**다. `AppState` / `ServiceBundle` / `build_services` /
`is_vector_fallback_available` 이 `app/api/dependencies.py`(웹 계층 이름)에 있으나,
실제로는 웹·MCP·bootstrap이 모두 의존하는 **컴포지션 루트**다.

현재 의존 현황(조사 결과):

| import 주체 | 대상 | 성격 |
| --- | --- | --- |
| `mcp_server.py` | `app.api.dependencies` → `AppState, ServiceBundle, build_services` | **MCP가 웹 계층을 import** |
| `bootstrap.py` | `app.api.dependencies` → `AppState` | 공유가 웹을 import |
| `main.py`, `api/routes/*` | `app.api.dependencies` | 웹 내부(정상) |
| 테스트 8개 | `app.api.dependencies` → `build_services` 등 | 컴포지션 루트를 직접 호출 |

즉 "MCP 전용" 파일이 "웹 전용" 패키지를 가로지른다. **이 파일 하나(`dependencies.py`)를
공유 계층으로 옮기는 것이 분리의 90%다.** 나머지 파일은 이미 계층이 명확하다.

## 2. 경계 결정: `web/` + `mcp/` + 코어는 최상위 유지

세 가지 안을 검토했다.

| 안 | 구조 | 판정 |
| --- | --- | --- |
| **안1 (채택)** | `app/web/`, `app/mcp/` 신설. 코어(`core/models/repositories/schemas/services/bootstrap`)는 최상위 유지. 컴포지션 루트만 `app/composition.py`로 이동 | ✅ 최소 이동, 경계 명확 |
| 안2 | `app/shared/` 패키지를 만들어 코어 전부를 그 아래로 이동 | ❌ 60+파일 경로 변경, import 전면 수정. 이득 대비 리스크 과다 |
| 안3 | 현행 유지 + `dependencies.py`만 이동 | △ 경계 파일이 여전히 `main.py`/`mcp_server.py`로 최상위에 섞임 |

**안1을 채택**한다. 이유:

- 코어 6종은 이미 도메인별로 잘 나뉘어 있고 "공유"임이 자명하다. `shared/`로 감싸는 것은
  YAGNI — 이름만 바뀌고 대량 경로 수정 리스크만 생긴다.
- 진짜 문제는 웹/MCP 진입점과 컴포지션 루트가 최상위에 흩어진 것. 이 둘만 각자 패키지로
  묶으면 "무엇이 웹 전용이고 무엇이 MCP 전용인가"가 디렉터리로 드러난다.

### 목표 구조

```
app/
├── composition.py          # ← (신설) AppState/ServiceBundle/build_services 등. 옛 api/dependencies.py
├── bootstrap.py            # 유지 (import 대상만 변경)
├── core/                   # 유지
├── models/                 # 유지
├── repositories/           # 유지
├── schemas/                # 유지
├── services/               # 유지
├── web/                    # ← (신설) 웹 전용
│   ├── __init__.py
│   ├── main.py             # 옛 app/main.py
│   ├── dependency_providers.py   # 옛 app/api/dependency_providers.py
│   └── routes/             # 옛 app/api/routes/*
│       ├── documents.py
│       ├── endpoints.py
│       ├── health.py
│       ├── search.py
│       └── sync.py
└── mcp/                    # ← (신설) MCP 전용
    ├── __init__.py
    ├── server.py           # 옛 app/mcp_server.py
    └── types.py            # 옛 app/mcp_types.py
```

`app/api/` 패키지는 제거된다(내용이 `web/`와 `composition.py`로 분산).

### 경계 규칙 (developer가 지켜야 할 계약)

1. `app/web/`, `app/mcp/`는 **서로 import하지 않는다.**
2. 둘 다 `app/composition.py`와 코어(`core/models/repositories/schemas/services`)에만 의존한다.
3. `app/composition.py`, `app/bootstrap.py`, 코어는 `web/`·`mcp/`를 **절대 import하지 않는다**
   (의존 방향: 진입점 → 컴포지션 루트 → 코어. 역방향 금지).

## 3. import 파급 범위와 변경 매핑

### 3.1 파일 이동 (git mv)

| 현재 | 이동 후 |
| --- | --- |
| `app/api/dependencies.py` | `app/composition.py` |
| `app/api/dependency_providers.py` | `app/web/dependency_providers.py` |
| `app/api/routes/*.py` | `app/web/routes/*.py` |
| `app/main.py` | `app/web/main.py` |
| `app/mcp_server.py` | `app/mcp/server.py` |
| `app/mcp_types.py` | `app/mcp/types.py` |

`app/api/__init__.py`, `app/api/routes/__init__.py`는 폐기하고
`app/web/__init__.py`, `app/web/routes/__init__.py`, `app/mcp/__init__.py`를 신설한다.

### 3.2 import 문 치환 (전량 grep 기반, 누락 없이)

절대 import만 허용(`ban-relative-imports = "all"`)하므로 문자열 치환으로 안전하게 처리 가능.

| 옛 경로 | 새 경로 | 영향 파일 수 |
| --- | --- | --- |
| `app.api.dependencies` | `app.composition` | app 6곳 + tests 8곳 = **14** |
| `app.api.dependency_providers` | `app.web.dependency_providers` | app 5곳 |
| `app.api.routes` | `app.web.routes` | `app/web/main.py` 내부 5개 import |
| `app.main` | `app.web.main` | tests 1곳(`conftest.py`) |
| `app.mcp_server` | `app.mcp.server` | tests 3곳(`conftest.py`, `test_mcp_server.py`, `test_mcp_documents.py`) |
| `app.mcp_types` | `app.mcp.types` | `app/mcp/server.py` 내부 |

**주의 지점 (developer 필독):**

- `bootstrap.py`의 `from app.api.dependencies import AppState` → `from app.composition import AppState`.
  bootstrap이 컴포지션 루트를 import하는 방향은 유지된다(정상).
- `web/main.py`의 `from app.bootstrap import ...`는 경로 불변.
- `mcp/server.py`가 더 이상 `app.api.*`를 건드리지 않게 된다 — **이 설계의 성공 판정 기준**:
  분리 후 `grep -rn "app.api" app/mcp/`가 0건이어야 한다.

## 4. tests/ 영향

테스트 파일은 **이동하지 않는다**(`tests/integration/test_api_*.py`, `test_mcp_*.py` 경로 유지).
import 경로만 3.2 표에 따라 치환한다.

- `tests/conftest.py`: `from app.main import create_app` → `from app.web.main import create_app`,
  `from app.mcp_server import create_mcp_server` → `from app.mcp.server import create_mcp_server`,
  `from app.api.dependencies import AppState/build_services` → `from app.composition import ...`
- `tests/unit/*` 중 `build_services`/`is_vector_fallback_available`를 import하는 7개 파일:
  `app.api.dependencies` → `app.composition` 치환만.
- `tests/integration/test_mcp_project_isolation.py`도 동일.

테스트 로직·픽스처 이름은 불변. 순수 import 경로 치환이므로 동작 회귀 위험 낮음.

## 5. 진입점 변경

entry-point(`[project.scripts]`)는 **없음**을 확인했다. 실행 경로 2개만 변경된다.

| 항목 | 현재 | 변경 후 | 조치 위치 |
| --- | --- | --- | --- |
| MCP 서버 실행 | `python -m app.mcp_server` | `python -m app.mcp.server` | README.md:131, Claude Desktop 설정 예시 |
| 웹서버 실행 | `uvicorn app.main:create_app --factory` | `uvicorn app.web.main:create_app --factory` | README.md:222 |
| `app = create_app()` 모듈 변수 | `app.main:app` | `app.web.main:app` | `web/main.py` 내 유지, 문서에 반영 |

- `app/mcp/server.py` 하단의 `if __name__ == "__main__": main()`은 그대로 동작
  (`python -m app.mcp.server`가 이 블록을 실행).
- **문서 갱신 필수**: `README.md`의 실행 명령·디렉터리 트리(31~34행), MCP 등록 예시(131행),
  웹 실행 예시(222행). 이는 architect가 developer 작업 완료 후 README에 반영한다.

## 6. 마이그레이션 리스크와 단계적 이행안

리스크는 **낮음~중간**. 순환 import 없음(의존 방향 단방향), entry-point 없음, 테스트가
컴포지션 루트를 직접 호출하므로 회귀 감지 가능.

### 단계 (각 단계 후 `uv run pytest`로 그린 확인)

1. **컴포지션 루트 이동**: `git mv app/api/dependencies.py app/composition.py`.
   이 파일을 import하는 14곳 일괄 치환(`app.api.dependencies` → `app.composition`).
   → 테스트 통과 확인. **여기까지가 MCP↔웹 결합 제거의 핵심.**
2. **MCP 패키지 생성**: `app/mcp/__init__.py` 신설, `mcp_server.py`→`mcp/server.py`,
   `mcp_types.py`→`mcp/types.py` 이동. 내부 `app.mcp_types`→`app.mcp.types` 치환.
   테스트 3곳 import 치환. → 통과 확인.
3. **웹 패키지 생성**: `app/web/__init__.py`, `web/routes/__init__.py` 신설.
   `main.py`→`web/main.py`, `dependency_providers.py`→`web/dependency_providers.py`,
   `api/routes/*`→`web/routes/*` 이동. 관련 import 치환. `app/api/` 잔여 폐기.
   `conftest.py` import 치환. → 통과 확인.
4. **문서·실행경로 갱신**: README 실행 명령/트리 갱신(architect). 로컬에서
   `python -m app.mcp.server`, `uvicorn app.web.main:create_app --factory` 기동 확인.

각 단계가 독립 커밋(파일별/논리단위 atomic). 1단계만으로도 "MCP가 웹을 import하는" 문제가
해소되므로, 시간 부족 시 1단계까지가 최소 유효 산출물이다.

### 검증 체크리스트 (완료 판정)

- [ ] `grep -rn "app\.api" app/ tests/` → 0건
- [ ] `grep -rn "app\.main\b" app/ tests/` → 0건 (`app.web.main`으로 대체)
- [ ] `grep -rn "app\.mcp_server\|app\.mcp_types" app/ tests/` → 0건
- [ ] `grep -rn "app\.api" app/mcp/` → 0건 (MCP↔웹 분리 성공 판정)
- [ ] `uv run pytest` 그린
- [ ] `python -m app.mcp.server`, `uvicorn app.web.main:create_app --factory` 정상 기동

## 7. 파일명 결정 근거

- `dependencies.py` → **`composition.py`**: 이 파일은 FastAPI `Depends`용 의존성이 아니라
  객체 그래프를 조립하는 컴포지션 루트다. FastAPI 의존성 주입 함수(`get_services`,
  `get_app_state`)는 별개로 `web/dependency_providers.py`에 남는다. 이름을 분리해
  "웹 프레임워크 의존성"과 "앱 컴포지션"을 구분한다.
- `mcp_server.py` → **`mcp/server.py`**, `mcp_types.py` → **`mcp/types.py`**: 패키지
  네임스페이스가 `mcp.` 접두를 제공하므로 파일명에서 `mcp_` 중복 제거.
