"""Privacy settings — account.getPrivacy / account.setPrivacy / enforcement."""
from __future__ import annotations

import json
import logging
from typing import List, Optional

import config
from mtproto import tl
from handlers import packers

log = logging.getLogger(__name__)

# ── InputPrivacyKey ─────────────────────────────────────────────────────────
INPUT_PRIVACY_KEY_STATUS_TIMESTAMP = 0x4f96cb18
INPUT_PRIVACY_KEY_CHAT_INVITE = 0xbdfb0426
INPUT_PRIVACY_KEY_PHONE_CALL = 0xfabadc5f
INPUT_PRIVACY_KEY_PHONE_P2P = 0xdb9e70d2
INPUT_PRIVACY_KEY_FORWARDS = 0xa4dd4c08
INPUT_PRIVACY_KEY_PROFILE_PHOTO = 0x5719bacc
INPUT_PRIVACY_KEY_PHONE_NUMBER = 0x0352dafa
INPUT_PRIVACY_KEY_ADDED_BY_PHONE = 0xd1219bdd
INPUT_PRIVACY_KEY_VOICE_MESSAGES = 0xaee69d68
INPUT_PRIVACY_KEY_ABOUT = 0x3823cc40
INPUT_PRIVACY_KEY_BIRTHDAY = 0xd65a11cc
INPUT_PRIVACY_KEY_STAR_GIFTS_AUTO_SAVE = 0xe1732341
INPUT_PRIVACY_KEY_NO_PAID_MESSAGES = 0xbdc597b4
INPUT_PRIVACY_KEY_SAVED_MUSIC = 0x4dbe9226

# ── InputPrivacyRule ──────────────────────────────────────────────────────────
INPUT_PRIVACY_VALUE_ALLOW_CONTACTS = 0x0d09e07b
INPUT_PRIVACY_VALUE_ALLOW_ALL = 0x184b35ce
INPUT_PRIVACY_VALUE_ALLOW_USERS = 0x131cc67f
INPUT_PRIVACY_VALUE_DISALLOW_CONTACTS = 0x0ba52007
INPUT_PRIVACY_VALUE_DISALLOW_ALL = 0xd66b66c9
INPUT_PRIVACY_VALUE_DISALLOW_USERS = 0x90110467
INPUT_PRIVACY_VALUE_ALLOW_CHAT_PARTICIPANTS = 0x840649cf
INPUT_PRIVACY_VALUE_DISALLOW_CHAT_PARTICIPANTS = 0xe94f0f86
INPUT_PRIVACY_VALUE_ALLOW_CLOSE_FRIENDS = 0x2f453e49
INPUT_PRIVACY_VALUE_ALLOW_PREMIUM = 0x77cdc9f1
INPUT_PRIVACY_VALUE_ALLOW_BOTS = 0x5a4fcce5
INPUT_PRIVACY_VALUE_DISALLOW_BOTS = 0xc4e57915

# ── PrivacyRule (output) ──────────────────────────────────────────────────────
PRIVACY_VALUE_ALLOW_CONTACTS = 0xfffe1bac
PRIVACY_VALUE_ALLOW_ALL = 0x65427b82
PRIVACY_VALUE_ALLOW_USERS = 0xb8905fb2
PRIVACY_VALUE_DISALLOW_CONTACTS = 0xf888fa1a
PRIVACY_VALUE_DISALLOW_ALL = 0x8b73e763
PRIVACY_VALUE_DISALLOW_USERS = 0xe4621141
PRIVACY_VALUE_ALLOW_CHAT_PARTICIPANTS = 0x6b134e8e
PRIVACY_VALUE_DISALLOW_CHAT_PARTICIPANTS = 0x41c87565
PRIVACY_VALUE_ALLOW_CLOSE_FRIENDS = 0xf7e8d89b
PRIVACY_VALUE_ALLOW_PREMIUM = 0xece9814b
PRIVACY_VALUE_ALLOW_BOTS = 0x21461b5d
PRIVACY_VALUE_DISALLOW_BOTS = 0xf6a5f82f

ACCOUNT_PRIVACY_RULES = 0x50a04e45
ACCOUNT_GET_PRIVACY = 0xdadbc950
ACCOUNT_SET_PRIVACY = 0xc9f81ce8

ALL_PRIVACY_KEYS = {
    INPUT_PRIVACY_KEY_STATUS_TIMESTAMP,
    INPUT_PRIVACY_KEY_CHAT_INVITE,
    INPUT_PRIVACY_KEY_PHONE_CALL,
    INPUT_PRIVACY_KEY_PHONE_P2P,
    INPUT_PRIVACY_KEY_FORWARDS,
    INPUT_PRIVACY_KEY_PROFILE_PHOTO,
    INPUT_PRIVACY_KEY_PHONE_NUMBER,
    INPUT_PRIVACY_KEY_ADDED_BY_PHONE,
    INPUT_PRIVACY_KEY_VOICE_MESSAGES,
    INPUT_PRIVACY_KEY_ABOUT,
    INPUT_PRIVACY_KEY_BIRTHDAY,
    INPUT_PRIVACY_KEY_STAR_GIFTS_AUTO_SAVE,
    INPUT_PRIVACY_KEY_NO_PAID_MESSAGES,
    INPUT_PRIVACY_KEY_SAVED_MUSIC,
}


def _default_rules() -> List[dict]:
    return [{'type': 'allowAll'}]


def parse_input_privacy_rules(r: tl.Reader, storage) -> List[dict]:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    rules: List[dict] = []
    for _ in range(count):
        cid = r.u32()
        if cid == INPUT_PRIVACY_VALUE_ALLOW_ALL:
            rules.append({'type': 'allowAll'})
        elif cid == INPUT_PRIVACY_VALUE_DISALLOW_ALL:
            rules.append({'type': 'disallowAll'})
        elif cid == INPUT_PRIVACY_VALUE_ALLOW_CONTACTS:
            rules.append({'type': 'allowContacts'})
        elif cid == INPUT_PRIVACY_VALUE_DISALLOW_CONTACTS:
            rules.append({'type': 'disallowContacts'})
        elif cid == INPUT_PRIVACY_VALUE_ALLOW_CLOSE_FRIENDS:
            rules.append({'type': 'allowCloseFriends'})
        elif cid == INPUT_PRIVACY_VALUE_ALLOW_PREMIUM:
            rules.append({'type': 'allowPremium'})
        elif cid == INPUT_PRIVACY_VALUE_ALLOW_BOTS:
            rules.append({'type': 'allowBots'})
        elif cid == INPUT_PRIVACY_VALUE_DISALLOW_BOTS:
            rules.append({'type': 'disallowBots'})
        elif cid == INPUT_PRIVACY_VALUE_ALLOW_USERS:
            assert r.u32() == tl.VECTOR
            n = r.i32()
            user_ids = []
            for _ in range(n):
                u = packers.parse_input_user(r, storage)
                if u:
                    user_ids.append(int(u['id']))
            rules.append({'type': 'allowUsers', 'user_ids': user_ids})
        elif cid == INPUT_PRIVACY_VALUE_DISALLOW_USERS:
            assert r.u32() == tl.VECTOR
            n = r.i32()
            user_ids = []
            for _ in range(n):
                u = packers.parse_input_user(r, storage)
                if u:
                    user_ids.append(int(u['id']))
            rules.append({'type': 'disallowUsers', 'user_ids': user_ids})
        elif cid in (INPUT_PRIVACY_VALUE_ALLOW_CHAT_PARTICIPANTS,
                     INPUT_PRIVACY_VALUE_DISALLOW_CHAT_PARTICIPANTS):
            assert r.u32() == tl.VECTOR
            n = r.i32()
            chat_ids = [int(r.i64()) for _ in range(n)]
            if cid == INPUT_PRIVACY_VALUE_ALLOW_CHAT_PARTICIPANTS:
                rules.append({'type': 'allowChatParticipants', 'chat_ids': chat_ids})
            else:
                rules.append({'type': 'disallowChatParticipants', 'chat_ids': chat_ids})
        else:
            log.debug('skip unknown inputPrivacyValue %#010x', cid)
    return rules or _default_rules()


def _pack_privacy_rule(rule: dict) -> bytes:
    w = tl.Writer()
    rtype = rule.get('type') or 'allowAll'
    if rtype == 'allowAll':
        w.u32(PRIVACY_VALUE_ALLOW_ALL)
    elif rtype == 'disallowAll':
        w.u32(PRIVACY_VALUE_DISALLOW_ALL)
    elif rtype == 'allowContacts':
        w.u32(PRIVACY_VALUE_ALLOW_CONTACTS)
    elif rtype == 'disallowContacts':
        w.u32(PRIVACY_VALUE_DISALLOW_CONTACTS)
    elif rtype == 'allowCloseFriends':
        w.u32(PRIVACY_VALUE_ALLOW_CLOSE_FRIENDS)
    elif rtype == 'allowPremium':
        w.u32(PRIVACY_VALUE_ALLOW_PREMIUM)
    elif rtype == 'allowBots':
        w.u32(PRIVACY_VALUE_ALLOW_BOTS)
    elif rtype == 'disallowBots':
        w.u32(PRIVACY_VALUE_DISALLOW_BOTS)
    elif rtype == 'allowUsers':
        w.u32(PRIVACY_VALUE_ALLOW_USERS)
        ids = [int(x) for x in (rule.get('user_ids') or [])]
        w.u32(tl.VECTOR)
        w.i32(len(ids))
        for uid in ids:
            w.i64(uid)
    elif rtype == 'disallowUsers':
        w.u32(PRIVACY_VALUE_DISALLOW_USERS)
        ids = [int(x) for x in (rule.get('user_ids') or [])]
        w.u32(tl.VECTOR)
        w.i32(len(ids))
        for uid in ids:
            w.i64(uid)
    elif rtype == 'allowChatParticipants':
        w.u32(PRIVACY_VALUE_ALLOW_CHAT_PARTICIPANTS)
        ids = [int(x) for x in (rule.get('chat_ids') or [])]
        w.u32(tl.VECTOR)
        w.i32(len(ids))
        for cid in ids:
            w.i64(cid)
    elif rtype == 'disallowChatParticipants':
        w.u32(PRIVACY_VALUE_DISALLOW_CHAT_PARTICIPANTS)
        ids = [int(x) for x in (rule.get('chat_ids') or [])]
        w.u32(tl.VECTOR)
        w.i32(len(ids))
        for cid in ids:
            w.i64(cid)
    else:
        w.u32(PRIVACY_VALUE_ALLOW_ALL)
    return w.bytes_out()


def pack_account_privacy_rules(rules: List[dict], storage=None) -> bytes:
    user_ids: set[int] = set()
    chat_ids: set[int] = set()
    for rule in rules:
        for uid in rule.get('user_ids') or []:
            user_ids.add(int(uid))
        for cid in rule.get('chat_ids') or []:
            chat_ids.add(int(cid))
    users = []
    if storage is not None:
        for uid in sorted(user_ids):
            u = storage.get_user_by_id(uid)
            if u:
                users.append(u)
    chats = []
    if storage is not None:
        for cid in sorted(chat_ids):
            if cid >= config.CHANNEL_ID_MIN:
                ch = storage.get_channel(cid)
                if ch:
                    chats.append(ch)
    w = tl.Writer()
    w.u32(ACCOUNT_PRIVACY_RULES)
    w.u32(tl.VECTOR)
    w.i32(len(rules))
    for rule in rules:
        w.raw(_pack_privacy_rule(rule))
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(packers.pack_channel(ch, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, storage=storage))
    return w.bytes_out()


def privacy_allows(rules: Optional[List[dict]], owner_id: int, viewer_id: int,
                     storage) -> bool:
    if owner_id == viewer_id:
        return True
    if not rules:
        return True
    if any(r.get('type') == 'disallowAll' for r in rules):
        return False
    viewer = storage.get_user_by_id(viewer_id) or {}
    for rule in rules:
        rtype = rule.get('type')
        if rtype == 'disallowUsers' and viewer_id in (rule.get('user_ids') or []):
            return False
        if rtype == 'disallowContacts' and storage.users_are_contacts(owner_id, viewer_id):
            return False
        if rtype == 'disallowChatParticipants':
            shared = storage.shared_channel_ids(owner_id, viewer_id)
            blocked = {int(x) for x in (rule.get('chat_ids') or [])}
            if shared & blocked:
                return False
        if rtype == 'disallowBots' and viewer.get('is_bot'):
            return False
    allowed = False
    for rule in rules:
        rtype = rule.get('type')
        if rtype == 'allowAll':
            allowed = True
        elif rtype == 'allowContacts' and storage.users_are_contacts(owner_id, viewer_id):
            allowed = True
        elif rtype == 'allowUsers' and viewer_id in (rule.get('user_ids') or []):
            allowed = True
        elif rtype == 'allowPremium' and viewer.get('premium'):
            allowed = True
        elif rtype == 'allowBots' and viewer.get('is_bot'):
            allowed = True
        elif rtype == 'allowCloseFriends':
            allowed = True
        elif rtype == 'allowChatParticipants':
            shared = storage.shared_channel_ids(owner_id, viewer_id)
            allowed_ids = {int(x) for x in (rule.get('chat_ids') or [])}
            if shared & allowed_ids:
                allowed = True
    if any(r.get('type') in ('allowAll', 'allowContacts', 'allowUsers', 'allowPremium',
                             'allowBots', 'allowCloseFriends', 'allowChatParticipants')
           for r in rules):
        return allowed
    return True


def can_view_privacy(storage, owner_id: int, viewer_id: int,
                     privacy_key: int) -> bool:
    rules = storage.get_privacy_rules(owner_id, privacy_key)
    return privacy_allows(rules, owner_id, viewer_id, storage)


def handle_account_get_privacy(session, req_msg_id, r, storage, push_queue,
                               registry):
    from handlers.rpc import require_auth
    from handlers.paid_messages import (
        INPUT_PRIVACY_KEY_NO_PAID_MESSAGES,
        pack_no_paid_messages_privacy,
    )
    user, err = require_auth(session, storage)
    if user is None:
        return err

    key_cid = r.u32()
    if key_cid == INPUT_PRIVACY_KEY_NO_PAID_MESSAGES:
        return pack_no_paid_messages_privacy(storage, user['id'])
    if key_cid not in ALL_PRIVACY_KEYS:
        log.debug('getPrivacy unknown key %#010x', key_cid)
        return pack_account_privacy_rules(_default_rules(), storage)
    rules = storage.get_privacy_rules(user['id'], key_cid)
    return pack_account_privacy_rules(rules, storage)


def handle_account_set_privacy(session, req_msg_id, r, storage, push_queue,
                              registry):
    from handlers.rpc import require_auth
    from handlers.paid_messages import (
        INPUT_PRIVACY_KEY_NO_PAID_MESSAGES,
        pack_no_paid_messages_privacy,
    )
    user, err = require_auth(session, storage)
    if user is None:
        return err

    key_cid = r.u32()
    if key_cid == INPUT_PRIVACY_KEY_NO_PAID_MESSAGES:
        from handlers.paid_messages import parse_input_privacy_rules as parse_paid
        exempt = parse_paid(r, storage)
        storage.set_paid_message_exceptions(user['id'], exempt)
        return pack_no_paid_messages_privacy(storage, user['id'])

    rules = parse_input_privacy_rules(r, storage)
    if key_cid in ALL_PRIVACY_KEYS:
        storage.set_privacy_rules(user['id'], key_cid, rules)
        log.debug('setPrivacy user=%s key=%#010x rules=%s',
                  user['id'], key_cid, [r.get('type') for r in rules])
    return pack_account_privacy_rules(rules, storage)


ACCOUNT_GET_PRIVACY_LEGACY = 0xbea7f17b
ACCOUNT_UPDATE_BIRTHDAY = 0xcc6e0c11
BIRTHDAY = 0x6c8e1e06
CONTACT_BIRTHDAY = 0x1d998733


def pack_birthday(day: int, month: int, year: int = 0) -> bytes:
    flags = 1 if year else 0
    w = tl.Writer()
    w.u32(BIRTHDAY)
    w.i32(flags)
    w.i32(int(day))
    w.i32(int(month))
    if flags & 1:
        w.i32(int(year))
    return w.bytes_out()


def pack_disallowed_gifts_settings(settings: dict) -> bytes:
    from handlers.paid_messages import DISALLOWED_GIFTS_SETTINGS
    dg = settings or {}
    dg_flags = 0
    if dg.get('disallow_unlimited_stargifts'):
        dg_flags |= 1
    if dg.get('disallow_limited_stargifts'):
        dg_flags |= 2
    if dg.get('disallow_unique_stargifts'):
        dg_flags |= 4
    if dg.get('disallow_premium_gifts'):
        dg_flags |= 8
    if dg.get('disallow_stargifts_from_channels'):
        dg_flags |= 16
    w = tl.Writer()
    w.u32(DISALLOWED_GIFTS_SETTINGS)
    w.i32(dg_flags)
    return w.bytes_out()


def decode_user_birthday(raw: int) -> Optional[tuple[int, int, int]]:
    value = int(raw or 0)
    if value <= 0:
        return None
    if value >= 10000:
        year = value // 10000
        month = (value // 100) % 100
        day = value % 100
    else:
        year = 0
        month = (value // 100) % 100
        day = value % 100
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return day, month, year


def encode_user_birthday(day: int, month: int, year: int = 0) -> int:
    day = int(day)
    month = int(month)
    year = int(year or 0)
    if year > 0:
        return year * 10000 + month * 100 + day
    return month * 100 + day


def check_can_message_user(storage, sender_id: int, recipient_id: int) -> None:
    if sender_id == recipient_id or recipient_id >= config.CHANNEL_ID_MIN:
        return
    recipient = storage.get_user_by_id(recipient_id)
    if not recipient:
        raise ValueError('PEER_ID_INVALID')
    gp = storage.get_global_privacy(recipient_id)
    if gp.get('new_noncontact_peers_require_premium'):
        if not storage.users_are_contacts(recipient_id, sender_id):
            sender = storage.get_user_by_id(sender_id) or {}
            if not sender.get('premium'):
                raise ValueError('PREMIUM_ACCOUNT_REQUIRED')


def check_can_send_voice(storage, sender_id: int, recipient_id: int) -> None:
    if sender_id == recipient_id or recipient_id >= config.CHANNEL_ID_MIN:
        return
    if not can_view_privacy(
            storage, recipient_id, sender_id, INPUT_PRIVACY_KEY_VOICE_MESSAGES):
        raise ValueError('VOICE_MESSAGES_FORBIDDEN')


def check_can_invite_user(storage, invitee_id: int, inviter_id: int) -> None:
    if invitee_id == inviter_id:
        return
    if not can_view_privacy(
            storage, invitee_id, inviter_id, INPUT_PRIVACY_KEY_CHAT_INVITE):
        raise ValueError('USER_PRIVACY_RESTRICTED')


def viewer_can_link_forward_author(storage, author_id: int,
                                   viewer_id: int) -> bool:
    if not author_id or author_id == viewer_id:
        return True
    if author_id >= config.CHANNEL_ID_MIN:
        return True
    return can_view_privacy(
        storage, author_id, viewer_id, INPUT_PRIVACY_KEY_FORWARDS)


def handle_account_update_birthday(session, req_msg_id, r, storage, push_queue,
                                   registry):
    from handlers.rpc import require_auth
    from mtproto import tl as tl_mod
    user, err = require_auth(session, storage)
    if user is None:
        return err

    flags = r.i32()
    encoded = 0
    if flags & 1:
        assert r.u32() == BIRTHDAY
        bflags = r.i32()
        day = int(r.i32())
        month = int(r.i32())
        year = int(r.i32()) if bflags & 1 else 0
        encoded = encode_user_birthday(day, month, year)
    storage.set_user_birthday(user['id'], encoded)
    w = tl.Writer()
    w.u32(tl_mod.BOOL_TRUE)
    return w.bytes_out()


PRIVACY_HANDLERS = {
    ACCOUNT_GET_PRIVACY: handle_account_get_privacy,
    ACCOUNT_GET_PRIVACY_LEGACY: handle_account_get_privacy,
    ACCOUNT_SET_PRIVACY: handle_account_set_privacy,
    ACCOUNT_UPDATE_BIRTHDAY: handle_account_update_birthday,
}
