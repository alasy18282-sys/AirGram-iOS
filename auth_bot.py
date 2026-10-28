#!/usr/bin/env python3
"""Telegram bot: assigns random +7 phone numbers and delivers login codes."""
from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import config
from airgram_chain import core as chain_core
from airgram_chain import service as chain_service
from db.storage import Storage, format_phone_display

log = logging.getLogger(__name__)


def _verification_catalog() -> dict:
    """Catalog of frame verifications grantable via /<type> commands."""
    try:
        from frame_verification.catalog import load_catalog
        return load_catalog()
    except Exception as exc:
        log.warning('verification catalog unavailable: %s', exc)
        return {}


def _api(method: str, **params) -> dict:
    token = config.AUTH_BOT_TOKEN
    if not token:
        raise RuntimeError('MYTELEGRAM_AUTH_BOT_TOKEN is not set')
    url = f'https://api.telegram.org/bot{token}/{method}'
    if params:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data, method='POST')
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=65) as resp:
        return json.loads(resp.read())


def send_message(chat_id: int, text: str, reply_markup: str | None = None) -> bool:
    params = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if reply_markup:
        params['reply_markup'] = reply_markup
    try:
        result = _api('sendMessage', **params)
        return bool(result.get('ok'))
    except Exception as exc:
        log.warning('sendMessage to %s failed: %s', chat_id, exc)
        return False


def _web_app_markup(url: str, label: str) -> str:
    return json.dumps({'inline_keyboard': [[{
        'text': label, 'web_app': {'url': url}}]]}, ensure_ascii=False)


def _url_markup(url: str, label: str) -> str:
    return json.dumps({'inline_keyboard': [[{
        'text': label, 'url': url}]]}, ensure_ascii=False)


def send_web_app_button(chat_id: int, text: str, url: str,
                        label: str = 'Открыть KoalGram Wallet') -> bool:
    if send_message(chat_id, text, reply_markup=_web_app_markup(url, label)):
        return True
    return send_message(chat_id, text, reply_markup=_url_markup(url, label))


def is_admin(telegram_user_id: int) -> bool:
    return telegram_user_id in config.AUTH_BOT_ADMIN_IDS


def _command(text: str) -> tuple[str, list[str]]:
    head = (text or '').strip().split()[0] if text else ''
    if not head.startswith('/'):
        return '', []
    head = head.split('@', 1)[0]
    parts = text.strip().split()
    args = parts[1:] if len(parts) > 1 else []
    return head.lower(), args


def handle_start(chat_id: int, telegram_user_id: int, storage: Storage) -> None:
    phone = storage.assign_bot_phone(telegram_user_id)
    numbers = storage.list_bot_phones_for_telegram(telegram_user_id)
    if len(numbers) <= 1:
        send_message(chat_id, f'Ваш номер: <b>{format_phone_display(phone)}</b>')
        return
    lines = [
        f'• <b>{format_phone_display(row["phone"])}</b>'
        for row in numbers
    ]
    send_message(chat_id, 'Ваши номера:\n' + '\n'.join(lines))


def handle_phone(chat_id: int, telegram_user_id: int, storage: Storage) -> None:
    numbers = storage.list_bot_phones_for_telegram(telegram_user_id)
    if not numbers:
        handle_start(chat_id, telegram_user_id, storage)
        return
    if len(numbers) == 1:
        send_message(
            chat_id,
            f'Ваш номер: <b>{format_phone_display(numbers[0]["phone"])}</b>',
        )
        return
    lines = [
        f'• <b>{format_phone_display(row["phone"])}</b>'
        for row in numbers
    ]
    send_message(chat_id, 'Ваши номера:\n' + '\n'.join(lines))


def handle_givenumber(chat_id: int, admin_id: int, args: list[str],
                      storage: Storage) -> None:
    if not is_admin(admin_id):
        send_message(chat_id, 'Нет доступа.')
        return
    if not args:
        send_message(
            chat_id,
            'Использование: <code>/givenumber &lt;telegram_user_id&gt; [номер]</code>',
        )
        return
    try:
        target_id = int(args[0])
    except ValueError:
        send_message(chat_id, 'Неверный Telegram ID.')
        return
    if target_id <= 0:
        send_message(chat_id, 'Неверный Telegram ID.')
        return

    phone_arg = args[1] if len(args) > 1 else None
    if not phone_arg:
        send_message(chat_id, 'Укажите номер: <code>/givenumber &lt;id&gt; &lt;номер&gt;</code>')
        return
    try:
        phone = storage.add_bot_phone(
            target_id, phone_arg, source='admin', reassign=True,
        )
    except ValueError:
        send_message(chat_id, 'Неверный номер телефона.')
        return
    display = format_phone_display(phone)
    all_numbers = storage.list_bot_phones_for_telegram(target_id)
    numbers_text = '\n'.join(
        f'• <b>{format_phone_display(row["phone"])}</b>' for row in all_numbers
    )
    send_message(
        chat_id,
        f'Пользователю <code>{target_id}</code> выдан номер: <b>{display}</b>\n\n'
        f'Все номера пользователя:\n{numbers_text}',
    )
    try:
        send_message(
            target_id,
            f'Вам выдан номер для входа: <b>{display}</b>\n\n'
            f'Все ваши номера:\n{numbers_text}\n'
            f'Используйте любой из них в KoalGram при авторизации.',
        )
    except Exception as exc:
        log.info('Could not notify target user %d: %s', target_id, exc)


def handle_help(chat_id: int, telegram_user_id: int) -> None:
    lines = [
        '<b>Команды</b>',
        '/start — получить номер для входа',
        '/phone — показать ваши номера',
        '/shop — магазин: баланс, покупка звёзд, топ',
        '/balance — баланс звёзд',
        '/buy &lt;звёзды&gt; [промокод] — купить звёзды (счёт в Telegram)',
        '/top — топ донатов',
        '/id — ваш Telegram ID',
        '',
        '<b>Блокчейн KoalGram</b>',
        '/wallet — кошелёк KoalGram, GRAM и NFT',
        '/send &lt;ссылка на NFT&gt; — перенести уникальный подарок в блокчейн',
        '/confirm &lt;код&gt; — подтвердить перенос (код приходит владельцу подарка)',
    ]
    if is_admin(telegram_user_id):
        lines.extend([
            '',
            '<b>Админ-команды</b>',
            '/lookup &lt;@user|номер&gt; — найти пользователя (профиль, баланс, статусы)',
            '/grant &lt;@user&gt; &lt;звёзды&gt; — выдать звёзды',
            '/revoke &lt;@user&gt; &lt;звёзды&gt; — снять звёзды',
            '/premium &lt;@user&gt; [дни] — выдать премиум (по умолчанию 30)',
            '/unpremium &lt;@user&gt; — снять премиум',
            '/gram &lt;@user&gt; &lt;nanoton&gt; — выдать GRAM (1e9 = 1 GRAM)',
            '/ban &lt;@user&gt; — забанить аккаунт',
            '/unban &lt;запрос&gt; — разбанить (username/номер/id)',
            '/freeze &lt;@user&gt; — заморозить аккаунт (значок заморозки)',
            '/unfreeze &lt;@user&gt; — разморозить',
            '/givenumber &lt;telegram_id&gt; &lt;номер&gt; — выдать номер для входа',
            '/cname &lt;@user|id&gt; &lt;username&gt; [цена] — выдать collectible-username',
            '/cname remove &lt;@user|id&gt; &lt;username&gt; — снять collectible-username',
            '/cname list &lt;@user|id&gt; — коллекционные юзернеймы пользователя',
            '/black &lt;ссылка на подарок&gt; — чёрный фон у NFT-подарка',
            '/rebuild_top — пересобрать кэш топа донатов',
        ])
        lines.extend(verification_help_lines())
    lines.extend([
        '',
        '<b>Фитчи сервера</b>',
        '• Подарки: магазин, апгрейды в NFT, коллекции, закрепление на профиле,',
        '  ношение как emoji-статус, перепродажа, офферы, крафт, аукционы',
        '• Кейсы в боте: /shop — покупка кейсов и вывод выигрыша',
        '• Премиум, звёзды, GRAM (TON), платные сообщения',
        '• Коллекционные юзернеймы (t.me/.../имя), анонимные номера',
        '• Верификационные значки (frame verification) — /verifications',
        '• Стикеры, кастомные эмодзи, статусы, истории, реакции, темы',
        '• Заморозка/блокировка с плашками, scam/fake метки, жалобы (report)',
        '• Верификация бизнес-аккаунтов и каналов, бейджи ботов',
    ])
    send_message(chat_id, '\n'.join(lines))


def verification_help_lines() -> list[str]:
    """One help line per verification type grantable from the bot."""
    from frame_verification.catalog import is_timed_verification

    lines = []
    for vid, item in _verification_catalog().items():
        title = item.get('title') or vid
        hint = f" — {item['description']}" if item.get('description') else ''
        if is_timed_verification(vid):
            lines.append(
                f'/{vid} &lt;@user&gt; [план] — выдать верификацию «{title}»{hint} '
                '(план: forever/month/year…, по умолчанию forever)')
        else:
            lines.append(
                f'/{vid} &lt;@user&gt; — выдать верификацию «{title}»{hint} '
                '(бессрочно)')
    lines.append('/unverify &lt;@user&gt; — снять верификацию')
    lines.append('/verifications — список доступных верификаций')
    return lines


def _resolve_shop_user_id(storage: Storage, telegram_user_id: int) -> int | None:
    """Fake-server account (users.id) bound to the real Telegram id."""
    row = storage._conn.execute(
        '''SELECT u.id FROM users u
           JOIN bot_phone_assignments bpa ON bpa.phone = u.phone
           WHERE bpa.telegram_user_id=?
           ORDER BY bpa.created_at DESC LIMIT 1''',
        (telegram_user_id,)).fetchone()
    return int(row['id']) if row else None


def handle_shop_help(chat_id: int, admin: bool) -> None:
    lines = [
        '<b>Магазин KoalGram</b>',
        '/balance — баланс звёзд',
        '/buy &lt;звёзды&gt; [промокод] — купить звёзды (счёт в Telegram)',
        '/top — топ донатов',
        '/id — ваш Telegram ID',
        '',
        '<b>Кошелёк и NFT</b>',
        '/wallet — кошелёк KoalGram, GRAM и NFT',
        '/send &lt;ссылка на NFT&gt; — перенести подарок в блокчейн',
        '/confirm &lt;код&gt; — подтвердить перенос',
    ]
    if admin:
        lines += [
            '',
            '<b>Админка</b>',
            '/help — все команды и фитчи сервера',
            '/lookup &lt;@user|номер&gt; — найти пользователя',
            '/grant &lt;@user&gt; &lt;звёзды&gt; — выдать звёзды',
            '/revoke &lt;@user&gt; &lt;звёзды&gt; — снять звёзды',
            '/premium &lt;@user&gt; [дни] — выдать премиум (30)',
            '/unpremium &lt;@user&gt; — снять премиум',
            '/gram &lt;@user&gt; &lt;nanoton&gt; — выдать граммы',
            '/ban &lt;@user&gt; / /unban &lt;запрос&gt;',
            '/freeze &lt;@user&gt; / /unfreeze &lt;@user&gt;',
            '/rebuild_top — пересобрать топ донатов',
            '/givenumber &lt;telegram_user_id&gt; [номер] — выдать номер',
        ]
        lines += verification_help_lines()
    send_message(chat_id, '\n'.join(lines))


def handle_buy(chat_id: int, telegram_user_id: int, args: list[str],
               storage: Storage) -> None:
    if not args or not args[0].lstrip('-').isdigit():
        send_message(chat_id, 'Использование: <code>/buy &lt;звёзды&gt; [промокод]</code>\n'
                              'Минимум 10 000 звёзд.')
        return
    stars = int(args[0])
    promo = args[1] if len(args) > 1 else ''
    user_id = _resolve_shop_user_id(storage, telegram_user_id)
    if not user_id:
        send_message(chat_id,
                     'Сначала войдите в клиент KoalGram с вашим номером '
                     '(номер выдаётся по /start), потом покупайте звёзды.')
        return
    res = _shop_call('create-star-order',
                     {'--user-id': user_id, '--stars': stars,
                      '--promo': promo, '--target-user-id': user_id})
    if not res.get('ok'):
        send_message(chat_id, f'⚠️ {res.get("error") or "Не удалось создать заказ"}')
        return
    invoice = _api('sendInvoice',
                   chat_id=chat_id,
                   title=f'{res["stars"]} звёзд KoalGram',
                   description=(f'Пополнение баланса: +{res["stars"]} ⭐'
                                + (f' (промокод {res["promo"]}, −{res["discountPercent"]}%)'
                                   if res.get('promo') else '')),
                   payload=res['payload'],
                   currency='XTR',
                   prices=json.dumps([{'label': f'{res["stars"]} звёзд',
                                       'amount': int(res['xtr'])}]))
    if invoice.get('ok'):
        send_message(chat_id,
                     f'🧾 Счёт на <b>{res["stars"]} ⭐</b> ({res["xtr"]} XTR) '
                     f'отправлен выше — оплатите его, звёзды зачислятся автоматически.')
    else:
        send_message(chat_id,
                     f'⚠️ Не удалось создать счёт: '
                     f'{invoice.get("description") or "ошибка"}')


def handle_balance(chat_id: int, telegram_user_id: int,
                   storage: Storage) -> None:
    user_id = _resolve_shop_user_id(storage, telegram_user_id)
    if not user_id:
        send_message(chat_id, 'Аккаунт не найден — войдите в клиент KoalGram '
                              'с вашим номером из /start.')
        return
    balance = storage.get_stars_balance(user_id)
    send_message(chat_id, f'Ваш баланс: <b>{balance} ⭐</b>')


def handle_top(chat_id: int, telegram_user_id: int) -> None:
    res = _shop_call('top-donors', {'--telegram-id': telegram_user_id})
    donors = res.get('donors') or []
    lines = ['<b>Топ донатов</b>']
    for i, d in enumerate(donors[:10], 1):
        who = d.get('displayName') or d.get('username') or \
            f"id{d.get('telegramUserId', '?')}"
        lines.append(f'{i}. {who} — <b>{d.get("stars", 0)} ⭐</b>')
    viewer = res.get('viewer')
    if isinstance(viewer, dict) and viewer:
        lines.append('')
        lines.append(f'Ваше место: <b>{viewer.get("rank", "—")}</b> '
                     f'({viewer.get("stars", 0)} ⭐)')
    send_message(chat_id, '\n'.join(lines))


ADMIN_COMMANDS = {
    '/lookup': ('admin-lookup', ('username',)),
    '/grant': ('admin-grant-stars', ('username', 'amount')),
    '/revoke': ('admin-revoke-stars', ('username', 'amount')),
    '/premium': ('admin-grant-premium', ('username', 'days')),
    '/unpremium': ('admin-revoke-premium', ('username',)),
    '/gram': ('admin-grant-gram', ('username', 'nanoton')),
    '/ban': ('admin-ban', ('username',)),
    '/unban': ('admin-unban', ('query',)),
    '/freeze': ('admin-freeze', ('username',)),
    '/unfreeze': ('admin-unfreeze', ('username',)),
}


def handle_grant_verification(chat_id: int, admin_id: int, vtype: str,
                              args: list[str]) -> None:
    """Admin grant of a frame verification: /major @username [plan]."""
    catalog = _verification_catalog()
    vdef = catalog.get(vtype)
    if not vdef:
        send_message(chat_id, f'Неизвестная верификация: {vtype}')
        return
    if not args:
        from frame_verification.catalog import is_timed_verification
        usage = ('Использование: <code>/' + vtype +
                 ' &lt;@username&gt; [план]</code>\nПланы: forever (по умолчанию), '
                 'day, week, month, year')
        if not is_timed_verification(vtype):
            usage = ('Использование: <code>/' + vtype +
                     ' &lt;@username&gt;</code>\nБессрочная верификация.')
        send_message(chat_id, usage)
        return
    from frame_verification.catalog import get_plan_label
    from frame_verification.service import grant_verification

    username = args[0]
    plan = (args[1].lower() if len(args) > 1 else 'forever')
    payment_id = f'admin-{vtype}-{username.strip().lstrip("@").lower()}-{int(time.time())}'
    try:
        result = grant_verification(
            username=username,
            verification_type=vtype,
            plan=plan,
            telegram_payment_id=payment_id,
            stars_amount=0,
            buyer_telegram_id=admin_id,
            force=True,
        )
    except Exception as exc:
        log.exception('verification grant failed')
        send_message(chat_id, f'⚠️ Ошибка: {exc}')
        return
    if not result.get('ok'):
        send_message(chat_id, f'⚠️ {result.get("error") or "Не удалось выдать верификацию"}')
        return
    title = result.get('verification_title') or vdef.get('title') or vtype
    if result.get('duplicate'):
        send_message(chat_id, 'Эта верификация уже выдана (повтор запроса).')
        return
    expires = result.get('expires_at')
    plan_label = get_plan_label(vtype, plan)
    send_message(
        chat_id,
        f'✅ Верификация <b>«{title}»</b> выдана <b>@{result.get("username")}</b>\n'
        f'Тип: {vtype} · План: {plan_label}'
        + ('' if not expires else f'\nДействует до: {time.strftime("%Y-%m-%d %H:%M", time.localtime(expires))}'))


def _resolve_bot_target(storage: Storage, raw: str):
    """Resolve a fake-server user by id, primary or collectible username."""
    raw = (raw or '').strip()
    if not raw:
        return None
    if raw.lstrip('-').isdigit():
        return storage.get_user_by_id(int(raw))
    username = raw.lstrip('@').lower()
    row = storage._conn.execute(
        'SELECT * FROM users WHERE lower(username)=?', (username,)
    ).fetchone()
    if row:
        return dict(row)
    owner = storage.get_collectible_username_owner(username)
    if owner:
        return storage.get_user_by_id(int(owner['user_id']))
    return None


def handle_collectible_username(chat_id: int, admin_id: int,
                                args: list[str], storage: Storage) -> None:
    """Grant/remove/list collectible usernames: /cname @user name [price]."""
    from db.storage import (
        is_valid_collectible_username, normalize_collectible_username,
    )
    from handlers.collectible_usernames import (
        MAX_ACTIVE_USERNAMES, count_active_usernames,
        notify_username_changed,
    )

    usage = ('Использование:\n'
             '<code>/cname &lt;@user|id&gt; &lt;username&gt; [цена]</code>\n'
             '<code>/cname remove &lt;@user|id&gt; &lt;username&gt;</code>\n'
             '<code>/cname list &lt;@user|id&gt;</code>')
    if not args:
        send_message(chat_id, usage)
        return

    action = args[0].lower()
    if action in ('remove', 'del', 'delete'):
        if len(args) < 3:
            send_message(chat_id, usage)
            return
        user = _resolve_bot_target(storage, args[1])
        if not user:
            send_message(chat_id, 'Пользователь не найден.')
            return
        username = normalize_collectible_username(args[2])
        try:
            storage.remove_collectible_username(int(user['id']), username)
        except ValueError as exc:
            send_message(chat_id, f'⚠️ Ошибка: <code>{exc}</code>')
            return
        notify_username_changed(
            storage, int(user['id']), notify_contacts=True)
        send_message(
            chat_id,
            f'✅ Collectible <code>@{username}</code> снят с '
            f'<code>{user["id"]}</code> @{user.get("username") or ""}')
        return

    if action in ('list', 'show'):
        if len(args) < 2:
            send_message(chat_id, usage)
            return
        user = _resolve_bot_target(storage, args[1])
        if not user:
            send_message(chat_id, 'Пользователь не найден.')
            return
        rows = storage.get_collectible_usernames(
            int(user['id']), include_inactive=False)
        lines = [f'Коллекционные юзернеймы <code>{user["id"]}</code> '
                 f'@{user.get("username") or ""}:']
        if not rows:
            lines.append('— нет')
        for row in rows:
            price = f' · {row["price"]}' if row.get('price') else ''
            lines.append(f'• <code>@{row["username"]}</code>{price}')
        send_message(chat_id, '\n'.join(lines))
        return

    if len(args) < 2:
        send_message(chat_id, usage)
        return
    user = _resolve_bot_target(storage, args[0])
    if not user:
        send_message(chat_id, 'Пользователь не найден.')
        return
    username = normalize_collectible_username(args[1])
    if not is_valid_collectible_username(username):
        send_message(chat_id, 'Некорректный collectible username.')
        return
    price = args[2] if len(args) > 2 else ''

    existing = storage.get_collectible_username_owner(username)
    same_owner = bool(existing and int(existing['user_id']) == int(user['id']))
    if existing and not same_owner:
        send_message(chat_id, f'⚠️ <code>@{username}</code> уже занят.')
        return
    if not same_owner and count_active_usernames(
            storage, int(user['id'])) >= MAX_ACTIVE_USERNAMES:
        send_message(chat_id,
                     f'⚠️ У пользователя уже {MAX_ACTIVE_USERNAMES} активных '
                     'юзернеймов (максимум).')
        return

    try:
        row = storage.grant_collectible_username(
            int(user['id']), username, price=price,
            granted_by=int(admin_id), active=True)
    except ValueError as exc:
        send_message(chat_id, f'⚠️ Ошибка: <code>{exc}</code>')
        return
    notify_username_changed(storage, int(user['id']), notify_contacts=True)
    send_message(
        chat_id,
        '✅ Выдан collectible username\n'
        f'Пользователь: <code>{user["id"]}</code> @{user.get("username") or ""}\n'
        f'Username: <code>@{row.get("username") or username}</code>'
        + (f'\nЦена: <code>{price}</code>' if price else ''))


def _gift_slug_from_value(value: str) -> str:
    """Extract the unique gift slug from an nft link or a bare slug."""
    text = (value or '').strip()
    if not text:
        return ''
    match = re.search(r'(?:t\.me|telegram\.me)/nft/([A-Za-z0-9_-]+)', text)
    if match:
        return match.group(1)
    match = re.search(r'tg://nft\?slug=([A-Za-z0-9_-]+)', text)
    if match:
        return match.group(1)
    return text.split('?', 1)[0].strip('/').split('/')[-1]


def handle_black(chat_id: int, args: list[str], storage: Storage) -> None:
    """Set a unique gift's backdrop to solid black: /black <nft link>."""
    if not args:
        send_message(
            chat_id,
            'Использование: <code>/black &lt;ссылка на подарок&gt;</code>\n'
            'Например: <code>/black https://t.me/nft/ResistanceDog-65</code>')
        return
    slug = _gift_slug_from_value(' '.join(args))
    if not slug:
        send_message(chat_id, '⚠️ Не удалось разобрать ссылку на подарок.')
        return
    row = storage.find_saved_star_gift_by_slug(slug)
    if not row:
        send_message(chat_id, f'⚠️ Подарок <code>{slug}</code> не найден.')
        return
    try:
        saved, _attr = storage.set_saved_star_gift_black_backdrop(
            int(row['saved_id']))
    except ValueError as exc:
        send_message(chat_id, f'⚠️ Ошибка: <code>{exc}</code>')
        return
    send_message(
        chat_id,
        '✅ Фон подарка изменён на <b>чёрный</b>\n'
        f'Подарок: <code>{saved.get("unique_slug") or slug}</code>\n'
        f'Владелец: <code>{saved.get("owner_user_id")}</code>\n'
        'Переоткройте профиль/подарок, чтобы увидеть обновление.')


def _nft_webapp_url(nft: dict) -> str:
    address = str(nft.get('address') or '')
    return (f'{config.AIRGRAM_WALLET_URL}?nft='
            f'{urllib.parse.quote(address, safe="")}')


def handle_wallet(chat_id: int, telegram_user_id: int,
                  storage: Storage) -> None:
    user_id = chain_service.resolve_user_id(storage, telegram_user_id)
    if not user_id:
        send_message(chat_id, 'Аккаунт KoalGram не найден. Войдите в клиент '
                              'KoalGram с номером из /start, затем повторите.')
        return
    wallet = chain_service.get_linked_wallet(storage, user_id)
    if not wallet:
        send_web_app_button(
            chat_id,
            '<b>Кошелёк KoalGram</b>\n\n'
            'У вас пока нет кошелька. Откройте приложение и создайте его — '
            'уникальный TON-адрес привяжется к вашему кошельку KoalGram '
            'автоматически. После этого сюда можно переносить NFT-подарки '
            'командой <code>/send</code>.',
            config.AIRGRAM_WALLET_URL, 'Создать кошелёк')
        return
    try:
        address = chain_core.parse_address(wallet['address'])
        friendly = chain_core.friendly_address(address)
    except chain_core.ChainError:
        send_web_app_button(
            chat_id,
            '⚠️ Сохранённый адрес кошелька повреждён. Привяжите кошелёк заново '
            'в приложении.', config.AIRGRAM_WALLET_URL, 'Открыть приложение')
        return
    count = chain_core.count_nfts(address)
    info = chain_core.chain_info()
    gram = config.format_gram_amount(storage.get_ton_balance(user_id))
    text = (
        '<b>Кошелёк KoalGram</b>\n'
        f'Адрес: <code>{friendly}</code>\n'
        f'GRAM: <b>{gram}</b>\n'
        f'NFT в блокчейне: <b>{count}</b>\n'
        f'Сеть: KoalGram Chain · блок <b>{info["height"]}</b>\n\n'
        'Перенести подарок: <code>/send &lt;ссылка на NFT&gt;</code>')
    send_web_app_button(chat_id, text, config.AIRGRAM_WALLET_URL,
                        'Открыть кошелёк')


def handle_send(chat_id: int, telegram_user_id: int, args: list[str],
                storage: Storage) -> None:
    if not args:
        send_message(
            chat_id,
            'Использование: <code>/send &lt;ссылка на уникальный подарок&gt;</code>\n'
            'Например: <code>/send https://t.me/nft/plush-pepe-7</code>\n\n'
            'Подарок будет перенесён в блокчейн KoalGram на ваш кошелёк.')
        return
    slug = _gift_slug_from_value(' '.join(args))
    if not slug:
        send_message(chat_id, '⚠️ Не удалось разобрать ссылку на подарок.')
        return
    result = chain_service.start_send(storage, telegram_user_id, slug)
    if not result.get('ok'):
        if result.get('status') == 'already':
            nft = result.get('nft') or {}
            text = (
                'ℹ️ <b>Подарок уже в блокчейне KoalGram</b>\n'
                f'Подарок: <b>{html.escape(nft.get("title") or slug)}</b>\n'
                f'Владелец: <code>{nft.get("friendlyAddress") or ""}</code>')
            send_web_app_button(chat_id, text, _nft_webapp_url(nft),
                                'Открыть NFT')
            return
        send_message(chat_id, f'⚠️ {html.escape(str(result.get("error") or "Ошибка"))}')
        return
    if result.get('status') == 'no_wallet':
        owner_tg = int(result.get('ownerTgId') or 0)
        saved = result.get('saved') or {}
        owner_user_id = int(saved.get('owner_user_id') or 0)
        initiator_user_id = chain_service.resolve_user_id(
            storage, telegram_user_id) or 0
        is_owner = initiator_user_id == owner_user_id
        title = html.escape(result.get('title') or 'подарок')
        if owner_tg:
            owner_text = (
                '<b>Нужен кошелёк KoalGram</b>\n\n'
                f'Подарок «{title}» пока нельзя перенести в блокчейн: '
                'у вашего аккаунта нет кошелька.\n\n'
                'Откройте приложение и создайте кошелёк — уникальный '
                'TON-адрес привяжется к аккаунту KoalGram автоматически. '
                'После этого повторите <code>/send</code>.')
            send_web_app_button(owner_tg, owner_text,
                                result.get('walletUrl') or
                                config.AIRGRAM_WALLET_URL, 'Создать кошелёк')
        if not is_owner:
            send_message(
                chat_id,
                f'⚠️ У владельца подарка «{title}» ещё нет кошелька KoalGram. '
                'Мы отправили ему инструкцию в бот. Как только кошелёк будет '
                'создан, повторите <code>/send</code>.')
        return
    code = str(result.get('code') or '')
    owner_tg = int(result.get('ownerTgId') or 0)
    title = html.escape(result.get('title') or 'подарок')
    wallet_address = str(result.get('walletAddress') or '')
    try:
        wallet_friendly = chain_core.friendly_address(wallet_address)
    except chain_core.ChainError:
        wallet_friendly = wallet_address
    if result.get('ownerIsInitiator'):
        who = 'вы'
    else:
        initiator = storage.get_user_by_id(
            int(result.get('initiatorUserId') or 0)) or {}
        username = initiator.get('username') or ''
        name = initiator.get('first_name') or ''
        who = (f'@{html.escape(str(username))}' if username
               else html.escape(str(name or telegram_user_id)))
        who += f' (ID {telegram_user_id})'
    code_text = (
        '🔐 <b>Код подтверждения KoalGram</b>\n'
        f'<code>{code}</code>\n\n'
        f'Подарок: <b>{title}</b>\n'
        f'Запрос от: {who}\n'
        f'Кошелёк: <code>{wallet_friendly}</code>\n\n'
        'Код действует 10 минут. Никому не сообщайте его, если не хотите '
        'перенести подарок в блокчейн KoalGram.')
    if result.get('ownerIsInitiator'):
        if not send_message(chat_id, code_text + '\n\nВведите: '
                            f'<code>/confirm {code}</code>'):
            chain_core.cancel_send_request(int(result['requestId']))
            send_message(chat_id, '⚠️ Не удалось отправить код. Попробуйте ещё раз.')
        return
    if not send_message(owner_tg, code_text):
        chain_core.cancel_send_request(int(result['requestId']))
        send_message(chat_id, '⚠️ Не удалось отправить код владельцу подарка. '
                              'Попросите его нажать /start в @KoalGramApp и '
                              'повторите запрос.')
        return
    send_message(
        chat_id,
        f'🔐 Код отправлен владельцу подарка «{title}» в @KoalGramApp.\n'
        'Попросите у него код и введите:\n'
        '<code>/confirm &lt;код&gt;</code>\n\n'
        'Код действует 10 минут.')


def handle_confirm(chat_id: int, telegram_user_id: int, args: list[str],
                   storage: Storage) -> None:
    code = (args[0] if args else '').strip()
    if not code:
        send_message(chat_id, 'Использование: <code>/confirm &lt;код из 6 цифр&gt;</code>')
        return
    result = chain_service.confirm_send(storage, telegram_user_id, code)
    if not result.get('ok'):
        message = result.get('message') or result.get('error') or 'Ошибка'
        send_message(chat_id, f'⚠️ {html.escape(str(message))}')
        return
    nft = result.get('nft') or {}
    title = html.escape(nft.get('title') or 'NFT')
    owner_address = nft.get('friendlyAddress') or ''
    tx_hash = str(result.get('txHash') or '')
    block_height = (result.get('block') or {}).get('height')
    lines = ['✅ <b>NFT перенесён в блокчейн KoalGram</b>']
    if result.get('already'):
        lines.insert(0, 'ℹ️ Подарок уже находился в блокчейне.')
    lines += [
        f'Подарок: <b>{title}</b>',
        f'Владелец: <code>{owner_address}</code>',
    ]
    if block_height:
        lines.append(f'Блок: <b>{block_height}</b>')
    if tx_hash:
        lines.append(f'Транзакция: <code>{tx_hash[:20]}…</code>')
    text = '\n'.join(lines)
    send_web_app_button(chat_id, text, _nft_webapp_url(nft), 'Открыть NFT')
    owner_tg = int(result.get('ownerTgId') or 0)
    if owner_tg and owner_tg != chat_id:
        owner_text = (
            '🎁 <b>Подарок перенесён в блокчейн KoalGram</b>\n'
            f'Подарок: <b>{title}</b>\n'
            f'Теперь в клиенте владелец — кошелёк <code>{owner_address}</code>.\n'
            'NFT доступен в WebApp.')
        send_web_app_button(owner_tg, owner_text, _nft_webapp_url(nft),
                            'Открыть NFT')


def handle_unverify(chat_id: int, args: list[str]) -> None:
    if not args:
        send_message(chat_id, 'Использование: <code>/unverify &lt;@username&gt;</code>')
        return
    from frame_verification.push import enqueue_verification_push
    from frame_verification.service import lookup_username

    res = lookup_username(args[0])
    if not res.get('ok'):
        send_message(chat_id, f'⚠️ {res.get("error") or "Не найдено"}')
        return
    storage = Storage.open_tool()
    peer_id = int(res['user_id'])
    storage.delete_bot_verification(peer_id)
    enqueue_verification_push(storage, peer_id)
    send_message(chat_id, f'✅ Верификация снята: {res.get("full_name")} '
                          f'(<code>{peer_id}</code>)')


def handle_verifications(chat_id: int) -> None:
    from frame_verification.catalog import get_verification_price, is_timed_verification
    catalog = _verification_catalog()
    if not catalog:
        send_message(chat_id, 'Каталог верификаций пуст (verifications.json не найден).')
        return
    lines = ['<b>Доступные верификации</b>']
    for vid, item in catalog.items():
        title = item.get('title') or vid
        hint = f" — {item['description']}" if item.get('description') else ''
        if is_timed_verification(vid):
            plans = ', '.join(
                f'{pid}={get_verification_price(vid, pid)}★'
                for pid in (item.get('plans') or {}))
            lines.append(f'/{vid} — «{title}»{hint} (по времени: {plans})')
        else:
            price = item.get('price', 0)
            if item.get('adminOnly'):
                lines.append(f'/{vid} — «{title}»{hint} (админ-выдача, навсегда)')
            else:
                lines.append(f'/{vid} — «{title}»{hint} ({price}★, навсегда)')
    lines.append('')
    lines.append('Выдача: <code>/' + catalog_keys_hint(catalog) +
                 ' &lt;@username&gt; [план]</code>')
    send_message(chat_id, '\n'.join(lines))


def catalog_keys_hint(catalog: dict) -> str:
    return '|'.join(f'/{vid}' for vid in catalog)


def handle_admin_command(chat_id: int, cmd: str, args: list[str]) -> None:
    spec = ADMIN_COMMANDS[cmd]
    command, fields = spec
    days_default = {'days': 30}
    values: list[str] = []
    for i, field in enumerate(fields):
        if field in days_default and (i >= len(args) or not args[i]):
            values.append(str(days_default[field]))
        elif i < len(args):
            values.append(args[i])
        else:
            send_message(
                chat_id,
                'Использование: <code>' + cmd + ' ' +
                ' '.join(f'&lt;{f}&gt;' for f in fields) + '</code>')
            return
    flags = {f'--{field.replace("_", "-")}': value
             for field, value in zip(fields, values)}
    result = _shop_call(command, flags)
    if isinstance(result, dict) and result.get('ok') is not None:
        text = json.dumps(result, ensure_ascii=False, default=str)
        send_message(chat_id, f'<pre>{text[:3500]}</pre>')
    else:
        send_message(chat_id, f'<pre>{str(result)[:3500]}</pre>')


def dispatch_message(chat_id: int, user_id: int, text: str,
                     storage: Storage) -> None:
    cmd, args = _command(text)
    if cmd in ('/start',):
        handle_start(chat_id, user_id, storage)
    elif cmd in ('/phone',):
        handle_phone(chat_id, user_id, storage)
    elif cmd in ('/givenumber',):
        handle_givenumber(chat_id, user_id, args, storage)
    elif cmd in ('/help',):
        handle_help(chat_id, user_id)
    elif cmd in ('/shop',):
        handle_shop_help(chat_id, is_admin(user_id))
    elif cmd in ('/buy',):
        handle_buy(chat_id, user_id, args, storage)
    elif cmd in ('/balance',):
        handle_balance(chat_id, user_id, storage)
    elif cmd in ('/top',):
        handle_top(chat_id, user_id)
    elif cmd in ('/id',):
        send_message(chat_id, f'Ваш Telegram ID: <code>{user_id}</code>')
    elif cmd in ('/wallet', '/airgram'):
        handle_wallet(chat_id, user_id, storage)
    elif cmd in ('/send', '/nft'):
        handle_send(chat_id, user_id, args, storage)
    elif cmd in ('/confirm',):
        handle_confirm(chat_id, user_id, args, storage)
    elif cmd in ('/unverify',):
        if not is_admin(user_id):
            send_message(chat_id, 'Нет доступа.')
            return
        handle_unverify(chat_id, args)
    elif cmd in ('/black',):
        if not is_admin(user_id):
            send_message(chat_id, 'Нет доступа.')
            return
        handle_black(chat_id, args, storage)
    elif cmd in ('/cname', '/collectible'):
        if not is_admin(user_id):
            send_message(chat_id, 'Нет доступа.')
            return
        handle_collectible_username(chat_id, user_id, args, storage)
    elif cmd in ('/verifications',):
        handle_verifications(chat_id)
    elif cmd in ADMIN_COMMANDS:
        if not is_admin(user_id):
            send_message(chat_id, 'Нет доступа.')
            return
        handle_admin_command(chat_id, cmd, args)
    elif cmd.lstrip('/') in _verification_catalog():
        # Frame verification grant commands, e.g. /major @username
        if not is_admin(user_id):
            send_message(chat_id, 'Нет доступа.')
            return
        handle_grant_verification(chat_id, user_id, cmd.lstrip('/'), args)
    elif not cmd and re.fullmatch(r'\d{6}', text.strip()):
        handle_confirm(chat_id, user_id, [text.strip()], storage)


def handle_payment_update(update: dict) -> None:
    """Shop Stars payments: approve pre-checkout, credit on success."""
    try:
        pre = update.get('pre_checkout_query')
        if pre:
            payload = pre.get('invoice_payload', '')
            amount = int(pre.get('total_amount', 0))
            check = _shop_call(
                'validate-payment',
                {'--payload': payload, '--xtr': amount})
            ok = bool(check.get('ok'))
            _api('answerPreCheckoutQuery',
                 pre_checkout_query_id=pre['id'],
                 ok=ok,
                 error_message='' if ok else str(check.get('error') or 'Заказ недействителен'))
            return
        msg = update.get('message') or {}
        pay = msg.get('successful_payment')
        if pay:
            payload = pay.get('invoice_payload', '')
            charge_id = (pay.get('telegram_payment_charge_id') or
                         pay.get('provider_payment_charge_id') or '')
            buyer = int(((msg.get('from') or {}).get('id')) or 0)
            result = _shop_call(
                'complete-payment',
                {'--payload': payload, '--payment-id': charge_id,
                 '--buyer-telegram-id': buyer})
            chat_id = (msg.get('chat') or {}).get('id')
            if chat_id:
                if result.get('ok'):
                    send_message(
                        chat_id,
                        f'✅ Оплата принята: <b>+{result.get("stars", 0)} ⭐</b>\n'
                        f'Новый баланс: <b>{result.get("balance", 0)} ⭐</b>')
                else:
                    send_message(
                        chat_id,
                        f'⚠️ Платёж получен, но заказ не зачтён: '
                        f'{result.get("error") or "неизвестная ошибка"}\n'
                        f'Payload: <code>{payload}</code>')
    except Exception:
        log.exception('Payment update handling failed')


def _shop_call(command: str, flags: dict) -> dict:
    """Call the shop bridge CLI (separate process, its own DB connection)."""
    import subprocess
    argv = [sys.executable, '-m', 'shop_webapp.cli', command]
    for key, value in flags.items():
        argv += [key, str(value)]
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             cwd=os.path.dirname(os.path.abspath(__file__)),
                             timeout=90)
        return json.loads(out.stdout or '{}')
    except Exception:
        log.exception('shop bridge call failed')
        return {'ok': False, 'error': 'internal error'}


def run_bot() -> None:
    if not config.AUTH_BOT_TOKEN:
        print('Set MYTELEGRAM_AUTH_BOT_TOKEN or create .telegram_auth_bot.env')
        sys.exit(1)

    if os.environ.get('SHOP_BOT_TOKEN'):
        print(
            'SHOP_BOT_TOKEN is set — use /home/user/frag/shop_bot.py instead.\n'
            'Running auth_bot.py alongside shop_bot.py breaks Telegram polling.',
        )
        sys.exit(1)

    storage = Storage.open_tool()
    try:
        chain_core.ensure_ready()
        info = chain_core.chain_info()
        log.info('KoalGram chain ready: %s, height=%s, nfts=%s',
                 info['chainId'], info['height'], info['nfts'])
    except Exception:
        log.exception('KoalGram chain initialization failed')
    offset = 0
    log.info('Auth bot started (admins: %s)',
             ', '.join(str(x) for x in sorted(config.AUTH_BOT_ADMIN_IDS)))

    while True:
        try:
            result = _api('getUpdates', offset=offset, timeout=30)
            for update in result.get('result', []):
                offset = update['update_id'] + 1
                if 'pre_checkout_query' in update or \
                        ((update.get('message') or {}).get('successful_payment')):
                    handle_payment_update(update)
                    continue
                message = update.get('message') or {}
                text = (message.get('text') or '').strip()
                chat = message.get('chat') or {}
                user = message.get('from') or {}
                chat_id = chat.get('id')
                user_id = user.get('id')
                if not chat_id or not user_id or not text:
                    continue
                dispatch_message(chat_id, user_id, text, storage)
        except urllib.error.HTTPError as exc:
            log.error('Telegram HTTP %s: %s', exc.code, exc.read())
            time.sleep(5)
        except Exception:
            log.exception('Polling error')
            time.sleep(5)


if __name__ == '__main__':
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    run_bot()
