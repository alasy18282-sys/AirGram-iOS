#!/usr/bin/env python3
"""Telegram moderation bot for report complaints."""
from __future__ import annotations

import json
import logging
import sys
import time
from threading import Event
import urllib.error
import urllib.parse
import urllib.request

import config
from db.storage import Storage
from report_moderation import apply_report_action

log = logging.getLogger(__name__)


def _api(method: str, **params) -> dict:
    token = config.REPORT_ADMIN_BOT_TOKEN
    if not token:
        raise RuntimeError('MYTELEGRAM_REPORT_ADMIN_BOT_TOKEN is not set')
    url = f'https://api.telegram.org/bot{token}/{method}'
    if params:
        data = urllib.parse.urlencode(params).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=65) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _send_message(chat_id: int, text: str) -> None:
    _api('sendMessage', chat_id=chat_id, text=text, parse_mode='HTML')


def _answer_callback(callback_query_id: str, text: str,
                     *, show_alert: bool = False) -> None:
    _api(
        'answerCallbackQuery',
        callback_query_id=callback_query_id,
        text=text,
        show_alert='true' if show_alert else 'false',
    )


def _is_admin(telegram_user_id: int) -> bool:
    return int(telegram_user_id) in config.REPORT_ADMIN_IDS


def _handle_start(chat_id: int, user_id: int) -> None:
    if not _is_admin(user_id):
        _send_message(chat_id, 'Нет доступа.')
        return
    _send_message(
        chat_id,
        '<b>Report Admin Bot</b>\n'
        '/start — показать это сообщение\n'
        '/openreports — показать открытые жалобы',
    )


def _handle_open_reports(chat_id: int, user_id: int, storage: Storage) -> None:
    if not _is_admin(user_id):
        _send_message(chat_id, 'Нет доступа.')
        return
    rows = storage.list_open_report_complaints(limit=20)
    if not rows:
        _send_message(chat_id, 'Открытых жалоб нет.')
        return
    lines = ['<b>Открытые жалобы</b>']
    for row in rows:
        lines.append(
            f'• #{int(row["id"])} — {row.get("reason") or "-"} '
            f'на <code>{int(row.get("reported_user_id") or 0)}</code>'
        )
    _send_message(chat_id, '\n'.join(lines))


def _dispatch_message(message: dict, storage: Storage) -> None:
    text = (message.get('text') or '').strip()
    chat_id = int((message.get('chat') or {}).get('id') or 0)
    user_id = int((message.get('from') or {}).get('id') or 0)
    if not text or not chat_id or not user_id:
        return
    cmd = text.split()[0].split('@', 1)[0].lower()
    if cmd == '/start':
        _handle_start(chat_id, user_id)
    elif cmd == '/openreports':
        _handle_open_reports(chat_id, user_id, storage)


def _handle_callback(query: dict, storage: Storage) -> None:
    callback_id = query.get('id') or ''
    from_user = query.get('from') or {}
    user_id = int(from_user.get('id') or 0)
    if not _is_admin(user_id):
        _answer_callback(callback_id, 'Нет доступа.', show_alert=True)
        return

    data = (query.get('data') or '').strip()
    parts = data.split(':')
    if len(parts) != 3 or parts[0] != 'report' or not parts[1].isdigit():
        _answer_callback(callback_id, 'Некорректное действие.', show_alert=True)
        return

    complaint_id = int(parts[1])
    action = parts[2]
    try:
        complaint, result_text = apply_report_action(
            storage,
            complaint_id,
            action=action,
            moderator_tg_id=user_id,
        )
    except ValueError as exc:
        _answer_callback(callback_id, str(exc), show_alert=True)
        return
    except Exception:
        log.exception('Failed to apply moderation action')
        _answer_callback(callback_id, 'Не удалось применить действие.', show_alert=True)
        return

    message = query.get('message') or {}
    chat_id = int((message.get('chat') or {}).get('id') or 0)
    message_id = int(message.get('message_id') or 0)
    if chat_id and message_id:
        try:
            _api(
                'editMessageReplyMarkup',
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=json.dumps({'inline_keyboard': []}),
            )
        except Exception:
            pass

    _answer_callback(callback_id, 'Вердикт применён.')
    target_id = int(complaint.get('reported_user_id') or 0)
    _send_message(
        chat_id or user_id,
        f'Жалоба #{complaint_id} обработана.\n'
        f'Пользователь: <code>{target_id}</code>\n'
        f'Результат: <b>{result_text}</b>',
    )


def run_bot(*, stop_event: Event | None = None,
            storage: Storage | None = None) -> None:
    if not config.REPORT_ADMIN_BOT_TOKEN:
        if stop_event is None:
            print('Set MYTELEGRAM_REPORT_ADMIN_BOT_TOKEN')
            sys.exit(1)
        log.warning('Report admin bot token is not configured; poller disabled')
        return

    own_storage = storage is None
    if storage is None:
        storage = Storage.open_tool()
    stop_event = stop_event or Event()
    offset = 0
    log.info('Report admin bot started (admins: %s)',
             ', '.join(str(x) for x in sorted(config.REPORT_ADMIN_IDS)))
    while not stop_event.is_set():
        try:
            result = _api('getUpdates', offset=offset, timeout=30)
            for update in result.get('result', []):
                offset = int(update['update_id']) + 1
                if update.get('callback_query'):
                    _handle_callback(update['callback_query'], storage)
                elif update.get('message'):
                    _dispatch_message(update['message'], storage)
        except urllib.error.HTTPError as exc:
            log.error('Telegram HTTP %s: %s', exc.code, exc.read())
            stop_event.wait(5)
        except Exception:
            log.exception('Polling error')
            stop_event.wait(5)
    if own_storage:
        conn = getattr(storage, '_conn', None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == '__main__':
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    run_bot()
