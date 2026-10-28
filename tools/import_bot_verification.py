#!/usr/bin/env python3
"""Import bot verification icon pack and assign verification to a user.

Usage:
  python tools/import_bot_verification.py --session mysession \\
      --user @username --pack https://t.me/addemoji/MyPack --index 0 \\
      --organization "My Company"

  python tools/import_bot_verification.py --session mysession \\
      --user 12345 --pack AnimatedEmojies --index 2 --organization "Org" --bot @mybot

API credentials load from frame_verification/telethon_env (.telegram_api.env).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frame_verification.telethon_env import telethon_credentials
from db.storage import Storage
from tools.import_sticker_sets import import_set, parse_set_reference

try:
    from telethon import TelegramClient
except ImportError:
    print('Install telethon: pip install telethon', file=sys.stderr)
    sys.exit(1)


def resolve_user(storage: Storage, ref: str) -> dict:
    """Resolve target peer (user, bot, or channel) for verification."""
    import config
    ref = ref.strip()
    if ref.lstrip('-').isdigit():
        peer_id = int(ref)
        if peer_id >= int(config.CHANNEL_ID_MIN):
            channel = storage.get_channel(peer_id)
            if channel:
                return channel
        user = storage.get_user_by_id(peer_id)
        if user:
            return user
        raise SystemExit(f'Peer not found: {ref}')
    user = storage.get_user_by_username(ref)
    if user:
        return user
    channel = storage.get_channel_by_username(ref)
    if channel:
        return channel
    raise SystemExit(f'User/bot/channel not found: {ref}')


def resolve_bot(storage: Storage, ref: str | None) -> dict:
    if not ref:
        bot = storage.get_user_by_username('mybot')
        if bot:
            return bot
        raise SystemExit('Bot not found; pass --bot @username')
    ref = ref.strip()
    if ref.lstrip('-').isdigit():
        bot = storage.get_user_by_id(int(ref))
    else:
        bot = storage.get_user_by_username(ref)
    if not bot or not bot.get('is_bot'):
        raise SystemExit(f'Bot not found: {ref}')
    return bot


def pick_icon_document(storage: Storage, set_id: int, index: int) -> dict:
    docs = storage.get_sticker_documents(set_id)
    if not docs:
        raise SystemExit(f'No documents in set {set_id}; import the pack first')
    if index < 0 or index >= len(docs):
        raise SystemExit(
            f'Index {index} out of range (pack has {len(docs)} emoji, '
            f'use 0..{len(docs) - 1})')
    return docs[index]


async def main() -> None:
    parser = argparse.ArgumentParser(
        description='Import bot verification icon and assign to user')
    parser.add_argument('--session', required=True,
                        help='Telethon session name (without .session extension)')
    parser.add_argument('--user', required=True,
                        help='Target user @username or numeric id')
    parser.add_argument('--pack', default='',
                        help='Emoji pack short name or t.me/addemoji/ link')
    parser.add_argument('--index', type=int, default=0,
                        help='Emoji index in pack (0-based, default 0)')
    parser.add_argument('--organization', default='',
                        help='Organization name for verification badge text')
    parser.add_argument('--bot', default='',
                        help='Verifier bot @username or id (default: @mybot)')
    parser.add_argument('--description', default='',
                        help='Custom description (default: auto-generated Russian text)')
    parser.add_argument('--clear', action='store_true',
                        help='Remove verification from user instead of setting')
    args = parser.parse_args()

    api_id, api_hash = telethon_credentials()

    storage = Storage.open_tool()
    user = resolve_user(storage, args.user)

    if args.clear:
        storage.delete_bot_verification(user['id'])
        print(f'Removed bot verification from user {user["id"]}')
        return

    if not args.pack or not args.organization:
        raise SystemExit('--pack and --organization are required unless using --clear')

    bot = resolve_bot(storage, args.bot or None)
    short_name, is_emoji = parse_set_reference(args.pack, force_emoji=True)

    session_path = str(Path(args.session).with_suffix(''))
    async with TelegramClient(
            session_path, api_id, api_hash,
            connection_retries=10,
            retry_delay=3,
            request_retries=5) as client:
        await import_set(client, storage, short_name, emoji=is_emoji)

    row = storage._conn.execute(
        'SELECT id FROM sticker_sets WHERE short_name=?', (short_name,)
    ).fetchone()
    if not row:
        raise SystemExit(f'Sticker set {short_name} not found after import')
    set_id = int(row['id'])
    doc = pick_icon_document(storage, set_id, args.index)

    description = args.description.strip()
    if not description:
        description = (
            f'Аккаунт верифицирован организацией «{args.organization}».')

    storage.set_bot_verification(
        user['id'], bot['id'], doc['id'], args.organization, description)

    print(f'Bot verification set for user {user["id"]} (@{user.get("username") or "?"})')
    print(f'  Bot: {bot["id"]} (@{bot.get("username") or "?"})')
    print(f'  Icon: document {doc["id"]} (pack @{short_name}, index {args.index})')
    print(f'  Text: {description}')


if __name__ == '__main__':
    asyncio.run(main())
