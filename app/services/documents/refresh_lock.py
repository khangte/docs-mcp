"""배치 CLI/MCP 도구가 공유하는 재색인 advisory lock.

배치 CLI(`app/scripts/refresh_documents.py`)와 MCP `refresh_index` 도구가
같은 문서군(축 A/B)을 동시에 재색인하면 두 writer 가 같은 `document_meta` 행에
동시에 붙을 수 있다(`docs/architect-review/53_data_flow_scenarios.md` 케이스 4).
두 경로가 같은 Postgres advisory lock key 를 잡아 상호 배제하도록 이 모듈에
lock key/획득/해제를 모아둔다.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

#: 임의 고정값. 이 저장소 다른 곳에서 advisory lock 키로 쓰지 않는다.
LOCK_KEY_META_SYNC = 733100501
LOCK_KEY_REGISTERED_RESYNC = 733100502


def select_lock_key(include_registered: bool) -> int:
    """`include_registered` 여부로 축 A/B 락 키를 가른다."""
    return LOCK_KEY_REGISTERED_RESYNC if include_registered else LOCK_KEY_META_SYNC


def try_advisory_lock(session: Session, lock_key: int) -> bool:
    """세션 단위 advisory lock 획득을 시도한다."""
    return bool(
        session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar()
    )


def advisory_unlock(session: Session, lock_key: int) -> None:
    """`try_advisory_lock` 으로 잡은 락을 명시적으로 해제한다.

    배치 CLI 는 프로세스 종료로 커넥션이 물리적으로 닫혀 자동 해제되지만,
    MCP 서버는 커넥션 풀을 재사용해 `session.close()` 가 커넥션을 반납할 뿐
    닫지는 않으므로 명시적 unlock 이 필요하다 — 안 하면 락이 풀 안 커넥션에
    계속 붙어 있어 다음 호출들이 영영 락을 못 잡는다.
    """
    session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
