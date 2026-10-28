"""Complaint moderation helpers for the external Telegram report bot."""
from __future__ import annotations

from typing import Optional

import config
from db.storage import Storage
from handlers import packers


def _enqueue_user_refresh(storage: Storage, user_id: int) -> None:
    target = storage.get_user_by_id(int(user_id))
    if not target:
        return
    recipients = set(storage.get_users_who_see_user(int(user_id)))
    recipients.add(int(user_id))
    for recipient_id in sorted(recipients):
        update = packers.pack_updates(
            [packers.pack_update_user(int(user_id))],
            [target],
            user_id=int(recipient_id),
            storage=storage,
            increment_seq=True,
        )
        storage.enqueue_pending_update(int(recipient_id), update)


def apply_report_action(storage: Storage, complaint_id: int, *,
                        action: str, moderator_tg_id: int) -> tuple[dict, str]:
    complaint = storage.get_report_complaint(int(complaint_id))
    if not complaint:
        raise ValueError('REPORT_NOT_FOUND')
    if (complaint.get('status') or '').strip().lower() != 'open':
        raise ValueError('REPORT_ALREADY_RESOLVED')

    target_id = int(complaint.get('reported_user_id') or 0)
    target = storage.get_user_by_id(target_id)
    if not target:
        raise ValueError('TARGET_NOT_FOUND')
    if target_id in {
        int(config.SERVICE_USER_ID),
        int(config.STICKERS_BOT_ID),
        int(config.REPORT_BOT_ID),
        int(config.GIFTS_BOT_ID),
        int(config.PREMIUM_BOT_ID),
    } or target.get('is_bot'):
        raise ValueError('TARGET_PROTECTED')

    action = (action or '').strip().lower()
    status_text = ''
    if action == 'dismiss':
        status_text = 'Жалоба закрыта без мер.'
    elif action == 'noncontacts':
        storage.set_user_paid_message_price(
            target_id, config.STARS_PAID_MESSAGE_AMOUNT_MAX)
        _enqueue_user_refresh(storage, target_id)
        status_text = (
            'Для пользователя включено ограничение на сообщения от '
            'неконтактов.'
        )
    elif action == 'freeze':
        storage.freeze_user_account(target_id, refresh=True)
        _enqueue_user_refresh(storage, target_id)
        status_text = 'Аккаунт заморожен.'
    elif action == 'delete':
        if not storage.is_user_banned(target_id):
            storage.ban_user_account(target_id, block_recreate=True)
        _enqueue_user_refresh(storage, target_id)
        status_text = 'Аккаунт удалён.'
    else:
        raise ValueError('REPORT_ACTION_INVALID')

    complaint = storage.resolve_report_complaint(
        int(complaint_id),
        admin_action=action,
        moderator_tg_id=int(moderator_tg_id),
    )
    return complaint, status_text
