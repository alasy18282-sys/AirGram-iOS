"""Send shop/auth codes via Telegram bot."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)


def _bot_token() -> str:
    return (
        os.environ.get('SHOP_BOT_TOKEN', '').strip()
        or os.environ.get('MYTELEGRAM_AUTH_BOT_TOKEN', '').strip()
    )


def send_message(chat_id: int, text: str) -> bool:
    token = _bot_token()
    if not token:
        log.warning('Shop bot token not configured')
        return False
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = urllib.parse.urlencode({
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
    }).encode()
    req = urllib.request.Request(url, data=payload, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return bool(data.get('ok'))
    except urllib.error.HTTPError as exc:
        log.warning('Telegram HTTP %s: %s', exc.code, exc.read())
        return False
    except Exception as exc:
        log.warning('Failed to send message: %s', exc)
        return False


def send_code(chat_id: int, code: str, *, purpose: str = 'входа') -> bool:
    emoji = '<tg-emoji emoji-id="5420323339723881652">⚠️</tg-emoji>'
    text = (
        f"Ваш код:\n<b>{code}</b>\n\n"
        f"{emoji} Не сообщайте никому данный код! Если его отправили не вы, просто проинорируйте. "
        f"Если данный код 6-ти значный, значит это мини-приложение."
    )
    return send_message(chat_id, text)
