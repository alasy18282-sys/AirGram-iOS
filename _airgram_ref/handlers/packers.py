"""TL serializers for users, messages, dialogs, updates."""
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from mtproto import tl
import config

log = logging.getLogger(__name__)

SERVICE_USER_ID = config.SERVICE_USER_ID

_MENTION_RE = re.compile(r'@([a-zA-Z][a-zA-Z0-9_]{4,31})')
_TME_ADDSET_RE = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me)/'
    r'(addstickers|addemoji)/([a-zA-Z][a-zA-Z0-9_.]*)',
    re.IGNORECASE)
_TME_USERNAME_RE = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me)/'
    r'([a-zA-Z][a-zA-Z0-9_]{4,31})(\?[a-zA-Z0-9_=.-]+)?',
    re.IGNORECASE)
_BOT_COMMAND_RE = re.compile(
    r'(?<!\S)(/[A-Za-z][A-Za-z0-9_]{0,63}(?:@[A-Za-z][A-Za-z0-9_]{4,31})?)'
)
_TG_SCHEME_RE = re.compile(r'tg://[^\s<>\[\]()"\']+', re.IGNORECASE)
_HTTP_URL_RE = re.compile(r'https?://[^\s<>\[\]()"\']+', re.IGNORECASE)


def _trim_link_trailing_punct(raw: str) -> str:
    return raw.rstrip('.,!?;:)]}\'"')


def _append_entity(entities: List[dict], seen: set[tuple], text: str,
                   start: int, end: int, entity: dict) -> None:
    span = (start, end)
    if span in seen:
        return
    for s0, s1 in seen:
        if start < s1 and s0 < end:
            return
    seen.add(span)
    entities.append(entity)


def utf16_len(text: str) -> int:
    """Telegram entity offsets are measured in UTF-16 code units."""
    n = 0
    for ch in text:
        n += 2 if ord(ch) > 0xFFFF else 1
    return n


def utf16_offset(text: str, char_index: int) -> int:
    return utf16_len(text[:char_index])


def detect_text_entities(text: str) -> List[dict]:
    """Auto-detect @username, bot commands, http(s):// and tg:// links."""
    entities: List[dict] = []
    seen: set[tuple] = set()

    for m in _BOT_COMMAND_RE.finditer(text):
        _append_entity(entities, seen, text, m.start(), m.end(), {
            'type': 'bot_command',
            'offset': utf16_offset(text, m.start()),
            'length': utf16_len(m.group(1)),
        })

    for m in _MENTION_RE.finditer(text):
        _append_entity(entities, seen, text, m.start(), m.end(), {
            'type': 'mention',
            'offset': utf16_offset(text, m.start()),
            'length': utf16_len(m.group(0)),
        })

    for m in _TG_SCHEME_RE.finditer(text):
        raw = _trim_link_trailing_punct(m.group(0))
        end = m.start() + len(raw)
        _append_entity(entities, seen, text, m.start(), end, {
            'type': 'text_url',
            'offset': utf16_offset(text, m.start()),
            'length': utf16_len(raw),
            'url': raw,
        })

    for m in _TME_ADDSET_RE.finditer(text):
        kind, set_name = m.group(1).lower(), m.group(2)
        raw = _trim_link_trailing_punct(m.group(0))
        end = m.start() + len(raw)
        _append_entity(entities, seen, text, m.start(), end, {
            'type': 'text_url',
            'offset': utf16_offset(text, m.start()),
            'length': utf16_len(raw),
            'url': f'https://t.me/{kind}/{set_name}',
        })

    for m in _TME_USERNAME_RE.finditer(text):
        username = m.group(1)
        query = m.group(2) or ''
        raw = _trim_link_trailing_punct(m.group(0))
        end = m.start() + len(raw)
        _append_entity(entities, seen, text, m.start(), end, {
            'type': 'text_url',
            'offset': utf16_offset(text, m.start()),
            'length': utf16_len(raw),
            'url': f'https://t.me/{username}{query}',
        })

    for m in _HTTP_URL_RE.finditer(text):
        raw = _trim_link_trailing_punct(m.group(0))
        end = m.start() + len(raw)
        _append_entity(entities, seen, text, m.start(), end, {
            'type': 'url',
            'offset': utf16_offset(text, m.start()),
            'length': utf16_len(raw),
        })

    entities.sort(key=lambda e: e['offset'])
    return entities


def merge_entities(primary: List[dict], extra: List[dict]) -> List[dict]:
    if not extra:
        return primary
    if not primary:
        return extra

    def overlaps(a: dict, b: dict) -> bool:
        a0, a1 = a['offset'], a['offset'] + a['length']
        b0, b1 = b['offset'], b['offset'] + b['length']
        return a0 < b1 and b0 < a1

    merged = list(primary)
    for ent in extra:
        if any(overlaps(ent, cur) for cur in merged):
            continue
        merged.append(ent)
    merged.sort(key=lambda e: e['offset'])
    return merged


def pack_message_reply_header(reply_to_msg_id: int,
                              reply_to_peer_id: Optional[int] = None) -> bytes:
    flags = 1 << 4
    if reply_to_peer_id:
        flags |= 1 << 0
    w = tl.Writer()
    w.u32(tl.MESSAGE_REPLY_HEADER)
    w.i32(flags)
    w.i32(reply_to_msg_id)
    if flags & (1 << 0):
        w.raw(pack_peer_user(reply_to_peer_id))
    return w.bytes_out()


def pack_reply_markup(reply_markup: dict) -> bytes:
    if not reply_markup or reply_markup.get('type') not in ('keyboard', 'reply_keyboard'):
        return b''
    rows = reply_markup.get('rows') or []
    flags = 0
    if reply_markup.get('resize', True):
        flags |= 1 << 0
    if reply_markup.get('single_use'):
        flags |= 1 << 1
    placeholder = (reply_markup.get('placeholder') or '').strip()
    if placeholder:
        flags |= 1 << 3
    if reply_markup.get('persistent'):
        flags |= 1 << 4

    w = tl.Writer()
    w.u32(0x85dd99d1)  # replyKeyboardMarkup
    w.i32(flags)
    w.u32(tl.VECTOR)
    w.i32(len(rows))
    for row in rows:
        buttons = row if isinstance(row, list) else []
        w.u32(0x77608b83)  # keyboardButtonRow
        w.u32(tl.VECTOR)
        w.i32(len(buttons))
        for text in buttons:
            w.u32(0x7d170cff)  # keyboardButton
            w.i32(0)  # flags
            w.string(str(text))
    if placeholder:
        w.string(placeholder)
    return w.bytes_out()


def login_code_text(code: str) -> tuple[str, List[dict]]:
    prefix = 'Код для входа в Airgram: '
    suffix = (
        '. Не давайте код никому, даже если его требуют от имени Airgram!\n\n'
        '❗️Этот код используется для входа в Ваш аккаунт в Airgram. '
        'Он не может быть использован для чего-либо ещё. \n\n'
        'Если Вы не запрашивали код для входа, проигнорируйте это сообщение.'
    )
    text = prefix + code + suffix
    entities = [{
        'type': 'spoiler',
        'offset': utf16_len(prefix),
        'length': utf16_len(code),
    }]
    return text, entities


def pack_peer_user(user_id: int) -> bytes:
    w = tl.Writer()
    w.u32(tl.PEER_USER)
    w.i64(user_id)
    return w.bytes_out()


def is_channel_id(peer_id: int) -> bool:
    return int(peer_id) >= config.CHANNEL_ID_MIN


def pack_peer_channel(channel_id: int) -> bytes:
    w = tl.Writer()
    w.u32(0xa2a5371e)  # peerChannel
    w.i64(channel_id)
    return w.bytes_out()


def pack_peer(peer_id: int) -> bytes:
    if is_channel_id(peer_id):
        return pack_peer_channel(peer_id)
    return pack_peer_user(peer_id)


def pack_chat_photo_empty() -> bytes:
    w = tl.Writer()
    w.u32(0x37c1011c)  # chatPhotoEmpty
    return w.bytes_out()


def pack_photo_empty() -> bytes:
    """photoEmpty#2331b22d — used in ChatFull/UserFull, not channel.photo."""
    w = tl.Writer()
    w.u32(0x2331b22d)
    w.i64(0)
    return w.bytes_out()


def pack_chat_admin_rights_creator() -> bytes:
    """chatAdminRights#5fb224d5 — full rights for group creator."""
    return pack_chat_admin_rights(
        (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
        | (1 << 7) | (1 << 9) | (1 << 11) | (1 << 12) | (1 << 13)
        | (1 << 14) | (1 << 15) | (1 << 16) | (1 << 17) | (1 << 18))


def pack_chat_admin_rights(rights: int) -> bytes:
    w = tl.Writer()
    w.u32(0x5fb224d5)
    w.i32(int(rights))
    return w.bytes_out()


def parse_chat_admin_rights(r: tl.Reader) -> int:
    cid = r.u32()
    if cid != 0x5fb224d5:
        raise ValueError(f'unsupported ChatAdminRights {cid:#010x}')
    return r.i32()


def pack_chat_reactions_all() -> bytes:
    w = tl.Writer()
    w.u32(0x52928bca)  # chatReactionsAll layer 224
    w.i32(0)
    return w.bytes_out()


def pack_chat_banned_rights_open() -> bytes:
    """chatBannedRights — no restrictions."""
    w = tl.Writer()
    w.u32(0x9f120418)
    w.i32(0)
    w.i32(0)
    return w.bytes_out()


def pack_channel(ch: dict, viewer_id: Optional[int] = None,
                 storage=None) -> bytes:
    """channel#1c32b11c — megagroup or broadcast."""
    flags = 0
    flags2 = 0
    is_megagroup = bool(int(ch.get('megagroup') or 0))
    if is_megagroup:
        flags |= 1 << 8  # megagroup
        flags |= 1 << 18  # default_banned_rights
    else:
        flags |= 1 << 5  # broadcast
    is_creator = (
        viewer_id is not None
        and int(ch.get('creator_id') or 0) == int(viewer_id)
    )
    is_admin = is_creator
    if not is_admin and storage is not None and viewer_id is not None:
        is_admin = storage.is_channel_admin(int(ch['id']), int(viewer_id))
    is_member = True
    if storage is not None and viewer_id is not None:
        is_member = storage.is_channel_member(int(ch['id']), int(viewer_id))
    if is_creator:
        flags |= 1 << 0  # creator
    if is_admin:
        flags |= 1 << 14  # admin_rights
    if ch.get('left') or not is_member:
        flags |= 1 << 2
    username = (ch.get('username') or '').strip().lower()
    if username:
        flags |= 1 << 6
    if ch.get('invite_hash'):
        flags |= 1 << 20  # has_link
    flags |= 1 << 13  # access_hash
    flags |= 1 << 17  # participants_count
    send_paid_messages_stars = int(ch.get('send_paid_messages_stars') or 0)
    linked_monoforum_id = int(ch.get('linked_monoforum_id') or 0)
    is_monoforum = bool(int(ch.get('monoforum') or 0))
    if is_monoforum:
        flags2 |= 1 << 17
        if linked_monoforum_id:
            flags2 |= 1 << 18
        if storage is not None:
            linked = storage.get_linked_channel_for_monoforum(int(ch['id']))
            if linked:
                send_paid_messages_stars = int(
                    linked.get('send_paid_messages_stars') or send_paid_messages_stars)
        if send_paid_messages_stars > 0:
            flags2 |= 1 << 14
    else:
        if int(ch.get('broadcast_messages_allowed') or 0):
            flags2 |= 1 << 16
        if linked_monoforum_id:
            flags2 |= 1 << 18
    w = tl.Writer()
    w.u32(0x1c32b11c)
    w.i32(flags)
    w.i32(flags2)
    w.i64(int(ch['id']))
    w.i64(int(ch['access_hash']))
    w.string(ch.get('title') or 'Group')
    if flags & (1 << 6):
        w.string(username)
    w.raw(pack_chat_photo_empty())
    w.i32(int(ch.get('date') or 0))
    if flags & (1 << 14):
        w.raw(pack_chat_admin_rights_creator() if is_creator
              else pack_chat_admin_rights(
                  (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)
                  | (1 << 5) | (1 << 7) | (1 << 9) | (1 << 11) | (1 << 12)
                  | (1 << 13) | (1 << 14) | (1 << 15) | (1 << 16)))
    if flags & (1 << 18):
        w.raw(pack_chat_banned_rights_open())
    w.i32(int(ch.get('participants_count') or 1))
    if flags2 & (1 << 14):
        w.i64(send_paid_messages_stars)
    if flags2 & (1 << 18):
        w.i64(linked_monoforum_id)
    return w.bytes_out()


def pack_bot_info_empty() -> bytes:
    """Single botInfo with no optional fields."""
    w = tl.Writer()
    w.u32(0x4d8a0299)  # botInfo
    w.i32(0)
    return w.bytes_out()


def pack_channel_full(ch: dict, viewer_id: Optional[int] = None,
                      storage=None) -> bytes:
    flags = (1 << 0) | (1 << 30)  # participants_count, available_reactions
    flags2 = 1 << 19  # stargifts_available
    send_paid_stars = int(ch.get('send_paid_messages_stars') or 0)
    if int(ch.get('broadcast_messages_allowed') or 0):
        flags2 |= 1 << 20  # paid_messages_available
        if send_paid_stars > 0:
            flags2 |= 1 << 21
    is_creator = (
        viewer_id is not None
        and int(ch.get('creator_id') or 0) == int(viewer_id)
    )
    is_admin = is_creator
    if not is_admin and storage is not None and viewer_id is not None:
        is_admin = storage.is_channel_admin(int(ch['id']), int(viewer_id))
    if is_admin:
        flags |= 1 << 3  # can_view_participants
        flags |= 1 << 6  # can_set_username
    if is_creator:
        flags |= 1 << 1  # admins_count
        flags |= 1 << 20  # can_view_stats
        flags2 |= 1 << 0  # can_delete_channel
    if ch.get('invite_hash'):
        flags |= 1 << 23
    if ch.get('pinned_msg_id'):
        flags |= 1 << 5
    w = tl.Writer()
    w.u32(0xe4e0b29d)  # channelFull layer 224
    w.i32(flags)
    w.i32(flags2)
    w.i64(int(ch['id']))
    w.string(ch.get('about') or '')
    w.i32(int(ch.get('participants_count') or 1))
    if flags & (1 << 1):
        w.i32(int(ch.get('admins_count') or 1))
    w.i32(int(ch.get('read_inbox_max_id') or 0))
    w.i32(int(ch.get('read_outbox_max_id') or 0))
    w.i32(int(ch.get('unread_count') or 0))
    w.raw(pack_photo_empty())
    w.raw(pack_peer_notify_settings())
    if flags & (1 << 23):
        ih = ch['invite_hash']
        link = f'https://t.me/+{ih}'
        ew = tl.Writer()
        ew.u32(0xa22cbd96)  # chatInviteExported
        ew.i32(1 << 5)  # permanent
        ew.string(link)
        ew.i64(int(ch.get('creator_id') or 0))
        ew.i32(int(ch.get('date') or time.time()))
        w.raw(ew.bytes_out())
    w.u32(tl.VECTOR)
    w.i32(0)  # bot_info: Vector<BotInfo>
    if flags & (1 << 5):
        w.i32(int(ch['pinned_msg_id']))
    w.i32(int(ch.get('pts') or 1))
    if flags & (1 << 30):
        w.raw(pack_chat_reactions_all())
    if flags2 & (1 << 21):
        w.i64(send_paid_stars)
    return w.bytes_out()


def pack_peer_notify_settings() -> bytes:
    w = tl.Writer()
    w.u32(tl.PEER_NOTIFY_SETTINGS)
    w.i32(0)
    return w.bytes_out()


def pack_config(dc_id: int, dc_ip: str, dc_port: int) -> bytes:
    """config#cc1a241e — layer 186."""
    now = int(time.time())
    w = tl.Writer()
    w.u32(tl.CONFIG)
    w.i32(0)  # flags
    w.i32(now)
    w.i32(now + 300)
    w.bool(False)  # test_mode
    w.i32(dc_id)
    w.u32(tl.VECTOR)
    w.i32(1)
    w.u32(tl.DC_OPTION)
    w.i32(0)  # dcOption flags
    w.i32(dc_id)
    w.string(dc_ip)
    w.i32(dc_port)
    w.string('apv.stel.com')
    w.i32(200)           # chat_size_max
    w.i32(200000)        # megagroup_size_max
    w.i32(100)           # forwarded_count_max
    w.i32(210000)        # online_update_period_ms
    w.i32(5000)          # offline_blur_timeout_ms
    w.i32(30000)         # offline_idle_timeout_ms
    w.i32(300000)        # online_cloud_timeout_ms
    w.i32(30000)         # notify_cloud_delay_ms
    w.i32(1500)          # notify_default_delay_ms
    w.i32(60000)         # push_chat_period_ms
    w.i32(2)             # push_chat_limit
    w.i32(172800)        # edit_time_limit
    w.i32(172800)        # revoke_time_limit
    w.i32(172800)        # revoke_pm_time_limit
    w.i32(2419200)       # rating_e_decay
    w.i32(200)           # stickers_recent_limit
    w.i32(604800)        # channels_read_media_period
    w.i32(20000)         # call_receive_timeout_ms
    w.i32(90000)         # call_ring_timeout_ms
    w.i32(30000)         # call_connect_timeout_ms
    w.i32(10000)         # call_packet_timeout_ms
    w.string('https://t.me/')
    w.i32(1024)          # caption_length_max
    w.i32(4096)          # message_length_max
    w.i32(dc_id)         # webfile_dc_id
    return w.bytes_out()


def pack_peer_settings(show_add_contact: bool = False,
                       charge_paid_message_stars: Optional[int] = None) -> bytes:
    flags = 0
    if show_add_contact:
        flags |= (1 << 1)
    if charge_paid_message_stars is not None:
        flags |= (1 << 14)
    w = tl.Writer()
    w.u32(tl.PEER_SETTINGS)
    w.i32(flags)
    if flags & (1 << 14):
        w.i64(int(charge_paid_message_stars))
    return w.bytes_out()


def pack_entities(entities: List[dict], text: str = '') -> bytes:
    known_types = (
        'spoiler', 'bold', 'code', 'pre', 'mention', 'url', 'text_url',
        'custom_emoji', 'bot_command',
    )
    text_len = utf16_len(text) if text else None
    known = []
    for e in entities:
        if e.get('type') not in known_types:
            continue
        off = int(e.get('offset') or 0)
        ln = int(e.get('length') or 0)
        if off < 0 or ln <= 0:
            continue
        if text_len is not None and off + ln > text_len:
            continue
        known.append(e)
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(known))
    for ent in known:
        etype = ent['type']
        if etype == 'spoiler':
            w.u32(tl.MESSAGE_ENTITY_SPOILER)
        elif etype == 'bold':
            w.u32(tl.MESSAGE_ENTITY_BOLD)
        elif etype == 'code':
            w.u32(tl.MESSAGE_ENTITY_CODE)
        elif etype == 'pre':
            w.u32(tl.MESSAGE_ENTITY_PRE)
        elif etype == 'mention':
            w.u32(tl.MESSAGE_ENTITY_MENTION)
        elif etype == 'url':
            w.u32(tl.MESSAGE_ENTITY_URL)
        elif etype == 'bot_command':
            w.u32(tl.MESSAGE_ENTITY_BOT_COMMAND)
        elif etype == 'text_url':
            w.u32(tl.MESSAGE_ENTITY_TEXT_URL)
        elif etype == 'custom_emoji':
            w.u32(tl.MESSAGE_ENTITY_CUSTOM_EMOJI)
        else:
            continue
        w.i32(ent['offset'])
        w.i32(ent['length'])
        if etype == 'text_url':
            w.string(ent.get('url') or '')
        elif etype == 'custom_emoji':
            w.i64(int(ent.get('document_id') or 0))
    return w.bytes_out()


def pack_peer_color(*, background_emoji_id: int = 0, color_id: int = 0) -> bytes:
    """peerColor#b54b5acf — accent / background custom emoji."""
    flags = 0
    if color_id:
        flags |= 1
    if background_emoji_id:
        flags |= 2
    w = tl.Writer()
    w.u32(0xb54b5acf)
    w.i32(flags)
    if flags & 1:
        w.i32(int(color_id))
    if flags & 2:
        w.i64(int(background_emoji_id))
    return w.bytes_out()


def pack_username(username: str, *, editable: bool = False,
                  active: bool = True) -> bytes:
    flags = 0
    if editable:
        flags |= 1 << 0
    if active:
        flags |= 1 << 1
    w = tl.Writer()
    w.u32(tl.USERNAME)
    w.i32(flags)
    w.string(username)
    return w.bytes_out()


def collect_usernames(u: dict, storage=None, *,
                      include_inactive: bool = True) -> list[tuple[int, str, bool, bool]]:
    rows: list[tuple[int, str, bool, bool]] = []
    primary = (u.get('username') or '').strip().lower()
    collectible_rows = []
    collectible_names = set()
    if storage is not None:
        collectible_rows = storage.get_collectible_usernames(
            int(u['id']), include_inactive=include_inactive)
        collectible_names = {
            (row.get('username') or '').strip().lower()
            for row in collectible_rows
            if (row.get('username') or '').strip()
        }
    if primary and primary not in collectible_names:
        rows.append((int(u.get('username_sort_order') or 0), primary, True, True))
    for row in collectible_rows:
        username = (row.get('username') or '').strip().lower()
        if username:
            rows.append((
                int(row.get('sort_order') or 0),
                username,
                False,
                bool(row.get('active')),
            ))
    rows.sort(key=lambda item: (
        0 if item[3] else 1,
        item[0],
        0 if item[2] else 1,
        item[1],
    ))
    return rows


def user_has_collectible_username(u: dict, storage=None) -> bool:
    if storage is None:
        return False
    rows = storage.get_collectible_usernames(int(u['id']), include_inactive=True)
    return bool(rows)


def main_active_username(u: dict, storage=None) -> str:
    for _, username, _, active in collect_usernames(u, storage):
        if active:
            return username
    return ''


def pack_usernames_vector(u: dict, storage=None, *, include_inactive: bool = True) -> bytes:
    rows = collect_usernames(u, storage, include_inactive=include_inactive)

    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(rows))
    for _, username, editable, active in rows:
        w.raw(pack_username(username, editable=editable, active=active))
    return w.bytes_out()


def pack_user_deleted(u: dict) -> bytes:
    """Minimal deleted user — client shows localized «Deleted Account»."""
    w = tl.Writer()
    w.u32(tl.USER)
    w.i32((1 << 0) | (1 << 13))
    w.i32(0)
    w.i64(int(u['id']))
    w.i64(int(u['access_hash']))
    return w.bytes_out()



def _ensure_custom_emoji_available(storage, doc_id: int) -> None:
    if storage is None or not doc_id:
        return
    try:
        from shop_webapp.emoji_import import ensure_custom_emoji_document
        ensure_custom_emoji_document(storage, int(doc_id))
    except Exception:
        pass

def pack_user_frozen_for_others(u: dict, storage=None) -> bytes:
    """Frozen account as deleted, with a client-rendered custom badge hint."""
    doc_id = 0
    if storage is not None:
        doc_id = storage.freeze_emoji_document_id()
        _ensure_custom_emoji_available(storage, doc_id)

    flags = (1 << 0) | (1 << 13)
    flags2 = 0
    if doc_id:
        flags2 |= (1 << 8)   # color / peerColor background emoji
        flags2 |= (1 << 14)  # bot_verification_icon

    w = tl.Writer()
    w.u32(tl.USER)
    w.i32(flags)
    w.i32(flags2)
    w.i64(int(u['id']))
    w.i64(int(u['access_hash']))
    if flags2 & (1 << 8):
        w.raw(pack_peer_color(background_emoji_id=doc_id))
    if flags2 & (1 << 14):
        w.i64(doc_id)
    return w.bytes_out()


def pack_user(u: dict, is_self: bool = False, viewer_id: Optional[int] = None,
              storage=None) -> bytes:
    uid = u['id']
    if u.get('banned'):
        return pack_user_deleted(u)

    if not is_self and storage is not None and storage.is_user_frozen(uid):
        return pack_user_frozen_for_others(u, storage=storage)

    hide_profile = False
    hide_phone = False
    hide_photo = False
    hide_status = False
    if storage is not None and viewer_id is not None and not is_self:
        from handlers.privacy import (
            INPUT_PRIVACY_KEY_PROFILE_PHOTO,
            INPUT_PRIVACY_KEY_PHONE_NUMBER,
            INPUT_PRIVACY_KEY_STATUS_TIMESTAMP,
            can_view_privacy,
        )
        if storage.should_hide_profile_from(viewer_id, uid):
            hide_profile = True
        if not can_view_privacy(storage, uid, viewer_id,
                                INPUT_PRIVACY_KEY_PHONE_NUMBER):
            hide_phone = True
        if not can_view_privacy(storage, uid, viewer_id,
                                INPUT_PRIVACY_KEY_PROFILE_PHOTO):
            hide_photo = True
        if not can_view_privacy(storage, uid, viewer_id,
                                INPUT_PRIVACY_KEY_STATUS_TIMESTAMP):
            hide_status = True

    u = dict(u)
    if hide_profile or hide_phone:
        u['phone'] = None
    if hide_profile or hide_status:
        u['online_until'] = 0
        u['last_seen'] = 1
        u['_hide_status'] = True
    if hide_profile:
        u['emoji_status_document_id'] = 0
        u['emoji_status_until'] = 0
        u['emoji_status_collectible_id'] = 0
        profile_photo = None
    else:
        profile_photo = None
        if storage is not None:
            profile_photo = storage.get_user_profile_photo(uid)

    username_rows = []
    has_collectible_username = False
    if storage is not None and not hide_profile:
        username_rows = collect_usernames(
            u, storage, include_inactive=is_self)
        has_collectible_username = user_has_collectible_username(u, storage)
        if not is_self:
            username_rows = [
                row for row in username_rows
                if row[2] or row[3]
            ]

    flags = 0
    flags |= (1 << 0)
    flags |= (1 << 1)
    flags |= (1 << 2)   # always send last_name (may be empty)
    username_for_user = ''
    if has_collectible_username:
        username_for_user = ''
    elif storage is not None and not hide_profile:
        username_for_user = main_active_username(u, storage)
    elif u.get('username'):
        username_for_user = (u.get('username') or '').strip().lower()
    if username_for_user:
        flags |= (1 << 3)
    if u.get('phone') and uid != SERVICE_USER_ID and not hide_phone:
        flags |= (1 << 4)
    flags |= (1 << 6)   # status
    if is_self:
        flags |= (1 << 10)
    if u.get('is_bot'):
        flags |= (1 << 14)
    if u.get('verified'):
        flags |= (1 << 17)
    if u.get('premium') and uid != SERVICE_USER_ID:
        flags |= (1 << 28)
    status_bytes = _pack_user_emoji_status(u, storage)
    if status_bytes:
        flags |= (1 << 30)
    if not hide_profile and not hide_photo and storage is not None:
        profile_photo = storage.get_user_profile_photo(uid)
    if profile_photo and not hide_profile and not hide_photo:
        flags |= (1 << 5)
    if uid == SERVICE_USER_ID:
        flags |= (1 << 17)
        flags |= (1 << 23)

    icon_id = int(u.get('bot_verification_icon') or 0)
    usernames_bytes = b''
    flags2 = 0
    if username_rows:
        w_usernames = tl.Writer()
        w_usernames.u32(tl.VECTOR)
        w_usernames.i32(len(username_rows))
        for _, username, editable, active in username_rows:
            w_usernames.raw(pack_username(
                username, editable=editable, active=active))
        usernames_bytes = w_usernames.bytes_out()
        flags2 |= 1 << 0
        if is_self:
            log.debug('pack self usernames: user_id=%s count=%d collectible=%s',
                      uid, len(username_rows), has_collectible_username)
    if icon_id:
        _ensure_custom_emoji_available(storage, icon_id)
        flags2 |= (1 << 14)
    stories_max = 0
    if storage is not None and not is_self:
        stories_max = storage.get_story_max_id(uid)
    if stories_max:
        flags2 |= 1 << 5
    from handlers.paid_messages import user_send_paid_messages_stars
    paid_stars = user_send_paid_messages_stars(u)
    if paid_stars > 0:
        flags2 |= 1 << 15

    w = tl.Writer()
    w.u32(tl.USER)
    w.i32(flags)
    w.i32(flags2)
    w.i64(uid)
    w.i64(u['access_hash'])
    w.string(u.get('first_name') or (
        config.SERVICE_USER_NAME if uid == SERVICE_USER_ID else 'User'))
    w.string(u.get('last_name') or '')
    if flags & (1 << 3):
        w.string(username_for_user)
    if flags & (1 << 4):
        from handlers.phone_countries import phone_for_user_field
        raw_phone = u.get('phone', '')
        w.string(phone_for_user_field(raw_phone) if raw_phone else '')
    if flags & (1 << 5):
        from handlers.media import pack_user_profile_photo
        w.raw(pack_user_profile_photo(profile_photo))
    w.raw(pack_user_status(u))
    if flags & (1 << 14):
        w.i32(int(u.get('bot_info_version') or 0))
    if flags & (1 << 30):
        w.raw(status_bytes)
    if flags2 & (1 << 0):
        w.raw(usernames_bytes)
    if flags2 & (1 << 5):
        from handlers.stories import pack_recent_story
        w.raw(pack_recent_story(stories_max))
    if flags2 & (1 << 14):
        w.i64(icon_id)
    if flags2 & (1 << 15):
        w.i64(paid_stars)
    return w.bytes_out()


def pack_user_min(u: dict) -> bytes:
    """Min user (id + access_hash only) for auction leaderboards."""
    w = tl.Writer()
    w.u32(tl.USER)
    w.i32(1 << 20)  # min
    w.i32(0)
    w.i64(int(u['id']))
    w.i64(int(u['access_hash']))
    return w.bytes_out()


def pack_bot_verification(bot_id: int, icon_id: int,
                          description: str) -> bytes:
    w = tl.Writer()
    w.u32(tl.BOT_VERIFICATION)
    w.i64(bot_id)
    w.i64(icon_id)
    w.string(description)
    return w.bytes_out()


def pack_emoji_status(document_id: int, until: int = 0) -> bytes:
    flags = 0
    if until:
        flags |= 1
    w = tl.Writer()
    w.u32(0xe7ff068a)  # emojiStatus
    w.i32(flags)
    w.i64(document_id)
    if flags & 1:
        w.i32(until)
    return w.bytes_out()


def _pack_user_emoji_status(u: dict, storage=None) -> Optional[bytes]:
    if not u.get('premium'):
        return None
    collectible_id = int(u.get('emoji_status_collectible_id') or 0)
    if collectible_id and storage:
        from handlers.star_gifts import pack_emoji_status_collectible
        saved = storage.get_saved_star_gift_by_unique_id_global(collectible_id)
        if saved and int(saved['owner_user_id']) == int(u['id']):
            gift_meta = storage.get_star_gift(int(saved['gift_id']))
            if gift_meta:
                return pack_emoji_status_collectible(
                    saved, gift_meta, storage,
                    int(u.get('emoji_status_until') or 0))
        return None
    doc_id = int(u.get('emoji_status_document_id') or 0)
    if doc_id:
        return pack_emoji_status(doc_id, int(u.get('emoji_status_until') or 0))
    return None


def pack_update_user_emoji_status(user_id: int,
                                   status: Optional[dict],
                                   storage=None) -> bytes:
    w = tl.Writer()
    w.u32(0x28373599)  # updateUserEmojiStatus
    w.i64(user_id)
    if status:
        if status.get('collectible_id') and storage:
            from handlers.star_gifts import pack_emoji_status_collectible
            saved = storage.get_saved_star_gift_by_unique_id_global(
                int(status['collectible_id']))
            if saved and int(saved['owner_user_id']) == int(user_id):
                gift_meta = storage.get_star_gift(int(saved['gift_id']))
                if gift_meta:
                    w.raw(pack_emoji_status_collectible(
                        saved, gift_meta, storage,
                        int(status.get('until') or 0)))
                    return w.bytes_out()
        if status.get('document_id'):
            w.raw(pack_emoji_status(
                int(status['document_id']),
                int(status.get('until') or 0)))
        else:
            w.u32(0x2de11aae)  # emojiStatusEmpty
    else:
        w.u32(0x2de11aae)  # emojiStatusEmpty
    return w.bytes_out()


def pack_user_status(u: dict) -> bytes:
    if u.get('_hide_status'):
        w = tl.Writer()
        w.u32(tl.USER_STATUS_RECENTLY)
        w.i32(0)  # flags (by_me)
        return w.bytes_out()
    now = int(time.time())
    online_until = int(u.get('online_until') or 0)
    last_seen = int(u.get('last_seen') or 0)
    if online_until > now:
        w = tl.Writer()
        w.u32(tl.USER_STATUS_ONLINE)
        w.i32(online_until)
        return w.bytes_out()
    w = tl.Writer()
    w.u32(tl.USER_STATUS_OFFLINE)
    w.i32(last_seen or now)
    return w.bytes_out()


def pack_update_user_status(user_id: int, u: dict) -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_USER_STATUS)
    w.i64(user_id)
    w.raw(pack_user_status(u))
    return w.bytes_out()


def pack_update_user(user_id: int) -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_USER)
    w.i64(user_id)
    return w.bytes_out()


def pack_update_user_name(u: dict, storage=None) -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_USER_NAME)
    w.i64(int(u['id']))
    w.string(u.get('first_name') or '')
    w.string(u.get('last_name') or '')
    w.raw(pack_usernames_vector(u, storage, include_inactive=True))
    return w.bytes_out()


def pack_update_peer_blocked(peer_id: int, *, blocked: bool = True) -> bytes:
    flags = 0
    if blocked:
        flags |= 1 << 0
    w = tl.Writer()
    w.u32(tl.UPDATE_PEER_BLOCKED)
    w.i32(flags)
    w.raw(pack_peer_user(peer_id))
    return w.bytes_out()


def pack_update_read_featured_emoji_stickers() -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_READ_FEATURED_EMOJI_STICKERS)
    return w.bytes_out()


def pack_update_config() -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_CONFIG)
    return w.bytes_out()


def pack_banner_refresh_push(storage=None, *, recipient_id: int) -> bytes:
    """updateConfig makes clients invalidate cached promo data."""
    return pack_updates(
        [pack_update_config()],
        [],
        user_id=recipient_id,
        storage=storage,
        increment_seq=False,
    )


def pack_user_verification_push(u: dict, storage=None, *,
                                recipient_id: Optional[int] = None) -> bytes:
    """Push updated user peer (verification icon) to connected clients."""
    recipient_id = int(recipient_id if recipient_id is not None else u['id'])
    return pack_updates(
        [pack_update_user(int(u['id']))],
        [u],
        user_id=recipient_id,
        storage=storage,
        increment_seq=True,
    )


def pack_freeze_user_push(u: dict, storage=None, *,
                          recipient_id: Optional[int] = None) -> bytes:
    """Push updated user peer after freeze / unfreeze / freeze settings change."""
    recipient_id = int(recipient_id if recipient_id is not None else u['id'])
    return pack_updates(
        [pack_update_user(int(u['id']))],
        [u],
        user_id=recipient_id,
        storage=storage,
        increment_seq=True,
    )


def pack_peer_settings_response(peer_user: dict, storage=None,
                                viewer_id: Optional[int] = None) -> bytes:
    charge = None
    if storage is not None and viewer_id is not None:
        from handlers.paid_messages import peer_charge_stars
        charge = peer_charge_stars(storage, peer_user['id'], viewer_id)
    w = tl.Writer()
    w.u32(tl.MESSAGES_PEER_SETTINGS)
    w.raw(pack_peer_settings(charge_paid_message_stars=charge))
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(pack_user(
        peer_user,
        is_self=viewer_id is not None and int(peer_user['id']) == int(viewer_id),
        viewer_id=viewer_id,
        storage=storage))
    return w.bytes_out()


def pack_peer_settings_response_channel(ch: dict,
                                        viewer_id: Optional[int] = None,
                                        storage=None) -> bytes:
    charge = None
    if storage is not None:
        charge_val = storage.get_channel_direct_message_stars(int(ch['id']))
        if charge_val > 0:
            charge = charge_val
    w = tl.Writer()
    w.u32(tl.MESSAGES_PEER_SETTINGS)
    w.raw(pack_peer_settings(charge_paid_message_stars=charge))
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def _verification_description(bv: dict) -> str:
    stored = (bv.get('description') or '').strip()
    if stored:
        return stored
    from frame_verification.catalog import description_for_organization
    return description_for_organization(bv.get('company') or '')


STARS_RATING = 0x1b0e4f07


def pack_stars_rating(rating: dict) -> bytes:
    """starsRating#1b0e4f07 — profile level badge (corefork.telegram.org)."""
    flags = 0
    next_level = rating.get('next_level_stars')
    if next_level is not None:
        flags |= 1 << 0
    w = tl.Writer()
    w.u32(STARS_RATING)
    w.i32(flags)
    w.i32(int(rating['level']))
    w.i64(int(rating['current_level_stars']))
    w.i64(int(rating['stars']))
    if flags & (1 << 0):
        w.i64(int(next_level))
    return w.bytes_out()


def _should_show_stars_rating(rating: dict, *, viewer_is_self: bool) -> bool:
    level = int(rating.get('level') or 0)
    stars = int(rating.get('stars') or 0)
    if level != 0:
        return True
    if viewer_is_self and stars != 0:
        return True
    return False


def pack_business_intro(intro: dict, storage=None) -> bytes:
    flags = 0
    sticker_doc = None
    if storage is not None:
        sticker_id = int(intro.get('sticker_document_id') or 0)
        if sticker_id:
            sticker_doc = storage.get_document_by_id(sticker_id)
            if sticker_doc:
                flags |= 1 << 0
    w = tl.Writer()
    w.u32(tl.BUSINESS_INTRO)
    w.i32(flags)
    w.string((intro.get('title') or '').strip())
    w.string((intro.get('description') or '').strip())
    if flags & (1 << 0) and sticker_doc and storage is not None:
        from handlers.stickers import pack_document
        ss = storage.get_sticker_set_by_id(int(sticker_doc.get('set_id') or 0))
        w.raw(pack_document(sticker_doc, ss))
    return w.bytes_out()


def parse_input_business_recipients(
        r: tl.Reader, storage, *,
        self_user_id: Optional[int] = None) -> dict:
    cid_or_flags = r.u32()
    if cid_or_flags == tl.INPUT_BUSINESS_RECIPIENTS:
        flags = r.i32()
    elif (cid_or_flags & ~0x3F) == 0:
        # Some clients may encode this structure "bare", so the first word is
        # flags directly.
        flags = int(cid_or_flags)
    else:
        # Forward compatibility: unknown constructor with expected payload
        # layout (flags + optional users vector).
        flags = r.i32()
    flags = int(flags) & 0x3F
    users: list[int] = []
    if flags & (1 << 4):
        marker = r.u32()
        if marker == tl.VECTOR:
            count = r.i32()
            for _ in range(count):
                user = parse_input_user(r, storage, self_user_id=self_user_id)
                if user:
                    users.append(int(user['id']))
        else:
            # Invalid users vector marker: keep recipients without explicit
            # users to avoid rejecting entire greeting payload.
            flags &= ~(1 << 4)
    return {'flags': int(flags), 'users': users}


def pack_business_recipients(recipients: dict) -> bytes:
    flags = int(recipients.get('flags') or 0)
    users = [int(uid) for uid in (recipients.get('users') or []) if int(uid) > 0]
    if users:
        flags |= 1 << 4
    else:
        flags &= ~(1 << 4)
    w = tl.Writer()
    w.u32(tl.BUSINESS_RECIPIENTS)
    w.i32(flags)
    if flags & (1 << 4):
        w.u32(tl.VECTOR)
        w.i32(len(users))
        for uid in users:
            w.i64(uid)
    return w.bytes_out()


def pack_business_greeting_message(config_row: dict) -> bytes:
    w = tl.Writer()
    w.u32(tl.BUSINESS_GREETING_MESSAGE)
    w.i32(int(config_row.get('shortcut_id') or 0))
    w.raw(pack_business_recipients({
        'flags': int(config_row.get('recipients_flags') or 0),
        'users': list(config_row.get('recipients_users') or []),
    }))
    w.i32(int(config_row.get('no_activity_days') or 0))
    return w.bytes_out()


def parse_business_away_message_schedule(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid == tl.BUSINESS_AWAY_MESSAGE_SCHEDULE_OUTSIDE_WORK_HOURS:
        return {'schedule_type': 'outside_work_hours'}
    if cid == tl.BUSINESS_AWAY_MESSAGE_SCHEDULE_CUSTOM:
        return {
            'schedule_type': 'custom',
            'schedule_start': int(r.i32()),
            'schedule_end': int(r.i32()),
        }
    if cid != tl.BUSINESS_AWAY_MESSAGE_SCHEDULE_ALWAYS:
        log.warning('parse_business_away_message_schedule: unknown cid=%#010x', cid)
    return {'schedule_type': 'always'}


def pack_business_away_message_schedule(config_row: dict) -> bytes:
    schedule_type = (config_row.get('schedule_type') or 'always').strip() or 'always'
    w = tl.Writer()
    if schedule_type == 'outside_work_hours':
        w.u32(tl.BUSINESS_AWAY_MESSAGE_SCHEDULE_OUTSIDE_WORK_HOURS)
    elif schedule_type == 'custom':
        w.u32(tl.BUSINESS_AWAY_MESSAGE_SCHEDULE_CUSTOM)
        w.i32(int(config_row.get('schedule_start') or 0))
        w.i32(int(config_row.get('schedule_end') or 0))
    else:
        w.u32(tl.BUSINESS_AWAY_MESSAGE_SCHEDULE_ALWAYS)
    return w.bytes_out()


def parse_input_business_away_message(
        r: tl.Reader, storage, *,
        self_user_id: Optional[int] = None) -> dict:
    cid = r.u32()
    if cid != tl.INPUT_BUSINESS_AWAY_MESSAGE:
        raise ValueError('BUSINESS_AWAY_INVALID')
    flags = r.i32()
    offline_only = bool(flags & 1)
    if flags & 1:
        r.bool()
    shortcut_id = r.i32()
    schedule = parse_business_away_message_schedule(r)
    recipients = parse_input_business_recipients(
        r, storage, self_user_id=self_user_id)
    return {
        'offline_only': offline_only,
        'shortcut_id': int(shortcut_id),
        'recipients_flags': int(recipients.get('flags') or 0),
        'recipients_users': list(recipients.get('users') or []),
        **schedule,
    }


def pack_business_away_message(config_row: dict) -> bytes:
    flags = 0
    if config_row.get('offline_only'):
        flags |= 1
    w = tl.Writer()
    w.u32(tl.BUSINESS_AWAY_MESSAGE)
    w.i32(flags)
    if flags & 1:
        w.u32(tl.BOOL_TRUE)
    w.i32(int(config_row.get('shortcut_id') or 0))
    w.raw(pack_business_away_message_schedule(config_row))
    w.raw(pack_business_recipients({
        'flags': int(config_row.get('recipients_flags') or 0),
        'users': list(config_row.get('recipients_users') or []),
    }))
    return w.bytes_out()


def business_away_schedule_active(config_row: dict, storage, owner_id: int) -> bool:
    schedule_type = (config_row.get('schedule_type') or 'always').strip() or 'always'
    if schedule_type == 'always':
        return True
    if schedule_type == 'custom':
        now = int(time.time())
        start = int(config_row.get('schedule_start') or 0)
        end = int(config_row.get('schedule_end') or 0)
        return start <= now <= end
    if schedule_type == 'outside_work_hours':
        work_hours = storage.get_business_work_hours(int(owner_id))
        if not work_hours:
            return True
        return not _business_open_now(work_hours)
    return False


def parse_input_geo_point(r: tl.Reader) -> Optional[dict]:
    cid = r.u32()
    if cid == tl.INPUT_GEO_POINT_EMPTY:
        return None
    if cid != tl.INPUT_GEO_POINT:
        raise ValueError('GEO_POINT_INVALID')
    flags = r.i32()
    lat = r.f64()
    long = r.f64()
    accuracy_radius = 0
    if flags & 1:
        accuracy_radius = int(r.i32())
    return {
        'lat': float(lat),
        'long': float(long),
        'accuracy_radius': accuracy_radius,
    }


def pack_geo_point(location: dict) -> bytes:
    w = tl.Writer()
    w.u32(tl.GEO_POINT)
    flags = 0
    accuracy = int(location.get('accuracy_radius') or 0)
    if accuracy > 0:
        flags |= 1
    w.i32(flags)
    w.f64(float(location.get('long') or 0))
    w.f64(float(location.get('lat') or 0))
    w.i64(int(location.get('access_hash') or 0))
    if flags & 1:
        w.i32(accuracy)
    return w.bytes_out()


def pack_business_location(location: dict) -> bytes:
    w = tl.Writer()
    w.u32(tl.BUSINESS_LOCATION)
    flags = 0
    if location.get('has_geo'):
        flags |= 1
    w.i32(flags)
    if flags & 1:
        w.raw(pack_geo_point(location))
    w.string((location.get('address') or '').strip())
    return w.bytes_out()


def _business_open_now(work_hours: dict) -> bool:
    timezone_id = (work_hours.get('timezone_id') or '').strip() or 'UTC'
    weekly_open = work_hours.get('weekly_open') or []
    try:
        now = datetime.now(ZoneInfo(timezone_id))
    except Exception:
        now = datetime.utcnow()
    minute_of_week = now.weekday() * 24 * 60 + now.hour * 60 + now.minute
    week = 7 * 24 * 60
    shifted_minute = minute_of_week + week
    for item in weekly_open:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get('start_minute') or 0)
            end = int(item.get('end_minute') or 0)
        except (TypeError, ValueError):
            continue
        if start <= minute_of_week < end:
            return True
        if end > week and start <= shifted_minute < end:
            return True
    return False


def pack_business_work_hours(work_hours: dict) -> bytes:
    flags = 0
    if _business_open_now(work_hours):
        flags |= 1 << 0
    timezone_id = (work_hours.get('timezone_id') or '').strip() or 'UTC'
    weekly_open = work_hours.get('weekly_open') or []
    normalized: List[dict] = []
    for item in weekly_open:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get('start_minute') or 0)
            end = int(item.get('end_minute') or 0)
        except (TypeError, ValueError):
            continue
        normalized.append({'start_minute': start, 'end_minute': end})
    normalized.sort(key=lambda row: (row['start_minute'], row['end_minute']))

    w = tl.Writer()
    w.u32(tl.BUSINESS_WORK_HOURS)
    w.i32(flags)
    w.string(timezone_id)
    w.u32(tl.VECTOR)
    w.i32(len(normalized))
    for item in normalized:
        w.u32(tl.BUSINESS_WEEKLY_OPEN)
        w.i32(int(item['start_minute']))
        w.i32(int(item['end_minute']))
    return w.bytes_out()


def _write_user_full_body(w: tl.Writer, *, flags: int, flags2: int,
                          about: str = '',
                          profile_photo=None,
                          show_add_contact: bool = False,
                          settings_charge: Optional[int] = None,
                          stories_bytes: Optional[bytes] = None,
                          gifts_count: int = 0,
                          bv: Optional[dict] = None,
                          bv_desc: str = '',
                          viewer_stars: Optional[int] = None,
                          stars_rating: Optional[dict] = None,
                          business_work_hours: Optional[dict] = None,
                          business_location: Optional[dict] = None,
                          business_intro: Optional[dict] = None,
                          business_greeting_message: Optional[dict] = None,
                          business_away_message: Optional[dict] = None,
                          main_profile_tab: int = 0,
                          saved_music_doc: Optional[dict] = None,
                          birthday_bytes: Optional[bytes] = None,
                          disallowed_gifts_bytes: Optional[bytes] = None,
                          personal_channel_id: int = 0,
                          personal_channel_message: int = 0,
                          storage=None) -> None:
    """Serialize userFull#6cbe645 fields after id in official layer 227 order."""
    if flags & (1 << 1):
        w.string(about)
    w.raw(pack_peer_settings(
        show_add_contact=show_add_contact,
        charge_paid_message_stars=settings_charge))
    if flags & (1 << 21):
        from handlers.media import pack_photo
        w.raw(pack_photo(profile_photo))
    if flags & (1 << 2):
        from handlers.media import pack_photo
        w.raw(pack_photo(profile_photo))
    if flags & (1 << 22):
        from handlers.media import pack_photo
        w.raw(pack_photo(profile_photo))
    w.raw(pack_peer_notify_settings())
    if flags & (1 << 3):
        pass  # bot_info — not implemented
    if flags & (1 << 6):
        pass  # pinned_msg_id — not implemented
    w.i32(0)  # common_chats_count
    if flags & (1 << 11):
        pass  # folder_id
    if flags & (1 << 14):
        pass  # ttl_period
    if flags & (1 << 15):
        pass  # theme
    if flags & (1 << 16):
        pass  # private_forward_name
    if flags & (1 << 17):
        pass  # bot_group_admin_rights
    if flags & (1 << 18):
        pass  # bot_broadcast_admin_rights
    if flags & (1 << 24):
        pass  # wallpaper
    if flags & (1 << 25) and stories_bytes:
        w.raw(stories_bytes)
    if flags2 & (1 << 0) and business_work_hours:
        w.raw(pack_business_work_hours(business_work_hours))
    if flags2 & (1 << 1) and business_location:
        w.raw(pack_business_location(business_location))
    if flags2 & (1 << 2) and business_greeting_message:
        w.raw(pack_business_greeting_message(business_greeting_message))
    if flags2 & (1 << 3) and business_away_message:
        w.raw(pack_business_away_message(business_away_message))
    if flags2 & (1 << 4) and business_intro:
        w.raw(pack_business_intro(business_intro, storage))
    if flags2 & (1 << 5) and birthday_bytes:
        w.raw(birthday_bytes)
    if flags2 & (1 << 6):
        w.i64(int(personal_channel_id))
        w.i32(int(personal_channel_message))
    if flags2 & (1 << 8):
        w.i32(int(gifts_count))
    if flags2 & (1 << 11):
        pass  # starref_program
    if flags2 & (1 << 12) and bv:
        w.raw(pack_bot_verification(
            int(bv['bot_id']),
            int(bv['icon_id']),
            bv_desc,
        ))
    if flags2 & (1 << 14):
        w.i64(int(viewer_stars or 0))
    if flags2 & (1 << 15) and disallowed_gifts_bytes:
        w.raw(disallowed_gifts_bytes)
    if flags2 & (1 << 17) and stars_rating:
        w.raw(pack_stars_rating(stars_rating))
    if flags2 & (1 << 18):
        pass  # stars_my_pending_rating / date
    if flags2 & (1 << 20):
        w.u32(int(main_profile_tab))
    if flags2 & (1 << 21) and saved_music_doc and storage is not None:
        from handlers.stickers import pack_document
        ss = storage.get_sticker_set_by_id(saved_music_doc['set_id'])
        w.raw(pack_document(saved_music_doc, ss))
    if flags2 & (1 << 22):
        pass  # note
    if flags2 & (1 << 25):
        pass  # bot_manager_id — not implemented


def pack_user_full_deleted(u: dict) -> bytes:
    w = tl.Writer()
    w.u32(tl.USER_FULL)
    w.i32(0)
    w.i32(0)
    w.i64(int(u['id']))
    _write_user_full_body(w, flags=0, flags2=0,
                          show_add_contact=False)
    return w.bytes_out()


def pack_user_full_frozen(u: dict, storage=None) -> bytes:
    doc_id = storage.freeze_emoji_document_id() if storage is not None else 0
    _ensure_custom_emoji_available(storage, doc_id)

    flags = 1 << 7  # can_pin_message
    flags2 = 0
    bv = None
    desc = ''
    if doc_id:
        flags2 |= 1 << 12  # bot_verification
        flags2 |= 1 << 16  # display_gifts_button
        bv = {
            'bot_id': int(SERVICE_USER_ID),
            'icon_id': int(doc_id),
        }
        desc = 'Аккаунт заморожен'

    w = tl.Writer()
    w.u32(tl.USER_FULL)
    w.i32(flags)
    w.i32(flags2)
    w.i64(int(u['id']))
    _write_user_full_body(
        w, flags=flags, flags2=flags2,
        show_add_contact=False,
        bv=bv, bv_desc=desc, storage=storage)
    return w.bytes_out()


def pack_user_full(u: dict, storage=None, viewer_is_self: bool = False,
                   viewer_id: Optional[int] = None) -> bytes:
    if u.get('banned'):
        return pack_user_full_deleted(u)

    if not viewer_is_self and storage is not None and storage.is_user_frozen(u['id']):
        return pack_user_full_frozen(u, storage=storage)

    hide_profile = False
    hide_about = False
    hide_birthday = False
    hide_saved_music = False
    if storage is not None and viewer_id is not None and not viewer_is_self:
        from handlers.privacy import (
            INPUT_PRIVACY_KEY_ABOUT,
            INPUT_PRIVACY_KEY_BIRTHDAY,
            INPUT_PRIVACY_KEY_SAVED_MUSIC,
            can_view_privacy,
        )
        if storage.should_hide_profile_from(viewer_id, u['id']):
            hide_profile = True
        if not can_view_privacy(storage, u['id'], viewer_id,
                                INPUT_PRIVACY_KEY_ABOUT):
            hide_about = True
        if not can_view_privacy(storage, u['id'], viewer_id,
                                INPUT_PRIVACY_KEY_BIRTHDAY):
            hide_birthday = True
        if not can_view_privacy(storage, u['id'], viewer_id,
                                INPUT_PRIVACY_KEY_SAVED_MUSIC):
            hide_saved_music = True

    flags = 1 << 7  # can_pin_message
    if (storage is not None and viewer_id is not None and not viewer_is_self
            and storage.is_blocked(viewer_id, u['id'])):
        flags |= 1 << 0  # blocked — "you blocked this user" bar
    about = (u.get('about') or '').strip()
    if hide_profile or hide_about:
        about = ''
    if about:
        flags |= (1 << 1)

    profile_photo = None
    hide_photo = False
    if storage is not None and viewer_id is not None and not viewer_is_self:
        from handlers.privacy import (
            INPUT_PRIVACY_KEY_PROFILE_PHOTO,
            can_view_privacy,
        )
        if not can_view_privacy(storage, u['id'], viewer_id,
                                INPUT_PRIVACY_KEY_PROFILE_PHOTO):
            hide_photo = True
    if storage is not None and not hide_profile and not hide_photo:
        profile_photo = storage.get_user_profile_photo(u['id'])
    if profile_photo:
        flags |= (1 << 2)

    bv = u.get('bot_verification')
    flags2 = 0
    desc = ''
    if bv:
        icon_id = int(bv.get('icon_id') or 0)
        if icon_id:
            _ensure_custom_emoji_available(storage, icon_id)
        flags2 |= (1 << 12)
        flags2 |= (1 << 16)  # profile tab — verification badge needs this
        desc = _verification_description(bv)

    active_stories = []
    if storage is not None:
        active_stories = storage.get_active_stories(u['id'])
    if active_stories:
        flags |= 1 << 26  # stories_pinned_available
        # PeerStories with full media is fetched via getPeerStories/getAllStories.
        # Packing it into userFull (~270KB) breaks profile and story state.

    gifts_count = 0
    visible_collections = []
    show_gifts_button = bool(u.get('premium'))
    if storage is not None:
        gp = storage.get_global_privacy(u['id'])
        show_gifts_button = bool(u.get('premium')) and bool(
            gp.get('display_gifts_button', True))
        gifts_count = storage.count_saved_star_gifts(u['id'], saved_only=True)
        visible_collections = storage.list_visible_star_gift_collections(u['id'])
    if gifts_count > 0 or visible_collections:
        flags2 |= 1 << 8  # stargifts_count
        if show_gifts_button:
            flags2 |= 1 << 16  # display_gifts_button — profile gifts tab
    elif not viewer_is_self and not u.get('is_bot') and show_gifts_button:
        flags2 |= 1 << 16  # send-gift entry in chat for others

    stars_rating = None
    if storage is not None and not u.get('is_bot'):
        stars_rating = storage.get_stars_rating(u['id'])
        if _should_show_stars_rating(stars_rating, viewer_is_self=viewer_is_self):
            flags2 |= 1 << 17

    saved_music_doc = None
    business_intro = None
    business_greeting_message = None
    business_away_message = None
    business_work_hours = None
    business_location = None
    main_profile_tab = 0
    personal_channel_id = 0
    personal_channel_message = 0
    if storage is not None and not u.get('is_bot'):
        business_work_hours = storage.get_business_work_hours(u['id'])
        if business_work_hours:
            flags2 |= 1 << 0
        business_location = storage.get_business_location(u['id'])
        if business_location:
            flags2 |= 1 << 1
        if viewer_is_self:
            business_greeting_message = storage.get_business_greeting_message(u['id'])
            if business_greeting_message:
                flags2 |= 1 << 2
            business_away_message = storage.get_business_away_message(u['id'])
            if business_away_message:
                flags2 |= 1 << 3
        business_intro = storage.get_business_intro(u['id'])
        if business_intro:
            flags2 |= 1 << 4
        saved_music_doc = storage.get_user_saved_music_first(u['id'])
        if saved_music_doc and not hide_saved_music:
            flags2 |= 1 << 21
        main_profile_tab = storage.get_user_main_profile_tab(u['id'])
        if main_profile_tab:
            flags2 |= 1 << 20
        personal_channel_id, personal_channel_message = (
            storage.get_user_personal_channel(u['id']))
        if personal_channel_id:
            flags2 |= 1 << 6

    viewer_stars = None
    if storage is not None and viewer_id is not None:
        from handlers.paid_messages import user_full_paid_stars_for_viewer
        viewer_stars = user_full_paid_stars_for_viewer(
            storage, u, viewer_id, viewer_is_self=viewer_is_self)
        if viewer_stars is not None:
            flags2 |= 1 << 14

    from handlers.paid_messages import (
        peer_charge_stars,
        can_view_paid_messages_revenue,
    )
    if viewer_is_self:
        show_revenue = can_view_paid_messages_revenue(u, viewer_is_self=True)
        if not show_revenue and storage is not None:
            show_revenue = (
                config.can_use_ton_payments(u['id'])
                or storage.get_ton_balance(u['id']) > 0
            )
        if show_revenue:
            flags2 |= 1 << 9  # can_view_revenue — paid messages / GRAM revenue

    settings_charge = None
    if storage is not None and viewer_id is not None and not viewer_is_self:
        settings_charge = peer_charge_stars(storage, u['id'], viewer_id)

    birthday_bytes = None
    if storage is not None and not hide_birthday:
        from handlers.privacy import decode_user_birthday, pack_birthday
        decoded = decode_user_birthday(u.get('user_birthday'))
        if decoded:
            day, month, year = decoded
            birthday_bytes = pack_birthday(day, month, year)
            flags2 |= 1 << 5

    disallowed_gifts_bytes = None
    if viewer_is_self and storage is not None:
        gp_self = storage.get_global_privacy(u['id'])
        dg = gp_self.get('disallowed_gifts') or {}
        if dg:
            from handlers.privacy import pack_disallowed_gifts_settings
            disallowed_gifts_bytes = pack_disallowed_gifts_settings(dg)
            flags2 |= 1 << 15

    if storage is not None and viewer_id is not None and not viewer_is_self:
        from handlers.privacy import (
            INPUT_PRIVACY_KEY_VOICE_MESSAGES,
            can_view_privacy,
        )
        gp = storage.get_global_privacy(u['id'])
        if gp.get('new_noncontact_peers_require_premium'):
            if not storage.users_are_contacts(u['id'], viewer_id):
                flags |= 1 << 29  # contact_require_premium
        if not can_view_privacy(
                storage, u['id'], viewer_id, INPUT_PRIVACY_KEY_VOICE_MESSAGES):
            flags |= 1 << 20  # voice_messages_forbidden

    w = tl.Writer()
    w.u32(tl.USER_FULL)
    w.i32(flags)
    w.i32(flags2)
    w.i64(u['id'])
    _write_user_full_body(
        w,
        flags=flags,
        flags2=flags2,
        about=about,
        profile_photo=profile_photo,
        show_add_contact=not viewer_is_self,
        settings_charge=settings_charge,
        gifts_count=gifts_count,
        bv=bv,
        bv_desc=desc,
        viewer_stars=viewer_stars,
        stars_rating=stars_rating,
        business_work_hours=business_work_hours,
        business_location=business_location,
        business_intro=business_intro,
        business_greeting_message=business_greeting_message,
        business_away_message=business_away_message,
        main_profile_tab=main_profile_tab,
        saved_music_doc=saved_music_doc,
        birthday_bytes=birthday_bytes,
        disallowed_gifts_bytes=disallowed_gifts_bytes,
        personal_channel_id=personal_channel_id if storage is not None else 0,
        personal_channel_message=(
            personal_channel_message if storage is not None else 0),
        storage=storage,
    )
    return w.bytes_out()


def pack_users_user_full(u: dict, viewer_is_self: bool = False,
                         storage=None, viewer_id: Optional[int] = None) -> bytes:
    users_map = {u['id']: u}
    if storage is not None and storage.is_user_frozen(u['id']) and not viewer_is_self:
        bot = storage.get_user_by_id(int(SERVICE_USER_ID))
        if bot and bot['id'] != u['id']:
            users_map[bot['id']] = bot
    if storage is not None and u.get('bot_verification'):
        bot = storage.get_user_by_id(int(u['bot_verification']['bot_id']))
        if bot and bot['id'] != u['id']:
            users_map[bot['id']] = bot

    w = tl.Writer()
    w.u32(tl.USERS_USER_FULL)
    w.raw(pack_user_full(u, storage, viewer_is_self=viewer_is_self,
                         viewer_id=viewer_id))
    w.u32(tl.VECTOR)
    w.i32(0)
    users = list(users_map.values())
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for user in users:
        w.raw(pack_user(
            user, is_self=viewer_is_self and user['id'] == u['id'],
            viewer_id=viewer_id, storage=storage))
    return w.bytes_out()


def _message_is_out(msg: dict) -> bool:
    """Whether the message is outgoing for the mailbox owner (viewer)."""
    out = msg.get('out')
    if out is not None:
        return bool(out)
    owner_id = int(msg['owner_id'])
    from_id = int(msg['from_id'])
    peer_id = int(msg['peer_id'])
    action_json = msg.get('action_json')
    if isinstance(action_json, str) and action_json:
        try:
            action_json = json.loads(action_json)
        except json.JSONDecodeError:
            action_json = None
    if isinstance(action_json, dict):
        if action_json.get('type') == 'monoforum_direct':
            return int(action_json.get('sender_id') or 0) == owner_id
    if owner_id == peer_id and from_id == owner_id:
        return True  # Saved Messages / self-chat
    return from_id == owner_id and peer_id != owner_id


def _pack_service_message_empty(msg: dict) -> bytes:
    """Fallback service message shell when action packing fails."""
    owner_id = msg['owner_id']
    from_id = msg['from_id']
    peer_id = msg['peer_id']
    is_out = _message_is_out(msg)
    flags = 1 << 1 if is_out else 1 << 8
    chat_peer = peer_id if is_out else from_id
    w = tl.Writer()
    w.u32(tl.MESSAGE_SERVICE)
    w.i32(flags)
    w.i32(int(msg['id']))
    if flags & (1 << 8):
        w.raw(pack_peer_user(from_id))
    w.raw(pack_peer_user(chat_peer))
    w.i32(int(msg['date']))
    aw = tl.Writer()
    aw.u32(0xb6aee4b5)  # messageActionEmpty
    w.raw(aw.bytes_out())
    return w.bytes_out()


def pack_message_fwd_header(fwd: dict, storage=None,
                            viewer_id: Optional[int] = None) -> bytes:
    flags = 0
    from_id = int(fwd.get('from_id') or 0)
    from_name = (fwd.get('from_name') or '').strip()
    channel_post = int(fwd.get('channel_post') or 0)
    date = int(fwd.get('date') or 0)
    if storage is not None and viewer_id is not None and from_id and not from_name:
        from handlers.privacy import viewer_can_link_forward_author
        if from_id < config.CHANNEL_ID_MIN:
            if not viewer_can_link_forward_author(storage, from_id, viewer_id):
                from_id = 0
    if from_id and not from_name:
        flags |= 1
    if from_name:
        flags |= 1 << 5
    if channel_post:
        flags |= 1 << 2
    w = tl.Writer()
    w.u32(0x4e4df4bb)  # messageFwdHeader
    w.i32(flags)
    if flags & 1:
        w.raw(pack_peer(from_id))
    if flags & (1 << 5):
        w.string(from_name)
    w.i32(date)
    if flags & (1 << 2):
        w.i32(channel_post)
    return w.bytes_out()


def pack_message(msg: dict, storage=None, *,
                 quick_reply_shortcut_id: Optional[int] = None) -> bytes:
    """message#3ae56482 — Airgram layer layout (flags + flags2)."""
    action_json = msg.get('action_json')
    if isinstance(action_json, str) and action_json:
        action_json = json.loads(action_json)
    is_channel_direct = bool(
        action_json and action_json.get('type') == 'channel_direct')
    is_monoforum_direct = bool(
        action_json and action_json.get('type') == 'monoforum_direct')
    if action_json and not (is_channel_direct or is_monoforum_direct):
        if action_json.get('type') == 'channel_create':
            from handlers.groups import pack_service_channel_create
            return pack_service_channel_create(msg, action_json, storage)
        if action_json.get('type') == 'chat_create':
            from handlers.groups import pack_service_chat_create
            return pack_service_chat_create(msg, action_json, storage)
        if action_json.get('type') == 'chat_edit_title':
            from handlers.groups import pack_service_chat_edit_title
            return pack_service_chat_edit_title(msg, action_json, storage)
        if action_json.get('type') == 'pin_message':
            from handlers.groups import pack_service_pin_message
            return pack_service_pin_message(msg, action_json, storage)
        if action_json.get('type') == 'paid_messages_price':
            from handlers.paid_messages import pack_service_paid_messages_price
            return pack_service_paid_messages_price(msg, action_json)
        if action_json.get('type') == 'paid_messages_refunded':
            from handlers.paid_messages import pack_service_paid_messages_refunded
            return pack_service_paid_messages_refunded(msg, action_json)
        if action_json.get('type') == 'suggested_post_approval':
            from handlers.groups import pack_service_suggested_post_approval
            return pack_service_suggested_post_approval(msg, action_json, storage)
        from handlers.star_gifts import pack_service_message
        try:
            return pack_service_message(msg, action_json, storage)
        except (KeyError, ValueError, TypeError) as exc:
            log.warning('service message %s pack failed: %s — using empty action',
                        msg.get('id'), exc)
            return _pack_service_message_empty(msg)

    entities = msg.get('entities') or []
    if isinstance(entities, str):
        entities = json.loads(entities)

    owner_id = msg['owner_id']
    from_id = msg['from_id']
    peer_id = msg['peer_id']

    is_out = _message_is_out(msg)
    is_broadcast = False
    if is_channel_id(peer_id) and storage is not None:
        ch = storage.get_channel(peer_id)
        if ch:
            is_broadcast = not bool(int(ch.get('megagroup') or 0))

    flags = 0
    if is_out:
        flags |= (1 << 1)
    if entities:
        flags |= (1 << 7)
    reply_to_msg_id = int(msg.get('reply_to_msg_id') or 0)
    if reply_to_msg_id:
        flags |= (1 << 3)
    reply_markup_bytes = b''
    reply_markup_raw = msg.get('reply_markup_json')
    if reply_markup_raw:
        if isinstance(reply_markup_raw, str):
            try:
                reply_markup_raw = json.loads(reply_markup_raw)
            except Exception:
                reply_markup_raw = None
        reply_markup_bytes = pack_reply_markup(reply_markup_raw)
        if reply_markup_bytes:
            flags |= 1 << 6

    media_doc_id = int(msg.get('media_document_id') or 0)
    media_photo_id = int(msg.get('media_photo_id') or 0)
    media_bytes = b''
    poll_raw = msg.get('media_poll_json')
    if poll_raw:
        from handlers.polls import parse_poll_json, pack_message_media_poll
        poll_data = parse_poll_json(poll_raw)
        if poll_data and storage is not None:
            media_bytes = pack_message_media_poll(
                storage, poll_data, int(peer_id), int(msg['id']),
                viewer_id=int(owner_id))
            if media_bytes:
                flags |= (1 << 9)
    elif media_photo_id and storage is not None:
        from handlers.media import pack_message_media_photo
        photo_row = storage.get_photo_by_id(media_photo_id)
        if photo_row:
            media_bytes = pack_message_media_photo(photo_row)
            if media_bytes:
                flags |= (1 << 9)
    elif media_doc_id and storage is not None:
        from handlers.stickers import pack_document
        doc_row = storage.get_sticker_document_by_id(media_doc_id)
        if doc_row:
            ss = storage.get_sticker_set_by_id(doc_row['set_id'])
            media_bytes = pack_message_media_document(
                doc_row, ss, int(msg.get('date') or 0) or None)
            if media_bytes:
                flags |= (1 << 9)

    if is_channel_id(peer_id):
        chat_peer = peer_id
        if is_broadcast and not is_channel_direct:
            flags |= 1 << 14  # post — required for broadcast channel messages
        else:
            flags |= 1 << 8  # megagroup — show sender
    else:
        if not is_out:
            flags |= (1 << 8)
        chat_peer = peer_id if is_out else from_id

    views = forwards = 0
    if is_broadcast and not is_channel_direct and storage is not None:
        stats = storage.get_message_views(peer_id, int(msg['id']))
        views = int(stats.get('views') or 0)
        forwards = int(stats.get('forwards') or 0)
        flags |= 1 << 10

    paid_stars = int(msg.get('paid_message_stars') or 0)
    flags2 = 0
    if paid_stars > 0:
        flags2 |= 1 << 6
    if msg.get('offline'):
        flags2 |= 1 << 1
    qr_shortcut_id = int(quick_reply_shortcut_id or msg.get('quick_reply_shortcut_id') or 0)
    if qr_shortcut_id:
        flags |= 1 << 30
    suggested_raw = msg.get('suggested_post_json')
    suggested_post = None
    if suggested_raw:
        if isinstance(suggested_raw, str):
            try:
                suggested_post = json.loads(suggested_raw)
            except json.JSONDecodeError:
                suggested_post = None
        elif isinstance(suggested_raw, dict):
            suggested_post = suggested_raw
    if suggested_post:
        flags2 |= 1 << 7
        if int(suggested_post.get('price_stars') or 0) > 0:
            flags2 |= 1 << 8
    saved_peer_id = int(msg.get('saved_peer_id') or 0)
    if saved_peer_id:
        flags |= 1 << 28
    fwd_from = None
    fwd_raw = msg.get('fwd_from_json')
    if fwd_raw:
        if isinstance(fwd_raw, str) and fwd_raw:
            try:
                fwd_from = json.loads(fwd_raw)
            except json.JSONDecodeError:
                fwd_from = None
        elif isinstance(fwd_raw, dict):
            fwd_from = fwd_raw
    if fwd_from:
        flags |= 1 << 2

    w = tl.Writer()
    w.u32(tl.MESSAGE)
    w.i32(flags)
    w.i32(flags2)
    w.i32(msg['id'])
    if flags & (1 << 8):
        w.raw(pack_peer(from_id))
    w.raw(pack_peer(chat_peer))
    if flags & (1 << 28):
        w.raw(pack_peer(saved_peer_id))
    if flags & (1 << 2):
        viewer = int(msg.get('owner_id') or 0)
        w.raw(pack_message_fwd_header(
            fwd_from or {}, storage=storage, viewer_id=viewer))
    if flags & (1 << 3):
        w.raw(pack_message_reply_header(
            reply_to_msg_id, msg.get('reply_to_peer_id')))
    w.i32(msg['date'])
    w.string(msg['text'])
    if media_bytes:
        w.raw(media_bytes)
    if reply_markup_bytes:
        w.raw(reply_markup_bytes)
    if entities:
        w.raw(pack_entities(entities, msg['text']))
    if flags & (1 << 10):
        w.i32(views)
        w.i32(forwards)
    if flags2 & (1 << 6):
        w.i64(paid_stars)
    if flags2 & (1 << 7):
        w.raw(pack_suggested_post(suggested_post or {}))
    if flags & (1 << 30):
        w.i32(int(qr_shortcut_id))
    return w.bytes_out()


def pack_message_media_document(doc_row: dict, ss: Optional[dict],
                                message_date: Optional[int] = None) -> bytes:
    """messageMediaDocument#52d8ccd9 with embedded document."""
    if not doc_row:
        return b''
    from handlers.media import message_media_document_flags
    from handlers.stickers import pack_document
    media_flags = message_media_document_flags(doc_row)
    w = tl.Writer()
    w.u32(0x52d8ccd9)
    w.i32(media_flags)
    if media_flags & 1:
        w.raw(pack_document(doc_row, ss, date_override=message_date))
    return w.bytes_out()


def pack_dialog(d: dict) -> bytes:
    top = int(d['top_message_id'] or 0)
    unread = int(d.get('unread_count') or 0)
    read_inbox = min(int(d.get('read_inbox_max_id') or 0), top)
    read_outbox = min(int(d.get('read_outbox_max_id') or 0), top)
    if unread > 0 and read_inbox >= top:
        read_inbox = max(0, top - 1)

    w = tl.Writer()
    w.u32(tl.DIALOG)
    w.i32(0)
    w.raw(pack_peer(int(d['peer_id'])))
    w.i32(top)
    w.i32(read_inbox)
    w.i32(read_outbox)
    w.i32(unread)
    w.i32(0)
    w.i32(0)
    w.i32(0)
    w.raw(pack_peer_notify_settings())
    return w.bytes_out()


def pack_messages_dialogs(dialogs: List[dict], messages: List[dict],
                          users: List[dict], viewer_id: Optional[int] = None,
                          storage=None, chats: Optional[List[dict]] = None) -> bytes:
    chats = chats or []
    w = tl.Writer()
    w.u32(tl.MESSAGES_DIALOGS)
    w.u32(tl.VECTOR)
    w.i32(len(dialogs))
    for d in dialogs:
        w.raw(pack_dialog(d))
    w.u32(tl.VECTOR)
    w.i32(len(messages))
    for m in messages:
        w.raw(pack_message(m, storage))
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(pack_user(
            u, is_self=viewer_id is not None and u['id'] == viewer_id,
            viewer_id=viewer_id, storage=storage))
    return w.bytes_out()


def pack_messages_messages(messages: List[dict], users: List[dict],
                           viewer_id: Optional[int] = None,
                           storage=None,
                           chats: Optional[List[dict]] = None) -> bytes:
    chats = chats or []
    w = tl.Writer()
    w.u32(tl.MESSAGES_MESSAGES)
    w.u32(tl.VECTOR)
    w.i32(len(messages))
    for m in messages:
        w.raw(pack_message(m, storage))
    w.u32(tl.VECTOR)
    w.i32(0)  # topics
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(pack_user(
            u, is_self=viewer_id is not None and u['id'] == viewer_id,
            viewer_id=viewer_id, storage=storage))
    return w.bytes_out()


def pack_messages_slice(messages: List[dict], users: List[dict],
                        total_count: int,
                        viewer_id: Optional[int] = None,
                        storage=None,
                        offset_id: int = 0,
                        add_offset: int = 0,
                        chats: Optional[List[dict]] = None) -> bytes:
    chats = chats or []
    if (not messages
            or total_count <= 0
            or len(messages) >= total_count):
        return pack_messages_messages(
            messages, users, viewer_id, storage, chats=chats)
    w = tl.Writer()
    w.u32(tl.MESSAGES_SLICE)
    w.i32(0)
    w.i32(total_count)
    w.u32(tl.VECTOR)
    w.i32(len(messages))
    for m in messages:
        w.raw(pack_message(m, storage))
    w.u32(tl.VECTOR)
    w.i32(0)  # topics
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(pack_user(
            u, is_self=viewer_id is not None and u['id'] == viewer_id,
            viewer_id=viewer_id, storage=storage))
    return w.bytes_out()


def pack_update_stars_balance(balance: int) -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_STARS_BALANCE)
    sw = tl.Writer()
    sw.u32(0xbbb6b4a3)  # starsAmount
    sw.i64(balance)
    sw.i32(0)
    w.raw(sw.bytes_out())
    return w.bytes_out()


def pack_user_stars_balance(storage, user_id: int) -> bytes:
    return pack_update_stars_balance(storage.get_client_stars_balance(user_id))


def pack_update_ton_balance(nanos: int) -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_STARS_BALANCE)
    sw = tl.Writer()
    sw.u32(0x74aee3e0)  # starsTonAmount
    sw.i64(int(nanos))
    w.raw(sw.bytes_out())
    return w.bytes_out()


def pack_user_ton_balance(storage, user_id: int) -> bytes:
    return pack_update_ton_balance(storage.get_ton_balance(user_id))


def pack_update_new_message(msg: dict, pts: int, storage=None, *,
                            pts_count: int = 1) -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_NEW_MESSAGE)
    w.raw(pack_message(msg, storage))
    w.i32(pts)
    w.i32(int(pts_count))
    return w.bytes_out()


def pack_affected_history(pts: int, pts_count: int = 1,
                          offset: int = 0) -> bytes:
    w = tl.Writer()
    w.u32(tl.MESSAGES_AFFECTED_HISTORY)
    w.i32(int(pts))
    w.i32(int(pts_count))
    w.i32(int(offset))
    return w.bytes_out()


def pack_affected_messages(pts: int, pts_count: int) -> bytes:
    w = tl.Writer()
    w.u32(tl.MESSAGES_AFFECTED_MESSAGES)
    w.i32(int(pts))
    w.i32(int(pts_count))
    return w.bytes_out()


def pack_update_read_history_outbox(peer_id: int, max_id: int,
                                    pts: int, *, pts_count: int = 0) -> bytes:
    w = tl.Writer()
    w.u32(0x2f2f21bf)  # updateReadHistoryOutbox
    w.raw(pack_peer_user(peer_id))
    w.i32(max_id)
    w.i32(pts)
    w.i32(int(pts_count))
    return w.bytes_out()


def pack_update_read_history_inbox(peer_id: int, max_id: int, pts: int,
                                    unread: int = 0, *,
                                    pts_count: int = 0) -> bytes:
    w = tl.Writer()
    w.u32(0x9e84bc99)  # updateReadHistoryInbox
    w.i32(0)
    w.raw(pack_peer_user(peer_id))
    w.i32(max_id)
    w.i32(unread)
    w.i32(pts)
    w.i32(int(pts_count))
    return w.bytes_out()



def pack_message_delivery_update_bytes(msg: dict, pts: int, storage=None, *,
                                       dialog_peer_id: int,
                                       unread: int = 0) -> List[bytes]:
    """Single updateNewMessage — bundled read-history desyncs client PTS."""
    return [pack_update_new_message(msg, pts, storage, pts_count=1)]


def pack_update_edit_message(msg: dict, pts: int, storage=None, *,
                             pts_count: int = 1) -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_EDIT_MESSAGE)
    w.raw(pack_message(msg, storage))
    w.i32(int(pts))
    w.i32(int(pts_count))
    return w.bytes_out()


def pack_message_delivery_updates(msg: dict, pts: int, users: List[dict],
                                  viewer_id: int, storage=None, *,
                                  dialog_peer_id: int,
                                  unread: int = 0) -> bytes:
    return pack_updates(
        pack_message_delivery_update_bytes(
            msg, pts, storage, dialog_peer_id=dialog_peer_id, unread=unread),
        users, user_id=viewer_id, storage=storage, increment_seq=False)


def pack_updates(new_updates: List[bytes], users: List[dict],
                 user_id: Optional[int] = None,
                 storage=None, increment_seq: bool = True,
                 chats: Optional[List[dict]] = None) -> bytes:
    seq = 0
    if increment_seq and storage is not None and user_id is not None:
        seq = storage.increment_seq(user_id)
    chats = chats or []
    w = tl.Writer()
    w.u32(tl.UPDATES)
    w.u32(tl.VECTOR)
    w.i32(len(new_updates))
    for upd in new_updates:
        w.raw(upd)
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(pack_user(
            u,
            is_self=user_id is not None and int(u['id']) == int(user_id),
            viewer_id=user_id,
            storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(pack_channel(ch, user_id, storage=storage))
    w.i32(int(time.time()))
    w.i32(seq)
    return w.bytes_out()


def pack_update_short_sent_message(msg: dict, pts: int,
                                   entities: Optional[List[dict]] = None,
                                   storage=None) -> bytes:
    """updateShortSentMessage — TD applies random_id from RPC context."""
    if isinstance(entities, str):
        entities = json.loads(entities) if entities else []
    elif entities is None:
        entities = detect_text_entities(msg.get('text') or '') if msg.get('text') else []
    else:
        entities = list(entities)

    flags = 1 << 1  # out
    media_bytes = b''
    media_doc_id = int(msg.get('media_document_id') or 0)
    media_photo_id = int(msg.get('media_photo_id') or 0)
    poll_raw = msg.get('media_poll_json')
    if poll_raw and storage is not None:
        from handlers.polls import parse_poll_json, pack_message_media_poll
        poll_data = parse_poll_json(poll_raw)
        if poll_data:
            media_bytes = pack_message_media_poll(
                storage, poll_data, int(msg.get('peer_id') or 0),
                int(msg['id']), viewer_id=int(msg.get('owner_id') or 0))
            if media_bytes:
                flags |= (1 << 9)
    elif media_photo_id and storage is not None:
        from handlers.media import pack_message_media_photo
        photo_row = storage.get_photo_by_id(media_photo_id)
        if photo_row:
            media_bytes = pack_message_media_photo(photo_row)
            if media_bytes:
                flags |= (1 << 9)
    elif media_doc_id and storage is not None:
        from handlers.stickers import pack_document
        doc_row = storage.get_sticker_document_by_id(media_doc_id)
        if doc_row:
            ss = storage.get_sticker_set_by_id(doc_row['set_id'])
            media_bytes = pack_message_media_document(
                doc_row, ss, int(msg.get('date') or 0) or None)
            if media_bytes:
                flags |= (1 << 9)
    if entities:
        flags |= (1 << 7)

    w = tl.Writer()
    w.u32(tl.UPDATE_SHORT_SENT_MESSAGE)
    w.i32(flags)
    w.i32(msg['id'])
    w.i32(pts)
    w.i32(1)
    w.i32(msg['date'])
    if media_bytes:
        w.raw(media_bytes)
    if entities:
        w.raw(pack_entities(entities, msg['text']))
    return w.bytes_out()


def pack_send_message_updates(sender_msg: dict, random_id: int,
                              users: List[dict], pts: int,
                              extra_messages: Optional[List[tuple]] = None,
                              storage=None,
                              extra_updates: Optional[List[bytes]] = None
                              ) -> bytes:
    """Sender ack for messages.sendMessage.

    Plain text: updateShortSentMessage.
    Media / extras: updateMessageID + updateNewMessage.
    """
    # Android clients can keep the outgoing bubble in "Updating" state when
    # only updateShortSentMessage is returned. Always return full updates with
    # updateMessageID + updateNewMessage to deterministically resolve random_id.

    w_mid = tl.Writer()
    w_mid.u32(tl.UPDATE_MESSAGE_ID)
    w_mid.i32(sender_msg['id'])
    w_mid.i64(random_id)

    peer_id = int(sender_msg.get('peer_id') or 0)
    updates = [w_mid.bytes_out()]
    updates.extend(extra_updates or [])
    updates.extend(pack_message_delivery_update_bytes(
        sender_msg, pts, storage, dialog_peer_id=peer_id))
    for msg, msg_pts in (extra_messages or []):
        updates.extend(pack_message_delivery_update_bytes(
            msg, msg_pts, storage,
            dialog_peer_id=int(msg.get('peer_id') or peer_id)))
    return pack_updates(
        updates, users, user_id=sender_msg['owner_id'],
        storage=storage, increment_seq=True)


def pack_update_delete_messages(msg_ids: List[int], pts: int,
                                pts_count: int) -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_DELETE_MESSAGES)
    w.u32(tl.VECTOR)
    w.i32(len(msg_ids))
    for mid in msg_ids:
        w.i32(int(mid))
    w.i32(int(pts))
    w.i32(int(pts_count))
    return w.bytes_out()


def pack_update_short_wrapper(update_bytes: bytes) -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATE_SHORT)
    w.raw(update_bytes)
    w.i32(int(time.time()))
    return w.bytes_out()


def pack_inbox_push_updates(recipient_msg: dict, users: List[dict], pts: int,
                            storage=None, *, unread: Optional[int] = None) -> bytes:
    """Push incoming message — full updates container for reliable real-time delivery."""
    owner_id = int(recipient_msg['owner_id'])
    updates = [
        pack_update_new_message(recipient_msg, pts, storage, pts_count=1),
    ]
    return pack_updates(
        updates, users, user_id=owner_id, storage=storage, increment_seq=True)


def pack_edit_message_push(msg: dict, pts: int, users: List[dict],
                           storage=None) -> bytes:
    owner_id = int(msg['owner_id'])
    return pack_updates(
        [pack_update_edit_message(msg, pts, storage, pts_count=1)],
        users, user_id=owner_id, storage=storage, increment_seq=True)


def pack_update_short_message(msg: dict, pts: int) -> bytes:
    """updateShortMessage — lightweight push for private chats."""
    entities = msg.get('entities') or []
    if isinstance(entities, str):
        entities = json.loads(entities)

    from_id = msg['from_id']
    is_out = _message_is_out(msg)
    chat_peer = msg['peer_id'] if is_out else from_id

    flags = 0
    if is_out:
        flags |= (1 << 1)
    if entities:
        flags |= (1 << 7)

    w = tl.Writer()
    w.u32(tl.UPDATE_SHORT_MESSAGE)
    w.i32(flags)
    w.i32(msg['id'])
    w.i64(chat_peer)
    w.string(msg['text'])
    w.i32(pts)
    w.i32(1)
    w.i32(msg['date'])
    if entities:
        w.raw(pack_entities(entities, msg['text']))
    return w.bytes_out()


def pack_incoming_updates(recipient_msg: dict, users: List[dict],
                          pts: int, storage=None) -> bytes:
    return pack_inbox_push_updates(recipient_msg, users, pts, storage)


def pack_incoming_push(recipient_msg: dict, users: List[dict], pts: int,
                       storage=None) -> bytes:
    """Always push full updates with users so the client can render immediately."""
    return pack_inbox_push_updates(recipient_msg, users, pts, storage)


def pack_update_short(msg: dict, pts: int, users: List[dict],
                      storage=None) -> bytes:
    """Full updates container (not updateShortMessage) with users included."""
    return pack_inbox_push_updates(msg, users, pts, storage)


def pack_updates_difference(messages: List[dict], users: List[dict],
                            pts: int, storage=None, *,
                            viewer_id: Optional[int] = None) -> bytes:
    packed_messages = []
    for m in messages:
        try:
            packed_messages.append(pack_message(m, storage))
        except (KeyError, ValueError, TypeError) as exc:
            log.warning('skip message %s in updates.difference: %s',
                        m.get('id'), exc)
    st = (storage.get_user_state(viewer_id) if storage and viewer_id
          else None)
    channels: List[dict] = []
    if storage is not None:
        seen_channels = set()
        for m in messages:
            peer_id = int(m.get('peer_id') or 0)
            if peer_id >= config.CHANNEL_ID_MIN and peer_id not in seen_channels:
                ch = storage.get_channel(peer_id)
                if ch:
                    channels.append(ch)
                    seen_channels.add(peer_id)
    w = tl.Writer()
    w.u32(tl.UPDATES_DIFFERENCE)
    w.u32(tl.VECTOR)
    w.i32(len(packed_messages))
    for pm in packed_messages:
        w.raw(pm)
    w.u32(tl.VECTOR)
    w.i32(len(channels))
    for ch in channels:
        w.raw(pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(pack_user(
            u,
            is_self=viewer_id is not None and int(u['id']) == int(viewer_id),
            viewer_id=viewer_id,
            storage=storage))
    w.u32(tl.UPDATES_STATE)
    w.i32(pts)
    w.i32(int(st['qts']) if st else 0)
    w.i32(int(st['date']) if st else int(time.time()))
    w.i32(int(st['seq']) if st else 0)
    w.i32(0)
    return w.bytes_out()


def pack_contacts_found(users: List[dict], storage=None,
                        channels: Optional[List[dict]] = None,
                        viewer_id: Optional[int] = None) -> bytes:
    channels = channels or []
    w = tl.Writer()
    w.u32(tl.CONTACTS_FOUND)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(len(users) + len(channels))
    for u in users:
        w.raw(pack_peer_user(u['id']))
    for ch in channels:
        w.raw(pack_peer_channel(int(ch['id'])))
    w.u32(tl.VECTOR)
    w.i32(len(channels))
    for ch in channels:
        w.raw(pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(pack_user(
            u, viewer_id=viewer_id, storage=storage,
            is_self=viewer_id is not None and int(u['id']) == int(viewer_id)))
    return w.bytes_out()


def pack_contacts_resolved_peer(u: dict, storage=None,
                                viewer_id: Optional[int] = None) -> bytes:
    w = tl.Writer()
    w.u32(tl.CONTACTS_RESOLVED_PEER)
    w.raw(pack_peer_user(u['id']))
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(pack_user(
        u, viewer_id=viewer_id, storage=storage,
        is_self=viewer_id is not None and int(u['id']) == int(viewer_id)))
    return w.bytes_out()


def pack_contacts_resolved_peer_channel(ch: dict, viewer_id: Optional[int] = None,
                                        storage=None) -> bytes:
    w = tl.Writer()
    w.u32(tl.CONTACTS_RESOLVED_PEER)
    w.raw(pack_peer_channel(int(ch['id'])))
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def pack_auth_authorization(u: dict, storage=None) -> bytes:
    w = tl.Writer()
    w.u32(tl.AUTH_AUTHORIZATION)
    w.i32(0)
    w.raw(pack_user(u, is_self=True, storage=storage))
    return w.bytes_out()


def pack_auth_sign_up_required() -> bytes:
    w = tl.Writer()
    w.u32(tl.AUTH_AUTHORIZATION_SIGN_UP_REQUIRED)
    w.i32(0)
    return w.bytes_out()


def pack_json_bool(value: bool) -> bytes:
    w = tl.Writer()
    w.u32(tl.JSON_BOOL)
    w.bool(value)
    return w.bytes_out()


def pack_json_string(value: str) -> bytes:
    w = tl.Writer()
    w.u32(tl.JSON_STRING)
    w.string(value)
    return w.bytes_out()


def pack_json_number(value: float) -> bytes:
    import struct
    w = tl.Writer()
    w.u32(tl.JSON_NUMBER)
    w.raw(struct.pack('<d', float(value)))
    return w.bytes_out()


def pack_json_array(items: list) -> bytes:
    w = tl.Writer()
    w.u32(tl.JSON_ARRAY)
    w.u32(tl.VECTOR)
    w.i32(len(items))
    for item in items:
        if isinstance(item, str):
            w.raw(pack_json_string(item))
        elif isinstance(item, bool):
            w.raw(pack_json_bool(item))
        elif isinstance(item, (int, float)):
            w.raw(pack_json_number(item))
    return w.bytes_out()


def pack_json_object(entries: dict) -> bytes:
    w = tl.Writer()
    w.u32(tl.JSON_OBJECT)
    w.u32(tl.VECTOR)
    w.i32(len(entries))
    for key, val in entries.items():
        w.u32(tl.JSON_OBJECT_VALUE)
        w.string(key)
        w.raw(val)
    return w.bytes_out()


def pack_json_object_empty() -> bytes:
    return pack_json_object({})


def build_app_config_entries(user: Optional[dict] = None,
                             storage=None) -> dict:
    """Client config from help.getAppConfig — corefork.telegram.org/api/config."""
    import config
    entries = {
        # Polls — required for attach-menu Poll item (pollOptionsLimit etc.)
        'poll_answers_max': pack_json_number(12),
        'poll_close_period_max': pack_json_number(2_628_000),
        'poll_answer_delete_period': pack_json_number(300),
        'poll_countries_max': pack_json_number(12),
        'premium_purchase_blocked': pack_json_bool(False),
        'stars_purchase_blocked': pack_json_bool(False),
        'stargifts_enabled': pack_json_bool(True),
        'stars_gifts_enabled': pack_json_bool(True),
        'stargifts_blocked': pack_json_bool(False),
        'enable_gifts_in_profile': pack_json_bool(True),
        'stargifts_message_length_max': pack_json_number(255),
        'stargifts_convert_period_max': pack_json_number(90 * 86400),
        'stargifts_pinned_to_top_limit': pack_json_number(6),
        'gift_resale_star_count_min': pack_json_number(config.STARGIFT_RESALE_STARS_MIN),
        'gift_resale_star_count_max': pack_json_number(config.STARGIFT_RESALE_STARS_MAX),
        'stars_stargift_resale_amount_min': pack_json_number(config.STARGIFT_RESALE_STARS_MIN),
        'stars_stargift_resale_amount_max': pack_json_number(config.STARGIFT_RESALE_STARS_MAX),
        'ton_stargift_resale_amount_min': pack_json_number(config.STARGIFT_RESALE_TON_MIN),
        'ton_stargift_resale_amount_max': pack_json_number(config.STARGIFT_RESALE_TON_MAX),
        'stars_stargift_resale_commission_permille': pack_json_number(
            config.STARGIFT_RESALE_COMMISSION_PERMILLE),
        'ton_stargift_resale_commission_permille': pack_json_number(
            config.STARGIFT_RESALE_COMMISSION_PERMILLE),
        'stars_usd_sell_rate_x1000': pack_json_number(1500),
        'ton_usd_rate': pack_json_number(
            config.TON_USD_RATE if config.TON_USD_RATE > 0 else 3.0),
        'stars_rating_learnmore_url': pack_json_string(
            config.STARS_RATING_LEARNMORE_URL),
        'stars_paid_messages_available': pack_json_bool(
            config.STARS_PAID_MESSAGES_AVAILABLE),
        'stars_paid_message_amount_max': pack_json_number(
            config.STARS_PAID_MESSAGE_AMOUNT_MAX),
        'stars_paid_message_amount_default': pack_json_number(
            config.STARS_PAID_MESSAGE_AMOUNT_DEFAULT),
        'stars_paid_message_commission_permille': pack_json_number(
            config.STARS_PAID_MESSAGE_COMMISSION_PERMILLE),
        'collectible_usernames_available': pack_json_bool(True),
        'usernames_order_available': pack_json_bool(True),
        'fragment_prefixes': pack_json_array(['888', '9']),
        'fragment_prefix': pack_json_string('https://fragment.com/'),
        'fragment_link': pack_json_string('https://fragment.com/'),
        'fragment_username_purchase_url': pack_json_string(
            'https://fragment.com/username/{username}'),
        'fragment_phone_purchase_url': pack_json_string(
            'https://fragment.com/number/{phone}'),
        'whitelisted_domains': pack_json_array([
            'telegram.dog',
            'telegram.me',
            'telegram.org',
            't.me',
            'telesco.pe',
            'fragment.com',
            'translations.telegram.org',
        ]),
        'ton_blockchain_explorer_url': pack_json_string('https://t.me/nft/'),
        'inapp_update_check_delay': pack_json_number(365 * 86400),
    }
    if config.TON_TOPUP_URL:
        entries['ton_topup_url'] = pack_json_string(config.TON_TOPUP_URL)
    if storage is not None and user and user.get('frozen'):
        freeze_doc_id = storage.freeze_emoji_document_id()
        if freeze_doc_id:
            try:
                from shop_webapp.emoji_import import ensure_custom_emoji_document
                ensure_custom_emoji_document(storage, freeze_doc_id)
            except Exception:
                pass
            entries['freeze_emoji_document_id'] = pack_json_number(freeze_doc_id)
        settings = storage.get_freeze_settings() or {}
        frozen_at = int(user.get('frozen_at') or 0)
        frozen_until = int(user.get('frozen_until') or 0)
        if frozen_at:
            entries['freeze_since_date'] = pack_json_number(frozen_at)
        if frozen_until:
            entries['freeze_until_date'] = pack_json_number(frozen_until)
        appeal = (settings.get('appealUrl') or 'https://t.me/SpamBot').strip()
        if appeal:
            entries['freeze_appeal_url'] = pack_json_string(appeal)
    return entries


def app_config_hash(entries: dict) -> int:
    import zlib
    parts = []
    for key in sorted(entries):
        val = entries[key]
        parts.append(f'{key}={val.hex()}')
    return zlib.crc32('\n'.join(parts).encode('utf-8')) & 0x7FFFFFFF


def pack_app_config_json(user: Optional[dict] = None,
                         storage=None) -> bytes:
    return pack_json_object(build_app_config_entries(user, storage))


def skip_input_notify_peer(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0x193b4417:  # inputNotifyUsers
        return
    if cid == 0x4a95e84e:  # inputNotifyChats
        return
    if cid == 0xb1db7c7e:  # inputNotifyBroadcasts
        return
    if cid == 0xb8bc5b0c:  # inputNotifyPeer
        skip_input_peer(r)
        return
    if cid == 0x5c467992:  # inputNotifyForumTopic
        skip_input_peer(r)
        r.i32()
        return
    raise ValueError(f'unsupported InputNotifyPeer {cid:#010x}')


def skip_input_peer(r: tl.Reader) -> None:
    cid = r.u32()
    if cid in (tl.INPUT_PEER_EMPTY, tl.INPUT_PEER_SELF):
        return
    if cid == tl.INPUT_PEER_USER:
        r.i64()
        r.i64()
        return
    if cid == tl.INPUT_PEER_CHAT:
        r.i64()
        return
    if cid == tl.INPUT_PEER_CHANNEL:
        r.i64()
        r.i64()
        return
    if cid == tl.INPUT_PEER_USER_FROM_MESSAGE:
        skip_input_peer(r)
        r.i32()
        r.i64()
        return
    if cid == tl.INPUT_PEER_CHANNEL_FROM_MESSAGE:
        skip_input_peer(r)
        r.i32()
        r.i64()
        return
    raise ValueError(f'unsupported InputPeer {cid:#010x}')


def parse_input_peer(r: tl.Reader,
                     self_user_id: Optional[int] = None) -> Optional[int]:
    cid = r.u32()
    if cid == tl.INPUT_PEER_EMPTY:
        return None
    if cid == tl.INPUT_PEER_SELF:
        return self_user_id
    if cid == tl.INPUT_PEER_USER:
        uid = r.i64()
        r.i64()
        return uid
    if cid == tl.INPUT_PEER_CHAT:
        r.i64()
        return None
    if cid == tl.INPUT_PEER_CHANNEL:
        cid_val = r.i64()
        r.i64()
        return cid_val
    if cid == tl.INPUT_PEER_USER_FROM_MESSAGE:
        skip_input_peer(r)
        r.i32()
        return r.i64()
    if cid == tl.INPUT_PEER_CHANNEL_FROM_MESSAGE:
        skip_input_peer(r)
        r.i32()
        return r.i64()
    raise ValueError(f'unsupported InputPeer {cid:#010x}')


def parse_input_user(r: tl.Reader, storage,
                     self_user_id: Optional[int] = None) -> Optional[dict]:
    cid = r.u32()
    if cid == tl.INPUT_USER_SELF:
        if self_user_id:
            return storage.get_user_by_id(self_user_id)
        return None
    if cid == tl.INPUT_USER_EMPTY:
        return None
    if cid == tl.INPUT_USER:
        uid = r.i64()
        r.i64()
        return storage.get_user_by_id(uid)
    if cid == 0x1da448e2:  # inputUserFromMessage
        skip_input_peer(r)
        r.i32()
        uid = r.i64()
        return storage.get_user_by_id(uid)
    raise ValueError(f'unsupported InputUser {cid:#010x}')


def pack_empty_updates() -> bytes:
    w = tl.Writer()
    w.u32(tl.UPDATES)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.i32(int(time.time()))
    w.i32(0)
    return w.bytes_out()


def parse_reply_to(r: tl.Reader) -> Optional[dict]:
    cid = r.u32()
    if cid == tl.INPUT_REPLY_TO_MESSAGE:
        flags = r.i32()
        reply_to_msg_id = r.i32()
        result: dict = {'reply_to_msg_id': reply_to_msg_id}
        if flags & 1:
            r.i32()
        if flags & 2:
            result['reply_to_peer_id'] = parse_input_peer(r)
        if flags & 4:
            r.string()
        if flags & 8:
            skip_entities(r)
        if flags & 16:
            r.i32()
        if flags & 32:
            result['monoforum_peer_id'] = parse_input_peer(r)
        if flags & 64:
            r.i32()
        if flags & 128:
            r.tl_bytes()
        return result
    if cid == tl.INPUT_REPLY_TO_STORY:
        skip_input_peer(r)
        r.i32()
    elif cid == tl.INPUT_REPLY_TO_MONOFORUM:
        return {'monoforum_peer_id': parse_input_peer(r)}
    return None


def skip_input_reply_to(r: tl.Reader) -> None:
    parse_reply_to(r)


def skip_entities(r: tl.Reader) -> None:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    for _ in range(count):
        skip_entity(r)


def skip_entity(r: tl.Reader) -> None:
    cid = r.u32()
    r.i32()  # offset
    r.i32()  # length
    if cid == tl.MESSAGE_ENTITY_TEXT_URL:
        r.string()
    elif cid == tl.MESSAGE_ENTITY_PRE:
        r.string()  # language
    elif cid == tl.MESSAGE_ENTITY_CUSTOM_EMOJI:
        r.i64()


def parse_entities(r: tl.Reader) -> List[dict]:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    entities = []
    for _ in range(count):
        cid = r.u32()
        offset = r.i32()
        length = r.i32()
        if cid == tl.MESSAGE_ENTITY_SPOILER:
            entities.append({'type': 'spoiler', 'offset': offset, 'length': length})
        elif cid == tl.MESSAGE_ENTITY_BOLD:
            entities.append({'type': 'bold', 'offset': offset, 'length': length})
        elif cid == tl.MESSAGE_ENTITY_CODE:
            entities.append({'type': 'code', 'offset': offset, 'length': length})
        elif cid == tl.MESSAGE_ENTITY_MENTION:
            entities.append({'type': 'mention', 'offset': offset, 'length': length})
        elif cid == tl.MESSAGE_ENTITY_URL:
            entities.append({'type': 'url', 'offset': offset, 'length': length})
        elif cid == tl.MESSAGE_ENTITY_BOT_COMMAND:
            entities.append({'type': 'bot_command', 'offset': offset, 'length': length})
        elif cid == tl.MESSAGE_ENTITY_TEXT_URL:
            url = r.string()
            entities.append({
                'type': 'text_url', 'offset': offset, 'length': length, 'url': url,
            })
        elif cid == tl.MESSAGE_ENTITY_PRE:
            r.string()
            entities.append({'type': 'pre', 'offset': offset, 'length': length})
        elif cid == tl.MESSAGE_ENTITY_CUSTOM_EMOJI:
            document_id = r.i64()
            entities.append({
                'type': 'custom_emoji',
                'offset': offset,
                'length': length,
                'document_id': document_id,
            })
    return entities


def pack_messages_peer_dialogs(dialogs: List[dict], messages: List[dict],
                               users: List[dict], pts: int,
                               storage=None, chats: Optional[List[dict]] = None,
                               viewer_id: Optional[int] = None) -> bytes:
    chats = chats or []
    w = tl.Writer()
    w.u32(0x3371c354)  # messages.peerDialogs
    w.u32(tl.VECTOR)
    w.i32(len(dialogs))
    for d in dialogs:
        w.raw(pack_dialog(d))
    w.u32(tl.VECTOR)
    w.i32(len(messages))
    for m in messages:
        w.raw(pack_message(m, storage))
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(pack_user(
            u, viewer_id=viewer_id, storage=storage,
            is_self=viewer_id is not None and int(u['id']) == int(viewer_id)))
    w.u32(tl.UPDATES_STATE)
    w.i32(pts)
    w.i32(0)
    w.i32(int(time.time()))
    w.i32(0)
    w.i32(0)
    return w.bytes_out()


def skip_reply_markup(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0xa03e5b85:  # replyKeyboardHide
        r.i32()
    elif cid == 0x85dd99d1:  # replyKeyboardForceReply
        r.i32()
    elif cid == 0x48a30254:  # replyInlineMarkup
        assert r.u32() == tl.VECTOR
        n = r.i32()
        for _ in range(n):
            r.i32()  # flags per row — skip row minimally
            assert r.u32() == tl.VECTOR
            m = r.i32()
            for _ in range(m):
                r.u32()
                r.string()
                if True:
                    r.i32()  # optional fields — best effort
    # ignore unknown markup types


def parse_input_document(r: tl.Reader) -> Optional[int]:
    cid = r.u32()
    if cid == 0x1abfb575:  # inputDocument
        doc_id = r.i64()
        r.i64()  # access_hash
        r.tl_bytes()  # file_reference
        return doc_id
    if cid == 0x72f0eaae:  # inputDocumentEmpty
        return None
    raise ValueError(f'unsupported InputDocument {cid:#010x}')


def skip_input_photo(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0x1cd7bf0d:  # inputPhotoEmpty
        return
    if cid == 0x3bb3b94a:  # inputPhoto
        r.i64()
        r.i64()
        r.tl_bytes()
        return
    raise ValueError(f'unsupported InputPhoto {cid:#010x}')


def skip_video_size(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0xde33b094:  # videoSize
        flags = r.i32()
        r.string()  # type
        r.i32()  # w
        r.i32()  # h
        r.i32()  # size
        if flags & 1:
            r.f64()  # video_start_ts
        return
    if cid == 0xf85c413c:  # videoSizeEmojiMarkup
        r.i64()  # emoji_id
        assert r.u32() == tl.VECTOR
        n = r.i32()
        for _ in range(n):
            r.i32()  # background_colors
        return
    if cid == 0xda082fef:  # videoSizeStickerMarkup
        _skip_input_sticker_set_inline(r)
        r.i64()  # sticker_id
        assert r.u32() == tl.VECTOR
        n = r.i32()
        for _ in range(n):
            r.i32()
        return
    raise ValueError(f'unsupported VideoSize {cid:#010x}')


def _skip_input_sticker_set_inline(r: tl.Reader) -> None:
    cid = r.u32()
    if cid in (
            0xffb62b95, 0x028703c8, 0x0cde3739, 0xc88b3b02,
            0x04c4d4ce, 0x29d0f5ee, 0x44c1f8e9, 0x49748553,
            0x1cf671a0):
        return
    if cid == 0x9de7a269:  # inputStickerSetID
        r.i64()
        r.i64()
        return
    if cid == 0x861cc8a0:  # inputStickerSetShortName
        r.string()
        return
    if cid == 0xe67f520e:  # inputStickerSetDice
        r.string()
        return
    raise ValueError(f'unsupported InputStickerSet {cid:#010x}')


def parse_input_media(r: tl.Reader) -> Optional[int]:
    cid = r.u32()
    if cid == 0xa8763ab5:  # inputMediaDocument
        flags = r.i32()
        doc_id = parse_input_document(r)
        if flags & 1:
            r.i32()  # ttl_seconds
        if flags & 2:
            r.string()  # query
        if flags & 8:
            skip_input_photo(r)  # video_cover flags.3
        if flags & 16:
            r.i32()  # video_timestamp
        return doc_id
    raise ValueError(f'unsupported InputMedia {cid:#010x}')


def message_has_custom_emoji(entities: List[dict]) -> bool:
    return any(e.get('type') == 'custom_emoji' for e in entities)


def parse_suggested_post(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid != 0x0e8e37e5:
        raise ValueError(f'unsupported SuggestedPost {cid:#010x}')
    flags = r.i32()
    data: dict = {}
    if flags & 1:
        data['schedule_date'] = int(r.i32())
    if flags & 2:
        data['accepted'] = True
    if flags & 4:
        data['rejected'] = True
    if flags & 8:
        if r.u32() != 0xbbb6b4a3:
            raise ValueError('unsupported StarsAmount in SuggestedPost')
        data['price_stars'] = int(r.i64())
        r.i32()
    return data


def pack_suggested_post(data: dict) -> bytes:
    flags = 0
    schedule_date = int(data.get('schedule_date') or 0)
    price_stars = int(data.get('price_stars') or 0)
    if schedule_date:
        flags |= 1
    if data.get('accepted'):
        flags |= 2
    if data.get('rejected'):
        flags |= 4
    if price_stars > 0:
        flags |= 8
    w = tl.Writer()
    w.u32(0x0e8e37e5)
    w.i32(flags)
    if flags & 1:
        w.i32(schedule_date)
    if flags & 8:
        w.u32(0xbbb6b4a3)
        w.i64(price_stars)
        w.i32(0)
    return w.bytes_out()


def skip_suggested_post(r: tl.Reader) -> None:
    parse_suggested_post(r)


def skip_messages_filter(r: tl.Reader) -> None:
    cid = r.u32()
    if cid in (0x57e4f166, 0x9609a51c, 0x65bc039):  # empty / photos / video
        return
    if cid in (0x54a7e8, 0x9fc2e796, 0x33344b48):  # document / url / gif
        return
    if cid in (0x3751b49e, 0x3a20ecb8, 0x4389bb86):  # voice / music / chatPhotos
        return
    if cid in (0x7a7c717a, 0xb54989e2, 0x71fda418):  # phoneCalls / round / myMentions
        return
    if cid in (0x63247e24, 0x42781908):  # geo / contacts
        return
    if cid == 0xcd77d957:  # channelMessagesFilter
        r.i32()
        return


def skip_input_quick_reply_shortcut(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0x24596d41:  # inputQuickReplyShortcut
        r.string()
        return
    if cid == 0x01190cf1:  # inputQuickReplyShortcutId
        r.i32()
        return
    if cid == 0x01190cf0:  # inputQuickReplyShortcutEmpty (legacy clients)
        return


def _skip_input_rich_files(r: tl.Reader) -> None:
    if r.u32() != tl.VECTOR:
        return
    count = r.i32()
    for _ in range(count):
        cid = r.u32()
        if cid == 0x7c964660:  # inputRichFileLocation
            r.i64()
            r.i64()
            r.i32()
            r.i32()
        elif cid == 0x8f1e3487:  # inputRichFileUploaded
            r.i64()
            r.i32()
            r.string()
            r.i32()
            r.i32()


def _skip_page_block(r: tl.Reader, cid: int) -> None:
    if cid in (0x70dd0cec, 0x175015c5, 0xbf4dea82, 0x8ffa9a1f):
        r.string()
        return
    if cid == 0x037c0be5:  # pageBlockList
        if r.u32() != tl.VECTOR:
            return
        for _ in range(r.i32()):
            _skip_page_block(r, r.u32())
        return
    if cid == 0x31f9590d:  # pageBlockOrderedList
        r.i32()
        if r.u32() != tl.VECTOR:
            return
        for _ in range(r.i32()):
            r.string()
        return
    if cid == 0x9a8ae1e1:  # pageBlockBlockquote
        flags = r.i32()
        r.string()
        if flags & 1:
            r.string()
        return
    if cid == 0x48870999:  # pageBlockPreformatted
        r.i32()
        r.string()
        return
    if cid == 0xef1751b5:  # pageBlockDivider
        return
    if cid == 0x804361ea:  # pageBlockPhoto
        flags = r.i32()
        r.i64()
        r.string()
        if flags & 1:
            if r.u32() != tl.VECTOR:
                return
            for _ in range(r.i32()):
                r.i32()
        return
    if cid == 0x9a986ae0:  # pageBlockEmbed
        flags = r.i32()
        r.i32()
        r.i32()
        r.i32()
        r.i32()
        if flags & 1:
            r.i64()
        if flags & 2:
            r.i32()
        if flags & 4:
            r.i32()
        if flags & 8:
            r.i32()
        return


def _extract_page_block_text(r: tl.Reader, cid: int) -> str:
    if cid in (0x70dd0cec, 0x175015c5, 0xbf4dea82, 0x8ffa9a1f):
        return r.string()
    _skip_page_block(r, cid)
    return ''


def _parse_input_rich_message(r: tl.Reader) -> tuple[str, list]:
    cid = r.u32()
    if cid == 0xdacb836a:  # inputRichMessageHTML
        flags = r.i32()
        html = r.string()
        if flags & (1 << 2):
            _skip_input_rich_files(r)
        return _html_to_plain_text(html), []
    if cid == 0x004b572c:  # inputRichMessageMarkdown
        flags = r.i32()
        markdown = r.string()
        if flags & (1 << 2):
            _skip_input_rich_files(r)
        return markdown, []
    if cid == 0xe4c449fc:  # inputRichMessage
        flags = r.i32()
        texts: list[str] = []
        if r.u32() == tl.VECTOR:
            for _ in range(r.i32()):
                bcid = r.u32()
                chunk = _extract_page_block_text(r, bcid)
                if chunk:
                    texts.append(chunk)
        if flags & (1 << 2):
            if r.u32() == tl.VECTOR:
                for _ in range(r.i32()):
                    r.i64()
                    r.i64()
        if flags & (1 << 3):
            if r.u32() == tl.VECTOR:
                for _ in range(r.i32()):
                    r.i64()
                    r.i64()
        if flags & (1 << 4):
            if r.u32() == tl.VECTOR:
                for _ in range(r.i32()):
                    _skip_input_user(r)
        return '\n'.join(texts), []
    return '', []


def _skip_input_user(r: tl.Reader) -> None:
    cid = r.u32()
    if cid in (0xf7c1b13f, 0xb98886cf):
        return
    if cid in (0xf21158c6, 0x1da448e2):
        r.i64()
        r.i64()
        if cid == 0x1da448e2:
            skip_input_peer(r)
            r.i32()


def _html_to_plain_text(html: str) -> str:
    if not html:
        return ''
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    return text.replace('&nbsp;', ' ').replace('&amp;', '&').strip()


def parse_send_message_tail(r: tl.Reader, flags: int) -> tuple[int, str, list, dict, Optional[dict]]:
    """Parse optional sendMessage tail."""
    allow_paid_stars = 0
    rich_text = ''
    rich_entities: list = []
    suggested_post: dict = {}
    shortcut: Optional[dict] = None
    if flags & (1 << 10):
        r.i32()  # schedule_date
    if flags & (1 << 24):
        r.i32()  # schedule_repeat_period
    if flags & (1 << 13):
        skip_input_peer(r)  # send_as
    if flags & (1 << 17):
        try:
            from handlers.quick_replies import parse_input_quick_reply_shortcut
            shortcut = parse_input_quick_reply_shortcut(r)
        except (ValueError, AssertionError):
            try:
                skip_input_quick_reply_shortcut(r)
            except (ValueError, AssertionError):
                pass
    if flags & (1 << 18):
        r.i64()  # effect
    if flags & (1 << 21):
        allow_paid_stars = r.i64()
    if flags & (1 << 22):
        try:
            suggested_post = parse_suggested_post(r)
        except ValueError:
            pass
    if flags & (1 << 23):
        try:
            rich_text, rich_entities = _parse_input_rich_message(r)
        except (ValueError, AssertionError):
            pass
    return allow_paid_stars, rich_text, rich_entities, suggested_post, shortcut


def unpack_send_message_tail(r: tl.Reader, flags: int) -> tuple[int, str, list, dict, Optional[dict]]:
    """Return sendMessage tail fields; tolerate older 3/4-value returns."""
    result = parse_send_message_tail(r, flags)
    if len(result) >= 5:
        return result[0], result[1], result[2], result[3], result[4]
    if len(result) == 4:
        return result[0], result[1], result[2], result[3], None
    if len(result) == 3:
        return result[0], result[1], result[2], {}, None
    return 0, '', [], {}, None


def _skip_input_rich_message(r: tl.Reader) -> None:
    _parse_input_rich_message(r)


def skip_send_message_tail(r: tl.Reader, flags: int) -> None:
    parse_send_message_tail(r, flags)
