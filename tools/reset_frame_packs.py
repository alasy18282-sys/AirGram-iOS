#!/usr/bin/env python3
"""Delete verification emoji packs from DB and re-import from Telegram."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

MTG_ROOT = Path(__file__).resolve().parents[1]
if str(MTG_ROOT) not in sys.path:
    sys.path.insert(0, str(MTG_ROOT))

import config
from frame_verification.catalog import CATALOG_PATH
from frame_verification.telethon_env import ensure_telethon_env
from frame_verification.import_packs import import_missing_packs
from tools.import_sticker_sets import parse_set_reference

def verification_pack_names() -> list[str]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    names: list[str] = []
    seen: set[str] = set()
    for item in data.values():
        pack = str(item.get("pack") or "").strip()
        if not pack:
            continue
        short_name, _ = parse_set_reference(pack, force_emoji=True)
        if short_name not in seen:
            seen.add(short_name)
            names.append(short_name)
    return names


def delete_verification_packs() -> list[str]:
    names = verification_pack_names()
    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    deleted: list[str] = []

    try:
        for short_name in names:
            row = conn.execute(
                "SELECT id FROM sticker_sets WHERE short_name=?",
                (short_name,),
            ).fetchone()
            if not row:
                print(f"skip {short_name} (not in db)")
                continue

            set_id = int(row["id"])
            conn.execute("DELETE FROM sticker_packs WHERE set_id=?", (set_id,))
            conn.execute("DELETE FROM sticker_documents WHERE set_id=?", (set_id,))
            conn.execute("DELETE FROM sticker_sets WHERE id=?", (set_id,))
            conn.commit()

            pack_dir = Path(config.STICKERS_DATA_DIR) / str(set_id)
            if pack_dir.is_dir():
                shutil.rmtree(pack_dir)

            deleted.append(short_name)
            print(f"deleted {short_name} (set_id={set_id})")
    finally:
        conn.close()

    return deleted


def main() -> None:
    ensure_telethon_env()
    print("Removing verification packs...")
    removed = delete_verification_packs()
    if removed:
        print("Removed:", ", ".join(removed))
    else:
        print("Nothing to remove.")

    print("Re-importing from Telegram...")
    imported = import_missing_packs()
    print("Imported:", ", ".join(imported) if imported else "none")


if __name__ == "__main__":
    main()
