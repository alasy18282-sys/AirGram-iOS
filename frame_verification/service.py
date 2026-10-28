from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

MTG_ROOT = Path(__file__).resolve().parents[1]
if str(MTG_ROOT) not in sys.path:
    sys.path.insert(0, str(MTG_ROOT))

import config
from db.storage import Storage
from frame_verification.telethon_env import ensure_telethon_env, telethon_credentials
from frame_verification.admins import is_frame_admin
from frame_verification.catalog import (
    get_plan_label,
    get_verification,
    get_verification_description,
    get_verification_price,
    is_admin_only_verification,
    is_timed_verification,
    load_catalog,
    resolve_icon_index,
)
from frame_verification.push import enqueue_verification_push, purge_expired_and_notify

VERIFIER_BOT = os.environ.get("FRAME_VERIFIER_BOT", "mybot")
TELETHON_SESSION = os.environ.get(
    "FRAME_TELETHON_SESSION",
    str(MTG_ROOT / "mysession"),
)

PLAN_SECONDS: dict[str, Optional[int]] = {
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "year": 365 * 86400,
    "forever": None,
}

PLAN_LABELS = {
    "day": "1 день",
    "week": "1 неделя",
    "month": "1 месяц",
    "year": "1 год",
    "forever": "Навсегда",
}


def _sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def normalize_transaction_ref(transaction_ref: str) -> str:
    ref = transaction_ref.strip().strip("`\"'")
    match = re.search(r"stx[A-Za-z0-9_-]+", ref)
    if match:
        return match.group(0)
    return ref


def _pack_short_name(pack_url: str) -> str:
    from tools.import_sticker_sets import parse_set_reference

    short_name, _ = parse_set_reference(pack_url, force_emoji=True)
    return short_name


def _organization_to_verification_id(organization: str) -> str | None:
    org = (organization or "").strip()
    if not org:
        return None
    for vid, item in load_catalog().items():
        if item.get("organization") == org:
            return vid
    return None


def _verification_state(verification: Optional[dict]) -> dict[str, Any]:
    if not verification:
        return {
            "verified": False,
            "verification_forever": False,
            "verification_expires_at": None,
            "verification_company": None,
            "owned_forever": None,
            "can_purchase": True,
        }

    expires_at = verification.get("expires_at")
    now = int(time.time())
    company = verification.get("company")

    if expires_at is not None and int(expires_at) <= now:
        return {
            "verified": False,
            "verification_forever": False,
            "verification_expires_at": int(expires_at),
            "verification_company": company,
            "owned_forever": None,
            "can_purchase": True,
        }

    forever = expires_at is None
    owned_forever = _organization_to_verification_id(company) if forever else None
    return {
        "verified": True,
        "verification_forever": forever,
        "verification_expires_at": int(expires_at) if expires_at else None,
        "verification_company": company,
        "owned_forever": owned_forever,
        "can_purchase": True,
    }


def compute_new_expires_at(
    plan: str,
    existing_verification: Optional[dict],
    organization: str,
    now: int | None = None,
) -> tuple[Optional[int], str]:
    now = now if now is not None else int(time.time())
    plan_seconds = PLAN_SECONDS.get(plan)
    if plan_seconds is None and plan != "forever":
        raise ValueError(f"Unknown plan: {plan}")

    if existing_verification:
        current_company = (existing_verification.get("company") or "").strip()
        current_expires = existing_verification.get("expires_at")
        same_org = current_company == organization

        if same_org and current_expires is None:
            raise ValueError(
                "У пользователя уже вечная верификация этого типа. Покупка недоступна."
            )

        if same_org and current_expires is not None:
            if plan == "forever":
                return None, "upgrade_forever"
            base = max(now, int(current_expires))
            return base + int(plan_seconds), "extend"

    if plan == "forever":
        return None, "new_forever"
    return now + int(plan_seconds), "new"


def can_purchase_verification(
    verification: Optional[dict],
    verification_type: str,
    plan: str | None = None,
    now: int | None = None,
) -> bool:
    vdef = get_verification(verification_type)
    if not vdef:
        return False

    if is_timed_verification(verification_type):
        if plan and plan not in (vdef.get("plans") or {}):
            return False
        if not verification:
            return True

        now = now if now is not None else int(time.time())
        expires_at = verification.get("expires_at")
        if expires_at is not None and int(expires_at) <= now:
            return True

        company = (verification.get("company") or "").strip()
        if company == vdef["organization"] and expires_at is None:
            return plan != "forever"
        return True

    if not verification:
        return True

    now = now if now is not None else int(time.time())
    expires_at = verification.get("expires_at")
    if expires_at is not None and int(expires_at) <= now:
        return True

    company = (verification.get("company") or "").strip()
    if expires_at is None and company == vdef["organization"]:
        return False

    return True


def _ensure_schema_conn(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "ALTER TABLE bot_verifications ADD COLUMN expires_at INTEGER"
        )
    except sqlite3.OperationalError:
        pass

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS frame_verification_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_payment_id TEXT UNIQUE NOT NULL,
            buyer_telegram_id INTEGER,
            username TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            stars_amount INTEGER NOT NULL,
            promo TEXT,
            expires_at INTEGER,
            created_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            refunded_at INTEGER
        )
        """
    )
    try:
        conn.execute(
            "ALTER TABLE frame_verification_orders ADD COLUMN refunded_at INTEGER"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE frame_verification_orders ADD COLUMN verification_type TEXT"
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()


def _ensure_schema(storage: Storage) -> None:
    _ensure_schema_conn(storage._conn)


def _resolve_bot(storage: Storage) -> dict:
    bot = storage.get_user_by_username(VERIFIER_BOT)
    if not bot or not bot.get("is_bot"):
        raise ValueError(f"Verifier bot not found: {VERIFIER_BOT}")
    return bot


def _get_verification_icon_id(
    storage: Storage,
    pack_short_name: str,
    icon_index: int,
) -> int:
    row = storage._conn.execute(
        "SELECT id FROM sticker_sets WHERE short_name=?",
        (pack_short_name,),
    ).fetchone()
    if not row:
        raise ValueError(
            f"Emoji pack {pack_short_name!r} is not imported. "
            "Run import_bot_verification.py first."
        )

    docs = storage.get_sticker_documents(int(row["id"]))
    if not docs:
        raise ValueError(f"No documents in pack {pack_short_name!r}")

    if icon_index < 0 or icon_index >= len(docs):
        alts = ", ".join(
            f"{i}:{(d.get('alt') or '?')}" for i, d in enumerate(docs[:20])
        )
        raise ValueError(
            f"Icon index {icon_index} out of range (pack has {len(docs)} emoji). "
            f"Available: {alts}"
        )
    return int(docs[icon_index]["id"])


def _resolve_verification_icon_id(storage: Storage, vdef: dict) -> int:
    """Icon document for a verification type.

    ``documentId`` pins the exact badge, so the emoji pack does not have to be
    imported first (the DB may have been recreated after a reset).  Legacy
    catalog entries without it keep using pack + index.
    """
    doc_id = int(vdef.get("documentId") or 0)
    if doc_id:
        try:
            from shop_webapp.emoji_import import ensure_custom_emoji_document
            ensure_custom_emoji_document(storage, doc_id)
        except Exception:
            pass
        return doc_id

    pack = str(vdef.get("pack") or "")
    if not pack:
        raise ValueError("Verification has neither documentId nor pack")
    pack_short_name = _pack_short_name(pack)
    try:
        return _get_verification_icon_id(
            storage, pack_short_name, resolve_icon_index(vdef))
    except ValueError:
        from frame_verification.import_packs import import_missing_packs
        import_missing_packs()
        asyncio.run(_import_pack_if_needed(storage, pack))
        return _get_verification_icon_id(
            storage, pack_short_name, resolve_icon_index(vdef))


async def _import_pack_if_needed(storage: Storage, pack_url: str) -> str:
    short_name = _pack_short_name(pack_url)
    row = storage._conn.execute(
        "SELECT id FROM sticker_sets WHERE short_name=?",
        (short_name,),
    ).fetchone()
    if row:
        return short_name

    ensure_telethon_env()
    api_id, api_hash = telethon_credentials()
    if not api_id or not api_hash:
        raise ValueError(
            f"Pack {short_name!r} missing and TELEGRAM_API_ID/HASH not set"
        )

    from telethon import TelegramClient
    from tools.import_sticker_sets import import_set, parse_set_reference

    _, is_emoji = parse_set_reference(pack_url, force_emoji=True)
    session_path = str(Path(TELETHON_SESSION).with_suffix(""))

    async with TelegramClient(
        session_path,
        api_id,
        api_hash,
        connection_retries=10,
        retry_delay=3,
        request_retries=5,
    ) as client:
        await import_set(client, storage, short_name, emoji=is_emoji)

    return short_name


def _peer_display_name(peer: dict) -> str:
    if peer.get("peer_type") == "channel":
        return (peer.get("title") or f"@{peer['username']}").strip()
    full = " ".join(
        part for part in (peer.get("first_name"), peer.get("last_name")) if part
    ).strip()
    return full or f"@{peer['username']}"


def _resolve_verification_peer(storage: Storage, username: str) -> Optional[dict]:
    """Resolve @username to a user, bot, or public channel on this server."""
    normalized = username.strip().lstrip("@").lower()
    if not normalized:
        return None
    user = storage.get_user_by_username(normalized)
    if user:
        return {
            "peer_type": "bot" if user.get("is_bot") else "user",
            "id": int(user["id"]),
            "username": normalized,
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
            "title": "",
        }
    channel = storage.get_channel_by_username(normalized)
    if channel:
        return {
            "peer_type": "channel",
            "id": int(channel["id"]),
            "username": normalized,
            "first_name": "",
            "last_name": "",
            "title": channel.get("title") or "",
        }
    return None


def lookup_username(
    username: str,
    verification_type: str | None = None,
    plan: str | None = None,
) -> dict[str, Any]:
    normalized = username.strip().lstrip("@").lower()
    if not normalized:
        return {"ok": False, "error": "Введите username."}

    storage = Storage.open_tool()
    peer = _resolve_verification_peer(storage, normalized)
    if not peer:
        return {
            "ok": False,
            "error": (
                "Username не найден на сервере. "
                "Сначала зарегистрируйте аккаунт / создайте канал с этим @username."
            ),
        }

    verification = storage.get_bot_verification(peer["id"])
    display_name = _peer_display_name(peer)
    state = _verification_state(verification)

    if verification_type:
        state["can_purchase"] = can_purchase_verification(
            verification,
            verification_type,
            plan,
        )

    return {
        "ok": True,
        "username": normalized,
        "display": f"@{normalized}",
        "first_name": peer.get("first_name") or peer.get("title") or "",
        "last_name": peer.get("last_name") or "",
        "full_name": display_name,
        "user_id": peer["id"],
        "peer_type": peer["peer_type"],
        **state,
    }


def grant_verification(
    *,
    username: str,
    verification_type: str,
    telegram_payment_id: str,
    stars_amount: int,
    buyer_telegram_id: int | None = None,
    promo: str | None = None,
    plan: str = "forever",
    force: bool = False,
) -> dict[str, Any]:
    vdef = get_verification(verification_type)
    if not vdef:
        return {"ok": False, "error": f"Unknown verification: {verification_type}"}

    if is_admin_only_verification(verification_type) and not is_frame_admin(
            buyer_telegram_id):
        return {"ok": False, "error": "Эта верификация доступна только администраторам."}

    if is_timed_verification(verification_type):
        plans = vdef.get("plans") or {}
        if plan not in plans:
            return {"ok": False, "error": f"Unknown plan: {plan}"}
    else:
        plan = "forever"

    storage = Storage.open_tool()
    _ensure_schema(storage)
    purge_expired_and_notify(storage)

    normalized = username.strip().lstrip("@").lower()
    peer = _resolve_verification_peer(storage, normalized)
    if not peer:
        return {
            "ok": False,
            "error": f"@{normalized} не найден в базе MyTelegram (user/bot/channel).",
        }
    peer_id = int(peer["id"])

    now = int(time.time())
    existing = storage._conn.execute(
        "SELECT * FROM frame_verification_orders WHERE telegram_payment_id=?",
        (telegram_payment_id,),
    ).fetchone()
    if existing and existing["status"] == "completed":
        return {
            "ok": True,
            "duplicate": True,
            "order_id": int(existing["id"]),
            "user_id": int(existing["user_id"]),
            "plan": existing["plan"],
            "verification_type": existing["verification_type"] or verification_type,
        }

    if not existing:
        storage._conn.execute(
            """
            INSERT INTO frame_verification_orders
                (telegram_payment_id, buyer_telegram_id, username, user_id, plan,
                 stars_amount, promo, expires_at, created_at, status, verification_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'pending', ?)
            """,
            (
                telegram_payment_id,
                buyer_telegram_id,
                normalized,
                peer_id,
                plan,
                stars_amount,
                promo,
                now,
                verification_type,
            ),
        )
        storage._conn.commit()
    elif buyer_telegram_id and not existing["buyer_telegram_id"]:
        storage._conn.execute(
            """
            UPDATE frame_verification_orders
            SET buyer_telegram_id=?, username=?, user_id=?, plan=?,
                stars_amount=?, promo=?, verification_type=?
            WHERE telegram_payment_id=?
            """,
            (
                buyer_telegram_id,
                normalized,
                peer_id,
                plan,
                stars_amount,
                promo,
                verification_type,
                telegram_payment_id,
            ),
        )
        storage._conn.commit()

    def _mark_failed(error: str) -> dict[str, Any]:
        storage._conn.execute(
            """
            UPDATE frame_verification_orders
            SET status='failed'
            WHERE telegram_payment_id=? AND status != 'completed'
            """,
            (telegram_payment_id,),
        )
        storage._conn.commit()
        return {"ok": False, "error": error}

    existing_bv = storage.get_bot_verification(peer_id)
    if not force and not can_purchase_verification(existing_bv, verification_type, plan):
        return _mark_failed(
            f"У @{normalized} уже есть вечная верификация «{vdef['title']}». "
            "Покупка недоступна."
        )

    try:
        icon_id = _resolve_verification_icon_id(storage, vdef)
        bot = _resolve_bot(storage)
    except Exception as exc:
        return _mark_failed(str(exc))

    now = int(time.time())
    try:
        expires_at, grant_mode = compute_new_expires_at(
            plan,
            None if force else existing_bv if (
                existing_bv
                and (existing_bv.get("company") or "").strip() == vdef["organization"]
            ) else None,
            vdef["organization"],
            now,
        )
    except ValueError as exc:
        return _mark_failed(str(exc))

    storage.set_bot_verification(
        peer_id,
        bot["id"],
        icon_id,
        vdef["organization"],
        description=get_verification_description(vdef),
        expires_at=expires_at,
    )
    enqueue_verification_push(storage, peer_id)

    storage._conn.execute(
        """
        UPDATE frame_verification_orders
        SET buyer_telegram_id=?, username=?, user_id=?, plan=?, stars_amount=?,
            promo=?, expires_at=?, created_at=?, status='completed',
            verification_type=?
        WHERE telegram_payment_id=?
        """,
        (
            buyer_telegram_id,
            normalized,
            peer_id,
            plan,
            stars_amount,
            promo,
            expires_at,
            now,
            verification_type,
            telegram_payment_id,
        ),
    )
    storage._conn.commit()

    order_row = storage._conn.execute(
        "SELECT id FROM frame_verification_orders WHERE telegram_payment_id=?",
        (telegram_payment_id,),
    ).fetchone()
    order_id = int(order_row["id"]) if order_row else 0

    try:
        from shop_webapp.service import bump_top_donors_version
        bump_top_donors_version()
    except Exception:
        pass

    return {
        "ok": True,
        "duplicate": False,
        "order_id": order_id,
        "user_id": peer_id,
        "username": normalized,
        "peer_type": peer["peer_type"],
        "plan": plan,
        "verification_type": verification_type,
        "verification_title": vdef["title"],
        "expires_at": expires_at,
        "grant_mode": grant_mode,
        "icon_id": icon_id,
        "bot_id": bot["id"],
    }


def parse_invoice_payload(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid invoice payload: {exc}") from exc

    if isinstance(data, dict) and "u" in data:
        username = str(data["u"]).strip().lstrip("@").lower()
        verification_type = str(data.get("v") or "frame").strip()
        vdef = get_verification(verification_type) or {}
        if is_timed_verification(verification_type):
            plan = str(data.get("p") or "month").strip()
        else:
            plan = "forever"
        promo = data.get("c")
        if promo is not None:
            promo = str(promo).strip() or None
        return {
            "type": "frame_verification",
            "username": username,
            "verification_type": verification_type,
            "plan": plan,
            "promo": promo,
        }

    if data.get("type") == "frame_verification" and data.get("username"):
        if "verification_type" not in data and data.get("plan") in load_catalog():
            data["verification_type"] = data["plan"]
        if "verification_type" not in data:
            data["verification_type"] = "frame"
        vtype = data["verification_type"]
        if not is_timed_verification(vtype):
            data["plan"] = "forever"
        return data

    raise ValueError("Unsupported invoice type")


def get_order_by_transaction_ref(transaction_ref: str) -> Optional[dict[str, Any]]:
    ref = normalize_transaction_ref(transaction_ref)
    if not ref:
        return None

    conn = _sqlite()
    try:
        candidates = [ref]
        if ref.startswith("stxG-"):
            candidates.append(ref.replace("stxG-", "stx", 1))
        if ref.startswith("stx") and not ref.startswith("stxG-"):
            candidates.append(ref.replace("stx", "stxG-", 1))

        row = None
        for candidate in candidates:
            row = conn.execute(
                "SELECT * FROM frame_verification_orders WHERE telegram_payment_id=?",
                (candidate,),
            ).fetchone()
            if row:
                break

        if not row:
            for candidate in candidates:
                row = conn.execute(
                    "SELECT * FROM frame_verification_orders WHERE telegram_payment_id LIKE ?",
                    (f"%{candidate}%",),
                ).fetchone()
                if row:
                    break

        if not row:
            core = ref[3:] if ref.startswith("stx") else ref
            if len(core) >= 16:
                row = conn.execute(
                    "SELECT * FROM frame_verification_orders WHERE telegram_payment_id LIKE ?",
                    (f"%{core[:32]}%",),
                ).fetchone()

        if not row and ref.isdigit():
            row = conn.execute(
                "SELECT * FROM frame_verification_orders WHERE id=?",
                (int(ref),),
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def refund_payment_direct(
    *,
    telegram_payment_id: str,
    buyer_telegram_id: int,
    bot_token: str,
    username: str | None = None,
) -> dict[str, Any]:
    payment_id = normalize_transaction_ref(telegram_payment_id)
    refund = refund_star_payment_api(bot_token, int(buyer_telegram_id), payment_id)
    if not refund.get("ok"):
        return refund

    order = get_order_by_transaction_ref(payment_id)
    if order and order.get("status") != "refunded":
        return finalize_refund(str(order["telegram_payment_id"]))

    if username:
        conn = _sqlite()
        try:
            user = conn.execute(
                "SELECT id FROM users WHERE lower(username)=?",
                (username.strip().lstrip("@").lower(),),
            ).fetchone()
            if user:
                uid = int(user["id"])
                conn.execute(
                    "DELETE FROM bot_verifications WHERE user_id=?",
                    (uid,),
                )
                conn.commit()
                from frame_verification.push import enqueue_verification_push
                enqueue_verification_push(Storage.open_tool(), uid)
        finally:
            conn.close()

    return {
        "ok": True,
        "telegram_payment_id": payment_id,
        "buyer_telegram_id": int(buyer_telegram_id),
        "orphan_refund": order is None,
    }


def refund_star_payment_api(
    bot_token: str,
    user_id: int,
    telegram_payment_charge_id: str,
) -> dict[str, Any]:
    payload = urllib.parse.urlencode(
        {
            "user_id": int(user_id),
            "telegram_payment_charge_id": telegram_payment_charge_id,
        }
    ).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/refundStarPayment",
        data=payload,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "error": body or str(exc)}
        return {
            "ok": False,
            "error": data.get("description") or body or str(exc),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if not data.get("ok"):
        return {
            "ok": False,
            "error": data.get("description") or "Telegram отклонил возврат.",
        }
    return {"ok": True, "result": data.get("result", True)}


def finalize_refund(telegram_payment_id: str) -> dict[str, Any]:
    conn = _sqlite()
    try:
        row = conn.execute(
            "SELECT * FROM frame_verification_orders WHERE telegram_payment_id=?",
            (telegram_payment_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Транзакция не найдена."}

        order = dict(row)
        if order.get("status") == "refunded":
            return {
                "ok": False,
                "error": "По этой транзакции возврат уже выполнен.",
                "already_refunded": True,
            }

        now = int(time.time())
        conn.execute(
            "DELETE FROM bot_verifications WHERE user_id=?",
            (int(order["user_id"]),),
        )
        conn.execute(
            """
            UPDATE frame_verification_orders
            SET status='refunded', refunded_at=?
            WHERE telegram_payment_id=?
            """,
            (now, telegram_payment_id),
        )
        conn.commit()
        user_id = int(order["user_id"])
    finally:
        conn.close()

    storage = Storage.open_tool()
    enqueue_verification_push(storage, user_id)

    try:
        from shop_webapp.service import bump_top_donors_version
        bump_top_donors_version()
    except Exception:
        pass

    return {
        "ok": True,
        "order_id": int(order["id"]),
        "username": order["username"],
        "user_id": int(order["user_id"]),
        "stars_amount": int(order["stars_amount"]),
        "buyer_telegram_id": order.get("buyer_telegram_id"),
        "telegram_payment_id": order["telegram_payment_id"],
        "verification_type": order.get("verification_type"),
    }
