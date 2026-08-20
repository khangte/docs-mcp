# 51. 프로젝트 구조 · git 추적 상태 전반 점검

- 작성: architect
- 일자: 2026-08-20
- 기준 커밋: `ea9e8d4`
- 범위: 디렉터리 구조, git 추적/무시 상태, 자격증명 노출, 문서-코드 정합성, 빌드/의존성 설정

## 1. 요약

워킹트리는 클린하고(수정·미추적 파일 0건), 자격증명은 현재 트리와 전체 커밋 히스토리 어디에도 올라가 있지 않다.
구조상 치명적 결함은 없다. 다만 재현성·문서 정합성 측면에서 조치가 필요한 항목 4건, 정리 권고 2건을 확인했다.

## 2. 현황

### 2.1 git 추적 상태

- `git status --porcelain -uall` 결과 0건. 미추적 파일도 없다.
- 추적 파일 분포: `app/` 88, `docs/` 85, `tests/` 73, `alembic/` 17, 루트 설정 6.
- 무시되는 항목: 캐시류(`.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `__pycache__`, `.coverage`),
  가상환경(`.venv`), 빌드 산출물(`docs_mcp.egg-info`), 에이전트 워크스페이스(`.claude/`, `.claude-logs/`,
  `.team/`, `team/`, `harness/`, `docs/superpowers/`, `.serena`), 로컬 설정(`.env`, `.ports`, `CLAUDE.md`),
  자격증명(`secrets/`).

### 2.2 자격증명 노출 점검

- 로컬에 `secrets/my-project-1559283220378-789fee435d88.json`(GCP 서비스 계정 키)와 `.env`가 존재하나
  둘 다 `.gitignore`로 차단되어 있다.
- 히스토리 전수 점검(`git log --all --diff-filter=A --name-only`)에서 `secret`/`.env`/`credential`/`.pem`/
  `.key` 패턴으로 추가된 파일 없음. **과거 커밋 누출도 없다.**
- 추적 중인 `.json`은 전부 `tests/fixtures/` 하위 평가용 코퍼스로, 민감정보 아님.

### 2.3 마이그레이션 체인

- `alembic/versions/` 14개, 루트 1개(`dfbe6143212a`), head 1개(`8a8db5f9c592`). 분기·다중 head 없음.

## 3. 조치 필요 항목

### 3.1 [높음] [완료 2026-08-20] `uv.lock`이 gitignore로 빠져 있다

`.gitignore` 의 `uv.lock` 라인 때문에 로컬에 존재하는 632KB 잠금 파일이 커밋되지 않는다.
이 저장소는 라이브러리가 아니라 실행형 애플리케이션(`[project.scripts] docs-mcp`)이고,
`torch`를 CPU 전용 인덱스로 고정(`[tool.uv.sources]`)하는 등 해석 결과가 환경에 따라 갈릴 여지가 크다.
잠금 파일이 없으면 팀원·배포 환경마다 다른 의존성 트리가 만들어지고, 임베딩 모델을 쓰는 특성상
`torch`/`sentence-transformers` 버전 차이는 검색 결과 재현성에 직접 영향을 준다.

권고: `.gitignore`에서 `uv.lock` 제거 후 커밋.

### 3.2 [중간] [완료 2026-08-20] `.env.example`에 `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY`가 없다

`app/core/config.py:45`가 읽는 `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY`(기본 `indexed`, 롤백값 `fetch`)가
`.env.example`에 누락되어 있다. `.env.example`의 나머지 12개 환경변수는 모두 문서화되어 있어
이 항목만 비대칭이다. 이 값은 `search_documents` 회귀 시 즉시 되돌리는 롤백 스위치이므로
운영자가 존재를 몰라서는 안 된다.

권고: `.env.example`의 "튜닝" 섹션에 `DOCS_MCP_SEARCH_STRATEGY` 바로 아래로 추가.

### 3.3 [중간] HTML 아키텍처 문서 4종이 Notion 재귀 색인을 반영하지 않았다

`ea9e8d4`는 `ARCHITECTURE.md`와 `README.md`만 갱신했고, `docs/` 하위 HTML 문서 4종은
마지막 갱신이 2026-08-15~17에 멈춰 있다. 그 이후 `app/`에 10개 커밋이 들어갔고,
그중 `cb7f022`(Notion 하위 데이터베이스·하위 페이지·본문 텍스트 색인 보강)는 기능 추가다.

| 파일 | 마지막 갱신 | Notion 재귀 색인 언급 |
|---|---|---|
| `docs/architecture_detailed.html` | 2026-08-15 | 없음 |
| `docs/archive/architecture-overview.html` | 2026-08-15 | 없음 |
| `docs/archive/architecture-presentation-diagram.html` | 2026-08-17 | 없음 |
| `docs/archive/portfolio-architecture.html` | 2026-08-15 | 없음 |

경로는 4.1 조치 이후 **현재 위치** 기준이다 — 점검 시점에는 넷 다 `docs/` 루트에 있었고,
아래 3종은 그 조치로 `docs/archive/`로 옮겨졌다(하이픈 이름 유지, 4.1 참조).

`refresh_lock`은 `architecture_detailed.html`·`archive/portfolio-architecture.html`에 반영돼 있어,
누락은 Notion 재귀 색인 건에 한정된다.

권고: 4종 중 실제로 유지할 문서를 먼저 정하고(4.1 참조), 유지 대상에만 Notion 재귀 색인 경로를 반영.

**조치 완료 (2026-08-20).** 4.1 판정에 따라 `docs/architecture_detailed.html` 한 곳에만 반영했다.

- §3 MCP 표면: `refresh_index` 설명에 본문 색인(`index_bodies` 기본 `True`) 추가 — `cb7f022`가 기본값을
  `False`에서 `True`로 바꿨는데 표에는 "메타 캐시 동기화"만 적혀 있었다.
- §7 외부 통합: Notion 행의 "가져오는 것"을 `child_page`/`child_database` 재귀 탐색으로, "파싱"을
  헤딩·리스트 마커 복원까지 포함하도록 수정.
- §7 말미: 재귀 색인 설계를 카드 3장으로 추가 — (01) 목록화는 하위 트리를 독립 문서로 평탄화하며
  `MAX_PAGE_DEPTH = 4` · `MAX_CONTAINER_DEPTH = 3` · `MAX_PAGES = 500`으로 끊는다, (02) 본문 재귀는
  `child_page`에서 멈춰 부모·자식 중복 색인을 막는다, (03) DB 행 속성은 블록에 없어 `GET /pages/{id}`를
  한 번 더 호출해 붙인다. 마커 복원이 없으면 모든 청크 앵커가 `# 개요`로 뭉개지는 이유도 함께 적었다.

아카이브 대상 3종은 갱신하지 않았다 — 보존이 목적이므로 시점 기록으로 남기는 편이 맞다.

### 3.4 [낮음] [완료 2026-08-20] `docs/architect-review/` 파일명 규칙이 중간에 바뀌었다

`01-app-layout-refactor.md` ~ `31-refresh-index-batch-automation.md`는 하이픈,
`32_notion_page_id_legacy_slot_seed.md` ~ `50_refresh_lock_abort_asymmetry_verdict.md`는 언더스코어를 쓴다.
`CLAUDE.md`의 프로젝트 규칙은 파일·폴더에 `snake_case`를 요구하므로 32번 이후가 규칙에 맞고
01~31이 어긋난 상태다.

권고: 일괄 리네임은 문서 상호 참조 링크를 깨뜨리므로, 리네임을 한다면 링크 갱신과 함께 한 커밋으로 처리한다.
우선순위는 낮으니 다른 문서 작업과 묶어 처리해도 무방하다.

**조치 완료 (2026-08-20).** 통일 범위가 `docs/architect-review/`에 그치지 않고 `docs/` 전반으로
넓어졌다. 두 묶음으로 처리했다.

1. `docs/architect-review/` 01~31번 31개 파일을 `git mv`로 언더스코어 규칙으로 통일
   (예: `01-app-layout-refactor.md` → `01_app_layout_refactor.md`).
2. 같은 규칙을 어기고 있던 `docs/` 하위 나머지 11개 파일도 함께 리네임 — `docs/adr/` 4개,
   `docs/exec_plans/` 4개(`eval_set_expansion_plan.md`, `refresh_index_api_document_resync.md`,
   `search_p4_p5_p6_plan.md`, `search_p4_rrf_k_sweep_plan.md`), `docs/search_flow.md`,
   `docs/implementation_journey.md`, `docs/architecture_detailed.html`.

이를 참조하던 `README.md`, `ARCHITECTURE.md`, `docs/operations.md`, `docs/adr/README.md`,
`docs/architect-review/` 내부 상호 참조 문서들의 경로 문자열을 전수 치환했다. 문서 경로를 주석·
docstring에 담고 있던 `app/services/search/keyword_search.py`와 테스트 2개도 같은 치환 대상이었다.

**최종 스윕 (2026-08-20).** 위 치환 이후 `docs/` 전체를 다시 훑어 남은 경로 인용을 정리했다.

- `docs/implementation_journey.md`의 글롭 표기 `NN-*.md`를 `NN_*.md`로.
- 삭제·이동된 문서를 가리키던 참조를 현재 위치로 — `docs/search-rrf-reevaluation.md`(exec_plans
  SPEC.md)는 `docs/architect-review/07_search_rrf_reevaluation.md`로, `docs/product_specs/plan.md`
  (20번)는 `docs/archive/plan.md`로.
- 4.1에서 아카이브로 옮긴 HTML을 가리키던 44·45·46·48·49번의 인용 경로에 `archive/`를 붙였다.
  앞서 "시점 기록이라 고치지 않는다"고 적었던 판단을 뒤집은 것이다 — 문서가 삭제된 것이 아니라
  자리만 옮겼고, 인용이 실재하는 파일을 가리키지 못하면 기록으로서의 값도 없다.
- 저장소에 더는 없는 문서 참조 2종은 경로를 고치는 대신 그 사실을 본문에 적었다.
  `docs/supabase-migration-review.md`(6·8번)는 커밋 `52636bd`에서 삭제됐고,
  `docs/portfolio-components.html`(44번)은 커밋된 적이 없는 작업 중 로컬 파일이었다.

**남은 것 — 코드·테스트 30군데.** 스윕이 `docs/` 안쪽만 덮었다. 주석·docstring에 문서 경로를
담고 있는 `app/`·`tests/`·`scripts/` 파일 30군데가 아직 옛 경로를 가리킨다. 그중 5건은 하이픈
문제가 아니라 **번호가 틀린 것**이라(커밋 `be3e545`의 56→50 재정렬을 주석이 따라가지 않았다)
단순 치환으로는 못 고친다 — 특히 `32-refresh-index-batch-automation.md`는 지금
`32_notion_page_id_legacy_slot_seed.md`라는 다른 주제 문서다. 파일·줄 단위 목록과 치환 대상은
`.team/_runtime/doc_path_refs_todo.md`에 정리해 developer에 전달했다.

`docs/archive/`의 파일 **이름**은 바꾸지 않았다(4.1 참조). 다만 아카이브 문서가 **밖으로 거는 링크**는
대상 파일이 실제로 리네임됐으므로 함께 고쳤다 — `archive/plan.md`의 `docs/search_flow.md` 참조 1건,
`archive/architecture-overview.html`의 상세 문서 링크 1건. 후자는 파일명 치환만으로는 부족했다.
아카이브로 내려가면서 `./architecture_detailed.html`이 `docs/archive/` 기준으로 해석돼 깨졌으므로
`../architecture_detailed.html`로 보정했다. 이름 보존과 링크 유효성은 별개다.

## 4. 정리 권고

### 4.1 아키텍처 HTML 문서 4종의 역할 중복

점검 시점에 `docs/` 루트에 함께 있던 `architecture_detailed.html`(878줄),
`architecture-overview.html`(407줄), `architecture-presentation-diagram.html`(417줄),
`portfolio-architecture.html`(429줄) 네 개가
같은 시스템을 서로 다른 상세도로 설명한다. 총 2131줄이며 코드가 바뀔 때마다 네 곳을 동기화해야 한다.
3.3의 누락도 이 구조에서 나온 결과다.

권고: 발표·포트폴리오 목적이 끝난 문서는 `docs/archive/`로 옮기고, 살아있는 참조 문서만 `docs/` 루트에 남긴다.

**조치 완료 (2026-08-20).** `architecture_detailed.html` 한 개만 살아있는 참조로 남기고 나머지 3종을
`docs/archive/`로 옮겼다(`git mv`).

| 파일 | 판정 | 근거 |
|---|---|---|
| `docs/architecture_detailed.html` | 유지 | 8개 섹션으로 MCP 표면 16 tools·검색 파이프라인·저장/색인·외부 통합을 모두 덮는 유일한 전체 문서 |
| `docs/archive/architecture-overview.html` | 아카이브 | 서사형 요약. `README.md`의 "검색 아키텍처(요약)"와 detailed §1이 같은 내용을 이미 덮는다 |
| `docs/archive/portfolio-architecture.html` | 아카이브 | 포트폴리오용. overview와 설계 결정 3가지·기술 스택이 거의 그대로 겹친다 |
| `docs/archive/architecture-presentation-diagram.html` | 아카이브 | 발표용 단일 다이어그램. 일회성 목적이 끝났다 |

두 계층(요약 + 상세)을 유지하는 선택지도 있었으나, 그것이 바로 3.3의 드리프트를 만든 구조다.
입문용 서사는 이미 `README.md`가 맡고 있어 요약본 HTML을 따로 살려 둘 이유가 없다.

링크 정리: `architecture_detailed.html`의 헤더 내비게이션이 `./architecture-overview.html`을 가리키고
있어 `README.md`와 `docs/archive/`를 가리키도록 고쳤다. 나머지 인바운드 참조는
`docs/architect-review/` 44·45·46·48·49번의 본문 인용으로, 그 시점의 판단 기록이므로 경로를
고쳐 쓰지 않았다.

**아카이브 3종 파일명 (2026-08-20 사용자 결정).** 3.4에서 정리한 `snake_case` 통일은 `docs/` 하위
살아있는 문서에만 적용하고, `docs/archive/`로 옮긴 3종은 `architecture-overview.html` ·
`portfolio-architecture.html` · `architecture-presentation-diagram.html` 하이픈 이름을
**그대로 유지한다**. 아카이브는 그 시점 산출물을 손대지 않고 보존하는 것이 목적이고, 이름을 바꾸면
44·45·46·48·49번이 인용한 경로가 가리키는 대상과 어긋난다.

### 4.2 CI 설정 부재

`.github/` 가 없어 `ruff`·`pytest`가 로컬 실행에만 의존한다. `pyproject.toml`에 `ruff`/`mypy`가
dev 그룹으로 등록되어 있고 테스트 73개가 있으므로 워크플로 한 개로 게이트를 걸 수 있다.
도입 여부는 이 저장소를 어디까지 공개·협업할지에 달린 문제이므로 lead 판단 사항으로 남긴다.

## 5. 조치 대상이 아닌 것 (확인 후 정상 판정)

- `output/.gitkeep`, `output/logs/.gitkeep` — 런타임 산출물 디렉터리 유지용, 의도된 추적.
- `CLAUDE.md`, `harness/`, `team/`, `.team/`, `docs/superpowers/` 무시 — 에이전트 로컬 워크스페이스로 의도된 배제.
- `ARCHITECTURE.md`가 참조하는 `app/**/*.py` 경로 전부 실재. 경로 드리프트 없음.
- `docs/architect-review/` 3개 문서(17·30·47)가 무시 경로(`docs/superpowers` 등)를 언급하나,
  설계 판단 근거로서의 인용이며 깨진 링크가 아니다.
