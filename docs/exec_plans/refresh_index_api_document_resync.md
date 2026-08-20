# refresh_index 확장: URL 기반 ApiDocument 재동기화

## 1. 판단 결론

**타당하다. 확장한다.** 단, 두 파이프라인을 하나의 도구에 합치되 **응답을 병합하지 않고 네임스페이스를 분리**한다.

근거:

- `refresh_index`의 도메인 의미는 "search 계열 도구가 최신 문서를 잡도록 색인을 갱신한다"이다. `search_documents`(document_meta)와 `search_endpoints`(api_document)는 별개 파이프라인이지만, 클라이언트 관점에서 "새로 등록/수정한 게 검색에 안 잡힌다 → 갱신 도구를 부른다"는 **동일한 사용자 의도** 하나에 대응한다. 도구를 둘로 쪼개면 클라이언트가 "어느 갱신을 불러야 하나"를 매번 판단해야 한다.
- `SyncService.resync`가 이미 존재하고 HTTP `/sync/{document_id}`로 검증돼 있다. 신규 로직이 아니라 **기존 검증된 서비스를 MCP 표면에 노출**하는 작업이다.
- `resync`는 `source_url`이 없으면 `document.raw_text`로 재빌드한다(원본 재fetch 불가). 즉 raw_document 등록 문서는 "재동기화해도 무의미"하다는 사실을 서비스가 이미 안다. 우리는 그 대상을 **애초에 호출하지 않도록** 상위에서 필터만 하면 된다.

## 2. 파라미터 설계

기존 시그니처를 **하위호환으로 확장**한다. 기존 `source`/`project`는 그대로 두고, api_document 재동기화 스위치를 파라미터 하나로 추가한다.

```python
async def refresh_index(
    source: str | None = None,
    project: str | None = None,
    include_registered: bool = False,   # 신규
    force: bool = False,                # 신규
) -> RefreshIndexResult | ErrorPayload:
```

- **`include_registered`** (기본 `False`): URL 기반 ApiDocument 재동기화를 켜는 스위치.
  - **기본값을 False로 두는 게 핵심이다.** 기존 클라이언트는 document_meta만 갱신하는 값싼 호출을 기대한다. api_document resync는 문서마다 원본 URL을 재fetch + 재파싱 + 재색인하므로 비용이 크다. 옵트인이 아니면 기존 호출이 조용히 무거워진다.
- **`force`** (기본 `False`): `resync`의 `force`로 그대로 전달. 해시가 같아도 강제 재색인. `include_registered=False`면 무시된다.
- **`project`**: 두 파이프라인 공통 필터. document_meta는 지금처럼 프로젝트 소스로 좁히고, api_document는 `project`로 좁힌 문서 집합만 resync한다.
- **`source`**: document_meta 전용(drive/notion). api_document 선택에는 관여하지 않는다(api_document에는 drive/notion 개념이 없다). docstring에 명시한다.

### 대상 문서 지정 방식: **project 단위 자동 필터**

특정 `document_id` 파라미터는 **추가하지 않는다**. 단건 재동기화는 이미 HTTP `/sync/{document_id}`가 담당하고, MCP `refresh_index`의 의미는 "검색이 최신을 반영하도록 일괄 갱신"이다. 단건까지 넣으면 도구 의미가 흐려진다(YAGNI). 대상은 다음 규칙으로 자동 선정한다:

- `project`가 주어지면 그 프로젝트의 문서만, 없으면 전체 문서.
- **그중 `source_url IS NOT NULL`인 것만** (raw_document 등록 문서 자동 제외 — §4).

## 3. 응답 형식

두 파이프라인 결과를 **하나의 dict에 서로 다른 키로** 담는다. 병합(숫자 합산)하지 않는다 — added/updated의 의미 단위가 다르기 때문이다(document_meta는 "메타 행", api_document는 "문서").

기존 `RefreshIndexResult`의 top-level 키(`synced/added/updated/removed/failed_sources`)는 **document_meta 결과로 그대로 유지**해 하위호환을 지킨다. api_document 결과는 신규 키 `registered` 아래에 중첩한다.

```python
class RegisteredResyncResult(TypedDict):
    """include_registered=True 일 때 URL 기반 ApiDocument 재동기화 집계."""
    total: int          # 재동기화 대상(source_url 있는) 문서 수
    reindexed: int      # 해시가 바뀌어 재색인된 문서 수
    skipped: int        # 해시 동일로 건너뛴 문서 수
    failed: list[str]   # 실패한 document_id 목록(부분 실패 허용)


class RefreshIndexResult(TypedDict):
    # --- document_meta (기존, 그대로) ---
    synced: int
    added: int
    updated: int
    removed: int
    failed_sources: list[str]
    # --- api_document (신규, 선택) ---
    registered: NotRequired[RegisteredResyncResult]
```

- `include_registered=False`면 `registered` 키 자체가 없다(기존 응답과 바이트 동일).
- `include_registered=True`면 항상 `registered`가 있고, 대상이 0건이면 `{total:0, reindexed:0, skipped:0, failed:[]}`.

`RegistrationResult.status`는 `"reindexed" | "skipped"`(등록이 아니라 재동기화이므로 `"registered"`는 나오지 않는다)를 카운트에 매핑한다.

## 4. raw_document 문서 제외를 스키마/로직에 반영

**로직(필터)로 반영한다. 스키마 제약은 추가하지 않는다.**

- ApiDocument는 `source_url`이 nullable이다(raw_document 등록 시 NULL). 이 컬럼이 곧 "재동기화 가능 여부"의 자연 표현이다. 별도 boolean 플래그를 추가할 필요가 없다(YAGNI — 컬럼이 이미 사실을 담고 있다).
- `DocumentRepository`에 **필터 조회 메서드를 추가**한다:

  ```python
  def list_resyncable(self, project: str | None = None) -> Sequence[ApiDocument]:
      """source_url 이 있는(URL 기반) 문서만 반환한다. raw_document 등록 문서는 제외."""
      stmt = select(ApiDocument).where(ApiDocument.source_url.is_not(None))
      if project is not None:
          stmt = stmt.where(ApiDocument.project == project)
      return self._session.execute(stmt).scalars().all()
  ```

- **이중 안전장치**: 상위에서 이미 필터하지만, 만약 필터를 우회해 raw_document 문서가 resync에 들어와도 `resync`는 `document.raw_text`로 재빌드하고 해시가 동일해 `"skipped"`가 된다(무해). 즉 크래시하지 않는다. 그래도 원칙은 **상위 필터로 애초에 대상에서 뺀다** — 무의미한 재파싱 비용과 오해를 부르는 skip 카운트를 피하기 위함이다.

## 5. 에러 처리: 부분 실패 허용

document_meta 파이프라인은 이미 부분 실패를 허용한다(`failed_sources`). api_document도 **동일 원칙**을 따른다:

- 대상 문서를 순회하며 문서별로 `resync(doc.id, force=force)`를 **개별 try/except**로 감싼다.
- `resync`는 문서마다 자체 커밋한다 → 한 문서 실패가 다른 문서 결과를 롤백하지 않는다(트랜잭션 독립).
- 실패 시 해당 `document_id`를 `failed`에 담고 계속 진행. 예외는 로깅(`logging`, `print` 금지)하되 삼키지 않고 목록으로 노출한다.
- **document_meta 파이프라인과 api_document 파이프라인도 서로 독립**: 한쪽 전면 실패가 다른 쪽을 막지 않는다. 단, 순서는 document_meta 먼저(값싼 것 먼저) → api_document.
- **전면 실패 정책**: 기존 `refresh()`는 "대상 0 or 전량 실패 & 변경 0"이면 `IntegrationError`를 던진다. 이 동작은 유지한다. api_document 쪽은 대상이 0건이어도 정상 반환(빈 집계)한다 — "URL 기반 문서가 없음"은 오류가 아니다. api_document가 전량 실패하면 `failed` 목록으로만 알리고 예외로 승격하지 않는다(document_meta가 성공했을 수 있으므로 도구 전체를 실패시키면 안 된다).

## 6. 구현 지시 요약 (developer용)

터치 파일:

1. **`app/repositories/document_repository.py`**: `list_resyncable(project)` 추가 (§4).
2. **`app/mcp/types.py`**: `RegisteredResyncResult` TypedDict 추가, `RefreshIndexResult`에 `registered: NotRequired[...]` 추가. `from typing import NotRequired` 임포트 확인.
3. **`app/mcp/tools/sources.py`** `refresh_index`:
   - 파라미터 `include_registered: bool = False`, `force: bool = False` 추가.
   - `_inner`에서 기존 `document_index_service.refresh(...)` 호출 후, `include_registered`면 `bundle.document_repo.list_resyncable(project)`로 대상 문서를 얻어 각 문서를 `bundle.sync_service.resync(doc.id, force=force)`로 순회 (`document_repo`/`sync_service` 모두 `ServiceBundle` 필드로 노출됨 — `app/composition.py:143,148`).
   - 문서별 try/except로 `reindexed/skipped/failed` 집계.
   - payload 병합: `_to_refresh_payload(refresh_result)`에 `registered` 키를 조건부로 추가.
   - docstring 갱신(한국어): `include_registered`/`force` 설명, `source`는 api_document에 무관함, 응답의 `registered` 필드 설명, raw_document 문서는 제외됨을 명시.
4. **세션 경계 주의**: `refresh()`는 콜백 안 세션을 쓰고, `resync`도 같은 `bundle`의 세션/서비스를 쓴다. `resync`가 내부에서 `commit()`한다는 점을 확인하고, `refresh()`가 남긴 미커밋 상태가 없는지(refresh는 배치마다 커밋하므로 반환 시점엔 클린) 검토할 것. 문제 없으면 그대로, 세션 충돌 조짐 있으면 architect에 확인 요청.

테스트 방향(TDD):

- `list_resyncable`: source_url 있는 문서만, project 필터 동작, raw_document 문서 제외.
- `refresh_index(include_registered=False)`: 응답에 `registered` 키 없음(하위호환).
- `refresh_index(include_registered=True)`: URL 문서는 resync 호출됨, raw_document 문서는 호출 안 됨, 집계 정확.
- 문서 하나 resync 실패 시 나머지 계속 진행 + `failed`에 담김.
- api_document 대상 0건이어도 document_meta 결과는 정상 반환.
