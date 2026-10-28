"""Push banner refresh to connected Telegram clients."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.storage import Storage

log = logging.getLogger(__name__)


def _list_recipient_ids(storage: 'Storage') -> list[int]:
    rows = storage._conn.execute(
        'SELECT DISTINCT user_id FROM sessions WHERE user_id IS NOT NULL',
    ).fetchall()
    return [int(row['user_id']) for row in rows]


def enqueue_banner_refresh(storage: 'Storage') -> int:
    """Notify online clients to refetch help.getPromoData via updateConfig."""
    from handlers.packers import pack_banner_refresh_push

    recipients = _list_recipient_ids(storage)
    for user_id in recipients:
        storage.enqueue_pending_update(
            user_id,
            pack_banner_refresh_push(storage=storage, recipient_id=user_id),
        )
    if recipients:
        log.info('Queued banner refresh for %d users', len(recipients))
    return len(recipients)
