#!/usr/bin/env python3
"""Re-import verification packs to refresh emoji order (sort_order)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frame_verification.telethon_env import ensure_telethon_env
from frame_verification.import_packs import import_missing_packs

ensure_telethon_env()

if __name__ == "__main__":
    imported = import_missing_packs()
    if imported:
        print("Refreshed:", ", ".join(imported))
    else:
        print("No packs refreshed")
