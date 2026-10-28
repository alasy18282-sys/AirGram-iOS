#!/usr/bin/env python3
"""KoalGram gift userbot — управление подарками через команды в чате.

Подключается к локальному MTG-серверу (Telethon) и выполняет операции
через Storage API.

Команды (исходящие сообщения, начинаются с точки):
  .getgiftid              — список id всех подарков в каталоге
  .gift <id> <username> <кол-во>   — подарить пользователю
  .selfgift <id> <кол-во>          — подарить себе
  .selfupgradegifts <id>           — улучшить все неулучшенные подарки типа id

Запуск:
  pip install telethon
  python gifts.py --phone +79XXXXXXXXX
  python gifts.py --session gifts_session --phone +79XXXXXXXXX --host 127.0.0.1
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from db.storage import Storage
from frame_verification.telethon_env import (
    prepare_telethon_session,
    telethon_credentials,
)

RSA_PUB_PATH = ROOT / 'keys' / 'server_rsa_public_pkcs1.pem'


def _register_server_rsa_key() -> None:
    """Telethon only knows production Telegram keys; register our DC key."""
    from telethon.crypto import rsa
    pub = RSA_PUB_PATH.read_bytes()
    rsa.add_key(pub, old=False)
    rsa.add_key(pub, old=True)

try:
    from telethon import TelegramClient
    from telethon.network import ConnectionTcpObfuscated
except ImportError:
    print('Установите telethon: pip install telethon', file=sys.stderr)
    sys.exit(1)

log = logging.getLogger(__name__)

MAX_GIFT_BATCH = 100
CMD_RE = re.compile(r'^\.(\w+)(?:\s+(.*))?$', re.IGNORECASE)


def _default_host() -> str:
    return os.environ.get('MYTELEGRAM_HOST', '127.0.0.1').strip() or '127.0.0.1'


def _default_port() -> int:
    raw = os.environ.get('MYTELEGRAM_PORT', str(config.SERVER_PORT))
    try:
        return int(raw)
    except ValueError:
        return config.SERVER_PORT


def _resolve_gift(storage: Storage, token: str) -> Optional[dict]:
    token = (token or '').strip()
    if not token:
        return None
    if token.lstrip('-').isdigit():
        return storage.get_star_gift(int(token))
    low = token.lower()
    for gift in storage.list_star_gifts():
        title = (gift.get('title') or '').lower()
        if low in title or title.startswith(low):
            return gift
    return None


def _resolve_user(storage: Storage, token: str) -> Optional[dict]:
    token = (token or '').strip().lstrip('@')
    if not token:
        return None
    if token.lstrip('-').isdigit():
        return storage.get_user_by_id(int(token))
    return storage.get_user_by_username(token)


def _list_unupgraded_saved(storage: Storage, owner_id: int,
                           gift_id: int) -> list[dict]:
    rows = storage._conn.execute(
        '''SELECT * FROM saved_star_gifts
           WHERE owner_user_id=? AND gift_id=? AND upgraded=0
           ORDER BY date ASC, saved_id ASC''',
        (int(owner_id), int(gift_id)),
    ).fetchall()
    return [dict(r) for r in rows]


def _format_gift_catalog(storage: Storage) -> str:
    gifts = storage.list_star_gifts()
    if not gifts:
        return 'Каталог подарков пуст.'
    lines = ['Подарки (id | название | звёзды):']
    for g in gifts:
        gid = int(g['gift_id'])
        title = (g.get('title') or '?').strip()
        stars = int(g.get('stars') or 0)
        limited = ''
        if int(g.get('limited') or 0):
            rem = int(g.get('availability_remains') or 0)
            limited = f' | осталось {rem}'
        lines.append(f'{gid} | {title} | {stars}⭐{limited}')
    return '\n'.join(lines)


def _cmd_getgiftid(storage: Storage) -> str:
    return _format_gift_catalog(storage)


def _cmd_gift(storage: Storage, me_id: int, args: list[str]) -> str:
    if len(args) < 3:
        return 'Использование: .gift <id> <username> <кол-во>'
    gift = _resolve_gift(storage, args[0])
    if not gift:
        return f'Подарок не найден: {args[0]}'
    user = _resolve_user(storage, args[1])
    if not user:
        return f'Пользователь не найден: {args[1]}'
    try:
        count = int(args[2])
    except ValueError:
        return 'Количество должно быть числом.'
    if count <= 0 or count > MAX_GIFT_BATCH:
        return f'Количество: 1–{MAX_GIFT_BATCH}.'

    gift_id = int(gift['gift_id'])
    recipient_id = int(user['id'])
    ok = 0
    errors: list[str] = []

    def _send_one() -> None:
        storage.send_star_gift(me_id, recipient_id, gift_id)

    for _ in range(count):
        try:
            storage._retry_on_db_lock(_send_one)
            ok += 1
        except Exception as exc:
            errors.append(str(exc))
            break

    title = gift.get('title') or str(gift_id)
    uname = user.get('username') or user['id']
    msg = f'Подарено {ok}/{count} × {title} → @{uname} (id {recipient_id}).'
    if errors:
        msg += f'\nОшибка: {errors[0]}'
    return msg


def _cmd_selfgift(storage: Storage, me_id: int, args: list[str]) -> str:
    if len(args) < 2:
        return 'Использование: .selfgift <id> <кол-во>'
    gift = _resolve_gift(storage, args[0])
    if not gift:
        return f'Подарок не найден: {args[0]}'
    try:
        count = int(args[1])
    except ValueError:
        return 'Количество должно быть числом.'
    if count <= 0 or count > MAX_GIFT_BATCH:
        return f'Количество: 1–{MAX_GIFT_BATCH}.'

    gift_id = int(gift['gift_id'])
    ok = 0
    errors: list[str] = []

    def _send_one() -> None:
        storage.send_star_gift(me_id, me_id, gift_id)

    for _ in range(count):
        try:
            storage._retry_on_db_lock(_send_one)
            ok += 1
        except Exception as exc:
            errors.append(str(exc))
            break

    title = gift.get('title') or str(gift_id)
    msg = f'Себе подарено {ok}/{count} × {title}.'
    if errors:
        msg += f'\nОшибка: {errors[0]}'
    return msg


def _cmd_selfupgradegifts(storage: Storage, me_id: int, args: list[str]) -> str:
    if len(args) < 1:
        return 'Использование: .selfupgradegifts <id>'
    gift = _resolve_gift(storage, args[0])
    if not gift:
        return f'Подарок не найден: {args[0]}'
    gift_id = int(gift['gift_id'])
    rows = _list_unupgraded_saved(storage, me_id, gift_id)
    if not rows:
        return f'Нет неулучшенных подарков типа {gift_id}.'

    ok = 0
    errors: list[str] = []

    for row in rows:
        msg_id = int(row['msg_id'])
        prepaid = bool(int(row.get('prepaid_upgrade') or 0))

        def _upgrade(mid: int = msg_id, prep: bool = prepaid) -> None:
            storage.upgrade_saved_star_gift(
                me_id, mid, deduct_stars=not prep)

        try:
            storage._retry_on_db_lock(_upgrade)
            ok += 1
        except Exception as exc:
            errors.append(f'msg {msg_id}: {exc}')
            if str(exc) in ('BALANCE_TOO_LOW', 'STARGIFT_NOT_UPGRADABLE'):
                break

    title = gift.get('title') or str(gift_id)
    msg = f'Улучшено {ok}/{len(rows)} × {title} (id {gift_id}).'
    if errors:
        msg += '\n' + '\n'.join(errors[:5])
    return msg


def _dispatch_command(storage: Storage, me_id: int,
                      text: str) -> Optional[str]:
    m = CMD_RE.match((text or '').strip())
    if not m:
        return None
    cmd = m.group(1).lower()
    tail = (m.group(2) or '').strip()
    args = tail.split() if tail else []

    if cmd == 'getgiftid':
        return _cmd_getgiftid(storage)
    if cmd == 'gift':
        return _cmd_gift(storage, me_id, args)
    if cmd == 'selfgift':
        return _cmd_selfgift(storage, me_id, args)
    if cmd == 'selfupgradegifts':
        return _cmd_selfupgradegifts(storage, me_id, args)
    return f'Неизвестная команда: .{cmd}'


POLL_INTERVAL_SEC = 0.5


def _max_message_id(storage: Storage, owner_id: int) -> int:
    row = storage._conn.execute(
        'SELECT COALESCE(MAX(id), 0) AS m FROM messages WHERE owner_id=?',
        (int(owner_id),),
    ).fetchone()
    return int(row['m'])


async def _command_poll_loop(client: TelegramClient, storage: Storage,
                             me_id: int, last_id: int) -> None:
    """Watch DB for dot-commands sent from any client (KoalGram, Telethon, etc.)."""
    while True:
        rows = storage._conn.execute(
            '''SELECT id, peer_id, text FROM messages
               WHERE owner_id=? AND from_id=? AND id > ?
               ORDER BY id ASC''',
            (me_id, me_id, last_id),
        ).fetchall()
        for row in rows:
            last_id = int(row['id'])
            text = (row['text'] or '').strip()
            if not CMD_RE.match(text):
                continue
            log.info('Команда: %s (msg_id=%s peer=%s)', text, last_id, row['peer_id'])
            try:
                reply = _dispatch_command(storage, me_id, text)
            except Exception as exc:
                log.exception('command failed: %s', text)
                reply = f'Ошибка: {exc}'
            if reply:
                peer = int(row['peer_id'])
                dest: int | str = 'me' if peer == me_id else peer
                await client.send_message(dest, reply)
        await asyncio.sleep(POLL_INTERVAL_SEC)


async def _run(client: TelegramClient, storage: Storage) -> None:
    me = await client.get_me()
    me_id = int(me.id)
    log.info('Вошли как %s (id=%s)', me.username or me.first_name, me_id)

    help_text = (
        'Gift userbot активен.\n'
        'Команды:\n'
        '  .getgiftid\n'
        '  .gift <id> <username> <кол-во>\n'
        '  .selfgift <id> <кол-во>\n'
        '  .selfupgradegifts <id>'
    )
    await client.send_message('me', help_text)
    last_id = _max_message_id(storage, me_id)
    asyncio.create_task(_command_poll_loop(client, storage, me_id, last_id))
    log.info('Слушаю команды (исходящие сообщения с точкой).')
    await client.run_until_disconnected()


async def _main_async(args: argparse.Namespace) -> None:
    _register_server_rsa_key()
    api_id, api_hash = telethon_credentials()
    session_path, cleanup = prepare_telethon_session(args.session)
    host = args.host
    port = args.port

    client = TelegramClient(
        session_path, api_id, api_hash,
        connection=ConnectionTcpObfuscated,
        connection_retries=5,
        retry_delay=2,
        timeout=30,
    )
    client.session.set_dc(config.DC_ID, host, port)

    storage = Storage.open_tool()

    try:
        log.info('Подключение к %s:%s (obfuscated MTProto)...', host, port)
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await client.is_user_authorized():
            phone = args.phone
            if not phone:
                phone = input('Телефон (+79...): ').strip()
            if not phone:
                raise SystemExit('Нужен --phone для первого входа.')
            log.info('Запрос кода для %s на %s:%s', phone, host, port)
            await client.send_code_request(phone)
            code = args.code or input('Код из KoalGram / auth-бота: ').strip()
            try:
                await client.sign_in(phone=phone, code=code)
            except Exception as exc:
                from telethon.errors import SessionPasswordNeededError
                if not isinstance(exc, SessionPasswordNeededError):
                    raise
                pwd = input('2FA пароль: ').strip()
                await client.sign_in(password=pwd)

        await _run(client, storage)
    finally:
        await client.disconnect()
        if cleanup:
            cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description='KoalGram gift userbot')
    parser.add_argument(
        '--session', default=os.environ.get('GIFTS_SESSION', 'gifts_session'),
        help='Имя файла Telethon-сессии (по умолчанию gifts_session)')
    parser.add_argument(
        '--phone', default=os.environ.get('GIFTS_PHONE', ''),
        help='Телефон для первого входа (+79...)')
    parser.add_argument(
        '--code', default=os.environ.get('GIFTS_CODE', ''),
        help='Код входа (опционально, иначе спросит в терминале)')
    parser.add_argument(
        '--host', default=_default_host(),
        help=f'Хост MTG-сервера (по умолчанию {_default_host()})')
    parser.add_argument(
        '--port', type=int, default=_default_port(),
        help=f'Порт MTG-сервера (по умолчанию {_default_port()})')
    parser.add_argument(
        '-v', '--verbose', action='store_true', help='Подробные логи')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )

    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        print('\nВыход.')


if __name__ == '__main__':
    main()
