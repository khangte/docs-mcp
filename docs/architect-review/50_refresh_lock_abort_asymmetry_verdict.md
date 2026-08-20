# 50. refresh_index advisory lock — aborted 트랜잭션 비대칭 판정

- 작성: architect
- 원 리뷰: reviewer(동시성 락 리뷰), 수정요청 1건
- 관련: `docs/architect-review/49_data_flow_scenarios.md` 케이스 4, `docs/architect-review/31_refresh_index_batch_automation.md` §3.3

## 1. 판정 요약

**지적 타당. 수정 필요. 두 층 모두 고친다** — 단, 두 수정은 목적이 다르며 각각 독립적으로 정당하다.

| # | 대상 | 목적 | 필수 여부 |
| --- | --- | --- | --- |
| F1 | `app/mcp/tools/sources.py` `finally` | **락 해제 보장**(근본 수정) | 필수 |
| F2 | `app/services/documents/registered_resync.py:53` | **부분 실패 허용 계약 복구** | 필수(락과 무관한 자체 결함) |

## 2. 리뷰 내용 검증

리뷰가 지적한 경로를 코드로 확인했다.

1. `sync_service.resync` 는 재색인 시 `session.delete` / `sa_delete` / `flush` 를 직접 친다
   (`app/services/ingestor/sync_service.py:185-191`). 여기서 `IntegrityError` 등
   `SQLAlchemyError` 가 나올 수 있고, 이는 `DomainError` 도 `IntegrationError` 도 아니다.
2. `resync_registered_documents` 의 `except (DomainError, IntegrationError)`
   (`registered_resync.py:53`)는 그것을 잡지 못한다 → 루프를 뚫고 전파.
3. 전파 경로에 `refresh_index` 의 `finally: advisory_unlock(...)` 이 있다
   (`app/mcp/tools/sources.py:102-103`). 세션 트랜잭션이 이미 aborted 라
   `SELECT pg_advisory_unlock(...)` 자체가 `InFailedSqlTransaction` 으로 실패한다.
4. 결과가 두 겹으로 나쁘다.
   - `finally` 안의 새 예외가 원래 예외를 대체해 **원인이 로그에서 사라진다**.
   - **락이 안 풀린다.** advisory lock 은 비트랜잭셔널이라 rollback 으로도 안 풀리고,
     `session.close()` 는 커넥션을 풀에 *반납*할 뿐 닫지 않는다
     (`refresh_lock.py:35-38` 이 이미 명시한 바로 그 이유). 반납된 커넥션에 락이 붙은 채 남아,
     이후 다른 커넥션을 잡은 `refresh_index` 는 영구히 `refresh_in_progress` 를 받는다.
     (같은 커넥션을 재배정받으면 재진입으로 성공해 버려 **증상이 간헐적**이라는 점이 더 나쁘다.)
5. 비대칭 지적도 맞다. `document_index_service._refresh_source:300-312` 는
   `except Exception` 으로 넓게 잡고 **rollback 후 재raise** 하므로, 그 경로로 나오는 예외는
   항상 세션이 깨끗한 상태다. `refresh()` 는 `_PartialRefreshError` 를 삼키고 계속하므로
   aborted 트랜잭션이 도구 계층까지 올라오지 않는다.

즉 **락을 지키는 `finally` 가, 락과 무관한 호출자(resync)의 예외 처리 폭에 의존하고 있다.**
이게 구조적 결함이다.

## 3. 판정 근거 — 왜 한쪽만 고치면 안 되나

### F1 을 필수로 두는 이유 (근본 수정)

락 해제는 **callee 가 세션을 어떤 상태로 남겼든 성공해야 한다.** `registered_resync` 만 고치면
지금 알려진 한 경로만 막히고, 앞으로 `refresh_index` 안에 단계가 하나 추가될 때마다
같은 함정이 다시 열린다. 방어는 락을 잡은 지점에 있어야 한다.

`finally` 에서 unlock 직전에 `session.rollback()` 을 치는 것이 정답이다.

- **확정분을 잃지 않는다.** 이 도구의 커밋 경계는 이미 배치 단위(`BATCH_SIZE=100`,
  `document_index_service.py:448-464`)와 문서 단위(`sync_service.resync` 자체 커밋)로 내려가 있다.
  `finally` 시점의 미커밋 분량은 실패해서 어차피 버려야 할 것뿐이다.
- **락을 풀지 않는다.** advisory lock 은 트랜잭션에 묶이지 않으므로 rollback 후에도 유지되며,
  그 다음 `pg_advisory_unlock` 이 정상 트랜잭션에서 실행된다.
- 정상 경로에서도 무해하다(미커밋 변경이 없으므로 새 트랜잭션이 열릴 뿐이다).

### F2 를 별도로 필수로 두는 이유 (락과 무관)

`registered_resync` 의 명시된 계약은 **"문서 하나가 실패해도 나머지는 계속 진행한다"**
(`registered_resync.py:43-44`). 그런데 DB 레벨 오류에서만 그 계약이 깨져,
문서 1건의 `IntegrityError` 가 남은 전체 문서의 재동기화를 취소시킨다.
F1 이 락 누수를 막아 준 뒤에도 이 손실은 그대로 남는다. 따라서 F1 의 부수 효과로 덮지 않고
독립 결함으로 고친다.

**단, `except Exception` 으로 넓히지 않는다.** 이 핸들러는 재raise 없이 `continue` 하므로,
범위를 다 열면 `TypeError` 같은 프로그래밍 오류까지 "실패한 문서 1건"으로 조용히 집계된다.
`_refresh_source` 의 `except Exception` 은 **재raise 가 붙어 있어** 사정이 다르다 — 그 선례를
근거로 여기까지 넓히는 것은 잘못된 유추다. `SQLAlchemyError` 만 명시적으로 추가한다.

## 4. developer 지시 (수정 항목)

### F1. `app/mcp/tools/sources.py` — unlock 전 방어적 rollback

```python
            finally:
                # 재색인 도중 SQLAlchemyError 로 트랜잭션이 aborted 면 unlock 쿼리
                # 자체가 실패해, 원래 예외를 가리면서 락까지 남는다(풀에 반납된
                # 커넥션은 닫히지 않으므로 자동 해제도 안 된다). 락 해제는 callee 가
                # 남긴 세션 상태에 의존하면 안 되므로 여기서 먼저 정리한다.
                bundle.session.rollback()
                advisory_unlock(bundle.session, lock_key)
```

- 커밋 경계가 이미 배치/문서 단위라 이 rollback 이 확정분을 되돌리지 않는다는 점을 주석에 남긴다.
- 배치 CLI(`app/scripts/refresh_documents.py`)에는 **적용하지 않는다.** 프로세스 종료로 커넥션이
  물리적으로 닫혀 락이 자동 해제되고, 명시적 unlock 도 없어 같은 함정이 없다. 현행 유지.

### F2. `app/services/documents/registered_resync.py`

```python
from sqlalchemy.exc import SQLAlchemyError
...
        except (DomainError, IntegrationError, SQLAlchemyError) as e:
```

- `except Exception` 으로 넓히지 말 것(위 §3 근거).
- 기존 `session.rollback()` + `failed.append` + `continue` 흐름은 그대로 둔다.
- docstring 에 "DB 레벨 오류(`SQLAlchemyError`)도 문서 단위로 격리한다" 한 줄을 추가한다.

### F3. 테스트 2건

1. **통합** — `resync` 가 `SQLAlchemyError` 를 던지도록 만든 상태에서
   `refresh_index(include_registered=True)` 를 호출한 뒤, **다른 세션에서** 같은 lock key 를
   `pg_try_advisory_lock` 으로 잡을 수 있어야 한다(락이 실제로 풀렸는지 확인).
   같은 세션으로 검증하면 advisory lock 재진입 때문에 항상 통과해 **테스트가 무의미해진다** —
   반드시 새 세션/커넥션으로 검증할 것.
2. **단위** — `registered_resync`: 문서 3건 중 2번째가 `IntegrityError` 를 낼 때
   `failed == [2번째 id]` 이고 3번째가 정상 처리돼 `total=3, reindexed+skipped+len(failed)==3`.

## 5. 리뷰의 나머지 항목

- 배치/MCP lock key 정합성(`733100501`/`733100502`, `refresh_lock.py:16-17`) — **문제없음, 승인.**
  키 상수를 `refresh_lock` 한 곳으로 모으고 배치가 그것을 가져다 쓰는 구조도 의도대로다.
- 축 A/B 키 분리 유지도 정상(무거운 축 B 가 가벼운 축 A 틱을 굶기지 않게 하는 doc31 §3.3 결정).
- 787 passed / ruff clean 보고는 그대로 인정한다. F1~F3 반영 후 재실행만 요구한다.
