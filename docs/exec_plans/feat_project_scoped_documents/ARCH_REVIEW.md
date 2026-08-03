# SPEC 설계 감수 보고서 (ARCH_REVIEW)

- 대상: `docs/exec_plans/feat_project_scoped_documents/SPEC.md`
- 성격: 구현 착수 전 **설계 관점 감수**(코드 수정 없음). 실제 파일을 열어 SPEC 변경지점 표(31~44행) 및 의존성 순서(462~473행)와 대조.
- 결론: 의존성 순서는 논리적으로 타당하며, 특히 **기능 5 → 기능 6 전제 관계는 실코드로 확인됨**. 다만 착수 전 해소할 리스크 4건과 SPEC 문면 정정 2건이 있음. SPEC 본문 반영 여부는 lead 판단.

---

## R1 (HIGH · 누락된 파급 지점) — config seed 를 넣을 자리가 SPEC 이 지목한 곳에 없다

**근거**
- SPEC 371행: 프로젝트 seed(`DOCS_MCP_DRIVE_FOLDER_ID`/`DOCS_MCP_NOTION_DATABASE_ID` 설정 시 `(DEFAULT_PROJECT, 값)` 을 없을 때만 삽입)를 "`bootstrap_app_state()` 에서 1회" 수행하라고 지시.
- 실제 `app/bootstrap.py:15-26` 은 engine 생성 + `create_all(engine)` + `AppState.from_engine(...)` 만 수행. **DB 세션도 ProjectSource 저장소도 없다.** seed 는 세션 + repo 가 필요하므로 현 구조에 그대로 넣을 수 없다.
- 더 근본적 문제: AppState 생성 경로가 **3개**로 갈라져 있다.
  1. `bootstrap_app_state()` — `app/mcp_server.py:535`, `app/main.py:93` 이 사용.
  2. `app/main.py:82-91` 커스텀 fetcher 주입 경로 — `bootstrap` 을 **우회**하고 `create_db_engine` + `create_all` + `AppState.from_engine` 을 직접 호출.
  3. `AppState.from_engine()` — 위 두 경로가 공통으로 도달하는 최하위 지점.

**영향**
- seed 를 `bootstrap_app_state` 에만 넣으면 `main.py` 커스텀 fetcher 경로(주로 테스트)에서 seed 가 누락돼, "기존 `.env` 사용자가 아무것도 안 해도 이전과 동일하게 동작" 이라는 기능 5 검증기준(SPEC 380행)이 그 경로에서 깨진다.

**해소 방향**
- seed 위치를 세 경로가 공통으로 지나는 지점(예: `create_all` 직후, 세션을 여는 별도 `seed_default_sources(session_factory, settings)` 함수)으로 재지정하고, `bootstrap_app_state` 와 main.py 우회 경로 양쪽에서 호출.
- SPEC 371행의 "`bootstrap_app_state()` 에서" 문면을 위 공용 지점으로 정정 필요.

---

## R2 (교차 프로젝트 삭제 — SPEC 이 이미 정확히 짚음, 실코드로 확인됨)

**근거**
- `app/services/documents/document_index_service.py:186` `existing = {m.external_id: m for m in self._meta_repo.list_by_source(source_name)}`
- 동 파일 `:201-203` 삭제 감지 루프: `for external_id, row in existing.items(): if external_id not in seen: self._meta_repo.delete(row)`
- `refresh` 가 여러 프로젝트를 순회하게 되면 모든 프로젝트가 같은 `source_name="drive"`(또는 `"notion"`)를 공유하므로, 프로젝트 A refresh 시 B 프로젝트 행이 `not in seen` 으로 판정돼 **삭제된다.**

**평가**
- SPEC 이 이 지점을 이미 정확히 인지(기능 6, `list_by_project_source` 신설 — SPEC 393·398행, 검증기준 417행 "교차 프로젝트 삭제 방지 — 가장 깨지기 쉬운 지점"). 해법 방향 적절.

**추가 지적**
- `_refresh_source` 뿐 아니라 **`_resolve_targets`(`document_index_service.py:156-165`)의 `source_name` 단일 매칭 로직도 `(project, source)` 쌍 순회로 전면 교체**돼야 한다. SPEC 398행이 `resolver.resolve_all()` 순회를 언급하나 교체 대상 메서드명(`_resolve_targets`)을 명시하지 않아 구현 시 놓칠 여지가 있음.

---

## R3 (MEDIUM · 하위호환 마이그레이션 순서 — 논리적 허점 없음)

**근거·평가**
- UNIQUE 교체 순서(SPEC 177~182):
  1. `project` 컬럼 `server_default='default'` 추가 → 2. 기존 `uq_document_meta_source_external` DROP → 3. 새 `uq_document_meta_project_source_external` `(project, source, external_id)` 생성.
- 이 순서는 안전하다. `server_default='default'` 백필로 기존 행이 전부 `project='default'` 가 되므로 `(default, source, external_id)` 유일성이 유지돼 새 제약 생성이 실패하지 않는다. DROP 을 ADD 보다 먼저 하지 않으므로 중복 방지 창도 없다. downgrade 역순도 무결.
- Postgres 11+ 에서 `server_default` 있는 NOT NULL 컬럼 추가는 테이블 재작성 없이 즉시 완료되므로 3단계 백필 생략도 타당.

**주의**
- `document_meta.source` 는 현재 `String(16)`(`app/models/document_meta.py:49`), 신규 `project` 는 `String(128)`. **마이그레이션의 컬럼 길이가 모델 정의와 어긋나지 않도록** `sa.String(128)` 명시 확인 필요.

---

## R4 (LOW · search_service get_document 어댑터 선택 폴백 경로)

**근거**
- `app/services/documents/document_search_service.py:203` `document_source = self._sources.get(row.source)` 가 resolver 방식(`resolver.resolve_for_project(row.project)[row.source]`)으로 바뀔 때, SPEC 406행의 "메타에 없으면 `DEFAULT_PROJECT` 의 해당 source 어댑터로 폴백" 은 resolver 에 `DEFAULT_PROJECT` 매핑조차 없으면 여전히 `None` 이 되어 기존 `_LOG.warning` 후 skip 경로(`:204-206`)로 떨어진다.

**평가·해소 방향**
- 계약상 오류는 아니나(조용한 skip 은 기존 정책과 일관), 이 폴백-실패 케이스를 검증 테스트에 포함해 "폴백도 실패하면 skip" 이 의도된 동작임을 고정하길 권장.

---

## SPEC 문면 정정 (블로커 아님)

**C1 — `DEFAULT_PROJECT` / `PROJECT_MAX_LENGTH` 는 이미 존재한다.**
- SPEC 162·222행은 이 상수들을 "새로 정의" 하라고 서술하나, 실제로 `app/models/openapi.py:33` `DEFAULT_PROJECT = "default"`, `:35` `PROJECT_MAX_LENGTH = 128` 이 **이미 존재**한다. 신규 추가가 아니라 재사용이며, 구현자가 중복 정의하지 않도록 문면 정정 필요.

**C2 — 변경지점 표(31~44행) 대조 결과: 파일 경로·현상 모두 정확.**
- 특히 `sync_service.register()` 호출부는 SPEC 27·263행 주장대로 `app/` 내부에 정확히 2곳뿐임을 grep 으로 확인:
  - `app/mcp_server.py:264`
  - `app/api/routes/documents.py:27`
  - 나머지 호출부는 전부 `tests/`. → FastAPI 라우트 최소 수정 범위 산정(SPEC 27·262행)이 정확.
- 그 외 표의 10개 지점(모델/저장소/서비스/dependencies/source_factory/mcp_server) 파일·라인 현상 모두 실코드와 일치.

---

## 종합

- 의존성 순서(1→2→3→4→5→6→7→8) 타당. 3번까지 완료 시 "OpenAPI 문서 격리" 단독으로 의미 있는 중간 검증 지점이 된다는 SPEC 473행 판단도 동의.
- **착수 전 반드시 해소**: R1(seed 위치 재지정 + main.py 우회 경로 포함), R2 추가지적(`_resolve_targets` 교체 명시).
- **구현 중 유의**: R3 컬럼 길이, R4 폴백-실패 테스트, C1 중복 정의 방지.
- 코드는 감수 중 일절 수정하지 않음.
