# `snippet_as_of` MCP 응답 노출 여부 판정

- 일시: 2026-08-15
- 작성: architect
- 선행: `docs/architect-review/35_drive_notion_embedding_migration_and_refresh_strategy.md` §Phase0-2,
  `docs/architect-review/37_document_search_phase3_rrf_verdict.md` §2.4,
  `docs/architect-review/41_backfill_result_verification_and_indexed_default_gate.md` §2
- 질문: `DocumentSearchItem.snippet_as_of` 가 서비스 DTO 에만 있고 MCP 응답
  (`app/mcp/payloads.py:119`)에는 실리지 않는다. 노출해야 하는가, 뺀 근거가 있는가.

---

## 판정 요약

**노출한다. 의도적 제외 근거는 없고, 구현 누락으로 판단한다.**

현행 상태에서는 doc35 이 "이번 변경에서 유일하게 깨지는 겉면 계약"이라고 못박은
사실(스니펫 출처가 라이브 원문 → 동기화 시점 캐시)이 **호출 LLM 에 전달되지 않는다.**
계약 변경을 명시하려고 만든 필드가 계약 경계를 못 넘고 서비스 내부에서 멈춰 있다.

## 1. 원 설계 의도 — 노출이 맞다

- doc35 §Phase0-2: "검색 스니펫의 출처가 캐시 본문으로 바뀐다 — '스니펫은 마지막
  동기화 시점, 원문은 최신'이라는 불일치가 새로 생기므로 `DocumentSearchItem` 에
  `last_synced_at`(또는 `snippet_as_of`)을 **노출해 계약을 명시**할 것을 권고한다.
  이게 이번 변경에서 유일하게 **깨지는 겉면 계약**이다."
  → "겉면 계약(외부에서 보이는 계약)"이라는 표현 자체가 수신자를 호출자로 지목한다.
  내부 DTO 필드는 겉면이 아니다.
- doc37 §2.4: 같은 취지로 `DocumentSearchItem` 에 필드 추가를 지시.
- doc41 §2("전환 조건과 롤백"): "겉면 계약 변경은 **이미 명시돼 있다**: score 스케일이
  RRF 로 …, 스니펫 출처가 동기화 시점 캐시가 될 수 있어 `snippet_as_of` 노출".
  기본값 `indexed` 전환 승인은 **이 전제 위에 서 있다.** 전제가 실제로는 성립하지
  않으므로, 승인 근거를 사후에 맞추는 의미도 있다.

두 문서가 `DocumentSearchItem`(서비스 DTO)을 지목한 것은 사실이지만, 그건 그 DTO 가
MCP 응답으로 그대로 옮겨지는 얇은 변환 계층(`_to_document_search_payload`)을 전제로
쓴 표현이다. 실제로 `version` 은 같은 방식으로 DTO → 페이로드까지 옮겨졌고
`snippet_as_of` 만 빠졌다.

## 2. 의도적 제외 근거는 없다

`snippet_as_of` 를 언급한 문서는 doc35/39/43 셋뿐이고 **셋 다 노출 방향**이다.
"페이로드에는 싣지 않는다"는 결정을 내린 문서도, 코드 주석도 없다.
(doc37 §2.5 가 명시적으로 뺀 필드는 `match_type` 이며, 그건 "문서 검색 계약에 없던
필드라 추가하지 않는다"고 이유까지 남겼다 — 뺄 때는 이렇게 남는다.)

`DocumentSearchItem.snippet_as_of` 는 단위 테스트도 있다
(`tests/unit/test_document_search_service.py:1138`, `:1183` — 청크 스니펫이면 값이 있고
title-only 매치면 None). 즉 서비스 계층까지는 의도대로 구현됐고, 경계 변환 한 줄이
누락된 형태다.

## 3. 노출 형태

| 항목 | 결정 |
|---|---|
| 타입 | `str \| None`(ISO8601 문자열). 기존 규약과 동일 — `indexed_at`/`created_at`/`updated_at` 모두 `.isoformat()` 문자열로 내보낸다(`app/mcp/tools/documents.py:53`, `app/mcp/payloads.py:164`). |
| None 의미 | 유지한다. `fetch` 전략(라이브 fetch)과 title arm 단독 매치는 캐시 발췌가 아니므로 `None` — "이 스니펫에는 기준 시각 개념이 없다"는 뜻이다. |
| 이름 | `snippet_as_of` 유지. `last_synced_at` 으로 내보내면 "메타 동기화 시각"으로 읽혀 **어느 것의 시각인지**가 흐려진다. 계약의 핵심은 "이 **스니펫**이 언제 기준인가"다. |
| 위치 | 결과 항목(item) 단위. 문서마다 마지막 동기화 시각이 다르므로 응답 최상위로 올리면 안 된다. |

## 4. developer 변경 범위

1. `app/mcp/types.py` — `DocumentSearchItemPayload` 에 `snippet_as_of: str | None` 추가.
   같은 클래스 docstring 이 낡았다: "score 는 제목 매칭(1단계)과 본문 매칭(2단계)을
   합산한 0.0~1.0 값이다" → 기본 전략에서 score 는 **RRF 점수(0.0x 스케일, 절대값 비교
   불가·순서만 유의미)** 이고 `fetch` 전략에서만 0.0~1.0 가중합이다. 함께 고친다.
2. `app/mcp/payloads.py:119` `_to_document_search_payload` — 매핑 한 줄 추가:
   `"snippet_as_of": item.snippet_as_of.isoformat() if item.snippet_as_of else None`.
3. `app/mcp/tools/documents.py` — `search_documents` docstring 의 Returns 필드 목록에
   `snippet_as_of` 추가하고, **본문 설명이 낡은 것도 같이 고친다**: 현재 "2단계로
   동작한다. 먼저 메타 캐시의 제목으로 후보를 추리고, 그 후보 본문만 원본 API 에서
   실시간으로 가져와…"는 `fetch` 전략 서술이다. 기본은 제목+키워드+벡터 3-arm RRF 이고
   검색 경로에서 외부 API 를 부르지 않는다. `query_variants` 설명의 "1단계 후보 필터"도
   "제목·키워드 arm 의 후보 필터(벡터 arm 은 원본 질의만 임베딩)"로 정정.
4. `tests/integration/test_mcp_documents.py:329`
   `test_search_documents_returns_expected_fields` — 필드 집합을 **정확히** 비교하는
   `assert set(item) == {...}` 라 반드시 함께 고쳐야 한다(7개 → 8개). 값 검증 케이스를
   하나 더 붙이면 좋다: `indexed` 전략에서 청크 스니펫이면 문자열, title-only 매치면
   `None`.
5. 검증: `uv run pytest tests/unit/test_document_search_service.py tests/integration/test_mcp_documents.py`.

**함께 고칠 것(같은 반경).** `app/mcp/tools/endpoints.py` 의 `search_endpoints` docstring
도 "키워드 우선, 0건일 때만 벡터 보조"로 낡았다(기본은 `rrf` 융합). `docs/operations.md`
"제공되는 도구 전체 목록"은 이 docstring 들에서 생성되는 AUTO-GENERATED 블록이고 문서
쪽은 이미 실제 동작으로 고쳐 뒀으므로, docstring 을 맞추지 않으면 다음 재생성에서
문서가 도로 낡은 서술로 되돌아간다.

## 5. 스코프 밖

`last_synced_at` 컬럼은 `DateTime`(naive)이라 `.isoformat()` 결과에 UTC 오프셋이 붙지
않는다. 다만 이건 `indexed_at`/`created_at`/`updated_at` 등 기존 노출 필드 전부가 똑같이
갖고 있는 프로젝트 전역 규약이므로, 이번 건에서 혼자 tz-aware 로 바꾸면 오히려
불일치가 생긴다. 바꾸려면 노출 시각 필드 전체를 한 번에 다루는 별건으로 연다.
