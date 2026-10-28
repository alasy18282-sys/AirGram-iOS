"""Megagroup (channel) handlers — create, invite, messaging, sync."""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

from mtproto import tl
from mtproto.tl import Writer
from db.storage import Storage
from handlers import packers
from handlers.peer_colors import is_valid_color_id

import config


def _require_auth(session, storage):
    from handlers.rpc import require_auth
    return require_auth(session, storage)


CHANNELS_CREATE_CHANNEL = 0x91006707
MESSAGES_CREATE_CHAT = 0x92ceddd4
MESSAGES_GET_CHATS = 0x49e9528f
MESSAGES_GET_FULL_CHAT = 0xaeb00b34
MESSAGES_EDIT_CHAT_TITLE = 0x73783ffd
MESSAGES_EXPORT_CHAT_INVITE = 0xa455de90
MESSAGES_CHECK_CHAT_INVITE = 0x3eadb1bb
MESSAGES_IMPORT_CHAT_INVITE = 0x6c50051c
MESSAGES_INVITED_USERS = 0x7f5defa6
MESSAGES_CHAT_FULL = 0xe5d7d19c
MESSAGES_CHATS = 0x64ff9fd5
CHANNELS_GET_FULL_CHANNEL = 0x08736a09
CHANNELS_GET_CHANNELS = 0x0a7f6bbb
CHANNELS_JOIN_CHANNEL = 0x24b524c5
CHANNELS_LEAVE_CHANNEL = 0xf836aa95
CHANNELS_INVITE_TO_CHANNEL = 0xc9e33d54
CHANNELS_GET_PARTICIPANTS = 0x77ced9d0
CHANNELS_GET_PARTICIPANT = 0xa0ab6cc6
CHANNELS_EDIT_TITLE = 0x566decd0
CHANNELS_EDIT_PHOTO = 0xf12e57c9
MESSAGES_EDIT_CHAT_PHOTO = 0x35ddd674
CHANNELS_GET_SEND_AS = 0xe785a43f
CHANNELS_SEND_AS_PEERS = 0xf496b0c6
SEND_AS_PEER = 0xb81c7034
CHANNEL_PARTICIPANT_CREATOR = 0x2fe601d3
CHANNELS_CHECK_USERNAME = 0x10e6bd2c
CHANNELS_UPDATE_USERNAME = 0x3514b3de
CHANNELS_REORDER_USERNAMES = 0xb45ced1d
CHANNELS_TOGGLE_SIGNATURES = 0x418d549c
CHANNELS_UPDATE_PAID_MESSAGES_PRICE = 0x4b12327b
MESSAGES_EDIT_CHAT_DEFAULT_BANNED_RIGHTS = 0xa5866b41
MESSAGES_GET_SEARCH_COUNTERS = 0x1bbcf300
MESSAGES_SEARCH = 0x29ee847a
INPUT_MESSAGES_FILTER_EMPTY = 0x57e2f66c
HELP_GET_PROMO_DATA = 0xc0977421
HELP_DISMISS_SUGGESTION = 0xf50dbaa1
MESSAGES_GET_AVAILABLE_REACTIONS = 0x18dea0ac
CHANNELS_GET_ADMINED_PUBLIC_CHANNELS = 0xf8b036af
MESSAGES_UPDATE_PINNED_MESSAGE = 0xd2aaf7ec
MESSAGE_ACTION_PIN_MESSAGE = 0x94bd38ed
MESSAGES_EDIT_CHAT_ABOUT = 0xdef60797
CHANNELS_READ_HISTORY = 0xcc104937
UPDATES_GET_CHANNEL_DIFFERENCE = 0x03173d78
INPUT_CHANNEL = 0xf35aec28
MESSAGE_ACTION_CHANNEL_CREATE = 0x95d2ac92
MESSAGE_ACTION_CHAT_CREATE = 0xbd47cbad
MESSAGE_ACTION_CHAT_EDIT_TITLE = 0xb5a1ce5a
UPDATE_CHANNEL = 0x635b4c09
UPDATE_NEW_CHANNEL_MESSAGE = 0x62ba04d9
UPDATE_EDIT_CHANNEL_MESSAGE = 0x1b3f4df7
MESSAGE_ACTION_SUGGESTED_POST_APPROVAL = 0xee7a1596
UPDATE_READ_CHANNEL_INBOX = 0x922e6e10
UPDATE_READ_CHANNEL_OUTBOX = 0xb75f99a9
UPDATES_CHANNEL_DIFFERENCE = 0x2064674e
UPDATES_CHANNEL_DIFFERENCE_EMPTY = 0x3e11affb
UPDATES_CHANNEL_DIFFERENCE_TOO_LONG = 0x6a9d7b35
CHANNEL_PARTICIPANTS = 0x9ab0feaf
CHANNEL_PARTICIPANT = 0x1bd54456
CHANNEL_PARTICIPANT_ADMIN = 0x34c3bb53
CHANNELS_CHANNEL_PARTICIPANT = 0xdfb80317
CHANNELS_EDIT_ADMIN = 0x9a98ad68
CHANNELS_GET_ADMIN_LOG = 0x33ddf480
CHANNELS_EDIT_BANNED = 0x96e6cd81
CHANNELS_DELETE_MESSAGES = 0x84c1fd4e
MESSAGES_DELETE_MESSAGES = 0xe58e95d2
MESSAGES_GET_MESSAGES_VIEWS = 0x5784d3e1
MESSAGES_MESSAGE_VIEWS = 0xb6c4f543
MESSAGE_VIEWS = 0x455b853d
UPDATE_DELETE_CHANNEL_MESSAGES = 0xc32d5b12
CHANNELS_ADMIN_LOG_RESULTS = 0xed8af74d
CHANNEL_ADMIN_LOG_EVENT = 0x1fad68cd
UPDATE_CHANNEL_PARTICIPANT = 0x985d3abb
CHANNEL_PARTICIPANTS_ADMINS = 0xb4608969
CHANNEL_PARTICIPANTS_RECENT = 0xde3f3c79
CHAT_INVITE_EXPORTED = 0xa22cbd96
CHAT_INVITE = 0x5c9d3702
CHAT_INVITE_ALREADY = 0x5a686d7c
CHANNEL_MESSAGES_FILTER_EMPTY = 0x94d42ee7
CHANNELS_REPORT_SPAM = 0xf44a8315
CHANNELS_GET_MESSAGES = 0xad8c9a23
CHANNELS_GET_MESSAGES_V2 = 0x93d7b347
CHANNELS_DELETE_CHANNEL = 0xc0111fe3
CHANNELS_EXPORT_MESSAGE_LINK = 0xe63fadeb
EXPORTED_MESSAGE_LINK = 0x5dab1af4
CHANNELS_SET_STICKERS = 0xea8ca4f9
CHANNELS_READ_MESSAGE_CONTENTS = 0xeab5dc38
CHANNELS_DELETE_HISTORY = 0x9baa9647
CHANNELS_TOGGLE_PRE_HISTORY_HIDDEN = 0xeabbb94c
CHANNELS_GET_LEFT_CHANNELS = 0x8341ecc0
CHANNELS_SET_DISCUSSION_GROUP = 0x40582bb2
CHANNELS_EDIT_LOCATION = 0x58e63f6d
CHANNELS_TOGGLE_SLOW_MODE = 0xedd49ef0
CHANNELS_GET_INACTIVE_CHANNELS = 0x11e831ee
MESSAGES_INACTIVE_CHATS = 0xa927fec5
CHANNELS_CONVERT_TO_GIGAGROUP = 0x0b290c69
CHANNELS_DELETE_PARTICIPANT_HISTORY = 0x367544db
CHANNELS_TOGGLE_JOIN_TO_SEND = 0xe4cb9580
CHANNELS_TOGGLE_JOIN_REQUEST = 0x4c2985b6
CHANNELS_TOGGLE_USERNAME = 0x50f24105
CHANNELS_DEACTIVATE_ALL_USERNAMES = 0x0a245dd3
CHANNELS_TOGGLE_FORUM = 0x3ff75734
CHANNELS_TOGGLE_ANTI_SPAM = 0x68f3e4eb
CHANNELS_REPORT_ANTI_SPAM_FALSE_POSITIVE = 0xa850a693
CHANNELS_TOGGLE_PARTICIPANTS_HIDDEN = 0x6a6e7854
CHANNELS_UPDATE_COLOR = 0xd8aa3671
CHANNELS_TOGGLE_VIEW_FORUM_AS_MESSAGES = 0x9738bb15
CHANNELS_UPDATE_EMOJI_STATUS = 0xf0d3e6a8
CHANNELS_SET_BOOSTS_TO_UNBLOCK_RESTRICTIONS = 0xad399cee
CHANNELS_SET_EMOJI_STICKERS = 0x3cd930b7
CHANNELS_RESTRICT_SPONSORED_MESSAGES = 0x9ae91519
CHANNELS_SEARCH_POSTS = 0xf2c4f24d
CHANNELS_TOGGLE_AUTOTRANSLATION = 0x167fc0a1
CHANNELS_GET_MESSAGE_AUTHOR = 0xece2a0e6
CHANNELS_CHECK_SEARCH_POSTS_FLOOD = 0x22567115
SEARCH_POSTS_FLOOD = 0x3e0b5b6a
CHANNELS_SET_MAIN_PROFILE_TAB = 0x3583fcb1


def _rpc_error(code: int, msg: str) -> bytes:
    w = Writer()
    w.u32(tl.RPC_ERROR)
    w.i32(code)
    w.string(msg)
    return w.bytes_out()


def parse_input_channel(r: tl.Reader) -> Optional[tuple]:
    cid = r.u32()
    if cid == 0xee8c1e86:  # inputChannelEmpty
        return None
    if cid != INPUT_CHANNEL:
        raise ValueError(f'unsupported InputChannel {cid:#010x}')
    channel_id = r.i64()
    access_hash = r.i64()
    return int(channel_id), int(access_hash)


def _create_channel_megagroup(flags: int) -> bool:
    """Parse channels.createChannel flags into megagroup vs broadcast channel."""
    is_broadcast = bool(flags & 1)   # flags.0 broadcast
    is_megagroup = bool(flags & 2)   # flags.1 megagroup
    is_forum = bool(flags & 32)     # flags.5 forum
    if is_broadcast:
        return False
    if is_megagroup or is_forum:
        return True
    return False


def parse_input_users(r: tl.Reader, storage: Storage,
                      self_user_id: int) -> List[int]:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    ids = []
    for _ in range(count):
        u = packers.parse_input_user(r, storage, self_user_id=self_user_id)
        if u:
            ids.append(int(u['id']))
    return ids


def _skip_channel_messages_filter(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == CHANNEL_MESSAGES_FILTER_EMPTY:
        return
    if cid == 0xcd77d957:  # channelMessagesFilter
        r.i32()  # flags
        assert r.u32() == tl.VECTOR
        count = r.i32()
        for _ in range(count):
            r.i32()
            r.i32()


def _skip_channel_participants_filter(r: tl.Reader) -> str:
    cid = r.u32()
    if cid == CHANNEL_PARTICIPANTS_ADMINS:
        return 'admins'
    if cid == CHANNEL_PARTICIPANTS_RECENT:
        return 'recent'
    if cid == 0x656ac4b:  # channelParticipantsSearch
        r.string()
        return 'search'
    return 'all'


def _users_for_chat_full(storage: Storage, channel_id: int,
                         viewer_id: int) -> List[dict]:
    """Users for messages.chatFull — not every group member."""
    users_map: dict[int, dict] = {}
    viewer = storage.get_user_by_id(viewer_id)
    if viewer:
        users_map[viewer_id] = viewer
    ch = storage.get_channel(channel_id)
    if ch:
        creator_id = int(ch.get('creator_id') or 0)
        if creator_id and creator_id not in users_map:
            u = storage.get_user_by_id(creator_id)
            if u:
                users_map[creator_id] = u
        bv = ch.get('bot_verification') or {}
        bot_id = int(bv.get('bot_id') or 0)
        if bot_id and bot_id not in users_map:
            bot = storage.get_user_by_id(bot_id)
            if bot:
                users_map[bot_id] = bot
    for uid in storage.list_channel_admin_ids(channel_id):
        if uid not in users_map:
            u = storage.get_user_by_id(uid)
            if u:
                users_map[uid] = u
    return list(users_map.values())


def _collect_channel_users(storage: Storage, channel_id: int,
                           viewer_id: int) -> List[dict]:
    users_map = {}
    ch = storage.get_channel(channel_id)
    if ch:
        creator = storage.get_user_by_id(int(ch.get('creator_id') or 0))
        if creator:
            users_map[creator['id']] = creator
    for uid in storage.list_channel_member_ids(channel_id):
        if uid not in users_map:
            u = storage.get_user_by_id(uid)
            if u:
                users_map[uid] = u
    viewer = storage.get_user_by_id(viewer_id)
    if viewer:
        users_map[viewer['id']] = viewer
    return list(users_map.values())


def _users_for_channel_message(storage: Storage, msg: dict,
                               extra_user_ids: Optional[List[int]] = None
                               ) -> List[dict]:
    """Users referenced by channel updates — not every group member."""
    users_map = {}
    for uid in (int(msg.get('from_id') or 0), int(msg.get('owner_id') or 0)):
        if uid and uid not in users_map:
            u = storage.get_user_by_id(uid)
            if u:
                users_map[uid] = u
    for uid in (extra_user_ids or []):
        uid = int(uid or 0)
        if uid and uid not in users_map:
            u = storage.get_user_by_id(uid)
            if u:
                users_map[uid] = u
    return list(users_map.values())


def _pack_messages_chat_full(ch_full: dict, ch: dict,
                             viewer_id: int, storage: Storage) -> bytes:
    users = _users_for_chat_full(storage, int(ch['id']), viewer_id)
    chats = [ch]
    mono = storage.get_channel_monoforum(int(ch['id']))
    if mono:
        chats.append(mono)
    w = Writer()
    w.u32(MESSAGES_CHAT_FULL)
    w.raw(packers.pack_channel_full(ch_full, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for chat in chats:
        w.raw(packers.pack_channel(chat, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(
            u, is_self=int(u['id']) == int(viewer_id), storage=storage))
    return w.bytes_out()


def _pack_messages_chats(channels: List[dict], viewer_id: int,
                         storage=None) -> bytes:
    w = Writer()
    w.u32(MESSAGES_CHATS)
    w.u32(tl.VECTOR)
    w.i32(len(channels))
    for ch in channels:
        w.raw(packers.pack_channel(ch, viewer_id, storage=storage))
    return w.bytes_out()


def _pack_channel_difference_empty(pts: int, *, final: bool = True) -> bytes:
    flags = 1 if final else 0
    w = Writer()
    w.u32(UPDATES_CHANNEL_DIFFERENCE_EMPTY)
    w.i32(flags)
    w.i32(pts)
    return w.bytes_out()


def _pack_channel_difference_too_long(pts: int) -> bytes:
    w = Writer()
    w.u32(UPDATES_CHANNEL_DIFFERENCE_TOO_LONG)
    w.i32(0)
    w.i32(pts)
    return w.bytes_out()


def _pack_channel_difference(ch: dict, messages: List[dict], users: List[dict],
                             pts: int, viewer_id: int, storage=None,
                             *, final: bool = True) -> bytes:
    flags = 1 if final else 0
    w = Writer()
    w.u32(UPDATES_CHANNEL_DIFFERENCE)
    w.i32(flags)
    w.i32(pts)
    w.u32(tl.VECTOR)
    w.i32(len(messages))
    for m in messages:
        w.raw(packers.pack_message(m, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(packers.pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, storage=storage))
    return w.bytes_out()


def _pack_invited_users(updates: bytes) -> bytes:
    w = Writer()
    w.u32(MESSAGES_INVITED_USERS)
    w.raw(updates)
    w.u32(tl.VECTOR)
    w.i32(0)  # missing_invitees
    return w.bytes_out()


def pack_chat_invite_exported(link: str, admin_id: int, *,
                              title: str = '') -> bytes:
    flags = 1 << 5  # permanent
    if title:
        flags |= 1 << 8
    w = Writer()
    w.u32(CHAT_INVITE_EXPORTED)
    w.i32(flags)
    w.string(link)
    w.i64(admin_id)
    w.i32(int(time.time()))
    if flags & (1 << 8):
        w.string(title)
    return w.bytes_out()


def pack_chat_invite(ch: dict) -> bytes:
    about = ch.get('about') or ''
    flags = (1 << 0) | (1 << 2)  # channel, public
    if int(ch.get('megagroup') or 0):
        flags |= 1 << 3  # megagroup
    if about:
        flags |= 1 << 5
    w = Writer()
    w.u32(CHAT_INVITE)
    w.i32(flags)
    w.string(ch.get('title') or 'Group')
    if flags & (1 << 5):
        w.string(about)
    w.raw(packers.pack_photo_empty())
    w.i32(int(ch.get('participants_count') or 1))
    w.i32(0)  # color
    return w.bytes_out()


def _pack_channel_participant(channel_id: int, uid: int, ch: dict,
                              storage: Optional[Storage] = None) -> bytes:
    if int(ch.get('creator_id') or 0) == int(uid):
        pw = Writer()
        pw.u32(CHANNEL_PARTICIPANT_CREATOR)
        pw.i32(0)
        pw.i64(uid)
        pw.raw(packers.pack_chat_admin_rights_creator())
        return pw.bytes_out()
    member = storage.get_channel_member_row(channel_id, uid) if storage else None
    if member and int(member.get('is_admin') or 0):
        pw = Writer()
        pw.u32(CHANNEL_PARTICIPANT_ADMIN)
        flags = 0
        rank = (member.get('admin_rank') or '').strip()
        if rank:
            flags |= 1 << 2
        pw.i32(flags)
        pw.i64(uid)
        pw.i64(int(member.get('promoted_by') or ch.get('creator_id') or 0))
        pw.i32(int(member.get('date') or time.time()))
        pw.raw(packers.pack_chat_admin_rights(
            (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)
            | (1 << 5) | (1 << 7) | (1 << 9) | (1 << 11) | (1 << 12)
            | (1 << 13) | (1 << 14) | (1 << 15) | (1 << 16)))
        if flags & (1 << 2):
            pw.string(rank)
        return pw.bytes_out()
    pw = Writer()
    pw.u32(CHANNEL_PARTICIPANT)
    pw.i32(0)
    pw.i64(uid)
    pw.i32(int(member.get('date') if member else time.time()))
    return pw.bytes_out()


def handle_channels_get_send_as(session, req_msg_id, r, storage, push_queue,
                                registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    r.i32()  # flags
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id and peer_id >= config.CHANNEL_ID_MIN:
        if not storage.is_channel_member(peer_id, viewer['id']):
            return _rpc_error(400, 'CHANNEL_PRIVATE')

    sw = Writer()
    sw.u32(SEND_AS_PEER)
    sw.i32(0)
    sw.raw(packers.pack_peer_user(viewer['id']))

    w = Writer()
    w.u32(CHANNELS_SEND_AS_PEERS)
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(sw.bytes_out())
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(packers.pack_user(
        viewer, is_self=True, storage=storage))
    return w.bytes_out()


def _channel_from_request(r: tl.Reader, storage: Storage,
                          viewer_id: int, *, allow_public: bool = False) -> tuple:
    parsed = parse_input_channel(r)
    if not parsed:
        raise ValueError('CHANNEL_INVALID')
    channel_id, access_hash = parsed
    ch = storage.get_channel_by_access_hash(channel_id, access_hash)
    if not ch:
        raise ValueError('CHANNEL_INVALID')
    if not storage.is_channel_member(channel_id, viewer_id):
        monoforum_access = False
        if int(ch.get('monoforum') or 0):
            linked = storage.get_linked_channel_for_monoforum(channel_id)
            monoforum_access = (
                bool(linked and storage.is_channel_admin(
                    int(linked['id']), viewer_id))
                or storage.count_messages_for_peer(viewer_id, channel_id) > 0
            )
        if not monoforum_access and not (allow_public and ch.get('username')):
            raise ValueError('CHANNEL_PRIVATE')
    return channel_id, access_hash, ch


def _normalize_invite_hash(raw: str) -> str:
    raw = (raw or '').strip()
    if raw.startswith('https://t.me/+'):
        return raw.split('+', 1)[1]
    if raw.startswith('https://t.me/joinchat/'):
        return raw.rsplit('/', 1)[-1]
    if raw.startswith('+'):
        return raw[1:]
    return raw


def pack_message_action_channel_create(title: str) -> bytes:
    w = Writer()
    w.u32(MESSAGE_ACTION_CHANNEL_CREATE)
    w.string(title)
    return w.bytes_out()


def pack_message_action_chat_create(title: str, user_ids: List[int]) -> bytes:
    w = Writer()
    w.u32(MESSAGE_ACTION_CHAT_CREATE)
    w.string(title)
    w.u32(tl.VECTOR)
    w.i32(len(user_ids))
    for uid in user_ids:
        w.i64(uid)
    return w.bytes_out()


def pack_message_action_suggested_post_approval(action: dict) -> bytes:
    flags = 0
    reject_comment = (action.get('reject_comment') or '').strip()
    schedule_date = int(action.get('schedule_date') or 0)
    price_stars = int(action.get('price_stars') or 0)
    if action.get('rejected'):
        flags |= 1
    if action.get('balance_too_low'):
        flags |= 2
    if reject_comment:
        flags |= 4
    if schedule_date:
        flags |= 8
    if price_stars > 0:
        flags |= 16
    w = Writer()
    w.u32(MESSAGE_ACTION_SUGGESTED_POST_APPROVAL)
    w.i32(flags)
    if flags & 4:
        w.string(reject_comment)
    if flags & 8:
        w.i32(schedule_date)
    if flags & 16:
        w.u32(0xbbb6b4a3)
        w.i64(price_stars)
        w.i32(0)
    return w.bytes_out()


def pack_service_suggested_post_approval(msg: dict, action: dict,
                                         storage=None) -> bytes:
    return _pack_channel_service_message_bytes(
        msg, pack_message_action_suggested_post_approval(action),
        storage=storage)


def pack_message_action_pin_message() -> bytes:
    w = Writer()
    w.u32(MESSAGE_ACTION_PIN_MESSAGE)
    return w.bytes_out()


def pack_service_pin_message(msg: dict, action: dict, storage=None) -> bytes:
    return _pack_channel_service_message_bytes(
        msg, pack_message_action_pin_message(), storage=storage)


def pack_message_action_chat_edit_title(title: str) -> bytes:
    w = Writer()
    w.u32(MESSAGE_ACTION_CHAT_EDIT_TITLE)
    w.string(title)
    return w.bytes_out()


def _pack_channel_service_message_bytes(msg: dict, action_body: bytes,
                                        storage=None) -> bytes:
    from_id = msg['from_id']
    peer_id = msg['peer_id']
    is_out = msg.get('out')
    if is_out is None:
        is_out = from_id == msg['owner_id']
    flags = 1 << 9  # reactions_are_possible
    if is_out:
        flags |= 1 << 1
    else:
        flags |= 1 << 8
    if storage is not None:
        ch = storage.get_channel(int(peer_id))
        if ch and not int(ch.get('megagroup') or 0):
            flags |= 1 << 14  # post (broadcast channel)
    w = Writer()
    w.u32(0x7a800e0a)  # messageService layer 224
    w.i32(flags)
    w.i32(msg['id'])
    if flags & (1 << 8):
        w.raw(packers.pack_peer_user(from_id))
    w.raw(packers.pack_peer_channel(peer_id))
    w.i32(msg['date'])
    w.raw(action_body)
    return w.bytes_out()


def pack_service_channel_create(msg: dict, action: dict, storage=None) -> bytes:
    return _pack_channel_service_message_bytes(
        msg, pack_message_action_channel_create(action.get('title') or ''),
        storage=storage)


def pack_service_chat_create(msg: dict, action: dict, storage=None) -> bytes:
    users = [int(u) for u in (action.get('users') or [])]
    return _pack_channel_service_message_bytes(
        msg, pack_message_action_chat_create(action.get('title') or '', users),
        storage=storage)


def pack_service_chat_edit_title(msg: dict, action: dict, storage=None) -> bytes:
    return _pack_channel_service_message_bytes(
        msg, pack_message_action_chat_edit_title(action.get('title') or ''),
        storage=storage)


def pack_update_channel(channel_id: int) -> bytes:
    w = Writer()
    w.u32(UPDATE_CHANNEL)
    w.i64(channel_id)
    return w.bytes_out()


def pack_update_new_channel_message(msg: dict, pts: int,
                                    storage=None) -> bytes:
    w = Writer()
    w.u32(UPDATE_NEW_CHANNEL_MESSAGE)
    w.raw(packers.pack_message(msg, storage))
    w.i32(pts)
    w.i32(1)
    return w.bytes_out()


def pack_update_edit_channel_message(msg: dict, pts: int,
                                     storage=None) -> bytes:
    w = Writer()
    w.u32(UPDATE_EDIT_CHANNEL_MESSAGE)
    w.raw(packers.pack_message(msg, storage))
    w.i32(pts)
    w.i32(1)
    return w.bytes_out()


def pack_update_read_channel_inbox(channel_id: int, max_id: int, pts: int,
                                    unread: int = 0) -> bytes:
    w = Writer()
    w.u32(UPDATE_READ_CHANNEL_INBOX)
    w.i32(0)
    w.i64(channel_id)
    w.i32(max_id)
    w.i32(unread)
    w.i32(pts)
    return w.bytes_out()


def pack_update_read_channel_outbox(channel_id: int, max_id: int) -> bytes:
    w = Writer()
    w.u32(UPDATE_READ_CHANNEL_OUTBOX)
    w.i64(channel_id)
    w.i32(max_id)
    return w.bytes_out()


def _pack_channel_service_message(channel_id: int, msg_id: int, title: str,
                                  creator_id: int, date: int,
                                  owner_id: int) -> dict:
    return {
        'owner_id': owner_id,
        'id': msg_id,
        'from_id': creator_id,
        'peer_id': channel_id,
        'date': date,
        'text': '',
        'entities': [],
        'action_json': {
            'type': 'channel_create',
            'title': title,
        },
        'out': owner_id == creator_id,
    }


def _push_channel_service_to_members(storage, push_queue, session, channel_id,
                                   member_ids, msg, pts_channel, users):
    for uid in member_ids:
        if uid == msg.get('from_id'):
            continue
        member_msg = storage.get_message(uid, msg['id'])
        if member_msg:
            _push_channel_message(
                storage, push_queue, session, uid, member_msg,
                pts_channel, channel_id)


def _fanout_channel_updates(storage, push_queue, session, channel_id,
                            result: dict, users: List[dict], *,
                            skip_user_id: int = 0):
    if push_queue is None:
        return
    for uid in result.get('member_ids', []):
        if uid == skip_user_id:
            continue
        member_msg = result['member_msgs'].get(uid)
        if member_msg:
            _push_channel_message(
                storage, push_queue, session, uid, member_msg,
                result['pts_channel'], channel_id)


def _build_title_edit_updates(storage, viewer, channel_id: int, title: str,
                              push_queue=None, session=None):
    action_json = json.dumps(
        {'type': 'chat_edit_title', 'title': title}, ensure_ascii=False)
    result = storage.send_channel_message(
        channel_id, viewer['id'], '', [], 0, 0, action_json=action_json)
    ch = storage.get_channel(channel_id)
    sender_msg = result['sender_msg']
    users_map = {viewer['id']: viewer}
    for uid in result['member_ids']:
        if uid not in users_map:
            u = storage.get_user_by_id(uid)
            if u:
                users_map[uid] = u
    users = list(users_map.values())
    _fanout_channel_updates(
        storage, push_queue, session, channel_id, result, users,
        skip_user_id=viewer['id'])
    updates_list = [
        pack_update_channel(channel_id),
        pack_update_new_channel_message(
            sender_msg, result['pts_channel'], storage),
        pack_update_read_channel_inbox(
            channel_id, int(sender_msg['id']), result['pts_channel'], 0),
    ]
    return packers.pack_updates(
        updates_list, users,
        user_id=viewer['id'], storage=storage, chats=[ch])


def _push_channel_message(storage, push_queue, session, member_id,
                          msg: dict, pts_channel: int,
                          channel_id: int):
    if push_queue is None or not msg:
        return
    users = _users_for_channel_message(storage, msg)
    dialog = storage.ensure_dialog(member_id, channel_id)
    unread = int(dialog.get('unread_count') or 0)
    updates = [
        pack_update_new_channel_message(msg, pts_channel, storage),
        pack_update_read_channel_inbox(
            channel_id, int(msg['id']), pts_channel, unread),
    ]
    upd = packers.pack_updates(
        updates, users, user_id=member_id, storage=storage,
        increment_seq=False)
    push_queue.push_to_user(
        member_id, upd, exclude_auth_key_id=session.auth_key_id)


def _deliver_channel_message(storage, push_queue, session, sender, channel_id,
                             text, entities, reply_to_msg_id, random_id,
                             media_document_id: int = 0,
                             media_photo_id: int = 0,
                             media_poll_json: str = ''):
    try:
        result = storage.send_channel_message(
            channel_id, sender['id'], text, entities, reply_to_msg_id,
            media_document_id, media_photo_id,
            media_poll_json=media_poll_json, random_id=random_id)
    except ValueError as exc:
        from handlers.rpc import rpc_error
        return rpc_error(400, str(exc))
    sender_msg = result['sender_msg']
    ch = storage.get_channel(channel_id)
    users = _users_for_channel_message(storage, sender_msg)

    for uid, msg in result['member_msgs'].items():
        if uid == sender['id']:
            continue
        _push_channel_message(
            storage, push_queue, session, uid, msg,
            result['pts_channel'], channel_id)

    w_mid = Writer()
    w_mid.u32(tl.UPDATE_MESSAGE_ID)
    w_mid.i32(sender_msg['id'])
    w_mid.i64(random_id)
    updates = [
        w_mid.bytes_out(),
        pack_update_new_channel_message(
            sender_msg, result['pts_channel'], storage),
        pack_update_read_channel_outbox(channel_id, int(sender_msg['id'])),
    ]
    return packers.pack_updates(
        updates, users, user_id=sender['id'], storage=storage,
        increment_seq=False, chats=[ch] if ch else [])


def _deliver_channel_direct_message(storage, push_queue, session, sender,
                                    channel_id, text, entities,
                                    reply_to_msg_id, random_id,
                                    media_document_id: int = 0,
                                    media_photo_id: int = 0,
                                    media_poll_json: str = ''):
    try:
        result = storage.send_channel_direct_message(
            channel_id, sender['id'], text, entities, reply_to_msg_id,
            media_document_id, media_photo_id, media_poll_json=media_poll_json)
    except ValueError as exc:
        from handlers.rpc import rpc_error
        return rpc_error(400, str(exc))

    sender_msg = result['sender_msg']
    ch = storage.get_channel(channel_id)
    users = _users_for_channel_message(storage, sender_msg)

    for uid, msg in result['member_msgs'].items():
        if uid == sender['id']:
            continue
        _push_channel_message(
            storage, push_queue, session, uid, msg,
            result['pts_channel'], channel_id)

    w_mid = Writer()
    w_mid.u32(tl.UPDATE_MESSAGE_ID)
    w_mid.i32(sender_msg['id'])
    w_mid.i64(random_id)
    updates = [
        w_mid.bytes_out(),
        pack_update_new_channel_message(
            sender_msg, result['pts_channel'], storage),
        pack_update_read_channel_outbox(channel_id, int(sender_msg['id'])),
    ]
    return packers.pack_updates(
        updates, users, user_id=sender['id'], storage=storage,
        increment_seq=False, chats=[ch] if ch else [])


def _push_monoforum_message(storage, push_queue, session, member_id,
                            msg: dict, pts_channel: int,
                            chats: List[dict], monoforum_id: int):
    if push_queue is None or not msg:
        return
    users = _users_for_channel_message(storage, msg)
    updates = [
        pack_update_new_channel_message(msg, pts_channel, storage),
    ]
    upd = packers.pack_updates(
        updates, users, user_id=member_id, storage=storage,
        increment_seq=False, chats=chats)
    push_queue.push_to_user(
        member_id, upd, exclude_auth_key_id=session.auth_key_id)


def _deliver_monoforum_message(storage, push_queue, session, sender,
                               monoforum_id, text, entities,
                               reply_to_msg_id, random_id,
                               media_document_id: int = 0,
                               media_photo_id: int = 0,
                               media_poll_json: str = '',
                               topic_user_id: int = 0,
                               paid_message_stars: int = 0,
                               suggested_post_json: str = ''):
    try:
        result = storage.send_monoforum_message(
            monoforum_id, sender['id'], text, entities, reply_to_msg_id,
            media_document_id, media_photo_id, media_poll_json=media_poll_json,
            topic_user_id=topic_user_id,
            paid_message_stars=paid_message_stars,
            suggested_post_json=suggested_post_json)
    except ValueError as exc:
        from handlers.rpc import rpc_error
        msg = str(exc)
        if msg.startswith('ALLOW_PAYMENT_REQUIRED_'):
            return rpc_error(403, msg)
        return rpc_error(400, msg)

    sender_msg = result['sender_msg']
    mono = storage.get_channel(monoforum_id)
    linked = storage.get_channel(result['linked_channel_id'])
    chats = [ch for ch in (mono, linked) if ch]
    topic_user_id = int(result.get('topic_user_id') or 0)
    users = _users_for_channel_message(
        storage, sender_msg,
        extra_user_ids=[topic_user_id] if topic_user_id else None)

    for uid, msg in result['member_msgs'].items():
        if uid == sender['id']:
            continue
        _push_monoforum_message(
            storage, push_queue, session, uid, msg,
            result['pts_channel'], chats, monoforum_id)

    w_mid = Writer()
    w_mid.u32(tl.UPDATE_MESSAGE_ID)
    w_mid.i32(sender_msg['id'])
    w_mid.i64(random_id)
    updates = [
        w_mid.bytes_out(),
        pack_update_new_channel_message(
            sender_msg, result['pts_channel'], storage),
        pack_update_read_channel_outbox(monoforum_id, int(sender_msg['id'])),
    ]
    return packers.pack_updates(
        updates, users, user_id=sender['id'], storage=storage,
        increment_seq=False, chats=chats)


def _build_channel_create_updates(storage, viewer, ch, title, *,
                                  push_queue=None, session=None,
                                  member_ids: Optional[List[int]] = None):
    channel_id = int(ch['id'])
    if member_ids is not None:
        users_list = [int(viewer['id'])]
        for uid in member_ids:
            uid = int(uid)
            if uid != int(viewer['id']) and uid not in users_list:
                users_list.append(uid)
        action = {'type': 'chat_create', 'title': title, 'users': users_list}
    else:
        action = {'type': 'channel_create', 'title': title}
    action_json = json.dumps(action, ensure_ascii=False)
    result = storage.send_channel_message(
        channel_id, viewer['id'], '', [], 0, 0, action_json=action_json)
    ch = storage.get_channel(channel_id)
    creator_msg = result['sender_msg']
    users = [viewer]
    for uid in storage.list_channel_member_ids(channel_id):
        if uid != viewer['id']:
            u = storage.get_user_by_id(uid)
            if u:
                users.append(u)
    updates_list = [
        pack_update_channel(channel_id),
        pack_update_new_channel_message(
            creator_msg, result['pts_channel'], storage),
        pack_update_read_channel_inbox(
            channel_id, int(creator_msg['id']), result['pts_channel'], 0),
    ]
    w_mid = Writer()
    w_mid.u32(tl.UPDATE_MESSAGE_ID)
    w_mid.i32(int(creator_msg['id']))
    w_mid.i64(0)
    updates_list.insert(0, w_mid.bytes_out())
    _fanout_channel_updates(
        storage, push_queue, session, channel_id, result, users,
        skip_user_id=viewer['id'])
    return packers.pack_updates(
        updates_list, users, user_id=viewer['id'], storage=storage,
        increment_seq=False, chats=[ch])


def handle_channels_create_channel(session, req_msg_id, r, storage,
                                   push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    title = r.string()
    about = r.string()
    if flags & 4:
        gcid = r.u32()
        if gcid == 0x48222ddd:
            gflags = r.i32()
            r.f64()
            r.f64()
            if gflags & 1:
                r.i32()
        r.string()
    if flags & 16:
        r.i32()

    try:
        ch = storage.create_channel(
            viewer['id'], title, [],
            megagroup=_create_channel_megagroup(flags))
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if about:
        storage._conn.execute(
            'UPDATE channels SET about=? WHERE id=?', (about, ch['id']))
        storage._conn.commit()
        ch = storage.get_channel(ch['id'])
    return _build_channel_create_updates(
        storage, viewer, ch, title,
        push_queue=push_queue, session=session)


def handle_messages_create_chat(session, req_msg_id, r, storage, push_queue,
                                registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    user_ids = parse_input_users(r, storage, viewer['id'])
    title = r.string()
    if flags & 1:
        r.i32()

    try:
        ch = storage.create_channel(
            viewer['id'], title, user_ids, megagroup=True)
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    updates = _build_channel_create_updates(
        storage, viewer, ch, title,
        member_ids=storage.list_channel_member_ids(ch['id']),
        push_queue=push_queue, session=session)
    return _pack_invited_users(updates)


def handle_channels_get_full_channel(session, req_msg_id, r, storage,
                                     push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    ch = storage.get_channel_by_access_hash(channel_id, access_hash)
    if not ch:
        return _rpc_error(400, 'CHANNEL_INVALID')
    is_member = storage.is_channel_member(channel_id, viewer['id'])
    if not is_member and not ch.get('username'):
        return _rpc_error(400, 'CHANNEL_PRIVATE')

    dialog = (
        storage.ensure_dialog(viewer['id'], channel_id)
        if is_member else {}
    )
    ch_full = dict(ch)
    ch_full['read_inbox_max_id'] = int(dialog.get('read_inbox_max_id') or 0)
    ch_full['read_outbox_max_id'] = int(dialog.get('read_outbox_max_id') or 0)
    ch_full['unread_count'] = int(dialog.get('unread_count') or 0)
    ch_full['admins_count'] = len(storage.list_channel_admin_ids(channel_id))
    ch_full['broadcast_messages_allowed'] = int(
        ch.get('broadcast_messages_allowed') or 0)
    ch_full['send_paid_messages_stars'] = storage.get_channel_direct_message_stars(
        channel_id)
    try:
        ch_full['invite_hash'] = storage.ensure_channel_invite_hash(channel_id)
    except ValueError:
        pass
    return _pack_messages_chat_full(ch_full, ch, viewer['id'], storage)


def handle_messages_get_full_chat(session, req_msg_id, r, storage, push_queue,
                                registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    chat_id = r.i64()
    if chat_id < config.CHANNEL_ID_MIN:
        return _rpc_error(400, 'CHAT_ID_INVALID')
    ch = storage.get_channel(int(chat_id))
    if not ch:
        return _rpc_error(400, 'CHAT_ID_INVALID')
    body = (Writer().u32(INPUT_CHANNEL).i64(chat_id)
            .i64(int(ch['access_hash'])).bytes_out())
    return handle_channels_get_full_channel(
        session, req_msg_id, tl.Reader(body), storage, push_queue, registry)


def handle_channels_get_channels(session, req_msg_id, r, storage, push_queue,
                                 registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    assert r.u32() == tl.VECTOR
    count = r.i32()
    channels = []
    for _ in range(count):
        parsed = parse_input_channel(r)
        if not parsed:
            continue
        channel_id, access_hash = parsed
        ch = storage.get_channel_by_access_hash(channel_id, access_hash)
        if ch:
            channels.append(ch)
    return _pack_messages_chats(channels, viewer['id'], storage=storage)


def handle_channels_invite_to_channel(session, req_msg_id, r, storage,
                                      push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    if not storage.get_channel_by_access_hash(channel_id, access_hash):
        return _rpc_error(400, 'CHANNEL_INVALID')

    user_ids = parse_input_users(r, storage, viewer['id'])
    try:
        added = storage.add_channel_members(channel_id, viewer['id'], user_ids)
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    ch = storage.get_channel(channel_id)
    users_map = {viewer['id']: viewer}
    for uid in added:
        u = storage.get_user_by_id(uid)
        if u:
            users_map[uid] = u
    updates = packers.pack_updates(
        [pack_update_channel(channel_id)], list(users_map.values()),
        user_id=viewer['id'], storage=storage, chats=[ch])
    return _pack_invited_users(updates)


def _join_result(session, updates_bytes: bytes) -> bytes:
    """channels.joinChannel / messages.importChatInvite return type.

    Layer 226+ wraps Updates into messages.ChatInviteJoinResult; older
    layers return Updates directly.
    """
    layer = int(getattr(session, 'client_layer', 0) or 0)
    if layer >= 226:
        w = Writer()
        w.u32(tl.MESSAGES_CHAT_INVITE_JOIN_RESULT_OK)
        w.raw(updates_bytes)
        return w.bytes_out()
    return updates_bytes


def handle_channels_join_channel(session, req_msg_id, r, storage, push_queue,
                                 registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    if not storage.get_channel_by_access_hash(channel_id, access_hash):
        return _rpc_error(400, 'CHANNEL_INVALID')
    try:
        storage.join_channel(channel_id, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    storage.add_channel_admin_log(
        channel_id, viewer['id'], 'participant_join', {})

    ch = storage.get_channel(channel_id)
    updates = packers.pack_updates(
        [pack_update_channel(channel_id)],
        [viewer], user_id=viewer['id'], storage=storage, chats=[ch])
    return _join_result(session, updates)


def handle_channels_leave_channel(session, req_msg_id, r, storage, push_queue,
                                  registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, _ = parsed
    try:
        storage.leave_channel(channel_id, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    storage.add_channel_admin_log(
        channel_id, viewer['id'], 'participant_leave', {})

    ch = storage.get_channel(channel_id)
    return packers.pack_updates(
        [pack_update_channel(channel_id)], [viewer],
        user_id=viewer['id'], storage=storage, chats=[ch] if ch else [])


def handle_channels_get_participants(session, req_msg_id, r, storage,
                                     push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    if not storage.get_channel_by_access_hash(channel_id, access_hash):
        return _rpc_error(400, 'CHANNEL_INVALID')

    _filter = _skip_channel_participants_filter(r)
    _offset = r.i32()
    _limit = r.i32()
    _hash = r.i64()

    if not storage.is_channel_member(channel_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')

    if _filter != 'admins' and not storage.is_channel_admin(
            channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')

    if _filter == 'admins':
        member_ids = storage.list_channel_admin_ids(channel_id)
    else:
        member_ids = storage.list_channel_member_ids(channel_id)
    ch = storage.get_channel(channel_id)
    users = []
    for uid in member_ids:
        u = storage.get_user_by_id(uid)
        if u:
            users.append(u)

    w = Writer()
    w.u32(CHANNEL_PARTICIPANTS)
    w.i32(len(member_ids))
    w.u32(tl.VECTOR)
    w.i32(len(member_ids))
    for uid in member_ids:
        w.raw(_pack_channel_participant(channel_id, uid, ch, storage))
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(packers.pack_channel(ch, viewer['id'], storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, storage=storage))
    return w.bytes_out()


def _collect_users_for_channel_messages(storage, messages: List[dict],
                                        viewer: dict) -> List[dict]:
    users_map = {viewer['id']: viewer}
    for m in messages:
        uid = int(m.get('from_id') or 0)
        if uid and uid not in users_map:
            u = storage.get_user_by_id(uid)
            if u:
                users_map[uid] = u
    return list(users_map.values())


def _fanout_channel_metadata(storage, push_queue, session, channel_id,
                             viewer, users: List[dict]):
    if push_queue is None:
        return
    ch = storage.get_channel(channel_id)
    upd = packers.pack_updates(
        [pack_update_channel(channel_id)], users,
        user_id=viewer['id'], storage=storage, chats=[ch],
        increment_seq=False)
    for uid in storage.list_channel_member_ids(channel_id):
        if uid == viewer['id']:
            continue
        push_queue.push_to_user(
            uid, upd, exclude_auth_key_id=session.auth_key_id)


def _pack_channel_metadata_updates(storage, viewer, channel_id: int,
                                   push_queue=None, session=None) -> bytes:
    ch = storage.get_channel(channel_id)
    chats = [ch] if ch else []
    mono = storage.get_channel_monoforum(channel_id)
    if mono:
        chats.append(mono)
    users = _collect_users_for_channel_messages(storage, [], viewer)
    _fanout_channel_metadata(
        storage, push_queue, session, channel_id, viewer, users)
    updates = [pack_update_channel(channel_id)]
    if mono:
        updates.append(pack_update_channel(int(mono['id'])))
    return packers.pack_updates(
        updates, users,
        user_id=viewer['id'], storage=storage, chats=chats)


def handle_updates_get_channel_difference(session, req_msg_id, r, storage,
                                          push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    try:
        channel_id, _, ch = _channel_from_request(
            r, storage, viewer['id'], allow_public=True)
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    _skip_channel_messages_filter(r)
    client_pts = r.i32()
    limit = r.i32()

    new_pts = int(ch['pts'])
    if not storage.is_channel_member(channel_id, viewer['id']):
        return _pack_channel_difference_empty(new_pts)
    if client_pts >= new_pts:
        return _pack_channel_difference_empty(new_pts)

    messages = storage.get_channel_difference_messages(
        viewer['id'], channel_id, client_pts, limit)
    if not messages:
        storage.ensure_channel_messages_for_member(channel_id, viewer['id'])
        messages = storage.get_channel_difference_messages(
            viewer['id'], channel_id, client_pts, limit)
    if not messages:
        if client_pts < new_pts:
            channel_top = int(ch.get('top_message_id') or 0)
            if channel_top <= 0:
                storage.sync_channel_member_pts(
                    channel_id, viewer['id'], new_pts)
                return _pack_channel_difference_empty(new_pts)
            log.warning(
                'Channel %d pts gap %d->%d for user %d without messages',
                channel_id, client_pts, new_pts, viewer['id'],
            )
            return _pack_channel_difference_too_long(new_pts)
        return _pack_channel_difference_empty(new_pts)

    storage.sync_channel_member_pts(channel_id, viewer['id'], new_pts)
    users = _collect_users_for_channel_messages(storage, messages, viewer)
    return _pack_channel_difference(
        ch, messages, users, new_pts,
        viewer['id'], storage=storage)


def handle_messages_get_chats(session, req_msg_id, r, storage, push_queue,
                              registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    assert r.u32() == tl.VECTOR
    count = r.i32()
    channels = []
    for _ in range(count):
        chat_id = int(r.i64())
        if chat_id < config.CHANNEL_ID_MIN:
            continue
        ch = storage.get_channel(chat_id)
        if ch and storage.is_channel_member(chat_id, viewer['id']):
            channels.append(ch)
    return _pack_messages_chats(channels, viewer['id'], storage=storage)


def handle_messages_edit_chat_title(session, req_msg_id, r, storage,
                                    push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    chat_id = int(r.i64())
    title = r.string()
    if chat_id < config.CHANNEL_ID_MIN:
        return _rpc_error(400, 'CHAT_ID_INVALID')
    if not storage.is_channel_member(chat_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    try:
        ch = storage.set_channel_title(chat_id, title, actor_id=viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    return _build_title_edit_updates(
        storage, viewer, chat_id, title, push_queue, session)


def handle_channels_edit_title(session, req_msg_id, r, storage, push_queue,
                               registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    if not storage.get_channel_by_access_hash(channel_id, access_hash):
        return _rpc_error(400, 'CHANNEL_INVALID')
    if not storage.is_channel_member(channel_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    title = r.string()
    try:
        storage.set_channel_title(channel_id, title, actor_id=viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    return _build_title_edit_updates(
        storage, viewer, channel_id, title, push_queue, session)


def handle_channels_check_username(session, req_msg_id, r, storage,
                                   push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    if not storage.get_channel_by_access_hash(channel_id, access_hash):
        return _rpc_error(400, 'CHANNEL_INVALID')
    username = r.string()
    ok = storage.is_channel_username_available(username, channel_id)
    w = Writer()
    w.u32(tl.BOOL_TRUE if ok else tl.BOOL_FALSE)
    return w.bytes_out()


def handle_channels_update_username(session, req_msg_id, r, storage,
                                    push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    if not storage.get_channel_by_access_hash(channel_id, access_hash):
        return _rpc_error(400, 'CHANNEL_INVALID')
    if not storage.is_channel_member(channel_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    ch = storage.get_channel(channel_id)
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    username = r.string()
    try:
        storage.set_channel_username(channel_id, username)
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    _fanout_channel_metadata(
        storage, push_queue, session, channel_id, viewer,
        _collect_users_for_channel_messages(storage, [], viewer))
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_toggle_signatures(session, req_msg_id, r, storage,
                                      push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    r.i32()  # flags
    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    if not storage.get_channel_by_access_hash(channel_id, access_hash):
        return _rpc_error(400, 'CHANNEL_INVALID')
    if not storage.is_channel_member(channel_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_reorder_usernames(session, req_msg_id, r, storage,
                                      push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    if not storage.get_channel_by_access_hash(channel_id, access_hash):
        return _rpc_error(400, 'CHANNEL_INVALID')
    if not storage.is_channel_member(channel_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    assert r.u32() == tl.VECTOR
    for _ in range(r.i32()):
        r.string()
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_channels_update_paid_messages_price(session, req_msg_id, r, storage,
                                               push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    ch = storage.get_channel_by_access_hash(channel_id, access_hash)
    if not ch:
        return _rpc_error(400, 'CHANNEL_INVALID')
    if int(ch.get('monoforum') or 0):
        return _rpc_error(400, 'CHANNEL_MONOFORUM_UNSUPPORTED')
    if not storage.is_channel_member(channel_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')

    stars = r.i64()
    storage.set_channel_paid_messages_price(
        channel_id, stars=stars,
        broadcast_messages_allowed=bool(flags & 1))
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_messages_update_pinned_message(session, req_msg_id, r, storage,
                                          push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    msg_id = int(r.i32())
    if not peer_id or peer_id < config.CHANNEL_ID_MIN:
        return _rpc_error(400, 'PEER_ID_INVALID')
    if not storage.is_channel_member(peer_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')

    unpin = bool(flags & 2)
    if unpin:
        ch = storage.set_channel_pinned_msg(peer_id, 0)
        return packers.pack_updates(
            [pack_update_channel(peer_id)], [viewer],
            user_id=viewer['id'], storage=storage, chats=[ch])

    ch = storage.set_channel_pinned_msg(peer_id, msg_id)
    action_json = json.dumps({'type': 'pin_message'}, ensure_ascii=False)
    result = storage.send_channel_message(
        peer_id, viewer['id'], '', [], 0, 0, action_json=action_json)
    sender_msg = result['sender_msg']
    users_map = {viewer['id']: viewer}
    for uid in result['member_ids']:
        if uid not in users_map:
            u = storage.get_user_by_id(uid)
            if u:
                users_map[uid] = u
    users = list(users_map.values())
    _fanout_channel_updates(
        storage, push_queue, session, peer_id, result, users,
        skip_user_id=viewer['id'])
    updates_list = [
        pack_update_channel(peer_id),
        pack_update_new_channel_message(
            sender_msg, result['pts_channel'], storage),
        pack_update_read_channel_inbox(
            peer_id, int(sender_msg['id']), result['pts_channel'], 0),
    ]
    return packers.pack_updates(
        updates_list, users, user_id=viewer['id'],
        storage=storage, chats=[ch])


def handle_channels_edit_photo(session, req_msg_id, r, storage, push_queue,
                               registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    if not storage.get_channel_by_access_hash(channel_id, access_hash):
        return _rpc_error(400, 'CHANNEL_INVALID')
    if not storage.is_channel_member(channel_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    r.u32()  # InputChatPhoto — ignore (empty / unchanged)
    ch = storage.get_channel(channel_id)
    return packers.pack_updates(
        [pack_update_channel(channel_id)], [viewer],
        user_id=viewer['id'], storage=storage, chats=[ch])


def handle_messages_edit_chat_photo(session, req_msg_id, r, storage,
                                    push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    chat_id = int(r.i64())
    r.u32()  # InputChatPhoto
    if chat_id < config.CHANNEL_ID_MIN:
        return _rpc_error(400, 'CHAT_ID_INVALID')
    ch = storage.get_channel(chat_id)
    if not ch or not storage.is_channel_member(chat_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    return packers.pack_updates(
        [pack_update_channel(chat_id)], [viewer],
        user_id=viewer['id'], storage=storage, chats=[ch])


def handle_messages_edit_chat_about(session, req_msg_id, r, storage,
                                    push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    about = r.string()
    if not peer_id or peer_id < config.CHANNEL_ID_MIN:
        return _rpc_error(400, 'PEER_ID_INVALID')
    if not storage.is_channel_member(peer_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    ch = storage.get_channel(peer_id)
    prev_about = (ch.get('about') or '') if ch else ''
    storage._conn.execute(
        'UPDATE channels SET about=? WHERE id=?', (about, peer_id))
    storage._conn.commit()
    if prev_about != about:
        storage.add_channel_admin_log(
            peer_id, viewer['id'], 'change_about',
            {'prev': prev_about, 'new': about})
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_messages_export_chat_invite(session, req_msg_id, r, storage,
                                       push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if flags & (1 << 0):
        r.i32()
    if flags & (1 << 1):
        r.i32()
    if flags & (1 << 4):
        r.string()
    if flags & (1 << 5):
        r.u32()

    if not peer_id or peer_id < config.CHANNEL_ID_MIN:
        return _rpc_error(400, 'PEER_ID_INVALID')
    if not storage.is_channel_member(peer_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    ch = storage.get_channel(peer_id)
    invite_hash = storage.ensure_channel_invite_hash(peer_id)
    link = f'https://t.me/+{invite_hash}'
    return pack_chat_invite_exported(
        link, viewer['id'], title=ch.get('title') or '')


def handle_messages_check_chat_invite(session, req_msg_id, r, storage,
                                      push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    invite_hash = _normalize_invite_hash(r.string())
    ch = storage.get_channel_by_invite_hash(invite_hash)
    if not ch:
        return _rpc_error(400, 'INVITE_HASH_EXPIRED')
    if storage.is_channel_member(ch['id'], viewer['id']):
        w = Writer()
        w.u32(CHAT_INVITE_ALREADY)
        w.raw(packers.pack_channel(ch, viewer['id'], storage=storage))
        return w.bytes_out()
    return pack_chat_invite(ch)


def handle_messages_import_chat_invite(session, req_msg_id, r, storage,
                                       push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    invite_hash = _normalize_invite_hash(r.string())
    ch = storage.get_channel_by_invite_hash(invite_hash)
    if not ch:
        return _rpc_error(400, 'INVITE_HASH_EXPIRED')
    try:
        storage.join_channel(ch['id'], viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    ch = storage.get_channel(ch['id'])
    return _join_result(session, packers.pack_updates(
        [pack_update_channel(ch['id'])], [viewer],
        user_id=viewer['id'], storage=storage, chats=[ch]))


def handle_channels_get_participant(session, req_msg_id, r, storage,
                                    push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    try:
        channel_id, _, ch = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    participant_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if not participant_id:
        return _rpc_error(400, 'PARTICIPANT_ID_INVALID')
    if not storage.is_channel_member(channel_id, participant_id):
        return _rpc_error(400, 'USER_NOT_PARTICIPANT')

    ch = storage.get_channel(channel_id)
    user = storage.get_user_by_id(participant_id)
    pw = _pack_channel_participant(channel_id, participant_id, ch, storage)

    w = Writer()
    w.u32(CHANNELS_CHANNEL_PARTICIPANT)
    w.raw(pw)
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(packers.pack_channel(ch, viewer['id'], storage=storage))
    w.u32(tl.VECTOR)
    w.i32(1 if user else 0)
    if user:
        w.raw(packers.pack_user(user, storage=storage))
    return w.bytes_out()


def handle_channels_read_history(session, req_msg_id, r, storage, push_queue,
                                 registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    parsed = parse_input_channel(r)
    if not parsed:
        return _rpc_error(400, 'CHANNEL_INVALID')
    channel_id, access_hash = parsed
    if not storage.get_channel_by_access_hash(channel_id, access_hash):
        return _rpc_error(400, 'CHANNEL_INVALID')
    max_id = r.i32()
    try:
        storage.read_channel_history(channel_id, viewer['id'], max_id)
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def deliver_to_peer(storage, push_queue, session, sender, peer_id, text,
                    entities, reply_to_msg_id, random_id,
                    media_document_id: int = 0,
                    media_photo_id: int = 0,
                    media_poll_json: str = '',
                    monoforum_peer_id: int = 0,
                    paid_message_stars: int = 0,
                    suggested_post_json: str = ''):
    """Route message to user DM or channel."""
    if peer_id >= config.CHANNEL_ID_MIN:
        ch = storage.get_channel(peer_id)
        if ch and int(ch.get('monoforum') or 0):
            topic_user_id = int(monoforum_peer_id or 0)
            return _deliver_monoforum_message(
                storage, push_queue, session, sender, peer_id, text, entities,
                reply_to_msg_id, random_id, media_document_id, media_photo_id,
                media_poll_json=media_poll_json, topic_user_id=topic_user_id,
                paid_message_stars=paid_message_stars,
                suggested_post_json=suggested_post_json)
        if not storage.is_channel_member(peer_id, sender['id']):
            if storage.can_send_direct_to_channel(peer_id, sender['id']):
                mono = storage.get_channel_monoforum(peer_id)
                return _deliver_monoforum_message(
                    storage, push_queue, session, sender, int(mono['id']),
                    text, entities, reply_to_msg_id, random_id,
                    media_document_id, media_photo_id,
                    media_poll_json=media_poll_json,
                    paid_message_stars=paid_message_stars,
                    suggested_post_json=suggested_post_json)
            from handlers.rpc import rpc_error
            return rpc_error(400, 'CHANNEL_PRIVATE')
        if not storage.can_post_to_channel(peer_id, sender['id']):
            if storage.can_send_direct_to_channel(peer_id, sender['id']):
                mono = storage.get_channel_monoforum(peer_id)
                return _deliver_monoforum_message(
                    storage, push_queue, session, sender, int(mono['id']),
                    text, entities, reply_to_msg_id, random_id,
                    media_document_id, media_photo_id,
                    media_poll_json=media_poll_json,
                    paid_message_stars=paid_message_stars,
                    suggested_post_json=suggested_post_json)
            from handlers.rpc import rpc_error
            return rpc_error(400, 'CHAT_ADMIN_REQUIRED')
        return _deliver_channel_message(
            storage, push_queue, session, sender, peer_id, text, entities,
            reply_to_msg_id, random_id, media_document_id, media_photo_id,
            media_poll_json=media_poll_json)
    from handlers.rpc import _deliver_private_message
    return _deliver_private_message(
        storage, push_queue, session, sender, peer_id, text, entities,
        reply_to_msg_id, random_id, media_document_id, media_photo_id,
        media_poll_json=media_poll_json)

def _read_messages_filter(r: tl.Reader) -> int:
    cid = r.u32()
    if cid == 0x9609a051:
        return cid
    if cid == 0xcedee1a0:
        return cid
    if cid == 0x3751b49e:
        return cid
    if cid == 0x5492e5a9:
        return cid
    if cid == 0x1bb00436:
        return cid
    if cid == 0x9fc00e65:
        return cid
    if cid == 0x70548afc:
        return cid
    if cid == 0x50f5c392:
        return cid
    if cid == 0x7a7c17a4:
        return cid
    if cid == 0xc1f8e165:
        r.i32()
        return cid
    if cid == 0x8031f93b:
        return cid
    if cid == 0x22201222:
        return cid
    if cid == 0x7ef0dd87:
        return cid
    if cid == 0x49a02969:
        return cid
    if cid == 0x9ed84a87:
        return cid
    if cid == 0x3c763c86:
        return cid
    return cid


def _skip_reaction(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0x1b2286b8:  # reactionEmoji
        r.string()
        return
    if cid == 0x8935fc73:  # reactionCustomEmoji
        r.i64()
        return
    if cid in (0x79f5d419, 0x523da4eb):  # reactionEmpty / reactionPaid
        return
    raise ValueError(f'unsupported Reaction {cid:#010x}')


def _skip_input_sticker_set(r: tl.Reader) -> None:
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


def _skip_input_geo_point(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0xe4c123d6:  # inputGeoPointEmpty
        return
    if cid == 0x48222faf:  # inputGeoPoint
        flags = r.i32()
        r.f64()
        r.f64()
        if flags & 1:
            r.i32()
        return
    raise ValueError(f'unsupported InputGeoPoint {cid:#010x}')


def _skip_input_chat_photo(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0x1ca48f57:  # inputChatPhotoEmpty
        return
    if cid == 0x8953ad37:  # inputChatPhoto
        packers.skip_input_photo(r)
        return
    raise ValueError(f'unsupported InputChatPhoto {cid:#010x}')


def _skip_emoji_status(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0x2de11aae:  # emojiStatusEmpty
        return
    if cid == 0xe7ff068a:  # emojiStatus
        flags = r.i32()
        r.i64()
        if flags & 1:
            r.i32()
        return
    if cid == 0x7184603b:  # emojiStatusCollectible
        flags = r.i32()
        r.i64()
        r.i64()
        r.string()
        r.string()
        r.i64()
        r.i32()
        r.i32()
        r.i32()
        r.i32()
        if flags & 1:
            r.i32()
        return
    raise ValueError(f'unsupported EmojiStatus {cid:#010x}')


def _skip_profile_tab(r: tl.Reader) -> None:
    cid = r.u32()
    if cid in (
            0xb98cd696, 0x4d4bd46a, 0x72c64955, 0xab339c00,
            0x9f27d26e, 0xe477092e, 0xd3656499, 0xa2c0f695):
        return
    raise ValueError(f'unsupported ProfileTab {cid:#010x}')


def _parse_input_message_ids(r: tl.Reader) -> List[int]:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    ids: List[int] = []
    for _ in range(count):
        cid = r.u32()
        if cid in (0xa676a322, 0xbad88395):  # inputMessageID/replyTo
            ids.append(int(r.i32()))
        elif cid == 0x86872538:  # inputMessagePinned
            # pinned alias; keep 0 marker, caller may ignore
            ids.append(0)
        elif cid == 0xacfa1a7e:  # inputMessageCallbackQuery
            ids.append(int(r.i32()))
            r.i64()
        else:
            raise ValueError(f'unsupported InputMessage {cid:#010x}')
    return [mid for mid in ids if mid > 0]


def _pack_search_counter(filter_cid: int, count: int = 0) -> bytes:
    w = Writer()
    w.u32(0xa187dcaf)  # messages.searchCounter
    w.i32(0)
    w.u32(filter_cid)
    w.i32(count)
    return w.bytes_out()


def handle_messages_get_search_counters(session, req_msg_id, r, storage,
                                        push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if flags & (1 << 2):
        packers.skip_input_peer(r)
    if flags & (1 << 0):
        r.i32()
    assert r.u32() == tl.VECTOR
    n = r.i32()
    filters = [_read_messages_filter(r) for _ in range(n)]
    total = 0
    if peer_id:
        total = storage.count_messages_for_peer(viewer['id'], peer_id)
    w = Writer()
    w.u32(tl.VECTOR)
    w.i32(len(filters))
    for fid in filters:
        count = total if fid == INPUT_MESSAGES_FILTER_EMPTY else 0
        w.raw(_pack_search_counter(fid, count))
    return w.bytes_out()


def handle_messages_search(session, req_msg_id, r, storage, push_queue,
                           registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id is None:
        return _rpc_error(400, 'PEER_ID_INVALID')
    _q = r.string()
    if flags & (1 << 0):
        packers.skip_input_peer(r)  # from_id
    if flags & (1 << 2):
        packers.skip_input_peer(r)  # saved_peer_id
    if flags & (1 << 3):
        assert r.u32() == tl.VECTOR
        rc = r.i32()
        for _ in range(rc):
            _skip_reaction(r)
    if flags & (1 << 1):
        r.i32()  # top_msg_id
    filter_id = _read_messages_filter(r)
    _min_date = r.i32()
    _max_date = r.i32()
    offset_id = r.i32()
    add_offset = r.i32()
    limit = r.i32()
    max_id = r.i32()
    min_id = r.i32()
    _hash = r.i64()

    chats = []
    if peer_id >= config.CHANNEL_ID_MIN:
        ch = storage.get_channel(peer_id)
        if ch and (storage.is_channel_member(peer_id, viewer['id'])
                   or ch.get('username')):
            chats = [ch]

    if filter_id != INPUT_MESSAGES_FILTER_EMPTY:
        return packers.pack_messages_slice(
            [], [], 0, viewer['id'], storage,
            offset_id=offset_id, add_offset=add_offset, chats=chats)

    storage.repair_dialog_top_message(viewer['id'], peer_id)
    messages, total = storage.get_messages_history(
        viewer['id'], peer_id, offset_id, add_offset, limit, max_id, min_id)
    users = storage.collect_users_for_messages(viewer['id'], messages)
    return packers.pack_messages_slice(
        messages, users, total, viewer['id'], storage,
        offset_id=offset_id, add_offset=add_offset, chats=chats)


def handle_messages_get_available_reactions(session, req_msg_id, r, storage,
                                            push_queue, registry):
    req_hash = r.i32()
    # 0x9da9403b is messages.getRecentStickers — wrong type crashes Desktop on signUp.
    reactions_hash = 0
    if req_hash and int(req_hash) == reactions_hash:
        w = Writer()
        w.u32(0x9f071957)  # messages.availableReactionsNotModified
        return w.bytes_out()
    w = Writer()
    w.u32(0x768e3aad)  # messages.availableReactions
    w.i32(reactions_hash)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_help_get_promo_data(session, req_msg_id, r, storage, push_queue,
                               registry):
    # Registration-safe: avoid chat-list promo/top-bar payloads.
    from shop_webapp.banner import pack_promo_data_empty
    return pack_promo_data_empty()


def handle_help_dismiss_suggestion(session, req_msg_id, r, storage, push_queue,
                                   registry):
  # Banner has no dismissible pending_suggestions; accept silently.
    packers.skip_input_peer(r)
    r.string()
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_messages_edit_chat_default_banned_rights(session, req_msg_id, r,
                                                  storage, push_queue,
                                                  registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    r.u32()  # ChatBannedRights — ignore
    if not peer_id or peer_id < config.CHANNEL_ID_MIN:
        return _rpc_error(400, 'PEER_ID_INVALID')
    if not storage.is_channel_member(peer_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    return _pack_channel_metadata_updates(
        storage, viewer, peer_id, push_queue, session)


def handle_channels_get_admined_public_channels(session, req_msg_id, r,
                                                storage, push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    r.i32()  # flags — for_personal etc.
    channels = storage.list_user_admined_public_channels(viewer['id'])
    w = Writer()
    w.u32(MESSAGES_CHATS)
    w.u32(tl.VECTOR)
    w.i32(len(channels))
    for ch in channels:
        w.raw(packers.pack_channel(ch, viewer['id'], storage=storage))
    return w.bytes_out()


def handle_channels_get_channel_recommendations(session, req_msg_id, r, storage,
                                              push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    if flags & 1:
        parse_input_channel(r)
    w = Writer()
    w.u32(MESSAGES_CHATS)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_messages_get_future_chat_creator_after_leave(session, req_msg_id, r,
                                                      storage, push_queue,
                                                      registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    packers.skip_input_peer(r)
    return packers.pack_user(viewer, storage=storage)


def pack_update_channel_participant(channel_id: int, actor_id: int,
                                    user_id: int, prev: bytes, new: bytes,
                                    date: int) -> bytes:
    flags = (1 << 0) | (1 << 1)
    w = Writer()
    w.u32(UPDATE_CHANNEL_PARTICIPANT)
    w.i32(flags)
    w.i64(channel_id)
    w.i32(date)
    w.i64(actor_id)
    w.i64(user_id)
    w.raw(prev)
    w.raw(new)
    w.i32(0)
    return w.bytes_out()


def _pack_admin_log_action(action_type: str, action_data: dict,
                           channel_id: int, ch: dict,
                           storage: Storage) -> bytes:
    w = Writer()
    if action_type == 'change_title':
        w.u32(0xe6dfb825)
        w.string(action_data.get('prev') or '')
        w.string(action_data.get('new') or '')
    elif action_type == 'change_about':
        w.u32(0x55188a2e)
        w.string(action_data.get('prev') or '')
        w.string(action_data.get('new') or '')
    elif action_type == 'participant_join':
        w.u32(0x183040d3)
    elif action_type == 'participant_leave':
        w.u32(0xf89777f2)
    elif action_type == 'toggle_admin':
        uid = int(action_data['user_id'])
        member = storage.get_channel_member_row(channel_id, uid)
        member_date = int(
            action_data.get('date')
            or (member.get('date') if member else time.time()))
        plain = Writer()
        plain.u32(CHANNEL_PARTICIPANT)
        plain.i32(0)
        plain.i64(uid)
        plain.i32(member_date)
        plain_bytes = plain.bytes_out()

        rank = (action_data.get('rank') or '').strip()
        admin = Writer()
        admin.u32(CHANNEL_PARTICIPANT_ADMIN)
        admin_flags = 1 << 2 if rank else 0
        admin.i32(admin_flags)
        admin.i64(uid)
        admin.i64(int(action_data.get('promoted_by')
                      or ch.get('creator_id') or 0))
        admin.i32(member_date)
        admin.raw(packers.pack_chat_admin_rights(
            (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)
            | (1 << 5) | (1 << 7) | (1 << 9) | (1 << 11) | (1 << 12)
            | (1 << 13) | (1 << 14) | (1 << 15) | (1 << 16)))
        if admin_flags & (1 << 2):
            admin.string(rank)
        admin_bytes = admin.bytes_out()

        if action_data.get('promoted'):
            prev = plain_bytes
            new = admin_bytes
        else:
            prev = admin_bytes
            new = plain_bytes
        w.u32(0xd5676710)
        w.raw(prev)
        w.raw(new)
    else:
        w.u32(0x183040d3)
    return w.bytes_out()


def _pack_admin_log_event(row: dict, ch: dict, storage: Storage) -> bytes:
    action_data = json.loads(row.get('action_json') or '{}')
    w = Writer()
    w.u32(CHANNEL_ADMIN_LOG_EVENT)
    w.i64(int(row['id']))
    w.i32(int(row['date']))
    w.i64(int(row['user_id']))
    w.raw(_pack_admin_log_action(
        row['action_type'], action_data, int(row['channel_id']), ch, storage))
    return w.bytes_out()


def handle_channels_edit_admin(session, req_msg_id, r, storage, push_queue,
                               registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    req_flags = r.i32()
    try:
        channel_id, _, ch = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if int(ch.get('creator_id') or 0) != int(viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')

    target = packers.parse_input_user(r, storage, self_user_id=viewer['id'])
    if not target:
        return _rpc_error(400, 'USER_ID_INVALID')
    try:
        rights = packers.parse_chat_admin_rights(r)
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    rank = r.string() if req_flags & 1 else None

    prev = _pack_channel_participant(channel_id, target['id'], ch, storage)
    try:
        if rights == 0:
            storage.set_channel_admin(
                channel_id, target['id'], is_admin=False)
        else:
            storage.set_channel_admin(
                channel_id, target['id'], is_admin=True,
                promoted_by=viewer['id'], rank=rank)
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    ch = storage.get_channel(channel_id)
    new = _pack_channel_participant(channel_id, target['id'], ch, storage)
    now = int(time.time())
    storage.add_channel_admin_log(
        channel_id, viewer['id'], 'toggle_admin',
        {'user_id': target['id'], 'promoted': rights != 0,
         'rank': rank, 'promoted_by': viewer['id'],
         'date': int((storage.get_channel_member_row(
             channel_id, target['id']) or {}).get('date') or now)})

    users = _collect_channel_users(storage, channel_id, viewer['id'])
    upd = pack_update_channel_participant(
        channel_id, viewer['id'], target['id'], prev, new, now)
    return packers.pack_updates(
        [upd, pack_update_channel(channel_id)], users,
        user_id=viewer['id'], storage=storage, chats=[ch])


def handle_channels_get_admin_log(session, req_msg_id, r, storage, push_queue,
                                  registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    req_flags = r.i32()
    try:
        channel_id, _, ch = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')

    _q = r.string()
    if req_flags & 1:
        r.i32()  # ChannelAdminLogEventsFilter flags
    if req_flags & 2:
        assert r.u32() == tl.VECTOR
        admin_count = r.i32()
        for _ in range(admin_count):
            packers.parse_input_user(r, storage, self_user_id=viewer['id'])
    max_id = r.i64()
    min_id = r.i64()
    limit = r.i32()

    rows = storage.list_channel_admin_log(
        channel_id, max_id=max_id, min_id=min_id, limit=limit)
    users = _collect_channel_users(storage, channel_id, viewer['id'])

    w = Writer()
    w.u32(CHANNELS_ADMIN_LOG_RESULTS)
    w.u32(tl.VECTOR)
    w.i32(len(rows))
    for row in rows:
        w.raw(_pack_admin_log_event(row, ch, storage))
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(packers.pack_channel(ch, viewer['id'], storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(
            u, is_self=int(u['id']) == int(viewer['id']), storage=storage))
    return w.bytes_out()


def handle_channels_edit_banned(session, req_msg_id, r, storage, push_queue,
                                registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    try:
        channel_id, _, ch = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if int(ch.get('creator_id') or 0) != int(viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')

    packers.skip_input_peer(r)
    r.u32()  # ChatBannedRights
    r.i32()
    r.i32()

    ch = storage.get_channel(channel_id)
    return packers.pack_updates(
        [pack_update_channel(channel_id)], [viewer],
        user_id=viewer['id'], storage=storage, chats=[ch])


def pack_update_delete_channel_messages(channel_id: int, msg_ids: List[int],
                                        pts: int, pts_count: int) -> bytes:
    w = Writer()
    w.u32(UPDATE_DELETE_CHANNEL_MESSAGES)
    w.i64(channel_id)
    w.u32(tl.VECTOR)
    w.i32(len(msg_ids))
    for mid in msg_ids:
        w.i32(int(mid))
    w.i32(int(pts))
    w.i32(int(pts_count))
    return w.bytes_out()


def _fanout_channel_delete(storage, push_queue, session, channel_id: int,
                           result: dict, viewer_id: int):
    if push_queue is None or session is None:
        return
    upd = pack_update_delete_channel_messages(
        channel_id, result['msg_ids'], result['pts'], result['pts_count'])
    ch = storage.get_channel(channel_id)
    users = []
    for uid in result['member_ids']:
        if uid == viewer_id:
            continue
        u = storage.get_user_by_id(uid)
        if u:
            users.append(u)
    packed = packers.pack_updates(
        [upd], users, user_id=viewer_id, storage=storage,
        increment_seq=False, chats=[ch] if ch else [])
    for uid in result['member_ids']:
        if uid == viewer_id:
            continue
        push_queue.push_to_user(
            uid, packed, exclude_auth_key_id=session.auth_key_id)


def _pack_affected_messages(pts: int, pts_count: int) -> bytes:
    from handlers.packers import pack_affected_messages
    return pack_affected_messages(pts, pts_count)


def _current_pts(storage, user_id: int) -> int:
    return int(storage.get_user_state(user_id)['pts'])


def _increment_pts_by(storage, user_id: int, count: int) -> int:
    if count <= 0:
        return _current_pts(storage, user_id)
    pts = 0
    for _ in range(count):
        pts = storage.increment_pts(user_id)
    return pts


def _repair_or_reset_dialog(storage, owner_id: int, peer_id: int) -> None:
    if not peer_id:
        return
    row = storage._conn.execute(
        'SELECT 1 FROM messages WHERE owner_id=? AND peer_id=? LIMIT 1',
        (owner_id, peer_id),
    ).fetchone()
    if row:
        storage.repair_dialog(owner_id, peer_id)
        return
    storage._conn.execute(
        '''UPDATE dialogs SET top_message_id=0, unread_count=0,
           last_message_date=0, last_outgoing=0, read_inbox_max_id=0,
           read_outbox_max_id=0 WHERE owner_id=? AND peer_id=?''',
        (owner_id, peer_id),
    )
    storage._conn.commit()


def _push_user_delete_messages(storage, push_queue, user_id: int,
                               msg_ids: List[int], pts: int,
                               pts_count: int,
                               exclude_auth_key_id: Optional[int] = None,
                               allow_enqueue: bool = True
                               ) -> None:
    if push_queue is None or not msg_ids:
        return
    upd = packers.pack_update_delete_messages(msg_ids, pts, pts_count)
    packed = packers.pack_updates(
        [upd], [], user_id=user_id, storage=storage, increment_seq=False)
    push_queue.push_to_user(
        user_id, packed, exclude_auth_key_id=exclude_auth_key_id,
        allow_enqueue=allow_enqueue)


def handle_channels_delete_messages(session, req_msg_id, r, storage,
                                    push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, ch = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    assert r.u32() == tl.VECTOR
    count = r.i32()
    msg_ids = [r.i32() for _ in range(count)]
    try:
        result = storage.delete_channel_messages(
            channel_id, msg_ids, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    _fanout_channel_delete(
        storage, push_queue, session, channel_id, result, viewer['id'])
    ch = storage.get_channel(channel_id)
    updates = packers.pack_updates(
        [pack_update_delete_channel_messages(
            channel_id, result['msg_ids'], result['pts'],
            result['pts_count'])],
        [viewer], user_id=viewer['id'], storage=storage, chats=[ch] if ch else [])
    return updates


def handle_messages_delete_messages(session, req_msg_id, r, storage,
                                    push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    _revoke = bool(flags & 1)
    assert r.u32() == tl.VECTOR
    count = r.i32()
    msg_ids = [r.i32() for _ in range(count)]
    import logging
    logging.getLogger(__name__).info(
        'delete messages viewer=%s revoke=%s ids=%s',
        viewer['id'], _revoke, msg_ids[:20])
    channel_id = None
    for mid in msg_ids:
        msg = storage.get_message(viewer['id'], mid)
        if msg and int(msg.get('peer_id') or 0) >= config.CHANNEL_ID_MIN:
            channel_id = int(msg['peer_id'])
            break
    if channel_id is not None:
        try:
            result = storage.delete_channel_messages(
                channel_id, msg_ids, viewer['id'])
        except ValueError as exc:
            return _rpc_error(400, str(exc))
        _fanout_channel_delete(
            storage, push_queue, session, channel_id, result, viewer['id'])
        ch = storage.get_channel(channel_id)
        return packers.pack_updates(
            [pack_update_delete_channel_messages(
                channel_id, result['msg_ids'], result['pts'],
                result['pts_count'])],
            [viewer], user_id=viewer['id'], storage=storage,
            chats=[ch] if ch else [])
    seen_ids = set()
    viewer_msgs = []
    for mid in msg_ids:
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        msg = storage.get_message(viewer['id'], mid)
        if msg:
            viewer_msgs.append(msg)

    viewer_msg_ids = [int(m['id']) for m in viewer_msgs]
    viewer_peer_ids = {
        int(m.get('peer_id') or 0)
        for m in viewer_msgs
        if int(m.get('peer_id') or 0) < config.CHANNEL_ID_MIN
    }
    peer_deletes: Dict[int, List[int]] = {}
    if _revoke:
        for msg in viewer_msgs:
            peer_id = int(msg.get('peer_id') or 0)
            if (peer_id <= 0 or peer_id == int(viewer['id'])
                    or peer_id >= config.CHANNEL_ID_MIN):
                continue
            peer_msg = storage.get_message(peer_id, int(msg['id']))
            if (peer_msg
                    and int(peer_msg.get('peer_id') or 0) == int(viewer['id'])):
                peer_deletes.setdefault(peer_id, []).append(int(msg['id']))

    for mid in viewer_msg_ids:
        storage._conn.execute(
            'DELETE FROM messages WHERE owner_id=? AND id=?',
            (viewer['id'], mid))
    for peer_id, mids in peer_deletes.items():
        for mid in mids:
            storage._conn.execute(
                'DELETE FROM messages WHERE owner_id=? AND id=?',
                (peer_id, mid))
    storage._conn.commit()

    for peer_id in viewer_peer_ids:
        _repair_or_reset_dialog(storage, viewer['id'], peer_id)
    for peer_id in peer_deletes:
        _repair_or_reset_dialog(storage, peer_id, viewer['id'])

    pts_count = len(viewer_msg_ids)
    pts = _increment_pts_by(storage, viewer['id'], pts_count)
    _push_user_delete_messages(
        storage, push_queue, viewer['id'], viewer_msg_ids, pts, pts_count,
        exclude_auth_key_id=getattr(session, 'auth_key_id', None),
        allow_enqueue=False)

    for peer_id, mids in peer_deletes.items():
        peer_pts_count = len(mids)
        peer_pts = _increment_pts_by(storage, peer_id, peer_pts_count)
        _push_user_delete_messages(
            storage, push_queue, peer_id, mids, peer_pts, peer_pts_count)

    logging.getLogger(__name__).info(
        'delete messages done viewer=%s deleted=%s pts=%s peer_deletes=%s',
        viewer['id'], viewer_msg_ids, pts,
        {pid: mids for pid, mids in peer_deletes.items()})
    return _pack_affected_messages(pts, pts_count)


def handle_messages_get_messages_views(session, req_msg_id, r, storage,
                                       push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id is None or peer_id < config.CHANNEL_ID_MIN:
        return _rpc_error(400, 'PEER_ID_INVALID')
    if not storage.is_channel_member(peer_id, viewer['id']):
        return _rpc_error(400, 'CHANNEL_PRIVATE')
    assert r.u32() == tl.VECTOR
    count = r.i32()
    msg_ids = [r.i32() for _ in range(count)]
    increment = r.u32() == tl.BOOL_TRUE
    if increment:
        storage.increment_message_views(peer_id, msg_ids, viewer['id'])
    ch = storage.get_channel(peer_id)
    w = Writer()
    w.u32(MESSAGES_MESSAGE_VIEWS)
    w.u32(tl.VECTOR)
    w.i32(len(msg_ids))
    for mid in msg_ids:
        stats = storage.get_message_views(peer_id, mid)
        vw = Writer()
        vw.u32(MESSAGE_VIEWS)
        flags = (1 << 0) | (1 << 1)
        vw.i32(flags)
        vw.i32(int(stats.get('views') or 0))
        vw.i32(int(stats.get('forwards') or 0))
        w.raw(vw.bytes_out())
    w.u32(tl.VECTOR)
    w.i32(1 if ch else 0)
    if ch:
        w.raw(packers.pack_channel(ch, viewer['id'], storage=storage))
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def _pack_exported_message_link(link: str, html: str = '') -> bytes:
    w = Writer()
    w.u32(EXPORTED_MESSAGE_LINK)
    w.string(link)
    w.string(html)
    return w.bytes_out()


def _pack_messages_inactive_chats(chats: List[dict], viewer_id: int,
                                  storage: Storage) -> bytes:
    w = Writer()
    w.u32(MESSAGES_INACTIVE_CHATS)
    w.u32(tl.VECTOR)
    w.i32(0)  # dates
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(packers.pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(0)  # users
    return w.bytes_out()


def _pack_search_posts_flood(*, query_is_free: bool = True,
                             total_daily: int = 100,
                             remains: int = 100) -> bytes:
    flags = 1 if query_is_free else 0
    w = Writer()
    w.u32(SEARCH_POSTS_FLOOD)
    w.i32(flags)
    w.i32(int(total_daily))
    w.i32(int(remains))
    return w.bytes_out()


def handle_channels_report_spam(session, req_msg_id, r, storage, push_queue,
                                registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    packers.skip_input_peer(r)
    assert r.u32() == tl.VECTOR
    for _ in range(r.i32()):
        r.i32()
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_channels_get_messages(session, req_msg_id, r, storage, push_queue,
                                 registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, ch = _channel_from_request(
            r, storage, viewer['id'], allow_public=True)
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    try:
        ids = _parse_input_message_ids(r)
    except (AssertionError, ValueError):
        return _rpc_error(400, 'MSG_ID_INVALID')
    messages = []
    for mid in ids:
        msg = storage.get_channel_message_for_viewer(
            channel_id, mid, viewer['id'])
        if msg:
            messages.append(msg)
    users = storage.collect_users_for_messages(viewer['id'], messages)
    return packers.pack_messages_messages(
        messages, users, viewer['id'], storage, chats=[ch] if ch else [])


def handle_channels_delete_channel(session, req_msg_id, r, storage, push_queue,
                                   registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, ch = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if int(ch.get('creator_id') or 0) != int(viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    member_ids = storage.list_channel_member_ids(channel_id)
    for uid in member_ids:
        storage.leave_channel(channel_id, uid)
    ch = storage.get_channel(channel_id)
    return packers.pack_updates(
        [pack_update_channel(channel_id)], [viewer],
        user_id=viewer['id'], storage=storage, chats=[ch] if ch else [])


def handle_channels_export_message_link(session, req_msg_id, r, storage,
                                        push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    try:
        channel_id, _, ch = _channel_from_request(
            r, storage, viewer['id'], allow_public=True)
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    msg_id = int(r.i32())
    # flags.0 grouped / flags.1 thread are accepted but ignored.
    _ = flags
    base = (ch.get('username') or '').strip()
    if base:
        link = f'https://t.me/{base}/{msg_id}'
    else:
        link = f'https://t.me/c/{channel_id}/{msg_id}'
    return _pack_exported_message_link(link)


def handle_channels_set_stickers(session, req_msg_id, r, storage, push_queue,
                                 registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, ch = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    try:
        _skip_input_sticker_set(r)
    except ValueError:
        return _rpc_error(400, 'STICKERSET_INVALID')
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_channels_read_message_contents(session, req_msg_id, r, storage,
                                          push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    assert r.u32() == tl.VECTOR
    for _ in range(r.i32()):
        r.i32()
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_channels_delete_history(session, req_msg_id, r, storage, push_queue,
                                   registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    max_id = int(r.i32())
    _ = bool(flags & 1)  # for_everyone
    rows = storage._conn.execute(
        '''SELECT id FROM messages
           WHERE owner_id=? AND peer_id=? AND from_id=?
             AND (? = 0 OR id <= ?)
           ORDER BY id ASC''',
        (viewer['id'], channel_id, viewer['id'], max_id, max_id),
    ).fetchall()
    msg_ids = [int(row['id']) for row in rows]
    if msg_ids:
        try:
            storage.delete_channel_messages(channel_id, msg_ids, viewer['id'])
        except ValueError:
            pass
    ch = storage.get_channel(channel_id)
    return packers.pack_updates(
        [pack_update_channel(channel_id)], [viewer],
        user_id=viewer['id'], storage=storage, chats=[ch] if ch else [])


def handle_channels_toggle_pre_history_hidden(session, req_msg_id, r, storage,
                                              push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, ch = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    _enabled = (r.u32() == tl.BOOL_TRUE)
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_get_left_channels(session, req_msg_id, r, storage,
                                      push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    _offset = r.i32()
    return _pack_messages_chats([], viewer['id'], storage=storage)


def handle_channels_set_discussion_group(session, req_msg_id, r, storage,
                                         push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
        group_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    if not storage.is_channel_admin(group_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_channels_edit_location(session, req_msg_id, r, storage, push_queue,
                                  registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    try:
        _skip_input_geo_point(r)
    except ValueError:
        return _rpc_error(400, 'GEO_POINT_INVALID')
    r.string()
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_channels_toggle_slow_mode(session, req_msg_id, r, storage,
                                     push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    _seconds = r.i32()
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_get_inactive_channels(session, req_msg_id, r, storage,
                                          push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    return _pack_messages_inactive_chats([], viewer['id'], storage)


def handle_channels_convert_to_gigagroup(session, req_msg_id, r, storage,
                                         push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, ch = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if int(ch.get('creator_id') or 0) != int(viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_delete_participant_history(session, req_msg_id, r, storage,
                                               push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    participant_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if not participant_id:
        return _rpc_error(400, 'PARTICIPANT_ID_INVALID')
    rows = storage._conn.execute(
        '''SELECT id FROM messages
           WHERE owner_id=? AND peer_id=? AND from_id=?
           ORDER BY id ASC''',
        (viewer['id'], channel_id, int(participant_id)),
    ).fetchall()
    ids = [int(row['id']) for row in rows]
    if not ids:
        ch = storage.get_channel(channel_id)
        return packers.pack_affected_history(int(ch.get('pts') or 0), 0, 0)
    try:
        result = storage.delete_channel_messages(channel_id, ids, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    _fanout_channel_delete(storage, push_queue, session, channel_id,
                           result, viewer['id'])
    return packers.pack_affected_history(result['pts'], result['pts_count'], 0)


def handle_channels_toggle_join_to_send(session, req_msg_id, r, storage,
                                        push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    _enabled = (r.u32() == tl.BOOL_TRUE)
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_toggle_join_request(session, req_msg_id, r, storage,
                                        push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    _enabled = (r.u32() == tl.BOOL_TRUE)
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_toggle_join_request_v228(session, req_msg_id, r, storage,
                                             push_queue, registry):
    """Layer 228: flags:# apply_to_invites:flags.1?true channel enabled
    guard_bot:flags.0?InputUser."""
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    _enabled = (r.u32() == tl.BOOL_TRUE)
    if flags & (1 << 0):
        packers._skip_input_user(r)  # guard_bot
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_toggle_username(session, req_msg_id, r, storage, push_queue,
                                    registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    r.string()
    _active = (r.u32() == tl.BOOL_TRUE)
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_channels_deactivate_all_usernames(session, req_msg_id, r, storage,
                                             push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_channels_toggle_forum(session, req_msg_id, r, storage, push_queue,
                                 registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    _enabled = (r.u32() == tl.BOOL_TRUE)
    _tabs = (r.u32() == tl.BOOL_TRUE)
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_toggle_anti_spam(session, req_msg_id, r, storage,
                                     push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    _enabled = (r.u32() == tl.BOOL_TRUE)
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_report_anti_spam_false_positive(session, req_msg_id, r,
                                                    storage, push_queue,
                                                    registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        _channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    r.i32()
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_channels_toggle_participants_hidden(session, req_msg_id, r, storage,
                                               push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    _enabled = (r.u32() == tl.BOOL_TRUE)
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_update_color(session, req_msg_id, r, storage, push_queue,
                                 registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    for_profile = bool(flags & (1 << 1))
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    color_id = -1
    bg_emoji = 0
    if flags & (1 << 2):
        color_id = int(r.i32())
    if flags & (1 << 0):
        bg_emoji = int(r.i64())
    if not (flags & (1 << 2)) and not (flags & (1 << 0)):
        color_id = -1
        bg_emoji = 0
    if bg_emoji and not storage.get_document_by_id(bg_emoji):
        return _rpc_error(400, 'DOCUMENT_INVALID')
    if color_id >= 0 and not is_valid_color_id(color_id, for_profile=for_profile):
        return _rpc_error(400, 'COLOR_INVALID')
    storage.set_channel_peer_color(
        channel_id,
        for_profile=for_profile,
        color_id=color_id,
        background_emoji_id=bg_emoji,
    )
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_toggle_view_forum_as_messages(session, req_msg_id, r,
                                                  storage, push_queue,
                                                  registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    _enabled = (r.u32() == tl.BOOL_TRUE)
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_update_emoji_status(session, req_msg_id, r, storage,
                                        push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    try:
        _skip_emoji_status(r)
    except ValueError:
        return _rpc_error(400, 'EMOJI_STATUS_INVALID')
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_set_boosts_to_unblock_restrictions(session, req_msg_id, r,
                                                       storage, push_queue,
                                                       registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    r.i32()
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_set_emoji_stickers(session, req_msg_id, r, storage,
                                       push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    try:
        _skip_input_sticker_set(r)
    except ValueError:
        return _rpc_error(400, 'STICKERSET_INVALID')
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_channels_restrict_sponsored_messages(session, req_msg_id, r, storage,
                                                push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    _restricted = (r.u32() == tl.BOOL_TRUE)
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_search_posts(session, req_msg_id, r, storage, push_queue,
                                 registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    if flags & 1:
        r.string()  # hashtag
    if flags & 2:
        r.string()  # query
    r.i32()  # offset_rate
    packers.skip_input_peer(r)  # offset_peer
    r.i32()  # offset_id
    _limit = r.i32()
    if flags & 4:
        r.i64()  # allow_paid_stars
    return packers.pack_messages_messages([], [], viewer['id'], storage, chats=[])


def handle_channels_toggle_autotranslation(session, req_msg_id, r, storage,
                                           push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    _enabled = (r.u32() == tl.BOOL_TRUE)
    return _pack_channel_metadata_updates(
        storage, viewer, channel_id, push_queue, session)


def handle_channels_get_message_author(session, req_msg_id, r, storage,
                                       push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    msg_id = int(r.i32())
    msg = storage.get_message(viewer['id'], msg_id)
    if not msg or int(msg.get('peer_id') or 0) != int(channel_id):
        return _rpc_error(400, 'MSG_ID_INVALID')
    action_json = msg.get('action_json')
    if isinstance(action_json, str) and action_json:
        try:
            action_json = json.loads(action_json)
        except json.JSONDecodeError:
            action_json = {}
    author_id = int(msg.get('from_id') or 0)
    if isinstance(action_json, dict) and action_json.get('type') == 'monoforum_direct':
        author_id = int(action_json.get('sender_id') or author_id)
    author = storage.get_user_by_id(author_id)
    if not author:
        return _rpc_error(400, 'USER_ID_INVALID')
    return packers.pack_user(
        author, is_self=int(author['id']) == int(viewer['id']), storage=storage)


def handle_channels_check_search_posts_flood(session, req_msg_id, r, storage,
                                             push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    if flags & 1:
        r.string()
    return _pack_search_posts_flood(query_is_free=True, total_daily=100, remains=100)


def handle_channels_set_main_profile_tab(session, req_msg_id, r, storage,
                                         push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    try:
        channel_id, _, _ = _channel_from_request(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not storage.is_channel_admin(channel_id, viewer['id']):
        return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    try:
        _skip_profile_tab(r)
    except ValueError:
        return _rpc_error(400, 'PROFILE_TAB_INVALID')
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


GROUP_HANDLERS = {
    MESSAGES_CREATE_CHAT: handle_messages_create_chat,
    CHANNELS_CREATE_CHANNEL: handle_channels_create_channel,
    MESSAGES_GET_CHATS: handle_messages_get_chats,
    MESSAGES_GET_FULL_CHAT: handle_messages_get_full_chat,
    MESSAGES_EDIT_CHAT_TITLE: handle_messages_edit_chat_title,
    MESSAGES_EXPORT_CHAT_INVITE: handle_messages_export_chat_invite,
    MESSAGES_CHECK_CHAT_INVITE: handle_messages_check_chat_invite,
    MESSAGES_IMPORT_CHAT_INVITE: handle_messages_import_chat_invite,
    CHANNELS_GET_FULL_CHANNEL: handle_channels_get_full_channel,
    CHANNELS_GET_CHANNELS: handle_channels_get_channels,
    CHANNELS_INVITE_TO_CHANNEL: handle_channels_invite_to_channel,
    CHANNELS_JOIN_CHANNEL: handle_channels_join_channel,
    CHANNELS_LEAVE_CHANNEL: handle_channels_leave_channel,
    CHANNELS_GET_PARTICIPANTS: handle_channels_get_participants,
    CHANNELS_GET_PARTICIPANT: handle_channels_get_participant,
    CHANNELS_EDIT_TITLE: handle_channels_edit_title,
    CHANNELS_EDIT_PHOTO: handle_channels_edit_photo,
    MESSAGES_EDIT_CHAT_PHOTO: handle_messages_edit_chat_photo,
    MESSAGES_EDIT_CHAT_ABOUT: handle_messages_edit_chat_about,
    CHANNELS_GET_SEND_AS: handle_channels_get_send_as,
    CHANNELS_CHECK_USERNAME: handle_channels_check_username,
    CHANNELS_UPDATE_USERNAME: handle_channels_update_username,
    CHANNELS_REORDER_USERNAMES: handle_channels_reorder_usernames,
    CHANNELS_TOGGLE_SIGNATURES: handle_channels_toggle_signatures,
    CHANNELS_UPDATE_PAID_MESSAGES_PRICE: (
        handle_channels_update_paid_messages_price),
    MESSAGES_EDIT_CHAT_DEFAULT_BANNED_RIGHTS: (
        handle_messages_edit_chat_default_banned_rights),
    MESSAGES_GET_SEARCH_COUNTERS: handle_messages_get_search_counters,
    MESSAGES_SEARCH: handle_messages_search,
    MESSAGES_GET_AVAILABLE_REACTIONS: handle_messages_get_available_reactions,
    HELP_GET_PROMO_DATA: handle_help_get_promo_data,
    HELP_DISMISS_SUGGESTION: handle_help_dismiss_suggestion,
    CHANNELS_GET_ADMINED_PUBLIC_CHANNELS: (
        handle_channels_get_admined_public_channels),
    CHANNELS_EDIT_ADMIN: handle_channels_edit_admin,
    CHANNELS_GET_ADMIN_LOG: handle_channels_get_admin_log,
    CHANNELS_EDIT_BANNED: handle_channels_edit_banned,
    CHANNELS_DELETE_MESSAGES: handle_channels_delete_messages,
    MESSAGES_DELETE_MESSAGES: handle_messages_delete_messages,
    MESSAGES_GET_MESSAGES_VIEWS: handle_messages_get_messages_views,
    MESSAGES_UPDATE_PINNED_MESSAGE: handle_messages_update_pinned_message,
    CHANNELS_READ_HISTORY: handle_channels_read_history,
    CHANNELS_REPORT_SPAM: handle_channels_report_spam,
    CHANNELS_GET_MESSAGES: handle_channels_get_messages,
    CHANNELS_GET_MESSAGES_V2: handle_channels_get_messages,
    CHANNELS_DELETE_CHANNEL: handle_channels_delete_channel,
    CHANNELS_EXPORT_MESSAGE_LINK: handle_channels_export_message_link,
    CHANNELS_SET_STICKERS: handle_channels_set_stickers,
    CHANNELS_READ_MESSAGE_CONTENTS: handle_channels_read_message_contents,
    CHANNELS_DELETE_HISTORY: handle_channels_delete_history,
    CHANNELS_TOGGLE_PRE_HISTORY_HIDDEN: handle_channels_toggle_pre_history_hidden,
    CHANNELS_GET_LEFT_CHANNELS: handle_channels_get_left_channels,
    CHANNELS_SET_DISCUSSION_GROUP: handle_channels_set_discussion_group,
    CHANNELS_EDIT_LOCATION: handle_channels_edit_location,
    CHANNELS_TOGGLE_SLOW_MODE: handle_channels_toggle_slow_mode,
    CHANNELS_GET_INACTIVE_CHANNELS: handle_channels_get_inactive_channels,
    CHANNELS_CONVERT_TO_GIGAGROUP: handle_channels_convert_to_gigagroup,
    CHANNELS_DELETE_PARTICIPANT_HISTORY: handle_channels_delete_participant_history,
    CHANNELS_TOGGLE_JOIN_TO_SEND: handle_channels_toggle_join_to_send,
    CHANNELS_TOGGLE_JOIN_REQUEST: handle_channels_toggle_join_request,
    tl.CHANNELS_TOGGLE_JOIN_REQUEST_V228: handle_channels_toggle_join_request_v228,
    tl.CHANNELS_JOIN_CHANNEL_V228: handle_channels_join_channel,
    tl.MESSAGES_IMPORT_CHAT_INVITE_V228: handle_messages_import_chat_invite,
    CHANNELS_TOGGLE_USERNAME: handle_channels_toggle_username,
    CHANNELS_DEACTIVATE_ALL_USERNAMES: handle_channels_deactivate_all_usernames,
    CHANNELS_TOGGLE_FORUM: handle_channels_toggle_forum,
    CHANNELS_TOGGLE_ANTI_SPAM: handle_channels_toggle_anti_spam,
    CHANNELS_REPORT_ANTI_SPAM_FALSE_POSITIVE: (
        handle_channels_report_anti_spam_false_positive),
    CHANNELS_TOGGLE_PARTICIPANTS_HIDDEN: handle_channels_toggle_participants_hidden,
    CHANNELS_UPDATE_COLOR: handle_channels_update_color,
    CHANNELS_TOGGLE_VIEW_FORUM_AS_MESSAGES: (
        handle_channels_toggle_view_forum_as_messages),
    CHANNELS_UPDATE_EMOJI_STATUS: handle_channels_update_emoji_status,
    CHANNELS_SET_BOOSTS_TO_UNBLOCK_RESTRICTIONS: (
        handle_channels_set_boosts_to_unblock_restrictions),
    CHANNELS_SET_EMOJI_STICKERS: handle_channels_set_emoji_stickers,
    CHANNELS_RESTRICT_SPONSORED_MESSAGES: (
        handle_channels_restrict_sponsored_messages),
    CHANNELS_SEARCH_POSTS: handle_channels_search_posts,
    CHANNELS_TOGGLE_AUTOTRANSLATION: handle_channels_toggle_autotranslation,
    CHANNELS_GET_MESSAGE_AUTHOR: handle_channels_get_message_author,
    CHANNELS_CHECK_SEARCH_POSTS_FLOOD: handle_channels_check_search_posts_flood,
    CHANNELS_SET_MAIN_PROFILE_TAB: handle_channels_set_main_profile_tab,
    UPDATES_GET_CHANNEL_DIFFERENCE: handle_updates_get_channel_difference,
    0x25a71742: handle_channels_get_channel_recommendations,
    0x3b7d0ea6: handle_messages_get_future_chat_creator_after_leave,
}
