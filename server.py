"""
MyTelegram Python Server
Listens on TCP port 20443, speaks MTProto 2.0 (obfuscated abridged transport).
"""
import argparse
import asyncio
import logging
import os
import sys
from threading import Event, Thread

import config
from mtproto import crypto
from mtproto.connection import Connection
from mtproto.log_filter import IOSOnlyFilter
import mtproto.log_filter as log_filter
from db.storage import Storage
from handlers.rpc import dispatch
from handlers.push import ConnectionRegistry

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'server.log')


class FlushingFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()


def _ensure_service_schemas(storage: Storage) -> None:
    """Create tables owned by optional modules on a freshly initialized DB."""
    steps = (
        ('shop', lambda: _shop_schema(storage)),
        ('cases', lambda: _shop_cases_schema(storage)),
        ('crash', lambda: _shop_crash_schema(storage)),
        ('banner', lambda: _shop_banner_schema(storage)),
        ('rentals', lambda: _rentals_schema(storage)),
        ('frame_verification', lambda: _frame_schema(storage)),
    )
    for name, step in steps:
        try:
            step()
        except Exception as exc:
            log.warning('Schema init failed for %s: %s', name, exc)


def _shop_schema(storage: Storage) -> None:
    from shop_webapp.service import _ensure_tables
    _ensure_tables(storage)


def _shop_cases_schema(storage: Storage) -> None:
    from shop_webapp.cases import ensure_cases_schema
    ensure_cases_schema(storage)


def _shop_crash_schema(storage: Storage) -> None:
    from shop_webapp.crash import ensure_crash_schema
    ensure_crash_schema(storage)


def _shop_banner_schema(storage: Storage) -> None:
    from shop_webapp.banner import ensure_banner_table
    ensure_banner_table(storage)


def _rentals_schema(storage: Storage) -> None:
    from rentals_webapp.service import _ensure_tables
    _ensure_tables(storage)


def _frame_schema(storage: Storage) -> None:
    from frame_verification.service import _ensure_schema
    _ensure_schema(storage)


def _configure_logging(ios_only_console: bool) -> None:
    import mtproto.log_filter as log_filter
    log_filter.LOG_IOS_ONLY = ios_only_console

    stream_handler = logging.StreamHandler(sys.stdout)
    if ios_only_console:
        stream_handler.addFilter(IOSOnlyFilter())

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            stream_handler,
            FlushingFileHandler(LOG_FILE, encoding='utf-8'),
        ],
        force=True,
    )


class Server:
    def __init__(self):
        log.info('Loading RSA private key from %s', config.PRIVATE_KEY_PATH)
        self._priv_key   = crypto.load_private_key(config.PRIVATE_KEY_PATH)
        self._fingerprint = crypto.rsa_public_fingerprint(self._priv_key)
        log.info('RSA fingerprint: %#018x', self._fingerprint)

        self._storage = Storage(config.DB_PATH)
        _ensure_service_schemas(self._storage)
        from handlers.media import warm_star_gift_media
        warm_star_gift_media(self._storage)
        self._registry = ConnectionRegistry()
        self._report_admin_stop = Event()
        self._report_admin_thread: Thread | None = None
        log.info('Persisted %d auth keys, %d sessions in %s',
                 self._storage.count_auth_keys(),
                 self._storage.count_sessions(),
                 config.DB_PATH)

    def _start_report_admin_poller(self) -> None:
        if not config.REPORT_ADMIN_BOT_TOKEN:
            log.info('Report admin poller disabled: no bot token configured')
            return
        if self._report_admin_thread and self._report_admin_thread.is_alive():
            return
        from report_admin_bot import run_bot
        self._report_admin_thread = Thread(
            target=run_bot,
            kwargs={'stop_event': self._report_admin_stop},
            name='report-admin-bot',
            daemon=True,
        )
        self._report_admin_thread.start()
        log.info('Report admin poller thread started')

    async def handle_client(self,
                            reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter):
        conn = Connection(
            reader, writer,
            self._priv_key, self._fingerprint,
            self._storage, dispatch, self._registry,
        )
        await conn.run()

    async def _pending_push_loop(self) -> None:
        from handlers.push import PushQueue
        from frame_verification.push import purge_expired_and_notify
        from shop_webapp.push_wake import wait_for_wake

        purge_interval = 60.0
        last_purge = 0.0
        while True:
            try:
                woke = await wait_for_wake(timeout=2.0)
                pending = self._storage.list_pending_update_users()
                if not woke and not pending:
                    continue
                now = asyncio.get_running_loop().time()
                if now - last_purge >= purge_interval:
                    purge_expired_and_notify(self._storage)
                    removed = self._storage.purge_stale_pending_updates()
                    if removed:
                        log.info('Purged %d stale pending updates', removed)
                    last_purge = now
                push_queue = PushQueue(self._registry, self._storage)
                from handlers.push import PENDING_UPDATES_DRAIN_LIMIT
                if PENDING_UPDATES_DRAIN_LIMIT > 0:
                    for user_id in pending:
                        connections = self._registry.get_connections_for_user(
                            user_id, self._storage)
                        if connections:
                            seen_keys: set[int] = set()
                            for _conn, auth_key_id in connections:
                                if auth_key_id in seen_keys:
                                    continue
                                seen_keys.add(auth_key_id)
                                push_queue.flush_pending_for_user(
                                    user_id, auth_key_id=auth_key_id,
                                    limit=PENDING_UPDATES_DRAIN_LIMIT)
                        else:
                            push_queue.flush_pending_for_user(
                                user_id, limit=PENDING_UPDATES_DRAIN_LIMIT)
                    await push_queue.flush()
            except Exception as exc:
                log.warning('Pending push loop error: %s', exc)

    async def run(self):
        srv = await asyncio.start_server(
            self.handle_client,
            config.SERVER_HOST,
            config.SERVER_PORT,
        )
        addrs = ', '.join(str(s.getsockname()) for s in srv.sockets)
        log.info('MyTelegram server listening on %s', addrs)
        log.info('Log file: %s', LOG_FILE)
        if log_filter.LOG_IOS_ONLY:
            log.info('Console logging: iOS clients only (--logios)')
        log.info('RSA fingerprint: %#018x', self._fingerprint)
        self._start_report_admin_poller()
        try:
            from airgram_chain import core as chain_core
            chain_core.ensure_ready()
            info = chain_core.chain_info()
            log.info(
                'KoalGram chain ready: height=%s nfts=%s wallets=%s',
                info.get('height'), info.get('nfts'), info.get('wallets'))
        except Exception:
            log.exception('KoalGram chain initialization failed')
        push_task = asyncio.create_task(self._pending_push_loop())
        from handlers.star_gifts import auction_round_loop
        auction_task = asyncio.create_task(
            auction_round_loop(self._storage, self._registry))
        from tools.refresh_star_gift_values import daily_refresh_loop
        gift_value_task = asyncio.create_task(daily_refresh_loop())
        try:
            async with srv:
                await srv.serve_forever()
        finally:
            self._report_admin_stop.set()
            if self._report_admin_thread is not None:
                self._report_admin_thread.join(timeout=1.0)
            push_task.cancel()
            auction_task.cancel()
            gift_value_task.cancel()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MyTelegram Python Server')
    parser.add_argument(
        '--logios',
        action='store_true',
        help='Log only iOS client traffic to console (full log still in logs/server.log)',
    )
    args = parser.parse_args()
    _configure_logging(args.logios)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    log = logging.getLogger('server')
    asyncio.run(Server().run())
