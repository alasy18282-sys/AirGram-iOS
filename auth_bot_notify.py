"""Send login codes to users via the auth Telegram bot."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import config

log = logging.getLogger(__name__)


def _send_message(chat_id: int, text: str) -> bool:
    token = config.AUTH_BOT_TOKEN
    if not token:
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
        if not data.get('ok'):
            log.warning('Telegram API error: %s', data)
            return False
        return True
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        log.warning('Telegram HTTP %s: %s', exc.code, body)
        return False
    except Exception as exc:
        log.warning('Failed to send auth code via Telegram: %s', exc)
        return False


def _format_code_message(code: str) -> str:
    emoji = '<tg-emoji emoji-id="5420323339723881652">⚠️</tg-emoji>'
    return (
        f'Код для входа:\n<b>{code}</b>\n\n'
        f'{emoji} Не сообщайте никому этот код. '
        f'Если вы не запрашивали вход — проигнорируйте сообщение.'
    )


def _send_code_with_retries(telegram_user_id: int, phone: str, text: str) -> bool:
    for attempt in range(3):
        if _send_message(telegram_user_id, text):
            log.info(
                'Auth code sent to Telegram user %d for +%s',
                telegram_user_id, phone,
            )
            return True
        if attempt < 2:
            time.sleep(1.0)
    log.warning(
        'Failed to send auth code to Telegram user %d for +%s after retries',
        telegram_user_id, phone,
    )
    return False


def try_notify_login_code(storage, phone: str, code: str,
                          *, blocking: bool = False) -> bool:
    """Deliver a login code through the auth bot.

    The MTProto dispatcher runs on the asyncio event loop, so the HTTP request
    must not block it: a slow Telegram API call (15s timeout x 3 attempts)
    freezes every connected client and looks like endless "Connecting".
    """
    if not config.AUTH_BOT_TOKEN:
        log.warning('Auth bot token not configured, cannot send code for +%s', phone)
        return False
    try:
        telegram_user_id = storage.get_telegram_user_by_phone(phone)
    except Exception:
        # A schema/storage failure here must never hang the client: auth.sendCode
        # waits on this handler, and an exception means no RPC response at all.
        log.exception('Phone->telegram lookup failed for +%s', phone)
        return False
    if not telegram_user_id:
        log.info(
            'No Telegram user linked to +%s — code not sent via bot '
            '(use /start in auth bot first)',
            phone,
        )
        return False
    text = _format_code_message(code)
    if blocking:
        return _send_code_with_retries(int(telegram_user_id), phone, text)
    threading.Thread(
        target=_send_code_with_retries,
        args=(int(telegram_user_id), phone, text),
        name='login-code-notify',
        daemon=True,
    ).start()
    return True
