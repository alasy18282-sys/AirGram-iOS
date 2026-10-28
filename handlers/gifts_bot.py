"""System Gifts Bot — delivers case winnings on KoalGram."""
from __future__ import annotations

import config


START_TEXT = (
    '🎁 <b>Gift Bot</b> — бот выдачи подарков из кейсов KoalGram (@giftbot).\n\n'
    'Когда вы выигрываете подарок в кейсе и выбираете «Вывести» в магазине, '
    'подарок автоматически отправляется на указанный username в KoalGram.\n\n'
    'Откройте кейсы в магазине KoalGram Core.'
)


def is_gifts_bot(user: dict) -> bool:
    return int(user.get('id') or 0) == int(config.GIFTS_BOT_ID)


def _command_name(text: str) -> str:
    first = (text or '').strip().split(None, 1)[0] if (text or '').strip() else ''
    if not first.startswith('/'):
        return ''
    return first.split('@', 1)[0].lower()


def handle_gifts_bot_message(storage, sender: dict, incoming_msg: dict) -> dict:
    text = (incoming_msg.get('text') or incoming_msg.get('message') or '').strip()
    cmd = _command_name(text)
    if cmd in ('/start', '/help'):
        return {
            'text': START_TEXT,
            'entities': [{'type': 'bold', 'offset': 3, 'length': 5}],
        }
    return {
        'text': (
            'Вывод подарков выполняется через магазин кейсов.\n'
            'Откройте кейс, выиграйте подарок и нажмите «Вывести» в профиле.'
        ),
    }
