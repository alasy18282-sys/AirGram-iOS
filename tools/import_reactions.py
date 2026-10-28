#!/usr/bin/env python3
"""Import message reactions from original Telegram/MyTelegram SQLite DB.

Examples:
  python3 tools/import_reactions.py --source-db /path/to/original.db
  python3 tools/import_reactions.py --source-db /path/to/original.db --clear
  python3 tools/import_reactions.py --source-db /path/to/original.db --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config


def ensure_target_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_reactions (
            scope_peer_id INTEGER NOT NULL,
            msg_id        INTEGER NOT NULL,
            actor_id      INTEGER NOT NULL,
            reaction_key  TEXT NOT NULL,
            big           INTEGER NOT NULL DEFAULT 0,
            date          INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope_peer_id, msg_id, actor_id, reaction_key)
        )
        """
    )
    conn.commit()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def encode_reaction_key(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        if not raw:
            return ""
        if raw.startswith("emoji:") or raw.startswith("custom:"):
            return raw
        return f"emoji:{raw}"
    if not isinstance(raw, dict):
        return ""
    rtype = str(raw.get("type") or "").strip().lower()
    if rtype == "emoji":
        emo = str(raw.get("emoticon") or "")
        return f"emoji:{emo}" if emo else ""
    if rtype == "custom":
        doc_id = int(raw.get("document_id") or 0)
        return f"custom:{doc_id}" if doc_id > 0 else ""
    # Fallback for Telegram-like payloads.
    if "emoticon" in raw:
        emo = str(raw.get("emoticon") or "")
        return f"emoji:{emo}" if emo else ""
    if "document_id" in raw:
        doc_id = int(raw.get("document_id") or 0)
        return f"custom:{doc_id}" if doc_id > 0 else ""
    return ""


def import_from_message_reactions(source: sqlite3.Connection) -> list[tuple]:
    cols = columns(source, "message_reactions")
    required = {"scope_peer_id", "msg_id", "actor_id", "reaction_key"}
    if not required.issubset(cols):
        raise RuntimeError(
            "source table message_reactions has incompatible schema "
            f"(missing: {sorted(required - cols)})"
        )
    sql = (
        "SELECT scope_peer_id, msg_id, actor_id, reaction_key, "
        + ("big" if "big" in cols else "0")
        + " AS big, "
        + ("date" if "date" in cols else "0")
        + " AS date "
        + "FROM message_reactions"
    )
    rows = source.execute(sql).fetchall()
    out: list[tuple] = []
    now = int(time.time())
    for row in rows:
        reaction_key = encode_reaction_key(row["reaction_key"])
        if not reaction_key:
            continue
        out.append(
            (
                int(row["scope_peer_id"]),
                int(row["msg_id"]),
                int(row["actor_id"]),
                reaction_key,
                1 if int(row["big"] or 0) else 0,
                int(row["date"] or now),
            )
        )
    return out


def import_from_messages_json(source: sqlite3.Connection) -> list[tuple]:
    cols = columns(source, "messages")
    # We only parse rich per-peer entries if recent_reactions are embedded in JSON.
    candidate_cols = [c for c in cols if "reaction" in c.lower()]
    if not candidate_cols:
        return []
    owner_col = "owner_id" if "owner_id" in cols else None
    peer_col = "peer_id" if "peer_id" in cols else None
    msg_col = "id" if "id" in cols else None
    date_col = "date" if "date" in cols else None
    if not owner_col or not peer_col or not msg_col:
        return []
    out: list[tuple] = []
    now = int(time.time())
    for payload_col in candidate_cols:
        try:
            rows = source.execute(
                f"SELECT {owner_col} AS owner_id, {peer_col} AS peer_id, "
                f"{msg_col} AS msg_id, "
                f"{date_col if date_col else '0'} AS msg_date, "
                f"{payload_col} AS payload "
                "FROM messages "
                f"WHERE {payload_col} IS NOT NULL AND {payload_col} != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            payload_raw = row["payload"]
            if not isinstance(payload_raw, str):
                continue
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            recent = payload.get("recent_reactions") or payload.get("reactions") or []
            if not isinstance(recent, list):
                continue
            owner_id = int(row["owner_id"])
            peer_id = int(row["peer_id"])
            scope_peer_id = peer_id if peer_id >= config.CHANNEL_ID_MIN else min(owner_id, peer_id)
            msg_id = int(row["msg_id"])
            fallback_date = int(row["msg_date"] or now)
            for item in recent:
                if not isinstance(item, dict):
                    continue
                actor_id = int(item.get("peer_id") or item.get("actor_id") or 0)
                if actor_id <= 0:
                    continue
                reaction_key = encode_reaction_key(item.get("reaction"))
                if not reaction_key:
                    continue
                out.append(
                    (
                        scope_peer_id,
                        msg_id,
                        actor_id,
                        reaction_key,
                        1 if item.get("big") else 0,
                        int(item.get("date") or fallback_date),
                    )
                )
    # deduplicate
    uniq = {}
    for row in out:
        uniq[(row[0], row[1], row[2], row[3])] = row
    return list(uniq.values())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import reactions from original Telegram/MyTelegram SQLite DB."
    )
    parser.add_argument("--source-db", required=True, help="Path to source SQLite DB")
    parser.add_argument(
        "--target-db",
        default=config.DB_PATH,
        help=f"Path to target SQLite DB (default: {config.DB_PATH})",
    )
    parser.add_argument("--clear", action="store_true", help="Clear target reactions before import")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print stats without writing")
    args = parser.parse_args()

    source_path = Path(args.source_db).expanduser().resolve()
    target_path = Path(args.target_db).expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit(f"Source DB does not exist: {source_path}")
    if source_path == target_path:
        raise SystemExit("Source and target DB must be different files")

    src = sqlite3.connect(str(source_path))
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(str(target_path))
    dst.row_factory = sqlite3.Row
    try:
        ensure_target_schema(dst)

        rows: list[tuple] = []
        source_mode = ""
        if table_exists(src, "message_reactions"):
            rows = import_from_message_reactions(src)
            source_mode = "table:message_reactions"
        elif table_exists(src, "messages"):
            rows = import_from_messages_json(src)
            source_mode = "table:messages(+reaction JSON)"
        else:
            raise SystemExit("No supported reaction source found in source DB")

        print(f"Source mode: {source_mode}")
        print(f"Prepared rows: {len(rows)}")
        if args.dry_run:
            return

        if args.clear:
            dst.execute("DELETE FROM message_reactions")

        dst.executemany(
            """
            INSERT OR REPLACE INTO message_reactions
            (scope_peer_id, msg_id, actor_id, reaction_key, big, date)
            VALUES (?,?,?,?,?,?)
            """,
            rows,
        )
        dst.commit()
        print(f"Imported rows: {len(rows)}")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
