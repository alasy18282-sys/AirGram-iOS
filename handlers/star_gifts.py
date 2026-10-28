"""Star gifts catalog, upgrade attributes, and saved gifts RPC handlers."""
import json
import logging
import random
import time
from typing import List, Optional

from mtproto import tl
from db.storage import Storage, normalize_unix_time
from handlers import packers
from handlers.star_gift_rarity import pack_star_gift_rarity
from handlers.star_gift_value import localize_star_gift_value
from handlers.stickers import (
    DOCUMENT,
    DOCUMENT_ATTR_IMAGE_SIZE,
    DOCUMENT_ATTR_STICKER,
    INPUT_STICKER_SET_EMPTY,
    pack_document,
)
import config

log = logging.getLogger(__name__)

# ── TL constructors (layer 224) ─────────────────────────────
STAR_GIFT = 0x313a9547
STAR_GIFT_BACKGROUND = 0xaff56398
PAYMENTS_STAR_GIFTS = 0x2ed82995
PAYMENTS_STAR_GIFTS_NOT_MODIFIED = 0xa388a368
STAR_GIFT_ATTRIBUTE_MODEL = 0x565251e2
STAR_GIFT_ATTRIBUTE_PATTERN = 0x4e7085ea
STAR_GIFT_ATTRIBUTE_BACKDROP = 0x9f2504e4
STAR_GIFT_ATTRIBUTE_RARITY = 0x36437737
STAR_GIFT_ATTRIBUTE_ORIGINAL_DETAILS = 0xe0bff26c
PAYMENTS_STAR_GIFT_UPGRADE_ATTRIBUTES = 0x46c6e36f
PAYMENTS_STAR_GIFT_UPGRADE_PREVIEW = 0x3de1dfed
STAR_GIFT_UPGRADE_PRICE = 0x99ea331d
PAYMENTS_CHECK_CAN_SEND_GIFT_OK = 0x374fa7ad
PAYMENTS_CHECK_CAN_SEND_GIFT_FAIL = 0xd5e58274
PAYMENTS_STARS_STATUS = 0x6c9ce8ed
STARS_AMOUNT = 0xbbb6b4a3
STARS_TON_AMOUNT = 0x74aee3e0
PAYMENTS_SAVED_STAR_GIFTS = 0x95f389b1
SAVED_STAR_GIFT = 0x41df43fc
PAYMENTS_STAR_GIFT_COLLECTIONS_NOT_MODIFIED = 0xa0ba4f17
PAYMENTS_STAR_GIFT_COLLECTION = 0x9d6b13b0
PAYMENTS_STAR_GIFT_COLLECTIONS = 0x8a2932f3
PAYMENTS_GET_STAR_GIFTS = 0xc4563590
PAYMENTS_GET_STAR_GIFT_UPGRADE_ATTRIBUTES = 0x6d038b58
PAYMENTS_GET_STAR_GIFT_UPGRADE_PREVIEW = 0x9c9abcb1
PAYMENTS_GET_STAR_GIFT_WITHDRAWAL_URL = 0xd06e93a8
PAYMENTS_CHECK_CAN_SEND_GIFT = 0xc0c4edc9
PAYMENTS_GET_STARS_STATUS = 0x4ea9b3bf
PAYMENTS_GET_STARS_TRANSACTIONS = 0x69da4557
PAYMENTS_GET_STARS_TRANSACTIONS_BY_ID = 0x2dca16b8
PAYMENTS_GET_STARS_TOPUP_OPTIONS = 0xc00ec7d3
PAYMENTS_GET_STARS_GIFT_OPTIONS = 0xd3c96bc8
STARS_TRANSACTION = 0x13659eb0
STARS_TRANSACTION_PEER = 0xd80da15d
STARS_TRANSACTION_PEER_UNSUPPORTED = 0x95f2bfe4
PAYMENTS_GET_STARS_SUBSCRIPTIONS = 0x032512c5
PAYMENTS_GET_SAVED_STAR_GIFTS = 0xa319e569
PAYMENTS_GET_SAVED_STAR_GIFT = 0xb455a106
PAYMENTS_SAVE_STAR_GIFT = 0x2a2a697c
PAYMENTS_CONVERT_STAR_GIFT = 0x74bf076b
PAYMENTS_GET_STAR_GIFT_COLLECTIONS = 0x981b91dd
PAYMENTS_CREATE_STAR_GIFT_COLLECTION = 0xa29cd42c
PAYMENTS_CREATE_STAR_GIFT_COLLECTION_V2 = 0x1f4a0e87
PAYMENTS_UPDATE_STAR_GIFT_COLLECTION = 0x4fddbee7
PAYMENTS_DELETE_STAR_GIFT_COLLECTION = 0xa1b70815
PAYMENTS_DELETE_STAR_GIFT_COLLECTION_V2 = 0xad5648e8
PAYMENTS_GET_PREMIUM_GIFT_CODE_OPTIONS = 0x2757ba54
PAYMENTS_GET_STAR_GIFT_ACTIVE_AUCTIONS = 0xa5d0514d
PAYMENTS_STAR_GIFT_ACTIVE_AUCTIONS_NOT_MODIFIED = 0xdb33dad0
PAYMENTS_STAR_GIFT_ACTIVE_AUCTIONS = 0xaef6abbc
PAYMENTS_GET_STAR_GIFT_AUCTION_STATE = 0x5c9ff4d6
PAYMENTS_STAR_GIFT_AUCTION_STATE = 0x6b39f4ec
INPUT_STAR_GIFT_AUCTION = 0x02e16c98
INPUT_STAR_GIFT_AUCTION_SLUG = 0x7ab58308
STAR_GIFT_AUCTION_STATE = 0x771a4e66
STAR_GIFT_AUCTION_STATE_FINISHED = 0x972dabbf
STAR_GIFT_AUCTION_STATE_NOT_MODIFIED = 0xfe333952
STAR_GIFT_AUCTION_USER_STATE = 0x2eeed1c4
STAR_GIFT_ACTIVE_AUCTION_STATE = 0xd31bc45d
AUCTION_BID_LEVEL = 0x310240cc
STAR_GIFT_AUCTION_ROUND = 0x3aae0528
INPUT_INVOICE_STAR_GIFT_AUCTION_BID = 0x1ecafa10
UPDATE_STAR_GIFT_AUCTION_STATE = 0x48e246c2
UPDATE_STAR_GIFT_AUCTION_USER_STATE = 0xdc58f31e
PAYMENTS_GET_PAYMENT_FORM = 0x37148dbb
PAYMENTS_SEND_STARS_FORM = 0x7998c914
PAYMENTS_PAYMENT_FORM_STAR_GIFT = 0xb425cfe1
PAYMENTS_PAYMENT_RESULT = 0x4e5f810d
INPUT_INVOICE_STAR_GIFT = 0xe8625e92
INPUT_INVOICE_STAR_GIFT_UPGRADE = 0x4d818d5d
INPUT_INVOICE_STAR_GIFT_RESALE = 0xc39f5324
INPUT_INVOICE_STAR_GIFT_TRANSFER = 0x4a5f5bd9
INVOICE = 0x49ee584
LABELED_PRICE = 0xcb296bf8
MESSAGE_SERVICE = 0x7a800e0a
MESSAGE_ACTION_STAR_GIFT = 0xea2c31d3
MESSAGE_ACTION_STAR_GIFT_UNIQUE = 0xe6c31522
MESSAGE_ACTION_STAR_GIFT_PURCHASE_OFFER = 0x774278d4
MESSAGE_ACTION_STAR_GIFT_PURCHASE_OFFER_DECLINED = 0x73ada76b
STAR_GIFT_UNIQUE = 0x85f0a9cd
PAYMENTS_GET_UNIQUE_STAR_GIFT = 0xa1974d72
PAYMENTS_UNIQUE_STAR_GIFT = 0x416c56e8
PAYMENTS_GET_UNIQUE_STAR_GIFT_VALUE_INFO = 0x4365af6b
PAYMENTS_UNIQUE_STAR_GIFT_VALUE_INFO = 0x512fe446
PAYMENTS_GET_RESALE_STAR_GIFTS = 0x7a5fa236
PAYMENTS_RESALE_STAR_GIFTS = 0x947a12df
STAR_GIFT_ATTRIBUTE_ID_MODEL = 0x48aaae3c
STAR_GIFT_ATTRIBUTE_ID_PATTERN = 0x4a162433
STAR_GIFT_ATTRIBUTE_ID_BACKDROP = 0x1f01c757
STAR_GIFT_ATTRIBUTE_COUNTER = 0x2eb1b658
PAYMENTS_UPGRADE_STAR_GIFT = 0xaed6e4f5
PAYMENTS_GET_CRAFT_STAR_GIFTS = 0xfd05dd00
PAYMENTS_CRAFT_STAR_GIFT = 0xb0f9684f
UPDATE_STAR_GIFT_CRAFT_FAIL = 0xac072444
PAYMENTS_TOGGLE_STAR_GIFTS_PINNED_TO_TOP = 0x1513e7b0
PAYMENTS_TRANSFER_STAR_GIFT = 0x7f18176a
PAYMENTS_UPDATE_STAR_GIFT_PRICE = 0xedbe6ccb
PAYMENTS_SEND_STAR_GIFT_OFFER = 0x8fb86b41
PAYMENTS_RESOLVE_STAR_GIFT_OFFER = 0xe9ce781c
ACCOUNT_GET_COLLECTIBLE_EMOJI_STATUSES = 0x2e7b4543
INPUT_EMOJI_STATUS_COLLECTIBLE = 0x7141dbf
GLOBAL_PRIVACY_SETTINGS = 0xfe41b34f
INPUT_SAVED_STAR_GIFT_USER = 0x69279795
INPUT_SAVED_STAR_GIFT_CHAT = 0xf101aa7f
INPUT_SAVED_STAR_GIFT_SLUG = 0x2085c238
PEER_CHANNEL = 0xa2a5371e
TEXT_WITH_ENTITIES = 0x751f3146

_AUCTION_SUBSCRIBERS: dict[int, dict[int, float]] = {}
_AUCTION_SUBSCRIBE_TIMEOUT = 60


def _subscribe_auction(gift_id: int, user_id: int,
                       timeout: int = _AUCTION_SUBSCRIBE_TIMEOUT) -> None:
    exp = time.time() + timeout
    subs = _AUCTION_SUBSCRIBERS.setdefault(int(gift_id), {})
    subs[int(user_id)] = max(subs.get(int(user_id), 0), exp)


def _auction_subscribers(gift_id: int, storage: Storage) -> set[int]:
    now = time.time()
    subs = _AUCTION_SUBSCRIBERS.get(int(gift_id), {})
    active = {uid for uid, exp in subs.items() if exp > now}
    for uid in list(subs.keys()):
        if subs[uid] <= now:
            del subs[uid]
    for bid in storage.list_auction_bids(int(gift_id), active_only=True):
        active.add(int(bid['user_id']))
    return active


def _rpc_error(code: int, message: str) -> bytes:
    w = tl.Writer()
    w.u32(tl.RPC_ERROR)
    w.i32(code)
    w.string(message)
    return w.bytes_out()


def handle_payments_get_star_gift_withdrawal_url(
        session, req_msg_id, r, storage, push_queue, registry):
    """Fail with a client-recognized code when official export is unavailable.

    Telegram's method creates the Fragment withdrawal URL using the real
    account's 2FA SRP proof. KoalGram's local MTProto account is not that
    Telegram session; silently minting or pretending an NFT was exported
    would misrepresent ownership.
    """
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    # Consume InputSavedStarGift + InputCheckPasswordSRP so the desktop RPC
    # dispatcher sees a fully-read query even though this server cannot mint
    # Telegram's official Fragment withdrawal URL.
    r.read(r.remaining())
    return _rpc_error(400, 'STARGIFT_WITHDRAWAL_UNAVAILABLE')


def _permille_rarity(permille: int) -> bytes:
    w = tl.Writer()
    w.u32(STAR_GIFT_ATTRIBUTE_RARITY)
    w.i32(int(permille))
    return w.bytes_out()


def build_gift_sticker_attrs(alt: str = '🎁', w: int = 512, h: int = 512) -> bytes:
    bw = tl.Writer()
    bw.u32(DOCUMENT_ATTR_IMAGE_SIZE)
    bw.i32(w)
    bw.i32(h)
    img = bw.bytes_out()
    bw = tl.Writer()
    bw.u32(DOCUMENT_ATTR_STICKER)
    bw.i32(0)
    bw.string(alt or '🎁')
    bw.u32(INPUT_STICKER_SET_EMPTY)
    stk = bw.bytes_out()
    out = tl.Writer()
    out.u32(tl.VECTOR)
    out.i32(2)
    out.raw(img)
    out.raw(stk)
    return out.bytes_out()


def pack_gift_document(doc: dict, *, date_override: Optional[int] = None) -> bytes:
    """Document for star gifts — sticker attr with empty stickerset."""
    ref = doc.get('file_reference') or b'\x00' * 8
    if isinstance(ref, str):
        ref = bytes.fromhex(ref) if ref else b'\x00' * 8
    attrs = build_gift_sticker_attrs(
        doc.get('alt') or '🎁',
        int(doc.get('width') or 512),
        int(doc.get('height') or 512))
    doc_flags = 0
    thumbs_bytes = b''
    try:
        from handlers.media import pack_document_thumbs
        doc_flags, thumbs_bytes = pack_document_thumbs(doc)
    except Exception:
        pass
    w = tl.Writer()
    w.u32(DOCUMENT)
    w.i32(doc_flags)
    w.i64(doc['id'])
    w.i64(doc['access_hash'])
    w.tl_bytes(ref)
    w.i32(int(date_override if date_override is not None
               else doc.get('date') or int(time.time())))
    w.string(doc.get('mime_type') or 'application/x-tgsticker')
    w.i64(int(doc.get('size') or 0))
    if doc_flags & 1:
        w.raw(thumbs_bytes)
    w.i32(config.DC_ID)
    w.raw(attrs)
    return w.bytes_out()


def _pack_released_by(gift: dict, storage: Storage) -> Optional[bytes]:
    pid = gift.get('released_by_peer_id')
    if pid is None:
        return None
    pt = int(gift.get('released_by_peer_type') or 0)
    pid = int(pid)
    if pt == 0:
        if not storage.get_user_by_id(pid):
            return None
        return packers.pack_peer_user(pid)
    if not storage.get_channel(pid):
        return None
    return packers.pack_peer_channel(pid)


def _collect_star_gift_catalog_peers(gifts: List[dict], storage: Storage
                                     ) -> tuple[List[dict], List[dict]]:
    users_map: dict[int, dict] = {}
    chats_map: dict[int, dict] = {}
    for gift in gifts:
        pid = gift.get('released_by_peer_id')
        if pid is None:
            continue
        pt = int(gift.get('released_by_peer_type') or 0)
        pid = int(pid)
        if pt == 0:
            if pid not in users_map:
                u = storage.get_user_by_id(pid)
                if u:
                    users_map[pid] = u
        elif pid not in chats_map:
            ch = storage.get_channel(pid)
            if ch:
                chats_map[pid] = ch
    return list(users_map.values()), list(chats_map.values())


def _pack_text_with_entities(text: str, entities: list) -> bytes:
    w = tl.Writer()
    w.u32(TEXT_WITH_ENTITIES)
    w.string(text or '')
    w.raw(packers.pack_entities(entities or [], text or ''))
    return w.bytes_out()


def _gift_message_has_content(text: str, entities: list) -> bool:
    if (text or '').strip():
        return True
    return bool(entities)


def _gift_name_hidden(action: dict, saved_row: Optional[dict] = None) -> bool:
    if 'hide_name' in action:
        return bool(action['hide_name'])
    if 'name_hidden' in action:
        return bool(int(action['name_hidden']))
    if saved_row is not None:
        return bool(int(saved_row.get('name_hidden') or 0))
    return False


def _gift_message_fields(action: dict,
                         saved_row: Optional[dict] = None) -> tuple[str, list]:
    text = action.get('message_text')
    entities = action.get('message_entities')
    for src in (action, saved_row):
        if src is None:
            continue
        if text is None and 'message_text' in src:
            text = src.get('message_text') or ''
        if entities is None:
            if src.get('message_entities') is not None:
                entities = src['message_entities']
            elif src.get('message_entities_json'):
                raw = src['message_entities_json']
                entities = json.loads(raw) if isinstance(raw, str) else raw
    return text or '', entities or []


def _should_pack_original_details(row: dict) -> bool:
    return (bool(int(row.get('upgraded') or 0))
            and bool(int(row.get('original_details_kept', 1) or 0)))


def _pack_star_gift_attribute_original_details(row: dict, *,
                                                 show_details: bool = True
                                                 ) -> bytes:
    """starGiftAttributeOriginalDetails — sender/recipient/message on profile."""
    from_id = int(row.get('from_user_id') or 0)
    recipient_id = int(row.get('owner_user_id') or 0)
    date = int(row.get('date') or 0)
    message_text, entities = _gift_message_fields(row)

    flags = 0
    if show_details and from_id:
        flags |= 1 << 0
    if show_details and _gift_message_has_content(message_text, entities):
        flags |= 1 << 1

    w = tl.Writer()
    w.u32(STAR_GIFT_ATTRIBUTE_ORIGINAL_DETAILS)
    w.i32(flags)
    if flags & (1 << 0):
        w.raw(packers.pack_peer_user(from_id))
    w.raw(packers.pack_peer_user(recipient_id))
    w.i32(date)
    if flags & (1 << 1):
        w.raw(_pack_text_with_entities(message_text, entities))
    return w.bytes_out()


def _saved_gift_details_visible(row: dict, viewer_id: Optional[int] = None,
                                profile_owner_id: Optional[int] = None,
                                storage: Optional[Storage] = None) -> bool:
    """savedStarGift: from_id/message public unless name_hidden; then owner only."""
    if not int(row.get('name_hidden') or 0):
        return True
    if viewer_id is not None and profile_owner_id is not None:
        if int(viewer_id) == int(profile_owner_id):
            return True
        if (storage is not None and packers.is_channel_id(int(profile_owner_id))
                and storage.is_channel_admin(int(profile_owner_id), int(viewer_id))):
            return True
    return False


def _unix_ts(value, default: int = 0) -> int:
    return normalize_unix_time(value) or default


def _shop_gift_row(gift: dict, storage: Storage) -> dict:
    """Shop catalog; resale counts come from listed upgraded gifts."""
    g = dict(gift)
    drop_at = _unix_ts(g.get('drop_at'))
    if drop_at > int(time.time()):
        # Telegram clients render locked gifts in the market and enable the
        # purchase action once this timestamp is reached.
        g['locked_until_date'] = drop_at
    elif g.get('drop_at'):
        g['locked_until_date'] = None
    gift_id = int(g['gift_id'])
    if g.get('is_auction'):
        g['limited'] = 1
        g['availability_total'] = int(g.get('availability_total') or 0)
        remains = int(g.get('availability_remains') or 0)
        st = storage._ensure_auction_state_row(gift_id)
        finished = int(st.get('finished') or 0)
        if remains <= 0 or finished:
            g['sold_out'] = 1
            g['availability_remains'] = 0
        else:
            g['sold_out'] = 0
            g['availability_remains'] = remains
        g['stars'] = int(g.get('auction_min_bid') or g.get('stars') or 1)
        g['first_sale_date'] = None
        g['last_sale_date'] = None
        resale_count = storage.count_resale_gifts(gift_id)
        min_price = storage.min_resell_price(gift_id)
        g['availability_resale'] = resale_count
        g['resell_min_stars'] = min_price if resale_count > 0 else None
        return g
    resale_count = storage.count_resale_gifts(gift_id)
    min_price = storage.min_resell_price(gift_id)
    g['availability_resale'] = resale_count
    g['resell_min_stars'] = min_price if resale_count > 0 else None
    if g.get('limited'):
        remains = int(g.get('availability_remains') or 0)
        g['sold_out'] = int(g.get('sold_out') or remains <= 0)
        g['availability_remains'] = max(0, remains)
    else:
        g['sold_out'] = 0
    if not g['sold_out']:
        g['first_sale_date'] = None
        g['last_sale_date'] = None
    return g


def _gift_title(gift: dict) -> str:
    return (gift.get('title') or '').strip() or 'Gift'


def _gift_star_background_colors(gift: dict,
                                  storage: Storage) -> Optional[tuple[int, int, int]]:
    """Palette for starGift.background — required for auction previews."""
    center = gift.get('bg_center_color')
    edge = gift.get('bg_edge_color')
    text = gift.get('bg_text_color')
    if center is not None or edge is not None or text is not None:
        return (
            int(center or 0x6b93c4),
            int(edge or 0x3d5a80),
            int(text or 0xffffff),
        )
    if not int(gift.get('is_auction') or 0):
        return None
    for attr in storage.list_star_gift_upgrade_attrs(int(gift['gift_id'])):
        if attr.get('attr_type') == 'backdrop':
            return (
                int(attr.get('center_color') or 0x6b93c4),
                int(attr.get('edge_color') or 0x3d5a80),
                int(attr.get('text_color') or 0xffffff),
            )
    return (0x6b93c4, 0x3d5a80, 0xffffff)


def _gift_upgradable(gift: dict, storage: Storage) -> bool:
    return storage.gift_can_upgrade(int(gift['gift_id']))


def _pack_stars_amount(*, stars: int = 0, nanos: int = 0,
                       ton: int = 0) -> bytes:
    w = tl.Writer()
    if ton > 0:
        w.u32(STARS_TON_AMOUNT)
        w.i64(int(ton))
    else:
        w.u32(STARS_AMOUNT)
        w.i64(int(stars))
        w.i32(int(nanos))
    return w.bytes_out()


def _parse_stars_amount(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid == STARS_TON_AMOUNT:
        return {'ton': int(r.i64()), 'stars': 0, 'nanos': 0}
    if cid == STARS_AMOUNT:
        return {'stars': int(r.i64()), 'nanos': int(r.i32()), 'ton': 0}
    raise ValueError(f'unsupported StarsAmount {cid:#010x}')


def _gift_sendability_reason(error_code: str) -> str:
    if error_code == 'STARGIFT_USAGE_LIMITED':
        return 'Sold Out'
    if error_code == 'STARGIFT_USER_USAGE_LIMITED':
        return 'Purchase limit reached'
    return 'Gift cannot be sent right now'


def _pack_check_can_send_gift_fail(error_code: str) -> bytes:
    w = tl.Writer()
    w.u32(PAYMENTS_CHECK_CAN_SEND_GIFT_FAIL)
    w.raw(_pack_text_with_entities(_gift_sendability_reason(error_code), []))
    return w.bytes_out()


def pack_star_gift(gift: dict, storage: Storage, *, for_shop: bool = False,
                   stars_override: Optional[int] = None,
                   viewer_id: Optional[int] = None) -> bytes:
    if for_shop:
        gift = _shop_gift_row(gift, storage)
    doc = storage.get_sticker_document_by_id(int(gift['document_id']))
    if not doc:
        raise KeyError(f'missing document for gift {gift["gift_id"]}')

    upgradable = _gift_upgradable(gift, storage)

    flags = 0
    if gift.get('limited'):
        flags |= 1 << 0
    sold_out = bool(gift.get('sold_out'))
    if gift.get('limited') and not sold_out:
        sold_out = int(gift.get('availability_remains') or 0) <= 0
    if sold_out:
        flags |= 1 << 1
    if gift.get('birthday'):
        flags |= 1 << 2
    if upgradable:
        flags |= 1 << 3
    resale_count = int(gift.get('availability_resale') or 0)
    resell_min = gift.get('resell_min_stars')
    if resale_count or resell_min:
        flags |= 1 << 4
    title = _gift_title(gift)
    flags |= 1 << 5
    released = _pack_released_by(gift, storage)
    if released:
        flags |= 1 << 6
    if gift.get('require_premium'):
        flags |= 1 << 7
    if gift.get('limited_per_user'):
        flags |= 1 << 8
    if gift.get('locked_until_date'):
        flags |= 1 << 9
    if gift.get('peer_color_available'):
        flags |= 1 << 10
    if gift.get('is_auction'):
        flags |= 1 << 11
    bg = _gift_star_background_colors(gift, storage)
    if bg:
        flags |= 1 << 13
    if upgradable:
        variants = storage.count_star_gift_upgrade_attrs(int(gift['gift_id']))
        if variants:
            flags |= 1 << 12
    else:
        variants = 0
    per_user_total = int(gift.get('per_user_total') or 1)
    per_user_remains = storage.get_star_gift_user_remains(viewer_id, gift)
    if per_user_remains is None:
        per_user_remains = int(gift.get('per_user_remains') or per_user_total)

    w = tl.Writer()
    w.u32(STAR_GIFT)
    w.i32(flags)
    w.i64(int(gift['gift_id']))
    w.raw(pack_gift_document(doc))
    w.i64(int(stars_override if stars_override is not None else gift['stars']))
    if flags & 1:
        w.i32(int(gift.get('availability_remains') or 0))
        w.i32(int(gift.get('availability_total') or 0))
    if flags & (1 << 4):
        w.i64(resale_count)
    w.i64(int(gift['convert_stars']))
    if flags & (1 << 1):
        w.i32(_unix_ts(gift.get('first_sale_date')))
        w.i32(_unix_ts(gift.get('last_sale_date')))
    if flags & (1 << 3):
        w.i64(int(storage.get_gift_upgrade_stars(int(gift['gift_id']))))
    if flags & (1 << 4) and resell_min:
        w.i64(int(resell_min))
    if flags & (1 << 5):
        w.string(title)
    if flags & (1 << 6):
        w.raw(released)
    if flags & (1 << 8):
        w.i32(per_user_total)
        w.i32(per_user_remains)
    if flags & (1 << 9):
        w.i32(_unix_ts(gift.get('locked_until_date')))
    if flags & (1 << 11):
        w.string(gift.get('auction_slug') or '')
        w.i32(int(gift.get('gifts_per_round') or 0))
        w.i32(_unix_ts(gift.get('auction_start_date')))
    if flags & (1 << 12):
        w.i32(variants)
    if flags & (1 << 13):
        w.u32(STAR_GIFT_BACKGROUND)
        w.i32(bg[0])
        w.i32(bg[1])
        w.i32(bg[2])
    return w.bytes_out()


def _pack_star_gift_attribute(attr: dict, storage: Storage) -> bytes:
    atype = attr['attr_type']
    rarity = pack_star_gift_rarity(int(attr.get('rarity_permille') or 100))
    w = tl.Writer()
    if atype == 'model':
        w.u32(STAR_GIFT_ATTRIBUTE_MODEL)
        flags = 1 if attr.get('crafted') else 0
        w.i32(flags)
        w.string(attr['name'])
        doc = storage.get_sticker_document_by_id(int(attr['document_id']))
        w.raw(pack_gift_document(doc) if doc else pack_gift_document({
            'id': 0, 'access_hash': 0, 'size': 0, 'mime_type': 'application/x-tgsticker',
        }))
        w.raw(rarity)
    elif atype == 'pattern':
        w.u32(STAR_GIFT_ATTRIBUTE_PATTERN)
        w.string(attr['name'])
        doc = storage.get_sticker_document_by_id(int(attr['document_id']))
        w.raw(pack_gift_document(doc) if doc else pack_gift_document({
            'id': 0, 'access_hash': 0, 'size': 0, 'mime_type': 'application/x-tgsticker',
        }))
        w.raw(rarity)
    elif atype == 'backdrop':
        w.u32(STAR_GIFT_ATTRIBUTE_BACKDROP)
        w.string(attr['name'])
        w.i32(int(attr.get('backdrop_id') or 0))
        w.i32(int(attr.get('center_color') or 0))
        w.i32(int(attr.get('edge_color') or 0))
        w.i32(int(attr.get('pattern_color') or 0))
        w.i32(int(attr.get('text_color') or 0))
        w.raw(rarity)
    else:
        raise ValueError(f'unknown attr type {atype}')
    return w.bytes_out()


def _saved_gift_attrs(row: dict, storage: Storage) -> List[dict]:
    attrs = []
    for key in ('model_attr_id', 'pattern_attr_id', 'backdrop_attr_id'):
        aid = row.get(key)
        if not aid:
            continue
        attr = storage.get_star_gift_upgrade_attr_by_id(int(aid))
        if attr:
            attrs.append(attr)
    return attrs


def _saved_gift_attrs_with_fallback(row: dict, storage: Storage) -> List[dict]:
    attrs = _saved_gift_attrs(row, storage)
    has_model = any(a.get('attr_type') == 'model' for a in attrs)
    has_pattern = any(a.get('attr_type') == 'pattern' for a in attrs)
    if has_model and has_pattern:
        return attrs
    gift_id = int(row['gift_id'])
    if int(row.get('upgraded') or 0):
        seed = int(row.get('unique_id') or row.get('saved_id') or 0)
        picked = storage._deterministic_pick_upgrade_attrs(gift_id, seed)
        out = []
        for atype in ('model', 'pattern', 'backdrop'):
            attr = picked.get(atype)
            if attr:
                out.append(attr)
        if len(out) >= 2:
            return out
    pool = storage.list_star_gift_upgrade_attrs(gift_id)
    models = [a for a in pool if a.get('attr_type') == 'model'
              and not int(a.get('crafted') or 0)]
    patterns = [a for a in pool if a.get('attr_type') == 'pattern']
    backdrops = [a for a in pool if a.get('attr_type') == 'backdrop']
    out = list(attrs)
    if not has_model and models:
        out.append(models[0])
    if not has_pattern and patterns:
        out.append(patterns[0])
    if not any(a.get('attr_type') == 'backdrop' for a in out) and backdrops:
        out.append(backdrops[0])
    return out


def _saved_gift_can_craft_at(row: dict, storage: Storage, *, now: int) -> Optional[int]:
    """Timestamp when crafting becomes available; None if not craftable."""
    if not int(row.get('upgraded') or 0):
        return None
    if int(row.get('burned') or 0):
        return None
    if int(row.get('crafted') or 0):
        return None
    if storage.saved_gift_chain_owner(row):
        return None
    if not storage.gift_has_craft_models(int(row['gift_id'])):
        return None
    can_at = int(row.get('can_craft_at') or 0)
    if can_at <= now:
        return now
    return can_at


def _resolve_gift_meta(storage: Storage, row: dict) -> dict:
    gift_meta = storage.get_star_gift(int(row['gift_id']))
    if gift_meta:
        return gift_meta
    slug = str(row.get('unique_slug') or '').strip()
    title = ''
    if slug and '-' in slug:
        title = slug.rsplit('-', 1)[0].replace('-', ' ').strip().title()
    if not title:
        title = f'Gift {row["gift_id"]}'
    total = max(int(row.get('unique_num') or 0), 1)
    return {
        'gift_id': int(row['gift_id']),
        'title': title,
        'stars': int(row.get('stars') or 0),
        'convert_stars': int(row.get('convert_stars') or 0),
        'upgrade_stars': int(row.get('upgrade_stars') or 0),
        'availability_total': max(total * 1000, 1000),
        'availability_remains': 0,
        'limited': 1,
        'sold_out': 0,
        'document_id': 0,
        'require_premium': 0,
        'birthday': 0,
        'peer_color_available': 0,
        'is_auction': 0,
        'sort_order': 0,
    }


def pack_star_gift_unique(row: dict, gift_meta: dict, storage: Storage, *,
                          viewer_id: Optional[int] = None,
                          profile_owner_id: Optional[int] = None,
                          include_original_details: bool = True) -> bytes:
    attrs = _saved_gift_attrs_with_fallback(row, storage)
    if len(attrs) < 2:
        raise KeyError('missing unique gift attributes')

    title = (gift_meta.get('title') or 'Gift').strip() or 'Gift'
    slug = row.get('unique_slug') or f'gift-{row.get("unique_num") or 1}'
    num = int(row.get('unique_num') or 1)
    total = int(gift_meta.get('availability_total') or 100000)
    owner_id = int(row['owner_user_id'])
    blockchain = storage.get_gift_blockchain_link(slug)
    on_blockchain = bool(blockchain and blockchain.get('owner_address'))
    chain_owner = bool(blockchain and Storage.is_airgram_chain_address(
        str(blockchain.get('owner_address') or '')))
    resell_stars = int(row.get('resell_stars') or 0)
    resell_nanos = int(row.get('resell_stars_nanos') or 0)
    resell_ton = bool(int(row.get('resell_ton') or 0))
    value_info = storage.get_star_gift_value(int(row['gift_id']))
    if value_info:
        value_info = localize_star_gift_value(
            value_info, storage,
            int(viewer_id or profile_owner_id or owner_id))

    flags = 0
    burned = bool(int(row.get('burned') or 0))
    if on_blockchain:
        flags |= (1 << 2) | (1 << 3) | (1 << 12)
    elif not burned:
        flags |= 1 << 0  # owner_id
    if gift_meta.get('require_premium'):
        flags |= 1 << 6
    if burned:
        flags |= 1 << 14
    if int(row.get('crafted') or 0):
        flags |= 1 << 15
    craft_chance = 0
    gift_id = int(row['gift_id'])
    if (storage is not None
            and not chain_owner
            and storage.gift_has_craft_models(gift_id)
            and not burned
            and not int(row.get('crafted') or 0)):
        craft_chance = storage.craft_chance_for_saved(row)
    if craft_chance > 0:
        flags |= 1 << 16
    resell_amounts = []
    if resell_ton and resell_nanos > 0:
        flags |= 1 << 4
        flags |= 1 << 7  # resale_ton_only
        resell_amounts.append(_pack_stars_amount(ton=resell_nanos))
        stars_equiv = config.nanoton_to_stars(resell_nanos)
        if stars_equiv > 0:
            resell_amounts.append(_pack_stars_amount(stars=stars_equiv))
    elif resell_stars > 0 or resell_nanos > 0:
        flags |= 1 << 4
        resell_amounts.append(_pack_stars_amount(
            stars=resell_stars, nanos=resell_nanos))
        ton_nanos = config.stars_to_nanoton(resell_stars) + int(resell_nanos or 0)
        if ton_nanos > 0:
            resell_amounts.append(_pack_stars_amount(ton=ton_nanos))
    # offer_min_stars enables "Offer to buy" on profile
    flags |= 1 << 13
    if value_info:
        flags |= 1 << 8

    w = tl.Writer()
    w.u32(STAR_GIFT_UNIQUE)
    w.i32(flags)
    w.i64(int(row['unique_id']))
    w.i64(int(row['gift_id']))
    w.string(title)
    w.string(slug)
    w.i32(num)
    if flags & (1 << 0):
        w.raw(packers.pack_peer(owner_id))
    if flags & (1 << 2):
        w.string(str(blockchain.get('owner_address') or '').strip())
    show_details = _saved_gift_details_visible(
        row, viewer_id,
        profile_owner_id or int(row.get('owner_user_id') or 0),
        storage=storage)
    packed_attrs = [_pack_star_gift_attribute(a, storage) for a in attrs]
    if include_original_details and _should_pack_original_details(row):
        packed_attrs.append(_pack_star_gift_attribute_original_details(
            row, show_details=show_details))
    w.u32(tl.VECTOR)
    w.i32(len(packed_attrs))
    for attr_bytes in packed_attrs:
        w.raw(attr_bytes)
    w.i32(storage.count_issued_unique_star_gifts(int(row['gift_id'])))
    w.i32(total)
    if flags & (1 << 3):
        w.string(slug)
    if flags & (1 << 4):
        w.u32(tl.VECTOR)
        w.i32(len(resell_amounts))
        for amount in resell_amounts:
            w.raw(amount)
    if flags & (1 << 8):
        w.i64(int(value_info['value']))
        w.string(str(value_info['currency']))
        w.i64(int(value_info.get('value_usd_amount') or 0))
    if flags & (1 << 12):
        w.raw(packers.pack_peer_user(owner_id))
    w.i32(config.STARGIFT_OFFER_MIN_STARS)
    if flags & (1 << 16):
        w.i32(int(craft_chance))
    return w.bytes_out()


def pack_emoji_status_collectible(saved: dict, gift_meta: dict,
                                  storage: Storage, until: int = 0) -> bytes:
    attrs = _saved_gift_attrs_with_fallback(saved, storage)
    model = next((a for a in attrs if a['attr_type'] == 'model'), None)
    pattern = next((a for a in attrs if a['attr_type'] == 'pattern'), None)
    backdrop = next((a for a in attrs if a['attr_type'] == 'backdrop'), None)
    if not model or not pattern:
        raise KeyError('missing collectible attrs')

    base_title = (gift_meta.get('title') or 'Gift').strip() or 'Gift'
    unique_num = int(saved.get('unique_num') or 0)
    title = f'{base_title} #{unique_num}' if unique_num else base_title
    flags = 0
    if until:
        flags |= 1
    w = tl.Writer()
    w.u32(0x7184603b)  # emojiStatusCollectible
    w.i32(flags)
    w.i64(int(saved['unique_id']))
    w.i64(int(model['document_id']))
    w.string(title)
    w.string(saved.get('unique_slug') or '')
    w.i64(int(pattern['document_id']))
    w.i32(int(backdrop.get('center_color') or 0) if backdrop else 0)
    w.i32(int(backdrop.get('edge_color') or 0) if backdrop else 0)
    w.i32(int(backdrop.get('pattern_color') or 0) if backdrop else 0)
    w.i32(int(backdrop.get('text_color') or 0) if backdrop else 0)
    if flags & 1:
        w.i32(int(until))
    return w.bytes_out()


def _pick_preview_attrs(gift_id: int, storage: Storage) -> List[dict]:
    """One random model/pattern/backdrop for upgrade preview."""
    attrs = storage.list_star_gift_upgrade_attrs(gift_id)
    out = []
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
        out.append(pick)
    return out


def _auction_fallback_preview_attrs(gift: dict,
                                    storage: Storage) -> List[dict]:
    """Synthetic model/pattern/backdrop for auction gifts without upgrade pool."""
    gift_id = int(gift['gift_id'])
    doc_id = int(gift['document_id'])
    title = _gift_title(gift)
    bg = _gift_star_background_colors(gift, storage) or (
        0x6b93c4, 0x3d5a80, 0xffffff)
    center, edge, text = bg
    pattern_color = (center + edge) // 2
    return [
        {
            'gift_id': gift_id,
            'attr_type': 'model',
            'name': title,
            'rarity_permille': 1000,
            'document_id': doc_id,
            'crafted': False,
        },
        {
            'gift_id': gift_id,
            'attr_type': 'pattern',
            'name': 'Pattern',
            'rarity_permille': 1000,
            'document_id': doc_id,
        },
        {
            'gift_id': gift_id,
            'attr_type': 'backdrop',
            'name': 'Backdrop',
            'rarity_permille': 1000,
            'backdrop_id': gift_id % 100000,
            'center_color': center,
            'edge_color': edge,
            'pattern_color': pattern_color,
            'text_color': text,
        },
    ]


def _preview_attrs_for_gift(gift: dict, storage: Storage) -> List[dict]:
    gift_id = int(gift['gift_id'])
    if storage.gift_can_upgrade(gift_id):
        sample = _pick_preview_attrs(gift_id, storage)
        if len(sample) >= 2:
            return sample
    if int(gift.get('is_auction') or 0):
        return _auction_fallback_preview_attrs(gift, storage)
    return _pick_preview_attrs(gift_id, storage)


def _pack_star_gift_upgrade_preview_response(
        sample: List[dict], storage: Storage, *,
        include_price: bool = False, gift_id: int = 0) -> bytes:
    w = tl.Writer()
    w.u32(PAYMENTS_STAR_GIFT_UPGRADE_PREVIEW)
    w.u32(tl.VECTOR)
    w.i32(len(sample))
    for a in sample:
        w.raw(_pack_star_gift_attribute(a, storage))
    w.u32(tl.VECTOR)
    if include_price and gift_id:
        w.i32(1)
        pw = tl.Writer()
        pw.u32(STAR_GIFT_UPGRADE_PRICE)
        pw.i32(int(time.time()))
        pw.i64(int(storage.get_gift_upgrade_stars(gift_id)))
        w.raw(pw.bytes_out())
    else:
        w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def pack_payments_star_gifts(gifts: List[dict], storage: Storage,
                             catalog_hash: int,
                             viewer_id: Optional[int] = None) -> bytes:
    users, chats = _collect_star_gift_catalog_peers(gifts, storage)
    w = tl.Writer()
    w.u32(PAYMENTS_STAR_GIFTS)
    w.i32(catalog_hash)
    w.u32(tl.VECTOR)
    w.i32(len(gifts))
    for g in gifts:
        w.raw(pack_star_gift(
            g, storage, for_shop=True, viewer_id=viewer_id))
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(packers.pack_channel(
            ch, viewer_id=viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, storage=storage))
    return w.bytes_out()


def pack_saved_star_gift(row: dict, storage: Storage, *,
                         viewer_id: Optional[int] = None,
                         profile_owner_id: Optional[int] = None) -> bytes:
    """savedStarGift#41df43fc — layer 224 field order."""
    gift_meta = _resolve_gift_meta(storage, row)

    upgraded = bool(int(row.get('upgraded') or 0))
    show_details = _saved_gift_details_visible(
        row, viewer_id, profile_owner_id, storage=storage)
    message_text, message_entities = _gift_message_fields(row)
    name_hidden = bool(int(row.get('name_hidden') or 0))
    convert_stars = int(row.get('convert_stars') or 0)
    prepaid = bool(int(row.get('prepaid_upgrade') or 0))
    upgrade_stars = int(row.get('upgrade_stars') or 0) if prepaid else 0
    now = int(time.time())

    flags = 0
    if name_hidden:
        flags |= 1 << 0
    if show_details and row.get('from_user_id'):
        flags |= 1 << 1
    if show_details and _gift_message_has_content(message_text, message_entities):
        flags |= 1 << 2
    if row.get('msg_id'):
        flags |= 1 << 3
    if convert_stars:
        flags |= 1 << 4
    if int(row.get('unsaved') or 0):
        flags |= 1 << 5
    if upgrade_stars and not upgraded:
        flags |= 1 << 6
    if upgraded:
        flags |= (1 << 7) | (1 << 8) | (1 << 13) | (1 << 14)
        if row.get('unique_num'):
            flags |= 1 << 19
    elif storage.gift_can_upgrade(int(row['gift_id'])):
        flags |= 1 << 10
    if row.get('saved_id'):
        flags |= 1 << 11
    if row.get('pinned_to_top'):
        flags |= 1 << 12

    collection_ids = []
    if row.get('saved_id'):
        collection_ids = storage.get_saved_gift_collection_ids(int(row['saved_id']))
        if profile_owner_id is not None:
            visible = storage.visible_star_gift_collection_ids(int(profile_owner_id))
            collection_ids = [cid for cid in collection_ids if cid in visible]
    if collection_ids:
        flags |= 1 << 15

    can_craft_at = _saved_gift_can_craft_at(row, storage, now=now)
    if can_craft_at is not None:
        flags |= 1 << 20

    w = tl.Writer()
    w.u32(SAVED_STAR_GIFT)
    w.i32(flags)
    if flags & (1 << 1):
        w.raw(packers.pack_peer_user(int(row['from_user_id'])))
    w.i32(int(row['date']))
    if upgraded:
        w.raw(pack_star_gift_unique(
            row, gift_meta, storage,
            viewer_id=viewer_id, profile_owner_id=profile_owner_id,
            include_original_details=False))
    else:
        stars_override = None
        if row.get('stars') is not None:
            stars_override = int(row['stars'])
        w.raw(pack_star_gift(
            gift_meta, storage, stars_override=stars_override))
    if flags & (1 << 2):
        w.raw(_pack_text_with_entities(message_text, message_entities))
    if flags & (1 << 3):
        w.i32(int(row['msg_id']))
    if flags & (1 << 11):
        w.i64(int(row['saved_id']))
    if flags & (1 << 4):
        w.i64(convert_stars)
    if flags & (1 << 6):
        w.i64(upgrade_stars)
    if flags & (1 << 7):
        w.i32(now)
    if flags & (1 << 8):
        w.i64(int(row.get('transfer_stars') or 25))
    if flags & (1 << 13):
        w.i32(now)
    if flags & (1 << 14):
        w.i32(now)
    if flags & (1 << 15):
        w.u32(tl.VECTOR)
        w.i32(len(collection_ids))
        for cid in collection_ids:
            w.i32(int(cid))
    if flags & (1 << 19):
        w.i32(int(row.get('unique_num') or 0))
    if flags & (1 << 20):
        w.i32(int(can_craft_at))
    return w.bytes_out()


def parse_input_saved_star_gift(r: tl.Reader) -> Optional[dict]:
    cid = r.u32()
    if cid == INPUT_SAVED_STAR_GIFT_USER:
        return {'kind': 'user', 'msg_id': r.i32()}
    if cid == INPUT_SAVED_STAR_GIFT_CHAT:
        peer = packers.parse_input_peer(r)
        return {'kind': 'chat', 'peer_id': peer, 'saved_id': r.i64()}
    if cid == INPUT_SAVED_STAR_GIFT_SLUG:
        return {'kind': 'slug', 'slug': r.string()}
    raise ValueError(f'unsupported InputSavedStarGift {cid:#010x}')


# ── RPC handlers ───────────────────────────────────────────

def handle_payments_get_star_gifts(session, req_msg_id, r, storage, push_queue, registry):
    req_hash = r.i32()
    storage.refresh_star_gift_drop_hash_if_due()
    catalog_hash = storage.get_star_gifts_catalog_hash()
    viewer_id = None
    if session is not None and getattr(session, 'auth_key_id', None):
        viewer_id = storage.get_session_user(session.auth_key_id)
    gifts = storage.list_shop_star_gifts(viewer_id=viewer_id)
    # Recomputing on every request is expensive (COUNT/MIN per gift) and
    # causes auth/UI stalls under load. Keep the stored hash hot and only
    # rebuild when it is not initialized.
    if gifts and not catalog_hash:
        catalog_hash = storage.recompute_star_gifts_catalog_hash()
    log.debug('getStarGifts hash=%d catalog_hash=%d gifts=%d',
             req_hash, catalog_hash, len(gifts))
    if req_hash and int(req_hash) == int(catalog_hash):
        w = tl.Writer()
        w.u32(PAYMENTS_STAR_GIFTS_NOT_MODIFIED)
        return w.bytes_out()
    return pack_payments_star_gifts(
        gifts, storage, catalog_hash, viewer_id=viewer_id)


def handle_payments_get_star_gift_upgrade_attributes(session, req_msg_id, r,
                                                     storage, push_queue, registry):
    gift_id = r.i64()
    gift = storage.get_star_gift(gift_id)
    if not gift:
        return _rpc_error(400, 'STARGIFT_INVALID')
    attrs = storage.list_star_gift_upgrade_attrs(gift_id)
    if not attrs and int(gift.get('is_auction') or 0):
        attrs = _auction_fallback_preview_attrs(gift, storage)
    w = tl.Writer()
    w.u32(PAYMENTS_STAR_GIFT_UPGRADE_ATTRIBUTES)
    w.u32(tl.VECTOR)
    w.i32(len(attrs))
    for a in attrs:
        w.raw(_pack_star_gift_attribute(a, storage))
    return w.bytes_out()


def handle_payments_get_star_gift_upgrade_preview(session, req_msg_id, r,
                                                  storage, push_queue, registry):
    gift_id = r.i64()
    gift = storage.get_star_gift(gift_id)
    if not gift:
        return _rpc_error(400, 'STARGIFT_INVALID')
    if not storage.gift_can_upgrade(gift_id):
        if not int(gift.get('is_auction') or 0):
            return _rpc_error(400, 'STARGIFT_NOT_UPGRADABLE')
        sample = _auction_fallback_preview_attrs(gift, storage)
        return _pack_star_gift_upgrade_preview_response(
            sample, storage, include_price=False)
    sample = _preview_attrs_for_gift(gift, storage)
    return _pack_star_gift_upgrade_preview_response(
        sample, storage,
        include_price=True, gift_id=gift_id)


def handle_payments_check_can_send_gift(session, req_msg_id, r, storage,
                                        push_queue, registry):
    gift_id = r.i64()
    gift = storage.get_star_gift(gift_id)
    if not gift:
        return _rpc_error(400, 'STARGIFT_INVALID')
    viewer_id = None
    if session is not None and getattr(session, 'auth_key_id', None):
        viewer_id = storage.get_session_user(session.auth_key_id)
    send_error = storage.get_star_gift_send_error(viewer_id, gift)
    if send_error:
        return _pack_check_can_send_gift_fail(send_error)
    w = tl.Writer()
    w.u32(PAYMENTS_CHECK_CAN_SEND_GIFT_OK)
    return w.bytes_out()


def _normalize_stars_tx_row(row: dict) -> dict:
    """Ensure rows have client-visible title/peer for Desktop stars history."""
    row = dict(row)
    amount_signed = int(row.get('amount') or 0)
    if amount_signed < 0:
        if not row.get('title') and row.get('description'):
            row['title'] = row['description']
        if not row.get('title'):
            row['title'] = 'Stars purchase'
    elif amount_signed > 0 and not row.get('title') and row.get('description'):
        row['title'] = 'Stars top-up'
    if not row.get('peer_user_id') and not row.get('peer_channel_id'):
        row['peer_user_id'] = config.SERVICE_USER_ID
    return row


def _client_stars_tx_amount(row: dict) -> int:
    """Signed stars amount for MTProto clients (negative = spend, positive = credit)."""
    amount = int(row['amount'])
    if (amount > 0 and not row.get('refund') and not row.get('offer')
            and not row.get('gift') and amount >= 10_000_000):
        return 0
    return amount


def _ton_history_rows(rows: List[dict]) -> List[dict]:
    out = []
    for row in rows:
        item = dict(row)
        nanos = int(item.get('amount') or 0)
        item['amount'] = 0
        item['amount_ton'] = nanos
        out.append(item)
    return out


def _client_stars_history_rows(rows: List[dict]) -> List[dict]:
    """Omit huge inbound credits — client offer UI miscounts them as reserved."""
    kept = []
    for row in rows:
        amount = int(row.get('amount') or 0)
        if (amount > 0 and not row.get('refund') and not row.get('offer')
                and not row.get('gift') and amount >= 10_000_000):
            continue
        kept.append(row)
    return kept


def _push_stars_balance_refresh(storage, push_queue, user_id: int, *,
                                  exclude_auth_key_id=None) -> None:
    if push_queue is None or not user_id:
        return
    push_queue.push_to_user(
        user_id,
        packers.pack_user_stars_balance(storage, user_id),
        exclude_auth_key_id=exclude_auth_key_id)


def _push_ton_balance_refresh(storage, push_queue, user_id: int, *,
                              exclude_auth_key_id=None) -> None:
    if push_queue is None or not user_id:
        return
    push_queue.push_to_user(
        user_id,
        packers.pack_user_ton_balance(storage, user_id),
        exclude_auth_key_id=exclude_auth_key_id)


def _payment_balance_updates(storage, user_id: int, *,
                             ton: bool = False) -> list[bytes]:
    updates = [packers.pack_user_stars_balance(storage, user_id)]
    if ton:
        updates.append(packers.pack_user_ton_balance(storage, user_id))
    return updates


def _is_active_offer_hold(storage, row: dict) -> bool:
    if not storage or not row.get('offer') or row.get('refund'):
        return False
    amount = int(row.get('amount') or 0)
    if amount >= 0:
        return False
    buyer_id = int(row.get('user_id') or 0)
    owner_id = int(row.get('peer_user_id') or 0)
    if not buyer_id or not owner_id:
        return False
    return storage.has_active_star_gift_offer_hold(
        buyer_id, owner_id, abs(amount))


def pack_stars_transaction(row: dict, storage=None) -> bytes:
    row = _normalize_stars_tx_row(row)
    amount_signed = int(row['amount'])
    flags = 0
    if row.get('refund'):
        flags |= 1 << 3
    if row.get('gift'):
        flags |= 1 << 10
    if row.get('stargift_upgrade'):
        flags |= 1 << 18
    if row.get('stargift_resale'):
        flags |= 1 << 22
    if row.get('stargift_auction_bid'):
        flags |= 1 << 28
    if row.get('offer'):
        flags |= 1 << 29
    if _is_active_offer_hold(storage, row):
        flags |= 1 << 4
    paid_msg_id = int(row.get('paid_messages') or 0)
    if paid_msg_id:
        flags |= 1 << 19
        flags |= 1 << 8
    elif amount_signed > 0 and not row.get('refund') and not row.get('offer'):
        if row.get('title'):
            flags |= 1 << 0
    elif row.get('title') and not paid_msg_id:
        flags |= 1 << 0
    if row.get('description') and amount_signed < 0:
        flags |= 1 << 1
    if (amount_signed < 0 and not (flags & (1 << 4))
            and not row.get('refund') and not row.get('offer')):
        flags |= 1 << 5
    amount_stars = _client_stars_tx_amount(row)
    amount_ton = int(row.get('amount_ton') or 0)
    w = tl.Writer()
    w.u32(STARS_TRANSACTION)
    w.i32(flags)
    w.string(row['tx_id'])
    w.raw(_pack_stars_amount(
        stars=amount_stars, nanos=0, ton=amount_ton))
    w.i32(int(row['date']))
    pw = tl.Writer()
    peer_uid = row.get('peer_user_id')
    peer_cid = row.get('peer_channel_id')
    if peer_uid:
        pw.u32(STARS_TRANSACTION_PEER)
        pw.raw(packers.pack_peer_user(int(peer_uid)))
    elif peer_cid:
        pw.u32(STARS_TRANSACTION_PEER)
        pw.raw(packers.pack_peer_channel(int(peer_cid)))
    else:
        pw.u32(STARS_TRANSACTION_PEER_UNSUPPORTED)
    w.raw(pw.bytes_out())
    if flags & (1 << 0):
        w.string(row.get('title') or '')
    if flags & (1 << 1):
        w.string(row.get('description') or '')
    if flags & (1 << 5):
        w.i32(int(row.get('date') or 0))
        w.string('')
    if flags & (1 << 8):
        w.i32(paid_msg_id or int(row.get('msg_id') or 0))
    if flags & (1 << 19):
        w.i32(paid_msg_id)
    return w.bytes_out()


def _collect_users_for_transactions(storage, rows: List[dict],
                                    viewer_id: int) -> List[dict]:
    users_map = {}
    viewer = storage.get_user_by_id(viewer_id)
    if viewer:
        users_map[viewer_id] = viewer
    service = storage.get_user_by_id(config.SERVICE_USER_ID)
    if service:
        users_map[config.SERVICE_USER_ID] = service
    for row in rows:
        uid = row.get('peer_user_id')
        if uid and int(uid) not in users_map:
            u = storage.get_user_by_id(int(uid))
            if u:
                users_map[int(uid)] = u
    return list(users_map.values())


def _pack_stars_status(storage, user_id: int, *, history=None,
                       next_offset: str = '', ton: bool = False) -> bytes:
    balance = storage.get_ton_balance(user_id) if ton else storage.get_client_stars_balance(user_id)
    history = history or []
    users = _collect_users_for_transactions(storage, history, user_id)
    flags = 0
    if next_offset:
        flags |= 1 << 0
    if history:
        flags |= 1 << 3
    w = tl.Writer()
    w.u32(PAYMENTS_STARS_STATUS)
    w.i32(flags)
    if ton:
        sw = tl.Writer()
        sw.u32(STARS_TON_AMOUNT)
        sw.i64(balance)
        w.raw(sw.bytes_out())
    else:
        w.raw(_pack_stars_amount(stars=balance))
    # TL conditional fields must be serialized in increasing flag-bit order.
    if flags & (1 << 0):
        w.string(next_offset)
    # flags.1 subscriptions — omitted
    # flags.2 subscriptions_next_offset — omitted
    if flags & (1 << 3):
        w.u32(tl.VECTOR)
        w.i32(len(history))
        for row in history:
            w.raw(pack_stars_transaction(row, storage))
    # flags.4 subscriptions_missing_balance — omitted
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, storage=storage))
    return w.bytes_out()


def _prepare_stars_user(storage, user_id: int) -> None:
    storage.ensure_user_stars(user_id)


def handle_payments_get_stars_status(session, req_msg_id, r, storage,
                                     push_queue, registry):
    from handlers.rpc import require_auth
    flags = r.i32()  # ton:flags.0
    ton = bool(flags & 1)
    packers.skip_input_peer(r)
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    if ton:
        recent, _ = storage.list_ton_transactions(viewer['id'], limit=5)
        recent = _ton_history_rows(recent)
        if push_queue is not None and storage.get_ton_balance(viewer['id']) > 0:
            _push_ton_balance_refresh(
                storage, push_queue, viewer['id'],
                exclude_auth_key_id=getattr(session, 'auth_key_id', None))
        return _pack_stars_status(
            storage, viewer['id'], history=recent, ton=True)
    _prepare_stars_user(storage, viewer['id'])
    recent, _ = storage.list_stars_transactions(viewer['id'], limit=5)
    recent = _client_stars_history_rows(recent)
    return _pack_stars_status(storage, viewer['id'], history=recent)


def handle_payments_get_stars_transactions(session, req_msg_id, r, storage,
                                           push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    inbound = bool(flags & (1 << 0))
    outbound = bool(flags & (1 << 1))
    ascending = bool(flags & (1 << 2))
    ton = bool(flags & (1 << 4))
    if flags & (1 << 3):
        r.string()  # subscription_id
    packers.skip_input_peer(r)
    offset = r.string()
    limit = r.i32()

    if ton:
        rows, next_off = storage.list_ton_transactions(
            viewer['id'], offset=offset, limit=limit,
            inbound=inbound, outbound=outbound)
        rows = _ton_history_rows(rows)
        if ascending:
            rows = list(reversed(rows))
        if push_queue is not None and storage.get_ton_balance(viewer['id']) > 0:
            _push_ton_balance_refresh(
                storage, push_queue, viewer['id'],
                exclude_auth_key_id=getattr(session, 'auth_key_id', None))
        return _pack_stars_status(
            storage, viewer['id'], history=rows,
            next_offset=next_off, ton=True)
    _prepare_stars_user(storage, viewer['id'])
    rows, next_off = storage.list_stars_transactions(
        viewer['id'], offset=offset, limit=limit,
        inbound=inbound, outbound=outbound)
    rows = _client_stars_history_rows(rows)
    if ascending:
        rows = list(reversed(rows))
    return _pack_stars_status(
        storage, viewer['id'], history=rows, next_offset=next_off)


def handle_payments_get_stars_transactions_by_id(session, req_msg_id, r,
                                                 storage, push_queue,
                                                 registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()  # ton:flags.0
    ton = bool(flags & 1)
    packers.skip_input_peer(r)
    assert r.u32() == tl.VECTOR
    count = r.i32()
    ids = []
    for _ in range(count):
        cid = r.u32()
        if cid == 0x206ae6d1:  # inputStarsTransaction
            r.i32()  # flags
            ids.append(r.string())
    if ton:
        return _pack_stars_status(storage, viewer['id'], ton=True)
    _prepare_stars_user(storage, viewer['id'])
    rows = storage.get_stars_transactions_by_ids(viewer['id'], ids)
    rows = _client_stars_history_rows(rows)
    return _pack_stars_status(storage, viewer['id'], history=rows)


def handle_payments_get_stars_subscriptions(session, req_msg_id, r, storage,
                                            push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    packers.skip_input_peer(r)
    return _pack_stars_status(storage, viewer['id'])


def _pack_empty_vector() -> bytes:
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_payments_get_stars_topup_options(session, req_msg_id, r, storage,
                                            push_queue, registry):
    return _pack_empty_vector()


def handle_payments_get_stars_gift_options(session, req_msg_id, r, storage,
                                           push_queue, registry):
    flags = r.i32()
    if flags & 1:
        packers.parse_input_user(r, storage)
    return _pack_empty_vector()


def handle_payments_get_saved_star_gifts(session, req_msg_id, r, storage,
                                         push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    if config.GIFTS_HIDDEN:
        # Global hide: inventories stay intact in SQLite, but no profile and
        # no "My gifts" list exposes them.
        w = tl.Writer()
        w.u32(PAYMENTS_SAVED_STAR_GIFTS)
        w.i32(0)  # flags
        w.i32(0)  # count
        w.u32(tl.VECTOR)
        w.i32(0)  # gifts
        w.u32(tl.VECTOR)
        w.i32(0)  # chats
        w.u32(tl.VECTOR)
        w.i32(0)  # users
        return w.bytes_out()

    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    collection_id = None
    if flags & (1 << 6):
        raw_collection_id = r.i32()
        if raw_collection_id > 0:
            collection_id = raw_collection_id
    offset = r.string()
    limit = r.i32()

    target_id = peer_id if peer_id else viewer['id']
    skip = int(offset) if offset.isdigit() else 0
    lim = min(max(limit, 1), 100)

    target_channel = None
    is_channel_target = packers.is_channel_id(int(target_id))
    if viewer['id'] != target_id:
        if is_channel_target:
            target_channel = storage.get_channel(target_id)
            if not target_channel:
                w = tl.Writer()
                w.u32(PAYMENTS_SAVED_STAR_GIFTS)
                w.i32(0)
                w.i32(0)
                w.u32(tl.VECTOR)
                w.i32(0)
                w.u32(tl.VECTOR)
                w.i32(0)
                w.u32(tl.VECTOR)
                w.i32(0)
                return w.bytes_out()
        else:
            target = storage.get_user_by_id(target_id)
            if not target:
                w = tl.Writer()
                w.u32(PAYMENTS_SAVED_STAR_GIFTS)
                w.i32(0)
                w.i32(0)
                w.u32(tl.VECTOR)
                w.i32(0)
                w.u32(tl.VECTOR)
                w.i32(0)
                w.u32(tl.VECTOR)
                w.i32(0)
                return w.bytes_out()

    exclude_saved = bool(flags & (1 << 1))
    exclude_unsaved = bool(flags & (1 << 0))
    viewer_manages_channel = False
    if is_channel_target:
        viewer_manages_channel = storage.is_channel_admin(
            int(target_id), int(viewer['id']))
    if viewer['id'] != target_id and not viewer_manages_channel:
        # Others only see gifts pinned on the profile.
        exclude_unsaved = True
        exclude_saved = False
    elif exclude_saved:
        exclude_unsaved = False

    users_map = {}
    chats_map = {}
    if is_channel_target:
        ch = target_channel or storage.get_channel(int(target_id))
        if ch:
            chats_map[int(ch['id'])] = ch
    packed_rows = []
    total = 0
    batch_skip = skip
    attempts = 0
    while len(packed_rows) < lim and attempts < 6:
        rows, total = storage.list_saved_star_gifts(
            target_id, batch_skip, lim,
            exclude_unsaved=exclude_unsaved,
            exclude_saved=exclude_saved,
            exclude_unlimited=bool(flags & (1 << 2)),
            exclude_unique=bool(flags & (1 << 4)),
            sort_by_value=bool(flags & (1 << 5)),
            exclude_upgradable=bool(flags & (1 << 7)),
            exclude_unupgradable=bool(flags & (1 << 8)),
            peer_color_available=bool(flags & (1 << 9)),
            collection_id=collection_id,
        )
        if not rows:
            break
        for row in rows:
            try:
                packed_rows.append(pack_saved_star_gift(
                    row, storage, viewer_id=viewer['id'],
                    profile_owner_id=target_id))
            except (KeyError, ValueError, TypeError) as exc:
                log.warning('pack_saved_star_gift %s failed: %s',
                            row.get('saved_id'), exc)
                continue
            if (_saved_gift_details_visible(
                    row, viewer['id'], target_id, storage=storage)
                    and row.get('from_user_id')):
                u = storage.get_user_by_id(int(row['from_user_id']))
                if u:
                    users_map[u['id']] = u
            if len(packed_rows) >= lim:
                break
        batch_skip += len(rows)
        attempts += 1
        if batch_skip >= total:
            break
    log.debug(
        'getSavedStarGifts viewer=%s target=%s total=%d page=%d '
        'collection=%s flags=%#x ex_unsaved=%d ex_saved=%d ex_unique=%d '
        'ex_upgradable=%d ex_unupgradable=%d',
        viewer['id'], target_id, total, len(packed_rows), collection_id,
        flags, int(exclude_unsaved), int(exclude_saved),
        int(bool(flags & (1 << 4))), int(bool(flags & (1 << 7))),
        int(bool(flags & (1 << 8))))

    if batch_skip > skip and not packed_rows:
        log.debug(
            'getSavedStarGifts viewer=%s target=%s: rows failed to pack at offset %d',
            viewer['id'], target_id, skip)

    next_off = str(batch_skip) if batch_skip < total else None
    w = tl.Writer()
    w.u32(PAYMENTS_SAVED_STAR_GIFTS)
    out_flags = 0
    if next_off:
        out_flags |= 1 << 0
    w.i32(out_flags)
    w.i32(total)
    w.u32(tl.VECTOR)
    w.i32(len(packed_rows))
    for packed in packed_rows:
        w.raw(packed)
    if next_off:
        w.string(next_off)
    chats = list(chats_map.values())
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(packers.pack_channel(ch, viewer['id'], storage=storage))
    users = list(users_map.values())
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(
            u, storage=storage, viewer_id=viewer['id'],
            is_self=int(u['id']) == int(viewer['id'])))
    return w.bytes_out()


def _resolve_saved_star_gift(ref: dict, viewer_id: int,
                             storage: Storage) -> Optional[dict]:
    if ref['kind'] == 'slug':
        return storage.get_saved_star_gift_by_slug(ref['slug'])
    if ref['kind'] == 'user':
        return storage.get_saved_star_gift_by_msg(viewer_id, ref['msg_id'])
    if ref['kind'] == 'chat':
        saved = storage.get_saved_star_gift_by_id(int(ref['saved_id']))
        if not saved:
            return None
        peer_id = ref.get('peer_id')
        if peer_id and int(saved.get('owner_user_id') or 0) != int(peer_id):
            return None
        return saved
    return None


def handle_payments_get_saved_star_gift(session, req_msg_id, r, storage,
                                        push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    assert r.u32() == tl.VECTOR
    n = r.i32()
    rows = []
    users_map = {}
    for _ in range(n):
        try:
            ref = parse_input_saved_star_gift(r)
            if ref.get('kind') == 'slug':
                burned = storage.get_saved_star_gift_by_slug(ref['slug'])
                if burned and int(burned.get('burned') or 0):
                    return _rpc_error(400, 'STARGIFT_ALREADY_BURNED')
            row = _resolve_saved_star_gift(ref, viewer['id'], storage)
            if row:
                owner_id = int(row.get('owner_user_id') or viewer['id'])
                if (int(row.get('unsaved') or 0)
                        and viewer['id'] != owner_id):
                    continue
                rows.append(row)
                if (_saved_gift_details_visible(
                        row, viewer['id'], owner_id, storage=storage)
                        and row.get('from_user_id')):
                    u = storage.get_user_by_id(int(row['from_user_id']))
                    if u:
                        users_map[u['id']] = u
        except (ValueError, KeyError):
            continue

    w = tl.Writer()
    w.u32(PAYMENTS_SAVED_STAR_GIFTS)
    w.i32(0)
    w.i32(len(rows))
    w.u32(tl.VECTOR)
    w.i32(len(rows))
    chats_map = {}
    for row in rows:
        owner_id = int(row.get('owner_user_id') or viewer['id'])
        w.raw(pack_saved_star_gift(
            row, storage, viewer_id=viewer['id'], profile_owner_id=owner_id))
        if packers.is_channel_id(owner_id) and owner_id not in chats_map:
            ch = storage.get_channel(owner_id)
            if ch:
                chats_map[owner_id] = ch
    chats = list(chats_map.values())
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(packers.pack_channel(ch, viewer['id'], storage=storage))
    users = list(users_map.values())
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(
            u, storage=storage, viewer_id=viewer['id'],
            is_self=int(u['id']) == int(viewer['id'])))
    return w.bytes_out()


def _viewer_owns_or_manages_gift(storage: Storage, viewer_id: int,
                                 saved: dict) -> bool:
    owner_id = int(saved.get('owner_user_id') or 0)
    if owner_id == int(viewer_id):
        return True
    if packers.is_channel_id(owner_id):
        return storage.is_channel_admin(owner_id, int(viewer_id))
    return False


def handle_payments_save_star_gift(session, req_msg_id, r, storage,
                                   push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    unsave = bool(flags & 1)
    ref = parse_input_saved_star_gift(r)
    saved = _resolve_saved_star_gift(ref, viewer['id'], storage)
    if (not saved
            or not _viewer_owns_or_manages_gift(storage, viewer['id'], saved)):
        return _rpc_error(400, 'STARGIFT_NOT_FOUND')
    storage.update_saved_star_gift_flags(
        saved['saved_id'], unsaved=unsave)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_payments_convert_star_gift(session, req_msg_id, r, storage,
                                      push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    ref = parse_input_saved_star_gift(r)
    saved = None
    if ref['kind'] == 'user':
        saved = storage.get_saved_star_gift_by_msg(viewer['id'], ref['msg_id'])
    if not saved:
        return _rpc_error(400, 'STARGIFT_NOT_FOUND')
    if storage.saved_gift_chain_owner(saved):
        return _rpc_error(400, 'STARGIFT_ON_BLOCKCHAIN')
    original_stars = int(saved.get('stars') or 0)
    stars = int(saved.get('convert_stars') or 0)
    if original_stars > 0:
        storage.apply_stars_rating_convert_penalty(viewer['id'], original_stars)
    if stars > 0:
        storage.adjust_stars_balance(
            viewer['id'], stars, title='Gift conversion',
            description='Converted gift to stars', _skip_rating=True)
    storage._conn.execute(
        'DELETE FROM saved_star_gifts WHERE saved_id=?', (saved['saved_id'],))
    storage._conn.commit()
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def _resolve_saved_star_gift_id(ref: dict, viewer_id: int,
                                storage: Storage) -> Optional[int]:
    saved = _resolve_saved_star_gift(ref, viewer_id, storage)
    if not saved:
        return None
    return int(saved['saved_id'])


def _parse_input_saved_star_gift_vector(r: tl.Reader) -> List[dict]:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    refs = []
    for _ in range(count):
        refs.append(parse_input_saved_star_gift(r))
    return refs


def _resolve_saved_ids_from_refs(refs: List[dict], viewer_id: int,
                                 storage: Storage) -> List[int]:
    saved_ids = []
    for ref in refs:
        sid = _resolve_saved_star_gift_id(ref, viewer_id, storage)
        if sid is not None:
            saved_ids.append(sid)
    return saved_ids


def _collection_with_items(storage: Storage, collection_id: int,
                           owner_id: int) -> Optional[dict]:
    for col in storage.list_star_gift_collections(owner_id):
        if int(col['collection_id']) == int(collection_id):
            return col
    return None


def _compute_collection_hash(col: dict) -> int:
    import hashlib
    parts = (f"{col['collection_id']}:{col.get('title') or ''}:"
             f"{','.join(str(x) for x in (col.get('gift_saved_ids') or []))}")
    h = hashlib.sha256(parts.encode()).digest()
    return int.from_bytes(h[:8], 'big', signed=True)


def pack_star_gift_collection(col: dict, storage: Storage,
                              viewer_id: Optional[int] = None) -> bytes:
    """Pack a starGiftCollection object (layer 210+)."""
    gifts = col.get('gift_saved_ids') or []
    w = tl.Writer()
    w.u32(PAYMENTS_STAR_GIFT_COLLECTION)
    w.i32(0)  # flags (no icon)
    w.i32(int(col['collection_id']))
    w.string(col.get('title') or '')
    w.i32(len(gifts))
    w.i64(_compute_collection_hash(col))
    return w.bytes_out()


def _pack_star_gift_collections(cols, storage: Storage,
                                viewer_id: Optional[int] = None) -> bytes:
    w = tl.Writer()
    w.u32(PAYMENTS_STAR_GIFT_COLLECTIONS)
    w.u32(tl.VECTOR)
    w.i32(len(cols))
    for col in cols:
        w.raw(pack_star_gift_collection(col, storage, viewer_id))
    return w.bytes_out()


def _visible_star_gift_collections(cols: list) -> list:
    """Only titled collections are shown in profile."""
    return [c for c in cols if (c.get('title') or '').strip()]


def _get_collections_response(storage: Storage, peer_id: int,
                              req_hash: int,
                              viewer_id: Optional[int] = None) -> bytes:
    if config.GIFTS_HIDDEN:
        w = tl.Writer()
        w.u32(PAYMENTS_STAR_GIFT_COLLECTIONS_NOT_MODIFIED)
        return w.bytes_out()
    cols = storage.list_visible_star_gift_collections(peer_id)
    current_hash = storage.compute_star_gift_collections_hash(peer_id)
    if req_hash and req_hash == current_hash:
        w = tl.Writer()
        w.u32(PAYMENTS_STAR_GIFT_COLLECTIONS_NOT_MODIFIED)
        return w.bytes_out()
    if not cols:
        w = tl.Writer()
        w.u32(PAYMENTS_STAR_GIFT_COLLECTIONS_NOT_MODIFIED)
        return w.bytes_out()
    return _pack_star_gift_collections(cols, storage, viewer_id)


def handle_payments_get_star_gift_collections(session, req_msg_id, r, storage,
                                              push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        peer_id = None
        try:
            peer_id = packers.parse_input_peer(r)
        except Exception:
            pass
        _hash = r.i64()
        w = tl.Writer()
        w.u32(PAYMENTS_STAR_GIFT_COLLECTIONS_NOT_MODIFIED)
        return w.bytes_out()

    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    req_hash = r.i64()
    target_id = peer_id if peer_id and peer_id > 0 else viewer['id']
    return _get_collections_response(storage, target_id, req_hash, viewer['id'])


def handle_payments_get_star_gift_collections_v2(session, req_msg_id, r,
                                                  storage, push_queue,
                                                  registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    _flags = r.i32()
    peer_id = None
    try:
        peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'] if viewer else 0)
    except Exception:
        pass
    req_hash = r.i64()
    if viewer is None:
        w = tl.Writer()
        w.u32(PAYMENTS_STAR_GIFT_COLLECTIONS_NOT_MODIFIED)
        return w.bytes_out()
    target_id = peer_id if peer_id and peer_id > 0 else viewer['id']
    return _get_collections_response(storage, target_id, req_hash, viewer['id'])


def handle_payments_create_star_gift_collection(session, req_msg_id, r,
                                                storage, push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = 0
    title = ''
    gift_refs: List[dict] = []
    try:
        flags = r.i32()
        try:
            packers.parse_input_peer(r, self_user_id=viewer['id'])
        except Exception:
            pass
        if flags & 1:
            title = r.string()
        else:
            title = r.string()
    except Exception:
        pass

    return _finish_create_star_gift_collection(
        storage, viewer, title, gift_refs)


def handle_payments_create_star_gift_collection_v2(session, req_msg_id, r,
                                                  storage, push_queue,
                                                  registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if int(peer_id or 0) != int(viewer['id']):
        return _rpc_error(400, 'PEER_ID_INVALID')
    title = r.string()
    gift_refs = _parse_input_saved_star_gift_vector(r)
    return _finish_create_star_gift_collection(
        storage, viewer, title, gift_refs)


def _finish_create_star_gift_collection(storage: Storage, viewer: dict,
                                        title: str,
                                        _gift_refs: List[dict]) -> bytes:
    """Create an empty collection; gifts are added via updateStarGiftCollection."""
    col = storage.create_star_gift_collection(viewer['id'], title or '')
    col = (_collection_with_items(
        storage, col['collection_id'], viewer['id']) or col)
    return pack_star_gift_collection(col, storage, viewer['id'])


def handle_payments_update_star_gift_collection(session, req_msg_id, r,
                                                storage, push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if int(peer_id or 0) != int(viewer['id']):
        return _rpc_error(400, 'PEER_ID_INVALID')
    collection_id = r.i32()

    title = None
    if flags & (1 << 0):
        title = r.string()
    delete_ids: List[int] = []
    if flags & (1 << 1):
        delete_ids = _resolve_saved_ids_from_refs(
            _parse_input_saved_star_gift_vector(r), viewer['id'], storage)
    add_ids: List[int] = []
    if flags & (1 << 2):
        add_ids = _resolve_saved_ids_from_refs(
            _parse_input_saved_star_gift_vector(r), viewer['id'], storage)
    order_ids: List[int] = []
    if flags & (1 << 3):
        order_ids = _resolve_saved_ids_from_refs(
            _parse_input_saved_star_gift_vector(r), viewer['id'], storage)

    col = storage.get_star_gift_collection(collection_id)
    if not col or int(col['owner_id']) != int(viewer['id']):
        return _rpc_error(400, 'COLLECTION_ID_INVALID')

    if title is not None:
        storage.edit_star_gift_collection(
            collection_id, viewer['id'], title=title)
    if delete_ids:
        storage.remove_gifts_from_collection(
            collection_id, viewer['id'], delete_ids)
    if add_ids:
        storage.add_gifts_to_collection(
            collection_id, viewer['id'], add_ids)
    if order_ids:
        storage.reorder_gifts_in_collection(
            collection_id, viewer['id'], order_ids)

    col = _collection_with_items(storage, collection_id, viewer['id'])
    if not col:
        return _rpc_error(400, 'COLLECTION_ID_INVALID')
    return pack_star_gift_collection(col, storage, viewer['id'])


def handle_payments_edit_star_gift_collection(session, req_msg_id, r,
                                              storage, push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    collection_id = 0
    title = None
    try:
        flags = r.i32()
        collection_id = r.i64()
        if flags & 1:
            title = r.string()
    except Exception:
        pass

    if collection_id:
        col = storage.edit_star_gift_collection(
            collection_id, viewer['id'], title=title)
        if col:
            col = (_collection_with_items(
                storage, collection_id, viewer['id']) or col)
            return pack_star_gift_collection(col, storage, viewer['id'])
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_payments_delete_star_gift_collection(session, req_msg_id, r,
                                                storage, push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    collection_id = 0
    try:
        collection_id = r.i64()
    except Exception:
        pass

    if collection_id:
        storage.delete_star_gift_collection(collection_id, viewer['id'])
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_payments_delete_star_gift_collection_v2(session, req_msg_id, r,
                                                  storage, push_queue,
                                                  registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if int(peer_id or 0) != int(viewer['id']):
        return _rpc_error(400, 'PEER_ID_INVALID')
    collection_id = r.i32()
    storage.delete_star_gift_collection(collection_id, viewer['id'])
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def pack_global_privacy_settings(user: Optional[dict] = None) -> bytes:
    from handlers.paid_messages import pack_global_privacy_settings as _pack
    return _pack(user)


def _resolve_star_gift_for_action(action: dict, storage: Storage,
                                  owner_user_id: int = 0
                                  ) -> tuple[Optional[dict], int, Optional[dict]]:
    saved_row = action.get('saved_row')
    gift_id = int(action.get('gift_id') or 0)
    if saved_row:
        gift_id = int(saved_row.get('gift_id') or gift_id)
    gift = storage.get_star_gift(gift_id) if gift_id else None
    if not gift and saved_row:
        gift_id = int(saved_row['gift_id'])
        gift = storage.get_star_gift(gift_id)
    if not gift:
        sid = int(action.get('saved_id') or 0)
        if sid:
            saved_row = (saved_row
                         or storage.get_saved_star_gift_by_msg(owner_user_id, sid)
                         or storage.get_saved_star_gift_by_id(sid))
            if saved_row:
                gift_id = int(saved_row['gift_id'])
                gift = storage.get_star_gift(gift_id)
    return gift, gift_id, saved_row


def pack_message_action_star_gift(action: dict, storage: Storage,
                                  from_user_id: int,
                                  owner_user_id: int = 0) -> bytes:
    gift, gift_id, saved_row = _resolve_star_gift_for_action(
        action, storage, owner_user_id)
    if not gift:
        raise KeyError(action.get('gift_id'))

    action = dict(action, saved_row=saved_row) if saved_row else action
    name_hidden = _gift_name_hidden(action, saved_row)
    message_text, entities = _gift_message_fields(action, saved_row)
    convert_stars = int(action.get('convert_stars') or gift.get('convert_stars') or 0)
    prepaid = bool(action.get('prepaid_upgrade'))
    can_upgrade = False
    upgrade_stars = None
    if not action.get('upgraded'):
        can_upgrade = bool(action.get('can_upgrade'))
        if not can_upgrade:
            can_upgrade = storage.gift_can_upgrade(gift_id)
        if can_upgrade:
            upgrade_stars = int(
                action.get('upgrade_stars')
                or storage.get_gift_upgrade_stars(gift_id))

    flags = 0
    if name_hidden:
        flags |= 1 << 0
    if _gift_message_has_content(message_text, entities):
        flags |= 1 << 1
    if action.get('saved'):
        flags |= 1 << 2
    if action.get('upgraded'):
        flags |= 1 << 5
    if convert_stars:
        flags |= 1 << 4
    if can_upgrade:
        flags |= 1 << 10
    if prepaid:
        flags |= 1 << 13
    if upgrade_stars:
        flags |= 1 << 8
    if from_user_id and not name_hidden:
        flags |= 1 << 11
    if action.get('saved_id') is not None:
        flags |= 1 << 12
    if action.get('gift_msg_id'):
        flags |= 1 << 15
    if action.get('auction_acquired'):
        flags |= 1 << 17
    if action.get('to_id') is not None:
        flags |= 1 << 18
    if action.get('gift_num') is not None:
        flags |= 1 << 19

    w = tl.Writer()
    w.u32(MESSAGE_ACTION_STAR_GIFT)
    w.i32(flags)
    stars_override = None
    if action.get('auction_acquired'):
        if saved_row and saved_row.get('stars') is not None:
            stars_override = int(saved_row['stars'])
        elif action.get('bid_amount') is not None:
            stars_override = int(action['bid_amount'])
    w.raw(pack_star_gift(
        gift, storage, stars_override=stars_override))
    if flags & (1 << 1):
        w.raw(_pack_text_with_entities(message_text, entities))
    if flags & (1 << 4):
        w.i64(convert_stars)
    if flags & (1 << 5):
        w.i32(int(action.get('upgrade_msg_id') or 0))
    if flags & (1 << 8) and upgrade_stars:
        w.i64(int(upgrade_stars))
    if flags & (1 << 11):
        w.raw(packers.pack_peer_user(int(from_user_id)))
    if flags & (1 << 12):
        peer_id = int(action.get('peer_id') or owner_user_id or from_user_id)
        w.raw(packers.pack_peer(peer_id))
        w.i64(int(action['saved_id']))
    if flags & (1 << 15):
        w.i32(int(action['gift_msg_id']))
    if flags & (1 << 18):
        w.raw(packers.pack_peer(int(action['to_id'])))
    if flags & (1 << 19):
        w.i32(int(action['gift_num']))
    return w.bytes_out()


def _resolve_saved_row_for_unique_action(action: dict,
                                         storage: Storage,
                                         owner_id: Optional[int] = None
                                         ) -> Optional[dict]:
    if action.get('saved_row'):
        return action['saved_row']
    saved = None
    unique_id = int(action.get('unique_id') or 0)
    if unique_id and owner_id is not None:
        saved = storage.get_saved_star_gift_by_unique_id(owner_id, unique_id)
    if not saved and unique_id:
        saved = storage.get_saved_star_gift_by_unique_id_global(unique_id)
    if not saved and action.get('saved_id'):
        saved = storage.get_saved_star_gift_by_msg_id(int(action['saved_id']))
        if not saved and owner_id is not None:
            saved = storage.get_saved_star_gift_by_msg(
                owner_id, int(action['saved_id']))
    if not saved:
        for raw_id in (unique_id, int(action.get('gift_id') or 0)):
            if not raw_id:
                continue
            if storage.get_star_gift(raw_id):
                continue
            saved = storage.get_saved_star_gift_by_unique_id_global(raw_id)
            if saved:
                break
    return saved


def _gift_meta_for_unique_action(action: dict, saved_row: Optional[dict],
                                 storage: Storage) -> tuple:
    gift_id = int((saved_row or {}).get('gift_id') or action.get('gift_id') or 0)
    if saved_row:
        gift_id = int(saved_row['gift_id'])
    elif gift_id and not storage.get_star_gift(gift_id):
        alt = storage.get_saved_star_gift_by_unique_id_global(gift_id)
        if alt:
            saved_row = alt
            gift_id = int(alt['gift_id'])
    gift_meta = storage.get_star_gift(gift_id) if gift_id else None
    return saved_row, gift_meta, gift_id


def pack_message_action_star_gift_purchase_offer(action: dict,
                                                 storage: Storage) -> bytes:
    saved = storage.get_saved_star_gift_by_id(int(action['saved_id']))
    if not saved:
        raise KeyError(action.get('saved_id'))
    gift_meta = _resolve_gift_meta(storage, saved)

    flags = 0
    if action.get('accepted'):
        flags |= 1 << 0
    if action.get('declined'):
        flags |= 1 << 1

    w = tl.Writer()
    w.u32(MESSAGE_ACTION_STAR_GIFT_PURCHASE_OFFER)
    w.i32(flags)
    w.raw(pack_star_gift_unique(saved, gift_meta, storage))
    if int(action.get('price_ton') or 0) > 0:
        w.raw(_pack_stars_amount(ton=int(action['price_ton'])))
    else:
        w.raw(_pack_stars_amount(
            stars=int(action.get('price_stars') or 0),
            nanos=int(action.get('price_nanos') or 0)))
    w.i32(int(action.get('expires_at') or 0))
    return w.bytes_out()


def pack_message_action_star_gift_purchase_offer_declined(
        action: dict, storage: Storage) -> bytes:
    saved = storage.get_saved_star_gift_by_id(int(action['saved_id']))
    if not saved:
        saved = storage.get_saved_star_gift_by_slug(action.get('slug') or '')
    if not saved:
        raise KeyError(action.get('saved_id') or action.get('slug'))
    gift_meta = _resolve_gift_meta(storage, saved)

    flags = 0
    if action.get('expired'):
        flags |= 1 << 0

    w = tl.Writer()
    w.u32(MESSAGE_ACTION_STAR_GIFT_PURCHASE_OFFER_DECLINED)
    w.i32(flags)
    w.raw(pack_star_gift_unique(saved, gift_meta, storage))
    if int(action.get('price_ton') or 0) > 0:
        w.raw(_pack_stars_amount(ton=int(action['price_ton'])))
    else:
        w.raw(_pack_stars_amount(
            stars=int(action.get('price_stars') or 0),
            nanos=int(action.get('price_nanos') or 0)))
    return w.bytes_out()


def pack_message_action_star_gift_unique(action: dict, storage: Storage,
                                         owner_user_id: Optional[int] = None
                                         ) -> bytes:
    saved_row, gift_meta, gift_id = _gift_meta_for_unique_action(
        action,
        _resolve_saved_row_for_unique_action(
            action, storage, owner_user_id),
        storage)
    if not saved_row or not gift_meta:
        raise KeyError(gift_id or action.get('unique_id') or action.get('gift_id'))

    now = int(time.time())
    flags = 0
    if action.get('upgrade'):
        flags |= 1 << 0
    if action.get('transferred'):
        flags |= 1 << 1
    if action.get('saved'):
        flags |= 1 << 2
    if action.get('from_offer'):
        flags |= 1 << 14
    if action.get('craft'):
        flags |= 1 << 16
    flags |= (1 << 3) | (1 << 4) | (1 << 9) | (1 << 10)
    if action.get('from_id') is not None or saved_row.get('from_user_id'):
        flags |= 1 << 6
    if action.get('peer_id') is not None and action.get('saved_id') is not None:
        flags |= 1 << 7
    if action.get('resale_amount') is not None:
        flags |= 1 << 8
    can_craft_at = _saved_gift_can_craft_at(saved_row, storage, now=now)
    if can_craft_at is not None:
        flags |= 1 << 15
    # Layer 229 added name_hidden/message to messageActionStarGiftUnique.
    # Omitting the bit for a hidden gift makes clients reveal the sender.
    if packers.current_layer() >= 229 and _gift_name_hidden(action, saved_row):
        flags |= 1 << 17

    w = tl.Writer()
    w.u32(packers.star_gift_unique_action_ctor())
    w.i32(flags)
    w.raw(pack_star_gift_unique(saved_row, gift_meta, storage))
    w.i32(int(action.get('can_export_at') or now))
    w.i64(int(action.get('transfer_stars') or saved_row.get('transfer_stars') or 25))
    if flags & (1 << 6):
        from_id = int(action.get('from_id') or saved_row.get('from_user_id') or 0)
        w.raw(packers.pack_peer_user(from_id))
    if flags & (1 << 7):
        w.raw(packers.pack_peer(int(action['peer_id'])))
        w.i64(int(action['saved_id']))
    if flags & (1 << 8):
        if int(action.get('resale_ton') or 0) > 0:
            w.raw(_pack_stars_amount(ton=int(action['resale_ton'])))
        else:
            w.raw(_pack_stars_amount(
                stars=int(action['resale_amount']),
                nanos=int(action.get('resale_nanos') or 0)))
    w.i32(int(action.get('can_transfer_at') or now))
    w.i32(int(action.get('can_resell_at') or now))
    if flags & (1 << 15):
        w.i32(int(can_craft_at))
    return w.bytes_out()


def pack_service_message(msg: dict, action: dict,
                         storage: Optional[Storage]) -> bytes:
    owner_id = msg['owner_id']
    from_id = msg['from_id']
    peer_id = msg['peer_id']

    is_out = packers._message_is_out(msg)
    is_channel = packers.is_channel_id(int(peer_id))

    flags = 0
    if is_out:
        flags |= 1 << 1
    # Channel gift posts must keep from_id even when out — otherwise clients
    # attribute the gift to the channel itself ("channel sent a gift").
    if (not is_out) or (is_channel and from_id):
        flags |= 1 << 8
    if is_channel:
        flags |= 1 << 9  # reactions_are_possible
        if storage is not None:
            ch = storage.get_channel(int(peer_id))
            if ch and not int(ch.get('megagroup') or 0):
                flags |= 1 << 14  # post

    chat_peer = peer_id if is_out else from_id
    if is_channel:
        chat_peer = peer_id

    w = tl.Writer()
    w.u32(MESSAGE_SERVICE)
    w.i32(flags)
    w.i32(msg['id'])
    if flags & (1 << 8):
        w.raw(packers.pack_peer_user(from_id))
    w.raw(packers.pack_peer(chat_peer))
    w.i32(msg['date'])
    try:
        if action.get('type') == 'star_gift_unique':
            if storage and action.get('saved_row') is None:
                saved = _resolve_saved_row_for_unique_action(
                    action, storage, owner_id=owner_id)
                if saved:
                    action = dict(action, saved_row=saved)
            w.raw(pack_message_action_star_gift_unique(
                action, storage, owner_user_id=owner_id))
        elif action.get('type') == 'star_gift_purchase_offer':
            w.raw(pack_message_action_star_gift_purchase_offer(action, storage))
        elif action.get('type') == 'star_gift_purchase_offer_declined':
            w.raw(pack_message_action_star_gift_purchase_offer_declined(
                action, storage))
        else:
            if storage and action.get('type') == 'star_gift' and action.get('saved_row') is None:
                sid = int(action.get('saved_id') or msg.get('id') or 0)
                if sid:
                    saved = storage.get_saved_star_gift_by_id(sid)
                    if not saved and not is_channel:
                        saved = storage.get_saved_star_gift_by_msg(owner_id, sid)
                    if saved:
                        action = dict(action, saved_row=saved)
            # Normal channel gifts must not set auction-only to_id
            if is_channel and action.get('type') == 'star_gift':
                action = dict(action)
                action.pop('to_id', None)
            w.raw(pack_message_action_star_gift(
                action, storage, from_id, owner_user_id=owner_id))
    except (KeyError, ValueError, TypeError) as exc:
        log.warning('pack_service_message %s failed: %s', msg.get('id'), exc)
        aw = tl.Writer()
        aw.u32(0xb6aef7b0)  # messageActionEmpty
        w.raw(aw.bytes_out())
    return w.bytes_out()


def parse_input_invoice_star_gift(r: tl.Reader,
                                  self_user_id: Optional[int] = None) -> dict:
    cid = r.u32()
    if cid != INPUT_INVOICE_STAR_GIFT:
        raise ValueError(f'unsupported InputInvoice {cid:#010x}')
    flags = r.i32()
    hide_name = bool(flags & 1)
    peer_id = packers.parse_input_peer(r, self_user_id=self_user_id)
    gift_id = r.i64()
    message_text = ''
    message_entities = []
    if flags & 2:
        assert r.u32() == TEXT_WITH_ENTITIES
        message_text = r.string()
        message_entities = packers.parse_entities(r)
    return {
        'peer_id': peer_id,
        'gift_id': gift_id,
        'hide_name': hide_name,
        'include_upgrade': bool(flags & 4),
        'pay_ton': bool(flags & 8),
        'message_text': message_text,
        'message_entities': message_entities,
    }


def parse_input_invoice_star_gift_upgrade(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid != INPUT_INVOICE_STAR_GIFT_UPGRADE:
        raise ValueError(f'unsupported InputInvoice {cid:#010x}')
    flags = r.i32()
    ref = parse_input_saved_star_gift(r)
    return {
        'type': 'upgrade',
        'keep_original_details': bool(flags & 1),
        'stargift': ref,
    }


def parse_input_invoice_star_gift_resale(r: tl.Reader,
                                         self_user_id: Optional[int] = None
                                         ) -> dict:
    assert r.u32() == INPUT_INVOICE_STAR_GIFT_RESALE
    flags = r.i32()
    slug = r.string()
    peer_id = packers.parse_input_peer(r, self_user_id=self_user_id)
    return {
        'type': 'resale',
        'slug': slug,
        'peer_id': peer_id,
        'ton': bool(flags & 1),
    }


def parse_input_invoice_star_gift_transfer(r: tl.Reader,
                                           self_user_id: Optional[int] = None
                                           ) -> dict:
    assert r.u32() == INPUT_INVOICE_STAR_GIFT_TRANSFER
    ref = parse_input_saved_star_gift(r)
    to_id = packers.parse_input_peer(r, self_user_id=self_user_id)
    return {'type': 'transfer', 'stargift': ref, 'peer_id': to_id}


def parse_input_invoice_star_gift_auction_bid(
        r: tl.Reader, self_user_id: Optional[int] = None) -> dict:
    cid = r.u32()
    if cid != INPUT_INVOICE_STAR_GIFT_AUCTION_BID:
        raise ValueError(f'unsupported InputInvoice {cid:#010x}')
    flags = r.i32()
    hide_name = bool(flags & 1)
    update_bid = bool(flags & 4)
    peer_id = None
    if flags & 8:
        peer_id = packers.parse_input_peer(r, self_user_id=self_user_id)
    gift_id = r.i64()
    bid_amount = r.i64()
    message_text = ''
    message_entities = []
    if flags & 2:
        assert r.u32() == TEXT_WITH_ENTITIES
        message_text = r.string()
        message_entities = packers.parse_entities(r)
    return {
        'type': 'auction_bid',
        'peer_id': peer_id,
        'gift_id': gift_id,
        'bid_amount': int(bid_amount),
        'hide_name': hide_name,
        'update_bid': update_bid,
        'message_text': message_text,
        'message_entities': message_entities,
    }


def parse_input_invoice(r: tl.Reader,
                        self_user_id: Optional[int] = None) -> dict:
    pos = r.pos
    cid = r.u32()
    r.pos = pos
    if cid == INPUT_INVOICE_STAR_GIFT:
        return parse_input_invoice_star_gift(r, self_user_id)
    if cid == INPUT_INVOICE_STAR_GIFT_UPGRADE:
        return parse_input_invoice_star_gift_upgrade(r)
    if cid == INPUT_INVOICE_STAR_GIFT_RESALE:
        return parse_input_invoice_star_gift_resale(r, self_user_id)
    if cid == INPUT_INVOICE_STAR_GIFT_TRANSFER:
        return parse_input_invoice_star_gift_transfer(r, self_user_id)
    if cid == INPUT_INVOICE_STAR_GIFT_AUCTION_BID:
        return parse_input_invoice_star_gift_auction_bid(r, self_user_id)
    raise ValueError(f'unsupported InputInvoice {cid:#010x}')


def _saved_from_stargift_ref(storage: Storage, viewer_id: int,
                             ref: dict) -> Optional[dict]:
    if ref.get('kind') != 'user':
        return None
    return storage.get_saved_star_gift_by_msg(viewer_id, int(ref['msg_id']))


def _upgrade_payment_amount(storage: Storage, invoice: dict,
                            viewer_id: int) -> int:
    saved = _saved_from_stargift_ref(
        storage, viewer_id, invoice.get('stargift') or {})
    if not saved:
        raise ValueError('STARGIFT_NOT_FOUND')
    if int(saved.get('prepaid_upgrade') or 0):
        return 0
    gift = storage.get_star_gift(int(saved['gift_id']))
    if not gift or not storage.gift_can_upgrade(int(saved['gift_id'])):
        raise ValueError('STARGIFT_NOT_UPGRADABLE')
    return storage.get_gift_upgrade_stars(int(saved['gift_id']))


def _perform_upgrade(storage: Storage, viewer_id: int,
                     invoice: dict) -> dict:
    ref = invoice.get('stargift') or {}
    if ref.get('kind') != 'user':
        raise ValueError('STARGIFT_INVALID')
    saved = storage.get_saved_star_gift_by_msg(viewer_id, int(ref['msg_id']))
    free_upgrade = (
        invoice.get('type') == 'upgrade_free'
        or bool(saved and int(saved.get('prepaid_upgrade') or 0)))
    return storage.upgrade_saved_star_gift(
        viewer_id,
        int(ref['msg_id']),
        keep_original=bool(invoice.get('keep_original_details')),
        deduct_stars=not free_upgrade,
    )


def _transfer_payment_amount(storage: Storage, invoice: dict,
                             viewer_id: int) -> int:
    saved = _saved_from_stargift_ref(
        storage, viewer_id, invoice.get('stargift') or {})
    if not saved or not int(saved.get('upgraded') or 0):
        raise ValueError('STARGIFT_NOT_FOUND')
    if storage.saved_gift_chain_owner(saved):
        raise ValueError('STARGIFT_ON_BLOCKCHAIN')
    return int(saved.get('transfer_stars') or 0)


def _perform_transfer(storage: Storage, viewer_id: int,
                      invoice: dict) -> dict:
    ref = invoice.get('stargift') or {}
    to_id = int(invoice.get('peer_id') or 0)
    if ref.get('kind') != 'user' or not to_id:
        raise ValueError('STARGIFT_INVALID')
    saved = _saved_from_stargift_ref(storage, viewer_id, ref)
    if saved and storage.saved_gift_chain_owner(saved):
        raise ValueError('STARGIFT_ON_BLOCKCHAIN')
    amount = int(saved.get('transfer_stars') or 0) if saved else 0
    if amount > 0 and not config.INFINITE_STARS:
        balance = storage.get_stars_balance(viewer_id)
        if balance < amount:
            raise ValueError('BALANCE_TOO_LOW')
        storage.adjust_stars_balance(
            viewer_id, -amount, peer_user_id=to_id, title='Gift transfer')
    elif amount > 0:
        storage.adjust_stars_balance(
            viewer_id, -amount, peer_user_id=to_id, title='Gift transfer')
    return storage.transfer_saved_star_gift(
        viewer_id, int(ref['msg_id']), to_id)


def _pack_gift_action_updates(storage: Storage, viewer: dict,
                              msg: dict, pts: int, users: List[dict],
                              *, peer_id: int,
                              extra_updates: Optional[List[bytes]] = None,
                              chats: Optional[List[dict]] = None
                              ) -> bytes:
    from handlers.rpc import _enrich_service_push
    msg = _enrich_service_push(storage, msg)
    updates_list = list(extra_updates or [])
    if packers.is_channel_id(peer_id):
        from handlers.groups import pack_update_new_channel_message
        updates_list.append(
            pack_update_new_channel_message(msg, int(pts), storage))
    else:
        updates_list.extend(packers.pack_message_delivery_update_bytes(
            msg, pts, storage, dialog_peer_id=peer_id))
    return packers.pack_updates(
        updates_list, users, user_id=viewer['id'],
        storage=storage, increment_seq=False, chats=chats or [])


def _pack_upgrade_result_updates(storage: Storage, viewer: dict,
                                 result: dict) -> bytes:
    saved = result['saved']
    svc_msg = result['service_msg']
    action_json = svc_msg['action_json']
    if isinstance(action_json, str):
        action = json.loads(action_json)
    else:
        action = dict(action_json)
    action['saved_row'] = saved
    svc_msg = dict(svc_msg, action_json=action)

    from handlers.rpc import _enrich_service_push
    svc_msg = _enrich_service_push(storage, svc_msg)

    users = [viewer]
    from_u = storage.get_user_by_id(int(saved.get('from_user_id') or 0))
    if from_u and from_u['id'] != viewer['id']:
        users.append(from_u)

    updates_list = [packers.pack_user_stars_balance(storage, viewer['id'])]
    updates_list.extend(packers.pack_message_delivery_update_bytes(
        svc_msg, result['pts'], storage, dialog_peer_id=viewer['id']))
    for chat_msg in result.get('gift_chat_updates') or []:
        if int(chat_msg['owner_id']) == int(viewer['id']):
            updates_list.append(packers.pack_update_edit_message(
                chat_msg, int(chat_msg['pts']), storage, pts_count=1))
    return packers.pack_updates(
        updates_list,
        users,
        user_id=viewer['id'],
        storage=storage,
        increment_seq=False,
    )


def _push_after_upgrade(storage: Storage, push_queue, session, viewer: dict,
                        result: dict) -> None:
    """Notify upgrader's other devices and both chat peers about the upgrade."""
    if push_queue is None or session is None:
        return
    saved = result['saved']
    svc_msg = dict(result['service_msg'])
    action_json = svc_msg.get('action_json')
    if isinstance(action_json, str):
        action = json.loads(action_json)
    elif isinstance(action_json, dict):
        action = dict(action_json)
    else:
        action = {}
    action['saved_row'] = saved
    svc_msg['action_json'] = action
    from handlers.rpc import _enrich_service_push
    svc_msg = _enrich_service_push(storage, svc_msg)

    users = [viewer]
    from_u = storage.get_user_by_id(int(saved.get('from_user_id') or 0))
    if from_u and from_u['id'] != viewer['id']:
        users.append(from_u)
    upd = packers.push_factory(
        packers.pack_inbox_push_updates, svc_msg, users,
        int(result['pts']), storage)
    push_queue.push_to_user(
        viewer['id'], upd, exclude_auth_key_id=session.auth_key_id)

    for chat_msg in result.get('gift_chat_updates') or []:
        owner_id = int(chat_msg['owner_id'])
        peer_id = int(chat_msg['from_id'])
        if chat_msg['owner_id'] == chat_msg['from_id']:
            peer_id = int(chat_msg['peer_id'])
        chat_users = [storage.get_user_by_id(owner_id)]
        peer_u = storage.get_user_by_id(peer_id)
        if peer_u:
            chat_users.append(peer_u)
        chat_users = [u for u in chat_users if u]
        edit_upd = packers.push_factory(
            packers.pack_edit_message_push,
            chat_msg, int(chat_msg['pts']), chat_users, storage)
        exclude = session.auth_key_id if owner_id == viewer['id'] else None
        push_queue.push_to_user(owner_id, edit_upd, exclude_auth_key_id=exclude)


def _pack_invoice_stars(amount: int) -> bytes:
    pw = tl.Writer()
    pw.u32(LABELED_PRICE)
    pw.string('')
    pw.i64(amount)
    price = pw.bytes_out()
    w = tl.Writer()
    w.u32(INVOICE)
    w.i32(0)
    w.string('XTR')
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(price)
    return w.bytes_out()


def _pack_invoice_ton(nanos: int) -> bytes:
    pw = tl.Writer()
    pw.u32(LABELED_PRICE)
    pw.string('')
    pw.i64(int(nanos))
    price = pw.bytes_out()
    w = tl.Writer()
    w.u32(INVOICE)
    w.i32(0)
    w.string('TON')
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(price)
    return w.bytes_out()


_PENDING_FORMS: dict = {}


def _store_payment_form(invoice: dict) -> int:
    form_id = random.randint(1, (1 << 63) - 1)
    while form_id in _PENDING_FORMS:
        form_id = random.randint(1, (1 << 63) - 1)
    _PENDING_FORMS[form_id] = dict(invoice)
    return form_id


def _pack_payment_form_star_gift(form_id: int, amount: int, *,
                                 ton: bool = False) -> bytes:
    w = tl.Writer()
    w.u32(PAYMENTS_PAYMENT_FORM_STAR_GIFT)
    w.i64(form_id)
    w.raw(_pack_invoice_ton(amount) if ton else _pack_invoice_stars(amount))
    return w.bytes_out()


def _skip_theme_params(r: tl.Reader, flags: int) -> None:
    if flags & 1:
        if r.u32() != tl.DATA_JSON:
            raise ValueError('expected DataJSON theme_params')
        r.string()


def handle_payments_get_payment_form(session, req_msg_id, r, storage,
                                   push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    try:
        invoice = parse_input_invoice(r, self_user_id=viewer['id'])
    except ValueError:
        return _rpc_error(400, 'INVOICE_INVALID')
    try:
        _skip_theme_params(r, flags)
    except ValueError:
        return _rpc_error(400, 'THEME_PARAMS_INVALID')

    if invoice.get('type') == 'transfer':
        try:
            amount = _transfer_payment_amount(storage, invoice, viewer['id'])
        except ValueError as exc:
            return _rpc_error(400, str(exc))
        balance = storage.get_stars_balance(viewer['id'])
        if amount > 0 and balance < amount:
            return _rpc_error(400, 'BALANCE_TOO_LOW')
        form_id = _store_payment_form(invoice)
        return _pack_payment_form_star_gift(form_id, amount)

    if invoice.get('type') == 'upgrade':
        try:
            amount = _upgrade_payment_amount(storage, invoice, viewer['id'])
        except ValueError as exc:
            return _rpc_error(400, str(exc))
        balance = storage.get_stars_balance(viewer['id'])
        if amount > 0 and not config.INFINITE_STARS and balance < amount:
            return _rpc_error(400, 'BALANCE_TOO_LOW')
        form_id = _store_payment_form(invoice)
        return _pack_payment_form_star_gift(form_id, amount)

    if invoice.get('type') == 'resale':
        saved = storage.get_saved_star_gift_by_slug(invoice.get('slug') or '')
        if not saved:
            return _rpc_error(400, 'STARGIFT_NOT_FOUND')
        if int(saved.get('burned') or 0):
            return _rpc_error(400, 'STARGIFT_ALREADY_BURNED')
        is_ton = bool(invoice.get('ton'))
        if is_ton and not config.can_use_ton_payments(viewer['id']):
            return _rpc_error(400, 'TON_PAYMENT_NOT_ALLOWED')
        has_stars = int(saved.get('resell_stars') or 0) > 0
        ton_price = storage.resell_ton_nanos(saved)
        if is_ton and ton_price <= 0:
            return _rpc_error(400, 'STARGIFT_NOT_FOUND')
        if not is_ton and not has_stars:
            return _rpc_error(400, 'STARGIFT_NOT_FOUND')
        peer_id = invoice.get('peer_id')
        if not peer_id:
            return _rpc_error(400, 'PEER_ID_INVALID')
        if not storage.get_user_by_id(peer_id):
            return _rpc_error(400, 'PEER_ID_INVALID')
        amount = int(saved['resell_stars'])
        if is_ton:
            balance = storage.get_ton_balance(viewer['id'])
            if balance < ton_price:
                return _rpc_error(400, 'BALANCE_TOO_LOW')
        else:
            balance = storage.get_stars_balance(viewer['id'])
            if balance < amount:
                return _rpc_error(400, 'BALANCE_TOO_LOW')
        form_id = _store_payment_form(invoice)
        if is_ton:
            return _pack_payment_form_star_gift(form_id, ton_price, ton=True)
        return _pack_payment_form_star_gift(form_id, amount)

    if invoice.get('type') == 'auction_bid':
        gift_id = int(invoice.get('gift_id') or 0)
        gift = storage.get_star_gift(gift_id)
        if not gift or not gift.get('is_auction'):
            return _rpc_error(400, 'STARGIFT_INVALID')
        timing = storage._auction_timing(gift)
        if timing['finished']:
            return _rpc_error(400, 'AUCTION_FINISHED')
        bid_amount = int(invoice.get('bid_amount') or 0)
        existing = storage.get_auction_bid(gift_id, viewer['id'])
        if existing and not int(existing.get('returned') or 0):
            if not invoice.get('update_bid'):
                return _rpc_error(400, 'BID_ALREADY_PLACED')
            charge = bid_amount - int(existing['bid_amount'])
            if charge <= 0:
                return _rpc_error(400, 'BID_TOO_LOW')
        else:
            if invoice.get('update_bid'):
                return _rpc_error(400, 'BID_NOT_FOUND')
            peer_id = invoice.get('peer_id')
            if not peer_id:
                return _rpc_error(400, 'PEER_ID_INVALID')
            if not storage.get_user_by_id(peer_id):
                return _rpc_error(400, 'PEER_ID_INVALID')
            min_bid = int(gift.get('auction_min_bid') or gift.get('stars') or 1)
            if bid_amount < min_bid:
                return _rpc_error(400, 'BID_TOO_LOW')
            charge = bid_amount
        balance = storage.get_stars_balance(viewer['id'])
        if not config.INFINITE_STARS and balance < charge:
            return _rpc_error(400, 'BALANCE_TOO_LOW')
        form_id = _store_payment_form(invoice)
        return _pack_payment_form_star_gift(form_id, charge)

    peer_id = invoice.get('peer_id')
    gift_id = int(invoice.get('gift_id') or 0)
    if not peer_id or not gift_id:
        return _rpc_error(400, 'PEER_ID_INVALID')

    gift = storage.get_star_gift(gift_id)
    if not gift:
        return _rpc_error(400, 'STARGIFT_INVALID')

    recipient = storage.get_user_by_id(peer_id)
    channel = None
    if not recipient:
        if packers.is_channel_id(peer_id):
            channel = storage.get_channel(peer_id)
            if not channel or int(channel.get('monoforum') or 0):
                return _rpc_error(400, 'PEER_ID_INVALID')
            username = (channel.get('username') or '').strip()
            is_member = storage.is_channel_member(peer_id, viewer['id'])
            is_creator = int(channel.get('creator_id') or 0) == int(viewer['id'])
            if not username and not is_member and not is_creator:
                return _rpc_error(400, 'CHANNEL_PRIVATE')
        else:
            return _rpc_error(400, 'PEER_ID_INVALID')

    send_error = storage.get_star_gift_send_error(
        viewer['id'], gift,
        recipient_id=None if channel is not None else peer_id)
    if send_error:
        return _rpc_error(400, send_error)

    amount = int(gift['stars'])
    if invoice.get('include_upgrade'):
        if not storage.gift_can_upgrade(gift_id):
            return _rpc_error(400, 'STARGIFT_NOT_UPGRADABLE')
        amount += storage.get_gift_upgrade_stars(gift_id)
    pay_ton = bool(invoice.get('pay_ton'))
    if pay_ton:
        if not config.can_use_ton_payments(viewer['id']):
            return _rpc_error(400, 'TON_PAYMENT_NOT_ALLOWED')
        ton_amount = config.stars_to_nanoton(amount)
        balance = storage.get_ton_balance(viewer['id'])
        if balance < ton_amount:
            return _rpc_error(400, 'BALANCE_TOO_LOW')
    else:
        balance = storage.get_stars_balance(viewer['id'])
        if not config.INFINITE_STARS and balance < amount:
            return _rpc_error(400, 'BALANCE_TOO_LOW')

    form_id = _store_payment_form(invoice)
    if pay_ton:
        return _pack_payment_form_star_gift(form_id, ton_amount, ton=True)
    return _pack_payment_form_star_gift(form_id, amount)


def handle_payments_send_stars_form(session, req_msg_id, r, storage,
                                  push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    form_id = r.i64()
    try:
        invoice = parse_input_invoice(r, self_user_id=viewer['id'])
    except ValueError:
        return _rpc_error(400, 'INVOICE_INVALID')

    pending = _PENDING_FORMS.pop(form_id, None)
    if pending:
        invoice = pending

    if invoice.get('type') == 'transfer':
        try:
            result = _perform_transfer(storage, viewer['id'], invoice)
        except ValueError as exc:
            return _rpc_error(400, str(exc))
        recipient = storage.get_user_by_id(int(invoice['peer_id']))
        users = [viewer]
        if recipient:
            users.append(recipient)
        if push_queue is not None and recipient and recipient['id'] != viewer['id']:
            from handlers.rpc import _push_incoming
            _push_incoming(
                storage, push_queue, recipient['id'],
                result['recipient_msg'], users,
                result['pts_recipient'], session,
                saved_row=result.get('saved'))
        updates = _pack_gift_action_updates(
            storage, viewer, result['owner_msg'], result['pts_owner'],
            users, peer_id=int(invoice['peer_id']))
        w = tl.Writer()
        w.u32(PAYMENTS_PAYMENT_RESULT)
        w.raw(updates)
        return w.bytes_out()

    if invoice.get('type') == 'upgrade':
        try:
            result = _perform_upgrade(storage, viewer['id'], invoice)
        except ValueError as exc:
            code = str(exc)
            return _rpc_error(400, code)
        _push_after_upgrade(storage, push_queue, session, viewer, result)
        updates = _pack_upgrade_result_updates(storage, viewer, result)
        w = tl.Writer()
        w.u32(PAYMENTS_PAYMENT_RESULT)
        w.raw(updates)
        return w.bytes_out()

    if invoice.get('type') == 'resale':
        try:
            result = storage.purchase_resale_gift(
                viewer['id'],
                invoice.get('slug') or '',
                int(invoice.get('peer_id') or 0),
                ton=bool(invoice.get('ton')),
            )
        except ValueError as exc:
            return _rpc_error(400, str(exc))

        recipient = storage.get_user_by_id(int(invoice['peer_id']))
        seller = storage.get_user_by_id(int(result['seller_id']))
        users = [viewer]
        if recipient and recipient['id'] != viewer['id']:
            users.append(recipient)
        if seller and seller['id'] not in {u['id'] for u in users}:
            users.append(seller)

        if push_queue is not None and recipient and recipient['id'] != viewer['id']:
            from handlers.rpc import _push_incoming
            _push_incoming(
                storage, push_queue, recipient['id'],
                result['recipient_msg'], users,
                result['pts_recipient'], session,
                saved_row=result.get('saved'))

        updates = _pack_gift_action_updates(
            storage, viewer, result['buyer_msg'], result['pts_buyer'],
            users, peer_id=int(invoice['peer_id']),
            extra_updates=_payment_balance_updates(
                storage, viewer['id'], ton=bool(invoice.get('ton'))))
        w = tl.Writer()
        w.u32(PAYMENTS_PAYMENT_RESULT)
        w.raw(updates)
        return w.bytes_out()

    if invoice.get('type') == 'auction_bid':
        gift_id = int(invoice['gift_id'])
        try:
            runtime = storage.place_auction_bid(
                viewer['id'],
                gift_id,
                peer_id=int(
                    invoice.get('peer_id')
                    or (storage.get_auction_bid(
                        gift_id, viewer['id']) or {}
                    ).get('peer_id')
                    or 0),
                bid_amount=int(invoice['bid_amount']),
                hide_name=bool(invoice.get('hide_name')),
                update_bid=bool(invoice.get('update_bid')),
                message_text=invoice.get('message_text') or '',
                message_entities=invoice.get('message_entities') or [],
            )
        except ValueError as exc:
            return _rpc_error(400, str(exc))
        notify_auction_bid_placed(
            storage, push_queue, gift_id, viewer['id'],
            runtime=runtime,
            exclude_auth_key_id=(
                session.auth_key_id if session is not None else None))
        bid_updates = [packers.pack_user_stars_balance(storage, viewer['id'])]
        if runtime:
            bid_updates.append(
                _pack_update_star_gift_auction_state(gift_id, runtime))
            bid_updates.append(
                _pack_update_star_gift_auction_user_state(
                    gift_id, viewer['id'], storage))
        updates = packers.pack_updates(
            bid_updates,
            [viewer],
            user_id=viewer['id'],
            storage=storage,
            increment_seq=False,
        )
        w = tl.Writer()
        w.u32(PAYMENTS_PAYMENT_RESULT)
        w.raw(updates)
        return w.bytes_out()

    peer_id = invoice.get('peer_id')
    gift_id = int(invoice.get('gift_id') or 0)
    if not peer_id or not gift_id:
        return _rpc_error(400, 'PEER_ID_INVALID')

    gift = storage.get_star_gift(gift_id)

    try:
        result = storage.send_star_gift(
            viewer['id'], peer_id, gift_id,
            message_text=invoice.get('message_text') or '',
            message_entities=invoice.get('message_entities') or [],
            hide_name=bool(invoice.get('hide_name')),
            include_upgrade=bool(invoice.get('include_upgrade')),
            pay_ton=bool(invoice.get('pay_ton')),
        )
    except ValueError as exc:
        code = str(exc)
        if code == 'BALANCE_TOO_LOW':
            return _rpc_error(400, code)
        return _rpc_error(400, code)

    sender = viewer
    recipient = storage.get_user_by_id(peer_id)
    channel = None
    users = [sender]
    chats = []
    if packers.is_channel_id(peer_id):
        channel = storage.get_channel(peer_id)
        if channel:
            chats.append(channel)
    elif recipient:
        users.append(recipient)

    sender_msg = result['sender_msg']
    recipient_msg = result.get('recipient_msg')

    if push_queue is not None:
        from handlers.rpc import _push_incoming
        if channel and result.get('channel_gift_silent'):
            # No wall post / member fan-out — gift lives on channel profile only.
            pass
        elif channel:
            # Legacy path: channel pts only (no common pts / seq bumps).
            from handlers.groups import _push_channel_message
            pts_channel = int(result.get('pts_channel') or 0)
            for mid, member_msg in (result.get('member_msgs') or {}).items():
                if int(mid) == int(viewer['id']):
                    continue
                try:
                    _push_channel_message(
                        storage, push_queue, session, int(mid),
                        member_msg, pts_channel, int(peer_id))
                except Exception:
                    log.exception('channel gift push failed for %s', mid)
        elif recipient and recipient['id'] != viewer['id']:
            _push_incoming(
                storage, push_queue, peer_id, recipient_msg, users,
                result['pts_recipient'], session,
                saved_row=result.get('saved'))

    if channel and result.get('channel_gift_silent'):
        # Complete the payment without a public channel service message.
        updates = packers.pack_updates(
            _payment_balance_updates(
                storage, viewer['id'], ton=bool(invoice.get('pay_ton'))),
            users, user_id=viewer['id'],
            storage=storage, increment_seq=False, chats=chats)
    elif channel and sender_msg:
        from handlers.groups import (
            pack_update_new_channel_message,
            pack_update_read_channel_outbox,
        )
        from handlers.rpc import _enrich_service_push
        sender_msg = _enrich_service_push(storage, sender_msg)
        pts_channel = int(result.get('pts_channel') or 0)
        updates_list = list(_payment_balance_updates(
            storage, viewer['id'], ton=bool(invoice.get('pay_ton'))))
        updates_list.extend([
            pack_update_new_channel_message(sender_msg, pts_channel, storage),
            pack_update_read_channel_outbox(int(peer_id), int(sender_msg['id'])),
        ])
        updates = packers.pack_updates(
            updates_list, users, user_id=viewer['id'],
            storage=storage, increment_seq=False, chats=chats)
    else:
        updates = _pack_gift_action_updates(
            storage, viewer, sender_msg,
            result['pts_sender'],
            users, peer_id=peer_id,
            extra_updates=_payment_balance_updates(
                storage, viewer['id'], ton=bool(invoice.get('pay_ton'))),
            chats=chats)

    w = tl.Writer()
    w.u32(PAYMENTS_PAYMENT_RESULT)
    w.raw(updates)
    return w.bytes_out()


def handle_payments_get_unique_star_gift(session, req_msg_id, r, storage,
                                         push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    slug = r.string()
    saved = storage.get_saved_star_gift_by_slug(slug)
    if not saved:
        return _rpc_error(400, 'STARGIFT_NOT_FOUND')
    if int(saved.get('burned') or 0):
        return _rpc_error(400, 'STARGIFT_ALREADY_BURNED')
    owner_id = int(saved.get('owner_user_id') or 0)
    if (int(saved.get('unsaved') or 0)
            and not _viewer_owns_or_manages_gift(storage, viewer['id'], saved)):
        return _rpc_error(400, 'STARGIFT_NOT_FOUND')
    gift_meta = _resolve_gift_meta(storage, saved)

    if not _viewer_owns_or_manages_gift(storage, viewer['id'], saved):
        _prepare_stars_user(storage, viewer['id'])
        if push_queue is not None:
            _push_stars_balance_refresh(
                storage, push_queue, viewer['id'],
                exclude_auth_key_id=getattr(session, 'auth_key_id', None))

    owner = storage.get_user_by_id(owner_id)
    users = []
    chats = []
    if owner:
        users.append(owner)
    elif packers.is_channel_id(owner_id):
        ch = storage.get_channel(owner_id)
        if ch:
            chats.append(ch)
    if (_saved_gift_details_visible(
            saved, viewer['id'], owner_id, storage=storage)
            and saved.get('from_user_id')):
        sender = storage.get_user_by_id(int(saved['from_user_id']))
        if sender and (not owner or sender['id'] != owner['id']):
            users.append(sender)
    w = tl.Writer()
    w.u32(PAYMENTS_UNIQUE_STAR_GIFT)
    w.raw(pack_star_gift_unique(
        saved, gift_meta, storage,
        viewer_id=viewer['id'], profile_owner_id=owner_id))
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(packers.pack_channel(ch, viewer['id'], storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(
            u, storage=storage, viewer_id=viewer['id'],
            is_self=int(u['id']) == int(viewer['id'])))
    return w.bytes_out()


def _pack_unique_star_gift_value_info(info: dict) -> bytes:
    flags = 0
    has_last_sale = (info.get('last_sale_date') is not None
                     and info.get('last_sale_price') is not None)
    if has_last_sale:
        flags |= 1 << 0
    if info.get('last_sale_on_fragment'):
        flags |= 1 << 1
    if info.get('floor_price') is not None:
        flags |= 1 << 2
    if info.get('average_price') is not None:
        flags |= 1 << 3
    if info.get('listed_count') is not None:
        flags |= 1 << 4
    has_fragment = (info.get('fragment_listed_count') is not None
                    and info.get('fragment_listed_url'))
    if has_fragment:
        flags |= 1 << 5
    if info.get('value_is_average'):
        flags |= 1 << 6

    w = tl.Writer()
    w.u32(PAYMENTS_UNIQUE_STAR_GIFT_VALUE_INFO)
    w.i32(flags)
    w.string(str(info['currency']))
    w.i64(int(info['value']))
    w.i32(int(info.get('initial_sale_date') or 0))
    w.i64(int(info.get('initial_sale_stars') or 0))
    w.i64(int(info.get('initial_sale_price') or 0))
    if has_last_sale:
        w.i32(int(info['last_sale_date']))
        w.i64(int(info['last_sale_price']))
    if flags & (1 << 2):
        w.i64(int(info['floor_price']))
    if flags & (1 << 3):
        w.i64(int(info['average_price']))
    if flags & (1 << 4):
        w.i32(int(info['listed_count']))
    if has_fragment:
        w.i32(int(info['fragment_listed_count']))
        w.string(str(info['fragment_listed_url']))
    return w.bytes_out()


def handle_payments_get_unique_star_gift_value_info(
        session, req_msg_id, r, storage, push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    slug = r.string()
    saved = storage.find_saved_star_gift_by_slug(slug)
    if not saved:
        return _rpc_error(400, 'STARGIFT_SLUG_INVALID')
    if (int(saved.get('unsaved') or 0)
            and not _viewer_owns_or_manages_gift(
                storage, viewer['id'], saved)):
        return _rpc_error(400, 'STARGIFT_SLUG_INVALID')
    info = storage.get_star_gift_value(int(saved['gift_id']))
    if not info:
        return _rpc_error(400, 'STARGIFT_VALUE_UNAVAILABLE')
    info = localize_star_gift_value(info, storage, int(viewer['id']))
    return _pack_unique_star_gift_value_info(info)


def _parse_star_gift_attribute_ids(r: tl.Reader) -> dict:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    models: List[int] = []
    patterns: List[int] = []
    backdrops: List[int] = []
    for _ in range(count):
        cid = r.u32()
        if cid == STAR_GIFT_ATTRIBUTE_ID_MODEL:
            models.append(int(r.i64()))
        elif cid == STAR_GIFT_ATTRIBUTE_ID_PATTERN:
            patterns.append(int(r.i64()))
        elif cid == STAR_GIFT_ATTRIBUTE_ID_BACKDROP:
            backdrops.append(int(r.i32()))
        else:
            raise ValueError('STARGIFT_ATTRIBUTE_INVALID')
    return {'models': models, 'patterns': patterns, 'backdrops': backdrops}


def _pack_star_gift_attribute_id(kind: str, *, document_id: int = 0,
                                 backdrop_id: int = 0) -> bytes:
    w = tl.Writer()
    if kind == 'model':
        w.u32(STAR_GIFT_ATTRIBUTE_ID_MODEL)
        w.i64(int(document_id))
    elif kind == 'pattern':
        w.u32(STAR_GIFT_ATTRIBUTE_ID_PATTERN)
        w.i64(int(document_id))
    elif kind == 'backdrop':
        w.u32(STAR_GIFT_ATTRIBUTE_ID_BACKDROP)
        w.i32(int(backdrop_id))
    else:
        raise ValueError(f'unknown attribute kind {kind!r}')
    return w.bytes_out()


def _pack_star_gift_attribute_counter(item: dict) -> bytes:
    w = tl.Writer()
    w.u32(STAR_GIFT_ATTRIBUTE_COUNTER)
    kind = item['kind']
    if kind in ('model', 'pattern'):
        w.raw(_pack_star_gift_attribute_id(
            kind, document_id=int(item['document_id'])))
    else:
        w.raw(_pack_star_gift_attribute_id(
            kind, backdrop_id=int(item['backdrop_id'])))
    w.i32(int(item['count']))
    return w.bytes_out()


def handle_payments_get_resale_star_gifts(session, req_msg_id, r, storage,
                                          push_queue, registry):
    from handlers.rpc import require_auth, rpc_error

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    req_attributes_hash = None
    if flags & (1 << 0):
        req_attributes_hash = r.i64()
    sort_by_price = bool(flags & (1 << 1))
    sort_by_num = bool(flags & (1 << 2))
    for_craft = bool(flags & (1 << 4))
    stars_only = bool(flags & (1 << 5))
    gift_id = int(r.i64())
    attribute_filters = None
    if flags & (1 << 3):
        try:
            attribute_filters = _parse_star_gift_attribute_ids(r)
        except ValueError as exc:
            return rpc_error(400, str(exc))
    offset = r.string()
    limit = max(1, min(int(r.i32()), 100))

    gift = storage.get_star_gift(gift_id)
    if not gift:
        return rpc_error(400, 'STARGIFT_INVALID')

    rows, total = storage.list_resale_gifts(
        gift_id, offset, limit,
        sort_by_price=sort_by_price,
        sort_by_num=sort_by_num,
        stars_only=stars_only,
        attribute_filters=attribute_filters,
        for_craft=for_craft,
    )

    users_map = {}
    packed_gifts = []
    for row in rows:
        gift_meta = storage.get_star_gift(int(row['gift_id']))
        if not gift_meta:
            continue
        try:
            packed_gifts.append(pack_star_gift_unique(
                row, gift_meta, storage, viewer_id=int(viewer['id'])))
        except KeyError:
            continue
        oid = int(row['owner_user_id'])
        if oid not in users_map:
            u = storage.get_user_by_id(oid)
            if u:
                users_map[oid] = u

    next_offset = ''
    if len(rows) >= limit and rows:
        next_offset = str(rows[-1]['saved_id'])

    attrs_hash = storage.compute_resale_attributes_hash(gift_id)
    include_attributes = bool(flags & (1 << 0))
    if include_attributes and req_attributes_hash == attrs_hash:
        include_attributes = False
    include_counters = not offset

    resp_flags = 0
    if next_offset:
        resp_flags |= 1 << 0
    if include_attributes:
        resp_flags |= 1 << 1
    if include_counters:
        resp_flags |= 1 << 2

    w = tl.Writer()
    w.u32(PAYMENTS_RESALE_STAR_GIFTS)
    w.i32(resp_flags)
    w.i32(total)
    w.u32(tl.VECTOR)
    w.i32(len(packed_gifts))
    for g in packed_gifts:
        w.raw(g)
    if resp_flags & (1 << 0):
        w.string(next_offset)
    if resp_flags & (1 << 1):
        catalog_attrs = storage.list_star_gift_upgrade_attrs(gift_id)
        w.u32(tl.VECTOR)
        w.i32(len(catalog_attrs))
        for attr in catalog_attrs:
            w.raw(_pack_star_gift_attribute(attr, storage))
        w.i64(int(attrs_hash))
    w.u32(tl.VECTOR)
    w.i32(0)
    if resp_flags & (1 << 2):
        counters = storage.compute_resale_attribute_counters(gift_id)
        w.u32(tl.VECTOR)
        w.i32(len(counters))
        for item in counters:
            w.raw(_pack_star_gift_attribute_counter(item))
    w.u32(tl.VECTOR)
    w.i32(len(users_map))
    for u in users_map.values():
        w.raw(packers.pack_user(u, storage=storage))
    return w.bytes_out()


def pack_update_star_gift_craft_fail() -> bytes:
    w = tl.Writer()
    w.u32(UPDATE_STAR_GIFT_CRAFT_FAIL)
    return w.bytes_out()


def handle_payments_get_craft_star_gifts(session, req_msg_id, r, storage,
                                         push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    gift_id = int(r.i64())
    offset = r.string()
    limit = int(r.i32())

    gift = storage.get_star_gift(gift_id)
    if not gift:
        return _rpc_error(400, 'STARGIFT_INVALID')

    rows, total, next_off = storage.list_craftable_saved_gifts(
        viewer['id'], gift_id, offset, limit)

    users_map = {}
    packed_rows = []
    for row in rows:
        try:
            packed_rows.append(pack_saved_star_gift(
                row, storage, viewer_id=viewer['id'],
                profile_owner_id=viewer['id']))
        except (KeyError, ValueError, TypeError) as exc:
            log.warning('pack_saved_star_gift craft %s failed: %s',
                        row.get('saved_id'), exc)
            continue
        if row.get('from_user_id'):
            u = storage.get_user_by_id(int(row['from_user_id']))
            if u:
                users_map[u['id']] = u

    w = tl.Writer()
    w.u32(PAYMENTS_SAVED_STAR_GIFTS)
    out_flags = 0
    if next_off:
        out_flags |= 1 << 0
    w.i32(out_flags)
    w.i32(total)
    w.u32(tl.VECTOR)
    w.i32(len(packed_rows))
    for packed in packed_rows:
        w.raw(packed)
    if next_off:
        w.string(next_off)
    w.u32(tl.VECTOR)
    w.i32(0)
    users = list(users_map.values())
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(
            u, storage=storage, viewer_id=viewer['id'],
            is_self=int(u['id']) == int(viewer['id'])))
    return w.bytes_out()


def _pack_craft_result_updates(storage: Storage, viewer: dict,
                               result: dict) -> bytes:
    if not result.get('success'):
        return packers.pack_updates(
            [pack_update_star_gift_craft_fail()],
            [viewer],
            user_id=viewer['id'],
            storage=storage,
            increment_seq=True,
        )

    saved = result['saved']
    svc_msg = result['service_msg']
    action_json = svc_msg['action_json']
    action = json.loads(action_json) if isinstance(action_json, str) else dict(action_json)
    action['saved_row'] = saved
    svc_msg = dict(svc_msg, action_json=action)

    from handlers.rpc import _enrich_service_push
    svc_msg = _enrich_service_push(storage, svc_msg)

    users = [viewer]
    from_u = storage.get_user_by_id(int(saved.get('from_user_id') or 0))
    if from_u and from_u['id'] != viewer['id']:
        users.append(from_u)

    updates_list = packers.pack_message_delivery_update_bytes(
        svc_msg, result['pts'], storage, dialog_peer_id=viewer['id'])
    return packers.pack_updates(
        updates_list,
        users,
        user_id=viewer['id'],
        storage=storage,
        increment_seq=False,
    )


def handle_payments_craft_star_gift(session, req_msg_id, r, storage,
                                    push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    assert r.u32() == tl.VECTOR
    count = r.i32()
    if count < 1 or count > 4:
        return _rpc_error(400, 'STARGIFT_INVALID')
    refs = []
    for _ in range(count):
        try:
            refs.append(parse_input_saved_star_gift(r))
        except ValueError:
            return _rpc_error(400, 'STARGIFT_INVALID')

    try:
        result = storage.craft_saved_star_gifts(viewer['id'], refs)
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    _push_craft_result(storage, push_queue, session, viewer, result)
    return _pack_craft_result_updates(storage, viewer, result)


def _push_craft_result(storage: Storage, push_queue, session,
                       viewer: dict, result: dict) -> None:
    """Feed the craft service message to the user's other devices."""
    if push_queue is None or not result.get('success'):
        return
    saved = result['saved']
    svc_msg = dict(result['service_msg'])
    action_json = svc_msg.get('action_json')
    action = json.loads(action_json) if isinstance(action_json, str) else dict(action_json or {})
    action['saved_row'] = saved
    svc_msg['action_json'] = action

    from handlers.rpc import _enrich_service_push
    svc_msg = _enrich_service_push(storage, svc_msg)

    users = [viewer]
    from_u = storage.get_user_by_id(int(saved.get('from_user_id') or 0))
    if from_u and from_u['id'] != viewer['id']:
        users.append(from_u)

    upd = packers.push_factory(
        packers.pack_inbox_push_updates,
        svc_msg, users, int(result['pts']), storage)
    push_queue.push_to_user(
        viewer['id'], upd, exclude_auth_key_id=session.auth_key_id)


def handle_payments_upgrade_star_gift(session, req_msg_id, r, storage,
                                      push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    try:
        ref = parse_input_saved_star_gift(r)
    except ValueError:
        return _rpc_error(400, 'STARGIFT_INVALID')

    invoice = {
        'type': 'upgrade_free',
        'keep_original_details': bool(flags & 1),
        'stargift': ref,
    }
    try:
        result = _perform_upgrade(storage, viewer['id'], invoice)
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    _push_after_upgrade(storage, push_queue, session, viewer, result)
    return _pack_upgrade_result_updates(storage, viewer, result)


def _pack_transfer_updates(storage: Storage, viewer: dict,
                           result: dict) -> bytes:
    users = [viewer]
    recipient = storage.get_user_by_id(
        int(result['saved']['owner_user_id']))
    if recipient and recipient['id'] != viewer['id']:
        users.append(recipient)
    owner_msg = result['owner_msg']
    action = json.loads(owner_msg['action_json'])
    action['saved_row'] = result['saved']
    owner_msg = dict(owner_msg, action_json=action)
    from handlers.rpc import _enrich_service_push
    owner_msg = _enrich_service_push(storage, owner_msg)
    peer_id = int(action.get('peer_id') or (
        recipient['id'] if recipient else 0))
    return _pack_gift_action_updates(
        storage, viewer, owner_msg, result['pts_owner'],
        users, peer_id=peer_id)


def handle_payments_toggle_star_gifts_pinned_to_top(session, req_msg_id, r,
                                                    storage, push_queue,
                                                    registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    owner_id = int(peer_id or viewer['id'])
    if packers.is_channel_id(owner_id):
        if not storage.is_channel_admin(owner_id, viewer['id']):
            return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    elif owner_id != viewer['id']:
        return _rpc_error(400, 'PEER_ID_INVALID')

    assert r.u32() == tl.VECTOR
    count = r.i32()
    pinned_ids = []
    for _ in range(count):
        ref = parse_input_saved_star_gift(r)
        if ref.get('kind') == 'user':
            row = storage.get_saved_star_gift_by_msg(owner_id, int(ref['msg_id']))
            if row:
                pinned_ids.append(int(row['saved_id']))
        elif ref.get('kind') == 'chat':
            pinned_ids.append(int(ref['saved_id']))
        elif ref.get('kind') == 'slug':
            row = storage.get_saved_star_gift_by_slug(ref['slug'])
            if row and int(row.get('owner_user_id') or 0) == owner_id:
                pinned_ids.append(int(row['saved_id']))
    storage.toggle_star_gifts_pinned(owner_id, pinned_ids)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_payments_transfer_star_gift(session, req_msg_id, r, storage,
                                       push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    ref = parse_input_saved_star_gift(r)
    to_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if ref.get('kind') != 'user' or not to_id:
        return _rpc_error(400, 'STARGIFT_INVALID')

    try:
        result = storage.transfer_saved_star_gift(
            viewer['id'], int(ref['msg_id']), to_id)
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    recipient = storage.get_user_by_id(to_id)
    users = [viewer]
    if recipient:
        users.append(recipient)
    if push_queue is not None and recipient:
        from handlers.rpc import _push_incoming
        _push_incoming(
            storage, push_queue, to_id, result['recipient_msg'], users,
            result['pts_recipient'], session,
            saved_row=result.get('saved'))

    return _pack_transfer_updates(storage, viewer, result)


def handle_payments_update_star_gift_price(session, req_msg_id, r, storage,
                                           push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    ref = parse_input_saved_star_gift(r)
    try:
        price = _parse_stars_amount(r)
    except ValueError:
        return _rpc_error(400, 'STARGIFT_INVALID')
    if ref.get('kind') != 'user':
        return _rpc_error(400, 'STARGIFT_INVALID')

    try:
        if price.get('ton'):
            if not config.can_use_ton_payments(viewer['id']):
                return _rpc_error(400, 'TON_PAYMENT_NOT_ALLOWED')
            storage.set_saved_gift_resell_price(
                viewer['id'], int(ref['msg_id']), 0, int(price['ton']),
                ton=True)
        else:
            storage.set_saved_gift_resell_price(
                viewer['id'], int(ref['msg_id']),
                int(price['stars']), int(price['nanos']))
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    viewer = storage.get_user_by_id(viewer['id'])
    w = tl.Writer()
    w.u32(tl.UPDATES)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(packers.pack_user(viewer, is_self=True, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(0)
    w.i32(int(time.time()))
    w.i32(0)
    return w.bytes_out()


def handle_payments_send_star_gift_offer(session, req_msg_id, r, storage,
                                         push_queue, registry):
    from handlers.rpc import require_auth, _push_incoming

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    owner_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    slug = r.string()
    try:
        price = _parse_stars_amount(r)
    except ValueError:
        return _rpc_error(400, 'STARGIFT_INVALID')
    duration = r.i32()
    random_id = r.i64()
    if flags & 1:
        r.i64()  # allow_paid_stars

    if not owner_id:
        return _rpc_error(400, 'PEER_ID_INVALID')

    try:
        result = storage.send_star_gift_offer(
            viewer['id'], owner_id, slug,
            price_stars=int(price.get('stars') or 0),
            price_nanos=int(price.get('nanos') or 0),
            price_ton=int(price.get('ton') or 0),
            duration=duration,
            random_id=random_id,
        )
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    owner = storage.get_user_by_id(owner_id)
    users = [viewer]
    if owner:
        users.append(owner)

    if push_queue is not None and owner and owner['id'] != viewer['id']:
        _push_incoming(
            storage, push_queue, owner_id,
            result['owner_msg'], users,
            result['pts_owner'], session,
            saved_row=result.get('saved'))

    updates = _pack_gift_action_updates(
        storage, viewer, result['buyer_msg'], result['pts_buyer'],
        users, peer_id=owner_id,
        extra_updates=_payment_balance_updates(
            storage, viewer['id'], ton=bool(price.get('ton'))))
    return updates


def handle_payments_resolve_star_gift_offer(session, req_msg_id, r, storage,
                                            push_queue, registry):
    from handlers.rpc import require_auth, _push_incoming

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    decline = bool(flags & 1)
    offer_msg_id = r.i32()

    try:
        result = storage.resolve_star_gift_offer(
            viewer['id'], offer_msg_id, decline=decline)
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    buyer = storage.get_user_by_id(int(result['buyer_id']))
    users = [viewer]
    if buyer and buyer['id'] != viewer['id']:
        users.append(buyer)

    if push_queue is not None and buyer and buyer['id'] != viewer['id']:
        _push_incoming(
            storage, push_queue, buyer['id'],
            result['buyer_msg'], users,
            result['pts_buyer'], session,
            saved_row=result.get('saved'))
        _push_ton_balance_refresh(storage, push_queue, buyer['id'])

    extra = []
    if not decline:
        extra.append(packers.pack_user_stars_balance(storage, viewer['id']))
    extra.append(packers.pack_user_ton_balance(storage, viewer['id']))

    return _pack_gift_action_updates(
        storage, viewer, result['owner_msg'], result['pts_owner'],
        users, peer_id=int(result['buyer_id']),
        extra_updates=extra or None)


def handle_account_get_collectible_emoji_statuses(session, req_msg_id, r,
                                                  storage, push_queue,
                                                  registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    _hash = r.i64()
    # Registration-safe: do not send collectible emoji statuses during startup.
    w = tl.Writer()
    w.u32(0x90c467d1)  # account.emojiStatuses
    w.i64(int(time.time()))
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_payments_get_premium_gift_code_options(session, req_msg_id, r,
                                                  storage, push_queue, registry):
    flags = r.i32()
    if flags & 1:
        packers.skip_input_peer(r)
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def _parse_input_star_gift_auction(r: tl.Reader) -> tuple[int, Optional[str]]:
    cid = r.u32()
    if cid == INPUT_STAR_GIFT_AUCTION:
        return int(r.i64()), None
    if cid == INPUT_STAR_GIFT_AUCTION_SLUG:
        return 0, r.string()
    raise ValueError(f'unsupported InputStarGiftAuction {cid:#010x}')


def _build_bid_levels(bids: List[dict], gifts_left: int,
                      min_bid: int) -> List[dict]:
    if not bids:
        now = int(time.time())
        return [{'pos': 1, 'amount': int(min_bid), 'date': now}]
    levels = []
    for i, bid in enumerate(bids[:100]):
        levels.append({
            'pos': i + 1,
            'amount': int(bid['bid_amount']),
            'date': int(bid['bid_date']),
        })
    if len(bids) > 100:
        checkpoints = list(range(200, min(gifts_left, 1000) + 1, 100))
        if gifts_left > 1000:
            checkpoints.extend(
                list(range(2000, gifts_left + 1, 1000)))
        if gifts_left not in checkpoints and gifts_left > 100:
            checkpoints.append(gifts_left)
        for cp in checkpoints:
            if cp <= len(bids):
                bid = bids[cp - 1]
                levels.append({
                    'pos': cp,
                    'amount': int(bid['bid_amount']),
                    'date': int(bid['bid_date']),
                })
    return levels


def _pack_auction_bid_level(level: dict) -> bytes:
    w = tl.Writer()
    w.u32(AUCTION_BID_LEVEL)
    w.i32(int(level['pos']))
    w.i64(int(level['amount']))
    w.i32(int(level['date']))
    return w.bytes_out()


def _pack_star_gift_auction_round(num: int, duration: int) -> bytes:
    w = tl.Writer()
    w.u32(STAR_GIFT_AUCTION_ROUND)
    w.i32(int(num))
    w.i32(int(duration))
    return w.bytes_out()


def _build_auction_rounds(timing: dict) -> List[bytes]:
    """One StarGiftAuctionRound covers all rounds with the same duration."""
    total_rounds = int(timing.get('total_rounds') or 0)
    if total_rounds <= 0:
        return []
    duration = int(timing.get('round_duration') or 1)
    return [_pack_star_gift_auction_round(1, duration)]


def _pack_star_gift_auction_state(runtime: dict) -> bytes:
    gift = runtime['gift']
    st = runtime['state']
    timing = runtime['timing']
    bids = runtime['bids']
    gifts_left = runtime['gifts_left']

    if int(st.get('finished') or 0) or timing['finished'] or gifts_left <= 0:
        w = tl.Writer()
        w.u32(STAR_GIFT_AUCTION_STATE_FINISHED)
        w.i32(0)
        w.i32(int(timing['start_date']))
        w.i32(int(timing['end_date']))
        w.i64(int(st.get('average_price') or 0))
        return w.bytes_out()

    bid_levels = _build_bid_levels(
        bids, gifts_left, int(runtime['min_bid_amount']))
    top_bidders = [int(b['user_id']) for b in bids[:3]]
    rounds = _build_auction_rounds(timing)

    w = tl.Writer()
    w.u32(STAR_GIFT_AUCTION_STATE)
    w.i32(int(st.get('version') or 1))
    w.i32(int(timing['start_date']))
    w.i32(int(timing['end_date']))
    w.i64(int(runtime['min_bid_amount']))
    w.u32(tl.VECTOR)
    w.i32(len(bid_levels))
    for lvl in bid_levels:
        w.raw(_pack_auction_bid_level(lvl))
    w.u32(tl.VECTOR)
    w.i32(len(top_bidders))
    for uid in top_bidders:
        w.i64(uid)
    w.i32(int(timing['next_round_at']))
    w.i32(int(st.get('last_gift_num') or 0))
    w.i32(int(gifts_left))
    w.i32(int(timing['current_round']))
    w.i32(int(timing['total_rounds']))
    w.u32(tl.VECTOR)
    w.i32(len(rounds))
    for rnd in rounds:
        w.raw(rnd)
    return w.bytes_out()


def _pack_star_gift_auction_user_state(bid: dict, acquired: int) -> bytes:
    flags = 0
    if int(bid.get('returned') or 0):
        flags |= 1 << 1
    elif bid.get('bid_date'):
        flags |= 1 << 0
    w = tl.Writer()
    w.u32(STAR_GIFT_AUCTION_USER_STATE)
    w.i32(flags)
    if flags & (1 << 0):
        w.i64(int(bid['bid_amount']))
        w.i32(int(bid['bid_date']))
        w.i64(int(bid['bid_amount']) + 1)
        w.raw(packers.pack_peer_user(int(bid['peer_id'])))
    w.i32(int(acquired))
    return w.bytes_out()


def _auction_user_state_for(runtime: dict, user_id: int,
                            storage: Storage) -> bytes:
    gift_id = int(runtime['gift']['gift_id'])
    bid = storage.get_auction_bid(gift_id, user_id) or {}
    acquired = storage.count_auction_acquired(gift_id, user_id)
    return _pack_star_gift_auction_user_state(bid, acquired)


def _collect_auction_users(storage: Storage, runtime: dict,
                           viewer_id: int) -> List[dict]:
    users_map = {}
    for bid in runtime.get('bids') or []:
        uid = int(bid['user_id'])
        if uid not in users_map:
            u = storage.get_user_by_id(uid)
            if u:
                users_map[uid] = u
    viewer = storage.get_user_by_id(viewer_id)
    if viewer:
        users_map[viewer_id] = viewer
    return list(users_map.values())


def _pack_auction_user(u: dict, *, viewer_id: int, storage: Storage) -> bytes:
    return packers.pack_user(
        u, is_self=int(u['id']) == int(viewer_id), storage=storage)


def _ensure_auction_open(storage: Storage, gift_id: int) -> Optional[dict]:
    gift = storage.get_star_gift(gift_id)
    if not gift or not int(gift.get('is_auction') or 0):
        return None
    return storage.get_auction_runtime(gift_id)


def _pack_update_star_gift_auction_state(gift_id: int, runtime: dict) -> bytes:
    w = tl.Writer()
    w.u32(UPDATE_STAR_GIFT_AUCTION_STATE)
    w.i64(int(gift_id))
    w.raw(_pack_star_gift_auction_state(runtime))
    return w.bytes_out()


def _pack_update_star_gift_auction_user_state(gift_id: int, user_id: int,
                                              storage: Storage) -> bytes:
    bid = storage.get_auction_bid(gift_id, user_id) or {}
    acquired = storage.count_auction_acquired(gift_id, user_id)
    w = tl.Writer()
    w.u32(UPDATE_STAR_GIFT_AUCTION_USER_STATE)
    w.i64(int(gift_id))
    w.raw(_pack_star_gift_auction_user_state(bid, acquired))
    return w.bytes_out()


def _push_auction_state_updates(storage: Storage, push_queue, gift_id: int,
                                runtime: dict, *,
                                exclude_user_id: Optional[int] = None,
                                exclude_auth_key_id: Optional[int] = None,
                                also_user_ids: Optional[set[int]] = None
                                ) -> None:
    if push_queue is None:
        return
    state_upd = _pack_update_star_gift_auction_state(gift_id, runtime)
    targets = _auction_subscribers(gift_id, storage)
    if also_user_ids:
        targets |= also_user_ids
    for uid in targets:
        if exclude_user_id is not None and uid == exclude_user_id:
            continue
        upd = packers.pack_updates(
            [state_upd],
            [],
            user_id=uid,
            storage=storage,
            increment_seq=False,
        )
        push_queue.push_to_user(
            uid, upd, exclude_auth_key_id=exclude_auth_key_id)


def _push_auction_user_state(storage: Storage, push_queue, gift_id: int,
                             user_id: int, *,
                             exclude_auth_key_id: Optional[int] = None
                             ) -> None:
    if push_queue is None:
        return
    user_upd = _pack_update_star_gift_auction_user_state(
        gift_id, user_id, storage)
    upd = packers.pack_updates(
        [user_upd],
        [],
        user_id=user_id,
        storage=storage,
        increment_seq=False,
    )
    push_queue.push_to_user(
        user_id, upd, exclude_auth_key_id=exclude_auth_key_id)


def _push_auction_win_delivery(storage: Storage, push_queue,
                               winner: dict) -> None:
    if push_queue is None:
        return
    from handlers.rpc import _enrich_service_push

    sender_id = int(winner['user_id'])
    recipient_id = int(winner['peer_id'])
    saved = winner.get('saved')
    sender = storage.get_user_by_id(sender_id)
    recipient = storage.get_user_by_id(recipient_id)
    if not sender:
        return

    sender_msg = winner.get('sender_msg')
    recipient_msg = winner.get('recipient_msg')
    if not sender_msg:
        return

    users = [sender]
    if recipient and recipient['id'] != sender['id']:
        users.append(recipient)

    def _attach_saved(msg):
        if not msg or not saved:
            return msg
        action_json = msg.get('action_json')
        if isinstance(action_json, str) and action_json:
            action = json.loads(action_json)
        elif isinstance(action_json, dict):
            action = dict(action_json)
        else:
            action = {}
        action['saved_row'] = saved
        return dict(msg, action_json=action)

    sender_msg = _enrich_service_push(storage, _attach_saved(sender_msg))
    sender_upd = packers.push_factory(
        packers.pack_inbox_push_updates,
        sender_msg, users, int(winner['pts_sender']), storage)
    push_queue.push_to_user(sender_id, sender_upd)

    _push_auction_user_state(storage, push_queue, int(saved['gift_id']),
                             sender_id)

    if recipient and recipient['id'] != sender['id'] and recipient_msg:
        recipient_msg = _enrich_service_push(
            storage, _attach_saved(recipient_msg))
        recipient_upd = packers.push_factory(
            packers.pack_inbox_push_updates,
            recipient_msg, users, int(winner['pts_recipient']), storage)
        push_queue.push_to_user(recipient_id, recipient_upd)


def notify_auction_bid_placed(storage: Storage, push_queue, gift_id: int,
                              bidder_id: int, *,
                              runtime: Optional[dict] = None,
                              exclude_auth_key_id: Optional[int] = None
                              ) -> None:
    if runtime is None:
        runtime = storage.get_auction_runtime(gift_id)
    if not runtime:
        return
    _subscribe_auction(gift_id, bidder_id)
    _push_auction_user_state(
        storage, push_queue, gift_id, bidder_id,
        exclude_auth_key_id=exclude_auth_key_id)
    _push_auction_state_updates(
        storage, push_queue, gift_id, runtime,
        exclude_auth_key_id=exclude_auth_key_id)


def process_due_auction_rounds(storage: Storage, push_queue) -> None:
    for gift_id in storage.list_active_auction_gift_ids():
        proc = storage.process_auction_rounds(gift_id)
        if not proc.get('changed'):
            continue
        runtime = storage.build_auction_runtime(gift_id, state=proc['state'])
        if not runtime:
            continue
        winner_ids = {int(w['user_id']) for w in proc.get('winners') or []}
        _push_auction_state_updates(
            storage, push_queue, gift_id, runtime,
            also_user_ids=winner_ids)
        for winner in proc.get('winners') or []:
            _push_auction_win_delivery(storage, push_queue, winner)


async def auction_round_loop(storage: Storage, registry) -> None:
    import asyncio
    import logging
    from handlers.push import PushQueue

    log = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(1)
        try:
            push_queue = PushQueue(registry, storage)
            process_due_auction_rounds(storage, push_queue)
            await push_queue.flush()
        except Exception as exc:
            log.warning('Auction round loop error: %s', exc)


def _pack_payments_star_gift_auction_state(runtime: dict, storage: Storage,
                                           viewer_id: int,
                                           *, timeout: int = 60) -> bytes:
    gift = runtime['gift']
    users = _collect_auction_users(storage, runtime, viewer_id)
    catalog_users, catalog_chats = _collect_star_gift_catalog_peers(
        [gift], storage)
    users_map = {int(u['id']): u for u in users}
    for u in catalog_users:
        users_map.setdefault(int(u['id']), u)
    users = list(users_map.values())
    w = tl.Writer()
    w.u32(PAYMENTS_STAR_GIFT_AUCTION_STATE)
    w.raw(pack_star_gift(gift, storage, for_shop=True))
    w.raw(_pack_star_gift_auction_state(runtime))
    w.raw(_auction_user_state_for(runtime, viewer_id, storage))
    w.i32(int(timeout))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(_pack_auction_user(u, viewer_id=viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(catalog_chats))
    for ch in catalog_chats:
        w.raw(packers.pack_channel(
            ch, viewer_id=viewer_id, storage=storage))
    return w.bytes_out()


def _pack_star_gift_active_auction_state(runtime: dict, storage: Storage,
                                         viewer_id: int) -> bytes:
    w = tl.Writer()
    w.u32(STAR_GIFT_ACTIVE_AUCTION_STATE)
    w.raw(pack_star_gift(runtime['gift'], storage, for_shop=True))
    w.raw(_pack_star_gift_auction_state(runtime))
    w.raw(_auction_user_state_for(runtime, viewer_id, storage))
    return w.bytes_out()


def handle_payments_get_star_gift_auction_state(session, req_msg_id, r,
                                                storage, push_queue,
                                                registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    try:
        gift_id, slug = _parse_input_star_gift_auction(r)
    except ValueError:
        return _rpc_error(400, 'STARGIFT_INVALID')
    client_version = r.i32()

    if slug:
        gift = storage.get_star_gift_by_auction_slug(slug)
    else:
        gift = storage.get_star_gift(gift_id)
    if not gift or not gift.get('is_auction'):
        return _rpc_error(400, 'STARGIFT_INVALID')

    runtime = _ensure_auction_open(storage, int(gift['gift_id']))
    if not runtime:
        return _rpc_error(400, 'STARGIFT_INVALID')
    gift = runtime['gift']
    _subscribe_auction(int(gift['gift_id']), viewer['id'])

    server_version = int(runtime['state'].get('version') or 1)
    if client_version and client_version == server_version:
        w = tl.Writer()
        w.u32(PAYMENTS_STAR_GIFT_AUCTION_STATE)
        w.raw(pack_star_gift(gift, storage, for_shop=True))
        w.u32(STAR_GIFT_AUCTION_STATE_NOT_MODIFIED)
        w.raw(_auction_user_state_for(runtime, viewer['id'], storage))
        w.i32(60)
        users = _collect_auction_users(storage, runtime, viewer['id'])
        w.u32(tl.VECTOR)
        w.i32(len(users))
        for u in users:
            w.raw(_pack_auction_user(
                u, viewer_id=viewer['id'], storage=storage))
        w.u32(tl.VECTOR)
        w.i32(0)
        return w.bytes_out()

    return _pack_payments_star_gift_auction_state(
        runtime, storage, viewer['id'])


def handle_payments_get_star_gift_active_auctions(session, req_msg_id, r,
                                                  storage, push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    req_hash = r.i64()
    local_hash = storage.compute_active_auctions_hash(viewer['id'])
    if req_hash and req_hash == local_hash:
        w = tl.Writer()
        w.u32(PAYMENTS_STAR_GIFT_ACTIVE_AUCTIONS_NOT_MODIFIED)
        return w.bytes_out()

    auctions = storage.list_user_active_auctions(viewer['id'])
    packed = []
    users_map = {}
    viewer_u = storage.get_user_by_id(viewer['id'])
    if viewer_u:
        users_map[viewer['id']] = viewer_u
    for runtime in auctions:
        packed.append(_pack_star_gift_active_auction_state(
            runtime, storage, viewer['id']))
        for u in _collect_auction_users(storage, runtime, viewer['id']):
            users_map[u['id']] = u

    w = tl.Writer()
    w.u32(PAYMENTS_STAR_GIFT_ACTIVE_AUCTIONS)
    w.u32(tl.VECTOR)
    w.i32(len(packed))
    for p in packed:
        w.raw(p)
    users = list(users_map.values())
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(_pack_auction_user(u, viewer_id=viewer['id'], storage=storage))
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


STAR_GIFT_HANDLERS = {
    PAYMENTS_GET_STAR_GIFT_WITHDRAWAL_URL:
        handle_payments_get_star_gift_withdrawal_url,
    PAYMENTS_GET_STAR_GIFTS: handle_payments_get_star_gifts,
    PAYMENTS_GET_UNIQUE_STAR_GIFT: handle_payments_get_unique_star_gift,
    PAYMENTS_GET_UNIQUE_STAR_GIFT_VALUE_INFO:
        handle_payments_get_unique_star_gift_value_info,
    PAYMENTS_GET_RESALE_STAR_GIFTS: handle_payments_get_resale_star_gifts,
    PAYMENTS_GET_STAR_GIFT_UPGRADE_ATTRIBUTES: (
        handle_payments_get_star_gift_upgrade_attributes),
    PAYMENTS_GET_STAR_GIFT_UPGRADE_PREVIEW: (
        handle_payments_get_star_gift_upgrade_preview),
    PAYMENTS_CHECK_CAN_SEND_GIFT: handle_payments_check_can_send_gift,
    PAYMENTS_GET_STARS_STATUS: handle_payments_get_stars_status,
    PAYMENTS_GET_STARS_TRANSACTIONS: handle_payments_get_stars_transactions,
    PAYMENTS_GET_STARS_TRANSACTIONS_BY_ID: (
        handle_payments_get_stars_transactions_by_id),
    PAYMENTS_GET_STARS_SUBSCRIPTIONS: handle_payments_get_stars_subscriptions,
    PAYMENTS_GET_STARS_TOPUP_OPTIONS: handle_payments_get_stars_topup_options,
    PAYMENTS_GET_STARS_GIFT_OPTIONS: handle_payments_get_stars_gift_options,
    PAYMENTS_GET_SAVED_STAR_GIFTS: handle_payments_get_saved_star_gifts,
    PAYMENTS_GET_SAVED_STAR_GIFT: handle_payments_get_saved_star_gift,
    PAYMENTS_SAVE_STAR_GIFT: handle_payments_save_star_gift,
    PAYMENTS_CONVERT_STAR_GIFT: handle_payments_convert_star_gift,
    PAYMENTS_GET_STAR_GIFT_COLLECTIONS: handle_payments_get_star_gift_collections,
    PAYMENTS_CREATE_STAR_GIFT_COLLECTION: handle_payments_create_star_gift_collection,
    PAYMENTS_CREATE_STAR_GIFT_COLLECTION_V2: (
        handle_payments_create_star_gift_collection_v2),
    PAYMENTS_UPDATE_STAR_GIFT_COLLECTION: (
        handle_payments_update_star_gift_collection),
    PAYMENTS_DELETE_STAR_GIFT_COLLECTION: handle_payments_delete_star_gift_collection,
    PAYMENTS_DELETE_STAR_GIFT_COLLECTION_V2: (
        handle_payments_delete_star_gift_collection_v2),
    PAYMENTS_GET_PREMIUM_GIFT_CODE_OPTIONS: (
        handle_payments_get_premium_gift_code_options),
    PAYMENTS_GET_PAYMENT_FORM: handle_payments_get_payment_form,
    PAYMENTS_SEND_STARS_FORM: handle_payments_send_stars_form,
    PAYMENTS_UPGRADE_STAR_GIFT: handle_payments_upgrade_star_gift,
    PAYMENTS_GET_CRAFT_STAR_GIFTS: handle_payments_get_craft_star_gifts,
    PAYMENTS_CRAFT_STAR_GIFT: handle_payments_craft_star_gift,
    PAYMENTS_TOGGLE_STAR_GIFTS_PINNED_TO_TOP: (
        handle_payments_toggle_star_gifts_pinned_to_top),
    PAYMENTS_TRANSFER_STAR_GIFT: handle_payments_transfer_star_gift,
    PAYMENTS_UPDATE_STAR_GIFT_PRICE: handle_payments_update_star_gift_price,
    PAYMENTS_SEND_STAR_GIFT_OFFER: handle_payments_send_star_gift_offer,
    PAYMENTS_RESOLVE_STAR_GIFT_OFFER: handle_payments_resolve_star_gift_offer,
    ACCOUNT_GET_COLLECTIBLE_EMOJI_STATUSES: (
        handle_account_get_collectible_emoji_statuses),
    PAYMENTS_GET_STAR_GIFT_ACTIVE_AUCTIONS: (
        handle_payments_get_star_gift_active_auctions),
    PAYMENTS_GET_STAR_GIFT_AUCTION_STATE: (
        handle_payments_get_star_gift_auction_state),
}
