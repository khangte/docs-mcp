# 32. refresh_index 배치 자동화 설계 — 서버 밖 상주 러너

- 작성: architect
- 요청: lead — "대상은 검색품질 평가 배치가 아니라 `refresh_index`(문서 재색인) 배치다.
  MCP 서버 내부 스케줄링 반려는 유지하되 '서버 밖 상주 러너' 방향으로 실제 설계를 진행하라."
- 선행 판정: `docs/architect-review/31-eval-batch-automation.md` §2 (내부 스케줄러 반려)
- 대상 코드: `app/mcp/tools/sources.py:34`(`refresh_index`),
  `app/services/documents/document_index_service.py`, `app/services/ingestor/sync_service.py:137`(`resync`)

---

## 0. 전제 — 무엇이 유지되고 무엇이 바뀌나

doc/31 §2의 반려는 그대로 유지한다: **MCP stdio 서버 프로세스 안에 크론·APScheduler를 두지
않는다**(클라이언트가 세션마다 띄우는 단명 프로세스라 중복 기동·리더 선출 문제가 생기고,
클라이언트가 서버를 내리면 스케줄도 죽어 "주기 실행" 계약을 지킬 수 없다).

바뀌는 것은 **스케줄 주체를 서버 밖으로 옮겨 실제로 만든다**는 점이다. 아래 설계에서
애플리케이션은 스케줄을 모른다 — 한 번 돌고 종료하는 배치 진입점만 갖고, 주기는 OS
스케줄러가 소유한다.

## 1. 왜 필요한가 — 자동화의 이득은 "편의"가 아니라 "검색 정확성"

`search_documents`는 2단계 구조다(`document_search_service.py` 모듈 docstring).

1. **1단계**: `document_meta` 캐시의 제목·URL 토큰 매칭으로 후보를 추린다.
   **후보가 0건이면 본문 fetch 없이 즉시 빈 리스트를 반환**한다.
2. **2단계**: 1단계 후보의 본문만 실시간으로 가져와 점수를 매긴다.

즉 **메타 캐시에 없는 문서는 검색 경로에 아예 진입하지 못한다.** 그런데 이 캐시를 채우는
유일한 경로가 클라이언트 LLM의 `refresh_index` 호출이다. 그 결과 현재 `search_documents`
docstring은 "캐시에 없는 신규 문서는 검색되지 않을 수 있으며, 그럴 때는 refresh_index를
먼저 실행한다"라고 **탐지 책임을 사용자·호출 LLM에게 넘기고 있다.**

자동화가 없애는 것은 이 루프다.

- 지금: 사용자가 문서를 만든다 → 검색한다 → **빈 결과** → 원인을 짐작한다 → refresh를
  요청한다 → 다시 검색한다.
- 이후: 배치가 캐시를 최신으로 유지 → 첫 검색이 바로 맞는다.

부수 이득으로, "검색 전에 일단 refresh_index부터 호출"하는 회피 습관이 생기면 그 호출의
외부 API 지연이 그대로 대화 지연이 된다. 배치가 미리 채워 두면 검색 경로는 캐시 히트로 끝난다.

삭제 반영도 같은 축이다. 원본에서 사라진 문서는 refresh 시점에만 캐시에서 제거되므로
(`_refresh_source`의 삭제 감지), 자동화 전에는 없어진 문서가 검색 결과에 계속 뜬다.

## 2. 트리거 조건 — 주기 폴링이다 (변경 감지 푸시가 아니다)

`refresh_index`는 성격이 다른 두 작업을 한 도구에 담고 있다. **자동화 주기도 갈라야 한다.**

### 2.1 축 A — 메타 캐시 동기화 (핵심, 잦게)

`document_index_service.refresh()`: 각 (project, source)의 `list_files()`로 목록 전량을 받아
제목·수정시각·URL만 upsert하고, 목록에서 사라진 행은 삭제한다. 본문은 가져오지 않는다.

**푸시(웹훅)를 채택하지 않는 이유**: Google Drive push notification과 Notion webhook은 둘 다
**공개 HTTPS 수신 엔드포인트**를 전제한다. 이 저장소에는 HTTP 서버가 없다(MCP stdio 전용).
수신 서버·TLS·공개 주소를 새로 들여야 하고, Drive watch 채널은 만료되므로 **재등록 크론이
또 필요**하다 — 폴링을 없애려다 폴링을 하나 더 만드는 구조다. 얻는 것은 반영 지연이
1시간에서 수 초로 줄어드는 것뿐이며, 문서 검색 캐시에 그만한 값어치가 없다. **폴링 채택.**

**폴링 자체가 이미 변경 감지다**: `_apply_changes`가 `modified_at`·제목·URL을 비교해 실제로
바뀐 행만 `updated`로 세고, 동일하면 `last_synced_at`만 갱신한다. 매 틱 전량 조회여도 DB
쓰기는 변경분에 비례한다.

**증분 조회로 좁히지 않는다**: Drive `modifiedTime >`·Notion `last_edited_time` 필터로 목록을
줄일 수 있지만, **삭제 감지가 전량 목록을 전제로 한다**(`existing` 집합에서 `seen`을 뺀
나머지를 삭제로 판정). 증분으로 바꾸면 삭제된 문서가 캐시에 영구히 남는다. 목록 호출은
메타만 받으므로 저렴하다 — 전량 유지가 맞다.

**권장 주기: 1시간.**(lead 지시로 15분안에서 변경 — 협업문서를 만든 뒤 검색이 안 되는
체감 지연이 분 단위가 아니라 시간 단위로도 충분히 허용되는 운영 판단) 근거는 (a) 신규
문서가 검색에 잡히기까지 최대 1시간 지연은 실시간 협업 도구가 아닌 사내 문서 검색
용도로 충분히 허용되는 수준이고, (b) (project, source)당 하루 24회 목록 조회는
Drive/Notion 쿼터에 무의미한 수준이며 15분 대비 API 호출량이 1/4로 준다는 점이다. 단
Drive 어댑터는 하위 폴더를 `MAX_FOLDERS`까지 BFS로 순회하므로 **호출 수가 폴더 수에
비례**한다 — 폴더 트리가 큰 프로젝트는 이보다 더 늘린다(T6 실측 후 조정값 기록).

**T6 실측(완료)**: 실 소스 1틱 = **47초**, 1시간 주기 예산 3600초의 **1.3%**. 주기 대비
여유가 커 1시간을 조정 없이 확정한다. 폴더 수에 비례하는 특성은 그대로이므로 폴더 트리가
훨씬 큰 배포에서는 자기 환경 재측정이 필요하다는 단서만 README에 남긴다. 함께 systemd
user timer 를 실제 등록하고 1회 수동 실행해 스케줄 경로(cwd/PATH/자격증명/로그)를
검증했다. 이 과정에서 `requests` 미선언 결함이 드러나 pyproject 에 추가했다 —
`google.auth.transport.requests.Request` 가 하드 의존하는데 선언이 빠져 drive 축이 매 틱
즉시 실패하고 있었다(커밋 `809bdea`).

### 2.2 축 B — URL 기반 Document 재색인 (부수, 드물게)

`include_registered=True` 경로(`_resync_registered` → `sync_service.resync`): `source_url`이 있는
Document마다 원본을 다시 fetch하고 해시를 비교해, 바뀐 문서만 재파싱·재임베딩한다.

비용 축이 A와 다르다. **변경이 없어도 문서마다 네트워크 fetch가 발생**하고, 변경되면 로컬
CPU 임베딩이 돈다. 1시간마다 돌릴 물건이 아니다. 이미 도구에서도 "비용이 크므로 옵트인"으로
기본 False다.

**권장 주기: 1일 1회(야간).** `--force`는 배치에서 쓰지 않는다 — 해시 동일 시 skip이 정상
경로이고, force는 사람이 이상 징후를 의심할 때 쓰는 수동 스위치로 남긴다.

### 2.3 두 축을 한 타이머에 묶지 않는다

주기가 다르고(1시간 vs 1일), 실패 파급도 다르다(A 실패는 검색 신선도 저하, B 실패는 특정
문서 색인 정체). 한 타이머로 묶으면 무거운 B가 가벼운 A의 주기를 지배한다.

## 3. 어디서 어떻게 띄우나

**배포 환경 사실관계**: 앱은 컨테이너가 아니다(`docker-compose.yml`에는 postgres만 있다).
MCP 서버는 클라이언트가 `uv`로 직접 실행한다. 따라서 "상주 러너"를 놓을 자리는 호스트다.

### 3.1 채택 — 원샷 CLI 진입점 + OS 스케줄러

**앱에는 루프도 스케줄러도 넣지 않는다.** 한 번 돌고 종료하는
`python -m app.scripts.refresh_documents`만 만든다. 주기 관리·다음 틱 재시도·로그 보존은
cron/systemd timer가 이미 하는 일이라 파이썬으로 재구현할 이유가 없다. 이 저장소에는
같은 형태의 선례가 이미 있다(`app/scripts/reembed.py` — `bootstrap_app_state()` 후 1회 실행,
`__main__` 진입).

**갱신 주기를 `Settings`에 넣지 않는다.** `DOCS_MCP_REFRESH_INTERVAL` 같은 필드를 만들면
앱이 스스로 스케줄한다는 오해가 생기고, 실제 주기(타이머 파일)와 이중 관리가 된다.
주기는 타이머에만 존재한다.

### 3.2 스케줄러 선택지

| 옵션                       | 판정                                  | 근거                                                                                                                |
| -------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| **systemd user timer**     | **권장**(리눅스 호스트 상시 가동 시)  | `Persistent=true`로 꺼져 있던 동안 밀린 실행 보정, `OnFailure=`로 실패 훅, journal이 stderr JSON 로그를 그대로 수집 |
| **cron**                   | **대안**(WSL2 등 systemd 미가용 환경) | 어디에나 있고 단순. 다만 환경변수·cwd가 빈약해 3.4의 함정을 반드시 처리해야 함                                      |
| compose 서비스(sleep 루프) | 비권장                                | 앱 이미지가 없어 새로 만들어야 하고, `while true; sleep`은 스케줄러 재구현                                          |
| APScheduler 상주 프로세스  | 비권장                                | 의존성 추가 + "그 프로세스는 누가 살려두나"라는 감시 문제를 새로 떠안는다                                           |

현 개발 환경이 WSL2이므로 **운영 문서에는 systemd timer 유닛과 cron 두 줄을 모두 싣고,
기본 안내는 systemd 가용 시 timer**로 한다.

systemd user timer (예시):

```ini
# ~/.config/systemd/user/docs-refresh.service
[Service]
Type=oneshot
WorkingDirectory=/home/<user>/projects/docs-mcp
ExecStart=/home/<user>/.local/bin/uv run python -m app.scripts.refresh_documents

# ~/.config/systemd/user/docs-refresh.timer
[Timer]
OnBootSec=2min
OnUnitActiveSec=1h
Persistent=true
[Install]
WantedBy=timers.target
```

축 B는 같은 형태로 `docs-resync.service`(`ExecStart=... --include-registered`) +
`OnCalendar=daily` 타이머를 하나 더 둔다.

cron (대안):

```cron
0 * * * * cd /home/<user>/projects/docs-mcp && /home/<user>/.local/bin/uv run python -m app.scripts.refresh_documents >> output/logs/refresh.log 2>&1
30 3 * * *  cd /home/<user>/projects/docs-mcp && /home/<user>/.local/bin/uv run python -m app.scripts.refresh_documents --include-registered >> output/logs/resync.log 2>&1
```

### 3.3 중복 실행 방지 — Postgres advisory lock

1시간 타이머인데 한 틱이 1시간을 넘길 수 있다(대형 Drive 폴더 BFS, 야간 재색인). 겹치면 같은
소스를 동시 갱신해 삭제 감지가 서로의 중간 상태를 보게 된다.

- 배치 본문을 `pg_try_advisory_lock(<고정키>)`로 감싸고, 획득 실패 시 "이미 실행 중" INFO
  로그 후 **정상 종료(exit 0)** 한다.
- `flock` 대신 DB 어드바이저리 락을 쓰는 이유: **이미 있는 인프라라 새 의존성이 0**이고,
  나중에 러너가 여러 호스트로 늘어도 그대로 맞는다(파일 락은 호스트 로컬이라 그때 깨진다).
- **축 A와 축 B는 다른 락 키**를 쓴다. 야간 재색인이 1시간 틱을 굶기면 안 된다.

### 3.4 실행 환경 함정 (운영 문서에 반드시 싣는다)

크론/타이머 실행이 손으로 돌릴 때와 다르게 깨지는 지점들이다.

- **cwd**: `app/core/config.py`가 import 시점에 `load_dotenv(find_dotenv())`를 부른다.
  `find_dotenv()`는 **cwd 기준 상향 탐색**이라, cron의 기본 cwd(홈)에서는 `.env`를 못 찾는다.
  Drive 서비스계정 파일을 상대경로로 지정한 경우도 같이 깨진다.
  → `WorkingDirectory=` / `cd <repo> &&` **필수**.
- **PATH**: cron의 PATH에 `uv`가 없다. **절대경로**로 쓴다.
- **자격증명**: `DOCS_MCP_NOTION_TOKEN`, Drive 서비스계정 파일이 러너 사용자 권한으로 읽혀야
  한다(`secrets/` 권한 확인). 자격증명 누락은 소스 전량 실패 → exit 1로 드러난다(4.1).
- **로그**: 로거는 stderr에 JSON 한 줄을 낸다. systemd면 journal이 받고, cron이면
  `output/logs/`(이미 존재)로 리다이렉트한다. 해당 로그 파일 패턴을 `.gitignore`에 추가한다.

## 4. 기존 코드와의 통합 지점

현행 `refresh_index` 도구 본문(`app/mcp/tools/sources.py:73-85`)이 조립하는 것은 둘이다.

1. `bundle.document_index_service.refresh(source=..., project=...)` — 서비스 계층에 있다. 그대로 재사용 가능.
2. `include_registered`일 때 `_resync_registered(bundle, project=..., force=...)` —
   **이 함수가 MCP 계층인 `app/mcp/tools/sources.py`에 산다**(파일 하단 모듈 함수).

배치 스크립트가 (2)를 쓰려고 MCP 도구 모듈을 import하면 계층 역전이다(스크립트 → MCP 도구
→ 서비스). 로직을 복사하는 것은 더 나쁘다 — 부분 실패 처리·롤백 규칙이 두 벌이 된다.

**통합안: `_resync_registered`를 서비스 계층으로 내리고, 양쪽이 같은 함수를 부른다.**

- `app/services/documents/registered_resync.py`로 옮기고 이름을
  `resync_registered_documents(bundle, *, project, force)`로 공개화한다.
  **동작·반환 스키마는 무변경**(`total/reindexed/skipped/failed`).
- `sources.py`는 그 함수를 import해 payload에 얹는 얇은 래퍼로 남는다. 기존 MCP 도구 테스트는
  그대로 green이어야 한다(변경이 이동뿐이므로).
- 신규 `app/scripts/refresh_documents.py`:

  ```
  bootstrap_app_state() → build_services() → advisory lock 획득
    document_index_service.refresh(source, project)
    [--include-registered] resync_registered_documents(bundle, project, force)
  → 집계 INFO 로그 1줄 → 종료코드로 결과 전달
  ```

  `bootstrap_app_state()`가 `create_all` + `seed_default_sources`까지 수행하므로 러너도 서버와
  동일한 상태 전제를 얻는다(`reembed.py`와 같은 패턴).

- 인자는 도구와 같은 의미로 맞춘다: `--source drive|notion`, `--project`,
  `--include-registered`, `--force`. 타임아웃 인자는 두지 않는다 — 소스 어댑터가 이미
  `document_source_timeout_seconds`를 쓴다.

- **MCP 도구 `refresh_index`는 그대로 남긴다.** 사용자가 "지금 반영해줘"라고 할 때의 즉시
  경로가 여전히 필요하다. 배치는 그 경로를 대체하는 게 아니라 **평소 지연을 없애서 호출할
  일 자체를 줄인다.**

### 4.1 종료 코드 규약 (스케줄러가 읽는 유일한 신호)

| 상황                                         | 로그                                  | 종료코드                                       |
| -------------------------------------------- | ------------------------------------- | ---------------------------------------------- |
| 전 대상 실패(`refresh`가 `IntegrationError`) | ERROR                                 | **1** — systemd `OnFailure`/cron 메일이 잡는다 |
| 부분 실패(`failed_sources` 비어있지 않음)    | WARN + 실패 라벨                      | **0**                                          |
| 락 미획득(이미 실행 중)                      | INFO                                  | **0**                                          |
| 정상                                         | INFO + `synced/added/updated/removed` | **0**                                          |

부분 실패를 실패로 올리지 않는 이유: 서비스가 "실패한 항목만 다음 갱신에서 재시도"하도록
설계돼 있고(모듈 docstring), 매 틱마다 알림을 울리면 알림이 죽는다. 대신 로그에 실패
`<project>/<source>` 라벨이 남으므로 지속 실패는 로그로 추적된다.

## 5. 하지 않을 것 (명시)

- **웹훅 수신 서버** — 공개 HTTPS·채널 갱신 크론을 새로 들이는 값어치가 없다(2.1).
- **앱 내부 스케줄러/무한 루프** — doc/31 §2 판정 유지 + OS 스케줄러 재구현(3.1).
- **갱신 주기 설정값(`Settings` 필드)** — 스케줄 소유권을 이중화한다(3.1).
- **증분 목록 조회** — 삭제 감지를 파괴한다(2.1).
- **배치 실행 이력 테이블·대시보드** — 로그와 종료코드로 충분하고, 문서 재색인 이력은 이미
  `document_sync_history`에 남는다.
- **자동 `--force` 재색인** — 해시 비교를 무력화해 매일 전량 재임베딩을 유발한다(2.2).

## 6. 태스크 분할 (developer 인계용)

| #   | 내용                                                                                                | 완료 판단                                                  |
| --- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| T1  | `_resync_registered`를 `app/services/documents/registered_resync.py`로 이동, `sources.py`는 래퍼로  | 기존 MCP 도구 테스트 무수정 green                          |
| T2  | `app/scripts/refresh_documents.py` 진입점(인자 4종, 집계 로그, 종료코드 규약 4.1)                   | 로컬에서 1회 실행 시 도구 호출과 동일한 집계가 로그로 나옴 |
| T3  | advisory lock 가드(축 A/B 별도 키)                                                                  | 두 프로세스 동시 실행 시 뒤엣것이 INFO + exit 0            |
| T4  | 단위 테스트 — 가짜 서비스로 4.1의 4분기 종료코드 검증(DB·외부 API 없이)                             | `uv run pytest` green                                      |
| T5  | 운영 문서: README에 "자동 동기화" 절 — systemd 유닛 2종 + cron 2줄 + 3.4 함정 목록 + 주기 조정 기준 | —                                                          |
| T6  | (완료) 실 소스 1틱 소요 실측 → 1시간 주기 적정성 확인, 대형 Drive 폴더면 조정값 기록                 | 1틱 47초(예산 1.3%), 1시간 유지 확정 — §2.1                |

**커밋 분할**

- `refactor: registered resync 를 MCP 계층에서 서비스 계층으로 이동`
- `feat: 문서 소스 주기 동기화 배치 진입점(app/scripts/refresh_documents.py)`
- `feat: 배치 중복 실행 방지 advisory lock + 종료코드 규약`
- `docs: 자동 동기화(systemd timer/cron) 운영 가이드`

## 7. 요약

- 자동화의 목적은 실행 편의가 아니라 **메타 캐시 신선도 = 검색 정확성**이다. 지금은 캐시에
  없는 문서가 검색 자체에서 배제되고, 그 탐지 책임이 사용자에게 있다.
- 트리거는 **주기 폴링**이다. 웹훅은 이 배포 형태(HTTP 서버 없음)에 맞지 않고, 폴링이 이미
  변경 감지 역할을 한다. 축 A(메타, 1시간)와 축 B(URL 문서 재색인, 1일)를 분리한다.
- 실행 형태는 **원샷 CLI + systemd timer(대안 cron)**. 앱은 스케줄을 모른다. 중복 실행은
  Postgres advisory lock으로 막는다(새 의존성 0).
- 통합 지점의 유일한 구조 변경은 **`_resync_registered`를 MCP 계층에서 서비스 계층으로
  내리는 것**이다. 그 외 서비스 로직과 MCP 도구는 무변경으로 남는다.
