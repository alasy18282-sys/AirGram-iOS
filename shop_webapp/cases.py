"""Loot cases — admin CRUD, opening, inventory, gift delivery."""
from __future__ import annotations

import json
import os
import random
import secrets
import threading
import time
from pathlib import Path
from typing import Any

import config
from db.storage import Storage

CASE_STATUSES_HELD = ('held',)
CASE_STATUSES_DONE = ('sold', 'withdrawn', 'delivered')
DEFAULT_CASE_OPEN_BATCH = 3
MAX_ADMIN_CASE_OPEN_BATCH = 50
_cases_repair_done = False


def _open(storage: Storage | None = None) -> Storage:
    if storage is not None:
        ensure_cases_schema(storage)
        return storage
    from shop_webapp.service import _open as open_shop

    s = open_shop()
    ensure_cases_schema(s)
    return s


def ensure_cases_schema(storage: Storage) -> None:
    storage._conn.executescript('''
        CREATE TABLE IF NOT EXISTS shop_cases (
            case_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            cover_path TEXT NOT NULL,
            price_xtr INTEGER NOT NULL,
            price_airgram INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shop_case_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            gift_id INTEGER NOT NULL,
            weight INTEGER NOT NULL DEFAULT 1000,
            UNIQUE(case_id, gift_id)
        );
        CREATE TABLE IF NOT EXISTS shop_case_orders (
            nonce TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            case_id TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            xtr INTEGER NOT NULL,
            airgram INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'xtr',
            status TEXT NOT NULL DEFAULT 'pending',
            payment_id TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shop_case_wins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            case_id TEXT NOT NULL,
            gift_id INTEGER NOT NULL,
            stars_value INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'held',
            order_nonce TEXT,
            withdraw_username TEXT,
            saved_gift_id INTEGER,
            delivery_error TEXT,
            created_at INTEGER NOT NULL,
            resolved_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_case_wins_user
            ON shop_case_wins(user_id, status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_case_wins_created
            ON shop_case_wins(created_at DESC);
        CREATE TABLE IF NOT EXISTS shop_case_live_presence (
            viewer_key TEXT PRIMARY KEY,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_case_items_case
            ON shop_case_items(case_id);
        CREATE INDEX IF NOT EXISTS idx_case_orders_case_status
            ON shop_case_orders(case_id, status);
        CREATE TABLE IF NOT EXISTS shop_admin_case_batch (
            telegram_user_id INTEGER PRIMARY KEY,
            max_batch INTEGER NOT NULL DEFAULT 3,
            updated_at INTEGER NOT NULL
        );
    ''')
    storage._conn.commit()
    try:
        storage._conn.execute(
            'ALTER TABLE shop_cases ADD COLUMN admin_only INTEGER NOT NULL DEFAULT 0',
        )
        storage._conn.commit()
    except Exception:
        pass
    try:
        storage._conn.execute(
            'ALTER TABLE shop_cases ADD COLUMN price_airgram INTEGER NOT NULL DEFAULT 0',
        )
        storage._conn.commit()
    except Exception:
        pass
    try:
        storage._conn.execute(
            'ALTER TABLE shop_case_orders ADD COLUMN airgram INTEGER NOT NULL DEFAULT 0',
        )
        storage._conn.commit()
    except Exception:
        pass
    try:
        storage._conn.execute(
            "ALTER TABLE shop_case_orders ADD COLUMN currency TEXT NOT NULL DEFAULT 'xtr'",
        )
        storage._conn.commit()
    except Exception:
        pass
    try:
        storage._conn.execute(
            'ALTER TABLE shop_cases ADD COLUMN open_limit INTEGER NOT NULL DEFAULT 0',
        )
        storage._conn.commit()
    except Exception:
        pass
    try:
        storage._conn.execute(
            'ALTER TABLE shop_case_orders ADD COLUMN buyer_telegram_id INTEGER NOT NULL DEFAULT 0',
        )
        storage._conn.commit()
    except Exception:
        pass
    global _cases_repair_done
    if not _cases_repair_done:
        _repair_delivered_case_gifts(storage)
        _cases_repair_done = True


def _case_opens_used_map(storage: Storage) -> dict[str, int]:
    rows = storage._conn.execute(
        '''SELECT case_id, COALESCE(SUM(count), 0) AS n
           FROM shop_case_orders WHERE status=? GROUP BY case_id''',
        ('paid',),
    ).fetchall()
    return {str(r['case_id']): int(r['n']) for r in rows}


def _case_opens_used(storage: Storage, case_id: str) -> int:
    row = storage._conn.execute(
        '''SELECT COALESCE(SUM(count), 0) AS n FROM shop_case_orders
           WHERE case_id=? AND status=?''',
        (case_id, 'paid'),
    ).fetchone()
    return int(row['n'] if row else 0)


def _attach_open_limit_stats(
    storage: Storage,
    data: dict[str, Any],
    case_id: str,
    row: dict,
    *,
    opens_used: int | None = None,
) -> None:
    open_limit = int(row.get('open_limit') or 0)
    data['openLimit'] = open_limit
    if opens_used is None:
        opens_used = _case_opens_used(storage, case_id)
    if open_limit <= 0:
        data['opensTotal'] = opens_used
        data['opensRemaining'] = None
        data['soldOut'] = False
        return
    used = opens_used
    data['opensTotal'] = used
    data['opensRemaining'] = max(0, open_limit - used)
    data['soldOut'] = used >= open_limit


def _shop_admin_telegram_ids() -> set[int]:
    raw = os.environ.get('SHOP_ADMIN_IDS', '8549770368,8014265847')
    return {
        int(part.strip())
        for part in raw.split(',')
        if part.strip().isdigit()
    }


def _is_shop_admin(
    storage: Storage,
    user_id: int = 0,
    *,
    telegram_user_id: int = 0,
) -> bool:
    tg = int(telegram_user_id or 0)
    if tg <= 0 and user_id > 0:
        from shop_webapp.service import _telegram_id_for_shop_user

        resolved = _telegram_id_for_shop_user(storage, user_id)
        if resolved:
            tg = int(resolved)
    return tg > 0 and tg in _shop_admin_telegram_ids()


def _resolve_shop_admin_telegram_id(
    storage: Storage,
    user_id: int = 0,
    *,
    telegram_user_id: int = 0,
) -> int:
    tg = int(telegram_user_id or 0)
    if tg <= 0 and user_id > 0:
        from shop_webapp.service import _telegram_id_for_shop_user

        resolved = _telegram_id_for_shop_user(storage, user_id)
        if resolved:
            tg = int(resolved)
    return tg


def get_case_open_batch_max(
    storage: Storage,
    user_id: int = 0,
    *,
    telegram_user_id: int = 0,
) -> int:
    tg = _resolve_shop_admin_telegram_id(
        storage, user_id, telegram_user_id=telegram_user_id,
    )
    if tg <= 0 or tg not in _shop_admin_telegram_ids():
        return DEFAULT_CASE_OPEN_BATCH
    row = storage._conn.execute(
        'SELECT max_batch FROM shop_admin_case_batch WHERE telegram_user_id=?',
        (tg,),
    ).fetchone()
    if not row:
        return DEFAULT_CASE_OPEN_BATCH
    return max(1, min(MAX_ADMIN_CASE_OPEN_BATCH, int(row['max_batch'])))


def admin_get_case_batch_limit(*, telegram_user_id: int) -> dict[str, Any]:
    storage = _open()
    tg = int(telegram_user_id or 0)
    if tg <= 0 or tg not in _shop_admin_telegram_ids():
        return {'ok': False, 'error': 'Нет доступа'}
    limit = get_case_open_batch_max(storage, telegram_user_id=tg)
    return {
        'ok': True,
        'maxBatch': limit,
        'defaultBatch': DEFAULT_CASE_OPEN_BATCH,
        'maxAllowed': MAX_ADMIN_CASE_OPEN_BATCH,
    }


def admin_set_case_batch_limit(
    *,
    telegram_user_id: int,
    max_batch: int,
) -> dict[str, Any]:
    storage = _open()
    tg = int(telegram_user_id or 0)
    if tg <= 0 or tg not in _shop_admin_telegram_ids():
        return {'ok': False, 'error': 'Нет доступа'}
    try:
        limit = int(max_batch)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверное значение'}
    if limit < 1 or limit > MAX_ADMIN_CASE_OPEN_BATCH:
        return {
            'ok': False,
            'error': f'Укажите число от 1 до {MAX_ADMIN_CASE_OPEN_BATCH}',
        }
    now = int(time.time())
    storage._conn.execute(
        '''INSERT INTO shop_admin_case_batch
           (telegram_user_id, max_batch, updated_at)
           VALUES (?,?,?)
           ON CONFLICT(telegram_user_id) DO UPDATE SET
               max_batch=excluded.max_batch,
               updated_at=excluded.updated_at''',
        (tg, limit, now),
    )
    storage._conn.commit()
    return {
        'ok': True,
        'maxBatch': limit,
        'defaultBatch': DEFAULT_CASE_OPEN_BATCH,
        'maxAllowed': MAX_ADMIN_CASE_OPEN_BATCH,
    }


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_cover_path(raw: str) -> str | None:
    text = (raw or '').strip()
    if not text:
        return None
    p = Path(text)
    if not p.is_absolute():
        p = _root() / text.lstrip('/')
    try:
        p = p.resolve()
    except OSError:
        return None
    root = _root().resolve()
    if root not in p.parents and p != root:
        return None
    if not p.is_file():
        return None
    ext = p.suffix.lower()
    if ext not in ('.png', '.gif', '.webp', '.jpg', '.jpeg', '.webm'):
        return None
    return str(p.relative_to(root)).replace('\\', '/')


def _gift_public(storage: Storage, gift: dict) -> dict[str, Any]:
    from shop_webapp.service import _gift_public

    item = _gift_public(gift)
    doc_id = int(gift.get('document_id') or 0)
    if doc_id > 0 and not item.get('imagePath'):
        doc = storage.get_sticker_document_by_id(doc_id) or {}
        item['imagePath'] = str(doc.get('file_path') or '')
    return item


def _case_row(storage: Storage, case_id: str) -> dict | None:
    row = storage._conn.execute(
        'SELECT * FROM shop_cases WHERE case_id=?', (case_id,),
    ).fetchone()
    return dict(row) if row else None


def _star_gifts_map(storage: Storage, gift_ids: list[int]) -> dict[int, dict]:
    unique = sorted({int(g) for g in gift_ids if int(g) > 0})
    if not unique:
        return {}
    placeholders = ','.join('?' * len(unique))
    rows = storage._conn.execute(
        f'SELECT * FROM star_gifts WHERE gift_id IN ({placeholders})',
        unique,
    ).fetchall()
    return {int(r['gift_id']): dict(r) for r in rows}


def _case_items(storage: Storage, case_id: str) -> list[dict[str, Any]]:
    rows = storage._conn.execute(
        '''SELECT i.*, g.title, g.stars, g.document_id
           FROM shop_case_items i
           JOIN star_gifts g ON g.gift_id = i.gift_id
           WHERE i.case_id=?
           ORDER BY i.weight DESC, g.stars DESC''',
        (case_id,),
    ).fetchall()
    gifts_map = _star_gifts_map(storage, [int(r['gift_id']) for r in rows])
    out: list[dict[str, Any]] = []
    for row in rows:
        gift = gifts_map.get(int(row['gift_id']))
        if not gift:
            continue
        pub = _gift_public(storage, gift)
        out.append({
            'giftId': pub['giftId'],
            'title': pub['title'],
            'stars': pub['stars'],
            'imagePath': pub.get('imagePath') or '',
            'weight': int(row['weight']),
        })
    return out


def _case_public(storage: Storage, row: dict, *, with_items: bool = False,
                 viewer_is_admin: bool = False,
                 opens_used_map: dict[str, int] | None = None) -> dict[str, Any]:
    admin_only = bool(int(row.get('admin_only') or 0))
    base_price_xtr = int(row['price_xtr'])
    base_price_airgram = int(row.get('price_airgram') or 0)
    price_xtr = base_price_xtr
    price_airgram = base_price_airgram
    is_free = bool(admin_only and viewer_is_admin)
    if is_free:
        price_xtr = 0
        price_airgram = 0
    data = {
        'caseId': row['case_id'],
        'title': row['title'],
        'coverPath': row['cover_path'],
        'priceXtr': price_xtr,
        'basePriceXtr': base_price_xtr,
        'priceKoalGram': price_airgram,
        'basePriceKoalGram': base_price_airgram,
        'payWithXtr': base_price_xtr > 0,
        'payWithKoalGram': base_price_airgram > 0,
        'isFree': is_free,
        'adminOnly': admin_only,
        'sortOrder': int(row['sort_order'] or 0),
    }
    opens_used = None
    if opens_used_map is not None:
        opens_used = opens_used_map.get(row['case_id'], 0)
    _attach_open_limit_stats(
        storage, data, row['case_id'], dict(row), opens_used=opens_used,
    )
    if with_items:
        items = _case_items(storage, row['case_id'])
        total_w = sum(int(i['weight']) for i in items) or 1
        for item in items:
            item['chancePercent'] = round(item['weight'] * 100 / total_w, 2)
        data['items'] = items
    return data


def compute_auto_weights(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cheaper gifts get higher weight (inverse price)."""
    scored: list[tuple[int, int, int]] = []
    for item in items:
        gid = int(item['giftId'])
        stars = max(1, int(item.get('stars') or 1))
        scored.append((gid, stars, max(1, 1_000_000 // stars)))
    total = sum(s for _, _, s in scored) or 1
    return [
        {
            'giftId': str(gid),
            'stars': stars,
            'weight': max(1, round(score / total * 10_000)),
        }
        for gid, stars, score in scored
    ]


def admin_list_cases() -> dict[str, Any]:
    storage = _open()
    rows = storage._conn.execute(
        'SELECT * FROM shop_cases ORDER BY sort_order, title',
    ).fetchall()
    cases = [
        _case_public(storage, dict(r), with_items=True, viewer_is_admin=True)
        for r in rows
    ]
    gifts = storage.list_star_gifts()
    catalog = [
        _gift_public(storage, g)
        for g in gifts
        if not int(g.get('shop_hidden') or 0)
    ]
    return {'ok': True, 'cases': cases, 'catalog': catalog}


def admin_save_case(
    *,
    case_id: str = '',
    title: str,
    cover_path: str,
    price_xtr: int,
    price_airgram: int = 0,
    items: list[dict[str, Any]],
    sort_order: int = 0,
    admin_only: bool = False,
    open_limit: int = 0,
) -> dict[str, Any]:
    storage = _open()
    title = (title or '').strip()
    if not title:
        return {'ok': False, 'error': 'Укажите название кейса'}
    cover = _resolve_cover_path(cover_path)
    if not cover:
        return {'ok': False, 'error': 'Обложка не найдена на сервере'}
    try:
        price = int(price_xtr)
        price_ag = int(price_airgram)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверная цена'}
    if not admin_only and price <= 0 and price_ag <= 0:
        return {
            'ok': False,
            'error': 'Укажите цену в Telegram Stars и/или звёздах KoalGram',
        }
    if price < 0 or price_ag < 0:
        return {'ok': False, 'error': 'Цена не может быть отрицательной'}
    try:
        limit = max(0, int(open_limit))
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверный лимит открытий'}
    if not items:
        return {'ok': False, 'error': 'Добавьте хотя бы один подарок'}

    parsed_items: list[tuple[int, int]] = []
    for raw in items:
        try:
            gid = int(raw.get('giftId') or raw.get('gift_id'))
            weight = int(raw.get('weight') or 0)
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'Неверный подарок в содержимом'}
        if weight <= 0:
            return {'ok': False, 'error': 'Вес подарка должен быть > 0'}
        if not storage.get_star_gift(gid):
            return {'ok': False, 'error': f'Подарок {gid} не найден'}
        parsed_items.append((gid, weight))

    now = int(time.time())
    cid = (case_id or '').strip() or secrets.token_hex(8)
    existing = _case_row(storage, cid)
    if existing:
        storage._conn.execute(
            '''UPDATE shop_cases
               SET title=?, cover_path=?, price_xtr=?, price_airgram=?,
                   sort_order=?, admin_only=?, open_limit=?, updated_at=?
               WHERE case_id=?''',
            (title, cover, price, price_ag, int(sort_order), int(admin_only),
             limit, now, cid),
        )
        storage._conn.execute(
            'DELETE FROM shop_case_items WHERE case_id=?', (cid,),
        )
    else:
        storage._conn.execute(
            '''INSERT INTO shop_cases
               (case_id, title, cover_path, price_xtr, price_airgram, sort_order,
                admin_only, open_limit, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (cid, title, cover, price, price_ag, int(sort_order),
             int(admin_only), limit, now, now),
        )
    for gid, weight in parsed_items:
        storage._conn.execute(
            '''INSERT INTO shop_case_items (case_id, gift_id, weight)
               VALUES (?,?,?)''',
            (cid, gid, weight),
        )
    storage._conn.commit()
    row = _case_row(storage, cid)
    return {'ok': True, 'case': _case_public(storage, row, with_items=True, viewer_is_admin=True)}


def admin_delete_case(case_id: str) -> dict[str, Any]:
    storage = _open()
    cid = (case_id or '').strip()
    if not _case_row(storage, cid):
        return {'ok': False, 'error': 'Кейс не найден'}
    storage._conn.execute('DELETE FROM shop_case_items WHERE case_id=?', (cid,))
    storage._conn.execute('DELETE FROM shop_cases WHERE case_id=?', (cid,))
    storage._conn.commit()
    return {'ok': True, 'caseId': cid}


def admin_auto_weights(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {'ok': False, 'error': 'Нет подарков'}
    weighted = compute_auto_weights(items)
    return {'ok': True, 'items': weighted}


def admin_auto_weights(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {'ok': False, 'error': 'Нет подарков'}
    weighted = compute_auto_weights(items)
    return {'ok': True, 'items': weighted}


def _normalize_case_stats_period(period: str) -> str:
    p = (period or 'month').strip().lower()
    if p in ('day', '1d', '24h', 'день'):
        return 'day'
    if p in ('week', '7d', 'неделя'):
        return 'week'
    if p in ('month', '30d', 'месяц'):
        return 'month'
    return 'month'


def _case_stats_chart(storage: Storage, period: str,
                      now: int) -> tuple[list[dict[str, Any]], int, int]:
    period = _normalize_case_stats_period(period)
    chart: list[dict[str, Any]] = []
    if period == 'day':
        since = now - 24 * 3600
        start_hour = ((now // 3600) - 23) * 3600
        rows = storage._conn.execute(
            '''SELECT ((created_at / 3600) * 3600) AS bucket,
                      COALESCE(SUM(count), 0) AS opens
               FROM shop_case_orders
               WHERE status=? AND created_at >= ?
               GROUP BY bucket
               ORDER BY bucket''',
            ('paid', since),
        ).fetchall()
        opens_map = {int(r['bucket']): int(r['opens']) for r in rows}
        for i in range(24):
            ts = start_hour + i * 3600
            chart.append({
                'label': time.strftime('%H:%M', time.gmtime(ts)),
                'opens': opens_map.get(ts, 0),
            })
        since = start_hour
    elif period == 'week':
        day_start = (now // 86400) * 86400
        since = day_start - 6 * 86400
        rows = storage._conn.execute(
            '''SELECT date(created_at, 'unixepoch') AS day,
                      COALESCE(SUM(count), 0) AS opens
               FROM shop_case_orders
               WHERE status=? AND created_at >= ?
               GROUP BY day
               ORDER BY day''',
            ('paid', since),
        ).fetchall()
        opens_by_day = {str(r['day']): int(r['opens']) for r in rows}
        for offset in range(6, -1, -1):
            day_ts = day_start - offset * 86400
            day_key = time.strftime('%Y-%m-%d', time.gmtime(day_ts))
            chart.append({
                'label': time.strftime('%d.%m', time.gmtime(day_ts)),
                'day': day_key,
                'opens': opens_by_day.get(day_key, 0),
            })
    else:
        day_start = (now // 86400) * 86400
        since = day_start - 29 * 86400
        rows = storage._conn.execute(
            '''SELECT date(created_at, 'unixepoch') AS day,
                      COALESCE(SUM(count), 0) AS opens
               FROM shop_case_orders
               WHERE status=? AND created_at >= ?
               GROUP BY day
               ORDER BY day''',
            ('paid', since),
        ).fetchall()
        opens_by_day = {str(r['day']): int(r['opens']) for r in rows}
        for offset in range(29, -1, -1):
            day_ts = day_start - offset * 86400
            day_key = time.strftime('%Y-%m-%d', time.gmtime(day_ts))
            chart.append({
                'label': time.strftime('%d.%m', time.gmtime(day_ts)),
                'day': day_key,
                'opens': opens_by_day.get(day_key, 0),
            })
    period_row = storage._conn.execute(
        '''SELECT COALESCE(SUM(count), 0) AS n FROM shop_case_orders
           WHERE status=? AND created_at >= ?''',
        ('paid', since),
    ).fetchone()
    period_opens = int(period_row['n'] if period_row else 0)
    return chart, since, period_opens


def admin_case_stats(*, period: str = 'month') -> dict[str, Any]:
    storage = _open()
    now = int(time.time())
    total_row = storage._conn.execute(
        '''SELECT COALESCE(SUM(count), 0) AS n FROM shop_case_orders
           WHERE status=?''',
        ('paid',),
    ).fetchone()
    total_opens = int(total_row['n'] if total_row else 0)
    pending_row = storage._conn.execute(
        '''SELECT COALESCE(SUM(count), 0) AS n FROM shop_case_orders
           WHERE status='pending' AND created_at > ?''',
        (now - 300,),
    ).fetchone()
    currently_opening = int(pending_row['n'] if pending_row else 0)
    norm_period = _normalize_case_stats_period(period)
    chart, _, period_opens = _case_stats_chart(storage, norm_period, now)
    opens_map = _case_opens_used_map(storage)
    by_case: list[dict[str, Any]] = []
    for row in storage._conn.execute(
        'SELECT * FROM shop_cases ORDER BY sort_order, title',
    ).fetchall():
        item = dict(row)
        cid = item['case_id']
        opens = opens_map.get(cid, 0)
        limit = int(item.get('open_limit') or 0)
        by_case.append({
            'caseId': cid,
            'title': item['title'],
            'opens': opens,
            'openLimit': limit,
            'opensRemaining': max(0, limit - opens) if limit > 0 else None,
        })
    return {
        'ok': True,
        'period': norm_period,
        'periodOpens': period_opens,
        'totalOpens': total_opens,
        'currentlyOpening': currently_opening,
        'chart': chart,
        'byCase': by_case,
    }


def list_cases_public(*, user_id: int = 0, telegram_user_id: int = 0) -> dict[str, Any]:
    storage = _open()
    viewer_is_admin = _is_shop_admin(
        storage, user_id, telegram_user_id=telegram_user_id,
    )
    rows = storage._conn.execute(
        'SELECT * FROM shop_cases ORDER BY sort_order, title',
    ).fetchall()
    opens_map = _case_opens_used_map(storage)
    cases = []
    for row in rows:
        item = dict(row)
        if int(item.get('admin_only') or 0) and not viewer_is_admin:
            continue
        cases.append(
            _case_public(
                storage, item, viewer_is_admin=viewer_is_admin,
                opens_used_map=opens_map,
            ),
        )
    max_batch = get_case_open_batch_max(
        storage, user_id, telegram_user_id=telegram_user_id,
    )
    return {
        'ok': True,
        'cases': cases,
        'isAdmin': viewer_is_admin,
        'maxCaseOpenBatch': max_batch,
    }


def get_case_public(
    case_id: str,
    *,
    user_id: int = 0,
    telegram_user_id: int = 0,
) -> dict[str, Any]:
    storage = _open()
    row = _case_row(storage, (case_id or '').strip())
    if not row:
        return {'ok': False, 'error': 'Кейс не найден'}
    viewer_is_admin = _is_shop_admin(
        storage, user_id, telegram_user_id=telegram_user_id,
    )
    if int(row.get('admin_only') or 0) and not viewer_is_admin:
        return {'ok': False, 'error': 'Кейс не найден'}
    max_batch = get_case_open_batch_max(
        storage, user_id, telegram_user_id=telegram_user_id,
    )
    return {
        'ok': True,
        'case': _case_public(storage, row, with_items=True, viewer_is_admin=viewer_is_admin),
        'isAdmin': viewer_is_admin,
        'maxCaseOpenBatch': max_batch,
    }


def demo_roll(case_id: str) -> dict[str, Any]:
    storage = _open()
    row = _case_row(storage, (case_id or '').strip())
    if not row:
        return {'ok': False, 'error': 'Кейс не найден'}
    items = _case_items(storage, row['case_id'])
    if not items:
        return {'ok': False, 'error': 'Кейс пуст'}
    picked = _roll_item(items)
    return {'ok': True, 'demo': True, 'item': picked}


def _roll_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(int(i['weight']) for i in items)
    if total <= 0:
        return items[0]
    point = random.randint(1, total)
    acc = 0
    for item in items:
        acc += int(item['weight'])
        if point <= acc:
            return item
    return items[-1]


def create_case_order(
    user_id: int,
    case_id: str,
    count: int = 1,
    *,
    telegram_user_id: int = 0,
    currency: str = 'auto',
) -> dict[str, Any]:
    storage = _open()
    cid = (case_id or '').strip()
    row = _case_row(storage, cid)
    if not row:
        return {'ok': False, 'error': 'Кейс не найден'}
    admin_only = bool(int(row.get('admin_only') or 0))
    is_admin = _is_shop_admin(storage, user_id, telegram_user_id=telegram_user_id)
    if admin_only and not is_admin:
        return {'ok': False, 'error': 'Кейс только для админов'}
    max_batch = get_case_open_batch_max(
        storage, user_id, telegram_user_id=telegram_user_id,
    )
    try:
        n = max(1, min(max_batch, int(count)))
    except (TypeError, ValueError):
        n = 1
    items = _case_items(storage, cid)
    if not items:
        return {'ok': False, 'error': 'Кейс пуст'}
    open_limit = int(row.get('open_limit') or 0)
    if open_limit > 0:
        used = _case_opens_used(storage, cid)
        if used >= open_limit:
            return {'ok': False, 'error': 'Лимит открытий исчерпан'}
        if used + n > open_limit:
            remaining = max(0, open_limit - used)
            return {
                'ok': False,
                'error': f'Осталось открытий: {remaining}',
            }
    unit_xtr = int(row['price_xtr'])
    unit_airgram = int(row.get('price_airgram') or 0)
    if admin_only and is_admin:
        total_xtr = 0
        total_airgram = 0
        pay_currency = 'free'
    else:
        curr = (currency or 'auto').strip().lower()
        if curr == 'auto':
            if unit_xtr > 0 and unit_airgram <= 0:
                curr = 'xtr'
            elif unit_airgram > 0 and unit_xtr <= 0:
                curr = 'airgram'
            elif unit_xtr > 0 and unit_airgram > 0:
                return {'ok': False, 'error': 'Выберите способ оплаты'}
            else:
                return {'ok': False, 'error': 'Кейс недоступен для покупки'}
        if curr in ('xtr', 'tg', 'telegram'):
            curr = 'xtr'
            if unit_xtr <= 0:
                return {'ok': False, 'error': 'Кейс нельзя купить за Telegram Stars'}
            total_xtr = unit_xtr * n
            total_airgram = 0
            pay_currency = 'xtr'
        elif curr in ('airgram', 'balance', 'ag'):
            curr = 'airgram'
            if unit_airgram <= 0:
                return {'ok': False, 'error': 'Кейс нельзя купить за звёзды KoalGram'}
            total_xtr = 0
            total_airgram = unit_airgram * n
            pay_currency = 'airgram'
        elif curr in ('free',):
            if not (admin_only and is_admin):
                return {'ok': False, 'error': 'Кейс недоступен бесплатно'}
            total_xtr = 0
            total_airgram = 0
            pay_currency = 'free'
        else:
            return {'ok': False, 'error': 'Неверный способ оплаты'}
    nonce = secrets.token_hex(16)
    storage._conn.execute(
        '''INSERT INTO shop_case_orders
           (nonce, user_id, case_id, count, xtr, airgram, currency, created_at)
           VALUES (?,?,?,?,?,?,?,?)''',
        (nonce, user_id, cid, n, total_xtr, total_airgram, pay_currency,
         int(time.time())),
    )
    storage._conn.commit()
    return {
        'ok': True,
        'nonce': nonce,
        'caseId': cid,
        'count': n,
        'xtr': total_xtr,
        'airgram': total_airgram,
        'currency': pay_currency,
        'unitXtr': 0 if (admin_only and is_admin) else unit_xtr,
        'unitKoalGram': 0 if (admin_only and is_admin) else unit_airgram,
        'free': pay_currency == 'free',
        'payload': f'shop_case:{user_id}:{cid}:{n}:{nonce}',
        'title': f'{row["title"]} ×{n}',
    }


def validate_case_order(payload: str, xtr_amount: int) -> dict[str, Any]:
    parts = (payload or '').split(':')
    if len(parts) != 5 or parts[0] != 'shop_case':
        return {'ok': False, 'error': 'Неверный payload'}
    try:
        user_id = int(parts[1])
        count = int(parts[3])
        nonce = parts[4]
    except ValueError:
        return {'ok': False, 'error': 'Неверный payload'}
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_case_orders WHERE nonce=?', (nonce,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    order = dict(row)
    if order['status'] != 'pending':
        return {'ok': False, 'error': 'Заказ уже обработан'}
    if int(order['user_id']) != user_id or int(order['count']) != count:
        return {'ok': False, 'error': 'Несовпадение заказа'}
    pay_currency = (order.get('currency') or 'xtr').lower()
    if pay_currency != 'xtr':
        return {'ok': False, 'error': 'Заказ не для Telegram Stars'}
    if int(order['xtr']) != int(xtr_amount):
        return {'ok': False, 'error': 'Неверная сумма'}
    return {'ok': True}


def complete_case_order(
    payload: str,
    payment_id: str,
    buyer_telegram_id: int = 0,
) -> dict[str, Any]:
    parts = (payload or '').split(':')
    if len(parts) != 5 or parts[0] != 'shop_case':
        return {'ok': False, 'error': 'Неверный payload'}
    try:
        user_id = int(parts[1])
        case_id = parts[2]
        count = int(parts[3])
        nonce = parts[4]
    except ValueError:
        return {'ok': False, 'error': 'Неверный payload'}
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_case_orders WHERE nonce=?', (nonce,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    if row['status'] == 'paid':
        return {'ok': True, 'already': True, 'wins': _wins_for_order(storage, nonce)}
    if int(row['user_id']) != user_id or row['case_id'] != case_id:
        return {'ok': False, 'error': 'Несовпадение заказа'}
    items = _case_items(storage, case_id)
    if not items:
        return {'ok': False, 'error': 'Кейс пуст'}
    storage._conn.execute(
        '''UPDATE shop_case_orders
           SET status=?, payment_id=?, buyer_telegram_id=?
           WHERE nonce=?''',
        ('paid', payment_id, int(buyer_telegram_id or 0), nonce),
    )
    win_ids: list[int] = []
    now = int(time.time())
    for _ in range(count):
        picked = _roll_item(items)
        gid = int(picked['giftId'])
        stars = int(picked['stars'])
        cur = storage._conn.execute(
            '''INSERT INTO shop_case_wins
               (user_id, case_id, gift_id, stars_value, status, order_nonce, created_at)
               VALUES (?,?,?,?,?,?,?)''',
            (user_id, case_id, gid, stars, 'held', nonce, now),
        )
        win_ids.append(int(cur.lastrowid))
    storage._conn.commit()
    wins = _wins_public_batch(storage, win_ids)
    from shop_webapp.service import bump_top_donors_version
    bump_top_donors_version()
    return {'ok': True, 'product': 'case', 'wins': wins, 'count': count}


def pay_case_order(
    user_id: int,
    nonce: str,
    buyer_telegram_id: int = 0,
) -> dict[str, Any]:
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_case_orders WHERE nonce=?', ((nonce or '').strip(),),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заказ не найден'}
    order = dict(row)
    if order['status'] != 'pending':
        return {'ok': False, 'error': 'Заказ уже обработан'}
    if int(order['user_id']) != int(user_id):
        return {'ok': False, 'error': 'Несовпадение заказа'}
    pay_currency = (order.get('currency') or 'xtr').lower()
    if pay_currency != 'airgram':
        return {'ok': False, 'error': 'Заказ не для оплаты звёздами KoalGram'}
    amount = int(order.get('airgram') or 0)
    if amount <= 0:
        return {'ok': False, 'error': 'Неверная сумма'}
    balance = storage.get_stars_balance(user_id)
    if not config.INFINITE_STARS and balance < amount:
        return {'ok': False, 'error': 'Недостаточно звёзд на балансе'}
    case_row = _case_row(storage, order['case_id'])
    title = (case_row or {}).get('title') or 'Кейс'
    storage.adjust_stars_balance(
        user_id,
        -amount,
        title=f'Открытие кейса «{title}»',
        peer_user_id=config.SERVICE_USER_ID,
    )
    payload = (
        f'shop_case:{user_id}:{order["case_id"]}:{int(order["count"])}:{order["nonce"]}'
    )
    result = complete_case_order(
        payload,
        f'airgram_balance:{order["nonce"]}',
        buyer_telegram_id=buyer_telegram_id,
    )
    if not result.get('ok'):
        storage.adjust_stars_balance(
            user_id,
            amount,
            refund=True,
            title=f'Возврат за кейс «{title}»',
            peer_user_id=config.SERVICE_USER_ID,
        )
        return result
    result['balance'] = storage.get_stars_balance(user_id)
    result['airgram'] = amount
    from shop_webapp.service import _profile

    user_row = storage.get_user_by_id(user_id)
    if user_row:
        result['user'] = _profile(storage, user_row)
    return result


def _win_row_public(
    storage: Storage,
    row: dict[str, Any],
    *,
    doc_cache: dict[int, str] | None = None,
) -> dict[str, Any]:
    if doc_cache is None:
        doc_cache = {}
    doc_id = int(row.get('document_id') or 0)
    image_path = ''
    if doc_id > 0:
        if doc_id not in doc_cache:
            doc = storage.get_sticker_document_by_id(doc_id) or {}
            doc_cache[doc_id] = str(doc.get('file_path') or '')
        image_path = doc_cache[doc_id]
    gift_title = row.get('gift_title')
    return {
        'winId': int(row['id']),
        'caseId': row['case_id'],
        'caseTitle': row.get('case_title') or '',
        'giftId': str(row['gift_id']),
        'title': gift_title or f'Gift {row["gift_id"]}',
        'stars': int(row['stars_value']),
        'imagePath': image_path,
        'status': row['status'],
        'withdrawUsername': row['withdraw_username'],
    }


def _wins_public_batch(storage: Storage, win_ids: list[int]) -> list[dict[str, Any]]:
    unique = [int(w) for w in win_ids if int(w) > 0]
    if not unique:
        return []
    placeholders = ','.join('?' * len(unique))
    rows = storage._conn.execute(
        f'''SELECT w.*, g.title AS gift_title, g.document_id,
                   c.title AS case_title
            FROM shop_case_wins w
            LEFT JOIN star_gifts g ON g.gift_id = w.gift_id
            LEFT JOIN shop_cases c ON c.case_id = w.case_id
            WHERE w.id IN ({placeholders})
            ORDER BY w.id''',
        unique,
    ).fetchall()
    doc_cache: dict[int, str] = {}
    return [_win_row_public(storage, dict(r), doc_cache=doc_cache) for r in rows]


def _wins_for_order(storage: Storage, nonce: str) -> list[dict[str, Any]]:
    rows = storage._conn.execute(
        'SELECT id FROM shop_case_wins WHERE order_nonce=? ORDER BY id',
        (nonce,),
    ).fetchall()
    return _wins_public_batch(storage, [int(r['id']) for r in rows])


def list_user_wins(user_id: int) -> dict[str, Any]:
    storage = _open()
    rows = storage._conn.execute(
        '''SELECT w.*, g.title AS gift_title, g.document_id,
                  c.title AS case_title
           FROM shop_case_wins w
           LEFT JOIN star_gifts g ON g.gift_id = w.gift_id
           LEFT JOIN shop_cases c ON c.case_id = w.case_id
           WHERE w.user_id=? AND w.status IN ('held','withdrawing')
           ORDER BY w.created_at DESC''',
        (user_id,),
    ).fetchall()
    doc_cache: dict[int, str] = {}
    return {
        'ok': True,
        'wins': [
            _win_row_public(storage, dict(r), doc_cache=doc_cache) for r in rows
        ],
    }


LIVE_DROPS_LIMIT = 28
LIVE_PRESENCE_TTL = 55  # seconds — viewers currently on case-open screen


def _touch_case_live_presence(storage: Storage, viewer_key: str) -> None:
    key = (viewer_key or '').strip()[:80]
    if not key:
        return
    now = int(time.time())
    storage._conn.execute(
        '''INSERT INTO shop_case_live_presence (viewer_key, updated_at)
           VALUES (?, ?)
           ON CONFLICT(viewer_key) DO UPDATE SET updated_at=excluded.updated_at''',
        (key, now),
    )
    # Keep table tiny
    storage._conn.execute(
        'DELETE FROM shop_case_live_presence WHERE updated_at < ?',
        (now - LIVE_PRESENCE_TTL * 4,),
    )
    storage._conn.commit()


def _active_case_openers(storage: Storage) -> int:
    now = int(time.time())
    row = storage._conn.execute(
        '''SELECT COUNT(*) AS c FROM shop_case_live_presence
           WHERE updated_at >= ?''',
        (now - LIVE_PRESENCE_TTL,),
    ).fetchone()
    presence = int(row['c'] if row else 0)
    # Also count distinct openers who just won (covers users mid-spin).
    recent = storage._conn.execute(
        '''SELECT COUNT(DISTINCT user_id) AS c FROM shop_case_wins
           WHERE created_at >= ?''',
        (now - 120,),
    ).fetchone()
    wins_active = int(recent['c'] if recent else 0)
    return max(presence, wins_active)


def list_live_drops(
    *,
    viewer_key: str = '',
    limit: int = LIVE_DROPS_LIMIT,
) -> dict[str, Any]:
    """Public live feed: recent case gifts + how many people are opening now."""
    storage = _open()
    ensure_cases_schema(storage)
    if viewer_key:
        _touch_case_live_presence(storage, viewer_key)
    lim = max(1, min(int(limit or LIVE_DROPS_LIMIT), 40))
    rows = storage._conn.execute(
        '''SELECT w.*, g.title AS gift_title, g.document_id,
                  c.title AS case_title
           FROM shop_case_wins w
           LEFT JOIN star_gifts g ON g.gift_id = w.gift_id
           LEFT JOIN shop_cases c ON c.case_id = w.case_id
           ORDER BY w.id DESC
           LIMIT ?''',
        (lim,),
    ).fetchall()
    doc_cache: dict[int, str] = {}
    drops: list[dict[str, Any]] = []
    for row in rows:
        pub = _win_row_public(storage, dict(row), doc_cache=doc_cache)
        drops.append({
            'winId': pub['winId'],
            'giftId': pub['giftId'],
            'title': pub['title'],
            'stars': pub['stars'],
            'imagePath': pub['imagePath'],
            'caseId': pub['caseId'],
            'caseTitle': pub['caseTitle'],
            'createdAt': int(dict(row).get('created_at') or 0),
        })
    return {
        'ok': True,
        'active': _active_case_openers(storage),
        'drops': drops,
        'serverTime': int(time.time()),
    }


def _win_public(storage: Storage, win_id: int) -> dict[str, Any]:
    wins = _wins_public_batch(storage, [win_id])
    return wins[0] if wins else {}


def sell_win(user_id: int, win_id: int) -> dict[str, Any]:
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_case_wins WHERE id=? AND user_id=?',
        (win_id, user_id),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Приз не найден'}
    if row['status'] != 'held':
        return {'ok': False, 'error': 'Приз уже обработан'}
    stars = int(row['stars_value'])
    balance = storage.adjust_stars_balance(
        user_id,
        stars,
        title=f'Продажа подарка ({stars} звёзд)',
        peer_user_id=config.SERVICE_USER_ID,
    )
    now = int(time.time())
    storage._conn.execute(
        '''UPDATE shop_case_wins SET status=?, resolved_at=? WHERE id=?''',
        ('sold', now, win_id),
    )
    storage._conn.commit()
    return {'ok': True, 'stars': stars, 'balance': balance}


def lookup_airgram_recipient(username: str) -> dict[str, Any]:
    storage = _open()
    uname = (username or '').strip().lstrip('@').lower()
    if not uname:
        return {'ok': False, 'error': 'Введите @username'}
    if not uname.replace('_', '').isalnum():
        return {'ok': False, 'error': 'Неверный username'}
    target = storage.get_user_by_username(uname)
    if not target:
        return {'ok': False, 'error': f'Пользователь @{uname} не найден на сервере'}
    if target.get('is_bot'):
        return {'ok': False, 'error': 'Нельзя выводить подарок на бота — укажите свой аккаунт'}
    return {
        'ok': True,
        'userId': int(target['id']),
        'username': uname,
        'firstName': (target.get('first_name') or '').strip(),
        'isBot': bool(target.get('is_bot')),
    }


def withdraw_win(user_id: int, win_id: int, username: str) -> dict[str, Any]:
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_case_wins WHERE id=? AND user_id=?',
        (win_id, user_id),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Приз не найден'}
    if row['status'] != 'held':
        return {'ok': False, 'error': 'Приз уже обработан'}
    lookup = lookup_airgram_recipient(username)
    if not lookup.get('ok'):
        return lookup
    uname = lookup['username']
    storage._conn.execute(
        '''UPDATE shop_case_wins
           SET status=?, withdraw_username=?, resolved_at=?
           WHERE id=?''',
        ('withdrawing', uname, int(time.time()), win_id),
    )
    storage._conn.commit()
    result = deliver_win(int(win_id))
    if not result.get('ok'):
        storage._conn.execute(
            '''UPDATE shop_case_wins SET status=?, delivery_error=?
               WHERE id=? AND status=?''',
            ('held', result.get('error') or 'delivery failed', win_id, 'withdrawing'),
        )
        storage._conn.commit()
        return result
    return result


def deliver_win(win_id: int) -> dict[str, Any]:
    storage = _open()
    row = storage._conn.execute(
        'SELECT * FROM shop_case_wins WHERE id=?', (win_id,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Приз не найден'}
    if row['status'] in ('delivered', 'withdrawn', 'sold'):
        return {'ok': True, 'already': True, 'winId': win_id}
    if row['status'] not in ('withdrawing', 'held'):
        return {'ok': False, 'error': 'Неверный статус'}
    uname = (row['withdraw_username'] or '').strip().lstrip('@')
    if not uname:
        return {'ok': False, 'error': 'Username не указан'}
    target = storage.get_user_by_username(uname)
    if not target:
        return {'ok': False, 'error': f'Пользователь @{uname} не найден'}
    gift_id = int(row['gift_id'])
    sender_id = int(config.GIFTS_BOT_ID)
    recipient_id = int(target['id'])
    gift = storage.get_star_gift(gift_id)
    if not gift:
        return {'ok': False, 'error': 'Подарок недоступен для выдачи'}
    try:
        saved = _grant_gift_free(storage, sender_id, recipient_id, gift_id)
        saved_id = int(saved['saved_id'])
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}
    now = int(time.time())
    storage._conn.execute(
        '''UPDATE shop_case_wins
           SET status=?, saved_gift_id=?, resolved_at=?, delivery_error=NULL
           WHERE id=?''',
        ('delivered', saved_id, now, win_id),
    )
    storage._conn.commit()
    gift_title = (gift.get('title') or str(gift_id))
    threading.Thread(
        target=_notify_gifts_bot_delivery,
        kwargs={
            'telegram_user_id': _telegram_for_user(storage, int(row['user_id'])),
            'username': uname,
            'gift_title': gift_title,
        },
        daemon=True,
    ).start()
    return {
        'ok': True,
        'winId': win_id,
        'username': uname,
        'savedGiftId': saved_id,
    }


def _grant_gift_free(storage: Storage, sender_id: int, recipient_id: int,
                     gift_id: int) -> dict:
    return storage.grant_star_gift_free(sender_id, recipient_id, gift_id)


def _repair_delivered_case_gifts(storage: Storage) -> None:
    """Backfill chat messages for gifts delivered before full grant flow."""
    backfill = getattr(storage, 'backfill_saved_star_gift_messages', None)
    if not callable(backfill):
        return
    rows = storage._conn.execute(
        '''SELECT saved_gift_id FROM shop_case_wins
           WHERE status='delivered' AND saved_gift_id IS NOT NULL''',
    ).fetchall()
    sender_id = int(config.GIFTS_BOT_ID)
    for row in rows:
        saved = storage.get_saved_star_gift_by_id(int(row['saved_gift_id']))
        if not saved or int(saved.get('upgraded') or 0):
            continue
        backfill(saved, sender_id)


def _telegram_for_user(storage: Storage, user_id: int) -> int | None:
    from shop_webapp.service import _telegram_id_for_shop_user

    return _telegram_id_for_shop_user(storage, user_id)


def _notify_gifts_bot_delivery(*, telegram_user_id: int | None,
                               username: str, gift_title: str) -> None:
    token = (
        os.environ.get('GIFTS_BOT_TOKEN', '').strip()
        or os.environ.get('MYTELEGRAM_GIFTS_BOT_TOKEN', '').strip()
    )
    if not token or not telegram_user_id:
        return
    import urllib.parse
    import urllib.request

    text = (
        f'🎁 Подарок <b>{gift_title}</b> выведен на @{username} в KoalGram через @giftbot.'
    )
    data = urllib.parse.urlencode({
        'chat_id': int(telegram_user_id),
        'text': text,
        'parse_mode': 'HTML',
    }).encode()
    try:
        urllib.request.urlopen(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data=data,
            timeout=10,
        )
    except Exception:
        pass


def process_delivery_queue(limit: int = 20) -> dict[str, Any]:
    storage = _open()
    rows = storage._conn.execute(
        '''SELECT id FROM shop_case_wins
           WHERE status='withdrawing'
           ORDER BY created_at LIMIT ?''',
        (max(1, int(limit)),),
    ).fetchall()
    done = 0
    errors: list[str] = []
    for row in rows:
        result = deliver_win(int(row['id']))
        if result.get('ok'):
            done += 1
        else:
            errors.append(f'{row["id"]}: {result.get("error")}')
    return {'ok': True, 'processed': done, 'errors': errors}
