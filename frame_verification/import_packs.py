from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

from db.storage import Storage
from frame_verification.telethon_env import ensure_telethon_env, telethon_credentials
from frame_verification.catalog import CATALOG_PATH
from tools.import_sticker_sets import import_set, parse_set_reference

MTG_ROOT = Path(__file__).resolve().parents[1]


def _pack_urls() -> list[str]:
    if not CATALOG_PATH.is_file():
        return []
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    urls: list[str] = []
    seen: set[str] = set()
    for item in data.values():
        pack = str(item.get("pack") or "").strip()
        if pack and pack not in seen:
            seen.add(pack)
            urls.append(pack)
    return urls


async def _import_missing_async() -> list[str]:
    ensure_telethon_env()
    api_id, api_hash = telethon_credentials()
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID/HASH not configured")

    from telethon import TelegramClient
    import os

    session = os.environ.get("FRAME_TELETHON_SESSION", str(MTG_ROOT / "mysession"))
    session_path = str(Path(session).with_suffix(""))

    storage = Storage.open_tool()
    storage._conn.execute("PRAGMA busy_timeout=120000")
    imported: list[str] = []

    async with TelegramClient(
        session_path,
        api_id,
        api_hash,
        connection_retries=10,
        retry_delay=3,
        request_retries=5,
    ) as client:
        for pack_url in _pack_urls():
            short_name, is_emoji = parse_set_reference(pack_url, force_emoji=True)
            await import_set(client, storage, short_name, emoji=is_emoji)
            imported.append(short_name)

    return imported


def import_missing_packs(max_attempts: int = 8) -> list[str]:
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return asyncio.run(_import_missing_async())
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_error = exc
            time.sleep(min(2 ** attempt, 15))
    if last_error:
        raise last_error
    return []
