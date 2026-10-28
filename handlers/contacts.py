"""contacts.block / unblock / getBlocked — corefork.telegram.org/api/block."""
from __future__ import annotations

import time

from handlers import packers
from mtproto import tl

CONTACTS_BLOCK = 0x2E2E8734
CONTACTS_UNBLOCK = 0xB550D328
CONTACTS_GET_BLOCKED = 0x9A868F80
PEER_BLOCKED = 0xE8FD8014
CONTACTS_BLOCKED = 0xADE3D0A5


def _rpc_error(code: int, message: str) -> bytes:
    w = tl.Writer()
    w.u32(tl.RPC_ERROR)
    w.i32(code)
    w.string(message)
    return w.bytes_out()


def handle_contacts_block(session, req_msg_id, r, storage, push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id is None or peer_id < 0:
        return _rpc_error(400, 'PEER_ID_INVALID')
    try:
        storage.block_user(viewer['id'], peer_id)
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    peer = storage.get_user_by_id(peer_id)
    if peer is not None:
        upd = packers.pack_updates(
            [packers.pack_update_peer_blocked(peer_id, blocked=True)],
            [peer],
            user_id=viewer['id'],
            storage=storage,
            increment_seq=True,
        )
        push_queue.push_to_user(viewer['id'], upd)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_contacts_unblock(session, req_msg_id, r, storage, push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id is None or peer_id < 0:
        return _rpc_error(400, 'PEER_ID_INVALID')
    storage.unblock_user(viewer['id'], peer_id)
    peer = storage.get_user_by_id(peer_id)
    if peer is not None:
        upd = packers.pack_updates(
            [packers.pack_update_peer_blocked(peer_id, blocked=False)],
            [peer],
            user_id=viewer['id'],
            storage=storage,
            increment_seq=True,
        )
        push_queue.push_to_user(viewer['id'], upd)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_contacts_get_blocked(session, req_msg_id, r, storage,
                                push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    blocked_ids = storage.list_blocked_user_ids(viewer['id'])
    users = []
    blocked_entries = []
    for uid in blocked_ids:
        u = storage.get_user_by_id(uid)
        if not u:
            continue
        users.append(u)
        bw = tl.Writer()
        bw.u32(PEER_BLOCKED)
        bw.raw(packers.pack_peer_user(uid))
        bw.i32(int(time.time()))
        blocked_entries.append(bw.bytes_out())
    w = tl.Writer()
    w.u32(CONTACTS_BLOCKED)
    w.u32(tl.VECTOR)
    w.i32(len(blocked_entries))
    for entry in blocked_entries:
        w.raw(entry)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(
            u, viewer_id=viewer['id'], storage=storage))
    return w.bytes_out()


CONTACTS_HANDLERS = {
    CONTACTS_BLOCK: handle_contacts_block,
    CONTACTS_UNBLOCK: handle_contacts_unblock,
    CONTACTS_GET_BLOCKED: handle_contacts_get_blocked,
}
