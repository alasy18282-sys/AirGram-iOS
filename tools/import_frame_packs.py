#!/usr/bin/env python3
"""Import all emoji packs used by frame/verifications.json into MyTelegram DB."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.storage import Storage
from frame_verification.catalog import CATALOG_PATH
from frame_verification.telethon_env import ensure_telethon_env, telethon_credentials
from tools.import_sticker_sets import import_set, parse_set_reference

try:
    from telethon import TelegramClient
except ImportError:
    print("Install telethon: pip install telethon", file=sys.stderr)
    sys.exit(1)

SESSION = os.environ.get("FRAME_TELETHON_SESSION", str(ROOT / "mysession"))


def collect_pack_urls() -> list[str]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    urls: list[str] = []
    seen: set[str] = set()
    for item in data.values():
        pack = str(item.get("pack") or "").strip()
        if not pack or pack in seen:
            continue
        seen.add(pack)
        urls.append(pack)
    return urls


async def main() -> None:
    ensure_telethon_env()
    api_id, api_hash = telethon_credentials()

    storage = Storage.open_tool()
    session_path = str(Path(SESSION).with_suffix(""))
    packs = collect_pack_urls()

    async with TelegramClient(
        session_path,
        api_id,
        api_hash,
        connection_retries=10,
        retry_delay=3,
        request_retries=5,
    ) as client:
        for pack_url in packs:
            short_name, is_emoji = parse_set_reference(pack_url, force_emoji=True)
            row = storage._conn.execute(
                "SELECT id FROM sticker_sets WHERE short_name=?",
                (short_name,),
            ).fetchone()
            if row:
                print(f"skip {short_name} (already imported)")
                continue
            print(f"import {short_name} ...")
            await import_set(client, storage, short_name, emoji=is_emoji)
            print(f"ok {short_name}")

    rows = storage._conn.execute(
        "SELECT short_name FROM sticker_sets ORDER BY short_name"
    ).fetchall()
    print("packs in db:", [r["short_name"] for r in rows])


if __name__ == "__main__":
    asyncio.run(main())
