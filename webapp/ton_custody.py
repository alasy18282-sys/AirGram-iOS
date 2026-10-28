"""Custodial KoalGram TON wallet and NFT deposit attribution.

The signing mnemonic is deliberately kept out of the WebApp and API responses.
NFT deposits are attributed only after TonAPI confirms both current ownership
and the on-chain comment emitted by the matching, short-lived deposit intent.
"""
from __future__ import annotations

import json
import base64
import binascii
import logging
import os
import re
import secrets
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger('ton_custody')

KEY_FILE = Path(os.environ.get(
    'AIRGRAM_TON_CUSTODY_WALLET_FILE',
    Path(__file__).resolve().parents[1] / 'keys' / 'ton_custody_wallet.json'))
TONAPI_BASE = os.environ.get('AIRGRAM_TONAPI_URL', 'https://tonapi.io/v2').rstrip('/')
TONAPI_KEY = os.environ.get('AIRGRAM_TONAPI_KEY', '')
INTENT_TTL = 30 * 60
RAW_ADDRESS = re.compile(r'-?\d+:[0-9a-fA-F]{64}')


def custody_wallet() -> dict:
    """Read public wallet metadata and enforce private file permissions."""
    if not KEY_FILE.is_file():
        raise RuntimeError(f'Custody wallet is not provisioned: {KEY_FILE}')
    mode = stat.S_IMODE(KEY_FILE.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError('Custody wallet key file must be mode 0600')
    data = json.loads(KEY_FILE.read_text(encoding='utf-8'))
    address = str(data.get('address') or '')
    if not address:
        raise RuntimeError('Custody wallet has no address')
    return {'address': address, 'network': data.get('network', 'mainnet')}


def _ensure_tables(storage) -> None:
    storage._conn.executescript('''
        CREATE TABLE IF NOT EXISTS airgram_blockchain_deposit_intents (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            nft_address TEXT NOT NULL,
            sender_address TEXT NOT NULL,
            memo TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            confirmed_at INTEGER,
            tx_hash TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_airgram_chain_intents_user
            ON airgram_blockchain_deposit_intents(user_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS airgram_blockchain_gifts (
            nft_address TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            collection_address TEXT,
            title TEXT NOT NULL DEFAULT '',
            image_url TEXT,
            telegram_slug TEXT,
            assigned_at INTEGER NOT NULL,
            intent_token TEXT NOT NULL UNIQUE,
            FOREIGN KEY(intent_token)
                REFERENCES airgram_blockchain_deposit_intents(token)
        );
        CREATE INDEX IF NOT EXISTS idx_airgram_chain_gifts_user
            ON airgram_blockchain_gifts(user_id, assigned_at DESC);
    ''')
    storage._conn.commit()


def _tonapi(path: str) -> dict:
    headers = {'Accept': 'application/json', 'User-Agent': 'KoalGram/1.0'}
    if TONAPI_KEY:
        headers['Authorization'] = f'Bearer {TONAPI_KEY}'
    request = urllib.request.Request(TONAPI_BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode('utf-8', 'replace')
        raise RuntimeError(f'TON indexer returned HTTP {exc.code}: {detail}') from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError('TON indexer is temporarily unavailable') from exc


def _tonapi_address(address: str) -> str:
    return urllib.parse.quote(address, safe=':-_')


def _nft_info(nft_address: str) -> dict:
    return _tonapi('/nfts/' + _tonapi_address(nft_address))


def _address_of(obj) -> str:
    if isinstance(obj, dict):
        obj = obj.get('address') or ''
    return _canonical_address(str(obj or ''))


def _canonical_address(value: str) -> str:
    """Normalize raw and TON friendly addresses to workchain:hash."""
    value = value.strip()
    if RAW_ADDRESS.fullmatch(value):
        workchain, digest = value.split(':', 1)
        return f'{int(workchain)}:{digest.lower()}'
    try:
        encoded = value.replace('-', '+').replace('_', '/')
        payload = base64.b64decode(encoded + '=' * (-len(encoded) % 4), validate=True)
        if len(payload) != 36 or binascii.crc_hqx(payload[:34], 0) != int.from_bytes(payload[34:], 'big'):
            return value
        workchain = int.from_bytes(payload[1:2], 'big', signed=True)
        return f'{workchain}:{payload[2:34].hex()}'
    except (ValueError, binascii.Error):
        return value


def _verified_deposit(intent: dict, server_address: str) -> dict | None:
    """Verify NFT owner and transfer memo directly against the NFT history."""
    nft_address = str(intent['nft_address'])
    info = _nft_info(nft_address)
    if _address_of(info.get('owner')) != _canonical_address(server_address):
        return None

    history = _tonapi('/nfts/' + _tonapi_address(nft_address)
                      + '/history?limit=100')
    for operation in history.get('operations') or []:
        if operation.get('operation') != 'transfer':
            continue
        if _address_of(operation.get('recipient')) != _canonical_address(server_address):
            continue
        if str(operation.get('comment') or '') != intent['memo']:
            continue
        metadata = info.get('metadata') or {}
        title = str(metadata.get('name') or info.get('name') or 'TON Gift')[:200]
        image = metadata.get('image') or metadata.get('image_url')
        collection = info.get('collection') or {}
        return {
            'nft_address': nft_address,
            'collection_address': str(collection.get('address') or '') or None,
            'title': title,
            'image_url': str(image)[:2048] if image else None,
            'tx_hash': str(operation.get('transaction_hash') or '') or None,
        }
    return None


def _complete_intent(storage, intent: dict, server_address: str) -> bool:
    try:
        asset = _verified_deposit(intent, server_address)
    except RuntimeError as exc:
        log.info('NFT deposit verification deferred for %s: %s',
                 intent['nft_address'], exc)
        return False
    if not asset:
        return False
    now = int(time.time())
    try:
        storage._conn.execute('''
            INSERT INTO airgram_blockchain_gifts
                (nft_address, user_id, collection_address, title, image_url,
                 assigned_at, intent_token)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (asset['nft_address'], int(intent['user_id']),
              asset['collection_address'], asset['title'], asset['image_url'],
              now, intent['token']))
        storage._conn.execute('''
            UPDATE airgram_blockchain_deposit_intents
               SET status='confirmed', confirmed_at=?, tx_hash=?
             WHERE token=? AND status='pending'
        ''', (now, asset['tx_hash'], intent['token']))
        storage._conn.commit()
        return True
    except Exception as exc:
        storage._conn.rollback()
        if 'UNIQUE constraint failed' not in str(exc):
            raise
        # A deposit is single-owner and cannot be assigned to a second account.
        storage._conn.execute('''
            UPDATE airgram_blockchain_deposit_intents
               SET status='rejected', error='NFT is already assigned'
             WHERE token=? AND status='pending'
        ''', (intent['token'],))
        storage._conn.commit()
        return False


def sync_pending_deposits(storage, user_id: int, *, limit: int = 5) -> int:
    wallet = custody_wallet()
    _ensure_tables(storage)
    now = int(time.time())
    storage._conn.execute('''
        UPDATE airgram_blockchain_deposit_intents SET status='expired'
         WHERE status='pending' AND expires_at < ?
    ''', (now,))
    rows = storage._conn.execute('''
        SELECT * FROM airgram_blockchain_deposit_intents
         WHERE user_id=? AND status='pending' AND expires_at >= ?
         ORDER BY created_at LIMIT ?
    ''', (int(user_id), now, int(limit))).fetchall()
    storage._conn.commit()
    return sum(_complete_intent(storage, dict(row), wallet['address'])
               for row in rows)


def get_wallet_view(storage, user_id: int) -> dict:
    wallet = custody_wallet()
    _ensure_tables(storage)
    sync_pending_deposits(storage, int(user_id))
    inventory = _tonapi('/accounts/' + _tonapi_address(wallet['address'])
                        + '/nfts?limit=1000')
    current_assets = {
        _canonical_address(str(item.get('address') or '')): item
        for item in inventory.get('nft_items') or []
    }
    assigned = storage._conn.execute('''
        SELECT nft_address, collection_address, title, image_url, assigned_at
          FROM airgram_blockchain_gifts
         WHERE user_id=? ORDER BY assigned_at DESC
    ''', (int(user_id),)).fetchall()

    gifts = []
    for row in assigned:
        info = current_assets.get(_canonical_address(str(row['nft_address'])))
        if not info:
            # It was moved outside custody; don't claim it is still held here.
            continue
        metadata = info.get('metadata') or {}
        gifts.append({
            'address': row['nft_address'],
            'title': str(metadata.get('name') or row['title'] or 'TON Gift')[:200],
            'image': metadata.get('image') or metadata.get('image_url') or row['image_url'],
            'collection': row['collection_address'],
            'assignedAt': int(row['assigned_at']),
        })
    pending = storage._conn.execute('''
        SELECT COUNT(*) AS n FROM airgram_blockchain_deposit_intents
         WHERE user_id=? AND status='pending' AND expires_at>=?
    ''', (int(user_id), int(time.time()))).fetchone()
    return {
        'ok': True,
        'network': wallet['network'],
        'address': wallet['address'],
        'gifts': gifts,
        'pendingDeposits': int(pending['n'] if pending else 0),
    }


def create_deposit_intent(storage, user_id: int, nft_address: str,
                          sender_address: str) -> dict:
    nft_address = nft_address.strip()
    sender_address = sender_address.strip()
    if not RAW_ADDRESS.fullmatch(nft_address) or not RAW_ADDRESS.fullmatch(sender_address):
        return {'ok': False, 'error': 'TON-кошелёк или адрес NFT указан неверно'}
    wallet = custody_wallet()
    _ensure_tables(storage)
    try:
        info = _nft_info(nft_address)
    except RuntimeError as exc:
        return {'ok': False, 'error': str(exc)}
    if _address_of(info.get('owner')) != _canonical_address(sender_address):
        return {'ok': False,
                'error': 'Этот NFT сейчас не принадлежит выбранному TON-кошельку'}
    existing = storage._conn.execute(
        'SELECT user_id FROM airgram_blockchain_gifts WHERE nft_address=?',
        (nft_address,)).fetchone()
    if existing:
        return {'ok': False, 'error': 'Этот NFT уже находится в KoalGram'}
    pending_nft = storage._conn.execute('''
        SELECT 1 FROM airgram_blockchain_deposit_intents
         WHERE nft_address=? AND status='pending' AND expires_at>=? LIMIT 1
    ''', (nft_address, int(time.time()))).fetchone()
    if pending_nft:
        return {'ok': False, 'error': 'Перевод этого NFT уже ожидает подтверждения'}
    active = storage._conn.execute('''
        SELECT COUNT(*) AS n FROM airgram_blockchain_deposit_intents
         WHERE user_id=? AND status='pending' AND expires_at>=?
    ''', (int(user_id), int(time.time()))).fetchone()
    if int(active['n'] or 0) >= 5:
        return {'ok': False,
                'error': 'У вас уже есть 5 переводов, ожидающих подтверждения'}

    now = int(time.time())
    token = secrets.token_hex(16)
    memo = f'KoalGram deposit {token}'
    storage._conn.execute('''
        INSERT INTO airgram_blockchain_deposit_intents
            (token, user_id, nft_address, sender_address, memo,
             status, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
    ''', (token, int(user_id), nft_address, sender_address, memo,
          now, now + INTENT_TTL))
    storage._conn.commit()
    return {
        'ok': True,
        'token': token,
        'memo': memo,
        'recipient': wallet['address'],
        'expiresAt': now + INTENT_TTL,
    }


def finish_deposit_intent(storage, user_id: int, token: str) -> dict:
    _ensure_tables(storage)
    row = storage._conn.execute('''
        SELECT * FROM airgram_blockchain_deposit_intents
         WHERE token=? AND user_id=?
    ''', (token, int(user_id))).fetchone()
    if not row:
        return {'ok': False, 'error': 'Заявка на перевод не найдена'}
    intent = dict(row)
    if intent['status'] == 'confirmed':
        return {'ok': True, 'status': 'confirmed'}
    if intent['status'] != 'pending' or int(intent['expires_at']) < int(time.time()):
        return {'ok': False, 'error': 'Срок заявки истёк, создайте новую'}
    try:
        done = _complete_intent(storage, intent, custody_wallet()['address'])
    except RuntimeError as exc:
        return {'ok': False, 'error': str(exc)}
    return {'ok': True, 'status': 'confirmed' if done else 'pending'}
