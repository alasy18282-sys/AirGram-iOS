"""Cross-process wake signal for instant pending-update delivery."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import config

WAKE_FILE = Path(config.DB_PATH).parent / 'push_wake'


def signal_pending_push() -> None:
    """Notify my-server to flush pending_updates immediately."""
    WAKE_FILE.parent.mkdir(parents=True, exist_ok=True)
    WAKE_FILE.write_text(str(time.time()), encoding='utf-8')


def _wake_mtime() -> float:
    try:
        return WAKE_FILE.stat().st_mtime
    except OSError:
        return 0.0


async def wait_for_wake(*, timeout: float = 0.25) -> bool:
    """Wait until push_wake is touched or timeout expires."""
    start = _wake_mtime()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if _wake_mtime() > start:
            return True
        await asyncio.sleep(0.02)
    return _wake_mtime() > start
