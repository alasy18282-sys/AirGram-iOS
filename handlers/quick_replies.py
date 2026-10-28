"""Telegram Business quick reply shortcuts."""
from __future__ import annotations

import hashlib
import json
import logging
import struct
from typing import List, Optional

from mtproto import tl
from handlers import packers

log = logging.getLogger(__name__)

QUICK_REPLY = 0x0697102b
MESSAGES_QUICK_REPLIES = 0xc68d6695
UPDATE_QUICK_REPLY_MESSAGE = 0x3e050d0f
UPDATE_NEW_QUICK_REPLY = 0xf53da717
UPDATE_DELETE_QUICK_REPLY = tl.UPDATE_DELETE_QUICK_REPLY


def parse_input_quick_reply_shortcut(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid == 0x24596d41:  # inputQuickReplyShortcut
        return {'type': 'name', 'shortcut': r.string()}
    if cid == 0x01190cf1:  # inputQuickReplyShortcutId
        return {'type': 'id', 'shortcut_id': int(r.i32())}
    if cid == 0x01190cf0:  # inputQuickReplyShortcutEmpty
        return {'type': 'empty'}
    raise ValueError(f'QUICK_REPLY_SHORTCUT_INVALID:{cid:#010x}')


def _hash_ids(ids: List[int]) -> int:
    h = 0
    for mid in ids:
        h = h ^ (h >> 21)
        h = h ^ ((h << 35) & 0xFFFFFFFFFFFFFFFF)
        h = h ^ (h >> 4)
        h = (h + int(mid)) & 0xFFFFFFFFFFFFFFFF
    if h >= 1 << 63:
        h -= 1 << 64
    return h


def _quick_replies_hash(shortcuts: List[dict]) -> int:
    parts: List[int] = []
    for item in shortcuts:
        sid = int(item.get('shortcut_id') or 0)
        name = (item.get('shortcut') or '').encode('utf-8')
        digest = hashlib.md5(name).hexdigest()[:16]
        parts.extend([
            sid & 0xFFFFFFFFFFFFFFFF,
            int(digest, 16),
            int(item.get('top_message_id') or 0) & 0xFFFFFFFFFFFFFFFF,
        ])
    return _hash_ids(parts)


def _quick_reply_messages_hash(messages: List[dict]) -> int:
    return _hash_ids([int(m.get('id') or 0) for m in messages])


def pack_quick_reply(shortcut_row: dict) -> bytes:
    w = tl.Writer()
    w.u32(QUICK_REPLY)
    w.i32(int(shortcut_row.get('shortcut_id') or 0))
    w.string((shortcut_row.get('shortcut') or '').strip())
    w.i32(int(shortcut_row.get('top_message_id') or 0))
    w.i32(int(shortcut_row.get('msg_count') or 0))
    return w.bytes_out()


def pack_messages_quick_replies(shortcuts: List[dict], messages: List[dict],
                                viewer: dict, storage) -> bytes:
    w = tl.Writer()
    w.u32(MESSAGES_QUICK_REPLIES)
    w.u32(tl.VECTOR)
    w.i32(len(shortcuts))
    for item in shortcuts:
        w.raw(pack_quick_reply(item))
    w.u32(tl.VECTOR)
    w.i32(len(messages))
    for msg in messages:
        w.raw(packers.pack_message(msg, storage))
    w.u32(tl.VECTOR)
    w.i32(0)  # chats
    users = [viewer]
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, is_self=True, viewer_id=u['id'], storage=storage))
    return w.bytes_out()


def pack_update_quick_reply_message(msg: dict, storage) -> bytes:
    w = tl.Writer()
    w.u32(UPDATE_QUICK_REPLY_MESSAGE)
    w.raw(packers.pack_message(msg, storage))
    return w.bytes_out()


def pack_update_new_quick_reply(shortcut_row: dict) -> bytes:
    w = tl.Writer()
    w.u32(UPDATE_NEW_QUICK_REPLY)
    w.raw(pack_quick_reply(shortcut_row))
    return w.bytes_out()


def pack_update_delete_quick_reply(shortcut_id: int) -> bytes:
    w = tl.Writer()
    w.u32(UPDATE_DELETE_QUICK_REPLY)
    w.i32(int(shortcut_id))
    return w.bytes_out()


def push_delete_quick_reply_update(storage, push_queue, user_id: int,
                                   shortcut_id: int, session=None) -> None:
    if push_queue is None:
        return
    user = storage.get_user_by_id(int(user_id))
    if not user:
        return
    upd = packers.pack_updates(
        [pack_update_delete_quick_reply(shortcut_id)],
        [user],
        user_id=int(user_id),
        storage=storage,
    )
    exclude = None
    if session is not None and hasattr(session, 'auth_key_id'):
        exclude = session.auth_key_id
    push_queue.push_to_user(int(user_id), upd, exclude_auth_key_id=exclude)


def deliver_quick_reply_message(storage, sender: dict, text: str, entities: list,
                                *, media_document_id: int = 0,
                                media_photo_id: int = 0,
                                media_poll_json: str = '',
                                random_id: int,
                                shortcut_info: dict) -> bytes:
    shortcut_id = storage.resolve_quick_reply_shortcut(
        int(sender['id']), shortcut_info)
    created_new_shortcut = storage.ensure_quick_reply_shortcut(
        int(sender['id']), shortcut_id,
        name=_shortcut_name(shortcut_info))
    msg, shortcut_row, _ = storage.add_quick_reply_message(
        int(sender['id']), shortcut_id, text, entities,
        media_document_id=media_document_id,
        media_photo_id=media_photo_id,
        media_poll_json=media_poll_json)
    updates = []
    if created_new_shortcut:
        updates.append(pack_update_new_quick_reply(shortcut_row))
    updates.append(pack_update_quick_reply_message(msg, storage))
    away = storage.get_business_away_message(int(sender['id']))
    if away and int(away.get('shortcut_id') or 0) <= 0 and int(shortcut_id) == -1:
        storage.patch_business_away_shortcut_id(int(sender['id']), shortcut_id)
    return packers.pack_updates(
        updates, [sender], user_id=int(sender['id']), storage=storage)


def _shortcut_name(shortcut_info: dict) -> str:
    if shortcut_info.get('type') == 'name':
        return (shortcut_info.get('shortcut') or '').strip()
    return ''


def handle_get_quick_replies(session, req_msg_id, r, storage, push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    req_hash = r.i64()
    shortcuts = storage.list_quick_reply_shortcuts(int(viewer['id']), public_only=True)
    current_hash = _quick_replies_hash(shortcuts)
    if req_hash and int(req_hash) == int(current_hash):
        w = tl.Writer()
        w.u32(tl.MESSAGES_QUICK_REPLIES_NOT_MODIFIED)
        return w.bytes_out()
    top_messages = storage.get_quick_reply_top_messages(int(viewer['id']), shortcuts)
    return pack_messages_quick_replies(
        shortcuts, top_messages, viewer, storage)


def handle_get_quick_reply_messages(session, req_msg_id, r, storage,
                                    push_queue, registry):
    from handlers.rpc import require_auth

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    shortcut_id = int(r.i32())
    msg_ids = None
    if flags & 1:
        assert r.u32() == tl.VECTOR
        msg_ids = [int(r.i32()) for _ in range(r.i32())]
    req_hash = r.i64()
    messages = storage.get_quick_reply_messages(
        int(viewer['id']), shortcut_id, msg_ids=msg_ids)
    current_hash = _quick_reply_messages_hash(messages)
    if req_hash and int(req_hash) == int(current_hash):
        w = tl.Writer()
        w.u32(tl.MESSAGES_MESSAGES_NOT_MODIFIED)
        w.i32(len(messages))
        return w.bytes_out()
    return packers.pack_messages_messages(messages, [viewer], viewer['id'], storage)


def send_shortcut_to_peer(storage, owner: dict, peer_id: int, shortcut_id: int,
                          *, offline: bool = True):
    messages = storage.get_quick_reply_messages(int(owner['id']), shortcut_id)
    if not messages:
        return None
    sent = []
    last_result = None
    for qr_msg in messages:
        entities = qr_msg.get('entities') or []
        if isinstance(entities, str):
            entities = json.loads(entities) if entities else []
        last_result = storage.send_private_message(
            int(owner['id']), int(peer_id),
            qr_msg.get('text') or '',
            entities,
            media_document_id=int(qr_msg.get('media_document_id') or 0),
            media_photo_id=int(qr_msg.get('media_photo_id') or 0),
            media_poll_json=qr_msg.get('media_poll_json') or '',
            deliver_to_recipient=True,
            offline=offline)
        if last_result.get('recipient_msg'):
            sent.append(last_result)
    if not sent:
        return None
    return {
        'recipient_msg': sent[-1]['recipient_msg'],
        'pts_recipient': sent[-1]['pts_recipient'],
        'users': [owner],
        'all_results': sent,
    }


def handle_delete_quick_reply_shortcut(session, req_msg_id, r, storage,
                                       push_queue, registry):
    from handlers.rpc import require_auth, rpc_error

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    shortcut_id = int(r.i32())
    if shortcut_id == -1:
        storage.clear_business_away_message(int(viewer['id']))
    elif not storage.delete_quick_reply_shortcut(int(viewer['id']), shortcut_id):
        return rpc_error(400, 'SHORTCUT_INVALID')

    push_delete_quick_reply_update(
        storage, push_queue, int(viewer['id']), shortcut_id, session=session)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()
