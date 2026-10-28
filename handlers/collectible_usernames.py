"""Collectible username helpers: push refresh, active limits."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import config

if TYPE_CHECKING:
    from db.storage import Storage
    from handlers.push import PushQueue

log = logging.getLogger(__name__)

MAX_ACTIVE_USERNAMES = 10


def count_active_usernames(storage: 'Storage', user_id: int) -> int:
    user = storage.get_user_by_id(int(user_id))
    if not user:
        return 0
    count = 0
    if (user.get('username') or '').strip():
        count += 1
    for row in storage.get_collectible_usernames(int(user_id), include_inactive=False):
        if (row.get('username') or '').strip():
            count += 1
    return count


def _build_username_updates(storage: 'Storage', user_id: int, *,
                            notify_contacts: bool) -> tuple[list[bytes], list[dict]]:
    from handlers.packers import pack_update_user, pack_update_user_name

    user = storage.get_user_by_id(int(user_id))
    if not user:
        return [], []

    updates = [pack_update_user_name(user, storage)]
    users = [user]
    if notify_contacts:
        updates = [pack_update_user(int(user_id))]
    return updates, users


def notify_username_changed(
    storage: 'Storage',
    user_id: int,
    push_queue: Optional['PushQueue'] = None,
    *,
    notify_contacts: bool = False,
) -> int:
    """Deliver updateUserName to connected clients; queue for offline sessions."""
    from handlers.packers import pack_updates

    user = storage.get_user_by_id(int(user_id))
    if not user:
        return 0

    recipients = {int(user_id)}
    if notify_contacts:
        recipients.update(storage.get_users_who_know(int(user_id)))

    delivered = 0
    for recipient_id in recipients:
        if int(recipient_id) >= config.CHANNEL_ID_MIN:
            continue
        updates, users = _build_username_updates(
            storage, int(user_id), notify_contacts=notify_contacts)
        payload = pack_updates(
            updates,
            users,
            user_id=recipient_id,
            storage=storage,
            increment_seq=False,
        )
        if push_queue is not None and recipient_id == int(user_id):
            push_queue.push_to_user(recipient_id, payload, allow_enqueue=True)
        else:
            storage.enqueue_pending_update(recipient_id, payload)
        delivered += 1

    if delivered:
        log.debug('Username refresh for user %s sent to %d recipient(s)',
                 user_id, delivered)
    return delivered


def maybe_push_username_state(storage: 'Storage', user_id: int,
                              push_queue: Optional['PushQueue'] = None) -> None:
    """After login, refresh self client if collectible usernames are attached."""
    if storage.get_collectible_usernames(int(user_id), include_inactive=True):
        notify_username_changed(storage, int(user_id), push_queue)
