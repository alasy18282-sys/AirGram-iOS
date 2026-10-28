from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

_ENV_PATH = Path(__file__).resolve().parents[1] / ".telegram_api.env"
_LOADED = False


def ensure_telethon_env() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    if _ENV_PATH.is_file():
        for raw_line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and not os.environ.get(key):
                os.environ[key] = value

def telethon_credentials() -> tuple[int, str]:
    ensure_telethon_env()
    api_id = int(os.environ.get("TELEGRAM_API_ID", "0") or "0")
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    return api_id, api_hash


def resolve_telethon_session_path(session: str | Path) -> Path:
    """Absolute Telethon session base path (without .session suffix)."""
    raw = Path(session)
    if raw.suffix == '.session':
        raw = raw.with_suffix('')
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    return raw.resolve()


def _session_sqlite_is_locked(base: Path, *, timeout_sec: float = 1.0) -> bool:
    db = base.with_suffix('.session')
    if not db.is_file():
        return False
    try:
        conn = sqlite3.connect(str(db), timeout=timeout_sec)
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.commit()
        finally:
            conn.close()
        return False
    except sqlite3.OperationalError:
        return True


def prepare_telethon_session(
        session: str | Path) -> tuple[str, Optional[Callable[[], None]]]:
    """Return Telethon session path; copy to temp dir if sqlite is locked."""
    base = resolve_telethon_session_path(session)
    db = base.with_suffix('.session')
    if not db.is_file():
        return str(base), None
    if not _session_sqlite_is_locked(base):
        return str(base), None

    tmp_root = Path(tempfile.mkdtemp(prefix='telethon-session-'))
    copy_base = tmp_root / base.name
    copy_db = copy_base.with_suffix('.session')
    shutil.copy2(db, copy_db)
    for suffix in ('-journal', '-wal', '-shm'):
        sidecar = Path(f'{db}{suffix}')
        if sidecar.is_file():
            shutil.copy2(sidecar, Path(f'{copy_db}{suffix}'))

    print(
        f'Сессия {db.name} занята другим процессом — '
        f'используется временная копия.',
        file=sys.stderr,
    )

    def cleanup() -> None:
        shutil.rmtree(tmp_root, ignore_errors=True)

    return str(copy_base), cleanup
