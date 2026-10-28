#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import config
from db.storage import normalize_collectible_username, is_valid_collectible_username

TOKEN = os.environ.get('MYTELEGRAM_COLLECTIBLE_BOT_TOKEN', '').strip()
ADMINS = {8549770368, 8014265847}
API = f"https://api.telegram.org/bot{TOKEN}"

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)


def api(method: str, **params):
    data = urlencode(params).encode('utf-8')
    req = Request(f"{API}/{method}", data=data, method='POST')
    with urlopen(req, timeout=35) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    if not payload.get('ok'):
        raise RuntimeError(payload)
    return payload.get('result')


def send(chat_id: int, text: str) -> None:
    api('sendMessage', chat_id=chat_id, text=text, parse_mode='HTML', disable_web_page_preview=True)


def parse_grant_time(date_text: str) -> int:
    dt = datetime.strptime(date_text.strip(), '%d.%m.%Y %H:%M')
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS collectible_usernames (
        username TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0,
        price TEXT NOT NULL DEFAULT '',
        granted_at INTEGER NOT NULL DEFAULT 0,
        granted_by INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL DEFAULT 0
    )''')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_collectible_usernames_user '
        'ON collectible_usernames(user_id, sort_order, username)'
    )
    conn.execute('''CREATE TABLE IF NOT EXISTS gift_blockchain_links (
        slug TEXT PRIMARY KEY,
        owner_address TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL DEFAULT 0
    )''')
    conn.commit()
    return conn


def resolve_user(conn: sqlite3.Connection, raw: str):
    raw = raw.strip()
    if raw.lstrip('-').isdigit():
        row = conn.execute('SELECT * FROM users WHERE id=?', (int(raw),)).fetchone()
        return dict(row) if row else None
    username = normalize_collectible_username(raw)
    row = conn.execute(
        'SELECT * FROM users WHERE lower(username)=?', (username,),
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        '''SELECT u.* FROM collectible_usernames cu
           JOIN users u ON u.id=cu.user_id
           WHERE lower(cu.username)=? AND cu.active=1''',
        (username,),
    ).fetchone()
    return dict(row) if row else None


def grant_collectible_username(conn: sqlite3.Connection, user_id: int, username: str,
                               price: str, granted_at: int, granted_by: int) -> dict:
    primary = conn.execute(
        'SELECT id FROM users WHERE lower(username)=?', (username,),
    ).fetchone()
    if primary and int(primary['id']) != int(user_id):
        raise ValueError('USERNAME_OCCUPIED')
    owner = conn.execute(
        'SELECT * FROM collectible_usernames WHERE lower(username)=?', (username,),
    ).fetchone()
    if owner and int(owner['user_id']) != int(user_id):
        raise ValueError('USERNAME_OCCUPIED')
    channel = conn.execute(
        'SELECT id FROM channels WHERE lower(username)=?', (username,),
    ).fetchone()
    if channel:
        raise ValueError('USERNAME_OCCUPIED')
    order_row = conn.execute(
        '''SELECT MAX(v) AS n FROM (
               SELECT COALESCE(MAX(sort_order), 0) AS v
               FROM collectible_usernames WHERE user_id=?
               UNION ALL
               SELECT COALESCE(username_sort_order, 0) AS v
               FROM users WHERE id=?
           )''',
        (int(user_id), int(user_id)),
    ).fetchone()
    sort_order = int(order_row['n'] if order_row and order_row['n'] is not None else 0) + 1
    now = int(time.time())
    conn.execute(
        '''INSERT INTO collectible_usernames
           (username, user_id, active, sort_order, price, granted_at, granted_by, created_at)
           VALUES (?, ?, 1, ?, ?, ?, ?, ?)
           ON CONFLICT(username) DO UPDATE SET
               user_id=excluded.user_id, active=1, sort_order=excluded.sort_order,
               price=excluded.price, granted_at=excluded.granted_at,
               granted_by=excluded.granted_by''',
        (username, int(user_id), sort_order, price, int(granted_at), int(granted_by), now),
    )
    conn.commit()
    row = conn.execute(
        'SELECT * FROM collectible_usernames WHERE username=?', (username,),
    ).fetchone()
    return dict(row)


def remove_collectible_username(conn: sqlite3.Connection, user_id: int, username: str) -> None:
    row = conn.execute(
        'SELECT * FROM collectible_usernames WHERE lower(username)=?', (username,),
    ).fetchone()
    if not row or int(row['user_id']) != int(user_id):
        raise ValueError('USERNAME_NOT_FOUND')
    conn.execute(
        'DELETE FROM collectible_usernames WHERE username=? AND user_id=?',
        (username, int(user_id)),
    )
    conn.commit()


def parse_gift_slug(raw: str) -> str:
    value = (raw or '').strip()
    if not value:
        return ''
    if re.fullmatch(r'[A-Za-z0-9_-]+', value):
        return value.lower()
    parsed = urlparse(value)
    if parsed.scheme == 'tg':
        slug = parse_qs(parsed.query).get('slug', [''])[0]
        return slug.strip().strip('/').lower()
    if parsed.scheme in ('http', 'https'):
        parts = [part for part in parsed.path.split('/') if part]
        if len(parts) >= 2 and parts[0].lower() == 'nft':
            return parts[1].strip().lower()
    return ''


def get_saved_gift_owner(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute(
        '''SELECT s.unique_slug, s.owner_user_id, u.username
           FROM saved_star_gifts s
           LEFT JOIN users u ON u.id=s.owner_user_id
           WHERE s.unique_slug=? AND s.upgraded=1
           LIMIT 1''',
        (slug,),
    ).fetchone()
    return dict(row) if row else None


def get_saved_gift_by_slug(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute(
        '''SELECT s.*, u.username AS owner_username
           FROM saved_star_gifts s
           LEFT JOIN users u ON u.id=s.owner_user_id
           WHERE s.unique_slug=? AND s.upgraded=1
           LIMIT 1''',
        (slug,),
    ).fetchone()
    return dict(row) if row else None


def find_black_backdrop_attr_id(conn: sqlite3.Connection, gift_id: int) -> int | None:
    row = conn.execute(
        '''SELECT id FROM star_gift_upgrade_attrs
           WHERE attr_type='backdrop' AND lower(name)='black' AND gift_id=?
           ORDER BY id LIMIT 1''',
        (int(gift_id),),
    ).fetchone()
    if row:
        return int(row['id'])
    row = conn.execute(
        '''SELECT id FROM star_gift_upgrade_attrs
           WHERE attr_type='backdrop' AND lower(name)='black' AND gift_id=0
           ORDER BY id LIMIT 1''',
    ).fetchone()
    return int(row['id']) if row else None


def set_gift_backdrop_black(conn: sqlite3.Connection, slug: str) -> dict:
    saved = get_saved_gift_by_slug(conn, slug)
    if not saved:
        raise ValueError('GIFT_NOT_FOUND')
    backdrop_attr_id = find_black_backdrop_attr_id(conn, int(saved['gift_id']))
    if not backdrop_attr_id:
        raise ValueError('BLACK_BACKDROP_NOT_FOUND')
    conn.execute(
        'UPDATE saved_star_gifts SET backdrop_attr_id=? WHERE saved_id=?',
        (backdrop_attr_id, int(saved['saved_id'])),
    )
    conn.commit()
    return {
        'saved': saved,
        'backdrop_attr_id': backdrop_attr_id,
    }


def upsert_blockchain_gift(conn: sqlite3.Connection, slug: str,
                           owner_address: str) -> dict:
    now = int(time.time())
    conn.execute(
        '''INSERT INTO gift_blockchain_links
           (slug, owner_address, created_at, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(slug) DO UPDATE SET
               owner_address=excluded.owner_address,
               updated_at=excluded.updated_at''',
        (slug, owner_address.strip(), now, now),
    )
    conn.commit()
    row = conn.execute(
        'SELECT * FROM gift_blockchain_links WHERE slug=?',
        (slug,),
    ).fetchone()
    return dict(row)


def remove_blockchain_gift(conn: sqlite3.Connection, slug: str) -> bool:
    cur = conn.execute('DELETE FROM gift_blockchain_links WHERE slug=?', (slug,))
    conn.commit()
    return bool(cur.rowcount)


def wake_server() -> None:
    try:
        Path(config.DB_PATH).with_name('push_wake').touch()
    except Exception:
        pass


def handle_message(msg: dict) -> None:
    chat_id = int(msg['chat']['id'])
    from_id = int(msg.get('from', {}).get('id') or 0)
    text = (msg.get('text') or '').strip()

    if from_id not in ADMINS:
        send(chat_id, 'Доступ запрещен.')
        return

    if text == '/start':
        send(chat_id, 'Команды:\n'
                      '<code>/username give (юзернейм/id) (collectible) (цена) (04.07.2026 00:00)</code>\n'
                      '<code>/username remove (юзернейм/id) (collectible)</code>\n'
                      '<code>/blockchain add (ссылка_на_подарок) (ton_адрес)</code>\n'
                      '<code>/blockchain remove (ссылка_на_подарок)</code>\n'
                      '<code>/black (ссылка_на_подарок)</code>')
        return

    parts = text.split()
    command = parts[0].split('@', 1)[0].lower() if parts else ''
    conn = open_db()

    if command == '/black':
        if len(parts) < 2:
            send(chat_id, 'Пример: <code>/black https://t.me/nft/slug</code>')
            return
        slug = parse_gift_slug(parts[1])
        if not slug:
            send(chat_id, 'Не удалось определить slug подарка из ссылки.')
            return
        try:
            result = set_gift_backdrop_black(conn, slug)
        except ValueError as exc:
            if str(exc) == 'GIFT_NOT_FOUND':
                send(chat_id, 'Подарок с таким slug не найден в базе.')
                return
            if str(exc) == 'BLACK_BACKDROP_NOT_FOUND':
                send(chat_id, 'Фон Black не найден для этого типа подарка.')
                return
            send(chat_id, f'Ошибка: <code>{exc}</code>')
            return
        saved = result['saved']
        wake_server()
        send(
            chat_id,
            'Фон подарка изменён на <b>Black</b>\n'
            f'Slug: <code>{slug}</code>\n'
            f'Владелец: <code>{saved["owner_user_id"]}</code> '
            f'@{saved.get("owner_username") or ""}\n'
            f'Ссылка: <code>https://t.me/nft/{slug}</code>',
        )
        return

    if len(parts) < 2 or command not in ('/username', '/blockchain'):
        send(chat_id, 'Неверная команда. Напиши /start.')
        return

    action = parts[1]

    if command == '/username' and action == 'give':
        if len(parts) < 7:
            send(chat_id, 'Пример: <code>/username give monk crow 10000 04.07.2026 00:00</code>')
            return
        target_raw = parts[2]
        collectible = normalize_collectible_username(parts[3])
        price = parts[4]
        date_text = ' '.join(parts[5:7])

        if not is_valid_collectible_username(collectible):
            send(chat_id, 'Collectible username некорректный.')
            return
        try:
            granted_at = parse_grant_time(date_text)
        except ValueError:
            send(chat_id, 'Дата должна быть в формате <code>04.07.2026 00:00</code>.')
            return
        user = resolve_user(conn, target_raw)
        if not user:
            send(chat_id, 'Пользователь не найден.')
            return
        try:
            row = grant_collectible_username(
                conn, int(user['id']), collectible, price=price,
                granted_at=granted_at, granted_by=from_id)
        except ValueError as exc:
            send(chat_id, f'Ошибка: <code>{exc}</code>')
            return
        wake_server()
        send(chat_id,
             'Выдан collectible username\n'
             f'Пользователь: <code>{user["id"]}</code> @{user.get("username") or ""}\n'
             f'Username: <code>@{row["username"]}</code>\n'
             f'Цена: <code>{row["price"]}</code>\n'
             f'Дата: <code>{date_text}</code>')
        return

    if command == '/username' and action == 'remove':
        if len(parts) != 4:
            send(chat_id, 'Пример: <code>/username remove monk crow</code>')
            return
        user = resolve_user(conn, parts[2])
        if not user:
            send(chat_id, 'Пользователь не найден.')
            return
        collectible = normalize_collectible_username(parts[3])
        try:
            remove_collectible_username(conn, int(user['id']), collectible)
        except ValueError as exc:
            send(chat_id, f'Ошибка: <code>{exc}</code>')
            return
        wake_server()
        send(chat_id, f'Удален collectible username <code>@{collectible}</code>.')
        return

    if command == '/blockchain' and action == 'add':
        if len(parts) != 4:
            send(chat_id, 'Пример: <code>/blockchain add https://t.me/nft/slug EQ...</code>')
            return
        slug = parse_gift_slug(parts[2])
        owner_address = parts[3].strip()
        if not slug:
            send(chat_id, 'Не удалось определить slug подарка из ссылки.')
            return
        if not owner_address:
            send(chat_id, 'TON адрес обязателен.')
            return
        owner = get_saved_gift_owner(conn, slug)
        if not owner:
            send(chat_id, 'Подарок с таким slug не найден в базе.')
            return
        row = upsert_blockchain_gift(conn, slug, owner_address)
        wake_server()
        send(
            chat_id,
            'Для подарка включена TON-плашка\n'
            f'Slug: <code>{slug}</code>\n'
            f'Владелец: <code>{row["owner_address"]}</code>\n'
            f'Telegram: <code>{owner["owner_user_id"]}</code> @{owner.get("username") or ""}\n'
            f'Ссылка: <code>https://t.me/nft/{slug}</code>',
        )
        return

    if command == '/blockchain' and action == 'remove':
        if len(parts) != 3:
            send(chat_id, 'Пример: <code>/blockchain remove https://t.me/nft/slug</code>')
            return
        slug = parse_gift_slug(parts[2])
        if not slug:
            send(chat_id, 'Не удалось определить slug подарка из ссылки.')
            return
        if not remove_blockchain_gift(conn, slug):
            send(chat_id, 'Для этого подарка TON-плашка не найдена.')
            return
        wake_server()
        send(chat_id, f'TON-плашка удалена для подарка <code>{slug}</code>.')
        return

    send(chat_id, 'Неизвестная команда. Напиши /start.')


def poll() -> None:
    offset = 0
    log.info('collectible username bot started')
    while True:
        try:
            updates = api('getUpdates', offset=offset, timeout=30, allowed_updates=json.dumps(['message']))
            for upd in updates:
                offset = max(offset, int(upd['update_id']) + 1)
                msg = upd.get('message')
                if msg:
                    handle_message(msg)
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            log.warning('poll failed: %s', exc)
            time.sleep(3)


if __name__ == '__main__':
    if not TOKEN:
        raise SystemExit('Set MYTELEGRAM_COLLECTIBLE_BOT_TOKEN')
    poll()
