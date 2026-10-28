"""Send Market login codes via the auth Telegram bot."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)


def _bot_token() -> str:
    # Prefer auth bot so codes arrive in the same chat as shop/auth login.
    return (
        os.environ.get('MYTELEGRAM_AUTH_BOT_TOKEN', '').strip()
        or os.environ.get('SHOP_BOT_TOKEN', '').strip()
        or os.environ.get('RENTALS_BOT_TOKEN', '').strip()
    )


def send_message(chat_id: int, text: str) -> bool:
    token = _bot_token()
    if not token:
        log.warning('Auth/Market bot token not configured')
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


def send_code(chat_id: int, code: str) -> bool:
    emoji = '<tg-emoji emoji-id="5420323339723881652">⚠️</tg-emoji>'
    text = (
        f'Код для входа в Market:\n<b>{code}</b>\n\n'
        f'{emoji} Не сообщайте никому этот код. '
        f'Если вы не запрашивали вход — проигнорируйте сообщение.'
    )
    return send_message(chat_id, text)
