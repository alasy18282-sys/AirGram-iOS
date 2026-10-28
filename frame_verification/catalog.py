from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Prefer the catalog shipped inside the server tree; fall back to the legacy
# ../frag/frame location from the original host layout.
_CATALOG_PATH = Path(__file__).resolve().parent / "verifications.json"
if not _CATALOG_PATH.is_file():
    _CATALOG_PATH = (
        Path(__file__).resolve().parents[2] / "frag" / "frame" / "verifications.json"
    )

CATALOG_PATH = _CATALOG_PATH


def load_catalog() -> dict[str, dict[str, Any]]:
    data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    return {str(key): value for key, value in data.items()}


def get_verification(verification_id: str) -> dict[str, Any] | None:
    return load_catalog().get(verification_id)


def is_timed_verification(verification_id: str) -> bool:
    vdef = get_verification(verification_id)
    return bool(vdef and vdef.get("timed"))


def get_verification_plans(verification_id: str) -> dict[str, dict[str, Any]]:
    vdef = get_verification(verification_id) or {}
    return dict(vdef.get("plans") or {})


def get_verification_price(verification_id: str, plan: str | None = None) -> int:
    vdef = get_verification(verification_id)
    if not vdef:
        raise ValueError(f"Unknown verification: {verification_id}")
    if vdef.get("timed"):
        plans = vdef.get("plans") or {}
        plan_id = plan or "month"
        entry = plans.get(plan_id)
        if not entry:
            raise ValueError(f"Unknown plan: {plan_id}")
        return int(entry["price"])
    return int(vdef["price"])


def resolve_icon_index(vdef: dict[str, Any]) -> int:
    """Return 0-based emoji index in Telegram pack order."""
    idx = int(vdef.get("index", 0))
    if vdef.get("indexBase") == 1:
        idx -= 1
    return max(0, idx)


TELEGRAM_VERIFICATION_DESCRIPTION = (
    "This account is verified as official by the representatives of Telegram"
)


def description_for_organization(org: str) -> str:
    org = (org or "").strip()
    if org.lower() == "telegram":
        return TELEGRAM_VERIFICATION_DESCRIPTION
    org = org or "организацией"
    return f"Аккаунт верифицирован организацией «{org}»."


def get_verification_description(vdef: dict[str, Any]) -> str:
    """Profile text shown on verified accounts."""
    custom = (vdef.get("description") or "").strip()
    if custom:
        return custom
    return description_for_organization(vdef.get("organization") or "")


def get_plan_label(verification_id: str, plan: str) -> str:
    vdef = get_verification(verification_id)
    if not vdef:
        return plan
    if vdef.get("timed"):
        entry = (vdef.get("plans") or {}).get(plan)
        return str(entry["label"]) if entry else plan
    return "Навсегда"


def list_verifications(*, include_admin: bool = False) -> list[dict[str, Any]]:
    catalog = load_catalog()
    return [
        {"id": vid, **item}
        for vid, item in catalog.items()
        if include_admin or not item.get("adminOnly")
    ]


def is_admin_only_verification(verification_id: str) -> bool:
    vdef = get_verification(verification_id)
    return bool(vdef and vdef.get("adminOnly"))
