"""Import custom emoji documents from Telegram into the local sticker DB."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from frame_verification.telethon_env import ensure_telethon_env, telethon_credentials

log = logging.getLogger(__name__)

BANNER_SET_SHORT = 'AirgramBannerEmoji'
BANNER_SET_TITLE = 'KoalGram Banner Emoji'
BANNER_SET_ID = 900000000001
_SESSION = os.environ.get(
    'FRAME_TELETHON_SESSION',
    str(ROOT / 'mysession'),
)


def _bot_token() -> str:
    return (
        os.environ.get('SHOP_BOT_TOKEN', '').strip()
        or os.environ.get('MYTELEGRAM_AUTH_BOT_TOKEN', '').strip()
    )


def _to_i64(value: int) -> int:
    value = int(value) % (1 << 64)
    if value >= (1 << 63):
        value -= (1 << 64)
    return value


BANNER_SET_ACCESS_HASH = _to_i64(0x414752414D454D4F)


def _stable_access_hash(seed: int) -> int:
    raw = int(hashlib.md5(f'emoji:{seed}'.encode()).hexdigest()[:15], 16)
    return _to_i64(raw)


def _doc_extension(mime: str) -> str:
    mime = mime or 'image/webp'
    if 'tgsticker' in mime:
        return '.tgs'
    if mime == 'video/webm':
        return '.webm'
    return '.webp'


def _bot_api_request(method: str, payload: dict[str, Any]) -> Any:
    token = _bot_token()
    if not token:
        raise RuntimeError('bot token not configured')
    url = f'https://api.telegram.org/bot{token}/{method}'
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Bot API HTTP {exc.code}: {detail}') from exc
    if not body.get('ok'):
        raise RuntimeError(body.get('description') or 'Bot API error')
    return body.get('result')


def _download_bot_file(token: str, file_path: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    if tmp.is_file():
        tmp.unlink(missing_ok=True)
    url = f'https://api.telegram.org/file/bot{token}/{file_path}'
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    if not data:
        raise RuntimeError('empty emoji download')
    tmp.write_bytes(data)
    tmp.replace(dest)


def _banner_set_ids(storage) -> tuple[int, int]:
    row = storage._conn.execute(
        'SELECT id, access_hash FROM sticker_sets '
        'WHERE lower(short_name)=lower(?) LIMIT 1',
        (BANNER_SET_SHORT,),
    ).fetchone()
    if row:
        return int(row['id']), int(row['access_hash'])
    return BANNER_SET_ID, BANNER_SET_ACCESS_HASH


def _ensure_banner_set(storage) -> tuple[int, int]:
    set_id, access_hash = _banner_set_ids(storage)
    doc_hash = int(hashlib.md5(
        f'{set_id}:{BANNER_SET_SHORT}'.encode(),
    ).hexdigest()[:8], 16)
    count = len(storage.get_sticker_documents(set_id))
    storage.upsert_sticker_set(
        set_id,
        access_hash,
        BANNER_SET_SHORT,
        BANNER_SET_TITLE,
        128,
        count,
        doc_hash,
    )
    return set_id, access_hash


def _store_emoji_document(
    storage,
    *,
    doc_id: int,
    access_hash: int,
    path: Path,
    mime: str,
    alt: str,
    size: int,
    width: int = 512,
    height: int = 512,
) -> dict[str, Any]:
    from handlers.stickers import (
        build_document_attr_custom_emoji,
        build_document_attr_image_size,
    )

    set_id, set_access_hash = _ensure_banner_set(storage)
    attrs = [
        build_document_attr_image_size(width, height).hex(),
        build_document_attr_custom_emoji(
            alt,
            set_id,
            set_access_hash,
            free=True,
            text_color=False,
        ).hex(),
    ]
    row = {
        'id': int(doc_id),
        'access_hash': _to_i64(access_hash),
        'set_id': set_id,
        'file_path': str(path),
        'mime_type': mime,
        'size': int(size),
        'width': width,
        'height': height,
        'alt': alt or '⭐',
        'file_reference': b'\x00' * 8,
        'date': int(time.time()),
        'attributes_json': json.dumps(attrs),
        'sort_order': 0,
    }
    _ensure_banner_set(storage)
    storage.upsert_sticker_document(row)
    _ensure_banner_set(storage)
    try:
        storage.mark_sticker_set_installed(set_id)
    except Exception:
        pass
    return {'alt': row['alt'], 'documentId': int(doc_id)}


def _import_via_bot_api(storage, doc_id: int) -> Optional[dict[str, Any]]:
    token = _bot_token()
    if not token:
        return None

    stickers = _bot_api_request(
        'getCustomEmojiStickers',
        {'custom_emoji_ids': [str(doc_id)]},
    )
    if not stickers:
        log.warning('Bot API returned no sticker for custom emoji %s', doc_id)
        return None

    sticker = stickers[0]
    alt = (sticker.get('emoji') or '').strip() or '⭐'
    is_animated = bool(sticker.get('is_animated'))
    is_video = bool(sticker.get('is_video'))
    if is_animated:
        mime = 'application/x-tgsticker'
    elif is_video:
        mime = 'video/webm'
    else:
        mime = 'image/webp'

    file_info = _bot_api_request('getFile', {'file_id': sticker['file_id']})
    tg_path = file_info.get('file_path') or ''
    if not tg_path:
        raise RuntimeError('Bot API getFile returned empty path')

    access_hash = _stable_access_hash(doc_id)
    set_id, _ = _banner_set_ids(storage)
    out_dir = Path(config.STICKERS_DATA_DIR) / str(set_id)
    path = out_dir / f'{doc_id}{_doc_extension(mime)}'
    if not path.is_file() or path.stat().st_size <= 0:
        _download_bot_file(token, tg_path, path)

    return _store_emoji_document(
        storage,
        doc_id=doc_id,
        access_hash=access_hash,
        path=path,
        mime=mime,
        alt=alt,
        size=int(path.stat().st_size),
        width=int(sticker.get('width') or 512),
        height=int(sticker.get('height') or 512),
    )


def _pack_doc_row(
    doc,
    path: Path,
    *,
    set_id: int,
    set_access_hash: int,
    sort_order: int = 0,
) -> dict[str, Any]:
    from handlers.stickers import (
        build_document_attr_custom_emoji,
        build_document_attr_image_size,
    )
    from telethon.tl.types import (
        DocumentAttributeCustomEmoji,
        DocumentAttributeImageSize,
        DocumentAttributeSticker,
    )

    w, h = 512, 512
    alt = '⭐'
    text_color = False
    for attr in doc.attributes or []:
        if isinstance(attr, DocumentAttributeImageSize):
            w, h = attr.w, attr.h
        elif isinstance(attr, DocumentAttributeCustomEmoji):
            alt = attr.alt or alt
            text_color = bool(getattr(attr, 'text_color', False))
        elif isinstance(attr, DocumentAttributeSticker):
            alt = attr.alt or alt

    attrs = [
        build_document_attr_image_size(w, h).hex(),
        build_document_attr_custom_emoji(
            alt,
            set_id,
            set_access_hash,
            free=True,
            text_color=text_color,
        ).hex(),
    ]
    return {
        'id': int(doc.id),
        'access_hash': _to_i64(doc.access_hash),
        'set_id': set_id,
        'file_path': str(path),
        'mime_type': doc.mime_type or 'image/webp',
        'size': int(doc.size or 0),
        'width': w,
        'height': h,
        'alt': alt,
        'file_reference': doc.file_reference or b'\x00' * 8,
        'date': int(doc.date.timestamp()) if hasattr(doc.date, 'timestamp')
        else int(time.time()),
        'attributes_json': json.dumps(attrs),
        'sort_order': sort_order,
    }


async def _import_via_telethon(storage, doc_id: int) -> Optional[dict[str, Any]]:
    from telethon import TelegramClient
    from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
    from telethon.tl.types import InputDocumentFileLocation
    from tools.import_sticker_sets import _download_document

    ensure_telethon_env()
    api_id, api_hash = telethon_credentials()
    session_path = str(Path(_SESSION).with_suffix(''))
    async with TelegramClient(
        session_path,
        api_id,
        api_hash,
        connection_retries=5,
        retry_delay=2,
        request_retries=3,
    ) as client:
        if not await client.is_user_authorized():
            log.warning('Telethon session is not authorized; cannot import emoji %s', doc_id)
            return None

        result = await client(GetCustomEmojiDocumentsRequest(document_id=[int(doc_id)]))
        if not result:
            log.warning('Telegram returned no document for custom emoji %s', doc_id)
            return None

        doc = result[0]
        set_id, set_access_hash = _ensure_banner_set(storage)
        mime = doc.mime_type or 'image/webp'
        out_dir = Path(config.STICKERS_DATA_DIR) / str(set_id)
        path = out_dir / f'{doc.id}{_doc_extension(mime)}'
        if not path.is_file() or path.stat().st_size <= 0:
            if not await _download_document(
                    client,
                    doc,
                    path,
                    short_name=BANNER_SET_SHORT,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                await client.download_file(
                    InputDocumentFileLocation(
                        id=doc.id,
                        access_hash=doc.access_hash,
                        file_reference=doc.file_reference or b'',
                        thumb_size='',
                    ),
                    file=str(path),
                )
        if not path.is_file() or path.stat().st_size <= 0:
            return None

        row = _pack_doc_row(
            doc,
            path,
            set_id=set_id,
            set_access_hash=set_access_hash,
        )
        storage.upsert_sticker_document(row)
        _ensure_banner_set(storage)
        return {'alt': row['alt'], 'documentId': int(doc.id)}


def _import_via_telethon_sync(storage, doc_id: int) -> Optional[dict[str, Any]]:
    try:
        return asyncio.run(_import_via_telethon(storage, doc_id))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_import_via_telethon(storage, doc_id))
        finally:
            loop.close()


def _find_local_document(doc_id: int):
    """Locate an already downloaded custom-emoji file by document id."""
    data_dir = Path(config.STICKERS_DATA_DIR)
    if not data_dir.is_dir():
        return None
    for ext in ('.tgs', '.webm', '.webp'):
        for path in data_dir.glob(f'*/{int(doc_id)}{ext}'):
            try:
                if path.is_file() and path.stat().st_size > 0:
                    return path
            except OSError:
                continue
    return None


def ensure_custom_emoji_document(storage, doc_id: int) -> Optional[dict[str, Any]]:
    """Ensure custom emoji document exists locally; import from Telegram if needed."""
    doc_id = int(doc_id)
    existing = storage.get_sticker_document_by_id(doc_id)
    if existing and existing.get('file_path'):
        path = Path(str(existing['file_path']))
        if path.is_file() and path.stat().st_size > 0:
            return {
                'alt': existing.get('alt') or '⭐',
                'documentId': doc_id,
            }

    local_path = _find_local_document(doc_id)
    if local_path is not None:
        mime = {
            '.tgs': 'application/x-tgsticker',
            '.webm': 'video/webm',
            '.webp': 'image/webp',
        }[local_path.suffix.lower()]
        try:
            row = _store_emoji_document(
                storage,
                doc_id=doc_id,
                access_hash=_stable_access_hash(doc_id),
                path=local_path,
                mime=mime,
                alt='⭐',
                size=int(local_path.stat().st_size),
            )
            log.info('Registered local emoji document %s from %s',
                     doc_id, local_path)
            return row
        except Exception as exc:
            log.warning('Local emoji registration failed for %s: %s',
                        doc_id, exc)

    errors: list[str] = []
    try:
        imported = _import_via_bot_api(storage, doc_id)
        if imported:
            return imported
    except Exception as exc:
        errors.append(f'bot:{exc}')
        log.warning('Bot API emoji import failed for %s: %s', doc_id, exc)

    try:
        imported = _import_via_telethon_sync(storage, doc_id)
        if imported:
            return imported
    except Exception as exc:
        errors.append(f'telethon:{exc}')
        log.warning('Telethon emoji import failed for %s: %s', doc_id, exc)

    if errors:
        log.warning('Emoji import failed for %s: %s', doc_id, '; '.join(errors))
    return None
