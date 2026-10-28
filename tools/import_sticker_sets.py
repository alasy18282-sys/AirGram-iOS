#!/usr/bin/env python3
"""Import sticker / emoji sets from real Telegram into MyTelegram server DB.

Usage:
  pip install telethon
  python tools/import_sticker_sets.py --session mysession --set HotCherry
  python tools/import_sticker_sets.py --session mysession --set https://t.me/addemoji/MemeBundle --emoji
  python tools/import_sticker_sets.py --session mysession --set AnimatedEmojies --emoji
  python tools/import_sticker_sets.py --session mysession --all-installed

API credentials load from frame_verification/telethon_env (.telegram_api.env).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from db.storage import Storage
from frame_verification.telethon_env import telethon_credentials
from handlers.stickers import (
    build_document_attr_image_size,
    build_document_attr_sticker,
    build_document_attr_custom_emoji,
    build_document_attr_filename,
)

try:
    from telethon import TelegramClient
    from telethon.tl.functions.messages import GetStickerSetRequest
    from telethon.tl.types import (
        DocumentAttributeCustomEmoji,
        DocumentAttributeFilename,
        DocumentAttributeImageSize,
        DocumentAttributeSticker,
        InputDocumentFileLocation,
        InputStickerSetShortName,
        PhotoPathSize,
    )
except ImportError:
    print('Install telethon: pip install telethon', file=sys.stderr)
    sys.exit(1)


_SET_LINK_RE = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me)/'
    r'(addstickers|addemoji)/([a-zA-Z0-9_.]+)',
    re.IGNORECASE)


def parse_set_reference(name: str, force_emoji: bool = False) -> tuple[str, bool]:
    """Return (short_name, is_custom_emoji_pack)."""
    name = name.strip()
    m = _SET_LINK_RE.search(name)
    if m:
        kind, short_name = m.group(1).lower(), m.group(2)
        return short_name, kind == 'addemoji' or force_emoji
    return name.lstrip('@'), force_emoji


def _attr_bytes(doc, set_id: int, set_access_hash: int, emoji: bool,
                text_color: bool = False) -> list[str]:
    out: list[str] = []
    w, h = 512, 512
    alt = ''
    free = False
    for attr in doc.attributes or []:
        if isinstance(attr, DocumentAttributeImageSize):
            w, h = attr.w, attr.h
        elif isinstance(attr, DocumentAttributeCustomEmoji):
            alt = attr.alt or alt
            free = bool(attr.free)
            text_color = bool(attr.text_color)
        elif isinstance(attr, DocumentAttributeSticker):
            alt = attr.alt or alt
    out.append(build_document_attr_image_size(w, h).hex())
    mime = doc.mime_type or ''
    if emoji:
        out.append(build_document_attr_custom_emoji(
            alt, set_id, set_access_hash,
            free=free, text_color=text_color).hex())
        if 'tgsticker' in mime:
            fname = 'AnimatedSticker.tgs'
            for attr in doc.attributes or []:
                if isinstance(attr, DocumentAttributeFilename):
                    fname = attr.file_name or fname
                    break
            out.append(build_document_attr_filename(fname).hex())
    else:
        out.append(build_document_attr_sticker(
            alt, set_id, set_access_hash).hex())
    return out


def _set_flags(telethon_set, emoji: bool) -> int:
    flags = 0
    if getattr(telethon_set, 'official', False):
        flags |= 2
    if getattr(telethon_set, 'masks', False):
        flags |= 4
    if getattr(telethon_set, 'text_color', False):
        flags |= 512
    if getattr(telethon_set, 'emojis', False) or emoji:
        flags |= 128
    return flags


DEFAULT_SYSTEM_SETS = [
    ('AnimatedEmojies', True),
    ('EmojiAnimations', True),
    ('StatusPack', True),
    ('EmojiGenericAnimations', True),
    ('GiftsPremium', False),
    ('GiftsTons', False),
    ('Topics', True),
]

# Telethon input types for sets without a public short_name link.
SPECIAL_INPUT_SETS = [
    ('GiftsTons', 'InputStickerSetTonGifts'),
]

# Dice stickersets — fetched via inputStickerSetDice(emoticon).
DICE_IMPORT_EMOTICONS = ('🎲', '🎯', '🏀', '⚽', '🎰', '🎳')

_DOWNLOAD_RETRIES = 6
_DOWNLOAD_BACKOFF_SEC = 2.0


def _doc_extension(mime: str) -> str:
    mime = mime or 'image/webp'
    if 'tgsticker' in mime:
        return '.tgs'
    if mime == 'video/webm':
        return '.webm'
    return '.webp'


async def _refresh_document(client: TelegramClient, short_name: str, doc_id: int):
    """Re-fetch sticker set metadata to renew file_reference."""
    result = await client(GetStickerSetRequest(
        stickerset=InputStickerSetShortName(short_name=short_name),
        hash=0,
    ))
    for doc in result.documents:
        if doc.id == doc_id:
            return doc
    return None


async def _download_document(
        client: TelegramClient,
        doc,
        path: Path,
        *,
        short_name: str,
        attempt: int = 0) -> bool:
    if path.is_file() and path.stat().st_size > 0:
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.part')
    if tmp.is_file():
        tmp.unlink(missing_ok=True)

    last_exc: Exception | None = None
    current = doc
    for try_no in range(_DOWNLOAD_RETRIES):
        if try_no:
            await asyncio.sleep(_DOWNLOAD_BACKOFF_SEC * try_no)
        try:
            saved = await client.download_media(current, file=str(tmp))
            target = Path(saved) if saved else tmp
            if not target.is_file() or target.stat().st_size <= 0:
                raise OSError(f'empty download for document {current.id}')
            if target != path:
                target.replace(path)
            if tmp.is_file() and tmp != path:
                tmp.unlink(missing_ok=True)
            return True
        except Exception as exc:
            last_exc = exc
            err = str(exc).lower()
            if 'file_reference' in err or 'file reference' in err:
                refreshed = await _refresh_document(client, short_name, current.id)
                if refreshed is not None:
                    current = refreshed
            if tmp.is_file():
                tmp.unlink(missing_ok=True)

    try:
        from telethon.tl.types import InputDocumentFileLocation
        await client.download_file(
            InputDocumentFileLocation(
                id=current.id,
                access_hash=current.access_hash,
                file_reference=current.file_reference or b'',
                thumb_size='',
            ),
            file=str(tmp),
        )
        if tmp.is_file() and tmp.stat().st_size > 0:
            tmp.replace(path)
            return True
    except Exception as exc:
        last_exc = exc
        if tmp.is_file():
            tmp.unlink(missing_ok=True)

    print(
        f'ERROR: download failed for document {current.id} '
        f'(attempt {attempt + 1}): {last_exc}',
        file=sys.stderr,
    )
    return False


async def import_special_set(client: TelegramClient, storage: Storage,
                           label: str, input_cls_name: str,
                           *, emoji: bool = False) -> None:
    from telethon.tl import types as tt
    input_cls = getattr(tt, input_cls_name, None)
    if input_cls is None:
        raise RuntimeError(f'Telethon has no {input_cls_name}')
    result = await client(GetStickerSetRequest(stickerset=input_cls(), hash=0))
    await _import_sticker_set_result(
        client, storage, result, emoji=emoji, label=label)


async def import_dice_set(client: TelegramClient, storage: Storage,
                          emoticon: str) -> None:
    from telethon.tl.types import InputStickerSetDice
    result = await client(GetStickerSetRequest(
        stickerset=InputStickerSetDice(emoticon=emoticon),
        hash=0,
    ))
    await _import_sticker_set_result(
        client, storage, result, emoji=False, label=f'dice:{emoticon}')


async def _import_sticker_set_result(client: TelegramClient, storage: Storage,
                                     result, *, emoji: bool,
                                     label: str = '') -> None:
    ss = result.set
    emoji = emoji or bool(getattr(ss, 'emojis', False))
    set_id = ss.id
    access_hash = ss.access_hash
    flags = _set_flags(ss, emoji)
    text_color = bool(flags & 512)
    doc_hash = int(hashlib.md5(
        f'{set_id}:{ss.count}:{ss.hash}'.encode()).hexdigest()[:8], 16)

    out_dir = Path(config.STICKERS_DATA_DIR) / str(set_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    storage.upsert_sticker_set(
        set_id, access_hash, ss.short_name, ss.title,
        flags, ss.count, doc_hash)

    packs: list[tuple[str, list[int]]] = []
    for pack in result.packs:
        packs.append((pack.emoticon, list(pack.documents)))

    imported = 0
    total = len(result.documents)
    for sort_order, doc in enumerate(result.documents):
        index = sort_order + 1
        mime = doc.mime_type or 'image/webp'
        path = out_dir / f'{doc.id}{_doc_extension(mime)}'
        if not path.is_file():
            print(f'  [{index}/{total}] downloading {doc.id} ...', flush=True)
            if not await _download_document(
                    client, doc, path, short_name=ss.short_name):
                continue
        thumb_path = out_dir / f'{doc.id}_thumb.webp'
        path_bytes = b''
        for thumb in doc.thumbs or []:
            if isinstance(thumb, PhotoPathSize) and thumb.bytes:
                path_bytes = thumb.bytes
                break
        if not thumb_path.is_file():
            try:
                await client.download_file(
                    InputDocumentFileLocation(
                        id=doc.id,
                        access_hash=doc.access_hash,
                        file_reference=doc.file_reference or b'',
                        thumb_size='m',
                    ),
                    file=str(thumb_path),
                )
            except Exception:
                if thumb_path.is_file():
                    thumb_path.unlink(missing_ok=True)
        attrs = _attr_bytes(doc, set_id, access_hash, bool(flags & 128), text_color)
        alt = ''
        for attr in doc.attributes or []:
            if isinstance(attr, (DocumentAttributeSticker, DocumentAttributeCustomEmoji)):
                alt = attr.alt or ''
        storage.upsert_sticker_document({
            'id': doc.id,
            'access_hash': doc.access_hash,
            'set_id': set_id,
            'file_path': str(path),
            'mime_type': mime,
            'size': doc.size,
            'width': 512,
            'height': 512,
            'alt': alt,
            'file_reference': doc.file_reference or b'\x00' * 8,
            'date': doc.date.timestamp() if hasattr(doc.date, 'timestamp') else int(time.time()),
            'attributes_json': __import__('json').dumps(attrs),
            'sort_order': sort_order,
            'thumb_path': str(thumb_path) if thumb_path.is_file() else '',
            'thumb_path_bytes': path_bytes,
        })
        imported += 1

    if imported == 0:
        raise RuntimeError(
            f'No documents downloaded for {ss.short_name}. '
            'Check network/VPN or Telegram session access to premium emoji files.')

    # Keep only packs that reference imported documents.
    imported_ids = {
        doc['id'] for doc in storage.get_sticker_documents(set_id)
    }
    packs = [
        (emoticon, [did for did in ids if did in imported_ids])
        for emoticon, ids in packs
    ]
    packs = [(emoticon, ids) for emoticon, ids in packs if ids]

    storage.replace_sticker_packs(set_id, packs)
    flags = int(flags) & ~1  # not installed by default
    storage.upsert_sticker_set(
        set_id, access_hash, ss.short_name, ss.title,
        flags, imported, doc_hash)
    kind = 'emoji' if flags & 128 else 'sticker'
    print(f'Imported {kind} set @{ss.short_name} ({ss.title}): '
          f'{imported}/{total} documents')


async def import_set(client: TelegramClient, storage: Storage, short_name: str,
                     emoji: bool = False) -> None:
    short_name, emoji_from_url = parse_set_reference(short_name, emoji)
    result = await client(GetStickerSetRequest(
        stickerset=InputStickerSetShortName(short_name=short_name),
        hash=0,
    ))
    await _import_sticker_set_result(
        client, storage, result,
        emoji=emoji_from_url or emoji, label=short_name)


async def import_installed(client: TelegramClient, storage: Storage) -> None:
    from telethon.tl.functions.messages import GetAllStickersRequest
    all_stickers = await client(GetAllStickersRequest(hash=0))
    for ss in all_stickers.sets:
        await import_set(client, storage, ss.short_name)


async def main() -> None:
    parser = argparse.ArgumentParser(description='Import Telegram sticker sets')
    parser.add_argument('--session', required=True,
                        help='Telethon session name (without .session extension)')
    parser.add_argument('--set', action='append', default=[],
                        help='Sticker set short name (without @), repeatable')
    parser.add_argument('--emoji', action='store_true',
                        help='Import as custom/premium emoji pack (also auto-detected for t.me/addemoji/ links)')
    parser.add_argument('--all-installed', action='store_true',
                        help='Import all sticker sets installed in the session account')
    parser.add_argument('--defaults', action='store_true',
                        help='Import built-in system emoji/sticker sets')
    parser.add_argument('--dice', action='store_true',
                        help='Import animated dice stickersets (🎲🎯🏀⚽🎰🎳)')
    parser.add_argument('--featured-catalog', action='store_true',
                        help='Import config.FEATURED_EMOJI_CATALOG and mark as trending')
    args = parser.parse_args()

    api_id, api_hash = telethon_credentials()

    if not args.set and not args.all_installed and not args.defaults \
            and not args.featured_catalog and not args.dice:
        parser.error(
            'Provide --set NAME, --defaults, --dice, --featured-catalog '
            'and/or --all-installed')

    storage = Storage.open_tool()
    session_path = str(Path(args.session).with_suffix(''))
    async with TelegramClient(
            session_path, api_id, api_hash,
            connection_retries=10,
            retry_delay=3,
            request_retries=5) as client:
        if args.all_installed:
            await import_installed(client, storage)
        if args.defaults or args.dice:
            for emoticon in DICE_IMPORT_EMOTICONS:
                try:
                    await import_dice_set(client, storage, emoticon)
                except Exception as exc:
                    print(f'WARN: could not import dice {emoticon}: {exc}',
                          file=sys.stderr)
        if args.defaults:
            for short_name, is_emoji in DEFAULT_SYSTEM_SETS:
                try:
                    await import_set(client, storage, short_name, emoji=is_emoji)
                except Exception as exc:
                    print(f'WARN: could not import {short_name}: {exc}', file=sys.stderr)
            for label, input_cls in SPECIAL_INPUT_SETS:
                try:
                    await import_special_set(
                        client, storage, label, input_cls)
                except Exception as exc:
                    print(f'WARN: could not import {label}: {exc}', file=sys.stderr)
        for name in args.set:
            await import_set(client, storage, name, emoji=args.emoji)
        if args.featured_catalog:
            for name in config.FEATURED_EMOJI_CATALOG:
                try:
                    await import_set(
                        client, storage,
                        f'https://t.me/addemoji/{name}', emoji=True)
                except Exception as exc:
                    print(f'WARN: could not import {name}: {exc}', file=sys.stderr)
            storage.apply_featured_emoji_catalog(config.FEATURED_EMOJI_CATALOG)
            print(f'Featured catalog applied: {len(config.FEATURED_EMOJI_CATALOG)} packs')


if __name__ == '__main__':
    asyncio.run(main())
