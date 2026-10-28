#!/usr/bin/env python3
"""Import star gifts catalog and upgrade attributes from real Telegram.

Usage:
  pip install telethon
  python tools/import_star_gifts.py --session mysession
  python tools/import_star_gifts.py --session mysession --menu
  python tools/import_star_gifts.py --session mysession --name "Blue Star"
  python tools/import_star_gifts.py --session mysession --name "Blue Star" --gift-count 500
  python tools/import_star_gifts.py --session mysession --name "Heart" --upgrade-only
  python tools/import_star_gifts.py --session mysession --name "Plush Pepe" \
    --drop-at "2026-11-01 19:30"  # Moscow time
  python tools/import_star_gifts.py --session mysession --name "Heart" \\
    --auction true --num-of-rounds 5 --round-time 5 --win-per-round 3 --min-bid 100

Interactive menu (default without --catalog-only / --upgrade-only / --name):
  1. Import gift by name or id (then publish now or schedule a drop)
  2. Import upgrade attributes (models / patterns / backdrops) for a gift
  3. Import full Telegram catalog
  4. Import gift as auction (with rounds / min bid settings)
  5. Delete gift from local DB (full remove or hide from shop only)
  6. Import upgrade attributes for auction gift
  7. Restore gift to shop by id (after hide from shop)
  8. Import craft models for a gift (crafted upgrade attributes)
  9. Import gift from Custom Emoji ID
  10. Import models from Custom Emoji ID (patterns/backdrops from random gift; chance per model)
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frame_verification.telethon_env import (
    prepare_telethon_session,
    telethon_credentials,
)
import config
from db.storage import Storage, normalize_unix_time
from handlers.stickers import build_document_attr_image_size, INPUT_STICKER_SET_EMPTY
from handlers.star_gift_rarity import (
    TIER_EPIC,
    TIER_LEGENDARY,
    TIER_RARE,
    TIER_UNCOMMON,
    parse_telegram_rarity,
    rarity_label,
)

try:
    from telethon import TelegramClient
    from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
    from telethon.tl.functions.payments import (
        GetSavedStarGiftsRequest,
        GetStarGiftsRequest,
        GetStarGiftUpgradeAttributesRequest,
    )
    from telethon.tl.types import (
        DocumentAttributeImageSize,
        DocumentAttributeSticker,
        StarGiftAttributeBackdrop,
        StarGiftAttributeModel,
        StarGiftAttributePattern,
    )
except ImportError:
    print('Install telethon: pip install telethon', file=sys.stderr)
    sys.exit(1)

GIFTCHANGES_API_ROOT = 'https://api.changes.tg'
GIFTCHANGES_CREDIT_LINE = 'Powered by @GiftChanges (api.changes.tg)'
_giftchanges_ids_cache: Optional[dict[int, str]] = None


def _permille(attr) -> int:
    return parse_telegram_rarity(getattr(attr, 'rarity', None))


_RARITY_TIER_ALIASES = {
    'uncommon': TIER_UNCOMMON,
    'необычный': TIER_UNCOMMON,
    'необычная': TIER_UNCOMMON,
    'редкий': TIER_RARE,
    'редкая': TIER_RARE,
    'rare': TIER_RARE,
    'эпик': TIER_EPIC,
    'эпический': TIER_EPIC,
    'эпическая': TIER_EPIC,
    'epic': TIER_EPIC,
    'легендарный': TIER_LEGENDARY,
    'легендарная': TIER_LEGENDARY,
    'legendary': TIER_LEGENDARY,
}


def _parse_rarity_input(raw: str, *, default: int = 100) -> int:
    raw = (raw or '').strip()
    if not raw:
        return int(default)
    tier = _RARITY_TIER_ALIASES.get(raw.casefold())
    if tier is not None:
        return tier
    if raw.endswith('%'):
        try:
            pct = float(raw[:-1].strip().replace(',', '.'))
        except ValueError as exc:
            raise ValueError(f'invalid percent: {raw!r}') from exc
        if pct <= 0:
            raise ValueError('percent must be positive')
        return max(1, int(round(pct * 10)))
    if raw.lstrip('-').isdigit():
        value = int(raw)
        if value == 0:
            raise ValueError('rarity must be non-zero')
        return value
    raise ValueError(f'invalid rarity: {raw!r}')


def _prompt_rarity_permille(*, default: int = 100) -> int:
    default_label = rarity_label(default)
    while True:
        raw = input(
            f'  Шанс [{default_label}], Enter=по умолчанию '
            f'(permille, %, uncommon/rare/epic/legendary): '
        ).strip()
        try:
            return _parse_rarity_input(raw, default=default)
        except ValueError:
            print('  Введите permille (100), процент (10%), '
                  'редкость или оставьте пусто.')


def _doc_extension(mime: str) -> str:
    mime = mime or 'application/x-tgsticker'
    if 'tgsticker' in mime:
        return '.tgs'
    if mime == 'video/webm':
        return '.webm'
    return '.webp'


def _build_sticker_attrs(width: int = 512, height: int = 512,
                         alt: str = '🎁') -> list[str]:
    width = int(width or 512)
    height = int(height or 512)
    alt = alt or '🎁'
    out = [build_document_attr_image_size(width, height).hex()]
    from mtproto import tl
    bw = tl.Writer()
    bw.u32(0x6319d612)
    bw.i32(0)
    bw.string(alt)
    bw.u32(INPUT_STICKER_SET_EMPTY)
    out.append(bw.bytes_out().hex())
    return out


def _gift_sticker_attrs(doc, alt: str = '🎁') -> list[str]:
    w, h = 512, 512
    for attr in doc.attributes or []:
        if isinstance(attr, DocumentAttributeImageSize):
            w, h = attr.w, attr.h
        elif isinstance(attr, DocumentAttributeSticker):
            alt = attr.alt or alt
    return _build_sticker_attrs(w, h, alt)


def _giftchanges_request(url: str, *, method: str = 'GET'):
    req = urllib_request.Request(
        url,
        headers={'User-Agent': 'mytelegram-import-star-gifts/1.0'},
        method=method,
    )
    return urllib_request.urlopen(req, timeout=20)


async def _giftchanges_json(path: str, *,
                            allow_404: bool = False) -> Optional[dict]:
    url = f'{GIFTCHANGES_API_ROOT}{path}'

    def _load() -> Optional[dict]:
        try:
            with _giftchanges_request(url) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib_error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            raise

    return await asyncio.to_thread(_load)


async def _giftchanges_download(path: str, dest: Path) -> bool:
    url = f'{GIFTCHANGES_API_ROOT}{path}'

    def _load() -> bool:
        if dest.is_file() and dest.stat().st_size > 0:
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + '.part')
        try:
            with _giftchanges_request(url) as resp, tmp.open('wb') as fh:
                shutil.copyfileobj(resp, fh)
            if not tmp.is_file() or tmp.stat().st_size <= 0:
                return False
            tmp.replace(dest)
            return True
        finally:
            if tmp.is_file() and tmp != dest:
                tmp.unlink(missing_ok=True)

    try:
        return await asyncio.to_thread(_load)
    except Exception as exc:
        print(f'  WARN: download from api.changes.tg {path}: {exc}',
              file=sys.stderr)
        return False


def _giftchanges_name_keys(name: str) -> set[str]:
    raw = (name or '').strip()
    if not raw:
        return set()
    casefolded = raw.casefold()
    cleaned = ''.join(ch if ch.isalnum() else ' ' for ch in casefolded)
    parts = cleaned.split()
    keys = {casefolded}
    if parts:
        keys.add(''.join(parts))
        keys.add('-'.join(parts))
        keys.add('_'.join(parts))
        keys.add(' '.join(parts))
    return {k for k in keys if k}


async def fetch_giftchanges_ids_map() -> dict[int, str]:
    global _giftchanges_ids_cache
    if _giftchanges_ids_cache is not None:
        return dict(_giftchanges_ids_cache)
    payload = await _giftchanges_json('/ids')
    out: dict[int, str] = {}
    if isinstance(payload, dict):
        for raw_id, title in payload.items():
            if str(raw_id).lstrip('-').isdigit():
                out[int(raw_id)] = str(title)
    _giftchanges_ids_cache = dict(out)
    return out


def find_giftchanges_gifts_by_query(ids_map: dict[int, str],
                                    query: str) -> List[dict]:
    query = (query or '').strip()
    if not query:
        return []
    if query.lstrip('-').isdigit():
        gid = int(query)
        title = ids_map.get(gid)
        if title is not None:
            return [{'gift_id': gid, 'title': title, 'stars': None}]
        return [{'gift_id': gid, 'title': str(gid), 'stars': None}]
    q = query.casefold()
    q_keys = _giftchanges_name_keys(query)
    exact = []
    partial = []
    for gid, title in ids_map.items():
        keys = _giftchanges_name_keys(title)
        item = {'gift_id': int(gid), 'title': title, 'stars': None}
        if q in keys or q_keys.intersection(keys):
            exact.append(item)
        elif q in title.casefold():
            partial.append(item)
    if exact:
        return sorted(exact, key=lambda g: (g['title'].casefold(), g['gift_id']))
    return sorted(partial, key=lambda g: (g['title'].casefold(), g['gift_id']))


def _synthetic_gift_doc_id(gift_id: int) -> int:
    digest = hashlib.blake2b(str(int(gift_id)).encode('utf-8'),
                             digest_size=8).digest()
    return (int.from_bytes(digest, 'big') & 0x0FFFFFFFFFFFFFFF) | 0x7000000000000000


def _synthetic_gift_id_from_emoji(emoji_id: int) -> int:
    digest = hashlib.blake2b(
        f'emoji:{int(emoji_id)}'.encode('utf-8'),
        digest_size=8,
    ).digest()
    return (int.from_bytes(digest, 'big') & 0x0FFFFFFFFFFFFFFF) | 0x6100000000000000


async def fetch_custom_emoji_document(client: TelegramClient,
                                      emoji_id: int):
    result = await client(
        GetCustomEmojiDocumentsRequest(document_id=[int(emoji_id)]))
    if not result:
        return None
    return result[0]

async def _download_doc(client: TelegramClient, doc, dest: Path) -> bool:
    if dest.is_file() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    try:
        saved = await client.download_media(doc, file=str(tmp))
        target = Path(saved) if saved else tmp
        if not target.is_file() or target.stat().st_size <= 0:
            return False
        if target != dest:
            target.replace(dest)
        return True
    except Exception as exc:
        print(f'  WARN: download {doc.id}: {exc}', file=sys.stderr)
        return False
    finally:
        if tmp.is_file() and tmp != dest:
            tmp.unlink(missing_ok=True)


def _doc_date(doc) -> int:
    raw = getattr(doc, 'date', None)
    if raw is None:
        return int(time.time())
    if hasattr(raw, 'timestamp'):
        return int(raw.timestamp())
    return int(raw)


async def _import_document(client: TelegramClient, storage: Storage,
                           doc, subdir: str, *, commit: bool = True) -> dict:
    ext = _doc_extension(doc.mime_type)
    out_dir = Path(config.STAR_GIFTS_DATA_DIR) / subdir
    path = out_dir / f'{doc.id}{ext}'
    await _download_doc(client, doc, path)
    thumb_path = path.with_name(f'{path.stem}_thumb.webp')
    if not thumb_path.is_file():
        from handlers.media import render_tgs_thumbnail
        render_tgs_thumbnail(path, thumb_path)
    attrs = _gift_sticker_attrs(doc)
    rel = path
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        pass
    thumb_rel = thumb_path
    try:
        thumb_rel = thumb_path.relative_to(ROOT)
    except ValueError:
        pass
    row = {
        'id': doc.id,
        'access_hash': doc.access_hash,
        'set_id': config.STAR_GIFTS_DOC_SET_ID,
        'file_path': str(rel),
        'mime_type': doc.mime_type or 'application/x-tgsticker',
        'size': int(getattr(doc, 'size', 0) or 0),
        'width': 512,
        'height': 512,
        'alt': '🎁',
        'file_reference': bytes(doc.file_reference or b''),
        'date': _doc_date(doc),
        'attributes_json': json.dumps(attrs),
        'thumb_path': str(thumb_rel) if thumb_path.is_file() else '',
    }
    for attr in doc.attributes or []:
        if isinstance(attr, DocumentAttributeImageSize):
            row['width'] = attr.w
            row['height'] = attr.h
        elif isinstance(attr, DocumentAttributeSticker):
            row['alt'] = attr.alt or '🎁'
    storage.upsert_sticker_document(row, commit=commit)
    return row


def _peer_from_released(released) -> tuple[Optional[int], Optional[int]]:
    if released is None:
        return None, None
    cls = type(released).__name__
    if cls == 'PeerUser':
        return 0, int(released.user_id)
    if cls == 'PeerChannel':
        return 1, int(released.channel_id)
    return None, None


def _released_by_label(storage: Storage, peer_type, peer_id) -> str:
    if peer_id is None:
        return ''
    if int(peer_type or 0) == 0:
        user = storage.get_user_by_id(int(peer_id))
        if user and user.get('username'):
            return '@' + str(user['username'])
        if user:
            name = ' '.join(
                part for part in (
                    user.get('first_name') or '',
                    user.get('last_name') or '',
                ) if part).strip()
            return name or str(user['id'])
    return str(peer_id)


def _resolve_released_by(storage: Storage, raw: str) -> tuple[Optional[int], Optional[int]]:
    raw = (raw or '').strip()
    if not raw:
        return None, None
    if raw.lstrip('-').isdigit():
        uid = int(raw)
        if storage.get_user_by_id(uid):
            return 0, uid
        print(f'  WARN: пользователь id={uid} не найден', file=sys.stderr)
        return None, None
    username = raw.lstrip('@').lower()
    user = storage.get_user_by_username(username)
    if user:
        return 0, int(user['id'])
    print(f'  WARN: username {username!r} не найден', file=sys.stderr)
    return None, None


def _parse_bool_flag(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    v = value.strip().casefold()
    if v in ('true', '1', 'yes', 'y', 'да', 'on'):
        return True
    if v in ('false', '0', 'no', 'n', 'нет', 'off'):
        return False
    raise ValueError(f'invalid boolean: {value!r}')


def parse_moscow_drop_time(value: str) -> int:
    """Parse a drop time entered in Moscow local time."""
    raw = (value or '').replace('\u200b', '').replace('\ufeff', '')
    raw = raw.replace('\u00a0', ' ')
    raw = re.sub(r'\s+', ' ', raw).strip()
    raw = raw.replace('T', ' ')
    parsed = None
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S',
                '%d.%m.%Y %H:%M', '%d.%m.%Y %H:%M:%S'):
        try:
            parsed = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(
            'drop time format must be YYYY-MM-DD HH:MM (Moscow)')
    result = int(parsed.replace(tzinfo=ZoneInfo('Europe/Moscow')).timestamp())
    if result <= int(time.time()):
        raise ValueError('drop time must be in the future (Moscow time)')
    return result


def prompt_moscow_drop_at() -> Optional[int]:
    print('  Отложенный дроп:')
    print('    1. Опубликовать сразу')
    print('    2. Запланировать дату и время (МСК)')
    choice = input('  Выбор [1/2], Enter=сразу: ').strip()
    if choice not in ('2', 'schedule', 'отложить', 'дата'):
        return 0
    while True:
        raw = input(
            '  Дата и время дропа, МСК (YYYY-MM-DD HH:MM): '
        ).strip()
        try:
            drop_at = parse_moscow_drop_time(raw)
            local = datetime.fromtimestamp(
                drop_at, ZoneInfo('Europe/Moscow'))
            print(f'  Дроп запланирован на {local:%d.%m.%Y %H:%M} МСК')
            return drop_at
        except ValueError as exc:
            print(f'  {exc}')


def _coerce_bool(value) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        raw = value.strip().casefold()
        if raw in ('true', '1', 'yes', 'да', 'on'):
            return True
        if raw in ('false', '0', 'no', 'нет', 'off', ''):
            return False
    return bool(value)


def _optional_positive_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return None
    return iv if iv > 0 else None


def _gift_row_from_tl(gift, document_id: int) -> dict:
    peer_type, peer_id = _peer_from_released(getattr(gift, 'released_by', None))
    row = {
        'gift_id': int(gift.id),
        'stars': int(gift.stars),
        'convert_stars': int(gift.convert_stars),
        'upgrade_stars': int(gift.upgrade_stars) if gift.upgrade_stars else None,
        'limited': bool(getattr(gift, 'limited', False)),
        'sold_out': bool(getattr(gift, 'sold_out', False)),
        'birthday': bool(getattr(gift, 'birthday', False)),
        'require_premium': bool(getattr(gift, 'require_premium', False)),
        'limited_per_user': bool(getattr(gift, 'limited_per_user', False)),
        'availability_total': getattr(gift, 'availability_total', None),
        'availability_remains': getattr(gift, 'availability_remains', None),
        'availability_resale': 0,
        'title': getattr(gift, 'title', None),
        'document_id': document_id,
        'released_by_peer_type': peer_type,
        'released_by_peer_id': peer_id,
        'per_user_total': _optional_positive_int(getattr(gift, 'per_user_total', None)),
        'per_user_remains': _optional_positive_int(
            getattr(gift, 'per_user_remains', None)),
        'locked_until_date': normalize_unix_time(
            getattr(gift, 'locked_until_date', None)),
        'is_auction': bool(getattr(gift, 'auction', False)),
        'auction_slug': getattr(gift, 'auction_slug', None),
        'gifts_per_round': getattr(gift, 'gifts_per_round', None),
        'auction_start_date': normalize_unix_time(
            getattr(gift, 'auction_start_date', None)),
        'first_sale_date': normalize_unix_time(
            getattr(gift, 'first_sale_date', None)),
        'last_sale_date': normalize_unix_time(
            getattr(gift, 'last_sale_date', None)),
        'resell_min_stars': None,
        'peer_color_available': bool(getattr(gift, 'peer_color_available', False)),
        'upgrade_variants': int(getattr(gift, 'upgrade_variants', 0) or 0),
        'auction_total_rounds': None,
        'auction_round_duration': None,
        'auction_min_bid': None,
        'auction_end_date': None,
    }
    if not row.get('per_user_total') and not row.get('per_user_remains'):
        row['limited_per_user'] = False
    return row


def _slugify(title: str, gift_id: int) -> str:
    base = ''.join(
        ch.lower() if ch.isalnum() else '-'
        for ch in (title or '').strip())
    base = '-'.join(part for part in base.split('-') if part)
    return base or f'auction-{gift_id}'


def apply_auction_overrides(row: dict, auction_opts: Optional[dict]) -> dict:
    """Apply CLI auction settings; leave non-auction gifts unchanged."""
    if not auction_opts or auction_opts.get('auction') is None:
        return row
    is_auction = bool(auction_opts['auction'])
    row = dict(row)
    if not is_auction:
        row['is_auction'] = 0
        return row

    num_rounds = int(auction_opts.get('num_of_rounds') or 1)
    win_per_round = int(auction_opts.get('win_per_round') or 1)
    round_time_min = int(auction_opts.get('round_time') or 5)
    min_bid = int(auction_opts.get('min_bid') or row.get('stars') or 100)

    total_gifts = win_per_round * num_rounds
    round_seconds = round_time_min * 60
    total_duration = round_seconds * num_rounds
    start = int(time.time()) + 30

    gift_id = int(row['gift_id'])
    row.update({
        'is_auction': 1,
        'limited': 1,
        'availability_total': total_gifts,
        'availability_remains': total_gifts,
        'gifts_per_round': win_per_round,
        'auction_total_rounds': num_rounds,
        'auction_round_duration': round_seconds,
        'auction_min_bid': min_bid,
        'stars': min_bid,
        'auction_start_date': start,
        'auction_end_date': start + total_duration,
        'auction_slug': row.get('auction_slug') or _slugify(
            row.get('title') or '', gift_id),
    })
    return row


def apply_limited_gift_count(row: dict, count: int) -> dict:
    """Mark gift as limited and set total/remaining availability."""
    row = dict(row)
    count = max(1, int(count))
    row['limited'] = True
    row['availability_total'] = count
    row['availability_remains'] = count
    row['sold_out'] = False
    row['first_sale_date'] = None
    row['last_sale_date'] = None
    return row


def apply_metadata_overrides(row: dict, metadata_opts: Optional[dict]) -> dict:
    """Apply explicit CLI metadata overrides for imported gifts."""
    if not metadata_opts:
        return row
    row = dict(row)
    gift_count = metadata_opts.get('gift_count')
    passthrough = {
        k: v for k, v in metadata_opts.items()
        if k != 'gift_count' and v is not None
    }
    for key, value in passthrough.items():
        row[key] = value

    if gift_count is not None:
        row = apply_limited_gift_count(row, gift_count)
    elif (metadata_opts.get('availability_total') is not None
            or metadata_opts.get('availability_remains') is not None):
        row['limited'] = True
    per_user_total = metadata_opts.get('per_user_total')
    per_user_remains = metadata_opts.get('per_user_remains')
    if per_user_total is not None or per_user_remains is not None:
        total = int(per_user_total or 0)
        remains = int(per_user_remains or 0)
        if total <= 0 and remains <= 0:
            row['limited_per_user'] = False
            row['per_user_total'] = None
            row['per_user_remains'] = None
        else:
            row['limited_per_user'] = True
            if per_user_total is not None:
                row['per_user_total'] = total if total > 0 else None
            if per_user_remains is not None:
                row['per_user_remains'] = remains if remains > 0 else None

    if 'limited' in metadata_opts and not bool(row.get('limited')):
        row['availability_total'] = None
        row['availability_remains'] = None
        row['sold_out'] = False
    if 'limited_per_user' in metadata_opts and not bool(row.get('limited_per_user')):
        row['per_user_total'] = None
        row['per_user_remains'] = None
    if 'released_by_peer_id' in metadata_opts:
        row['released_by_peer_type'] = metadata_opts.get('released_by_peer_type')
        row['released_by_peer_id'] = metadata_opts.get('released_by_peer_id')
    if metadata_opts.get('is_tester_gift') is not None:
        row['is_tester_gift'] = bool(metadata_opts['is_tester_gift'])
    return row


def _prompt_int(label: str, *, default: Optional[int] = None,
                minimum: int = 1) -> int:
    hint = f' [{default}]' if default is not None else ''
    while True:
        raw = input(f'{label}{hint}: ').strip()
        if not raw and default is not None:
            return default
        if raw.isdigit() and int(raw) >= minimum:
            return int(raw)
        print('Введите положительное число.')


def _fmt_bool(value) -> str:
    coerced = _coerce_bool(value)
    if coerced is None:
        return '—'
    return 'true' if coerced else 'false'


def _fmt_optional(value) -> str:
    if value is None:
        return '—'
    return str(value)


def _prompt_override_text(label: str, current: Optional[str] = None) -> Optional[str]:
    raw = input(f'{label} [{_fmt_optional(current)}], Enter=оставить: ').strip()
    return raw or None


def _prompt_override_int(label: str, current: Optional[int] = None,
                         *, minimum: int = 0,
                         zero_means_unset: bool = False) -> Optional[int]:
    hint = ', 0=выкл' if zero_means_unset else ''
    while True:
        raw = input(
            f'{label} [{_fmt_optional(current)}], Enter=оставить{hint}: '
        ).strip()
        if not raw:
            return None
        if raw.lstrip('-').isdigit():
            val = int(raw)
            if zero_means_unset and val == 0:
                return None
            if val >= minimum:
                return val
        print('Введите число или оставьте пусто.')


def _prompt_override_bool(label: str, current=None) -> Optional[bool]:
    while True:
        raw = input(
            f'{label} [{_fmt_bool(current)}], Enter=оставить (true/false): '
        ).strip()
        if not raw:
            return None
        try:
            return _parse_bool_flag(raw)
        except ValueError:
            print('Введите true/false или оставьте пусто.')


def _prompt_limited_gift_count(*, limited: Optional[bool] = None,
                               availability_total: Optional[int] = None,
                               availability_remains: Optional[int] = None
                               ) -> tuple[Optional[bool], Optional[int]]:
    """Ask whether the gift is limited and how many copies exist."""
    limited_override = _prompt_override_bool(
        '  Ограниченный тираж (limited)', limited)
    count_default = availability_total
    if count_default is None and availability_remains is not None:
        count_default = availability_remains

    if limited_override is False:
        return False, None

    effective_limited = (
        limited_override if limited_override is not None else bool(limited))
    if not effective_limited:
        return limited_override, None

    count = _prompt_override_int(
        '  Количество подарков', count_default, minimum=1)
    if count is not None:
        return True, count
    if count_default is not None:
        return True, int(count_default)
    return True, None


def prompt_metadata_opts(*,
                         storage: Optional[Storage] = None,
                         title: Optional[str] = None,
                         stars: Optional[int] = None,
                         convert_stars: Optional[int] = None,
                         upgrade_stars: Optional[int] = None,
                         limited: Optional[bool] = None,
                         availability_total: Optional[int] = None,
                         availability_remains: Optional[int] = None,
                         require_premium: Optional[bool] = None,
                         limited_per_user: Optional[bool] = None,
                         per_user_total: Optional[int] = None,
                         per_user_remains: Optional[int] = None,
                         birthday: Optional[bool] = None,
                         peer_color_available: Optional[bool] = None,
                         upgrade_variants: Optional[int] = None,
                         released_by: Optional[str] = None,
                         is_tester_gift: Optional[bool] = None) -> Optional[dict]:
    print()
    print('Параметры подарка:')
    limited_override, gift_count = _prompt_limited_gift_count(
        limited=limited,
        availability_total=availability_total,
        availability_remains=availability_remains,
    )
    out = {
        'title': _prompt_override_text('  Название', title),
        'stars': _prompt_override_int('  Цена (stars)', stars),
        'convert_stars': _prompt_override_int('  Convert stars', convert_stars),
        'upgrade_stars': _prompt_override_int('  Upgrade stars', upgrade_stars),
        'require_premium': _prompt_override_bool(
            '  Require premium', require_premium),
        'limited_per_user': _prompt_override_bool(
            '  Limited per user', limited_per_user),
        'per_user_total': _prompt_override_int(
            '  Per-user total', per_user_total, zero_means_unset=True),
        'per_user_remains': _prompt_override_int(
            '  Per-user remains', per_user_remains, zero_means_unset=True),
        'birthday': _prompt_override_bool('  Birthday', birthday),
        'peer_color_available': _prompt_override_bool(
            '  Peer color available', peer_color_available),
        'upgrade_variants': _prompt_override_int(
            '  Upgrade variants', upgrade_variants),
    }
    released_hint = released_by or 'нет'
    released_raw = input(
        f'  Released By (username/id) [{released_hint}], Enter=оставить, -=убрать: '
    ).strip()
    if released_raw == '-':
        out['released_by_peer_type'] = None
        out['released_by_peer_id'] = None
    elif released_raw and storage is not None:
        peer_type, peer_id = _resolve_released_by(storage, released_raw)
        if peer_id is not None:
            out['released_by_peer_type'] = peer_type
            out['released_by_peer_id'] = peer_id
    if limited_override is not None:
        out['limited'] = limited_override
    if gift_count is not None:
        out['gift_count'] = gift_count
    tester = _prompt_override_bool(
        '  Tester gift (только для тестеров)', is_tester_gift)
    if tester is not None:
        out['is_tester_gift'] = tester
    out = {k: v for k, v in out.items() if v is not None}
    return out or None


def prompt_auction_opts(*, default_min_bid: Optional[int] = None) -> dict:
    print()
    print('Параметры аукциона:')
    num_rounds = _prompt_int('  Кол-во раундов', default=3)
    round_time = _prompt_int('  Длительность раунда (мин)', default=5)
    win_per_round = _prompt_int('  Победителей за раунд (top N)', default=3)
    min_bid = _prompt_int(
        '  Мин. ставка (stars)',
        default=default_min_bid or 100)
    total_gifts = win_per_round * num_rounds
    total_min = round_time * num_rounds
    print(f'  → всего подарков: {total_gifts}, '
          f'длительность аукциона: {total_min} мин')
    return {
        'auction': True,
        'num_of_rounds': num_rounds,
        'win_per_round': win_per_round,
        'round_time': round_time,
        'min_bid': min_bid,
    }


def _reset_auction_runtime(storage: Storage, gift_id: int) -> None:
    storage._conn.execute(
        'DELETE FROM star_gift_auction_state WHERE gift_id=?', (gift_id,))
    storage._conn.execute(
        'DELETE FROM star_gift_auction_bids WHERE gift_id=?', (gift_id,))
    storage._conn.execute(
        'DELETE FROM star_gift_auction_acquired WHERE gift_id=?', (gift_id,))
    storage._conn.commit()


def apply_auction_to_local_gift(storage: Storage, gift: dict,
                                auction_opts: dict) -> dict:
    row = apply_auction_overrides(dict(gift), auction_opts)
    row['shop_hidden'] = 0
    storage.upsert_star_gift(row)
    _reset_auction_runtime(storage, int(row['gift_id']))
    storage.recompute_star_gifts_catalog_hash()
    return row


def _print_auction_summary(row: dict) -> None:
    print(f'  Аукцион: {row["gifts_per_round"]}/раунд × '
          f'{row["auction_total_rounds"]} раундов = '
          f'{row["availability_total"]} подарков, '
          f'{row["auction_round_duration"] // 60} мин/раунд, '
          f'мин. ставка {row["auction_min_bid"]} stars, '
          f'slug={row.get("auction_slug")}')


def _gift_title(gift) -> str:
    return (getattr(gift, 'title', None) or '').strip() or str(gift.id)


async def fetch_telegram_catalog(client: TelegramClient) -> List:
    result = await client(GetStarGiftsRequest(hash=0))
    if type(result).__name__ == 'StarGiftsNotModified':
        return []
    return list(result.gifts or [])


async def fetch_saved_star_gifts(client: TelegramClient,
                                 *, limit: Optional[int] = None) -> List:
    out = []
    offset = ''
    while True:
        batch = 100 if limit is None else min(100, limit - len(out))
        if batch <= 0:
            break
        result = await client(GetSavedStarGiftsRequest(
            peer='me', offset=offset, limit=batch))
        gifts = list(result.gifts or [])
        out.extend(gifts)
        next_offset = getattr(result, 'next_offset', None)
        if not gifts or not next_offset:
            break
        offset = next_offset
    return out


async def import_gift_from_giftchanges(storage: Storage, gift_id: int, *,
                                       auction_opts: Optional[dict] = None,
                                       metadata_opts: Optional[dict] = None,
                                       announce: bool = False) -> bool:
    gift_id = int(gift_id)
    try:
        ids_map = await fetch_giftchanges_ids_map()
    except Exception as exc:
        print(f'  WARN: api.changes.tg /ids for {gift_id}: {exc}',
              file=sys.stderr)
        ids_map = {}
    try:
        details = await _giftchanges_json(f'/gift/{gift_id}', allow_404=True)
    except Exception as exc:
        print(f'  WARN: api.changes.tg /gift/{gift_id}: {exc}', file=sys.stderr)
        details = None

    title = None
    if isinstance(details, dict):
        raw = details.get('gift') or {}
        title = raw.get('name')
    existing = storage.get_star_gift(gift_id)
    title = (title or (existing or {}).get('title') or ids_map.get(gift_id)
             or str(gift_id)).strip()

    storage.ensure_star_gifts_doc_set()
    doc_id = int(existing['document_id']) if existing else _synthetic_gift_doc_id(gift_id)
    path = Path(config.STAR_GIFTS_DATA_DIR) / 'catalog' / f'{doc_id}.tgs'
    ok = await _giftchanges_download(f'/original/{gift_id}.tgs', path)
    if not ok:
        return False

    rel = path
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        pass
    thumb_path = path.with_name(f'{path.stem}_thumb.webp')
    if not thumb_path.is_file():
        from handlers.media import render_tgs_thumbnail
        render_tgs_thumbnail(path, thumb_path)
    thumb_rel = thumb_path
    try:
        thumb_rel = thumb_path.relative_to(ROOT)
    except ValueError:
        pass
    doc_row = {
        'id': doc_id,
        'access_hash': 0,
        'set_id': config.STAR_GIFTS_DOC_SET_ID,
        'file_path': str(rel),
        'mime_type': 'application/x-tgsticker',
        'size': int(path.stat().st_size if path.is_file() else 0),
        'width': 512,
        'height': 512,
        'alt': '🎁',
        'file_reference': b'\x00' * 8,
        'date': int(time.time()),
        'attributes_json': json.dumps(_build_sticker_attrs()),
        'thumb_path': str(thumb_rel) if thumb_path.is_file() else '',
    }
    storage.upsert_sticker_document(doc_row)

    row = {
        'gift_id': gift_id,
        'stars': int((existing or {}).get('stars') or 0),
        'convert_stars': int((existing or {}).get('convert_stars') or 0),
        'upgrade_stars': (existing or {}).get('upgrade_stars'),
        'limited': bool((existing or {}).get('limited') or 0),
        'sold_out': bool((existing or {}).get('sold_out') or 0),
        'birthday': bool((existing or {}).get('birthday') or 0),
        'require_premium': bool((existing or {}).get('require_premium') or 0),
        'limited_per_user': bool((existing or {}).get('limited_per_user') or 0),
        'availability_total': (existing or {}).get('availability_total'),
        'availability_remains': (existing or {}).get('availability_remains'),
        'availability_resale': 0,
        'title': title,
        'document_id': doc_id,
        'shop_hidden': 0,
        'sort_order': (existing or {}).get('sort_order'),
        'released_by_peer_type': (existing or {}).get('released_by_peer_type'),
        'released_by_peer_id': (existing or {}).get('released_by_peer_id'),
        'per_user_total': (existing or {}).get('per_user_total'),
        'per_user_remains': (existing or {}).get('per_user_remains'),
        'locked_until_date': (existing or {}).get('locked_until_date'),
        'is_auction': False,
        'auction_slug': None,
        'gifts_per_round': None,
        'auction_start_date': None,
        'first_sale_date': (existing or {}).get('first_sale_date'),
        'last_sale_date': (existing or {}).get('last_sale_date'),
        'resell_min_stars': None,
        'peer_color_available': bool((existing or {}).get('peer_color_available') or 0),
        'upgrade_variants': int((existing or {}).get('upgrade_variants') or 0),
        'auction_total_rounds': None,
        'auction_round_duration': None,
        'auction_min_bid': None,
        'auction_end_date': None,
    }
    row = apply_metadata_overrides(row, metadata_opts)
    row = apply_auction_overrides(row, auction_opts)
    storage.upsert_star_gift(row)
    storage.recompute_star_gifts_catalog_hash()
    extra = ' via api.changes.tg'
    title = row.get('title') or title
    limited_extra = ''
    if row.get('limited') and row.get('availability_total') is not None:
        limited_extra = (
            f', limited {row["availability_remains"]}/'
            f'{row["availability_total"]}')
    if row.get('stars'):
        print(f'  + gift {gift_id}: {title} ({row["stars"]} stars'
              f'{limited_extra}){extra}')
    else:
        print(f'  + gift {gift_id}: {title} (stars unknown'
              f'{limited_extra}){extra}')
    if announce:
        from gift_news_notify import announce_new_gift
        await announce_new_gift(storage, int(gift_id))
    return True


def find_gifts_by_query(gifts: List, query: str) -> List:
    """Match Telegram catalog gifts by numeric id or title (substring)."""
    query = (query or '').strip()
    if not query:
        return []
    if query.lstrip('-').isdigit():
        gid = int(query)
        return [g for g in gifts if int(g.id) == gid]
    q = query.casefold()
    exact = [g for g in gifts if _gift_title(g).casefold() == q]
    if exact:
        return exact
    partial = [g for g in gifts if q in _gift_title(g).casefold()]
    return sorted(partial, key=lambda g: _gift_title(g).casefold())


def _saved_gift_label(saved) -> str:
    gift = getattr(saved, 'gift', None)
    if gift is None:
        return 'unknown'
    title = (getattr(gift, 'title', None) or '').strip()
    if title:
        return title
    slug = (getattr(gift, 'slug', None) or '').strip()
    if slug:
        return slug
    gift_id = getattr(gift, 'gift_id', None)
    if gift_id is not None:
        return str(gift_id)
    return str(getattr(gift, 'id', None) or 'unknown')


def _saved_gift_importable(saved):
    gift = getattr(saved, 'gift', None)
    if gift is None:
        return None
    if type(gift).__name__ == 'StarGift' and getattr(gift, 'sticker', None):
        return gift
    return None


def _metadata_defaults_from_tg_gift(gift, storage: Optional[Storage] = None) -> dict:
    peer_type, peer_id = _peer_from_released(getattr(gift, 'released_by', None))
    released_by = ''
    if storage is not None and peer_id is not None:
        released_by = _released_by_label(storage, peer_type, peer_id)
    title = (getattr(gift, 'title', None) or '').strip() or _gift_title(gift)
    return {
        'title': title or None,
        'stars': int(getattr(gift, 'stars', 0) or 0) or None,
        'convert_stars': int(getattr(gift, 'convert_stars', 0) or 0) or None,
        'upgrade_stars': _optional_positive_int(getattr(gift, 'upgrade_stars', None)),
        'limited': _coerce_bool(getattr(gift, 'limited', False)),
        'availability_total': getattr(gift, 'availability_total', None),
        'availability_remains': getattr(gift, 'availability_remains', None),
        'require_premium': _coerce_bool(getattr(gift, 'require_premium', False)),
        'limited_per_user': _coerce_bool(getattr(gift, 'limited_per_user', False)),
        'per_user_total': _optional_positive_int(getattr(gift, 'per_user_total', None)),
        'per_user_remains': _optional_positive_int(
            getattr(gift, 'per_user_remains', None)),
        'birthday': _coerce_bool(getattr(gift, 'birthday', False)),
        'peer_color_available': _coerce_bool(
            getattr(gift, 'peer_color_available', False)),
        'upgrade_variants': _optional_positive_int(
            getattr(gift, 'upgrade_variants', None)),
        'released_by': released_by or None,
    }


def _metadata_defaults_from_local_gift(gift: dict, storage: Optional[Storage] = None) -> dict:
    released_by = ''
    if storage is not None and gift.get('released_by_peer_id') is not None:
        released_by = _released_by_label(
            storage,
            gift.get('released_by_peer_type'),
            gift.get('released_by_peer_id'),
        )
    return {
        'title': gift.get('title'),
        'stars': gift.get('stars'),
        'convert_stars': gift.get('convert_stars'),
        'upgrade_stars': _optional_positive_int(gift.get('upgrade_stars')),
        'limited': _coerce_bool(gift.get('limited')),
        'availability_total': gift.get('availability_total'),
        'availability_remains': gift.get('availability_remains'),
        'require_premium': _coerce_bool(gift.get('require_premium')),
        'limited_per_user': _coerce_bool(gift.get('limited_per_user')),
        'per_user_total': _optional_positive_int(gift.get('per_user_total')),
        'per_user_remains': _optional_positive_int(gift.get('per_user_remains')),
        'birthday': _coerce_bool(gift.get('birthday')),
        'peer_color_available': _coerce_bool(gift.get('peer_color_available')),
        'upgrade_variants': _optional_positive_int(gift.get('upgrade_variants')),
        'released_by': released_by or None,
        'is_tester_gift': _coerce_bool(gift.get('is_tester_gift')),
    }


def _metadata_defaults_for_import(gift, storage: Optional[Storage] = None) -> dict:
    """Telegram source merged with local DB row (local wins on re-import)."""
    defaults = _metadata_defaults_from_tg_gift(gift, storage)
    if storage is None:
        return defaults
    existing = storage.get_star_gift(int(getattr(gift, 'id', 0)))
    if not existing:
        return defaults
    local = _metadata_defaults_from_local_gift(existing, storage)
    return {**defaults, **local}


def find_saved_gifts_by_query(saved_gifts: List, query: str) -> List:
    query = (query or '').strip()
    if not query:
        return []
    if query.lstrip('-').isdigit():
        raw_id = int(query)
        return [
            saved for saved in saved_gifts
            if raw_id in {
                int(v) for v in (
                    getattr(getattr(saved, 'gift', None), 'id', 0) or 0,
                    getattr(getattr(saved, 'gift', None), 'gift_id', 0) or 0,
                    getattr(saved, 'saved_id', 0) or 0,
                    getattr(saved, 'msg_id', 0) or 0,
                ) if v
            }
        ]
    q = query.casefold()
    exact = [s for s in saved_gifts if _saved_gift_label(s).casefold() == q]
    if exact:
        return exact
    partial = [s for s in saved_gifts if q in _saved_gift_label(s).casefold()]
    return sorted(partial, key=lambda s: _saved_gift_label(s).casefold())


def find_local_gifts_by_query(storage: Storage, query: str) -> List[dict]:
    query = (query or '').strip()
    if not query:
        return []
    gifts = storage.list_star_gifts()
    if query.lstrip('-').isdigit():
        gid = int(query)
        return [g for g in gifts if int(g['gift_id']) == gid]
    q = query.casefold()
    exact = [g for g in gifts if (g.get('title') or '').casefold() == q]
    if exact:
        return exact
    return [g for g in gifts if q in (g.get('title') or '').casefold()]


def _gift_shop_status_label(g: dict) -> str:
    if int(g.get('shop_hidden') or 0):
        return ', скрыт из магазина'
    return ''


def _print_gift_choices(gifts: List, *, from_db: bool = False) -> None:
    for i, g in enumerate(gifts, 1):
        if from_db:
            title = g.get('title') or g['gift_id']
            print(
                f'  {i}. {title} (id={g["gift_id"]}, {g.get("stars")} stars'
                f'{_gift_shop_status_label(g)})'
            )
        elif hasattr(g, 'gift'):
            gift = g.gift
            label = _saved_gift_label(g)
            gift_id = getattr(gift, 'id', None)
            kind = type(gift).__name__
            extra = ''
            if kind == 'StarGiftUnique':
                base_id = getattr(gift, 'gift_id', None)
                extra = f', base_id={base_id}' if base_id is not None else ''
            print(f'  {i}. {label} ({kind}, id={gift_id}{extra})')
        else:
            print(f'  {i}. {_gift_title(g)} (id={g.id}, {g.stars} stars)')


def _pick_from_list(items: List, prompt: str = 'Номер') -> Optional[object]:
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    _print_gift_choices(items, from_db=isinstance(items[0], dict))
    while True:
        raw = input(f'{prompt} [1-{len(items)}], Enter=1: ').strip()
        if not raw:
            return items[0]
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(items):
                return items[idx - 1]
        print('Неверный номер, попробуйте снова.')


async def import_gift_objects(client: TelegramClient, storage: Storage,
                              gifts: List, *, clear_all: bool = False,
                              auction_opts: Optional[dict] = None,
                              metadata_opts: Optional[dict] = None,
                              catalog_sorting: bool = False,
                              announce: bool = False) -> int:
    if not gifts:
        return 0
    storage.ensure_star_gifts_doc_set()
    if clear_all:
        storage.clear_star_gifts_catalog()

    dedup: dict[int, object] = {}
    for gift in gifts:
        if type(gift).__name__ != 'StarGift':
            continue
        dedup[int(gift.id)] = gift

    count = 0
    for idx, gift in enumerate(dedup.values(), start=1):
        sticker = gift.sticker
        if not sticker:
            print(f'  SKIP gift {gift.id}: no sticker')
            continue
        doc = await _import_document(client, storage, sticker, 'catalog')
        row = _gift_row_from_tl(gift, doc['id'])
        # Telegram reports availability_remains = 0 for gifts sold out on its
        # own marketplace.  This server keeps its own stock, so imported
        # limited gifts start with a full shelf instead of being unsellable.
        if row.get('limited'):
            total = int(row.get('availability_total') or 0)
            if total > 0 and int(row.get('availability_remains') or 0) <= 0:
                row['availability_remains'] = total
                row['sold_out'] = False
        # Imported gifts must be visible in the shop. Admin can hide them later
        # via menu option 5; re-importing publishes them again.
        row['shop_hidden'] = 0
        if catalog_sorting:
            row['sort_order'] = idx
        row = apply_metadata_overrides(row, metadata_opts)
        row = apply_auction_overrides(row, auction_opts)
        storage.upsert_star_gift(row)
        title = row.get('title') or gift.id
        extra = ''
        if row.get('is_auction'):
            round_minutes = row.get('auction_round_duration')
            round_part = (
                f'{int(round_minutes) // 60} min/round, '
                if round_minutes else '')
            extra = (f' [auction: {row["gifts_per_round"]}/round × '
                     f'{row["auction_total_rounds"]} rounds = '
                     f'{row["availability_total"]} gifts, '
                     f'{round_part}'
                     f'min bid {row["auction_min_bid"]}]')
        limited_extra = ''
        if row.get('limited') and row.get('availability_total') is not None:
            limited_extra = (
                f', limited {row["availability_remains"]}/'
                f'{row["availability_total"]}')
        print(f'  + gift {gift.id}: {title} ({row["stars"]} stars'
              f'{limited_extra}){extra}')
        if announce:
            from gift_news_notify import announce_new_gift
            await announce_new_gift(storage, int(row['gift_id']))
        count += 1

    storage.recompute_star_gifts_catalog_hash()
    return count


async def import_catalog(client: TelegramClient, storage: Storage,
                         gift_ids: Optional[List[int]] = None,
                         *, clear_all: bool = False,
                         auction_opts: Optional[dict] = None,
                         metadata_opts: Optional[dict] = None,
                         announce: Optional[bool] = None) -> int:
    gifts = await fetch_telegram_catalog(client)
    if not gifts and not gift_ids:
        print('Каталог Telegram пуст или не изменился (hash).')
        return 0

    if not gift_ids:
        if not gifts:
            print('Подарки не найдены в каталоге Telegram.')
            return 0
        return await import_gift_objects(
            client, storage, gifts,
            clear_all=clear_all,
            auction_opts=auction_opts,
            metadata_opts=metadata_opts,
            catalog_sorting=True,
            announce=False)

    announce_gifts = announce if announce is not None else not clear_all

    ids = {int(gid) for gid in gift_ids}
    found = {int(g.id): g for g in gifts if int(g.id) in ids}
    missing = sorted(ids - set(found))
    if missing:
        saved_gifts = await fetch_saved_star_gifts(client)
        for saved in saved_gifts:
            gift = _saved_gift_importable(saved)
            if gift is None:
                continue
            gid = int(gift.id)
            if gid in ids and gid not in found:
                found[gid] = gift
        missing = sorted(ids - set(found))

    total = 0
    if found:
        total += await import_gift_objects(
            client, storage, list(found.values()),
            auction_opts=auction_opts,
            metadata_opts=metadata_opts,
            announce=announce_gifts)

    imported_api_ids = set()
    for gid in missing:
        if await import_gift_from_giftchanges(
                storage, gid, auction_opts=auction_opts,
                metadata_opts=metadata_opts,
                announce=announce_gifts):
            imported_api_ids.add(int(gid))
            total += 1

    missing = sorted(ids - set(found) - imported_api_ids)
    if missing:
        missing_text = ', '.join(str(gid) for gid in missing)
        print('  WARN: не найдено в каталоге Telegram, saved gifts или '
              f'api.changes.tg: {missing_text}', file=sys.stderr)

    if total <= 0:
        print('Подарки не найдены в каталоге Telegram, saved gifts или '
              'api.changes.tg.')
        return 0
    return total


_UPGRADE_DOWNLOAD_CONCURRENCY = 8
_PROGRESS_FLUSH_EVERY = 5
_PROGRESS_FLUSH_INTERVAL = 0.2


class _ProgressPrinter:
    """Print progress without letting terminal flush stall the import loop."""

    def __init__(self, total: int, started_at: float):
        self.total = total
        self.started_at = started_at
        self.done = 0
        self._last_flush = 0.0

    def line(self, label: str, *, force: bool = False) -> None:
        self.done += 1
        print(
            f'    [{self.done}/{self.total}] {label} '
            f'({_format_import_rate(self.done, self.started_at)})',
            flush=False,
        )
        now = time.monotonic()
        if (
            force
            or self.done >= self.total
            or self.done % _PROGRESS_FLUSH_EVERY == 0
            or (now - self._last_flush) >= _PROGRESS_FLUSH_INTERVAL
        ):
            sys.stdout.flush()
            self._last_flush = now


def _upgrade_attr_label(attr) -> Optional[str]:
    """Human-readable kind + name for progress logs."""
    name = (getattr(attr, 'name', None) or '').strip() or '?'
    if isinstance(attr, StarGiftAttributeModel):
        return f'модель: {name}'
    if isinstance(attr, StarGiftAttributePattern):
        return f'узор: {name}'
    if isinstance(attr, StarGiftAttributeBackdrop):
        return f'фон: {name}'
    return None


def _format_import_rate(done: int, started_at: float) -> str:
    elapsed = max(time.monotonic() - started_at, 1e-6)
    rate = done / elapsed
    if elapsed < 60:
        return f'{rate:.1f}/с, {elapsed:.1f}с'
    minutes = int(elapsed // 60)
    seconds = elapsed - minutes * 60
    return f'{rate:.1f}/с, {minutes}м {seconds:.0f}с'


def _upgrade_doc_row(doc, path: Path) -> dict:
    sticker_attrs = _gift_sticker_attrs(doc)
    rel = path
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        pass
    doc_row = {
        'id': doc.id,
        'access_hash': doc.access_hash,
        'set_id': config.STAR_GIFTS_DOC_SET_ID,
        'file_path': str(rel),
        'mime_type': doc.mime_type or 'application/x-tgsticker',
        'size': int(getattr(doc, 'size', 0) or 0),
        'width': 512,
        'height': 512,
        'alt': '🎁',
        'file_reference': bytes(doc.file_reference or b''),
        'date': _doc_date(doc),
        'attributes_json': json.dumps(sticker_attrs),
    }
    for da in doc.attributes or []:
        if isinstance(da, DocumentAttributeImageSize):
            doc_row['width'] = da.w
            doc_row['height'] = da.h
        elif isinstance(da, DocumentAttributeSticker):
            doc_row['alt'] = da.alt or '🎁'
    return doc_row


async def import_upgrade_attrs(client: TelegramClient, storage: Storage,
                               gift_ids: Optional[List[int]] = None,
                               *, craft_only: bool = False,
                               announce: bool = False) -> int:
    gifts = storage.list_star_gifts()
    if gift_ids:
        ids = set(gift_ids)
        gifts = [g for g in gifts if int(g['gift_id']) in ids]
    if not gifts:
        print('Нет подарков в локальной БД. Сначала импортируйте подарок (п.1).')
        return 0

    total = 0
    sem = asyncio.Semaphore(_UPGRADE_DOWNLOAD_CONCURRENCY)

    for gift in gifts:
        gid = int(gift['gift_id'])
        title = gift.get('title') or gid
        is_auction = bool(int(gift.get('is_auction') or 0))
        if not gift.get('upgrade_stars') and not is_auction:
            print(f'  SKIP {title}: нет upgrade_stars в каталоге')
            continue
        storage.set_star_gift_upgrade_importing(gid, True)
        try:
            try:
                result = await client(GetStarGiftUpgradeAttributesRequest(gift_id=gid))
            except Exception as exc:
                print(f'  WARN: upgrade attrs for {gid}: {exc}', file=sys.stderr)
                continue

            attrs = list(result.attributes or [])
            if craft_only:
                sync_rows = []
                for attr in attrs:
                    if (isinstance(attr, StarGiftAttributeModel)
                            and bool(getattr(attr, 'crafted', False))):
                        sync_rows.append({
                            'attr_type': 'model',
                            'crafted': True,
                            'document_id': int(attr.document.id),
                            'rarity_permille': _permille(attr),
                        })
                synced = storage.sync_craft_model_rarities(gid, sync_rows)
                if synced:
                    print(f'  · {title}: обновлено редкостей {synced}',
                          flush=True)

            work: list[tuple[str, object]] = []
            for attr in attrs:
                if isinstance(attr, StarGiftAttributeModel):
                    if craft_only and not bool(getattr(attr, 'crafted', False)):
                        continue
                    work.append(('model', attr))
                elif isinstance(attr, StarGiftAttributePattern):
                    if craft_only:
                        continue
                    work.append(('pattern', attr))
                elif isinstance(attr, StarGiftAttributeBackdrop):
                    if craft_only:
                        continue
                    work.append(('backdrop', attr))

            attr_total = len(work)
            print(f'  Импорт улучшений «{title}» (id={gid}): '
                  f'{attr_total} атрибутов...', flush=True)
            if not work:
                if craft_only:
                    print(f'  · {title}: крафтовых моделей нет', flush=True)
                else:
                    print(f'  · {title}: атрибутов улучшения нет', flush=True)
                continue

            started_at = time.monotonic()
            progress = _ProgressPrinter(attr_total, started_at)

            async def _download_media(kind: str, attr):
                label = _upgrade_attr_label(attr) or kind
                if kind == 'backdrop':
                    row = {
                        'gift_id': gid,
                        'attr_type': 'backdrop',
                        'name': attr.name,
                        'rarity_permille': _permille(attr),
                        'backdrop_id': int(attr.backdrop_id),
                        'center_color': int(attr.center_color),
                        'edge_color': int(attr.edge_color),
                        'pattern_color': int(attr.pattern_color),
                        'text_color': int(attr.text_color),
                    }
                    return label, None, row, 'backdrop'

                subdir = (f'upgrade/{gid}/models' if kind == 'model'
                          else f'upgrade/{gid}/patterns')
                doc = attr.document
                ext = _doc_extension(doc.mime_type)
                path = Path(config.STAR_GIFTS_DATA_DIR) / subdir / f'{doc.id}{ext}'
                async with sem:
                    ok = await _download_doc(client, doc, path)
                if not ok:
                    raise RuntimeError(f'download failed for {label}')
                doc_row = _upgrade_doc_row(doc, path)
                if kind == 'model':
                    attr_row = {
                        'gift_id': gid,
                        'attr_type': 'model',
                        'name': attr.name,
                        'rarity_permille': _permille(attr),
                        'document_id': doc.id,
                        'crafted': bool(getattr(attr, 'crafted', False)),
                    }
                else:
                    attr_row = {
                        'gift_id': gid,
                        'attr_type': 'pattern',
                        'name': attr.name,
                        'rarity_permille': _permille(attr),
                        'document_id': doc.id,
                    }
                return label, doc_row, attr_row, kind

            download_tasks = [
                asyncio.create_task(_download_media(kind, attr))
                for kind, attr in work
            ]
            doc_rows: list[dict] = []
            attr_rows: list[dict] = []
            models = patterns = backdrops = 0
            errors: list[BaseException] = []

            try:
                for fut in asyncio.as_completed(download_tasks):
                    try:
                        label, doc_row, attr_row, kind = await fut
                    except BaseException as exc:
                        errors.append(exc)
                        continue
                    if doc_row is not None:
                        doc_rows.append(doc_row)
                    attr_rows.append(attr_row)
                    if kind == 'model':
                        models += 1
                    elif kind == 'pattern':
                        patterns += 1
                    else:
                        backdrops += 1
                    progress.line(label)
            finally:
                for task in download_tasks:
                    if not task.done():
                        task.cancel()
                if any(not t.done() for t in download_tasks):
                    await asyncio.gather(*download_tasks, return_exceptions=True)

            if errors:
                for exc in errors:
                    print(f'  WARN: {exc}', file=sys.stderr)
                # Keep previous pool intact if anything failed.
                print(f'  WARN {title}: импорт прерван, старый пул сохранён',
                      flush=True)
                continue

            # One transaction: documents + attr merge/replace.
            for doc_row in doc_rows:
                storage.upsert_sticker_document(doc_row, commit=False)
            if craft_only:
                merged = storage.merge_craft_upgrade_attrs(gid, attr_rows)
                storage.repair_orphaned_saved_gift_attrs(gid)
                total += merged
                print(
                    f'  + {title}: добавлено/обновлено крафтовых моделей {merged} '
                    f'({_format_import_rate(progress.done, started_at)})',
                    flush=True,
                )
            else:
                storage.replace_star_gift_upgrade_attrs(gid, attr_rows)
                total += len(attr_rows)
                print(
                    f'  + {title}: {models} моделей, {patterns} узоров, '
                    f'{backdrops} фонов '
                    f'({_format_import_rate(progress.done, started_at)})',
                    flush=True,
                )
                if announce:
                    # News posting runs over the network (Telethon) — commit
                    # first so the DB write lock is never held across awaits.
                    storage._conn.commit()
                    from gift_news_notify import announce_gift_upgrade
                    await announce_gift_upgrade(storage, gid)
            sys.stdout.flush()
        finally:
            storage.set_star_gift_upgrade_importing(gid, False)

    storage.recompute_star_gifts_catalog_hash()
    return total


async def resolve_gift_ids_from_query(
        client: TelegramClient, storage: Storage, query: str) -> List[int]:
    """Resolve gift id(s) by name or id (catalog, local DB, saved gifts, API)."""
    catalog = await fetch_telegram_catalog(client)
    matches = find_gifts_by_query(catalog, query)
    if matches:
        pick = _pick_from_list(matches)
        return [int(pick.id)] if pick else []

    local = find_local_gifts_by_query(storage, query)
    if local:
        pick = _pick_from_list(local)
        return [int(pick['gift_id'])] if pick else []

    saved = await fetch_saved_star_gifts(client)
    saved_matches = find_saved_gifts_by_query(saved, query)
    if saved_matches:
        pick = _pick_from_list(saved_matches)
        gift = _saved_gift_importable(pick) if pick else None
        if gift is not None:
            return [int(gift.id)]
        if pick:
            kind = type(getattr(pick, 'gift', None)).__name__
            print('Подарок найден только среди saved gifts как '
                  f'{kind}; прямой импорт поддержан только для StarGift.')
        return []

    try:
        ids_map = await fetch_giftchanges_ids_map()
    except Exception as exc:
        print(f'  WARN: api.changes.tg /ids: {exc}', file=sys.stderr)
        ids_map = {}
    api_matches = find_giftchanges_gifts_by_query(ids_map, query)
    if api_matches:
        pick = _pick_from_list(api_matches)
        return [int(pick['gift_id'])] if pick else []

    print('Подарок «'
          f'{query}» не найден в каталоге, локальной БД, saved gifts или '
          'api.changes.tg.')
    return []


async def menu_import_gift(client: TelegramClient, storage: Storage) -> None:
    query = input('Название или id подарка: ').strip()
    if not query:
        print('Отмена.')
        return
    catalog = await fetch_telegram_catalog(client)
    matches = find_gifts_by_query(catalog, query)
    if matches:
        pick = _pick_from_list(matches)
        if not pick:
            return
        metadata_opts = prompt_metadata_opts(
            storage=storage, **_metadata_defaults_for_import(pick, storage))
        drop_at = prompt_moscow_drop_at()
        if drop_at is not None:
            metadata_opts = dict(metadata_opts or {})
            metadata_opts['drop_at'] = drop_at
        n = await import_catalog(
            client, storage, [int(pick.id)], metadata_opts=metadata_opts)
        print(f'Импортировано подарков: {n}')
        return

    saved = await fetch_saved_star_gifts(client)
    saved_matches = find_saved_gifts_by_query(saved, query)
    if saved_matches:
        pick = _pick_from_list(saved_matches)
        if not pick:
            return
        gift = _saved_gift_importable(pick)
        if gift is None:
            kind = type(getattr(pick, 'gift', None)).__name__
            print('Найдено только в saved gifts как '
                  f'{kind}; прямой импорт поддержан только для StarGift.')
            return
        metadata_opts = prompt_metadata_opts(
            storage=storage, **_metadata_defaults_for_import(gift, storage))
        drop_at = prompt_moscow_drop_at()
        if drop_at is not None:
            metadata_opts = dict(metadata_opts or {})
            metadata_opts['drop_at'] = drop_at
        n = await import_gift_objects(
            client, storage, [gift], metadata_opts=metadata_opts,
            announce=True)
        print(f'Импортировано подарков: {n}')
        return

    try:
        ids_map = await fetch_giftchanges_ids_map()
    except Exception as exc:
        print(f'  WARN: api.changes.tg /ids: {exc}', file=sys.stderr)
        ids_map = {}
    api_matches = find_giftchanges_gifts_by_query(ids_map, query)
    if not api_matches:
        print('Не найдено в каталоге Telegram, saved gifts или api.changes.tg: '
              f'«{query}»')
        return
    pick = _pick_from_list(api_matches)
    if not pick:
        return
    existing = storage.get_star_gift(int(pick['gift_id']))
    defaults = (_metadata_defaults_from_local_gift(existing, storage)
                if existing else {})
    defaults['title'] = defaults.get('title') or pick.get('title')
    metadata_opts = prompt_metadata_opts(storage=storage, **defaults)
    drop_at = prompt_moscow_drop_at()
    if drop_at is not None:
        metadata_opts = dict(metadata_opts or {})
        metadata_opts['drop_at'] = drop_at
    n = await import_catalog(
        client, storage, [int(pick["gift_id"])], metadata_opts=metadata_opts)
    print(f'Импортировано подарков: {n}')


async def menu_import_upgrade(client: TelegramClient, storage: Storage) -> None:
    query = input('Название или id подарка: ').strip()
    if not query:
        print('Отмена.')
        return

    local = find_local_gifts_by_query(storage, query)
    gift_ids: List[int] = []
    if local:
        pick = _pick_from_list(local)
        if pick:
            gift_ids = [int(pick['gift_id'])]
    else:
        gift_ids = await resolve_gift_ids_from_query(client, storage, query)

    if not gift_ids:
        ans = input('Подарка нет в БД. Импортировать из Telegram? [y/N]: ').strip()
        if ans.lower() in ('y', 'yes', 'д', 'да'):
            gift_ids = await resolve_gift_ids_from_query(client, storage, query)
            if not gift_ids:
                print('Не найдено в каталоге Telegram или saved gifts.')
                return
            await import_catalog(client, storage, gift_ids)
        else:
            return

    m = await import_upgrade_attrs(client, storage, gift_ids, announce=True)
    print(f'Импортировано атрибутов улучшения: {m}')


def _count_crafted_models(storage: Storage, gift_id: int) -> int:
    row = storage._conn.execute(
        '''SELECT COUNT(*) AS c FROM star_gift_upgrade_attrs
           WHERE (gift_id=? OR gift_id=0) AND attr_type='model' AND crafted=1''',
        (int(gift_id),),
    ).fetchone()
    return int(row['c'])


async def menu_import_crafts(client: TelegramClient, storage: Storage) -> None:
    query = input('Название или id подарка для импорта крафтов: ').strip()
    if not query:
        print('Отмена.')
        return

    local = find_local_gifts_by_query(storage, query)
    gift_ids: List[int] = []
    if local:
        pick = _pick_from_list(local)
        if pick:
            gift_ids = [int(pick['gift_id'])]
    else:
        gift_ids = await resolve_gift_ids_from_query(client, storage, query)

    if not gift_ids:
        ans = input('Подарка нет в БД. Импортировать из Telegram? [y/N]: ').strip()
        if ans.lower() in ('y', 'yes', 'д', 'да'):
            gift_ids = await resolve_gift_ids_from_query(client, storage, query)
            if not gift_ids:
                print('Не найдено в каталоге Telegram или saved gifts.')
                return
            await import_catalog(client, storage, gift_ids)
        else:
            return

    gid = int(gift_ids[0])
    before = _count_crafted_models(storage, gid)
    total = await import_upgrade_attrs(client, storage, gift_ids, craft_only=True)
    after = _count_crafted_models(storage, gid)
    repaired = storage.repair_orphaned_saved_gift_attrs(gid)
    title = (storage.get_star_gift(gid) or {}).get('title') or gid
    print(f'Подарок «{title}» (id={gid}): импортировано атрибутов {total}, '
          f'крафтовых моделей {after} (+{max(after - before, 0)})')
    if repaired:
        print(f'  Восстановлено атрибутов у {repaired} улучшенных подарков.')
    if after == 0:
        print('  WARN: crafted-моделей нет — возможно, у подарка нет крафта в Telegram.')


def _upgrade_attr_row_from_db(attr: dict, gift_id: int) -> dict:
    return {
        'gift_id': int(gift_id),
        'attr_type': attr['attr_type'],
        'name': attr['name'],
        'rarity_permille': int(attr.get('rarity_permille') or 100),
        'document_id': attr.get('document_id'),
        'backdrop_id': attr.get('backdrop_id'),
        'center_color': attr.get('center_color'),
        'edge_color': attr.get('edge_color'),
        'pattern_color': attr.get('pattern_color'),
        'text_color': attr.get('text_color'),
        'crafted': int(attr.get('crafted') or 0),
    }


def _pick_random_upgrade_source(storage: Storage,
                                *, exclude_gift_id: Optional[int] = None
                                ) -> Optional[int]:
    candidates: List[int] = []
    for gift in storage.list_star_gifts():
        gid = int(gift['gift_id'])
        if exclude_gift_id is not None and gid == int(exclude_gift_id):
            continue
        attrs = storage.list_star_gift_upgrade_attrs(gid)
        has_pattern = any(a['attr_type'] == 'pattern' for a in attrs)
        has_backdrop = any(a['attr_type'] == 'backdrop' for a in attrs)
        if has_pattern and has_backdrop:
            candidates.append(gid)
    if not candidates:
        return None
    return random.choice(candidates)


async def import_gift_from_custom_emoji(client: TelegramClient,
                                        storage: Storage,
                                        emoji_id: int, *,
                                        metadata_opts: Optional[dict] = None,
                                        announce: bool = True) -> bool:
    emoji_id = int(emoji_id)
    doc = await fetch_custom_emoji_document(client, emoji_id)
    if doc is None:
        print(f'  WARN: custom emoji {emoji_id} не найден в Telegram',
              file=sys.stderr)
        return False

    storage.ensure_star_gifts_doc_set()
    doc_row = await _import_document(client, storage, doc, 'catalog')
    existing = storage.get_star_gift(_synthetic_gift_id_from_emoji(emoji_id))
    gift_id = int((metadata_opts or {}).get('gift_id')
                  or (existing or {}).get('gift_id')
                  or _synthetic_gift_id_from_emoji(emoji_id))
    row = {
        'gift_id': gift_id,
        'stars': int((existing or {}).get('stars') or 0),
        'convert_stars': int((existing or {}).get('convert_stars') or 0),
        'upgrade_stars': (existing or {}).get('upgrade_stars'),
        'limited': bool((existing or {}).get('limited') or 0),
        'sold_out': bool((existing or {}).get('sold_out') or 0),
        'birthday': bool((existing or {}).get('birthday') or 0),
        'require_premium': bool((existing or {}).get('require_premium') or 0),
        'limited_per_user': bool((existing or {}).get('limited_per_user') or 0),
        'availability_total': (existing or {}).get('availability_total'),
        'availability_remains': (existing or {}).get('availability_remains'),
        'availability_resale': 0,
        'title': (existing or {}).get('title') or f'Emoji {emoji_id}',
        'document_id': int(doc_row['id']),
        'shop_hidden': 0,
        'sort_order': (existing or {}).get('sort_order'),
        'released_by_peer_type': (existing or {}).get('released_by_peer_type'),
        'released_by_peer_id': (existing or {}).get('released_by_peer_id'),
        'per_user_total': (existing or {}).get('per_user_total'),
        'per_user_remains': (existing or {}).get('per_user_remains'),
        'locked_until_date': (existing or {}).get('locked_until_date'),
        'is_auction': False,
        'auction_slug': None,
        'gifts_per_round': None,
        'auction_start_date': None,
        'first_sale_date': (existing or {}).get('first_sale_date'),
        'last_sale_date': (existing or {}).get('last_sale_date'),
        'resell_min_stars': None,
        'peer_color_available': bool(
            (existing or {}).get('peer_color_available') or 0),
        'upgrade_variants': int((existing or {}).get('upgrade_variants') or 0),
        'auction_total_rounds': None,
        'auction_round_duration': None,
        'auction_min_bid': None,
        'auction_end_date': None,
        'is_tester_gift': bool((existing or {}).get('is_tester_gift') or 0),
    }
    row = apply_metadata_overrides(row, metadata_opts)
    storage.upsert_star_gift(row)
    storage.recompute_star_gifts_catalog_hash()
    title = row.get('title') or gift_id
    tester = ' [tester]' if row.get('is_tester_gift') else ''
    print(f'  + gift {gift_id}: {title} (emoji {emoji_id}){tester}')
    if announce:
        from gift_news_notify import announce_new_gift
        await announce_new_gift(storage, int(gift_id))
    return True


async def import_models_from_custom_emoji(client: TelegramClient,
                                          storage: Storage,
                                          gift_id: int) -> int:
    gid = int(gift_id)
    gift = storage.get_star_gift(gid)
    if not gift:
        print(f'Подарок id={gid} не найден в локальной БД.')
        return 0

    source_gid = _pick_random_upgrade_source(storage, exclude_gift_id=gid)
    if source_gid is None:
        print('Нет подарка с узорами и фонами для копирования.')
        return 0
    source = storage.get_star_gift(source_gid) or {}
    source_title = source.get('title') or source_gid
    print(f'Узоры и фоны будут взяты с «{source_title}» (id={source_gid})')

    new_models: List[dict] = []
    while True:
        emoji_raw = input('Custom Emoji ID (Enter = закончить): ').strip()
        if not emoji_raw:
            break
        if not emoji_raw.lstrip('-').isdigit():
            print('Введите числовой Custom Emoji ID.')
            continue
        name = input('Название модели (пусто = стоп): ').strip()
        if not name:
            break
        rarity = _prompt_rarity_permille()

        emoji_id = int(emoji_raw)
        doc = await fetch_custom_emoji_document(client, emoji_id)
        if doc is None:
            print(f'  WARN: custom emoji {emoji_id} не найден', file=sys.stderr)
            continue

        subdir = f'upgrade/{gid}/models'
        ext = _doc_extension(doc.mime_type)
        path = Path(config.STAR_GIFTS_DATA_DIR) / subdir / f'{doc.id}{ext}'
        ok = await _download_doc(client, doc, path)
        if not ok:
            print(f'  WARN: не удалось скачать emoji {emoji_id}', file=sys.stderr)
            continue
        doc_row = _upgrade_doc_row(doc, path)
        storage.upsert_sticker_document(doc_row)
        new_models.append({
            'gift_id': gid,
            'attr_type': 'model',
            'name': name,
            'rarity_permille': rarity,
            'document_id': int(doc.id),
            'crafted': 0,
        })
        print(f'  + модель «{name}» (emoji {emoji_id}, шанс {rarity_label(rarity)})')

    if not new_models:
        print('Модели не добавлены.')
        return 0

    existing = storage.list_star_gift_upgrade_attrs(gid)
    model_rows = [
        _upgrade_attr_row_from_db(a, gid)
        for a in existing if a['attr_type'] == 'model'
    ]
    model_rows.extend(new_models)

    source_attrs = storage.list_star_gift_upgrade_attrs(source_gid)
    pattern_rows = [
        _upgrade_attr_row_from_db(a, gid)
        for a in source_attrs if a['attr_type'] == 'pattern'
    ]
    backdrop_rows = [
        _upgrade_attr_row_from_db(a, gid)
        for a in source_attrs if a['attr_type'] == 'backdrop'
    ]
    attr_rows = model_rows + pattern_rows + backdrop_rows
    storage.replace_star_gift_upgrade_attrs(gid, attr_rows)
    storage.set_star_gift_upgrade_importing(gid, False)

    if not gift.get('upgrade_stars'):
        updated = dict(gift)
        updated['upgrade_stars'] = int(gift.get('stars') or 100)
        storage.upsert_star_gift(updated)

    storage.recompute_star_gifts_catalog_hash()
    title = gift.get('title') or gid
    print(f'Подарок «{title}» (id={gid}): добавлено моделей {len(new_models)}, '
          f'узоров {len(pattern_rows)}, фонов {len(backdrop_rows)}')
    from gift_news_notify import announce_gift_upgrade
    await announce_gift_upgrade(storage, gid)
    return len(new_models)


async def menu_import_gift_from_emoji(client: TelegramClient,
                                      storage: Storage) -> None:
    emoji_raw = input('Custom Emoji ID: ').strip()
    if not emoji_raw or not emoji_raw.lstrip('-').isdigit():
        print('Отмена.')
        return
    emoji_id = int(emoji_raw)
    existing = storage.get_star_gift(_synthetic_gift_id_from_emoji(emoji_id))
    defaults = (_metadata_defaults_from_local_gift(existing, storage)
                if existing else {'title': f'Emoji {emoji_id}'})
    metadata_opts = prompt_metadata_opts(storage=storage, **defaults)
    ok = await import_gift_from_custom_emoji(
        client, storage, emoji_id, metadata_opts=metadata_opts)
    if ok:
        print('Импортировано подарков: 1')
    else:
        print('Импорт не выполнен.')


async def menu_import_models_from_emoji(client: TelegramClient,
                                        storage: Storage) -> None:
    query = input('Название или id подарка для моделей: ').strip()
    if not query:
        print('Отмена.')
        return

    local = find_local_gifts_by_query(storage, query)
    gift_ids: List[int] = []
    if local:
        pick = _pick_from_list(local)
        if pick:
            gift_ids = [int(pick['gift_id'])]
    else:
        gift_ids = await resolve_gift_ids_from_query(client, storage, query)

    if not gift_ids:
        ans = input('Подарка нет в БД. Импортировать из Telegram? [y/N]: ').strip()
        if ans.lower() in ('y', 'yes', 'д', 'да'):
            gift_ids = await resolve_gift_ids_from_query(client, storage, query)
            if not gift_ids:
                print('Не найдено в каталоге Telegram или saved gifts.')
                return
            await import_catalog(client, storage, gift_ids)
        else:
            return

    await import_models_from_custom_emoji(client, storage, int(gift_ids[0]))


async def menu_import_auction(client: TelegramClient, storage: Storage) -> None:
    query = input('Название или id подарка: ').strip()
    if not query:
        print('Отмена.')
        return

    catalog = await fetch_telegram_catalog(client)
    cat_matches = find_gifts_by_query(catalog, query)
    local_matches = find_local_gifts_by_query(storage, query)
    saved_matches: List = []

    if cat_matches:
        pick = _pick_from_list(cat_matches)
        if not pick:
            return
        metadata_opts = prompt_metadata_opts(
            storage=storage, **_metadata_defaults_for_import(pick, storage))
        default_min = (metadata_opts or {}).get('stars')
        if default_min is None:
            default_min = int(pick.stars)
        auction_opts = prompt_auction_opts(default_min_bid=default_min)
        n = await import_catalog(
            client, storage, [int(pick.id)],
            auction_opts=auction_opts, metadata_opts=metadata_opts)
        print(f'Импортировано аукционных подарков: {n}')
        return

    if local_matches:
        pick = _pick_from_list(local_matches)
        if not pick:
            return
        metadata_opts = prompt_metadata_opts(
            storage=storage, **_metadata_defaults_from_local_gift(pick, storage))
        default_min = (metadata_opts or {}).get('stars')
        if default_min is None:
            default_min = int(pick.get('stars') or 100)
        auction_opts = prompt_auction_opts(default_min_bid=default_min)
        row = apply_metadata_overrides(dict(pick), metadata_opts)
        row = apply_auction_to_local_gift(storage, row, auction_opts)
        title = row.get('title') or row['gift_id']
        print(f'  + локальный подарок {title} переведён в аукцион:')
        _print_auction_summary(row)
        return

    saved = await fetch_saved_star_gifts(client)
    saved_matches = find_saved_gifts_by_query(saved, query)
    if saved_matches:
        pick = _pick_from_list(saved_matches)
        if not pick:
            return
        gift = _saved_gift_importable(pick)
        if gift is None:
            kind = type(getattr(pick, 'gift', None)).__name__
            print('Подарок найден только среди saved gifts как '
                  f'{kind}; импорт в аукцион поддержан только для StarGift.')
            return
        metadata_opts = prompt_metadata_opts(
            storage=storage, **_metadata_defaults_for_import(gift, storage))
        default_min = (metadata_opts or {}).get('stars')
        if default_min is None:
            default_min = int(gift.stars)
        auction_opts = prompt_auction_opts(default_min_bid=default_min)
        n = await import_gift_objects(
            client, storage, [gift],
            auction_opts=auction_opts, metadata_opts=metadata_opts,
            announce=True)
        print(f'Импортировано аукционных подарков: {n}')
        return

    try:
        ids_map = await fetch_giftchanges_ids_map()
    except Exception as exc:
        print(f'  WARN: api.changes.tg /ids: {exc}', file=sys.stderr)
        ids_map = {}
    api_matches = find_giftchanges_gifts_by_query(ids_map, query)
    if api_matches:
        pick = _pick_from_list(api_matches)
        if not pick:
            return
        existing = storage.get_star_gift(int(pick['gift_id']))
        defaults = (_metadata_defaults_from_local_gift(existing, storage)
                    if existing else {})
        defaults['title'] = defaults.get('title') or pick.get('title')
        metadata_opts = prompt_metadata_opts(storage=storage, **defaults)
        default_min = (metadata_opts or {}).get('stars')
        if default_min is None:
            default_min = int((existing or {}).get('stars') or 100)
        auction_opts = prompt_auction_opts(default_min_bid=default_min)
        n = await import_catalog(
            client, storage, [int(pick['gift_id'])],
            auction_opts=auction_opts, metadata_opts=metadata_opts)
        print(f'Импортировано аукционных подарков: {n}')
        return

    print('Не найдено: '
          f'«{query}». Подарок недоступен в каталоге, локальной БД или '
          'saved gifts текущего аккаунта, а также api.changes.tg.')


async def menu_delete_gift(storage: Storage) -> None:
    query = input('Название или id подарка для удаления: ').strip()
    if not query:
        print('Отмена.')
        return
    matches = find_local_gifts_by_query(storage, query)
    if not matches:
        print(f'В локальной БД не найдено: «{query}»')
        return
    pick = _pick_from_list(matches)
    if not pick:
        return
    gid = int(pick['gift_id'])
    title = pick.get('title') or gid
    print()
    print('Режим удаления:')
    print('  1. Удалить полностью (профили, улучшения, аукцион, каталог)')
    print('  2. Убрать только из магазина (в профилях и KoalGram останется)')
    mode = input('Выбор [1/2], Enter=1: ').strip() or '1'
    if mode == '2':
        ans = input(
            f'Скрыть «{title}» (id={gid}) из магазина? [y/N]: '
        ).strip()
        if ans.lower() not in ('y', 'yes', 'д', 'да'):
            print('Отмена.')
            return
        if storage.hide_star_gift_from_shop(gid):
            print(f'Подарок «{title}» (id={gid}) убран из магазина.')
        else:
            print('Не удалось скрыть подарок.')
        return
    if mode != '1':
        print('Неизвестный режим.')
        return
    ans = input(
        f'Полностью удалить «{title}» (id={gid}) из БД? [y/N]: '
    ).strip()
    if ans.lower() not in ('y', 'yes', 'д', 'да'):
        print('Отмена.')
        return
    storage.delete_star_gift(gid)
    print(f'Удалён подарок {title} (id={gid}).')


async def menu_restore_gift(storage: Storage) -> None:
    raw = input('ID подарка для возврата в магазин: ').strip()
    if not raw or not raw.lstrip('-').isdigit():
        print('Отмена.')
        return
    gid = int(raw)
    gift = storage.get_star_gift(gid)
    if not gift:
        print(f'Подарок id={gid} не найден в БД.')
        return
    title = gift.get('title') or gid
    if not int(gift.get('shop_hidden') or 0):
        print(f'«{title}» (id={gid}) уже доступен в магазине.')
        return
    ans = input(f'Вернуть «{title}» (id={gid}) в магазин? [y/N]: ').strip()
    if ans.lower() not in ('y', 'yes', 'д', 'да'):
        print('Отмена.')
        return
    if storage.restore_star_gift_to_shop(gid):
        print(f'Подарок «{title}» (id={gid}) снова в магазине.')
    else:
        print('Не удалось вернуть подарок в магазин.')


async def menu_import_auction_upgrades(client: TelegramClient,
                                       storage: Storage) -> None:
    query = input('Название или id аукционного подарка: ').strip()
    if not query:
        print('Отмена.')
        return
    matches = find_local_gifts_by_query(storage, query)
    auction = [g for g in matches if int(g.get('is_auction') or 0)]
    if not auction and matches:
        print('Найденный подарок не является аукционным. Используйте п.2.')
        return
    if not auction:
        print(f'Аукционный подарок не найден в БД: «{query}»')
        return
    pick = _pick_from_list(auction)
    if not pick:
        return
    gift_ids = [int(pick['gift_id'])]
    m = await import_upgrade_attrs(client, storage, gift_ids, announce=True)
    print(f'Импортировано атрибутов улучшения: {m}')


async def run_menu(client: TelegramClient, storage: Storage) -> None:
    while True:
        print()
        print('=== Импорт Star Gifts ===')
        print('  1. Импортировать подарок (по названию или id, затем выбрать дроп)')
        print('  2. Импортировать улучшения для подарка (модели / узоры / фоны)')
        print('  3. Импортировать весь каталог Telegram')
        print('  4. Импортировать подарок как аукцион')
        print('  5. Удалить подарок из локальной БД')
        print('  6. Импортировать улучшения для аукционного подарка')
        print('  7. Вернуть подарок в магазин по id')
        print('  8. Импортировать крафтовые модели для подарка')
        print('  9. Импортировать подарок по Custom Emoji ID')
        print('  10. Импортировать модели по Custom Emoji ID')
        print('  0. Выход')
        choice = input('Выбор: ').strip()
        if choice in ('0', 'q', 'quit', 'exit'):
            break
        if choice == '1':
            await menu_import_gift(client, storage)
        elif choice == '2':
            await menu_import_upgrade(client, storage)
        elif choice == '3':
            n = await import_catalog(client, storage, clear_all=True)
            print(f'Каталог: {n} подарков')
            ans = input('Импортировать улучшения для всех? [y/N]: ').strip()
            if ans.lower() in ('y', 'yes', 'д', 'да'):
                m = await import_upgrade_attrs(client, storage)
                print(f'Атрибутов улучшения: {m}')
        elif choice == '4':
            await menu_import_auction(client, storage)
        elif choice == '5':
            await menu_delete_gift(storage)
        elif choice == '6':
            await menu_import_auction_upgrades(client, storage)
        elif choice == '7':
            await menu_restore_gift(storage)
        elif choice == '8':
            await menu_import_crafts(client, storage)
        elif choice == '9':
            await menu_import_gift_from_emoji(client, storage)
        elif choice == '10':
            await menu_import_models_from_emoji(client, storage)
        else:
            print('Неизвестный пункт.')


async def main() -> None:
    parser = argparse.ArgumentParser(
        description='Import star gifts from Telegram',
        epilog=(f'{GIFTCHANGES_CREDIT_LINE}. Hidden-gift fallback uses this API.'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--session', required=True,
                        help='Telethon session name (without .session)')
    parser.add_argument('--name', '--gift-name', dest='name', default='',
                        help='Gift title (substring) or numeric id')
    parser.add_argument('--gift-id', action='append', type=int, default=[],
                        help='Import specific gift id (repeatable)')
    parser.add_argument('--catalog-only', action='store_true',
                        help='Import catalog only (CLI batch mode)')
    parser.add_argument('--upgrade-only', action='store_true',
                        help='Import upgrade attributes only')
    parser.add_argument('--craft-only', action='store_true',
                        help='Import crafted models only (merge, no pool replace)')
    parser.add_argument('--all', action='store_true',
                        help='Import full Telegram catalog')
    parser.add_argument('--menu', action='store_true',
                        help='Interactive menu (default if no batch flags)')
    parser.add_argument('--title', default='',
                        help='Override imported gift title')
    parser.add_argument('--stars', type=int, default=None,
                        help='Override gift stars price')
    parser.add_argument('--convert-stars', type=int, default=None,
                        help='Override gift convert_stars')
    parser.add_argument('--upgrade-stars', type=int, default=None,
                        help='Override gift upgrade_stars')
    parser.add_argument('--limited', default=None,
                        help='Override limited flag (true/false)')
    parser.add_argument('--gift-count', '--count', type=int, default=None,
                        dest='gift_count',
                        help='Limited edition size (sets limited + availability)')
    parser.add_argument('--availability-total', type=int, default=None,
                        help='Override availability_total')
    parser.add_argument('--availability-remains', type=int, default=None,
                        help='Override availability_remains')
    parser.add_argument('--require-premium', default=None,
                        help='Override require_premium flag (true/false)')
    parser.add_argument('--limited-per-user', default=None,
                        help='Override limited_per_user flag (true/false)')
    parser.add_argument('--per-user-total', type=int, default=None,
                        help='Override per_user_total')
    parser.add_argument('--per-user-remains', type=int, default=None,
                        help='Override per_user_remains')
    parser.add_argument('--birthday', default=None,
                        help='Override birthday flag (true/false)')
    parser.add_argument('--peer-color-available', default=None,
                        help='Override peer_color_available flag (true/false)')
    parser.add_argument('--upgrade-variants', type=int, default=None,
                        help='Override upgrade_variants')
    parser.add_argument('--drop-at', default='',
                        help=('Delay shop release until YYYY-MM-DD HH:MM '
                              '(Europe/Moscow; also accepts DD.MM.YYYY HH:MM)'))
    parser.add_argument('--released-by', default='',
                        help='Released By username or user id (empty = none)')
    parser.add_argument('--auction', default=None,
                        help='Mark gift as auction (true/false)')
    parser.add_argument('--num-of-rounds', type=int, default=None,
                        help='Number of auction rounds')
    parser.add_argument('--round-time', type=int, default=None,
                        help='Round duration in minutes')
    parser.add_argument('--win-per-round', type=int, default=None,
                        help='Winners (gifts) per round, e.g. top 3')
    parser.add_argument('--min-bid', type=int, default=None,
                        help='Minimum bid in stars')
    parser.add_argument('--is-tester-gift', default=None,
                        help='Mark gift as tester-only (true/false)')
    parser.add_argument('--delete', action='store_true',
                        help='Delete gift from local DB (--name or --gift-id)')
    parser.add_argument('--list-json', action='store_true',
                        help='Output Telegram gifts list as JSON and exit')
    args = parser.parse_args()

    if args.drop_at.strip() and not (args.name or args.gift_id):
        parser.error('--drop-at requires --name or --gift-id for one gift')

    metadata_opts = {}
    bool_override_flags = {
        'limited': args.limited,
        'require_premium': args.require_premium,
        'limited_per_user': args.limited_per_user,
        'birthday': args.birthday,
        'peer_color_available': args.peer_color_available,
        'is_tester_gift': args.is_tester_gift,
    }
    for key, raw in bool_override_flags.items():
        if raw is None:
            continue
        try:
            metadata_opts[key] = _parse_bool_flag(raw)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            sys.exit(1)

    scalar_overrides = {
        'title': args.title.strip() or None,
        'stars': args.stars,
        'convert_stars': args.convert_stars,
        'upgrade_stars': args.upgrade_stars,
        'gift_count': args.gift_count,
        'availability_total': args.availability_total,
        'availability_remains': args.availability_remains,
        'per_user_total': args.per_user_total,
        'per_user_remains': args.per_user_remains,
        'upgrade_variants': args.upgrade_variants,
    }
    if args.drop_at.strip():
        try:
            scalar_overrides['drop_at'] = parse_moscow_drop_time(args.drop_at)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            sys.exit(1)
    for key, value in scalar_overrides.items():
        if value is not None:
            metadata_opts[key] = value
    if not metadata_opts:
        metadata_opts = None

    auction_opts = None
    if args.auction is not None:
        try:
            auction_flag = _parse_bool_flag(args.auction)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            sys.exit(1)
        auction_opts = {
            'auction': auction_flag,
            'num_of_rounds': args.num_of_rounds,
            'win_per_round': args.win_per_round,
            'round_time': args.round_time,
            'min_bid': args.min_bid,
        }
        if auction_flag:
            missing = [name for name, val in (
                ('--num-of-rounds', args.num_of_rounds),
                ('--round-time', args.round_time),
                ('--win-per-round', args.win_per_round),
                ('--min-bid', args.min_bid),
            ) if val is None]
            if missing:
                print('Auction import requires: ' + ', '.join(missing),
                      file=sys.stderr)
                sys.exit(1)

    api_id, api_hash = telethon_credentials()

    storage = Storage.open_tool()
    session_path, cleanup_session = prepare_telethon_session(args.session)

    released_raw = (args.released_by or '').strip()
    if released_raw:
        metadata_opts = metadata_opts or {}
        if released_raw == '-':
            metadata_opts['released_by_peer_type'] = None
            metadata_opts['released_by_peer_id'] = None
        else:
            peer_type, peer_id = _resolve_released_by(storage, released_raw)
            if peer_id is not None:
                metadata_opts['released_by_peer_type'] = peer_type
                metadata_opts['released_by_peer_id'] = peer_id

    batch_mode = bool(
        args.catalog_only or args.upgrade_only or args.craft_only or args.all
        or args.gift_id or args.name or args.delete or args.list_json)

    try:
        async with TelegramClient(
                session_path, api_id, api_hash,
                connection_retries=10, retry_delay=3, request_retries=5) as client:
            if args.list_json:
                gifts = await fetch_telegram_catalog(client)
                payload = [
                    {
                        'gift_id': str(int(g.id)),
                        'title': _gift_title(g),
                        'stars': int(getattr(g, 'stars', 0) or 0),
                        'convert_stars': int(getattr(g, 'convert_stars', 0) or 0),
                        'upgrade_stars': int(getattr(g, 'upgrade_stars', 0) or 0),
                        'limited': bool(getattr(g, 'limited', False)),
                        'availability_total': int(getattr(g, 'availability_total', 0) or 0),
                        'availability_remains': int(getattr(g, 'availability_remains', 0) or 0),
                        'require_premium': bool(getattr(g, 'require_premium', False)),
                        'birthday': bool(getattr(g, 'birthday', False)),
                        'peer_color_available': bool(
                            getattr(g, 'peer_color_available', False)),
                    }
                    for g in gifts
                    if type(g).__name__ == 'StarGift'
                ]
                print(json.dumps(payload, ensure_ascii=False))
                return
            if args.menu or not batch_mode:
                await run_menu(client, storage)
            else:
                if args.delete:
                    gift_ids: Optional[List[int]] = list(args.gift_id) or None
                    if args.name and not gift_ids:
                        local = find_local_gifts_by_query(storage, args.name)
                        if not local:
                            print(f'Not found: {args.name!r}', file=sys.stderr)
                            sys.exit(1)
                        pick = local[0] if len(local) == 1 else _pick_from_list(local)
                        gift_ids = [int(pick['gift_id'])] if pick else []
                    if not gift_ids:
                        print('Specify --name or --gift-id', file=sys.stderr)
                        sys.exit(1)
                    for gid in gift_ids:
                        storage.delete_star_gift(int(gid))
                        print(f'Deleted gift id={gid}')
                    print('Done.')
                    return

                gift_ids: Optional[List[int]] = list(args.gift_id) or None
                if args.name and not gift_ids:
                    gift_ids = await resolve_gift_ids_from_query(
                        client, storage, args.name)
                    if not gift_ids:
                        sys.exit(1)

                if args.all and not gift_ids:
                    gift_ids = None

                if not args.upgrade_only and not args.craft_only:
                    clear = args.all and not gift_ids and not args.name
                    print('Importing star gifts catalog...')
                    n = await import_catalog(
                        client, storage, gift_ids, clear_all=clear,
                        auction_opts=auction_opts,
                        metadata_opts=metadata_opts)
                    print(f'Catalog: {n} gifts')
                if not args.catalog_only:
                    if gift_ids:
                        missing_local_ids = [
                            int(gid) for gid in gift_ids
                            if not storage.get_star_gift(int(gid))
                        ]
                        if missing_local_ids:
                            print('Importing missing gifts before upgrade import...')
                            await import_catalog(
                                client, storage, missing_local_ids,
                                metadata_opts=metadata_opts)
                    label = 'crafted models' if args.craft_only else 'upgrade attributes'
                    print(f'Importing {label}...')
                    m = await import_upgrade_attrs(
                        client, storage, gift_ids,
                        craft_only=args.craft_only,
                        announce=bool(gift_ids))
                    if args.craft_only and gift_ids:
                        for gid in gift_ids:
                            fixed = storage.repair_orphaned_saved_gift_attrs(int(gid))
                            if fixed:
                                print(f'Repaired attrs for {fixed} upgraded gifts '
                                      f'(gift_id={gid})')
                    print(f'Upgrade pool: {m} attribute entries')

        print('Готово. Подарки уже видны в магазине — перезапуск сервера не нужен.')
    finally:
        if cleanup_session:
            cleanup_session()


if __name__ == '__main__':
    asyncio.run(main())
