"""Shop WebApp business logic."""
from __future__ import annotations

import json
import math
import os
import random
import secrets
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from pathlib import Path

import config
from db.storage import (
    Storage,
    format_phone_display,
    normalize_phone,
    normalize_collectible_username,
    is_valid_collectible_username,
)
from handlers.phone_countries import anon_phone_lookup_variants, canonical_anon_phone
from shop_webapp.notify import send_code

STARS_PER_XTR = 10_000

STAR_PACKS: dict[str, dict[str, int]] = {
    '10000': {'stars': 10_000, 'xtr': 1},
    '100000': {'stars': 100_000, 'xtr': 10},
    '1000000': {'stars': 1_000_000, 'xtr': 100},
    '10000000': {'stars': 10_000_000, 'xtr': 1000},
}

ANON_PRICES: dict[str, int] = {
    'random_8': 25,
    'random_4': 45,
    'custom_8': 75,
    'custom_4': 100,
}

PREMIUM_SHOP_PLANS: dict[int, dict[str, int]] = {
    1: {'days': 30, 'xtr': 5},
    3: {'days': 90, 'xtr': 15},
    12: {'days': 365, 'xtr': 60},
}

ANON_PHONE_PREFIX = '888'

COLLECTIBLE_USERNAME_MIN_LEN = 4
COLLECTIBLE_USERNAME_MAX_LEN = 12
COLLECTIBLE_USERNAME_PRICES: dict[int, int] = {
    4: 50,
    5: 30,
    6: 25,
    7: 25,
    8: 25,
    9: 25,
    10: 25,
    11: 25,
    12: 25,
}

PROMO_CODES: dict[str, int] = {
    'top': 20,
}

SESSION_TTL = 30 * 86400
CODE_TTL = 300

_TOP_DONORS_VERSION = 0
_DISPLAY_NAME_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_DISPLAY_NAME_TTL = 600.0
_TOP_DONORS_CACHE: dict[str, Any] | None = None
_TOP_DONORS_CACHE_AT = 0.0
_TOP_DONORS_CACHE_TTL = 15.0
_gifts_bot_seeded = False


def _ensure_tables(storage: Storage) -> None:
    c = storage._conn
    c.executescript('''
        CREATE TABLE IF NOT EXISTS shop_webapp_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            telegram_user_id INTEGER NOT NULL,
            phone TEXT NOT NULL DEFAULT '',
            code TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS shop_webapp_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            telegram_user_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shop_star_orders (
            nonce TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            target_user_id INTEGER NOT NULL DEFAULT 0,
            stars INTEGER NOT NULL,
            xtr INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_id TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shop_anon_orders (
            nonce TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            target_user_id INTEGER NOT NULL DEFAULT 0,
            kind TEXT NOT NULL,
            digits TEXT NOT NULL DEFAULT '',
            xtr INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_id TEXT,
            phone TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shop_username_orders (
            nonce TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            target_user_id INTEGER NOT NULL DEFAULT 0,
            username TEXT NOT NULL,
            xtr INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_id TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shop_premium_orders (
            nonce TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            target_user_id INTEGER NOT NULL,
            recipient_username TEXT NOT NULL,
            months INTEGER NOT NULL,
            days INTEGER NOT NULL,
            xtr INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_id TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS anonymous_numbers (
            phone TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
    ''')
    c.commit()
    for stmt in (
        'ALTER TABLE shop_star_orders ADD COLUMN promo TEXT NOT NULL DEFAULT ""',
        'ALTER TABLE shop_anon_orders ADD COLUMN promo TEXT NOT NULL DEFAULT ""',
        'ALTER TABLE shop_star_orders ADD COLUMN refunded_at INTEGER',
        'ALTER TABLE shop_star_orders ADD COLUMN target_user_id INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE shop_anon_orders ADD COLUMN target_user_id INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE shop_star_orders ADD COLUMN buyer_telegram_id INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE shop_anon_orders ADD COLUMN buyer_telegram_id INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE shop_username_orders ADD COLUMN buyer_telegram_id INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE shop_premium_orders ADD COLUMN buyer_telegram_id INTEGER NOT NULL DEFAULT 0',
    ):
        try:
            c.execute(stmt)
        except Exception:
            pass
    c.commit()
    global _gifts_bot_seeded
    if not _gifts_bot_seeded:
        try:
            storage._seed_gifts_bot()
            _gifts_bot_seeded = True
        except Exception:
            pass


_storage_singleton: Storage | None = None
_tables_ready = False


def _open() -> Storage:
    global _storage_singleton, _tables_ready
    if _storage_singleton is not None:
        try:
            _storage_singleton._conn.execute('SELECT 1')
            if not _tables_ready:
                _ensure_tables(_storage_singleton)
                _tables_ready = True
            return _storage_singleton
        except Exception:
            _storage_singleton = None
            _tables_ready = False
    delay = 0.05
    last_exc: Exception | None = None
    for _ in range(12):
        try:
            storage = Storage.open_tool()
            _ensure_tables(storage)
            _storage_singleton = storage
            _tables_ready = True
            return storage
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if 'locked' not in msg and 'busy' not in msg:
                raise
            last_exc = exc
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError('database open failed')


def _new_code() -> str:
    return f'{secrets.randbelow(1000000):06d}'


def _session_token() -> str:
    return secrets.token_hex(32)


def stars_xtr_for_amount(stars: int) -> int:
    if stars < STARS_PER_XTR:
        raise ValueError('Минимум 10 000 звёзд')
    return math.ceil(stars / STARS_PER_XTR)


def _base_xtr_stars(stars: int) -> tuple[int, int] | tuple[None, None]:
    pack = STAR_PACKS.get(str(stars))
    if pack:
        return pack['stars'], pack['xtr']
    if stars < STARS_PER_XTR:
        return None, None
    return stars, stars_xtr_for_amount(stars)


def _apply_promo(base_xtr: int, promo: str) -> tuple[int, str | None, int | None, str | None]:
    promo_norm = (promo or '').strip().lower()
    if not promo_norm:
        return base_xtr, None, None, None
    discount = PROMO_CODES.get(promo_norm)
    if discount is None:
        return base_xtr, None, None, 'Неверный промокод'
    final = max(0, round(base_xtr * (100 - discount) / 100))
    return final, promo_norm, discount, None


def is_valid_shop_collectible_username(username: str) -> bool:
    username = normalize_collectible_username(username)
    if not username or len(username) < COLLECTIBLE_USERNAME_MIN_LEN:
        return False
    if len(username) > COLLECTIBLE_USERNAME_MAX_LEN:
        return False
    if username[-1] == '_':
        return False
    return is_valid_collectible_username(username)


def collectible_username_price_xtr(username: str) -> int | None:
    username = normalize_collectible_username(username)
    if not is_valid_shop_collectible_username(username):
        return None
    length = len(username)
    return COLLECTIBLE_USERNAME_PRICES.get(length)


def validate_promo(
    promo: str,
    *,
    stars: int = 0,
    kind: str = '',
    username: str = '',
) -> dict[str, Any]:
    promo_norm = (promo or '').strip().lower()
    if not promo_norm:
        return {'ok': True, 'applied': False}

    if kind:
        if kind == 'collectible_username':
            base_xtr = collectible_username_price_xtr(username)
            if base_xtr is None:
                return {
                    'ok': False,
                    'error': (
                        f'Username: от {COLLECTIBLE_USERNAME_MIN_LEN} '
                        f'до {COLLECTIBLE_USERNAME_MAX_LEN} символов'
                    ),
                }
            product = 'username'
        elif kind in ANON_PRICES:
            base_xtr = ANON_PRICES[kind]
            product = 'anon'
        else:
            return {'ok': False, 'error': 'Неверный тип товара'}
    else:
        parsed = _base_xtr_stars(int(stars or 0))
        if not parsed[0]:
            return {'ok': False, 'error': 'Минимум 10 000 звёзд'}
        base_xtr = parsed[1]
        product = 'stars'

    final_xtr, code, discount, err = _apply_promo(base_xtr, promo_norm)
    if err:
        return {'ok': False, 'error': err}
    return {
        'ok': True,
        'applied': True,
        'promo': code,
        'discountPercent': discount,
        'originalXtr': base_xtr,
        'finalXtr': final_xtr,
        'product': product,
    }


def catalog() -> dict[str, Any]:
    return {
        'starPacks': [
            {'id': pid, **pack} for pid, pack in STAR_PACKS.items()
        ],
        'anonPrices': ANON_PRICES,
        'anonPricesAreXtr': True,
        'collectibleUsernamePrices': COLLECTIBLE_USERNAME_PRICES,
        'collectibleUsernameMinLen': COLLECTIBLE_USERNAME_MIN_LEN,
        'collectibleUsernameMaxLen': COLLECTIBLE_USERNAME_MAX_LEN,
        'customMinStars': STARS_PER_XTR,
        'starsPerXtr': STARS_PER_XTR,
        'premiumPlans': [
            {'months': months, **plan}
            for months, plan in sorted(PREMIUM_SHOP_PLANS.items())
        ],
    }


def _wake_server() -> None:
    try:
        Path(config.DB_PATH).with_name('push_wake').touch()
    except Exception:
        pass


def resolve_purchase_target(
    buyer_user_id: int,
    target: str = '',
    *,
    telegram_user_id: int = 0,
) -> dict[str, Any]:
    storage = _open()
    raw = (target or '').strip()
    if not raw:
        user = storage.get_user_by_id(int(buyer_user_id))
        if not user:
            return {'ok': False, 'error': 'Пользователь не найден'}
        user_id = int(user['id'])
    elif raw.lstrip('@').replace('_', '').isalnum() and not raw.isdigit():
        username = normalize_collectible_username(raw)
        owner = storage.get_collectible_username_owner(username)
        if owner:
            user = storage.get_user_by_id(int(owner['user_id']))
        else:
            user = storage.get_user_by_username(username)
        if not user:
            return {'ok': False, 'error': 'Аккаунт не найден'}
        user_id = int(user['id'])
    else:
        phone = canonical_anon_phone(normalize_phone(raw))
        if not phone:
            return {'ok': False, 'error': 'Неверный номер или username'}
        user = storage.get_user_by_phone(phone)
        if not user:
            return {'ok': False, 'error': 'Аккаунт с таким номером не найден'}
        user_id = int(user['id'])

    if telegram_user_id > 0 and not _telegram_owns_user(
            storage, telegram_user_id, user_id):
        return {'ok': False, 'error': 'Можно выбрать только свой аккаунт'}
    user = storage.get_user_by_id(user_id)
    return {'ok': True, 'userId': user_id, 'user': user}


def _telegram_owns_user(storage: Storage, telegram_user_id: int,
                        user_id: int) -> bool:
    user = storage.get_user_by_id(int(user_id))
    if not user:
        return False
    phone = (user.get('phone') or '').strip()
    if phone:
        tg = storage.get_telegram_user_by_phone(phone)
        if tg and int(tg) == int(telegram_user_id):
            return True
    row = storage._conn.execute(
        '''SELECT 1 FROM shop_webapp_sessions
           WHERE telegram_user_id=? AND user_id=? LIMIT 1''',
        (int(telegram_user_id), int(user_id)),
    ).fetchone()
    return bool(row)


def _phone_option_subtitle(user: dict | None, *, current: bool) -> str:
    if not user:
        return 'Сначала войдите с этим номером'
    parts: list[str] = []
    if current:
        parts.append('Текущий номер')
    name = (user.get('first_name') or '').strip()
    if name:
        parts.append(name)
    uname = (user.get('username') or '').strip()
    if uname:
        parts.append(f'@{uname}')
    return ' · '.join(parts) or 'Аккаунт по этому номеру'


def list_purchase_targets(token: str) -> dict[str, Any]:
    sess = get_session(token)
    if not sess.get('ok'):
        return sess
    storage = _open()
    current_user_id = int(sess['user']['id'])
    telegram_user_id = int(sess.get('telegramUserId') or 0)
    if telegram_user_id <= 0:
        return {'ok': False, 'error': 'Неверный Telegram ID'}

    phones_seen: set[str] = set()
    options: list[dict[str, Any]] = []

    def add_phone_option(phone: str) -> None:
        phone = normalize_phone(phone)
        if not phone or phone in phones_seen:
            return
        phones_seen.add(phone)
        user = storage.get_user_by_phone(phone)
        current = bool(user and int(user['id']) == current_user_id)
        options.append({
            'value': phone,
            'userId': int(user['id']) if user else 0,
            'kind': 'phone',
            'title': format_phone_display(phone),
            'subtitle': _phone_option_subtitle(user, current=current),
            'current': current,
            'registered': bool(user),
        })

    current = storage.get_user_by_id(current_user_id)
    if current and current.get('phone'):
        add_phone_option(current['phone'])

    for row in storage.list_bot_phones_for_telegram(telegram_user_id):
        add_phone_option(row['phone'])

    for row in storage._conn.execute(
        'SELECT phone FROM anonymous_numbers WHERE user_id=?',
        (current_user_id,),
    ).fetchall():
        add_phone_option(row['phone'])

    options.sort(key=lambda item: (not item['current'], item['title']))
    if not options and current and current.get('phone'):
        add_phone_option(current['phone'])

    return {
        'ok': True,
        'options': options,
        'currentUserId': current_user_id,
    }


def send_register_code(telegram_user_id: int) -> dict[str, Any]:
    if telegram_user_id <= 0:
        return {'ok': False, 'error': 'Неверный Telegram ID'}
    storage = _open()
    phone = storage.assign_bot_phone(telegram_user_id)
    code = _new_code()
    expires = int(time.time()) + CODE_TTL
    storage._conn.execute(
        'DELETE FROM shop_webapp_codes WHERE kind=? AND telegram_user_id=?',
        ('register', telegram_user_id),
    )
    storage._conn.execute(
        '''INSERT INTO shop_webapp_codes
           (kind, telegram_user_id, phone, code, expires_at)
           VALUES (?,?,?,?,?)''',
        ('register', telegram_user_id, phone, code, expires),
    )
    storage._conn.commit()
    sent = send_code(telegram_user_id, code, purpose='регистрации')
    return {
        'ok': True,
        'phone': format_phone_display(phone),
        'codeSent': sent,
    }


def complete_register(
    telegram_user_id: int,
    code: str,
    first_name: str,
    last_name: str = '',
) -> dict[str, Any]:
    if telegram_user_id <= 0:
        return {'ok': False, 'error': 'Неверный Telegram ID'}
    first_name = (first_name or '').strip()
    if not first_name:
        return {'ok': False, 'error': 'Введите имя'}
    storage = _open()
    row = storage._conn.execute(
        '''SELECT * FROM shop_webapp_codes
           WHERE kind=? AND telegram_user_id=? AND code=? AND used=0
             AND expires_at>?''',
        ('register', telegram_user_id, code.strip(), int(time.time())),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Неверный или просроченный код'}
    phone = row['phone']
    storage._conn.execute(
        'UPDATE shop_webapp_codes SET used=1 WHERE id=?', (row['id'],),
    )
    storage._conn.commit()

    user = storage.get_user_by_phone(phone)
    if not user:
        user = storage.create_user(phone, first_name, (last_name or '').strip())
    else:
        storage._conn.execute(
            'UPDATE users SET first_name=?, last_name=? WHERE id=?',
            (first_name, (last_name or '').strip(), user['id']),
        )
        storage._conn.commit()
        user = storage.get_user_by_id(user['id'])

    storage.assign_bot_phone(telegram_user_id, phone=phone, source='register')
    storage.sync_telegram_phone_links(telegram_user_id)
    token = _create_session(storage, int(user['id']), telegram_user_id)
    return {'ok': True, 'token': token, 'user': _profile(storage, user)}


def get_assigned_phone(telegram_user_id: int) -> dict[str, Any]:
    if telegram_user_id <= 0:
        return {'ok': False, 'error': 'Неверный Telegram ID'}
    storage = _open()
    rows = storage.list_bot_phones_for_telegram(telegram_user_id)
    if not rows:
        return {'ok': False, 'error': 'Напишите /start боту, чтобы получить номер'}
    numbers = [
        {
            'phone': format_phone_display(r['phone']),
            'phoneDigits': r['phone'],
            'source': r.get('source') or 'auto',
        }
        for r in rows
    ]
    primary = numbers[0]
    return {
        'ok': True,
        'phone': primary['phone'],
        'phoneDigits': primary['phoneDigits'],
        'numbers': numbers,
    }


def list_my_phones(telegram_user_id: int = 0, token: str = '') -> dict[str, Any]:
    storage = _open()
    user_id = 0
    if token:
        sess = get_session(token)
        if not sess.get('ok'):
            return sess
        user_id = int(sess['user']['id'])
        if not telegram_user_id:
            telegram_user_id = int(sess.get('telegramUserId') or 0)
    if telegram_user_id <= 0:
        return {'ok': False, 'error': 'Неверный Telegram ID'}

    seen: set[str] = set()
    numbers: list[dict[str, Any]] = []

    for row in storage.list_bot_phones_for_telegram(telegram_user_id):
        phone = row['phone']
        if phone in seen:
            continue
        seen.add(phone)
        numbers.append({
            'phone': format_phone_display(phone),
            'phoneDigits': phone,
            'source': _phone_source_label(row.get('source') or 'auto'),
        })

    if user_id:
        anon_rows = storage._conn.execute(
            '''SELECT phone, kind FROM anonymous_numbers
               WHERE user_id=? ORDER BY created_at DESC''',
            (user_id,),
        ).fetchall()
        for row in anon_rows:
            phone = row['phone']
            if phone in seen:
                continue
            seen.add(phone)
            numbers.append({
                'phone': format_phone_display(phone),
                'phoneDigits': phone,
                'source': _phone_source_label(row['kind'] or 'anon'),
            })

    return {'ok': True, 'numbers': numbers}


def _phone_source_label(source: str) -> str:
    labels = {
        'auto': 'Авто',
        'admin': 'Выдан',
        'random_8': 'KoalGram случайный',
        'random_4': 'KoalGram короткий',
        'custom_8': 'KoalGram свой',
        'custom_4': 'KoalGram свой короткий',
    }
    return labels.get(source, source or 'Номер')


def send_login_code(phone_raw: str, telegram_user_id: int = 0) -> dict[str, Any]:
    phone = canonical_anon_phone(normalize_phone(phone_raw))
    if not phone:
        return {'ok': False, 'error': 'Неверный номер'}
    storage = _open()

    linked_tg = None
    resolved_phone = phone
    for variant in anon_phone_lookup_variants(phone):
        linked_tg = storage.get_telegram_user_by_phone(variant)
        if linked_tg:
            resolved_phone = variant
            break
    phone = resolved_phone
    if not linked_tg:
        return {
            'ok': False,
            'error': 'Номер не привязан к боту. Напишите /start боту.',
        }
    if telegram_user_id > 0 and linked_tg != telegram_user_id:
        return {'ok': False, 'error': 'Это не ваш номер'}

    user = storage.get_user_by_phone(phone)
    if user and storage.is_user_banned(int(user['id'])):
        if storage.is_ban_blocking_recreate(int(user['id'])):
            return {'ok': False, 'error': 'Номер заблокирован'}

    code = _new_code()
    expires = int(time.time()) + CODE_TTL
    storage._conn.execute(
        'DELETE FROM shop_webapp_codes WHERE kind=? AND phone=?',
        ('login', phone),
    )
    storage._conn.execute(
        '''INSERT INTO shop_webapp_codes
           (kind, telegram_user_id, phone, code, expires_at)
           VALUES (?,?,?,?,?)''',
        ('login', linked_tg, phone, code, expires),
    )
    storage._conn.commit()
    sent = send_code(linked_tg, code, purpose='входа')
    return {'ok': True, 'codeSent': sent}


def complete_login(phone_raw: str, code: str) -> dict[str, Any]:
    phone = normalize_phone(phone_raw)
    storage = _open()
    row = storage._conn.execute(
        '''SELECT * FROM shop_webapp_codes
           WHERE kind=? AND phone=? AND code=? AND used=0 AND expires_at>?''',
        ('login', phone, code.strip(), int(time.time())),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Неверный или просроченный код'}
    user = storage.get_user_by_phone(phone)
    if not user:
        user = storage.create_user(phone, 'User', '')
    elif storage.is_user_banned(int(user['id'])):
        if storage.is_ban_blocking_recreate(int(user['id'])):
            return {'ok': False, 'error': 'Номер заблокирован'}
        user = storage.recreate_banned_user(phone, 'User', '')
    storage._conn.execute(
        'UPDATE shop_webapp_codes SET used=1 WHERE id=?', (row['id'],),
    )
    storage._conn.commit()
    tg_id = int(row['telegram_user_id'])
    storage.assign_bot_phone(tg_id, phone=phone, source='login')
    storage.sync_telegram_phone_links(tg_id)
    token = _create_session(storage, int(user['id']), tg_id)
    return {'ok': True, 'token': token, 'user': _profile(storage, user)}


def _create_session(storage: Storage, user_id: int,
                    telegram_user_id: int) -> str:
    token = _session_token()
    now = int(time.time())
    storage._conn.execute(
        '''INSERT INTO shop_webapp_sessions
           (token, user_id, telegram_user_id, created_at, expires_at)
           VALUES (?,?,?,?,?)''',
        (token, user_id, telegram_user_id, now, now + SESSION_TTL),
    )
    storage._conn.commit()
    return token


def _profile(storage: Storage, user: dict) -> dict[str, Any]:
    uid = int(user['id'])
    return {
        'id': uid,
        'firstName': user.get('first_name') or '',
        'lastName': user.get('last_name') or '',
        'username': user.get('username') or None,
        'phone': format_phone_display(user.get('phone') or ''),
        'stars': storage.get_stars_balance(uid),
    }


def get_session(token: str) -> dict[str, Any]:
    if not token:
        return {'ok': False, 'error': 'Не авторизован'}
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_webapp_sessions WHERE token=? AND expires_at>?',
        (token, int(time.time())),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Сессия истекла'}
    user = storage.get_user_by_id(int(row['user_id']))
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    return {'ok': True, 'user': _profile(storage, user), 'telegramUserId': int(row['telegram_user_id'])}


def create_star_order(user_id: int, stars: int, promo: str = '',
                      target_user_id: int = 0) -> dict[str, Any]:
    parsed = _base_xtr_stars(int(stars))
    if not parsed[0]:
        return {'ok': False, 'error': 'Минимум 10 000 звёзд'}
    amount, base_xtr = parsed
    final_xtr, promo_code, discount, err = _apply_promo(base_xtr, promo)
    if err:
        return {'ok': False, 'error': err}
    storage = _open()
    target = int(target_user_id or user_id)
    target_user = storage.get_user_by_id(target)
    if not target_user:
        return {'ok': False, 'error': 'Получатель не найден'}
    balance = storage.get_stars_balance(target)
    nonce = secrets.token_hex(16)
    storage._conn.execute(
        '''INSERT INTO shop_star_orders
           (nonce, user_id, target_user_id, stars, xtr, promo, created_at)
           VALUES (?,?,?,?,?,?,?)''',
        (nonce, user_id, target, amount, final_xtr, promo_code or '',
         int(time.time())),
    )
    storage._conn.commit()
    return {
        'ok': True,
        'nonce': nonce,
        'stars': amount,
        'xtr': final_xtr,
        'originalXtr': base_xtr,
        'promo': promo_code,
        'discountPercent': discount,
        'balance': balance,
        'targetUserId': target,
        'payload': f'shop_stars:{user_id}:{amount}:{nonce}',
    }


def complete_star_order(
    payload: str,
    payment_id: str,
    buyer_telegram_id: int = 0,
) -> dict[str, Any]:
    parts = (payload or '').split(':')
    if len(parts) != 4 or parts[0] != 'shop_stars':
        return {'ok': False, 'error': 'Неверный payload'}
    try:
        user_id = int(parts[1])
        stars = int(parts[2])
        nonce = parts[3]
    except ValueError:
        return {'ok': False, 'error': 'Неверный payload'}
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_star_orders WHERE nonce=?', (nonce,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    if row['status'] == 'paid':
        return {'ok': True, 'already': True, 'balance': storage.get_stars_balance(user_id)}
    if int(row['user_id']) != user_id or int(row['stars']) != stars:
        return {'ok': False, 'error': 'Несовпадение заказа'}
    target_user_id = int(row['target_user_id'] or row['user_id'])
    expected_xtr = int(row['xtr'])
    payer_tid = int(buyer_telegram_id or 0)
    storage._conn.execute(
        '''UPDATE shop_star_orders
           SET status=?, payment_id=?, buyer_telegram_id=?
           WHERE nonce=?''',
        ('paid', payment_id, payer_tid, nonce),
    )
    storage._conn.commit()
    new_balance = storage.adjust_stars_balance(
        target_user_id, stars,
        title=f'{stars} звёзд',
        peer_user_id=config.SERVICE_USER_ID,
    )
    _bump_top_donors_version()
    return {
        'ok': True,
        'balance': new_balance,
        'stars': stars,
        'targetUserId': target_user_id,
    }


def validate_star_order(payload: str, xtr_amount: int) -> dict[str, Any]:
    parts = (payload or '').split(':')
    if len(parts) != 4 or parts[0] != 'shop_stars':
        return {'ok': False, 'error': 'Неверный payload'}
    storage = _open()
    nonce = parts[3]
    row = storage._conn.execute(
        'SELECT * FROM shop_star_orders WHERE nonce=?', (nonce,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    if row['status'] != 'pending':
        return {'ok': False, 'error': 'Заказ уже обработан'}
    if int(row['xtr']) != int(xtr_amount):
        return {'ok': False, 'error': 'Неверная сумма'}
    return {'ok': True}


def validate_payment(payload: str, xtr_amount: int) -> dict[str, Any]:
    if (payload or '').startswith('shop_anon:'):
        return validate_anon_order(payload, xtr_amount)
    if (payload or '').startswith('shop_username:'):
        return validate_username_order(payload, xtr_amount)
    if (payload or '').startswith('shop_premium:'):
        return validate_premium_order(payload, xtr_amount)
    if (payload or '').startswith('shop_case:'):
        from shop_webapp import cases
        return cases.validate_case_order(payload, xtr_amount)
    return validate_star_order(payload, xtr_amount)


def complete_payment(
    payload: str,
    payment_id: str,
    buyer_telegram_id: int = 0,
) -> dict[str, Any]:
    if (payload or '').startswith('shop_anon:'):
        return complete_anon_order(payload, payment_id, buyer_telegram_id)
    if (payload or '').startswith('shop_username:'):
        return complete_username_order(payload, payment_id, buyer_telegram_id)
    if (payload or '').startswith('shop_premium:'):
        return complete_premium_order(payload, payment_id, buyer_telegram_id)
    if (payload or '').startswith('shop_case:'):
        from shop_webapp import cases
        return cases.complete_case_order(payload, payment_id, buyer_telegram_id)
    return complete_star_order(payload, payment_id, buyer_telegram_id)


def _telegram_id_for_shop_user(storage: Storage, user_id: int) -> int | None:
    row = storage._conn.execute(
        '''SELECT telegram_user_id FROM shop_webapp_sessions
           WHERE user_id=? ORDER BY created_at DESC LIMIT 1''',
        (user_id,),
    ).fetchone()
    if row:
        return int(row['telegram_user_id'])
    user = storage.get_user_by_id(user_id)
    if not user:
        return None
    phone = (user.get('phone') or '').strip()
    if phone:
        tg = storage.get_telegram_user_by_phone(phone)
        if tg:
            return int(tg)
    row = storage._conn.execute(
        '''SELECT bpa.telegram_user_id
           FROM bot_phone_assignments bpa
           JOIN users u ON u.phone = bpa.phone
           WHERE u.id=?
           ORDER BY bpa.created_at DESC LIMIT 1''',
        (user_id,),
    ).fetchone()
    if row:
        return int(row['telegram_user_id'])
    return None


def _bump_top_donors_version() -> None:
    global _TOP_DONORS_VERSION, _TOP_DONORS_CACHE, _TOP_DONORS_CACHE_AT
    _TOP_DONORS_VERSION += 1
    _TOP_DONORS_CACHE = None
    _TOP_DONORS_CACHE_AT = 0.0


def bump_top_donors_version() -> None:
    """Notify top-donors consumers (webapp polling) that rankings changed."""
    _bump_top_donors_version()


def _shop_bot_token() -> str:
    return (
        os.environ.get('SHOP_BOT_TOKEN', '').strip()
        or config.AUTH_BOT_TOKEN
        or ''
    )


def _frame_bot_token() -> str:
    token = (os.environ.get('FRAME_BOT_TOKEN', '') or '').strip()
    if token:
        return token
    frame_env = os.path.join(os.path.dirname(config.__file__), '.telegram_frame_bot.env')
    if os.path.isfile(frame_env):
        try:
            for line in open(frame_env, encoding='utf-8'):
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                if key.strip() == 'FRAME_BOT_TOKEN':
                    token = val.strip().strip('"').strip("'")
                    if token:
                        return token
        except OSError:
            pass
    # Match /home/user/frag/frame.py fallback used by the live Frame bot.
    frame_py = '/home/user/frag/frame.py'
    if os.path.isfile(frame_py):
        try:
            import re
            text = open(frame_py, encoding='utf-8').read()
            match = re.search(
                r'FRAME_BOT_TOKEN\",\s*\"([^\"]+)\"',
                text,
            )
            if match:
                return match.group(1).strip()
        except OSError:
            pass
    return ''


def _tg_bot_api_token(token: str, method: str, **params: Any) -> dict[str, Any]:
    token = (token or '').strip()
    if not token:
        return {}
    url = f'https://api.telegram.org/bot{token}/{method}'
    if params:
        data = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None},
        ).encode()
        req = urllib.request.Request(url, data=data, method='POST')
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tg_bot_api(method: str, **params: Any) -> dict[str, Any]:
    return _tg_bot_api_token(_shop_bot_token(), method, **params)


def _telegram_display_name(telegram_user_id: int) -> dict[str, Any]:
    tid = int(telegram_user_id)
    now = time.time()
    cached = _DISPLAY_NAME_CACHE.get(tid)
    if cached and now - cached[0] < _DISPLAY_NAME_TTL:
        return cached[1]
    data = _tg_bot_api('getChat', chat_id=tid)
    chat = data.get('result') if data.get('ok') else None
    if not isinstance(chat, dict):
        result = {
            'displayName': f'ID {tid}',
            'username': None,
            'hasPhoto': False,
        }
    else:
        first = (chat.get('first_name') or '').strip()
        last = (chat.get('last_name') or '').strip()
        username = (chat.get('username') or '').strip() or None
        display = ' '.join(x for x in (first, last) if x)
        if username:
            display = display or f'@{username}'
        if not display:
            display = f'ID {tid}'
        result = {
            'displayName': display,
            'username': username,
            'hasPhoto': bool(chat.get('photo')),
        }
    _DISPLAY_NAME_CACHE[tid] = (now, result)
    return result


def _is_real_telegram_star_payment(payment_id: str | None) -> bool:
    ref = (payment_id or '').strip()
    return ref.startswith('stx')


def _payer_telegram_id_for_order(
    storage: Storage,
    user_id: int,
    created_at: int,
    buyer_telegram_id: int = 0,
) -> int | None:
    tid = int(buyer_telegram_id or 0)
    uid = int(user_id)
    if tid > 0 and _telegram_owns_user(storage, tid, uid):
        return tid
    row = storage._conn.execute(
        '''SELECT telegram_user_id FROM shop_webapp_sessions
           WHERE user_id=? AND created_at <= ?
           ORDER BY created_at DESC LIMIT 1''',
        (uid, int(created_at or 0)),
    ).fetchone()
    if row:
        session_tid = int(row['telegram_user_id'])
        if _telegram_owns_user(storage, session_tid, uid):
            return session_tid
    row = storage._conn.execute(
        '''SELECT telegram_user_id FROM shop_webapp_sessions
           WHERE user_id=? ORDER BY created_at DESC LIMIT 1''',
        (uid,),
    ).fetchone()
    if row:
        session_tid = int(row['telegram_user_id'])
        if _telegram_owns_user(storage, session_tid, uid):
            return session_tid
    return None


def _transaction_payer_telegram_id(
    storage: Storage,
    user_id: int,
    created_at: int,
    buyer_telegram_id: int = 0,
) -> int | None:
    """Resolve payer for a paid order: actual Telegram buyer first, session fallback."""
    tid = int(buyer_telegram_id or 0)
    if tid > 0:
        return tid
    return _payer_telegram_id_for_order(
        storage,
        int(user_id),
        int(created_at or 0),
    )


def _fetch_bot_star_transactions(token: str) -> list[dict[str, Any]]:
    """Fetch every Star transaction for a bot via Telegram getStarTransactions."""
    token = (token or '').strip()
    if not token:
        return []
    transactions: list[dict[str, Any]] = []
    offset = 0
    while True:
        data = _tg_bot_api_token(
            token,
            'getStarTransactions',
            offset=offset,
            limit=100,
        )
        if not data.get('ok'):
            break
        chunk = ((data.get('result') or {}).get('transactions') or [])
        if not isinstance(chunk, list) or not chunk:
            break
        for item in chunk:
            if isinstance(item, dict):
                transactions.append(item)
        if len(chunk) < 100:
            break
        offset += len(chunk)
        if offset > 50_000:
            break
    return transactions


def _net_donations_from_star_transactions(
    transactions: list[dict[str, Any]],
) -> dict[int, int]:
    """Net Telegram Stars paid to the bot by each user (payments minus refunds)."""
    totals: dict[int, int] = {}
    for tx in transactions:
        amount = int(tx.get('amount') or 0)
        if amount <= 0:
            continue
        source = tx.get('source') if isinstance(tx.get('source'), dict) else {}
        receiver = tx.get('receiver') if isinstance(tx.get('receiver'), dict) else {}
        if source.get('type') == 'user':
            user = source.get('user') if isinstance(source.get('user'), dict) else {}
            tid = int(user.get('id') or 0)
            if tid > 0:
                totals[tid] = totals.get(tid, 0) + amount
        if receiver.get('type') == 'user':
            user = receiver.get('user') if isinstance(receiver.get('user'), dict) else {}
            tid = int(user.get('id') or 0)
            if tid > 0:
                totals[tid] = totals.get(tid, 0) - amount
    return {tid: amount for tid, amount in totals.items() if amount > 0}


def _collect_top_donor_totals_from_db(storage: Storage) -> dict[int, int]:
    """Fallback: sum paid stx orders from local DB when Bot API is unavailable."""
    from shop_webapp.cases import ensure_cases_schema
    ensure_cases_schema(storage)
    totals: dict[int, int] = {}

    def _add(telegram_user_id: int | None, amount: int) -> None:
        if not telegram_user_id or amount <= 0:
            return
        tid = int(telegram_user_id)
        totals[tid] = totals.get(tid, 0) + int(amount)

    shop_query = '''
        SELECT user_id, xtr, created_at, COALESCE(buyer_telegram_id, 0) AS buyer_telegram_id
        FROM {table}
        WHERE status='paid' AND payment_id LIKE 'stx%'
    '''
    for table in (
        'shop_star_orders',
        'shop_anon_orders',
        'shop_username_orders',
        'shop_premium_orders',
    ):
        for row in storage._conn.execute(shop_query.format(table=table)):
            payer = _transaction_payer_telegram_id(
                storage,
                int(row['user_id']),
                int(row['created_at'] or 0),
                int(row['buyer_telegram_id'] or 0),
            )
            _add(payer, int(row['xtr']))

    for row in storage._conn.execute(
        '''SELECT o.user_id, o.xtr, o.created_at,
                  COALESCE(o.buyer_telegram_id, 0) AS buyer_telegram_id
           FROM shop_case_orders o
           WHERE o.status='paid'
             AND o.currency='xtr'
             AND o.payment_id LIKE 'stx%' ''',
    ):
        payer = _transaction_payer_telegram_id(
            storage,
            int(row['user_id']),
            int(row['created_at'] or 0),
            int(row['buyer_telegram_id'] or 0),
        )
        _add(payer, int(row['xtr'] or 0))

    for row in storage._conn.execute(
        '''SELECT buyer_telegram_id, stars_amount
           FROM frame_verification_orders
           WHERE status='completed'
             AND COALESCE(refunded_at, 0) = 0
             AND buyer_telegram_id > 0
             AND stars_amount > 0
             AND telegram_payment_id LIKE 'stx%' ''',
    ):
        _add(int(row['buyer_telegram_id']), int(row['stars_amount']))
    return totals


def _collect_top_donor_totals(storage: Storage) -> dict[int, int]:
    """Sum every real Telegram Stars payment from AirAuthBot + Frame bot.

    Source of truth is Telegram Bot API getStarTransactions for both bots.
    Local DB is used only when API tokens/responses are unavailable.
    """
    global _TOP_DONORS_CACHE, _TOP_DONORS_CACHE_AT
    now = time.time()
    if (
        _TOP_DONORS_CACHE is not None
        and now - _TOP_DONORS_CACHE_AT < _TOP_DONORS_CACHE_TTL
    ):
        cached = _TOP_DONORS_CACHE.get('totals')
        if isinstance(cached, dict):
            return {int(k): int(v) for k, v in cached.items()}

    auth_token = _shop_bot_token()
    frame_token = _frame_bot_token()
    auth_txs = _fetch_bot_star_transactions(auth_token) if auth_token else []
    frame_txs = _fetch_bot_star_transactions(frame_token) if frame_token else []
    auth_totals = _net_donations_from_star_transactions(auth_txs)
    frame_totals = _net_donations_from_star_transactions(frame_txs)

    if auth_txs or frame_txs:
        totals: dict[int, int] = {}
        for part in (auth_totals, frame_totals):
            for tid, amount in part.items():
                totals[tid] = totals.get(tid, 0) + int(amount)
        source = 'api'
        shop_xtr = sum(auth_totals.values())
        frame_xtr = sum(frame_totals.values())
        shop_orders = sum(
            1 for tx in auth_txs
            if int(tx.get('amount') or 0) > 0
            and isinstance(tx.get('source'), dict)
            and tx['source'].get('type') == 'user'
        )
        frame_orders = sum(
            1 for tx in frame_txs
            if int(tx.get('amount') or 0) > 0
            and isinstance(tx.get('source'), dict)
            and tx['source'].get('type') == 'user'
        )
    else:
        totals = _collect_top_donor_totals_from_db(storage)
        source = 'db'
        shop_orders, shop_xtr = _shop_paid_xtr_totals(storage)
        frame_orders, frame_xtr = _frame_paid_xtr_totals(storage)

    _TOP_DONORS_CACHE = {
        'totals': totals,
        'source': source,
        'shopOrders': shop_orders,
        'shopTelegramStars': shop_xtr,
        'frameOrders': frame_orders,
        'frameTelegramStars': frame_xtr,
    }
    _TOP_DONORS_CACHE_AT = now
    return dict(totals)


def _shop_paid_xtr_totals(storage: Storage) -> tuple[int, int]:
    row = storage._conn.execute(
        '''SELECT COUNT(*), COALESCE(SUM(xtr), 0) FROM (
               SELECT xtr FROM shop_star_orders
               WHERE status='paid' AND payment_id LIKE 'stx%'
               UNION ALL
               SELECT xtr FROM shop_anon_orders
               WHERE status='paid' AND payment_id LIKE 'stx%'
               UNION ALL
               SELECT xtr FROM shop_username_orders
               WHERE status='paid' AND payment_id LIKE 'stx%'
               UNION ALL
               SELECT xtr FROM shop_premium_orders
               WHERE status='paid' AND payment_id LIKE 'stx%'
               UNION ALL
               SELECT xtr FROM shop_case_orders
               WHERE status='paid' AND currency='xtr' AND payment_id LIKE 'stx%'
           )''',
    ).fetchone()
    return int(row[0] or 0), int(row[1] or 0)


def _frame_paid_xtr_totals(storage: Storage) -> tuple[int, int]:
    row = storage._conn.execute(
        '''SELECT COUNT(*), COALESCE(SUM(stars_amount), 0)
           FROM frame_verification_orders
           WHERE status='completed'
             AND COALESCE(refunded_at, 0) = 0
             AND buyer_telegram_id > 0
             AND stars_amount > 0
             AND telegram_payment_id LIKE 'stx%' ''',
    ).fetchone()
    return int(row[0] or 0), int(row[1] or 0)


def _backfill_shop_buyer_telegram_ids(storage: Storage) -> int:
    updated = 0
    shop_query = '''
        SELECT nonce, user_id, created_at
        FROM {table}
        WHERE status='paid'
          AND payment_id LIKE 'stx%'
          AND COALESCE(buyer_telegram_id, 0) = 0
    '''
    for table in (
        'shop_star_orders',
        'shop_anon_orders',
        'shop_username_orders',
        'shop_premium_orders',
    ):
        for row in storage._conn.execute(shop_query.format(table=table)):
            payer = _payer_telegram_id_for_order(
                storage,
                int(row['user_id']),
                int(row['created_at'] or 0),
            )
            if not payer:
                continue
            storage._conn.execute(
                f'''UPDATE {table}
                    SET buyer_telegram_id=?
                    WHERE nonce=?''',
                (payer, row['nonce']),
            )
            updated += 1
    return updated


def _backfill_case_buyer_telegram_ids(storage: Storage) -> int:
    updated = 0
    for row in storage._conn.execute(
        '''SELECT nonce, user_id, created_at
           FROM shop_case_orders
           WHERE status='paid'
             AND payment_id LIKE 'airgram_balance:%'
             AND COALESCE(buyer_telegram_id, 0) = 0''',
    ):
        payer = _payer_telegram_id_for_order(
            storage,
            int(row['user_id']),
            int(row['created_at'] or 0),
        )
        if not payer:
            continue
        storage._conn.execute(
            '''UPDATE shop_case_orders
               SET buyer_telegram_id=?
               WHERE nonce=?''',
            (payer, row['nonce']),
        )
        updated += 1
    return updated


def rebuild_top_donors() -> dict[str, Any]:
    """Backfill payer telegram ids and refresh top-donors cache version."""
    storage = _open()
    _ensure_tables(storage)
    from shop_webapp.cases import ensure_cases_schema
    ensure_cases_schema(storage)
    admin_xtr_cleared = storage._conn.execute(
        '''UPDATE shop_case_orders
           SET xtr = 0
           WHERE status='paid'
             AND (
               payment_id = 'admin_case_free'
               OR payment_id LIKE 'airgram_balance:%'
             )''',
    ).rowcount
    backfilled = _backfill_shop_buyer_telegram_ids(storage)
    case_buyers = _backfill_case_buyer_telegram_ids(storage)
    storage._conn.commit()
    totals = _collect_top_donor_totals(storage)
    _bump_top_donors_version()
    shop_orders, shop_xtr = _shop_paid_xtr_totals(storage)
    frame_orders, frame_xtr = _frame_paid_xtr_totals(storage)
    return {
        'ok': True,
        'version': _TOP_DONORS_VERSION,
        'buyerTelegramIdsBackfilled': int(backfilled or 0),
        'caseBuyerTelegramIdsBackfilled': int(case_buyers or 0),
        'adminCaseXtrCleared': int(admin_xtr_cleared or 0),
        'donors': len(totals),
        'totalTelegramStars': sum(totals.values()),
        'stats': {
            'shopOrders': shop_orders,
            'shopTelegramStars': shop_xtr,
            'frameOrders': frame_orders,
            'frameTelegramStars': frame_xtr,
            'totalTelegramStars': shop_xtr + frame_xtr,
        },
    }


def _registered_telegram_user_ids(storage: Storage) -> list[int]:
    rows = storage._conn.execute(
        '''SELECT DISTINCT telegram_user_id AS tid FROM (
               SELECT telegram_user_id FROM bot_phone_assignments
               WHERE telegram_user_id > 0
               UNION
               SELECT telegram_user_id FROM telegram_phones
               WHERE telegram_user_id > 0
           )
           ORDER BY tid''',
    ).fetchall()
    return [int(row['tid']) for row in rows]


def _rank_donors(totals: dict[int, int]) -> list[tuple[int, int, int]]:
    """Return donors as (rank, telegram_user_id, telegram_stars), richest first."""
    ranked = sorted(
        ((tid, amount) for tid, amount in totals.items() if int(amount) > 0),
        key=lambda item: (-int(item[1]), int(item[0])),
    )
    return [
        (index, int(tid), int(amount))
        for index, (tid, amount) in enumerate(ranked, 1)
    ]


def _rank_messenger_users(
    storage: Storage,
    totals: dict[int, int],
) -> list[tuple[int, int, int]]:
    """Rank every messenger Telegram user by donated XTR (zeros included)."""
    registered = set(_registered_telegram_user_ids(storage))
    # Donors who paid but are not in messenger registry still get a place.
    for tid in totals:
        if int(totals.get(tid, 0)) > 0:
            registered.add(int(tid))
    ranked = sorted(
        registered,
        key=lambda tid: (-int(totals.get(tid, 0)), int(tid)),
    )
    return [
        (index, int(tid), int(totals.get(tid, 0)))
        for index, tid in enumerate(ranked, 1)
    ]


def get_top_donors(telegram_user_id: int = 0) -> dict[str, Any]:
    """Top by each person's donated Stars; rank among all messenger users."""
    storage = _open()
    # telegram_user_id -> sum of THAT person's payments in both bots
    personal_totals = _collect_top_donor_totals(storage)
    # Rank every messenger user by their personal donation sum (0 included)
    ranked_all = _rank_messenger_users(storage, personal_totals)
    ranked_donors = _rank_donors(personal_totals)
    rank_by_tid = {
        tid: (amount, rank)
        for rank, tid, amount in ranked_all
    }
    donors: list[dict[str, Any]] = []
    for rank, tid, amount in ranked_donors[:3]:
        # Place in the full messenger ranking (same order: donors first)
        full_rank = rank_by_tid.get(tid, (amount, rank))[1]
        tg_profile = _telegram_display_name(tid)
        donors.append({
            'rank': full_rank,
            'telegramUserId': tid,
            'telegramStars': amount,
            'stars': amount,
            'displayName': tg_profile['displayName'],
            'username': tg_profile.get('username'),
            'avatarUrl': f'/api/avatar/{tid}',
        })
    total_users = len(ranked_all)
    total_donors = len(ranked_donors)
    registered_ids = {tid for _, tid, _ in ranked_all}
    viewer = None
    if telegram_user_id:
        tid = int(telegram_user_id)
        if tid in rank_by_tid:
            personal_sum, rank = rank_by_tid[tid]
        else:
            personal_sum = int(personal_totals.get(tid, 0))
            rank = None
        tg_profile = _telegram_display_name(tid)
        viewer = {
            'telegramUserId': tid,
            'telegramStars': personal_sum,
            'stars': personal_sum,
            'rank': rank,
            'registered': tid in registered_ids,
            'totalUsers': total_users,
            'totalDonors': total_donors,
            'displayName': tg_profile['displayName'],
            'username': tg_profile.get('username'),
            'avatarUrl': f'/api/avatar/{tid}',
        }
    cached = _TOP_DONORS_CACHE if isinstance(_TOP_DONORS_CACHE, dict) else {}
    shop_paid_count = int(cached.get('shopOrders') or 0)
    shop_paid_xtr = int(cached.get('shopTelegramStars') or 0)
    frame_paid_count = int(cached.get('frameOrders') or 0)
    frame_paid_xtr = int(cached.get('frameTelegramStars') or 0)
    if not cached:
        shop_paid_count, shop_paid_xtr = _shop_paid_xtr_totals(storage)
        frame_paid_count, frame_paid_xtr = _frame_paid_xtr_totals(storage)
    return {
        'ok': True,
        'version': _TOP_DONORS_VERSION,
        'donors': donors,
        'viewer': viewer,
        'stats': {
            'shopOrders': shop_paid_count,
            'shopTelegramStars': shop_paid_xtr,
            'frameOrders': frame_paid_count,
            'frameTelegramStars': frame_paid_xtr,
            'totalTelegramStars': sum(personal_totals.values()),
            'registeredUsers': total_users,
            'totalDonors': total_donors,
            'source': cached.get('source') or 'db',
        },
    }


def get_star_order_by_payment_ref(transaction_ref: str) -> dict[str, Any] | None:
    from frame_verification.service import normalize_transaction_ref

    ref = normalize_transaction_ref(transaction_ref)
    if not ref:
        return None
    storage = _open()
    candidates = [ref]
    if ref.startswith('stxG-'):
        candidates.append(ref.replace('stxG-', 'stx', 1))
    elif ref.startswith('stx'):
        candidates.append(ref.replace('stx', 'stxG-', 1))

    for candidate in candidates:
        row = storage._conn.execute(
            'SELECT * FROM shop_star_orders WHERE payment_id=?',
            (candidate,),
        ).fetchone()
        if row:
            return dict(row)

    for candidate in candidates:
        row = storage._conn.execute(
            'SELECT * FROM shop_star_orders WHERE payment_id LIKE ?',
            (f'%{candidate}%',),
        ).fetchone()
        if row:
            return dict(row)

    core = ref[3:] if ref.startswith('stx') else ref
    if len(core) >= 16:
        row = storage._conn.execute(
            'SELECT * FROM shop_star_orders WHERE payment_id LIKE ?',
            (f'%{core[:32]}%',),
        ).fetchone()
        if row:
            return dict(row)
    return None


def admin_refund_star_payment(
    transaction_ref: str,
    bot_token: str,
    *,
    buyer_telegram_id: int | None = None,
) -> dict[str, Any]:
    from frame_verification.service import (
        normalize_transaction_ref,
        refund_star_payment_api,
    )

    payment_id = normalize_transaction_ref(transaction_ref)
    if not payment_id:
        return {'ok': False, 'error': 'Укажите ID транзакции'}

    order = get_star_order_by_payment_ref(payment_id)
    storage = _open()

    if order and order.get('status') == 'refunded':
        return {
            'ok': False,
            'error': 'По этой транзакции возврат уже выполнен.',
            'already_refunded': True,
        }

    user_id = int(order['user_id']) if order else None
    stars = int(order['stars']) if order else 0

    telegram_id = int(buyer_telegram_id) if buyer_telegram_id else None
    if not telegram_id and user_id is not None:
        telegram_id = _telegram_id_for_shop_user(storage, user_id)
    if not telegram_id:
        return {
            'ok': False,
            'error': (
                'Не найден Telegram ID покупателя. '
                'Укажите: /refund <id_транзакции> <telegram_user_id>'
            ),
        }

    refund = refund_star_payment_api(bot_token, telegram_id, payment_id)
    if not refund.get('ok'):
        err = (refund.get('error') or '').lower()
        already_at_telegram = (
            'refund' in err
            and ('already' in err or 'refunded' in err)
        )
        if not (already_at_telegram and order):
            return refund

    new_balance = None
    now = int(time.time())
    if order:
        if order.get('status') == 'paid' and stars > 0:
            new_balance = storage.adjust_stars_balance(
                user_id,
                -stars,
                refund=True,
                title=f'Возврат {stars:,} звёзд'.replace(',', ' '),
                peer_user_id=config.SERVICE_USER_ID,
            )
        storage._conn.execute(
            '''UPDATE shop_star_orders SET status=?, refunded_at=?
               WHERE nonce=?''',
            ('refunded', now, order['nonce']),
        )
        storage._conn.commit()
        _bump_top_donors_version()

    return {
        'ok': True,
        'telegram_payment_id': payment_id,
        'buyer_telegram_id': telegram_id,
        'user_id': user_id,
        'stars': stars,
        'balance': new_balance,
        'order_found': order is not None,
        'orphan_refund': order is None,
    }


def create_anon_order(user_id: int, kind: str, digits: str = '',
                      promo: str = '', target_user_id: int = 0) -> dict[str, Any]:
    if kind not in ANON_PRICES:
        return {'ok': False, 'error': 'Неверный тип номера'}
    digits = ''.join(c for c in (digits or '') if c.isdigit())
    if kind == 'custom_8' and len(digits) != 8:
        return {'ok': False, 'error': 'Введите 8 цифр'}
    if kind == 'custom_4' and len(digits) != 4:
        return {'ok': False, 'error': 'Введите 4 цифры'}
    storage = _open()
    if kind in ('custom_8', 'custom_4'):
        phone = canonical_anon_phone(ANON_PHONE_PREFIX + digits)
        if _phone_taken(storage, phone):
            return {'ok': False, 'error': 'Номер уже занят'}
    target = int(target_user_id or user_id)
    if not storage.get_user_by_id(target):
        return {'ok': False, 'error': 'Получатель не найден'}
    base_xtr = ANON_PRICES[kind]
    final_xtr, promo_code, discount, err = _apply_promo(base_xtr, promo)
    if err:
        return {'ok': False, 'error': err}
    nonce = secrets.token_hex(16)
    storage._conn.execute(
        '''INSERT INTO shop_anon_orders
           (nonce, user_id, target_user_id, kind, digits, xtr, promo, created_at)
           VALUES (?,?,?,?,?,?,?,?)''',
        (nonce, user_id, target, kind, digits, final_xtr, promo_code or '',
         int(time.time())),
    )
    storage._conn.commit()
    titles = {
        'random_8': 'Номер KoalGram +888 (8 цифр)',
        'random_4': 'Номер KoalGram +888 (4 цифры)',
        'custom_8': f'Номер +888{digits}',
        'custom_4': f'Номер +888{digits}',
    }
    return {
        'ok': True,
        'nonce': nonce,
        'xtr': final_xtr,
        'originalXtr': base_xtr,
        'promo': promo_code,
        'discountPercent': discount,
        'title': titles.get(kind, 'Анонимный номер'),
        'targetUserId': target,
        'payload': f'shop_anon:{user_id}:{kind}:{nonce}',
    }


def validate_anon_order(payload: str, xtr_amount: int) -> dict[str, Any]:
    parts = (payload or '').split(':')
    if len(parts) != 4 or parts[0] != 'shop_anon':
        return {'ok': False, 'error': 'Неверный payload'}
    storage = _open()
    nonce = parts[3]
    row = storage._conn.execute(
        'SELECT * FROM shop_anon_orders WHERE nonce=?', (nonce,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    if row['status'] != 'pending':
        return {'ok': False, 'error': 'Заказ уже обработан'}
    if int(row['xtr']) != int(xtr_amount):
        return {'ok': False, 'error': 'Неверная сумма'}
    return {'ok': True}


def complete_anon_order(
    payload: str,
    payment_id: str,
    buyer_telegram_id: int = 0,
) -> dict[str, Any]:
    parts = (payload or '').split(':')
    if len(parts) != 4 or parts[0] != 'shop_anon':
        return {'ok': False, 'error': 'Неверный payload'}
    try:
        user_id = int(parts[1])
        kind = parts[2]
        nonce = parts[3]
    except ValueError:
        return {'ok': False, 'error': 'Неверный payload'}
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_anon_orders WHERE nonce=?', (nonce,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    if row['status'] == 'paid':
        phone = row['phone'] or ''
        return {
            'ok': True,
            'already': True,
            'phone': format_phone_display(phone) if phone else None,
            'product': 'anon',
        }
    if int(row['user_id']) != user_id or row['kind'] != kind:
        return {'ok': False, 'error': 'Несовпадение заказа'}

    phone = canonical_anon_phone(_resolve_anon_phone(storage, kind, row['digits'] or '') or '')
    if not phone:
        return {'ok': False, 'error': 'Не удалось выдать номер'}

    target_user_id = int(row['target_user_id'] or row['user_id'])
    tg_id = _telegram_id_for_shop_user(storage, target_user_id)
    if tg_id:
        try:
            storage.assign_bot_phone(
                int(tg_id),
                phone=phone,
                source=kind,
            )
        except Exception:
            pass

    storage._conn.execute(
        '''INSERT OR REPLACE INTO anonymous_numbers
           (phone, user_id, kind, created_at) VALUES (?,?,?,?)''',
        (phone, target_user_id, kind, int(time.time())),
    )
    storage._conn.execute(
        '''UPDATE shop_anon_orders
           SET status=?, payment_id=?, phone=?, buyer_telegram_id=?
           WHERE nonce=?''',
        ('paid', payment_id, phone, int(buyer_telegram_id or 0), nonce),
    )
    storage._conn.commit()
    _bump_top_donors_version()
    return {
        'ok': True,
        'phone': format_phone_display(phone),
        'product': 'anon',
        'targetUserId': target_user_id,
    }


def lookup_collectible_username(username: str) -> dict[str, Any]:
    storage = _open()
    username = normalize_collectible_username(username)
    if not is_valid_shop_collectible_username(username):
        return {
            'ok': False,
            'error': 'Можно использовать символы a-z, 0-9 и _.',
        }
    price = collectible_username_price_xtr(username)
    if price is None:
        return {'ok': False, 'error': 'Некорректный username'}
    primary = storage._conn.execute(
        'SELECT id FROM users WHERE lower(username)=?', (username,),
    ).fetchone()
    if primary:
        return {'ok': False, 'error': 'Username уже занят'}
    if storage.get_collectible_username_owner(username):
        return {'ok': False, 'error': 'Username уже занят'}
    if storage.get_channel_by_username(username):
        return {'ok': False, 'error': 'Username уже занят'}
    return {
        'ok': True,
        'username': username,
        'length': len(username),
        'xtr': price,
        'available': True,
    }


def create_username_order(user_id: int, username: str, promo: str = '',
                          target_user_id: int = 0) -> dict[str, Any]:
    lookup = lookup_collectible_username(username)
    if not lookup.get('ok'):
        return lookup
    username = lookup['username']
    storage = _open()
    target = int(target_user_id or user_id)
    target_user = storage.get_user_by_id(target)
    if not target_user:
        return {'ok': False, 'error': 'Получатель не найден'}
    base_xtr = int(lookup['xtr'])
    final_xtr, promo_code, discount, err = _apply_promo(base_xtr, promo)
    if err:
        return {'ok': False, 'error': err}
    nonce = secrets.token_hex(16)
    storage._conn.execute(
        '''INSERT INTO shop_username_orders
           (nonce, user_id, target_user_id, username, xtr, created_at)
           VALUES (?,?,?,?,?,?)''',
        (nonce, user_id, target, username, final_xtr, int(time.time())),
    )
    storage._conn.commit()
    return {
        'ok': True,
        'nonce': nonce,
        'username': username,
        'xtr': final_xtr,
        'originalXtr': base_xtr,
        'promo': promo_code,
        'discountPercent': discount,
        'title': f'Collectible @{username}',
        'targetUserId': target,
        'payload': f'shop_username:{user_id}:{username}:{nonce}',
    }


def validate_username_order(payload: str, xtr_amount: int) -> dict[str, Any]:
    parts = (payload or '').split(':')
    if len(parts) != 4 or parts[0] != 'shop_username':
        return {'ok': False, 'error': 'Неверный payload'}
    storage = _open()
    nonce = parts[3]
    row = storage._conn.execute(
        'SELECT * FROM shop_username_orders WHERE nonce=?', (nonce,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    if row['status'] != 'pending':
        return {'ok': False, 'error': 'Заказ уже обработан'}
    if int(row['xtr']) != int(xtr_amount):
        return {'ok': False, 'error': 'Неверная сумма'}
    return {'ok': True}


def complete_username_order(
    payload: str,
    payment_id: str,
    buyer_telegram_id: int = 0,
) -> dict[str, Any]:
    parts = (payload or '').split(':')
    if len(parts) != 4 or parts[0] != 'shop_username':
        return {'ok': False, 'error': 'Неверный payload'}
    try:
        user_id = int(parts[1])
        username = normalize_collectible_username(parts[2])
        nonce = parts[3]
    except ValueError:
        return {'ok': False, 'error': 'Неверный payload'}
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_username_orders WHERE nonce=?', (nonce,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    if row['status'] == 'paid':
        return {
            'ok': True,
            'already': True,
            'username': row['username'],
            'product': 'username',
        }
    if int(row['user_id']) != user_id:
        return {'ok': False, 'error': 'Несовпадение заказа'}
    if normalize_collectible_username(row['username']) != username:
        return {'ok': False, 'error': 'Несовпадение заказа'}
    target_user_id = int(row['target_user_id'] or row['user_id'])
    primary = storage._conn.execute(
        'SELECT id FROM users WHERE lower(username)=?', (username,),
    ).fetchone()
    if primary:
        return {'ok': False, 'error': 'Username уже занят'}
    if storage.get_collectible_username_owner(username):
        return {'ok': False, 'error': 'Username уже занят'}
    if storage.get_channel_by_username(username):
        return {'ok': False, 'error': 'Username уже занят'}

    try:
        storage.grant_collectible_username(
            target_user_id,
            username,
            price=str(int(row['xtr'])),
            granted_by=user_id,
            active=True,
        )
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}

    storage._conn.execute(
        '''UPDATE shop_username_orders
           SET status=?, payment_id=?, buyer_telegram_id=?
           WHERE nonce=?''',
        ('paid', payment_id, int(buyer_telegram_id or 0), nonce),
    )
    storage._conn.commit()
    _bump_top_donors_version()
    _wake_server()
    return {
        'ok': True,
        'username': username,
        'product': 'username',
        'targetUserId': target_user_id,
    }


def _normalize_premium_username(username: str) -> str:
    return (username or '').strip().lstrip('@').lower()


def lookup_premium_recipient(username: str) -> dict[str, Any]:
    uname = _normalize_premium_username(username)
    if not uname:
        return {'ok': False, 'error': 'Введите username'}
    if not uname.replace('_', '').isalnum():
        return {'ok': False, 'error': 'Неверный username'}
    storage = _open()
    user = storage.get_user_by_username(uname)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден на сервере'}
    if user.get('is_bot'):
        return {'ok': False, 'error': 'Нельзя выводить подарок на бота — укажите свой аккаунт'}
    uid = int(user['id'])
    now = int(time.time())
    premium_until = int(user.get('premium_until') or 0)
    premium_active = bool(user.get('premium')) and premium_until > now
    return {
        'ok': True,
        'userId': uid,
        'username': uname,
        'firstName': (user.get('first_name') or '').strip(),
        'premium': premium_active,
        'premiumUntil': premium_until if premium_active else 0,
    }


def create_premium_order(
    user_id: int,
    months: int,
    recipient_username: str,
) -> dict[str, Any]:
    try:
        months = int(months)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверный тариф'}
    plan = PREMIUM_SHOP_PLANS.get(months)
    if not plan:
        return {'ok': False, 'error': 'Неверный тариф'}
    lookup = lookup_premium_recipient(recipient_username)
    if not lookup.get('ok'):
        return lookup
    target_uid = int(lookup['userId'])
    recipient_username = lookup['username']
    storage = _open()
    buyer = storage.get_user_by_id(int(user_id))
    if not buyer:
        return {'ok': False, 'error': 'Покупатель не найден'}
    xtr = int(plan['xtr'])
    days = int(plan['days'])
    nonce = secrets.token_hex(16)
    storage._conn.execute(
        '''INSERT INTO shop_premium_orders
           (nonce, user_id, target_user_id, recipient_username, months, days, xtr,
            created_at)
           VALUES (?,?,?,?,?,?,?,?)''',
        (nonce, user_id, target_uid, recipient_username, months, days, xtr,
         int(time.time())),
    )
    storage._conn.commit()
    labels = {1: '1 месяц', 3: '3 месяца', 12: '12 месяцев'}
    return {
        'ok': True,
        'nonce': nonce,
        'months': months,
        'days': days,
        'xtr': xtr,
        'recipientUsername': recipient_username,
        'targetUserId': target_uid,
        'title': f'Premium {labels.get(months, f"{months} мес.")} '
                 f'для @{recipient_username}',
        'payload': f'shop_premium:{user_id}:{months}:{nonce}',
    }


def validate_premium_order(payload: str, xtr_amount: int) -> dict[str, Any]:
    parts = (payload or '').split(':')
    if len(parts) != 4 or parts[0] != 'shop_premium':
        return {'ok': False, 'error': 'Неверный payload'}
    storage = _open()
    nonce = parts[3]
    row = storage._conn.execute(
        'SELECT * FROM shop_premium_orders WHERE nonce=?', (nonce,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    if row['status'] != 'pending':
        return {'ok': False, 'error': 'Заказ уже обработан'}
    if int(row['xtr']) != int(xtr_amount):
        return {'ok': False, 'error': 'Неверная сумма'}
    return {'ok': True}


def complete_premium_order(
    payload: str,
    payment_id: str,
    buyer_telegram_id: int = 0,
) -> dict[str, Any]:
    parts = (payload or '').split(':')
    if len(parts) != 4 or parts[0] != 'shop_premium':
        return {'ok': False, 'error': 'Неверный payload'}
    try:
        user_id = int(parts[1])
        months = int(parts[2])
        nonce = parts[3]
    except ValueError:
        return {'ok': False, 'error': 'Неверный payload'}
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_premium_orders WHERE nonce=?', (nonce,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    if row['status'] == 'paid':
        return {
            'ok': True,
            'already': True,
            'product': 'premium',
            'recipientUsername': row['recipient_username'],
            'targetUserId': int(row['target_user_id']),
            'months': int(row['months']),
        }
    if int(row['user_id']) != user_id:
        return {'ok': False, 'error': 'Несовпадение заказа'}
    if int(row['months']) != months:
        return {'ok': False, 'error': 'Несовпадение заказа'}
    target_user_id = int(row['target_user_id'])
    target_user = storage.get_user_by_id(target_user_id)
    if not target_user:
        return {'ok': False, 'error': 'Получатель не найден'}
    now = int(time.time())
    days = int(row['days'])
    current_until = int(target_user.get('premium_until') or 0)
    if target_user.get('premium') and current_until > now:
        end = current_until + days * 86400
    else:
        end = now + days * 86400
    try:
        storage.grant_premium(target_user_id, until_ts=end)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    storage._conn.execute(
        '''UPDATE shop_premium_orders
           SET status=?, payment_id=?, buyer_telegram_id=?
           WHERE nonce=?''',
        ('paid', payment_id, int(buyer_telegram_id or 0), nonce),
    )
    storage._conn.commit()
    _bump_top_donors_version()
    _wake_server()
    return {
        'ok': True,
        'product': 'premium',
        'recipientUsername': row['recipient_username'],
        'targetUserId': target_user_id,
        'months': months,
        'premiumUntil': end,
    }


def purchase_anonymous_number(
    token: str,
    kind: str,
    digits: str = '',
) -> dict[str, Any]:
    if kind not in ANON_PRICES:
        return {'ok': False, 'error': 'Неверный тип номера'}
    sess = get_session(token)
    if not sess.get('ok'):
        return sess
    user_id = int(sess['user']['id'])
    price = ANON_PRICES[kind]
    storage = _open()

    phone = canonical_anon_phone(_resolve_anon_phone(storage, kind, digits) or '')
    if not phone:
        return {'ok': False, 'error': 'Не удалось подобрать номер'}

    balance = storage.get_stars_balance(user_id)
    if not config.INFINITE_STARS and balance < price:
        return {'ok': False, 'error': f'Недостаточно звёзд (нужно {price})'}

    tg_id = _telegram_id_for_shop_user(storage, user_id)
    if tg_id:
        try:
            storage.assign_bot_phone(
                int(tg_id),
                phone=phone,
                source=kind,
            )
        except Exception:
            pass

    storage._conn.execute(
        '''INSERT OR REPLACE INTO anonymous_numbers
           (phone, user_id, kind, created_at) VALUES (?,?,?,?)''',
        (phone, user_id, kind, int(time.time())),
    )
    storage._conn.commit()

    if not config.INFINITE_STARS:
        storage.adjust_stars_balance(
            user_id, -price,
            title=f'Номер +{phone}',
            peer_user_id=config.SERVICE_USER_ID,
        )

    return {
        'ok': True,
        'phone': format_phone_display(phone),
        'balance': storage.get_stars_balance(user_id),
    }


def _resolve_anon_phone(storage: Storage, kind: str,
                        digits: str) -> str | None:
    digits = ''.join(c for c in digits if c.isdigit())

    if kind == 'random_8':
        phone = _unique_anon(storage, 8)
    elif kind == 'random_4':
        phone = _unique_anon(storage, 4)
    elif kind == 'custom_8':
        if len(digits) != 8 or not digits.isdigit():
            return None
        phone = canonical_anon_phone(ANON_PHONE_PREFIX + digits)
        if _phone_taken(storage, phone):
            return None
    elif kind == 'custom_4':
        if len(digits) != 4 or not digits.isdigit():
            return None
        phone = canonical_anon_phone(ANON_PHONE_PREFIX + digits)
        if _phone_taken(storage, phone):
            return None
    else:
        return None
    return canonical_anon_phone(phone or '') or None


def _phone_taken(storage: Storage, phone: str) -> bool:
    for variant in anon_phone_lookup_variants(phone):
        if storage.get_user_by_phone(variant):
            return True
        if storage._conn.execute(
            'SELECT 1 FROM telegram_phones WHERE phone=?', (variant,),
        ).fetchone():
            return True
        if storage._conn.execute(
            'SELECT 1 FROM bot_phone_assignments WHERE phone=?', (variant,),
        ).fetchone():
            return True
        if storage._conn.execute(
            'SELECT 1 FROM anonymous_numbers WHERE phone=?', (variant,),
        ).fetchone():
            return True
    return False


def _unique_anon(storage: Storage, length: int) -> str | None:
    for _ in range(200):
        suffix = ''.join(str(random.randint(0, 9)) for _ in range(length))
        phone = canonical_anon_phone(ANON_PHONE_PREFIX + suffix)
        if not _phone_taken(storage, phone):
            return phone
    return None


def list_anonymous_numbers(token: str) -> dict[str, Any]:
    sess = get_session(token)
    if not sess.get('ok'):
        return sess
    return list_my_phones(
        telegram_user_id=int(sess.get('telegramUserId') or 0),
        token=token,
    )


def admin_lookup_user(username: str) -> dict[str, Any]:
    storage = _open()
    user = storage.get_user_by_username(username)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    uid = int(user['id'])
    return {
        'ok': True,
        'user': {
            'id': uid,
            'username': user.get('username'),
            'firstName': user.get('first_name') or '',
            'phone': format_phone_display(user.get('phone') or ''),
            'stars': storage.get_stars_balance(uid),
            'gramNanoton': storage.get_ton_balance(uid),
            'gram': config.format_gram_amount(storage.get_ton_balance(uid)),
            'banned': storage.is_user_banned(uid),
            'banBlockRecreate': storage.is_ban_blocking_recreate(uid),
            'frozen': storage.is_user_frozen(uid),
            'premium': bool(user.get('premium')),
            'premiumUntil': int(user.get('premium_until') or 0),
            'scam': bool(user.get('scam')),
            'fake': bool(user.get('fake')),
        },
    }


def admin_set_user_label(username: str, label: str) -> dict[str, Any]:
    storage = _open()
    user = storage.get_user_by_username(username)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    normalized = (label or '').strip().lower()
    if normalized not in {'none', 'scam', 'fake'}:
        return {'ok': False, 'error': 'Неизвестная метка'}
    uid = int(user['id'])
    try:
        updated = storage.set_user_safety_label(uid, normalized)
        from frame_verification.push import enqueue_safety_label_push
        enqueue_safety_label_push(storage, uid)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    return {
        'ok': True,
        'userId': uid,
        'username': updated.get('username') or username.lstrip('@'),
        'label': normalized,
        'scam': bool(updated.get('scam')),
        'fake': bool(updated.get('fake')),
    }


def admin_grant_premium_username(username: str, *, days: int = 30) -> dict[str, Any]:
    storage = _open()
    user = storage.get_user_by_username(username)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    return admin_grant_premium(int(user['id']), days=days)


def admin_revoke_premium_username(username: str) -> dict[str, Any]:
    storage = _open()
    user = storage.get_user_by_username(username)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    return admin_revoke_premium(int(user['id']))


def admin_grant_stars(username: str, amount: int) -> dict[str, Any]:
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверное количество звёзд'}
    if amount <= 0:
        return {'ok': False, 'error': 'Количество должно быть больше нуля'}

    storage = _open()
    user = storage.get_user_by_username(username)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    uid = int(user['id'])
    if storage.is_user_banned(uid):
        return {'ok': False, 'error': 'Аккаунт заблокирован'}

    new_balance = storage.adjust_stars_balance(
        uid,
        amount,
        title=f'{amount} звёзд',
        peer_user_id=config.SERVICE_USER_ID,
    )
    return {
        'ok': True,
        'userId': uid,
        'username': user.get('username') or username.lstrip('@'),
        'amount': amount,
        'balance': new_balance,
    }


def admin_revoke_stars(username: str, amount: int) -> dict[str, Any]:
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверное количество звёзд'}
    if amount <= 0:
        return {'ok': False, 'error': 'Количество должно быть больше нуля'}

    storage = _open()
    user = storage.get_user_by_username(username)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    uid = int(user['id'])
    balance = storage.get_stars_balance(uid)
    if balance <= 0:
        return {'ok': False, 'error': 'У пользователя нет звёзд на балансе'}

    actual = min(amount, balance)
    new_balance = storage.adjust_stars_balance(
        uid,
        -actual,
        title=f'Списание {actual} звёзд',
        peer_user_id=config.SERVICE_USER_ID,
    )
    return {
        'ok': True,
        'userId': uid,
        'username': user.get('username') or username.lstrip('@'),
        'amount': actual,
        'requested': amount,
        'balance': new_balance,
        'partial': actual < amount,
    }


def admin_grant_gram(username: str, nanoton: int) -> dict[str, Any]:
    try:
        nanoton = int(nanoton)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверное количество GRAM'}
    if nanoton <= 0:
        return {'ok': False, 'error': 'Количество должно быть больше нуля'}

    storage = _open()
    user = storage.get_user_by_username(username)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    uid = int(user['id'])
    if storage.is_user_banned(uid):
        return {'ok': False, 'error': 'Аккаунт заблокирован'}

    new_balance = storage.adjust_ton_balance(
        uid,
        nanoton,
        title=f'{config.format_gram_amount(nanoton)} GRAM',
        peer_user_id=config.SERVICE_USER_ID,
    )
    try:
        from shop_webapp.push_wake import signal_pending_push
        signal_pending_push()
    except Exception:
        pass
    return {
        'ok': True,
        'userId': uid,
        'username': user.get('username') or username.lstrip('@'),
        'amountNanoton': nanoton,
        'amountGram': config.format_gram_amount(nanoton),
        'balanceNanoton': new_balance,
        'balanceGram': config.format_gram_amount(new_balance),
    }


def admin_ban_user(username: str, *, block_recreate: bool = False) -> dict[str, Any]:
    storage = _open()
    user = storage.get_user_by_username(username)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    uid = int(user['id'])
    if uid == config.SERVICE_USER_ID:
        return {'ok': False, 'error': 'Нельзя забанить системный аккаунт'}
    if storage.is_user_banned(uid):
        return {'ok': False, 'error': 'Аккаунт уже заблокирован'}

    old_username = user.get('username')
    try:
        sessions_revoked = storage.ban_user_account(
            uid, block_recreate=block_recreate,
        )
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}

    storage._conn.execute(
        'DELETE FROM shop_webapp_sessions WHERE user_id=?', (uid,),
    )
    storage._conn.commit()
    return {
        'ok': True,
        'userId': uid,
        'oldUsername': old_username,
        'sessionsRevoked': sessions_revoked,
        'blockRecreate': bool(block_recreate),
    }


def _enqueue_config_refresh(storage, user_id: int) -> None:
    from handlers.packers import pack_banner_refresh_push
    storage.enqueue_pending_update(
        user_id, pack_banner_refresh_push(storage=storage, recipient_id=user_id))


def admin_get_freeze_settings() -> dict[str, Any]:
    storage = _open()
    return {'ok': True, 'settings': storage.get_freeze_settings()}


def admin_set_freeze_settings(
    *,
    emoji: str = '',
    emoji_id: str = '',
    appeal_url: str = '',
    freeze_days: int = 30,
) -> dict[str, Any]:
    storage = _open()
    settings = storage.set_freeze_settings(
        emoji=emoji, emoji_id=emoji_id, appeal_url=appeal_url,
        freeze_days=freeze_days)
    try:
        from shop_webapp.freeze_push import enqueue_freeze_settings_refresh
        clients = enqueue_freeze_settings_refresh(storage)
    except Exception:
        clients = 0
    return {'ok': True, 'settings': settings, 'clientsNotified': clients}


def admin_freeze_user(username: str) -> dict[str, Any]:
    storage = _open()
    user = storage.get_user_by_username(username)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    uid = int(user['id'])
    if storage.is_user_banned(uid):
        return {'ok': False, 'error': 'Аккаунт удалён'}
    already = storage.is_user_frozen(uid)
    try:
        info = storage.freeze_user_account(uid, refresh=already)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    try:
        from shop_webapp.freeze_push import enqueue_freeze_change
        clients = enqueue_freeze_change(storage, uid)
    except Exception:
        clients = 0
    return {
        'ok': True,
        'userId': uid,
        'username': user.get('username') or username.lstrip('@'),
        'refreshed': already,
        'clientsNotified': clients,
        **info,
    }


def admin_unfreeze_user(username: str) -> dict[str, Any]:
    storage = _open()
    user = storage.get_user_by_username(username)
    if not user:
        user = storage.get_user_by_id(int(username)) if username.isdigit() else None
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    uid = int(user['id'])
    if not storage.is_user_frozen(uid):
        return {'ok': False, 'error': 'Аккаунт не заморожен'}
    try:
        storage.unfreeze_user_account(uid)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    try:
        from shop_webapp.freeze_push import enqueue_freeze_change
        clients = enqueue_freeze_change(storage, uid)
    except Exception:
        clients = 0
    return {
        'ok': True,
        'userId': uid,
        'username': user.get('username'),
        'clientsNotified': clients,
    }


def admin_give_number(telegram_user_id: int, phone: str) -> dict[str, Any]:
    try:
        tel_id = int(telegram_user_id)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверный Telegram ID'}
    if tel_id <= 0:
        return {'ok': False, 'error': 'Неверный Telegram ID'}

    storage = _open()
    normalized = normalize_phone(phone)
    if not normalized:
        return {'ok': False, 'error': 'Укажите номер телефона'}
    already = any(
        row['phone'] == normalized
        for row in storage.list_bot_phones_for_telegram(tel_id)
    )
    try:
        assigned = storage.add_bot_phone(
            tel_id, normalized, source='admin', reassign=True,
        )
    except ValueError:
        return {'ok': False, 'error': 'Неверный номер телефона'}
    return {
        'ok': True,
        'telegramUserId': tel_id,
        'phone': format_phone_display(assigned),
        'alreadyHad': already,
        'numbers': [
            format_phone_display(row['phone'])
            for row in storage.list_bot_phones_for_telegram(tel_id)
        ],
    }


def _resolve_admin_user_id(storage: Storage, raw_id: int) -> int | None:
    """Resolve MTG user id or Telegram user id to internal user id."""
    uid = int(raw_id)
    if storage.get_user_by_id(uid):
        return uid
    phone = storage.get_bot_phone_for_telegram(uid)
    if phone:
        user = storage.get_user_by_phone(phone)
        if user:
            return int(user['id'])
    return None


def admin_grant_premium(user_ref: int, *, days: int = 30) -> dict[str, Any]:
    try:
        ref = int(user_ref)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверный ID пользователя'}
    if ref <= 0:
        return {'ok': False, 'error': 'Неверный ID пользователя'}
    if days <= 0:
        days = 30
    storage = _open()
    uid = _resolve_admin_user_id(storage, ref)
    if not uid:
        return {'ok': False, 'error': 'Пользователь не найден'}
    try:
        user = storage.grant_premium(uid, days=days)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    until = int(user.get('premium_until') or 0)
    return {
        'ok': True,
        'userId': uid,
        'premium': True,
        'premiumUntil': until,
        'days': days,
    }


def admin_revoke_premium(user_ref: int) -> dict[str, Any]:
    try:
        ref = int(user_ref)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверный ID пользователя'}
    if ref <= 0:
        return {'ok': False, 'error': 'Неверный ID пользователя'}
    storage = _open()
    uid = _resolve_admin_user_id(storage, ref)
    if not uid:
        return {'ok': False, 'error': 'Пользователь не найден'}
    try:
        user = storage.revoke_premium(uid)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    return {
        'ok': True,
        'userId': uid,
        'premium': bool(user.get('premium')),
    }


def admin_set_stars_rating(user_ref: str | int, level: int) -> dict[str, Any]:
    """Set profile stars rating level for a user (by id or @username)."""
    from db.stars_rating import MAX_LEVEL

    try:
        level_i = int(level)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверный уровень рейтинга'}
    if level_i < 0 or level_i > MAX_LEVEL:
        return {'ok': False, 'error': f'Уровень должен быть от 0 до {MAX_LEVEL}'}

    storage = _open()
    raw = str(user_ref or '').strip()
    if not raw:
        return {'ok': False, 'error': 'Укажите user_id или @username'}

    uid: int | None = None
    username = ''
    if raw.lstrip('-').isdigit():
        uid = _resolve_admin_user_id(storage, int(raw))
        if uid:
            user = storage.get_user_by_id(uid) or {}
            username = str(user.get('username') or '')
    else:
        username = raw.lstrip('@')
        user = storage.get_user_by_username(username)
        if user:
            uid = int(user['id'])
            username = str(user.get('username') or username)

    if not uid:
        return {'ok': False, 'error': 'Пользователь не найден'}

    try:
        rating = storage.set_stars_rating_level(uid, level_i)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}

    return {
        'ok': True,
        'userId': uid,
        'username': username,
        'level': int(rating.get('level') or 0),
        'points': int(rating.get('stars') or 0),
        'nextLevelStars': rating.get('next_level_stars'),
    }


def admin_take_number(telegram_user_id: int, phone: str) -> dict[str, Any]:
    """Remove a specific number from a Telegram user."""
    try:
        tel_id = int(telegram_user_id)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверный Telegram ID'}
    if tel_id <= 0:
        return {'ok': False, 'error': 'Неверный Telegram ID'}

    storage = _open()
    normalized = normalize_phone(phone)
    if not normalized:
        return {'ok': False, 'error': 'Укажите номер телефона'}

    # Check that this number is actually linked to this Telegram user.
    has_number = any(
        row['phone'] == normalized
        for row in storage.list_bot_phones_for_telegram(tel_id)
    )
    if not has_number:
        return {'ok': False, 'error': 'У пользователя нет такого номера'}

    # Remove mappings from telegram_phones / bot_phone_assignments / anonymous_numbers.
    storage._conn.execute(
        'DELETE FROM telegram_phones WHERE telegram_user_id=? AND phone=?',
        (tel_id, normalized),
    )
    storage._conn.execute(
        'DELETE FROM bot_phone_assignments WHERE telegram_user_id=? AND phone=?',
        (tel_id, normalized),
    )
    storage._conn.execute(
        'DELETE FROM anonymous_numbers WHERE user_id=? AND phone=?',
        (tel_id, normalized),
    )
    storage._conn.commit()

    remaining = [
        format_phone_display(row['phone'])
        for row in storage.list_bot_phones_for_telegram(tel_id)
    ]
    return {
        'ok': True,
        'telegramUserId': tel_id,
        'phone': format_phone_display(normalized),
        'numbers': remaining,
    }

def _find_user_for_unban(query: str) -> dict[str, Any] | None:
    storage = _open()
    raw = (query or '').strip().lstrip('@')
    if not raw:
        return None

    if raw.isdigit():
        user = storage.get_user_by_id(int(raw))
        if user:
            return user

    phone = normalize_phone(raw)
    if len(phone) >= 7:
        user = storage.get_user_by_phone(phone)
        if user:
            return user

    user = storage.get_user_by_username(raw)
    if user:
        return user

    row = storage._conn.execute(
        'SELECT * FROM users WHERE lower(banned_username)=?',
        (raw.lower(),),
    ).fetchone()
    return storage._row_to_user(row) if row else None


def admin_lookup_unban(query: str) -> dict[str, Any]:
    user = _find_user_for_unban(query)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    uid = int(user['id'])
    storage = _open()
    return {
        'ok': True,
        'user': {
            'id': uid,
            'username': user.get('username'),
            'bannedUsername': user.get('banned_username'),
            'firstName': user.get('first_name') or '',
            'phone': format_phone_display(user.get('phone') or ''),
            'stars': storage.get_stars_balance(uid),
            'banned': storage.is_user_banned(uid),
            'banBlockRecreate': storage.is_ban_blocking_recreate(uid),
            'frozen': storage.is_user_frozen(uid),
        },
    }


def admin_unban_user(query: str) -> dict[str, Any]:
    user = _find_user_for_unban(query)
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    uid = int(user['id'])
    storage = _open()
    if not storage.is_user_banned(uid):
        return {'ok': False, 'error': 'Аккаунт не заблокирован'}
    if uid == config.SERVICE_USER_ID:
        return {'ok': False, 'error': 'Нельзя разбанить системный аккаунт'}

    try:
        restored = storage.unban_user_account(uid)
    except ValueError as exc:
        msg = str(exc)
        if msg == 'not banned':
            return {'ok': False, 'error': 'Аккаунт не заблокирован'}
        return {'ok': False, 'error': msg}

    return {
        'ok': True,
        'userId': uid,
        'firstName': restored.get('first_name'),
        'username': restored.get('username'),
    }


def admin_get_banner() -> dict[str, Any]:
    from shop_webapp.banner import get_banner
    return get_banner(_open())


def admin_set_banner(
    *,
    enabled: bool,
    emoji: str = '',
    emoji_id: str = '',
    title: str = '',
    description: str = '',
    url: str = '',
    suggestion_id: str = 'airgram_banner',
) -> dict[str, Any]:
    from shop_webapp.banner import set_banner
    return set_banner(
        _open(),
        enabled=enabled,
        emoji=emoji,
        emoji_id=emoji_id,
        title=title,
        description=description,
        url=url,
        suggestion_id=suggestion_id,
    )


def _gift_public(gift: dict) -> dict[str, Any]:
    return {
        'giftId': str(int(gift['gift_id'])),
        'title': gift.get('title') or '',
        'stars': int(gift.get('stars') or 0),
        'convertStars': int(gift.get('convert_stars') or 0),
        'upgradeStars': int(gift.get('upgrade_stars') or 0),
        'limited': bool(int(gift.get('limited') or 0)),
        'availabilityTotal': int(gift.get('availability_total') or 0),
        'availabilityRemains': int(gift.get('availability_remains') or 0),
        'soldOut': bool(int(gift.get('sold_out') or 0)),
        'requirePremium': bool(int(gift.get('require_premium') or 0)),
        'birthday': bool(int(gift.get('birthday') or 0)),
        'peerColorAvailable': bool(int(gift.get('peer_color_available') or 0)),
        'sortOrder': int(gift.get('sort_order') or 0),
        'documentId': str(int(gift.get('document_id') or 0)),
        'imagePath': str(gift.get('file_path') or ''),
        'isAuction': bool(int(gift.get('is_auction') or 0)),
    }


def admin_list_star_gifts() -> dict[str, Any]:
    storage = _open()
    gifts = storage.list_star_gifts()
    docs = {}
    doc_ids = [int(g.get('document_id') or 0) for g in gifts]
    for doc_id in doc_ids:
        if doc_id <= 0 or doc_id in docs:
            continue
        docs[doc_id] = storage.get_sticker_document_by_id(doc_id) or {}
    out = []
    for gift in gifts:
        item = _gift_public(gift)
        doc = docs.get(int(item.get('documentId') or 0)) or {}
        item['imagePath'] = str(doc.get('file_path') or '')
        item['shopHidden'] = bool(int(gift.get('shop_hidden') or 0))
        out.append(item)
    return {'ok': True, 'gifts': out}


def admin_update_star_gift(
    gift_id: int,
    *,
    title: str = '',
    stars: int | None = None,
    convert_stars: int | None = None,
    upgrade_stars: int | None = None,
    limited: bool | None = None,
    availability_total: int | None = None,
    availability_remains: int | None = None,
    sold_out: bool | None = None,
    require_premium: bool | None = None,
    birthday: bool | None = None,
    peer_color_available: bool | None = None,
    sort_order: int | None = None,
) -> dict[str, Any]:
    storage = _open()
    gift = storage.get_star_gift(int(gift_id))
    if not gift:
        return {'ok': False, 'error': 'Подарок не найден'}
    row = dict(gift)
    if title is not None:
        row['title'] = (title or '').strip() or row.get('title') or f'Gift {gift_id}'
    for key, value in (
        ('stars', stars),
        ('convert_stars', convert_stars),
        ('upgrade_stars', upgrade_stars),
        ('availability_total', availability_total),
        ('availability_remains', availability_remains),
        ('sort_order', sort_order),
    ):
        if value is not None:
            row[key] = int(value)
    for key, value in (
        ('limited', limited),
        ('sold_out', sold_out),
        ('require_premium', require_premium),
        ('birthday', birthday),
        ('peer_color_available', peer_color_available),
    ):
        if value is not None:
            row[key] = bool(value)
    storage.upsert_star_gift(row)
    storage.recompute_star_gifts_catalog_hash()
    updated = storage.get_star_gift(int(gift_id))
    if not updated:
        return {'ok': False, 'error': 'Подарок не найден после обновления'}
    doc = storage.get_sticker_document_by_id(int(updated.get('document_id') or 0)) or {}
    payload = _gift_public(updated)
    payload['imagePath'] = str(doc.get('file_path') or '')
    return {'ok': True, 'gift': payload}


def admin_delete_star_gift(gift_id: int) -> dict[str, Any]:
    storage = _open()
    gift = storage.get_star_gift(int(gift_id))
    if not gift:
        return {'ok': False, 'error': 'Подарок не найден'}
    storage.delete_star_gift(int(gift_id))
    return {'ok': True, 'giftId': int(gift_id)}


def admin_import_star_gifts(*, query: str = '', import_all: bool = False,
                            include_upgrades: bool = False,
                            settings: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    script = root / 'tools' / 'import_star_gifts.py'
    if not script.is_file():
        return {'ok': False, 'error': 'import_star_gifts.py not found'}

    args = ['python3', str(script)]
    session = (
        (config.SHOP_IMPORT_SESSION if hasattr(config, 'SHOP_IMPORT_SESSION') else '')
        or 'mysession'
    )
    args += ['--session', session]
    if import_all:
        args += ['--all']
    elif query.strip():
        args += ['--name', query.strip()]
    else:
        return {'ok': False, 'error': 'Укажите query или importAll'}
    if not include_upgrades:
        args += ['--catalog-only']

    settings = settings or {}
    title = str(settings.get('title') or '').strip()
    if title:
        args += ['--title', title]
    for key, flag in (
        ('stars', '--stars'),
        ('convertStars', '--convert-stars'),
        ('upgradeStars', '--upgrade-stars'),
        ('availabilityTotal', '--availability-total'),
        ('availabilityRemains', '--availability-remains'),
        ('giftCount', '--gift-count'),
    ):
        v = settings.get(key)
        if v is not None and str(v) != '':
            args += [flag, str(int(v))]
    for key, flag in (
        ('limited', '--limited'),
        ('requirePremium', '--require-premium'),
        ('birthday', '--birthday'),
        ('peerColorAvailable', '--peer-color-available'),
    ):
        v = settings.get(key)
        if v is not None:
            args += [flag, 'true' if bool(v) else 'false']

    env = dict(**os.environ)
    env['PYTHONPATH'] = str(root)
    def _run(sub_args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            sub_args,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )

    def _resolve_target_gift(storage: Storage) -> dict | None:
        q = query.strip()
        if not q:
            return None
        if q.lstrip('-').isdigit():
            return storage.get_star_gift(int(q))
        row = storage._conn.execute(
            'SELECT * FROM star_gifts WHERE lower(title)=lower(?) '
            'ORDER BY sort_order, gift_id LIMIT 1',
            (q,),
        ).fetchone()
        if row:
            return dict(row)
        row = storage._conn.execute(
            'SELECT * FROM star_gifts WHERE lower(title) LIKE ? '
            'ORDER BY sort_order, gift_id LIMIT 1',
            (f'%{q.lower()}%',),
        ).fetchone()
        return dict(row) if row else None

    try:
        if not include_upgrades:
            proc = _run(args)
            stdout = (proc.stdout or '').strip()
            stderr = (proc.stderr or '').strip()
            if proc.returncode == 0 and not import_all and query.strip():
                storage = _open()
                target = _resolve_target_gift(storage)
                if not target:
                    return {
                        'ok': False,
                        'query': query.strip(),
                        'importAll': bool(import_all),
                        'includeUpgrades': False,
                        'stdout': stdout,
                        'stderr': (stderr + '\nGift was not found in local catalog after import').strip(),
                        'exitCode': int(proc.returncode),
                    }
            return {
                'ok': proc.returncode == 0,
                'query': query.strip(),
                'importAll': bool(import_all),
                'includeUpgrades': False,
                'stdout': stdout,
                'stderr': stderr,
                'exitCode': int(proc.returncode),
            }

        # 1) Import catalog first.
        cat_args = [a for a in args if a != '--catalog-only']
        cat_args.append('--catalog-only')
        cat_proc = _run(cat_args)
        cat_stdout = (cat_proc.stdout or '').strip()
        cat_stderr = (cat_proc.stderr or '').strip()
        if cat_proc.returncode != 0:
            return {
                'ok': False,
                'query': query.strip(),
                'importAll': bool(import_all),
                'includeUpgrades': True,
                'phase': 'catalog',
                'stdout': cat_stdout,
                'stderr': cat_stderr,
                'exitCode': int(cat_proc.returncode),
            }

        # 2) Mark gifts as temporarily unavailable until upgrade attrs are imported.
        storage = _open()
        if import_all:
            targets = storage._conn.execute(
                '''SELECT gift_id FROM star_gifts
                   WHERE COALESCE(upgrade_stars, 0) > 0
                      OR COALESCE(is_auction, 0) = 1'''
            ).fetchall()
        else:
            q = query.strip()
            if q.lstrip('-').isdigit():
                targets = storage._conn.execute(
                    'SELECT gift_id FROM star_gifts WHERE gift_id=?',
                    (int(q),),
                ).fetchall()
            else:
                targets = storage._conn.execute(
                    'SELECT gift_id FROM star_gifts WHERE lower(title) LIKE ?',
                    (f'%{q.lower()}%',),
                ).fetchall()
        for row in targets:
            storage.set_star_gift_upgrade_importing(int(row['gift_id']), True)

        # 3) Import upgrade attrs for same scope.
        up_args = [a for a in args if a != '--catalog-only']
        up_args.append('--upgrade-only')
        up_proc = _run(up_args)
        up_stdout = (up_proc.stdout or '').strip()
        up_stderr = (up_proc.stderr or '').strip()
        if up_proc.returncode == 0 and not import_all and query.strip():
            target = _resolve_target_gift(storage)
            if not target:
                return {
                    'ok': False,
                    'query': query.strip(),
                    'importAll': bool(import_all),
                    'includeUpgrades': True,
                    'phase': 'verify',
                    'stdout': '\n'.join(x for x in [cat_stdout, up_stdout] if x),
                    'stderr': '\n'.join(
                        x for x in [cat_stderr, up_stderr,
                                    'Gift was not found in local catalog after import']
                        if x
                    ),
                    'exitCode': int(up_proc.returncode),
                }
            gift_id = int(target['gift_id'])
            if int(target.get('upgrade_stars') or 0) > 0:
                attrs = storage.count_star_gift_upgrade_attrs(gift_id)
                if attrs <= 0:
                    return {
                        'ok': False,
                        'query': query.strip(),
                        'importAll': bool(import_all),
                        'includeUpgrades': True,
                        'phase': 'verify',
                        'stdout': '\n'.join(x for x in [cat_stdout, up_stdout] if x),
                        'stderr': '\n'.join(
                            x for x in [cat_stderr, up_stderr,
                                        'Upgrade attributes were not imported for this gift']
                            if x
                        ),
                        'exitCode': int(up_proc.returncode),
                    }
        return {
            'ok': up_proc.returncode == 0,
            'query': query.strip(),
            'importAll': bool(import_all),
            'includeUpgrades': True,
            'phase': 'upgrades',
            'stdout': '\n'.join(x for x in [cat_stdout, up_stdout] if x),
            'stderr': '\n'.join(x for x in [cat_stderr, up_stderr] if x),
            'exitCode': int(up_proc.returncode),
        }
    except Exception as exc:
        return {'ok': False, 'error': f'Import failed: {exc}'}


def admin_import_craft_models(*, gift_id: int) -> dict[str, Any]:
    """Import craft-only upgrade models from Telegram for a gift id."""
    gid = int(gift_id)
    if gid <= 0:
        return {'ok': False, 'error': 'giftId required'}
    root = Path(__file__).resolve().parents[1]
    script = root / 'tools' / 'import_star_gifts.py'
    if not script.is_file():
        return {'ok': False, 'error': 'import_star_gifts.py not found'}
    session = (
        (config.SHOP_IMPORT_SESSION if hasattr(config, 'SHOP_IMPORT_SESSION') else '')
        or 'mysession'
    )
    env = dict(**os.environ)
    env['PYTHONPATH'] = str(root)
    try:
        proc = subprocess.run(
            ['python3', str(script), '--session', session,
             '--gift-id', str(gid), '--craft-only'],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except Exception as exc:
        return {'ok': False, 'error': f'Craft import failed: {exc}'}
    storage = _open()
    repaired = storage.repair_orphaned_saved_gift_attrs(gid)
    synced = storage.sync_saved_gift_craft_chances(gid)
    crafted = storage._conn.execute(
        '''SELECT COUNT(*) AS c FROM star_gift_upgrade_attrs
           WHERE (gift_id=? OR gift_id=0) AND attr_type='model' AND crafted=1''',
        (gid,),
    ).fetchone()['c']
    return {
        'ok': proc.returncode == 0,
        'giftId': gid,
        'craftedModels': int(crafted),
        'repairedGifts': int(repaired),
        'syncedCraftChances': int(synced),
        'stdout': (proc.stdout or '').strip(),
        'stderr': (proc.stderr or '').strip(),
        'exitCode': int(proc.returncode),
    }


def admin_list_telegram_star_gifts() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    script = root / 'tools' / 'import_star_gifts.py'
    if not script.is_file():
        return {'ok': False, 'error': 'import_star_gifts.py not found'}
    session = (
        (config.SHOP_IMPORT_SESSION if hasattr(config, 'SHOP_IMPORT_SESSION') else '')
        or 'mysession'
    )
    env = dict(**os.environ)
    env['PYTHONPATH'] = str(root)
    try:
        proc = subprocess.run(
            ['python3', str(script), '--session', session, '--list-json'],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception as exc:
        return {'ok': False, 'error': f'List failed: {exc}'}
    if proc.returncode != 0:
        return {'ok': False, 'error': (proc.stderr or proc.stdout or '').strip()}
    raw_out = (proc.stdout or '').strip()
    if not raw_out:
        return {'ok': True, 'gifts': []}
    try:
        gifts = json.loads(raw_out)
    except Exception:
        gifts = None
        # Fallback: find the last JSON-looking line (some libs/loggers may
        # print extra lines before JSON payload).
        for line in reversed(raw_out.splitlines()):
            line = line.strip()
            if not line:
                continue
            if not (line.startswith('[') or line.startswith('{')):
                continue
            try:
                gifts = json.loads(line)
                break
            except Exception:
                continue
        if gifts is None:
            return {'ok': False, 'error': 'Invalid list output'}
    if isinstance(gifts, dict):
        gifts = gifts.get('gifts') or []
    if not isinstance(gifts, list):
        return {'ok': False, 'error': 'Invalid list output'}
    return {'ok': True, 'gifts': gifts}


def _owner_public(user: dict | None) -> dict[str, Any]:
    if not user:
        return {}
    return {
        'id': int(user['id']),
        'username': user.get('username') or '',
        'firstName': user.get('first_name') or '',
        'lastName': user.get('last_name') or '',
        'phone': format_phone_display(user.get('phone') or ''),
    }


from handlers.star_gift_rarity import rarity_is_tier, rarity_label, TIER_LABELS


def _upgrade_attr_public(attr: dict | None, storage: Storage,
                         *, pool: list[dict] | None = None) -> dict[str, Any] | None:
    if not attr:
        return None
    doc = None
    doc_id = int(attr.get('document_id') or 0)
    if doc_id > 0:
        doc = storage.get_sticker_document_by_id(doc_id) or {}
    rarity = int(attr.get('rarity_permille') or 100)
    chance_percent = None
    if pool and not rarity_is_tier(rarity):
        same_type = [a for a in pool if a.get('attr_type') == attr.get('attr_type')]
        total = sum(int(a.get('rarity_permille') or 100) for a in same_type
                    if not rarity_is_tier(int(a.get('rarity_permille') or 100)))
        if total > 0:
            chance_percent = round(rarity / total * 100, 2)
    return {
        'id': int(attr['id']),
        'giftId': int(attr.get('gift_id') or 0),
        'type': str(attr.get('attr_type') or ''),
        'name': str(attr.get('name') or ''),
        'rarityPermille': rarity,
        'rarityTier': TIER_LABELS.get(rarity) if rarity_is_tier(rarity) else None,
        'rarityLabel': rarity_label(rarity),
        'chancePercent': chance_percent,
        'documentId': str(doc_id) if doc_id > 0 else '',
        'imagePath': str((doc or {}).get('file_path') or ''),
        'backdropId': int(attr['backdrop_id']) if attr.get('backdrop_id') is not None else None,
        'centerColor': int(attr.get('center_color') or 0),
        'edgeColor': int(attr.get('edge_color') or 0),
        'patternColor': int(attr.get('pattern_color') or 0),
        'textColor': int(attr.get('text_color') or 0),
        'crafted': bool(int(attr.get('crafted') or 0)),
    }


def _upgraded_gift_public(row: dict, storage: Storage,
                          *, pool: list[dict] | None = None) -> dict[str, Any]:
    gift = storage.get_star_gift(int(row['gift_id'])) or {}
    owner = storage.get_user_by_id(int(row['owner_user_id']))
    if pool is None:
        pool = storage.list_star_gift_upgrade_attrs(int(row['gift_id']))
    model = storage.get_star_gift_upgrade_attr_by_id(int(row['model_attr_id'] or 0))
    pattern = storage.get_star_gift_upgrade_attr_by_id(int(row['pattern_attr_id'] or 0))
    backdrop = storage.get_star_gift_upgrade_attr_by_id(int(row['backdrop_attr_id'] or 0))
    model_pub = _upgrade_attr_public(model, storage, pool=pool)
    pattern_pub = _upgrade_attr_public(pattern, storage, pool=pool)
    backdrop_pub = _upgrade_attr_public(backdrop, storage, pool=pool)
    base_doc = storage.get_sticker_document_by_id(int(gift.get('document_id') or 0)) or {}
    return {
        'savedId': int(row['saved_id']),
        'uniqueId': str(int(row['unique_id'] or 0)),
        'uniqueNum': int(row['unique_num'] or 0),
        'uniqueSlug': str(row.get('unique_slug') or ''),
        'giftId': str(int(row['gift_id'])),
        'giftTitle': str(gift.get('title') or f'Gift {row["gift_id"]}'),
        'total': int(gift.get('availability_total') or 0),
        'baseImagePath': str(base_doc.get('file_path') or ''),
        'owner': _owner_public(owner),
        'model': model_pub,
        'pattern': pattern_pub,
        'backdrop': backdrop_pub,
        'card': {
            'centerColor': backdrop_pub['centerColor'] if backdrop_pub else 0,
            'edgeColor': backdrop_pub['edgeColor'] if backdrop_pub else 0,
            'patternColor': backdrop_pub['patternColor'] if backdrop_pub else 0,
            'textColor': backdrop_pub['textColor'] if backdrop_pub else 0,
            'modelImagePath': (model_pub or {}).get('imagePath') or '',
            'patternImagePath': (pattern_pub or {}).get('imagePath') or '',
        },
        'date': int(row.get('date') or 0),
    }


def admin_list_upgraded_gifts(
    *,
    query: str = '',
    gift_id: int = 0,
    offset: int = 0,
    limit: int = 60,
) -> dict[str, Any]:
    storage = _open()
    clauses = ['s.upgraded=1', 's.unsaved=0']
    params: list[Any] = []
    q = (query or '').strip()
    if gift_id > 0:
        clauses.append('s.gift_id=?')
        params.append(int(gift_id))
    if q:
        if q.lstrip('#').isdigit():
            num = int(q.lstrip('#'))
            clauses.append('(s.unique_num=? OR s.saved_id=? OR s.unique_id=?)')
            params.extend([num, num, num])
        else:
            like = f'%{q.lower()}%'
            clauses.append(
                '''(
                    lower(s.unique_slug) LIKE ?
                    OR lower(g.title) LIKE ?
                    OR lower(COALESCE(u.username, '')) LIKE ?
                    OR lower(COALESCE(u.first_name, '')) LIKE ?
                    OR lower(COALESCE(u.last_name, '')) LIKE ?
                    OR COALESCE(u.phone, '') LIKE ?
                    OR CAST(s.owner_user_id AS TEXT)=?
                )'''
            )
            params.extend([like, like, like, like, like, f'%{q}%', q])
    where = ' AND '.join(clauses)
    total_row = storage._conn.execute(
        f'''SELECT COUNT(*) AS c
            FROM saved_star_gifts s
            JOIN star_gifts g ON g.gift_id=s.gift_id
            JOIN users u ON u.id=s.owner_user_id
            WHERE {where}''',
        params,
    ).fetchone()
    rows = storage._conn.execute(
        f'''SELECT s.*
            FROM saved_star_gifts s
            JOIN star_gifts g ON g.gift_id=s.gift_id
            JOIN users u ON u.id=s.owner_user_id
            WHERE {where}
            ORDER BY s.date DESC, s.saved_id DESC
            LIMIT ? OFFSET ?''',
        [*params, int(limit), int(offset)],
    ).fetchall()
    gifts = [_upgraded_gift_public(dict(r), storage) for r in rows]
    return {
        'ok': True,
        'gifts': gifts,
        'total': int(total_row['c'] or 0),
        'offset': int(offset),
        'limit': int(limit),
    }


def admin_get_upgraded_gift(*, saved_id: int = 0, unique_num: int = 0,
                            gift_id: int = 0) -> dict[str, Any]:
    storage = _open()
    row = None
    if saved_id > 0:
        row = storage.get_saved_star_gift_by_id(int(saved_id))
    elif unique_num > 0 and gift_id > 0:
        found = storage._conn.execute(
            '''SELECT * FROM saved_star_gifts
               WHERE upgraded=1 AND gift_id=? AND unique_num=?
               ORDER BY saved_id DESC LIMIT 1''',
            (int(gift_id), int(unique_num)),
        ).fetchone()
        row = dict(found) if found else None
    elif unique_num > 0:
        found = storage._conn.execute(
            '''SELECT * FROM saved_star_gifts
               WHERE upgraded=1 AND unique_num=?
               ORDER BY saved_id DESC LIMIT 1''',
            (int(unique_num),),
        ).fetchone()
        row = dict(found) if found else None
    if not row or not int(row.get('upgraded') or 0):
        return {'ok': False, 'error': 'Улучшенный подарок не найден'}
    gid = int(row['gift_id'])
    pool = storage.list_star_gift_upgrade_attrs(gid)
    gift = _upgraded_gift_public(row, storage, pool=pool)
    grouped = {
        'model': [],
        'pattern': [],
        'backdrop': [],
    }
    for attr in pool:
        pub = _upgrade_attr_public(attr, storage, pool=pool)
        if pub and pub['type'] in grouped:
            grouped[pub['type']].append(pub)
    return {
        'ok': True,
        'gift': gift,
        'availableAttrs': grouped,
    }


def admin_list_gift_upgrade_attrs(gift_id: int) -> dict[str, Any]:
    storage = _open()
    gift = storage.get_star_gift(int(gift_id))
    if not gift:
        return {'ok': False, 'error': 'Подарок не найден'}
    pool = storage.list_star_gift_upgrade_attrs(int(gift_id))
    grouped = {'model': [], 'pattern': [], 'backdrop': []}
    for attr in pool:
        pub = _upgrade_attr_public(attr, storage, pool=pool)
        if pub and pub['type'] in grouped:
            grouped[pub['type']].append(pub)
    return {
        'ok': True,
        'giftId': str(int(gift_id)),
        'giftTitle': str(gift.get('title') or f'Gift {gift_id}'),
        'attrs': grouped,
        'total': len(pool),
    }


def admin_update_gift_upgrade_attr(
    attr_id: int,
    *,
    name: str | None = None,
    rarity_permille: int | None = None,
    document_id: int | None = None,
    backdrop_id: int | None = None,
    center_color: int | None = None,
    edge_color: int | None = None,
    pattern_color: int | None = None,
    text_color: int | None = None,
    crafted: bool | None = None,
) -> dict[str, Any]:
    storage = _open()
    current = storage.get_star_gift_upgrade_attr_by_id(int(attr_id))
    if not current:
        return {'ok': False, 'error': 'Атрибут не найден'}
    fields: dict[str, Any] = {}
    if name is not None:
        fields['name'] = name
    if rarity_permille is not None:
        fields['rarity_permille'] = int(rarity_permille)
    if document_id is not None:
        fields['document_id'] = int(document_id)
    if backdrop_id is not None:
        fields['backdrop_id'] = int(backdrop_id)
    if center_color is not None:
        fields['center_color'] = int(center_color)
    if edge_color is not None:
        fields['edge_color'] = int(edge_color)
    if pattern_color is not None:
        fields['pattern_color'] = int(pattern_color)
    if text_color is not None:
        fields['text_color'] = int(text_color)
    if crafted is not None:
        fields['crafted'] = bool(crafted)
    updated = storage.update_star_gift_upgrade_attr(int(attr_id), **fields)
    if not updated:
        return {'ok': False, 'error': 'Не удалось обновить атрибут'}
    pool = storage.list_star_gift_upgrade_attrs(int(updated.get('gift_id') or 0))
    return {
        'ok': True,
        'attr': _upgrade_attr_public(updated, storage, pool=pool),
    }


def admin_update_upgraded_gift_attrs(
    saved_id: int,
    *,
    model_attr_id: int | None = None,
    pattern_attr_id: int | None = None,
    backdrop_attr_id: int | None = None,
) -> dict[str, Any]:
    storage = _open()
    row = storage.get_saved_star_gift_by_id(int(saved_id))
    if not row or not int(row.get('upgraded') or 0):
        return {'ok': False, 'error': 'Улучшенный подарок не найден'}
    gid = int(row['gift_id'])
    pool = storage.list_star_gift_upgrade_attrs(gid)
    pool_ids = {int(a['id']) for a in pool}
    for label, value in (
        ('model_attr_id', model_attr_id),
        ('pattern_attr_id', pattern_attr_id),
        ('backdrop_attr_id', backdrop_attr_id),
    ):
        if value is None:
            continue
        aid = int(value)
        if aid > 0 and aid not in pool_ids:
            return {'ok': False, 'error': f'Неверный атрибут для {label}'}
    updated = storage.update_saved_star_gift_attrs(
        int(saved_id),
        model_attr_id=model_attr_id,
        pattern_attr_id=pattern_attr_id,
        backdrop_attr_id=backdrop_attr_id,
    )
    if not updated:
        return {'ok': False, 'error': 'Не удалось обновить подарок'}
    return {
        'ok': True,
        'gift': _upgraded_gift_public(updated, storage, pool=pool),
    }
