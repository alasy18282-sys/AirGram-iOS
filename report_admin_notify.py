"""Send report complaints to admins via a Telegram Bot API bot."""
from __future__ import annotations

import html
import json
import mimetypes
import os
import uuid
import urllib.error
import urllib.parse
import urllib.request

import config


def _api(method: str, **params) -> dict:
    token = config.REPORT_ADMIN_BOT_TOKEN
    if not token:
        raise RuntimeError('REPORT_ADMIN_BOT_TOKEN is not configured')
    url = f'https://api.telegram.org/bot{token}/{method}'
    data = urllib.parse.urlencode(params).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _api_multipart(method: str, *, fields: dict[str, str],
                   files: dict[str, tuple[str, bytes, str]]) -> dict:
    token = config.REPORT_ADMIN_BOT_TOKEN
    if not token:
        raise RuntimeError('REPORT_ADMIN_BOT_TOKEN is not configured')
    boundary = f'----KoalGram{uuid.uuid4().hex}'
    body = bytearray()

    def add_line(line: bytes = b'') -> None:
        body.extend(line + b'\r\n')

    for key, value in fields.items():
        add_line(f'--{boundary}'.encode())
        add_line(
            f'Content-Disposition: form-data; name="{key}"'.encode()
        )
        add_line()
        add_line(str(value).encode('utf-8'))

    for key, (filename, payload, content_type) in files.items():
        add_line(f'--{boundary}'.encode())
        add_line(
            f'Content-Disposition: form-data; name="{key}"; '
            f'filename="{filename}"'.encode()
        )
        add_line(f'Content-Type: {content_type}'.encode())
        add_line()
        body.extend(payload)
        add_line()

    add_line(f'--{boundary}--'.encode())
    url = f'https://api.telegram.org/bot{token}/{method}'
    req = urllib.request.Request(
        url,
        data=bytes(body),
        method='POST',
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _complaint_message(storage, complaint: dict) -> str:
    reporter = storage.get_user_by_id(int(complaint.get('reporter_id') or 0)) or {}
    target = storage.get_user_by_id(int(complaint.get('reported_user_id') or 0)) or {}
    reporter_username = (reporter.get('username') or '').strip()
    target_username = (complaint.get('reported_username') or target.get('username') or '').strip()
    description = (complaint.get('description') or '').strip()
    evidence_mode = (complaint.get('evidence_mode') or '').strip()
    lines = [
        f'<b>Жалоба #{int(complaint["id"])}</b>',
        f'Причина: <b>{html.escape(complaint.get("reason") or "-")}</b>',
        f'Режим: <b>{html.escape(evidence_mode or "-")}</b>',
        '',
        'Кто пожаловался:',
        f'• ID: <code>{int(complaint.get("reporter_id") or 0)}</code>',
    ]
    if reporter_username:
        lines.append(f'• Username: @{html.escape(reporter_username)}')
    lines.extend([
        '',
        'На кого пожаловались:',
        f'• ID: <code>{int(complaint.get("reported_user_id") or 0)}</code>',
    ])
    if target_username:
        lines.append(f'• Username: @{html.escape(target_username)}')
    if description:
        lines.extend([
            '',
            'Описание / пояснение:',
            html.escape(description),
        ])
    return '\n'.join(lines)


def _inline_actions(complaint_id: int) -> str:
    markup = {
        'inline_keyboard': [
            [
                {'text': 'Запретить неконтактам', 'callback_data': f'report:{complaint_id}:noncontacts'},
                {'text': 'Заморозить', 'callback_data': f'report:{complaint_id}:freeze'},
            ],
            [
                {'text': 'Удалить аккаунт', 'callback_data': f'report:{complaint_id}:delete'},
                {'text': 'Без мер', 'callback_data': f'report:{complaint_id}:dismiss'},
            ],
        ]
    }
    return json.dumps(markup, ensure_ascii=False)


def _photo_path(storage, photo_id: int) -> str:
    if not photo_id:
        return ''
    row = storage.get_photo_by_id(int(photo_id))
    if not row or not row.get('file_path'):
        return ''
    path = row['file_path']
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(__file__), path)
    return path if os.path.isfile(path) else ''


def notify_report_admins(storage, complaint: dict) -> None:
    if not config.REPORT_ADMIN_BOT_TOKEN or not config.REPORT_ADMIN_IDS:
        return
    text = _complaint_message(storage, complaint)
    reply_markup = _inline_actions(int(complaint['id']))
    photo_path = _photo_path(storage, int(complaint.get('evidence_photo_id') or 0))

    for admin_id in sorted(config.REPORT_ADMIN_IDS):
        if photo_path:
            with open(photo_path, 'rb') as f:
                payload = f.read()
            mime = mimetypes.guess_type(photo_path)[0] or 'image/jpeg'
            _api_multipart(
                'sendPhoto',
                fields={
                    'chat_id': str(admin_id),
                    'caption': f'Доказательство по жалобе #{int(complaint["id"])}',
                },
                files={
                    'photo': (os.path.basename(photo_path), payload, mime),
                },
            )
        _api(
            'sendMessage',
            chat_id=admin_id,
            text=text,
            parse_mode='HTML',
            reply_markup=reply_markup,
            disable_web_page_preview='true',
        )
