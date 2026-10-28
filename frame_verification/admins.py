from __future__ import annotations

import os

_DEFAULT_ADMIN_IDS = "8549770368,8014265847"


def admin_telegram_ids() -> set[int]:
    raw = os.environ.get("FRAME_ADMIN_IDS", _DEFAULT_ADMIN_IDS)
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


def is_frame_admin(telegram_id: int | None) -> bool:
    if telegram_id is None:
        return False
    return int(telegram_id) in admin_telegram_ids()
