"""Sticker / emoji pack RPC handlers and TL packers."""
import json
import logging
import os
import struct
import time
import zlib
from typing import List, Optional

from mtproto import tl
from db.storage import Storage
import config

log = logging.getLogger(__name__)


def _rpc_error(code: int, message: str) -> bytes:
    w = tl.Writer()
    w.u32(tl.RPC_ERROR)
    w.i32(code)
    w.string(message)
    return w.bytes_out()

# ── TL constants (layer 186) ────────────────────────────────
DOCUMENT = 0x8fd4c4d8
DOCUMENT_ATTR_IMAGE_SIZE = 0x6c37c15c
DOCUMENT_ATTR_STICKER = 0x6319d612
INPUT_STICKER_SET_ID = 0x9de7a269
INPUT_STICKER_SET_SHORT_NAME = 0x861cc8a0
INPUT_STICKER_SET_EMPTY = 0xffb62b95
INPUT_STICKER_SET_ANIMATED_EMOJI = 0x28703c8
INPUT_STICKER_SET_ANIMATED_EMOJI_ANIMATIONS = 0xcde3739
INPUT_DOCUMENT_FILE_LOCATION = 0xbad07584
STICKER_SET = 0x2dd14edc
STICKER_PACK = 0x12b299d4
DOCUMENT_ATTR_CUSTOM_EMOJI = 0xfd149899
DOCUMENT_ATTR_FILENAME = 0x15590068
# Hex prefix of documentAttributeCustomEmoji in serialized TL (little-endian).
DOCUMENT_ATTR_CUSTOM_EMOJI_HEX = '999814fd'
INPUT_STICKER_SET_DICE = 0xe67f520e
INPUT_STICKER_SET_PREMIUM_GIFTS = 0xc88b3b02
INPUT_STICKER_SET_EMOJI_GENERIC_ANIMATIONS = 0x04c4d4ce
INPUT_STICKER_SET_EMOJI_DEFAULT_STATUSES = 0x29d0f5ee
INPUT_STICKER_SET_EMOJI_DEFAULT_TOPIC_ICONS = 0x44c1f8e9
INPUT_STICKER_SET_EMOJI_CHANNEL_DEFAULT_STATUSES = 0x49748553
INPUT_STICKER_SET_TON_GIFTS = 0x1cf671a0

SLUG_ALIASES = {
    'GiftsPremium': 'PremiumGiftStickers',
    'tg_placeholders_android': 'AnimatedEmojies',
    'tg_placeholders_ios': 'AnimatedEmojies',
    'AnimatedEmoji': 'RestrictedEmoji',
    'Custom Verification': 'CustomVerifications',
    'CustomVerification': 'CustomVerifications',
}

GIFT_SYSTEM_SLUGS = frozenset({
    'GiftsPremium',
    'PremiumGiftStickers',
    'GiftsTons',
})

GIFT_SYSTEM_SET_META = {
    'GiftsPremium': (
        config.STAR_GIFTS_DOC_SET_ID + 101,
        0x5A4F474946545031,
        'Premium Gifts',
    ),
    'PremiumGiftStickers': (
        config.STAR_GIFTS_DOC_SET_ID + 101,
        0x5A4F474946545031,
        'Premium Gifts',
    ),
    'GiftsTons': (
        config.STAR_GIFTS_DOC_SET_ID + 102,
        0x5A4F474946545432,
        'TON Gifts',
    ),
}

# Fallback when system sets were not imported yet (see tools/import_sticker_sets.py --defaults).
SLUG_FALLBACKS = {
    'GiftsTons': ('GiftsTons', 'DMJTGStarsEmoji', 'PremiumGiftStickers'),
    # StatusPack: no fallback — wrong custom-emoji sets crash Desktop (NaN/SafeRound).
    'StatusPack': ('StatusPack',),
    'AnimatedEmojies': ('AnimatedEmojies', 'CherryEmoji'),
    'EmojiAnimations': ('EmojiAnimations', 'CherryEmoji'),
    'EmojiGenericAnimations': ('EmojiGenericAnimations', 'CherryEmoji'),
    'RestrictedEmoji': ('RestrictedEmoji', 'AnimatedEmojies'),
    'CustomVerifications': ('CustomVerifications', 'MajorVerification'),
    'MajorVerification': ('MajorVerification', 'CustomVerifications'),
}

_SYSTEM_EMOJI_SLUGS = frozenset({
    'StatusPack', 'AnimatedEmojies', 'EmojiAnimations', 'EmojiGenericAnimations',
    'RestrictedEmoji', 'CustomVerifications', 'MajorVerification',
})

# Do NOT fall back StatusPack → random custom-emoji set: Desktop loads those
# TGS as emoji statuses and can crash (SafeRound/NaN) on bad animation data.
_EMOJI_STATUS_FALLBACK_SLUGS = frozenset()

DICE_SLUG_MAP = {
    '🎲': 'AnimatedDice2',
    '🎯': 'AnimatedDart',
    '🏀': 'AnimatedBasketball',
    '⚽': 'AnimatedPenalty',
    '⚽️': 'AnimatedPenalty',
    '🎰': 'SlotMachineAnimated',
    '🎳': 'AnimatedBowling',
}
MESSAGES_STICKERS = 0x30a6ec7e
MESSAGES_STICKER_SET = 0x6e153f16
MESSAGES_STICKER_SET_NOT_MODIFIED = 0xd3f924eb
MESSAGES_ALL_STICKERS = 0xcdbbcebb
MESSAGES_ALL_STICKERS_NOT_MODIFIED = 0xe86602c3
MESSAGES_FEATURED_STICKERS = 0xbe382906
MESSAGES_FEATURED_STICKERS_NOT_MODIFIED = 0xc6dc0c66
MESSAGES_SEARCH_STICKER_SETS = 0x35705b8a
MESSAGES_FOUND_STICKER_SETS = 0x8af09dd2
MESSAGES_FOUND_STICKERS = 0x82c9e290
MESSAGES_FOUND_STICKERS_NOT_MODIFIED = 0x6010c534
MESSAGES_RECENT_STICKERS = 0x88d37c56
MESSAGES_RECENT_STICKERS_NOT_MODIFIED = 0x0b17f890
STICKER_SET_COVERED = 0x6410a5d2
STICKER_SET_FULL_COVERED = 0x40d13c0e
MESSAGES_INSTALL_STICKER_SET = 0xc78fe460
MESSAGES_REORDER_STICKER_SETS = 0x78337739
STICKER_SET_INSTALL_RESULT_SUCCESS = 0x38641628
UPLOAD_FILE = 0x96a18d5
STORAGE_FILE_UNKNOWN = 0xaa963b05
HELP_PREMIUM_PROMO = 0x5334759c
PREMIUM_SUBSCRIPTION_OPTION = 0x5f2d1df2
ACCOUNT_UPDATE_EMOJI_STATUS = 0xfbd3de6b
ACCOUNT_GET_DEFAULT_EMOJI_STATUSES = 0xd6753386
ACCOUNT_EMOJI_STATUSES = 0x90c467d1
ACCOUNT_EMOJI_STATUSES_NOT_MODIFIED = 0xd08ce645
ACCOUNT_GET_RECENT_EMOJI_STATUSES = 0x0f578105
MESSAGES_EMOJI_GROUPS = 0x881fb94b
MESSAGES_EMOJI_GROUPS_NOT_MODIFIED = 0x6fb4ad87
EMOJI_GROUP = 0x7a9abda9
MESSAGES_GET_EMOJI_STATUS_GROUPS = 0x2ecd56cd
EMOJI_STATUS = 0xe7ff068a
EMOJI_STATUS_EMPTY = 0x2de11aae
INPUT_EMOJI_STATUS_COLLECTIBLE = 0x7141dbf
UPLOAD_GET_FILE = 0xbe5335be


def _i32_hash(value: int) -> int:
    """Telegram stickerSet.hash is signed int32."""
    v = int(value) & 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


def _custom_emoji_attr_flags(doc: dict, ss: Optional[dict]) -> tuple[str, bool, bool]:
    """Return (alt, free, text_color) aligned with official Telegram packs."""
    alt = doc.get('alt') or '🙂'
    text_color = bool(int((ss or {}).get('flags') or 0) & 512)
    free = False
    attrs_json = doc.get('attributes_json') or '[]'
    if DOCUMENT_ATTR_CUSTOM_EMOJI_HEX not in attrs_json:
        return alt, free, text_color
    for item in json.loads(attrs_json):
        raw = bytes.fromhex(item)
        r = tl.Reader(raw)
        if r.u32() != DOCUMENT_ATTR_CUSTOM_EMOJI:
            continue
        flags = r.i32()
        parsed_alt = r.string()
        if parsed_alt:
            alt = parsed_alt
        if flags & 2:
            text_color = True
        break
    return alt, free, text_color


def _build_custom_emoji_attrs(doc: dict, ss: Optional[dict]) -> List[str]:
    alt, free, text_color = _custom_emoji_attr_flags(doc, ss)
    set_id = int((ss or {}).get('id') or doc.get('set_id') or 0)
    set_access_hash = int((ss or {}).get('access_hash') or 0)
    attrs = [
        build_document_attr_image_size(
            int(doc.get('width') or 512),
            int(doc.get('height') or 512)).hex(),
        build_document_attr_custom_emoji(
            alt, set_id, set_access_hash,
            free=free, text_color=text_color).hex(),
    ]
    mime = (doc.get('mime_type') or '').lower()
    if 'tgsticker' in mime:
        attrs.append(build_document_attr_filename('AnimatedSticker.tgs').hex())
    return attrs


def pack_document(doc: dict, ss: Optional[dict] = None,
                  *, date_override: Optional[int] = None) -> bytes:
    attrs_json = doc.get('attributes_json') or '[]'
    is_custom_emoji = (
        (ss and int(ss.get('flags') or 0) & 128)
        or DOCUMENT_ATTR_CUSTOM_EMOJI_HEX in attrs_json
    )
    if is_custom_emoji:
        attrs = _build_custom_emoji_attrs(doc, ss)
    else:
        attrs = json.loads(attrs_json)
    doc_flags = 0
    thumbs_bytes = b''
    try:
        from handlers.media import pack_document_thumbs
        doc_flags, thumbs_bytes = pack_document_thumbs(doc)
    except Exception:
        doc_flags = 0
        thumbs_bytes = b''
    w = tl.Writer()
    w.u32(DOCUMENT)
    w.i32(doc_flags)
    w.i64(doc['id'])
    w.i64(doc['access_hash'])
    ref = doc.get('file_reference') or b'\x00' * 8
    if isinstance(ref, str):
        ref = bytes.fromhex(ref) if ref else b'\x00' * 8
    w.tl_bytes(ref)
    w.i32(int(date_override if date_override is not None
               else doc.get('date') or int(time.time())))
    w.string(doc.get('mime_type') or 'application/x-tgsticker')
    w.i64(int(doc.get('size') or 0))
    if doc_flags & 1:
        w.raw(thumbs_bytes)
    w.i32(config.DC_ID)
    w.u32(tl.VECTOR)
    w.i32(len(attrs))
    for attr in attrs:
        w.raw(bytes.fromhex(attr))
    return w.bytes_out()


def pack_sticker_set(ss: dict) -> bytes:
    flags = int(ss.get('flags') or 0)
    w = tl.Writer()
    w.u32(STICKER_SET)
    w.i32(flags)
    if flags & 1:
        w.i32(int(ss.get('installed_date') or int(time.time())))
    w.i64(ss['id'])
    w.i64(ss['access_hash'])
    w.string(ss['title'])
    w.string(ss['short_name'])
    w.i32(int(ss.get('count') or 0))
    w.i32(_i32_hash(ss.get('hash') or 0))
    return w.bytes_out()


def pack_sticker_pack(emoticon: str, doc_ids: List[int]) -> bytes:
    w = tl.Writer()
    w.u32(STICKER_PACK)
    w.string(emoticon)
    w.u32(tl.VECTOR)
    w.i32(len(doc_ids))
    for did in doc_ids:
        w.i64(did)
    return w.bytes_out()


STICKER_KEYWORD = 0xfcfeb29c


def pack_sticker_keyword(document_id: int, keywords: List[str]) -> bytes:
    """stickerKeyword#fcfeb29c document_id:long keyword:Vector<string>"""
    w = tl.Writer()
    w.u32(STICKER_KEYWORD)
    w.i64(int(document_id))
    w.u32(tl.VECTOR)
    w.i32(len(keywords))
    for kw in keywords:
        w.string(kw)
    return w.bytes_out()


def _pack_keywords_vector(docs: List[dict]) -> bytes:
    """Layer 224 messages.stickerSet requires keywords between packs and documents."""
    items: List[bytes] = []
    for doc in docs:
        alt = (doc.get('alt') or '').strip()
        if not alt:
            continue
        items.append(pack_sticker_keyword(int(doc['id']), [alt]))
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(items))
    for item in items:
        w.raw(item)
    return w.bytes_out()


def _sticker_packs_for_set(storage: Storage, set_id: int,
                           docs: List[dict]) -> List[tuple]:
    """Return sticker packs; synthesize from documents when DB packs are missing."""
    packs = storage.get_sticker_packs(set_id)
    if packs:
        return packs
    if not docs:
        return []
    out = []
    for doc in docs:
        emo = (doc.get('alt') or '').strip() or '\U0001f600'
        out.append((emo, [int(doc['id'])]))
    return out


def pack_messages_sticker_set(storage: Storage, set_id: int, *,
                              include_documents: bool = True) -> bytes:
    ss = storage.get_sticker_set_by_id(set_id)
    if not ss:
        raise KeyError(set_id)
    docs = storage.get_sticker_documents(set_id)
    packs = _sticker_packs_for_set(storage, set_id, docs)
    w = tl.Writer()
    w.u32(MESSAGES_STICKER_SET)
    w.raw(pack_sticker_set(ss))
    w.u32(tl.VECTOR)
    w.i32(len(packs))
    for emoticon, ids in packs:
        w.raw(pack_sticker_pack(emoticon, ids))
    # Layer 224: packs → keywords → documents (keywords was missing → Desktop desync)
    w.raw(_pack_keywords_vector(docs))
    w.u32(tl.VECTOR)
    if include_documents:
        w.i32(len(docs))
        for d in docs:
            w.raw(pack_document(d, ss))
    else:
        w.i32(0)
    return w.bytes_out()


def pack_empty_messages_sticker_set(
        *,
        set_id: int = 0,
        access_hash: int = 0,
        short_name: str = '',
        title: str = '') -> bytes:
    """Return an empty sticker set (MyTelegram-compatible fallback)."""
    if not set_id:
        # StickerSet.id is an identity key in Desktop. Returning every missing
        # system set as id=0 makes StatusPack, AnimatedEmojies, and animation
        # packs overwrite each other while the emoji footer is being built.
        # Keep placeholders distinct and stable across requests.
        seed = (short_name or title or 'UnavailableStickerSet').encode('utf-8')
        checksum = zlib.crc32(seed) & 0xFFFFFFFF
        set_id = 0x4D54000000000000 | checksum
        if not access_hash:
            access_hash = 0x2A47000000000000 | (checksum ^ 0xA5A5A5A5)
    w = tl.Writer()
    w.u32(MESSAGES_STICKER_SET)
    w.u32(STICKER_SET)
    w.i32(0)
    w.i64(set_id)
    w.i64(access_hash)
    w.string(title)
    w.string(short_name)
    w.i32(0)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)  # packs
    w.u32(tl.VECTOR)
    w.i32(0)  # keywords (required in layer 224)
    w.u32(tl.VECTOR)
    w.i32(0)  # documents
    return w.bytes_out()


def _gift_ref_name(ref: dict) -> str:
    if ref['kind'] == 'slug':
        return ref['slug']
    if ref['kind'] == 'short_name':
        return ref['short_name']
    return ''


def _catalog_gift_docs(storage: Storage) -> List[dict]:
    docs = []
    seen = set()
    for gift in storage.list_shop_star_gifts():
        doc_id = int(gift.get('document_id') or 0)
        if not doc_id or doc_id in seen:
            continue
        doc = storage.get_sticker_document_by_id(doc_id)
        if doc:
            docs.append(doc)
            seen.add(doc_id)
    return docs


def _virtual_gift_set_hash(docs: List[dict]) -> int:
    h = 0x47494654
    for doc in docs:
        h = (h * 31 + (int(doc['id']) & 0x7fffffff)) & 0x7fffffff
    return _i32_hash(h or len(docs))


def _doc_for_virtual_gift_set(doc: dict, ss: dict) -> dict:
    out = dict(doc)
    out['set_id'] = ss['id']
    attrs = [
        build_document_attr_image_size(
            int(doc.get('width') or 512),
            int(doc.get('height') or 512)).hex(),
        build_document_attr_sticker(
            doc.get('alt') or '\U0001f381',
            int(ss['id']),
            int(ss['access_hash'])).hex(),
    ]
    out['attributes_json'] = json.dumps(attrs)
    return out


def pack_catalog_gift_sticker_set(storage: Storage, short_name: str) -> Optional[bytes]:
    docs = _catalog_gift_docs(storage)
    if not docs:
        return None

    set_id, access_hash, title = GIFT_SYSTEM_SET_META.get(
        short_name, GIFT_SYSTEM_SET_META['GiftsPremium'])
    ss = {
        'id': set_id,
        'access_hash': access_hash,
        'short_name': short_name,
        'title': title,
        'flags': 1 | 4,
        'count': len(docs),
        'hash': _virtual_gift_set_hash(docs),
    }
    doc_ids = [int(d['id']) for d in docs]

    w = tl.Writer()
    w.u32(MESSAGES_STICKER_SET)
    w.raw(pack_sticker_set(ss))
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(pack_sticker_pack('\U0001f381', doc_ids))
    gift_docs = [_doc_for_virtual_gift_set(doc, ss) for doc in docs]
    w.raw(_pack_keywords_vector(gift_docs))
    w.u32(tl.VECTOR)
    w.i32(len(gift_docs))
    for doc in gift_docs:
        w.raw(pack_document(doc, ss))
    return w.bytes_out()


def pack_messages_all_stickers(storage: Storage, req_hash: int) -> bytes:
    sets = storage.list_sticker_sets(installed_only=True)
    cur_hash = storage.sticker_sets_hash(installed_only=True)
    if req_hash and req_hash == cur_hash:
        w = tl.Writer()
        w.u32(MESSAGES_ALL_STICKERS_NOT_MODIFIED)
        return w.bytes_out()
    w = tl.Writer()
    w.u32(MESSAGES_ALL_STICKERS)
    w.i64(cur_hash)
    w.u32(tl.VECTOR)
    w.i32(len(sets))
    for ss in sets:
        w.raw(pack_sticker_set(ss))
    return w.bytes_out()


def parse_input_sticker_set(r: tl.Reader) -> Optional[dict]:
    cid = r.u32()
    if cid == INPUT_STICKER_SET_ID:
        sid = r.i64()
        ah = r.i64()
        return {'kind': 'id', 'id': sid, 'access_hash': ah}
    if cid == INPUT_STICKER_SET_SHORT_NAME:
        return {'kind': 'short_name', 'short_name': r.string()}
    if cid == INPUT_STICKER_SET_EMPTY:
        return None
    if cid == INPUT_STICKER_SET_DICE:
        return {'kind': 'dice', 'emoticon': r.string()}
    special = {
        INPUT_STICKER_SET_ANIMATED_EMOJI: 'AnimatedEmojies',
        INPUT_STICKER_SET_ANIMATED_EMOJI_ANIMATIONS: 'EmojiAnimations',
        INPUT_STICKER_SET_PREMIUM_GIFTS: 'GiftsPremium',
        INPUT_STICKER_SET_EMOJI_GENERIC_ANIMATIONS: 'EmojiGenericAnimations',
        INPUT_STICKER_SET_EMOJI_DEFAULT_STATUSES: 'StatusPack',
        INPUT_STICKER_SET_EMOJI_DEFAULT_TOPIC_ICONS: 'Topics',
        INPUT_STICKER_SET_EMOJI_CHANNEL_DEFAULT_STATUSES: 'StatusPack',
        INPUT_STICKER_SET_TON_GIFTS: 'GiftsTons',
    }
    if cid in special:
        return {'kind': 'slug', 'slug': special[cid]}
    log.warning('Unknown InputStickerSet constructor: %#010x', cid)
    return {'kind': 'unknown'}


def _resolve_sticker_set(storage: Storage, ref: dict) -> Optional[dict]:
    def _is_blocked(ss: Optional[dict]) -> bool:
        return bool(ss and (ss.get('short_name') or '').lower() == 'airgrambanneremoji')

    if ref['kind'] == 'id':
        ss = storage.get_sticker_set_by_id(ref['id'])
        if _is_blocked(ss):
            log.warning('getStickerSet: blocked banner set by id=%s', ref.get('id'))
            return None
        return ss
    if ref['kind'] == 'short_name':
        alias = SLUG_ALIASES.get(ref['short_name'])
        if alias:
            ss = storage.get_sticker_set_by_short_name(alias)
            if _is_blocked(ss):
                log.warning('getStickerSet: blocked banner set alias=%s', alias)
                return None
            if ss:
                return ss
        ss = storage.get_sticker_set_by_short_name(ref['short_name'])
        if _is_blocked(ss):
            log.warning('getStickerSet: blocked banner set short_name=%s',
                        ref.get('short_name'))
            return None
        if ss:
            return ss
        if ref['short_name'] in _EMOJI_STATUS_FALLBACK_SLUGS:
            return storage.get_emoji_sticker_set()
        return None
    if ref['kind'] == 'slug':
        slug = SLUG_ALIASES.get(ref['slug'], ref['slug'])
        candidates = SLUG_FALLBACKS.get(slug, (slug,))
        for name in candidates:
            ss = storage.get_sticker_set_by_short_name(name)
            if _is_blocked(ss):
                log.warning('getStickerSet: blocked banner set slug=%s', name)
                return None
            if ss:
                return ss
        if ref['slug'] in _SYSTEM_EMOJI_SLUGS:
            ss = storage.get_sticker_set_by_short_name(ref['slug'])
            if _is_blocked(ss):
                log.warning('getStickerSet: blocked banner set system slug=%s',
                            ref.get('slug'))
                return None
            if ss:
                return ss
            if ref['slug'] in _EMOJI_STATUS_FALLBACK_SLUGS:
                return storage.get_emoji_sticker_set()
        return None
    if ref['kind'] == 'dice':
        emo = (ref.get('emoticon') or '').strip()
        slug = DICE_SLUG_MAP.get(emo, '')
        if not slug:
            stripped = emo.replace('\ufe0f', '')
            for known, name in DICE_SLUG_MAP.items():
                if known.replace('\ufe0f', '') == stripped:
                    slug = name
                    break
        if not slug:
            return None
        return storage.get_sticker_set_by_short_name(slug)
    if ref['kind'] == 'unknown':
        return None
    return None


def _bootstrap_sticker_ref(ref: dict) -> bool:
    """Login bootstrap for system slug packs must not ship TGS blobs.

    Dice sets are excluded: clients need documents[value] to stop the
    preview loop (documents[0]) after messageMediaDice is received.
    """
    return ref.get('kind') == 'slug'


def handle_messages_get_sticker_set(session, req_msg_id, r, storage, push_queue, registry):
    ref = parse_input_sticker_set(r)
    req_hash = r.i32()
    if not ref:
        log.debug('getStickerSet: invalid InputStickerSet')
        return _rpc_error(400, 'STICKERSET_INVALID')
    log.info('getStickerSet request ref=%r hash=%s', ref, int(req_hash))
    ss = _resolve_sticker_set(storage, ref)
    if not ss:
        gift_name = _gift_ref_name(ref)
        if gift_name in GIFT_SYSTEM_SLUGS:
            packed = pack_catalog_gift_sticker_set(storage, gift_name)
            if packed:
                log.debug('getStickerSet: virtual %s (%d docs)',
                         gift_name, len(_catalog_gift_docs(storage)))
                return packed
        log.info('getStickerSet: not found ref=%r — returning empty set', ref)
        if ref['kind'] == 'id':
            return pack_empty_messages_sticker_set(set_id=ref['id'])
        short_name = ''
        if ref['kind'] == 'short_name':
            short_name = ref['short_name']
        elif ref['kind'] == 'slug':
            short_name = ref['slug']
        return pack_empty_messages_sticker_set(short_name=short_name)
    cur_hash = _i32_hash(ss.get('hash') or 0)
    log.info('getStickerSet resolved=%s id=%s req_hash=%s cur_hash=%s',
             ss.get('short_name'), ss.get('id'), int(req_hash), int(cur_hash))
    bootstrap = _bootstrap_sticker_ref(ref)
    # Only bootstrap slug/dice requests may skip the payload — id/short_name
    # callers (emoji store, addemoji links) need documents every time.
    if req_hash and int(req_hash) == cur_hash and bootstrap:
        log.debug('getStickerSet: %s not modified (hash=%d)',
                 ss['short_name'], cur_hash)
        w = tl.Writer()
        w.u32(MESSAGES_STICKER_SET_NOT_MODIFIED)
        return w.bytes_out()
    log.debug('getStickerSet: %s (%d docs)', ss['short_name'], ss.get('count'))
    try:
        return pack_messages_sticker_set(
            storage, ss['id'], include_documents=not bootstrap)
    except Exception as exc:
        log.exception('getStickerSet pack failed for %s: %s', ss['short_name'], exc)
        return _rpc_error(400, 'STICKERSET_INVALID')


def handle_messages_get_all_stickers(session, req_msg_id, r, storage, push_queue, registry):
    req_hash = r.i64()
    return pack_messages_all_stickers(storage, int(req_hash))


def _push_emoji_catalog_refresh(session, storage, push_queue, registry) -> None:
    if push_queue is None or storage is None or session is None:
        return
    auth_key_id = int(getattr(session, 'auth_key_id', 0) or 0)
    user_id = storage.get_session_user(auth_key_id) if auth_key_id else None
    if not user_id:
        return
    try:
        from handlers.packers import pack_emoji_catalog_refresh
        push_queue.push_to_user(
            int(user_id),
            pack_emoji_catalog_refresh(storage, recipient_id=int(user_id)),
            exclude_auth_key_id=auth_key_id,
        )
    except Exception:
        log.exception('emoji catalog refresh push failed')


def handle_messages_get_emoji_stickers(session, req_msg_id, r, storage, push_queue, registry):
    req_hash = r.i64()
    sets = storage.list_emoji_sticker_sets(installed_only=True)
    cur_hash = storage.emoji_catalog_hash(featured_only=False)
    log.debug('getEmojiStickers hash=%s sets=%d catalog_v=%s',
              int(req_hash), len(sets), storage.emoji_catalog_version())
    if req_hash and int(req_hash) == int(cur_hash):
        w = tl.Writer()
        w.u32(MESSAGES_ALL_STICKERS_NOT_MODIFIED)
        return w.bytes_out()
    w = tl.Writer()
    w.u32(MESSAGES_ALL_STICKERS)
    w.i64(cur_hash)
    w.u32(tl.VECTOR)
    w.i32(len(sets))
    for ss in sets:
        w.raw(pack_sticker_set(ss))
    return w.bytes_out()


def handle_messages_get_stickers(session, req_msg_id, r, storage, push_queue, registry):
    _emoticon = r.string()
    _hash = r.i64()
    w = tl.Writer()
    w.u32(MESSAGES_STICKERS)
    w.i64(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_messages_get_featured_stickers(session, req_msg_id, r, storage, push_queue, registry):
    req_hash = r.i64()
    sets = storage.list_sticker_sets(installed_only=False)
    cur_hash = storage.sticker_sets_hash(installed_only=False)
    if req_hash and int(req_hash) == cur_hash:
        w = tl.Writer()
        w.u32(MESSAGES_FEATURED_STICKERS_NOT_MODIFIED)
        w.i32(len(sets))
        return w.bytes_out()
    w = tl.Writer()
    w.u32(MESSAGES_FEATURED_STICKERS)
    w.i32(0)  # flags
    w.i64(cur_hash)
    w.i32(len(sets))
    w.u32(tl.VECTOR)
    w.i32(len(sets))
    for ss in sets:
        w.raw(pack_sticker_set_covered(storage, ss))
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def pack_sticker_set_covered(storage: Storage, ss: dict) -> bytes:
    """stickerSetCovered#6410a5d2 set:StickerSet cover:Document"""
    docs = storage.get_sticker_documents(ss['id'])
    cover = pack_document(docs[0], ss) if docs else pack_document({
        'id': ss['id'], 'access_hash': ss['access_hash'],
        'file_reference': b'\x00' * 8, 'mime_type': 'image/webp',
        'size': 0, 'date': int(time.time()), 'attributes_json': '[]',
    })
    w = tl.Writer()
    w.u32(STICKER_SET_COVERED)
    w.raw(pack_sticker_set(ss))
    w.raw(cover)
    return w.bytes_out()


def pack_sticker_set_full_covered(storage: Storage, ss: dict) -> bytes:
    """stickerSetFullCovered#40d13c0e — required for custom emoji store previews."""
    set_id = int(ss['id'])
    docs = storage.get_sticker_documents(set_id)
    packs = _sticker_packs_for_set(storage, set_id, docs)
    w = tl.Writer()
    w.u32(STICKER_SET_FULL_COVERED)
    w.raw(pack_sticker_set(ss))
    w.u32(tl.VECTOR)
    w.i32(len(packs))
    for emoticon, ids in packs:
        w.raw(pack_sticker_pack(emoticon, ids))
    w.raw(_pack_keywords_vector(docs))
    w.u32(tl.VECTOR)
    w.i32(len(docs))
    for doc in docs:
        w.raw(pack_document(doc, ss))
    return w.bytes_out()


def pack_emoji_sticker_set_covered(storage: Storage, ss: dict) -> bytes:
    """Emoji catalog entries: full covered so clients skip getStickerSet."""
    if int(ss.get('flags') or 0) & 128:
        try:
            return pack_sticker_set_full_covered(storage, ss)
        except Exception as exc:
            log.warning('emoji full covered failed for %s: %s',
                        ss.get('short_name'), exc)
    return pack_sticker_set_covered(storage, ss)


def handle_messages_reorder_sticker_sets(session, req_msg_id, r, storage,
                                         push_queue, registry):
    _flags = r.i32()
    if r.u32() != tl.VECTOR:
        return _rpc_error(400, 'STICKERSET_INVALID')
    _count = r.i32()
    for _ in range(_count):
        r.i64()
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_messages_install_sticker_set(session, req_msg_id, r, storage,
                                        push_queue, registry):
    ref = parse_input_sticker_set(r)
    _archived = r.bool()
    if not ref:
        return _rpc_error(400, 'STICKERSET_INVALID')
    ss = _resolve_sticker_set(storage, ref)
    if not ss:
        log.debug('installStickerSet: not found ref=%r', ref)
        return _rpc_error(400, 'STICKERSET_INVALID')
    storage.mark_sticker_set_installed(ss['id'])
    log.debug('installStickerSet: %s', ss['short_name'])
    w = tl.Writer()
    w.u32(STICKER_SET_INSTALL_RESULT_SUCCESS)
    return w.bytes_out()


def handle_messages_search_sticker_sets(session, req_msg_id, r, storage,
                                        push_queue, registry):
    flags = r.i32()
    query = r.string()
    _hash = r.i64()
    emoji_search = bool(flags & 1)
    if emoji_search:
        sets = storage.search_sticker_sets(
            query, emoji_only=True, catalog_only=True)
        cur_hash = storage.emoji_catalog_hash(featured_only=True)
        pack_covered = pack_emoji_sticker_set_covered
    else:
        sets = storage.search_sticker_sets(query, emoji_only=False)
        cur_hash = storage.sticker_sets_hash(installed_only=False)
        pack_covered = pack_sticker_set_covered
    w = tl.Writer()
    w.u32(MESSAGES_FOUND_STICKER_SETS)
    w.i64(cur_hash)
    w.u32(tl.VECTOR)
    w.i32(len(sets))
    for ss in sets:
        w.raw(pack_covered(storage, ss))
    return w.bytes_out()


def handle_messages_search_emoji_sticker_sets(session, req_msg_id, r, storage,
                                              push_queue, registry):
    flags = r.i32()
    query = r.string()
    req_hash = r.i64()
    exclude_featured = bool(flags & 1)
    if exclude_featured:
        # Link-only / internal packs — never browsable outside featured tab.
        sets = []
    else:
        sets = storage.search_sticker_sets(
            query, emoji_only=True, catalog_only=True)
    cur_hash = storage.emoji_catalog_hash(featured_only=True)
    log.info('searchEmojiStickerSets q=%r exclude_featured=%s hash=%s sets=%d',
             query, exclude_featured, int(req_hash), len(sets))
    w = tl.Writer()
    w.u32(MESSAGES_FOUND_STICKER_SETS)
    w.i64(cur_hash)
    w.u32(tl.VECTOR)
    w.i32(len(sets))
    for ss in sets:
        w.raw(pack_emoji_sticker_set_covered(storage, ss))
    return w.bytes_out()


def handle_messages_search_stickers(session, req_msg_id, r, storage,
                                    push_queue, registry):
    flags = r.i32()
    emoji_only = bool(flags & 1)
    query = r.string()
    emoticon = ''
    offset = 0
    limit = 50
    req_hash = 0

    if r.remaining() >= 1:
        emoticon = r.string()
    if r.remaining() >= 8:
        if r.u32() == tl.VECTOR:
            lang_count = r.i32()
            for _ in range(lang_count):
                r.string()
        else:
            r.pos -= 4
    if r.remaining() >= 12:
        offset = r.i32()
        limit = r.i32()
        req_hash = r.i64()
    elif r.remaining() >= 8:
        req_hash = r.i64()

    if emoji_only:
        docs = storage.search_emoji_documents(
            query, emoticon, offset=offset, limit=limit or 50)
        cur_hash = storage.emoji_documents_hash()
        if req_hash and int(req_hash) == cur_hash:
            w = tl.Writer()
            w.u32(MESSAGES_FOUND_STICKERS_NOT_MODIFIED)
            return w.bytes_out()
        packed = []
        for doc_row in docs:
            ss = storage.get_sticker_set_by_id(doc_row['set_id'])
            if ss is None:
                ss = storage.get_emoji_sticker_set() or {
                    'id': int(doc_row.get('set_id') or doc_row['id']),
                    'access_hash': 0,
                    'short_name': 'AnimatedEmojies',
                    'title': 'Emoji',
                    'flags': 128,
                }
            packed.append(pack_document(doc_row, ss))
        w = tl.Writer()
        w.u32(MESSAGES_FOUND_STICKERS)
        w.i32(0)  # flags — no next_offset
        w.i64(cur_hash)
        w.u32(tl.VECTOR)
        w.i32(len(packed))
        for item in packed:
            w.raw(item)
        return w.bytes_out()

    sets = storage.search_sticker_sets(query, emoji_only=False)
    w = tl.Writer()
    w.u32(MESSAGES_FOUND_STICKER_SETS)
    w.i64(storage.sticker_sets_hash(installed_only=False))
    w.u32(tl.VECTOR)
    w.i32(len(sets))
    for ss in sets:
        w.raw(pack_sticker_set_covered(storage, ss))
    return w.bytes_out()


def handle_messages_get_featured_emoji_stickers(session, req_msg_id, r, storage,
                                              push_queue, registry):
    req_hash = r.i64()
    sets = storage.list_featured_emoji_sticker_sets()
    cur_hash = storage.emoji_catalog_hash(featured_only=True)
    log.info('getFeaturedEmojiStickers hash=%s sets=%d catalog_v=%s',
             int(req_hash), len(sets), storage.emoji_catalog_version())
    w = tl.Writer()
    w.u32(MESSAGES_FEATURED_STICKERS)
    w.i32(0)
    w.i64(cur_hash)
    w.i32(len(sets))
    w.u32(tl.VECTOR)
    w.i32(len(sets))
    for ss in sets:
        w.raw(pack_emoji_sticker_set_covered(storage, ss))
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_messages_get_recent_stickers(session, req_msg_id, r, storage, push_queue, registry):
    _flags = r.i32()  # attached:flags.0 — value is in flags, no extra bool field
    _hash = r.i64()
    w = tl.Writer()
    w.u32(MESSAGES_RECENT_STICKERS_NOT_MODIFIED)
    return w.bytes_out()


def handle_upload_get_file(session, req_msg_id, r, storage, push_queue, registry):
    from handlers import media as media_handlers
    flags = r.i32()
    _cdn = bool(flags & 2)
    cid = r.u32()
    result = media_handlers.read_upload_file_result(storage, cid, r)
    if result is None:
        log.debug('upload.getFile failed location=%#010x', cid)
        return _rpc_error(400, 'LOCATION_INVALID')
    data, file_type = result
    log.debug('upload.getFile ok location=%#010x bytes=%d', cid, len(data))
    w = tl.Writer()
    w.u32(UPLOAD_FILE)
    w.u32(file_type)
    w.i32(int(time.time()))
    w.tl_bytes(data)
    return w.bytes_out()


def _pack_premium_subscription_option(*, months: int, currency: str,
                                      amount: int, bot_url: str,
                                      current: bool = False,
                                      store_product: str = '') -> bytes:
    flags = 0
    if store_product:
        flags |= 1
    if current:
        flags |= 1 << 1
    w = tl.Writer()
    w.u32(PREMIUM_SUBSCRIPTION_OPTION)
    w.i32(flags)
    # current / can_purchase_upgrade are flags.N?true — bit only, no payload.
    w.i32(months)
    w.string(currency)
    w.i64(amount)
    w.string(bot_url)
    if flags & 1:
        w.string(store_product)
    return w.bytes_out()


def _format_premium_until(until_ts: int) -> str:
    if until_ts <= 0:
        return 'не ограничена'
    from datetime import datetime
    return datetime.fromtimestamp(int(until_ts)).strftime('%d.%m.%Y')


def _premium_promo_status_text(user: dict) -> str:
    until_ts = int(user.get('premium_until') or 0)
    until_label = _format_premium_until(until_ts)
    return (
        'Ваша подписка Telegram Premium активна.\n\n'
        f'Дата истечения подписки: {until_label}.'
    )


def handle_help_get_premium_promo(session, req_msg_id, r, storage, push_queue, registry):
    from handlers import packers as p
    status_text = 'Telegram Premium currently unavailable.'
    entities = []
    option = _pack_premium_subscription_option(
        months=1,
        currency='USD',
        # Desktop normalizes period prices and divides by the option amount.
        # Keep the trial entry non-zero so Premium sections stay visible
        # without producing NaN during post-auth UI construction.
        amount=100,
        bot_url=f'https://t.me/{config.PREMIUM_BOT_USERNAME}?start=trial',
        store_product='premium_trial',
    )
    users_packed = []

    w = tl.Writer()
    w.u32(HELP_PREMIUM_PROMO)
    w.string(status_text)
    w.raw(p.pack_entities(entities, status_text))
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(option)
    w.u32(tl.VECTOR)
    w.i32(len(users_packed))
    for packed_user in users_packed:
        w.raw(packed_user)
    return w.bytes_out()


def parse_emoji_status(r: tl.Reader) -> Optional[dict]:
    cid = r.u32()
    if cid == EMOJI_STATUS_EMPTY:
        return None
    if cid == EMOJI_STATUS:
        flags = r.i32()
        document_id = r.i64()
        until = r.i32() if flags & 1 else 0
        return {'document_id': document_id, 'until': until}
    if cid == INPUT_EMOJI_STATUS_COLLECTIBLE:
        flags = r.i32()
        collectible_id = r.i64()
        until = r.i32() if flags & 1 else 0
        return {'collectible_id': collectible_id, 'until': until}
    raise ValueError(f'unsupported EmojiStatus {cid:#010x}')


def handle_account_update_emoji_status(session, req_msg_id, r, storage, push_queue, registry):
    user_id = storage.get_session_user(session.auth_key_id)
    if not user_id:
        return _rpc_error(401, 'AUTH_KEY_UNREGISTERED')
    try:
        status = parse_emoji_status(r)
        storage.set_user_emoji_status(user_id, status)
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if push_queue is not None:
        from handlers import packers as p
        upd = p.pack_update_user_emoji_status(user_id, status, storage=storage)
        for uid in storage.get_users_who_know(user_id):
            if int(uid) >= config.CHANNEL_ID_MIN:
                continue
            push_queue.push_to_user(uid, upd)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def _pack_account_emoji_statuses(storage: Storage, *, limit: int = 100) -> list:
    """Default/recent emoji statuses — only a real StatusPack, else empty.

    Never substitute AirgramBannerEmoji / AnimatedEmojies: Desktop can crash
    decoding unsuitable TGS while building the status picker after signup.
    """
    from handlers import packers as p
    ss = storage.get_sticker_set_by_short_name('StatusPack')
    if not ss:
        return []
    statuses = []
    for doc in storage.get_sticker_documents(ss['id'])[:limit]:
        statuses.append(p.pack_emoji_status(doc['id']))
    return statuses


def _pack_account_emoji_statuses_response(storage: Storage, req_hash: int,
                                          *, limit: int = 100) -> bytes:
    statuses = _pack_account_emoji_statuses(storage, limit=limit)
    cur_hash = int(time.time()) // 3600
    if req_hash and int(req_hash) == cur_hash:
        w = tl.Writer()
        w.u32(ACCOUNT_EMOJI_STATUSES_NOT_MODIFIED)
        return w.bytes_out()
    w = tl.Writer()
    w.u32(ACCOUNT_EMOJI_STATUSES)
    w.i64(cur_hash)
    w.u32(tl.VECTOR)
    w.i32(len(statuses))
    for st in statuses:
        w.raw(st)
    return w.bytes_out()


def pack_emoji_group(title: str, icon_emoji_id: int,
                     emoticons: List[str]) -> bytes:
    w = tl.Writer()
    w.u32(EMOJI_GROUP)
    w.string(title)
    w.i64(int(icon_emoji_id))
    w.u32(tl.VECTOR)
    w.i32(len(emoticons))
    for emo in emoticons:
        w.string(emo)
    return w.bytes_out()


def _emoji_status_groups(storage: Storage) -> List[bytes]:
    ss = storage.get_emoji_sticker_set()
    if not ss:
        return []
    docs = {int(d['id']): d for d in storage.get_sticker_documents(ss['id'])}
    groups: List[bytes] = []
    seen_titles: set = set()
    for emoticon, doc_ids in storage.get_sticker_packs(ss['id']):
        title = (emoticon or '').strip() or 'Statuses'
        if title in seen_titles:
            continue
        icon_id = 0
        for doc_id in doc_ids:
            if int(doc_id) in docs:
                icon_id = int(doc_id)
                break
        if not icon_id:
            continue
        seen_titles.add(title)
        emos = [emoticon] if emoticon else []
        groups.append(pack_emoji_group(title, icon_id, emos))
    if not groups and docs:
        first_id = next(iter(docs))
        groups.append(pack_emoji_group('Statuses', first_id, []))
    return groups


def _emoji_groups_hash(groups: List[bytes]) -> int:
    h = 0
    for g in groups:
        h = (h * 31 + (zlib.crc32(g) & 0x7FFFFFFF)) & 0x7FFFFFFF
    return h


def handle_account_get_default_emoji_statuses(session, req_msg_id, r, storage,
                                              push_queue, registry):
    _hash = r.i64()
    w = tl.Writer()
    w.u32(ACCOUNT_EMOJI_STATUSES)
    w.i64(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_account_get_recent_emoji_statuses(session, req_msg_id, r, storage,
                                             push_queue, registry):
    _hash = r.i64()
    w = tl.Writer()
    w.u32(ACCOUNT_EMOJI_STATUSES)
    w.i64(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_messages_get_emoji_status_groups(session, req_msg_id, r, storage,
                                            push_queue, registry):
    req_hash = r.i32()
    groups = _emoji_status_groups(storage)
    cur_hash = _emoji_groups_hash(groups)
    if req_hash and int(req_hash) == cur_hash:
        w = tl.Writer()
        w.u32(MESSAGES_EMOJI_GROUPS_NOT_MODIFIED)
        return w.bytes_out()
    w = tl.Writer()
    w.u32(MESSAGES_EMOJI_GROUPS)
    w.i32(cur_hash)
    w.u32(tl.VECTOR)
    w.i32(len(groups))
    for g in groups:
        w.raw(g)
    return w.bytes_out()


def handle_messages_get_custom_emoji_documents(session, req_msg_id, r, storage,
                                             push_queue, registry):
    assert r.u32() == tl.VECTOR
    count = r.i32()
    doc_ids = [r.i64() for _ in range(count)]
    from handlers.stickers import pack_document
    from shop_webapp.emoji_import import ensure_custom_emoji_document
    packed = []
    for doc_id in doc_ids:
        doc_row = storage.get_sticker_document_by_id(doc_id)
        if not doc_row:
            try:
                ensure_custom_emoji_document(storage, int(doc_id))
            except Exception:
                pass
            doc_row = storage.get_sticker_document_by_id(doc_id)
        if not doc_row:
            continue
        ss = storage.get_sticker_set_by_id(doc_row['set_id'])
        if ss is None:
            ss = storage.get_sticker_set_by_short_name('RestrictedEmoji')
        if ss is None:
            ss = storage.get_emoji_sticker_set() or {
                'id': int(doc_row.get('set_id') or doc_id),
                'access_hash': 0,
                'short_name': 'AnimatedEmojies',
                'title': 'Emoji',
            }
        packed.append(pack_document(doc_row, ss))
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(packed))
    for item in packed:
        w.raw(item)
    return w.bytes_out()


STICKER_HANDLERS = {
    0xc8a0ec74: handle_messages_get_sticker_set,
    0xb8a0a1a8: handle_messages_get_all_stickers,
    0xfbfca18f: handle_messages_get_emoji_stickers,
    0xecf6736: handle_messages_get_featured_emoji_stickers,
    0xd5a5d3a1: handle_messages_get_stickers,
    0x64780b14: handle_messages_get_featured_stickers,
    0x9da9403b: handle_messages_get_recent_stickers,
    MESSAGES_INSTALL_STICKER_SET: handle_messages_install_sticker_set,
    MESSAGES_REORDER_STICKER_SETS: handle_messages_reorder_sticker_sets,
    MESSAGES_SEARCH_STICKER_SETS: handle_messages_search_sticker_sets,
    0x92b4494c: handle_messages_search_emoji_sticker_sets,
    0x29b1c66a: handle_messages_search_stickers,
    UPLOAD_GET_FILE: handle_upload_get_file,
    0xb81b93d4: handle_help_get_premium_promo,
    ACCOUNT_UPDATE_EMOJI_STATUS: handle_account_update_emoji_status,
    ACCOUNT_GET_DEFAULT_EMOJI_STATUSES: handle_account_get_default_emoji_statuses,
    ACCOUNT_GET_RECENT_EMOJI_STATUSES: handle_account_get_recent_emoji_statuses,
    MESSAGES_GET_EMOJI_STATUS_GROUPS: handle_messages_get_emoji_status_groups,
    0xd9ab0f54: handle_messages_get_custom_emoji_documents,
}


def build_document_attr_image_size(w: int, h: int) -> bytes:
    bw = tl.Writer()
    bw.u32(DOCUMENT_ATTR_IMAGE_SIZE)
    bw.i32(w)
    bw.i32(h)
    return bw.bytes_out()


def build_document_attr_custom_emoji(alt: str, set_id: int, set_access_hash: int,
                                   free: bool = False,
                                   text_color: bool = False) -> bytes:
    flags = 0
    if free:
        flags |= 1
    if text_color:
        flags |= 2
    bw = tl.Writer()
    bw.u32(DOCUMENT_ATTR_CUSTOM_EMOJI)
    bw.i32(flags)
    bw.string(alt or '🙂')
    bw.u32(INPUT_STICKER_SET_ID)
    bw.i64(set_id)
    bw.i64(set_access_hash)
    return bw.bytes_out()


def build_document_attr_filename(name: str) -> bytes:
    bw = tl.Writer()
    bw.u32(DOCUMENT_ATTR_FILENAME)
    bw.string(name or 'file')
    return bw.bytes_out()


def build_document_attr_sticker(alt: str, set_id: int, set_access_hash: int,
                                mask: bool = False) -> bytes:
    flags = 0
    if mask:
        flags |= 2
    bw = tl.Writer()
    bw.u32(DOCUMENT_ATTR_STICKER)
    bw.i32(flags)
    bw.string(alt or '🙂')
    bw.u32(INPUT_STICKER_SET_ID)
    bw.i64(set_id)
    bw.i64(set_access_hash)
    return bw.bytes_out()
