"""@PremiumBot — temporary Premium trials and subscription info."""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Optional

import config
from handlers import packers

TRIAL_BUTTON = 'Опробовать Premium'

START_NO_PREMIUM = (
    'Покупка Premium временно недоступна, но вы можете опробовать Premium '
    'на 1 неделю по кнопке «Опробовать Premium».'
)

START_HAS_PREMIUM = (
    'У вас уже активен KoalGram Premium{until_text}.'
)

TRIAL_OK = (
    'Premium активирован на {days} дн. (до {until}). Приятного пользования!'
)

TRIAL_USED = (
    'Пробный Premium уже был активирован ранее. Покупка Premium временно недоступна.'
)

TRIAL_ERROR = 'Не удалось активировать пробный Premium. Попробуйте позже.'


def is_premium_bot(user: dict) -> bool:
    return int(user.get('id') or 0) == int(config.PREMIUM_BOT_ID)


def _command_name(text: str) -> str:
    first = (text or '').strip().split(None, 1)[0] if (text or '').strip() else ''
    if not first.startswith('/'):
        return ''
    return first.split('@', 1)[0].lower()


def _start_payload(text: str) -> str:
    parts = (text or '').strip().split(maxsplit=1)
    if len(parts) < 2:
        return ''
    return parts[1].strip().lower()


def _trial_keyboard_json() -> str:
    return json.dumps({
        'type': 'keyboard',
        'resize': True,
        'single_use': False,
        'rows': [[TRIAL_BUTTON]],
    }, ensure_ascii=False)


def _reply(text: str, *, reply_markup_json: str = '',
           push_user_update: Optional[int] = None) -> dict:
    data = {
        'text': text,
        'entities': packers.detect_text_entities(text),
    }
    if reply_markup_json:
        data['reply_markup_json'] = reply_markup_json
    if push_user_update:
        data['push_user_update'] = int(push_user_update)
    return data


def _format_until(until_ts: int) -> str:
    if until_ts <= 0:
        return ''
    dt = datetime.fromtimestamp(until_ts)
    return f' до {dt.strftime("%d.%m.%Y")}'


def _handle_trial(storage, user_id: int) -> dict:
    try:
        user = storage.grant_premium(user_id, trial=True)
    except ValueError as exc:
        if str(exc) == 'PREMIUM_TRIAL_ALREADY_USED':
            return _reply(TRIAL_USED)
        return _reply(TRIAL_ERROR)
    until = int(user.get('premium_until') or 0)
    days = int(config.PREMIUM_TRIAL_DAYS)
    until_label = datetime.fromtimestamp(until).strftime('%d.%m.%Y') if until else '—'
    return _reply(
        TRIAL_OK.format(days=days, until=until_label),
        push_user_update=user_id,
    )


def _handle_start(storage, user_id: int) -> dict:
    user = storage.get_user_by_id(user_id) or {}
    if user.get('premium'):
        until = int(user.get('premium_until') or 0)
        return _reply(
            START_HAS_PREMIUM.format(until_text=_format_until(until)),
        )
    if int(user.get('premium_trial_used') or 0):
        return _reply(TRIAL_USED)
    return _reply(START_NO_PREMIUM, reply_markup_json=_trial_keyboard_json())


def handle_premium_bot_message(storage, sender: dict,
                               incoming_msg: dict) -> dict:
    user_id = int(sender['id'])
    text = (incoming_msg.get('text') or '').strip()

    command = _command_name(text)
    if command in ('/start', '/premium'):
        payload = _start_payload(text)
        user = storage.get_user_by_id(user_id) or {}
        if payload == 'trial' and not user.get('premium'):
            return _handle_trial(storage, user_id)
        return _handle_start(storage, user_id)

    if text == TRIAL_BUTTON:
        return _handle_trial(storage, user_id)

    if command and command.startswith('/'):
        return _handle_start(storage, user_id)

    user = storage.get_user_by_id(user_id) or {}
    if user.get('premium'):
        return _reply(
            START_HAS_PREMIUM.format(
                until_text=_format_until(int(user.get('premium_until') or 0))),
        )
    if int(user.get('premium_trial_used') or 0):
        return _reply(TRIAL_USED)
    return _reply(START_NO_PREMIUM, reply_markup_json=_trial_keyboard_json())
