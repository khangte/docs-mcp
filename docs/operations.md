# 운영 가이드

[README](../README.md) 는 프로젝트 개요와 최단 실행 경로만 다룹니다. 이 문서는 실제로
운영할 때 필요한 상세 — 클라이언트별 등록 방법, 전체 도구 목록, 배치 자동화 — 를 모읍니다.

- [MCP 클라이언트 등록](#mcp-클라이언트-등록)
- [레거시 환경변수](#레거시-환경변수)
- [프로젝트 격리 상세](#프로젝트-격리-상세)
- [제공되는 도구 전체 목록](#제공되는-도구-전체-목록)
- [자동 동기화 (배치)](#자동-동기화-배치)

진입점은 `app/mcp/server.py` 이며, 등록해두면 클라이언트가 `command`+`args`로 프로세스를
실행해 stdio 로 통신합니다. 단 MCP 서버가 DB 에 접속하므로 **PostgreSQL(+pgvector)은 미리
떠 있어야** 합니다.

## MCP 클라이언트 등록

Claude Code(CLI) 등록은 [README](../README.md#mcp-연동) 에 있습니다. 아래는 그 외 경로입니다.

### Claude Desktop (macOS/Windows)

Claude Desktop 의 설정 파일(`claude_desktop_config.json`)에 서버를 추가합니다.

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "docs-mcp": {
      "command": "uv",
      "args": ["run", "python", "-m", "app.mcp.server"],
      "cwd": "/path/to/docs-mcp",
      "env": {
        "DOCS_MCP_DATABASE_URL": "postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp"
      }
    }
  }
}
```

### uvx (uv 프로젝트 설치 없이 실행)

`pyproject.toml` 에 `docs-mcp` 스크립트가 등록되어 있어, `uv sync` 없이도
[uvx](https://docs.astral.sh/uv/guides/tools/)로 바로 실행할 수 있습니다.
`command`+`args`를 `uvx`+`["--from", "/path/to/docs-mcp", "docs-mcp"]`로
바꾸면 되며, `DOCS_MCP_DATABASE_URL`은 위와 동일하게 전달합니다.

> `uvx`는 애플리케이션 코드만 격리 설치할 뿐 DB는 대신 띄워주지 않으므로,
> 실행 전 PostgreSQL(+pgvector)이 별도로 떠 있어야 합니다(온프레미스 서버 어디든
> `docker compose up -d postgres`로 가능, 클라우드 관리형 Postgres 필수 아님).

## 레거시 환경변수

<!-- AUTO-GENERATED: app/core/config.py 기준 -->

**`project` 개념 도입 전 하위호환용입니다. 새로 시작한다면 비워두세요.**
`project="default"` 전용 슬롯 1개뿐이고 값을 바꾸면 서버 재시작이 필요합니다.
`register_drive_source`/`register_notion_source`/`register_notion_page` 도구로
등록하는 쪽을 권장합니다(project 별 다중 등록 가능, 재시작 불필요).

| 변수                           | 설명                                                                | 기본값 |
| ------------------------------ | --------------------------------------------------------------------- | ------ |
| `DOCS_MCP_DRIVE_FOLDER_ID`     | 기본 프로젝트용 Google Drive 폴더 ID(하위 폴더 재귀 포함)              | (없음) |
| `DOCS_MCP_NOTION_DATABASE_ID`  | 기본 프로젝트용 Notion 데이터베이스 ID. 비우면 워크스페이스 전체가 대상 | (없음) |
| `DOCS_MCP_NOTION_PAGE_ID`      | 기본 프로젝트용 Notion 허브 페이지 ID. 하위 페이지/데이터베이스를 재귀 탐색(최대 4단계)한 결과가 대상 | (없음) |

> `DOCS_MCP_NOTION_DATABASE_ID` 와 `DOCS_MCP_NOTION_PAGE_ID` 를 함께 설정하면 page 가 우선하고 database 는 무시됩니다.

<!-- /AUTO-GENERATED -->

## 프로젝트 격리 상세

이 서버는 하나의 프로세스·하나의 DB 로 **여러 프로젝트**의 문서를 함께
서비스합니다. `register_document` 는 `project` 지정이 필수이고, 조회·검색
도구들(`list_documents`, `search_endpoints`, `list_tags`, `resolve_ref`,
`search_documents`, `refresh_index`)은 `project` 로 범위를 좁힐 수
있습니다(생략 시 전체 프로젝트 대상 — 하위 호환).

> **`project` 는 단순 문자열 태그이며 보안 경계가 아닙니다.** 인증도 접근 제어도
> 없어, 같은 DB 자격증명을 가진 누구나 `project` 필터 없이 모든 문서를 조회할 수
> 있습니다. 막아주는 것은 "여러 프로젝트의 검색 결과가 섞이는 문제"뿐이므로, 신뢰
> 수준이 다른 사용자를 격리하려면 별도 서버·DB·인증 계층이 필요합니다.

프로젝트별 Drive/Notion 소스 매핑은 `register_drive_source`/`register_notion_source`/
`register_notion_page` 로 하며, 매핑 등록/변경은 서버 재시작 없이 다음 호출부터
반영됩니다. 자격증명(`DOCS_MCP_DRIVE_SERVICE_ACCOUNT_FILE`/`_JSON`,
`DOCS_MCP_NOTION_TOKEN`)은 서버 전체가 하나씩만 갖고, 프로젝트별로 달라지는
것은 그 자격증명으로 접근할 **폴더/DB 범위**뿐입니다.

**기존 문서의 취급**: `project` 개념이 도입되기 전에 등록된 문서는 모두
`project="default"` 로 백필되어 있습니다. 다른 프로젝트로 옮기려면 문서를
재등록하거나, DB 에 직접 SQL 로 `project` 컬럼을 갱신해야 합니다(제공되는
도구 중에는 기존 문서의 project 를 바꾸는 기능이 없습니다).

## 제공되는 도구 전체 목록

<!-- AUTO-GENERATED: app/mcp/server.py 도구 docstring 기준 -->

| 도구                     | 설명                                                                                                                                                                             | 반환 필드                                                                                                               |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `list_documents`         | 등록된 문서(Markdown/CSV/PDF/DOCX/OpenAPI)의 요약 목록을 반환한다. `project` 로 범위를 제한할 수 있다(생략 시 전체)                                                                       | document_id, title, version, doc_type, project, source_url, endpoints_count, indexed_at                                 |
| `register_document`      | 신규 문서를 등록한다. `project`(필수)와 URL 또는 원문 중 하나를 제공해야 한다 (`doc_type`으로 openapi/markdown/csv 강제 지정 가능, 생략 시 자동 판별)                            | document_id, title, version, doc_type, project, endpoints_count, sections_count, chunks_count, status                   |
| `search_endpoints`       | 자연어/키워드로 엔드포인트 **후보만** 가볍게 검색한다 (키워드+벡터 RRF 융합, 기본 전략 `rrf`). `project`/`document_id` 로 범위를 제한할 수 있다                                      | items[{endpoint_id, method, path, summary, match_type}]                                                                 |
| `get_endpoint_details`   | 특정 엔드포인트의 상세 정보를 조회한다 (`include_example=true`일 때만 curl 예시 포함)                                                                                            | endpoint_id, document_id, method, path, summary, description, tags, parameters, request_body, responses, (example_code) |
| `resolve_ref`            | `$ref` 컴포넌트 스키마를 필드 목록으로 펼친다 (중첩 `$ref`는 이름만 표기). `project`/`document_id` 로 여러 프로젝트의 동명 스키마 중 하나를 특정할 수 있다                       | name, document_id, fields[{name, type, required, description}]                                                          |
| `list_tags`              | 등록 문서의 태그 목록과 태그별 엔드포인트 수를 반환한다. `project`/`document_id` 로 범위를 제한할 수 있다                                                                        | tags[{name, endpoint_count}]                                                                                            |
| `search_documents`       | 팀 협업 문서(Google Drive / Notion)를 검색한다 (제목·본문 청크 **3-arm RRF**: `document_meta` 제목 + 색인된 section 청크의 키워드(FTS)·벡터 순위를 융합, 기본 전략 `indexed`). `project`/`source` 로 범위를 제한할 수 있다. 결과 0건/부족 시 `query_variants`(동의어·유사 표현 목록)로 후보 필터만 넓혀 재질의할 수 있다(점수·순위엔 영향 없음) | items[{title, source, project, url, snippet, score, version}] — `score` 는 RRF 점수라 **절대값 비교 불가·순서만 유의미** |
| `get_document`           | `source`("drive"/"notion")와 `external_id`(Drive file ID 또는 Notion page ID)로 협업 문서 한 건의 전체 원문을 조회한다 (항상 최신 원문, 캐시 아님)                               | title, source, url, content                                                                                             |
| `refresh_index`          | 협업 문서 메타 캐시(제목·수정일)를 원본과 동기화한다 (본문은 저장하지 않음). `project` 로 특정 프로젝트만 갱신할 수 있다. `include_registered=true`(기본 false)면 URL로 등록한 Document 도 원본을 재fetch+재색인한다(`raw_document` 등록분은 자동 제외, `force=true` 로 해시 동일해도 강제 재색인) | synced, added, updated, removed, failed_sources, (include_registered=true 일 때만) registered{total, reindexed, skipped, failed} |
| `register_drive_source`  | 프로젝트에 Google Drive 폴더를 매핑한다(upsert, 같은 project 재호출 시 폴더 교체)                                                                                                | project, folder_id, status                                                                                              |
| `list_drive_sources`     | 등록된 프로젝트→Drive 폴더 매핑 목록을 반환한다(project 오름차순). `project` 로 범위를 제한할 수 있다                                                                            | items[{project, folder_id, created_at, updated_at}]                                                                     |
| `remove_drive_source`    | 프로젝트의 Drive 폴더 매핑을 제거한다(멱등 — 미등록 project 도 오류 아님)                                                                                                        | project, removed                                                                                                        |
| `register_notion_source` | 프로젝트에 Notion 데이터베이스를 매핑한다(upsert, 같은 project 재호출 시 DB 교체). 한 project 는 database 매핑과 page 매핑을 동시에 가질 수 없다(나중 호출이 이전 매핑을 덮어씀) | project, database_id, status                                                                                            |
| `register_notion_page`   | 프로젝트에 Notion 허브 페이지를 매핑한다(upsert). 지정한 페이지 하위의 페이지·데이터베이스(그 안의 행 포함)를 재귀 탐색(최대 4단계)한 결과가 검색 대상이 된다                    | project, page_id, status                                                                                                |
| `list_notion_sources`    | 등록된 프로젝트→Notion 데이터베이스/페이지 매핑 목록을 반환한다(project 오름차순). `project` 로 범위를 제한할 수 있다                                                            | items[{project, database_id, kind, created_at, updated_at}]                                                             |
| `remove_notion_source`   | 프로젝트의 Notion 데이터베이스/페이지 매핑을 제거한다(멱등 — 미등록 project 도 오류 아님)                                                                                        | project, removed                                                                                                        |

협업 문서(Drive/Notion)의 검색 전략은 `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY` 로
정한다(기본 `indexed`, 롤백용 `fetch`, 미인식 값은 `fetch` 로 degrade).

- **`indexed`(기본)** — 제목(`document_meta`) + 동기화 시점에 색인된 본문 section
  청크의 키워드·벡터 순위를 RRF 로 융합한다. **검색 경로에서 외부 API 를 호출하지
  않으므로** 스니펫은 마지막 동기화 시점의 본문 발췌다. 본문 색인은
  `refresh_documents --index-bodies`(아래 축 C)가 채운다 — 아직 색인되지 않은
  문서도 제목 신호만으로는 계속 검색된다(색인 여부에 따른 분기가 없다).
- **`fetch`(롤백 스위치)** — 제목으로 후보를 추린 뒤 후보 본문만 호출 시점에 실시간
  조회해 가중합한다. 스니펫이 항상 최신인 대신 외부 API 비용이 크고, 텍스트를 못
  뽑는 파일(이미지·영상 등)은 fetch 실패로 결과에서 통째로 빠진다.

어느 전략이든 문서 목록·제목은 메타 캐시에서 오므로, 새로 만든 문서가 검색되지
않으면 `refresh_index`(또는 배치)를 먼저 실행한다. 원문 전체 조회(`get_document`)는
전략과 무관하게 항상 실시간이다.

흐름 상세: [`search-flow.md`](search-flow.md) §3.

Drive/Notion 자격증명이 없으면 협업 문서 도구(`search_documents`/`get_document`/
`refresh_index`)는 등록은 되지만 호출 시 "미구성" `IntegrationError`
(`no document source is configured: ...`)를 반환한다. **"소스 미설정"과 "검색 결과
0건"은 구별된다** — 소스가 정상 구성됐는데 맞는 문서가 없으면 오류가 아니라 빈
`items` 를 돌려준다. 어느 경우든 OpenAPI 경로는 영향받지 않는다.

결과가 0건이거나 기대보다 적으면 문서 제목이 질의와 다른 표현을 쓰는 경우가 많다.
같은 `query` 로 재호출하되 `query_variants` 에 동의어·영한 혼용을 담아 넘긴다:

```
search_documents(query="주문조회 API", query_variants=["결제 내역 조회", "order lookup"])
```

`query_variants` 는 제목·키워드 arm 의 후보 필터만 넓히고 점수·순위에는 섞이지
않는다(벡터 arm 은 원본 질의만 임베딩한다).

모든 도구는 `DomainError`/`IntegrationError` 발생 시 스택트레이스 대신
`{"error": true, "code": ..., "message": ...}` 형태의 에러 페이로드를 반환한다
(응답 스키마는 `app/mcp/types.py` 참고).

<!-- /AUTO-GENERATED -->

### 제공되는 리소스 (Resources)

- `document://{document_id}/raw`: 문서 원문 보기

## 자동 동기화 (배치)

`refresh_index` 를 수동 호출하지 않도록 메타 캐시(+선택적 등록 문서 재색인)를
갱신하는 원샷 CLI 를 제공합니다. MCP stdio 서버는 세션마다 뜨는 단명 프로세스라
스케줄러를 품을 수 없으므로, 이 스크립트는 **한 번 돌고 종료**하고 주기는 OS
스케줄러(systemd timer 또는 cron)가 소유합니다
(설계: [`architect-review/31_refresh_index_batch_automation.md`](architect-review/31_refresh_index_batch_automation.md)).

```bash
uv run python -m app.scripts.refresh_documents \
  [--source drive|notion] [--project PROJECT] [--include-registered] [--force] [--index-bodies]
```

`--index-bodies` 를 뺀 인자는 `refresh_index` 도구와 동일한 의미입니다. 세 축을
다른 주기로 돌립니다:

- **축 A(메타 캐시 동기화)** — 문서 목록·제목·수정일만 갱신(본문 미조회).
  **1시간마다** 권장. 실측 1틱 **47초**(1시간 예산의 1.3%)라 여유가 큽니다. 단
  Drive 하위 폴더 BFS 순회로 호출 수가 폴더 수에 비례하니, 폴더 트리가 큰
  프로젝트는 1틱을 직접 재보고 주기를 늘리세요.
- **축 B(등록 문서 재색인, `--include-registered`)** — `source_url` 이 있는 문서마다
  원본을 재fetch·재파싱·재임베딩합니다. 변경이 없어도 fetch 비용이 들어 **1일
  1회(야간)** 만 돌립니다. `--force` 는 배치에서 쓰지 않습니다(해시 동일 시 skip 이
  정상 경로).

- **축 C(협업 문서 본문 색인, `--index-bodies`)** — 메타가 가리키는 문서의 본문을
  fetch 해 section 청크로 색인합니다. 기본 검색 전략(`indexed`)의 키워드·벡터 신호가
  여기서 채워지므로 **협업 문서 검색을 쓰면 최소 1회는 실행**해야 합니다(미색인
  문서도 제목 신호로는 계속 검색됩니다). 축 A 와 같은 실행에 얹는 플래그이며, 문서당
  본문 fetch + 임베딩이 들어가 축 A 단독보다 훨씬 비쌉니다 — 정기 실행이 필요하면
  **축 B와 같은 야간 슬롯**에 두세요. 커밋 경계는 메타 변경 건수만이 아니라 본문
  fetch 건수(`fetched_bodies`)까지 합산해 잡으므로, 메타가 하나도 안 바뀌는 소급
  색인에서도 100건마다 중간 커밋이 일어납니다(마지막 한 건이 깨져도 앞의 색인이
  롤백되지 않습니다).
  - 지금은 실패를 기억하지 않아, **텍스트를 못 뽑는 파일(이미지·영상 등)과 빈 본문
    문서는 실행할 때마다 다시 fetch** 됩니다. 1회성 백필에서는 무해하지만 정기
    실행으로 돌릴 때는 이 비용을 감안하세요
    (`architect-review/41_backfill_result_verification_and_indexed_default_gate.md` §3).

중복 실행은 Postgres advisory lock 으로 막습니다. 축 A·축 B는 락 키가 달라 축 B 실행
중에도 축 A 틱이 굶지 않습니다. 락 키는 `--include-registered` 여부로만 갈리므로
(`_select_lock_key`), `--index-bodies` 만 준 실행은 **축 A 락을 씁니다** — 오래 도는
본문 색인이 시간당 메타 틱을 굶기지 않게 하려면 `--include-registered` 와 함께(축 B
슬롯에서) 돌리세요.

### systemd user timer (권장)

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

축 B는 같은 형태로 `docs-resync.service`(`ExecStart=... --include-registered`)
+ `OnCalendar=daily` 타이머를 하나 더 둡니다. 축 C를 정기 실행하려면 이 서비스의
`ExecStart` 에 `--index-bodies` 를 덧붙입니다.

```bash
systemctl --user enable --now docs-refresh.timer docs-resync.timer
```

### cron (systemd 미가용 환경, 예: WSL2)

```cron
0 * * * * cd /home/<user>/projects/docs-mcp && /home/<user>/.local/bin/uv run python -m app.scripts.refresh_documents >> output/logs/refresh.log 2>&1
30 3 * * * cd /home/<user>/projects/docs-mcp && /home/<user>/.local/bin/uv run python -m app.scripts.refresh_documents --include-registered >> output/logs/resync.log 2>&1
```

### 실행 환경 함정 (크론/타이머가 손으로 돌릴 때와 다르게 깨지는 지점)

- **cwd** — 설정 로딩이 `.env` 를 cwd 기준 상향 탐색으로 찾습니다. cron의
  기본 cwd(홈)에서는 `.env` 를 못 찾고, Drive 서비스계정 파일 상대경로도
  같이 깨집니다. → `WorkingDirectory=`(systemd) / `cd <repo> &&`(cron)
  **필수**.
- **PATH** — cron 의 PATH 에는 `uv` 가 없습니다. **절대경로**로 씁니다.
- **자격증명** — `DOCS_MCP_NOTION_TOKEN`, Drive 서비스계정 파일이 배치를
  실행하는 사용자 권한으로 읽혀야 합니다(`secrets/` 권한 확인). 누락되면
  소스 전량이 실패해 exit code 1 로 드러납니다.
- **로그** — stderr에 JSON 한 줄을 남깁니다. systemd 면 journal이 받고,
  cron 이면 `output/logs/` 로 리다이렉트하세요(이미 `.gitignore` 대상).

### 종료코드

| 상황                                     | 종료코드 |
| ---------------------------------------- | -------- |
| 전 대상 실패(모든 소스 갱신 실패)        | 1        |
| 부분 실패/락 미획득(이미 실행 중)/정상   | 0        |

부분 실패를 1로 올리지 않는 이유는 실패한 항목이 다음 갱신에서 자동
재시도되기 때문입니다(WARN 로그에 실패한 `<project>/<source>` 가 남으므로
지속 실패는 로그로 추적됩니다).
