from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.storage import Storage


def enqueue_safety_label_push(storage: 'Storage', user_id: int) -> None:
    """Notify online clients that a user's SCAM/FAKE label changed."""
    subject = storage.get_user_by_id(int(user_id))
    if not subject:
        return

    from handlers.packers import pack_freeze_user_push

    recipients = {int(user_id)}
    recipients.update(storage.get_users_who_know(int(user_id)))
    for recipient_id in recipients:
        storage.enqueue_pending_update(
            recipient_id,
            pack_freeze_user_push(
                subject,
                storage=storage,
                recipient_id=recipient_id,
            ),
        )


def enqueue_verification_push(storage: 'Storage', peer_id: int) -> None:
    """Notify online clients that a peer's verification badge changed."""
    import config

    peer_id = int(peer_id)
    if peer_id >= int(config.CHANNEL_ID_MIN):
        _enqueue_channel_verification_push(storage, peer_id)
        return

    subject = storage.get_user_by_id(peer_id)
    if not subject:
        return

    from handlers.packers import pack_user_verification_push

    recipients = {peer_id}
    recipients.update(storage.get_users_who_know(peer_id))
    for recipient_id in recipients:
        storage.enqueue_pending_update(
            recipient_id,
            pack_user_verification_push(
                subject,
                storage=storage,
                recipient_id=recipient_id,
            ),
        )


def _enqueue_channel_verification_push(storage: 'Storage', channel_id: int) -> None:
    channel = storage.get_channel(channel_id)
    if not channel:
        return

    from handlers.packers import pack_channel_verification_push

    recipients = set(storage.list_channel_member_ids(channel_id))
    # Also notify users who have a dialog with the channel (left members, etc.).
    try:
        rows = storage._conn.execute(
            'SELECT owner_id FROM dialogs WHERE peer_id=?',
            (channel_id,),
        ).fetchall()
        recipients.update(int(r['owner_id']) for r in rows)
    except Exception:
        pass

    for recipient_id in recipients:
        storage.enqueue_pending_update(
            recipient_id,
            pack_channel_verification_push(
                channel,
                storage=storage,
                recipient_id=recipient_id,
            ),
        )


def purge_expired_and_notify(storage: 'Storage') -> list[int]:
    expired = storage.purge_expired_bot_verifications()
    for peer_id in expired:
        enqueue_verification_push(storage, peer_id)
    return expired
