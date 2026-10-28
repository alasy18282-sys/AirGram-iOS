"""High-level KoalGram wallet flow: linking, /send requests and gift minting."""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import config
from airgram_chain import core
from airgram_chain import virtual as virtual_nfts
from airgram_chain.media import ensure_media

log = logging.getLogger(__name__)

GIFT_SLUG_HELP = (
    'Укажите ссылку на уникальный подарок, например:\n'
    '<code>/send https://t.me/nft/plush-pepe-7</code>'
)

# Minimal fixed network fee for GRAM transfers (0.0001 GRAM).
GRAM_SEND_FEE_NANO = 100_000


def ensure_wallet_table(storage) -> None:
    storage._conn.execute('''
        CREATE TABLE IF NOT EXISTS airgram_ton_wallet_links (
            user_id INTEGER PRIMARY KEY,
            address TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    ''')
    storage._conn.execute('''
        CREATE TABLE IF NOT EXISTS airgram_gram_pending (
            address TEXT PRIMARY KEY,
            amount_nano INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    ''')
    storage._conn.commit()


def add_pending_gram(storage, address: str, amount_nano: int) -> int:
    """Hold GRAM for an address that is not linked to a KoalGram account yet."""
    if amount_nano <= 0:
        return 0
    ensure_wallet_table(storage)
    raw = core.parse_address(address)
    now = int(time.time())
    storage._conn.execute(
        '''INSERT INTO airgram_gram_pending (address, amount_nano, updated_at)
           VALUES (?,?,?)
           ON CONFLICT(address) DO UPDATE SET
               amount_nano=amount_nano+excluded.amount_nano,
               updated_at=excluded.updated_at''',
        (raw, int(amount_nano), now))
    storage._conn.commit()
    row = storage._conn.execute(
        'SELECT amount_nano FROM airgram_gram_pending WHERE address=?',
        (raw,)).fetchone()
    return int(row['amount_nano']) if row else 0


def claim_pending_gram(storage, user_id: int, address: str) -> int:
    """Credit pending address-level GRAM to a freshly linked KoalGram account."""
    ensure_wallet_table(storage)
    try:
        raw = core.parse_address(address)
    except core.ChainError:
        return 0
    row = storage._conn.execute(
        'SELECT amount_nano FROM airgram_gram_pending WHERE address=?',
        (raw,)).fetchone()
    if not row or int(row['amount_nano']) <= 0:
        return 0
    amount = int(row['amount_nano'])
    storage._conn.execute(
        'DELETE FROM airgram_gram_pending WHERE address=?', (raw,))
    storage._conn.commit()
    storage.adjust_ton_balance(int(user_id), amount,
                              title='GRAM received')
    return amount


def get_linked_wallet(storage, user_id: int) -> Optional[dict]:
    ensure_wallet_table(storage)
    row = storage._conn.execute(
        'SELECT * FROM airgram_ton_wallet_links WHERE user_id=?',
        (int(user_id),)).fetchone()
    return dict(row) if row else None


def get_wallet_owner(storage, address: str) -> Optional[dict]:
    ensure_wallet_table(storage)
    try:
        raw = core.parse_address(address)
    except core.ChainError:
        return None
    rows = storage._conn.execute(
        'SELECT * FROM airgram_ton_wallet_links').fetchall()
    for row in rows:
        try:
            if core.parse_address(row['address']) == raw:
                return dict(row)
        except core.ChainError:
            continue
    return None


def link_wallet(storage, user_id: int, address: str) -> dict:
    ensure_wallet_table(storage)
    try:
        raw = core.parse_address(address)
    except core.ChainError as exc:
        return {'ok': False, 'error': exc.message}
    existing = get_linked_wallet(storage, user_id)
    if existing:
        try:
            current = core.parse_address(existing['address'])
        except core.ChainError:
            current = existing['address']
        if current == raw:
            claimed = claim_pending_gram(storage, int(user_id), raw)
            return {'ok': True, 'address': raw,
                    'friendlyAddress': core.friendly_address(raw),
                    'unchanged': True, 'claimedNano': claimed}
        owned = core.count_nfts(current)
        if owned > 0:
            return {'ok': False, 'error': (
                'На текущем кошельке уже есть NFT KoalGram. '
                'Смена адреса заблокирована, чтобы не потерять доступ к ним.')}
        pending = _pending_for_user(storage, user_id)
        if pending:
            return {'ok': False, 'error': (
                'Есть незавершённый запрос /send. '
                'Подтвердите его или подождите 10 минут.')}
    owner = get_wallet_owner(storage, raw)
    if owner and int(owner['user_id']) != int(user_id):
        return {'ok': False, 'error': (
            'Этот кошелёк уже привязан к другому аккаунту KoalGram.')}
    account_nft_count = core.count_nfts(raw)
    if account_nft_count and not owner:
        return {'ok': False, 'error': (
            'Этот кошелёк уже содержит NFT KoalGram другого аккаунта.')}
    now = int(time.time())
    try:
        storage._conn.execute(
            '''INSERT INTO airgram_ton_wallet_links
               (user_id, address, created_at, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                   address=excluded.address, updated_at=excluded.updated_at''',
            (int(user_id), raw, now, now))
        storage._conn.commit()
    except Exception:
        storage._conn.rollback()
        return {'ok': False, 'error': (
            'Этот кошелёк уже привязан к другому аккаунту KoalGram.')}
    claimed = claim_pending_gram(storage, int(user_id), raw)
    return {'ok': True, 'address': raw,
            'friendlyAddress': core.friendly_address(raw),
            'claimedNano': claimed}


def unlink_not_supported() -> dict:
    return {'ok': False, 'error': (        'Отвязать кошелёк нельзя: адрес — это ваш аккаунт в блокчейне KoalGram.')}


def _pending_for_user(storage, user_id: int) -> bool:
    conn = core.connect()
    try:
        row = conn.execute(
            '''SELECT 1 FROM chain_send_requests
               WHERE owner_user_id=? AND status IN ('pending','confirming')
               LIMIT 1''', (int(user_id),)).fetchone()
        return bool(row)
    finally:
        conn.close()


def _table_exists(storage, name: str) -> bool:
    row = storage._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone()
    return bool(row)


def resolve_user_id(storage, telegram_user_id: int) -> Optional[int]:
    row = storage._conn.execute(
        '''SELECT u.id FROM users u
           JOIN bot_phone_assignments bpa ON bpa.phone = u.phone
           WHERE bpa.telegram_user_id=?
           ORDER BY bpa.created_at DESC LIMIT 1''',
        (int(telegram_user_id),)).fetchone()
    if row:
        return int(row['id'])
    try:
        row = storage._conn.execute(
            '''SELECT user_id FROM shop_webapp_sessions
               WHERE telegram_user_id=? ORDER BY created_at DESC LIMIT 1''',
            (int(telegram_user_id),)).fetchone()
    except Exception:
        row = None
    return int(row['user_id']) if row else None


def resolve_telegram_id(storage, user_id: int) -> Optional[int]:
    row = storage._conn.execute(
        '''SELECT bpa.telegram_user_id FROM bot_phone_assignments bpa
           JOIN users u ON u.phone = bpa.phone
           WHERE u.id=? ORDER BY bpa.created_at DESC LIMIT 1''',
        (int(user_id),)).fetchone()
    if row:
        return int(row['telegram_user_id'])
    if _table_exists(storage, 'telegram_phones'):
        row = storage._conn.execute(
            '''SELECT tp.telegram_user_id FROM telegram_phones tp
               JOIN users u ON u.phone = tp.phone
               WHERE u.id=? ORDER BY tp.created_at DESC LIMIT 1''',
            (int(user_id),)).fetchone()
        if row:
            return int(row['telegram_user_id'])
    if _table_exists(storage, 'shop_webapp_sessions'):
        row = storage._conn.execute(
            '''SELECT telegram_user_id FROM shop_webapp_sessions
               WHERE user_id=? ORDER BY created_at DESC LIMIT 1''',
            (int(user_id),)).fetchone()
        if row:
            return int(row['telegram_user_id'])
    return None


def resolve_attrs(storage, saved: dict) -> dict:
    out: dict = {}
    for attr_type, column in (('model', 'model_attr_id'),
                              ('pattern', 'pattern_attr_id'),
                              ('backdrop', 'backdrop_attr_id')):
        attr_id = int(saved.get(column) or 0)
        if attr_id:
            attr = storage.get_star_gift_upgrade_attr_by_id(attr_id)
            if attr and attr.get('attr_type') == attr_type:
                out[attr_type] = attr
    if 'model' not in out or 'pattern' not in out:
        pool = storage.list_star_gift_upgrade_attrs(int(saved['gift_id']))
        for attr_type in ('model', 'pattern', 'backdrop'):
            if attr_type in out:
                continue
            candidates = [
                a for a in pool
                if a.get('attr_type') == attr_type
                and not (attr_type == 'model' and int(a.get('crafted') or 0))
            ]
            if candidates:
                out[attr_type] = candidates[0]
    return out


def _gift_meta(storage, saved: dict) -> dict:
    meta = storage.get_star_gift(int(saved['gift_id']))
    if meta:
        return meta
    slug = str(saved.get('unique_slug') or '')
    title = slug.rsplit('-', 1)[0].replace('-', ' ').title() if '-' in slug else 'Gift'
    return {'gift_id': int(saved['gift_id']), 'title': title or 'Gift',
            'stars': int(saved.get('stars') or 0)}


def _active_rental(storage, slug: str) -> bool:
    if not _table_exists(storage, 'gift_rentals'):
        return False
    row = storage._conn.execute(
        '''SELECT 1 FROM gift_rentals
           WHERE item_type='gift' AND item_key=?
             AND status IN ('listed','rented') LIMIT 1''', (slug,)).fetchone()
    return bool(row)


def prepare_gift(storage, initiator_tg_id: int, slug: str) -> dict:
    slug = (slug or '').strip().lower()
    saved = storage.find_saved_star_gift_by_slug(slug)
    if not saved:
        return {'ok': False, 'error': 'Подарок с такой ссылкой не найден.'}
    slug = str(saved.get('unique_slug') or slug).lower()
    if not int(saved.get('upgraded') or 0):
        return {'ok': False,
                'error': 'Это не уникальный подарок. Сначала улучшите его.'}
    if int(saved.get('burned') or 0):
        return {'ok': False, 'error': 'Подарок сожжён и не может быть перенесён.'}
    owner = storage.get_user_by_id(int(saved['owner_user_id']))
    if not owner:
        return {'ok': False, 'error': 'Владелец подарка не найден в KoalGram.'}
    initiator_user_id = resolve_user_id(storage, int(initiator_tg_id)) or 0
    owner_tg_id = resolve_telegram_id(storage, int(saved['owner_user_id'])) or 0
    if not owner_tg_id and initiator_user_id == int(saved['owner_user_id']):
        owner_tg_id = int(initiator_tg_id)
    wallet = get_linked_wallet(storage, int(saved['owner_user_id']))
    wallet_raw = ''
    if wallet:
        try:
            wallet_raw = core.parse_address(wallet['address'])
        except core.ChainError:
            wallet_raw = ''
    gift_meta = _gift_meta(storage, saved)
    attrs = resolve_attrs(storage, saved)

    existing = core.get_nft_by_slug_public(slug)
    if existing:
        _ensure_gift_link(storage, slug, existing.get('owner_address') or '')
        return {'ok': False, 'status': 'already', 'nft': existing,
                'error': 'Этот подарок уже находится в блокчейне KoalGram.',
                'saved': saved, 'gift_meta': gift_meta, 'owner': owner,
                'ownerTgId': owner_tg_id}
    if _active_rental(storage, slug):
        return {'ok': False, 'error': (
            'Подарок участвует в аренде. Сначала снимите его с аренды.')}
    link = storage.get_gift_blockchain_link(slug)
    if link and str(link.get('owner_address') or '').strip():
        link_address = str(link['owner_address']).strip()
        try:
            link_raw = core.parse_address(link_address)
        except core.ChainError:
            link_raw = ''
        if not link_raw:
            return {'ok': False, 'error': (
                'Подарок уже привязан к другому блокчейн-адресу '
                '(например, к аренде или маркету).')}
        if not wallet_raw:
            return {'ok': False, 'error': (
                'Подарок уже перенесён в блокчейн на другой кошелёк.')}
        if link_raw != wallet_raw:
            return {'ok': False, 'error': (
                'Подарок уже перенесён в блокчейн на другой кошелёк. '
                f'Сейчас он принадлежит {core.friendly_address(link_raw)}.')}
    if not wallet_raw:
        return {'ok': True, 'status': 'no_wallet', 'saved': saved,
                'gift_meta': gift_meta, 'attrs': attrs, 'owner': owner,
                'ownerTgId': owner_tg_id, 'slug': slug,
                'title': _display_title(saved, gift_meta),
                'walletUrl': config.AIRGRAM_WALLET_URL}
    return {'ok': True, 'status': 'ready', 'saved': saved,
            'gift_meta': gift_meta, 'attrs': attrs, 'owner': owner,
            'ownerTgId': owner_tg_id, 'walletAddress': wallet_raw,
            'slug': slug, 'title': _display_title(saved, gift_meta)}


def _display_title(saved: dict, gift_meta: dict) -> str:
    base = (gift_meta.get('title') or 'Gift').strip()
    number = int(saved.get('unique_num') or 0)
    return f'{base} #{number}' if number else base


def _attributes(saved: dict, attrs: dict, gift_meta: dict) -> list[dict]:
    out: list[dict] = []
    for attr_type, label in (('model', 'Model'), ('pattern', 'Pattern'),
                             ('backdrop', 'Backdrop')):
        attr = attrs.get(attr_type)
        if not attr:
            continue
        entry = {'trait_type': label, 'value': attr.get('name') or ''}
        rarity = int(attr.get('rarity_permille') or 0)
        if rarity > 0:
            entry['rarity'] = round(rarity / 10, 2)
        out.append(entry)
    number = int(saved.get('unique_num') or 0)
    if number:
        out.append({'trait_type': 'Number', 'value': number})
    out.append({'trait_type': 'Collection',
                'value': (gift_meta.get('title') or 'Gift').strip()})
    return out


def build_nft(storage, saved: dict, gift_meta: dict, attrs: dict,
              owner_raw: str, initiator_user_id: int) -> dict:
    slug = str(saved['unique_slug'])
    address = core.nft_address_for(int(saved['unique_id']))
    title = _display_title(saved, gift_meta)
    collection = (gift_meta.get('title') or 'Gift').strip()
    backdrop = attrs.get('backdrop') or {}
    model = attrs.get('model') or {}
    pattern = attrs.get('pattern') or {}
    media = ensure_media(
        address,
        gift_id=int(saved['gift_id']),
        model_document_id=int(model.get('document_id') or 0),
        pattern_document_id=int(pattern.get('document_id') or 0),
        backdrop=backdrop,
    )
    attributes = _attributes(saved, attrs, gift_meta)
    friendly = core.friendly_address(owner_raw)
    metadata = {
        'name': title,
        'description': (
            f'Уникальный подарок «{collection}» из KoalGram. '
            f'Перенесён в блокчейн KoalGram и принадлежит кошельку {friendly}.'),
        'external_url': f'{config.AIRGRAM_WALLET_URL}?nft={address}',
        'attributes': attributes,
        'collection': collection,
        'blockchain': 'koalgram',
        'chain_id': core.CHAIN_ID,
        'gift': {
            'slug': slug,
            'gift_id': int(saved['gift_id']),
            'unique_id': int(saved['unique_id']),
            'number': int(saved.get('unique_num') or 0),
        },
    }
    return {
        'address': address,
        'saved_id': int(saved['saved_id']),
        'unique_id': int(saved['unique_id']),
        'unique_slug': slug,
        'gift_id': int(saved['gift_id']),
        'title': title,
        'collection': collection,
        'model_name': model.get('name') or '',
        'pattern_name': pattern.get('name') or '',
        'backdrop_name': backdrop.get('name') or '',
        'attributes_json': json.dumps(attributes, ensure_ascii=False),
        'metadata_json': json.dumps(metadata, ensure_ascii=False),
        'image_path': media['image_path'],
        'thumb_path': media['thumb_path'],
    }


def _write_gift_link(storage, slug: str, address: str) -> None:
    """Persist the wallet link; retries transient SQLite lock errors."""
    last_error: Optional[Exception] = None
    for attempt in range(4):
        try:
            storage.upsert_gift_blockchain_link(slug, address)
            return
        except Exception as exc:
            last_error = exc
            try:
                if storage._conn.in_transaction:
                    storage._conn.rollback()
            except Exception:
                pass
            time.sleep(0.15 * (attempt + 1))
    log.error('Failed to write chain link for %s: %s', slug, last_error)
    if last_error is not None:
        raise last_error


def _ensure_gift_link(storage, slug: str, owner_raw: str) -> None:
    """Self-heal a missing main-DB link from the chain's authoritative owner."""
    if not owner_raw:
        return
    link = storage.get_gift_blockchain_link(slug)
    if link and str(link.get('owner_address') or '').strip():
        return
    try:
        _write_gift_link(storage, slug, core.friendly_address(owner_raw))
    except Exception as exc:
        log.warning('Link self-heal failed for %s: %s', slug, exc)


def mint_gift(storage, saved: dict, owner_raw: str, *,
              initiator_user_id: int,
              request_id: Optional[int] = None) -> dict:
    gift_meta = _gift_meta(storage, saved)
    attrs = resolve_attrs(storage, saved)
    nft = build_nft(storage, saved, gift_meta, attrs, owner_raw,
                    initiator_user_id)
    result = core.mint_nft(
        nft=nft, owner_address=owner_raw,
        owner_user_id=int(saved['owner_user_id']),
        initiator_user_id=int(initiator_user_id),
        request_id=request_id)
    if result.get('ok'):
        _write_gift_link(
            storage, str(saved['unique_slug']),
            core.friendly_address(owner_raw))
        public = core.nft_public(result['nft'])
        return {'ok': True, 'nft': public, 'block': result['block'],
                'txHash': result['tx_hash']}
    if result.get('error') == 'ALREADY_MINTED':
        existing = result.get('nft') or {}
        if existing:
            _ensure_gift_link(storage, str(saved['unique_slug']),
                              existing.get('owner_address') or '')
        return {'ok': False, 'already': True,
                'nft': core.nft_public(existing) if existing else None,
                'error': 'Подарок уже находится в блокчейне KoalGram.'}
    return result


def start_send(storage, initiator_tg_id: int, slug: str) -> dict:
    prepared = prepare_gift(storage, int(initiator_tg_id), slug)
    if not prepared.get('ok'):
        return prepared
    if prepared.get('status') == 'no_wallet':
        return prepared
    owner_tg_id = int(prepared.get('ownerTgId') or 0)
    if not owner_tg_id:
        return {'ok': False, 'status': 'no_owner_telegram',
                'error': ('Не удалось связаться с владельцем подарка: '
                          'он ещё не открывал @KoalGramApp. Попросите его '
                          'нажать /start в боте и повторите запрос.')}
    try:
        request = core.create_send_request(
            slug=prepared['slug'], saved_id=int(prepared['saved']['saved_id']),
            owner_user_id=int(prepared['saved']['owner_user_id']),
            owner_tg_id=owner_tg_id,
            initiator_tg_id=int(initiator_tg_id),
            wallet_address=prepared['walletAddress'])
    except core.ChainError as exc:
        return {'ok': False, 'error': exc.message}
    initiator_user_id = resolve_user_id(storage, int(initiator_tg_id)) or 0
    return {'ok': True, 'status': 'pending',
            'requestId': request['id'], 'code': request['code'],
            'expiresAt': request['expiresAt'], 'ownerTgId': owner_tg_id,
            'initiatorTgId': int(initiator_tg_id),
            'ownerIsInitiator': initiator_user_id == int(
                prepared['saved']['owner_user_id']),
            'title': prepared['title'], 'slug': prepared['slug'],
            'initiatorUserId': initiator_user_id,
            'ownerUserId': int(prepared['saved']['owner_user_id'])}


def confirm_send(storage, initiator_tg_id: int, code: str,
                 slug: str = '') -> dict:
    check = core.confirm_send_request(int(initiator_tg_id), code, slug)
    if not check.get('ok'):
        return check
    request = check['request']
    request_id = int(request['id'])
    saved = storage.get_saved_star_gift_by_id(int(request['saved_id']))
    if not saved:
        core.finish_send_request(request_id, ok=False)
        return {'ok': False, 'error': 'Подарок больше не найден.'}
    if int(saved.get('owner_user_id')) != int(request['owner_user_id']):
        core.finish_send_request(request_id, ok=False)
        return {'ok': False, 'error': (
            'Владелец подарка изменился. Запрос отменён, создайте новый.')}
    wallet = get_linked_wallet(storage, int(request['owner_user_id']))
    try:
        wallet_raw = core.parse_address(wallet['address']) if wallet else ''
    except core.ChainError:
        wallet_raw = ''
    try:
        request_raw = core.parse_address(request['wallet_address'])
    except core.ChainError:
        request_raw = ''
    if not wallet_raw or wallet_raw != request_raw:
        core.finish_send_request(request_id, ok=False)
        return {'ok': False, 'error': (
            'Кошелёк владельца изменился. Запрос отменён, создайте новый.')}
    link = storage.get_gift_blockchain_link(str(saved['unique_slug']))
    if link and str(link.get('owner_address') or '').strip():
        try:
            link_raw = core.parse_address(str(link['owner_address']).strip())
        except core.ChainError:
            link_raw = ''
        if link_raw and link_raw != wallet_raw:
            core.finish_send_request(request_id, ok=False)
            return {'ok': False, 'error': (
                'Подарок уже перенесён на другой кошелёк.')}
    try:
        result = mint_gift(
            storage, saved, wallet_raw,
            initiator_user_id=resolve_user_id(
                storage, int(request['initiator_tg_id'])) or 0,
            request_id=request_id)
    except core.ChainError as exc:
        core.finish_send_request(request_id, ok=False)
        log.warning('mint failed for %s: %s', request['slug'], exc)
        return {'ok': False, 'error': exc.message}
    except Exception:
        core.finish_send_request(request_id, ok=False)
        log.exception('mint failed for %s', request['slug'])
        return {'ok': False, 'error': (
            'Не удалось перенести подарок в блокчейн. Попробуйте ещё раз.')}
    if result.get('ok'):
        core.finish_send_request(request_id, ok=True)
        result.update({
            'requestId': request_id,
            'slug': request['slug'],
            'ownerTgId': int(request['owner_tg_id']),
            'initiatorTgId': int(request['initiator_tg_id']),
            'ownerUserId': int(request['owner_user_id']),
        })
        return result
    if result.get('already'):
        core.finish_send_request(request_id, ok=True)
        return {'ok': True, 'nft': result.get('nft'), 'already': True,
                'requestId': request_id, 'slug': request['slug'],
                'ownerTgId': int(request['owner_tg_id']),
                'initiatorTgId': int(request['initiator_tg_id']),
                'ownerUserId': int(request['owner_user_id'])}
    core.finish_send_request(request_id, ok=False)
    return result


def send_gram(storage, user_id: int, address: str, amount_nano: int,
              comment: str = '') -> dict:
    """Transfer GRAM from the KoalGram ledger with a fixed minimal fee."""
    wallet = get_linked_wallet(storage, int(user_id))
    if not wallet:
        return {'ok': False,
                'error': 'Сначала привяжите TON-кошелёк к KoalGram.'}
    try:
        sender_raw = core.parse_address(wallet['address'])
    except core.ChainError:
        sender_raw = str(wallet['address'] or '')
    try:
        raw = core.parse_address(address)
    except core.ChainError as exc:
        return {'ok': False, 'error': exc.message}
    if raw == sender_raw:
        return {'ok': False,
                'error': 'Получатель совпадает с вашим кошельком.'}
    try:
        amount_nano = int(amount_nano)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Некорректная сумма перевода.'}
    if amount_nano <= 0:
        return {'ok': False,
                'error': 'Сумма перевода должна быть больше нуля.'}
    fee = GRAM_SEND_FEE_NANO
    balance = storage.get_ton_balance(int(user_id))
    total = amount_nano + fee
    if balance < total:
        return {'ok': False,
                'error': 'Недостаточно GRAM на балансе с учётом комиссии.',
                'balanceNano': balance, 'feeNano': fee}
    owner = get_wallet_owner(storage, raw)
    peer_user_id = int(owner['user_id']) if owner else 0
    title = (comment or '').strip()[:128] or 'GRAM transfer'
    sender_balance = storage.adjust_ton_balance(
        int(user_id), -total, peer_user_id=peer_user_id, title=title)
    rows, _next = storage.list_ton_transactions(int(user_id), limit=1)
    tx_id = rows[0]['tx_id'] if rows else ''
    if peer_user_id:
        storage.adjust_ton_balance(
            peer_user_id, amount_nano,
            peer_user_id=int(user_id), title='GRAM received')
    else:
        # Address not linked yet: hold the funds for that address so they are
        # credited as soon as its owner links a KoalGram account.
        add_pending_gram(storage, raw, amount_nano)
    return {'ok': True, 'address': raw,
            'friendlyAddress': core.friendly_address(raw),
            'amountNano': amount_nano, 'feeNano': fee,
            'balanceNano': sender_balance, 'txId': tx_id,
            'peerUserId': peer_user_id,
            'pendingDelivery': peer_user_id == 0}


def wallet_payload(storage, user_id: int, *, include_nfts: bool = True) -> dict:
    wallet = get_linked_wallet(storage, int(user_id))
    if wallet:
        try:
            claim_pending_gram(storage, int(user_id), wallet['address'])
        except core.ChainError:
            pass
    gram_nano = storage.get_ton_balance(int(user_id))
    result: dict = {
        'ok': True,
        'linked': bool(wallet),
        'gramBalanceNano': gram_nano,
        'gramBalance': config.format_gram_amount(gram_nano),
        'walletUrl': config.AIRGRAM_WALLET_URL,
    }
    if not wallet:
        result.update({'address': '', 'friendlyAddress': '',
                       'nfts': [], 'nftCount': 0})
        return result
    try:
        raw = core.parse_address(wallet['address'])
    except core.ChainError:
        result.update({'linked': False, 'address': '', 'friendlyAddress': '',
                       'nfts': [], 'nftCount': 0,
                       'error': 'Сохранённый адрес кошелька повреждён.'})
        return result
    overview = core.wallet_overview(raw) if include_nfts else None
    result.update({
        'address': raw,
        'friendlyAddress': core.friendly_address(raw),
        'nftCount': core.count_nfts(raw),
        'chainAddressUrl': f'{config.AIRGRAM_WALLET_URL}?wallet={raw}',
    })
    if overview is not None:
        virtual = virtual_nfts.list_for_user(storage, int(user_id), raw)
        nfts = virtual + list(overview['nfts'])
        result['nfts'] = nfts
        result['nftCount'] = len(nfts)
        result['chainNftCount'] = overview['nftCount']
        result['accountCreatedAt'] = overview['createdAt']
    return result


def public_wallet(storage, address: str, *, include_nfts: bool = True) -> dict:
    try:
        overview = core.wallet_overview(address)
    except core.ChainError as exc:
        return {'ok': False, 'error': exc.message}
    payload = {
        'ok': True,
        'address': overview['address'],
        'friendlyAddress': overview['friendlyAddress'],
        'nftCount': core.count_nfts(overview['address']),
    }
    owner = get_wallet_owner(storage, overview['address'])
    owner_user_id = int(owner['user_id']) if owner else int(
        overview.get('ownerUserId') or 0)
    virtual = (virtual_nfts.list_for_user(storage, owner_user_id,
                                          overview['address'])
               if include_nfts and owner_user_id else [])
    if include_nfts:
        nfts = virtual + list(overview['nfts'])
        payload['nfts'] = nfts
        payload['nftCount'] = len(nfts)
        payload['chainNftCount'] = overview['nftCount']
    payload['ownerUserId'] = owner_user_id
    gram_nano = (storage.get_ton_balance(owner_user_id)
                 if owner_user_id else 0)
    payload['gramBalanceNano'] = gram_nano
    payload['gramBalance'] = config.format_gram_amount(gram_nano)
    transactions, _next = storage.list_ton_transactions(
        owner_user_id, limit=100) if owner_user_id else ([], '')
    payload['transactions'] = transactions
    activity = list(core.list_activity(overview['address'], limit=100))
    if owner_user_id:
        activity += virtual_nfts.events_for_user(
            storage, owner_user_id, overview['address'])
    activity.sort(key=lambda item: int(item.get('ts') or 0), reverse=True)
    payload['activity'] = activity[:100]
    return payload


def nft_detail(storage, address: str) -> Optional[dict]:
    detail = core.nft_detail(address)
    if detail:
        return detail
    return virtual_nfts.detail(storage, address)


def nft_metadata(storage, address: str) -> Optional[dict]:
    metadata = core.nft_metadata(address)
    if metadata:
        return metadata
    return virtual_nfts.metadata(storage, address)
