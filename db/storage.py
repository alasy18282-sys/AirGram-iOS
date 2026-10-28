"""
SQLite storage for users, auth keys, phone codes, messages, dialogs.
"""
import json
import os
import re
import secrets
import sqlite3
import time
import random
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo

import config
from handlers.packers import login_code_text

log = logging.getLogger(__name__)

SERVICE_USER_ID = config.SERVICE_USER_ID
DEFAULT_FREEZE_EMOJI_DOCUMENT_ID = 5449449325434266744
_PENDING_UPDATES_MAX_QUEUE = 50


def normalize_phone(phone: str) -> str:
    return ''.join(c for c in phone.strip().lstrip('+') if c.isdigit())


def normalize_collectible_username(username: str) -> str:
    return (username or '').strip().lstrip('@').lower()


_COLLECTIBLE_USERNAME_RE = re.compile(r'^[a-z0-9_]+$')


def _collectible_username_chars_valid(username: str) -> bool:
    return bool(_COLLECTIBLE_USERNAME_RE.fullmatch(username))


def normalize_star_gift_slug(slug: str) -> str:
    return (slug or '').strip().strip('/').lower()


def format_phone_display(phone: str) -> str:
    from handlers.phone_countries import format_phone_display as _fmt
    return _fmt(phone)


def is_valid_username(username: str) -> bool:
    if not username or len(username) < 5 or len(username) > 32:
        return False
    if not username[0].isalpha() or username[-1] == '_':
        return False
    return _collectible_username_chars_valid(username)


def is_valid_collectible_username(username: str) -> bool:
    username = normalize_collectible_username(username)
    if not username or len(username) > 32:
        return False
    if username[-1] == '_':
        return False
    return _collectible_username_chars_valid(username)


def normalize_unix_time(value) -> Optional[int]:
    """Coerce Telethon datetime / ISO strings / ints to unix seconds."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if hasattr(value, 'timestamp'):
        return int(value.timestamp())
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.lstrip('-').isdigit():
            return int(s)
        try:
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return None
    return None


class _RetryingCursor:
    """Cursor proxy: execute/executemany retried on lock, rest delegated."""

    def __init__(self, cur, conn: '_RetryingConn'):
        object.__setattr__(self, '_cur', cur)
        object.__setattr__(self, '_conn', conn)

    def execute(self, sql, parameters=()):
        return self._conn._retry(lambda: self._cur.execute(sql, parameters))

    def executemany(self, sql, seq_of_parameters):
        return self._conn._retry(
            lambda: self._cur.executemany(sql, seq_of_parameters))

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def __setattr__(self, name, value):
        setattr(self._cur, name, value)

    def __iter__(self):
        return iter(self._cur)


class _RetryingConn:
    """sqlite3 connection proxy that never surfaces 'database is locked'.

    Plain lock contention is absorbed by sqlite busy_timeout, but a stale
    read snapshot (SQLITE_BUSY_SNAPSHOT — another process committed while we
    hold an open transaction) fails instantly and ignores busy_timeout.  The
    wrapper first retries as-is, then rolls the transaction back (releasing
    the snapshot) and retries again, so concurrent tools importing gifts can
    never stall client RPCs with lock errors.
    """

    _OWN = ('_conn', '_attempts', '_max_wait')

    def __init__(self, conn: sqlite3.Connection,
                 attempts: int = 400, max_wait: float = 240.0):
        object.__setattr__(self, '_conn', conn)
        object.__setattr__(self, '_attempts', attempts)
        object.__setattr__(self, '_max_wait', max_wait)

    @staticmethod
    def _is_lock(exc: Exception) -> bool:
        msg = str(exc).lower()
        return 'locked' in msg or 'busy' in msg

    def _retry(self, fn):
        started = time.monotonic()
        delay = 0.05
        rolled_back = False
        last: Optional[Exception] = None
        for _ in range(self._attempts):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if not self._is_lock(exc):
                    raise
                last = exc
                waited = time.monotonic() - started
                if waited > self._max_wait:
                    raise
                # Phase 1 (~10s): plain retry keeps pending writes intact.
                # Phase 2: drop the stale snapshot and retry fresh.
                if waited > 10.0 and not rolled_back:
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass
                    rolled_back = True
                time.sleep(delay)
                delay = min(delay * 2, 1.0)
        raise last

    def execute(self, sql: str, parameters=()):
        return self._retry(lambda: self._conn.execute(sql, parameters))

    def executemany(self, sql: str, seq_of_parameters):
        return self._retry(
            lambda: self._conn.executemany(sql, seq_of_parameters))

    def executescript(self, sql_script: str):
        return self._retry(lambda: self._conn.executescript(sql_script))

    def commit(self):
        self._retry(self._conn.commit)

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def cursor(self, *args, **kwargs):
        return _RetryingCursor(self._conn.cursor(*args, **kwargs), self)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name in self._OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


class Storage:
    def __init__(self, db_path: str, *, maintenance: bool = True,
                 migrate: bool | None = None):
        self._path = db_path
        self._conn: sqlite3.Connection = None
        self._maintenance = maintenance
        self._migrate_on_init = maintenance if migrate is None else migrate
        self._last_due_star_gift_drops = None
        self._init()

    def _init(self):
        conn = sqlite3.connect(
            self._path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA busy_timeout=30000')
        self._conn = _RetryingConn(conn)
        c = self._conn.cursor()
        c.executescript('''
            CREATE TABLE IF NOT EXISTS auth_keys (
                auth_key_id   INTEGER PRIMARY KEY,
                auth_key      BLOB NOT NULL,
                created_at    INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                phone         TEXT UNIQUE NOT NULL,
                first_name    TEXT NOT NULL DEFAULT '',
                last_name     TEXT NOT NULL DEFAULT '',
                username      TEXT DEFAULT NULL,
                username_sort_order INTEGER NOT NULL DEFAULT 0,
                access_hash   INTEGER NOT NULL,
                about         TEXT NOT NULL DEFAULT '',
                created_at    INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                auth_key_id   INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (auth_key_id)
            );

            CREATE TABLE IF NOT EXISTS collectible_usernames (
                username      TEXT PRIMARY KEY,
                user_id       INTEGER NOT NULL,
                active        INTEGER NOT NULL DEFAULT 1,
                sort_order    INTEGER NOT NULL DEFAULT 0,
                price         TEXT NOT NULL DEFAULT '',
                granted_at    INTEGER NOT NULL DEFAULT 0,
                granted_by    INTEGER NOT NULL DEFAULT 0,
                created_at    INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS gift_blockchain_links (
                slug          TEXT PRIMARY KEY,
                owner_address TEXT NOT NULL DEFAULT '',
                created_at    INTEGER NOT NULL DEFAULT 0,
                updated_at    INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS business_intros (
                user_id             INTEGER PRIMARY KEY,
                title               TEXT NOT NULL DEFAULT '',
                description         TEXT NOT NULL DEFAULT '',
                sticker_document_id INTEGER NOT NULL DEFAULT 0,
                updated_at          INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS business_greeting_messages (
                user_id             INTEGER PRIMARY KEY,
                shortcut_id         INTEGER NOT NULL DEFAULT 0,
                no_activity_days    INTEGER NOT NULL DEFAULT 0,
                recipients_flags    INTEGER NOT NULL DEFAULT 0,
                recipients_users_json TEXT NOT NULL DEFAULT '[]',
                updated_at          INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS business_work_hours (
                user_id             INTEGER PRIMARY KEY,
                timezone_id         TEXT NOT NULL DEFAULT 'UTC',
                weekly_open_json    TEXT NOT NULL DEFAULT '[]',
                updated_at          INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS business_locations (
                user_id             INTEGER PRIMARY KEY,
                address             TEXT NOT NULL DEFAULT '',
                lat                 REAL NOT NULL DEFAULT 0,
                long                REAL NOT NULL DEFAULT 0,
                accuracy_radius     INTEGER NOT NULL DEFAULT 0,
                access_hash         INTEGER NOT NULL DEFAULT 0,
                has_geo             INTEGER NOT NULL DEFAULT 0,
                updated_at          INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS business_away_messages (
                user_id               INTEGER PRIMARY KEY,
                shortcut_id           INTEGER NOT NULL DEFAULT 0,
                offline_only          INTEGER NOT NULL DEFAULT 0,
                schedule_type         TEXT NOT NULL DEFAULT 'always',
                schedule_start        INTEGER NOT NULL DEFAULT 0,
                schedule_end          INTEGER NOT NULL DEFAULT 0,
                recipients_flags      INTEGER NOT NULL DEFAULT 0,
                recipients_users_json TEXT NOT NULL DEFAULT '[]',
                updated_at            INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS business_away_message_sent (
                owner_id              INTEGER NOT NULL,
                peer_id               INTEGER NOT NULL,
                sent_at               INTEGER NOT NULL,
                PRIMARY KEY (owner_id, peer_id)
            );

            CREATE TABLE IF NOT EXISTS quick_reply_shortcuts (
                user_id               INTEGER NOT NULL,
                shortcut_id           INTEGER NOT NULL,
                shortcut              TEXT NOT NULL DEFAULT '',
                top_message_id        INTEGER NOT NULL DEFAULT 0,
                msg_count             INTEGER NOT NULL DEFAULT 0,
                updated_at            INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, shortcut_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_quick_reply_shortcut_name
                ON quick_reply_shortcuts(user_id, shortcut);

            CREATE TABLE IF NOT EXISTS quick_reply_messages (
                user_id               INTEGER NOT NULL,
                shortcut_id           INTEGER NOT NULL,
                msg_id                INTEGER NOT NULL,
                text                  TEXT NOT NULL DEFAULT '',
                entities              TEXT NOT NULL DEFAULT '[]',
                media_document_id     INTEGER NOT NULL DEFAULT 0,
                media_photo_id        INTEGER NOT NULL DEFAULT 0,
                media_poll_json       TEXT NOT NULL DEFAULT '',
                date                  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, shortcut_id, msg_id)
            );

            CREATE TABLE IF NOT EXISTS phone_codes (
                phone         TEXT NOT NULL,
                code          TEXT NOT NULL,
                code_hash     TEXT NOT NULL,
                expires_at    INTEGER NOT NULL,
                used          INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (phone, code_hash)
            );

            CREATE TABLE IF NOT EXISTS login_tokens (
                token             BLOB PRIMARY KEY,
                expires_at        INTEGER NOT NULL,
                api_id            INTEGER NOT NULL,
                accepted_user_id  INTEGER,
                created_at        INTEGER NOT NULL,
                accepted_at       INTEGER
            );

            CREATE TABLE IF NOT EXISTS messages (
                owner_id      INTEGER NOT NULL,
                id            INTEGER NOT NULL,
                from_id       INTEGER NOT NULL,
                peer_id       INTEGER NOT NULL,
                date          INTEGER NOT NULL,
                text          TEXT NOT NULL,
                entities      TEXT NOT NULL DEFAULT '[]',
                reply_markup_json TEXT NOT NULL DEFAULT '',
                pts           INTEGER NOT NULL DEFAULT 0,
                saved_peer_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (owner_id, id)
            );

            CREATE TABLE IF NOT EXISTS dialogs (
                owner_id          INTEGER NOT NULL,
                peer_id           INTEGER NOT NULL,
                top_message_id    INTEGER NOT NULL DEFAULT 0,
                unread_count      INTEGER NOT NULL DEFAULT 0,
                last_message_date INTEGER NOT NULL DEFAULT 0,
                last_outgoing     INTEGER NOT NULL DEFAULT 0,
                read_inbox_max_id INTEGER NOT NULL DEFAULT 0,
                read_outbox_max_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (owner_id, peer_id)
            );

            CREATE TABLE IF NOT EXISTS user_state (
                user_id       INTEGER PRIMARY KEY,
                pts           INTEGER NOT NULL DEFAULT 0,
                date          INTEGER NOT NULL,
                seq           INTEGER NOT NULL DEFAULT 0,
                qts           INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS pending_login_messages (
                phone         TEXT PRIMARY KEY,
                code          TEXT NOT NULL,
                text          TEXT NOT NULL,
                entities      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_updates (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                update_data   BLOB NOT NULL,
                created_at    INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS temp_auth_bindings (
                temp_auth_key_id INTEGER PRIMARY KEY,
                perm_auth_key_id INTEGER NOT NULL,
                expires_at       INTEGER NOT NULL DEFAULT 0,
                created_at       INTEGER NOT NULL
            );
        ''')
        if self._migrate_on_init:
            self._retry_on_db_lock(lambda: self._migrate(c))
        if self._maintenance:
            self._run_maintenance(c)
        else:
            self._conn.commit()
        log.info('Database initialized: %s%s', self._path,
                 '' if self._maintenance else ' (tool mode)')

    def _run_maintenance(self, c: sqlite3.Cursor) -> None:
        if config.INFINITE_STARS:
            c.execute(
                'UPDATE stars_balances SET balance=?',
                (config.STARS_BALANCE,))
            c.execute(
                '''INSERT INTO stars_balances (user_id, balance)
                   SELECT id, ? FROM users
                   WHERE id NOT IN (SELECT user_id FROM stars_balances)''',
                (config.STARS_BALANCE,))
        self._expire_premium_users(c)
        if config.ALL_USERS_PREMIUM:
            c.execute('UPDATE users SET premium=1 WHERE id!=?',
                      (SERVICE_USER_ID,))
        self._sync_bot_verification_descriptions(c)
        self._purge_expired_bot_verifications(c)
        self._migrate_messages_schema(c)
        self._normalize_sticker_sets(c)
        self._normalize_black_backdrop(c)
        self._normalize_message_dates(c)
        self._cleanup_stale_public_channel_dialogs(c)
        self._conn.commit()
        dialog_peers = [
            (int(row['owner_id']), int(row['peer_id']))
            for row in self._conn.execute(
                'SELECT DISTINCT owner_id, peer_id FROM dialogs')
        ]
        for owner_id, peer_id in dialog_peers:
            try:
                self._retry_on_db_lock(
                    lambda oid=owner_id, pid=peer_id:
                        self.repair_dialog(oid, pid))
            except sqlite3.OperationalError as exc:
                if 'locked' not in str(exc).lower() and 'busy' not in str(exc).lower():
                    raise
                log.warning(
                    'repair_dialog skipped owner=%s peer=%s: %s',
                    owner_id, peer_id, exc)
        self._retry_on_db_lock(self._conn.commit)
        self._seed_service_user()
        self._ensure_service_user_branding()
        self._seed_demo_bot()
        self._seed_stickers_bot()
        self._seed_report_bot()
        self._seed_premium_bot()

    def _normalize_black_backdrop(self, c: sqlite3.Cursor) -> None:
        """Keep Telegram's Black backdrop as one shared 2.5% catalog entry.

        Earlier imports created per-gift duplicates, including incorrect
        rarity/color variants. Preserve the canonical row's id and remap saved
        gifts before removing the duplicates.
        """
        rows = c.execute(
            '''SELECT id FROM star_gift_upgrade_attrs
               WHERE attr_type='backdrop' AND lower(name)='black'
               ORDER BY CASE WHEN center_color=4672845
                                  AND edge_color=0
                                  AND pattern_color=14277081
                                  AND text_color=15658734 THEN 0 ELSE 1 END,
                        id'''
        ).fetchall()
        if not rows:
            return
        canonical_id = int(rows[0]['id'])
        old_ids = [int(row['id']) for row in rows if int(row['id']) != canonical_id]
        c.execute(
            '''UPDATE star_gift_upgrade_attrs
               SET gift_id=0, name='Black', rarity_permille=25,
                   backdrop_id=0, center_color=4672845, edge_color=0,
                   pattern_color=14277081, text_color=15658734, crafted=0
               WHERE id=?''',
            (canonical_id,),
        )
        if old_ids:
            marks = ','.join('?' for _ in old_ids)
            c.execute(
                f'''UPDATE saved_star_gifts SET backdrop_attr_id=?
                    WHERE backdrop_attr_id IN ({marks})''',
                [canonical_id, *old_ids],
            )
            c.execute(
                f'DELETE FROM star_gift_upgrade_attrs WHERE id IN ({marks})',
                old_ids,
            )
        c.execute(
            '''CREATE UNIQUE INDEX IF NOT EXISTS
               idx_star_gift_single_black_backdrop
               ON star_gift_upgrade_attrs(attr_type, lower(name))
               WHERE attr_type='backdrop' AND lower(name)='black' '''
        )

    def _cleanup_stale_public_channel_dialogs(self, c: sqlite3.Cursor) -> None:
        c.execute(
            '''DELETE FROM dialogs
               WHERE peer_id >= ?
                 AND EXISTS (
                   SELECT 1 FROM channels c
                   WHERE c.id=dialogs.peer_id
                     AND c.username IS NOT NULL
                 )
                 AND NOT EXISTS (
                   SELECT 1 FROM channel_members cm
                   WHERE cm.channel_id=dialogs.peer_id
                     AND cm.user_id=dialogs.owner_id
                     AND cm.left=0
                 )''',
            (config.CHANNEL_ID_MIN,),
        )

    @classmethod
    def open_tool(cls, db_path: str | None = None) -> 'Storage':
        """Open DB for import tools without blocking the running server."""
        return cls(db_path or config.DB_PATH, maintenance=False, migrate=False)

    def _retry_on_db_lock(self, fn, *, attempts: int = 12):
        delay = 0.05
        last_exc: Optional[Exception] = None
        for _ in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if 'locked' not in msg and 'busy' not in msg:
                    raise
                last_exc = exc
                time.sleep(delay)
                delay = min(delay * 2, 1.0)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError('database retry failed')

    def _migrate(self, c: sqlite3.Cursor) -> None:
        for stmt in (
            'ALTER TABLE users ADD COLUMN about TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE messages ADD COLUMN pts INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN is_bot INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN last_seen INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN online_until INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE dialogs ADD COLUMN last_message_date INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE dialogs ADD COLUMN last_outgoing INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE dialogs ADD COLUMN read_inbox_max_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE dialogs ADD COLUMN read_outbox_max_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN reply_to_msg_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN media_document_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN media_photo_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN action_json TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE messages ADD COLUMN reply_markup_json TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE users ADD COLUMN profile_photo_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE saved_star_gifts ADD COLUMN upgraded INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE saved_star_gifts ADD COLUMN unique_id INTEGER',
            'ALTER TABLE saved_star_gifts ADD COLUMN unique_slug TEXT',
            'ALTER TABLE saved_star_gifts ADD COLUMN unique_num INTEGER',
            'ALTER TABLE saved_star_gifts ADD COLUMN original_details_kept '
            'INTEGER NOT NULL DEFAULT 1',
            'ALTER TABLE saved_star_gifts ADD COLUMN model_attr_id INTEGER',
            'ALTER TABLE saved_star_gifts ADD COLUMN pattern_attr_id INTEGER',
            'ALTER TABLE saved_star_gifts ADD COLUMN backdrop_attr_id INTEGER',
            'ALTER TABLE saved_star_gifts ADD COLUMN resell_stars INTEGER',
            'ALTER TABLE saved_star_gifts ADD COLUMN resell_stars_nanos INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE saved_star_gifts ADD COLUMN resell_ton INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE stars_transactions ADD COLUMN offer INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE stars_transactions ADD COLUMN amount_ton INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE stars_transactions ADD COLUMN stargift_auction_bid INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE star_gifts ADD COLUMN auction_total_rounds INTEGER',
            'ALTER TABLE star_gifts ADD COLUMN auction_round_duration INTEGER',
            'ALTER TABLE star_gifts ADD COLUMN auction_min_bid INTEGER',
            'ALTER TABLE star_gifts ADD COLUMN auction_end_date INTEGER',
            'ALTER TABLE users ADD COLUMN premium INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN emoji_status_document_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN emoji_status_until INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN emoji_status_collectible_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN main_profile_tab INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN personal_channel_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN personal_channel_message INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE stars_balances ADD COLUMN stars_peak INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE bot_verifications ADD COLUMN expires_at INTEGER',
            'ALTER TABLE users ADD COLUMN noncontact_peers_paid_stars '
            'INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN paid_message_stars '
            'INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE stars_transactions ADD COLUMN paid_messages '
            'INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN banned INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN banned_username TEXT',
            'ALTER TABLE users ADD COLUMN banned_first_name TEXT',
            'ALTER TABLE users ADD COLUMN ban_block_recreate INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN media_poll_json TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE messages ADD COLUMN saved_peer_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN suggested_post_json TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE messages ADD COLUMN fwd_from_json TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE messages ADD COLUMN offline INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN display_gifts_button INTEGER NOT NULL DEFAULT 1',
            'ALTER TABLE users ADD COLUMN new_noncontact_peers_require_premium '
            'INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN hide_read_marks INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN disallowed_gifts_json TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE users ADD COLUMN user_birthday INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN frozen INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN frozen_at INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN frozen_until INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN scam INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN fake INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN label_hidden_about TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE saved_star_gifts ADD COLUMN burned INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE saved_star_gifts ADD COLUMN crafted INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE saved_star_gifts ADD COLUMN craft_chance_permille INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE saved_star_gifts ADD COLUMN can_craft_at INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN username_sort_order INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE star_gifts ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE star_gifts ADD COLUMN upgrade_importing INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE star_gifts ADD COLUMN shop_hidden INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE star_gifts ADD COLUMN is_auction INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE star_gifts ADD COLUMN is_tester_gift INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE pending_updates ADD COLUMN auth_key_id INTEGER',
            'ALTER TABLE pending_updates ADD COLUMN layer INTEGER',
            'ALTER TABLE users ADD COLUMN peer_color_id INTEGER NOT NULL DEFAULT -1',
            'ALTER TABLE users ADD COLUMN peer_color_bg_emoji_id '
            'INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN profile_color_id INTEGER NOT NULL DEFAULT -1',
            'ALTER TABLE users ADD COLUMN profile_color_bg_emoji_id '
            'INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE channels ADD COLUMN peer_color_id INTEGER NOT NULL DEFAULT -1',
            'ALTER TABLE channels ADD COLUMN peer_color_bg_emoji_id '
            'INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE channels ADD COLUMN profile_color_id INTEGER NOT NULL DEFAULT -1',
            'ALTER TABLE channels ADD COLUMN profile_color_bg_emoji_id '
            'INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE photos ADD COLUMN is_profile INTEGER NOT NULL DEFAULT 0',
        ):
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass
        try:
            # Chat screenshots shared as media must not appear in profile albums.
            c.execute(
                '''UPDATE photos
                   SET is_profile=1
                   WHERE id IN (
                     SELECT profile_photo_id FROM users
                     WHERE profile_photo_id IS NOT NULL AND profile_photo_id!=0
                   )'''
            )
        except sqlite3.OperationalError:
            pass
        try:
            c.execute(
                '''UPDATE users
                   SET label_hidden_about=about, about=''
                   WHERE (scam=1 OR fake=1)
                     AND about!='' AND label_hidden_about='' '''
            )
        except sqlite3.OperationalError:
            pass
        c.execute('''
            CREATE TABLE IF NOT EXISTS paid_message_exceptions (
                owner_user_id   INTEGER NOT NULL,
                exempt_user_id  INTEGER NOT NULL,
                PRIMARY KEY (owner_user_id, exempt_user_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS paid_message_payments (
                recipient_id    INTEGER NOT NULL,
                sender_id       INTEGER NOT NULL,
                msg_id          INTEGER NOT NULL,
                stars           INTEGER NOT NULL,
                refunded        INTEGER NOT NULL DEFAULT 0,
                created_at      INTEGER NOT NULL,
                PRIMARY KEY (recipient_id, sender_id, msg_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS ton_balances (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS ton_transactions (
                tx_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                date INTEGER NOT NULL,
                title TEXT,
                peer_user_id INTEGER,
                gift INTEGER NOT NULL DEFAULT 0,
                stargift_resale INTEGER NOT NULL DEFAULT 0,
                offer INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_privacy (
                user_id      INTEGER NOT NULL,
                privacy_key  INTEGER NOT NULL,
                rules_json   TEXT NOT NULL,
                PRIMARY KEY (user_id, privacy_key)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY,
                access_hash INTEGER NOT NULL,
                title TEXT NOT NULL,
                about TEXT NOT NULL DEFAULT '',
                creator_id INTEGER NOT NULL,
                megagroup INTEGER NOT NULL DEFAULT 1,
                date INTEGER NOT NULL,
                pts INTEGER NOT NULL DEFAULT 1,
                top_message_id INTEGER NOT NULL DEFAULT 0,
                participants_count INTEGER NOT NULL DEFAULT 0,
                monoforum INTEGER NOT NULL DEFAULT 0,
                linked_monoforum_id INTEGER NOT NULL DEFAULT 0,
                invite_hash TEXT,
                peer_color_id INTEGER NOT NULL DEFAULT -1,
                profile_color_id INTEGER NOT NULL DEFAULT -1
            )
        ''')
        try:
            c.execute('ALTER TABLE channels ADD COLUMN invite_hash TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE channels ADD COLUMN username TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE channels ADD COLUMN pinned_msg_id INTEGER')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute(
                'ALTER TABLE channels ADD COLUMN send_paid_messages_stars '
                'INTEGER NOT NULL DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute(
                'ALTER TABLE channels ADD COLUMN broadcast_messages_allowed '
                'INTEGER NOT NULL DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute(
                'ALTER TABLE channels ADD COLUMN monoforum '
                'INTEGER NOT NULL DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute(
                'ALTER TABLE channels ADD COLUMN linked_monoforum_id '
                'INTEGER NOT NULL DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        c.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_username '
            'ON channels(username) WHERE username IS NOT NULL'
        )
        c.execute('''
            CREATE TABLE IF NOT EXISTS channel_members (
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                left INTEGER NOT NULL DEFAULT 0,
                date INTEGER NOT NULL,
                PRIMARY KEY (channel_id, user_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS channel_member_state (
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                pts INTEGER NOT NULL DEFAULT 0,
                read_max_id INTEGER NOT NULL DEFAULT 0,
                unread_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (channel_id, user_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS channel_admin_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                date INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                action_json TEXT NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS channel_message_views (
                channel_id INTEGER NOT NULL,
                msg_id INTEGER NOT NULL,
                views INTEGER NOT NULL DEFAULT 0,
                forwards INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (channel_id, msg_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS channel_message_viewers (
                channel_id INTEGER NOT NULL,
                msg_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (channel_id, msg_id, user_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS poll_votes (
                peer_id INTEGER NOT NULL,
                msg_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                options_json TEXT NOT NULL,
                voted_at INTEGER NOT NULL,
                PRIMARY KEY (peer_id, msg_id, user_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS blocked_users (
                owner_id INTEGER NOT NULL,
                blocked_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (owner_id, blocked_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS account_freeze_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                emoji TEXT NOT NULL DEFAULT '❄️',
                emoji_id TEXT NOT NULL DEFAULT '',
                appeal_url TEXT NOT NULL DEFAULT 'https://t.me/SpamBot',
                freeze_days INTEGER NOT NULL DEFAULT 30
            )
        ''')
        c.execute(
            'INSERT OR IGNORE INTO account_freeze_settings (id) VALUES (1)',
        )
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_passwords (
                user_id       INTEGER PRIMARY KEY,
                salt1         BLOB NOT NULL,
                salt2         BLOB NOT NULL,
                g             INTEGER NOT NULL,
                p             BLOB NOT NULL,
                v             BLOB NOT NULL,
                hint          TEXT NOT NULL DEFAULT '',
                created_at    INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS pending_sign_in (
                auth_key_id   INTEGER PRIMARY KEY,
                phone         TEXT NOT NULL,
                code_hash     TEXT NOT NULL,
                user_id       INTEGER NOT NULL,
                expires_at    INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS anonymous_numbers (
                phone         TEXT PRIMARY KEY,
                user_id       INTEGER NOT NULL,
                kind          TEXT NOT NULL,
                created_at    INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS pending_srp (
                srp_id        INTEGER PRIMARY KEY,
                user_id       INTEGER NOT NULL,
                auth_key_id   INTEGER NOT NULL,
                b_hex         TEXT NOT NULL,
                srp_b         BLOB NOT NULL,
                salt1         BLOB NOT NULL,
                salt2         BLOB NOT NULL,
                g             INTEGER NOT NULL,
                p             BLOB NOT NULL,
                v             BLOB NOT NULL,
                created_at    INTEGER NOT NULL
            )
        ''')
        c.execute(
            'CREATE INDEX IF NOT EXISTS idx_channel_admin_log '
            'ON channel_admin_log(channel_id, id DESC)'
        )
        try:
            c.execute(
                'ALTER TABLE channel_members ADD COLUMN admin_rank TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute(
                'ALTER TABLE channel_members ADD COLUMN promoted_by INTEGER')
        except sqlite3.OperationalError:
            pass
        c.execute('''
            CREATE TABLE IF NOT EXISTS stars_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_id TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                date INTEGER NOT NULL,
                gift INTEGER NOT NULL DEFAULT 0,
                refund INTEGER NOT NULL DEFAULT 0,
                peer_user_id INTEGER,
                peer_channel_id INTEGER,
                title TEXT,
                description TEXT,
                stargift_upgrade INTEGER NOT NULL DEFAULT 0,
                stargift_resale INTEGER NOT NULL DEFAULT 0,
                offer INTEGER NOT NULL DEFAULT 0,
                amount_ton INTEGER NOT NULL DEFAULT 0,
                msg_id INTEGER,
                stargift_auction_bid INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute(
            'CREATE INDEX IF NOT EXISTS idx_stars_tx_user '
            'ON stars_transactions(user_id, date DESC)')
        c.execute('''
            CREATE TABLE IF NOT EXISTS sticker_sets (
                id INTEGER PRIMARY KEY,
                access_hash INTEGER NOT NULL,
                short_name TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                flags INTEGER NOT NULL DEFAULT 0,
                count INTEGER NOT NULL DEFAULT 0,
                hash INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS server_kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS stories (
                owner_id INTEGER NOT NULL,
                id INTEGER NOT NULL,
                date INTEGER NOT NULL,
                expire_date INTEGER NOT NULL,
                period INTEGER NOT NULL DEFAULT 86400,
                pinned INTEGER NOT NULL DEFAULT 0,
                caption TEXT NOT NULL DEFAULT '',
                entities_json TEXT NOT NULL DEFAULT '[]',
                media_kind TEXT NOT NULL,
                photo_id INTEGER,
                document_id INTEGER,
                PRIMARY KEY (owner_id, id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS story_reads (
                viewer_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                max_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (viewer_id, owner_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS story_views (
                owner_id INTEGER NOT NULL,
                story_id INTEGER NOT NULL,
                viewer_id INTEGER NOT NULL,
                date INTEGER NOT NULL,
                PRIMARY KEY (owner_id, story_id, viewer_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS star_gift_collections (
                collection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS star_gift_collection_items (
                collection_id INTEGER NOT NULL,
                saved_id INTEGER NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (collection_id, saved_id),
                FOREIGN KEY (collection_id) REFERENCES star_gift_collections(collection_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS sticker_documents (
                id INTEGER PRIMARY KEY,
                access_hash INTEGER NOT NULL,
                set_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size INTEGER NOT NULL DEFAULT 0,
                width INTEGER NOT NULL DEFAULT 512,
                height INTEGER NOT NULL DEFAULT 512,
                alt TEXT NOT NULL DEFAULT '',
                file_reference BLOB NOT NULL DEFAULT X'00',
                date INTEGER NOT NULL DEFAULT 0,
                attributes_json TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY(set_id) REFERENCES sticker_sets(id)
            )
        ''')
        try:
            c.execute('ALTER TABLE sticker_documents ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE sticker_documents ADD COLUMN thumb_path TEXT NOT NULL DEFAULT ""')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE sticker_documents ADD COLUMN thumb_path_bytes BLOB NOT NULL DEFAULT X\'\'')
        except sqlite3.OperationalError:
            pass
        c.execute('''
            CREATE TABLE IF NOT EXISTS sticker_packs (
                set_id INTEGER NOT NULL,
                emoticon TEXT NOT NULL,
                document_ids TEXT NOT NULL,
                PRIMARY KEY (set_id, emoticon)
            )
        ''')
        os.makedirs(config.STICKERS_DATA_DIR, exist_ok=True)
        os.makedirs(config.UPLOADS_DATA_DIR, exist_ok=True)
        os.makedirs(config.PHOTOS_DATA_DIR, exist_ok=True)
        c.execute('''
            CREATE TABLE IF NOT EXISTS upload_file_parts (
                file_id     INTEGER NOT NULL,
                file_part   INTEGER NOT NULL,
                data        BLOB NOT NULL,
                PRIMARY KEY (file_id, file_part)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS photos (
                id              INTEGER PRIMARY KEY,
                access_hash     INTEGER NOT NULL,
                owner_id        INTEGER NOT NULL DEFAULT 0,
                file_path       TEXT NOT NULL,
                file_reference  BLOB NOT NULL DEFAULT X'00',
                date            INTEGER NOT NULL,
                width           INTEGER NOT NULL DEFAULT 0,
                height          INTEGER NOT NULL DEFAULT 0,
                size            INTEGER NOT NULL DEFAULT 0,
                dc_id           INTEGER NOT NULL DEFAULT 1,
                is_profile      INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            INSERT OR IGNORE INTO sticker_sets
            (id, access_hash, short_name, title, flags, count, hash)
            VALUES (?, ?, 'Uploads', 'Uploads', 0, 0, 0)
        ''', (config.UPLOADS_DOC_SET_ID, secrets.randbits(63)))
        c.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username '
            'ON users(username) WHERE username IS NOT NULL'
        )
        c.execute(
            'CREATE INDEX IF NOT EXISTS idx_collectible_usernames_user '
            'ON collectible_usernames(user_id, sort_order, username)'
        )
        c.execute('''
            CREATE TABLE IF NOT EXISTS bot_verifications (
                user_id       INTEGER PRIMARY KEY,
                bot_id        INTEGER NOT NULL,
                icon_id       INTEGER NOT NULL,
                company       TEXT NOT NULL DEFAULT '',
                description   TEXT NOT NULL DEFAULT '',
                expires_at    INTEGER
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS bot_phone_assignments (
                telegram_user_id  INTEGER PRIMARY KEY,
                phone             TEXT UNIQUE NOT NULL,
                created_at        INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS system_bot_states (
                bot_id       INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                state_json   TEXT NOT NULL DEFAULT '{}',
                updated_at   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (bot_id, user_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS report_complaints (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id       INTEGER NOT NULL,
                reported_user_id  INTEGER NOT NULL,
                reported_username TEXT NOT NULL DEFAULT '',
                reason            TEXT NOT NULL DEFAULT '',
                evidence_mode     TEXT NOT NULL DEFAULT '',
                description       TEXT NOT NULL DEFAULT '',
                evidence_photo_id INTEGER NOT NULL DEFAULT 0,
                status            TEXT NOT NULL DEFAULT 'open',
                admin_action      TEXT NOT NULL DEFAULT '',
                moderator_tg_id   INTEGER NOT NULL DEFAULT 0,
                created_at        INTEGER NOT NULL DEFAULT 0,
                resolved_at       INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute(
            'CREATE INDEX IF NOT EXISTS idx_report_complaints_status '
            'ON report_complaints(status, created_at DESC)'
        )
        c.execute('''
            CREATE TABLE IF NOT EXISTS telegram_phones (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id  INTEGER NOT NULL,
                phone             TEXT NOT NULL UNIQUE,
                source            TEXT NOT NULL DEFAULT 'auto',
                created_at        INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_telegram_phones_user
            ON telegram_phones(telegram_user_id, created_at DESC)
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS channel_send_random_ids (
                random_id   INTEGER NOT NULL,
                sender_id   INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                msg_id      INTEGER NOT NULL,
                PRIMARY KEY (random_id, sender_id)
            )
        ''')
        self._migrate_telegram_phones(c)
        self._migrate_star_gifts_schema(c)
        self._migrate_stars_tx_android_compat(c)
        self._migrate_stars_default_balance(c)
        self._migrate_premium_temporal(c)
        self._migrate_premium_gifts_cleanup(c)
        self._migrate_gifts_receive_allowed(c)
        self._migrate_premium_bot_unfreeze(c)
        self._migrate_premium_no_auto_grant(c)

    def _migrate_stars_default_balance(self, c: sqlite3.Cursor) -> None:
        """Seed the configured opening balance without changing spent accounts."""
        default = int(config.DEFAULT_STARS_BALANCE)
        c.execute(
            '''INSERT INTO stars_balances (user_id, balance, stars_peak)
               SELECT id, ?, ? FROM users
               WHERE id NOT IN (SELECT user_id FROM stars_balances)''',
            (default, default))
        c.execute(
            '''UPDATE stars_balances
               SET balance=?, stars_peak=MAX(stars_peak, ?)
               WHERE balance=0
                 AND NOT EXISTS (
                     SELECT 1 FROM stars_transactions t
                     WHERE t.user_id=stars_balances.user_id
                 )''',
            (default, default))

    def _migrate_premium_temporal(self, c: sqlite3.Cursor) -> None:
        c.execute('''
            CREATE TABLE IF NOT EXISTS app_migrations (
                name TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
        ''')
        for stmt in (
            'ALTER TABLE users ADD COLUMN premium_until INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN premium_trial_used INTEGER NOT NULL DEFAULT 0',
        ):
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass
        row = c.execute(
            "SELECT 1 FROM app_migrations WHERE name='premium_temporal_v1'"
        ).fetchone()
        if row:
            return
        bot_ids = (
            int(config.SERVICE_USER_ID),
            int(config.STICKERS_BOT_ID),
            int(config.REPORT_BOT_ID),
            int(config.PREMIUM_BOT_ID),
        )
        placeholders = ','.join('?' * len(bot_ids))
        c.execute(
            f'''UPDATE users SET premium=0, premium_until=0, premium_trial_used=0
                WHERE id NOT IN ({placeholders})''',
            bot_ids,
        )
        c.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES ('premium_temporal_v1', ?)",
            (int(time.time()),),
        )

    def _expire_premium_users(self, c: sqlite3.Cursor) -> None:
        now = int(time.time())
        c.execute(
            '''UPDATE users SET premium=0, display_gifts_button=0,
                   emoji_status_document_id=0, emoji_status_collectible_id=0,
                   emoji_status_until=0
               WHERE premium=1 AND premium_until > 0 AND premium_until < ?
                 AND is_bot=0''',
            (now,),
        )

    def _migrate_premium_gifts_cleanup(self, c: sqlite3.Cursor) -> None:
        c.execute('''
            CREATE TABLE IF NOT EXISTS app_migrations (
                name TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
        ''')
        row = c.execute(
            "SELECT 1 FROM app_migrations WHERE name='premium_gifts_cleanup_v1'"
        ).fetchone()
        if row:
            return
        c.execute(
            '''UPDATE users SET display_gifts_button=0,
                   emoji_status_document_id=0, emoji_status_collectible_id=0,
                   emoji_status_until=0
               WHERE premium=0 AND is_bot=0''',
        )
        c.execute(
            '''UPDATE users SET banned=0, ban_block_recreate=0,
                   banned_username=NULL, banned_first_name=NULL,
                   frozen=0, frozen_at=0, frozen_until=0
               WHERE id=?''',
            (int(config.PREMIUM_BOT_ID),),
        )
        c.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES ('premium_gifts_cleanup_v1', ?)",
            (int(time.time()),),
        )

    def _migrate_gifts_receive_allowed(self, c: sqlite3.Cursor) -> None:
        c.execute('''
            CREATE TABLE IF NOT EXISTS app_migrations (
                name TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
        ''')
        row = c.execute(
            "SELECT 1 FROM app_migrations WHERE name='gifts_receive_allowed_v1'"
        ).fetchone()
        if row:
            return
        c.execute(
            '''UPDATE users SET display_gifts_button=1
               WHERE is_bot=0 AND id!=?''',
            (int(config.SERVICE_USER_ID),),
        )
        c.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES ('gifts_receive_allowed_v1', ?)",
            (int(time.time()),),
        )

    def _migrate_premium_bot_unfreeze(self, c: sqlite3.Cursor) -> None:
        c.execute('''
            CREATE TABLE IF NOT EXISTS app_migrations (
                name TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
        ''')
        row = c.execute(
            "SELECT 1 FROM app_migrations WHERE name='premium_bot_unfreeze_v1'"
        ).fetchone()
        if row:
            return
        c.execute(
            '''UPDATE users SET frozen=0, frozen_at=0, frozen_until=0
               WHERE id=?''',
            (int(config.PREMIUM_BOT_ID),),
        )
        c.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES ('premium_bot_unfreeze_v1', ?)",
            (int(time.time()),),
        )

    def _migrate_premium_no_auto_grant(self, c: sqlite3.Cursor) -> None:
        """New accounts inherited premium=1 from a bad column default."""
        c.execute('''
            CREATE TABLE IF NOT EXISTS app_migrations (
                name TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
        ''')
        row = c.execute(
            "SELECT 1 FROM app_migrations WHERE name='premium_no_auto_grant_v1'"
        ).fetchone()
        if row:
            return
        c.execute(
            '''UPDATE users SET premium=0,
                   emoji_status_document_id=0, emoji_status_collectible_id=0,
                   emoji_status_until=0
               WHERE premium=1 AND premium_until=0 AND premium_trial_used=0
                 AND is_bot=0''',
        )
        c.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES ('premium_no_auto_grant_v1', ?)",
            (int(time.time()),),
        )

    def _clear_premium_profile_extras(self, user_id: int) -> None:
        self._conn.execute(
            '''UPDATE users SET display_gifts_button=0,
                   emoji_status_document_id=0, emoji_status_collectible_id=0,
                   emoji_status_until=0
               WHERE id=? AND is_bot=0''',
            (int(user_id),),
        )

    def _migrate_stars_tx_android_compat(self, c: sqlite3.Cursor) -> None:
        """Normalize inbound credits for client stars history / offer UI."""
        c.execute('''
            UPDATE stars_transactions
            SET title = 'Stars top-up', description = NULL
            WHERE amount > 0 AND refund = 0 AND offer = 0 AND gift = 0
              AND amount >= 10000000
              AND (title IS NULL OR title = '')
              AND description IS NOT NULL AND description != ''
        ''')
        c.execute('''
            UPDATE stars_transactions
            SET description = NULL
            WHERE title = 'Opening balance'
              AND description IS NOT NULL AND description != ''
        ''')
        c.execute('''
            UPDATE stars_transactions
            SET amount = 0, title = 'Stars top-up', description = NULL
            WHERE amount > 0 AND refund = 0 AND offer = 0 AND gift = 0
              AND amount >= 10000000
        ''')
        c.execute('''
            UPDATE stars_transactions
            SET title = description
            WHERE amount < 0 AND (title IS NULL OR title = '')
              AND description IS NOT NULL AND description != ''
        ''')
        c.execute('''
            UPDATE stars_transactions
            SET peer_user_id = ?
            WHERE peer_user_id IS NULL AND peer_channel_id IS NULL
        ''', (config.SERVICE_USER_ID,))

    def _migrate_telegram_phones(self, c: sqlite3.Cursor) -> None:
        c.execute('''
            INSERT OR IGNORE INTO telegram_phones
                (telegram_user_id, phone, source, created_at)
            SELECT telegram_user_id, phone, 'auto', created_at
            FROM bot_phone_assignments
        ''')

    def _migrate_star_gifts_schema(self, c: sqlite3.Cursor) -> None:
        c.execute('''
            CREATE TABLE IF NOT EXISTS star_gifts (
                gift_id               INTEGER PRIMARY KEY,
                stars                 INTEGER NOT NULL,
                convert_stars         INTEGER NOT NULL,
                upgrade_stars         INTEGER,
                limited               INTEGER NOT NULL DEFAULT 0,
                sold_out              INTEGER NOT NULL DEFAULT 0,
                birthday              INTEGER NOT NULL DEFAULT 0,
                require_premium       INTEGER NOT NULL DEFAULT 0,
                limited_per_user      INTEGER NOT NULL DEFAULT 0,
                availability_total    INTEGER,
                availability_remains  INTEGER,
                availability_resale   INTEGER NOT NULL DEFAULT 0,
                title                 TEXT,
                document_id           INTEGER NOT NULL,
                sort_order            INTEGER NOT NULL DEFAULT 0,
                released_by_peer_type INTEGER,
                released_by_peer_id   INTEGER,
                per_user_total        INTEGER,
                per_user_remains      INTEGER,
                locked_until_date     INTEGER,
                is_auction            INTEGER NOT NULL DEFAULT 0,
                auction_slug          TEXT,
                gifts_per_round       INTEGER,
                auction_start_date    INTEGER,
                first_sale_date       INTEGER,
                last_sale_date        INTEGER,
                resell_min_stars      INTEGER,
                peer_color_available  INTEGER NOT NULL DEFAULT 0,
                upgrade_variants      INTEGER NOT NULL DEFAULT 0,
                upgrade_importing     INTEGER NOT NULL DEFAULT 0,
                shop_hidden           INTEGER NOT NULL DEFAULT 0,
                is_tester_gift        INTEGER NOT NULL DEFAULT 0,
                auction_total_rounds  INTEGER,
                auction_round_duration INTEGER,
                auction_min_bid       INTEGER,
                auction_end_date      INTEGER,
                drop_at               INTEGER
            )
        ''')
        try:
            c.execute('ALTER TABLE star_gifts ADD COLUMN drop_at INTEGER')
        except sqlite3.OperationalError:
            pass
        c.execute('''
            CREATE TABLE IF NOT EXISTS star_gift_value_cache (
                gift_id               INTEGER PRIMARY KEY,
                currency              TEXT NOT NULL,
                value                 INTEGER NOT NULL,
                value_usd_amount     INTEGER NOT NULL DEFAULT 0,
                value_is_average      INTEGER NOT NULL DEFAULT 0,
                initial_sale_date     INTEGER NOT NULL DEFAULT 0,
                initial_sale_stars    INTEGER NOT NULL DEFAULT 0,
                initial_sale_price    INTEGER NOT NULL DEFAULT 0,
                last_sale_date        INTEGER,
                last_sale_price       INTEGER,
                last_sale_on_fragment INTEGER NOT NULL DEFAULT 0,
                floor_price           INTEGER,
                average_price         INTEGER,
                listed_count          INTEGER,
                fragment_listed_count INTEGER,
                fragment_listed_url   TEXT,
                source_slug           TEXT NOT NULL DEFAULT '',
                updated_at            INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS currency_exchange_rates (
                currency TEXT PRIMARY KEY,
                units_per_usd REAL NOT NULL,
                updated_at INTEGER NOT NULL
            )
        ''')
        c.execute(
            "INSERT OR IGNORE INTO currency_exchange_rates "
            "(currency, units_per_usd, updated_at) VALUES ('USD', 1, 0)"
        )
        c.execute('''
            CREATE TABLE IF NOT EXISTS star_gift_upgrade_attrs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                gift_id           INTEGER NOT NULL DEFAULT 0,
                attr_type         TEXT NOT NULL,
                name              TEXT NOT NULL,
                rarity_permille   INTEGER NOT NULL DEFAULT 100,
                document_id       INTEGER,
                backdrop_id       INTEGER,
                center_color      INTEGER,
                edge_color        INTEGER,
                pattern_color     INTEGER,
                text_color        INTEGER,
                crafted           INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_star_gift_upgrade_gift
            ON star_gift_upgrade_attrs(gift_id, attr_type)
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS star_gifts_catalog (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                hash INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            INSERT OR IGNORE INTO star_gifts_catalog (id, hash) VALUES (1, 0)
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS star_gift_auction_state (
                gift_id               INTEGER PRIMARY KEY,
                version               INTEGER NOT NULL DEFAULT 1,
                last_gift_num         INTEGER NOT NULL DEFAULT 0,
                last_processed_round  INTEGER NOT NULL DEFAULT 0,
                finished              INTEGER NOT NULL DEFAULT 0,
                average_price         INTEGER
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS star_gift_auction_bids (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                gift_id               INTEGER NOT NULL,
                user_id               INTEGER NOT NULL,
                bid_amount            INTEGER NOT NULL,
                bid_date              INTEGER NOT NULL,
                peer_type             INTEGER NOT NULL DEFAULT 0,
                peer_id               INTEGER NOT NULL,
                hide_name             INTEGER NOT NULL DEFAULT 0,
                message_text          TEXT NOT NULL DEFAULT '',
                message_entities_json TEXT NOT NULL DEFAULT '[]',
                returned              INTEGER NOT NULL DEFAULT 0,
                UNIQUE(gift_id, user_id)
            )
        ''')
        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_auction_bids_gift
            ON star_gift_auction_bids(gift_id, returned, bid_amount DESC)
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS star_gift_auction_acquired (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                gift_id               INTEGER NOT NULL,
                user_id               INTEGER NOT NULL,
                recipient_id          INTEGER NOT NULL,
                bid_amount            INTEGER NOT NULL,
                round_num             INTEGER NOT NULL,
                position              INTEGER NOT NULL,
                gift_num              INTEGER NOT NULL,
                date                  INTEGER NOT NULL,
                name_hidden           INTEGER NOT NULL DEFAULT 0,
                message_text          TEXT NOT NULL DEFAULT '',
                message_entities_json TEXT NOT NULL DEFAULT '[]',
                saved_id              INTEGER
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS saved_star_gifts (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id         INTEGER NOT NULL,
                from_user_id          INTEGER NOT NULL,
                gift_id               INTEGER NOT NULL,
                msg_id                INTEGER,
                saved_id              INTEGER UNIQUE,
                stars                 INTEGER,
                convert_stars         INTEGER,
                upgrade_stars         INTEGER,
                name_hidden           INTEGER NOT NULL DEFAULT 0,
                unsaved               INTEGER NOT NULL DEFAULT 0,
                pinned_to_top         INTEGER NOT NULL DEFAULT 0,
                date                  INTEGER NOT NULL,
                message_text          TEXT NOT NULL DEFAULT '',
                message_entities_json TEXT NOT NULL DEFAULT '[]',
                can_upgrade           INTEGER NOT NULL DEFAULT 0,
                upgraded              INTEGER NOT NULL DEFAULT 0,
                unique_id             INTEGER,
                unique_slug           TEXT,
                unique_num            INTEGER,
                original_details_kept INTEGER NOT NULL DEFAULT 1,
                model_attr_id         INTEGER,
                pattern_attr_id       INTEGER,
                backdrop_attr_id      INTEGER,
                resell_stars          INTEGER,
                resell_stars_nanos    INTEGER NOT NULL DEFAULT 0,
                resell_ton            INTEGER NOT NULL DEFAULT 0,
                burned                INTEGER NOT NULL DEFAULT 0,
                crafted               INTEGER NOT NULL DEFAULT 0,
                craft_chance_permille INTEGER NOT NULL DEFAULT 0,
                can_craft_at          INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS star_gift_offers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                random_id       INTEGER NOT NULL UNIQUE,
                buyer_id        INTEGER NOT NULL,
                owner_id        INTEGER NOT NULL,
                saved_id        INTEGER NOT NULL,
                slug            TEXT NOT NULL,
                price_stars     INTEGER NOT NULL DEFAULT 0,
                price_nanos     INTEGER NOT NULL DEFAULT 0,
                price_ton       INTEGER NOT NULL DEFAULT 0,
                duration        INTEGER NOT NULL,
                expires_at      INTEGER NOT NULL,
                offer_msg_id    INTEGER NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending'
            )
        ''')
        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_star_gift_offers_msg
            ON star_gift_offers(owner_id, offer_msg_id)
        ''')
        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_saved_star_gifts_owner
            ON saved_star_gifts(owner_user_id, date DESC)
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS stars_balances (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL,
                stars_peak INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS ton_balances (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS ton_transactions (
                tx_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                date INTEGER NOT NULL,
                title TEXT,
                peer_user_id INTEGER,
                gift INTEGER NOT NULL DEFAULT 0,
                stargift_resale INTEGER NOT NULL DEFAULT 0,
                offer INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            UPDATE star_gifts SET
                availability_resale = 0,
                resell_min_stars = NULL
            WHERE availability_resale != 0
               OR resell_min_stars IS NOT NULL
        ''')
        c.execute('''
            UPDATE star_gifts SET availability_remains = availability_total
            WHERE limited = 1
              AND availability_remains IS NULL
              AND availability_total > 0
        ''')
        self.recompute_star_gifts_catalog_hash()
        try:
            c.execute(
                'UPDATE saved_star_gifts SET can_upgrade=0 WHERE upgraded=0'
            )
        except sqlite3.OperationalError:
            pass
        try:
            c.execute(
                'ALTER TABLE saved_star_gifts ADD COLUMN '
                'prepaid_upgrade INTEGER NOT NULL DEFAULT 0'
            )
        except sqlite3.OperationalError:
            pass
        try:
            c.execute(
                '''UPDATE saved_star_gifts
                   SET upgrade_stars=NULL
                   WHERE upgraded=0 AND prepaid_upgrade=0'''
            )
        except sqlite3.OperationalError:
            pass
        c.execute('''
            UPDATE sticker_sets SET count=(
                SELECT COUNT(*) FROM sticker_documents
                WHERE sticker_documents.set_id = sticker_sets.id
            )
            WHERE short_name='__stargifts__'
        ''')
        self._repair_broken_star_gift_messages(c)
        c.execute('''
            CREATE TABLE IF NOT EXISTS stars_ratings (
                user_id     INTEGER PRIMARY KEY,
                points      INTEGER NOT NULL DEFAULT 0,
                updated_at  INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_saved_music (
                user_id      INTEGER NOT NULL,
                document_id  INTEGER NOT NULL,
                sort_order   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, document_id)
            )
        ''')
        c.execute(
            'CREATE INDEX IF NOT EXISTS idx_user_saved_music_order '
            'ON user_saved_music(user_id, sort_order)'
        )
        os.makedirs(config.STAR_GIFTS_DATA_DIR, exist_ok=True)

    def _repair_broken_star_gift_messages(self, c: sqlite3.Cursor) -> None:
        """Fix service messages referencing missing gift_id placeholders."""
        rows = c.execute(
            '''SELECT owner_id, id, from_id, peer_id, date, action_json
               FROM messages
               WHERE action_json LIKE '%"gift_id": 999999001%'
                  OR action_json LIKE '%"gift_id":999999001%' '''
        ).fetchall()
        if not rows:
            return
        fallback = c.execute(
            'SELECT gift_id, convert_stars, upgrade_stars FROM star_gifts '
            'WHERE is_auction=1 ORDER BY gift_id LIMIT 1'
        ).fetchone()
        if not fallback:
            fallback = c.execute(
                'SELECT gift_id, convert_stars, upgrade_stars FROM star_gifts '
                'ORDER BY gift_id LIMIT 1'
            ).fetchone()
        if not fallback:
            return
        gift_id = int(fallback['gift_id'])
        log.info('Repairing %d star gift messages -> gift_id %s',
                 len(rows), gift_id)
        for row in rows:
            try:
                action = json.loads(row['action_json'])
            except json.JSONDecodeError:
                continue
            action['gift_id'] = gift_id
            c.execute(
                'UPDATE messages SET action_json=? WHERE owner_id=? AND id=?',
                (json.dumps(action, ensure_ascii=False),
                 int(row['owner_id']), int(row['id'])))
            owner_id = int(row['owner_id'])
            msg_id = int(row['id'])
            saved = c.execute(
                'SELECT saved_id FROM saved_star_gifts WHERE msg_id=? AND owner_user_id=?',
                (msg_id, owner_id)).fetchone()
            if saved:
                continue
            from_id = int(row['from_id'])
            c.execute(
                '''INSERT OR IGNORE INTO saved_star_gifts
                   (owner_user_id, from_user_id, gift_id, msg_id, saved_id,
                    stars, convert_stars, upgrade_stars, name_hidden, unsaved,
                    pinned_to_top, date, message_text, message_entities_json,
                    can_upgrade, upgraded)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (owner_id, from_id, gift_id, msg_id, msg_id,
                 int(action.get('bid_amount') or fallback['convert_stars'] or 0),
                 int(action.get('convert_stars') or fallback['convert_stars'] or 0),
                 None,
                 int(action.get('name_hidden') or 0), 0, 0,
                 int(row['date']),
                 action.get('message_text') or '',
                 json.dumps(action.get('message_entities') or [],
                            ensure_ascii=False),
                 0, 0))

    def _migrate_messages_schema(self, c: sqlite3.Cursor) -> None:
        row = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
        if not row or not row['sql']:
            return
        if 'PRIMARY KEY (owner_id, id)' in row['sql']:
            return
        log.info('Migrating messages table to composite primary key')
        c.executescript('''
            CREATE TABLE IF NOT EXISTS messages_new (
                owner_id      INTEGER NOT NULL,
                id            INTEGER NOT NULL,
                from_id       INTEGER NOT NULL,
                peer_id       INTEGER NOT NULL,
                date          INTEGER NOT NULL,
                text          TEXT NOT NULL,
                entities      TEXT NOT NULL DEFAULT '[]',
                pts           INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (owner_id, id)
            );
            INSERT INTO messages_new
                (owner_id, id, from_id, peer_id, date, text, entities, pts)
            SELECT owner_id, id, from_id, peer_id, date, text, entities, pts
            FROM messages;
            DROP TABLE messages;
            ALTER TABLE messages_new RENAME TO messages;
        ''')
        for stmt in (
            'ALTER TABLE messages ADD COLUMN reply_to_msg_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN media_document_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN media_photo_id INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN action_json TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE messages ADD COLUMN reply_markup_json TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE messages ADD COLUMN paid_message_stars INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE messages ADD COLUMN media_poll_json TEXT NOT NULL DEFAULT ""',
            'ALTER TABLE messages ADD COLUMN saved_peer_id INTEGER NOT NULL DEFAULT 0',
        ):
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass

    def _normalize_message_dates(self, c: sqlite3.Cursor) -> None:
        """Ensure message.date grows with message.id inside each mailbox."""
        peers = c.execute(
            'SELECT DISTINCT owner_id, peer_id FROM messages'
        ).fetchall()
        for peer in peers:
            owner_id = peer['owner_id']
            peer_id = peer['peer_id']
            rows = c.execute(
                '''SELECT owner_id, id, date FROM messages
                   WHERE owner_id=? AND peer_id=?
                   ORDER BY id ASC''',
                (owner_id, peer_id),
            ).fetchall()
            prev_date = 0
            for row in rows:
                cur_date = int(row['date'])
                if cur_date <= prev_date:
                    cur_date = prev_date + 1
                    c.execute(
                        'UPDATE messages SET date=? WHERE owner_id=? AND id=?',
                        (cur_date, owner_id, row['id']),
                    )
                prev_date = cur_date

    def _normalize_sticker_sets(self, c: sqlite3.Cursor) -> None:
        rows = c.execute('SELECT id, hash, flags FROM sticker_sets').fetchall()
        for row in rows:
            nh = self._i32_hash(row['hash'])
            if nh != row['hash']:
                c.execute(
                    'UPDATE sticker_sets SET hash=? WHERE id=?',
                    (nh, row['id'])
                )
        row = c.execute(
            "SELECT 1 FROM server_kv WHERE key='stickers_no_autoinstall'"
        ).fetchone()
        if not row:
            c.execute('UPDATE sticker_sets SET flags = flags & ~1')
            c.execute(
                "INSERT OR IGNORE INTO server_kv (key, value) "
                "VALUES ('stickers_no_autoinstall', '1')"
            )

    def _seed_service_user(self) -> None:
        row = self._conn.execute(
            'SELECT 1 FROM users WHERE id=?', (SERVICE_USER_ID,)
        ).fetchone()
        if row:
            return
        self._conn.execute(
            '''INSERT INTO users (id, phone, first_name, last_name, username,
                                  access_hash, about, created_at)
               VALUES (?,?,?,?,?,?,?,?)''',
            (SERVICE_USER_ID, '', config.SERVICE_USER_NAME, '', 'airgram',
             config.SERVICE_USER_ACCESS_HASH, '', int(time.time()))
        )
        self._conn.commit()

    def _ensure_service_user_branding(self) -> None:
        """Keep service account name/avatar in sync with config."""
        self._conn.execute(
            '''UPDATE users SET first_name=?, username=COALESCE(NULLIF(username,''), 'airgram')
               WHERE id=?''',
            (config.SERVICE_USER_NAME, SERVICE_USER_ID),
        )
        avatar_src = Path(config.SERVICE_AVATAR_PATH)
        if not avatar_src.is_file():
            self._conn.commit()
            return
        dest = Path(config.PHOTOS_DATA_DIR) / 'service_airgram.jpg'
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(avatar_src, dest)
        root = Path(__file__).resolve().parent.parent
        try:
            rel = dest.relative_to(root)
        except ValueError:
            rel = dest
        size = dest.stat().st_size
        width = height = 512
        try:
            from handlers.media import _image_size
            width, height = _image_size(str(dest))
        except Exception:
            pass
        user = self.get_user_by_id(SERVICE_USER_ID)
        photo_id = int((user or {}).get('profile_photo_id') or 0)
        if photo_id:
            self._conn.execute(
                '''UPDATE photos SET file_path=?, width=?, height=?, size=?, date=?
                   WHERE id=?''',
                (str(rel), width, height, size, int(time.time()), photo_id),
            )
        else:
            photo = self.create_photo(
                owner_id=SERVICE_USER_ID,
                file_path=str(rel),
                width=width,
                height=height,
                size=size,
            )
            self.set_user_profile_photo(SERVICE_USER_ID, photo['id'])
        self._conn.commit()

    def _seed_demo_bot(self) -> None:
        row = self._conn.execute(
            'SELECT 1 FROM users WHERE username=?', ('mybot',)
        ).fetchone()
        if row:
            return
        self._conn.execute(
            '''INSERT INTO users (id, phone, first_name, last_name, username,
                                  access_hash, about, is_bot, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)''',
            (888888, '888888888888', 'My', 'Bot', 'mybot',
             0x0123456789ABCDEF, 'Demo bot', 1, int(time.time()))
        )
        self._conn.commit()

    def _sync_user_avatar(self, user_id: int, avatar_path: str,
                          dest_name: str) -> None:
        avatar_src = Path(avatar_path)
        if not avatar_src.is_file():
            self._conn.commit()
            return
        dest = Path(config.PHOTOS_DATA_DIR) / dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(avatar_src, dest)
        root = Path(__file__).resolve().parent.parent
        try:
            rel = dest.relative_to(root)
        except ValueError:
            rel = dest
        size = dest.stat().st_size
        width = height = 512
        try:
            from handlers.media import _image_size
            width, height = _image_size(str(dest))
        except Exception:
            pass
        user = self.get_user_by_id(user_id)
        photo_id = int((user or {}).get('profile_photo_id') or 0)
        if photo_id:
            self._conn.execute(
                '''UPDATE photos SET file_path=?, width=?, height=?, size=?, date=?
                   WHERE id=?''',
                (str(rel), width, height, size, int(time.time()), photo_id),
            )
        else:
            photo = self.create_photo(
                owner_id=user_id,
                file_path=str(rel),
                width=width,
                height=height,
                size=size,
            )
            self.set_user_profile_photo(user_id, photo['id'])
        self._conn.commit()

    def _seed_stickers_bot(self) -> None:
        bot_id = int(config.STICKERS_BOT_ID)
        row = self._conn.execute(
            'SELECT 1 FROM users WHERE id=?', (bot_id,)
        ).fetchone()
        now = int(time.time())
        if not row:
            self._conn.execute(
                '''INSERT INTO users (id, phone, first_name, last_name, username,
                                      access_hash, about, is_bot, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)''',
                (
                    bot_id,
                    str(bot_id),
                    config.STICKERS_BOT_NAME,
                    '',
                    config.STICKERS_BOT_USERNAME,
                    config.STICKERS_BOT_ACCESS_HASH,
                    config.STICKERS_BOT_ABOUT,
                    1,
                    now,
                ),
            )
        else:
            self._conn.execute(
                '''UPDATE users SET first_name=?, last_name=?, username=?,
                                  access_hash=?, about=?, is_bot=1, premium=0,
                                  noncontact_peers_paid_stars=0
                   WHERE id=?''',
                (
                    config.STICKERS_BOT_NAME,
                    '',
                    config.STICKERS_BOT_USERNAME,
                    config.STICKERS_BOT_ACCESS_HASH,
                    config.STICKERS_BOT_ABOUT,
                    bot_id,
                ),
            )
        self._conn.commit()
        self._conn.execute(
            '''UPDATE users
               SET premium=0, noncontact_peers_paid_stars=0
               WHERE id=?''',
            (bot_id,),
        )
        self._conn.commit()
        self._sync_user_avatar(
            bot_id,
            config.STICKERS_BOT_AVATAR_PATH,
            'stickers_bot.jpg',
        )
        self.delete_bot_verification(bot_id)

    def _seed_report_bot(self) -> None:
        bot_id = int(config.REPORT_BOT_ID)
        row = self._conn.execute(
            'SELECT 1 FROM users WHERE id=?', (bot_id,)
        ).fetchone()
        now = int(time.time())
        if not row:
            self._conn.execute(
                '''INSERT INTO users (id, phone, first_name, last_name, username,
                                      access_hash, about, is_bot, premium,
                                      noncontact_peers_paid_stars, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    bot_id,
                    str(bot_id),
                    config.REPORT_BOT_NAME,
                    '',
                    config.REPORT_BOT_USERNAME,
                    config.REPORT_BOT_ACCESS_HASH,
                    config.REPORT_BOT_ABOUT,
                    1,
                    0,
                    0,
                    now,
                ),
            )
        else:
            self._conn.execute(
                '''UPDATE users SET first_name=?, last_name=?, username=?,
                                  access_hash=?, about=?, is_bot=1, premium=0,
                                  noncontact_peers_paid_stars=0
                   WHERE id=?''',
                (
                    config.REPORT_BOT_NAME,
                    '',
                    config.REPORT_BOT_USERNAME,
                    config.REPORT_BOT_ACCESS_HASH,
                    config.REPORT_BOT_ABOUT,
                    bot_id,
                ),
            )
        self._conn.commit()
        self._sync_user_avatar(
            bot_id,
            config.REPORT_BOT_AVATAR_PATH,
            'report_bot.jpg',
        )
        self.delete_bot_verification(bot_id)

    def _seed_premium_bot(self) -> None:
        bot_id = int(config.PREMIUM_BOT_ID)
        row = self._conn.execute(
            'SELECT 1 FROM users WHERE id=?', (bot_id,)
        ).fetchone()
        now = int(time.time())
        if not row:
            self._conn.execute(
                '''INSERT INTO users (id, phone, first_name, last_name, username,
                                      access_hash, about, is_bot, premium,
                                      noncontact_peers_paid_stars, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    bot_id,
                    str(bot_id),
                    config.PREMIUM_BOT_NAME,
                    '',
                    config.PREMIUM_BOT_USERNAME,
                    config.PREMIUM_BOT_ACCESS_HASH,
                    config.PREMIUM_BOT_ABOUT,
                    1,
                    0,
                    0,
                    now,
                ),
            )
        else:
            self._conn.execute(
                '''UPDATE users SET first_name=?, last_name=?, username=?,
                                  access_hash=?, about=?, is_bot=1, premium=0,
                                  noncontact_peers_paid_stars=0,
                                  banned=0, ban_block_recreate=0,
                                  banned_username=NULL, banned_first_name=NULL,
                                  frozen=0, frozen_at=0, frozen_until=0
                   WHERE id=?''',
                (
                    config.PREMIUM_BOT_NAME,
                    '',
                    config.PREMIUM_BOT_USERNAME,
                    config.PREMIUM_BOT_ACCESS_HASH,
                    config.PREMIUM_BOT_ABOUT,
                    bot_id,
                ),
            )
        self._conn.commit()
        self._sync_user_avatar(
            bot_id,
            config.PREMIUM_BOT_AVATAR_PATH,
            'premium_bot.png',
        )
        self.delete_bot_verification(bot_id)

    # ── Auth keys ──────────────────────────────

    def save_auth_key(self, auth_key_id: int, auth_key: bytes):
        self._conn.execute(
            'INSERT OR REPLACE INTO auth_keys VALUES (?,?,?)',
            (auth_key_id, auth_key, int(time.time()))
        )
        self._conn.commit()

    def get_auth_key(self, auth_key_id: int) -> Optional[bytes]:
        row = self._conn.execute(
            'SELECT auth_key FROM auth_keys WHERE auth_key_id=?', (auth_key_id,)
        ).fetchone()
        return bytes(row['auth_key']) if row else None

    def count_auth_keys(self) -> int:
        row = self._conn.execute('SELECT COUNT(*) AS n FROM auth_keys').fetchone()
        return int(row['n'])

    def count_sessions(self) -> int:
        row = self._conn.execute('SELECT COUNT(*) AS n FROM sessions').fetchone()
        return int(row['n'])

    def reset_auth_state(self) -> dict:
        """Clear sessions/auth keys and transient login state; keep users and content."""
        tables = (
            'pending_updates',
            'pending_srp',
            'pending_sign_in',
            'pending_login_messages',
            'phone_codes',
            'user_passwords',
            'temp_auth_bindings',
            'sessions',
            'auth_keys',
        )
        counts = {}
        for table in tables:
            row = self._conn.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()
            counts[table] = int(row['n'])
            self._conn.execute(f'DELETE FROM {table}')
        self._conn.commit()
        log.info('Auth state reset: %s', counts)
        return counts

    def clear_session(self, auth_key_id: int) -> None:
        self._conn.execute(
            'DELETE FROM sessions WHERE auth_key_id=?', (auth_key_id,)
        )
        self._conn.commit()

    def save_temp_auth_binding(self, temp_auth_key_id: int,
                               perm_auth_key_id: int,
                               expires_at: int = 0) -> None:
        self._conn.execute(
            '''INSERT INTO temp_auth_bindings
               (temp_auth_key_id, perm_auth_key_id, expires_at, created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(temp_auth_key_id) DO UPDATE SET
                 perm_auth_key_id=excluded.perm_auth_key_id,
                 expires_at=excluded.expires_at,
                 created_at=excluded.created_at''',
            (int(temp_auth_key_id), int(perm_auth_key_id),
             int(expires_at or 0), int(time.time())),
        )
        self._conn.commit()

    def get_temp_auth_perm(self, temp_auth_key_id: int) -> Optional[int]:
        now = int(time.time())
        row = self._conn.execute(
            '''SELECT perm_auth_key_id FROM temp_auth_bindings
               WHERE temp_auth_key_id=?
                 AND (expires_at=0 OR expires_at>?)''',
            (int(temp_auth_key_id), now),
        ).fetchone()
        return int(row['perm_auth_key_id']) if row else None

    def _linked_auth_keys(self, auth_key_id: int) -> List[int]:
        now = int(time.time())
        keys = {int(auth_key_id)}
        perm = self.get_temp_auth_perm(auth_key_id)
        if perm is not None:
            keys.add(int(perm))
        perms = [int(k) for k in keys]
        q = ','.join('?' for _ in perms)
        rows = self._conn.execute(
            f'''SELECT temp_auth_key_id FROM temp_auth_bindings
                WHERE perm_auth_key_id IN ({q})
                  AND (expires_at=0 OR expires_at>?)''',
            (*perms, now),
        ).fetchall()
        for row in rows:
            keys.add(int(row['temp_auth_key_id']))
        return list(keys)

    def resolve_session_user(self, auth_key_id: int) -> Optional[int]:
        user_id = self.get_session_user(auth_key_id)
        if user_id:
            return int(user_id)
        keys = self._linked_auth_keys(auth_key_id)
        if len(keys) <= 1:
            return None
        q = ','.join('?' for _ in keys)
        row = self._conn.execute(
            f'''SELECT user_id FROM sessions
                WHERE auth_key_id IN ({q})
                ORDER BY created_at DESC LIMIT 1''',
            keys,
        ).fetchone()
        return int(row['user_id']) if row else None

    def bind_related_auth_keys(self, auth_key_id: int, user_id: int) -> None:
        for key_id in self._linked_auth_keys(auth_key_id):
            if self.get_session_user(int(key_id)) != int(user_id):
                self.bind_session(int(key_id), int(user_id))

    # ── Users ──────────────────────────────────

    def _row_to_user(self, row) -> dict:
        d = dict(row)
        d['is_bot'] = bool(d.get('is_bot'))
        d['banned'] = bool(d.get('banned'))
        d['ban_block_recreate'] = bool(d.get('ban_block_recreate'))
        d['frozen'] = bool(d.get('frozen'))
        d['scam'] = bool(d.get('scam'))
        d['fake'] = bool(d.get('fake'))
        if int(d.get('id') or 0) in {
            int(config.STICKERS_BOT_ID),
            int(config.REPORT_BOT_ID),
            int(config.PREMIUM_BOT_ID),
        }:
            d['premium'] = False
            d['verified'] = True
            d['noncontact_peers_paid_stars'] = 0
        elif config.ALL_USERS_PREMIUM and d.get('id') != SERVICE_USER_ID:
            d['premium'] = True
        else:
            now = int(time.time())
            until = int(d.get('premium_until') or 0)
            active = bool(int(d.get('premium') or 0))
            if until > 0 and until <= now:
                active = False
            d['premium'] = active
        bv = self.get_bot_verification(d['id'])
        if bv and int(d.get('id') or 0) not in {
            int(config.STICKERS_BOT_ID),
            int(config.REPORT_BOT_ID),
            int(config.PREMIUM_BOT_ID),
        }:
            d['bot_verification_icon'] = bv['icon_id']
            d['bot_verification'] = bv
        return d

    def _sync_bot_verification_descriptions(self, c: sqlite3.Cursor) -> None:
        try:
            from frame_verification.catalog import (
                get_verification_description,
                load_catalog,
            )
            by_org: dict[str, str] = {}
            for vdef in load_catalog().values():
                org = (vdef.get('organization') or '').strip()
                if org:
                    by_org[org] = get_verification_description(vdef)
        except Exception:
            return
        for row in c.execute(
                'SELECT user_id, company, description FROM bot_verifications'
        ).fetchall():
            org = (row['company'] or '').strip()
            expected = by_org.get(org)
            if not expected:
                if not (row['description'] or '').strip():
                    from frame_verification.catalog import description_for_organization
                    expected = description_for_organization(org)
                else:
                    continue
            if (row['description'] or '').strip() != expected:
                c.execute(
                    'UPDATE bot_verifications SET description=? WHERE user_id=?',
                    (expected, row['user_id']))

    def _purge_expired_bot_verifications(
            self, c: sqlite3.Cursor, *, now: int | None = None) -> list[int]:
        now = int(now or time.time())
        rows = c.execute(
            '''SELECT user_id FROM bot_verifications
               WHERE expires_at IS NOT NULL AND expires_at <= ?''',
            (now,),
        ).fetchall()
        user_ids = [int(row['user_id']) for row in rows]
        if not user_ids:
            return []
        placeholders = ','.join('?' * len(user_ids))
        c.execute(
            f'DELETE FROM bot_verifications WHERE user_id IN ({placeholders})',
            user_ids,
        )
        return user_ids

    def purge_expired_bot_verifications(self, *, now: int | None = None) -> list[int]:
        user_ids = self._purge_expired_bot_verifications(
            self._conn.cursor(), now=now)
        if user_ids:
            self._conn.commit()
        return user_ids

    def get_bot_verification(self, user_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM bot_verifications WHERE user_id=?', (user_id,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        expires_at = data.get('expires_at')
        if expires_at and int(expires_at) <= int(time.time()):
            self.delete_bot_verification(user_id)
            return None
        return data

    def set_bot_verification(self, user_id: int, bot_id: int, icon_id: int,
                             company: str, description: str = '',
                             expires_at: int | None = None) -> dict:
        company = (company or '').strip() or 'организацией'
        if not description:
            from frame_verification.catalog import description_for_organization
            description = description_for_organization(company)
        self._conn.execute(
            '''INSERT OR REPLACE INTO bot_verifications
               (user_id, bot_id, icon_id, company, description, expires_at)
               VALUES (?,?,?,?,?,?)''',
            (user_id, bot_id, icon_id, company, description, expires_at),
        )
        self._conn.commit()
        return self.get_bot_verification(user_id)

    def delete_bot_verification(self, user_id: int) -> None:
        self._conn.execute(
            'DELETE FROM bot_verifications WHERE user_id=?', (user_id,)
        )
        self._conn.commit()

    def delete_bot_verification(self, user_id: int) -> None:
        self._conn.execute(
            'DELETE FROM bot_verifications WHERE user_id=?', (user_id,)
        )
        self._conn.commit()

    # ── Paid messages ──────────────────────────

    def get_user_paid_message_price(self, user_id: int) -> int:
        row = self._conn.execute(
            'SELECT noncontact_peers_paid_stars FROM users WHERE id=?',
            (user_id,),
        ).fetchone()
        return int(row['noncontact_peers_paid_stars'] or 0) if row else 0

    def set_user_paid_message_price(self, user_id: int, stars: int) -> int:
        import config as cfg
        stars = max(0, min(int(stars), cfg.STARS_PAID_MESSAGE_AMOUNT_MAX))
        self._conn.execute(
            'UPDATE users SET noncontact_peers_paid_stars=? WHERE id=?',
            (stars, user_id),
        )
        self._conn.commit()
        return stars

    def list_paid_message_exceptions(self, owner_id: int) -> List[int]:
        rows = self._conn.execute(
            'SELECT exempt_user_id FROM paid_message_exceptions '
            'WHERE owner_user_id=? ORDER BY exempt_user_id',
            (owner_id,),
        ).fetchall()
        return [int(r['exempt_user_id']) for r in rows]

    def set_paid_message_exceptions(self, owner_id: int,
                                    user_ids: List[int]) -> None:
        c = self._conn.cursor()
        c.execute(
            'DELETE FROM paid_message_exceptions WHERE owner_user_id=?',
            (owner_id,),
        )
        for uid in user_ids:
            c.execute(
                'INSERT OR IGNORE INTO paid_message_exceptions '
                '(owner_user_id, exempt_user_id) VALUES (?, ?)',
                (owner_id, int(uid)),
            )
        self._conn.commit()

    def add_paid_message_exception(self, owner_id: int,
                                   exempt_user_id: int) -> None:
        self._conn.execute(
            'INSERT OR IGNORE INTO paid_message_exceptions '
            '(owner_user_id, exempt_user_id) VALUES (?, ?)',
            (owner_id, int(exempt_user_id)),
        )
        self._conn.commit()

    def remove_paid_message_exception(self, owner_id: int,
                                      exempt_user_id: int) -> None:
        self._conn.execute(
            'DELETE FROM paid_message_exceptions '
            'WHERE owner_user_id=? AND exempt_user_id=?',
            (owner_id, int(exempt_user_id)),
        )
        self._conn.commit()

    def list_dialog_peer_ids(self, owner_id: int) -> List[int]:
        rows = self._conn.execute(
            'SELECT peer_id FROM dialogs WHERE owner_id=? AND peer_id!=?',
            (owner_id, owner_id),
        ).fetchall()
        return [int(r['peer_id']) for r in rows]

    def record_paid_message_payment(self, recipient_id: int, sender_id: int,
                                    msg_id: int, stars: int) -> None:
        if stars <= 0:
            return
        self._conn.execute(
            '''INSERT OR IGNORE INTO paid_message_payments
               (recipient_id, sender_id, msg_id, stars, created_at)
               VALUES (?,?,?,?,?)''',
            (recipient_id, sender_id, msg_id, stars, int(time.time())),
        )
        self._conn.commit()

    def get_paid_messages_revenue(self, recipient_id: int,
                                  sender_id: int) -> int:
        row = self._conn.execute(
            '''SELECT COALESCE(SUM(stars), 0) AS total
               FROM paid_message_payments
               WHERE recipient_id=? AND sender_id=? AND refunded=0''',
            (recipient_id, sender_id),
        ).fetchone()
        return int(row['total'] or 0)

    def refund_paid_messages_from_user(self, recipient_id: int,
                                       sender_id: int) -> tuple[int, int]:
        rows = self._conn.execute(
            '''SELECT msg_id, stars FROM paid_message_payments
               WHERE recipient_id=? AND sender_id=? AND refunded=0''',
            (recipient_id, sender_id),
        ).fetchall()
        if not rows:
            return 0, 0
        total = sum(int(r['stars']) for r in rows)
        count = len(rows)
        self._conn.execute(
            '''UPDATE paid_message_payments SET refunded=1
               WHERE recipient_id=? AND sender_id=? AND refunded=0''',
            (recipient_id, sender_id),
        )
        import config as cfg
        commission = cfg.STARS_PAID_MESSAGE_COMMISSION_PERMILLE
        for row in rows:
            stars = int(row['stars'])
            received = stars * commission // 1000
            msg_id = int(row['msg_id'])
            if received > 0:
                self.adjust_stars_balance(
                    recipient_id, -received, paid_message=True,
                    peer_user_id=sender_id, msg_id=msg_id, refund=True)
            self.adjust_stars_balance(
                sender_id, stars, paid_message=True,
                peer_user_id=recipient_id, msg_id=msg_id, refund=True)
        self._conn.commit()
        return count, total

    def recipient_messaged_peer_first(self, recipient_id: int,
                                      sender_id: int) -> bool:
        """True if recipient sent a real (non-service) message to sender first."""
        row = self._conn.execute(
            '''SELECT 1 FROM messages
               WHERE owner_id=? AND from_id=? AND peer_id=?
               AND (action_json IS NULL OR action_json = '')
               LIMIT 1''',
            (recipient_id, recipient_id, sender_id),
        ).fetchone()
        return bool(row)

    def is_paid_message_exempt(self, recipient_id: int,
                               sender_id: int) -> bool:
        if sender_id == recipient_id:
            return True
        if sender_id in self.list_paid_message_exceptions(recipient_id):
            return True
        if self.recipient_messaged_peer_first(recipient_id, sender_id):
            return True
        return False

    def get_required_paid_message_stars(self, recipient_id: int,
                                        sender_id: int) -> int:
        import config as cfg
        if not cfg.STARS_PAID_MESSAGES_AVAILABLE:
            return 0
        price = self.get_user_paid_message_price(recipient_id)
        if price <= 0:
            return 0
        recipient = self.get_user_by_id(recipient_id)
        if not recipient or recipient.get('is_bot'):
            return 0
        if self.is_paid_message_exempt(recipient_id, sender_id):
            return 0
        return price

    def charge_paid_message(self, sender_id: int, recipient_id: int,
                            stars: int, msg_id: int = 0) -> int:
        import config as cfg
        if stars <= 0:
            return self.get_stars_balance(sender_id)
        commission = cfg.STARS_PAID_MESSAGE_COMMISSION_PERMILLE
        received = stars * commission // 1000
        new_balance = self.adjust_stars_balance(
            sender_id, -stars, paid_message=True,
            peer_user_id=recipient_id, msg_id=msg_id)
        if received > 0:
            self.adjust_stars_balance(
                recipient_id, received, paid_message=True,
                peer_user_id=sender_id, msg_id=msg_id)
        if msg_id:
            self.record_paid_message_payment(
                recipient_id, sender_id, msg_id, stars)
        return new_balance

    def create_user(self, phone: str, first_name: str, last_name: str) -> Dict[str, Any]:
        phone = normalize_phone(phone)
        access_hash = self._random_access_hash()
        self._conn.execute(
            '''INSERT OR IGNORE INTO users
               (phone, first_name, last_name, access_hash, about, created_at,
                premium, premium_until, premium_trial_used)
               VALUES (?,?,?,?,?,?,0,0,0)''',
            (phone, first_name, last_name, access_hash, '', int(time.time()))
        )
        self._conn.commit()
        user = self.get_user_by_phone(phone)
        if user:
            opening_balance = int(config.DEFAULT_STARS_BALANCE)
            self._conn.execute(
                '''INSERT OR IGNORE INTO stars_balances (user_id, balance, stars_peak)
                   VALUES (?, ?, ?)''',
                (int(user['id']), opening_balance, opening_balance),
            )
            self._conn.commit()
            self.ensure_user_stars(int(user['id']))
        return user

    def get_user_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        from handlers.phone_countries import anon_phone_lookup_variants
        for variant in anon_phone_lookup_variants(phone):
            row = self._conn.execute(
                'SELECT * FROM users WHERE phone=?', (variant,)
            ).fetchone()
            if row:
                return self._row_to_user(row)
        phone = normalize_phone(phone)
        row = self._conn.execute(
            'SELECT * FROM users WHERE phone=?', (phone,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            'SELECT * FROM users WHERE id=?', (user_id,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def is_user_premium(self, user_id: int) -> bool:
        user = self.get_user_by_id(int(user_id))
        return bool(user and user.get('premium'))

    def grant_premium(self, user_id: int, *, days: int = 0,
                      until_ts: Optional[int] = None,
                      trial: bool = False) -> dict:
        uid = int(user_id)
        user = self.get_user_by_id(uid)
        if not user:
            raise ValueError('USER_ID_INVALID')
        now = int(time.time())
        if until_ts is not None:
            end = int(until_ts)
        elif days > 0:
            end = now + int(days) * 86400
        else:
            end = 0
        if trial:
            if int(user.get('premium_trial_used') or 0):
                raise ValueError('PREMIUM_TRIAL_ALREADY_USED')
            if end <= now:
                end = now + int(config.PREMIUM_TRIAL_DAYS) * 86400
            self._conn.execute(
                '''UPDATE users SET premium=1, premium_until=?, premium_trial_used=1
                   WHERE id=?''',
                (end, uid),
            )
        else:
            self._conn.execute(
                'UPDATE users SET premium=1, premium_until=? WHERE id=?',
                (end, uid),
            )
        self._conn.commit()
        return self.get_user_by_id(uid)

    def revoke_premium(self, user_id: int) -> dict:
        uid = int(user_id)
        if not self.get_user_by_id(uid):
            raise ValueError('USER_ID_INVALID')
        self._conn.execute(
            'UPDATE users SET premium=0, premium_until=0 WHERE id=?',
            (uid,),
        )
        self._clear_premium_profile_extras(uid)
        self._conn.commit()
        return self.get_user_by_id(uid)

    def set_user_safety_label(self, user_id: int, label: str) -> dict:
        uid = int(user_id)
        user = self.get_user_by_id(uid)
        if not user:
            raise ValueError('USER_ID_INVALID')
        normalized = (label or '').strip().lower()
        if normalized not in {'none', 'scam', 'fake'}:
            raise ValueError('USER_LABEL_INVALID')
        was_labeled = bool(user.get('scam') or user.get('fake'))
        if normalized == 'none':
            self._conn.execute(
                '''UPDATE users
                   SET scam=0, fake=0, about=label_hidden_about,
                       label_hidden_about=''
                   WHERE id=?''',
                (uid,),
            )
        elif was_labeled:
            self._conn.execute(
                'UPDATE users SET scam=?, fake=? WHERE id=?',
                (int(normalized == 'scam'), int(normalized == 'fake'), uid),
            )
        else:
            self._conn.execute(
                '''UPDATE users
                   SET scam=?, fake=?, label_hidden_about=about, about=''
                   WHERE id=?''',
                (int(normalized == 'scam'), int(normalized == 'fake'), uid),
            )
        self._conn.commit()
        return self.get_user_by_id(uid)

    def safety_label_about(self, user: dict, *, viewer_is_self: bool = False) -> str:
        if user.get('scam'):
            return config.SCAM_ACCOUNT_ABOUT
        if user.get('fake'):
            return config.FAKE_ACCOUNT_ABOUT
        return (user.get('about') or '').strip()

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        username = normalize_collectible_username(username)
        row = self._conn.execute(
            'SELECT * FROM users WHERE lower(username)=?', (username,)
        ).fetchone()
        if row:
            return self._row_to_user(row)
        row = self._conn.execute(
            '''SELECT u.* FROM collectible_usernames cu
               JOIN users u ON u.id=cu.user_id
               WHERE lower(cu.username)=? AND cu.active=1''',
            (username,),
        ).fetchone()
        return self._row_to_user(row) if row else None

    def search_users(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        q = query.strip().lstrip('@').lower()
        if not q:
            return []
        pattern = f'%{q}%'
        rows = self._conn.execute(
            '''SELECT * FROM users
               WHERE id != ?
                 AND username IS NOT NULL
                 AND (lower(username) LIKE ? OR lower(first_name) LIKE ?
                      OR lower(last_name) LIKE ?)
               ORDER BY lower(username) LIKE ? DESC, lower(username)
               LIMIT ?''',
            (SERVICE_USER_ID, pattern, pattern, pattern, f'{q}%', limit)
        ).fetchall()
        return [self._row_to_user(r) for r in rows]

    def search_channels(self, query: str, limit: int = 20) -> List[dict]:
        q = query.strip().lstrip('@').lower()
        if not q:
            return []
        pattern = f'%{q}%'
        rows = self._conn.execute(
            '''SELECT * FROM channels
               WHERE username IS NOT NULL
                 AND (lower(username) LIKE ? OR lower(title) LIKE ?)
               ORDER BY lower(username) LIKE ? DESC, lower(username)
               LIMIT ?''',
            (pattern, pattern, f'{q}%', limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def set_user_online(self, user_id: int, ttl: int = 300) -> dict:
        now = int(time.time())
        self._conn.execute(
            'UPDATE users SET online_until=?, last_seen=? WHERE id=?',
            (now + ttl, now, user_id)
        )
        self._conn.commit()
        return self.get_user_by_id(user_id)

    def set_user_offline(self, user_id: int) -> dict:
        now = int(time.time())
        self._conn.execute(
            'UPDATE users SET online_until=0, last_seen=? WHERE id=?',
            (now, user_id)
        )
        self._conn.commit()
        return self.get_user_by_id(user_id)

    def get_dialog_partner_ids(self, user_id: int) -> List[int]:
        rows = self._conn.execute(
            'SELECT peer_id FROM dialogs WHERE owner_id=?', (user_id,)
        ).fetchall()
        return [r['peer_id'] for r in rows]

    def update_user_phone(self, user_id: int, new_phone: str) -> Dict[str, Any]:
        new_phone = normalize_phone(new_phone)
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('user not found')
        old_phone = normalize_phone(user['phone'])
        if old_phone == new_phone:
            return user
        other = self.get_user_by_phone(new_phone)
        if other and int(other['id']) != int(user_id):
            raise ValueError('phone occupied')
        self._conn.execute(
            'UPDATE users SET phone=? WHERE id=?', (new_phone, user_id))
        self._conn.execute(
            'UPDATE telegram_phones SET phone=? WHERE phone=?',
            (new_phone, old_phone))
        self._conn.execute(
            'UPDATE bot_phone_assignments SET phone=? WHERE phone=?',
            (new_phone, old_phone))
        from handlers.phone_countries import is_collectible_phone
        if is_collectible_phone(new_phone):
            row = self._conn.execute(
                'SELECT kind FROM anonymous_numbers WHERE user_id=? OR phone=?',
                (user_id, new_phone),
            ).fetchone()
            kind = (row['kind'] if row else '') or 'custom_4'
            self._conn.execute(
                '''INSERT OR REPLACE INTO anonymous_numbers
                   (phone, user_id, kind, created_at) VALUES (?,?,?,?)''',
                (new_phone, user_id, kind, int(time.time())),
            )
        self._conn.commit()
        return self.get_user_by_id(user_id)

    def update_profile(self, user_id: int, *,
                       first_name: Optional[str] = None,
                       last_name: Optional[str] = None,
                       about: Optional[str] = None) -> Dict[str, Any]:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('user not found')
        if first_name is not None:
            user['first_name'] = first_name
        if last_name is not None:
            user['last_name'] = last_name
        if about is not None:
            if user.get('scam') or user.get('fake'):
                user['label_hidden_about'] = about
                user['about'] = ''
            else:
                user['about'] = about
        self._conn.execute(
            '''UPDATE users
               SET first_name=?, last_name=?, about=?, label_hidden_about=?
               WHERE id=?''',
            (
                user['first_name'],
                user['last_name'],
                user['about'],
                user.get('label_hidden_about') or '',
                user_id,
            )
        )
        self._conn.commit()
        return self.get_user_by_id(user_id)

    def get_business_intro(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            'SELECT * FROM business_intros WHERE user_id=?',
            (int(user_id),),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data['sticker_document_id'] = int(data.get('sticker_document_id') or 0)
        return data

    def set_business_intro(self, user_id: int, *, title: str,
                           description: str,
                           sticker_document_id: int = 0) -> Dict[str, Any]:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('USER_ID_INVALID')
        title = (title or '').strip()
        description = (description or '').strip()
        sticker_document_id = int(sticker_document_id or 0)
        if len(title) > 64 or len(description) > 512:
            raise ValueError('BUSINESS_INTRO_INVALID')
        if sticker_document_id:
            if not self.get_document_by_id(sticker_document_id):
                raise ValueError('STICKER_INVALID')
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO business_intros
               (user_id, title, description, sticker_document_id, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   title=excluded.title,
                   description=excluded.description,
                   sticker_document_id=excluded.sticker_document_id,
                   updated_at=excluded.updated_at''',
            (int(user_id), title, description, sticker_document_id, now),
        )
        self._conn.commit()
        return self.get_business_intro(user_id) or {}

    def clear_business_intro(self, user_id: int) -> None:
        self._conn.execute(
            'DELETE FROM business_intros WHERE user_id=?',
            (int(user_id),),
        )
        self._conn.commit()

    def get_system_bot_state(self, bot_id: int, user_id: int) -> Optional[dict]:
        row = self._conn.execute(
            '''SELECT state_json FROM system_bot_states
               WHERE bot_id=? AND user_id=?''',
            (int(bot_id), int(user_id)),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row['state_json'] or '{}')
        except Exception:
            return None

    def set_system_bot_state(self, bot_id: int, user_id: int,
                             state: dict) -> dict:
        payload = json.dumps(state or {}, ensure_ascii=False)
        self._conn.execute(
            '''INSERT INTO system_bot_states (bot_id, user_id, state_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(bot_id, user_id) DO UPDATE SET
                   state_json=excluded.state_json,
                   updated_at=excluded.updated_at''',
            (int(bot_id), int(user_id), payload, int(time.time())),
        )
        self._conn.commit()
        return state

    def clear_system_bot_state(self, bot_id: int, user_id: int) -> None:
        self._conn.execute(
            'DELETE FROM system_bot_states WHERE bot_id=? AND user_id=?',
            (int(bot_id), int(user_id)),
        )
        self._conn.commit()

    def create_report_complaint(self, *, reporter_id: int, reported_user_id: int,
                                reported_username: str, reason: str,
                                evidence_mode: str, description: str,
                                evidence_photo_id: int = 0) -> dict:
        now = int(time.time())
        cur = self._conn.execute(
            '''INSERT INTO report_complaints
               (reporter_id, reported_user_id, reported_username, reason,
                evidence_mode, description, evidence_photo_id, status,
                admin_action, moderator_tg_id, created_at, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'open', '', 0, ?, 0)''',
            (
                int(reporter_id),
                int(reported_user_id),
                (reported_username or '').strip().lower(),
                (reason or '').strip(),
                (evidence_mode or '').strip(),
                (description or '').strip(),
                int(evidence_photo_id or 0),
                now,
            ),
        )
        self._conn.commit()
        return self.get_report_complaint(int(cur.lastrowid)) or {}

    def get_report_complaint(self, complaint_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM report_complaints WHERE id=?',
            (int(complaint_id),),
        ).fetchone()
        return dict(row) if row else None

    def resolve_report_complaint(self, complaint_id: int, *,
                                 admin_action: str,
                                 moderator_tg_id: int) -> dict:
        now = int(time.time())
        self._conn.execute(
            '''UPDATE report_complaints
               SET status='resolved', admin_action=?, moderator_tg_id=?,
                   resolved_at=?
               WHERE id=?''',
            (
                (admin_action or '').strip(),
                int(moderator_tg_id or 0),
                now,
                int(complaint_id),
            ),
        )
        self._conn.commit()
        return self.get_report_complaint(complaint_id) or {}

    def list_open_report_complaints(self, limit: int = 100) -> List[dict]:
        rows = self._conn.execute(
            '''SELECT * FROM report_complaints
               WHERE status='open'
               ORDER BY created_at DESC, id DESC
               LIMIT ?''',
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_business_greeting_message(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            'SELECT * FROM business_greeting_messages WHERE user_id=?',
            (int(user_id),),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            users = json.loads(data.get('recipients_users_json') or '[]')
        except json.JSONDecodeError:
            users = []
        data['recipients_users'] = [
            int(uid) for uid in users if str(uid).lstrip('-').isdigit()
        ]
        return data

    def set_business_greeting_message(
            self, user_id: int, *, shortcut_id: int,
            no_activity_days: int, recipients_flags: int,
            recipients_users: List[int]) -> Dict[str, Any]:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('USER_ID_INVALID')
        shortcut_id = int(shortcut_id)
        no_activity_days = int(no_activity_days)
        if shortcut_id < 0:
            raise ValueError('BUSINESS_GREETING_INVALID')
        if no_activity_days < 0 or no_activity_days > 366:
            raise ValueError('BUSINESS_GREETING_INVALID')
        users = sorted({int(uid) for uid in (recipients_users or []) if int(uid) > 0})
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO business_greeting_messages
               (user_id, shortcut_id, no_activity_days, recipients_flags,
                recipients_users_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   shortcut_id=excluded.shortcut_id,
                   no_activity_days=excluded.no_activity_days,
                   recipients_flags=excluded.recipients_flags,
                   recipients_users_json=excluded.recipients_users_json,
                   updated_at=excluded.updated_at''',
            (int(user_id), shortcut_id, no_activity_days, int(recipients_flags),
             json.dumps(users), now),
        )
        self._conn.commit()
        return self.get_business_greeting_message(user_id) or {}

    def clear_business_greeting_message(self, user_id: int) -> None:
        self._conn.execute(
            'DELETE FROM business_greeting_messages WHERE user_id=?',
            (int(user_id),),
        )
        self._conn.commit()

    @staticmethod
    def _normalize_business_weekly_open(
            weekly_open: List[Dict[str, Any]]) -> List[Dict[str, int]]:
        ranges: List[tuple[int, int]] = []
        for raw in (weekly_open or []):
            if not isinstance(raw, dict):
                raise ValueError('BUSINESS_WORK_HOURS_PERIOD_INVALID')
            start = int(raw.get('start_minute') or 0)
            end = int(raw.get('end_minute') or 0)
            if start < 0 or start > 7 * 24 * 60:
                raise ValueError('BUSINESS_WORK_HOURS_PERIOD_INVALID')
            if end < 1 or end > 8 * 24 * 60:
                raise ValueError('BUSINESS_WORK_HOURS_PERIOD_INVALID')
            if end - start < 1:
                raise ValueError('BUSINESS_WORK_HOURS_PERIOD_INVALID')
            ranges.append((start, end))
        ranges.sort()

        merged: List[tuple[int, int]] = []
        for start, end in ranges:
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))

        if len(merged) > 28:
            raise ValueError('BUSINESS_WORK_HOURS_PERIOD_INVALID')
        return [
            {'start_minute': int(start), 'end_minute': int(end)}
            for start, end in merged
        ]

    def get_business_work_hours(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            'SELECT * FROM business_work_hours WHERE user_id=?',
            (int(user_id),),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            weekly_open = json.loads(data.get('weekly_open_json') or '[]')
        except json.JSONDecodeError:
            weekly_open = []
        if not isinstance(weekly_open, list):
            weekly_open = []
        parsed: List[Dict[str, int]] = []
        for item in weekly_open:
            if not isinstance(item, dict):
                continue
            try:
                start = int(item.get('start_minute') or 0)
                end = int(item.get('end_minute') or 0)
            except (TypeError, ValueError):
                continue
            parsed.append({'start_minute': start, 'end_minute': end})
        data['timezone_id'] = (data.get('timezone_id') or 'UTC').strip() or 'UTC'
        data['weekly_open'] = parsed
        return data

    def set_business_work_hours(self, user_id: int, *, timezone_id: str,
                                weekly_open: List[Dict[str, Any]]) -> Dict[str, Any]:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('USER_ID_INVALID')
        timezone_id = (timezone_id or '').strip()
        if not timezone_id:
            raise ValueError('TIMEZONE_INVALID')
        try:
            ZoneInfo(timezone_id)
        except Exception as exc:
            raise ValueError('TIMEZONE_INVALID') from exc
        normalized = self._normalize_business_weekly_open(weekly_open)
        if not normalized:
            raise ValueError('BUSINESS_WORK_HOURS_EMPTY')
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO business_work_hours
               (user_id, timezone_id, weekly_open_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   timezone_id=excluded.timezone_id,
                   weekly_open_json=excluded.weekly_open_json,
                   updated_at=excluded.updated_at''',
            (int(user_id), timezone_id, json.dumps(normalized), now),
        )
        self._conn.commit()
        return self.get_business_work_hours(user_id) or {}

    def clear_business_work_hours(self, user_id: int) -> None:
        self._conn.execute(
            'DELETE FROM business_work_hours WHERE user_id=?',
            (int(user_id),),
        )
        self._conn.commit()

    def get_business_location(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            'SELECT * FROM business_locations WHERE user_id=?',
            (int(user_id),),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data['has_geo'] = bool(int(data.get('has_geo') or 0))
        return data

    def set_business_location(
            self, user_id: int, *, address: str,
            lat: float = 0.0, long: float = 0.0,
            accuracy_radius: int = 0, has_geo: bool = False) -> Dict[str, Any]:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('USER_ID_INVALID')
        address = (address or '').strip()
        if not address:
            raise ValueError('BUSINESS_ADDRESS_INVALID')
        if len(address.encode('utf-8')) > 96:
            raise ValueError('BUSINESS_ADDRESS_ACTIVE')
        import struct
        import hashlib
        access_hash = 0
        if has_geo:
            blob = struct.pack('<dd', float(lat), float(long))
            access_hash = int.from_bytes(
                hashlib.sha256(blob).digest()[:8], 'little', signed=True)
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO business_locations
               (user_id, address, lat, long, accuracy_radius, access_hash,
                has_geo, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   address=excluded.address,
                   lat=excluded.lat,
                   long=excluded.long,
                   accuracy_radius=excluded.accuracy_radius,
                   access_hash=excluded.access_hash,
                   has_geo=excluded.has_geo,
                   updated_at=excluded.updated_at''',
            (int(user_id), address, float(lat), float(long),
             int(accuracy_radius or 0), int(access_hash),
             int(bool(has_geo)), now),
        )
        self._conn.commit()
        return self.get_business_location(user_id) or {}

    def clear_business_location(self, user_id: int) -> None:
        self._conn.execute(
            'DELETE FROM business_locations WHERE user_id=?',
            (int(user_id),),
        )
        self._conn.commit()

    def get_business_away_message(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            'SELECT * FROM business_away_messages WHERE user_id=?',
            (int(user_id),),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            users = json.loads(data.get('recipients_users_json') or '[]')
        except json.JSONDecodeError:
            users = []
        data['recipients_users'] = [
            int(uid) for uid in users if str(uid).lstrip('-').isdigit()
        ]
        data['offline_only'] = bool(int(data.get('offline_only') or 0))
        data['schedule_type'] = (data.get('schedule_type') or 'always').strip() or 'always'
        return data

    def set_business_away_message(
            self, user_id: int, *, shortcut_id: int, offline_only: bool,
            schedule_type: str, schedule_start: int, schedule_end: int,
            recipients_flags: int, recipients_users: List[int]) -> Dict[str, Any]:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('USER_ID_INVALID')
        schedule_type = (schedule_type or 'always').strip() or 'always'
        if schedule_type not in {'always', 'outside_work_hours', 'custom'}:
            raise ValueError('BUSINESS_AWAY_INVALID')
        schedule_start = int(schedule_start)
        schedule_end = int(schedule_end)
        if schedule_type == 'custom':
            if schedule_start <= 0 or schedule_end <= 0 or schedule_end < schedule_start:
                raise ValueError('BUSINESS_AWAY_INVALID')
        users = sorted({int(uid) for uid in (recipients_users or []) if int(uid) > 0})
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO business_away_messages
               (user_id, shortcut_id, offline_only, schedule_type,
                schedule_start, schedule_end, recipients_flags,
                recipients_users_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   shortcut_id=excluded.shortcut_id,
                   offline_only=excluded.offline_only,
                   schedule_type=excluded.schedule_type,
                   schedule_start=excluded.schedule_start,
                   schedule_end=excluded.schedule_end,
                   recipients_flags=excluded.recipients_flags,
                   recipients_users_json=excluded.recipients_users_json,
                   updated_at=excluded.updated_at''',
            (int(user_id), int(shortcut_id), int(bool(offline_only)),
             schedule_type, schedule_start, schedule_end, int(recipients_flags),
             json.dumps(users), now),
        )
        self._conn.execute(
            'DELETE FROM business_away_message_sent WHERE owner_id=?',
            (int(user_id),),
        )
        self._conn.commit()
        return self.get_business_away_message(user_id) or {}

    def quick_reply_shortcut_exists(self, user_id: int, shortcut_id: int) -> bool:
        row = self._conn.execute(
            'SELECT 1 FROM quick_reply_shortcuts WHERE user_id=? AND shortcut_id=?',
            (int(user_id), int(shortcut_id)),
        ).fetchone()
        return row is not None

    def delete_quick_reply_shortcut(self, user_id: int, shortcut_id: int) -> bool:
        uid = int(user_id)
        sid = int(shortcut_id)
        if not self.quick_reply_shortcut_exists(uid, sid):
            return False
        self._conn.execute(
            'DELETE FROM quick_reply_messages WHERE user_id=? AND shortcut_id=?',
            (uid, sid),
        )
        self._conn.execute(
            'DELETE FROM quick_reply_shortcuts WHERE user_id=? AND shortcut_id=?',
            (uid, sid),
        )
        self._conn.commit()
        return True

    def clear_business_away_message(self, user_id: int) -> None:
        uid = int(user_id)
        self._conn.execute(
            'DELETE FROM business_away_messages WHERE user_id=?',
            (uid,),
        )
        self._conn.execute(
            'DELETE FROM business_away_message_sent WHERE owner_id=?',
            (uid,),
        )
        self._conn.commit()
        self.delete_quick_reply_shortcut(uid, -1)

    def business_away_message_was_sent(self, owner_id: int, peer_id: int) -> bool:
        row = self._conn.execute(
            'SELECT 1 FROM business_away_message_sent WHERE owner_id=? AND peer_id=?',
            (int(owner_id), int(peer_id)),
        ).fetchone()
        return row is not None

    def mark_business_away_message_sent(self, owner_id: int, peer_id: int) -> None:
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO business_away_message_sent (owner_id, peer_id, sent_at)
               VALUES (?, ?, ?)
               ON CONFLICT(owner_id, peer_id) DO UPDATE SET
                   sent_at=excluded.sent_at''',
            (int(owner_id), int(peer_id), now),
        )
        self._conn.commit()

    def clear_business_away_message_sent(self, owner_id: int, peer_id: int) -> None:
        self._conn.execute(
            'DELETE FROM business_away_message_sent WHERE owner_id=? AND peer_id=?',
            (int(owner_id), int(peer_id)),
        )
        self._conn.commit()

    def patch_business_away_shortcut_id(self, user_id: int, shortcut_id: int) -> None:
        self._conn.execute(
            'UPDATE business_away_messages SET shortcut_id=?, updated_at=? WHERE user_id=?',
            (int(shortcut_id), int(time.time()), int(user_id)),
        )
        self._conn.commit()

    def resolve_quick_reply_shortcut(self, user_id: int, shortcut_info: dict) -> int:
        stype = (shortcut_info or {}).get('type')
        if stype == 'id':
            sid = int(shortcut_info.get('shortcut_id') or 0)
            if sid == -1:
                self.ensure_quick_reply_shortcut(user_id, -1, '')
                return -1
            if sid <= 0:
                raise ValueError('QUICK_REPLY_SHORTCUT_INVALID')
            self.ensure_quick_reply_shortcut(user_id, sid, '')
            return sid
        if stype == 'empty':
            self.ensure_quick_reply_shortcut(user_id, -1, '')
            return -1
        if stype == 'name':
            name = (shortcut_info.get('shortcut') or '').strip()
            if not name:
                raise ValueError('QUICK_REPLY_SHORTCUT_INVALID')
            row = self._conn.execute(
                'SELECT shortcut_id FROM quick_reply_shortcuts '
                'WHERE user_id=? AND shortcut=?',
                (int(user_id), name),
            ).fetchone()
            if row:
                return int(row['shortcut_id'])
            return self._allocate_quick_reply_shortcut_id(user_id, name)
        raise ValueError('QUICK_REPLY_SHORTCUT_INVALID')

    def _allocate_quick_reply_shortcut_id(self, user_id: int, name: str) -> int:
        row = self._conn.execute(
            'SELECT COALESCE(MAX(shortcut_id), 0) + 1 AS n '
            'FROM quick_reply_shortcuts WHERE user_id=? AND shortcut_id >= 0',
            (int(user_id),),
        ).fetchone()
        sid = max(1, int(row['n']))
        self.ensure_quick_reply_shortcut(user_id, sid, name)
        return sid

    def ensure_quick_reply_shortcut(
            self, user_id: int, shortcut_id: int, name: str = '') -> bool:
        row = self._conn.execute(
            'SELECT 1 FROM quick_reply_shortcuts WHERE user_id=? AND shortcut_id=?',
            (int(user_id), int(shortcut_id)),
        ).fetchone()
        if row:
            return False
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO quick_reply_shortcuts
               (user_id, shortcut_id, shortcut, top_message_id, msg_count, updated_at)
               VALUES (?, ?, ?, 0, 0, ?)''',
            (int(user_id), int(shortcut_id), (name or '').strip(), now),
        )
        self._conn.commit()
        return True

    def list_quick_reply_shortcuts(
            self, user_id: int, *, public_only: bool = False) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            'SELECT * FROM quick_reply_shortcuts WHERE user_id=? '
            'ORDER BY shortcut_id',
            (int(user_id),),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if public_only and int(item.get('shortcut_id') or 0) < 0:
                continue
            if public_only and int(item.get('msg_count') or 0) <= 0:
                continue
            out.append(item)
        return out

    def get_quick_reply_top_messages(
            self, user_id: int, shortcuts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        messages = []
        for sc in shortcuts:
            top_id = int(sc.get('top_message_id') or 0)
            if top_id <= 0:
                continue
            msg = self.get_quick_reply_message(user_id, int(sc['shortcut_id']), top_id)
            if msg:
                messages.append(msg)
        return messages

    def get_quick_reply_message(
            self, user_id: int, shortcut_id: int, msg_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            'SELECT * FROM quick_reply_messages '
            'WHERE user_id=? AND shortcut_id=? AND msg_id=?',
            (int(user_id), int(shortcut_id), int(msg_id)),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data['entities'] = json.loads(data.get('entities') or '[]')
        except json.JSONDecodeError:
            data['entities'] = []
        data['id'] = int(data['msg_id'])
        data['owner_id'] = int(user_id)
        data['from_id'] = int(user_id)
        data['peer_id'] = int(user_id)
        data['quick_reply_shortcut_id'] = int(shortcut_id)
        return data

    def get_quick_reply_messages(
            self, user_id: int, shortcut_id: int,
            msg_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        if msg_ids:
            placeholders = ','.join('?' for _ in msg_ids)
            rows = self._conn.execute(
                f'''SELECT * FROM quick_reply_messages
                    WHERE user_id=? AND shortcut_id=? AND msg_id IN ({placeholders})
                    ORDER BY msg_id''',
                [int(user_id), int(shortcut_id), *[int(x) for x in msg_ids]],
            ).fetchall()
        else:
            rows = self._conn.execute(
                'SELECT * FROM quick_reply_messages '
                'WHERE user_id=? AND shortcut_id=? ORDER BY msg_id',
                (int(user_id), int(shortcut_id)),
            ).fetchall()
        out = []
        for row in rows:
            msg = self.get_quick_reply_message(
                user_id, shortcut_id, int(row['msg_id']))
            if msg:
                out.append(msg)
        return out

    def add_quick_reply_message(
            self, user_id: int, shortcut_id: int, text: str,
            entities: List[dict], *, media_document_id: int = 0,
            media_photo_id: int = 0,
            media_poll_json: str = '') -> tuple:
        self.ensure_quick_reply_shortcut(user_id, shortcut_id, '')
        row = self._conn.execute(
            'SELECT COALESCE(MAX(msg_id), 0) + 1 AS n FROM quick_reply_messages '
            'WHERE user_id=? AND shortcut_id=?',
            (int(user_id), int(shortcut_id)),
        ).fetchone()
        msg_id = int(row['n'])
        now = int(time.time())
        entities_json = json.dumps(entities or [], ensure_ascii=False)
        self._conn.execute(
            '''INSERT INTO quick_reply_messages
               (user_id, shortcut_id, msg_id, text, entities,
                media_document_id, media_photo_id, media_poll_json, date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (int(user_id), int(shortcut_id), msg_id, text or '',
             entities_json, int(media_document_id), int(media_photo_id),
             media_poll_json or '', now),
        )
        count_row = self._conn.execute(
            'SELECT COUNT(*) AS c FROM quick_reply_messages '
            'WHERE user_id=? AND shortcut_id=?',
            (int(user_id), int(shortcut_id)),
        ).fetchone()
        msg_count = int(count_row['c'])
        self._conn.execute(
            '''UPDATE quick_reply_shortcuts
               SET top_message_id=?, msg_count=?, updated_at=?
               WHERE user_id=? AND shortcut_id=?''',
            (msg_id, msg_count, now, int(user_id), int(shortcut_id)),
        )
        self._conn.commit()
        shortcut_row = dict(self._conn.execute(
            'SELECT * FROM quick_reply_shortcuts WHERE user_id=? AND shortcut_id=?',
            (int(user_id), int(shortcut_id)),
        ).fetchone())
        msg = self.get_quick_reply_message(user_id, shortcut_id, msg_id)
        created_new = msg_count == 1
        return msg, shortcut_row, created_new

    def update_username(self, user_id: int, username: str) -> Dict[str, Any]:
        username = username.strip().lstrip('@').lower()
        if not is_valid_username(username):
            raise ValueError('USERNAME_INVALID')
        existing = self.get_user_by_username(username)
        if existing and existing['id'] != user_id:
            raise ValueError('USERNAME_OCCUPIED')
        owner = self.get_collectible_username_owner(username)
        if owner and int(owner['user_id']) != int(user_id):
            raise ValueError('USERNAME_OCCUPIED')
        if self.get_channel_by_username(username):
            raise ValueError('USERNAME_OCCUPIED')
        user = self.get_user_by_id(user_id)
        if user and user.get('username') == username:
            raise ValueError('USERNAME_NOT_MODIFIED')
        owned = self.get_collectible_usernames(user_id)
        if owned and not user.get('username'):
            order_row = self._conn.execute(
                '''SELECT MAX(v) AS n FROM (
                       SELECT COALESCE(MAX(sort_order), 0) AS v
                       FROM collectible_usernames WHERE user_id=?
                       UNION ALL
                       SELECT COALESCE(username_sort_order, 0) AS v
                       FROM users WHERE id=?
                   )''',
                (int(user_id), int(user_id)),
            ).fetchone()
            sort_order = int(
                order_row['n'] if order_row and order_row['n'] is not None else 0)
            self._conn.execute(
                'UPDATE users SET username=?, username_sort_order=? WHERE id=?',
                (username, sort_order, user_id),
            )
        else:
            self._conn.execute(
                'UPDATE users SET username=? WHERE id=?', (username, user_id)
            )
        self._conn.commit()
        return self.get_user_by_id(user_id)

    def is_username_available(self, username: str,
                              exclude_user_id: Optional[int] = None) -> bool:
        username = username.strip().lstrip('@').lower()
        if not is_valid_username(username):
            return False
        row = self._conn.execute(
            'SELECT id FROM users WHERE lower(username)=?', (username,)
        ).fetchone()
        if row and not (exclude_user_id and row['id'] == exclude_user_id):
            return False
        owner = self.get_collectible_username_owner(username)
        if owner and not (exclude_user_id and int(owner['user_id']) == int(exclude_user_id)):
            return False
        if self.get_channel_by_username(username):
            return False
        return True

    def get_collectible_username_owner(self, username: str, *,
                                       active_only: bool = False) -> Optional[Dict[str, Any]]:
        username = normalize_collectible_username(username)
        sql = 'SELECT * FROM collectible_usernames WHERE lower(username)=?'
        params: list[Any] = [username]
        if active_only:
            sql += ' AND active=1'
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def get_collectible_usernames(self, user_id: int, *,
                                  include_inactive: bool = True) -> List[Dict[str, Any]]:
        sql = 'SELECT * FROM collectible_usernames WHERE user_id=?'
        params: list[Any] = [int(user_id)]
        if not include_inactive:
            sql += ' AND active=1'
        sql += ' ORDER BY sort_order, lower(username)'
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def grant_collectible_username(self, user_id: int, username: str, *,
                                   price: str = '', granted_at: int = 0,
                                   granted_by: int = 0,
                                   active: bool = False) -> Dict[str, Any]:
        username = normalize_collectible_username(username)
        if not is_valid_collectible_username(username):
            raise ValueError('USERNAME_INVALID')
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('USER_ID_INVALID')
        primary = self._conn.execute(
            'SELECT id FROM users WHERE lower(username)=?', (username,)
        ).fetchone()
        if primary and int(primary['id']) != int(user_id):
            raise ValueError('USERNAME_OCCUPIED')
        owner = self.get_collectible_username_owner(username)
        if owner and int(owner['user_id']) != int(user_id):
            raise ValueError('USERNAME_OCCUPIED')
        if self.get_channel_by_username(username):
            raise ValueError('USERNAME_OCCUPIED')
        order_row = self._conn.execute(
            '''SELECT MAX(v) AS n FROM (
                   SELECT COALESCE(MAX(sort_order), 0) AS v
                   FROM collectible_usernames WHERE user_id=?
                   UNION ALL
                   SELECT COALESCE(username_sort_order, 0) AS v
                   FROM users WHERE id=?
               )''',
            (int(user_id), int(user_id)),
        ).fetchone()
        sort_order = int(order_row['n'] if order_row and order_row['n'] is not None else 0) + 1
        now = int(time.time())
        active_flag = 1 if active else 0
        self._conn.execute(
            '''INSERT INTO collectible_usernames
               (username, user_id, active, sort_order, price, granted_at, granted_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(username) DO UPDATE SET
                   user_id=excluded.user_id, active=excluded.active,
                   sort_order=excluded.sort_order,
                   price=excluded.price, granted_at=excluded.granted_at,
                   granted_by=excluded.granted_by''',
            (username, int(user_id), active_flag, sort_order, str(price or '').strip(),
             int(granted_at or now), int(granted_by or 0), now),
        )
        self._conn.commit()
        return self.get_collectible_username_owner(username) or {}

    def toggle_collectible_username(self, user_id: int, username: str,
                                    active: bool) -> None:
        username = normalize_collectible_username(username)
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('USER_ID_INVALID')
        primary = normalize_collectible_username(user.get('username') or '')
        if primary and primary == username:
            raise ValueError('USERNAME_NOT_MODIFIED')
        row = self.get_collectible_username_owner(username)
        if not row or int(row['user_id']) != int(user_id):
            raise ValueError('USERNAME_NOT_FOUND')
        if bool(row.get('active')) == bool(active):
            raise ValueError('USERNAME_NOT_MODIFIED')
        if active:
            from handlers.collectible_usernames import (
                MAX_ACTIVE_USERNAMES,
                count_active_usernames,
            )
            if count_active_usernames(self, int(user_id)) >= MAX_ACTIVE_USERNAMES:
                raise ValueError('USERNAMES_ACTIVE_TOO_MUCH')
        self._conn.execute(
            'UPDATE collectible_usernames SET active=? WHERE username=?',
            (1 if active else 0, username),
        )
        self._conn.commit()

    def reorder_usernames(self, user_id: int, order: List[str]) -> None:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('USER_ID_INVALID')
        primary = normalize_collectible_username(user.get('username') or '')
        normalized = []
        for item in order:
            username = normalize_collectible_username(item)
            if username and username not in normalized:
                normalized.append(username)

        owned_rows = self.get_collectible_usernames(user_id)
        active_owned = [
            r['username'] for r in owned_rows if bool(r.get('active'))
        ]
        inactive_owned = [
            r['username'] for r in owned_rows if not bool(r.get('active'))
        ]
        owned_names = set(active_owned) | set(inactive_owned)
        active_usernames = (
            ([] if not primary or primary in owned_names else [primary])
            + active_owned
        )
        allowed = set(active_usernames)
        if (
            len(normalized) != len(active_usernames)
            or set(normalized) != allowed
            or any(username not in allowed for username in normalized)
        ):
            raise ValueError('ORDER_INVALID')

        final_order = normalized + [
            username for username in inactive_owned
            if username and username not in normalized
        ]
        for idx, username in enumerate(final_order):
            if primary and username == primary:
                self._conn.execute(
                    'UPDATE users SET username_sort_order=? WHERE id=?',
                    (idx, int(user_id)),
                )
            else:
                self._conn.execute(
                    'UPDATE collectible_usernames SET sort_order=? WHERE user_id=? AND username=?',
                    (idx, int(user_id), username),
                )
        self._conn.commit()

    def reorder_collectible_usernames(self, user_id: int, order: List[str]) -> None:
        self.reorder_usernames(user_id, order)

    def remove_collectible_username(self, user_id: int, username: str) -> None:
        username = normalize_collectible_username(username)
        row = self.get_collectible_username_owner(username)
        if not row or int(row['user_id']) != int(user_id):
            raise ValueError('USERNAME_NOT_FOUND')
        self._conn.execute(
            'DELETE FROM collectible_usernames WHERE username=? AND user_id=?',
            (username, int(user_id)),
        )
        self._conn.commit()

    def get_gift_blockchain_link(self, slug: str) -> Optional[Dict[str, Any]]:
        slug = normalize_star_gift_slug(slug)
        if not slug:
            return None
        row = self._conn.execute(
            'SELECT * FROM gift_blockchain_links WHERE slug=?',
            (slug,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_gift_blockchain_link(self, slug: str,
                                    owner_address: str) -> Dict[str, Any]:
        slug = normalize_star_gift_slug(slug)
        owner_address = (owner_address or '').strip()
        if not slug:
            raise ValueError('STARGIFT_SLUG_INVALID')
        if not owner_address:
            raise ValueError('TON_ADDRESS_INVALID')
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO gift_blockchain_links
               (slug, owner_address, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(slug) DO UPDATE SET
                   owner_address=excluded.owner_address,
                   updated_at=excluded.updated_at''',
            (slug, owner_address, now, now),
        )
        self._conn.commit()
        return self.get_gift_blockchain_link(slug) or {}

    def remove_gift_blockchain_link(self, slug: str) -> bool:
        slug = normalize_star_gift_slug(slug)
        if not slug:
            return False
        cur = self._conn.execute(
            'DELETE FROM gift_blockchain_links WHERE slug=?',
            (slug,),
        )
        self._conn.commit()
        return bool(cur.rowcount)

    @staticmethod
    def is_airgram_chain_address(address: str) -> bool:
        """True when the value is a real TON wallet (KoalGram chain owner)."""
        text = (address or '').strip()
        if not text:
            return False
        try:
            from airgram_chain.core import parse_address

            parse_address(text)
        except Exception:
            return False
        return True

    def saved_gift_chain_owner(self, saved: dict) -> str:
        """Wallet address owning this gift on-chain; '' for regular gifts."""
        slug = str(saved.get('unique_slug') or '').strip()
        if not slug:
            return ''
        link = self.get_gift_blockchain_link(slug)
        address = str((link or {}).get('owner_address') or '').strip()
        if not self.is_airgram_chain_address(address):
            return ''
        return address

    def phone_exists(self, phone: str) -> bool:
        return self.get_user_by_phone(phone) is not None

    def get_collectible_phone_info(self, phone: str) -> Optional[Dict[str, Any]]:
        from handlers.phone_countries import is_collectible_phone, normalize_phone as np
        phone = np(phone)
        if not is_collectible_phone(phone):
            return None
        user = self.get_user_by_phone(phone)
        if not user:
            # Android calls fragment.getCollectibleInfo during +888 login UI
            # even before the account exists on this server.
            return {
                'phone': phone,
                'user_id': 0,
                'kind': 'anonymous',
                'purchase_date': int(time.time()),
            }
        info = {
            'phone': phone,
            'user_id': int(user['id']),
            'kind': '',
            'purchase_date': int(user.get('created_at') or 0),
        }
        row = self._conn.execute(
            'SELECT kind, created_at FROM anonymous_numbers WHERE phone=?',
            (phone,),
        ).fetchone()
        if not row:
            row = self._conn.execute(
                'SELECT kind, created_at FROM anonymous_numbers WHERE user_id=? '
                'ORDER BY created_at DESC LIMIT 1',
                (int(user['id']),),
            ).fetchone()
        if row:
            info['kind'] = row['kind'] or ''
            info['purchase_date'] = int(row['created_at'] or info['purchase_date'])
        return info

    # ── Sessions ───────────────────────────────

    def bind_session(self, auth_key_id: int, user_id: int):
        self._conn.execute(
            'INSERT OR REPLACE INTO sessions VALUES (?,?,?)',
            (auth_key_id, user_id, int(time.time()))
        )
        self._ensure_user_state(user_id)
        self._conn.commit()
        log.debug('Session bound: auth_key=%#018x user_id=%d', auth_key_id, user_id)

    def migrate_session_auth_key(self, from_key_id: int, to_key_id: int) -> None:
        """Copy user binding between temp and permanent auth keys."""
        if from_key_id == to_key_id:
            return
        user_id = (self.get_session_user(from_key_id)
                   or self.get_session_user(to_key_id))
        if user_id is None:
            return
        self.bind_session(from_key_id, user_id)
        self.bind_session(to_key_id, user_id)
        log.debug('Session migrated: %#018x <-> %#018x user_id=%d',
                 from_key_id, to_key_id, user_id)

    def clear_pending_updates(self, user_id: int) -> None:
        self._conn.execute(
            'DELETE FROM pending_updates WHERE user_id=?', (user_id,))
        self._conn.commit()

    def get_session_user(self, auth_key_id: int) -> Optional[int]:
        row = self._conn.execute(
            'SELECT user_id FROM sessions WHERE auth_key_id=?', (auth_key_id,)
        ).fetchone()
        return row['user_id'] if row else None

    def get_user_auth_keys(self, user_id: int) -> List[int]:
        rows = self._conn.execute(
            'SELECT auth_key_id FROM sessions WHERE user_id=?', (user_id,)
        ).fetchall()
        return [r['auth_key_id'] for r in rows]

    def get_recent_user_auth_keys(self, user_id: int,
                                  limit: int = 4) -> List[int]:
        rows = self._conn.execute(
            'SELECT auth_key_id FROM sessions WHERE user_id=? '
            'ORDER BY created_at DESC LIMIT ?',
            (user_id, max(1, int(limit)))
        ).fetchall()
        return [r['auth_key_id'] for r in rows]

    def is_user_banned(self, user_id: int) -> bool:
        row = self._conn.execute(
            'SELECT banned FROM users WHERE id=?', (user_id,)
        ).fetchone()
        return bool(row and row['banned'])

    def is_ban_blocking_recreate(self, user_id: int) -> bool:
        row = self._conn.execute(
            'SELECT ban_block_recreate FROM users WHERE id=?', (user_id,)
        ).fetchone()
        return bool(row and row['ban_block_recreate'])

    def ban_user_account(self, user_id: int, *, block_recreate: bool = False) -> int:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('user not found')
        if user_id == config.SERVICE_USER_ID:
            raise ValueError('cannot ban service user')
        if self.is_user_banned(user_id):
            raise ValueError('already banned')

        auth_keys = self.get_user_auth_keys(user_id)
        for auth_key_id in auth_keys:
            self._conn.execute(
                'DELETE FROM sessions WHERE auth_key_id=?', (auth_key_id,),
            )
            self._conn.execute(
                'DELETE FROM pending_sign_in WHERE auth_key_id=?', (auth_key_id,),
            )
        self.clear_pending_updates(user_id)
        old_username = user.get('username')
        old_first_name = (user.get('first_name') or '').strip() or 'User'
        self._conn.execute(
            '''UPDATE users SET username=NULL, banned=1, ban_block_recreate=?,
               banned_username=?, banned_first_name=?
               WHERE id=?''',
            (1 if block_recreate else 0, old_username, old_first_name, user_id),
        )
        self._conn.commit()
        return len(auth_keys)

    def recreate_banned_user(self, phone: str, first_name: str,
                             last_name: str = '') -> Dict[str, Any]:
        phone = normalize_phone(phone)
        user = self.get_user_by_phone(phone)
        if not user:
            raise ValueError('user not found')
        uid = int(user['id'])
        if not self.is_user_banned(uid):
            raise ValueError('not banned')
        if self.is_ban_blocking_recreate(uid):
            raise ValueError('blocked')

        self._conn.execute(
            '''UPDATE users SET first_name=?, last_name=?, username=NULL,
               banned=0, ban_block_recreate=0,
               banned_username=NULL, banned_first_name=NULL
               WHERE id=?''',
            (first_name.strip(), (last_name or '').strip(), uid),
        )
        self._conn.commit()
        return self.get_user_by_id(uid)

    def unban_user_account(self, user_id: int) -> dict[str, Any]:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('user not found')
        if not self.is_user_banned(user_id):
            raise ValueError('not banned')

        first_name = (user.get('banned_first_name') or '').strip() or 'User'
        username = (user.get('banned_username') or '').strip().lower() or None
        if username:
            existing = self.get_user_by_username(username)
            if existing and int(existing['id']) != user_id:
                username = None
            elif self.get_channel_by_username(username):
                username = None

        self._conn.execute(
            '''UPDATE users SET first_name=?, username=?, banned=0,
               ban_block_recreate=0, banned_username=NULL, banned_first_name=NULL
               WHERE id=?''',
            (first_name, username, user_id),
        )
        self._conn.commit()
        return {'first_name': first_name, 'username': username}

    # ── Block list ─────────────────────────────

    def block_user(self, owner_id: int, blocked_id: int) -> None:
        if owner_id == blocked_id:
            raise ValueError('PEER_ID_INVALID')
        if not self.get_user_by_id(blocked_id):
            raise ValueError('PEER_ID_INVALID')
        now = int(time.time())
        self._conn.execute(
            '''INSERT OR REPLACE INTO blocked_users
               (owner_id, blocked_id, created_at) VALUES (?,?,?)''',
            (owner_id, blocked_id, now))
        self._conn.commit()

    def unblock_user(self, owner_id: int, blocked_id: int) -> None:
        self._conn.execute(
            'DELETE FROM blocked_users WHERE owner_id=? AND blocked_id=?',
            (owner_id, blocked_id))
        self._conn.commit()

    def is_blocked(self, owner_id: int, blocked_id: int) -> bool:
        row = self._conn.execute(
            'SELECT 1 FROM blocked_users WHERE owner_id=? AND blocked_id=?',
            (owner_id, blocked_id)).fetchone()
        return row is not None

    def list_blocked_user_ids(self, owner_id: int) -> List[int]:
        rows = self._conn.execute(
            'SELECT blocked_id FROM blocked_users WHERE owner_id=? ORDER BY created_at DESC',
            (owner_id,)).fetchall()
        return [int(r['blocked_id']) for r in rows]

    def should_hide_profile_from(self, viewer_id: int, target_id: int) -> bool:
        if viewer_id == target_id:
            return False
        if self.is_user_frozen(target_id):
            return True
        return self.is_blocked(target_id, viewer_id)

    def users_are_contacts(self, user_a: int, user_b: int) -> bool:
        if user_a == user_b:
            return True
        row = self._conn.execute(
            '''SELECT 1 FROM dialogs
               WHERE owner_id=? AND peer_id=? AND top_message_id>0''',
            (user_a, user_b)).fetchone()
        return row is not None

    def shared_channel_ids(self, user_a: int, user_b: int) -> set[int]:
        rows_a = self._conn.execute(
            'SELECT channel_id FROM channel_members WHERE user_id=? AND left=0',
            (user_a,)).fetchall()
        rows_b = self._conn.execute(
            'SELECT channel_id FROM channel_members WHERE user_id=? AND left=0',
            (user_b,)).fetchall()
        a = {int(r['channel_id']) for r in rows_a}
        b = {int(r['channel_id']) for r in rows_b}
        return a & b

    def get_privacy_rules(self, user_id: int, privacy_key: int) -> list[dict]:
        row = self._conn.execute(
            'SELECT rules_json FROM user_privacy WHERE user_id=? AND privacy_key=?',
            (user_id, int(privacy_key))).fetchone()
        if not row or not row['rules_json']:
            return [{'type': 'allowAll'}]
        try:
            data = json.loads(row['rules_json'])
            return data if isinstance(data, list) and data else [{'type': 'allowAll'}]
        except json.JSONDecodeError:
            return [{'type': 'allowAll'}]

    def set_privacy_rules(self, user_id: int, privacy_key: int,
                          rules: list[dict]) -> None:
        payload = json.dumps(rules or [{'type': 'allowAll'}],
                             ensure_ascii=False, separators=(',', ':'))
        self._conn.execute(
            '''INSERT INTO user_privacy (user_id, privacy_key, rules_json)
               VALUES (?,?,?)
               ON CONFLICT(user_id, privacy_key) DO UPDATE SET rules_json=excluded.rules_json''',
            (user_id, int(privacy_key), payload))
        self._conn.commit()

    def get_global_privacy(self, user_id: int) -> dict:
        row = self._conn.execute(
            '''SELECT noncontact_peers_paid_stars, display_gifts_button,
                      new_noncontact_peers_require_premium, hide_read_marks,
                      disallowed_gifts_json
               FROM users WHERE id=?''',
            (user_id,)).fetchone()
        if not row:
            return {}
        disallowed = {}
        raw = row['disallowed_gifts_json'] or ''
        if raw:
            try:
                disallowed = json.loads(raw)
            except json.JSONDecodeError:
                disallowed = {}
        return {
            'noncontact_peers_paid_stars': int(row['noncontact_peers_paid_stars'] or 0),
            'display_gifts_button': bool(int(row['display_gifts_button'] or 0)),
            'new_noncontact_peers_require_premium': bool(
                int(row['new_noncontact_peers_require_premium'] or 0)),
            'hide_read_marks': bool(int(row['hide_read_marks'] or 0)),
            'disallowed_gifts': disallowed,
        }

    def set_global_privacy(self, user_id: int, settings: dict) -> None:
        disallowed = settings.get('disallowed_gifts') or {}
        show_gifts = bool(settings.get('display_gifts_button', True))
        if show_gifts and not self.is_user_premium(user_id):
            show_gifts = False
        self._conn.execute(
            '''UPDATE users SET
               display_gifts_button=?,
               new_noncontact_peers_require_premium=?,
               hide_read_marks=?,
               disallowed_gifts_json=?
               WHERE id=?''',
            (1 if show_gifts else 0,
             1 if settings.get('new_noncontact_peers_require_premium') else 0,
             1 if settings.get('hide_read_marks') else 0,
             json.dumps(disallowed, ensure_ascii=False),
             user_id))
        self._conn.commit()

    def is_private_message_undelivered(self, sender_id: int,
                                       recipient_id: int) -> bool:
        """Block either direction — sender keeps outgoing copy, no delivery."""
        return (self.is_blocked(recipient_id, sender_id)
                or self.is_blocked(sender_id, recipient_id))

    def check_private_messaging_allowed(self, sender_id: int,
                                        recipient_id: int) -> None:
        if self.is_user_frozen(sender_id):
            raise ValueError('FROZEN_METHOD_INVALID')
        from handlers.privacy import check_can_message_user
        check_can_message_user(self, sender_id, recipient_id)

    def set_user_birthday(self, user_id: int, encoded: int) -> None:
        self._conn.execute(
            'UPDATE users SET user_birthday=? WHERE id=?',
            (int(encoded or 0), int(user_id)))
        self._conn.commit()

    def document_is_voice(self, doc: dict) -> bool:
        try:
            attrs = json.loads(doc.get('attributes_json') or '[]')
        except json.JSONDecodeError:
            attrs = []
        for item in attrs:
            try:
                raw = bytes.fromhex(item)
            except (ValueError, TypeError):
                continue
            if len(raw) < 8:
                continue
            cid = int.from_bytes(raw[:4], 'little')
            if cid != 0x9852f9c6:
                continue
            aflags = int.from_bytes(raw[4:8], 'little', signed=True)
            return bool(aflags & (1 << 10))
        return False

    # ── Account freeze ─────────────────────────

    def get_freeze_settings(self) -> dict:
        row = self._conn.execute(
            'SELECT * FROM account_freeze_settings WHERE id=1').fetchone()
        if not row:
            return {
                'emoji': '',
                'emojiId': str(DEFAULT_FREEZE_EMOJI_DOCUMENT_ID),
                'appealUrl': 'https://t.me/SpamBot',
                'freezeDays': 30,
            }
        return {
            'emoji': row['emoji'] or '',
            'emojiId': row['emoji_id'] or str(DEFAULT_FREEZE_EMOJI_DOCUMENT_ID),
            'appealUrl': row['appeal_url'] or 'https://t.me/SpamBot',
            'freezeDays': int(row['freeze_days'] or 30),
        }

    def set_freeze_settings(self, *, emoji: str = '', emoji_id: str = '',
                            appeal_url: str = '',
                            freeze_days: int = 30) -> dict:
        emoji = (emoji or '').strip()
        emoji_id = str(emoji_id or '').strip()
        if emoji_id:
            try:
                from shop_webapp.emoji_import import ensure_custom_emoji_document
                imported = ensure_custom_emoji_document(self, int(emoji_id))
                if imported:
                    alt = (imported.get('alt') or '').strip()
                    if alt:
                        emoji = alt
            except Exception:
                pass
        if not emoji_id:
            emoji = emoji or ''
        self._conn.execute(
            '''UPDATE account_freeze_settings SET emoji=?, emoji_id=?,
               appeal_url=?, freeze_days=? WHERE id=1''',
            (emoji, emoji_id,
             (appeal_url or 'https://t.me/SpamBot').strip(),
             max(1, int(freeze_days or 30))))
        self._conn.commit()
        return self.get_freeze_settings()

    def freeze_emoji_document_id(self) -> int:
        settings = self.get_freeze_settings() or {}
        raw = (settings.get('emojiId') or '').strip()
        if not raw:
            return DEFAULT_FREEZE_EMOJI_DOCUMENT_ID
        try:
            return int(raw)
        except ValueError:
            return DEFAULT_FREEZE_EMOJI_DOCUMENT_ID

    def freeze_emoji_prefix(self) -> tuple[str, int]:
        """Custom freeze emoji alt + document id, or ('', 0) if not configured."""
        doc_id = self.freeze_emoji_document_id()
        if not doc_id:
            return '', 0
        try:
            from shop_webapp.emoji_import import ensure_custom_emoji_document
            imported = ensure_custom_emoji_document(self, doc_id)
            if not imported:
                return '', 0
            alt = (imported.get('alt') or '').strip()
            if not alt:
                return '', 0
            return alt, doc_id
        except Exception:
            return '', 0

    def freeze_emoji_display(self) -> str:
        alt, _ = self.freeze_emoji_prefix()
        return alt

    def is_user_frozen(self, user_id: int) -> bool:
        uid = int(user_id)
        if uid in {
            int(config.SERVICE_USER_ID),
            int(config.STICKERS_BOT_ID),
            int(config.REPORT_BOT_ID),
            int(config.PREMIUM_BOT_ID),
        }:
            return False
        row = self._conn.execute(
            'SELECT frozen FROM users WHERE id=?', (uid,)).fetchone()
        return bool(row and row['frozen'])

    def freeze_user_account(self, user_id: int,
                            freeze_days: Optional[int] = None,
                            *, refresh: bool = False) -> dict:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError('user not found')
        if int(user_id) in {
            int(config.SERVICE_USER_ID),
            int(config.STICKERS_BOT_ID),
            int(config.REPORT_BOT_ID),
            int(config.PREMIUM_BOT_ID),
        } or user.get('is_bot'):
            raise ValueError('TARGET_PROTECTED')
        if user.get('banned'):
            raise ValueError('already banned')
        settings = self.get_freeze_settings()
        days = int(freeze_days or settings.get('freezeDays') or 30)
        now = int(time.time())
        until = now + days * 86400
        if self.is_user_frozen(user_id):
            if not refresh:
                raise ValueError('already frozen')
            self._conn.execute(
                '''UPDATE users SET frozen_at=?, frozen_until=? WHERE id=?''',
                (now, until, user_id))
            self._conn.commit()
            return {'frozenAt': now, 'frozenUntil': until, 'refreshed': True}
        self._conn.execute(
            '''UPDATE users SET frozen=1, frozen_at=?, frozen_until=? WHERE id=?''',
            (now, until, user_id))
        self._conn.commit()
        return {'frozenAt': now, 'frozenUntil': until}

    def unfreeze_user_account(self, user_id: int) -> None:
        if not self.is_user_frozen(user_id):
            raise ValueError('not frozen')
        self._conn.execute(
            '''UPDATE users SET frozen=0, frozen_at=0, frozen_until=0 WHERE id=?''',
            (user_id,))
        self._conn.commit()

    # ── Phone codes ────────────────────────────

    def create_phone_code(self, phone: str) -> tuple[str, str]:
        phone = normalize_phone(phone)
        code = f'{secrets.randbelow(100000):05d}'
        code_hash = secrets.token_hex(16)
        expires = int(time.time()) + 300

        self._conn.execute('DELETE FROM phone_codes WHERE phone=?', (phone,))
        self._conn.execute(
            'INSERT INTO phone_codes VALUES (?,?,?,?,0)',
            (phone, code, code_hash, expires)
        )
        self._conn.commit()
        return code, code_hash

    def check_phone_code(self, phone: str, code_hash: str, code: str) -> bool:
        phone = normalize_phone(phone)
        row = self._conn.execute(
            '''SELECT 1 FROM phone_codes
               WHERE phone=? AND code_hash=? AND code=? AND used=0 AND expires_at>?''',
            (phone, code_hash, code, int(time.time()))
        ).fetchone()
        return row is not None

    def consume_phone_code(self, phone: str, code_hash: str) -> bool:
        phone = normalize_phone(phone)
        row = self._conn.execute(
            '''SELECT 1 FROM phone_codes
               WHERE phone=? AND code_hash=? AND used=0 AND expires_at>?''',
            (phone, code_hash, int(time.time()))
        ).fetchone()
        if not row:
            return False
        self._conn.execute(
            'UPDATE phone_codes SET used=1 WHERE phone=? AND code_hash=?',
            (phone, code_hash)
        )
        self._conn.commit()
        return True

    # ── QR login tokens ────────────────────────

    _LOGIN_TOKEN_TTL = 30

    def create_login_token(self, api_id: int,
                           except_ids: Optional[List[int]] = None) -> tuple[int, bytes]:
        now = int(time.time())
        self._conn.execute(
            'DELETE FROM login_tokens WHERE expires_at <= ?',
            (now,),
        )
        token = secrets.token_bytes(32)
        expires = now + self._LOGIN_TOKEN_TTL
        self._conn.execute(
            '''INSERT INTO login_tokens
               (token, expires_at, api_id, accepted_user_id, created_at, accepted_at)
               VALUES (?, ?, ?, NULL, ?, NULL)''',
            (token, expires, int(api_id), now),
        )
        self._conn.commit()
        return expires, token

    def accept_login_token(self, token: bytes, user_id: int) -> bool:
        now = int(time.time())
        row = self._conn.execute(
            '''SELECT expires_at, accepted_user_id FROM login_tokens
               WHERE token=?''',
            (token,),
        ).fetchone()
        if not row or int(row['expires_at']) <= now:
            return False
        if row['accepted_user_id'] is not None:
            return int(row['accepted_user_id']) == int(user_id)
        self._conn.execute(
            '''UPDATE login_tokens
               SET accepted_user_id=?, accepted_at=?
               WHERE token=?''',
            (int(user_id), now, token),
        )
        self._conn.commit()
        return True

    def resolve_login_token(self, token: bytes) -> Optional[dict]:
        now = int(time.time())
        row = self._conn.execute(
            '''SELECT expires_at, accepted_user_id FROM login_tokens
               WHERE token=?''',
            (token,),
        ).fetchone()
        if not row:
            return None
        expires = int(row['expires_at'])
        if expires <= now:
            self._conn.execute('DELETE FROM login_tokens WHERE token=?', (token,))
            self._conn.commit()
            return {'expired': True}
        accepted_user_id = row['accepted_user_id']
        if accepted_user_id is not None:
            self._conn.execute('DELETE FROM login_tokens WHERE token=?', (token,))
            self._conn.commit()
            return {'user_id': int(accepted_user_id)}
        return {'expires': expires}

    def verify_phone_code(self, phone: str, code_hash: str, code: str) -> bool:
        if not self.check_phone_code(phone, code_hash, code):
            return False
        return self.consume_phone_code(phone, code_hash)

    # ── Cloud password (2FA) ─────────────────

    def user_has_password(self, user_id: int) -> bool:
        row = self._conn.execute(
            'SELECT 1 FROM user_passwords WHERE user_id=?', (user_id,)
        ).fetchone()
        return row is not None

    def get_user_password(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            'SELECT * FROM user_passwords WHERE user_id=?', (user_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def set_user_password(self, user_id: int, *, salt1: bytes, salt2: bytes,
                          g: int, p: bytes, v: bytes, hint: str = '') -> None:
        now = int(time.time())
        self._conn.execute(
            '''INSERT OR REPLACE INTO user_passwords
               (user_id, salt1, salt2, g, p, v, hint, created_at)
               VALUES (?,?,?,?,?,?,?,?)''',
            (user_id, salt1, salt2, g, p, v, hint or '', now),
        )
        self._conn.commit()

    def delete_user_password(self, user_id: int) -> None:
        self._conn.execute('DELETE FROM user_passwords WHERE user_id=?', (user_id,))
        self._conn.commit()

    def set_pending_sign_in(self, auth_key_id: int, phone: str,
                            code_hash: str, user_id: int) -> None:
        phone = normalize_phone(phone)
        expires = int(time.time()) + 600
        self._conn.execute(
            '''INSERT OR REPLACE INTO pending_sign_in
               (auth_key_id, phone, code_hash, user_id, expires_at)
               VALUES (?,?,?,?,?)''',
            (auth_key_id, phone, code_hash, user_id, expires),
        )
        self._conn.commit()

    def get_pending_sign_in(self, auth_key_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            '''SELECT * FROM pending_sign_in
               WHERE auth_key_id=? AND expires_at>?''',
            (auth_key_id, int(time.time())),
        ).fetchone()
        return dict(row) if row else None

    def clear_pending_sign_in(self, auth_key_id: int) -> None:
        self._conn.execute(
            'DELETE FROM pending_sign_in WHERE auth_key_id=?', (auth_key_id,))
        self._conn.commit()

    def create_pending_srp(self, *, user_id: int, auth_key_id: int,
                           salt1: bytes, salt2: bytes, g: int, p: bytes,
                           v: bytes) -> 'SrpSession':
        from mtproto.srp import SrpSession, create_srp_session
        self._cleanup_expired_srp()
        session = create_srp_session(
            v=v, salt1=salt1, salt2=salt2, g=g, p=p)
        self._conn.execute(
            '''INSERT OR REPLACE INTO pending_srp
               (srp_id, user_id, auth_key_id, b_hex, srp_b, salt1, salt2, g, p, v, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (
                session.srp_id,
                user_id,
                auth_key_id,
                hex(session.b),
                session.srp_b,
                salt1,
                salt2,
                g,
                p,
                v,
                int(time.time()),
            ),
        )
        self._conn.commit()
        return session

    def _cleanup_expired_srp(self) -> None:
        cutoff = int(time.time()) - 600
        self._conn.execute('DELETE FROM pending_srp WHERE created_at<?', (cutoff,))
        # Called from read paths (get_pending_srp); an uncommitted DELETE would
        # hold the WAL write lock for the whole server lifetime.
        self._conn.commit()

    def get_pending_srp(self, srp_id: int) -> Optional['SrpSession']:
        from mtproto.srp import SrpSession
        self._cleanup_expired_srp()
        row = self._conn.execute(
            'SELECT * FROM pending_srp WHERE srp_id=?', (srp_id,)
        ).fetchone()
        if not row:
            return None
        return SrpSession(
            srp_id=int(row['srp_id']),
            srp_b=bytes(row['srp_b']),
            b=int(row['b_hex'], 16),
            salt1=bytes(row['salt1']),
            salt2=bytes(row['salt2']),
            g=int(row['g']),
            p=bytes(row['p']),
            v=bytes(row['v']),
        )

    def delete_pending_srp(self, srp_id: int) -> None:
        self._conn.execute('DELETE FROM pending_srp WHERE srp_id=?', (srp_id,))
        self._conn.commit()

    # ── Auth bot phone assignments ─────────────

    def list_bot_phones_for_telegram(self, telegram_user_id: int) -> List[dict]:
        rows = self._conn.execute(
            '''SELECT phone, source, created_at FROM telegram_phones
               WHERE telegram_user_id=? ORDER BY created_at DESC''',
            (telegram_user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_bot_phone_for_telegram(self, telegram_user_id: int) -> Optional[str]:
        row = self._conn.execute(
            '''SELECT phone FROM telegram_phones
               WHERE telegram_user_id=? ORDER BY created_at DESC LIMIT 1''',
            (telegram_user_id,),
        ).fetchone()
        if row:
            return row['phone']
        row = self._conn.execute(
            'SELECT phone FROM bot_phone_assignments WHERE telegram_user_id=?',
            (telegram_user_id,),
        ).fetchone()
        return row['phone'] if row else None

    def get_telegram_user_by_phone(self, phone: str) -> Optional[int]:
        from handlers.phone_countries import anon_phone_lookup_variants

        candidates: list[str] = []
        seen: set[str] = set()
        for variant in anon_phone_lookup_variants(phone):
            if variant and variant not in seen:
                seen.add(variant)
                candidates.append(variant)
        normalized = normalize_phone(phone)
        if normalized and normalized not in seen:
            candidates.append(normalized)
        for candidate in candidates:
            telegram_user_id = self._lookup_telegram_user_for_phone(candidate)
            if telegram_user_id:
                return telegram_user_id
        return None

    def _lookup_telegram_user_for_phone(self, phone: str) -> Optional[int]:
        phone = normalize_phone(phone)
        if not phone:
            return None
        row = self._conn.execute(
            'SELECT telegram_user_id FROM telegram_phones WHERE phone=?',
            (phone,),
        ).fetchone()
        if row:
            return int(row['telegram_user_id'])
        row = self._conn.execute(
            'SELECT telegram_user_id FROM bot_phone_assignments WHERE phone=?',
            (phone,),
        ).fetchone()
        if row:
            telegram_user_id = int(row['telegram_user_id'])
            self._ensure_telegram_phone_row(
                telegram_user_id, phone, source='legacy',
            )
            self._conn.commit()
            return telegram_user_id
        row = self._conn.execute(
            '''SELECT s.telegram_user_id FROM shop_webapp_sessions s
               JOIN users u ON u.id = s.user_id
               WHERE u.phone=?
               ORDER BY s.created_at DESC LIMIT 1''',
            (phone,),
        ).fetchone()
        if row:
            telegram_user_id = int(row['telegram_user_id'])
            self._ensure_telegram_phone_row(
                telegram_user_id, phone, source='shop_session',
            )
            self._conn.commit()
            return telegram_user_id
        row = self._conn.execute(
            '''SELECT s.telegram_user_id FROM anonymous_numbers an
               JOIN shop_webapp_sessions s ON s.user_id = an.user_id
               WHERE an.phone=?
               ORDER BY s.created_at DESC LIMIT 1''',
            (phone,),
        ).fetchone()
        if row:
            telegram_user_id = int(row['telegram_user_id'])
            self._ensure_telegram_phone_row(
                telegram_user_id, phone, source='shop_anon',
            )
            self._conn.commit()
            return telegram_user_id
        row = self._conn.execute(
            '''SELECT telegram_user_id FROM shop_webapp_codes
               WHERE phone=? AND telegram_user_id > 0
               ORDER BY id DESC LIMIT 1''',
            (phone,),
        ).fetchone()
        if row:
            telegram_user_id = int(row['telegram_user_id'])
            self._ensure_telegram_phone_row(
                telegram_user_id, phone, source='shop_code',
            )
            self._conn.commit()
            return telegram_user_id
        return None

    def backfill_telegram_phone_links(self) -> int:
        """Sync telegram_phones for all known Telegram users (one-time / startup)."""
        ids: set[int] = set()
        for row in self._conn.execute(
            'SELECT DISTINCT telegram_user_id FROM telegram_phones',
        ).fetchall():
            ids.add(int(row['telegram_user_id']))
        for row in self._conn.execute(
            'SELECT DISTINCT telegram_user_id FROM bot_phone_assignments',
        ).fetchall():
            ids.add(int(row['telegram_user_id']))
        for row in self._conn.execute(
            'SELECT DISTINCT telegram_user_id FROM shop_webapp_sessions',
        ).fetchall():
            ids.add(int(row['telegram_user_id']))
        for row in self._conn.execute(
            'SELECT DISTINCT telegram_user_id FROM shop_webapp_codes '
            'WHERE telegram_user_id > 0',
        ).fetchall():
            ids.add(int(row['telegram_user_id']))
        for tg_id in ids:
            self.sync_telegram_phone_links(tg_id)
        return len(ids)

    def sync_telegram_phone_links(self, telegram_user_id: int) -> None:
        """Link all phones this Telegram user may log in with (bot / shop)."""
        tg = int(telegram_user_id)
        if tg <= 0:
            return
        phones: set[str] = set()
        for row in self.list_bot_phones_for_telegram(tg):
            phones.add(normalize_phone(row['phone']))
        for row in self._conn.execute(
            '''SELECT u.phone FROM shop_webapp_sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.telegram_user_id=?''',
            (tg,),
        ).fetchall():
            if row['phone']:
                phones.add(normalize_phone(row['phone']))
        for row in self._conn.execute(
            '''SELECT an.phone FROM shop_webapp_sessions s
               JOIN anonymous_numbers an ON an.user_id = s.user_id
               WHERE s.telegram_user_id=?''',
            (tg,),
        ).fetchall():
            phones.add(normalize_phone(row['phone']))
        for row in self._conn.execute(
            '''SELECT phone FROM shop_webapp_codes
               WHERE telegram_user_id=? AND phone != '' ''',
            (tg,),
        ).fetchall():
            phones.add(normalize_phone(row['phone']))
        for phone in phones:
            if phone:
                self._ensure_telegram_phone_row(tg, phone, source='sync')
        self._conn.commit()

    def _ensure_telegram_phone_row(self, telegram_user_id: int, phone: str,
                                   source: str = 'auto') -> None:
        normalized = normalize_phone(phone)
        if not normalized:
            return
        row = self._conn.execute(
            'SELECT telegram_user_id FROM telegram_phones WHERE phone=?',
            (normalized,),
        ).fetchone()
        if row and int(row['telegram_user_id']) != int(telegram_user_id):
            return
        if row:
            return
        now = int(time.time())
        self._conn.execute(
            '''INSERT OR IGNORE INTO telegram_phones
               (telegram_user_id, phone, source, created_at)
               VALUES (?,?,?,?)''',
            (telegram_user_id, normalized, source or 'auto', now),
        )
        # This helper runs from phone lookups during auth.sendCode. Without a
        # commit the implicit write transaction locks out gift imports, shop
        # CLI calls and verification grants (database is locked).
        self._conn.commit()

    def add_bot_phone(self, telegram_user_id: int, phone: str, *,
                      source: str = 'admin', reassign: bool = True) -> str:
        def _write() -> str:
            normalized = normalize_phone(phone)
            if not normalized:
                raise ValueError('invalid phone')
            existing = self._conn.execute(
                'SELECT telegram_user_id FROM telegram_phones WHERE phone=?',
                (normalized,),
            ).fetchone()
            if existing:
                if int(existing['telegram_user_id']) == telegram_user_id:
                    return normalized
                if not reassign:
                    raise ValueError('phone taken')
                self._conn.execute(
                    'DELETE FROM telegram_phones WHERE phone=?', (normalized,),
                )
                self._conn.execute(
                    'DELETE FROM bot_phone_assignments WHERE phone=?',
                    (normalized,),
                )
            now = int(time.time())
            self._conn.execute(
                '''INSERT INTO telegram_phones
                   (telegram_user_id, phone, source, created_at)
                   VALUES (?,?,?,?)''',
                (telegram_user_id, normalized, source or 'admin', now),
            )
            self._conn.execute(
                'DELETE FROM bot_phone_assignments WHERE telegram_user_id=?',
                (telegram_user_id,),
            )
            self._conn.execute(
                '''INSERT OR REPLACE INTO bot_phone_assignments
                   (telegram_user_id, phone, created_at) VALUES (?,?,?)''',
                (telegram_user_id, normalized, now),
            )
            self._conn.commit()
            return normalized

        return self._retry_on_db_lock(_write)

    def assign_bot_phone(self, telegram_user_id: int, *, phone: str | None = None,
                         force_new: bool = False, source: str = 'auto') -> str:
        if phone is not None:
            return self.add_bot_phone(
                telegram_user_id, phone, source=source, reassign=True,
            )
        if not force_new:
            existing = self.get_bot_phone_for_telegram(telegram_user_id)
            if existing:
                self._ensure_telegram_phone_row(
                    telegram_user_id, existing, source=source or 'auto',
                )
                self._conn.commit()
                return existing
        for _ in range(100):
            generated = self._generate_unique_bot_phone()
            try:
                return self.add_bot_phone(
                    telegram_user_id, generated,
                    source=source or 'auto', reassign=False,
                )
            except (ValueError, sqlite3.IntegrityError):
                continue
        raise RuntimeError('Could not assign a unique phone number')

    def _generate_unique_bot_phone(self) -> str:
        while True:
            phone = '79' + ''.join(str(random.randint(0, 9)) for _ in range(9))
            if self._conn.execute(
                'SELECT 1 FROM users WHERE phone=?', (phone,)
            ).fetchone():
                continue
            if self._conn.execute(
                'SELECT 1 FROM telegram_phones WHERE phone=?', (phone,),
            ).fetchone():
                continue
            if self._conn.execute(
                'SELECT 1 FROM bot_phone_assignments WHERE phone=?', (phone,),
            ).fetchone():
                continue
            return phone

    # ── User state / PTS ───────────────────────

    def _ensure_user_state(self, user_id: int) -> None:
        now = int(time.time())
        self._conn.execute(
            'INSERT OR IGNORE INTO user_state (user_id, pts, date, seq, qts) VALUES (?,?,?,?,?)',
            (user_id, 0, now, 0, 0)
        )
        if self._conn.in_transaction:
            self._conn.commit()

    def get_user_state(self, user_id: int) -> Dict[str, int]:
        # Read first: get_user_state runs on almost every RPC (updates.state,
        # getDifference, ...). INSERT OR IGNORE on the hot path opened an
        # implicit transaction that was never committed, permanently holding
        # the SQLite write lock.
        row = self._conn.execute(
            'SELECT * FROM user_state WHERE user_id=?', (user_id,)
        ).fetchone()
        if row is None:
            self._ensure_user_state(user_id)
            row = self._conn.execute(
                'SELECT * FROM user_state WHERE user_id=?', (user_id,)
            ).fetchone()
        return dict(row)

    def increment_pts(self, user_id: int) -> int:
        self._ensure_user_state(user_id)
        self._conn.execute(
            'UPDATE user_state SET pts = pts + 1, date=? WHERE user_id=?',
            (int(time.time()), user_id)
        )
        self._conn.commit()
        return self.get_user_state(user_id)['pts']

    def increment_seq(self, user_id: int) -> int:
        self._ensure_user_state(user_id)
        self._conn.execute(
            'UPDATE user_state SET seq = seq + 1 WHERE user_id=?',
            (user_id,)
        )
        self._conn.commit()
        return self.get_user_state(user_id)['seq']

    # ── Messages / dialogs ─────────────────────

    def set_user_emoji_status(self, user_id: int,
                              status: Optional[dict]) -> None:
        if status and status.get('collectible_id'):
            if not self.is_user_premium(user_id):
                raise ValueError('PREMIUM_ACCOUNT_REQUIRED')
        if not status:
            self._conn.execute(
                '''UPDATE users SET emoji_status_document_id=0,
                   emoji_status_until=0, emoji_status_collectible_id=0
                   WHERE id=?''',
                (user_id,)
            )
        elif status.get('collectible_id'):
            saved = self.get_saved_star_gift_by_unique_id_global(
                int(status['collectible_id']))
            if not saved or int(saved['owner_user_id']) != int(user_id):
                raise ValueError('COLLECTIBLE_NOT_FOUND')
            if not int(saved.get('upgraded') or 0):
                raise ValueError('COLLECTIBLE_NOT_FOUND')
            self._conn.execute(
                '''UPDATE users SET emoji_status_document_id=0,
                   emoji_status_collectible_id=?, emoji_status_until=?
                   WHERE id=?''',
                (int(status['collectible_id']),
                 status.get('until') or 0, user_id)
            )
        else:
            self._conn.execute(
                '''UPDATE users SET emoji_status_document_id=?,
                   emoji_status_until=?, emoji_status_collectible_id=0
                   WHERE id=?''',
                (status['document_id'], status.get('until') or 0, user_id)
            )
        self._conn.commit()

    def set_user_peer_color(self, user_id: int, *, for_profile: bool,
                            color_id: int = -1,
                            background_emoji_id: int = 0) -> None:
        if for_profile:
            self._conn.execute(
                '''UPDATE users SET profile_color_id=?, profile_color_bg_emoji_id=?
                   WHERE id=?''',
                (int(color_id), int(background_emoji_id), int(user_id))
            )
        else:
            self._conn.execute(
                '''UPDATE users SET peer_color_id=?, peer_color_bg_emoji_id=?
                   WHERE id=?''',
                (int(color_id), int(background_emoji_id), int(user_id))
            )
        self._conn.commit()

    def set_channel_peer_color(self, channel_id: int, *, for_profile: bool,
                               color_id: int = -1,
                               background_emoji_id: int = 0) -> None:
        if for_profile:
            self._conn.execute(
                '''UPDATE channels SET profile_color_id=?, profile_color_bg_emoji_id=?
                   WHERE id=?''',
                (int(color_id), int(background_emoji_id), int(channel_id))
            )
        else:
            self._conn.execute(
                '''UPDATE channels SET peer_color_id=?, peer_color_bg_emoji_id=?
                   WHERE id=?''',
                (int(color_id), int(background_emoji_id), int(channel_id))
            )
        self._conn.commit()

    _background_emoji_cache: Optional[tuple] = None

    def get_default_background_emoji_ids(self) -> tuple[int, list[int]]:
        cached = Storage._background_emoji_cache
        if cached is not None:
            return cached
        rows = self._conn.execute(
            '''SELECT sd.id FROM sticker_documents sd
               JOIN sticker_sets ss ON sd.set_id = ss.id
               WHERE (ss.flags & 512) != 0
               ORDER BY sd.id'''
        ).fetchall()
        doc_ids = [int(r['id']) for r in rows]
        h = self._i32_hash(sum(doc_ids) if doc_ids else 0)
        cached = (h, doc_ids)
        Storage._background_emoji_cache = cached
        return cached

    def get_users_who_know(self, user_id: int) -> List[int]:
        rows = self._conn.execute(
            '''SELECT DISTINCT owner_id AS uid FROM dialogs WHERE peer_id=?
               UNION
               SELECT DISTINCT peer_id AS uid FROM dialogs WHERE owner_id=?''',
            (user_id, user_id)
        ).fetchall()
        return [int(r['uid']) for r in rows if int(r['uid']) != user_id]

    def get_users_who_see_user(self, user_id: int) -> List[int]:
        """Users who may display this peer (DMs + shared groups/channels)."""
        seen = {int(user_id)}
        seen.update(self.get_users_who_know(user_id))
        rows = self._conn.execute(
            'SELECT channel_id FROM channel_members WHERE user_id=? AND left=0',
            (user_id,),
        ).fetchall()
        for row in rows:
            for member_id in self.list_channel_member_ids(int(row['channel_id'])):
                seen.add(int(member_id))
        return sorted(seen)

    def list_frozen_user_ids(self) -> List[int]:
        rows = self._conn.execute(
            'SELECT id FROM users WHERE frozen=1',
        ).fetchall()
        return [int(r['id']) for r in rows]

    def _next_message_date(self, user_a: int, user_b: int) -> int:
        row = self._conn.execute(
            '''SELECT COALESCE(MAX(date), 0) AS d FROM messages
               WHERE (owner_id=? AND peer_id=?)
                  OR (owner_id=? AND peer_id=?)''',
            (user_a, user_b, user_b, user_a),
        ).fetchone()
        return max(int(time.time()), int(row['d']) + 1)

    def _allocate_message_id(self) -> int:
        """Next message id shared by both mailbox copies of the same event."""
        row = self._conn.execute(
            'SELECT COALESCE(MAX(id), 0) + 1 AS n FROM messages'
        ).fetchone()
        return int(row['n'])

    def _upsert_dialog(self, owner_id: int, peer_id: int, top_message_id: int,
                       msg_date: int, outgoing: bool, unread_delta: int = 0,
                       *, mark_outbox_delivered: bool = True) -> None:
        row = self._conn.execute(
            'SELECT unread_count, read_inbox_max_id, read_outbox_max_id '
            'FROM dialogs WHERE owner_id=? AND peer_id=?',
            (owner_id, peer_id)
        ).fetchone()
        unread = (row['unread_count'] if row else 0) + unread_delta
        read_inbox = row['read_inbox_max_id'] if row else 0
        read_outbox = row['read_outbox_max_id'] if row else 0
        if outgoing and mark_outbox_delivered:
            read_outbox = top_message_id
        elif unread_delta == 0:
            read_inbox = top_message_id
        self._conn.execute(
            '''INSERT INTO dialogs
               (owner_id, peer_id, top_message_id, unread_count, last_message_date,
                last_outgoing, read_inbox_max_id, read_outbox_max_id)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(owner_id, peer_id) DO UPDATE SET
                 top_message_id=excluded.top_message_id,
                 unread_count=excluded.unread_count,
                 last_message_date=excluded.last_message_date,
                 last_outgoing=excluded.last_outgoing,
                 read_inbox_max_id=excluded.read_inbox_max_id,
                 read_outbox_max_id=excluded.read_outbox_max_id''',
            (owner_id, peer_id, top_message_id, unread, msg_date,
             1 if outgoing else 0, read_inbox, read_outbox)
        )

    def ensure_dialog(self, owner_id: int, peer_id: int) -> dict:
        row = self._conn.execute(
            'SELECT * FROM dialogs WHERE owner_id=? AND peer_id=?',
            (owner_id, peer_id)
        ).fetchone()
        if row:
            return dict(row)
        self._conn.execute(
            '''INSERT OR IGNORE INTO dialogs
               (owner_id, peer_id, top_message_id, unread_count, last_message_date,
                last_outgoing, read_inbox_max_id, read_outbox_max_id)
               VALUES (?,?,0,0,0,0,0,0)''',
            (owner_id, peer_id)
        )
        self._conn.commit()
        return dict(self._conn.execute(
            'SELECT * FROM dialogs WHERE owner_id=? AND peer_id=?',
            (owner_id, peer_id)
        ).fetchone())

    def mark_dialog_read(self, owner_id: int, peer_id: int, max_id: int) -> int:
        row = self._conn.execute(
            'SELECT top_message_id FROM dialogs WHERE owner_id=? AND peer_id=?',
            (owner_id, peer_id)
        ).fetchone()
        if not row:
            return self.increment_pts(owner_id)
        top = row['top_message_id']
        read_up_to = min(max_id, top)
        self._conn.execute(
            '''UPDATE dialogs SET unread_count=0, read_inbox_max_id=?
               WHERE owner_id=? AND peer_id=?''',
            (read_up_to, owner_id, peer_id)
        )
        self._conn.commit()
        return self.increment_pts(owner_id)

    def _insert_message(self, msg_id: int, owner_id: int, from_id: int,
                        peer_id: int, text: str, entities: List[dict],
                        msg_date: int, reply_to_msg_id: int = 0,
                        media_document_id: int = 0,
                        media_photo_id: int = 0,
                        action_json: str = '',
                        reply_markup_json: str = '',
                        paid_message_stars: int = 0,
                        media_poll_json: str = '',
                        saved_peer_id: int = 0,
                        suggested_post_json: str = '',
                        fwd_from_json: str = '',
                        offline: bool = False) -> int:
        self._ensure_user_state(owner_id)
        entities_json = json.dumps(entities, ensure_ascii=False)
        self._conn.execute(
            'UPDATE user_state SET pts = pts + 1, date=? WHERE user_id=?',
            (msg_date, owner_id),
        )
        pts = int(self._conn.execute(
            'SELECT pts FROM user_state WHERE user_id=?', (owner_id,)
        ).fetchone()['pts'])
        self._conn.execute(
            '''INSERT INTO messages
               (owner_id, id, from_id, peer_id, date, text, entities, pts,
                reply_to_msg_id, media_document_id, media_photo_id, action_json,
                reply_markup_json, paid_message_stars, media_poll_json, saved_peer_id,
                suggested_post_json, fwd_from_json, offline)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (owner_id, msg_id, from_id, peer_id, msg_date, text, entities_json,
             pts, reply_to_msg_id, media_document_id, media_photo_id,
             action_json or '', reply_markup_json or '',
             int(paid_message_stars or 0), media_poll_json or '',
             int(saved_peer_id or 0), suggested_post_json or '',
             fwd_from_json or '', int(bool(offline)))
        )
        return pts

    def send_saved_message(self, user_id: int, text: str,
                           entities: Optional[List[dict]] = None,
                           reply_to_msg_id: int = 0,
                           media_document_id: int = 0,
                           media_photo_id: int = 0,
                           media_poll_json: str = '') -> dict:
        entities = entities or []
        msg_id = self._allocate_message_id()
        now = self._next_message_date(user_id, user_id)
        pts = self._insert_message(
            msg_id, user_id, user_id, user_id, text, entities, now,
            reply_to_msg_id, media_document_id, media_photo_id,
            media_poll_json=media_poll_json)
        self._upsert_dialog(user_id, user_id, msg_id, now, True, 0)
        self._conn.commit()
        msg = self.get_message(user_id, msg_id)
        return {
            'id': msg_id,
            'sender_msg': msg,
            'pts_sender': pts,
        }

    def send_private_message(self, sender_id: int, recipient_id: int,
                             text: str,
                             entities: Optional[List[dict]] = None,
                             reply_to_msg_id: int = 0,
                             media_document_id: int = 0,
                             media_photo_id: int = 0,
                             paid_message_stars: int = 0,
                             media_poll_json: str = '',
                             *, deliver_to_recipient: bool = True,
                             offline: bool = False) -> dict:
        if sender_id == recipient_id:
            raise ValueError('PEER_ID_INVALID')

        sender = self.get_user_by_id(sender_id)
        recipient = self.get_user_by_id(recipient_id)
        if not recipient:
            raise ValueError('PEER_ID_INVALID')
        if sender and sender.get('is_bot') and recipient.get('is_bot'):
            raise ValueError('USER_IS_BOT')

        msg_id = self._allocate_message_id()
        now = self._next_message_date(sender_id, recipient_id)
        if paid_message_stars > 0:
            self.charge_paid_message(
                sender_id, recipient_id, paid_message_stars, msg_id=msg_id)

        entities = entities or []
        pts_sender = self._insert_message(
            msg_id, sender_id, sender_id, recipient_id, text, entities, now,
            reply_to_msg_id, media_document_id, media_photo_id,
            paid_message_stars=paid_message_stars,
            media_poll_json=media_poll_json,
            offline=offline)
        pts_recipient = 0
        recipient_msg = None
        if deliver_to_recipient:
            pts_recipient = self._insert_message(
                msg_id, recipient_id, sender_id, sender_id, text, entities, now,
                reply_to_msg_id, media_document_id, media_photo_id,
                paid_message_stars=paid_message_stars,
                media_poll_json=media_poll_json,
                offline=offline)
            recipient_msg = self.get_message(recipient_id, msg_id)
            self._upsert_dialog(recipient_id, sender_id, msg_id, now, False, 1)
        self._upsert_dialog(
            sender_id, recipient_id, msg_id, now, True, 0,
            mark_outbox_delivered=deliver_to_recipient)
        self.clear_business_away_message_sent(sender_id, recipient_id)
        self._conn.commit()
        return {
            'id': msg_id,
            'sender_msg': self.get_message(sender_id, msg_id),
            'recipient_msg': recipient_msg,
            'pts_sender': pts_sender,
            'pts_recipient': pts_recipient,
        }

    def forward_messages(self, sender_id: int, from_peer_id: int,
                         to_peer_id: int, msg_ids: List[int],
                         *, drop_author: bool = False,
                         drop_media_captions: bool = False) -> List[dict]:
        if not msg_ids:
            raise ValueError('MESSAGE_IDS_EMPTY')
        results = []
        for src_id in msg_ids:
            src = self.get_message(sender_id, int(src_id))
            if not src:
                raise ValueError('MESSAGE_ID_INVALID')
            if int(src.get('peer_id') or 0) != int(from_peer_id):
                if from_peer_id >= config.CHANNEL_ID_MIN:
                    self.ensure_channel_messages_for_member(from_peer_id, sender_id)
                    src = self.get_message(sender_id, int(src_id))
                if not src or int(src.get('peer_id') or 0) != int(from_peer_id):
                    raise ValueError('MESSAGE_ID_INVALID')

            orig_from = int(src.get('from_id') or 0)
            fwd = {
                'from_id': orig_from,
                'date': int(src.get('date') or 0),
                'from_name': '',
                'channel_post': 0,
            }
            if from_peer_id >= config.CHANNEL_ID_MIN:
                fwd['from_id'] = from_peer_id
                fwd['channel_post'] = int(src_id)
            existing_fwd = src.get('fwd_from_json')
            if existing_fwd:
                if isinstance(existing_fwd, str):
                    try:
                        fwd = json.loads(existing_fwd)
                    except json.JSONDecodeError:
                        pass
                elif isinstance(existing_fwd, dict):
                    fwd = dict(existing_fwd)
            if drop_author:
                fwd['from_name'] = 'Forwarded message'
                fwd.pop('from_id', None)

            text = '' if drop_media_captions else (src.get('text') or '')
            entities = [] if drop_media_captions else (src.get('entities') or [])
            if isinstance(entities, str):
                try:
                    entities = json.loads(entities)
                except json.JSONDecodeError:
                    entities = []
            fwd_json = json.dumps(fwd, ensure_ascii=False, separators=(',', ':'))

            if to_peer_id >= config.CHANNEL_ID_MIN:
                raise ValueError('PEER_ID_INVALID')
            if to_peer_id == sender_id:
                msg_id = self._allocate_message_id()
                now = self._next_message_date(sender_id, sender_id)
                pts = self._insert_message(
                    msg_id, sender_id, sender_id, sender_id, text, entities, now,
                    media_document_id=int(src.get('media_document_id') or 0),
                    media_photo_id=int(src.get('media_photo_id') or 0),
                    media_poll_json=src.get('media_poll_json') or '',
                    fwd_from_json=fwd_json)
                self._upsert_dialog(sender_id, sender_id, msg_id, now, True, 0)
                self._conn.commit()
                results.append({
                    'sender_msg': self.get_message(sender_id, msg_id),
                    'pts_sender': pts,
                })
            else:
                result = self.send_private_message(
                    sender_id, to_peer_id, text, entities,
                    media_document_id=int(src.get('media_document_id') or 0),
                    media_photo_id=int(src.get('media_photo_id') or 0),
                    media_poll_json=src.get('media_poll_json') or '')
                msg_id = int(result['id'])
                self._conn.execute(
                    'UPDATE messages SET fwd_from_json=? WHERE id=?',
                    (fwd_json, msg_id))
                if result.get('recipient_msg'):
                    self._conn.execute(
                        'UPDATE messages SET fwd_from_json=? '
                        'WHERE owner_id=? AND id=?',
                        (fwd_json, to_peer_id, msg_id))
                self._conn.commit()
                result['sender_msg'] = self.get_message(sender_id, msg_id)
                results.append(result)
        return results

    def insert_paid_messages_price_notice(self, owner_id: int,
                                          stars: int) -> Optional[dict]:
        """messageActionPaidMessagesPrice in owner's Saved Messages."""
        action_json = json.dumps(
            {'type': 'paid_messages_price', 'stars': int(stars)},
            ensure_ascii=False)
        msg_id = self._allocate_message_id()
        now = self._next_message_date(owner_id, owner_id)
        pts = self._insert_message(
            msg_id, owner_id, owner_id, owner_id, '', [], now,
            action_json=action_json)
        self._upsert_dialog(owner_id, owner_id, msg_id, now, True, 0)
        return {'msg': self.get_message(owner_id, msg_id), 'pts': pts}

    def _insert_paid_price_service(self, owner_id: int, peer_id: int,
                                   stars: int) -> dict:
        """messageActionPaidMessagesPrice in a DM (both mailbox copies)."""
        action_json = json.dumps(
            {'type': 'paid_messages_price', 'stars': int(stars)},
            ensure_ascii=False)
        msg_id = self._allocate_message_id()
        now = self._next_message_date(owner_id, peer_id)
        pts_owner = self._insert_message(
            msg_id, owner_id, owner_id, peer_id, '', [], now,
            action_json=action_json)
        pts_peer = self._insert_message(
            msg_id, peer_id, owner_id, owner_id, '', [], now,
            action_json=action_json)
        self._upsert_dialog(owner_id, peer_id, msg_id, now, True, 0)
        self._upsert_dialog(peer_id, owner_id, msg_id, now, False, 1)
        return {
            'peer_id': peer_id,
            'owner_msg': self.get_message(owner_id, msg_id),
            'peer_msg': self.get_message(peer_id, msg_id),
            'pts_owner': pts_owner,
            'pts_peer': pts_peer,
        }

    def insert_paid_messages_price_notices(self, owner_id: int,
                                         stars: int) -> dict:
        """No service messages — clients learn the price via updateUser / userFull."""
        return {'saved': None, 'dms': []}

    def insert_paid_messages_refund_notice(self, owner_id: int, peer_id: int,
                                           count: int,
                                           stars: int) -> Optional[dict]:
        if count <= 0 or stars <= 0:
            return None
        action_json = json.dumps(
            {'type': 'paid_messages_refunded', 'count': count, 'stars': stars},
            ensure_ascii=False)
        msg_id = self._allocate_message_id()
        now = self._next_message_date(owner_id, peer_id)
        pts_owner = self._insert_message(
            msg_id, owner_id, owner_id, peer_id, '', [], now,
            action_json=action_json)
        pts_peer = self._insert_message(
            msg_id, peer_id, owner_id, owner_id, '', [], now,
            action_json=action_json)
        self._upsert_dialog(owner_id, peer_id, msg_id, now, True, 0)
        self._upsert_dialog(peer_id, owner_id, msg_id, now, False, 1)
        self._conn.commit()
        return {
            'peer_id': peer_id,
            'owner_msg': self.get_message(owner_id, msg_id),
            'peer_msg': self.get_message(peer_id, msg_id),
            'pts_owner': pts_owner,
            'pts_peer': pts_peer,
        }

    def send_bot_message(self, bot_id: int, user_id: int, text: str,
                         entities: Optional[List[dict]] = None,
                         media_document_id: int = 0,
                         media_photo_id: int = 0,
                         reply_markup_json: str = '') -> dict:
        entities = entities or []
        msg_id = self._allocate_message_id()
        now = self._next_message_date(user_id, bot_id)
        pts = self._insert_message(
            msg_id, user_id, bot_id, bot_id, text, entities, now,
            media_document_id=media_document_id,
            media_photo_id=media_photo_id,
            reply_markup_json=reply_markup_json)
        self._upsert_dialog(user_id, bot_id, msg_id, now, False, 1)
        self._conn.commit()
        return {
            'recipient_msg': self.get_message(user_id, msg_id),
            'pts_recipient': pts,
        }

    def _message_row(self, row) -> dict:
        d = dict(row)
        if isinstance(d.get('entities'), str):
            d['entities'] = json.loads(d['entities'])
        if d['from_id'] == d['owner_id'] == d['peer_id']:
            d['out'] = True
        if not d.get('action_json'):
            d.pop('action_json', None)
        d['offline'] = bool(int(d.get('offline') or 0))
        return d

    def send_star_gift_to_channel(
            self, sender_id: int, channel_id: int, gift_id: int,
            *, message_text: str = '',
            message_entities: Optional[List[dict]] = None,
            hide_name: bool = False,
            include_upgrade: bool = False,
            pay_ton: bool = False) -> dict:
        """Send a star gift to a channel profile (owner_user_id = channel_id)."""
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('PEER_ID_INVALID')
        if int(ch.get('monoforum') or 0):
            raise ValueError('PEER_ID_INVALID')
        username = (ch.get('username') or '').strip()
        is_member = self.is_channel_member(channel_id, sender_id)
        is_creator = int(ch.get('creator_id') or 0) == int(sender_id)
        if not username and not is_member and not is_creator:
            raise ValueError('CHANNEL_PRIVATE')

        gift = self.get_star_gift(gift_id)
        if not gift:
            raise ValueError('STARGIFT_INVALID')
        # Channel peers skip user global-privacy disallowed_gifts.
        send_error = self.get_star_gift_send_error(
            sender_id, gift, recipient_id=None)
        if send_error:
            raise ValueError(send_error)

        stars = int(gift['stars'])
        can_upgrade = self.gift_can_upgrade(gift_id)
        upgrade_price = (
            self.get_gift_upgrade_stars(gift_id) if can_upgrade else 0)
        prepaid = bool(
            include_upgrade and can_upgrade and upgrade_price > 0)
        if include_upgrade and not prepaid:
            raise ValueError('STARGIFT_NOT_UPGRADABLE')

        total_cost = stars + (upgrade_price if prepaid else 0)
        if pay_ton:
            if not config.can_use_ton_payments(sender_id):
                raise ValueError('TON_PAYMENT_NOT_ALLOWED')
            ton_cost = config.stars_to_nanoton(total_cost)
            balance = self.get_ton_balance(sender_id)
            if balance < ton_cost:
                raise ValueError('BALANCE_TOO_LOW')
            self.adjust_ton_balance(
                sender_id, -ton_cost, peer_channel_id=channel_id, gift=True,
                title=gift.get('title') or 'Gift')
        else:
            balance = self.get_stars_balance(sender_id)
            if not config.INFINITE_STARS and balance < total_cost:
                raise ValueError('BALANCE_TOO_LOW')
            self.adjust_stars_balance(
                sender_id, -stars, peer_channel_id=channel_id, gift=True,
                title=gift.get('title') or 'Gift')
            if prepaid:
                self.adjust_stars_balance(
                    sender_id, -upgrade_price, peer_channel_id=channel_id,
                    stargift_upgrade=True,
                    title=(gift.get('title') or 'Gift') + ' upgrade')

        msg_id = self._allocate_message_id()
        now = self._next_message_date(sender_id, channel_id)
        entities = message_entities or []
        convert_stars = int(gift.get('convert_stars') or 0)
        saved_upgrade_stars = upgrade_price if prepaid else None

        saved = self.insert_saved_star_gift({
            'owner_user_id': channel_id,
            'from_user_id': sender_id,
            'gift_id': gift_id,
            'msg_id': msg_id,
            'stars': stars,
            'convert_stars': convert_stars,
            'upgrade_stars': saved_upgrade_stars,
            'prepaid_upgrade': 1 if prepaid else 0,
            'name_hidden': 1 if hide_name else 0,
            'date': now,
            'message_text': message_text or '',
            'message_entities_json': json.dumps(entities, ensure_ascii=False),
            'can_upgrade': 1 if can_upgrade else 0,
        })
        if int(gift.get('limited') or 0):
            remains = max(0, int(gift.get('availability_remains') or 0) - 1)
            first_sale = normalize_unix_time(gift.get('first_sale_date')) or now
            sold_out = 1 if remains <= 0 else int(gift.get('sold_out') or 0)
            last_sale = now if sold_out else normalize_unix_time(
                gift.get('last_sale_date'))
            self._conn.execute(
                '''UPDATE star_gifts
                   SET availability_remains=?, sold_out=?,
                       first_sale_date=?, last_sale_date=?
                   WHERE gift_id=?''',
                (remains, sold_out, first_sale, last_sale, gift_id),
            )
            self._conn.commit()
            self.recompute_star_gifts_catalog_hash()

        channel_action = {
            'type': 'star_gift',
            'gift_id': int(gift_id),
            'hide_name': hide_name,
            'name_hidden': hide_name,
            'message_text': message_text or '',
            'message_entities': entities,
            'convert_stars': convert_stars,
            'can_upgrade': can_upgrade,
            'prepaid_upgrade': prepaid,
            'upgrade_stars': (
                int(upgrade_price) if (prepaid or can_upgrade) and upgrade_price
                else None),
            'saved': True,
            'peer_id': channel_id,
            'saved_id': int(saved['saved_id']),
            'gift_msg_id': msg_id,
            'from_id': sender_id,
        }
        # Gift is stored on the channel profile only — do not post a public
        # service message to every subscriber (spam + previously desynced pts).
        self._conn.commit()

        return {
            'id': msg_id,
            'saved': saved,
            'sender_msg': None,
            'recipient_msg': None,
            'pts_sender': 0,
            'pts_recipient': 0,
            'pts_channel': 0,
            'member_msgs': {},
            'member_ids': [],
            'channel_id': channel_id,
            'channel_gift_silent': True,
            'channel_action': channel_action,
        }

    def _insert_channel_mailbox_message(
            self, *, owner_id: int, msg_id: int, from_id: int,
            channel_id: int, now: int, text: str = '',
            entities_json: str = '[]', pts_channel: int,
            reply_to_msg_id: int = 0, media_document_id: int = 0,
            media_photo_id: int = 0, action_json: str = '',
            media_poll_json: str = '') -> dict:
        """Insert a channel message copy without bumping common user pts.

        Channel updates use channels.pts / channel_member_state.pts. Bumping
        user_state.pts here desyncs updates.getDifference and hangs clients.
        """
        self._conn.execute(
            '''INSERT INTO messages
               (owner_id, id, from_id, peer_id, date, text, entities, pts,
                reply_to_msg_id, media_document_id, media_photo_id,
                action_json, media_poll_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (owner_id, msg_id, from_id, channel_id, now, text or '',
             entities_json or '[]', int(pts_channel), reply_to_msg_id,
             media_document_id, media_photo_id, action_json or '',
             media_poll_json or ''))
        return self.get_message(owner_id, msg_id)

    def _broadcast_channel_gift_message(
            self, channel_id: int, sender_id: int, msg_id: int, now: int,
            action_json: str) -> dict:
        """Fan-out a gift service message to channel members (no admin check)."""
        member_ids = self.list_channel_member_ids(channel_id)
        pts_channel = self.increment_channel_pts(channel_id)
        result_msgs = {}
        for owner_id in member_ids:
            is_sender = int(owner_id) == int(sender_id)
            result_msgs[owner_id] = self._insert_channel_mailbox_message(
                owner_id=owner_id, msg_id=msg_id, from_id=sender_id,
                channel_id=channel_id, now=now, pts_channel=pts_channel,
                action_json=action_json or '')
            self._upsert_dialog(
                owner_id, channel_id, msg_id, now, is_sender,
                0 if is_sender else 1)
            self._conn.execute(
                '''UPDATE channel_member_state SET pts=?, unread_count=unread_count+?
                   WHERE channel_id=? AND user_id=?''',
                (pts_channel, 0 if is_sender else 1, channel_id, owner_id))

        self._conn.execute(
            'UPDATE channels SET top_message_id=? WHERE id=?',
            (msg_id, channel_id))
        self.init_channel_message_views(channel_id, msg_id)
        return {
            'pts_channel': pts_channel,
            'member_msgs': result_msgs,
            'member_ids': member_ids,
        }

    def send_star_gift(self, sender_id: int, recipient_id: int, gift_id: int,
                       *, message_text: str = '',
                       message_entities: Optional[List[dict]] = None,
                       hide_name: bool = False,
                       include_upgrade: bool = False,
                       pay_ton: bool = False) -> dict:
        if int(recipient_id) >= int(config.CHANNEL_ID_MIN):
            return self.send_star_gift_to_channel(
                sender_id, recipient_id, gift_id,
                message_text=message_text,
                message_entities=message_entities,
                hide_name=hide_name,
                include_upgrade=include_upgrade,
                pay_ton=pay_ton)

        recipient = self.get_user_by_id(recipient_id)
        if not recipient:
            raise ValueError('PEER_ID_INVALID')

        gift = self.get_star_gift(gift_id)
        if not gift:
            raise ValueError('STARGIFT_INVALID')
        send_error = self.get_star_gift_send_error(
            sender_id, gift, recipient_id=recipient_id)
        if send_error:
            raise ValueError(send_error)

        stars = int(gift['stars'])
        can_upgrade = self.gift_can_upgrade(gift_id)
        upgrade_price = (
            self.get_gift_upgrade_stars(gift_id) if can_upgrade else 0)
        prepaid = bool(
            include_upgrade and can_upgrade and upgrade_price > 0)
        if include_upgrade and not prepaid:
            raise ValueError('STARGIFT_NOT_UPGRADABLE')

        total_cost = stars + (upgrade_price if prepaid else 0)
        if pay_ton:
            if not config.can_use_ton_payments(sender_id):
                raise ValueError('TON_PAYMENT_NOT_ALLOWED')
            ton_cost = config.stars_to_nanoton(total_cost)
            balance = self.get_ton_balance(sender_id)
            if balance < ton_cost:
                raise ValueError('BALANCE_TOO_LOW')
        else:
            balance = self.get_stars_balance(sender_id)
            if not config.INFINITE_STARS and balance < total_cost:
                raise ValueError('BALANCE_TOO_LOW')

        if pay_ton:
            self.adjust_ton_balance(
                sender_id, -ton_cost, peer_user_id=recipient_id, gift=True,
                title=gift.get('title') or 'Gift')
        else:
            self.adjust_stars_balance(
                sender_id, -stars, peer_user_id=recipient_id, gift=True,
                title=gift.get('title') or 'Gift')
            if prepaid:
                self.adjust_stars_balance(
                    sender_id, -upgrade_price, peer_user_id=recipient_id,
                    stargift_upgrade=True,
                    title=(gift.get('title') or 'Gift') + ' upgrade')

        msg_id = self._allocate_message_id()
        now = self._next_message_date(sender_id, recipient_id)
        entities = message_entities or []
        convert_stars = int(gift.get('convert_stars') or 0)
        saved_upgrade_stars = upgrade_price if prepaid else None

        saved = self.insert_saved_star_gift({
            'owner_user_id': recipient_id,
            'from_user_id': sender_id,
            'gift_id': gift_id,
            'msg_id': msg_id,
            'stars': stars,
            'convert_stars': convert_stars,
            'upgrade_stars': saved_upgrade_stars,
            'prepaid_upgrade': 1 if prepaid else 0,
            'name_hidden': 1 if hide_name else 0,
            'date': now,
            'message_text': message_text or '',
            'message_entities_json': json.dumps(entities, ensure_ascii=False),
            'can_upgrade': 1 if can_upgrade else 0,
        })
        if int(gift.get('limited') or 0):
            remains = max(0, int(gift.get('availability_remains') or 0) - 1)
            first_sale = normalize_unix_time(gift.get('first_sale_date')) or now
            sold_out = 1 if remains <= 0 else int(gift.get('sold_out') or 0)
            last_sale = now if sold_out else normalize_unix_time(
                gift.get('last_sale_date'))
            self._conn.execute(
                '''UPDATE star_gifts
                   SET availability_remains=?, sold_out=?,
                       first_sale_date=?, last_sale_date=?
                   WHERE gift_id=?''',
                (remains, sold_out, first_sale, last_sale, gift_id),
            )
            self._conn.commit()
            self.recompute_star_gifts_catalog_hash()

        base_action = {
            'type': 'star_gift',
            'gift_id': int(gift_id),
            'hide_name': hide_name,
            'name_hidden': hide_name,
            'message_text': message_text or '',
            'message_entities': entities,
            'convert_stars': convert_stars,
            'can_upgrade': can_upgrade,
            'prepaid_upgrade': prepaid,
            'upgrade_stars': (
                int(upgrade_price) if (prepaid or can_upgrade) and upgrade_price
                else None),
        }

        if sender_id == recipient_id:
            action = dict(
                base_action,
                saved=True,
                peer_id=recipient_id,
                saved_id=msg_id,
                gift_msg_id=msg_id,
            )
            pts = self._insert_message(
                msg_id, sender_id, sender_id, sender_id, '', [], now,
                action_json=json.dumps(action, ensure_ascii=False))
            self._upsert_dialog(sender_id, sender_id, msg_id, now, True, 0)
            self._conn.commit()
            msg = self.get_message(sender_id, msg_id)
            return {
                'id': msg_id,
                'saved': saved,
                'sender_msg': msg,
                'recipient_msg': msg,
                'pts_sender': pts,
                'pts_recipient': pts,
            }

        sender_action = dict(base_action, gift_msg_id=msg_id)
        recipient_action = dict(
            base_action,
            saved=True,
            peer_id=recipient_id,
            saved_id=msg_id,
            gift_msg_id=msg_id,
        )

        pts_sender = self._insert_message(
            msg_id, sender_id, sender_id, recipient_id, '', [], now,
            action_json=json.dumps(sender_action, ensure_ascii=False))
        pts_recipient = self._insert_message(
            msg_id, recipient_id, sender_id, sender_id, '', [], now,
            action_json=json.dumps(recipient_action, ensure_ascii=False))
        self._upsert_dialog(sender_id, recipient_id, msg_id, now, True, 0)
        self._upsert_dialog(recipient_id, sender_id, msg_id, now, False, 1)
        self._conn.commit()
        return {
            'id': msg_id,
            'saved': saved,
            'sender_msg': self.get_message(sender_id, msg_id),
            'recipient_msg': self.get_message(recipient_id, msg_id),
            'pts_sender': pts_sender,
            'pts_recipient': pts_recipient,
        }

    def grant_star_gift_free(self, sender_id: int, recipient_id: int,
                             gift_id: int) -> dict:
        """Deliver a star gift without charging the sender (case prizes, etc.)."""
        recipient = self.get_user_by_id(recipient_id)
        if not recipient:
            raise ValueError('PEER_ID_INVALID')

        gift = self.get_star_gift(gift_id)
        if not gift:
            raise ValueError('STARGIFT_INVALID')
        send_error = self.get_star_gift_send_error(
            sender_id, gift, recipient_id=recipient_id,
            skip_usage_limited=True)
        if send_error:
            raise ValueError(send_error)

        stars = int(gift['stars'])
        can_upgrade = self.gift_can_upgrade(gift_id)
        upgrade_price = (
            self.get_gift_upgrade_stars(gift_id) if can_upgrade else 0)

        msg_id = self._allocate_message_id()
        now = self._next_message_date(sender_id, recipient_id)
        convert_stars = int(gift.get('convert_stars') or 0)

        saved = self.insert_saved_star_gift({
            'owner_user_id': recipient_id,
            'from_user_id': sender_id,
            'gift_id': gift_id,
            'msg_id': msg_id,
            'stars': stars,
            'convert_stars': convert_stars,
            'upgrade_stars': None,
            'prepaid_upgrade': 0,
            'name_hidden': 0,
            'date': now,
            'message_text': '',
            'message_entities_json': '[]',
            'can_upgrade': 1 if can_upgrade else 0,
        })
        # Case/bot grants must not consume shop availability counters.

        base_action = {
            'type': 'star_gift',
            'gift_id': int(gift_id),
            'hide_name': False,
            'name_hidden': False,
            'message_text': '',
            'message_entities': [],
            'convert_stars': convert_stars,
            'can_upgrade': can_upgrade,
            'prepaid_upgrade': False,
            'upgrade_stars': (
                int(upgrade_price) if can_upgrade and upgrade_price else None),
        }

        if sender_id == recipient_id:
            action = dict(
                base_action,
                saved=True,
                peer_id=recipient_id,
                saved_id=msg_id,
                gift_msg_id=msg_id,
            )
            self._insert_message(
                msg_id, sender_id, sender_id, sender_id, '', [], now,
                action_json=json.dumps(action, ensure_ascii=False))
            self._upsert_dialog(sender_id, sender_id, msg_id, now, True, 0)
            self._conn.commit()
            return saved

        sender_action = dict(base_action, gift_msg_id=msg_id)
        recipient_action = dict(
            base_action,
            saved=True,
            peer_id=recipient_id,
            saved_id=msg_id,
            gift_msg_id=msg_id,
        )

        self._insert_message(
            msg_id, sender_id, sender_id, recipient_id, '', [], now,
            action_json=json.dumps(sender_action, ensure_ascii=False))
        self._insert_message(
            msg_id, recipient_id, sender_id, sender_id, '', [], now,
            action_json=json.dumps(recipient_action, ensure_ascii=False))
        self._upsert_dialog(sender_id, recipient_id, msg_id, now, True, 0)
        self._upsert_dialog(recipient_id, sender_id, msg_id, now, False, 1)
        self._conn.commit()
        return saved

    def add_service_message(self, owner_id: int, text: str,
                            entities: List[dict]) -> dict:
        msg_id = self._allocate_message_id()
        now = self._next_message_date(owner_id, SERVICE_USER_ID)
        pts = self._insert_message(
            msg_id, owner_id, SERVICE_USER_ID, SERVICE_USER_ID, text, entities, now)
        self._upsert_dialog(owner_id, SERVICE_USER_ID, msg_id, now, False, 1)
        self._conn.commit()
        return self.get_message(owner_id, msg_id) | {'pts': pts}

    def get_channel_message_for_viewer(
            self, channel_id: int, msg_id: int, viewer_id: int
            ) -> Optional[dict]:
        """Fetch a channel message for profile/history, including public viewers."""
        channel_id = int(channel_id)
        msg_id = int(msg_id)
        viewer_id = int(viewer_id)
        ch = self.get_channel(channel_id)
        if not ch or msg_id <= 0:
            return None

        msg = self.get_message(viewer_id, msg_id)
        if msg and int(msg.get('peer_id') or 0) == channel_id:
            return msg

        is_member = self.is_channel_member(channel_id, viewer_id)
        if is_member:
            self.ensure_channel_messages_for_member(channel_id, viewer_id)
            msg = self.get_message(viewer_id, msg_id)
            if msg and int(msg.get('peer_id') or 0) == channel_id:
                return msg

        # Public channel / personal-channel preview: serve from source mailbox
        if is_member or (ch.get('username') or '').strip():
            source_id = self._channel_message_source_owner(channel_id)
            if source_id and source_id != viewer_id:
                msg = self.get_message(source_id, msg_id)
                if msg and int(msg.get('peer_id') or 0) == channel_id:
                    out = dict(msg)
                    out['owner_id'] = viewer_id
                    out['out'] = False
                    return out
        return None

    def get_message(self, owner_id: int, msg_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM messages WHERE owner_id=? AND id=?',
            (owner_id, msg_id)
        ).fetchone()
        return self._message_row(row) if row else None

    def get_messages_for_peer(self, owner_id: int, peer_id: int,
                              limit: int = 50) -> List[dict]:
        rows = self._conn.execute(
            '''SELECT * FROM (
                   SELECT * FROM messages
                   WHERE owner_id=? AND peer_id=?
                   ORDER BY date DESC, id DESC
                   LIMIT ?
               ) ORDER BY date ASC, id ASC''',
            (owner_id, peer_id, limit)
        ).fetchall()
        return [self._message_row(r) for r in rows]

    def count_messages_for_peer(self, owner_id: int, peer_id: int) -> int:
        row = self._conn.execute(
            'SELECT COUNT(*) AS n FROM messages WHERE owner_id=? AND peer_id=?',
            (owner_id, peer_id)
        ).fetchone()
        return int(row['n'])

    def get_messages_history(self, owner_id: int, peer_id: int,
                             offset_id: int, add_offset: int, limit: int,
                             max_id: int = 0, min_id: int = 0) -> tuple:
        """Return (messages newest-first by id, total_count) for messages.getHistory."""
        limit = max(1, min(int(limit or 50), 100))
        offset_id = int(offset_id or 0)
        add_offset = int(add_offset or 0)
        max_id = int(max_id or 0)
        min_id = int(min_id or 0)
        query_owner_id = owner_id
        public_channel_view = False
        if peer_id >= config.CHANNEL_ID_MIN:
            ch = self.get_channel(peer_id)
            if ch and ch.get('username') and not self.is_channel_member(
                    peer_id, owner_id):
                source_id = self._channel_message_source_owner(peer_id)
                if source_id:
                    query_owner_id = source_id
                    public_channel_view = True

        def _fetch_history(q_owner: int) -> tuple[list, int]:
            clauses = ['owner_id=?', 'peer_id=?']
            params: list = [q_owner, peer_id]
            if max_id > 0:
                clauses.append('id < ?')
                params.append(max_id)
            if min_id > 0:
                clauses.append('id > ?')
                params.append(min_id)
            where = ' AND '.join(clauses)

            total = int(self._conn.execute(
                f'SELECT COUNT(*) AS n FROM messages WHERE {where}',
                params,
            ).fetchone()['n'])
            if total == 0:
                return [], 0

            if offset_id > 0:
                # MTProto offset_id is exclusive: clients page older history
                # with offset_id = oldest loaded message id and would loop
                # forever if the anchor itself were returned again.
                anchor_idx = int(self._conn.execute(
                    f'''SELECT COUNT(*) AS n FROM messages
                        WHERE {where} AND id >= ?''',
                    params + [offset_id],
                ).fetchone()['n'])
                start = max(0, anchor_idx + add_offset)
            else:
                start = max(0, add_offset)

            if start >= total:
                return [], total

            rows = self._conn.execute(
                f'''SELECT * FROM messages WHERE {where}
                    ORDER BY id DESC LIMIT ? OFFSET ?''',
                params + [limit, start],
            ).fetchall()
            return [self._message_row(r) for r in rows], total

        messages, total = _fetch_history(query_owner_id)
        if (not messages and not public_channel_view
                and peer_id >= config.CHANNEL_ID_MIN
                and self.is_channel_member(peer_id, owner_id)):
            self.ensure_channel_messages_for_member(peer_id, owner_id)
            messages, total = _fetch_history(owner_id)
        return messages, total

    def repair_dialog(self, owner_id: int, peer_id: int) -> None:
        """Sync top_message_id, read markers and unread_count with stored messages."""
        top_row = self._conn.execute(
            'SELECT MAX(id) AS top_id FROM messages WHERE owner_id=? AND peer_id=?',
            (owner_id, peer_id),
        ).fetchone()
        top_id = int(top_row['top_id'] or 0) if top_row else 0
        if top_id <= 0:
            dialog = self._conn.execute(
                'SELECT * FROM dialogs WHERE owner_id=? AND peer_id=?',
                (owner_id, peer_id),
            ).fetchone()
            if dialog and int(dialog['top_message_id'] or 0) > 0:
                self._conn.execute(
                    '''UPDATE dialogs SET top_message_id=0, read_inbox_max_id=0,
                       read_outbox_max_id=0, unread_count=0
                       WHERE owner_id=? AND peer_id=?''',
                    (owner_id, peer_id),
                )
                self._conn.commit()
            return

        dialog = self._conn.execute(
            'SELECT * FROM dialogs WHERE owner_id=? AND peer_id=?',
            (owner_id, peer_id),
        ).fetchone()
        if not dialog:
            return

        def clamp_read(val: int) -> int:
            val = int(val or 0)
            if val <= 0:
                return 0
            if val >= top_id:
                return top_id
            row = self._conn.execute(
                '''SELECT MAX(id) AS m FROM messages
                   WHERE owner_id=? AND peer_id=? AND id<=?''',
                (owner_id, peer_id, val),
            ).fetchone()
            return int(row['m'] or 0) if row else 0

        read_inbox = clamp_read(dialog['read_inbox_max_id'])
        read_outbox = clamp_read(dialog['read_outbox_max_id'])
        unread = int(self._conn.execute(
            '''SELECT COUNT(*) AS n FROM messages
               WHERE owner_id=? AND peer_id=? AND id>? AND from_id!=?''',
            (owner_id, peer_id, read_inbox, owner_id),
        ).fetchone()['n'])

        if (int(dialog['top_message_id']) == top_id
                and int(dialog['read_inbox_max_id']) == read_inbox
                and int(dialog['read_outbox_max_id']) == read_outbox
                and int(dialog['unread_count']) == unread):
            return

        self._conn.execute(
            '''UPDATE dialogs SET top_message_id=?, read_inbox_max_id=?,
               read_outbox_max_id=?, unread_count=?
               WHERE owner_id=? AND peer_id=?''',
            (top_id, read_inbox, read_outbox, unread, owner_id, peer_id),
        )
        self._conn.commit()

    def repair_dialog_top_message(self, owner_id: int, peer_id: int) -> None:
        self.repair_dialog(owner_id, peer_id)

    def get_messages_since_pts(self, owner_id: int, pts: int) -> List[dict]:
        # Channel / megagroup copies use channel pts and must not appear in
        # updates.getDifference (common pts). Including them desyncs clients.
        rows = self._conn.execute(
            '''SELECT * FROM messages
               WHERE owner_id=? AND pts > ? AND peer_id < ?
               ORDER BY pts''',
            (owner_id, pts, int(config.CHANNEL_ID_MIN))
        ).fetchall()
        return [self._message_row(r) for r in rows]

    def list_user_channel_ids(self, user_id: int) -> List[int]:
        rows = self._conn.execute(
            '''SELECT channel_id FROM channel_members
               WHERE user_id=? AND left=0''',
            (user_id,)).fetchall()
        return [int(r['channel_id']) for r in rows]

    def _dialogs_where_sql(self, owner_id: int) -> tuple[str, list]:
        clauses = [
            'd.owner_id=?',
            '''(
                 d.top_message_id > 0
                 OR (
                   d.peer_id >= ?
                   AND EXISTS (
                     SELECT 1 FROM channel_members cm
                     WHERE cm.channel_id=d.peer_id
                       AND cm.user_id=d.owner_id AND cm.left=0
                   )
                 )
               )''',
        ]
        params: list = [owner_id, config.CHANNEL_ID_MIN]
        return ' AND '.join(clauses), params

    def count_dialogs(self, owner_id: int) -> int:
        where, params = self._dialogs_where_sql(owner_id)
        row = self._conn.execute(
            f'SELECT COUNT(*) AS n FROM dialogs d WHERE {where}',
            params,
        ).fetchone()
        return int(row['n'])

    def get_dialogs(self, owner_id: int, limit: int = 100,
                    offset_date: int = 0, offset_id: int = 0,
                    offset_peer: int = 0) -> List[dict]:
        where, params = self._dialogs_where_sql(owner_id)
        offset_date = int(offset_date or 0)
        offset_id = int(offset_id or 0)
        offset_peer = int(offset_peer or 0)
        if offset_date > 0 or offset_id > 0 or offset_peer != 0:
            where += ''' AND (
                d.last_message_date < ?
                OR (d.last_message_date = ? AND d.top_message_id < ?)
                OR (d.last_message_date = ? AND d.top_message_id = ?
                    AND d.peer_id < ?)
            )'''
            params.extend([
                offset_date, offset_date, offset_id,
                offset_date, offset_id, offset_peer,
            ])
        rows = self._conn.execute(
            f'''SELECT d.* FROM dialogs d
               WHERE {where}
               ORDER BY d.last_message_date DESC, d.top_message_id DESC,
                        d.peer_id DESC
               LIMIT ?''',
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def collect_users_for_messages(self, viewer_id: int,
                                   messages: List[dict]) -> List[dict]:
        ids = {viewer_id}
        for m in messages:
            ids.add(m['from_id'])
            ids.add(m['peer_id'])
        users = []
        for uid in ids:
            u = self.get_user_by_id(uid)
            if u:
                users.append(u)
        return users

    def enqueue_pending_update(self, user_id: int, update_data: bytes,
                               auth_key_id: Optional[int] = None,
                               *, layer: Optional[int] = None) -> None:
        if int(user_id) >= config.CHANNEL_ID_MIN:
            return
        pending = self._count_all_pending_updates(user_id)
        if pending >= _PENDING_UPDATES_MAX_QUEUE:
            self._trim_pending_updates(user_id, keep=_PENDING_UPDATES_MAX_QUEUE // 2)
        self._conn.execute(
            'INSERT INTO pending_updates (user_id, update_data, created_at, '
            'auth_key_id, layer) VALUES (?,?,?,?,?)',
            (user_id, update_data, int(time.time()), auth_key_id, layer),
        )
        self._conn.commit()
        try:
            from shop_webapp.push_wake import signal_pending_push
            signal_pending_push()
        except Exception:
            pass

    def list_pending_update_users(self) -> List[int]:
        rows = self._conn.execute(
            'SELECT DISTINCT user_id FROM pending_updates ORDER BY user_id'
        ).fetchall()
        return [int(row['user_id']) for row in rows]

    def purge_stale_pending_updates(self, max_age_seconds: int = 300) -> int:
        """Drop obsolete push copies; updates.getDifference remains authoritative."""
        cutoff = int(time.time()) - max(1, int(max_age_seconds))
        cur = self._conn.execute(
            'DELETE FROM pending_updates WHERE created_at < ?', (cutoff,))
        self._conn.commit()
        return max(0, int(cur.rowcount or 0))

    def _count_all_pending_updates(self, user_id: int) -> int:
        row = self._conn.execute(
            'SELECT COUNT(*) AS n FROM pending_updates WHERE user_id=?',
            (user_id,),
        ).fetchone()
        return int(row['n'] if row else 0)

    def _trim_pending_updates(self, user_id: int, *,
                              auth_key_id: Optional[int] = None,
                              keep: int = 100) -> None:
        keep = max(1, int(keep))
        if auth_key_id is None:
            row = self._conn.execute(
                'SELECT COUNT(*) AS n FROM pending_updates WHERE user_id=?',
                (user_id,),
            ).fetchone()
            if int(row['n']) <= keep:
                return
            to_delete = int(row['n']) - keep
            self._conn.execute(
                'DELETE FROM pending_updates WHERE id IN ('
                'SELECT id FROM pending_updates WHERE user_id=? '
                'ORDER BY id ASC LIMIT ?)',
                (user_id, to_delete),
            )
            self._conn.commit()
            return
        row = self._conn.execute(
            'SELECT COUNT(*) AS n FROM pending_updates '
            'WHERE user_id=? AND (auth_key_id IS NULL OR auth_key_id=?)',
            (user_id, auth_key_id),
        ).fetchone()
        if int(row['n']) <= keep:
            return
        to_delete = int(row['n']) - keep
        self._conn.execute(
            'DELETE FROM pending_updates WHERE id IN ('
            'SELECT id FROM pending_updates '
            'WHERE user_id=? AND (auth_key_id IS NULL OR auth_key_id=?) '
            'ORDER BY id ASC LIMIT ?)',
            (user_id, auth_key_id, to_delete),
        )
        self._conn.commit()

    def count_pending_updates(self, user_id: int,
                              auth_key_id: Optional[int] = None) -> int:
        if auth_key_id is None:
            row = self._conn.execute(
                'SELECT COUNT(*) AS n FROM pending_updates '
                'WHERE user_id=? AND auth_key_id IS NULL',
                (user_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                'SELECT COUNT(*) AS n FROM pending_updates '
                'WHERE user_id=? AND (auth_key_id IS NULL OR auth_key_id=?)',
                (user_id, auth_key_id),
            ).fetchone()
        return int(row['n'] if row else 0)

    def drain_pending_updates(self, user_id: int,
                              auth_key_id: Optional[int] = None,
                              *, limit: int = 0) -> List[bytes]:
        sql = (
            'SELECT id, update_data FROM pending_updates '
            'WHERE user_id=? AND auth_key_id IS NULL ORDER BY id'
        )
        params: list = [user_id]
        if auth_key_id is not None:
            sql = (
                'SELECT id, update_data FROM pending_updates '
                'WHERE user_id=? AND (auth_key_id IS NULL OR auth_key_id=?) '
                'ORDER BY id'
            )
            params = [user_id, auth_key_id]
        if limit > 0:
            sql += ' LIMIT ?'
            params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        if not rows:
            return []
        ids = [r['id'] for r in rows]
        self._conn.execute(
            f'DELETE FROM pending_updates WHERE id IN ({",".join("?" * len(ids))})',
            ids,
        )
        self._conn.commit()
        return [r['update_data'] for r in rows]

    def peek_pending_updates(self, user_id: int, *,
                             limit: int = 0) -> List[dict]:
        """Pending rows with their target key/layer, oldest first."""
        sql = ('SELECT id, auth_key_id, layer, update_data, created_at '
               'FROM pending_updates WHERE user_id=? ORDER BY id')
        params: list = [user_id]
        if limit > 0:
            sql += ' LIMIT ?'
            params.append(int(limit))
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def delete_pending_updates(self, ids: List[int]) -> None:
        if not ids:
            return
        self._conn.execute(
            f'DELETE FROM pending_updates WHERE id IN ({",".join("?" * len(ids))})',
            [int(i) for i in ids],
        )
        self._conn.commit()

    def store_pending_login_message(self, phone: str, code: str) -> None:
        text, entities = login_code_text(code)
        self._conn.execute(
            '''INSERT OR REPLACE INTO pending_login_messages
               (phone, code, text, entities) VALUES (?,?,?,?)''',
            (normalize_phone(phone), code, text,
             json.dumps(entities, ensure_ascii=False))
        )
        self._conn.commit()

    def deliver_pending_login_message(self, phone: str, user_id: int) -> Optional[dict]:
        from handlers.phone_countries import anon_phone_lookup_variants
        for variant in anon_phone_lookup_variants(phone):
            row = self._conn.execute(
                'SELECT * FROM pending_login_messages WHERE phone=?', (variant,)
            ).fetchone()
            if not row:
                continue
            entities = json.loads(row['entities'])
            msg = self.add_service_message(user_id, row['text'], entities)
            self._conn.execute(
                'DELETE FROM pending_login_messages WHERE phone=?', (variant,)
            )
            self._conn.commit()
            return msg
        return None

    def ensure_service_dialog_bootstrap(self, user_id: int, *,
                                        phone: str = '') -> Optional[dict]:
        """Ensure brand-new accounts have the Telegram service chat (777000)."""
        uid = int(user_id)
        row = self._conn.execute(
            'SELECT 1 FROM dialogs WHERE owner_id=? AND peer_id=?',
            (uid, SERVICE_USER_ID),
        ).fetchone()
        if row:
            return None
        if phone:
            msg = self.deliver_pending_login_message(phone, uid)
            if msg:
                return msg
        welcome = (
            'Добро пожаловать в KoalGram! '
            'Здесь будут приходить коды входа и уведомления от сервиса.'
        )
        return self.add_service_message(uid, welcome, [])

    def send_login_code_message(self, phone: str, code: str) -> Optional[dict]:
        user = self.get_user_by_phone(phone)
        if user:
            text, entities = login_code_text(code)
            return self.add_service_message(user['id'], text, entities)
        self.store_pending_login_message(phone, code)
        return None

    # ── Sticker sets ───────────────────────────

    @staticmethod
    def _i32_hash(value: int) -> int:
        v = int(value) & 0xFFFFFFFF
        return v - 0x100000000 if v >= 0x80000000 else v

    def upsert_sticker_set(self, set_id: int, access_hash: int, short_name: str,
                           title: str, flags: int, count: int,
                           doc_hash: int) -> dict:
        doc_hash = self._i32_hash(doc_hash)
        flags = int(flags)
        self._conn.execute(
            '''INSERT INTO sticker_sets
               (id, access_hash, short_name, title, flags, count, hash)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 access_hash=excluded.access_hash,
                 short_name=excluded.short_name,
                 title=excluded.title,
                 flags=excluded.flags,
                 count=excluded.count,
                 hash=excluded.hash''',
            (set_id, access_hash, short_name, title, flags, count, doc_hash)
        )
        self._conn.commit()
        return self.get_sticker_set_by_id(set_id)

    def allocate_custom_sticker_set_id(self) -> int:
        start = 9100000000000000000
        row = self._conn.execute(
            '''SELECT COALESCE(MAX(id), ?) + 1 AS n FROM sticker_sets
               WHERE id >= ?''',
            (start - 1, start),
        ).fetchone()
        return int(row['n'])

    def mark_sticker_set_installed(self, set_id: int) -> None:
        row = self._conn.execute(
            'SELECT flags FROM sticker_sets WHERE id=?', (set_id,)
        ).fetchone()
        if not row:
            return
        flags = int(row['flags']) | 1
        self._conn.execute(
            'UPDATE sticker_sets SET flags=? WHERE id=?',
            (flags, set_id)
        )
        self._conn.commit()

    def ensure_all_emoji_sets_installed(self) -> None:
        # Emoji sets are installed explicitly by the user (featured tab / addemoji).
        return

    def get_server_kv(self, key: str, default: str = '') -> str:
        row = self._conn.execute(
            'SELECT value FROM server_kv WHERE key=?', (key,)
        ).fetchone()
        return row['value'] if row else default

    def set_server_kv(self, key: str, value: str) -> None:
        self._conn.execute(
            '''INSERT OR REPLACE INTO server_kv (key, value) VALUES (?, ?)''',
            (key, value),
        )
        self._conn.commit()

    def apply_featured_emoji_catalog(self, short_names: List[str]) -> None:
        """Configure public emoji store catalog; packs stay regular (no featured flag)."""
        import json
        import config as app_config
        names = [n.strip().lstrip('@') for n in short_names if (n or '').strip()]
        self._conn.execute(
            'UPDATE sticker_sets SET flags = (flags & ~1) & ~256 WHERE flags & 128'
        )
        hidden = getattr(app_config, 'EMOJI_CATALOG_HIDDEN', frozenset())
        if hidden:
            for name in hidden:
                self._conn.execute(
                    '''UPDATE sticker_sets SET flags = (flags & ~256) & ~1
                       WHERE flags & 128 AND lower(short_name)=lower(?)''',
                    (name,),
                )
        self.set_server_kv('featured_emoji_order', json.dumps(names))
        ver = int(self.get_server_kv('emoji_catalog_version', '0') or 0) + 1
        self.set_server_kv('emoji_catalog_version', str(ver))
        for name in names:
            self._conn.execute(
                '''UPDATE sticker_sets SET flags = (flags & ~256) & ~1
                   WHERE flags & 128 AND lower(short_name)=lower(?)''',
                (name,),
            )
        self._conn.commit()

    def _emoji_catalog_short_names(self) -> List[str]:
        import json
        import config as app_config
        order_raw = self.get_server_kv('featured_emoji_order', '[]')
        try:
            order = json.loads(order_raw)
        except Exception:
            order = []
        if not order:
            order = list(getattr(app_config, 'FEATURED_EMOJI_CATALOG', ()))
        return [str(n).strip().lstrip('@') for n in order if (n or '').strip()]

    def _is_emoji_catalog_member(self, short_name: str) -> bool:
        key = (short_name or '').lower()
        if not key or self._is_emoji_catalog_hidden(key):
            return False
        return key in {n.lower() for n in self._emoji_catalog_short_names()}

    def _emoji_catalog_hidden_names(self) -> tuple:
        import config as app_config
        hidden = getattr(app_config, 'EMOJI_CATALOG_HIDDEN', frozenset())
        return tuple(n.lower() for n in hidden if n)

    def _is_emoji_catalog_hidden(self, short_name: str) -> bool:
        return (short_name or '').lower() in self._emoji_catalog_hidden_names()

    def emoji_catalog_version(self) -> int:
        return int(self.get_server_kv('emoji_catalog_version', '0') or 0)

    def emoji_catalog_hash(self, *, featured_only: bool = True) -> int:
        """Hash for featured/search emoji catalog responses (64-bit, i64)."""
        import struct
        import zlib
        if featured_only:
            sets = self.list_featured_emoji_sticker_sets()
        else:
            sets = self.list_emoji_sticker_sets(installed_only=True)
        parts = [f'{ss["id"]}:{ss.get("hash", 0)}' for ss in sets]
        blob = f'v{self.emoji_catalog_version()}|{"|".join(parts)}'.encode()
        crc = zlib.crc32(blob) & 0xFFFFFFFF
        return struct.unpack('<q', struct.pack('<I', crc) + b'\x00\x00\x00\x00')[0]

    def list_featured_emoji_sticker_sets(self) -> List[dict]:
        import json
        order_raw = self.get_server_kv('featured_emoji_order', '[]')
        try:
            order = json.loads(order_raw)
        except Exception:
            order = []
        if not order:
            order = self._emoji_catalog_short_names()
        if not order:
            return []
        by_name = {}
        for name in order:
            row = self._conn.execute(
                "SELECT * FROM sticker_sets WHERE flags & 128 != 0 "
                "AND lower(short_name)=lower(?)",
                (name,),
            ).fetchone()
            if row:
                by_name[name.lower()] = dict(row)
        out = []
        for name in order:
            row = by_name.get(str(name).lower())
            if row and not self._is_emoji_catalog_hidden(row.get('short_name') or ''):
                out.append(row)
        return out

    def upsert_sticker_document(self, doc: dict, *, commit: bool = True) -> dict:
        self._conn.execute(
            '''INSERT INTO sticker_documents
               (id, access_hash, set_id, file_path, mime_type, size, width, height,
                alt, file_reference, date, attributes_json, sort_order,
                thumb_path, thumb_path_bytes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 access_hash=excluded.access_hash,
                 set_id=excluded.set_id,
                 file_path=excluded.file_path,
                 mime_type=excluded.mime_type,
                 size=excluded.size,
                 width=excluded.width,
                 height=excluded.height,
                 alt=excluded.alt,
                 file_reference=excluded.file_reference,
                 date=excluded.date,
                 attributes_json=excluded.attributes_json,
                 sort_order=excluded.sort_order,
                 thumb_path=excluded.thumb_path,
                 thumb_path_bytes=excluded.thumb_path_bytes''',
            (doc['id'], doc['access_hash'], doc['set_id'], doc['file_path'],
             doc.get('mime_type') or 'image/webp', doc.get('size') or 0,
             doc.get('width') or 512, doc.get('height') or 512,
             doc.get('alt') or '', doc.get('file_reference') or b'\x00' * 8,
             doc.get('date') or int(time.time()),
             doc.get('attributes_json') or '[]',
             int(doc.get('sort_order') or 0),
             doc.get('thumb_path') or '',
             doc.get('thumb_path_bytes') or b'')
        )
        if commit:
            self._conn.commit()
        return doc

    def set_sticker_document_thumb(self, doc_id: int, thumb_path: str) -> None:
        self._conn.execute(
            'UPDATE sticker_documents SET thumb_path=? WHERE id=?',
            (thumb_path, int(doc_id)))
        self._conn.commit()

    def replace_sticker_packs(self, set_id: int,
                              packs: List[tuple]) -> None:
        self._conn.execute(
            'DELETE FROM sticker_packs WHERE set_id=?', (set_id,))
        for emoticon, doc_ids in packs:
            self._conn.execute(
                '''INSERT INTO sticker_packs (set_id, emoticon, document_ids)
                   VALUES (?,?,?)''',
                (set_id, emoticon, json.dumps(doc_ids))
            )
        self._conn.commit()

    def get_sticker_set_by_id(self, set_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM sticker_sets WHERE id=?', (set_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_sticker_set_by_short_name(self, short_name: str) -> Optional[dict]:
        short_name = short_name.strip().lstrip('@')
        row = self._conn.execute(
            'SELECT * FROM sticker_sets WHERE lower(short_name)=lower(?)',
            (short_name,)
        ).fetchone()
        return dict(row) if row else None

    def search_sticker_sets(self, query: str, emoji_only: bool = False,
                            *, catalog_only: bool = False) -> List[dict]:
        q = query.strip().lstrip('@')
        if emoji_only:
            base = 'SELECT * FROM sticker_sets WHERE flags & 128 != 0'
            params_hidden: list = []
            if catalog_only:
                catalog = [
                    n.lower() for n in self._emoji_catalog_short_names()
                    if not self._is_emoji_catalog_hidden(n)
                ]
                if not catalog:
                    return []
                placeholders = ','.join('?' * len(catalog))
                base += f' AND lower(short_name) IN ({placeholders})'
                params_hidden.extend(catalog)
            hidden = self._emoji_catalog_hidden_names()
            if hidden:
                placeholders = ','.join('?' * len(hidden))
                base += f' AND lower(short_name) NOT IN ({placeholders})'
                params_hidden.extend(hidden)
        else:
            base = 'SELECT * FROM sticker_sets WHERE (flags & 128) = 0'
            params_hidden = []
        if not q:
            rows = self._conn.execute(
                base + ' ORDER BY title', params_hidden).fetchall()
            return [dict(r) for r in rows]
        pattern = f'%{q.lower()}%'
        rows = self._conn.execute(
            base + ''' AND (lower(short_name) LIKE ? OR lower(title) LIKE ?)
               ORDER BY title''',
            params_hidden + [pattern, pattern],
        ).fetchall()
        return [dict(r) for r in rows]

    def search_emoji_documents(self, query: str = '', emoticon: str = '',
                               *, offset: int = 0,
                               limit: int = 50) -> List[dict]:
        """Search custom emoji documents for messages.searchStickers."""
        terms = []
        for part in (query, emoticon):
            part = (part or '').strip()
            if part:
                terms.append(part.lower())
        catalog = [
            n.lower() for n in self._emoji_catalog_short_names()
            if not self._is_emoji_catalog_hidden(n)
        ]
        catalog_sql = ''
        params: list = []
        if catalog:
            placeholders = ','.join('?' * len(catalog))
            catalog_sql = f' OR lower(ss.short_name) IN ({placeholders})'
            params.extend(catalog)
        base = (
            'SELECT sd.* FROM sticker_documents sd '
            'JOIN sticker_sets ss ON ss.id = sd.set_id '
            "WHERE ss.flags & 128 != 0 "
            f"AND (ss.flags & 1 != 0{catalog_sql}) "
            "AND lower(ss.short_name) != 'airgrambanneremoji' "
        )
        if terms:
            clauses = []
            for term in terms:
                clauses.append(
                    '(lower(sd.alt) LIKE ? OR lower(ss.title) LIKE ? '
                    'OR lower(ss.short_name) LIKE ?)')
                pattern = f'%{term}%'
                params.extend([pattern, pattern, pattern])
            base += ' AND (' + ' OR '.join(clauses) + ')'
        base += ' ORDER BY sd.sort_order, sd.id'
        if limit > 0:
            base += ' LIMIT ? OFFSET ?'
            params.extend([int(limit), max(0, int(offset))])
        rows = self._conn.execute(base, params).fetchall()
        return [dict(r) for r in rows]

    def emoji_documents_hash(self) -> int:
        catalog = [
            n.lower() for n in self._emoji_catalog_short_names()
            if not self._is_emoji_catalog_hidden(n)
        ]
        sql = (
            "SELECT COALESCE(SUM(sd.id), 0) AS h FROM sticker_documents sd "
            'JOIN sticker_sets ss ON ss.id = sd.set_id '
            "WHERE ss.flags & 128 != 0 "
            "AND lower(ss.short_name) != 'airgrambanneremoji' "
        )
        params: list = []
        if catalog:
            placeholders = ','.join('?' * len(catalog))
            sql += f'AND (ss.flags & 1 != 0 OR lower(ss.short_name) IN ({placeholders}))'
            params.extend(catalog)
        else:
            sql += 'AND ss.flags & 1 != 0'
        row = self._conn.execute(sql, params).fetchone()
        return int(row['h'] or 0)

    def get_emoji_sticker_set(self) -> Optional[dict]:
        for slug in ('RestrictedEmoji', 'AnimatedEmojies', 'StatusPack'):
            row = self._conn.execute(
                'SELECT * FROM sticker_sets WHERE lower(short_name)=lower(?) '
                'AND flags & 128 != 0 LIMIT 1',
                (slug,),
            ).fetchone()
            if row:
                return dict(row)
        # Never treat shop banner custom-emoji as the system emoji set —
        # Desktop loads those TGS during signup and can crash (SafeRound/NaN).
        row = self._conn.execute(
            "SELECT * FROM sticker_sets WHERE flags & 128 != 0 "
            "AND lower(short_name) != 'airgrambanneremoji' "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def list_sticker_sets(self, *, installed_only: bool = False) -> List[dict]:
        if installed_only:
            rows = self._conn.execute(
                'SELECT * FROM sticker_sets '
                'WHERE (flags & 128) = 0 AND (flags & 1) != 0 '
                'ORDER BY title'
            ).fetchall()
        else:
            rows = self._conn.execute(
                'SELECT * FROM sticker_sets WHERE (flags & 128) = 0 '
                'ORDER BY title'
            ).fetchall()
        return [dict(r) for r in rows]

    def list_emoji_sticker_sets(self, *, installed_only: bool = True) -> List[dict]:
        # Exclude shop-only banner pack from client emoji sticker catalogs.
        ban = " AND lower(short_name) != 'airgrambanneremoji' "
        if installed_only:
            rows = self._conn.execute(
                'SELECT * FROM sticker_sets '
                'WHERE flags & 128 != 0 AND (flags & 1) != 0 '
                + ban +
                'ORDER BY title'
            ).fetchall()
        else:
            rows = self._conn.execute(
                'SELECT * FROM sticker_sets WHERE flags & 128 != 0 '
                + ban +
                'ORDER BY title'
            ).fetchall()
        return [dict(r) for r in rows]

    def sticker_sets_hash(self, emoji_only: bool = False,
                          installed_only: bool = False,
                          featured_only: bool = False) -> int:
        if emoji_only:
            if featured_only:
                names = [
                    n.lower() for n in self._emoji_catalog_short_names()
                    if not self._is_emoji_catalog_hidden(n)
                ]
                if not names:
                    return self._i32_hash(0)
                placeholders = ','.join('?' * len(names))
                row = self._conn.execute(
                    f'SELECT COALESCE(SUM(hash), 0) AS h FROM sticker_sets '
                    f'WHERE flags & 128 != 0 AND lower(short_name) IN ({placeholders})',
                    names,
                ).fetchone()
            elif installed_only:
                row = self._conn.execute(
                    'SELECT COALESCE(SUM(hash), 0) AS h FROM sticker_sets '
                    'WHERE flags & 128 != 0 AND (flags & 1) != 0'
                ).fetchone()
            else:
                row = self._conn.execute(
                    'SELECT COALESCE(SUM(hash), 0) AS h FROM sticker_sets '
                    'WHERE flags & 128 != 0'
                ).fetchone()
        else:
            if installed_only:
                row = self._conn.execute(
                    'SELECT COALESCE(SUM(hash), 0) AS h FROM sticker_sets '
                    'WHERE (flags & 128) = 0 AND (flags & 1) != 0'
                ).fetchone()
            else:
                row = self._conn.execute(
                    'SELECT COALESCE(SUM(hash), 0) AS h FROM sticker_sets '
                    'WHERE (flags & 128) = 0'
                ).fetchone()
        return self._i32_hash(int(row['h']))

    def get_sticker_documents(self, set_id: int) -> List[dict]:
        rows = self._conn.execute(
            'SELECT * FROM sticker_documents WHERE set_id=? ORDER BY sort_order, id',
            (set_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_sticker_document_by_id(self, doc_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM sticker_documents WHERE id=?', (doc_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_document_by_id(self, doc_id: int) -> Optional[dict]:
        return self.get_sticker_document_by_id(doc_id)

    def save_upload_file_part(self, file_id: int, file_part: int,
                              data: bytes) -> None:
        self._conn.execute(
            '''INSERT INTO upload_file_parts (file_id, file_part, data)
               VALUES (?,?,?)
               ON CONFLICT(file_id, file_part) DO UPDATE SET data=excluded.data''',
            (int(file_id), int(file_part), data)
        )
        self._conn.commit()

    def clear_upload_file_parts(self, file_id: int) -> None:
        self._conn.execute(
            'DELETE FROM upload_file_parts WHERE file_id=?', (int(file_id),))
        self._conn.commit()

    def assemble_upload_file(self, file_id: int, parts: int,
                             dest_path: str) -> int:
        rows = self._conn.execute(
            '''SELECT file_part, data FROM upload_file_parts
               WHERE file_id=? ORDER BY file_part''',
            (int(file_id),)
        ).fetchall()
        if len(rows) < parts:
            raise ValueError('FILE_PARTS_MISSING')
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        total = 0
        with open(dest_path, 'wb') as out:
            for row in rows[:parts]:
                chunk = row['data']
                out.write(chunk)
                total += len(chunk)
        self.clear_upload_file_parts(file_id)
        return total

    def _allocate_upload_document_id(self) -> int:
        row = self._conn.execute(
            '''SELECT COALESCE(MAX(id), ?) + 1 AS n FROM sticker_documents
               WHERE id >= ?''',
            (7000000000000, 7000000000000)
        ).fetchone()
        return int(row['n'])

    def _allocate_photo_id(self) -> int:
        row = self._conn.execute(
            '''SELECT COALESCE(MAX(id), ?) + 1 AS n FROM photos
               WHERE id >= ?''',
            (8000000000000, 8000000000000)
        ).fetchone()
        return int(row['n'])

    def create_uploaded_document(self, *, file_path: str, mime_type: str,
                                 size: int, attributes_json: str,
                                 width: int = 0, height: int = 0) -> dict:
        doc_id = self._allocate_upload_document_id()
        doc = {
            'id': doc_id,
            'access_hash': secrets.randbits(63),
            'set_id': config.UPLOADS_DOC_SET_ID,
            'file_path': file_path,
            'mime_type': mime_type or 'application/octet-stream',
            'size': int(size),
            'width': int(width or 512),
            'height': int(height or 512),
            'alt': '',
            'file_reference': secrets.token_bytes(8),
            'date': int(time.time()),
            'attributes_json': attributes_json or '[]',
            'sort_order': 0,
        }
        return self.upsert_sticker_document(doc)

    def create_photo(self, *, owner_id: int, file_path: str, width: int,
                     height: int, size: int,
                     is_profile: bool = False) -> dict:
        photo_id = self._allocate_photo_id()
        photo = {
            'id': photo_id,
            'access_hash': secrets.randbits(63),
            'owner_id': int(owner_id),
            'file_path': file_path,
            'file_reference': secrets.token_bytes(8),
            'date': int(time.time()),
            'width': int(width or 512),
            'height': int(height or 512),
            'size': int(size),
            'dc_id': config.DC_ID,
            'is_profile': 1 if is_profile else 0,
        }
        self._conn.execute(
            '''INSERT INTO photos
               (id, access_hash, owner_id, file_path, file_reference, date,
                width, height, size, dc_id, is_profile)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (photo['id'], photo['access_hash'], photo['owner_id'],
             photo['file_path'], photo['file_reference'], photo['date'],
             photo['width'], photo['height'], photo['size'], photo['dc_id'],
             photo['is_profile'])
        )
        self._conn.commit()
        return photo

    def get_photo_by_id(self, photo_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM photos WHERE id=?', (int(photo_id),)
        ).fetchone()
        return dict(row) if row else None

    def mark_photo_profile(self, photo_id: int, *, is_profile: bool = True
                           ) -> None:
        self._conn.execute(
            'UPDATE photos SET is_profile=? WHERE id=?',
            (1 if is_profile else 0, int(photo_id)))
        self._conn.commit()

    def set_user_profile_photo(self, user_id: int, photo_id: int) -> dict:
        photo_id = int(photo_id or 0)
        if photo_id:
            self._conn.execute(
                'UPDATE photos SET is_profile=1 WHERE id=? AND owner_id=?',
                (photo_id, int(user_id)))
        self._conn.execute(
            'UPDATE users SET profile_photo_id=? WHERE id=?',
            (photo_id, int(user_id))
        )
        self._conn.commit()
        return self.get_user_by_id(user_id)

    def clear_user_profile_photo(self, user_id: int) -> dict:
        self._conn.execute(
            'UPDATE users SET profile_photo_id=0 WHERE id=?',
            (int(user_id),))
        self._conn.commit()
        return self.get_user_by_id(user_id)

    def delete_user_photos(self, user_id: int, photo_ids: List[int]
                           ) -> List[int]:
        deleted: List[int] = []
        user = self.get_user_by_id(user_id)
        current = int((user or {}).get('profile_photo_id') or 0)
        for pid in photo_ids:
            pid = int(pid or 0)
            if pid <= 0:
                continue
            row = self._conn.execute(
                'SELECT id FROM photos WHERE id=? AND owner_id=?',
                (pid, int(user_id))).fetchone()
            if not row:
                continue
            self._conn.execute('DELETE FROM photos WHERE id=?', (pid,))
            deleted.append(pid)
            if pid == current:
                current = 0
                self._conn.execute(
                    'UPDATE users SET profile_photo_id=0 WHERE id=?',
                    (int(user_id),))
        self._conn.commit()
        return deleted

    def get_user_profile_photo(self, user_id: int) -> Optional[dict]:
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        photo_id = int(user.get('profile_photo_id') or 0)
        if not photo_id:
            return None
        return self.get_photo_by_id(photo_id)

    def list_user_photos(self, user_id: int, limit: int = 50) -> List[dict]:
        rows = self._conn.execute(
            '''SELECT * FROM photos
               WHERE owner_id=? AND is_profile=1
               ORDER BY date DESC, id DESC LIMIT ?''',
            (int(user_id), int(limit))
        ).fetchall()
        return [dict(r) for r in rows]

    def get_sticker_packs(self, set_id: int) -> List[tuple]:
        rows = self._conn.execute(
            'SELECT emoticon, document_ids FROM sticker_packs WHERE set_id=?',
            (set_id,)
        ).fetchall()
        out = []
        for r in rows:
            out.append((r['emoticon'], json.loads(r['document_ids'])))
        return out

    # ── Star gifts ─────────────────────────────

    def ensure_star_gifts_doc_set(self) -> dict:
        """Virtual sticker set bucket for star-gift documents."""
        return self.upsert_sticker_set(
            config.STAR_GIFTS_DOC_SET_ID,
            access_hash=0x5A4F47494654,
            short_name='__stargifts__',
            title='Star Gifts',
            flags=1,
            count=0,
            doc_hash=0,
        )

    def get_star_gifts_catalog_hash(self) -> int:
        row = self._conn.execute(
            'SELECT hash FROM star_gifts_catalog WHERE id=1'
        ).fetchone()
        return int(row['hash']) if row else 0

    def set_star_gifts_catalog_hash(self, h: int) -> None:
        self._conn.execute(
            'INSERT INTO star_gifts_catalog (id, hash) VALUES (1, ?) '
            'ON CONFLICT(id) DO UPDATE SET hash=excluded.hash',
            (int(h),))
        self._conn.commit()

    def clear_star_gifts_catalog(self) -> None:
        self._conn.execute('DELETE FROM star_gifts')
        self._conn.commit()

    def clear_star_gift_upgrade_attrs(self, gift_id: Optional[int] = None,
                                      *, commit: bool = True) -> None:
        if gift_id is None:
            self._conn.execute('DELETE FROM star_gift_upgrade_attrs')
        else:
            self._conn.execute(
                'DELETE FROM star_gift_upgrade_attrs WHERE gift_id=?',
                (gift_id,))
        if commit:
            self._conn.commit()

    def set_star_gift_upgrade_importing(self, gift_id: int, importing: bool) -> None:
        """Block upgrades while import_star_gifts is loading upgrade attributes."""
        gid = int(gift_id)
        self._conn.execute(
            'UPDATE star_gifts SET upgrade_importing=?, upgrade_variants=0 '
            'WHERE gift_id=?',
            (1 if importing else 0, gid),
        )
        if importing:
            self._conn.execute(
                '''UPDATE saved_star_gifts SET can_upgrade=0
                   WHERE gift_id=? AND upgraded=0''',
                (gid,),
            )
        else:
            can = self._gift_has_upgrade_pool(gid)
            self._conn.execute(
                '''UPDATE saved_star_gifts SET can_upgrade=?
                   WHERE gift_id=? AND upgraded=0''',
                (1 if can else 0, gid),
            )
            variants = (self.count_star_gift_upgrade_attrs(gid) if can else 0)
            self._conn.execute(
                'UPDATE star_gifts SET upgrade_variants=? WHERE gift_id=?',
                (variants, gid),
            )
        self._conn.commit()

    def upsert_star_gift(self, gift: dict) -> dict:
        row = dict(gift)
        existing = (self.get_star_gift(int(row['gift_id']))
                    if row.get('gift_id') is not None else None)
        for key in (
            'limited', 'sold_out', 'birthday', 'require_premium',
            'limited_per_user', 'is_auction', 'peer_color_available',
            'is_tester_gift',
        ):
            row[key] = int(bool(row.get(key)))
        row['availability_resale'] = 0
        row['resell_min_stars'] = None
        if row.get('sort_order') is None:
            if existing and existing.get('sort_order') is not None:
                row['sort_order'] = int(existing['sort_order'])
            else:
                row['sort_order'] = self._next_star_gift_sort_order()
        else:
            row['sort_order'] = int(row.get('sort_order') or 0)
        if row.get('limited') and row.get('availability_remains') is None:
            total = row.get('availability_total')
            row['availability_remains'] = (
                int(total) if total is not None else None)
        if row.get('limited') and row.get('availability_remains') is not None:
            row['sold_out'] = int(
                row.get('sold_out') or int(row['availability_remains']) <= 0)
        if existing:
            if row.get('first_sale_date') is None:
                row['first_sale_date'] = existing.get('first_sale_date')
            if row.get('last_sale_date') is None:
                row['last_sale_date'] = existing.get('last_sale_date')
            if row.get('drop_at') is None:
                row['drop_at'] = existing.get('drop_at')
        row['upgrade_variants'] = int(row.get('upgrade_variants') or 0)
        # Shop visibility: callers that re-import a gift can explicitly bring it
        # back into the catalog (shop_hidden=0). When the key is omitted keep the
        # previous state so admin "hide" survives unrelated metadata updates.
        if row.get('shop_hidden') is None:
            row['shop_hidden'] = int((existing or {}).get('shop_hidden') or 0)
        else:
            row['shop_hidden'] = int(bool(row['shop_hidden']))
        for key in (
            'locked_until_date', 'auction_start_date', 'auction_end_date',
            'first_sale_date', 'last_sale_date', 'drop_at',
        ):
            row[key] = normalize_unix_time(row.get(key))
        if row['drop_at'] is not None and row['drop_at'] <= 0:
            row['drop_at'] = None
        cols = (
            'gift_id', 'stars', 'convert_stars', 'upgrade_stars', 'limited',
            'sold_out', 'birthday', 'require_premium', 'limited_per_user',
            'availability_total', 'availability_remains', 'availability_resale',
            'title', 'document_id', 'sort_order',
            'released_by_peer_type', 'released_by_peer_id',
            'per_user_total', 'per_user_remains', 'locked_until_date',
            'is_auction', 'auction_slug', 'gifts_per_round', 'auction_start_date',
            'auction_total_rounds', 'auction_round_duration', 'auction_min_bid',
            'auction_end_date',
            'first_sale_date', 'last_sale_date', 'resell_min_stars',
            'peer_color_available', 'upgrade_variants', 'is_tester_gift',
            'shop_hidden', 'drop_at',
        )
        vals = [row.get(c) for c in cols]
        placeholders = ','.join('?' * len(cols))
        updates = ','.join(f'{c}=excluded.{c}' for c in cols if c != 'gift_id')
        self._conn.execute(
            f'''INSERT INTO star_gifts ({','.join(cols)})
                VALUES ({placeholders})
                ON CONFLICT(gift_id) DO UPDATE SET {updates}''',
            vals)
        self._conn.commit()
        return self.get_star_gift(gift['gift_id'])

    def get_star_gift(self, gift_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM star_gifts WHERE gift_id=?', (gift_id,)
        ).fetchone()
        return dict(row) if row else None

    def _next_star_gift_sort_order(self) -> int:
        row = self._conn.execute(
            'SELECT COALESCE(MAX(sort_order), 0) AS v FROM star_gifts'
        ).fetchone()
        return int(row['v'] if row and row['v'] is not None else 0) + 1

    def list_star_gifts(self) -> List[dict]:
        rows = self._conn.execute(
            'SELECT * FROM star_gifts ORDER BY sort_order, gift_id'
        ).fetchall()
        return [dict(r) for r in rows]

    def list_shop_star_gifts(self, viewer_id: Optional[int] = None) -> List[dict]:
        """Gifts visible in Send a Gift / payments.getStarGifts catalog."""
        from config import STAR_GIFT_TESTER_USER_IDS

        if config.GIFTS_HIDDEN:
            # Global hide: rows stay in the DB (definitions + inventories), the
            # shop and the gift picker just render an empty catalog.
            return []

        rows = self._conn.execute(
            '''SELECT * FROM star_gifts
               WHERE COALESCE(shop_hidden, 0) = 0
               ORDER BY sort_order, gift_id''',
        ).fetchall()
        is_tester = (
            viewer_id is not None
            and int(viewer_id) in STAR_GIFT_TESTER_USER_IDS
        )
        out: List[dict] = []
        for row in rows:
            gift = dict(row)
            if int(gift.get('is_tester_gift') or 0) and not is_tester:
                continue
            doc_id = int(gift.get('document_id') or 0)
            if doc_id <= 0:
                continue
            if not self.get_sticker_document_by_id(doc_id):
                continue
            out.append(gift)
        return out

    def refresh_star_gift_drop_hash_if_due(self) -> bool:
        """Recompute the shop hash when scheduled gifts become unlocked."""
        now = int(time.time())
        due = tuple(int(row['gift_id']) for row in self._conn.execute(
            '''SELECT gift_id FROM star_gifts
               WHERE drop_at IS NOT NULL AND drop_at <= ?
                 AND COALESCE(shop_hidden, 0) = 0
               ORDER BY gift_id''', (now,)).fetchall())
        if due == self._last_due_star_gift_drops:
            return False
        self._last_due_star_gift_drops = due
        self.recompute_star_gifts_catalog_hash()
        return True

    def get_star_gift_value(self, gift_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM star_gift_value_cache WHERE gift_id=?',
            (int(gift_id),),
        ).fetchone()
        if row:
            return dict(row)

        # Fresh gifts often have no resale listings yet, so Telegram has no
        # market value to return. Still expose a clearly marked estimate based
        # on the primary Stars price and the configured Stars/GRAM exchange.
        gift = self.get_star_gift(int(gift_id))
        if not gift:
            return None
        ton_nanos = config.stars_to_nanoton(int(gift.get('stars') or 0))
        usd_cents = max(0, int(round(
            ton_nanos / config.NANOTON_PER_GRAM
            * float(config.TON_USD_RATE) * 100)))
        return {
            'gift_id': int(gift_id),
            'currency': 'USD',
            'value': usd_cents,
            'value_usd_amount': usd_cents,
            'value_is_average': 1,
            'initial_sale_date': None,
            'initial_sale_stars': int(gift.get('stars') or 0),
            'initial_sale_price': None,
            'last_sale_date': None,
            'last_sale_price': None,
            'last_sale_on_fragment': 0,
            'floor_price': None,
            'average_price': None,
            'listed_count': None,
            'fragment_listed_count': None,
            'fragment_listed_url': None,
            'source_slug': None,
            'updated_at': 0,
        }

    def upsert_star_gift_value(self, row: dict) -> None:
        columns = (
            'gift_id', 'currency', 'value', 'value_usd_amount',
            'value_is_average',
            'initial_sale_date', 'initial_sale_stars', 'initial_sale_price',
            'last_sale_date', 'last_sale_price', 'last_sale_on_fragment',
            'floor_price', 'average_price', 'listed_count',
            'fragment_listed_count', 'fragment_listed_url', 'source_slug',
            'updated_at',
        )
        values = [row.get(column) for column in columns]
        self._conn.execute(
            f'''INSERT INTO star_gift_value_cache ({','.join(columns)})
                VALUES ({','.join('?' for _ in columns)})
                ON CONFLICT(gift_id) DO UPDATE SET
                {','.join(f'{column}=excluded.{column}' for column in columns
                          if column != 'gift_id')}''',
            values,
        )
        self._conn.commit()

    def upsert_currency_exchange_rates(self, rates: dict,
                                       updated_at: Optional[int] = None) -> None:
        now = int(updated_at or time.time())
        rows = [(str(code).upper(), float(rate), now)
                for code, rate in rates.items()
                if len(str(code)) == 3 and float(rate) > 0]
        rows.append(('USD', 1.0, now))
        self._conn.executemany(
            '''INSERT INTO currency_exchange_rates
               (currency, units_per_usd, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(currency) DO UPDATE SET
                 units_per_usd=excluded.units_per_usd,
                 updated_at=excluded.updated_at''', rows)
        self._conn.commit()

    def get_currency_exchange_rate(self, currency: str) -> Optional[float]:
        row = self._conn.execute(
            'SELECT units_per_usd FROM currency_exchange_rates WHERE currency=?',
            ((currency or '').upper(),),
        ).fetchone()
        return float(row['units_per_usd']) if row else None

    def hide_star_gift_from_shop(self, gift_id: int) -> bool:
        gift = self.get_star_gift(int(gift_id))
        if not gift:
            return False
        self._conn.execute(
            'UPDATE star_gifts SET shop_hidden=1 WHERE gift_id=?',
            (int(gift_id),),
        )
        self._conn.commit()
        self.recompute_star_gifts_catalog_hash()
        return True

    def restore_star_gift_to_shop(self, gift_id: int) -> bool:
        gift = self.get_star_gift(int(gift_id))
        if not gift:
            return False
        self._conn.execute(
            'UPDATE star_gifts SET shop_hidden=0 WHERE gift_id=?',
            (int(gift_id),),
        )
        self._conn.commit()
        self.recompute_star_gifts_catalog_hash()
        return True

    def count_star_gift_purchases(self, user_id: int, gift_id: int) -> int:
        row = self._conn.execute(
            '''SELECT COUNT(*) AS c FROM saved_star_gifts
               WHERE from_user_id=? AND gift_id=?''',
            (int(user_id), int(gift_id)),
        ).fetchone()
        return int(row['c'] or 0) if row else 0

    def get_star_gift_user_remains(self, user_id: Optional[int],
                                   gift: dict | int) -> Optional[int]:
        gift_row = gift if isinstance(gift, dict) else self.get_star_gift(int(gift))
        if user_id is None or not gift_row:
            return None
        if not int(gift_row.get('limited_per_user') or 0):
            return None
        total = int(gift_row.get('per_user_total') or 0)
        if total <= 0:
            return 0
        bought = self.count_star_gift_purchases(int(user_id), int(gift_row['gift_id']))
        return max(0, total - bought)

    def get_star_gift_send_error(self, user_id: Optional[int],
                                 gift: dict | int,
                                 recipient_id: Optional[int] = None,
                                 *,
                                 skip_usage_limited: bool = False
                                 ) -> Optional[str]:
        from config import STAR_GIFT_TESTER_USER_IDS

        gift_row = gift if isinstance(gift, dict) else self.get_star_gift(int(gift))
        if not gift_row:
            return 'STARGIFT_INVALID'
        if int(gift_row.get('is_auction') or 0):
            return 'STARGIFT_INVALID'
        drop_at = int(gift_row.get('drop_at') or 0)
        if drop_at and drop_at > int(time.time()):
            return 'STARGIFT_LOCKED_UNTIL_DATE'
        if int(gift_row.get('is_tester_gift') or 0):
            if (user_id is None
                    or int(user_id) not in STAR_GIFT_TESTER_USER_IDS):
                return 'STARGIFT_USAGE_LIMITED'
        if not skip_usage_limited:
            if (int(gift_row.get('limited') or 0)
                    and int(gift_row.get('availability_remains') or 0) <= 0):
                return 'STARGIFT_USAGE_LIMITED'
        remains = self.get_star_gift_user_remains(user_id, gift_row)
        if remains is not None and remains <= 0:
            return 'STARGIFT_USER_USAGE_LIMITED'
        if recipient_id:
            gp = self.get_global_privacy(int(recipient_id))
            dg = gp.get('disallowed_gifts') or {}
            is_limited = bool(int(gift_row.get('limited') or 0))
            if dg.get('disallow_unlimited_stargifts') and not is_limited:
                return 'STARGIFT_NOT_ALLOWED'
            if dg.get('disallow_limited_stargifts') and is_limited:
                return 'STARGIFT_NOT_ALLOWED'
            if dg.get('disallow_premium_gifts') and int(
                    gift_row.get('require_premium') or 0):
                return 'STARGIFT_NOT_ALLOWED'
            if dg.get('disallow_unique_stargifts') and int(
                    gift_row.get('unique') or 0):
                return 'STARGIFT_NOT_ALLOWED'
        return None

    def upsert_star_gift_upgrade_attr(self, attr: dict, *,
                                      commit: bool = True) -> None:
        is_black = (
            attr.get('attr_type') == 'backdrop'
            and str(attr.get('name') or '').strip().lower() == 'black'
        )
        if is_black:
            # Black is a shared Telegram backdrop. Keep one global record with
            # the official palette and 2.5% weight, even when importing a
            # collection-specific copy of the catalog row.
            existing = self._conn.execute(
                '''SELECT id FROM star_gift_upgrade_attrs
                   WHERE attr_type='backdrop' AND lower(name)='black'
                   ORDER BY id LIMIT 1'''
            ).fetchone()
            values = (
                0, 'Black', 25, None, 0, 4672845, 0, 14277081,
                15658734, 0,
            )
            if existing:
                self._conn.execute(
                    '''UPDATE star_gift_upgrade_attrs
                       SET gift_id=?, name=?, rarity_permille=?, document_id=?,
                           backdrop_id=?, center_color=?, edge_color=?,
                           pattern_color=?, text_color=?, crafted=?
                       WHERE id=?''',
                    (*values, int(existing['id'])),
                )
            else:
                self._conn.execute(
                    '''INSERT INTO star_gift_upgrade_attrs
                       (gift_id, name, rarity_permille, document_id,
                        backdrop_id, center_color, edge_color, pattern_color,
                        text_color, crafted, attr_type)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'backdrop')''',
                    values,
                )
            if commit:
                self._conn.commit()
            return
        self._conn.execute(
            '''INSERT INTO star_gift_upgrade_attrs
               (gift_id, attr_type, name, rarity_permille, document_id,
                backdrop_id, center_color, edge_color, pattern_color, text_color,
                crafted)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (int(attr.get('gift_id') or 0), attr['attr_type'], attr['name'],
             int(attr.get('rarity_permille') or 100),
             attr.get('document_id'), attr.get('backdrop_id'),
             attr.get('center_color'), attr.get('edge_color'),
             attr.get('pattern_color'), attr.get('text_color'),
             int(attr.get('crafted') or 0)))
        if commit:
            self._conn.commit()

    def _upgrade_attr_match_key(self, attr: dict) -> tuple:
        atype = attr.get('attr_type') or ''
        name = str(attr.get('name') or '')
        if atype in ('model', 'pattern'):
            return (atype, int(attr.get('document_id') or 0), name)
        if atype == 'backdrop':
            return (atype, int(attr.get('backdrop_id') or 0), name)
        return (atype, name)

    def _remap_saved_gift_attr_ids(self, gift_id: int,
                                   old_attrs: List[dict]) -> None:
        gid = int(gift_id)
        old_key_by_id = {
            int(a['id']): self._upgrade_attr_match_key(a) for a in old_attrs
        }
        new_attrs = self.list_star_gift_upgrade_attrs(gid)
        new_id_by_key: dict[tuple, int] = {}
        for attr in new_attrs:
            key = self._upgrade_attr_match_key(attr)
            new_id_by_key.setdefault(key, int(attr['id']))
        for col in ('model_attr_id', 'pattern_attr_id', 'backdrop_attr_id'):
            rows = self._conn.execute(
                f'''SELECT saved_id, {col} AS attr_id FROM saved_star_gifts
                    WHERE gift_id=? AND upgraded=1 AND {col} IS NOT NULL''',
                (gid,),
            ).fetchall()
            for row in rows:
                old_id = int(row['attr_id'])
                new_id = new_id_by_key.get(old_key_by_id.get(old_id))
                if new_id and new_id != old_id:
                    self._conn.execute(
                        f'UPDATE saved_star_gifts SET {col}=? WHERE saved_id=?',
                        (new_id, int(row['saved_id'])))

    def _deterministic_pick_upgrade_attrs(self, gift_id: int, seed: int) -> dict:
        rng = random.Random(int(seed))
        attrs = self.list_star_gift_upgrade_attrs(gift_id)
        out: dict[str, dict] = {}
        for atype in ('model', 'pattern', 'backdrop'):
            pool = [a for a in attrs if a['attr_type'] == atype
                    and (int(a.get('gift_id') or 0) in (gift_id, 0))]
            if atype == 'model':
                pool = [a for a in pool if not int(a.get('crafted') or 0)]
            if not pool:
                continue
            total = sum(int(a.get('rarity_permille') or 100) for a in pool)
            r = rng.randint(0, max(total - 1, 0))
            acc = 0
            pick = pool[-1]
            for attr in pool:
                acc += int(attr.get('rarity_permille') or 100)
                if r < acc:
                    pick = attr
                    break
            out[atype] = pick
        return out

    def repair_orphaned_saved_gift_attrs(self, gift_id: int) -> int:
        """Re-assign attrs for upgraded gifts whose attr ids were invalidated."""
        gid = int(gift_id)
        rows = self._conn.execute(
            '''SELECT * FROM saved_star_gifts
               WHERE gift_id=? AND upgraded=1''',
            (gid,),
        ).fetchall()
        fixed = 0
        for row in rows:
            saved = dict(row)
            broken = False
            for key in ('model_attr_id', 'pattern_attr_id', 'backdrop_attr_id'):
                aid = saved.get(key)
                if aid and not self.get_star_gift_upgrade_attr_by_id(int(aid)):
                    broken = True
                    break
            if not broken:
                continue
            seed = int(saved.get('unique_id') or saved.get('saved_id') or 0)
            picked = self._deterministic_pick_upgrade_attrs(gid, seed)
            if 'model' not in picked or 'pattern' not in picked:
                continue
            craft_chance = self._craft_chance_from_model_attr(
                int(picked['model']['id']))
            self._conn.execute(
                '''UPDATE saved_star_gifts SET
                   model_attr_id=?, pattern_attr_id=?, backdrop_attr_id=?,
                   craft_chance_permille=?
                   WHERE saved_id=?''',
                (
                    int(picked['model']['id']),
                    int(picked['pattern']['id']),
                    int((picked.get('backdrop') or {}).get('id') or 0) or None,
                    craft_chance,
                    int(saved['saved_id']),
                ),
            )
            fixed += 1
        if fixed:
            self._conn.commit()
        self.sync_saved_gift_craft_chances(gid)
        return fixed

    def merge_craft_upgrade_attrs(self, gift_id: int,
                                  attrs: List[dict]) -> int:
        """Add or update crafted models without touching the regular upgrade pool."""
        gid = int(gift_id)
        added = 0
        updated = 0
        for attr in attrs:
            if attr.get('attr_type') != 'model' or not int(attr.get('crafted') or 0):
                continue
            doc_id = int(attr.get('document_id') or 0)
            if not doc_id:
                continue
            row = dict(attr)
            row['gift_id'] = gid
            existing = self._conn.execute(
                '''SELECT id FROM star_gift_upgrade_attrs
                   WHERE gift_id=? AND attr_type='model'
                     AND document_id=? AND crafted=1''',
                (gid, doc_id),
            ).fetchone()
            if existing:
                self.update_star_gift_upgrade_attr(
                    int(existing['id']),
                    name=row.get('name'),
                    rarity_permille=row.get('rarity_permille'),
                    document_id=doc_id,
                    crafted=True,
                )
                updated += 1
            else:
                self.upsert_star_gift_upgrade_attr(row, commit=False)
                added += 1
        if added or updated:
            self._conn.commit()
        return added + updated

    def sync_craft_model_rarities(self, gift_id: int, attrs: List[dict]) -> int:
        """Update rarity_permille for existing crafted models by document_id."""
        gid = int(gift_id)
        updated = 0
        for attr in attrs:
            if attr.get('attr_type') != 'model' or not int(attr.get('crafted') or 0):
                continue
            doc_id = int(attr.get('document_id') or 0)
            if not doc_id:
                continue
            rarity = int(attr.get('rarity_permille') or 100)
            cur = self._conn.execute(
                '''UPDATE star_gift_upgrade_attrs SET rarity_permille=?
                   WHERE gift_id=? AND attr_type='model'
                     AND document_id=? AND crafted=1
                     AND rarity_permille IS NOT ?''',
                (rarity, gid, doc_id, rarity),
            )
            updated += int(cur.rowcount)
        if updated:
            self._conn.commit()
        return updated

    def resolve_saved_star_gift_ref(self, owner_id: int,
                                    ref: dict) -> Optional[dict]:
        kind = ref.get('kind')
        if kind == 'slug':
            row = self.get_saved_star_gift_by_slug(ref['slug'])
            if row and int(row['owner_user_id']) != int(owner_id):
                return None
            return row
        if kind == 'user':
            return self.get_saved_star_gift_by_msg(owner_id, int(ref['msg_id']))
        if kind == 'chat':
            row = self.get_saved_star_gift_by_id(int(ref['saved_id']))
            if not row:
                return None
            if int(row['owner_user_id']) != int(owner_id):
                return None
            peer_id = ref.get('peer_id')
            if peer_id is not None and int(peer_id) != int(row['owner_user_id']):
                return None
            return row
        return None

    def replace_star_gift_upgrade_attrs(self, gift_id: int,
                                        attrs: List[dict]) -> int:
        """Atomically replace upgrade pool for a gift (one transaction)."""
        gid = int(gift_id)
        old_attrs = self.list_star_gift_upgrade_attrs(gid)
        self.clear_star_gift_upgrade_attrs(gid, commit=False)
        for attr in attrs:
            row = dict(attr)
            row['gift_id'] = gid
            self.upsert_star_gift_upgrade_attr(row, commit=False)
        self._remap_saved_gift_attr_ids(gid, old_attrs)
        self.repair_orphaned_saved_gift_attrs(gid)
        self._conn.commit()
        return len(attrs)

    def list_star_gift_upgrade_attrs(self, gift_id: int = 0) -> List[dict]:
        rows = self._conn.execute(
            '''SELECT * FROM star_gift_upgrade_attrs
               WHERE gift_id=? OR gift_id=0
               ORDER BY attr_type, rarity_permille DESC''',
            (gift_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_star_gift_upgrade_attr_by_id(self, attr_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM star_gift_upgrade_attrs WHERE id=?', (attr_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_star_gift_upgrade_attr(self, attr_id: int, **fields) -> Optional[dict]:
        allowed = {
            'name': 'name',
            'rarity_permille': 'rarity_permille',
            'document_id': 'document_id',
            'backdrop_id': 'backdrop_id',
            'center_color': 'center_color',
            'edge_color': 'edge_color',
            'pattern_color': 'pattern_color',
            'text_color': 'text_color',
            'crafted': 'crafted',
        }
        sets: list[str] = []
        params: list[Any] = []
        for key, col in allowed.items():
            if key not in fields or fields[key] is None:
                continue
            value = fields[key]
            if key == 'crafted':
                value = 1 if bool(value) else 0
            elif key in ('rarity_permille', 'document_id', 'backdrop_id',
                         'center_color', 'edge_color', 'pattern_color',
                         'text_color'):
                value = int(value)
            elif key == 'name':
                value = str(value or '').strip()
            sets.append(f'{col}=?')
            params.append(value)
        if not sets:
            return self.get_star_gift_upgrade_attr_by_id(attr_id)
        params.append(int(attr_id))
        self._conn.execute(
            f'UPDATE star_gift_upgrade_attrs SET {",".join(sets)} WHERE id=?',
            params,
        )
        self._conn.commit()
        return self.get_star_gift_upgrade_attr_by_id(attr_id)

    def update_saved_star_gift_attrs(
        self,
        saved_id: int,
        *,
        model_attr_id: int | None = None,
        pattern_attr_id: int | None = None,
        backdrop_attr_id: int | None = None,
    ) -> Optional[dict]:
        sets: list[str] = []
        params: list[Any] = []
        if model_attr_id is not None:
            sets.append('model_attr_id=?')
            params.append(int(model_attr_id) or None)
        if pattern_attr_id is not None:
            sets.append('pattern_attr_id=?')
            params.append(int(pattern_attr_id) or None)
        if backdrop_attr_id is not None:
            sets.append('backdrop_attr_id=?')
            params.append(int(backdrop_attr_id) or None)
        if not sets:
            return self.get_saved_star_gift_by_id(saved_id)
        params.append(int(saved_id))
        self._conn.execute(
            f'UPDATE saved_star_gifts SET {",".join(sets)} WHERE saved_id=?',
            params,
        )
        self._conn.commit()
        return self.get_saved_star_gift_by_id(saved_id)

    def count_star_gift_upgrade_attrs(self, gift_id: int = 0) -> int:
        row = self._conn.execute(
            '''SELECT COUNT(*) AS c FROM star_gift_upgrade_attrs
               WHERE gift_id=? OR gift_id=0''',
            (gift_id,)
        ).fetchone()
        return int(row['c'])

    def get_stars_balance(self, user_id: int) -> int:
        if config.INFINITE_STARS:
            return config.STARS_BALANCE
        row = self._conn.execute(
            'SELECT balance FROM stars_balances WHERE user_id=?', (user_id,)
        ).fetchone()
        if row:
            return int(row['balance'])
        opening_balance = int(config.DEFAULT_STARS_BALANCE)
        self._conn.execute(
            '''INSERT INTO stars_balances (user_id, balance, stars_peak)
               VALUES (?, ?, ?)''',
            (user_id, opening_balance, opening_balance))
        self._conn.commit()
        return opening_balance

    def sum_active_star_gift_offer_holds(self, buyer_id: int) -> int:
        now = int(time.time())
        row = self._conn.execute(
            '''SELECT COALESCE(SUM(price_stars), 0) AS total
               FROM star_gift_offers
               WHERE buyer_id=? AND status='pending' AND expires_at>?''',
            (buyer_id, now)).fetchone()
        return int(row['total'] or 0)

    def get_client_stars_balance(self, user_id: int) -> int:
        """Balance value safe for Telegram Android offer UI / AmountUtils."""
        if config.INFINITE_STARS:
            holds = self.sum_active_star_gift_offer_holds(user_id)
            return max(0, config.STARS_BALANCE - holds)
        row = self._conn.execute(
            'SELECT balance, stars_peak FROM stars_balances WHERE user_id=?',
            (user_id,)).fetchone()
        if not row:
            balance = self.get_stars_balance(user_id)
            return config.client_stars_balance(balance, peak=balance)
        balance = int(row['balance'])
        peak = max(int(row['stars_peak'] or 0), balance)
        return config.client_stars_balance(balance, peak=peak)

    def set_stars_balance(self, user_id: int, balance: int) -> None:
        if config.INFINITE_STARS:
            balance = config.STARS_BALANCE
        balance = int(balance)
        self._conn.execute(
            '''INSERT INTO stars_balances (user_id, balance, stars_peak)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 balance=excluded.balance,
                 stars_peak=MAX(stars_balances.stars_peak, excluded.balance)''',
            (user_id, balance, balance))
        self._conn.commit()

    def _ensure_ton_tables(self) -> None:
        if getattr(self, '_ton_tables_ready', False):
            return
        self._conn.execute('''
            CREATE TABLE IF NOT EXISTS ton_balances (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0
            )
        ''')
        self._conn.execute('''
            CREATE TABLE IF NOT EXISTS ton_transactions (
                tx_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                date INTEGER NOT NULL,
                title TEXT,
                peer_user_id INTEGER,
                gift INTEGER NOT NULL DEFAULT 0,
                stargift_resale INTEGER NOT NULL DEFAULT 0,
                offer INTEGER NOT NULL DEFAULT 0
            )
        ''')
        self._conn.commit()
        self._ton_tables_ready = True

    def get_ton_balance(self, user_id: int) -> int:
        self._ensure_ton_tables()
        row = self._conn.execute(
            'SELECT balance FROM ton_balances WHERE user_id=?', (int(user_id),),
        ).fetchone()
        return int(row['balance']) if row else 0

    def set_ton_balance(self, user_id: int, balance: int) -> None:
        balance = max(0, int(balance))
        self._conn.execute(
            '''INSERT INTO ton_balances (user_id, balance) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET balance=excluded.balance''',
            (int(user_id), balance),
        )
        self._conn.commit()

    def record_ton_transaction(self, user_id: int, amount: int, *,
                               peer_user_id: int = 0,
                               gift: bool = False,
                               stargift_resale: bool = False,
                               offer: bool = False,
                               title: str = '') -> str:
        tx_id = self._new_stars_tx_id()
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO ton_transactions
               (tx_id, user_id, amount, date, title, peer_user_id,
                gift, stargift_resale, offer)
               VALUES (?,?,?,?,?,?,?,?,?)''',
            (tx_id, int(user_id), int(amount), now,
             title or None, peer_user_id or None,
             1 if gift else 0, 1 if stargift_resale else 0, 1 if offer else 0),
        )
        self._conn.commit()
        return tx_id

    def list_ton_transactions(self, user_id: int, *,
                              offset: str = '',
                              limit: int = 20,
                              inbound: bool = False,
                              outbound: bool = False) -> tuple:
        self._ensure_ton_tables()
        limit = max(1, min(int(limit or 20), 100))
        clauses = ['user_id=?']
        params: list = [int(user_id)]
        if inbound:
            clauses.append('amount > 0')
        elif outbound:
            clauses.append('amount < 0')
        if offset:
            clauses.append('tx_id < ?')
            params.append(offset)
        where = ' AND '.join(clauses)
        rows = self._conn.execute(
            f'''SELECT * FROM ton_transactions WHERE {where}
                ORDER BY date DESC, tx_id DESC LIMIT ?''',
            (*params, limit + 1)).fetchall()
        next_off = ''
        if len(rows) > limit:
            next_off = rows[limit - 1]['tx_id']
            rows = rows[:limit]
        return [dict(r) for r in rows], next_off

    def adjust_ton_balance(self, user_id: int, delta: int, **tx_kw) -> int:
        delta = int(delta)
        balance = self.get_ton_balance(user_id)
        new_balance = max(0, balance + delta)
        self.set_ton_balance(user_id, new_balance)
        if delta:
            record_kw = {
                k: tx_kw[k] for k in (
                    'peer_user_id', 'gift', 'stargift_resale', 'offer', 'title',
                ) if k in tx_kw
            }
            self.record_ton_transaction(user_id, delta, **record_kw)
            try:
                from handlers.packers import pack_user_ton_balance
                self.enqueue_pending_update(
                    user_id, pack_user_ton_balance(self, user_id))
            except Exception:
                pass
        return new_balance

    def count_saved_star_gifts(self, owner_user_id: int, saved_only: bool = True) -> int:
        q = 'SELECT COUNT(*) AS c FROM saved_star_gifts WHERE owner_user_id=? AND burned=0'
        if saved_only:
            q += ' AND unsaved=0'
        row = self._conn.execute(q, (owner_user_id,)).fetchone()
        return int(row['c'])

    def list_saved_star_gifts(self, owner_user_id: int, offset: int = 0,
                              limit: int = 100, *,
                              exclude_unsaved: bool = False,
                              exclude_saved: bool = False,
                              exclude_unlimited: bool = False,
                              exclude_unique: bool = False,
                              sort_by_value: bool = False,
                              exclude_upgradable: bool = False,
                              exclude_unupgradable: bool = False,
                              peer_color_available: bool = False,
                              collection_id: Optional[int] = None,
                              exclude_in_collections: bool = False) -> tuple:
        join = ''
        if exclude_unlimited or peer_color_available:
            join = ' JOIN star_gifts g ON g.gift_id=s.gift_id'
        clauses = ['s.owner_user_id=?', 's.burned=0']
        params: list = [owner_user_id]
        if collection_id is not None and int(collection_id) > 0:
            join += (' JOIN star_gift_collection_items ci '
                     'ON ci.saved_id=s.saved_id')
            clauses.append('ci.collection_id=?')
            params.append(int(collection_id))
        elif exclude_in_collections:
            clauses.append(
                '''NOT EXISTS (
                     SELECT 1 FROM star_gift_collection_items ci
                     WHERE ci.saved_id=s.saved_id
                   )''')
        if exclude_unsaved:
            clauses.append('s.unsaved=0')
        if exclude_saved:
            clauses.append('s.unsaved=1')
        if exclude_unique:
            clauses.append('s.upgraded=0')
        if exclude_upgradable:
            clauses.append('(s.upgraded=1 OR s.can_upgrade=0)')
        if exclude_unupgradable:
            # Telegram's exclude_unupgradable flag removes ordinary gifts
            # that cannot be upgraded. Keep already upgraded collectibles and
            # gifts that still have an upgrade path. In particular, clients
            # request both exclude_upgradable and exclude_unupgradable to get
            # the owner's transferable unique gifts; the old predicate here
            # selected only unupgraded, upgradeable gifts and emptied "My
            # Gifts" when combined with exclude_upgradable.
            clauses.append('(s.upgraded=1 OR s.can_upgrade=1)')
        if exclude_unlimited:
            if exclude_upgradable and exclude_unupgradable:
                # Gift pickers use this three-flag combination to request
                # transferable unique gifts. Their original gift definition
                # may now have unlimited stock, but the upgraded collectible
                # itself remains transferable and belongs in "My Gifts".
                clauses.append('(g.limited=1 OR s.upgraded=1)')
            else:
                clauses.append('g.limited=1')
        if peer_color_available:
            clauses.append('g.peer_color_available=1')
        where = ' AND '.join(clauses)
        order = ('COALESCE(s.resell_stars, s.stars, 0) DESC, '
                 if sort_by_value else '')
        order += 's.pinned_to_top DESC, s.date DESC'
        total = self._conn.execute(
            f'SELECT COUNT(*) AS c FROM saved_star_gifts s{join} WHERE {where}',
            params).fetchone()['c']
        rows = self._conn.execute(
            f'''SELECT s.* FROM saved_star_gifts s{join}
                WHERE {where}
                ORDER BY {order}
                LIMIT ? OFFSET ?''',
            params + [limit, offset]).fetchall()
        return [dict(r) for r in rows], int(total)

    def get_saved_gift_collection_ids(self, saved_id: int) -> List[int]:
        rows = self._conn.execute(
            '''SELECT collection_id FROM star_gift_collection_items
               WHERE saved_id=? ORDER BY collection_id''',
            (int(saved_id),)).fetchall()
        return [int(r['collection_id']) for r in rows]

    def get_saved_star_gift_by_id(self, saved_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM saved_star_gifts WHERE saved_id=?', (saved_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_saved_star_gift_by_unique_id(self, owner_user_id: int,
                                         unique_id: int) -> Optional[dict]:
        row = self._conn.execute(
            '''SELECT * FROM saved_star_gifts
               WHERE owner_user_id=? AND unique_id=? AND upgraded=1''',
            (owner_user_id, unique_id),
        ).fetchone()
        return dict(row) if row else None

    def get_saved_star_gift_by_unique_id_global(self,
                                                unique_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM saved_star_gifts WHERE unique_id=? AND upgraded=1',
            (unique_id,),
        ).fetchone()
        return dict(row) if row else None

    def count_resale_gifts(self, gift_id: int) -> int:
        row = self._conn.execute(
            '''SELECT COUNT(*) AS c FROM saved_star_gifts
               WHERE gift_id=? AND upgraded=1 AND unsaved=0
                 AND (COALESCE(resell_stars, 0) > 0
                      OR (resell_ton=1 AND resell_stars_nanos > 0))''',
            (gift_id,),
        ).fetchone()
        return int(row['c'])

    def min_resell_price(self, gift_id: int) -> Optional[int]:
        row = self._conn.execute(
            '''SELECT MIN(resell_stars) AS m FROM saved_star_gifts
               WHERE gift_id=? AND upgraded=1 AND unsaved=0
                 AND COALESCE(resell_stars, 0) > 0''',
            (gift_id,),
        ).fetchone()
        val = row['m']
        return int(val) if val is not None else None

    def compute_resale_attributes_hash(self, gift_id: int) -> int:
        attrs = self.list_star_gift_upgrade_attrs(gift_id)
        h = 0x52455341  # 'RESA'
        for a in sorted(attrs, key=lambda x: (x['attr_type'], int(x['id']))):
            h = (h * 31 + int(a['id'])) & 0xFFFFFFFFFFFFFFFF
            name = a.get('name') or ''
            name_sum = sum(ord(c) for c in name)
            h = (h * 31 + name_sum) & 0xFFFFFFFFFFFFFFFF
        if h >= 2**63:
            h -= 2**64
        return h

    def compute_resale_attribute_counters(self, gift_id: int) -> List[dict]:
        """Count on-sale gifts per model/pattern/backdrop attribute."""
        base = (
            'FROM saved_star_gifts s '
            'JOIN star_gift_upgrade_attrs a ON a.id = {join_col} '
            'WHERE s.gift_id=? AND s.upgraded=1 AND s.unsaved=0 '
            'AND (COALESCE(s.resell_stars, 0) > 0 '
            'OR (s.resell_ton=1 AND s.resell_stars_nanos > 0))'
        )
        out: List[dict] = []
        for join_col, kind, id_field in (
            ('s.model_attr_id', 'model', 'document_id'),
            ('s.pattern_attr_id', 'pattern', 'document_id'),
            ('s.backdrop_attr_id', 'backdrop', 'backdrop_id'),
        ):
            rows = self._conn.execute(
                f'''SELECT a.{id_field} AS attr_key, COUNT(*) AS c
                    {base.format(join_col=join_col)}
                    GROUP BY a.{id_field}''',
                (gift_id,),
            ).fetchall()
            for row in rows:
                key = row['attr_key']
                if key is None:
                    continue
                out.append({
                    'kind': kind,
                    id_field: int(key),
                    'count': int(row['c']),
                })
        return out

    def _resale_listing_sql(self, gift_id: int, *,
                            stars_only: bool = False,
                            for_craft: bool = False,
                            attribute_filters: Optional[dict] = None
                            ) -> tuple[str, list]:
        clauses = [
            'gift_id=?',
            'upgraded=1',
            'unsaved=0',
            'burned=0',
            '(COALESCE(resell_stars, 0) > 0 '
            'OR (resell_ton=1 AND resell_stars_nanos > 0))',
        ]
        params: list = [gift_id]
        if for_craft:
            now = int(time.time())
            clauses.append('crafted=0')
            clauses.append('(can_craft_at=0 OR can_craft_at<=?)')
            params.append(now)
        if stars_only:
            clauses.append('COALESCE(resell_stars, 0) > 0')
        filters = attribute_filters or {}
        models = filters.get('models') or []
        patterns = filters.get('patterns') or []
        backdrops = filters.get('backdrops') or []
        if models:
            ph = ','.join('?' * len(models))
            clauses.append(
                f'model_attr_id IN (SELECT id FROM star_gift_upgrade_attrs '
                f'WHERE (gift_id=? OR gift_id=0) AND attr_type=? '
                f'AND document_id IN ({ph}))')
            params.extend([gift_id, 'model', *models])
        if patterns:
            ph = ','.join('?' * len(patterns))
            clauses.append(
                f'pattern_attr_id IN (SELECT id FROM star_gift_upgrade_attrs '
                f'WHERE (gift_id=? OR gift_id=0) AND attr_type=? '
                f'AND document_id IN ({ph}))')
            params.extend([gift_id, 'pattern', *patterns])
        if backdrops:
            ph = ','.join('?' * len(backdrops))
            clauses.append(
                f'backdrop_attr_id IN (SELECT id FROM star_gift_upgrade_attrs '
                f'WHERE (gift_id=? OR gift_id=0) AND attr_type=? '
                f'AND backdrop_id IN ({ph}))')
            params.extend([gift_id, 'backdrop', *backdrops])
        return ' AND '.join(clauses), params

    def list_resale_gifts(self, gift_id: int, offset: str = '',
                          limit: int = 50, *,
                          sort_by_price: bool = False,
                          sort_by_num: bool = False,
                          stars_only: bool = False,
                          for_craft: bool = False,
                          attribute_filters: Optional[dict] = None) -> tuple:
        where, params = self._resale_listing_sql(
            gift_id, stars_only=stars_only,
            for_craft=for_craft,
            attribute_filters=attribute_filters)

        total = self._conn.execute(
            f'SELECT COUNT(*) AS c FROM saved_star_gifts WHERE {where}',
            params,
        ).fetchone()['c']

        if offset:
            try:
                off_id = int(offset)
            except ValueError:
                off_id = 0
            if sort_by_price:
                where += (
                    ' AND (resell_stars > (SELECT resell_stars FROM saved_star_gifts '
                    'WHERE saved_id=?) OR (resell_stars = (SELECT resell_stars '
                    'FROM saved_star_gifts WHERE saved_id=?) AND saved_id > ?))')
                params.extend([off_id, off_id, off_id])
            elif sort_by_num:
                where += (
                    ' AND (unique_num > (SELECT unique_num FROM saved_star_gifts '
                    'WHERE saved_id=?) OR (unique_num = (SELECT unique_num '
                    'FROM saved_star_gifts WHERE saved_id=?) AND saved_id > ?))')
                params.extend([off_id, off_id, off_id])
            else:
                where += ' AND saved_id < ?'
                params.append(off_id)

        if sort_by_price:
            order = 'resell_stars ASC, saved_id ASC'
        elif sort_by_num:
            order = 'unique_num ASC, saved_id ASC'
        else:
            order = 'date DESC, saved_id DESC'

        rows = self._conn.execute(
            f'SELECT * FROM saved_star_gifts WHERE {where} '
            f'ORDER BY {order} LIMIT ?',
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows], int(total)

    def get_saved_star_gift_by_slug(self, slug: str) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM saved_star_gifts WHERE unique_slug=? AND upgraded=1',
            (slug,),
        ).fetchone()
        return dict(row) if row else None

    def find_saved_star_gift_by_slug(self, slug: str) -> Optional[dict]:
        """Lookup tolerant to CamelCase links (ResistanceDog-65 -> resistance-dog-65)."""
        slug = (slug or '').strip()
        if not slug:
            return None
        for candidate in (slug, slug.lower()):
            row = self.get_saved_star_gift_by_slug(candidate)
            if row:
                return row
        squashed = re.sub(r'[^a-z0-9]', '', slug.lower())
        if not squashed:
            return None
        rows = self._conn.execute(
            '''SELECT * FROM saved_star_gifts
               WHERE upgraded=1 AND unique_slug IS NOT NULL'''
        ).fetchall()
        for row in rows:
            row_slug = re.sub(
                r'[^a-z0-9]', '', str(row['unique_slug'] or '').lower())
            if row_slug == squashed:
                return dict(row)
        return None

    def set_saved_star_gift_black_backdrop(self, saved_id: int) -> tuple[dict, dict]:
        """Assign the Black backdrop to a unique gift.

        Official Telegram palette (as imported from the real catalog):
        radial gradient #474D4D -> #000000 with a light motif (#D9D9D9).
        Rarity is the canonical 25 permille (2.5%).
        """
        saved = self.get_saved_star_gift_by_id(int(saved_id))
        if not saved:
            raise ValueError('STARGIFT_NOT_FOUND')
        if not int(saved.get('upgraded') or 0):
            raise ValueError('STARGIFT_NOT_UPGRADED')
        official = {
            'name': 'Black',
            'backdrop_id': 0,
            'rarity_permille': 25,
            'center_color': 4672845,   # #474D4D radial center
            'edge_color': 0,           # #000000
            'pattern_color': 14277081, # #D9D9D9 motif
            'text_color': 15658734,    # #EEEEEE
        }
        self.upsert_star_gift_upgrade_attr({
            'gift_id': 0,
            'attr_type': 'backdrop',
            **official,
        })
        row = self._conn.execute(
            '''SELECT * FROM star_gift_upgrade_attrs
               WHERE gift_id=0 AND attr_type='backdrop' AND lower(name)='black'
               ORDER BY id LIMIT 1'''
        ).fetchone()
        attr = dict(row) if row else None
        if not attr:
            raise ValueError('STARGIFT_INVALID')
        saved = self.update_saved_star_gift_attrs(
            int(saved_id), backdrop_attr_id=int(attr['id']))
        return saved, attr

    def resell_ton_nanos(self, saved: dict) -> int:
        """GRAM price in nanoton for a resale listing (TON or Stars)."""
        nanos = int(saved.get('resell_stars_nanos') or 0)
        if bool(int(saved.get('resell_ton') or 0)) and nanos > 0:
            return nanos
        stars = int(saved.get('resell_stars') or 0)
        if stars > 0:
            return config.stars_to_nanoton(stars) + nanos
        return nanos

    def purchase_resale_gift(self, buyer_id: int, slug: str,
                             recipient_id: int, *, ton: bool = False) -> dict:
        saved = self.get_saved_star_gift_by_slug(slug)
        if not saved:
            raise ValueError('STARGIFT_NOT_FOUND')
        if int(saved.get('burned') or 0):
            raise ValueError('STARGIFT_ALREADY_BURNED')
        has_stars = int(saved.get('resell_stars') or 0) > 0
        ton_price = self.resell_ton_nanos(saved)
        has_ton = ton_price > 0
        if ton and not has_ton:
            raise ValueError('STARGIFT_NOT_FOUND')
        if not ton and not has_stars:
            raise ValueError('STARGIFT_NOT_FOUND')
        if int(saved.get('unsaved') or 0):
            raise ValueError('STARGIFT_NOT_FOUND')
        if self.saved_gift_chain_owner(saved):
            raise ValueError('STARGIFT_ON_BLOCKCHAIN')

        seller_id = int(saved['owner_user_id'])
        price = int(saved['resell_stars']) if not ton else 0
        if buyer_id == seller_id:
            raise ValueError('STARGIFT_INVALID')

        recipient = self.get_user_by_id(recipient_id)
        if not recipient:
            raise ValueError('PEER_ID_INVALID')

        if not ton:
            balance = self.get_stars_balance(buyer_id)
            if not config.INFINITE_STARS and balance < price:
                raise ValueError('BALANCE_TOO_LOW')
        else:
            if not config.can_use_ton_payments(buyer_id):
                raise ValueError('TON_PAYMENT_NOT_ALLOWED')
            balance = self.get_ton_balance(buyer_id)
            if balance < ton_price:
                raise ValueError('BALANCE_TOO_LOW')

        try:
            if not self._conn.in_transaction:
                self._conn.execute('BEGIN IMMEDIATE')
            if not ton:
                self.adjust_stars_balance(
                    buyer_id, -price, peer_user_id=seller_id,
                    stargift_resale=True, title='Resale purchase')
                payout = (price * config.STARGIFT_RESALE_COMMISSION_PERMILLE
                          // 1000)
                if payout > 0:
                    self.adjust_stars_balance(
                        seller_id, payout, peer_user_id=buyer_id,
                        stargift_resale=True, title='Resale sale')
            else:
                self.adjust_ton_balance(
                    buyer_id, -ton_price, peer_user_id=seller_id,
                    stargift_resale=True, title='Resale purchase')
                payout_ton = (
                    ton_price
                    * config.STARGIFT_RESALE_COMMISSION_PERMILLE // 1000
                )
                if payout_ton > 0:
                    self.adjust_ton_balance(
                        seller_id, payout_ton, peer_user_id=buyer_id,
                        stargift_resale=True, title='Resale sale (GRAM)')

            if int(self._conn.execute(
                    'SELECT emoji_status_collectible_id FROM users WHERE id=?',
                    (seller_id,)).fetchone()['emoji_status_collectible_id']
                    or 0) == int(saved.get('unique_id') or 0):
                self.set_user_emoji_status(seller_id, None)

            now = int(time.time())
            new_msg_id = self._allocate_message_id()
            self._conn.execute(
                '''UPDATE saved_star_gifts SET owner_user_id=?, msg_id=?,
                   pinned_to_top=0, resell_stars=NULL, resell_stars_nanos=0,
                   resell_ton=0, date=?
                   WHERE saved_id=? AND (
                       COALESCE(resell_stars, 0) > 0
                       OR (resell_ton=1 AND resell_stars_nanos > 0))''',
                (recipient_id, new_msg_id, now, saved['saved_id']),
            )
            if self._conn.total_changes == 0:
                raise ValueError('STARGIFT_NOT_FOUND')

            action = {
                'type': 'star_gift_unique',
                'gift_id': int(saved['gift_id']),
                'unique_id': int(saved['unique_id']),
                'saved_id': int(new_msg_id),
                'peer_id': recipient_id,
                'from_id': seller_id,
                'upgrade': False,
                'saved': True,
                'resale_amount': price,
                'resale_nanos': int(saved.get('resell_stars_nanos') or 0) if not ton else 0,
                'resale_ton': ton_price if ton else 0,
            }
            pts_buyer = self._insert_message(
                new_msg_id, buyer_id, buyer_id, recipient_id, '', [], now,
                action_json=json.dumps(action, ensure_ascii=False))
            if buyer_id == recipient_id:
                pts_recipient = pts_buyer
            else:
                pts_recipient = self._insert_message(
                    new_msg_id, recipient_id, buyer_id, buyer_id, '', [], now,
                    action_json=json.dumps(action, ensure_ascii=False))
            self._upsert_dialog(buyer_id, recipient_id, new_msg_id, now, True, 0)
            if buyer_id != recipient_id:
                self._upsert_dialog(recipient_id, buyer_id, new_msg_id, now, False, 1)
            else:
                self._upsert_dialog(recipient_id, buyer_id, new_msg_id, now, False, 0)
            self._conn.commit()
        except ValueError:
            if self._conn.in_transaction:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            raise
        except Exception:
            if self._conn.in_transaction:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            raise
        self.recompute_star_gifts_catalog_hash()

        saved = self.get_saved_star_gift_by_id(int(saved['saved_id']))
        return {
            'saved': saved,
            'buyer_msg': self.get_message(buyer_id, new_msg_id),
            'recipient_msg': self.get_message(recipient_id, new_msg_id),
            'pts_buyer': pts_buyer,
            'pts_recipient': pts_recipient,
            'seller_id': seller_id,
            'price': price,
        }

    def get_star_gift_offer(self, owner_id: int,
                            offer_msg_id: int) -> Optional[dict]:
        row = self._conn.execute(
            '''SELECT * FROM star_gift_offers
               WHERE owner_id=? AND offer_msg_id=? AND status='pending' ''',
            (owner_id, offer_msg_id),
        ).fetchone()
        return dict(row) if row else None

    def has_active_star_gift_offer_hold(self, buyer_id: int, owner_id: int,
                                        price_stars: int) -> bool:
        now = int(time.time())
        row = self._conn.execute(
            '''SELECT 1 FROM star_gift_offers
               WHERE buyer_id=? AND owner_id=? AND price_stars=?
                 AND status='pending' AND expires_at>? LIMIT 1''',
            (buyer_id, owner_id, int(price_stars), now),
        ).fetchone()
        return row is not None

    def send_star_gift_offer(self, buyer_id: int, owner_id: int, slug: str,
                             *, price_stars: int = 0, price_nanos: int = 0,
                             price_ton: int = 0, duration: int,
                             random_id: int) -> dict:
        if buyer_id == owner_id:
            raise ValueError('PEER_ID_INVALID')
        if duration not in config.STARGIFT_OFFER_DURATIONS:
            raise ValueError('DURATION_INVALID')

        dup = self._conn.execute(
            'SELECT 1 FROM star_gift_offers WHERE random_id=?',
            (random_id,)).fetchone()
        if dup:
            raise ValueError('OFFER_ALREADY_SENT')

        saved = self.get_saved_star_gift_by_slug(slug)
        if not saved or not int(saved.get('upgraded') or 0):
            raise ValueError('STARGIFT_NOT_FOUND')
        if int(saved.get('burned') or 0):
            raise ValueError('STARGIFT_ALREADY_BURNED')
        if int(saved.get('unsaved') or 0):
            raise ValueError('STARGIFT_NOT_FOUND')
        if int(saved['owner_user_id']) != owner_id:
            raise ValueError('STARGIFT_OWNER_INVALID')
        if self.saved_gift_chain_owner(saved):
            raise ValueError('STARGIFT_ON_BLOCKCHAIN')

        owner = self.get_user_by_id(owner_id)
        if not owner:
            raise ValueError('PEER_ID_INVALID')

        min_stars = config.STARGIFT_OFFER_MIN_STARS
        if price_ton > 0:
            if price_ton < config.STARGIFT_RESALE_TON_MIN:
                raise ValueError('PRICE_TOO_LOW')
            if price_ton > config.STARGIFT_RESALE_TON_MAX:
                raise ValueError('PRICE_TOO_HIGH')
            balance = self.get_ton_balance(buyer_id)
            if balance < price_ton:
                raise ValueError('BALANCE_TOO_LOW')
            self.adjust_ton_balance(
                buyer_id, -price_ton, peer_user_id=owner_id,
                offer=True, title='Gift offer')
        else:
            total_stars = int(price_stars)
            if total_stars < min_stars:
                raise ValueError('PRICE_TOO_LOW')
            if total_stars > config.STARGIFT_RESALE_STARS_MAX:
                raise ValueError('PRICE_TOO_HIGH')
            if not config.INFINITE_STARS:
                balance = self.get_stars_balance(buyer_id)
                if balance < total_stars:
                    raise ValueError('BALANCE_TOO_LOW')
            self.adjust_stars_balance(
                buyer_id, -total_stars, peer_user_id=owner_id,
                offer=True, title='Gift offer')

        now = int(time.time())
        expires_at = now + int(duration)
        msg_id = self._allocate_message_id()
        action = {
            'type': 'star_gift_purchase_offer',
            'slug': slug,
            'saved_id': int(saved['saved_id']),
            'price_stars': int(price_stars),
            'price_nanos': int(price_nanos),
            'price_ton': int(price_ton),
            'expires_at': expires_at,
        }
        pts_buyer = self._insert_message(
            msg_id, buyer_id, buyer_id, owner_id, '', [], now,
            action_json=json.dumps(action, ensure_ascii=False))
        pts_owner = self._insert_message(
            msg_id, owner_id, buyer_id, buyer_id, '', [], now,
            action_json=json.dumps(action, ensure_ascii=False))
        self._upsert_dialog(buyer_id, owner_id, msg_id, now, True, 0)
        self._upsert_dialog(owner_id, buyer_id, msg_id, now, False, 1)

        self._conn.execute(
            '''INSERT INTO star_gift_offers
               (random_id, buyer_id, owner_id, saved_id, slug,
                price_stars, price_nanos, price_ton, duration,
                expires_at, offer_msg_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (random_id, buyer_id, owner_id, saved['saved_id'], slug,
             int(price_stars), int(price_nanos), int(price_ton),
             int(duration), expires_at, msg_id))
        self._conn.commit()

        return {
            'buyer_msg': self.get_message(buyer_id, msg_id),
            'owner_msg': self.get_message(owner_id, msg_id),
            'pts_buyer': pts_buyer,
            'pts_owner': pts_owner,
            'offer_msg_id': msg_id,
            'saved': saved,
        }

    def resolve_star_gift_offer(self, owner_id: int, offer_msg_id: int,
                                *, decline: bool = False) -> dict:
        offer = self.get_star_gift_offer(owner_id, offer_msg_id)
        if not offer:
            raise ValueError('OFFER_NOT_FOUND')

        saved = self.get_saved_star_gift_by_id(int(offer['saved_id']))
        if not saved or int(saved.get('unsaved') or 0):
            raise ValueError('STARGIFT_NOT_FOUND')
        if int(saved['owner_user_id']) != owner_id:
            raise ValueError('STARGIFT_OWNER_INVALID')

        buyer_id = int(offer['buyer_id'])
        now = int(time.time())
        slug = offer['slug']
        price_stars = int(offer['price_stars'] or 0)
        price_ton = int(offer['price_ton'] or 0)
        price_nanos = int(offer['price_nanos'] or 0)

        if decline:
            if price_ton > 0:
                self.adjust_ton_balance(
                    buyer_id, price_ton, peer_user_id=owner_id,
                    offer=True, refund=True, title='Gift offer refund')
            elif price_stars > 0:
                self.adjust_stars_balance(
                    buyer_id, price_stars, peer_user_id=owner_id,
                    offer=True, refund=True, title='Gift offer refund')
            self._conn.execute(
                "UPDATE star_gift_offers SET status='declined' WHERE id=?",
                (offer['id'],))
            decline_msg_id = self._allocate_message_id()
            action = {
                'type': 'star_gift_purchase_offer_declined',
                'slug': slug,
                'saved_id': int(saved['saved_id']),
                'price_stars': price_stars,
                'price_nanos': price_nanos,
                'price_ton': price_ton,
            }
            pts_owner = self._insert_message(
                decline_msg_id, owner_id, owner_id, buyer_id, '', [], now,
                action_json=json.dumps(action, ensure_ascii=False))
            pts_buyer = self._insert_message(
                decline_msg_id, buyer_id, owner_id, owner_id, '', [], now,
                action_json=json.dumps(action, ensure_ascii=False))
            self._upsert_dialog(owner_id, buyer_id, decline_msg_id, now, True, 0)
            self._upsert_dialog(buyer_id, owner_id, decline_msg_id, now, False, 1)
            self._conn.commit()
            return {
                'declined': True,
                'owner_msg': self.get_message(owner_id, decline_msg_id),
                'buyer_msg': self.get_message(buyer_id, decline_msg_id),
                'pts_owner': pts_owner,
                'pts_buyer': pts_buyer,
                'buyer_id': buyer_id,
                'owner_id': owner_id,
            }

        # Accept offer — transfer gift to buyer
        if self.saved_gift_chain_owner(saved):
            raise ValueError('STARGIFT_ON_BLOCKCHAIN')
        if int(self._conn.execute(
                'SELECT emoji_status_collectible_id FROM users WHERE id=?',
                (owner_id,)).fetchone()['emoji_status_collectible_id']
                or 0) == int(saved.get('unique_id') or 0):
            self.set_user_emoji_status(owner_id, None)

        new_msg_id = self._allocate_message_id()
        self._conn.execute(
            '''UPDATE saved_star_gifts SET owner_user_id=?, msg_id=?,
               pinned_to_top=0, resell_stars=NULL, resell_stars_nanos=0,
               resell_ton=0, date=?
               WHERE saved_id=?''',
            (buyer_id, new_msg_id, now, saved['saved_id']),
        )

        commission = config.STARGIFT_RESALE_COMMISSION_PERMILLE
        if price_ton > 0:
            payout_ton = price_ton * commission // 1000
            if payout_ton > 0:
                self.adjust_ton_balance(
                    owner_id, payout_ton, peer_user_id=buyer_id,
                    offer=True, stargift_resale=True,
                    title='Gift offer sale (GRAM)')
        else:
            payout = price_stars * commission // 1000
            if payout > 0:
                self.adjust_stars_balance(
                    owner_id, payout, peer_user_id=buyer_id,
                    offer=True, stargift_resale=True, title='Gift offer sale')

        action = {
            'type': 'star_gift_unique',
            'gift_id': int(saved['gift_id']),
            'unique_id': int(saved['unique_id']),
            'saved_id': int(new_msg_id),
            'peer_id': buyer_id,
            'from_id': owner_id,
            'upgrade': False,
            'saved': True,
            'from_offer': True,
            'resale_amount': price_stars if not price_ton else 0,
            'resale_nanos': price_nanos if not price_ton else 0,
        }
        pts_owner = self._insert_message(
            new_msg_id, owner_id, owner_id, buyer_id, '', [], now,
            action_json=json.dumps(action, ensure_ascii=False))
        pts_buyer = self._insert_message(
            new_msg_id, buyer_id, owner_id, owner_id, '', [], now,
            action_json=json.dumps(action, ensure_ascii=False))
        self._upsert_dialog(owner_id, buyer_id, new_msg_id, now, True, 0)
        self._upsert_dialog(buyer_id, owner_id, new_msg_id, now, False, 1)

        self._conn.execute(
            "UPDATE star_gift_offers SET status='accepted' WHERE id=?",
            (offer['id'],))
        self._conn.commit()
        self.recompute_star_gifts_catalog_hash()

        saved = self.get_saved_star_gift_by_id(int(saved['saved_id']))
        return {
            'declined': False,
            'owner_msg': self.get_message(owner_id, new_msg_id),
            'buyer_msg': self.get_message(buyer_id, new_msg_id),
            'pts_owner': pts_owner,
            'pts_buyer': pts_buyer,
            'buyer_id': buyer_id,
            'owner_id': owner_id,
            'saved': saved,
        }

    def list_collectible_saved_gifts(self, owner_user_id: int) -> List[dict]:
        rows = self._conn.execute(
            '''SELECT * FROM saved_star_gifts
               WHERE owner_user_id=? AND upgraded=1 AND unsaved=0 AND burned=0
               ORDER BY date DESC''',
            (owner_user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def toggle_star_gifts_pinned(self, owner_user_id: int,
                                 pinned_ids: List[int]) -> None:
        self._conn.execute(
            'UPDATE saved_star_gifts SET pinned_to_top=0 WHERE owner_user_id=?',
            (owner_user_id,),
        )
        for pid in pinned_ids:
            self._conn.execute(
                '''UPDATE saved_star_gifts SET pinned_to_top=1
                   WHERE owner_user_id=? AND (saved_id=? OR msg_id=?)''',
                (owner_user_id, int(pid), int(pid)),
            )
        self._conn.commit()

    def set_saved_gift_resell_price(self, owner_user_id: int, msg_id: int,
                                    stars: int, nanos: int = 0,
                                    *, ton: bool = False) -> dict:
        saved = self.get_saved_star_gift_by_msg(owner_user_id, msg_id)
        if not saved or not int(saved.get('upgraded') or 0):
            raise ValueError('STARGIFT_NOT_FOUND')
        if self.saved_gift_chain_owner(saved):
            raise ValueError('STARGIFT_ON_BLOCKCHAIN')
        if ton:
            if not config.can_use_ton_payments(owner_user_id):
                raise ValueError('TON_PAYMENT_NOT_ALLOWED')
            if int(nanos) > 0:
                if int(nanos) < config.STARGIFT_RESALE_TON_MIN:
                    raise ValueError('PRICE_TOO_LOW')
                if int(nanos) > config.STARGIFT_RESALE_TON_MAX:
                    raise ValueError('PRICE_TOO_HIGH')
            self._conn.execute(
                '''UPDATE saved_star_gifts
                   SET resell_stars=NULL, resell_stars_nanos=?, resell_ton=1
                   WHERE saved_id=?''',
                (int(nanos), saved['saved_id']),
            )
        else:
            total_stars = int(stars or 0)
            if total_stars > 0:
                if total_stars < config.STARGIFT_RESALE_STARS_MIN:
                    raise ValueError('PRICE_TOO_LOW')
                if total_stars > config.STARGIFT_RESALE_STARS_MAX:
                    raise ValueError('PRICE_TOO_HIGH')
            self._conn.execute(
                '''UPDATE saved_star_gifts
                   SET resell_stars=?, resell_stars_nanos=?, resell_ton=0
                   WHERE saved_id=?''',
                (int(stars) if stars else None, int(nanos), saved['saved_id']),
            )
        self._conn.commit()
        self.recompute_star_gifts_catalog_hash()
        return self.get_saved_star_gift_by_id(int(saved['saved_id']))

    def transfer_saved_star_gift(self, owner_id: int, msg_id: int,
                                 recipient_id: int) -> dict:
        if owner_id == recipient_id:
            raise ValueError('PEER_ID_INVALID')
        saved = self.get_saved_star_gift_by_msg(owner_id, msg_id)
        if not saved or not int(saved.get('upgraded') or 0):
            raise ValueError('STARGIFT_NOT_FOUND')
        if self.saved_gift_chain_owner(saved):
            raise ValueError('STARGIFT_ON_BLOCKCHAIN')
        recipient = self.get_user_by_id(recipient_id)
        if not recipient:
            raise ValueError('PEER_ID_INVALID')

        now = int(time.time())
        new_msg_id = self._allocate_message_id()
        self._conn.execute(
            '''UPDATE saved_star_gifts SET owner_user_id=?, msg_id=?,
               pinned_to_top=0, resell_stars=NULL, resell_stars_nanos=0,
               resell_ton=0, date=?
               WHERE saved_id=?''',
            (recipient_id, new_msg_id, now, saved['saved_id']),
        )
        if int(self._conn.execute(
                'SELECT emoji_status_collectible_id FROM users WHERE id=?',
                (owner_id,)).fetchone()['emoji_status_collectible_id']
                or 0) == int(saved.get('unique_id') or 0):
            self.set_user_emoji_status(owner_id, None)

        action = {
            'type': 'star_gift_unique',
            'gift_id': int(saved['gift_id']),
            'unique_id': int(saved.get('unique_id') or 0),
            'saved_id': int(new_msg_id),
            'peer_id': recipient_id,
            'upgrade': False,
            'saved': True,
            'transferred': True,
        }
        pts_owner = self._insert_message(
            new_msg_id, owner_id, owner_id, recipient_id, '', [], now,
            action_json=json.dumps(action, ensure_ascii=False))
        pts_recipient = self._insert_message(
            new_msg_id, recipient_id, owner_id, owner_id, '', [], now,
            action_json=json.dumps(action, ensure_ascii=False))
        self._upsert_dialog(owner_id, recipient_id, new_msg_id, now, True, 0)
        self._upsert_dialog(recipient_id, owner_id, new_msg_id, now, False, 1)
        self._conn.commit()
        saved = self.get_saved_star_gift_by_id(int(saved['saved_id']))
        self.recompute_star_gifts_catalog_hash()
        return {
            'saved': saved,
            'owner_msg': self.get_message(owner_id, new_msg_id),
            'recipient_msg': self.get_message(recipient_id, new_msg_id),
            'pts_owner': pts_owner,
            'pts_recipient': pts_recipient,
        }

    def get_saved_star_gift_by_msg(self, owner_user_id: int,
                                   msg_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM saved_star_gifts WHERE owner_user_id=? AND msg_id=?',
            (owner_user_id, msg_id)).fetchone()
        return dict(row) if row else None

    def get_saved_star_gift_by_msg_id(self, msg_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM saved_star_gifts WHERE msg_id=?',
            (msg_id,)).fetchone()
        return dict(row) if row else None

    def _allocate_saved_star_gift_id(self) -> int:
        row = self._conn.execute(
            'SELECT COALESCE(MAX(saved_id), 0) + 1 AS n FROM saved_star_gifts'
        ).fetchone()
        return int(row['n'])

    def insert_saved_star_gift(self, row: dict) -> dict:
        saved_id = int(row.get('saved_id') or self._allocate_saved_star_gift_id())
        self._conn.execute(
            '''INSERT INTO saved_star_gifts
               (owner_user_id, from_user_id, gift_id, msg_id, saved_id, stars,
                convert_stars, upgrade_stars, prepaid_upgrade, name_hidden,
                unsaved, pinned_to_top, date, message_text,
                message_entities_json, can_upgrade)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (row['owner_user_id'], row['from_user_id'], row['gift_id'],
             row.get('msg_id'), saved_id, row.get('stars'),
             row.get('convert_stars'), row.get('upgrade_stars'),
             int(row.get('prepaid_upgrade') or 0),
             int(row.get('name_hidden') or 0), int(row.get('unsaved') or 0),
             int(row.get('pinned_to_top') or 0), row['date'],
             row.get('message_text') or '', row.get('message_entities_json') or '[]',
             int(row.get('can_upgrade') or 0)))
        self._conn.commit()
        return self.get_saved_star_gift_by_id(saved_id)

    def update_saved_star_gift_flags(self, saved_id: int, *,
                                     unsaved: Optional[bool] = None,
                                     pinned_to_top: Optional[bool] = None) -> None:
        sets = []
        params = []
        if unsaved is not None:
            sets.append('unsaved=?')
            params.append(1 if unsaved else 0)
        if pinned_to_top is not None:
            sets.append('pinned_to_top=?')
            params.append(1 if pinned_to_top else 0)
        if not sets:
            return
        params.append(saved_id)
        self._conn.execute(
            f'UPDATE saved_star_gifts SET {",".join(sets)} WHERE saved_id=?',
            params)
        self._conn.commit()

    def recompute_star_gifts_catalog_hash(self) -> int:
        gifts = self.list_shop_star_gifts()
        h = 0x53484F50  # 'SHOP'
        for g in gifts:
            gid = int(g['gift_id'])
            resale = self.count_resale_gifts(gid)
            h = (h * 31 + gid) & 0x7FFFFFFF
            h = (h * 31 + int(g.get('sort_order') or 0)) & 0x7FFFFFFF
            h = (h * 31 + int(g.get('sold_out') or 0)) & 0x7FFFFFFF
            h = (h * 31 + int(g.get('availability_remains') or 0)) & 0x7FFFFFFF
            h = (h * 31 + int(g.get('availability_total') or 0)) & 0x7FFFFFFF
            # The gift stays visible before its scheduled release, but the
            # catalog must refresh when its lock expires.
            h = (h * 31 + int(bool(g.get('drop_at') and int(g['drop_at']) > int(time.time())))) & 0x7FFFFFFF
            h = (h * 31 + resale) & 0x7FFFFFFF
            min_price = self.min_resell_price(gid) or 0
            h = (h * 31 + min_price) & 0x7FFFFFFF
        self.set_star_gifts_catalog_hash(h)
        for g in self.list_star_gifts():
            variants = (self.count_star_gift_upgrade_attrs(g['gift_id'])
                        if self._gift_has_upgrade_pool(g['gift_id'])
                        and not int(g.get('upgrade_importing') or 0) else 0)
            self._conn.execute(
                'UPDATE star_gifts SET upgrade_variants=? WHERE gift_id=?',
                (variants, g['gift_id']))
        self._conn.commit()
        return h

    def gift_can_upgrade(self, gift_id: int) -> bool:
        gift = self.get_star_gift(gift_id)
        if not gift:
            return False
        if int(gift.get('upgrade_importing') or 0):
            return False
        return self._gift_has_upgrade_pool(gift_id)

    def _gift_has_upgrade_pool(self, gift_id: int) -> bool:
        gift = self.get_star_gift(gift_id)
        if not gift:
            return False
        attrs = self.list_star_gift_upgrade_attrs(gift_id)
        types = {a['attr_type'] for a in attrs}
        if 'model' not in types or 'pattern' not in types:
            return False
        return self.get_gift_upgrade_stars(gift_id) > 0

    def get_gift_upgrade_stars(self, gift_id: int) -> int:
        gift = self.get_star_gift(gift_id)
        if not gift:
            return 0
        price = int(gift.get('upgrade_stars') or 0)
        if price > 0:
            return price
        attrs = self.list_star_gift_upgrade_attrs(gift_id)
        types = {a['attr_type'] for a in attrs}
        if 'model' in types and 'pattern' in types:
            return 25
        return 0

    def _pick_upgrade_attrs(self, gift_id: int) -> dict:
        attrs = self.list_star_gift_upgrade_attrs(gift_id)
        out = {}
        for atype in ('model', 'pattern', 'backdrop'):
            pool = [a for a in attrs if a['attr_type'] == atype
                    and (a['gift_id'] == gift_id or a['gift_id'] == 0)]
            if atype == 'model':
                pool = [a for a in pool if not int(a.get('crafted') or 0)]
            if not pool:
                continue
            total = sum(int(a.get('rarity_permille') or 100) for a in pool)
            r = random.randint(0, max(total - 1, 0))
            acc = 0
            pick = pool[-1]
            for a in pool:
                acc += int(a.get('rarity_permille') or 100)
                if r < acc:
                    pick = a
                    break
            out[atype] = pick
        return out

    def _allocate_unique_gift_num(self, gift_id: int) -> int:
        row = self._conn.execute(
            'SELECT COALESCE(MAX(unique_num), 0) + 1 AS n FROM saved_star_gifts '
            'WHERE gift_id=? AND upgraded=1',
            (gift_id,),
        ).fetchone()
        return int(row['n'])

    def count_issued_unique_star_gifts(self, gift_id: int) -> int:
        row = self._conn.execute(
            '''SELECT COUNT(DISTINCT unique_id) AS n FROM saved_star_gifts
               WHERE gift_id=? AND unique_id IS NOT NULL AND upgraded=1''',
            (int(gift_id),),
        ).fetchone()
        return int(row['n'] or 0)

    def _allocate_unique_gift_id(self) -> int:
        row = self._conn.execute(
            'SELECT COALESCE(MAX(unique_id), 0) + 1 AS n FROM saved_star_gifts'
        ).fetchone()
        uid = int(row['n'])
        if uid >= 2**63:
            raise ValueError('UNIQUE_ID_EXHAUSTED')
        return uid

    def _slugify_gift_title(self, title: str, num: int) -> str:
        base = ''.join(
            c.lower() if c.isalnum() else '-' for c in (title or 'gift'))
        base = '-'.join(p for p in base.split('-') if p) or 'gift'
        return f'{base}-{num}'

    def _mark_gift_messages_upgraded(self, gift_msg_id: int,
                                     upgrade_msg_id: int) -> List[dict]:
        """Mark star_gift service messages upgraded in private chats."""
        updated = []
        rows = self._conn.execute(
            '''SELECT owner_id, id, action_json FROM messages
               WHERE id=? AND action_json IS NOT NULL''',
            (gift_msg_id,)).fetchall()
        for row in rows:
            action = json.loads(row['action_json'])
            if action.get('type') != 'star_gift':
                continue
            action['can_upgrade'] = False
            action['upgraded'] = True
            action['upgrade_msg_id'] = int(upgrade_msg_id)
            new_pts = self.increment_pts(int(row['owner_id']))
            self._conn.execute(
                'UPDATE messages SET action_json=?, pts=? WHERE owner_id=? AND id=?',
                (json.dumps(action, ensure_ascii=False), new_pts,
                 row['owner_id'], row['id']))
            msg = self.get_message(int(row['owner_id']), int(row['id']))
            if msg:
                updated.append(msg)
        return updated

    def _craft_chance_permille(self) -> int:
        # Keep the normal per-gift craft odds at or below 25%, even when a
        # stale deployment environment still exports the old 100% value.
        return min(250, max(1, int(
            getattr(config, 'STARGIFT_CRAFT_CHANCE_PERMILLE', 250) or 250)))

    def _craft_chance_from_model_attr(self, model_attr_id: int) -> int:
        if not self.get_star_gift_upgrade_attr_by_id(int(model_attr_id or 0)):
            return 0
        return self._craft_chance_permille()

    def sync_saved_gift_craft_chances(self, gift_id: int) -> int:
        """Set a flat craft_chance_permille on all craftable upgraded gifts."""
        gid = int(gift_id)
        chance = self._craft_chance_permille()
        cur = self._conn.execute(
            '''UPDATE saved_star_gifts SET craft_chance_permille=?
               WHERE gift_id=? AND upgraded=1 AND burned=0 AND crafted=0
                 AND craft_chance_permille IS NOT ?''',
            (chance, gid, chance),
        )
        if cur.rowcount:
            self._conn.commit()
        return int(cur.rowcount)

    def craft_chance_for_saved(self, saved: dict) -> int:
        if int(saved.get('burned') or 0):
            return 0
        if int(saved.get('crafted') or 0):
            return 0
        if not int(saved.get('upgraded') or 0):
            return 0
        return self._craft_chance_permille()

    def gift_has_craft_models(self, gift_id: int) -> bool:
        row = self._conn.execute(
            '''SELECT 1 FROM star_gift_upgrade_attrs
               WHERE (gift_id=? OR gift_id=0) AND attr_type='model' AND crafted=1
               LIMIT 1''',
            (int(gift_id),),
        ).fetchone()
        return bool(row)

    def saved_gift_craft_eligible(self, saved: dict, *, now: Optional[int] = None,
                                  check_blockchain: bool = False) -> bool:
        now = int(now or time.time())
        if not int(saved.get('upgraded') or 0):
            return False
        if int(saved.get('burned') or 0):
            return False
        if int(saved.get('crafted') or 0):
            return False
        can_at = int(saved.get('can_craft_at') or 0)
        if can_at > now:
            return False
        if (int(saved.get('resell_stars') or 0) > 0
                or int(saved.get('resell_stars_nanos') or 0) > 0):
            return False
        if check_blockchain:
            slug = (saved.get('unique_slug') or '').strip()
            if slug:
                link = self.get_gift_blockchain_link(slug)
                if link and str(link.get('owner_address') or '').strip():
                    return False
        return True

    def list_craftable_saved_gifts(self, owner_id: int, gift_id: int,
                                   offset: str, limit: int) -> tuple:
        if not self.gift_has_craft_models(gift_id):
            return [], 0, ''
        skip = int(offset) if (offset or '').isdigit() else 0
        lim = min(max(int(limit), 1), 100)
        now = int(time.time())
        total = int(self._conn.execute(
            '''SELECT COUNT(*) AS c FROM saved_star_gifts
               WHERE owner_user_id=? AND gift_id=? AND upgraded=1
                 AND burned=0 AND crafted=0
                 AND (can_craft_at=0 OR can_craft_at<=?)
                 AND COALESCE(resell_stars,0)=0
                 AND COALESCE(resell_stars_nanos,0)=0''',
            (owner_id, int(gift_id), now),
        ).fetchone()['c'])
        rows = self._conn.execute(
            '''SELECT * FROM saved_star_gifts
               WHERE owner_user_id=? AND gift_id=? AND upgraded=1
                 AND burned=0 AND crafted=0
                 AND (can_craft_at=0 OR can_craft_at<=?)
                 AND COALESCE(resell_stars,0)=0
                 AND COALESCE(resell_stars_nanos,0)=0
               ORDER BY pinned_to_top DESC, date DESC
               LIMIT ? OFFSET ?''',
            (owner_id, int(gift_id), now, lim, skip),
        ).fetchall()
        results = [dict(r) for r in rows]
        next_off = ''
        if skip + len(results) < total:
            next_off = str(skip + len(results))
        return results, total, next_off

    def _pick_craft_model_attr(self, gift_id: int) -> dict:
        from handlers.star_gift_rarity import craft_model_pick_weight

        attrs = self.list_star_gift_upgrade_attrs(gift_id)
        pool = [a for a in attrs if a['attr_type'] == 'model'
                and int(a.get('crafted') or 0)]
        if not pool:
            raise ValueError('STARGIFT_NOT_CRAFTABLE')
        total = sum(
            craft_model_pick_weight(int(a.get('rarity_permille') or 100))
            for a in pool)
        r = random.randint(0, max(total - 1, 0))
        acc = 0
        pick = pool[-1]
        for a in pool:
            acc += craft_model_pick_weight(int(a.get('rarity_permille') or 100))
            if r < acc:
                pick = a
                break
        return pick

    def _pick_craft_inherited_attr(self, inputs: List[dict], attr_type: str) -> Optional[dict]:
        count = len(inputs)
        if count < 1:
            return None
        row = config.STARGIFTS_CRAFT_ATTRIBUTE_PERMILLES[min(count - 1, 3)]
        groups: dict[int, int] = {}
        for saved in inputs:
            attr_id = int(saved.get(f'{attr_type}_attr_id') or 0)
            if attr_id:
                groups[attr_id] = groups.get(attr_id, 0) + 1
        if not groups:
            return None
        group_items = list(groups.items())
        total_w = sum(groups.values())
        r = random.randint(0, max(total_w - 1, 0))
        acc = 0
        chosen_id = group_items[-1][0]
        for attr_id, weight in group_items:
            acc += weight
            if r < acc:
                chosen_id = attr_id
                break
        perm_idx = min(len(group_items) - 1, len(row) - 1)
        if random.randint(1, 1000) <= int(row[perm_idx]):
            return self.get_star_gift_upgrade_attr_by_id(chosen_id)
        return None

    def craft_saved_star_gifts(self, owner_id: int, refs: List[dict]) -> dict:
        if not refs or len(refs) > 4:
            raise ValueError('STARGIFT_INVALID')
        now = int(time.time())
        saved_rows: List[dict] = []
        for ref in refs:
            row = self.resolve_saved_star_gift_ref(owner_id, ref)
            if not row:
                raise ValueError('STARGIFT_INVALID')
            if self.saved_gift_chain_owner(row):
                raise ValueError('STARGIFT_ON_BLOCKCHAIN')
            saved_rows.append(row)

        gift_ids = {int(r['gift_id']) for r in saved_rows}
        if len(gift_ids) != 1:
            raise ValueError('STARGIFT_INVALID')
        gift_id = next(iter(gift_ids))
        if not self.gift_has_craft_models(gift_id):
            raise ValueError('STARGIFT_NOT_CRAFTABLE')

        for idx, row in enumerate(saved_rows):
            if not self.saved_gift_craft_eligible(
                    row, now=now, check_blockchain=(idx == 0)):
                raise ValueError('STARGIFT_NOT_CRAFTABLE')

        total_chance = min(
            sum(self.craft_chance_for_saved(r) for r in saved_rows), 1000)
        success = random.randint(1, 1000) <= total_chance

        first = saved_rows[0]
        other_ids = [int(r['saved_id']) for r in saved_rows[1:]]

        if not success:
            for row in saved_rows:
                self._conn.execute(
                    'UPDATE saved_star_gifts SET burned=1 WHERE saved_id=?',
                    (int(row['saved_id']),))
            self._conn.commit()
            return {'success': False, 'burned': saved_rows}

        model = self._pick_craft_model_attr(gift_id)
        pattern = self._pick_craft_inherited_attr(saved_rows, 'pattern')
        backdrop = self._pick_craft_inherited_attr(saved_rows, 'backdrop')
        fallback = self._pick_upgrade_attrs(gift_id)
        if not pattern:
            pattern = fallback.get('pattern')
        if not backdrop:
            backdrop = fallback.get('backdrop')

        for sid in other_ids:
            self._conn.execute(
                'UPDATE saved_star_gifts SET burned=1 WHERE saved_id=?', (sid,))

        self._conn.execute(
            '''UPDATE saved_star_gifts SET
               crafted=1, burned=0,
               model_attr_id=?, pattern_attr_id=?, backdrop_attr_id=?,
               craft_chance_permille=0, can_craft_at=0,
               resell_stars=NULL, resell_stars_nanos=0, resell_ton=0
               WHERE saved_id=?''',
            (
                int(model['id']),
                int(pattern['id']) if pattern else None,
                int(backdrop['id']) if backdrop else None,
                int(first['saved_id']),
            ),
        )
        self._conn.commit()
        first = self.get_saved_star_gift_by_id(int(first['saved_id']))
        gift = self.get_star_gift(gift_id)

        svc_msg_id = self._allocate_message_id()
        action = {
            'type': 'star_gift_unique',
            'gift_id': gift_id,
            'unique_id': int(first['unique_id']),
            'saved_id': int(first['msg_id']),
            'peer_id': owner_id,
            'craft': True,
            'saved': True,
            'from_id': int(first.get('from_user_id') or owner_id),
        }
        pts = self._insert_message(
            svc_msg_id, owner_id, owner_id, owner_id, '', [], now,
            action_json=json.dumps(action, ensure_ascii=False))
        self._upsert_dialog(owner_id, owner_id, svc_msg_id, now, True, 0)
        self._conn.commit()

        return {
            'success': True,
            'saved': first,
            'service_msg': self.get_message(owner_id, svc_msg_id),
            'pts': pts,
            'gift': gift,
            'burned_count': len(other_ids),
        }

    def upgrade_saved_star_gift(self, owner_id: int, msg_id: int, *,
                                keep_original: bool = False,
                                deduct_stars: bool = True) -> dict:
        saved = self.get_saved_star_gift_by_msg(owner_id, msg_id)
        if not saved:
            raise ValueError('STARGIFT_NOT_FOUND')
        if int(saved.get('upgraded') or 0):
            raise ValueError('STARGIFT_ALREADY_UPGRADED')
        gift_id = int(saved['gift_id'])
        if not self.gift_can_upgrade(gift_id):
            raise ValueError('STARGIFT_NOT_UPGRADABLE')

        gift = self.get_star_gift(gift_id)
        stars = self.get_gift_upgrade_stars(gift_id)
        prepaid = bool(int(saved.get('prepaid_upgrade') or 0))
        if deduct_stars and prepaid:
            deduct_stars = False
        if deduct_stars and stars > 0:
            balance = self.get_stars_balance(owner_id)
            if not config.INFINITE_STARS and balance < stars:
                raise ValueError('BALANCE_TOO_LOW')
            self.adjust_stars_balance(
                owner_id, -stars, stargift_upgrade=True,
                title=gift.get('title') or 'Gift upgrade')

        picked = self._pick_upgrade_attrs(gift_id)
        if 'model' not in picked or 'pattern' not in picked:
            raise ValueError('STARGIFT_NOT_UPGRADABLE')

        unique_num = self._allocate_unique_gift_num(gift_id)
        unique_id = self._allocate_unique_gift_id()
        title = (gift.get('title') or 'Gift').strip() or 'Gift'
        slug = self._slugify_gift_title(title, unique_num)
        total = int(gift.get('availability_total') or 100000)
        craft_chance = self._craft_chance_from_model_attr(int(picked['model']['id']))
        now = int(time.time())
        can_craft_at = now if self.gift_has_craft_models(gift_id) else 0

        self._conn.execute(
            '''UPDATE saved_star_gifts SET
               upgraded=1, can_upgrade=0, prepaid_upgrade=0, upgrade_stars=NULL,
               original_details_kept=?,
               unique_id=?, unique_slug=?, unique_num=?,
               model_attr_id=?, pattern_attr_id=?, backdrop_attr_id=?,
               craft_chance_permille=?, crafted=0, burned=0, can_craft_at=?
               WHERE saved_id=?''',
            (1 if keep_original else 0, unique_id, slug, unique_num,
             picked['model']['id'],
             picked['pattern']['id'],
             picked.get('backdrop', {}).get('id'),
             craft_chance,
             can_craft_at,
             saved['saved_id']),
        )
        self._conn.commit()
        saved = self.get_saved_star_gift_by_id(int(saved['saved_id']))

        svc_msg_id = self._allocate_message_id()
        action = {
            'type': 'star_gift_unique',
            'gift_id': gift_id,
            'unique_id': int(saved['unique_id']),
            'saved_id': int(saved['msg_id']),
            'peer_id': owner_id,
            'upgrade': True,
            'saved': True,
        }
        pts = self._insert_message(
            svc_msg_id, owner_id, owner_id, owner_id, '', [], now,
            action_json=json.dumps(action, ensure_ascii=False))
        self._upsert_dialog(owner_id, owner_id, svc_msg_id, now, True, 0)
        gift_chat_updates = self._mark_gift_messages_upgraded(msg_id, svc_msg_id)
        self._conn.commit()

        return {
            'saved': saved,
            'service_msg': self.get_message(owner_id, svc_msg_id),
            'gift_chat_updates': gift_chat_updates,
            'pts': pts,
            'picked': picked,
            'gift': gift,
            'total': total,
        }

    # ── Stars profile rating ───────────────────

    def get_stars_rating_points(self, user_id: int) -> int:
        row = self._conn.execute(
            'SELECT points FROM stars_ratings WHERE user_id=?',
            (int(user_id),)).fetchone()
        return int(row['points']) if row else 0

    def get_stars_rating(self, user_id: int) -> dict:
        from db.stars_rating import compute_stars_rating
        return compute_stars_rating(self.get_stars_rating_points(user_id))

    def set_stars_rating_points(self, user_id: int, points: int) -> dict:
        """Force absolute rating points for a user (admin tool)."""
        from db.stars_rating import compute_stars_rating

        uid = int(user_id)
        if not self.get_user_by_id(uid):
            raise ValueError('USER_ID_INVALID')
        pts = int(points)
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO stars_ratings (user_id, points, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   points = excluded.points,
                   updated_at = excluded.updated_at''',
            (uid, pts, now),
        )
        self._conn.commit()
        return compute_stars_rating(pts)

    def set_stars_rating_level(self, user_id: int, level: int) -> dict:
        """Set rating exactly to the threshold of the given level (0..MAX)."""
        from db.stars_rating import MAX_LEVEL, level_threshold

        level = int(level)
        if level < 0 or level > MAX_LEVEL:
            raise ValueError(f'LEVEL_INVALID (0..{MAX_LEVEL})')
        points = 0 if level <= 0 else level_threshold(level)
        return self.set_stars_rating_points(user_id, points)

    def _adjust_stars_rating_points(self, user_id: int, delta: int) -> None:
        delta = int(delta)
        if not delta:
            return
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO stars_ratings (user_id, points, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   points = stars_ratings.points + excluded.points,
                   updated_at = excluded.updated_at''',
            (int(user_id), delta, now))
        self._conn.commit()

    def _apply_stars_rating_delta(self, user_id: int, amount_delta: int,
                                  **tx_kw) -> None:
        from db.stars_rating import compute_rating_delta
        rating_delta = compute_rating_delta(amount_delta, **tx_kw)
        if rating_delta:
            self._adjust_stars_rating_points(user_id, rating_delta)

    def apply_stars_rating_convert_penalty(self, user_id: int,
                                           gift_stars: int) -> None:
        from db.stars_rating import compute_convert_penalty
        penalty = compute_convert_penalty(gift_stars)
        if penalty:
            self._adjust_stars_rating_points(user_id, penalty)

    # ── Profile saved music ────────────────────

    _MAX_SAVED_MUSIC = 100

    @staticmethod
    def compute_telegram_vector_hash(ids: List[int]) -> int:
        """Standard Telegram pagination hash (corefork.telegram.org/api/offsets)."""
        h = 0
        for doc_id in ids:
            h = (h ^ (h >> 21)) & 0xFFFFFFFFFFFFFFFF
            h = (h ^ (h << 35)) & 0xFFFFFFFFFFFFFFFF
            h = (h ^ (h >> 4)) & 0xFFFFFFFFFFFFFFFF
            h = (h + int(doc_id)) & 0xFFFFFFFFFFFFFFFF
        if h >= 1 << 63:
            return h - (1 << 64)
        return h

    def get_user_main_profile_tab(self, user_id: int) -> int:
        row = self._conn.execute(
            'SELECT main_profile_tab FROM users WHERE id=?',
            (int(user_id),)).fetchone()
        return int(row['main_profile_tab']) if row else 0

    def set_user_main_profile_tab(self, user_id: int, tab_cid: int) -> None:
        self._conn.execute(
            'UPDATE users SET main_profile_tab=? WHERE id=?',
            (int(tab_cid), int(user_id)))
        self._conn.commit()

    def get_user_personal_channel(self, user_id: int) -> tuple[int, int]:
        row = self._conn.execute(
            'SELECT personal_channel_id, personal_channel_message FROM users WHERE id=?',
            (int(user_id),)).fetchone()
        if not row:
            return 0, 0
        channel_id = int(row['personal_channel_id'] or 0)
        msg_id = int(row['personal_channel_message'] or 0)
        if not channel_id:
            return 0, 0
        resolved = self.resolve_personal_channel_message(channel_id, msg_id)
        if resolved and resolved != msg_id:
            try:
                self._conn.execute(
                    'UPDATE users SET personal_channel_message=? WHERE id=?',
                    (int(resolved), int(user_id)),
                )
                self._conn.commit()
            except Exception:
                pass
            msg_id = resolved
        elif not resolved:
            # Stale preview — fall back to channel top if it exists later
            msg_id = int((self.get_channel(channel_id) or {}).get(
                'top_message_id') or 0)
        return channel_id, msg_id

    def resolve_personal_channel_message(self, channel_id: int,
                                         preferred_msg_id: int = 0) -> int:
        """Return a message id that actually exists for this channel."""
        ch = self.get_channel(int(channel_id))
        if not ch:
            return 0
        candidates = []
        preferred = int(preferred_msg_id or 0)
        if preferred:
            candidates.append(preferred)
        top = int(ch.get('top_message_id') or 0)
        if top and top not in candidates:
            candidates.append(top)
        for mid in candidates:
            row = self._conn.execute(
                'SELECT 1 FROM messages WHERE id=? AND peer_id=? LIMIT 1',
                (int(mid), int(channel_id)),
            ).fetchone()
            if row:
                return int(mid)
        row = self._conn.execute(
            '''SELECT id FROM messages WHERE peer_id=?
               ORDER BY id DESC LIMIT 1''',
            (int(channel_id),),
        ).fetchone()
        return int(row['id']) if row else 0

    def set_user_personal_channel(self, user_id: int, channel_id: int,
                                  message_id: int) -> None:
        channel_id = int(channel_id or 0)
        message_id = int(message_id or 0)
        if channel_id:
            message_id = (
                self.resolve_personal_channel_message(channel_id, message_id)
                or message_id
            )
        self._conn.execute(
            'UPDATE users SET personal_channel_id=?, personal_channel_message=? WHERE id=?',
            (channel_id, message_id, int(user_id)))
        self._conn.commit()

    def list_user_admined_public_channels(self, user_id: int) -> List[dict]:
        rows = self._conn.execute(
            '''SELECT c.* FROM channels c
               INNER JOIN channel_members cm ON c.id = cm.channel_id
               WHERE cm.user_id=? AND cm.left=0 AND cm.is_admin=1
                 AND c.megagroup=0
                 AND c.username IS NOT NULL AND TRIM(c.username) != ''
               ORDER BY c.title COLLATE NOCASE''',
            (int(user_id),)).fetchall()
        return [dict(r) for r in rows]

    def _document_is_music(self, doc: dict) -> bool:
        mime = (doc.get('mime_type') or '').lower()
        if mime.startswith('audio/'):
            return True
        attrs = (doc.get('attributes_json') or '').lower()
        return '9852f9c6' in attrs or 'documentattributeaudio' in attrs

    def list_user_saved_music_ids(self, user_id: int, *,
                                  cleanup: bool = False) -> List[int]:
        rows = self._conn.execute(
            '''SELECT document_id FROM user_saved_music
               WHERE user_id=? ORDER BY sort_order, document_id''',
            (int(user_id),)).fetchall()
        ids: List[int] = []
        stale: List[int] = []
        for row in rows:
            doc_id = int(row['document_id'])
            doc = self.get_document_by_id(doc_id)
            if doc and self._document_is_music(doc):
                ids.append(doc_id)
            else:
                stale.append(doc_id)
        if cleanup and stale:
            for doc_id in stale:
                self.unsave_user_music(user_id, doc_id)
        return ids

    def get_user_saved_music_first(self, user_id: int) -> Optional[dict]:
        row = self._conn.execute(
            '''SELECT document_id FROM user_saved_music
               WHERE user_id=? ORDER BY sort_order, document_id LIMIT 1''',
            (int(user_id),)).fetchone()
        if not row:
            return None
        return self.get_document_by_id(int(row['document_id']))

    def compute_saved_music_ids_hash(self, user_id: int) -> int:
        ids = sorted(self.list_user_saved_music_ids(user_id))
        if not ids:
            return 0
        return self.compute_telegram_vector_hash(ids)

    def compute_saved_music_hash(self, user_id: int) -> int:
        ids = self.list_user_saved_music_ids(user_id)
        if not ids:
            return 0
        return self.compute_telegram_vector_hash(ids)

    def _rewrite_user_saved_music(self, user_id: int,
                                  document_ids: List[int]) -> None:
        user_id = int(user_id)
        self._conn.execute(
            'DELETE FROM user_saved_music WHERE user_id=?', (user_id,))
        for order, doc_id in enumerate(document_ids):
            self._conn.execute(
                '''INSERT INTO user_saved_music
                   (user_id, document_id, sort_order)
                   VALUES (?,?,?)''',
                (user_id, int(doc_id), order))
        self._conn.commit()

    def save_user_music(self, user_id: int, document_id: int, *,
                        after_id: Optional[int] = None) -> bool:
        document_id = int(document_id)
        user_id = int(user_id)
        doc = self.get_document_by_id(document_id)
        if not doc or not self._document_is_music(doc):
            raise ValueError('DOCUMENT_INVALID')

        ids = self.list_user_saved_music_ids(user_id)
        if after_id is not None and int(after_id) not in ids:
            return False

        if document_id in ids:
            ids.remove(document_id)
        elif len(ids) >= self._MAX_SAVED_MUSIC:
            raise ValueError('MUSIC_PROFILE_LIMIT')

        if after_id is None:
            ids.insert(0, document_id)
        else:
            pos = ids.index(int(after_id)) + 1
            ids.insert(pos, document_id)

        self._rewrite_user_saved_music(user_id, ids)
        return True

    def unsave_user_music(self, user_id: int, document_id: int) -> None:
        self._conn.execute(
            'DELETE FROM user_saved_music WHERE user_id=? AND document_id=?',
            (int(user_id), int(document_id)))
        self._conn.commit()
        if not self.list_user_saved_music_ids(user_id):
            if self.get_user_main_profile_tab(user_id) == 0x9f27d26e:
                self.set_user_main_profile_tab(user_id, 0)

    # ── Stars transactions ─────────────────────

    def _new_stars_tx_id(self) -> str:
        return f'{int(time.time())}{random.randint(100000, 999999)}'

    def record_stars_transaction(self, user_id: int, amount: int, *,
                                 peer_user_id: int = 0,
                                 peer_channel_id: int = 0,
                                 gift: bool = False,
                                 refund: bool = False,
                                 title: str = '',
                                 description: str = '',
                                 stargift_upgrade: bool = False,
                                 stargift_resale: bool = False,
                                 stargift_auction_bid: bool = False,
                                 offer: bool = False,
                                 amount_ton: int = 0,
                                 msg_id: int = 0) -> str:
        tx_id = self._new_stars_tx_id()
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO stars_transactions
               (tx_id, user_id, amount, date, gift, refund,
                peer_user_id, peer_channel_id, title, description,
                stargift_upgrade, stargift_resale, stargift_auction_bid,
                offer, amount_ton, msg_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (tx_id, user_id, int(amount), now,
             1 if gift else 0, 1 if refund else 0,
             peer_user_id or None, peer_channel_id or None,
             title or None, description or None,
             1 if stargift_upgrade else 0,
             1 if stargift_resale else 0,
             1 if stargift_auction_bid else 0,
             1 if offer else 0,
             int(amount_ton or 0), msg_id or None))
        self._conn.commit()
        return tx_id

    def adjust_stars_balance(self, user_id: int, delta: int, **tx_kw) -> int:
        skip_rating = bool(tx_kw.pop('_skip_rating', False))
        tx_kw.pop('paid_message', None)
        tx_kw.pop('shop_purchase', None)
        tx_kw.pop('anonymous_number', None)
        if delta and not skip_rating:
            self._apply_stars_rating_delta(user_id, int(delta), **tx_kw)
        record_kw = {
            k: tx_kw[k] for k in (
                'peer_user_id', 'peer_channel_id', 'gift', 'refund', 'title',
                'description', 'stargift_upgrade', 'stargift_resale',
                'stargift_auction_bid', 'offer', 'amount_ton', 'msg_id',
            ) if k in tx_kw
        }
        if config.INFINITE_STARS:
            if delta:
                self.record_stars_transaction(user_id, int(delta), **record_kw)
            return config.STARS_BALANCE
        balance = self.get_stars_balance(user_id)
        new_balance = max(0, balance + int(delta))
        self.set_stars_balance(user_id, new_balance)
        if delta:
            self.record_stars_transaction(user_id, int(delta), **record_kw)
        return new_balance

    def list_stars_transactions(self, user_id: int, *,
                                offset: str = '',
                                limit: int = 20,
                                inbound: bool = False,
                                outbound: bool = False) -> tuple:
        limit = max(1, min(int(limit or 20), 100))
        clauses = ['user_id=?']
        params: list = [user_id]
        if inbound:
            clauses.append('amount > 0')
        elif outbound:
            clauses.append('amount < 0')
        if offset:
            clauses.append('tx_id < ?')
            params.append(offset)
        where = ' AND '.join(clauses)
        rows = self._conn.execute(
            f'''SELECT * FROM stars_transactions WHERE {where}
                ORDER BY date DESC, tx_id DESC LIMIT ?''',
            (*params, limit + 1)).fetchall()
        next_off = ''
        if len(rows) > limit:
            next_off = rows[limit - 1]['tx_id']
            rows = rows[:limit]
        return [dict(r) for r in rows], next_off

    def get_stars_transactions_by_ids(self, user_id: int,
                                      tx_ids: List[str]) -> List[dict]:
        if not tx_ids:
            return []
        placeholders = ','.join('?' * len(tx_ids))
        rows = self._conn.execute(
            f'''SELECT * FROM stars_transactions
                WHERE user_id=? AND tx_id IN ({placeholders})
                ORDER BY date DESC''',
            (user_id, *tx_ids)).fetchall()
        return [dict(r) for r in rows]

    def _normalize_huge_stars_credits_for_user(self, user_id: int) -> None:
        """Android miscounts large inbound credits as reserved offer balance."""
        self._conn.execute('''
            UPDATE stars_transactions
            SET amount = 0, title = 'Stars top-up', description = NULL
            WHERE user_id=? AND amount > 0 AND refund = 0 AND offer = 0
              AND gift = 0 AND amount >= 10000000
        ''', (user_id,))
        self._conn.commit()

    def ensure_stars_opening_balance_tx(self, user_id: int) -> None:
        row = self._conn.execute(
            'SELECT 1 FROM stars_transactions WHERE user_id=? LIMIT 1',
            (user_id,)).fetchone()
        if row:
            return
        balance = self.get_stars_balance(user_id)
        if balance <= 0 and not config.INFINITE_STARS:
            return
        credit = balance if balance > 0 else config.DEFAULT_STARS_BALANCE
        if credit >= 10_000_000:
            credit = 0
        self.record_stars_transaction(
            user_id, credit, title='Opening balance')

    def ensure_user_stars(self, user_id: int) -> None:
        """Restore infinite balance and seed opening transaction if needed."""
        if not user_id:
            return
        if config.INFINITE_STARS:
            self.set_stars_balance(user_id, config.STARS_BALANCE)
        self._normalize_huge_stars_credits_for_user(user_id)
        self.ensure_stars_opening_balance_tx(user_id)

    # ── Channels / groups ─────────────────────

    def _allocate_channel_id(self) -> int:
        row = self._conn.execute(
            'SELECT COALESCE(MAX(id), ?) + 1 AS n FROM channels',
            (config.CHANNEL_ID_MIN - 1,)).fetchone()
        return max(config.CHANNEL_ID_MIN, int(row['n']))

    def _random_access_hash(self) -> int:
        return random.randint(1, (1 << 63) - 1)

    def get_channel(self, channel_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM channels WHERE id=?', (channel_id,)).fetchone()
        if not row:
            return None
        ch = dict(row)
        bv = self.get_bot_verification(int(ch['id']))
        if bv:
            ch['bot_verification_icon'] = bv['icon_id']
            ch['bot_verification'] = bv
        return ch

    def get_channel_by_access_hash(self, channel_id: int,
                                   access_hash: int) -> Optional[dict]:
        ch = self.get_channel(channel_id)
        if ch and int(ch['access_hash']) == int(access_hash):
            return ch
        return None

    def is_channel_member(self, channel_id: int, user_id: int) -> bool:
        row = self._conn.execute(
            '''SELECT 1 FROM channel_members
               WHERE channel_id=? AND user_id=? AND left=0''',
            (channel_id, user_id)).fetchone()
        return row is not None

    def list_channel_member_ids(self, channel_id: int) -> List[int]:
        rows = self._conn.execute(
            '''SELECT user_id FROM channel_members
               WHERE channel_id=? AND left=0''',
            (channel_id,)).fetchall()
        return [int(r['user_id']) for r in rows]

    def create_channel(self, creator_id: int, title: str,
                       member_ids: List[int], *,
                       megagroup: bool = True) -> dict:
        title = (title or '').strip()
        if not title:
            raise ValueError('CHAT_TITLE_EMPTY')
        members = {creator_id}
        for uid in member_ids:
            if uid and uid != creator_id:
                u = self.get_user_by_id(uid)
                if u and not u.get('is_bot'):
                    members.add(uid)
        if len(members) < 1:
            raise ValueError('USERS_TOO_FEW')

        channel_id = self._allocate_channel_id()
        access_hash = self._random_access_hash()
        now = int(time.time())
        self._conn.execute(
            '''INSERT INTO channels
               (id, access_hash, title, creator_id, date, pts,
                participants_count, megagroup)
               VALUES (?,?,?,?,?,?,?,?)''',
            (channel_id, access_hash, title, creator_id, now, 1,
             len(members), 1 if megagroup else 0))
        for uid in members:
            is_admin = 1 if uid == creator_id else 0
            self._conn.execute(
                '''INSERT INTO channel_members
                   (channel_id, user_id, is_admin, left, date)
                   VALUES (?,?,?,?,?)''',
                (channel_id, uid, is_admin, 0, now))
            self._conn.execute(
                '''INSERT INTO channel_member_state
                   (channel_id, user_id, pts, read_max_id, unread_count)
                   VALUES (?,?,0,0,0)''',
                (channel_id, uid))
            self._upsert_dialog(uid, channel_id, 0, now, True, 0)
        self.ensure_channel_invite_hash(channel_id)
        self._conn.commit()
        return self.get_channel(channel_id)

    def get_channel_monoforum(self, channel_id: int) -> Optional[dict]:
        ch = self.get_channel(channel_id)
        if not ch:
            return None
        mono_id = int(ch.get('linked_monoforum_id') or 0)
        mono = self.get_channel(mono_id) if mono_id else None
        if mono and int(mono.get('monoforum') or 0):
            return mono
        row = self._conn.execute(
            '''SELECT * FROM channels
               WHERE monoforum=1 AND linked_monoforum_id=?
               ORDER BY id LIMIT 1''',
            (channel_id,)).fetchone()
        return dict(row) if row else None

    def get_linked_channel_for_monoforum(self, monoforum_id: int) -> Optional[dict]:
        mono = self.get_channel(monoforum_id)
        if not mono or not int(mono.get('monoforum') or 0):
            return None
        linked_id = int(mono.get('linked_monoforum_id') or 0)
        return self.get_channel(linked_id) if linked_id else None

    def ensure_channel_monoforum(self, channel_id: int) -> dict:
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        existing = self.get_channel_monoforum(channel_id)
        if existing:
            if int(ch.get('linked_monoforum_id') or 0) != int(existing['id']):
                self._conn.execute(
                    'UPDATE channels SET linked_monoforum_id=? WHERE id=?',
                    (int(existing['id']), channel_id))
            if int(existing.get('linked_monoforum_id') or 0) != int(channel_id):
                self._conn.execute(
                    'UPDATE channels SET linked_monoforum_id=?, monoforum=1 WHERE id=?',
                    (channel_id, int(existing['id'])))
            for uid in self.list_channel_admin_ids(channel_id):
                self._conn.execute(
                    '''INSERT OR IGNORE INTO channel_members
                       (channel_id, user_id, is_admin, left, date)
                       VALUES (?,?,?,?,?)''',
                    (int(existing['id']), int(uid), 1, 0, int(time.time())))
                self._conn.execute(
                    '''INSERT OR IGNORE INTO channel_member_state
                       (channel_id, user_id, pts, read_max_id, unread_count)
                       VALUES (?,?,0,0,0)''',
                    (int(existing['id']), int(uid)))
            self._conn.commit()
            return self.get_channel(int(existing['id']))

        mono_id = self._allocate_channel_id()
        access_hash = self._random_access_hash()
        now = int(time.time())
        admin_ids = self.list_channel_admin_ids(channel_id)
        title = f"{ch.get('title') or 'Channel'} Direct"
        self._conn.execute(
            '''INSERT INTO channels
               (id, access_hash, title, about, creator_id, megagroup, date,
                pts, participants_count, monoforum, linked_monoforum_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (mono_id, access_hash, title, '', int(ch['creator_id']), 1, now,
             1, len(admin_ids), 1, channel_id))
        for uid in admin_ids:
            self._conn.execute(
                '''INSERT INTO channel_members
                   (channel_id, user_id, is_admin, left, date)
                   VALUES (?,?,?,?,?)''',
                (mono_id, int(uid), 1, 0, now))
            self._conn.execute(
                '''INSERT INTO channel_member_state
                   (channel_id, user_id, pts, read_max_id, unread_count)
                   VALUES (?,?,0,0,0)''',
                (mono_id, int(uid)))
            self._upsert_dialog(int(uid), mono_id, 0, now, True, 0)
        self._conn.execute(
            'UPDATE channels SET linked_monoforum_id=? WHERE id=?',
            (mono_id, channel_id))
        self._conn.commit()
        return self.get_channel(mono_id)

    def add_channel_members(self, channel_id: int, inviter_id: int,
                            user_ids: List[int]) -> List[int]:
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        if not self.is_channel_member(channel_id, inviter_id):
            raise ValueError('CHAT_ADMIN_REQUIRED')
        added = []
        now = int(time.time())
        from handlers.privacy import check_can_invite_user
        for uid in user_ids:
            if not uid or self.is_channel_member(channel_id, uid):
                continue
            u = self.get_user_by_id(uid)
            if not u:
                continue
            try:
                check_can_invite_user(self, uid, inviter_id)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            existing = self._conn.execute(
                '''SELECT * FROM channel_members
                   WHERE channel_id=? AND user_id=?''',
                (channel_id, uid)).fetchone()
            if existing:
                self._conn.execute(
                    '''UPDATE channel_members
                       SET left=0, date=? WHERE channel_id=? AND user_id=?''',
                    (now, channel_id, uid))
            else:
                self._conn.execute(
                    '''INSERT INTO channel_members
                       (channel_id, user_id, is_admin, left, date)
                       VALUES (?,?,0,0,?)''',
                    (channel_id, uid, now))
            self._conn.execute(
                '''INSERT OR IGNORE INTO channel_member_state
                   (channel_id, user_id, pts, read_max_id, unread_count)
                   VALUES (?,?,0,0,0)''',
                (channel_id, uid))
            self.ensure_dialog(uid, channel_id)
            added.append(uid)
        if added:
            cnt = len(self.list_channel_member_ids(channel_id))
            self._conn.execute(
                'UPDATE channels SET participants_count=? WHERE id=?',
                (cnt, channel_id))
            self._conn.commit()
            for uid in added:
                self.ensure_channel_messages_for_member(channel_id, uid)
        return added

    def leave_channel(self, channel_id: int, user_id: int) -> None:
        if not self.get_channel(channel_id):
            raise ValueError('CHANNEL_INVALID')
        self._conn.execute(
            'UPDATE channel_members SET left=1 WHERE channel_id=? AND user_id=?',
            (channel_id, user_id))
        cnt = len(self.list_channel_member_ids(channel_id))
        self._conn.execute(
            'UPDATE channels SET participants_count=? WHERE id=?',
            (cnt, channel_id))
        self._conn.commit()

    def is_channel_admin(self, channel_id: int, user_id: int) -> bool:
        ch = self.get_channel(channel_id)
        if ch and int(ch.get('creator_id') or 0) == int(user_id):
            return True
        row = self._conn.execute(
            '''SELECT is_admin FROM channel_members
               WHERE channel_id=? AND user_id=? AND left=0''',
            (channel_id, user_id)).fetchone()
        return bool(row and int(row['is_admin']))

    def list_channel_admin_ids(self, channel_id: int) -> List[int]:
        ch = self.get_channel(channel_id)
        ids = []
        if ch:
            creator = int(ch.get('creator_id') or 0)
            if creator:
                ids.append(creator)
        rows = self._conn.execute(
            '''SELECT user_id FROM channel_members
               WHERE channel_id=? AND left=0 AND is_admin=1''',
            (channel_id,)).fetchall()
        for row in rows:
            uid = int(row['user_id'])
            if uid not in ids:
                ids.append(uid)
        return ids

    def set_channel_admin(self, channel_id: int, user_id: int, *,
                          is_admin: bool,
                          promoted_by: Optional[int] = None,
                          rank: Optional[str] = None) -> None:
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        if int(ch.get('creator_id') or 0) == int(user_id):
            raise ValueError('CHAT_ADMIN_REQUIRED')
        if not self.is_channel_member(channel_id, user_id):
            raise ValueError('USER_NOT_PARTICIPANT')
        self._conn.execute(
            '''UPDATE channel_members
               SET is_admin=?, promoted_by=?, admin_rank=?
               WHERE channel_id=? AND user_id=? AND left=0''',
            (1 if is_admin else 0, promoted_by, rank, channel_id, user_id))
        self._conn.commit()

    def get_channel_member_row(self, channel_id: int,
                               user_id: int) -> Optional[dict]:
        row = self._conn.execute(
            '''SELECT * FROM channel_members
               WHERE channel_id=? AND user_id=? AND left=0''',
            (channel_id, user_id)).fetchone()
        return dict(row) if row else None

    def add_channel_admin_log(self, channel_id: int, user_id: int,
                              action_type: str,
                              action_data: dict) -> int:
        now = int(time.time())
        cur = self._conn.execute(
            '''INSERT INTO channel_admin_log
               (channel_id, user_id, date, action_type, action_json)
               VALUES (?,?,?,?,?)''',
            (channel_id, user_id, now, action_type,
             json.dumps(action_data, ensure_ascii=False)))
        self._conn.commit()
        return int(cur.lastrowid)

    def list_channel_admin_log(self, channel_id: int, *,
                               max_id: int = 0, min_id: int = 0,
                               limit: int = 50) -> List[dict]:
        limit = max(1, min(int(limit or 50), 100))
        query = (
            'SELECT * FROM channel_admin_log WHERE channel_id=?'
        )
        params: list = [channel_id]
        if max_id:
            query += ' AND id < ?'
            params.append(int(max_id))
        if min_id:
            query += ' AND id > ?'
            params.append(int(min_id))
        query += ' ORDER BY id DESC LIMIT ?'
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def can_post_to_channel(self, channel_id: int, user_id: int) -> bool:
        ch = self.get_channel(channel_id)
        if not ch or not self.is_channel_member(channel_id, user_id):
            return False
        if int(ch.get('megagroup') or 0):
            return True
        return self.is_channel_admin(channel_id, user_id)

    def get_channel_difference_messages(self, owner_id: int, channel_id: int,
                                        client_pts: int,
                                        limit: int) -> List[dict]:
        ch = self.get_channel(channel_id)
        if not ch:
            return []
        new_pts = int(ch['pts'])
        if client_pts >= new_pts:
            return []
        if self.is_channel_member(channel_id, owner_id):
            self.ensure_channel_messages_for_member(channel_id, owner_id)
        need = min(max(int(new_pts - client_pts), 1),
                   max(1, min(int(limit or 100), 100)))
        top_id = int(ch.get('top_message_id') or 0)
        if top_id <= 0:
            return []
        rows = self._conn.execute(
            '''SELECT * FROM messages
               WHERE owner_id=? AND peer_id=? AND id <= ?
               ORDER BY id DESC LIMIT ?''',
            (owner_id, channel_id, top_id, need)).fetchall()
        rows = list(reversed(rows))
        return [self._message_row(r) for r in rows]

    def _channel_message_source_owner(self, channel_id: int) -> int:
        row = self._conn.execute(
            '''SELECT owner_id FROM messages
               WHERE peer_id=?
                 AND (action_json IS NULL OR action_json = ''
                      OR (action_json NOT LIKE ?
                          AND action_json NOT LIKE ?))
               GROUP BY owner_id
               ORDER BY COUNT(*) DESC LIMIT 1''',
            (channel_id, '%"type":"channel_direct"%',
             '%"type": "channel_direct"%')).fetchone()
        if row:
            return int(row['owner_id'])
        ch = self.get_channel(channel_id)
        return int(ch.get('creator_id') or 0) if ch else 0

    def ensure_channel_messages_for_member(self, channel_id: int,
                                           user_id: int) -> None:
        if not self.is_channel_member(channel_id, user_id):
            return
        ch = self.get_channel(channel_id)
        if not ch:
            return
        source_id = self._channel_message_source_owner(channel_id)
        channel_top = int(ch.get('top_message_id') or 0)
        channel_pts = int(ch.get('pts') or 0)
        now = int(time.time())
        direct_patterns = (
            '%"type":"channel_direct"%',
            '%"type": "channel_direct"%',
        )

        if not source_id:
            if channel_top > 0:
                self._upsert_dialog(user_id, channel_id, channel_top, now,
                                    False, 0)
                self._conn.execute(
                    '''UPDATE channel_member_state SET pts=?
                       WHERE channel_id=? AND user_id=?''',
                    (channel_pts, channel_id, user_id))
                self._conn.commit()
            return

        src_count = int(self._conn.execute(
            '''SELECT COUNT(*) AS c FROM messages
               WHERE owner_id=? AND peer_id=?
                 AND (action_json IS NULL OR action_json = ''
                      OR (action_json NOT LIKE ?
                          AND action_json NOT LIKE ?))''',
            (source_id, channel_id, *direct_patterns)).fetchone()['c'])
        dst_count = int(self._conn.execute(
            '''SELECT COUNT(*) AS c FROM messages
               WHERE owner_id=? AND peer_id=?
                 AND (action_json IS NULL OR action_json = ''
                      OR (action_json NOT LIKE ?
                          AND action_json NOT LIKE ?))''',
            (user_id, channel_id, *direct_patterns)).fetchone()['c'])

        if src_count > 0 and dst_count >= src_count:
            self.repair_dialog_top_message(user_id, channel_id)
            return

        if src_count > 0:
            rows = self._conn.execute(
                '''SELECT * FROM messages
                   WHERE owner_id=? AND peer_id=?
                     AND (action_json IS NULL OR action_json = ''
                          OR (action_json NOT LIKE ?
                              AND action_json NOT LIKE ?))
                   ORDER BY id ASC''',
                (source_id, channel_id, *direct_patterns)).fetchall()
            for row in rows:
                self._conn.execute(
                    '''INSERT OR IGNORE INTO messages
                       (owner_id, id, from_id, peer_id, date, text, entities, pts,
                        reply_to_msg_id, media_document_id, media_photo_id,
                        action_json, paid_message_stars, media_poll_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (user_id, row['id'], row['from_id'], channel_id, row['date'],
                     row['text'], row['entities'], row['pts'],
                     row['reply_to_msg_id'], row['media_document_id'],
                     row['media_photo_id'], row['action_json'],
                     row['paid_message_stars'], row['media_poll_json']))
            top_id = channel_top or int(rows[-1]['id'])
            unread = max(0, len(rows) - 1)
            self._upsert_dialog(user_id, channel_id, top_id, now, False, unread)
            self._conn.execute(
                '''UPDATE channel_member_state SET pts=?, unread_count=?
                   WHERE channel_id=? AND user_id=?''',
                (channel_pts, unread, channel_id, user_id))
            self._conn.commit()
            return

        if channel_top > 0:
            self._upsert_dialog(user_id, channel_id, channel_top, now, False, 0)
            self._conn.execute(
                '''UPDATE channel_member_state SET pts=?
                   WHERE channel_id=? AND user_id=?''',
                (channel_pts, channel_id, user_id))
            self._conn.commit()

    def get_message_views(self, channel_id: int, msg_id: int) -> dict:
        row = self._conn.execute(
            '''SELECT views, forwards FROM channel_message_views
               WHERE channel_id=? AND msg_id=?''',
            (channel_id, msg_id)).fetchone()
        if row:
            return {'views': int(row['views']), 'forwards': int(row['forwards'])}
        return {'views': 0, 'forwards': 0}

    def get_user_poll_vote(self, peer_id: int, msg_id: int,
                           user_id: int) -> Optional[List[str]]:
        row = self._conn.execute(
            '''SELECT options_json FROM poll_votes
               WHERE peer_id=? AND msg_id=? AND user_id=?''',
            (peer_id, msg_id, user_id)).fetchone()
        if not row:
            return None
        return json.loads(row['options_json'])

    def list_poll_votes(self, peer_id: int, msg_id: int) -> List[dict]:
        rows = self._conn.execute(
            '''SELECT user_id, options_json FROM poll_votes
               WHERE peer_id=? AND msg_id=?''',
            (peer_id, msg_id)).fetchall()
        return [
            {'user_id': int(r['user_id']),
             'options': json.loads(r['options_json'])}
            for r in rows
        ]

    def update_message_poll(self, owner_id: int, peer_id: int, msg_id: int,
                            *, media_poll_json: str,
                            text: Optional[str] = None) -> Optional[dict]:
        row = self._conn.execute(
            '''SELECT * FROM messages
               WHERE owner_id=? AND id=? AND peer_id=?''',
            (int(owner_id), int(msg_id), int(peer_id))).fetchone()
        if not row:
            return None
        if text is not None:
            self._conn.execute(
                '''UPDATE messages SET media_poll_json=?, text=?
                   WHERE owner_id=? AND id=? AND peer_id=?''',
                (media_poll_json or '', text,
                 int(owner_id), int(msg_id), int(peer_id)))
        else:
            self._conn.execute(
                '''UPDATE messages SET media_poll_json=?
                   WHERE owner_id=? AND id=? AND peer_id=?''',
                (media_poll_json or '',
                 int(owner_id), int(msg_id), int(peer_id)))
        self._conn.commit()
        updated = self._conn.execute(
            '''SELECT * FROM messages
               WHERE owner_id=? AND id=? AND peer_id=?''',
            (int(owner_id), int(msg_id), int(peer_id))).fetchone()
        return dict(updated) if updated else None

    def update_channel_poll_message(self, channel_id: int, msg_id: int,
                                    *, media_poll_json: str,
                                    text: Optional[str] = None) -> None:
        source_id = self._channel_message_source_owner(channel_id)
        owner_ids = {int(source_id)} if source_id else set()
        rows = self._conn.execute(
            '''SELECT DISTINCT owner_id FROM messages
               WHERE peer_id=? AND id=?''',
            (int(channel_id), int(msg_id))).fetchall()
        for row in rows:
            owner_ids.add(int(row['owner_id']))
        for oid in owner_ids:
            if oid <= 0:
                continue
            self.update_message_poll(
                oid, channel_id, msg_id,
                media_poll_json=media_poll_json, text=text)

    def cast_poll_vote(self, peer_id: int, msg_id: int, user_id: int,
                       option_hexes: List[str], poll: dict) -> None:
        if poll.get('closed'):
            raise ValueError('MESSAGE_POLL_CLOSED')
        valid = {a['option'].lower(): a['option'] for a in poll.get('answers') or []}
        normalized = []
        for opt in option_hexes:
            key = opt.lower()
            if key not in valid:
                raise ValueError('OPTION_INVALID')
            normalized.append(valid[key])
        if not poll.get('multiple_choice') and len(option_hexes) > 1:
            raise ValueError('OPTIONS_TOO_MUCH')
        existing = self.get_user_poll_vote(peer_id, msg_id, user_id)
        if existing and poll.get('quiz'):
            raise ValueError('REVOTE_NOT_ALLOWED')
        if existing and poll.get('revoting_disabled'):
            raise ValueError('REVOTE_NOT_ALLOWED')
        now = int(time.time())
        self._conn.execute(
            '''INSERT OR REPLACE INTO poll_votes
               (peer_id, msg_id, user_id, options_json, voted_at)
               VALUES (?,?,?,?,?)''',
            (peer_id, msg_id, user_id,
             json.dumps(normalized, ensure_ascii=False), now))
        self._conn.commit()

    def increment_message_views(self, channel_id: int,
                                msg_ids: List[int],
                                user_id: Optional[int] = None) -> None:
        for mid in msg_ids:
            mid = int(mid)
            if user_id is not None:
                cur = self._conn.execute(
                    '''INSERT OR IGNORE INTO channel_message_viewers
                       (channel_id, msg_id, user_id) VALUES (?,?,?)''',
                    (channel_id, mid, int(user_id)))
                if cur.rowcount == 0:
                    continue
            row = self._conn.execute(
                '''SELECT views, forwards FROM channel_message_views
                   WHERE channel_id=? AND msg_id=?''',
                (channel_id, mid)).fetchone()
            if row:
                views = int(row['views']) + 1
                self._conn.execute(
                    '''UPDATE channel_message_views SET views=?
                       WHERE channel_id=? AND msg_id=?''',
                    (views, channel_id, mid))
            else:
                self._conn.execute(
                    '''INSERT INTO channel_message_views
                       (channel_id, msg_id, views, forwards)
                       VALUES (?,?,1,0)''',
                    (channel_id, mid))
        self._conn.commit()

    def init_channel_message_views(self, channel_id: int, msg_id: int) -> None:
        self._conn.execute(
            '''INSERT OR IGNORE INTO channel_message_views
               (channel_id, msg_id, views, forwards) VALUES (?,?,0,0)''',
            (channel_id, msg_id))
        self._conn.commit()

    def can_delete_channel_message(self, channel_id: int, user_id: int,
                                   msg: dict) -> bool:
        if int(msg.get('from_id') or 0) == int(user_id):
            return True
        if self.is_channel_admin(channel_id, user_id):
            return True
        return False

    def delete_channel_messages(self, channel_id: int, msg_ids: List[int],
                                actor_id: int) -> dict:
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        if not self.is_channel_member(channel_id, actor_id):
            raise ValueError('CHANNEL_PRIVATE')
        ids = sorted({int(i) for i in msg_ids if int(i) > 0})
        if not ids:
            raise ValueError('MESSAGE_ID_INVALID')
        for mid in ids:
            msg = self.get_message(actor_id, mid)
            if not msg or int(msg.get('peer_id') or 0) != channel_id:
                raise ValueError('MESSAGE_ID_INVALID')
            if not self.can_delete_channel_message(channel_id, actor_id, msg):
                raise ValueError('CHAT_ADMIN_REQUIRED')
        member_ids = self.list_channel_member_ids(channel_id)
        for mid in ids:
            for uid in member_ids:
                self._conn.execute(
                    '''DELETE FROM messages
                       WHERE owner_id=? AND id=? AND peer_id=?''',
                    (uid, mid, channel_id))
            self._conn.execute(
                '''DELETE FROM channel_message_views
                   WHERE channel_id=? AND msg_id=?''',
                (channel_id, mid))
        top_row = self._conn.execute(
            '''SELECT MAX(id) AS m FROM messages
               WHERE peer_id=? AND owner_id=?''',
            (channel_id, int(ch['creator_id']))).fetchone()
        top_id = int(top_row['m'] or 0) if top_row else 0
        self._conn.execute(
            'UPDATE channels SET top_message_id=? WHERE id=?',
            (top_id, channel_id))
        pts_count = len(ids)
        pts = int(ch.get('pts') or 0)
        for _ in range(pts_count):
            pts = self.increment_channel_pts(channel_id)
        for uid in member_ids:
            self.repair_dialog_top_message(uid, channel_id)
        self._conn.commit()
        return {'pts': pts, 'pts_count': pts_count, 'channel_id': channel_id,
                'msg_ids': ids, 'member_ids': member_ids}

    def sync_channel_member_pts(self, channel_id: int, user_id: int,
                                pts: int) -> None:
        self._conn.execute(
            '''UPDATE channel_member_state SET pts=?
               WHERE channel_id=? AND user_id=?''',
            (int(pts), channel_id, user_id))
        self._conn.commit()

    def join_channel(self, channel_id: int, user_id: int) -> None:
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        now = int(time.time())
        existing = self._conn.execute(
            '''SELECT * FROM channel_members
               WHERE channel_id=? AND user_id=?''',
            (channel_id, user_id)).fetchone()
        if existing and int(existing['left']) == 0:
            self.ensure_channel_messages_for_member(channel_id, user_id)
            return
        if existing:
            self._conn.execute(
                '''UPDATE channel_members
                   SET left=0, date=? WHERE channel_id=? AND user_id=?''',
                (now, channel_id, user_id))
        else:
            self._conn.execute(
                '''INSERT INTO channel_members
                   (channel_id, user_id, is_admin, left, date)
                   VALUES (?,?,0,0,?)''',
                (channel_id, user_id, now))
        self._conn.execute(
            '''INSERT OR IGNORE INTO channel_member_state
               (channel_id, user_id, pts, read_max_id, unread_count)
               VALUES (?,?,0,0,0)''',
            (channel_id, user_id))
        self.ensure_dialog(user_id, channel_id)
        cnt = len(self.list_channel_member_ids(channel_id))
        self._conn.execute(
            'UPDATE channels SET participants_count=? WHERE id=?',
            (cnt, channel_id))
        self._conn.commit()
        self.ensure_channel_messages_for_member(channel_id, user_id)

    def increment_channel_pts(self, channel_id: int) -> int:
        self._conn.execute(
            'UPDATE channels SET pts = pts + 1 WHERE id=?', (channel_id,))
        self._conn.commit()
        return int(self.get_channel(channel_id)['pts'])

    def _channel_send_result(self, channel_id: int, msg_id: int,
                             sender_id: int) -> dict:
        ch = self.get_channel(channel_id)
        pts_channel = int(ch['pts']) if ch else 0
        member_ids = self.list_channel_member_ids(channel_id)
        result_msgs = {}
        for owner_id in member_ids:
            msg = self.get_message(owner_id, msg_id)
            if msg:
                result_msgs[owner_id] = msg
        return {
            'id': msg_id,
            'pts_channel': pts_channel,
            'sender_msg': result_msgs.get(sender_id),
            'member_msgs': result_msgs,
            'member_ids': member_ids,
        }

    def send_channel_message(self, channel_id: int, sender_id: int,
                             text: str,
                             entities: Optional[List[dict]] = None,
                             reply_to_msg_id: int = 0,
                             media_document_id: int = 0,
                             media_photo_id: int = 0,
                             action_json: str = '',
                             media_poll_json: str = '',
                             random_id: int = 0) -> dict:
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        if not self.can_post_to_channel(channel_id, sender_id):
            raise ValueError('CHAT_ADMIN_REQUIRED')
        if not self.is_channel_member(channel_id, sender_id):
            raise ValueError('USER_NOT_PARTICIPANT')

        if random_id:
            row = self._conn.execute(
                'SELECT channel_id, msg_id FROM channel_send_random_ids '
                'WHERE random_id=? AND sender_id=?',
                (random_id, sender_id)).fetchone()
            if row and int(row['channel_id']) == channel_id:
                return self._channel_send_result(
                    channel_id, int(row['msg_id']), sender_id)

        entities = entities or []
        msg_id = self._allocate_message_id()
        now = self._next_message_date(sender_id, channel_id)
        member_ids = self.list_channel_member_ids(channel_id)
        pts_channel = self.increment_channel_pts(channel_id)
        entities_json = json.dumps(entities, ensure_ascii=False)

        result_msgs = {}
        for owner_id in member_ids:
            result_msgs[owner_id] = self._insert_channel_mailbox_message(
                owner_id=owner_id, msg_id=msg_id, from_id=sender_id,
                channel_id=channel_id, now=now, text=text,
                entities_json=entities_json, pts_channel=pts_channel,
                reply_to_msg_id=reply_to_msg_id,
                media_document_id=media_document_id,
                media_photo_id=media_photo_id,
                action_json=action_json or '',
                media_poll_json=media_poll_json or '')
            unread_delta = 0 if owner_id == sender_id else 1
            self._upsert_dialog(
                owner_id, channel_id, msg_id, now,
                owner_id == sender_id, unread_delta)
            self._conn.execute(
                '''UPDATE channel_member_state SET pts=?, unread_count=unread_count+?
                   WHERE channel_id=? AND user_id=?''',
                (pts_channel, unread_delta, channel_id, owner_id))

        self._conn.execute(
            'UPDATE channels SET top_message_id=? WHERE id=?',
            (msg_id, channel_id))
        self.init_channel_message_views(channel_id, msg_id)
        if random_id:
            self._conn.execute(
                'INSERT OR IGNORE INTO channel_send_random_ids '
                '(random_id, sender_id, channel_id, msg_id) VALUES (?,?,?,?)',
                (random_id, sender_id, channel_id, msg_id))
        self._conn.commit()
        return {
            'id': msg_id,
            'pts_channel': pts_channel,
            'sender_msg': result_msgs.get(sender_id),
            'member_msgs': result_msgs,
            'member_ids': member_ids,
        }

    def send_channel_direct_message(self, channel_id: int, sender_id: int,
                                    text: str,
                                    entities: Optional[List[dict]] = None,
                                    reply_to_msg_id: int = 0,
                                    media_document_id: int = 0,
                                    media_photo_id: int = 0,
                                    media_poll_json: str = '') -> dict:
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        if not self.is_channel_member(channel_id, sender_id):
            raise ValueError('CHANNEL_PRIVATE')

        entities = entities or []
        msg_id = self._allocate_message_id()
        now = self._next_message_date(sender_id, channel_id)
        admin_ids = self.list_channel_admin_ids(channel_id)
        recipient_ids: List[int] = []
        for uid in [sender_id, *admin_ids]:
            uid = int(uid)
            if uid not in recipient_ids and self.is_channel_member(channel_id, uid):
                recipient_ids.append(uid)
        if not recipient_ids:
            raise ValueError('CHANNEL_PRIVATE')

        pts_channel = self.increment_channel_pts(channel_id)
        action_json = json.dumps(
            {'type': 'channel_direct', 'sender_id': int(sender_id)},
            ensure_ascii=False, separators=(',', ':'))

        result_msgs = {}
        for owner_id in recipient_ids:
            result_msgs[owner_id] = self._insert_channel_mailbox_message(
                owner_id=owner_id, msg_id=msg_id, from_id=sender_id,
                channel_id=channel_id, now=now, text=text,
                entities_json=json.dumps(entities, ensure_ascii=False),
                pts_channel=pts_channel,
                reply_to_msg_id=reply_to_msg_id,
                media_document_id=media_document_id,
                media_photo_id=media_photo_id,
                action_json=action_json,
                media_poll_json=media_poll_json or '')
            unread_delta = 0 if owner_id == sender_id else 1
            self._upsert_dialog(
                owner_id, channel_id, msg_id, now,
                owner_id == sender_id, unread_delta)
            self._conn.execute(
                '''INSERT OR IGNORE INTO channel_member_state
                   (channel_id, user_id, pts, read_max_id, unread_count)
                   VALUES (?,?,0,0,0)''',
                (channel_id, owner_id))
            self._conn.execute(
                '''UPDATE channel_member_state SET pts=?, unread_count=unread_count+?
                   WHERE channel_id=? AND user_id=?''',
                (pts_channel, unread_delta, channel_id, owner_id))

        self._conn.commit()
        return {
            'id': msg_id,
            'pts_channel': pts_channel,
            'sender_msg': result_msgs.get(sender_id),
            'member_msgs': result_msgs,
            'member_ids': recipient_ids,
        }

    def can_send_direct_to_channel(self, channel_id: int,
                                   user_id: int) -> bool:
        ch = self.get_channel(channel_id)
        if not ch or int(ch.get('megagroup') or 0):
            return False
        if not int(ch.get('broadcast_messages_allowed') or 0):
            return False
        return self.get_channel_monoforum(channel_id) is not None

    def send_monoforum_message(self, monoforum_id: int, sender_id: int,
                               text: str,
                               entities: Optional[List[dict]] = None,
                               reply_to_msg_id: int = 0,
                               media_document_id: int = 0,
                               media_photo_id: int = 0,
                               media_poll_json: str = '',
                               topic_user_id: int = 0,
                               paid_message_stars: int = 0,
                               suggested_post_json: str = '') -> dict:
        mono = self.get_channel(monoforum_id)
        if not mono or not int(mono.get('monoforum') or 0):
            raise ValueError('CHANNEL_INVALID')
        linked = self.get_linked_channel_for_monoforum(monoforum_id)
        if not linked:
            raise ValueError('CHANNEL_INVALID')
        if not int(linked.get('broadcast_messages_allowed') or 0):
            raise ValueError('CHANNEL_PRIVATE')

        is_admin = self.is_channel_admin(int(linked['id']), sender_id)
        if not topic_user_id:
            topic_user_id = sender_id
        topic_user_id = int(topic_user_id)
        if not self.get_user_by_id(topic_user_id):
            raise ValueError('USER_ID_INVALID')
        if not is_admin:
            topic_user_id = sender_id

        msg_paid_stars = int(paid_message_stars or 0)
        if not is_admin:
            required = self.get_channel_direct_message_stars(int(linked['id']))
            if required > 0:
                if msg_paid_stars < required:
                    raise ValueError(f'ALLOW_PAYMENT_REQUIRED_{required}')
                if not config.INFINITE_STARS:
                    if self.get_stars_balance(sender_id) < required:
                        raise ValueError(f'ALLOW_PAYMENT_REQUIRED_{required}')
                self.adjust_stars_balance(
                    sender_id, -required,
                    title='Channel message',
                    peer_user_id=int(linked['id']),
                    description='Direct message to channel')
                msg_paid_stars = required

        from_id = int(linked['id']) if is_admin and topic_user_id != sender_id else int(sender_id)
        entities = entities or []
        msg_id = self._allocate_message_id()
        now = self._next_message_date(sender_id, monoforum_id)
        admin_ids = self.list_channel_admin_ids(int(linked['id']))
        recipient_ids: List[int] = []
        for uid in [topic_user_id, *admin_ids]:
            uid = int(uid)
            if uid not in recipient_ids and self.get_user_by_id(uid):
                recipient_ids.append(uid)
        if sender_id not in recipient_ids:
            recipient_ids.append(int(sender_id))
        if not recipient_ids:
            raise ValueError('CHANNEL_PRIVATE')

        for uid in admin_ids:
            self._conn.execute(
                '''INSERT OR IGNORE INTO channel_members
                   (channel_id, user_id, is_admin, left, date)
                   VALUES (?,?,?,?,?)''',
                (monoforum_id, int(uid), 1, 0, now))
        pts_channel = self.increment_channel_pts(monoforum_id)
        action_json = json.dumps(
            {
                'type': 'monoforum_direct',
                'sender_id': int(sender_id),
                'topic_user_id': int(topic_user_id),
                'linked_channel_id': int(linked['id']),
            },
            ensure_ascii=False, separators=(',', ':'))

        result_msgs = {}
        for owner_id in recipient_ids:
            pts = self._insert_message(
                msg_id, owner_id, from_id, monoforum_id, text, entities, now,
                reply_to_msg_id, media_document_id, media_photo_id,
                action_json=action_json, media_poll_json=media_poll_json,
                saved_peer_id=topic_user_id,
                paid_message_stars=msg_paid_stars if owner_id == sender_id else 0,
                suggested_post_json=suggested_post_json)
            unread_delta = 0 if owner_id == sender_id else 1
            self._upsert_dialog(
                owner_id, monoforum_id, msg_id, now,
                owner_id == sender_id, unread_delta)
            self._conn.execute(
                '''INSERT OR IGNORE INTO channel_member_state
                   (channel_id, user_id, pts, read_max_id, unread_count)
                   VALUES (?,?,0,0,0)''',
                (monoforum_id, owner_id))
            self._conn.execute(
                '''UPDATE channel_member_state SET pts=?, unread_count=unread_count+?
                   WHERE channel_id=? AND user_id=?''',
                (pts_channel, unread_delta, monoforum_id, owner_id))
            result_msgs[owner_id] = self.get_message(owner_id, msg_id)

        self._conn.execute(
            'UPDATE channels SET top_message_id=? WHERE id=?',
            (msg_id, monoforum_id))
        self._conn.commit()
        return {
            'id': msg_id,
            'pts_channel': pts_channel,
            'sender_msg': result_msgs.get(sender_id),
            'member_msgs': result_msgs,
            'member_ids': recipient_ids,
            'monoforum_id': monoforum_id,
            'linked_channel_id': int(linked['id']),
            'topic_user_id': int(topic_user_id),
        }

    def approve_suggested_post(self, monoforum_id: int, msg_id: int,
                               admin_id: int, *,
                               reject: bool = False,
                               schedule_date: int = 0,
                               reject_comment: str = '') -> dict:
        mono = self.get_channel(monoforum_id)
        if not mono or not int(mono.get('monoforum') or 0):
            raise ValueError('CHANNEL_INVALID')
        linked = self.get_linked_channel_for_monoforum(monoforum_id)
        if not linked or not self.is_channel_admin(int(linked['id']), admin_id):
            raise ValueError('CHAT_ADMIN_REQUIRED')
        src = self.get_message(admin_id, msg_id)
        if not src or int(src.get('peer_id') or 0) != monoforum_id:
            raise ValueError('MESSAGE_ID_INVALID')

        suggested: dict = {}
        raw = src.get('suggested_post_json') or ''
        if raw:
            try:
                suggested = json.loads(raw)
            except json.JSONDecodeError:
                suggested = {}
        if schedule_date:
            suggested['schedule_date'] = int(schedule_date)
        if reject:
            suggested['rejected'] = True
            suggested.pop('accepted', None)
            if reject_comment:
                suggested['reject_comment'] = reject_comment
        else:
            suggested['accepted'] = True
            suggested.pop('rejected', None)

        entities = src.get('entities') or []
        if isinstance(entities, str):
            try:
                entities = json.loads(entities)
            except json.JSONDecodeError:
                entities = []

        channel_result = None
        channel_id = int(linked['id'])
        if not reject:
            channel_result = self.send_channel_message(
                channel_id, admin_id,
                src.get('text') or '',
                entities,
                int(src.get('reply_to_msg_id') or 0),
                int(src.get('media_document_id') or 0),
                int(src.get('media_photo_id') or 0),
                media_poll_json=src.get('media_poll_json') or '')

        payload = json.dumps(suggested, ensure_ascii=False, separators=(',', ':'))
        self._conn.execute(
            '''UPDATE messages SET suggested_post_json=?
               WHERE peer_id=? AND id=?''',
            (payload, monoforum_id, msg_id))

        action = {
            'type': 'suggested_post_approval',
            'rejected': reject,
            'schedule_date': int(schedule_date or 0),
            'reject_comment': reject_comment,
            'price_stars': int(suggested.get('price_stars') or 0),
        }
        service_result = self.send_channel_message(
            monoforum_id, admin_id, '', [], 0, 0, 0,
            action_json=json.dumps(action, ensure_ascii=False))

        updated_msg = self.get_message(admin_id, msg_id) or {}
        return {
            'updated_msg': updated_msg,
            'monoforum_id': monoforum_id,
            'linked_channel_id': channel_id,
            'channel_result': channel_result,
            'service_result': service_result,
            'pts_monoforum': int(service_result['pts_channel']),
        }

    def update_suggested_post_status(self, monoforum_id: int, msg_id: int,
                                     admin_id: int, *,
                                     accepted: bool = False,
                                     rejected: bool = False,
                                     schedule_date: int = 0,
                                     reject_comment: str = '') -> dict:
        result = self.approve_suggested_post(
            monoforum_id, msg_id, admin_id,
            reject=rejected,
            schedule_date=schedule_date,
            reject_comment=reject_comment)
        return result['updated_msg']

    def list_monoforum_dialogs(self, owner_id: int,
                               monoforum_id: int,
                               limit: int = 50) -> List[dict]:
        limit = max(1, min(int(limit or 50), 100))
        rows = self._conn.execute(
            '''SELECT saved_peer_id AS peer_id,
                      MAX(id) AS top_message_id,
                      MAX(date) AS last_message_date,
                      SUM(CASE WHEN from_id != ? THEN 1 ELSE 0 END) AS unread_count
               FROM messages
               WHERE owner_id=? AND peer_id=? AND saved_peer_id>0
               GROUP BY saved_peer_id
               ORDER BY MAX(date) DESC, MAX(id) DESC
               LIMIT ?''',
            (owner_id, owner_id, monoforum_id, limit)).fetchall()
        dialogs = []
        for row in rows:
            top_id = int(row['top_message_id'] or 0)
            dialogs.append({
                'peer_id': int(row['peer_id']),
                'top_message_id': top_id,
                'read_inbox_max_id': top_id,
                'read_outbox_max_id': top_id,
                'unread_count': int(row['unread_count'] or 0),
                'last_message_date': int(row['last_message_date'] or 0),
            })
        return dialogs

    def get_monoforum_topic_history(self, owner_id: int,
                                    monoforum_id: int,
                                    topic_user_id: int,
                                    offset_id: int,
                                    add_offset: int,
                                    limit: int,
                                    max_id: int = 0,
                                    min_id: int = 0) -> tuple:
        limit = max(1, min(int(limit or 50), 100))
        rows = self._conn.execute(
            '''SELECT * FROM messages
               WHERE owner_id=? AND peer_id=? AND saved_peer_id=?
               ORDER BY id DESC''',
            (owner_id, monoforum_id, topic_user_id)).fetchall()
        if max_id > 0:
            rows = [r for r in rows if int(r['id']) < int(max_id)]
        if min_id > 0:
            rows = [r for r in rows if int(r['id']) > int(min_id)]
        total = len(rows)
        if offset_id > 0:
            idx = next(
                (i for i, r in enumerate(rows) if int(r['id']) == offset_id),
                None)
            if idx is None:
                idx = next(
                    (i for i, r in enumerate(rows)
                     if int(r['id']) < offset_id),
                    len(rows))
            rows = rows[idx + add_offset:]
        elif add_offset:
            rows = rows[max(add_offset, 0):]
        rows = rows[:limit]
        return [self._message_row(r) for r in rows], total

    def read_monoforum_topic(self, owner_id: int, monoforum_id: int,
                             topic_user_id: int, max_id: int) -> None:
        top = int(max_id or 0)
        if top <= 0:
            row = self._conn.execute(
                '''SELECT MAX(id) AS id FROM messages
                   WHERE owner_id=? AND peer_id=? AND saved_peer_id=?''',
                (owner_id, monoforum_id, topic_user_id)).fetchone()
            top = int(row['id'] or 0) if row else 0
        self._conn.execute(
            '''UPDATE dialogs
               SET unread_count=0, read_inbox_max_id=?
               WHERE owner_id=? AND peer_id=?''',
            (top, owner_id, monoforum_id))
        self._conn.execute(
            '''UPDATE channel_member_state
               SET unread_count=0, read_max_id=?
               WHERE channel_id=? AND user_id=?''',
            (top, monoforum_id, owner_id))
        self._conn.commit()

    def get_channel_member_state(self, channel_id: int,
                                 user_id: int) -> dict:
        row = self._conn.execute(
            'SELECT * FROM channel_member_state WHERE channel_id=? AND user_id=?',
            (channel_id, user_id)).fetchone()
        if row:
            return dict(row)
        return {'pts': 0, 'read_max_id': 0, 'unread_count': 0}

    def get_channel_messages_since_pts(self, channel_id: int, user_id: int,
                                       pts: int) -> List[dict]:
        if not self.is_channel_member(channel_id, user_id):
            return []
        rows = self._conn.execute(
            '''SELECT m.* FROM messages m
               WHERE m.owner_id=? AND m.peer_id=?
                 AND m.pts > (SELECT COALESCE(
                     (SELECT pts FROM channel_member_state
                      WHERE channel_id=? AND user_id=?), 0))
               ORDER BY m.id ASC''',
            (user_id, channel_id, channel_id, user_id)).fetchall()
        if pts > 0:
            rows = [r for r in rows if int(r['pts']) > pts]
        return [self._message_row(r) for r in rows]

    def ensure_channel_invite_hash(self, channel_id: int) -> str:
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        if ch.get('invite_hash'):
            return str(ch['invite_hash'])
        invite_hash = secrets.token_urlsafe(16).rstrip('=')[:22]
        self._conn.execute(
            'UPDATE channels SET invite_hash=? WHERE id=?',
            (invite_hash, channel_id))
        self._conn.commit()
        return invite_hash

    def get_channel_by_invite_hash(self, invite_hash: str) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM channels WHERE invite_hash=?',
            (invite_hash,)).fetchone()
        return dict(row) if row else None

    def set_channel_title(self, channel_id: int, title: str, *,
                          actor_id: Optional[int] = None) -> dict:
        title = (title or '').strip()
        if not title:
            raise ValueError('CHAT_TITLE_EMPTY')
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        prev_title = ch.get('title') or ''
        self._conn.execute(
            'UPDATE channels SET title=? WHERE id=?', (title, channel_id))
        self._conn.commit()
        if actor_id and prev_title != title:
            self.add_channel_admin_log(
                channel_id, actor_id, 'change_title',
                {'prev': prev_title, 'new': title})
        return self.get_channel(channel_id)

    def get_channel_by_username(self, username: str) -> Optional[dict]:
        username = username.strip().lstrip('@').lower()
        row = self._conn.execute(
            'SELECT * FROM channels WHERE lower(username)=?', (username,)
        ).fetchone()
        if not row:
            return None
        return self.get_channel(int(row['id']))

    def is_channel_username_available(self, username: str,
                                      exclude_channel_id: int = 0) -> bool:
        username = username.strip().lstrip('@').lower()
        if not username:
            return True
        if not is_valid_username(username):
            return False
        if self.get_user_by_username(username):
            return False
        row = self._conn.execute(
            'SELECT id FROM channels WHERE lower(username)=?', (username,)
        ).fetchone()
        if row and int(row['id']) != int(exclude_channel_id):
            return False
        return True

    def set_channel_username(self, channel_id: int, username: str) -> dict:
        username = username.strip().lstrip('@').lower()
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        if username and not self.is_channel_username_available(
                username, exclude_channel_id=channel_id):
            raise ValueError('USERNAME_OCCUPIED')
        self._conn.execute(
            'UPDATE channels SET username=? WHERE id=?',
            (username or None, channel_id))
        self._conn.commit()
        return self.get_channel(channel_id)

    def set_channel_pinned_msg(self, channel_id: int,
                               msg_id: int) -> dict:
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        self._conn.execute(
            'UPDATE channels SET pinned_msg_id=? WHERE id=?',
            (int(msg_id) if msg_id else None, channel_id))
        self._conn.commit()
        return self.get_channel(channel_id)

    def set_channel_paid_messages_price(self, channel_id: int, *,
                                        stars: int,
                                        broadcast_messages_allowed: bool) -> dict:
        ch = self.get_channel(channel_id)
        if not ch:
            raise ValueError('CHANNEL_INVALID')
        stars = max(0, int(stars or 0))
        linked_monoforum_id = int(ch.get('linked_monoforum_id') or 0)
        if broadcast_messages_allowed:
            mono = self.ensure_channel_monoforum(channel_id)
            linked_monoforum_id = int(mono['id'])
            self._conn.execute(
                '''UPDATE channels SET send_paid_messages_stars=?
                   WHERE id=?''',
                (stars, linked_monoforum_id))
        self._conn.execute(
            '''UPDATE channels
               SET send_paid_messages_stars=?,
                   broadcast_messages_allowed=?,
                   linked_monoforum_id=?
               WHERE id=?''',
            (stars, 1 if broadcast_messages_allowed else 0,
             linked_monoforum_id, channel_id))
        self._conn.commit()
        return self.get_channel(channel_id)

    def get_channel_direct_message_stars(self, channel_id: int) -> int:
        ch = self.get_channel(channel_id)
        if not ch:
            return 0
        if int(ch.get('monoforum') or 0):
            linked = self.get_linked_channel_for_monoforum(channel_id)
            if linked:
                return int(linked.get('send_paid_messages_stars') or 0)
            return int(ch.get('send_paid_messages_stars') or 0)
        if int(ch.get('broadcast_messages_allowed') or 0):
            return int(ch.get('send_paid_messages_stars') or 0)
        return 0

    def read_channel_history(self, channel_id: int, user_id: int,
                             max_id: int) -> int:
        if not self.is_channel_member(channel_id, user_id):
            raise ValueError('CHANNEL_PRIVATE')
        ch = self.get_channel(channel_id)
        dialog = self._conn.execute(
            'SELECT top_message_id FROM dialogs WHERE owner_id=? AND peer_id=?',
            (user_id, channel_id)).fetchone()
        top_id = max(
            int(ch.get('top_message_id') or 0),
            int(dialog['top_message_id'] or 0) if dialog else 0)
        read_up_to = min(int(max_id), top_id) if int(max_id) else top_id
        self._conn.execute(
            '''UPDATE dialogs SET unread_count=0, read_inbox_max_id=?
               WHERE owner_id=? AND peer_id=?''',
            (read_up_to, user_id, channel_id))
        self._conn.execute(
            '''UPDATE channel_member_state SET read_max_id=?, unread_count=0
               WHERE channel_id=? AND user_id=?''',
            (read_up_to, channel_id, user_id))
        self._conn.commit()
        return int(ch['pts'])

    def collect_channels_for_dialogs(self, viewer_id: int,
                                     dialogs: List[dict]) -> List[dict]:
        channels = []
        seen = set()
        for d in dialogs:
            pid = int(d['peer_id'])
            if pid < config.CHANNEL_ID_MIN or pid in seen:
                continue
            if not self.is_channel_member(pid, viewer_id):
                continue
            ch = self.get_channel(pid)
            if ch:
                channels.append(ch)
                seen.add(pid)
        return channels

    # ── Star gift auctions ─────────────────────

    def _ensure_auction_state_row(self, gift_id: int) -> dict:
        row = self._conn.execute(
            'SELECT * FROM star_gift_auction_state WHERE gift_id=?',
            (gift_id,)).fetchone()
        if row:
            return dict(row)
        self._conn.execute(
            '''INSERT INTO star_gift_auction_state
               (gift_id, version, last_gift_num, last_processed_round, finished)
               VALUES (?, 1, 0, 0, 0)''',
            (gift_id,))
        self._conn.commit()
        return dict(self._conn.execute(
            'SELECT * FROM star_gift_auction_state WHERE gift_id=?',
            (gift_id,)).fetchone())

    def _bump_auction_version(self, gift_id: int) -> int:
        st = self._ensure_auction_state_row(gift_id)
        version = int(st['version']) + 1
        self._conn.execute(
            'UPDATE star_gift_auction_state SET version=? WHERE gift_id=?',
            (version, gift_id))
        self._conn.commit()
        return version

    def get_star_gift_by_auction_slug(self, slug: str) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM star_gifts WHERE auction_slug=?', (slug,)
        ).fetchone()
        return dict(row) if row else None

    def list_auction_bids(self, gift_id: int, *, active_only: bool = True
                          ) -> List[dict]:
        q = 'SELECT * FROM star_gift_auction_bids WHERE gift_id=?'
        if active_only:
            q += ' AND returned=0'
        q += ' ORDER BY bid_amount DESC, bid_date ASC'
        rows = self._conn.execute(q, (gift_id,)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            try:
                d['message_entities'] = json.loads(
                    d.get('message_entities_json') or '[]')
            except json.JSONDecodeError:
                d['message_entities'] = []
            out.append(d)
        return out

    def get_auction_bid(self, gift_id: int, user_id: int) -> Optional[dict]:
        row = self._conn.execute(
            '''SELECT * FROM star_gift_auction_bids
               WHERE gift_id=? AND user_id=?''',
            (gift_id, user_id)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d['message_entities'] = json.loads(
                d.get('message_entities_json') or '[]')
        except json.JSONDecodeError:
            d['message_entities'] = []
        return d

    def count_auction_acquired(self, gift_id: int, user_id: int) -> int:
        row = self._conn.execute(
            '''SELECT COUNT(*) AS c FROM star_gift_auction_acquired
               WHERE gift_id=? AND user_id=?''',
            (gift_id, user_id)).fetchone()
        return int(row['c'])

    def _auction_timing(self, gift: dict) -> dict:
        now = int(time.time())
        start = int(gift.get('auction_start_date') or now)
        end = int(gift.get('auction_end_date') or start)
        round_dur = max(1, int(gift.get('auction_round_duration') or 300))
        total_rounds = max(1, int(gift.get('auction_total_rounds') or 1))
        if end <= start:
            end = start + round_dur * total_rounds
        if now < start:
            current_round = 1
            next_round_at = start + round_dur
        else:
            elapsed = now - start
            current_round = min(
                total_rounds,
                max(1, elapsed // round_dur + 1))
            next_round_at = start + current_round * round_dur
            if next_round_at > end:
                next_round_at = end
        finished = now >= end
        return {
            'now': now,
            'start_date': start,
            'end_date': end,
            'round_duration': round_dur,
            'total_rounds': total_rounds,
            'current_round': current_round,
            'next_round_at': next_round_at,
            'finished': finished,
            'gifts_per_round': max(1, int(gift.get('gifts_per_round') or 1)),
            'total_gifts': max(1, int(gift.get('availability_total') or 1)),
        }

    def _return_auction_bid(self, gift: dict, bid: dict) -> None:
        if int(bid.get('returned') or 0):
            return
        amount = int(bid['bid_amount'])
        self._conn.execute(
            'UPDATE star_gift_auction_bids SET returned=1 WHERE id=?',
            (bid['id'],))
        self.adjust_stars_balance(
            int(bid['user_id']), amount, refund=True,
            stargift_auction_bid=True,
            peer_user_id=int(bid['peer_id']),
            title=gift.get('title') or 'Auction refund',
            description='Bid returned')
        self._bump_auction_version(int(gift['gift_id']))

    def _deliver_auction_won_gift(self, gift: dict, bid: dict, *,
                                  round_num: int, position: int,
                                  gift_num: int) -> dict:
        sender_id = int(bid['user_id'])
        recipient_id = int(bid['peer_id'])
        gift_id = int(gift['gift_id'])
        entities = bid.get('message_entities') or []
        if isinstance(bid.get('message_entities_json'), str):
            try:
                entities = json.loads(bid['message_entities_json'])
            except json.JSONDecodeError:
                entities = []

        msg_id = self._allocate_message_id()
        now = self._next_message_date(sender_id, recipient_id)
        convert_stars = int(gift.get('convert_stars') or 0)
        can_upgrade = self.gift_can_upgrade(gift_id)
        hide_name = bool(int(bid.get('hide_name') or 0))

        saved = self.insert_saved_star_gift({
            'owner_user_id': recipient_id,
            'from_user_id': sender_id,
            'gift_id': gift_id,
            'msg_id': msg_id,
            'stars': int(bid['bid_amount']),
            'convert_stars': convert_stars,
            'upgrade_stars': None,
            'prepaid_upgrade': 0,
            'name_hidden': 1 if hide_name else 0,
            'date': now,
            'message_text': bid.get('message_text') or '',
            'message_entities_json': json.dumps(
                entities, ensure_ascii=False),
            'can_upgrade': 1 if can_upgrade else 0,
        })

        bid_amount = int(bid['bid_amount'])
        upgrade_price = (
            self.get_gift_upgrade_stars(gift_id) if can_upgrade else 0)
        base_action = {
            'type': 'star_gift',
            'gift_id': gift_id,
            'hide_name': hide_name,
            'name_hidden': hide_name,
            'message_text': bid.get('message_text') or '',
            'message_entities': entities,
            'convert_stars': convert_stars,
            'can_upgrade': can_upgrade,
            'prepaid_upgrade': False,
            'upgrade_stars': int(upgrade_price) if upgrade_price else None,
            'auction_acquired': True,
            'gift_num': gift_num,
            'to_id': recipient_id,
            'bid_amount': bid_amount,
        }

        if sender_id == recipient_id:
            action = dict(
                base_action,
                saved=True,
                peer_id=recipient_id,
                saved_id=msg_id,
                gift_msg_id=msg_id,
            )
            pts = self._insert_message(
                msg_id, sender_id, sender_id, sender_id, '', [], now,
                action_json=json.dumps(action, ensure_ascii=False))
            self._upsert_dialog(sender_id, sender_id, msg_id, now, True, 0)
            self._conn.commit()
            msg = self.get_message(sender_id, msg_id)
            delivery = {
                'saved': saved,
                'msg_id': msg_id,
                'sender_msg': msg,
                'recipient_msg': msg,
                'pts_sender': pts,
                'pts_recipient': pts,
            }
        else:
            sender_action = dict(base_action, gift_msg_id=msg_id)
            recipient_action = dict(
                base_action,
                saved=True,
                peer_id=recipient_id,
                saved_id=msg_id,
                gift_msg_id=msg_id,
            )
            pts_sender = self._insert_message(
                msg_id, sender_id, sender_id, recipient_id, '', [], now,
                action_json=json.dumps(sender_action, ensure_ascii=False))
            pts_recipient = self._insert_message(
                msg_id, recipient_id, sender_id, sender_id, '', [], now,
                action_json=json.dumps(recipient_action, ensure_ascii=False))
            self._upsert_dialog(sender_id, recipient_id, msg_id, now, True, 0)
            self._upsert_dialog(recipient_id, sender_id, msg_id, now, False, 1)
            self._conn.commit()
            delivery = {
                'saved': saved,
                'msg_id': msg_id,
                'sender_msg': self.get_message(sender_id, msg_id),
                'recipient_msg': self.get_message(recipient_id, msg_id),
                'pts_sender': pts_sender,
                'pts_recipient': pts_recipient,
            }

        self._conn.execute(
            '''INSERT INTO star_gift_auction_acquired
               (gift_id, user_id, recipient_id, bid_amount, round_num,
                position, gift_num, date, name_hidden, message_text,
                message_entities_json, saved_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (gift_id, sender_id, recipient_id, bid_amount,
             round_num, position, gift_num, now,
             1 if hide_name else 0,
             bid.get('message_text') or '',
             json.dumps(entities, ensure_ascii=False),
             msg_id))
        self._conn.commit()
        return delivery

    def _auction_process_result(self, gift_id: int, *,
                                changed: bool = False,
                                winners: Optional[List[dict]] = None,
                                finished: bool = False) -> dict:
        row = self._conn.execute(
            'SELECT * FROM star_gift_auction_state WHERE gift_id=?',
            (gift_id,)).fetchone()
        st = dict(row) if row else self._ensure_auction_state_row(gift_id)
        return {
            'state': st,
            'changed': changed,
            'winners': winners or [],
            'finished': finished,
        }

    def list_active_auction_gift_ids(self) -> List[int]:
        now = int(time.time())
        rows = self._conn.execute(
            '''SELECT gift_id FROM star_gifts
               WHERE is_auction=1
                 AND COALESCE(auction_end_date, 0) > ?''',
            (now - 3600,)).fetchall()
        return [int(r['gift_id']) for r in rows]

    def process_auction_rounds(self, gift_id: int) -> dict:
        gift = self.get_star_gift(gift_id)
        if not gift or not int(gift.get('is_auction') or 0):
            st = self._ensure_auction_state_row(gift_id)
            return {'state': st, 'changed': False, 'winners': [], 'finished': False}

        timing = self._auction_timing(gift)
        st = self._ensure_auction_state_row(gift_id)
        if int(st.get('finished') or 0):
            return self._auction_process_result(gift_id, finished=True)

        gifts_per_round = timing['gifts_per_round']
        start = timing['start_date']
        now = timing['now']
        total_rounds = timing['total_rounds']
        last_processed = int(st.get('last_processed_round') or 0)

        completed_round = 0
        if now >= start:
            completed_round = min(
                total_rounds,
                (now - start) // timing['round_duration'])

        last_gift_num = int(st.get('last_gift_num') or 0)
        winners_delivered: List[dict] = []
        changed = False

        for rnd in range(last_processed + 1, completed_round + 1):
            if last_gift_num >= timing['total_gifts']:
                break
            bids = self.list_auction_bids(gift_id, active_only=True)
            round_winners = bids[:gifts_per_round]
            for pos, bid in enumerate(round_winners, 1):
                if last_gift_num >= timing['total_gifts']:
                    break
                last_gift_num += 1
                delivery = self._deliver_auction_won_gift(
                    gift, bid,
                    round_num=rnd,
                    position=pos,
                    gift_num=last_gift_num)
                self._conn.execute(
                    'DELETE FROM star_gift_auction_bids WHERE id=?',
                    (bid['id'],))
                winners_delivered.append({
                    'user_id': int(bid['user_id']),
                    'peer_id': int(bid['peer_id']),
                    'bid_amount': int(bid['bid_amount']),
                    **delivery,
                })
            changed = True

        gifts_left = max(0, timing['total_gifts'] - last_gift_num)
        self._conn.execute(
            '''UPDATE star_gifts SET availability_remains=?
               WHERE gift_id=?''',
            (gifts_left, gift_id))

        if completed_round > last_processed:
            self._conn.execute(
                '''UPDATE star_gift_auction_state
                   SET last_processed_round=?, last_gift_num=?
                   WHERE gift_id=?''',
                (completed_round, last_gift_num, gift_id))
            self._bump_auction_version(gift_id)
            changed = True

        finished_now = False
        sold_out = gifts_left <= 0
        if (timing['finished'] or sold_out) and not int(st.get('finished') or 0):
            bids = self.list_auction_bids(gift_id, active_only=True)
            gifts_left = max(0, timing['total_gifts'] - last_gift_num)
            for i, bid in enumerate(bids):
                if i >= gifts_left:
                    self._return_auction_bid(gift, bid)
            avg = 0
            rows = self._conn.execute(
                '''SELECT AVG(bid_amount) AS a FROM star_gift_auction_acquired
                   WHERE gift_id=?''',
                (gift_id,)).fetchone()
            if rows and rows['a']:
                avg = int(rows['a'])
            self._conn.execute(
                '''UPDATE star_gift_auction_state
                   SET finished=1, average_price=?, last_gift_num=?
                   WHERE gift_id=?''',
                (avg, last_gift_num, gift_id))
            self._bump_auction_version(gift_id)
            changed = True
            finished_now = True

        self._conn.commit()
        return self._auction_process_result(
            gift_id,
            changed=changed,
            winners=winners_delivered,
            finished=finished_now or bool(int(st.get('finished') or 0)),
        )

    def build_auction_runtime(self, gift_id: int, *,
                              state: Optional[dict] = None) -> Optional[dict]:
        gift = self.get_star_gift(gift_id)
        if not gift or not int(gift.get('is_auction') or 0):
            return None
        if state is None:
            row = self._conn.execute(
                'SELECT * FROM star_gift_auction_state WHERE gift_id=?',
                (gift_id,)).fetchone()
            st = dict(row) if row else self._ensure_auction_state_row(gift_id)
        else:
            st = state
        timing = self._auction_timing(gift)
        bids = self.list_auction_bids(gift_id, active_only=True)
        gifts_left = max(0, int(gift.get('availability_remains') or 0))
        if gifts_left <= 0 and not timing['finished']:
            gifts_left = max(
                0,
                int(gift.get('availability_total') or 0)
                - int(st.get('last_gift_num') or 0))
        return {
            'gift': gift,
            'state': st,
            'timing': timing,
            'bids': bids,
            'gifts_left': gifts_left,
            'min_bid_amount': int(
                gift.get('auction_min_bid') or gift.get('stars') or 1),
        }

    def get_auction_runtime(self, gift_id: int) -> Optional[dict]:
        gift = self.get_star_gift(gift_id)
        if not gift or not int(gift.get('is_auction') or 0):
            return None
        proc = self.process_auction_rounds(gift_id)
        return self.build_auction_runtime(gift_id, state=proc['state'])

    def place_auction_bid(self, user_id: int, gift_id: int, *,
                          peer_id: int,
                          bid_amount: int,
                          hide_name: bool = False,
                          update_bid: bool = False,
                          message_text: str = '',
                          message_entities: Optional[List[dict]] = None
                          ) -> dict:
        gift = self.get_star_gift(gift_id)
        if not gift or not int(gift.get('is_auction') or 0):
            raise ValueError('STARGIFT_INVALID')

        timing = self._auction_timing(gift)
        if timing['finished'] or timing['now'] >= timing['end_date']:
            raise ValueError('AUCTION_FINISHED')
        st = self._ensure_auction_state_row(gift_id)
        if int(st.get('finished') or 0):
            raise ValueError('AUCTION_FINISHED')
        if int(gift.get('availability_remains') or 0) <= 0:
            raise ValueError('AUCTION_FINISHED')

        min_bid = int(gift.get('auction_min_bid') or gift.get('stars') or 1)
        recipient = self.get_user_by_id(peer_id) if peer_id else None
        if not update_bid and not recipient:
            raise ValueError('PEER_ID_INVALID')

        bid_amount = int(bid_amount)
        existing = self.get_auction_bid(gift_id, user_id)
        entities = message_entities or []

        if existing and int(existing.get('returned') or 0):
            existing = None

        if existing:
            if not update_bid:
                raise ValueError('BID_ALREADY_PLACED')
            old_amount = int(existing['bid_amount'])
            min_user = old_amount + 1
            if bid_amount < min_user:
                raise ValueError('BID_TOO_LOW')
            delta = bid_amount - old_amount
            if delta > 0 and not config.INFINITE_STARS:
                balance = self.get_stars_balance(user_id)
                if balance < delta:
                    raise ValueError('BALANCE_TOO_LOW')
            if delta > 0:
                self.adjust_stars_balance(
                    user_id, -delta,
                    stargift_auction_bid=True,
                    peer_user_id=int(existing['peer_id']),
                    title=gift.get('title') or 'Auction bid',
                    description='Bid increase')
            now = int(time.time())
            self._conn.execute(
                '''UPDATE star_gift_auction_bids
                   SET bid_amount=?, bid_date=?
                   WHERE gift_id=? AND user_id=?''',
                (bid_amount, now, gift_id, user_id))
        else:
            if update_bid:
                raise ValueError('BID_NOT_FOUND')
            if not peer_id:
                raise ValueError('PEER_ID_INVALID')
            if bid_amount < min_bid:
                raise ValueError('BID_TOO_LOW')
            if not config.INFINITE_STARS:
                balance = self.get_stars_balance(user_id)
                if balance < bid_amount:
                    raise ValueError('BALANCE_TOO_LOW')
            self.adjust_stars_balance(
                user_id, -bid_amount,
                stargift_auction_bid=True,
                peer_user_id=peer_id,
                title=gift.get('title') or 'Auction bid',
                description='New bid')
            now = int(time.time())
            self._conn.execute(
                '''INSERT INTO star_gift_auction_bids
                   (gift_id, user_id, bid_amount, bid_date, peer_type, peer_id,
                    hide_name, message_text, message_entities_json, returned)
                   VALUES (?,?,?,?,0,?,?,?,?,0)''',
                (gift_id, user_id, bid_amount, now, peer_id,
                 1 if hide_name else 0,
                 message_text or '',
                 json.dumps(entities, ensure_ascii=False)))

        self._bump_auction_version(gift_id)
        self._conn.commit()
        return self.get_auction_runtime(gift_id)

    def refresh_auction_schedule(self, gift_id: int) -> Optional[dict]:
        """Reset auction window to start soon; clear bids and runtime."""
        gift = self.get_star_gift(gift_id)
        if not gift or not int(gift.get('is_auction') or 0):
            return None
        total_rounds = max(1, int(gift.get('auction_total_rounds') or 1))
        round_dur = max(1, int(gift.get('auction_round_duration') or 300))
        total_gifts = int(gift.get('availability_total') or 0)
        if total_gifts <= 0:
            gpr = max(1, int(gift.get('gifts_per_round') or 1))
            total_gifts = gpr * total_rounds
        start = int(time.time()) + 30
        end = start + round_dur * total_rounds
        self._conn.execute(
            '''UPDATE star_gifts SET auction_start_date=?, auction_end_date=?,
               availability_total=?, availability_remains=?
               WHERE gift_id=?''',
            (start, end, total_gifts, total_gifts, gift_id))
        self._conn.execute(
            'DELETE FROM star_gift_auction_state WHERE gift_id=?', (gift_id,))
        self._conn.execute(
            'DELETE FROM star_gift_auction_bids WHERE gift_id=?', (gift_id,))
        self._conn.execute(
            'DELETE FROM star_gift_auction_acquired WHERE gift_id=?', (gift_id,))
        self._ensure_auction_state_row(gift_id)
        self._conn.commit()
        self.recompute_star_gifts_catalog_hash()
        return self.get_star_gift(gift_id)

    def delete_star_gift(self, gift_id: int) -> None:
        gid = int(gift_id)
        for table in (
            'star_gift_auction_acquired',
            'star_gift_auction_bids',
            'star_gift_auction_state',
            'star_gift_upgrade_attrs',
            'saved_star_gifts',
        ):
            self._conn.execute(
                f'DELETE FROM {table} WHERE gift_id=?', (gid,))
        doc_id = None
        row = self._conn.execute(
            'SELECT document_id FROM star_gifts WHERE gift_id=?', (gid,)
        ).fetchone()
        if row:
            doc_id = int(row['document_id'])
        self._conn.execute('DELETE FROM star_gifts WHERE gift_id=?', (gid,))
        if doc_id:
            other = self._conn.execute(
                'SELECT 1 FROM star_gifts WHERE document_id=? LIMIT 1',
                (doc_id,)).fetchone()
            if not other:
                self._conn.execute(
                    'DELETE FROM sticker_documents WHERE id=?', (doc_id,))
        self._conn.commit()
        self.recompute_star_gifts_catalog_hash()

    def get_active_stories(self, owner_id: int) -> List[dict]:
        now = int(time.time())
        rows = self._conn.execute(
            '''SELECT * FROM stories
               WHERE owner_id=? AND expire_date > ?
               ORDER BY id ASC''',
            (owner_id, now)).fetchall()
        return [dict(r) for r in rows]

    def list_users_with_active_stories(self) -> List[int]:
        now = int(time.time())
        rows = self._conn.execute(
            '''SELECT DISTINCT owner_id FROM stories
               WHERE expire_date > ? ORDER BY owner_id''',
            (now,)).fetchall()
        return [int(r['owner_id']) for r in rows]

    def get_story_state(self) -> str:
        now = int(time.time())
        row = self._conn.execute(
            '''SELECT COUNT(*) AS c, COALESCE(MAX(id), 0) AS max_id,
                      COALESCE(MAX(date), 0) AS max_date
               FROM stories WHERE expire_date > ?''',
            (now,)).fetchone()
        return f"{row['c']}:{row['max_id']}:{row['max_date']}"

    def get_story_max_id(self, owner_id: int) -> int:
        stories = self.get_active_stories(owner_id)
        if not stories:
            return 0
        return max(int(s['id']) for s in stories)

    def allocate_story_id(self, owner_id: int) -> int:
        row = self._conn.execute(
            'SELECT COALESCE(MAX(id), 0) + 1 AS n FROM stories WHERE owner_id=?',
            (owner_id,)).fetchone()
        return int(row['n'])

    def get_story(self, owner_id: int, story_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM stories WHERE owner_id=? AND id=?',
            (owner_id, story_id)).fetchone()
        return dict(row) if row else None

    def find_story_owner(self, story_id: int) -> Optional[int]:
        row = self._conn.execute(
            'SELECT owner_id FROM stories WHERE id=? ORDER BY date DESC LIMIT 1',
            (int(story_id),)).fetchone()
        return int(row['owner_id']) if row else None

    def create_story(self, owner_id: int, *, media_kind: str,
                     photo_id: Optional[int] = None,
                     document_id: Optional[int] = None,
                     caption: str = '',
                     entities: Optional[List[dict]] = None,
                     period: int = 86400,
                     pinned: bool = False) -> dict:
        now = int(time.time())
        period = int(period or 86400)
        sid = self.allocate_story_id(owner_id)
        expire = now + period
        entities = entities or []
        self._conn.execute(
            '''INSERT INTO stories
               (owner_id, id, date, expire_date, period, pinned, caption,
                entities_json, media_kind, photo_id, document_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (owner_id, sid, now, expire, period, 1 if pinned else 0,
             caption or '', json.dumps(entities, ensure_ascii=False),
             media_kind, photo_id, document_id))
        self._conn.commit()
        return self.get_story(owner_id, sid)

    def delete_stories(self, owner_id: int, story_ids: List[int]) -> None:
        for sid in story_ids:
            self._conn.execute(
                'DELETE FROM stories WHERE owner_id=? AND id=?',
                (owner_id, int(sid)))
            self._conn.execute(
                'DELETE FROM story_views WHERE owner_id=? AND story_id=?',
                (owner_id, int(sid)))
        self._conn.commit()

    def record_story_view(self, owner_id: int, story_id: int,
                          viewer_id: int) -> bool:
        if int(viewer_id) == int(owner_id):
            return False
        story = self.get_story(owner_id, story_id)
        if not story:
            return False
        now = int(time.time())
        cur = self._conn.execute(
            '''INSERT OR IGNORE INTO story_views
               (owner_id, story_id, viewer_id, date)
               VALUES (?,?,?,?)''',
            (owner_id, int(story_id), int(viewer_id), now))
        self._conn.commit()
        return cur.rowcount > 0

    def record_story_views(self, owner_id: int, story_ids: List[int],
                           viewer_id: int) -> None:
        for sid in story_ids:
            self.record_story_view(owner_id, int(sid), viewer_id)

    def get_story_view_count(self, owner_id: int, story_id: int) -> int:
        row = self._conn.execute(
            '''SELECT COUNT(*) AS c FROM story_views
               WHERE owner_id=? AND story_id=?''',
            (owner_id, int(story_id))).fetchone()
        return int(row['c']) if row else 0

    def get_story_recent_viewer_ids(self, owner_id: int, story_id: int,
                                    *, limit: int = 5) -> List[int]:
        rows = self._conn.execute(
            '''SELECT viewer_id FROM story_views
               WHERE owner_id=? AND story_id=?
               ORDER BY date DESC LIMIT ?''',
            (owner_id, int(story_id), int(limit))).fetchall()
        return [int(r['viewer_id']) for r in rows]

    def list_story_views(self, owner_id: int, story_id: int,
                         offset: int = 0, limit: int = 50) -> tuple:
        total = self.get_story_view_count(owner_id, story_id)
        rows = self._conn.execute(
            '''SELECT viewer_id, date FROM story_views
               WHERE owner_id=? AND story_id=?
               ORDER BY date DESC LIMIT ? OFFSET ?''',
            (owner_id, int(story_id), int(limit), int(offset))).fetchall()
        return [dict(r) for r in rows], total

    def list_story_reads_for_viewer(self, viewer_id: int) -> List[dict]:
        rows = self._conn.execute(
            'SELECT owner_id, max_id FROM story_reads WHERE viewer_id=?',
            (viewer_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_story_read_max_id(self, viewer_id: int, owner_id: int) -> int:
        row = self._conn.execute(
            'SELECT max_id FROM story_reads WHERE viewer_id=? AND owner_id=?',
            (viewer_id, owner_id)).fetchone()
        return int(row['max_id']) if row else 0

    def read_stories(self, viewer_id: int, owner_id: int, max_id: int) -> int:
        self._conn.execute(
            '''INSERT INTO story_reads (viewer_id, owner_id, max_id)
               VALUES (?,?,?)
               ON CONFLICT(viewer_id, owner_id) DO UPDATE SET
               max_id=CASE WHEN excluded.max_id > story_reads.max_id
                      THEN excluded.max_id ELSE story_reads.max_id END''',
            (viewer_id, owner_id, int(max_id)))
        for story in self.get_active_stories(owner_id):
            if int(story['id']) <= int(max_id):
                self.record_story_view(owner_id, int(story['id']), viewer_id)
        self._conn.commit()
        return int(max_id)

    def list_user_active_auctions(self, user_id: int) -> List[dict]:
        rows = self._conn.execute(
            '''SELECT DISTINCT gift_id FROM star_gift_auction_bids
               WHERE user_id=? AND bid_date IS NOT NULL''',
            (user_id,)).fetchall()
        out = []
        for row in rows:
            runtime = self.get_auction_runtime(int(row['gift_id']))
            if not runtime:
                continue
            if runtime['timing']['finished']:
                continue
            out.append(runtime)
        return out

    def compute_active_auctions_hash(self, user_id: int) -> int:
        parts = []
        for rt in self.list_user_active_auctions(user_id):
            bid = self.get_auction_bid(int(rt['gift']['gift_id']), user_id)
            if not bid or not bid.get('bid_date'):
                continue
            parts.append(
                f"{rt['state']['version']}:{bid['bid_date']}")
        if not parts:
            return 0
        import hashlib
        h = hashlib.sha256('|'.join(sorted(parts)).encode()).digest()
        return int.from_bytes(h[:8], 'big', signed=True)

    # ── Star Gift Collections ───────────────────────────────

    def list_star_gift_collections(self, owner_id: int) -> List[dict]:
        cols = self._conn.execute(
            '''SELECT * FROM star_gift_collections WHERE owner_id=?
               ORDER BY position, collection_id''',
            (owner_id,)).fetchall()
        result = []
        for col in cols:
            d = dict(col)
            items = self._conn.execute(
                '''SELECT saved_id FROM star_gift_collection_items
                   WHERE collection_id=? ORDER BY position''',
                (d['collection_id'],)).fetchall()
            d['gift_saved_ids'] = [int(r['saved_id']) for r in items]
            result.append(d)
        return result

    def list_visible_star_gift_collections(self, owner_id: int) -> List[dict]:
        """Collections shown in profile — titled only; untitled ones break the client."""
        visible = []
        for col in self.list_star_gift_collections(owner_id):
            title = (col.get('title') or '').strip()
            if title:
                visible.append(col)
        return visible

    def visible_star_gift_collection_ids(self, owner_id: int) -> set:
        return {int(c['collection_id'])
                for c in self.list_visible_star_gift_collections(owner_id)}

    def get_star_gift_collection(self, collection_id: int) -> Optional[dict]:
        row = self._conn.execute(
            'SELECT * FROM star_gift_collections WHERE collection_id=?',
            (int(collection_id),)).fetchone()
        return dict(row) if row else None

    def create_star_gift_collection(self, owner_id: int, title: str) -> dict:
        now = int(time.time())
        max_pos = self._conn.execute(
            'SELECT COALESCE(MAX(position),0) FROM star_gift_collections WHERE owner_id=?',
            (owner_id,)).fetchone()[0]
        cur = self._conn.execute(
            '''INSERT INTO star_gift_collections (owner_id, title, position, created_at)
               VALUES (?,?,?,?)''',
            (owner_id, title or '', int(max_pos) + 1, now))
        self._conn.commit()
        return self.get_star_gift_collection(cur.lastrowid)

    def edit_star_gift_collection(self, collection_id: int, owner_id: int,
                                  title: Optional[str] = None) -> Optional[dict]:
        col = self.get_star_gift_collection(collection_id)
        if not col or int(col['owner_id']) != int(owner_id):
            return None
        if title is not None:
            self._conn.execute(
                'UPDATE star_gift_collections SET title=? WHERE collection_id=?',
                (title, int(collection_id)))
            self._conn.commit()
        return self.get_star_gift_collection(collection_id)

    def delete_star_gift_collection(self, collection_id: int, owner_id: int) -> bool:
        col = self.get_star_gift_collection(collection_id)
        if not col or int(col['owner_id']) != int(owner_id):
            return False
        self._conn.execute(
            'DELETE FROM star_gift_collection_items WHERE collection_id=?',
            (int(collection_id),))
        self._conn.execute(
            'DELETE FROM star_gift_collections WHERE collection_id=?',
            (int(collection_id),))
        self._conn.commit()
        return True

    def add_gifts_to_collection(self, collection_id: int, owner_id: int,
                                saved_ids: List[int]) -> bool:
        col = self.get_star_gift_collection(collection_id)
        if not col or int(col['owner_id']) != int(owner_id):
            return False
        max_pos = self._conn.execute(
            'SELECT COALESCE(MAX(position),0) FROM star_gift_collection_items WHERE collection_id=?',
            (int(collection_id),)).fetchone()[0]
        pos = int(max_pos)
        for sid in saved_ids:
            saved = self.get_saved_star_gift_by_id(int(sid))
            if (not saved
                    or int(saved.get('owner_user_id') or 0) != int(owner_id)):
                continue
            pos += 1
            self._conn.execute(
                '''INSERT OR IGNORE INTO star_gift_collection_items
                   (collection_id, saved_id, position) VALUES (?,?,?)''',
                (int(collection_id), int(sid), pos))
        self._conn.commit()
        return True

    def remove_gifts_from_collection(self, collection_id: int, owner_id: int,
                                     saved_ids: List[int]) -> bool:
        col = self.get_star_gift_collection(collection_id)
        if not col or int(col['owner_id']) != int(owner_id):
            return False
        for sid in saved_ids:
            self._conn.execute(
                'DELETE FROM star_gift_collection_items WHERE collection_id=? AND saved_id=?',
                (int(collection_id), int(sid)))
        self._conn.commit()
        return True

    def reorder_gifts_in_collection(self, collection_id: int, owner_id: int,
                                    saved_ids: List[int]) -> bool:
        col = self.get_star_gift_collection(collection_id)
        if not col or int(col['owner_id']) != int(owner_id):
            return False
        for pos, sid in enumerate(saved_ids, 1):
            self._conn.execute(
                '''UPDATE star_gift_collection_items SET position=?
                   WHERE collection_id=? AND saved_id=?''',
                (pos, int(collection_id), int(sid)))
        self._conn.commit()
        return True

    def compute_star_gift_collections_hash(self, owner_id: int) -> int:
        cols = self.list_visible_star_gift_collections(owner_id)
        if not cols:
            return 0
        import hashlib
        parts = [f"{c['collection_id']}:{c['title']}:{','.join(str(x) for x in c['gift_saved_ids'])}"
                 for c in cols]
        h = hashlib.sha256('|'.join(parts).encode()).digest()
        return int.from_bytes(h[:8], 'big', signed=True)
