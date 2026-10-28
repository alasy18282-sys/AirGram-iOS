#!/usr/bin/env python3
"""Backup DB and reset auth/session state without wiping users or content."""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def _backup(db_path: Path) -> Path:
    backups = ROOT / 'backups'
    backups.mkdir(exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    dest = backups / f'mytelegram-{stamp}.db'
    shutil.copy2(db_path, dest)
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, backups / f'{dest.name}{suffix}')
    return dest


def _reset_via_sqlite(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    tables = (
        'pending_updates',
        'pending_srp',
        'pending_sign_in',
        'pending_login_messages',
        'phone_codes',
        'user_passwords',
        'sessions',
        'auth_keys',
    )
    counts = {}
    for table in tables:
        try:
            row = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()
            counts[table] = int(row[0])
            conn.execute(f'DELETE FROM {table}')
        except sqlite3.OperationalError:
            counts[table] = 0
    conn.commit()
    conn.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Backup mytelegram.db and clear auth/session state only.')
    parser.add_argument(
        '--db', default=config.DB_PATH, help='Path to mytelegram.db')
    parser.add_argument(
        '--no-backup', action='store_true', help='Skip backup (not recommended)')
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f'Database not found: {db_path}')
        return 1

    if not args.no_backup:
        backup = _backup(db_path)
        print(f'Backup: {backup}')

    counts = _reset_via_sqlite(db_path)
    print('Cleared rows:')
    for table, n in counts.items():
        print(f'  {table}: {n}')
    print('Users, messages, stickers and channels were kept.')
    print('Restart the server, then log in again in Telegram.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
