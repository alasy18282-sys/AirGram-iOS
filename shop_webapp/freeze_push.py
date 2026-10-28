"""Push freeze state / settings updates to connected Telegram clients."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.storage import Storage

log = logging.getLogger(__name__)


def _list_online_user_ids(storage: 'Storage') -> list[int]:
    rows = storage._conn.execute(
        'SELECT DISTINCT user_id FROM sessions WHERE user_id IS NOT NULL',
    ).fetchall()
    return [int(row['user_id']) for row in rows]


def _enqueue(storage: 'Storage', user_id: int, update_data: bytes) -> None:
    storage.enqueue_pending_update(int(user_id), update_data)


def enqueue_freeze_user_refresh(storage: 'Storage', user_id: int) -> int:
    """Notify clients that a user's freeze visibility changed."""
    from handlers.packers import pack_freeze_user_push

    user = storage.get_user_by_id(int(user_id))
    if not user:
        return 0
    recipients = storage.get_users_who_see_user(int(user_id))
    for recipient_id in recipients:
        _enqueue(
            storage,
            recipient_id,
            pack_freeze_user_push(
                user, storage=storage, recipient_id=recipient_id),
        )
    if recipients:
        log.info('Queued freeze user refresh for %s to %d users',
                 user_id, len(recipients))
    return len(recipients)


def enqueue_frozen_users_refresh(storage: 'Storage') -> int:
    """Refresh all currently frozen users for everyone who can see them."""
    total = 0
    for uid in storage.list_frozen_user_ids():
        total += enqueue_freeze_user_refresh(storage, uid)
    return total


def enqueue_freeze_config_refresh(storage: 'Storage',
                                  recipient_ids: set[int] | None = None) -> int:
    """updateConfig + featured emoji refresh for clients."""
    from handlers.packers import (
        pack_banner_refresh_push,
        pack_update_read_featured_emoji_stickers,
        pack_updates,
    )

    if recipient_ids is None:
        recipient_ids = set(_list_online_user_ids(storage))
    emoji_upd = pack_updates(
        [pack_update_read_featured_emoji_stickers()],
        [],
        increment_seq=False,
    )
    for user_id in recipient_ids:
        _enqueue(
            storage,
            user_id,
            pack_banner_refresh_push(storage=storage, recipient_id=user_id),
        )
        _enqueue(storage, user_id, emoji_upd)
    if recipient_ids:
        log.info('Queued freeze config refresh for %d users', len(recipient_ids))
    return len(recipient_ids)


def _freeze_settings_recipients(storage: 'Storage') -> set[int]:
    """Everyone who should see freeze emoji / frozen peer changes."""
    recipients = set(_list_online_user_ids(storage))
    for uid in storage.list_frozen_user_ids():
        recipients.update(storage.get_users_who_see_user(int(uid)))
        recipients.add(int(uid))
    return recipients


def enqueue_freeze_settings_refresh(storage: 'Storage') -> int:
    """Instant refresh after freeze settings change (emoji, appeal URL, days)."""
    recipients = _freeze_settings_recipients(storage)
    total = enqueue_freeze_config_refresh(storage, recipients)
    total += enqueue_frozen_users_refresh(storage)
    return total


def enqueue_freeze_change(storage: 'Storage', user_id: int) -> int:
    """Freeze/unfreeze: push peer update + app config to everyone affected."""
    pushed = enqueue_freeze_user_refresh(storage, user_id)
    recipients = set(storage.get_users_who_see_user(int(user_id)))
    recipients.add(int(user_id))
    recipients.update(_list_online_user_ids(storage))
    for recipient_id in recipients:
        from handlers.packers import pack_banner_refresh_push
        _enqueue(
            storage,
            recipient_id,
            pack_banner_refresh_push(storage=storage, recipient_id=recipient_id),
        )
    return pushed + len(recipients)
