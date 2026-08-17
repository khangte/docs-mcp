"""`app/services/documents/refresh_lock.py` advisory lock 유틸 테스트.

배치 CLI(`refresh_documents.py`)와 MCP `refresh_index` 도구가 같은 lock key 로
Postgres advisory lock 을 공유해야 두 writer 가 같은 `document_meta` 행에
동시에 붙지 않는다(`docs/architect-review/53_data_flow_scenarios.md` 케이스 4).
실제 lock 획득/충돌/해제는 진짜 Postgres 세션이 있어야 검증되므로 unit 이
아니라 integration 에 둔다.
"""

from __future__ import annotations

from app.services.documents.refresh_lock import (
    LOCK_KEY_META_SYNC,
    LOCK_KEY_REGISTERED_RESYNC,
    advisory_unlock,
    select_lock_key,
    try_advisory_lock,
)


def test_select_lock_key_splits_axis_a_and_b() -> None:
    """include_registered 여부로 축 A/B 락 키를 가른다."""
    assert select_lock_key(include_registered=False) == LOCK_KEY_META_SYNC
    assert select_lock_key(include_registered=True) == LOCK_KEY_REGISTERED_RESYNC


def test_second_session_fails_to_acquire_held_lock(session_factory) -> None:
    """한 세션이 잡은 락은 다른 세션에서 획득할 수 없다."""
    holder = session_factory()
    waiter = session_factory()
    try:
        assert try_advisory_lock(holder, LOCK_KEY_META_SYNC) is True
        assert try_advisory_lock(waiter, LOCK_KEY_META_SYNC) is False
    finally:
        advisory_unlock(holder, LOCK_KEY_META_SYNC)
        holder.close()
        waiter.close()


def test_advisory_unlock_releases_lock_for_other_sessions(session_factory) -> None:
    """advisory_unlock 후에는 다른 세션도 같은 락을 획득할 수 있다."""
    holder = session_factory()
    waiter = session_factory()
    try:
        assert try_advisory_lock(holder, LOCK_KEY_REGISTERED_RESYNC) is True
        advisory_unlock(holder, LOCK_KEY_REGISTERED_RESYNC)

        assert try_advisory_lock(waiter, LOCK_KEY_REGISTERED_RESYNC) is True
    finally:
        advisory_unlock(waiter, LOCK_KEY_REGISTERED_RESYNC)
        holder.close()
        waiter.close()
