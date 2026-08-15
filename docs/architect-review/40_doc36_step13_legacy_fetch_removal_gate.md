# doc36 13번(구경로 제거) 게이트 판정 — 지금은 반려, 선행 작업 지정

- 일시: 2026-08-15
- 작성: architect
- 선행: `docs/architect-review/36_drive_notion_embedding_migration_and_refresh_strategy.md` §1 Phase 3-13,
  `docs/architect-review/39_document_search_phase3_rrf_verdict.md`
- 질문: doc36 13번(`_body_fetch_budget`·`MAX_CONCURRENT_BODY_FETCHES` 등 2단계 예산 장치 삭제)을
  지금 진행해도 되는가.

---

## 결론

**반려(보류).** doc36 13번의 전제인 "전환 완료"가 충족되지 않았다. 지금 구경로를 지우면
협업 문서 검색이 제목 매칭 전용으로 퇴행한다.

---

## 1. 게이트 조건 실측

로컬 개발 DB(`docs-mcp-postgres-1`, 스키마 `app`) 기준:

| 확인 항목 | 실측값 |
|---|---|
| `app.document_meta` 행 수 | drive 164 / notion 110 (합 274) |
| `document_meta.document_id` 가 채워진 행 | **0** |
| `app.document` 행 수 | **0** |
| `app.chunk` 행 수 | **0** (`section` 청크 0건) |
| `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY` 기본값 | `"fetch"` (`app/core/config.py:45`) |

즉 **본문 색인이 한 건도 수행된 적이 없다.** Phase 1+2(색인 파이프라인)와 Phase 3(RRF 검색
경로)는 코드로만 존재하고, 데이터는 전부 미색인 상태다.

## 2. 지금 구경로를 지우면 생기는 회귀 (질문 1)

`_search_indexed` 는 `has_endpoint_chunks(project, chunk_type="section")` 가 False 면
keyword/vector arm 을 통째로 건너뛴다(`document_search_service.py:503`). 청크가 0건이므로
현재 상태에서 `indexed` 로 전환하면 **title arm 단독**으로만 융합이 돌아간다.

결과적으로 잃는 것:

- **본문에만 걸리는 매치 전량.** 제목/URL 토큰에 안 걸리는 문서는 후보 자체에 못 들어온다.
  doc36 §1-7·doc10 이 "제목엔 안 걸리고 본문에만 강하게 걸리는 문서"를 구제하려고
  `BODY_FETCH_OVERSCAN` 예산을 만든 그 케이스가 정확히 사라진다.
- **본문 기반 스니펫.** 승자 청크가 없으므로 전 결과가 `_fallback_snippet`(제목/URL 기반)로
  떨어진다.
- **결합 점수.** `TITLE_SCORE_WEIGHT*title + BODY_SCORE_WEIGHT*body` 의 body 항이 통째로 0.

fetch 경로를 남기고 플래그만 `fetch` 로 두는 한 회귀는 없지만, 그 상태에서 예산 장치를
지우는 것은 불가능하다(그 경로가 쓰는 코드다). **따라서 삭제는 색인 백필 완료 이후로 미룬다.**

## 3. 백필을 막고 있는 진짜 병목

`refresh_index(index_bodies=True)` 는 **MCP 도구 경로에만** 노출돼 있다
(`app/mcp/tools/sources.py:33`). 배치 스크립트 `app/scripts/refresh_documents.py` 는
`--source/--project/--include-registered/--force` 4종만 파싱하고
`refresh(source=..., project=...)` 만 호출한다 — `index_bodies` 를 넘기는 경로가 없다
(`refresh_documents.py:56-59`, `:85-87`).

274건 백필을 MCP 도구 한 번의 호출로 돌리는 것은 부적절하다(장시간 실행, 외부 API rate limit,
부분 실패 시 재개 불가). **배치 CLI 에 플래그를 노출하는 것이 게이트 충족의 선행 조건이다.**

## 4. 플래그 처리 방향 (질문 2)

3단계로 나눈다. 지금은 (a)만 한다.

- **(a) 현재 — 유지.** `document_search_strategy` 플래그·`fetch` 기본값·fetch 브랜치 전부 유지.
  백필 수단(§3)부터 만든다.
- **(b) 백필 완료 후 — 기본값만 뒤집는다.** `DOCS_MCP_DOCUMENT_SEARCH_STRATEGY` 기본값을
  `"indexed"` 로. 코드 삭제는 없다. 문제가 나면 환경변수 한 줄로 되돌린다(doc39 §2.7 의
  degrade 규약이 그대로 살아 있어야 하는 구간).
- **(c) 안정화 확인 후 — 플래그와 fetch 브랜치를 함께 걷어낸다.** 플래그만 남기고 브랜치를
  죽이는 중간 상태는 만들지 않는다 — 값이 하나뿐인 설정은 doc18 이 지적한 죽은 유연성이고,
  롤백 못 하는 플래그는 안전장치처럼 보이는 거짓 신호다. 지울 거면 같이 지운다.

## 5. 삭제 범위 확정 (질문 3) — (c) 시점에 적용

`get_document` 의 실시간 fetch 계약은 doc36 §0-2 확정대로 **유지**한다. 그 경로가 쓰는 것은
`DocumentSource.fetch()` 어댑터 메서드와 `_require_source`/`_find_meta_row` 뿐이며,
아래 검색 전용 장치와 겹치지 않는다.

**삭제 대상(검색 2단계 전용):**

- `_body_fetch_budget`, `BODY_FETCH_OVERSCAN`, `MAX_BODY_FETCH_CANDIDATES`
- `MAX_CONCURRENT_BODY_FETCHES`, `ThreadPoolExecutor` 임포트
- `_rank_with_body`, `_fetch_and_score`, `_select_candidates`
- `TITLE_SCORE_WEIGHT`, `BODY_SCORE_WEIGHT`
- `search_scorer._body_score` (다른 호출처 없음 — 문서 문자열 언급 2곳뿐)
- `document_search_strategy` 인자·`DOCUMENT_SEARCH_STRATEGY_INDEXED` 상수·config 필드·
  composition 배선
- 대응 테스트: `test_body_fetch_budget_*`, 동시성 상한 테스트,
  `test_unrecognized_document_search_strategy_degrades_to_fetch`,
  `tests/integration/test_mcp_documents.py` 의 `_body_fetch_budget` 단언

**존치 대상:**

- `DocumentSource.fetch()` 및 `get_document` 전 경로 (doc36 §0-2)
- `_title_score` (title arm 이 계속 쓴다), `_build_snippet`/`_fallback_snippet`
- `documents_tokenize`, `parse_version`

**문서 갱신:** `docs/search-flow.md` §5·§2(2단계 fetch 서술), 모듈 docstring 상단 2단계 설명.

## 6. 지금 실행할 것

1. **developer**: `refresh_documents.py` 에 `--index-bodies` 플래그 추가 →
   `refresh(source=..., project=..., index_bodies=args.index_bodies)` 로 전달. 기본 False
   (`include_registered` 와 동형 — 비용 큰 경로는 기본 off, doc36 §1-6). 단위 테스트 1건.
2. (1) 배포 후 운영에서 274건 백필 실행 → `app.chunk` 의 `section` 건수와
   `document_meta.document_id` 채워진 비율로 색인률 확인.
3. 색인률이 목표치에 닿으면 §4(b) 기본값 전환 → 검색 품질 확인 → §4(c) 삭제 착수.

doc36 13번은 3번 단계가 끝날 때까지 열어 둔다.
