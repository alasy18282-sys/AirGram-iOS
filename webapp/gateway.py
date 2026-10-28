"""projectre.work public gateway.

Port 443 carries BOTH protocols (Cloudflare proxies HTTPS here, while the
patched messenger clients speak raw MTProto to the same IP):

    0x16 (TLS ClientHello)  ->  https://127.0.0.1:8443  (shop WebApp)
    anything else           ->  tcp://127.0.0.1:20443   (MyTelegram MTProto)

The web server itself also listens on 0.0.0.0:80 (plain HTTP) so the zone
works with Cloudflare SSL mode "Flexible" as well, and on 127.0.0.1:8443
(TLS) and 127.0.0.1:8081 (plain) for the gateway's internal routing.

Run:  python3 -u webapp/gateway.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402

log = logging.getLogger('webapp')

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
WALLET_STATIC = STATIC / 'wallet'
CERT_DIR = ROOT / 'certs'
CERT_FILE = CERT_DIR / 'origin.crt'
KEY_FILE = CERT_DIR / 'origin.key'

MTPROTO_BACKEND = ('127.0.0.1', int(os.environ.get('MYTELEGRAM_SERVER_PORT', '20443')))
WEB_BACKEND_TLS = ('127.0.0.1', 8443)

BOT_TOKEN = config.AUTH_BOT_TOKEN
SHOP_ADMINS = {8014265847, 8549770368}
MAX_BODY = 1 << 20  # 1 MiB
_GRAM_RATE_CACHE: dict | None = None
_GRAM_RATE_CACHE_AT = 0


def _gram_market_rate() -> dict:
    """Fetch the live native TON/Gram market quote; retain a short cache."""
    global _GRAM_RATE_CACHE, _GRAM_RATE_CACHE_AT
    now = int(time.time())
    if _GRAM_RATE_CACHE and now - _GRAM_RATE_CACHE_AT < 60:
        return {**_GRAM_RATE_CACHE, 'stale': False}
    request = urllib.request.Request(
        'https://api.coingecko.com/api/v3/simple/price'
        '?ids=the-open-network&vs_currencies=usd,rub,eur',
        headers={'Accept': 'application/json', 'User-Agent': 'KoalGramWallet/1.0'},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode('utf-8'))
        rates = payload.get('the-open-network') or {}
        quote = {code: float(rates[code]) for code in ('usd', 'rub', 'eur')
                 if float(rates.get(code) or 0) > 0}
        if not quote:
            raise ValueError('empty Gram quote')
        _GRAM_RATE_CACHE = {
            'ok': True, 'symbol': 'GRAM', 'prices': quote,
            'provider': 'CoinGecko', 'updatedAt': now,
        }
        _GRAM_RATE_CACHE_AT = now
        return {**_GRAM_RATE_CACHE, 'stale': False}
    except Exception as exc:
        log.warning('Live Gram rate unavailable: %s', exc)
        if _GRAM_RATE_CACHE:
            return {**_GRAM_RATE_CACHE, 'stale': True}
        return {'ok': False, 'error': 'Текущий курс GRAM временно недоступен'}


# ──────────────────────────── TCP demux gateway ────────────────────────────

async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except Exception:
        # StreamReader has no public .transport; a secondary AttributeError here
        # used to mask every reset and abort the tunnel abruptly.
        def _peer(obj):
            try:
                transport = getattr(obj, 'transport', None)
                if transport is None:
                    transport = getattr(obj, '_transport', None)
                if transport is not None:
                    return transport.get_extra_info('peername')
            except Exception:
                pass
            return '?'

        log.debug('pipe %s -> %s closed: %s', _peer(reader), _peer(writer),
                  sys.exc_info()[1])
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle_443(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await reader.readexactly(3)
    except (ConnectionResetError, asyncio.IncompleteReadError):
        writer.close()
        return
    # TLS ClientHello: 0x16 0x03 0x00-0x04.  MTProto obfuscated transport
    # starts with random bytes, so requiring all three makes misrouting
    # probability negligible.
    is_tls = head[0] == 0x16 and head[1] == 0x03 and head[2] <= 0x04
    target = WEB_BACKEND_TLS if is_tls else MTPROTO_BACKEND
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(*target)
    except Exception:
        log.exception('backend %s unavailable', target)
        writer.close()
        return
    upstream_writer.write(head)
    log.info('443 -> %s (tls=%s)', target, is_tls)
    await asyncio.gather(
        _pipe(reader, upstream_writer), _pipe(upstream_reader, writer))


# ────────────────────────────── WebApp server ──────────────────────────────

# Client builds are served through the public 80/443 listeners: many mobile
# carriers drop arbitrary high ports (8080), while 80/443 always work.
CLIENTS_DIR = ROOT.parents[1] / 'clients'
DOWNLOADS = {
    '/dl/apk': ('Telegram-12.7.0-projectre2.apk',
                'application/vnd.android.package-archive'),
    '/dl/ipa': ('ios/KoalGram.ipa', 'application/octet-stream'),
    '/dl/exe': ('Telegram-projectre-portable.zip', 'application/zip'),
    '/dl/exe-raw': ('Telegram-projectre/Telegram.exe',
                    'application/vnd.microsoft.portable-executable'),
}


class WebApp:
    """HTTP endpoints backed by shop_webapp.bridge + direct storage access."""

    def __init__(self):
        from shop_webapp import bridge, service  # noqa: F401
        self.bridge = bridge
        self.service = service

    # ── auth helpers ──

    @staticmethod
    def _init_data_secret() -> bytes:
        return hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()

    def validate_init_data(self, init_data: str) -> dict | None:
        """Return the Telegram user dict from validated WebApp initData."""
        if not init_data or not BOT_TOKEN:
            return None
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop('hash', '')
        if not received_hash:
            return None
        check_str = '\n'.join(
            f'{k}={v}' for k, v in sorted(pairs.items()))
        calc = hmac.new(self._init_data_secret(), check_str.encode(),
                        hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received_hash):
            return None
        auth_date = int(pairs.get('auth_date', '0') or 0)
        if auth_date and time.time() - auth_date > 86400 * 2:
            return None
        try:
            return json.loads(pairs.get('user', '{}'))
        except json.JSONDecodeError:
            return None

    # ── user resolution ──

    def _resolve_user_id(self, storage, telegram_user_id: int) -> int | None:
        """Fake-server user id owning the given real Telegram id."""
        row = storage._conn.execute(
            '''SELECT u.id FROM users u
               JOIN bot_phone_assignments bpa ON bpa.phone = u.phone
               WHERE bpa.telegram_user_id=?
               ORDER BY bpa.created_at DESC LIMIT 1''',
            (telegram_user_id,)).fetchone()
        if row:
            return int(row['id'])
        row = storage._conn.execute(
            '''SELECT user_id FROM shop_webapp_sessions
               WHERE telegram_user_id=? ORDER BY created_at DESC LIMIT 1''',
            (telegram_user_id,)).fetchone()
        return int(row['user_id']) if row else None

    def _me(self, tg: dict) -> dict:
        storage = self.service._open()
        tid = int(tg['id'])
        user_id = self._resolve_user_id(storage, tid)
        out: dict = {
            'ok': True,
            'telegramId': tid,
            'firstName': tg.get('first_name') or '',
            'username': tg.get('username') or '',
            'isAdmin': tid in SHOP_ADMINS,
            'userId': user_id or 0,
        }
        phones = storage.list_bot_phones_for_telegram(tid)
        if phones:
            from db.storage import format_phone_display
            out['phone'] = format_phone_display(phones[0]['phone'])
            out['numbers'] = [format_phone_display(r['phone']) for r in phones]
        if user_id:
            out['stars'] = storage.get_stars_balance(user_id)
            gram_nano = storage.get_ton_balance(user_id)
            out['gramBalanceNano'] = gram_nano
            out['gramBalance'] = config.format_gram_amount(gram_nano)
            user = storage.get_user_by_id(user_id)
            if user:
                out['messengerName'] = (user.get('first_name') or '')
                out['messengerUsername'] = (user.get('username') or '')
            from airgram_chain import service as chain_service
            wallet = chain_service.get_linked_wallet(storage, user_id)
            if wallet:
                out['tonWalletAddress'] = wallet['address']
                try:
                    out['tonWalletFriendly'] = chain_service.core.friendly_address(
                        wallet['address'])
                except Exception:
                    out['tonWalletFriendly'] = ''
        return out

    def _link_ton_wallet(self, tg: dict, body: dict) -> dict:
        storage = self.service._open()
        user_id = self._resolve_user_id(storage, int(tg['id']))
        if not user_id:
            return {'ok': False,
                    'error': 'Сначала войдите в KoalGram с аккаунтом Telegram'}
        from airgram_chain import service as chain_service
        result = chain_service.link_wallet(
            storage, user_id, str(body.get('address') or ''))
        if result.get('ok'):
            result['wallet'] = chain_service.wallet_payload(
                storage, user_id, include_nfts=False)
        return result

    def _koalgram_register_start(self, tg: dict) -> dict:
        from shop_webapp.service import send_register_code
        result = send_register_code(int(tg['id']))
        if result.get('ok') and not result.get('codeSent'):
            return {'ok': False,
                    'error': 'Не удалось отправить код. Сначала откройте чат с @KoalGramApp и нажмите Start.'}
        return result

    def _koalgram_register_finish(self, tg: dict, body: dict) -> dict:
        from shop_webapp.service import complete_register
        return complete_register(
            int(tg['id']), str(body.get('code') or ''),
            str(body.get('firstName') or tg.get('first_name') or 'KoalGram'),
            str(body.get('lastName') or ''))

    def _blockchain_wallet(self, tg: dict) -> dict:
        storage = self.service._open()
        user_id = self._resolve_user_id(storage, int(tg['id']))
        if not user_id:
            return {'ok': False,
                    'error': 'Сначала войдите в KoalGram с вашим номером телефона'}
        from airgram_chain import core as chain_core
        from airgram_chain import service as chain_service
        wallet = chain_service.wallet_payload(storage, user_id)
        return {
            'ok': True,
            'legacyCustody': False,
            'address': wallet.get('address') or '',
            'friendlyAddress': wallet.get('friendlyAddress') or '',
            'nfts': wallet.get('nfts') or [],
            'chain': chain_core.chain_info(),
            'phone': self._me(tg).get('phone'),
        }

    def _koalgram_wallet(self, tg: dict) -> dict:
        storage = self.service._open()
        tid = int(tg['id'])
        user_id = self._resolve_user_id(storage, tid)
        if not user_id:
            return {'ok': False,
                    'error': 'Сначала войдите в KoalGram с вашим номером телефона'}
        from airgram_chain import core as chain_core
        from airgram_chain import service as chain_service
        wallet = chain_service.wallet_payload(storage, user_id)
        phones = storage.list_bot_phones_for_telegram(tid)
        phone = ''
        if phones:
            from db.storage import format_phone_display
            phone = format_phone_display(phones[0]['phone'])
        return {
            **wallet,
            'telegramId': tid,
            'userId': user_id,
            'phone': phone,
            'firstName': tg.get('first_name') or '',
            'username': tg.get('username') or '',
            'chain': chain_core.chain_info(),
        }

    def _koalgram_nfts(self, tg: dict | None, body: dict) -> dict:
        from airgram_chain import service as chain_service
        address = str(body.get('address') or '').strip()
        storage = self.service._open()
        if address:
            return chain_service.public_wallet(storage, address)
        if not tg:
            return {'ok': False, 'error': 'Укажите адрес кошелька'}
        user_id = self._resolve_user_id(storage, int(tg['id']))
        if not user_id:
            return {'ok': False, 'error': 'Аккаунт KoalGram не найден'}
        wallet = chain_service.wallet_payload(storage, user_id)
        return {'ok': True, 'address': wallet.get('address') or '',
                'friendlyAddress': wallet.get('friendlyAddress') or '',
                'nfts': wallet.get('nfts') or [],
                'nftCount': wallet.get('nftCount') or 0,
                'gramBalanceNano': wallet.get('gramBalanceNano') or 0,
                'gramBalance': wallet.get('gramBalance') or '0'}

    def _koalgram_nft_detail(self, body: dict) -> dict:
        from airgram_chain import service as chain_service
        address = str(body.get('address') or '').strip()
        if not address:
            return {'ok': False, 'error': 'Не указан адрес NFT'}
        detail = chain_service.nft_detail(self.service._open(), address)
        if not detail:
            return {'ok': False, 'error': 'NFT не найден в блокчейне KoalGram'}
        return {'ok': True, **detail}

    def _koalgram_send_gram(self, tg: dict, body: dict) -> dict:
        storage = self.service._open()
        user_id = self._resolve_user_id(storage, int(tg['id']))
        if not user_id:
            return {'ok': False,
                    'error': 'Сначала войдите в KoalGram с аккаунтом Telegram'}
        from airgram_chain import service as chain_service
        return chain_service.send_gram(
            storage, user_id,
            str(body.get('to') or body.get('address') or ''),
            body.get('amountNano') or 0,
            str(body.get('comment') or ''))

    def _koalgram_status(self) -> dict:
        from airgram_chain import core as chain_core
        return {
            'ok': True,
            'chain': chain_core.chain_info(),
            'blocks': chain_core.list_blocks(10),
            'activity': chain_core.list_activity('', limit=20),
            'verify': chain_core.verify_chain(20),
        }

    def _blockchain_deposit_intent(self, tg: dict, body: dict) -> dict:
        storage = self.service._open()
        user_id = self._resolve_user_id(storage, int(tg['id']))
        if not user_id:
            return {'ok': False,
                    'error': 'Сначала войдите в KoalGram с вашим номером телефона'}
        from webapp.ton_custody import create_deposit_intent
        return create_deposit_intent(
            storage, user_id,
            str(body.get('nftAddress') or ''),
            str(body.get('senderAddress') or ''))

    def _blockchain_deposit_finish(self, tg: dict, body: dict) -> dict:
        storage = self.service._open()
        user_id = self._resolve_user_id(storage, int(tg['id']))
        if not user_id:
            return {'ok': False,
                    'error': 'Сначала войдите в KoalGram с вашим номером телефона'}
        from webapp.ton_custody import finish_deposit_intent
        return finish_deposit_intent(
            storage, user_id, str(body.get('token') or ''))

    # ── bot API ──

    def _bot_api(self, method: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{BOT_TOKEN}/{method}',
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            log.warning('Bot API %s failed: %s', method, exc)
            return {'ok': False, 'error': str(exc)}

    def _buy_stars(self, tg: dict, stars: int, promo: str) -> dict:
        storage = self.service._open()
        tid = int(tg['id'])
        user_id = self._resolve_user_id(storage, tid)
        if not user_id:
            return {'ok': False,
                    'error': 'Сначала войдите в KoalGram с вашим номером, '
                             'потом вернитесь сюда'}
        res = self.service.create_star_order(
            user_id, int(stars), promo or '', target_user_id=user_id)
        if not res.get('ok'):
            return res
        inv = self._bot_api('sendInvoice', {
            'chat_id': tid,
            'title': f'{res["stars"]} звёзд KoalGram',
            'description': f'Пополнение баланса: +{res["stars"]} ⭐'
                           + (f' (промокод {res["promo"]}, −{res["discountPercent"]}%)'
                              if res.get('promo') else ''),
            'payload': res['payload'],
            'currency': 'XTR',
            'prices': [{'label': f'{res["stars"]} звёзд', 'amount': int(res['xtr'])}],
        })
        if not inv.get('ok'):
            return {'ok': False,
                    'error': 'Не удалось создать счёт: '
                             + str(inv.get('description') or inv.get('error'))}
        return {'ok': True, 'invoiceLink': inv['result']}

    # ── dispatch ──

    def api(self, command: str, body: dict, tg: dict | None) -> dict:
        tg = tg or {}
        tid = int(tg.get('id') or 0) if tg else 0
        is_admin = tid in SHOP_ADMINS

        if command == 'gram-rate':
            return _gram_market_rate()

        if command == 'me':
            if not tg:
                return {'ok': False, 'error': 'Откройте приложение из Telegram'}
            return self._me(tg)
        if command in ('airgram-register-start', 'koalgram-register-start'):
            if not tg:
                return {'ok': False, 'error': 'Откройте приложение из Telegram'}
            return self._koalgram_register_start(tg)
        if command in ('airgram-register-finish', 'koalgram-register-finish'):
            if not tg:
                return {'ok': False, 'error': 'Откройте приложение из Telegram'}
            return self._koalgram_register_finish(tg, body)
        if command == 'link-ton-wallet':
            if not tg:
                return {'ok': False, 'error': 'Откройте приложение из Telegram'}
            return self._link_ton_wallet(tg, body)
        if command in ('airgram-wallet', 'koalgram-wallet'):
            if not tg:
                return {'ok': False, 'error': 'Откройте приложение из Telegram'}
            return self._koalgram_wallet(tg)
        if command in ('airgram-nfts', 'koalgram-nfts'):
            return self._koalgram_nfts(tg, body)
        if command in ('airgram-nft-detail', 'koalgram-nft-detail'):
            return self._koalgram_nft_detail(body)
        if command in ('airgram-send-gram', 'koalgram-send-gram'):
            if not tg:
                return {'ok': False,
                        'error': 'Откройте приложение из Telegram'}
            return self._koalgram_send_gram(tg, body)
        if command in ('airgram-chain', 'koalgram-chain'):
            return self._koalgram_status()
        if command == 'blockchain-wallet':
            if not tg:
                return {'ok': False, 'error': 'Откройте приложение из Telegram'}
            return self._blockchain_wallet(tg)
        if command == 'blockchain-deposit-intent':
            if not tg:
                return {'ok': False, 'error': 'Откройте приложение из Telegram'}
            return self._blockchain_deposit_intent(tg, body)
        if command == 'blockchain-deposit-finish':
            if not tg:
                return {'ok': False, 'error': 'Откройте приложение из Telegram'}
            return self._blockchain_deposit_finish(tg, body)
        if command == 'buy-stars':
            if not tg:
                return {'ok': False, 'error': 'Откройте приложение из Telegram'}
            return self._buy_stars(tg, int(body.get('stars') or 0),
                                   str(body.get('promo') or ''))
        if command in ('get-assigned-phone', 'top-donors', 'catalog',
                       'send-login-code', 'login', 'get-session', 'session',
                       'list-anonymous'):
            argv = self._to_argv(body)
            if command in ('get-assigned-phone', 'top-donors'):
                argv += ['--telegram-id', str(tid or 0)]
            return self._bridge_call(command, argv)
        if command.startswith('admin-'):
            if not is_admin:
                return {'ok': False, 'error': 'Нет доступа'}
            argv = self._to_argv(body)
            if command in ('admin-lookup', 'admin-unban', 'admin-lookup-unban'):
                pass
            return self._bridge_call(command, argv)
        return {'ok': False, 'error': f'Неизвестная команда: {command}'}

    @staticmethod
    def _to_argv(body: dict) -> list[str]:
        argv: list[str] = []
        for key, value in (body or {}).items():
            if key in ('initData',):
                continue
            if isinstance(value, bool):
                argv += [f'--{key.replace("_", "-")}',
                         '1' if value else '0']
            else:
                argv += [f'--{key.replace("_", "-")}', str(value)]
        return argv

    def _bridge_call(self, command: str, argv: list[str]) -> dict:
        try:
            result = self.bridge.dispatch(command, argv)
        except Exception as exc:
            log.exception('bridge %s failed', command)
            return {'ok': False, 'error': str(exc)}
        if isinstance(result, dict):
            return result
        return {'ok': True, 'result': result}


APP = WebApp()


# ────────────────────────────── HTTP plumbing ──────────────────────────────

async def _proxy_target(target: tuple[str, int],
                        reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter,
                        first: bytes = b'') -> None:
    try:
        _, upstream = await asyncio.open_connection(*target)
        if first:
            upstream.write(first)
        await asyncio.gather(_pipe(reader, upstream), _pipe(upstream, writer))
    except Exception:
        writer.close()


async def _serve_chain_media(method: str, path: str,
                             writer: asyncio.StreamWriter) -> None:
    from airgram_chain.media import media_file
    name = urllib.parse.unquote(
        urllib.parse.urlsplit(path).path.rsplit('/', 1)[-1])
    match = re.fullmatch(r'([0-9a-f]{64})(_thumb)?\.webp', name)
    if not match:
        writer.write(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n'
                     b'Connection: close\r\n\r\n')
        await writer.drain()
        return
    target = media_file(name)
    if target is None:
        # Virtual username / +888 NFTs generate their artwork on demand.
        try:
            from airgram_chain import virtual as virtual_nfts
            virtual_nfts.ensure_media_for_address(
                APP.service._open(), f'0:{match.group(1)}')
        except Exception:
            log.exception('virtual NFT artwork failed for %s', name)
        target = media_file(name)
    if target is None:
        writer.write(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n'
                     b'Connection: close\r\n\r\n')
        await writer.drain()
        return
    payload = target.read_bytes()
    head = (f'HTTP/1.1 200 OK\r\n'
            f'Content-Type: image/webp\r\n'
            f'Content-Length: {len(payload)}\r\n'
            'Cache-Control: public, max-age=86400\r\n'
            'Connection: close\r\n\r\n')
    writer.write(head.encode('latin1'))
    if method != 'HEAD':
        writer.write(payload)
    await writer.drain()


async def _serve_download(method: str, path: str, headers: dict,
                          writer: asyncio.StreamWriter) -> bool:
    """Stream a client build (APK/IPA/EXE) with Range support."""
    key = path.split('?', 1)[0].rstrip('/')

    if key == '/dl':
        rows = []
        for url, (rel, _ctype) in DOWNLOADS.items():
            fp = CLIENTS_DIR / rel
            if fp.is_file():
                mb = fp.stat().st_size / (1024 * 1024)
                rows.append(f'<li><a href="{url}">{fp.name}</a> '
                            f'({mb:.1f} MB)</li>')
        html = ('<!doctype html><meta charset="utf-8"><title>Downloads</title>'
                '<h3>KoalGram clients</h3><ul>' + ''.join(rows) + '</ul>')
        payload = html.encode('utf-8')
        writer.write(
            (f'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
             f'Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n'
             ).encode('latin1'))
        if method != 'HEAD':
            writer.write(payload)
        await writer.drain()
        return True

    entry = DOWNLOADS.get(key)
    if entry is None:
        return False
    rel, ctype = entry
    fp = CLIENTS_DIR / rel
    if not fp.is_file():
        writer.write(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n'
                     b'Connection: close\r\n\r\n')
        await writer.drain()
        return True

    size = fp.stat().st_size
    start, end, status = 0, size - 1, '200 OK'
    rng = headers.get('range', '')
    if rng.startswith('bytes='):
        try:
            first, _, last = rng[6:].split(',', 1)[0].partition('-')
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            elif last:
                start = max(0, size - int(last))
                end = size - 1
            if start < 0 or start >= size or end < start:
                raise ValueError
            end = min(end, size - 1)
            status = '206 Partial Content'
        except ValueError:
            writer.write(('HTTP/1.1 416 Range Not Satisfiable\r\n'
                          f'Content-Range: bytes */{size}\r\n'
                          'Content-Length: 0\r\nConnection: close\r\n\r\n'
                          ).encode('latin1'))
            await writer.drain()
            return True

    length = end - start + 1
    head = (f'HTTP/1.1 {status}\r\n'
            f'Content-Type: {ctype}\r\n'
            f'Content-Length: {length}\r\n'
            f'Content-Disposition: attachment; filename="{fp.name}"\r\n'
            'Accept-Ranges: bytes\r\n'
            + (f'Content-Range: bytes {start}-{end}/{size}\r\n'
               if status.startswith('206') else '')
            + 'Cache-Control: public, max-age=3600\r\n'
            'Connection: close\r\n\r\n')
    writer.write(head.encode('latin1'))
    if method != 'HEAD':
        with fp.open('rb') as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(262144, remaining))
                if not chunk:
                    break
                writer.write(chunk)
                remaining -= len(chunk)
                await writer.drain()
    await writer.drain()
    log.info('download %s %s -> %d bytes (%s)', method, key, length, status)
    return True


async def handle_http(reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter,
                      *, scheme: str) -> None:
    peer = writer.get_extra_info('peername')
    try:
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = await reader.read(4096)
            if not chunk:
                return
            head += chunk
            if len(head) > 65536:
                return
        raw_head, _, rest = head.partition(b'\r\n\r\n')
        lines = raw_head.decode('latin1').split('\r\n')
        method, path, _ver = (lines[0].split(' ') + ['', ''])[:3]
        headers = {}
        for line in lines[1:]:
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip().lower()] = v.strip()

        body = rest
        clen = int(headers.get('content-length', '0') or 0)
        if method in ('GET', 'HEAD') and path.split('?', 1)[0].startswith(
                ('/api/airgram-nft-image/', '/api/koalgram-nft-image/')):
            await _serve_chain_media(method, path, writer)
            return
        if method in ('GET', 'HEAD') and (
                path.split('?', 1)[0].rstrip('/') == '/dl'
                or path.split('?', 1)[0] in DOWNLOADS):
            if await _serve_download(method, path, headers, writer):
                return
        while len(body) < clen:
            chunk = await reader.read(min(65536, clen - len(body)))
            if not chunk:
                break
            body += chunk

        status, ctype, payload = await _route(method, path, body, headers)

        writer.write(
            (f'HTTP/1.1 {status}\r\n'
             f'Content-Type: {ctype}\r\n'
             f'Content-Length: {len(payload)}\r\n'
             f'Cache-Control: no-store\r\n'
             f'X-Content-Scheme: {scheme}\r\n'
             'Connection: close\r\n\r\n').encode('latin1'))
        if method != 'HEAD':
            writer.write(payload)
        await writer.drain()
    except Exception:
        log.exception('http error from %s', peer)
    finally:
        try:
            writer.close()
        except Exception:
            pass


def _wallet_asset(relative: str) -> Path | None:
    root = WALLET_STATIC.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def _wallet_content_type(target: Path) -> str:
    content_type = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
    if content_type.startswith('text/') or content_type in (
            'application/javascript', 'application/json',
            'application/manifest+json'):
        content_type += '; charset=utf-8'
    return content_type


def _wallet_index() -> tuple[int, str, bytes]:
    try:
        return (200, 'text/html; charset=utf-8',
                (WALLET_STATIC / 'index.html').read_bytes())
    except OSError:
        return 503, 'text/plain; charset=utf-8', b'wallet is not built'


async def _route(method: str, path: str, body: bytes,
                 headers: dict) -> tuple[int, str, bytes]:
    clean_path = urllib.parse.urlsplit(path).path
    if method in ('GET', 'HEAD') and clean_path == '/health':
        return 200, 'application/json', json.dumps({'ok': True}).encode()
    if method in ('GET', 'HEAD') and clean_path.startswith('/api/'):
        api_path = clean_path
        if api_path.startswith('/api/airgram-'):
            api_path = '/api/koalgram-' + api_path[len('/api/airgram-'):]
        from airgram_chain import core as chain_core
        from airgram_chain import service as chain_service

        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)

        def _q(name: str) -> str:
            return str((query.get(name) or [''])[0])

        storage = APP.service._open()
        result: dict
        if api_path == '/api/koalgram-nfts':
            address = _q('address').strip()
            if not address:
                result = {'ok': False, 'error': 'Укажите адрес кошелька'}
            else:
                result = chain_service.public_wallet(storage, address)
        elif api_path == '/api/koalgram-nft-metadata/':
            result = {'ok': False, 'error': 'Не указан NFT'}
        elif api_path.startswith('/api/koalgram-nft-metadata/'):
            hex_part = urllib.parse.unquote(
                api_path[len('/api/koalgram-nft-metadata/'):]).strip()
            hex_part = hex_part[:-5] if hex_part.endswith('.json') else hex_part
            if not re.fullmatch(r'[0-9a-f]{64}', hex_part):
                return 404, 'application/json', b'{"ok":false,"error":"not found"}'
            metadata = chain_service.nft_metadata(storage, f'0:{hex_part}')
            if not metadata:
                return 404, 'application/json', b'{"ok":false,"error":"not found"}'
            return 200, 'application/json', json.dumps(
                metadata, ensure_ascii=False, default=str).encode('utf-8')
        elif api_path.startswith('/api/koalgram-nft/'):
            address = urllib.parse.unquote(
                api_path[len('/api/koalgram-nft/'):]).strip('/')
            detail = chain_service.nft_detail(storage, address)
            result = ({'ok': True, **detail} if detail
                      else {'ok': False, 'error': 'NFT не найден'})
        elif api_path == '/api/koalgram-chain':
            result = APP._koalgram_status()
        elif api_path == '/api/koalgram-activity':
            limit = _q('limit')
            try:
                limit_int = max(1, min(int(limit or 50), 200))
            except ValueError:
                limit_int = 50
            result = {'ok': True,
                      'events': chain_core.list_activity(_q('address'),
                                                         limit=limit_int)}
        elif api_path == '/api/koalgram-blocks':
            try:
                limit_int = max(1, min(int(_q('limit') or 20), 100))
            except ValueError:
                limit_int = 20
            result = {'ok': True, 'chain': chain_core.chain_info(),
                      'blocks': chain_core.list_blocks(limit_int)}
        else:
            return 404, 'application/json', b'{"ok":false,"error":"not found"}'
        return 200, 'application/json', json.dumps(
            result, ensure_ascii=False, default=str).encode('utf-8')
    if method == 'POST' and clean_path.startswith('/api/'):
        command = clean_path[len('/api/'):].strip('/')
        if command.startswith('airgram-'):
            command = 'koalgram-' + command[len('airgram-'):]
        try:
            data = json.loads(body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return 400, 'application/json', b'{"ok":false,"error":"bad json"}'
        init_data = str(data.get('initData') or '')
        tg = APP.validate_init_data(init_data) or None
        result = APP.api(command, data, tg)
        return 200, 'application/json', json.dumps(
            result, ensure_ascii=False, default=str).encode('utf-8')
    if method in ('GET', 'HEAD'):
        # The wallet is the main page: serve the Tonkeeper build from / and
        # keep /wallet/* as an alias for old links and deep links.
        relative_path = clean_path
        if relative_path == '/wallet':
            relative_path = '/'
        elif relative_path.startswith('/wallet/'):
            relative_path = relative_path[len('/wallet'):]
        relative = urllib.parse.unquote(relative_path).lstrip('/')
        if not relative:
            return _wallet_index()
        target = _wallet_asset(relative)
        if target is not None and target.is_file():
            try:
                return 200, _wallet_content_type(target), target.read_bytes()
            except OSError:
                return 404, 'text/plain; charset=utf-8', b'not found'
        if not Path(relative).suffix:
            return _wallet_index()
        return 404, 'text/plain; charset=utf-8', b'not found'
    return 404, 'text/plain', b'not found'


# ─────────────────────────────────── main ───────────────────────────────────

def build_ssl_context() -> ssl.SSLContext:
    if not (CERT_FILE.exists() and KEY_FILE.exists()):
        raise SystemExit(
            f'Origin certificate missing: {CERT_FILE}. '
            'Run openssl req -x509 -newkey rsa:2048 -nodes '
            f'-keyout {KEY_FILE} -out {CERT_FILE} -days 3650 '
            '-subj "/CN=projectre.work"')
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    return ctx


async def main() -> None:
    loop = asyncio.get_running_loop()
    ssl_ctx = build_ssl_context()
    try:
        from airgram_chain import core as chain_core
        chain_core.ensure_ready()
        info = chain_core.chain_info()
        log.info('KoalGram chain ready: %s, height=%s, nfts=%s',
                 info['chainId'], info['height'], info['nfts'])
    except Exception:
        log.exception('KoalGram chain initialization failed')

    srv_443 = await asyncio.start_server(handle_443, '0.0.0.0', 443)
    srv_80 = await asyncio.start_server(
        lambda r, w: handle_http(r, w, scheme='http'), '0.0.0.0', 80)
    srv_tls = await asyncio.start_server(
        lambda r, w: handle_http(r, w, scheme='https'),
        '127.0.0.1', 8443, ssl=ssl_ctx)
    srv_plain = await asyncio.start_server(
        lambda r, w: handle_http(r, w, scheme='http'), '127.0.0.1', 8081)

    for srv, label in ((srv_443, '443 demux (MTProto|TLS)'),
                       (srv_80, '80 http'),
                       (srv_tls, '127.0.0.1:8443 https'),
                       (srv_plain, '127.0.0.1:8081 http')):
        addrs = ', '.join(str(s.getsockname()) for s in srv.sockets)
        log.info('listening %s on %s', label, addrs)
    log.info('WebApp ready — admin ids: %s', sorted(SHOP_ADMINS))
    await asyncio.Event().wait()


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
