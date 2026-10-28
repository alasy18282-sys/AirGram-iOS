"""Virtual KoalGram NFTs: collectible usernames and anonymous +888 numbers.

They behave like chain NFTs in the wallet API (address, metadata, artwork,
history) but are derived from the main messenger database, so a username
appears in the wallet immediately after it is granted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Optional

import config

from airgram_chain import core
from airgram_chain import media

log = logging.getLogger(__name__)

USERNAME = 'username'
PHONE = 'phone'

USERNAME_COLLECTION = 'KoalGram Username'
PHONE_COLLECTION = 'KoalGram Number'


def virtual_address(kind: str, key: str) -> str:
    digest = hashlib.sha256(
        f'koalgram-virtual-nft|{kind}|{(key or "").strip().lower()}'.encode('utf-8')
    ).hexdigest()
    return f'0:{digest}'


def ensure_table(storage) -> None:
    storage._conn.execute('''
        CREATE TABLE IF NOT EXISTS koalgram_virtual_nfts (
            address    TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            kind       TEXT NOT NULL,
            item_key   TEXT NOT NULL,
            title      TEXT NOT NULL,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL DEFAULT 0,
            UNIQUE (kind, item_key)
        )
    ''')
    columns = {row[1] for row in storage._conn.execute(
        'PRAGMA table_info(koalgram_virtual_nfts)')}
    if 'updated_at' not in columns:
        storage._conn.execute(
            'ALTER TABLE koalgram_virtual_nfts '
            'ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0')
        storage._conn.execute(
            'UPDATE koalgram_virtual_nfts SET updated_at=created_at '
            'WHERE updated_at=0')
    storage._conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_koalgram_virtual_user '
        'ON koalgram_virtual_nfts(user_id)')
    storage._conn.commit()


def _insert(storage, kind: str, key: str, user_id: int, title: str,
            active: int, created_at: int) -> None:
    key = (key or '').strip().lower()
    if not key or not title:
        return
    address = virtual_address(kind, key)
    now = int(time.time())
    storage._conn.execute(
        '''INSERT INTO koalgram_virtual_nfts
           (address, user_id, kind, item_key, title, active, created_at,
            updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(kind, item_key) DO UPDATE SET
               user_id=excluded.user_id, title=excluded.title,
               updated_at=CASE
                   WHEN koalgram_virtual_nfts.active<>excluded.active
                   THEN excluded.updated_at
                   ELSE koalgram_virtual_nfts.updated_at END,
               active=excluded.active''',
        (address, int(user_id), kind, key, title,
         1 if active else 0, int(created_at or now), now))


def sync_registry(storage) -> None:
    """Mirror usernames and anonymous numbers into the virtual NFT registry."""
    from db.storage import format_phone_display
    from handlers.phone_countries import is_collectible_phone

    ensure_table(storage)

    for row in storage._conn.execute(
            'SELECT username, user_id, active, granted_at, created_at '
            'FROM collectible_usernames').fetchall():
        username = str(row['username'] or '')
        if not username:
            continue
        ts = int(row['granted_at'] or row['created_at'] or time.time())
        _insert(storage, USERNAME, username, int(row['user_id']),
                f'@{username}', int(row['active'] or 0), ts)

    for row in storage._conn.execute(
            'SELECT id, username, created_at FROM users '
            "WHERE username IS NOT NULL AND username != ''").fetchall():
        username = str(row['username'] or '')
        if not username:
            continue
        _insert(storage, USERNAME, username, int(row['id']),
                f'@{username}', 1, int(row['created_at'] or time.time()))

    for row in storage._conn.execute(
            'SELECT phone, user_id, created_at FROM anonymous_numbers').fetchall():
        phone = str(row['phone'] or '')
        if not phone or not is_collectible_phone(phone):
            continue
        _insert(storage, PHONE, phone, int(row['user_id']),
                format_phone_display(phone), 1,
                int(row['created_at'] or time.time()))

    for row in storage._conn.execute(
            "SELECT id, phone, created_at FROM users WHERE phone != ''").fetchall():
        phone = str(row['phone'] or '')
        if not phone or not is_collectible_phone(phone):
            continue
        _insert(storage, PHONE, phone, int(row['id']),
                format_phone_display(phone), 1,
                int(row['created_at'] or time.time()))

    # Deactivate entries whose source was revoked (username removed,
    # number released) so the wallet drops them and history shows a debit.
    now = int(time.time())
    for kind in (USERNAME, PHONE):
        if kind == USERNAME:
            live = {
                str(row['username'] or '').strip().lower()
                for row in storage._conn.execute(
                    'SELECT username FROM collectible_usernames '
                    'WHERE active=1').fetchall()
            } | {
                str(row['username'] or '').strip().lower()
                for row in storage._conn.execute(
                    "SELECT username FROM users "
                    "WHERE username IS NOT NULL AND username != ''").fetchall()
            }
        else:
            live = {
                str(row['phone'] or '').strip()
                for row in storage._conn.execute(
                    'SELECT phone FROM anonymous_numbers').fetchall()
            } | {
                str(row['phone'] or '').strip()
                for row in storage._conn.execute(
                    "SELECT phone FROM users WHERE phone != ''").fetchall()
            }
        for row in storage._conn.execute(
                'SELECT item_key FROM koalgram_virtual_nfts '
                'WHERE kind=? AND active=1', (kind,)).fetchall():
            if str(row['item_key']).strip().lower() not in live:
                storage._conn.execute(
                    'UPDATE koalgram_virtual_nfts '
                    'SET active=0, updated_at=? '
                    'WHERE kind=? AND item_key=?',
                    (now, kind, row['item_key']))

    storage._conn.commit()


def _registry_row(storage, address: str) -> Optional[dict]:
    ensure_table(storage)
    row = storage._conn.execute(
        'SELECT * FROM koalgram_virtual_nfts WHERE address=?',
        ((address or '').strip().lower(),)).fetchone()
    return dict(row) if row else None


def _row_for(storage, registry: dict, owner_raw: str) -> dict:
    kind = registry['kind']
    title = registry['title']
    collection = USERNAME_COLLECTION if kind == USERNAME else PHONE_COLLECTION
    subtitle = 'Username NFT' if kind == USERNAME else 'Anonymous number'
    media_names = media.ensure_virtual_media(
        registry['address'], title=title, subtitle=subtitle,
        kind=kind)
    title_label = 'Username' if kind == USERNAME else 'Phone number'
    attributes = [
        {'trait_type': 'Type', 'value': title_label},
        {'trait_type': 'Collection', 'value': collection},
    ]
    if kind == PHONE:
        attributes.append({'trait_type': 'Anonymous', 'value': 'Yes'})
    if not int(registry.get('active') or 0):
        attributes.append({'trait_type': 'Status', 'value': 'Inactive'})
    metadata = {
        'name': title,
        'description': (
            f'{title_label} {title} в KoalGram. '
            'Цифровой предмет кошелька KoalGram.'),
        'external_url': f'{config.AIRGRAM_WALLET_URL}?nft={registry["address"]}',
        'attributes': attributes,
        'collection': collection,
        'blockchain': 'koalgram',
        'chain_id': core.CHAIN_ID,
        'virtual_kind': kind,
    }
    row = {
        'address': registry['address'],
        'item_index': -1,
        'owner_address': owner_raw,
        'title': title,
        'collection': collection,
        'model_name': '',
        'pattern_name': '',
        'backdrop_name': '',
        'attributes_json': json.dumps(attributes, ensure_ascii=False),
        'metadata_json': json.dumps(metadata, ensure_ascii=False),
        'image_path': media_names['image_path'],
        'thumb_path': media_names['thumb_path'],
        'created_at': int(registry.get('created_at') or 0),
        'virtual': 1,
        'virtual_kind': kind,
        'active': int(registry.get('active') or 0),
    }
    return row


def _event_for(registry: dict, owner_raw: str, *,
               kind: str = 'nft_mint', ts: int = 0) -> dict:
    collection = (USERNAME_COLLECTION if registry['kind'] == USERNAME
                  else PHONE_COLLECTION)
    ts = int(ts or registry.get('created_at') or 0)
    tx_hash = 'koalgram-' + hashlib.sha256(
        f'{registry["address"]}|{kind}|{ts}'.encode('utf-8')).hexdigest()[:32]
    event_id = int(
        hashlib.sha256(f'{registry["address"]}|{kind}'.encode('utf-8')
                       ).hexdigest()[:12], 16)
    return {
        'id': event_id,
        'height': 0,
        'ts': ts,
        'kind': kind,
        'address': owner_raw,
        'nft_address': registry['address'],
        'tx_hash': tx_hash,
        'data': {
            'title': registry['title'],
            'collection': collection,
            'virtual': True,
            'virtual_kind': registry['kind'],
        },
    }


def _events_for_registry(registry: dict, owner_raw: str) -> list[dict]:
    events = [_event_for(registry, owner_raw)]
    updated_at = int(registry.get('updated_at') or 0)
    created_at = int(registry.get('created_at') or 0)
    if not int(registry.get('active') or 0) and updated_at > created_at:
        events.append(_event_for(registry, owner_raw, kind='nft_burn',
                                 ts=updated_at))
    return events


def list_for_user(storage, user_id: int, owner_raw: str) -> list[dict]:
    """Return virtual NFTs owned by the account, already in public shape."""
    if not user_id or not owner_raw:
        return []
    try:
        sync_registry(storage)
        rows = storage._conn.execute(
            '''SELECT * FROM koalgram_virtual_nfts
               WHERE user_id=? AND active=1
               ORDER BY kind DESC, lower(title)''',
            (int(user_id),)).fetchall()
        items = []
        for row in rows:
            public = core.nft_public(_row_for(storage, dict(row), owner_raw))
            public['virtual'] = True
            public['virtualKind'] = row['kind']
            public['active'] = int(row['active'] or 0)
            items.append(public)
        return items
    except Exception:
        log.exception('virtual NFT listing failed for user %s', user_id)
        return []


def events_for_user(storage, user_id: int, owner_raw: str) -> list[dict]:
    if not user_id or not owner_raw:
        return []
    try:
        sync_registry(storage)
        rows = storage._conn.execute(
            'SELECT * FROM koalgram_virtual_nfts WHERE user_id=?',
            (int(user_id),)).fetchall()
        events: list[dict] = []
        for row in rows:
            events.extend(_events_for_registry(dict(row), owner_raw))
        return events
    except Exception:
        log.exception('virtual NFT events failed for user %s', user_id)
        return []


def detail(storage, address: str) -> Optional[dict]:
    registry = _registry_row(storage, address)
    if not registry:
        return None
    owner_raw = ''
    from airgram_chain import service
    wallet = service.get_linked_wallet(storage, int(registry['user_id']))
    if wallet:
        try:
            owner_raw = core.parse_address(wallet['address'])
        except core.ChainError:
            owner_raw = ''
    public = core.nft_public(_row_for(storage, registry, owner_raw))
    public['virtual'] = True
    public['virtualKind'] = registry['kind']
    public['active'] = int(registry.get('active') or 0)
    events = _events_for_registry(registry, owner_raw)
    mint_event = _event_for(registry, owner_raw)
    return {
        'nft': public,
        'transactions': [{
            'tx_id': mint_event['tx_hash'],
            'tx_hash': mint_event['tx_hash'],
            'nft_address': registry['address'],
            'kind': 'nft_mint',
            'from_address': '',
            'to_address': owner_raw,
            'height': mint_event['height'],
            'lt': mint_event['ts'],
            'ts': mint_event['ts'],
        }],
        'events': events,
        'mintBlock': None,
        'owner': {
            'address': owner_raw,
            'friendlyAddress': core.friendly_address(owner_raw),
        },
    }


def metadata(storage, address: str) -> Optional[dict]:
    registry = _registry_row(storage, address)
    if not registry:
        return None
    row = _row_for(storage, registry, '')
    data = json.loads(row['metadata_json'])
    image, _thumb = core._media_urls(row['image_path'], '')
    data['image'] = image
    data['name'] = row['title']
    data['address'] = row['address']
    data['owner'] = ''
    return data


def ensure_media_for_address(storage, address: str) -> bool:
    registry = _registry_row(storage, address)
    if not registry:
        return False
    _row_for(storage, registry, '')
    return True
