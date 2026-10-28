"""Gift and collectible-username rental marketplace."""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import config
from db.storage import Storage, format_phone_display, normalize_phone, normalize_star_gift_slug
from handlers.phone_countries import anon_phone_lookup_variants, canonical_anon_phone
from rentals_webapp.notify import send_code as send_bot_code

ADMIN_IDS = frozenset(
    int(x.strip())
    for x in os.environ.get('RENTALS_ADMIN_IDS', '8549770368,8014265847').split(',')
    if x.strip().isdigit()
)
BLOCKCHAIN_ADDRESS = os.environ.get('RENTALS_BLOCKCHAIN_ADDRESS', 'market.ton').strip()
BOT_TOKEN = (
    os.environ.get('RENTALS_BOT_TOKEN')
    or os.environ.get('SHOP_BOT_TOKEN')
    or ''
).strip()
DEFAULT_DURATION_HOURS = (1, 6, 12, 24, 72, 168)
SESSION_TTL = 30 * 86400
CODE_TTL = 300

_GIFT_LINK_RE = re.compile(
    r'(?:t\.me/(?:nft/)?|telegram\.me/(?:nft/)?|'
    r'tg://nft\?slug=|fragment\.com/gift/)([A-Za-z0-9_-]+)',
    re.I,
)


def _open() -> Storage:
    return Storage.open_tool(os.environ.get('MTG_DB_PATH') or config.DB_PATH)


def _ensure_tables(storage: Storage) -> None:
    storage._conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS gift_rentals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL,              -- gift | username
            item_key TEXT NOT NULL,               -- unique_slug or username
            saved_id INTEGER,
            owner_user_id INTEGER NOT NULL,       -- listing owner (admin)
            title TEXT NOT NULL DEFAULT '',
            price_stars INTEGER NOT NULL DEFAULT 0,
            duration_hours INTEGER,               -- fixed hours; NULL = renter chooses
            duration_options_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'listed', -- listed | rented | archived
            renter_user_id INTEGER,
            rented_at INTEGER,
            expires_at INTEGER,
            blockchain_address TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gift_rentals_status
            ON gift_rentals(status, expires_at);
        CREATE INDEX IF NOT EXISTS idx_gift_rentals_key
            ON gift_rentals(item_type, item_key);

        CREATE TABLE IF NOT EXISTS rentals_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            telegram_user_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rentals_sessions_user
            ON rentals_sessions(user_id);

        CREATE TABLE IF NOT EXISTS rentals_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            telegram_user_id INTEGER NOT NULL DEFAULT 0,
            phone TEXT NOT NULL,
            code TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_rentals_codes_phone
            ON rentals_codes(kind, phone, used);

        CREATE TABLE IF NOT EXISTS rentals_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL UNIQUE,
            listing_id INTEGER NOT NULL,
            renter_user_id INTEGER NOT NULL,
            telegram_user_id INTEGER NOT NULL,
            duration_hours INTEGER NOT NULL,
            price_stars INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_id TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            paid_at INTEGER,
            expires_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rentals_orders_payload
            ON rentals_orders(payload);
        CREATE INDEX IF NOT EXISTS idx_rentals_orders_listing
            ON rentals_orders(listing_id, status);

        CREATE TABLE IF NOT EXISTS rentals_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0
        );
        '''
    )
    storage._conn.commit()


def get_setting(storage: Storage, key: str, default: str = '') -> str:
    _ensure_tables(storage)
    row = storage._conn.execute(
        'SELECT value FROM rentals_settings WHERE key=?', (key,),
    ).fetchone()
    if not row:
        return default
    return str(row['value'] if row['value'] is not None else default)


def set_setting(storage: Storage, key: str, value: str) -> None:
    _ensure_tables(storage)
    storage._conn.execute(
        '''INSERT INTO rentals_settings (key, value, updated_at)
           VALUES (?,?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at''',
        (str(key), str(value), _now()),
    )
    storage._conn.commit()


def is_market_closed(storage: Storage | None = None) -> bool:
    st = storage or _open()
    val = get_setting(st, 'market_closed', '0').strip().lower()
    return val in ('1', 'true', 'yes', 'on', 'да')


def _new_code() -> str:
    return f'{secrets.randbelow(1_000_000):06d}'


def _session_token() -> str:
    return secrets.token_urlsafe(32)


def _now() -> int:
    return int(time.time())


def is_admin(telegram_user_id: int | None) -> bool:
    return bool(telegram_user_id and int(telegram_user_id) in ADMIN_IDS)


def _is_admin_request(
    telegram_id: int = 0,
    data: dict | None = None,
    eff_tg: int = 0,
) -> bool:
    data = data or {}
    candidates = (
        telegram_id,
        eff_tg,
        data.get('openerTelegramId'),
        data.get('telegramUserId'),
        data.get('telegram_id'),
    )
    for raw in candidates:
        try:
            if is_admin(int(raw or 0)):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _gift_chain_owner(storage: Storage, slug: str) -> str:
    """KoalGram chain wallet owning the gift; '' when it is not on-chain."""
    try:
        return storage.saved_gift_chain_owner({'unique_slug': slug})
    except Exception:
        return ''


def parse_gift_slug(raw: str) -> str:
    text = (raw or '').strip()
    if not text:
        return ''
    m = _GIFT_LINK_RE.search(text)
    if m:
        return normalize_star_gift_slug(m.group(1))
    if '://' in text or text.startswith('tg:'):
        parsed = urlparse(text)
        qs = parse_qs(parsed.query or '')
        if qs.get('slug'):
            return normalize_star_gift_slug(qs['slug'][0])
        path = (parsed.path or '').rstrip('/').split('/')[-1]
        return normalize_star_gift_slug(path)
    return normalize_star_gift_slug(text)


def parse_username(raw: str) -> str:
    name = (raw or '').strip().lstrip('@').lower()
    if not re.fullmatch(r'[a-z][a-z0-9_]{3,31}', name or ''):
        return ''
    return name


def _normalize_price_options(raw, fallback_price: int = 0) -> list[dict[str, int]]:
    """Parse duration/price options into [{hours, priceStars}, ...]."""
    fallback = max(0, int(fallback_price or 0))
    items: list[dict[str, int]] = []

    def add(hours: int, price: int) -> None:
        h = int(hours)
        if h <= 0:
            return
        for it in items:
            if it['hours'] == h:
                it['priceStars'] = max(0, int(price))
                return
        items.append({'hours': h, 'priceStars': max(0, int(price))})

    parsed = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return [{'hours': h, 'priceStars': fallback} for h in DEFAULT_DURATION_HOURS]
        try:
            parsed = json.loads(text)
        except Exception:
            parts = re.split(r'[,;\s]+', text)
            for p in parts:
                if p.isdigit() and int(p) > 0:
                    add(int(p), fallback)
            return items or [{'hours': h, 'priceStars': fallback} for h in DEFAULT_DURATION_HOURS]

    if isinstance(parsed, dict):
        # {"24": 100, "72": 250} or {"hours":24,"priceStars":100}
        if 'hours' in parsed or 'priceStars' in parsed or 'price' in parsed:
            try:
                add(
                    int(parsed.get('hours') or 0),
                    int(parsed.get('priceStars') or parsed.get('price') or fallback),
                )
            except (TypeError, ValueError):
                pass
        else:
            for k, v in parsed.items():
                try:
                    add(int(k), int(v if not isinstance(v, dict) else (v.get('priceStars') or v.get('price') or fallback)))
                except (TypeError, ValueError):
                    continue
        return items or [{'hours': h, 'priceStars': fallback} for h in DEFAULT_DURATION_HOURS]

    if isinstance(parsed, (list, tuple)):
        for item in parsed:
            if isinstance(item, dict):
                try:
                    add(
                        int(item.get('hours') or item.get('durationHours') or 0),
                        int(item.get('priceStars') or item.get('price') or fallback),
                    )
                except (TypeError, ValueError):
                    continue
            else:
                try:
                    add(int(item), fallback)
                except (TypeError, ValueError):
                    continue
        return items or [{'hours': h, 'priceStars': fallback} for h in DEFAULT_DURATION_HOURS]

    try:
        h = int(parsed)
        return [{'hours': h, 'priceStars': fallback}] if h > 0 else [
            {'hours': x, 'priceStars': fallback} for x in DEFAULT_DURATION_HOURS
        ]
    except (TypeError, ValueError):
        return [{'hours': h, 'priceStars': fallback} for h in DEFAULT_DURATION_HOURS]


def _parse_duration_options(raw) -> list[int]:
    return [int(o['hours']) for o in _normalize_price_options(raw)]


def _price_for_hours(rental: dict, hours: int) -> int:
    options = _normalize_price_options(
        rental.get('duration_options_json'),
        int(rental.get('price_stars') or 0),
    )
    for opt in options:
        if int(opt['hours']) == int(hours):
            return max(0, int(opt['priceStars']))
    return max(0, int(rental.get('price_stars') or 0))


def _options_json_for_store(raw, fallback_price: int = 0) -> tuple[str, int]:
    options = _normalize_price_options(raw, fallback_price)
    base_price = min((int(o['priceStars']) for o in options), default=max(0, int(fallback_price or 0)))
    # Prefer first option's price as catalog "from" price if positive
    positive = [int(o['priceStars']) for o in options if int(o['priceStars']) > 0]
    catalog_price = min(positive) if positive else base_price
    return json.dumps(options, ensure_ascii=False), catalog_price


def _resolve_mtg_user_from_telegram(storage: Storage, telegram_id: int) -> Optional[dict]:
    telegram_id = int(telegram_id)
    # Prefer phone link table used by shop/auth bots
    row = storage._conn.execute(
        '''SELECT phone FROM telegram_phones
           WHERE telegram_user_id=? ORDER BY created_at DESC LIMIT 1''',
        (telegram_id,),
    ).fetchone()
    if row:
        user = storage.get_user_by_phone(row['phone'])
        if user:
            return user
    user = storage.get_user_by_id(telegram_id)
    if user:
        return user
    return None


def _owner_public(user: dict | None) -> dict[str, Any]:
    if not user:
        return {}
    return {
        'id': int(user['id']),
        'username': user.get('username') or '',
        'firstName': user.get('first_name') or '',
        'lastName': user.get('last_name') or '',
    }


def _gift_card(storage: Storage, saved: dict) -> dict[str, Any]:
    from shop_webapp.service import _upgraded_gift_public
    return _upgraded_gift_public(saved, storage)


_LAST_EXPIRE_AT = 0.0
_EXPIRE_THROTTLE_SEC = 20.0


def _expire_due(storage: Storage, *, force: bool = False) -> int:
    """Return expired rentals. Throttled for hot paths (catalog/inventory)."""
    global _LAST_EXPIRE_AT
    now_wall = time.time()
    if not force and (now_wall - _LAST_EXPIRE_AT) < _EXPIRE_THROTTLE_SEC:
        return 0
    _LAST_EXPIRE_AT = now_wall
    now = _now()
    rows = storage._conn.execute(
        '''SELECT * FROM gift_rentals
           WHERE status='rented' AND expires_at IS NOT NULL AND expires_at<=?''',
        (now,),
    ).fetchall()
    fixed = 0
    for row in rows:
        _return_rental(storage, dict(row), archive=False)
        fixed += 1
    return fixed


def _transfer_gift_ownership(
    storage: Storage,
    saved: dict,
    *,
    to_user_id: int,
    notify_saved_messages: bool,
) -> dict:
    """Move unique gift to to_user_id and optionally post Saved Messages card."""
    now = _now()
    new_msg_id = storage._allocate_message_id()
    storage._conn.execute(
        '''UPDATE saved_star_gifts SET owner_user_id=?, msg_id=?,
           pinned_to_top=0, unsaved=0, resell_stars=NULL, resell_stars_nanos=0,
           resell_ton=0, date=?
           WHERE saved_id=?''',
        (int(to_user_id), int(new_msg_id), now, int(saved['saved_id'])),
    )
    if notify_saved_messages:
        svc_msg_id = storage._allocate_message_id()
        action = {
            'type': 'star_gift_unique',
            'gift_id': int(saved['gift_id']),
            'unique_id': int(saved.get('unique_id') or 0),
            'saved_id': int(new_msg_id),
            'peer_id': int(to_user_id),
            'upgrade': False,
            'saved': True,
            'transferred': True,
        }
        storage._insert_message(
            svc_msg_id,
            int(to_user_id),
            int(to_user_id),
            int(to_user_id),
            '',
            [],
            now,
            action_json=json.dumps(action, ensure_ascii=False),
        )
        storage._upsert_dialog(int(to_user_id), int(to_user_id), svc_msg_id, now, True, 0)
    storage._conn.commit()
    return storage.get_saved_star_gift_by_id(int(saved['saved_id'])) or saved


def _restore_username_to_owner(
    storage: Storage,
    item_key: str,
    owner_id: int,
    *,
    active: bool = True,
) -> None:
    """Force collectible username back to listing owner (visible in profile)."""
    from db.storage import normalize_collectible_username

    uname = normalize_collectible_username(item_key)
    if not uname or not owner_id:
        return
    # Drop from temporary holders (renters), keep/create owner row
    storage._conn.execute(
        'DELETE FROM collectible_usernames WHERE lower(username)=? AND user_id!=?',
        (uname, int(owner_id)),
    )
    row = storage._conn.execute(
        'SELECT user_id FROM collectible_usernames WHERE lower(username)=?',
        (uname,),
    ).fetchone()
    now = _now()
    active_flag = 1 if active else 0
    if row and int(row['user_id']) == int(owner_id):
        storage._conn.execute(
            'UPDATE collectible_usernames SET active=? WHERE lower(username)=?',
            (active_flag, uname),
        )
    else:
        storage._conn.execute(
            '''INSERT INTO collectible_usernames
               (username, user_id, active, sort_order, price, granted_at, granted_by, created_at)
               VALUES (?, ?, ?, 0, '', ?, 0, ?)
               ON CONFLICT(username) DO UPDATE SET
                   user_id=excluded.user_id,
                   active=excluded.active,
                   granted_at=excluded.granted_at''',
            (uname, int(owner_id), active_flag, now, now),
        )
    storage._conn.commit()


def _return_rental(storage: Storage, rental: dict, *, archive: bool) -> None:
    item_type = rental['item_type']
    owner_id = int(rental['owner_user_id'])
    renter_id = int(rental.get('renter_user_id') or 0)
    item_key = rental['item_key']
    now = _now()

    if item_type == 'gift' and rental.get('saved_id'):
        saved = storage.get_saved_star_gift_by_id(int(rental['saved_id']))
        if saved and renter_id and int(saved.get('owner_user_id') or 0) == renter_id:
            _transfer_gift_ownership(
                storage, saved, to_user_id=owner_id, notify_saved_messages=False,
            )
        if not _gift_chain_owner(storage, item_key):
            if not archive:
                storage.upsert_gift_blockchain_link(item_key, BLOCKCHAIN_ADDRESS)
            else:
                storage.remove_gift_blockchain_link(item_key)
    elif item_type == 'username':
        # Always return to owner as active so it shows in Telegram/MTG profile
        _restore_username_to_owner(storage, item_key, owner_id, active=True)

    new_status = 'archived' if archive else 'listed'
    storage._conn.execute(
        '''UPDATE gift_rentals SET
           status=?, renter_user_id=NULL, rented_at=NULL, expires_at=NULL,
           updated_at=?
           WHERE id=?''',
        (new_status, now, int(rental['id'])),
    )
    storage._conn.commit()


def _sync_owner_usernames_inventory(storage: Storage, user_id: int) -> None:
    """
    Repair: usernames from finished/listed lots must belong to the listing owner.
    Skips currently rented lots (held by renter).
    """
    rows = storage._conn.execute(
        '''SELECT item_key, status FROM gift_rentals
           WHERE item_type='username' AND owner_user_id=?
             AND status IN ('listed', 'archived')''',
        (int(user_id),),
    ).fetchall()
    for row in rows:
        key = row['item_key']
        holder = storage.get_collectible_username_owner(key)
        if holder and int(holder['user_id']) == int(user_id):
            # Make sure it's active enough to show in profile after return
            if not bool(holder.get('active')) and row['status'] == 'archived':
                storage._conn.execute(
                    'UPDATE collectible_usernames SET active=1 WHERE lower(username)=lower(?)',
                    (key,),
                )
            continue
        # Missing or stuck on someone else — pull back
        if row['status'] == 'listed' or row['status'] == 'archived':
            # Don't steal if simultaneously rented under another row
            rented = storage._conn.execute(
                '''SELECT id FROM gift_rentals
                   WHERE item_type='username' AND lower(item_key)=lower(?)
                     AND status='rented' LIMIT 1''',
                (key,),
            ).fetchone()
            if rented:
                continue
            _restore_username_to_owner(storage, key, user_id, active=True)
    storage._conn.commit()


def _gift_notice_payload(storage: Storage, saved: dict) -> dict[str, Any]:
    card = _gift_card(storage, saved) if saved else {}
    title = card.get('giftTitle') or saved.get('unique_slug') or 'Gift'
    num = int(saved.get('unique_num') or card.get('uniqueNum') or 0)
    return {
        'toast': f'Вы добавили {title} #{num} в свой профиль.' if num else f'Вы добавили {title} в свой профиль.',
        'title': 'Сохранённый подарок',
        'subtitle': f'{title} #{num}' if num else title,
        'model': (card.get('model') or {}).get('name') or '',
        'pattern': (card.get('pattern') or {}).get('name') or '',
        'backdrop': (card.get('backdrop') or {}).get('name') or '',
        'gift': card,
        'slug': saved.get('unique_slug') or '',
    }

def serialize_listing(storage: Storage, row: dict,
                      *, include_card: bool = True) -> dict[str, Any]:
    item_type = row['item_type']
    out: dict[str, Any] = {
        'id': int(row['id']),
        'itemType': item_type,
        'itemKey': row['item_key'],
        'savedId': int(row['saved_id']) if row.get('saved_id') else None,
        'title': row.get('title') or row['item_key'],
        'priceStars': int(row.get('price_stars') or 0),
        'durationHours': (
            int(row['duration_hours']) if row.get('duration_hours') is not None else None
        ),
        'durationOptions': _normalize_price_options(
            row.get('duration_options_json'),
            int(row.get('price_stars') or 0),
        ),
        'status': row['status'],
        'renterUserId': (
            int(row['renter_user_id']) if row.get('renter_user_id') else None
        ),
        'rentedAt': int(row['rented_at']) if row.get('rented_at') else None,
        'expiresAt': int(row['expires_at']) if row.get('expires_at') else None,
        'blockchainAddress': row.get('blockchain_address') or '',
        'note': row.get('note') or '',
        'owner': _owner_public(storage.get_user_by_id(int(row['owner_user_id']))),
        'createdAt': int(row.get('created_at') or 0),
    }
    if include_card and item_type == 'gift' and row.get('saved_id'):
        saved = storage.get_saved_star_gift_by_id(int(row['saved_id']))
        if saved:
            out['gift'] = _gift_card(storage, saved)
    if item_type == 'username':
        out['username'] = row['item_key']
    # Always expose market address for active market lots
    if row.get('status') in ('listed', 'rented'):
        out['blockchainAddress'] = (
            row.get('blockchain_address') or BLOCKCHAIN_ADDRESS or 'market.ton'
        )
    return out


def list_catalog(*, telegram_id: int = 0, data: dict | None = None) -> dict[str, Any]:
    data = data or {}
    storage = _open()
    _ensure_tables(storage)
    _expire_due(storage)
    closed = is_market_closed(storage)
    admin = _is_admin_request(telegram_id, data, 0)
    if closed and not admin:
        return {
            'ok': True,
            'closed': True,
            'maintenance': True,
            'message': 'Мы временно недоступны',
            'emojiId': '6012840895982868130',
            'isAdmin': False,
            'blockchainAddress': BLOCKCHAIN_ADDRESS,
            'listings': [],
        }
    rows = storage._conn.execute(
        '''SELECT * FROM gift_rentals
           WHERE status IN ('listed', 'rented')
           ORDER BY
             CASE status WHEN 'listed' THEN 0 ELSE 1 END,
             id DESC'''
    ).fetchall()
    listings = []
    for r in rows:
        row = dict(r)
        if row.get('item_type') == 'gift' and row.get('item_key'):
            try:
                if not _gift_chain_owner(storage, row['item_key']):
                    storage.upsert_gift_blockchain_link(
                        row['item_key'], BLOCKCHAIN_ADDRESS,
                    )
            except Exception:
                pass
        listings.append(serialize_listing(storage, row))
    return {
        'ok': True,
        'closed': False,
        'maintenance': False,
        'isAdmin': admin,
        'blockchainAddress': BLOCKCHAIN_ADDRESS,
        'listings': listings,
    }



def _create_session(storage: Storage, user_id: int, telegram_user_id: int) -> str:
    token = _session_token()
    now = _now()
    storage._conn.execute(
        '''INSERT INTO rentals_sessions
           (token, user_id, telegram_user_id, created_at, expires_at)
           VALUES (?,?,?,?,?)''',
        (token, int(user_id), int(telegram_user_id), now, now + SESSION_TTL),
    )
    storage._conn.commit()
    return token


def get_session(token: str) -> dict[str, Any]:
    token = (token or '').strip()
    if not token:
        return {'ok': False, 'error': 'Нет сессии'}
    storage = _open()
    _ensure_tables(storage)
    row = storage._conn.execute(
        'SELECT * FROM rentals_sessions WHERE token=? AND expires_at>?',
        (token, _now()),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Сессия истекла'}
    user = storage.get_user_by_id(int(row['user_id']))
    if not user:
        return {'ok': False, 'error': 'Пользователь не найден'}
    return _profile_payload(
        storage,
        user,
        telegram_id=int(row['telegram_user_id']),
        token=token,
    )


def _ensure_user_for_telegram(
    storage: Storage,
    telegram_id: int,
    *,
    first_name: str = '',
    last_name: str = '',
    tg_username: str = '',
) -> tuple[Optional[dict], str]:
    phone = ''
    if not telegram_id:
        return None, ''
    try:
        phone = storage.assign_bot_phone(int(telegram_id)) or ''
        storage.sync_telegram_phone_links(int(telegram_id))
    except Exception:
        phone = storage.get_bot_phone_for_telegram(int(telegram_id)) or ''
    user = _resolve_mtg_user_from_telegram(storage, telegram_id)
    if not user and phone:
        user = storage.get_user_by_phone(phone)
    if not user and phone:
        name = (first_name or tg_username or 'Market').strip() or 'Market'
        user = storage.create_user(phone, name, (last_name or '').strip())
        if tg_username and user:
            try:
                storage._conn.execute(
                    'UPDATE users SET username=? WHERE id=? AND (username IS NULL OR username="")',
                    (tg_username.lstrip('@')[:32], int(user['id'])),
                )
                storage._conn.commit()
                user = storage.get_user_by_id(int(user['id']))
            except Exception:
                pass
    return user, phone


def _profile_payload(
    storage: Storage,
    user: dict,
    *,
    telegram_id: int,
    token: str = '',
    first_name: str = '',
    last_name: str = '',
    tg_username: str = '',
) -> dict[str, Any]:
    phone = (user.get('phone') or '').strip()
    display_name = (
        (user.get('first_name') or first_name or '').strip()
        or (user.get('username') or tg_username or '')
        or 'User'
    )
    uname = (user.get('username') or tg_username or '').lstrip('@')
    return {
        'ok': True,
        'token': token,
        'guest': False,
        'telegramId': int(telegram_id or 0),
        'isAdmin': is_admin(telegram_id),
        'mtgUserId': int(user['id']),
        'firstName': (user.get('first_name') or first_name or '').strip(),
        'lastName': (user.get('last_name') or last_name or '').strip(),
        'displayName': display_name,
        'username': uname,
        'phone': phone,
        'phoneDisplay': format_phone_display(phone) if phone else '',
        'stars': storage.get_stars_balance(int(user['id'])),
        'avatarUrl': f'/api/avatar/{int(telegram_id)}' if telegram_id else '',
    }



def send_login_code(phone_raw: str, telegram_user_id: int = 0) -> dict[str, Any]:
    phone = canonical_anon_phone(normalize_phone(phone_raw))
    if not phone:
        return {'ok': False, 'error': 'Неверный номер'}
    storage = _open()
    _ensure_tables(storage)

    linked_tg = None
    resolved = phone
    for variant in anon_phone_lookup_variants(phone):
        linked_tg = storage.get_telegram_user_by_phone(variant)
        if linked_tg:
            resolved = variant
            break
    phone = resolved
    if not linked_tg:
        return {
            'ok': False,
            'error': 'Номер не привязан. Напишите /start боту авторизации с этим аккаунтом.',
        }

    user = storage.get_user_by_phone(phone)
    if not user:
        user = storage.create_user(phone, 'User', '')

    code = _new_code()
    expires = _now() + CODE_TTL
    storage._conn.execute(
        'DELETE FROM rentals_codes WHERE kind=? AND phone=?',
        ('login', phone),
    )
    storage._conn.execute(
        '''INSERT INTO rentals_codes
           (kind, telegram_user_id, phone, code, expires_at, used)
           VALUES (?,?,?,?,?,0)''',
        ('login', int(linked_tg), phone, code, expires),
    )
    storage._conn.commit()
    sent = send_bot_code(int(linked_tg), code)
    return {
        'ok': True,
        'codeSent': sent,
        'phoneDisplay': format_phone_display(phone),
        'hint': 'Код отправлен в бота авторизации' if sent else 'Не удалось отправить код. Напишите /start боту авторизации.',
    }


def complete_login(phone_raw: str, code: str, *,
                   opener_telegram_id: int = 0) -> dict[str, Any]:
    phone = normalize_phone(phone_raw)
    if not phone or not (code or '').strip():
        return {'ok': False, 'error': 'Введите номер и код'}
    storage = _open()
    _ensure_tables(storage)
    row = None
    for variant in anon_phone_lookup_variants(phone):
        row = storage._conn.execute(
            '''SELECT * FROM rentals_codes
               WHERE kind=? AND phone=? AND code=? AND used=0 AND expires_at>?''',
            ('login', variant, code.strip(), _now()),
        ).fetchone()
        if row:
            phone = variant
            break
    if not row:
        return {'ok': False, 'error': 'Неверный или просроченный код'}

    user = storage.get_user_by_phone(phone)
    if not user:
        user = storage.create_user(phone, 'User', '')
    storage._conn.execute(
        'UPDATE rentals_codes SET used=1 WHERE id=?', (row['id'],),
    )
    storage._conn.commit()
    tg_id = int(row['telegram_user_id'] or 0)
    if tg_id:
        storage.assign_bot_phone(tg_id, phone=phone, source='rentals_login')
        storage.sync_telegram_phone_links(tg_id)
    token = _create_session(storage, int(user['id']), tg_id or int(opener_telegram_id or 0))
    payload = _profile_payload(storage, user, telegram_id=tg_id or opener_telegram_id, token=token)
    if opener_telegram_id and is_admin(opener_telegram_id):
        payload['isAdmin'] = True
        payload['openerTelegramId'] = int(opener_telegram_id)
    return payload


def logout(token: str) -> dict[str, Any]:
    token = (token or '').strip()
    if not token:
        return {'ok': True}
    storage = _open()
    _ensure_tables(storage)
    storage._conn.execute('DELETE FROM rentals_sessions WHERE token=?', (token,))
    storage._conn.commit()
    return {'ok': True}


def _actor_user(telegram_id: int, data: dict | None = None) -> tuple[Optional[dict], int, Storage]:
    """Return (mtg_user, effective_telegram_id, storage). Requires session token."""
    data = data or {}
    storage = _open()
    _ensure_tables(storage)
    token = str(data.get('token') or '').strip()
    if token:
        row = storage._conn.execute(
            'SELECT * FROM rentals_sessions WHERE token=? AND expires_at>?',
            (token, _now()),
        ).fetchone()
        if row:
            user = storage.get_user_by_id(int(row['user_id']))
            return user, int(row['telegram_user_id']), storage
    return None, int(telegram_id or 0), storage


def my_inventory(telegram_id: int, data: dict | None = None) -> dict[str, Any]:
    user, eff_tg, storage = _actor_user(telegram_id, data)
    if not user:
        return {'ok': False, 'error': 'Нужна авторизация'}
    _ensure_tables(storage)
    _expire_due(storage)
    _sync_owner_usernames_inventory(storage, int(user['id']))
    gifts = []
    rented_gift_saved = {
        int(r['saved_id'])
        for r in storage._conn.execute(
            '''SELECT saved_id FROM gift_rentals
               WHERE item_type='gift' AND status='rented' AND saved_id IS NOT NULL'''
        ).fetchall()
        if r['saved_id']
    }
    for saved in storage.list_collectible_saved_gifts(int(user['id'])):
        # Skip gifts currently rented out (owned by renter for the period)
        if int(saved['saved_id']) in rented_gift_saved:
            continue
        card = _gift_card(storage, saved)
        gifts.append({
            'savedId': int(saved['saved_id']),
            'slug': saved.get('unique_slug') or '',
            'title': card.get('giftTitle') or saved.get('unique_slug') or '',
            'uniqueNum': int(saved.get('unique_num') or 0),
            'gift': card,
        })
    usernames = []
    rented_names = {
        (r['item_key'] or '').lower()
        for r in storage._conn.execute(
            """SELECT item_key FROM gift_rentals
               WHERE item_type='username' AND status='rented'"""
        ).fetchall()
    }
    try:
        for r in storage.get_collectible_usernames(int(user['id'])):
            name = (r.get('username') or '').lower()
            if name in rented_names:
                continue
            usernames.append({
                'username': r.get('username') or '',
                'active': bool(r.get('active')),
            })
    except Exception:
        pass
    return {
        'ok': True,
        'isAdmin': is_admin(telegram_id) or is_admin(eff_tg),
        'gifts': gifts,
        'usernames': usernames,
    }


def admin_get_settings(telegram_id: int, data: dict | None = None) -> dict[str, Any]:
    data = data or {}
    _user, eff_tg, storage = _actor_user(telegram_id, data)
    if not _is_admin_request(telegram_id, data, eff_tg):
        return {'ok': False, 'error': 'Нет доступа'}
    closed = is_market_closed(storage)
    return {
        'ok': True,
        'marketClosed': closed,
        'emojiId': '6012840895982868130',
    }


def admin_set_settings(telegram_id: int, data: dict | None = None) -> dict[str, Any]:
    data = data or {}
    _user, eff_tg, storage = _actor_user(telegram_id, data)
    if not _is_admin_request(telegram_id, data, eff_tg):
        return {'ok': False, 'error': 'Нет доступа'}
    if 'marketClosed' in data or 'closed' in data:
        closed = _truthy(data.get('marketClosed') if 'marketClosed' in data else data.get('closed'))
        set_setting(storage, 'market_closed', '1' if closed else '0')
    return admin_get_settings(telegram_id, data)


def me(telegram_id: int, *,
       token: str = '',
       first_name: str = '',
       last_name: str = '',
       tg_username: str = '') -> dict[str, Any]:
    storage = _open()
    _ensure_tables(storage)
    closed = is_market_closed(storage)
    if token:
        sess = get_session(token)
        if sess.get('ok'):
            if telegram_id and is_admin(telegram_id):
                sess['isAdmin'] = True
                sess['openerTelegramId'] = int(telegram_id)
            sess['guest'] = False
            sess['marketClosed'] = closed
            return sess

    # Guest until explicit phone + code login in profile
    return {
        'ok': True,
        'guest': True,
        'telegramId': int(telegram_id or 0),
        'isAdmin': is_admin(telegram_id),
        'marketClosed': closed,
        'mtgUserId': None,
        'username': tg_username or '',
        'firstName': first_name or '',
        'lastName': last_name or '',
        'displayName': '',
        'phone': '',
        'phoneDisplay': '',
        'stars': 0,
        'avatarUrl': f'/api/avatar/{int(telegram_id)}' if telegram_id else '',
        'token': '',
    }


def admin_list_gift(telegram_id: int, gift_ref: str, *,
                    title: str = '',
                    price_stars: int = 0,
                    duration_hours: int | None = None,
                    duration_options: Any = None,
                    note: str = '',
                    data: dict | None = None) -> dict[str, Any]:
    data = data or {}
    admin = _is_admin_request(telegram_id, data, 0)
    actor, eff_tg, storage = _actor_user(telegram_id, data)
    admin = admin or _is_admin_request(telegram_id, data, eff_tg)
    if not actor:
        return {'ok': False, 'error': 'Войдите по номеру в профиле'}
    if is_market_closed(storage) and not admin:
        return {'ok': False, 'error': 'Маркет временно закрыт'}

    slug = parse_gift_slug(gift_ref)
    if not slug:
        return {'ok': False, 'error': 'Неверная ссылка или slug подарка'}
    saved = storage.get_saved_star_gift_by_slug(slug)
    if not saved or not int(saved.get('upgraded') or 0):
        return {'ok': False, 'error': 'Collectible-подарок не найден на сервере'}
    if int(saved.get('burned') or 0):
        return {'ok': False, 'error': 'Подарок сожжён'}
    if storage.saved_gift_chain_owner(saved):
        return {'ok': False,
                'error': 'Подарок уже перенесён в блокчейн-кошелёк и не может быть сдан в аренду'}

    # Only admins may claim gifts owned elsewhere; users list only own inventory
    if int(saved['owner_user_id']) != int(actor['id']):
        if admin:
            storage._conn.execute(
                'UPDATE saved_star_gifts SET owner_user_id=? WHERE saved_id=?',
                (int(actor['id']), int(saved['saved_id'])),
            )
            storage._conn.commit()
            saved = storage.get_saved_star_gift_by_id(int(saved['saved_id']))
        else:
            return {'ok': False, 'error': 'Можно выставить только свой подарок'}

    active = storage._conn.execute(
        '''SELECT id FROM gift_rentals
           WHERE item_type='gift' AND item_key=? AND status IN ('listed','rented')''',
        (slug,),
    ).fetchone()
    if active:
        return {'ok': False, 'error': 'Этот подарок уже выставлен'}

    gift = storage.get_star_gift(int(saved['gift_id'])) or {}
    title = (title or gift.get('title') or slug).strip()
    options_json, catalog_price = _options_json_for_store(duration_options, price_stars)
    fixed = int(duration_hours) if duration_hours else None
    if fixed is not None and fixed <= 0:
        fixed = None
    # if multi options present, keep duration open for renter choice
    parsed_opts = _normalize_price_options(options_json, catalog_price)
    if len(parsed_opts) > 1:
        fixed = None
    elif len(parsed_opts) == 1 and fixed is None:
        # still open choice of that single option is fine; keep null
        pass

    if not _gift_chain_owner(storage, slug):
        storage.upsert_gift_blockchain_link(slug, BLOCKCHAIN_ADDRESS)
    now = _now()
    cur = storage._conn.execute(
        '''INSERT INTO gift_rentals
           (item_type, item_key, saved_id, owner_user_id, title, price_stars,
            duration_hours, duration_options_json, status, blockchain_address,
            note, created_at, updated_at)
           VALUES ('gift',?,?,?,?,?,?,?,'listed',?,?,?,?)''',
        (
            slug,
            int(saved['saved_id']),
            int(actor['id']),
            title,
            catalog_price,
            fixed,
            options_json,
            BLOCKCHAIN_ADDRESS,
            note or '',
            now,
            now,
        ),
    )
    storage._conn.commit()
    row = storage._conn.execute(
        'SELECT * FROM gift_rentals WHERE id=?', (cur.lastrowid,)
    ).fetchone()
    return {'ok': True, 'listing': serialize_listing(storage, dict(row))}


def admin_list_username(telegram_id: int, username_ref: str, *,
                        title: str = '',
                        price_stars: int = 0,
                        duration_hours: int | None = None,
                        duration_options: Any = None,
                        note: str = '',
                        data: dict | None = None) -> dict[str, Any]:
    data = data or {}
    admin = _is_admin_request(telegram_id, data, 0)
    actor, eff_tg, storage = _actor_user(telegram_id, data)
    admin = admin or _is_admin_request(telegram_id, data, eff_tg)
    if not actor:
        return {'ok': False, 'error': 'Войдите по номеру в профиле'}
    if is_market_closed(storage) and not admin:
        return {'ok': False, 'error': 'Маркет временно закрыт'}

    username = parse_username(username_ref)
    if not username:
        return {'ok': False, 'error': 'Неверный username'}

    owner = storage.get_collectible_username_owner(username)
    if owner and int(owner['user_id']) not in (0, int(actor['id'])):
        if admin:
            try:
                storage.remove_collectible_username(int(owner['user_id']), username)
            except Exception:
                pass
            storage.grant_collectible_username(
                int(actor['id']), username, active=False, price='')
        else:
            return {'ok': False, 'error': 'Можно выставить только свой username'}
    if not owner:
        if admin:
            try:
                storage.grant_collectible_username(
                    int(actor['id']), username, active=False, price='')
            except Exception as exc:
                return {'ok': False, 'error': str(exc)}
        else:
            return {'ok': False, 'error': 'У вас нет этого collectible username'}

    active = storage._conn.execute(
        '''SELECT id FROM gift_rentals
           WHERE item_type='username' AND item_key=? AND status IN ('listed','rented')''',
        (username,),
    ).fetchone()
    if active:
        return {'ok': False, 'error': 'Этот username уже выставлен'}

    options_json, catalog_price = _options_json_for_store(duration_options, price_stars)
    fixed = int(duration_hours) if duration_hours else None
    if fixed is not None and fixed <= 0:
        fixed = None
    parsed_opts = _normalize_price_options(options_json, catalog_price)
    if len(parsed_opts) > 1:
        fixed = None
    now = _now()
    cur = storage._conn.execute(
        '''INSERT INTO gift_rentals
           (item_type, item_key, saved_id, owner_user_id, title, price_stars,
            duration_hours, duration_options_json, status, blockchain_address,
            note, created_at, updated_at)
           VALUES ('username',?,NULL,?,?,?,?,?,'listed',?,?,?,?)''',
        (
            username,
            int(actor['id']),
            (title or f'@{username}').strip(),
            catalog_price,
            fixed,
            options_json,
            BLOCKCHAIN_ADDRESS,
            note or '',
            now,
            now,
        ),
    )
    storage._conn.commit()
    row = storage._conn.execute(
        'SELECT * FROM gift_rentals WHERE id=?', (cur.lastrowid,)
    ).fetchone()
    return {'ok': True, 'listing': serialize_listing(storage, dict(row))}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on', 'да')


def _refund_rental_order(storage: Storage, rental: dict) -> dict[str, Any]:
    """Refund stars for the paid rental order (KoalGram balance or Telegram invoice)."""
    renter_id = int(rental.get('renter_user_id') or 0)
    listing_id = int(rental['id'])
    owner_id = int(rental.get('owner_user_id') or 0)
    row = storage._conn.execute(
        '''SELECT * FROM rentals_orders
           WHERE listing_id=? AND status='paid'
             AND (?=0 OR renter_user_id=?)
           ORDER BY paid_at DESC, id DESC LIMIT 1''',
        (listing_id, renter_id, renter_id),
    ).fetchone()
    if not row:
        return {
            'ok': True,
            'refunded': False,
            'skipped': True,
            'reason': 'Нет оплаченного счёта (бесплатная аренда или уже возврат)',
        }
    order = dict(row)
    payment_id = str(order.get('payment_id') or '').strip()
    price = int(order.get('price_stars') or 0)
    buyer_uid = int(order.get('renter_user_id') or renter_id or 0)

    # KoalGram in-app Stars: credit renter, debit owner
    if payment_id == 'airgram':
        if price <= 0 or not buyer_uid:
            return {
                'ok': True,
                'refunded': False,
                'skipped': True,
                'reason': 'Нет суммы для возврата KoalGram Stars',
            }
        title = (rental.get('title') or rental.get('item_key') or 'Аренда')
        try:
            storage.adjust_stars_balance(
                buyer_uid, price,
                title='Возврат аренды',
                description=f'KoalGram Market · {title}',
                peer_user_id=owner_id or None,
            )
            if owner_id and owner_id != buyer_uid:
                storage.adjust_stars_balance(
                    owner_id, -price,
                    title='Возврат дохода',
                    description=f'KoalGram Market · {title}',
                    peer_user_id=buyer_uid,
                )
        except Exception as exc:
            return {
                'ok': False,
                'refunded': False,
                'error': f'Ошибка возврата KoalGram Stars: {exc}',
            }
        storage._conn.execute(
            "UPDATE rentals_orders SET status='refunded' WHERE id=?",
            (int(order['id']),),
        )
        storage._conn.commit()
        return {
            'ok': True,
            'refunded': True,
            'stars': price,
            'via': 'airgram',
            'paymentId': payment_id or 'airgram',
        }

    buyer_tg = int(order.get('telegram_user_id') or 0)
    if not payment_id or not buyer_tg:
        return {
            'ok': False,
            'refunded': False,
            'error': 'Нет payment_id для возврата Stars',
        }
    if not BOT_TOKEN:
        return {
            'ok': False,
            'refunded': False,
            'error': 'RENTALS_BOT_TOKEN не настроен',
        }

    from frame_verification.service import refund_star_payment_api

    refund = refund_star_payment_api(BOT_TOKEN, buyer_tg, payment_id)
    if not refund.get('ok'):
        err = (refund.get('error') or '').lower()
        already = 'already' in err or 'refunded' in err
        if not already:
            return {
                'ok': False,
                'refunded': False,
                'error': refund.get('error') or 'Telegram отклонил возврат',
                'paymentId': payment_id,
            }
    storage._conn.execute(
        "UPDATE rentals_orders SET status='refunded' WHERE id=?",
        (int(order['id']),),
    )
    storage._conn.commit()
    return {
        'ok': True,
        'refunded': True,
        'paymentId': payment_id,
        'priceStars': int(order.get('price_stars') or 0),
        'buyerTelegramId': buyer_tg,
    }


def admin_unlist(telegram_id: int, listing_id: int,
                 data: dict | None = None) -> dict[str, Any]:
    data = data or {}
    opener_admin = is_admin(telegram_id) or is_admin(int(data.get('openerTelegramId') or 0))
    user, eff_tg, storage = _actor_user(telegram_id, data)
    _ensure_tables(storage)
    row = storage._conn.execute(
        'SELECT * FROM gift_rentals WHERE id=?', (int(listing_id),)
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Лот не найден'}
    rental = dict(row)
    is_owner = bool(user and int(user['id']) == int(rental['owner_user_id']))
    admin = opener_admin or is_admin(eff_tg)
    if not (admin or is_owner):
        return {'ok': False, 'error': 'Нет доступа'}

    if rental['status'] == 'rented':
        if not admin:
            return {'ok': False, 'error': 'Лот уже арендован — дождитесь окончания срока'}
        want_refund = _truthy(
            data.get('refundStars') if 'refundStars' in data else data.get('refund')
        )
        refund_info: dict[str, Any] = {'refunded': False, 'skipped': True}
        if want_refund:
            refund_info = _refund_rental_order(storage, rental)
            # Даже при ошибке возврата продолжаем снятие аренды —
            # предмет должен вернуться владельцу.
        _return_rental(storage, rental, archive=True)
        return {
            'ok': True,
            'forced': True,
            'refundRequested': want_refund,
            'refund': refund_info,
            'refunded': bool(refund_info.get('refunded')),
            'refundError': None if refund_info.get('ok', True) else refund_info.get('error'),
        }

    if rental['status'] != 'listed':
        return {'ok': False, 'error': 'Лот уже снят'}

    if rental['item_type'] == 'gift':
        if not _gift_chain_owner(storage, rental['item_key']):
            storage.remove_gift_blockchain_link(rental['item_key'])
    storage._conn.execute(
        "UPDATE gift_rentals SET status='archived', updated_at=? WHERE id=?",
        (_now(), int(listing_id)),
    )
    storage._conn.commit()
    return {'ok': True, 'forced': False}


def my_rentals(telegram_id: int, data: dict | None = None) -> dict[str, Any]:
    data = data or {}
    user, _eff_tg, storage = _actor_user(telegram_id, data)
    _ensure_tables(storage)
    _expire_due(storage)
    if not user:
        return {'ok': False, 'error': 'Войдите по номеру в профиле', 'rentals': []}
    rows = storage._conn.execute(
        '''SELECT * FROM gift_rentals
           WHERE renter_user_id=? AND status='rented'
           ORDER BY expires_at ASC, id DESC''',
        (int(user['id']),),
    ).fetchall()
    return {
        'ok': True,
        'rentals': [serialize_listing(storage, dict(r)) for r in rows],
    }


def _resolve_rent_hours(rental: dict, duration_hours: int | None) -> tuple[Optional[int], Optional[dict]]:
    options = _normalize_price_options(
        rental.get('duration_options_json'),
        int(rental.get('price_stars') or 0),
    )
    hours_list = [int(o['hours']) for o in options]
    fixed = rental.get('duration_hours')
    if fixed is not None:
        hours = int(fixed)
    else:
        if duration_hours is None:
            return None, {
                'ok': False,
                'error': 'Укажите срок аренды',
                'durationOptions': options,
            }
        hours = int(duration_hours)
        if hours not in hours_list:
            return None, {
                'ok': False,
                'error': 'Недопустимый срок',
                'durationOptions': options,
            }
    if hours <= 0:
        return None, {'ok': False, 'error': 'Неверный срок'}
    return hours, None


def prepare_rent_invoice(
    telegram_id: int,
    listing_id: int,
    *,
    duration_hours: int | None = None,
    data: dict | None = None,
) -> dict[str, Any]:
    data = data or {}
    user, eff_tg, storage = _actor_user(telegram_id, data)
    _ensure_tables(storage)
    _expire_due(storage, force=True)
    if not user:
        return {'ok': False, 'error': 'Войдите по номеру в профиле'}
    if is_market_closed(storage) and not _is_admin_request(telegram_id, data, eff_tg):
        return {'ok': False, 'error': 'Маркет временно закрыт', 'closed': True}

    row = storage._conn.execute(
        'SELECT * FROM gift_rentals WHERE id=?', (int(listing_id),)
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Лот не найден'}
    rental = dict(row)
    if rental['status'] != 'listed':
        return {'ok': False, 'error': 'Лот недоступен'}

    hours, err = _resolve_rent_hours(rental, duration_hours)
    if err:
        return err

    renter_id = int(user['id'])
    admin_free = _is_admin_request(telegram_id, data, eff_tg)
    if int(rental['owner_user_id']) == renter_id and not admin_free:
        return {'ok': False, 'error': 'Нельзя арендовать свой лот'}

    price = _price_for_hours(rental, hours)
    if price <= 0 or admin_free:
        # Free lots or admin: complete immediately without payment
        result = rent_listing(
            telegram_id, listing_id,
            duration_hours=hours,
            data={**data, 'paidViaInvoice': True, 'adminFree': True} if admin_free else {**data, 'paidViaInvoice': True},
        )
        if result.get('ok') and admin_free and price > 0:
            result['adminFree'] = True
            notice = result.get('notice')
            if isinstance(notice, dict):
                toast = notice.get('toast') or ''
                notice['toast'] = ((toast + ' · ') if toast else '') + 'Админ: без оплаты Stars'
                result['notice'] = notice
        return result

    # Pay with KoalGram Stars (in-app balance), transfer to listing owner
    owner_id = int(rental['owner_user_id'])
    balance = int(storage.get_stars_balance(renter_id) or 0)
    if balance < price:
        return {
            'ok': False,
            'error': f'Недостаточно KoalGram Stars. Нужно {price}, у вас {balance}',
            'needStars': price,
            'balance': balance,
        }

    title = (rental.get('title') or rental.get('item_key') or 'Аренда')
    try:
        storage.adjust_stars_balance(
            renter_id,
            -price,
            title='Аренда NFT',
            description=f'KoalGram Market · {title}',
            peer_user_id=owner_id,
        )
        storage.adjust_stars_balance(
            owner_id,
            price,
            title='Доход с аренды',
            description=f'KoalGram Market · {title}',
            peer_user_id=renter_id,
        )
    except Exception as exc:
        return {'ok': False, 'error': f'Ошибка оплаты Stars: {exc}'}

    # Verify debit actually applied (non-infinite stars mode)
    after = int(storage.get_stars_balance(renter_id) or 0)
    if (not config.INFINITE_STARS) and after > (balance - price):
        try:
            storage.adjust_stars_balance(renter_id, price, title='Возврат аренды', description='rollback')
            storage.adjust_stars_balance(owner_id, -price, title='Возврат аренды', description='rollback')
        except Exception:
            pass
        return {'ok': False, 'error': 'Не удалось списать KoalGram Stars'}

    result = rent_listing(
        telegram_id, listing_id,
        duration_hours=hours,
        data={**data, 'paidViaInvoice': True},
    )
    if not result.get('ok'):
        try:
            storage.adjust_stars_balance(
                renter_id, price,
                title='Возврат аренды',
                description=f'KoalGram Market · {title}',
            )
            storage.adjust_stars_balance(
                owner_id, -price,
                title='Отмена дохода',
                description=f'KoalGram Market · {title}',
            )
        except Exception:
            pass
        return result

    # Track paid order for possible admin refund
    now = _now()
    nonce = secrets.token_hex(4)
    payload = f'rent:{int(listing_id)}:{renter_id}:{int(hours)}:{nonce}'
    storage._conn.execute(
        '''INSERT INTO rentals_orders
           (payload, listing_id, renter_user_id, telegram_user_id,
            duration_hours, price_stars, status, payment_id, created_at, paid_at, expires_at)
           VALUES (?,?,?,?,?,?, 'paid', 'airgram', ?,?,?)''',
        (
            payload, int(listing_id), renter_id,
            int(data.get('openerTelegramId') or eff_tg or telegram_id or 0),
            int(hours), price, now, now, now + 30 * 60,
        ),
    )
    storage._conn.commit()

    notice = result.get('notice')
    if isinstance(notice, dict):
        toast = notice.get('toast') or ''
        notice['toast'] = ((toast + ' · ') if toast else '') + f'Списано {price} KoalGram Stars'
        result['notice'] = notice
    result['needInvoice'] = False
    result['paidWithKoalGramStars'] = True
    result['priceStars'] = price
    result['balance'] = int(storage.get_stars_balance(renter_id) or 0)
    return result


def validate_rent_payment(payload: str, total_amount: int) -> dict[str, Any]:
    payload = (payload or '').strip()
    storage = _open()
    _ensure_tables(storage)
    row = storage._conn.execute(
        'SELECT * FROM rentals_orders WHERE payload=?', (payload,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Счёт не найден'}
    order = dict(row)
    if order['status'] != 'pending':
        return {'ok': False, 'error': 'Счёт уже обработан'}
    if int(order['expires_at'] or 0) < _now():
        storage._conn.execute(
            "UPDATE rentals_orders SET status='cancelled' WHERE id=?",
            (int(order['id']),),
        )
        storage._conn.commit()
        return {'ok': False, 'error': 'Счёт истёк'}
    if int(total_amount or 0) != int(order['price_stars']):
        return {'ok': False, 'error': 'Неверная сумма'}
    listing = storage._conn.execute(
        'SELECT status FROM gift_rentals WHERE id=?',
        (int(order['listing_id']),),
    ).fetchone()
    if not listing or listing['status'] != 'listed':
        return {'ok': False, 'error': 'Лот недоступен'}
    return {'ok': True}


def complete_rent_payment(
    payload: str,
    payment_id: str,
    telegram_user_id: int,
) -> dict[str, Any]:
    payload = (payload or '').strip()
    storage = _open()
    _ensure_tables(storage)
    row = storage._conn.execute(
        'SELECT * FROM rentals_orders WHERE payload=?', (payload,),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Счёт не найден'}
    order = dict(row)
    if order['status'] == 'paid':
        return {'ok': True, 'already': True}
    if order['status'] != 'pending':
        return {'ok': False, 'error': 'Счёт недоступен'}
    if int(order.get('telegram_user_id') or 0) and int(telegram_user_id or 0):
        if int(order['telegram_user_id']) != int(telegram_user_id):
            return {'ok': False, 'error': 'Оплата от другого аккаунта'}

    result = rent_listing(
        int(telegram_user_id or order['telegram_user_id'] or 0),
        int(order['listing_id']),
        duration_hours=int(order['duration_hours']),
        data={
            'paidViaInvoice': True,
            'token': '',
            # force renter from order
            '_invoiceRenterUserId': int(order['renter_user_id']),
        },
    )
    if not result.get('ok'):
        return result

    storage._conn.execute(
        '''UPDATE rentals_orders SET
           status='paid', payment_id=?, paid_at=?
           WHERE id=?''',
        (str(payment_id or ''), _now(), int(order['id'])),
    )
    storage._conn.commit()

    # Credit listing owner with in-app Stars (renter paid via Telegram invoice)
    price = int(order['price_stars'] or 0)
    listing = result.get('listing') or {}
    owner_id = int((listing.get('owner') or {}).get('id') or 0)
    if not owner_id:
        row_l = storage._conn.execute(
            'SELECT owner_user_id, title, item_key FROM gift_rentals WHERE id=?',
            (int(order['listing_id']),),
        ).fetchone()
        if row_l:
            owner_id = int(row_l['owner_user_id'] or 0)
            if not listing.get('title'):
                listing['title'] = row_l['title'] or row_l['item_key']
    if price > 0 and owner_id:
        try:
            storage.adjust_stars_balance(
                owner_id,
                price,
                title='Доход с аренды',
                description=f"KoalGram Market · {listing.get('title') or listing.get('itemKey') or order['listing_id']}",
            )
        except Exception:
            log = __import__('logging').getLogger('rentals')
            log.exception('Failed to credit rental owner %s', owner_id)

    return {
        'ok': True,
        'product': 'rent',
        'listing': result.get('listing'),
        'notice': result.get('notice'),
        'priceStars': price,
        'ownerCredited': bool(price > 0 and owner_id),
    }


def rent_listing(telegram_id: int, listing_id: int, *,
                 duration_hours: int | None = None,
                 data: dict | None = None) -> dict[str, Any]:
    data = data or {}
    user, eff_tg, storage = _actor_user(telegram_id, data)
    _ensure_tables(storage)
    _expire_due(storage, force=True)

    invoice_renter = int(data.get('_invoiceRenterUserId') or 0)
    paid_via_invoice = bool(data.get('paidViaInvoice'))

    if invoice_renter and paid_via_invoice:
        user = storage.get_user_by_id(invoice_renter)
    if not user:
        return {'ok': False, 'error': 'Войдите по номеру в профиле'}

    row = storage._conn.execute(
        'SELECT * FROM gift_rentals WHERE id=?', (int(listing_id),)
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'Лот не найден'}
    rental = dict(row)
    if rental['status'] != 'listed':
        return {'ok': False, 'error': 'Лот недоступен'}

    hours, err = _resolve_rent_hours(rental, duration_hours)
    if err:
        return err

    price = int(rental.get('price_stars') or 0)
    renter_id = int(user['id'])
    admin_free = bool(data.get('adminFree')) or _is_admin_request(telegram_id, data, eff_tg)
    if int(rental['owner_user_id']) == renter_id and not admin_free:
        return {'ok': False, 'error': 'Нельзя арендовать свой лот'}

    # Payment already handled in prepare_rent_invoice (KoalGram Stars)
    if price > 0 and not paid_via_invoice and not admin_free:
        return {
            'ok': False,
            'error': 'Оплатите аренду KoalGram Stars через Market',
        }

    now = _now()
    expires = now + hours * 3600
    notice = None
    same_user = int(rental['owner_user_id']) == renter_id

    if rental['item_type'] == 'gift':
        saved = storage.get_saved_star_gift_by_id(int(rental['saved_id']))
        if not saved:
            return {'ok': False, 'error': 'Подарок пропал'}
        if int(saved['owner_user_id']) != int(rental['owner_user_id']):
            return {'ok': False, 'error': 'Подарок больше не у владельца лота'}
        if storage.saved_gift_chain_owner(saved):
            return {'ok': False,
                    'error': 'Подарок перенесён в блокчейн-кошелёк и недоступен для аренды'}
        if not same_user:
            saved = _transfer_gift_ownership(
                storage, saved, to_user_id=renter_id, notify_saved_messages=True,
            )
        if not _gift_chain_owner(storage, rental['item_key']):
            storage.upsert_gift_blockchain_link(rental['item_key'], BLOCKCHAIN_ADDRESS)
        notice = _gift_notice_payload(storage, saved)
    else:
        try:
            # Move collectible username from current holder to renter
            from db.storage import normalize_collectible_username
            uname = normalize_collectible_username(rental['item_key'])
            if same_user:
                # Keep ownership; just mark active for the rental period
                storage._conn.execute(
                    'UPDATE collectible_usernames SET active=1 WHERE lower(username)=?',
                    (uname,),
                )
                storage._conn.commit()
            else:
                current = storage.get_collectible_username_owner(uname)
                if current and int(current['user_id']) != renter_id:
                    storage._conn.execute(
                        'DELETE FROM collectible_usernames WHERE lower(username)=?',
                        (uname,),
                    )
                    storage._conn.commit()
                storage.grant_collectible_username(
                    renter_id, uname, active=True, price='')
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}
        notice = {
            'toast': f'Вы получите @{rental["item_key"]} на время аренды.',
            'title': 'Collectible Username',
            'subtitle': f'@{rental["item_key"]}',
            'model': '',
            'pattern': '',
            'backdrop': '',
            'gift': None,
            'slug': rental['item_key'],
        }

    storage._conn.execute(
        '''UPDATE gift_rentals SET
           status='rented', renter_user_id=?, rented_at=?, expires_at=?,
           updated_at=?
           WHERE id=?''',
        (renter_id, now, expires, now, int(rental['id'])),
    )
    storage._conn.commit()
    row2 = storage._conn.execute(
        'SELECT * FROM gift_rentals WHERE id=?', (int(rental['id']),)
    ).fetchone()
    return {
        'ok': True,
        'listing': serialize_listing(storage, dict(row2)),
        'notice': notice,
    }

def handle_action(action: str, telegram_id: int, data: dict | None = None) -> dict[str, Any]:
    data = data or {}
    action = (action or '').strip().lower().replace('_', '-')
    tid = int(telegram_id or 0)
    token = str(data.get('token') or '')

    if action in ('me',):
        return me(
            tid,
            token=token,
            first_name=str(data.get('firstName') or data.get('first_name') or ''),
            last_name=str(data.get('lastName') or data.get('last_name') or ''),
            tg_username=str(data.get('username') or data.get('tgUsername') or ''),
        )
    if action == 'session':
        return get_session(token)
    if action in ('send-login-code', 'login-send-code'):
        return send_login_code(str(data.get('phone') or ''), tid)
    if action in ('login', 'complete-login'):
        return complete_login(
            str(data.get('phone') or ''),
            str(data.get('code') or ''),
            opener_telegram_id=tid,
        )
    if action == 'logout':
        return logout(token)
    if action in ('inventory', 'my-gifts', 'my-inventory'):
        return my_inventory(tid, data)
    if action in ('catalog', 'list', 'listings'):
        return list_catalog(telegram_id=tid, data=data)
    if action in ('list-gift', 'admin-list-gift'):
        return admin_list_gift(
            tid,
            str(data.get('giftRef') or data.get('gift') or data.get('link') or data.get('slug') or ''),
            title=str(data.get('title') or ''),
            price_stars=int(data.get('priceStars') or data.get('price') or 0),
            duration_hours=(
                int(data['durationHours'])
                if data.get('durationHours') not in (None, '', 0, '0')
                else None
            ),
            duration_options=data.get('durationOptions') or data.get('prices'),
            note=str(data.get('note') or ''),
            data=data,
        )
    if action in ('list-username', 'admin-list-username'):
        return admin_list_username(
            tid,
            str(data.get('username') or data.get('usernameRef') or ''),
            title=str(data.get('title') or ''),
            price_stars=int(data.get('priceStars') or data.get('price') or 0),
            duration_hours=(
                int(data['durationHours'])
                if data.get('durationHours') not in (None, '', 0, '0')
                else None
            ),
            duration_options=data.get('durationOptions') or data.get('prices'),
            note=str(data.get('note') or ''),
            data=data,
        )
    if action in ('admin-settings', 'get-settings', 'settings'):
        return admin_get_settings(tid, data)
    if action in ('admin-set-settings', 'set-settings'):
        return admin_set_settings(tid, data)
    if action in ('unlist', 'admin-unlist'):
        return admin_unlist(tid, int(data.get('id') or data.get('listingId') or 0), data=data)
    if action in ('my-rentals', 'rentals-mine'):
        return my_rentals(tid, data)
    if action in ('prepare-rent-invoice', 'rent-invoice', 'create-rent-invoice'):
        hours = data.get('durationHours')
        return prepare_rent_invoice(
            tid,
            int(data.get('id') or data.get('listingId') or 0),
            duration_hours=int(hours) if hours not in (None, '') else None,
            data=data,
        )
    if action in ('validate-rent-payment',):
        return validate_rent_payment(
            str(data.get('payload') or ''),
            int(data.get('totalAmount') or data.get('amount') or 0),
        )
    if action in ('complete-rent-payment',):
        return complete_rent_payment(
            str(data.get('payload') or ''),
            str(data.get('paymentId') or ''),
            int(data.get('telegramUserId') or tid or 0),
        )
    if action == 'rent':
        hours = data.get('durationHours')
        # Legacy direct rent disabled for paid lots — use invoice
        return prepare_rent_invoice(
            tid,
            int(data.get('id') or data.get('listingId') or 0),
            duration_hours=int(hours) if hours not in (None, '') else None,
            data=data,
        )
    if action in ('expire', 'tick'):
        storage = _open()
        _ensure_tables(storage)
        n = _expire_due(storage, force=True)
        return {'ok': True, 'expired': n}

    return {'ok': False, 'error': f'Unknown action: {action}'}

