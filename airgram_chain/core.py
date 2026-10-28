"""Storage and consensus primitives for the KoalGram chain.

The chain lives in its own SQLite database so gift/NFT accounting can never
corrupt the messenger database. Every state change is committed as a signed
block: Ed25519 signature over the block header, Merkle root over transaction
hashes and a digest of the resulting NFT state.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

import config

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    _ED25519 = True
except Exception:  # pragma: no cover - cryptography is present in production
    _ED25519 = False

CHAIN_ID = 'airgram-mainnet-1'
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(config.AIRGRAM_CHAIN_DB_PATH)
MEDIA_DIR = Path(config.AIRGRAM_CHAIN_MEDIA_DIR)
VALIDATOR_PATH = Path(config.AIRGRAM_CHAIN_VALIDATOR_PATH)
LOCK_PATH = DB_PATH.with_suffix('.lock')

CODE_TTL = 600
MAX_CODE_ATTEMPTS = 5
MAX_PENDING_PER_HOUR = 10
MAX_PENDING_TOTAL = 5

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False
_EMPTY_MERKLE = hashlib.sha256(b'').hexdigest()


class ChainError(Exception):
    def __init__(self, code: str, message: str = ''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _json_canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False)


def sha256_hex(value: str | bytes) -> str:
    data = value.encode('utf-8') if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


# ───────────────────────────── addresses ─────────────────────────────

def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def is_raw_address(address: str) -> bool:
    try:
        workchain, hash_part = (address or '').strip().lower().split(':', 1)
        int(workchain)
    except (ValueError, AttributeError):
        return False
    return len(hash_part) == 64 and all(c in '0123456789abcdef' for c in hash_part)


def normalize_address(address: str) -> str:
    address = (address or '').strip().lower()
    if not is_raw_address(address):
        raise ChainError('TON_ADDRESS_INVALID', 'Некорректный TON-адрес кошелька')
    workchain, hash_part = address.split(':', 1)
    return f'{int(workchain)}:{hash_part}'


def parse_address(value: str) -> str:
    """Accept raw `0:hex`, 64-char hex or a user-friendly TON address."""
    text = (value or '').strip()
    if is_raw_address(text):
        return normalize_address(text)
    candidate = text.lower()
    if len(candidate) == 64 and all(c in '0123456789abcdef' for c in candidate):
        return f'0:{candidate}'
    try:
        padded = text + '=' * (-len(text) % 4)
        data = base64.urlsafe_b64decode(padded.encode('ascii'))
    except Exception:
        data = b''
    if len(data) == 36 and data[0] in (0x11, 0x51):
        if _crc16(data[:34]) == int.from_bytes(data[34:36], 'big'):
            workchain = int.from_bytes(data[1:2], 'big', signed=True)
            return f'{workchain}:{data[2:34].hex()}'
    raise ChainError('TON_ADDRESS_INVALID', 'Некорректный TON-адрес кошелька')


def friendly_address(address: str) -> str:
    try:
        workchain, hash_part = normalize_address(address).split(':', 1)
    except ChainError:
        return address or ''
    payload = (b'\x51' + int(workchain).to_bytes(1, 'big', signed=True)
               + bytes.fromhex(hash_part))
    crc = _crc16(payload)
    return base64.urlsafe_b64encode(payload + crc.to_bytes(2, 'big')).decode('ascii')


def short_address(address: str) -> str:
    address = (address or '').strip()
    if ':' in address:
        return address.split(':', 1)[1][:8]
    return address[:8]


def nft_address_for(unique_id: int) -> str:
    digest = sha256_hex(f'airgram-nft-v2|{CHAIN_ID}|{int(unique_id)}')
    return f'0:{digest}'


# ───────────────────────────── db plumbing ─────────────────────────────

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS chain_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chain_blocks (
    height      INTEGER PRIMARY KEY,
    hash        TEXT NOT NULL UNIQUE,
    prev_hash   TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    proposer    TEXT NOT NULL,
    tx_count    INTEGER NOT NULL,
    merkle_root TEXT NOT NULL,
    state_hash  TEXT NOT NULL,
    signature   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chain_txs (
    tx_hash           TEXT PRIMARY KEY,
    lt                INTEGER NOT NULL UNIQUE,
    height            INTEGER NOT NULL,
    ts                INTEGER NOT NULL,
    kind              TEXT NOT NULL,
    from_address      TEXT NOT NULL DEFAULT '',
    to_address        TEXT NOT NULL DEFAULT '',
    nft_address       TEXT NOT NULL DEFAULT '',
    initiator_user_id INTEGER NOT NULL DEFAULT 0,
    owner_user_id     INTEGER NOT NULL DEFAULT 0,
    request_id        INTEGER,
    payload_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chain_txs_address
    ON chain_txs(to_address, lt DESC);
CREATE INDEX IF NOT EXISTS idx_chain_txs_height
    ON chain_txs(height DESC);
CREATE TABLE IF NOT EXISTS chain_accounts (
    address       TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL,
    created_at    INTEGER NOT NULL,
    nft_count     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chain_nfts (
    address           TEXT PRIMARY KEY,
    item_index        INTEGER NOT NULL UNIQUE,
    owner_address     TEXT NOT NULL,
    owner_user_id     INTEGER NOT NULL,
    minted_for_user_id INTEGER NOT NULL,
    initiator_user_id INTEGER NOT NULL DEFAULT 0,
    gift_id           INTEGER NOT NULL,
    saved_id          INTEGER NOT NULL UNIQUE,
    unique_id         INTEGER NOT NULL UNIQUE,
    unique_slug       TEXT NOT NULL UNIQUE,
    title             TEXT NOT NULL,
    collection        TEXT NOT NULL,
    model_name        TEXT NOT NULL DEFAULT '',
    pattern_name      TEXT NOT NULL DEFAULT '',
    backdrop_name     TEXT NOT NULL DEFAULT '',
    attributes_json   TEXT NOT NULL DEFAULT '[]',
    image_path        TEXT NOT NULL DEFAULT '',
    thumb_path        TEXT NOT NULL DEFAULT '',
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    mint_tx_hash      TEXT NOT NULL,
    created_at        INTEGER NOT NULL,
    burned            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chain_nfts_owner
    ON chain_nfts(owner_address, created_at DESC);
CREATE TABLE IF NOT EXISTS chain_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    height      INTEGER NOT NULL,
    ts          INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    address     TEXT NOT NULL DEFAULT '',
    nft_address TEXT NOT NULL DEFAULT '',
    tx_hash     TEXT NOT NULL DEFAULT '',
    data_json   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chain_events_address
    ON chain_events(address, id DESC);
CREATE TABLE IF NOT EXISTS chain_send_requests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    code_salt        TEXT NOT NULL,
    code_hash        TEXT NOT NULL,
    slug             TEXT NOT NULL,
    saved_id         INTEGER NOT NULL,
    owner_user_id    INTEGER NOT NULL,
    owner_tg_id      INTEGER NOT NULL,
    initiator_tg_id  INTEGER NOT NULL,
    wallet_address   TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    attempts         INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL,
    expires_at       INTEGER NOT NULL,
    confirmed_at     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chain_requests_pending
    ON chain_send_requests(initiator_tg_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chain_requests_slug
    ON chain_send_requests(slug, status);
'''


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=20, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=20000')
    ensure_ready(conn)
    return conn


@contextmanager
def writer_lock() -> Iterator[None]:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, 'a+') as handle:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - non-POSIX fallback
            pass
        try:
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except ImportError:  # pragma: no cover
                pass


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield conn
        conn.execute('COMMIT')
    except Exception:
        conn.execute('ROLLBACK')
        raise


def get_meta(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute('SELECT value FROM chain_meta WHERE key=?', (key,)).fetchone()
    return row['value'] if row else ''


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        '''INSERT INTO chain_meta (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value''',
        (key, str(value)))


# ───────────────────────────── validator ─────────────────────────────

_VALIDATOR_CACHE: Optional[dict] = None


def _create_validator_file(path: Path) -> dict:
    key = Ed25519PrivateKey.generate()
    private_bytes = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    public_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    payload = {
        'private_key': private_bytes.hex(),
        'public_key': public_bytes.hex(),
        'created_at': int(time.time()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return payload


def _validator() -> dict:
    global _VALIDATOR_CACHE
    if _VALIDATOR_CACHE:
        return _VALIDATOR_CACHE
    if not _ED25519:
        raise ChainError('CHAIN_CRYPTO_UNAVAILABLE',
                         'Криптография Ed25519 недоступна на сервере')
    path = VALIDATOR_PATH
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_suffix('.lock'), 'a+') as handle:
            try:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except ImportError:  # pragma: no cover
                pass
            try:
                if not path.is_file():
                    _create_validator_file(path)
            finally:
                try:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except ImportError:  # pragma: no cover
                    pass
    payload = json.loads(path.read_text(encoding='utf-8'))
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(payload['private_key']))
    public_hex = payload.get('public_key') or key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()
    _VALIDATOR_CACHE = {'key': key, 'public_key': public_hex}
    return _VALIDATOR_CACHE


def validator_public_key() -> str:
    return _validator()['public_key']


def _sign(data: bytes) -> str:
    return _validator()['key'].sign(data).hex()


# ───────────────────────────── block building ─────────────────────────────

def _merkle_root(tx_hashes: list[str]) -> str:
    if not tx_hashes:
        return _EMPTY_MERKLE
    layer = [bytes.fromhex(h) for h in tx_hashes]
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [hashlib.sha256(layer[i] + layer[i + 1]).digest()
                 for i in range(0, len(layer), 2)]
    return layer[0].hex()


def _state_hash(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    digest.update(b'airgram-state-v1\n')
    for row in conn.execute(
            'SELECT address, owner_user_id, nft_count FROM chain_accounts '
            'ORDER BY address'):
        digest.update(f'A|{row["address"]}|{row["owner_user_id"]}|'
                      f'{row["nft_count"]}\n'.encode())
    for row in conn.execute(
            'SELECT address, owner_address, saved_id, unique_slug, burned '
            'FROM chain_nfts ORDER BY address'):
        digest.update(f'N|{row["address"]}|{row["owner_address"]}|'
                      f'{row["saved_id"]}|{row["unique_slug"]}|'
                      f'{row["burned"]}\n'.encode())
    return digest.hexdigest()


def _block_hash(header: dict) -> str:
    return sha256_hex(_json_canonical(header))


def _alloc_lt(conn: sqlite3.Connection, count: int = 1) -> int:
    last = int(get_meta(conn, 'last_lt') or 0)
    base = max(last + 1, int(time.time() * 1_000_000))
    set_meta(conn, 'last_lt', str(base + count - 1))
    return base


def _append_block(conn: sqlite3.Connection, tx_hashes: list[str]) -> dict:
    height = int(get_meta(conn, 'height') or 0) + 1
    prev_hash = get_meta(conn, 'last_hash')
    ts = int(time.time())
    proposer = validator_public_key()
    merkle_root = _merkle_root(tx_hashes)
    state_hash = _state_hash(conn)
    header = {
        'height': height,
        'prev_hash': prev_hash,
        'ts': ts,
        'proposer': proposer,
        'tx_count': len(tx_hashes),
        'merkle_root': merkle_root,
        'state_hash': state_hash,
    }
    block_hash = _block_hash(header)
    signature = _sign(bytes.fromhex(block_hash))
    conn.execute(
        '''INSERT INTO chain_blocks
           (height, hash, prev_hash, ts, proposer, tx_count, merkle_root,
            state_hash, signature)
           VALUES (?,?,?,?,?,?,?,?,?)''',
        (height, block_hash, prev_hash, ts, proposer, len(tx_hashes),
         merkle_root, state_hash, signature))
    set_meta(conn, 'height', str(height))
    set_meta(conn, 'last_hash', block_hash)
    total = int(get_meta(conn, 'total_txs') or 0) + len(tx_hashes)
    set_meta(conn, 'total_txs', str(total))
    return {**header, 'hash': block_hash, 'signature': signature}


def _create_genesis(conn: sqlite3.Connection) -> None:
    validator = _validator()
    proposer = validator['public_key']
    state_hash = _state_hash(conn)
    header = {
        'height': 0,
        'prev_hash': '0' * 64,
        'ts': int(time.time()),
        'proposer': proposer,
        'tx_count': 0,
        'merkle_root': _EMPTY_MERKLE,
        'state_hash': state_hash,
    }
    block_hash = _block_hash(header)
    signature = _sign(bytes.fromhex(block_hash))
    conn.execute(
        '''INSERT INTO chain_blocks
           (height, hash, prev_hash, ts, proposer, tx_count, merkle_root,
            state_hash, signature)
           VALUES (0,?,?,?,?,0,?,?,?)''',
        (block_hash, header['prev_hash'], header['ts'], proposer,
         header['merkle_root'], state_hash, signature))
    set_meta(conn, 'height', '0')
    set_meta(conn, 'last_hash', block_hash)
    set_meta(conn, 'genesis_hash', block_hash)
    set_meta(conn, 'chain_id', CHAIN_ID)
    set_meta(conn, 'validator_public_key', proposer)
    set_meta(conn, 'created_at', str(header['ts']))
    set_meta(conn, 'last_lt', '0')
    set_meta(conn, 'total_txs', '0')


def ensure_ready(conn: Optional[sqlite3.Connection] = None) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        own_conn = conn is None
        conn = conn or connect_raw()
        try:
            conn.executescript(_SCHEMA)
            if not get_meta(conn, 'genesis_hash'):
                conn.execute('BEGIN IMMEDIATE')
                try:
                    if not get_meta(conn, 'genesis_hash'):
                        _create_genesis(conn)
                    conn.execute('COMMIT')
                except Exception:
                    conn.execute('ROLLBACK')
                    raise
            _SCHEMA_READY = True
        finally:
            if own_conn:
                conn.close()


def connect_raw() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=20, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=20000')
    return conn


# ───────────────────────────── minting ─────────────────────────────

def get_nft_by_address(conn: sqlite3.Connection, address: str) -> Optional[dict]:
    row = conn.execute('SELECT * FROM chain_nfts WHERE address=?',
                       ((address or '').strip(),)).fetchone()
    return dict(row) if row else None


def get_nft_by_slug(conn: sqlite3.Connection, slug: str) -> Optional[dict]:
    row = conn.execute('SELECT * FROM chain_nfts WHERE unique_slug=?',
                       ((slug or '').strip(),)).fetchone()
    return dict(row) if row else None


def get_nft_by_saved(conn: sqlite3.Connection, saved_id: int) -> Optional[dict]:
    row = conn.execute('SELECT * FROM chain_nfts WHERE saved_id=?',
                       (int(saved_id),)).fetchone()
    return dict(row) if row else None


def mint_nft(*, nft: dict, owner_address: str, owner_user_id: int,
             initiator_user_id: int, request_id: Optional[int] = None) -> dict:
    """Atomically mint a fully prepared NFT record and seal a new block."""
    owner_address = normalize_address(owner_address)
    nft_address = nft['address']
    if not is_raw_address(nft_address):
        raise ChainError('NFT_ADDRESS_INVALID', 'Некорректный адрес NFT')
    with writer_lock():
        conn = connect()
        try:
            with transaction(conn):
                existing = conn.execute(
                    '''SELECT * FROM chain_nfts
                       WHERE saved_id=? OR unique_id=? OR unique_slug=?
                       LIMIT 1''',
                    (int(nft['saved_id']), int(nft['unique_id']),
                     nft['unique_slug'])).fetchone()
                if existing:
                    return {'ok': False, 'error': 'ALREADY_MINTED',
                            'nft': dict(existing)}
                account = conn.execute(
                    'SELECT * FROM chain_accounts WHERE address=?',
                    (owner_address,)).fetchone()
                if account and int(account['owner_user_id']) != int(owner_user_id):
                    raise ChainError(
                        'WALLET_IN_USE',
                        'Этот кошелёк уже привязан к другому аккаунту KoalGram')
                now = int(time.time())
                item_index = int(conn.execute(
                    'SELECT COALESCE(MAX(item_index), 0) + 1 AS n '
                    'FROM chain_nfts').fetchone()['n'])
                lt = _alloc_lt(conn)
                payload = {
                    'gift_id': int(nft['gift_id']),
                    'saved_id': int(nft['saved_id']),
                    'unique_id': int(nft['unique_id']),
                    'slug': nft['unique_slug'],
                    'title': nft['title'],
                    'collection': nft['collection'],
                    'metadata_hash': sha256_hex(nft['metadata_json']),
                    'request_id': int(request_id) if request_id else 0,
                }
                payload_json = _json_canonical(payload)
                tx_body = {
                    'kind': 'nft_mint',
                    'lt': lt,
                    'ts': now,
                    'from': '',
                    'to': owner_address,
                    'nft': nft_address,
                    'payload_hash': sha256_hex(payload_json),
                }
                tx_hash = sha256_hex(_json_canonical(tx_body))
                height = int(get_meta(conn, 'height') or 0) + 1
                conn.execute(
                    '''INSERT INTO chain_txs
                       (tx_hash, lt, height, ts, kind, from_address,
                        to_address, nft_address, initiator_user_id,
                        owner_user_id, request_id, payload_json)
                       VALUES (?,?,?,?,'nft_mint','',?,?,?,?,?,?)''',
                    (tx_hash, lt, height, now, owner_address, nft_address,
                     int(initiator_user_id), int(owner_user_id),
                     int(request_id) if request_id else None, payload_json))
                conn.execute(
                    '''INSERT INTO chain_nfts
                       (address, item_index, owner_address, owner_user_id,
                        minted_for_user_id, initiator_user_id, gift_id,
                        saved_id, unique_id, unique_slug, title, collection,
                        model_name, pattern_name, backdrop_name,
                        attributes_json, image_path, thumb_path,
                        metadata_json, mint_tx_hash, created_at, burned)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
                    (nft_address, item_index, owner_address, int(owner_user_id),
                     int(owner_user_id), int(initiator_user_id),
                     int(nft['gift_id']), int(nft['saved_id']),
                     int(nft['unique_id']), nft['unique_slug'], nft['title'],
                     nft['collection'], nft['model_name'], nft['pattern_name'],
                     nft['backdrop_name'], nft['attributes_json'],
                     nft['image_path'], nft['thumb_path'],
                     nft['metadata_json'], tx_hash, now))
                conn.execute(
                    '''INSERT INTO chain_accounts
                       (address, owner_user_id, created_at, nft_count)
                       VALUES (?,?,?,1)
                       ON CONFLICT(address) DO UPDATE SET
                           owner_user_id=excluded.owner_user_id,
                           nft_count=chain_accounts.nft_count+1''',
                    (owner_address, int(owner_user_id), now))
                event_data = _json_canonical({
                    'title': nft['title'],
                    'slug': nft['unique_slug'],
                    'collection': nft['collection'],
                    'image': nft['thumb_path'],
                })
                conn.execute(
                    '''INSERT INTO chain_events
                       (height, ts, kind, address, nft_address, tx_hash,
                        data_json)
                       VALUES (?,?,'nft_mint',?,?,?,?)''',
                    (height, now, owner_address, nft_address, tx_hash,
                     event_data))
                block = _append_block(conn, [tx_hash])
            minted = get_nft_by_address(conn, nft_address)
            return {'ok': True, 'nft': minted, 'block': block,
                    'tx_hash': tx_hash}
        finally:
            conn.close()


# ───────────────────────────── reads ─────────────────────────────

def _media_urls(image_path: str, thumb_path: str) -> tuple[str, str]:
    base = config.AIRGRAM_PUBLIC_BASE_URL
    media_dir = Path(config.AIRGRAM_CHAIN_MEDIA_DIR)

    def url(name: str) -> str:
        if not name:
            return ''
        try:
            version = int((media_dir / name).stat().st_mtime)
        except OSError:
            version = 0
        return f'{base}/api/koalgram-nft-image/{name}?v={version}'

    return url(image_path), url(thumb_path)


def nft_public(row: dict) -> dict:
    payload = dict(row)
    payload['friendlyAddress'] = friendly_address(row['owner_address'])
    payload['ownerShort'] = short_address(row['owner_address'])
    image, thumb = _media_urls(row.get('image_path') or '',
                               row.get('thumb_path') or '')
    payload['imageUrl'] = image
    payload['thumbUrl'] = thumb
    payload['metadataUrl'] = (
        f'{config.AIRGRAM_PUBLIC_BASE_URL}/api/koalgram-nft-metadata/'
        f'{row["address"].split(":", 1)[1]}.json')
    payload['explorerUrl'] = (
        f'{config.AIRGRAM_WALLET_URL}?nft={row["address"]}')
    try:
        payload['attributes'] = json.loads(row.get('attributes_json') or '[]')
    except json.JSONDecodeError:
        payload['attributes'] = []
    for key in ('attributes_json', 'metadata_json'):
        payload.pop(key, None)
    return payload


def list_nfts(address: str = '', *, limit: int = 200, offset: int = 0) -> list[dict]:
    conn = connect()
    try:
        args: list[Any] = []
        where = 'burned=0'
        if address:
            where += ' AND owner_address=?'
            args.append(normalize_address(address))
        args += [max(1, min(int(limit), 500)), max(0, int(offset))]
        rows = conn.execute(
            f'''SELECT * FROM chain_nfts WHERE {where}
                ORDER BY item_index DESC LIMIT ? OFFSET ?''', args).fetchall()
        return [nft_public(dict(r)) for r in rows]
    finally:
        conn.close()


def count_nfts(address: str = '') -> int:
    conn = connect()
    try:
        if address:
            row = conn.execute(
                'SELECT COUNT(*) AS c FROM chain_nfts WHERE burned=0 '
                'AND owner_address=?', (normalize_address(address),)).fetchone()
        else:
            row = conn.execute(
                'SELECT COUNT(*) AS c FROM chain_nfts WHERE burned=0').fetchone()
        return int(row['c'])
    finally:
        conn.close()


def get_nft(address: str) -> Optional[dict]:
    conn = connect()
    try:
        row = get_nft_by_address(conn, (address or '').strip())
        return nft_public(row) if row else None
    finally:
        conn.close()


def get_nft_by_slug_public(slug: str) -> Optional[dict]:
    conn = connect()
    try:
        row = get_nft_by_slug(conn, slug)
        return nft_public(row) if row else None
    finally:
        conn.close()


def nft_metadata(address: str) -> Optional[dict]:
    conn = connect()
    try:
        row = get_nft_by_address(conn, (address or '').strip())
        if not row:
            return None
        data = json.loads(row.get('metadata_json') or '{}')
        image, _thumb = _media_urls(row.get('image_path') or '', '')
        data['image'] = image
        data['name'] = row['title']
        data['address'] = row['address']
        data['owner'] = row['owner_address']
        if not data.get('description'):
            data['description'] = 'Уникальный подарок KoalGram на блокчейне KoalGram.'
        return data
    finally:
        conn.close()


def nft_detail(address: str) -> Optional[dict]:
    conn = connect()
    try:
        row = get_nft_by_address(conn, (address or '').strip())
        if not row:
            return None
        tx_rows = conn.execute(
            'SELECT * FROM chain_txs WHERE nft_address=? ORDER BY lt DESC',
            (row['address'],)).fetchall()
        event_rows = conn.execute(
            'SELECT * FROM chain_events WHERE nft_address=? ORDER BY id DESC',
            (row['address'],)).fetchall()
        events = []
        for event in event_rows:
            item = dict(event)
            try:
                item['data'] = json.loads(item.pop('data_json') or '{}')
            except json.JSONDecodeError:
                item['data'] = {}
            events.append(item)
        block = conn.execute(
            'SELECT height, hash, ts, signature FROM chain_blocks WHERE height=?',
            (int(tx_rows[0]['height']) if tx_rows else 0,)).fetchone()
        return {
            'nft': nft_public(dict(row)),
            'transactions': [dict(r) for r in tx_rows],
            'events': events,
            'mintBlock': dict(block) if block else None,
            'owner': {
                'address': row['owner_address'],
                'friendlyAddress': friendly_address(row['owner_address']),
            },
        }
    finally:
        conn.close()


def chain_info() -> dict:
    conn = connect()
    try:
        height = int(get_meta(conn, 'height') or 0)
        last = conn.execute(
            'SELECT * FROM chain_blocks WHERE height=?', (height,)).fetchone()
        blocks = int(conn.execute(
            'SELECT COUNT(*) AS c FROM chain_blocks').fetchone()['c'])
        nfts = int(conn.execute(
            'SELECT COUNT(*) AS c FROM chain_nfts WHERE burned=0').fetchone()['c'])
        accounts = int(conn.execute(
            'SELECT COUNT(*) AS c FROM chain_accounts').fetchone()['c'])
        return {
            'ok': True,
            'chainId': get_meta(conn, 'chain_id') or CHAIN_ID,
            'height': height,
            'blocks': blocks,
            'transactions': int(get_meta(conn, 'total_txs') or 0),
            'nfts': nfts,
            'wallets': accounts,
            'lastBlockHash': get_meta(conn, 'last_hash'),
            'genesisHash': get_meta(conn, 'genesis_hash'),
            'validatorPublicKey': get_meta(conn, 'validator_public_key'),
            'lastBlockAt': int(last['ts']) if last else 0,
            'merkleRoot': last['merkle_root'] if last else '',
            'stateHash': last['state_hash'] if last else '',
            'lastSignature': last['signature'] if last else '',
        }
    finally:
        conn.close()


def verify_chain(limit: int = 200) -> dict:
    """Recompute hashes/signatures for the latest blocks."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    conn = connect()
    try:
        public_hex = get_meta(conn, 'validator_public_key')
        if not public_hex:
            return {'ok': False, 'error': 'NO_VALIDATOR'}
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
        rows = conn.execute(
            'SELECT * FROM chain_blocks ORDER BY height DESC LIMIT ?',
            (max(1, min(int(limit), 1000)),)).fetchall()
        checked = 0
        for row in rows:
            header = {
                'height': int(row['height']),
                'prev_hash': row['prev_hash'],
                'ts': int(row['ts']),
                'proposer': row['proposer'],
                'tx_count': int(row['tx_count']),
                'merkle_root': row['merkle_root'],
                'state_hash': row['state_hash'],
            }
            block_hash = _block_hash(header)
            if block_hash != row['hash']:
                return {'ok': False, 'error': 'HASH_MISMATCH',
                        'height': int(row['height'])}
            tx_hashes = [r['tx_hash'] for r in conn.execute(
                'SELECT tx_hash FROM chain_txs WHERE height=? ORDER BY lt',
                (int(row['height']),)).fetchall()]
            if _merkle_root(tx_hashes) != row['merkle_root']:
                return {'ok': False, 'error': 'MERKLE_MISMATCH',
                        'height': int(row['height'])}
            try:
                public_key.verify(bytes.fromhex(row['signature']),
                                  bytes.fromhex(block_hash))
            except Exception:
                return {'ok': False, 'error': 'SIGNATURE_INVALID',
                        'height': int(row['height'])}
            checked += 1
        return {'ok': True, 'checkedBlocks': checked,
                'height': int(get_meta(conn, 'height') or 0)}
    finally:
        conn.close()


def list_blocks(limit: int = 20) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            'SELECT * FROM chain_blocks ORDER BY height DESC LIMIT ?',
            (max(1, min(int(limit), 100)),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_activity(address: str = '', *, limit: int = 50) -> list[dict]:
    conn = connect()
    try:
        where = ''
        args: list[Any] = []
        if address:
            where = 'WHERE address=? OR nft_address IN (SELECT address FROM chain_nfts WHERE owner_address=?)'
            args = [normalize_address(address), normalize_address(address)]
        args.append(max(1, min(int(limit), 200)))
        rows = conn.execute(
            f'''SELECT * FROM chain_events {where}
                ORDER BY id DESC LIMIT ?''', args).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item['data'] = json.loads(item.pop('data_json') or '{}')
            except json.JSONDecodeError:
                item['data'] = {}
            out.append(item)
        return out
    finally:
        conn.close()


def wallet_overview(address: str) -> dict:
    address = parse_address(address)
    conn = connect()
    try:
        account = conn.execute(
            'SELECT * FROM chain_accounts WHERE address=?', (address,)).fetchone()
        return {
            'address': address,
            'friendlyAddress': friendly_address(address),
            'nftCount': int(account['nft_count']) if account else 0,
            'ownerUserId': int(account['owner_user_id']) if account else 0,
            'createdAt': int(account['created_at']) if account else 0,
            'nfts': list_nfts(address),
        }
    finally:
        conn.close()


# ───────────────────────────── send requests ─────────────────────────────

def _hash_code(salt: str, code: str) -> str:
    return hashlib.sha256(f'{salt}:{code}'.encode('utf-8')).hexdigest()


def _expire_stale(conn: sqlite3.Connection, now: int) -> None:
    conn.execute(
        '''UPDATE chain_send_requests
           SET status='expired'
           WHERE status IN ('pending','confirming') AND expires_at<=?''',
        (now,))
    conn.execute(
        '''UPDATE chain_send_requests
           SET status='pending', confirmed_at=NULL
           WHERE status='confirming' AND confirmed_at IS NOT NULL
             AND confirmed_at < ?''',
        (now - 120,))


def create_send_request(*, slug: str, saved_id: int, owner_user_id: int,
                        owner_tg_id: int, initiator_tg_id: int,
                        wallet_address: str, ttl: int = CODE_TTL) -> dict:
    wallet_address = normalize_address(wallet_address)
    now = int(time.time())
    code = f'{secrets.randbelow(1_000_000):06d}'
    salt = secrets.token_hex(16)
    with writer_lock():
        conn = connect()
        try:
            with transaction(conn):
                _expire_stale(conn, now)
                conn.execute(
                    '''UPDATE chain_send_requests SET status='cancelled'
                       WHERE status='pending' AND slug=?''', (slug,))
                recent = conn.execute(
                    '''SELECT COUNT(*) AS c FROM chain_send_requests
                       WHERE initiator_tg_id=? AND created_at>=?''',
                    (int(initiator_tg_id), now - 3600)).fetchone()['c']
                if int(recent) >= MAX_PENDING_PER_HOUR:
                    raise ChainError(
                        'RATE_LIMIT',
                        'Слишком много запросов. Попробуйте позже.')
                active = conn.execute(
                    '''SELECT COUNT(*) AS c FROM chain_send_requests
                       WHERE initiator_tg_id=? AND status IN ('pending','confirming')''',
                    (int(initiator_tg_id),)).fetchone()['c']
                if int(active) >= MAX_PENDING_TOTAL:
                    raise ChainError(
                        'RATE_LIMIT',
                        'У вас уже есть несколько неподтверждённых запросов. '
                        'Завершите их или подождите 10 минут.')
                cur = conn.execute(
                    '''INSERT INTO chain_send_requests
                       (code_salt, code_hash, slug, saved_id, owner_user_id,
                        owner_tg_id, initiator_tg_id, wallet_address, status,
                        attempts, created_at, expires_at)
                       VALUES (?,?,?,?,?,?,?,?,'pending',0,?,?)''',
                    (salt, _hash_code(salt, code), slug, int(saved_id),
                     int(owner_user_id), int(owner_tg_id),
                     int(initiator_tg_id), wallet_address, now,
                     now + int(ttl)))
                request_id = int(cur.lastrowid)
            return {'ok': True, 'id': request_id, 'code': code,
                    'expiresAt': now + int(ttl), 'ttl': int(ttl)}
        except ChainError:
            raise
        finally:
            conn.close()


def cancel_send_request(request_id: int) -> None:
    with writer_lock():
        conn = connect()
        try:
            with transaction(conn):
                conn.execute(
                    "UPDATE chain_send_requests SET status='cancelled' "
                    "WHERE id=? AND status IN ('pending','confirming')",
                    (int(request_id),))
        finally:
            conn.close()


def confirm_send_request(initiator_tg_id: int, code: str,
                         slug: str = '') -> dict:
    code = (code or '').strip()
    if not (code.isdigit() and len(code) == 6):
        return {'ok': False, 'error': 'INVALID_FORMAT',
                'message': 'Код состоит из 6 цифр.'}
    now = int(time.time())
    with writer_lock():
        conn = connect()
        try:
            with transaction(conn):
                _expire_stale(conn, now)
                args: list[Any] = [int(initiator_tg_id)]
                cond = "initiator_tg_id=? AND status='pending'"
                if slug:
                    cond += ' AND slug=?'
                    args.append(slug)
                row = conn.execute(
                    f'''SELECT * FROM chain_send_requests WHERE {cond}
                        ORDER BY id DESC LIMIT 1''', args).fetchone()
                if not row:
                    return {'ok': False, 'error': 'NO_PENDING',
                            'message': 'Нет активного запроса. '
                                       'Отправьте /send <ссылка на подарок> заново.'}
                if int(row['attempts']) >= MAX_CODE_ATTEMPTS:
                    conn.execute(
                        "UPDATE chain_send_requests SET status='locked' "
                        'WHERE id=?', (int(row['id']),))
                    return {'ok': False, 'error': 'TOO_MANY_ATTEMPTS',
                            'message': 'Слишком много неверных попыток. '
                                       'Запросите новый код.'}
                if not secrets.compare_digest(
                        _hash_code(row['code_salt'], code), row['code_hash']):
                    attempts = int(row['attempts']) + 1
                    status = 'locked' if attempts >= MAX_CODE_ATTEMPTS else 'pending'
                    conn.execute(
                        'UPDATE chain_send_requests SET attempts=?, status=? '
                        'WHERE id=?', (attempts, status, int(row['id'])))
                    left = max(0, MAX_CODE_ATTEMPTS - attempts)
                    return {'ok': False, 'error': 'INVALID_CODE',
                            'attemptsLeft': left,
                            'message': f'Неверный код. Осталось попыток: {left}.'}
                conn.execute(
                    "UPDATE chain_send_requests SET status='confirming', "
                    'confirmed_at=? WHERE id=?', (now, int(row['id'])))
                request = dict(row)
        finally:
            conn.close()
    return {'ok': True, 'request': request}


def finish_send_request(request_id: int, *, ok: bool) -> None:
    with writer_lock():
        conn = connect()
        try:
            with transaction(conn):
                if ok:
                    conn.execute(
                        "UPDATE chain_send_requests SET status='confirmed', "
                        'confirmed_at=? WHERE id=?',
                        (int(time.time()), int(request_id)))
                else:
                    conn.execute(
                        "UPDATE chain_send_requests SET status='pending', "
                        'confirmed_at=NULL WHERE id=? AND status=\'confirming\'',
                        (int(request_id),))
        finally:
            conn.close()


def pending_for_owner(owner_tg_id: int) -> list[dict]:
    now = int(time.time())
    conn = connect()
    try:
        rows = conn.execute(
            '''SELECT * FROM chain_send_requests
               WHERE owner_tg_id=? AND status='pending' AND expires_at>?
               ORDER BY id DESC''', (int(owner_tg_id), now)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
