#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from db.storage import Storage
from .service import grant_verification, lookup_username


def list_pack(short_name: str) -> dict:
    storage = Storage.open_tool()
    row = storage._conn.execute(
        "SELECT id, title FROM sticker_sets WHERE short_name=?",
        (short_name.lstrip("@"),),
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"Pack {short_name!r} not found"}
    docs = storage.get_sticker_documents(int(row["id"]))
    return {
        "ok": True,
        "short_name": short_name.lstrip("@"),
        "title": row["title"],
        "emoji": [
            {"index": i, "alt": d.get("alt") or "", "id": int(d["id"])}
            for i, d in enumerate(docs)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Frame verification bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    lookup = sub.add_parser("lookup")
    lookup.add_argument("username")
    lookup.add_argument("--verification-type", default=None)
    lookup.add_argument("--plan", default=None)

    grant = sub.add_parser("grant")
    grant.add_argument("--username", required=True)
    grant.add_argument("--verification-type", required=True)
    grant.add_argument("--plan", default="forever")
    grant.add_argument("--payment-id", required=True)
    grant.add_argument("--stars", type=int, required=True)
    grant.add_argument("--buyer-id", type=int, default=None)
    grant.add_argument("--promo", default=None)

    pack = sub.add_parser("list-pack")
    pack.add_argument("short_name")

    args = parser.parse_args()

    if args.command == "lookup":
        result = lookup_username(
            args.username,
            verification_type=args.verification_type,
            plan=args.plan,
        )
    elif args.command == "list-pack":
        result = list_pack(args.short_name)
    else:
        result = grant_verification(
            username=args.username,
            verification_type=args.verification_type,
            plan=args.plan,
            telegram_payment_id=args.payment_id,
            stars_amount=args.stars,
            buyer_telegram_id=args.buyer_id,
            promo=args.promo,
        )

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    # Always exit 0 on handled business results. Node bridges treat non-zero
    # exits as transport failures and otherwise show a fake "network/DB" error.


if __name__ == "__main__":
    main()
