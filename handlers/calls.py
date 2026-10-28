"""1:1 phone call signalling.

The server never touches media: it assigns call ids, relays the Diffie-Hellman
public values between the two participants and hands out WebRTC endpoints
(TURN/STUN) that the clients feed into tgcalls.  Media then flows peer-to-peer
or through the TURN relay.
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import time
from typing import Dict, List, Optional

import config
from mtproto import tl
from handlers import packers

log = logging.getLogger(__name__)

PHONE_CALL_WAITING = 0xc5226f17
PHONE_CALL_REQUESTED = 0x14b0ed0c
PHONE_CALL_ACCEPTED = 0x3660c311
PHONE_CALL_FINAL = 0x30535af5
PHONE_CALL_DISCARDED = 0x50ca4de1
PHONE_CONNECTION_WEBRTC = 0x635fe375
PHONE_CALL_PROTOCOL = 0xfc878fc8
UPDATE_PHONE_CALL = 0xab0f6b1e
UPDATE_PHONE_CALL_SIGNALING_DATA = 0x2661bf09
PHONE_PHONE_CALL = 0xec82e140
INPUT_PHONE_CALL = 0x1e36fded
DATA_JSON = 0x7d748d04

REASON_MISSED = 0x85e42301
REASON_DISCONNECT = 0xe095c1a0
REASON_HANGUP = 0x57adc690
REASON_BUSY = 0xfaf7e8c9
REASON_MIGRATE = 0x9fbbf1f7

MESSAGES_GET_DH_CONFIG = 0x26cf8950
MESSAGES_DH_CONFIG = 0x2c221edd

# RFC 3526 MODP group 14: public 2048-bit safe prime, accepted by the
# clients' Utilities.isGoodPrime(p, 2).  The server only relays g_a/g_b, so a
# well-known good prime is all that is needed.
_DH_PRIME = bytes.fromhex(
    'FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1'
    '29024E088A67CC74020BBEA63B139B22514A08798E3404DD'
    'EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245'
    'E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED'
    'EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D'
    'C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F'
    '83655D23DCA3AD961C62F356208552BB9ED529077096966D'
    '670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B'
    'E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9'
    'DE2BCBF6955817183995497CEA956AE515D2261898FA0510'
    '15728E5A8AACAA68FFFFFFFFFFFFFFFF')
_DH_VERSION = 1

TURN_PORT = int(os.environ.get('MYTELEGRAM_TURN_PORT', '3478'))
TURN_USERNAME = os.environ.get('MYTELEGRAM_TURN_USERNAME', 'projectre')
TURN_PASSWORD = os.environ.get(
    'MYTELEGRAM_TURN_PASSWORD', 'projectre-calls-2026')

_ACTIVE_STATES = ('requested', 'waiting', 'accepted', 'active')
_CALL_TTL = 900
_CALLS: Dict[int, dict] = {}
_USER_ACTIVE: Dict[int, int] = {}


def _now() -> int:
    return int(time.time())


def _err(code: int, message: str) -> bytes:
    from handlers.rpc import rpc_error
    return rpc_error(code, message)


def _prune_calls() -> None:
    now = _now()
    for call_id, call in list(_CALLS.items()):
        if now - int(call['updated_at']) <= _CALL_TTL:
            continue
        _CALLS.pop(call_id, None)
        for user_id in (call['caller_id'], call['callee_id']):
            if _USER_ACTIVE.get(user_id) == call_id:
                _USER_ACTIVE.pop(user_id, None)


def _active_call_id(user_id: int) -> int:
    call_id = _USER_ACTIVE.get(int(user_id))
    if not call_id:
        return 0
    call = _CALLS.get(call_id)
    if call and call['state'] in _ACTIVE_STATES:
        return call_id
    _USER_ACTIVE.pop(int(user_id), None)
    return 0


def _parse_input_phone_call(r: tl.Reader) -> tuple[int, int]:
    cid = r.u32()
    if cid != INPUT_PHONE_CALL:
        raise ValueError(f'unsupported InputPhoneCall {cid:#010x}')
    return r.i64(), r.i64()


def _parse_protocol(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid != PHONE_CALL_PROTOCOL:
        raise ValueError(f'unsupported PhoneCallProtocol {cid:#010x}')
    flags = r.i32()
    min_layer = r.i32()
    max_layer = r.i32()
    assert r.u32() == tl.VECTOR
    versions = [r.string() for _ in range(r.i32())]
    return {
        'udp_p2p': bool(flags & 1),
        'udp_reflector': bool(flags & 2),
        'min_layer': min_layer,
        'max_layer': max_layer,
        'library_versions': versions,
    }


def _parse_reason(r: tl.Reader) -> tuple[int, str]:
    cid = r.u32()
    slug = ''
    if cid == REASON_MIGRATE:
        slug = r.string()
    return cid, slug


def _pack_protocol(protocol: Optional[dict]) -> bytes:
    proto = protocol or {}
    min_layer = int(proto.get('min_layer') or 65)
    max_layer = int(proto.get('max_layer') or 92)
    if max_layer < min_layer:
        max_layer = min_layer
    versions = list(proto.get('library_versions') or [])
    w = tl.Writer()
    w.u32(PHONE_CALL_PROTOCOL)
    # No udp_reflector: its relay protocol is Telegram-specific and we do not
    # run one.  Clients fall back to P2P/TURN, which we do provide.
    w.i32(1)  # udp_p2p
    w.i32(min_layer)
    w.i32(max_layer)
    w.u32(tl.VECTOR)
    w.i32(len(versions))
    for version in versions:
        w.string(version)
    return w.bytes_out()


def _pack_connections() -> bytes:
    host = config.SERVER_PUBLIC_IP
    endpoints = (
        # id, turn, stun, username, password
        (1, True, True, TURN_USERNAME, TURN_PASSWORD),
        (2, False, True, '', ''),
    )
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(endpoints))
    for endpoint_id, turn, stun, username, password in endpoints:
        flags = (1 if turn else 0) | (2 if stun else 0)
        w.u32(PHONE_CONNECTION_WEBRTC)
        w.i32(flags)
        w.i64(endpoint_id)
        w.string(host)
        w.string('')
        w.i32(TURN_PORT)
        w.string(username)
        w.string(password)
    return w.bytes_out()


def _pack_common(w: tl.Writer, ctor: int, flags: int, call: dict) -> None:
    w.u32(ctor)
    w.i32(flags)
    w.i64(int(call['id']))
    w.i64(int(call['access_hash']))
    w.i32(int(call['date']))
    w.i64(int(call['caller_id']))
    w.i64(int(call['callee_id']))


def _pack_waiting(call: dict, receive_date: int = 0) -> bytes:
    flags = 1 if receive_date else 0
    if call['video']:
        flags |= 1 << 6
    w = tl.Writer()
    _pack_common(w, PHONE_CALL_WAITING, flags, call)
    w.raw(_pack_protocol(call['protocol_caller']))
    if flags & 1:
        w.i32(int(receive_date))
    return w.bytes_out()


def _pack_requested(call: dict) -> bytes:
    flags = (1 << 6) if call['video'] else 0
    w = tl.Writer()
    _pack_common(w, PHONE_CALL_REQUESTED, flags, call)
    w.tl_bytes(call['g_a_hash'])
    w.raw(_pack_protocol(call['protocol_caller']))
    return w.bytes_out()


def _pack_accepted(call: dict) -> bytes:
    flags = (1 << 6) if call['video'] else 0
    w = tl.Writer()
    _pack_common(w, PHONE_CALL_ACCEPTED, flags, call)
    w.tl_bytes(call['g_b'])
    w.raw(_pack_protocol(call['protocol_callee'] or call['protocol_caller']))
    return w.bytes_out()


def _pack_final(call: dict, g_a_or_b: bytes) -> bytes:
    flags = 1 << 5  # p2p_allowed
    if call['video']:
        flags |= 1 << 6
    w = tl.Writer()
    _pack_common(w, PHONE_CALL_FINAL, flags, call)
    w.tl_bytes(g_a_or_b)
    w.i64(int(call['key_fingerprint']))
    w.raw(_pack_protocol(call['protocol_callee'] or call['protocol_caller']))
    w.raw(_pack_connections())
    w.i32(int(call['start_date'] or _now()))
    return w.bytes_out()


def _pack_reason(reason_cid: int, slug: str = '') -> bytes:
    w = tl.Writer()
    w.u32(reason_cid)
    if reason_cid == REASON_MIGRATE:
        w.string(slug)
    return w.bytes_out()


def _discard_stale(call: dict, storage, push_queue) -> None:
    """Drop our own call that never got answered.

    A client that crashed or lost connectivity mid-setup leaves a call in
    waiting/requested forever (TTL is 15 min), which would block any new
    call from that account.  Re-placing a call releases it.
    """
    call['state'] = 'discarded'
    call['reason_cid'] = REASON_MISSED
    call['reason_slug'] = 'missed'
    call['updated_at'] = _now()
    _dump_call(call, 'discarded (stale)')
    discarded = _pack_update_phone_call(_pack_discarded(
        call, reason_cid=REASON_MISSED, reason_slug='missed', duration=0))
    for uid in {int(call['caller_id']), int(call['callee_id'])}:
        _push_call_update(storage, push_queue, uid, discarded, call,
                          session=None, exclude_current=False)
        if _USER_ACTIVE.get(uid) == call['id']:
            _USER_ACTIVE.pop(uid, None)


def _pack_discarded(call: dict, *, reason_cid: int = 0, reason_slug: str = '',
                    duration: int = 0, need_rating: bool = False,
                    need_debug: bool = False) -> bytes:
    flags = 0
    if reason_cid:
        flags |= 1
    if duration:
        flags |= 1 << 1
    if need_rating:
        flags |= 1 << 2
    if need_debug:
        flags |= 1 << 3
    if call['video']:
        flags |= 1 << 6
    w = tl.Writer()
    w.u32(PHONE_CALL_DISCARDED)
    w.i32(flags)
    w.i64(int(call['id']))
    if flags & 1:
        w.raw(_pack_reason(reason_cid, reason_slug))
    if flags & (1 << 1):
        w.i32(int(duration))
    return w.bytes_out()


def _pack_update_phone_call(call_bytes: bytes) -> bytes:
    w = tl.Writer()
    w.u32(UPDATE_PHONE_CALL)
    w.raw(call_bytes)
    return w.bytes_out()


def _pack_update_signaling(call_id: int, data: bytes) -> bytes:
    w = tl.Writer()
    w.u32(UPDATE_PHONE_CALL_SIGNALING_DATA)
    w.i64(int(call_id))
    w.tl_bytes(data)
    return w.bytes_out()


def _call_users(storage, call: dict) -> List[dict]:
    users = []
    for user_id in (call['caller_id'], call['callee_id']):
        user = storage.get_user_by_id(int(user_id))
        if user:
            users.append(user)
    return users


def _pack_result(call_bytes: bytes, call: dict, viewer: dict,
                 storage) -> bytes:
    users = _call_users(storage, call)
    w = tl.Writer()
    w.u32(PHONE_PHONE_CALL)
    w.raw(call_bytes)
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for user in users:
        w.raw(packers.pack_user(
            user, is_self=int(user['id']) == int(viewer['id']),
            viewer_id=viewer['id'], storage=storage))
    return w.bytes_out()


def _push_call_update(storage, push_queue, recipient_id: int,
                      update_bytes: bytes, call: dict, *,
                      session=None, exclude_current: bool = False) -> None:
    if push_queue is None:
        return
    users = _call_users(storage, call)
    # phoneCall updates do not affect the account pts sequence: like the real
    # server they must carry seq=0, otherwise clients treat the growing seq as
    # a gap in the update state and ask for a getDifference instead of applying
    # the call update (which aborts call setup).
    update = packers.push_factory(
        packers.pack_updates, [update_bytes], users,
        user_id=int(recipient_id), storage=storage, increment_seq=False)
    exclude = None
    if exclude_current and session is not None:
        exclude = getattr(session, 'auth_key_id', None)
    push_queue.push_to_user(int(recipient_id), update,
                            exclude_auth_key_id=exclude)


def _new_call_id() -> int:
    return random.getrandbits(62) | (1 << 61)


def _new_access_hash() -> int:
    value = random.getrandbits(63)
    return -value if random.getrandbits(1) else value


def _dump_call(call: dict, event: str) -> None:
    log.info('call %s id=%s caller=%s callee=%s video=%s',
             event, call.get('id'), call.get('caller_id'),
             call.get('callee_id'), call.get('video'))


def handle_messages_get_dh_config(session, req_msg_id, r, storage,
                                  push_queue, registry):
    _version = r.i32()
    random_length = r.i32()
    random_length = max(0, min(int(random_length), 1024))
    w = tl.Writer()
    w.u32(MESSAGES_DH_CONFIG)
    w.i32(2)  # g
    w.tl_bytes(_DH_PRIME)
    w.i32(_DH_VERSION)
    w.tl_bytes(os.urandom(random_length))
    return w.bytes_out()


def handle_phone_get_call_config(session, req_msg_id, r, storage,
                                 push_queue, registry):
    from handlers.rpc import require_auth
    user, err = require_auth(session, storage)
    if user is None:
        return err
    w = tl.Writer()
    w.u32(DATA_JSON)
    w.string('{}')
    return w.bytes_out()


def handle_phone_request_call(session, req_msg_id, r, storage, push_queue,
                              registry):
    from handlers.rpc import require_auth
    from handlers.privacy import INPUT_PRIVACY_KEY_PHONE_CALL, can_view_privacy

    caller, err = require_auth(session, storage)
    if caller is None:
        return err
    _prune_calls()

    flags = r.i32()
    video = bool(flags & 1)
    target = packers.parse_input_user(r, storage, self_user_id=caller['id'])
    _random_id = r.i32()
    g_a_hash = r.tl_bytes()
    try:
        protocol = _parse_protocol(r)
    except ValueError:
        return _err(400, 'CALL_PROTOCOL_INVALID')

    if not target:
        return _err(400, 'USER_ID_INVALID')
    callee_id = int(target['id'])
    if (callee_id == int(caller['id']) or target.get('is_bot')
            or callee_id >= config.CHANNEL_ID_MIN):
        return _err(400, 'CALL_PEER_INVALID')
    if len(g_a_hash) != 32:
        return _err(400, 'CALL_PROTOCOL_INVALID')

    prev_id = _active_call_id(caller['id'])
    if prev_id:
        prev = _CALLS.get(prev_id)
        if prev and prev.get('state') in ('waiting', 'requested'):
            _discard_stale(prev, storage, push_queue)
        else:
            return _err(400, 'CALL_ALREADY_DECLINED')

    if (storage.is_blocked(callee_id, caller['id'])
            or storage.is_blocked(caller['id'], callee_id)):
        return _err(403, 'USER_PRIVACY_RESTRICTED')
    if not can_view_privacy(storage, callee_id, caller['id'],
                            INPUT_PRIVACY_KEY_PHONE_CALL):
        return _err(403, 'USER_PRIVACY_RESTRICTED')

    now = _now()
    call = {
        'id': _new_call_id(),
        'access_hash': _new_access_hash(),
        'caller_id': int(caller['id']),
        'callee_id': callee_id,
        'video': video,
        'date': now,
        'start_date': 0,
        'receive_date': 0,
        'state': 'waiting',
        'g_a_hash': g_a_hash,
        'g_a': b'',
        'g_b': b'',
        'key_fingerprint': 0,
        'protocol_caller': protocol,
        'protocol_callee': None,
        'reason_cid': 0,
        'reason_slug': '',
        'duration': 0,
        'updated_at': now,
        'created_at': now,
    }
    _CALLS[call['id']] = call
    _dump_call(call, 'requested')

    callee_prev_id = _active_call_id(callee_id)
    if callee_prev_id:
        # An incoming call whose caller is no longer connected can never be
        # answered (client crashed / lost network), so it must not keep the
        # callee busy until the TTL expires.
        callee_prev = _CALLS.get(callee_prev_id)
        caller_online = True
        if callee_prev is not None:
            try:
                caller_online = registry.has_live_connections(
                    int(callee_prev['caller_id']))
            except Exception:
                caller_online = True
        if (callee_prev is not None
                and callee_prev['state'] in ('waiting', 'requested')
                and not caller_online):
            _discard_stale(callee_prev, storage, push_queue)
            log.info('call %s released stale incoming call',
                     callee_prev['id'])
        else:
            call['state'] = 'discarded'
            call['reason_cid'] = REASON_BUSY
            call['updated_at'] = now
            _push_call_update(
                storage, push_queue, call['caller_id'],
                _pack_update_phone_call(_pack_discarded(
                    call, reason_cid=REASON_BUSY)),
                call, session=session, exclude_current=False)
            log.info('call %s callee busy', call['id'])
            return _pack_result(_pack_waiting(call), call, caller, storage)

    _USER_ACTIVE[call['caller_id']] = call['id']
    _USER_ACTIVE[callee_id] = call['id']

    _push_call_update(
        storage, push_queue, callee_id,
        _pack_update_phone_call(_pack_requested(call)),
        call, session=session, exclude_current=False)
    _push_call_update(
        storage, push_queue, call['caller_id'],
        _pack_update_phone_call(_pack_waiting(call)),
        call, session=session, exclude_current=True)
    return _pack_result(_pack_waiting(call), call, caller, storage)


def handle_phone_received_call(session, req_msg_id, r, storage, push_queue,
                               registry):
    from handlers.rpc import require_auth

    user, err = require_auth(session, storage)
    if user is None:
        return err
    try:
        call_id, access_hash = _parse_input_phone_call(r)
    except ValueError:
        return _err(400, 'CALL_PEER_INVALID')
    call = _CALLS.get(call_id)
    if (not call or int(call['access_hash']) != int(access_hash)
            or int(call['callee_id']) != int(user['id'])
            or call['state'] not in ('requested', 'waiting')):
        return _err(400, 'CALL_PEER_INVALID')

    if not call['receive_date']:
        call['receive_date'] = _now()
        call['updated_at'] = call['receive_date']
        call['state'] = 'requested'
        _push_call_update(
            storage, push_queue, call['caller_id'],
            _pack_update_phone_call(
                _pack_waiting(call, receive_date=call['receive_date'])),
            call, session=session, exclude_current=False)
        _push_call_update(
            storage, push_queue, call['callee_id'],
            _pack_update_phone_call(
                _pack_waiting(call, receive_date=call['receive_date'])),
            call, session=session, exclude_current=True)

    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_phone_accept_call(session, req_msg_id, r, storage, push_queue,
                             registry):
    from handlers.rpc import require_auth

    user, err = require_auth(session, storage)
    if user is None:
        return err
    try:
        call_id, access_hash = _parse_input_phone_call(r)
        g_b = r.tl_bytes()
        protocol = _parse_protocol(r)
    except ValueError:
        return _err(400, 'CALL_PEER_INVALID')

    call = _CALLS.get(call_id)
    if (not call or int(call['access_hash']) != int(access_hash)
            or int(call['callee_id']) != int(user['id'])):
        return _err(400, 'CALL_PEER_INVALID')
    if call['state'] not in ('requested', 'waiting'):
        return _err(400, 'CALL_ALREADY_DECLINED')

    call['g_b'] = g_b
    call['protocol_callee'] = protocol
    call['state'] = 'accepted'
    call['updated_at'] = _now()
    _dump_call(call, 'accepted')

    _push_call_update(
        storage, push_queue, call['caller_id'],
        _pack_update_phone_call(_pack_accepted(call)),
        call, session=session, exclude_current=False)
    _push_call_update(
        storage, push_queue, call['callee_id'],
        _pack_update_phone_call(_pack_accepted(call)),
        call, session=session, exclude_current=True)
    return _pack_result(_pack_accepted(call), call, user, storage)


def handle_phone_confirm_call(session, req_msg_id, r, storage, push_queue,
                              registry):
    from handlers.rpc import require_auth

    user, err = require_auth(session, storage)
    if user is None:
        return err
    try:
        call_id, access_hash = _parse_input_phone_call(r)
        g_a = r.tl_bytes()
        key_fingerprint = r.i64()
        protocol = _parse_protocol(r)
    except ValueError:
        return _err(400, 'CALL_PEER_INVALID')

    call = _CALLS.get(call_id)
    if (not call or int(call['access_hash']) != int(access_hash)
            or int(call['caller_id']) != int(user['id'])):
        return _err(400, 'CALL_PEER_INVALID')
    if call['state'] != 'accepted':
        return _err(400, 'CALL_ALREADY_DECLINED')
    if not call['g_b']:
        return _err(400, 'CALL_PROTOCOL_INVALID')

    if hashlib.sha256(g_a).digest() != bytes(call['g_a_hash']):
        log.warning('call %s confirm g_a hash mismatch', call_id)
        return _err(400, 'CALL_PROTOCOL_INVALID')

    call['g_a'] = g_a
    call['key_fingerprint'] = key_fingerprint
    call['protocol_caller'] = protocol
    call['state'] = 'active'
    call['start_date'] = _now()
    call['updated_at'] = call['start_date']
    _dump_call(call, 'active')

    _push_call_update(
        storage, push_queue, call['callee_id'],
        _pack_update_phone_call(_pack_final(call, call['g_a'])),
        call, session=session, exclude_current=False)
    _push_call_update(
        storage, push_queue, call['caller_id'],
        _pack_update_phone_call(_pack_final(call, call['g_b'])),
        call, session=session, exclude_current=True)
    return _pack_result(_pack_final(call, call['g_b']), call, user, storage)


def handle_phone_discard_call(session, req_msg_id, r, storage, push_queue,
                              registry):
    from handlers.rpc import require_auth

    user, err = require_auth(session, storage)
    if user is None:
        return err
    flags = r.i32()
    _video = bool(flags & 1)
    try:
        call_id, access_hash = _parse_input_phone_call(r)
    except ValueError:
        return _err(400, 'CALL_PEER_INVALID')
    duration = max(0, r.i32())
    reason_cid, reason_slug = _parse_reason(r)
    _connection_id = r.i64()

    call = _CALLS.get(call_id)
    if (not call or int(call['access_hash']) != int(access_hash)
            or int(user['id']) not in (int(call['caller_id']),
                                       int(call['callee_id']))):
        return _err(400, 'CALL_PEER_INVALID')

    if call['state'] in _ACTIVE_STATES:
        was_active = call['state'] == 'active'
        call['state'] = 'discarded'
        call['reason_cid'] = reason_cid
        call['reason_slug'] = reason_slug
        call['duration'] = duration
        call['updated_at'] = _now()
        _dump_call(call, 'discarded reason=%#010x duration=%d'
                   % (reason_cid, duration))
        need_rating = bool(was_active and duration >= 5)
        discarded = _pack_update_phone_call(_pack_discarded(
            call, reason_cid=reason_cid, reason_slug=reason_slug,
            duration=duration, need_rating=need_rating))
        for uid in {int(call['caller_id']), int(call['callee_id'])}:
            _push_call_update(
                storage, push_queue, uid, discarded, call, session=session,
                exclude_current=(uid == int(user['id'])))
        for uid in (int(call['caller_id']), int(call['callee_id'])):
            if _USER_ACTIVE.get(uid) == call_id:
                _USER_ACTIVE.pop(uid, None)

    return packers.pack_updates(
        [], [], user_id=int(user['id']), storage=storage)


def handle_phone_send_signaling_data(session, req_msg_id, r, storage,
                                     push_queue, registry):
    from handlers.rpc import require_auth

    user, err = require_auth(session, storage)
    if user is None:
        return err
    try:
        call_id, access_hash = _parse_input_phone_call(r)
    except ValueError:
        return _err(400, 'CALL_PEER_INVALID')
    data = r.tl_bytes()

    call = _CALLS.get(call_id)
    if (not call or int(call['access_hash']) != int(access_hash)
            or int(user['id']) not in (int(call['caller_id']),
                                       int(call['callee_id']))):
        return _err(400, 'CALL_PEER_INVALID')
    if call['state'] != 'active':
        log.debug('signaling for call %s ignored in state %s',
                  call_id, call['state'])
        return _err(400, 'CALL_ALREADY_DECLINED')

    peer_id = (call['callee_id'] if int(user['id']) == int(call['caller_id'])
               else call['caller_id'])
    _push_call_update(
        storage, push_queue, peer_id,
        _pack_update_signaling(call_id, data),
        call, session=session, exclude_current=False)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_phone_set_call_rating(session, req_msg_id, r, storage, push_queue,
                                 registry):
    from handlers.rpc import require_auth

    user, err = require_auth(session, storage)
    if user is None:
        return err
    _flags = r.i32()
    try:
        call_id, _access_hash = _parse_input_phone_call(r)
    except ValueError:
        return _err(400, 'CALL_PEER_INVALID')
    rating = r.i32()
    comment = r.string()
    log.info('call rating id=%s user=%s rating=%s comment_len=%d',
             call_id, user['id'], rating, len(comment or ''))
    return packers.pack_updates(
        [], [], user_id=int(user['id']), storage=storage)


def handle_phone_save_call_debug(session, req_msg_id, r, storage, push_queue,
                                 registry):
    from handlers.rpc import require_auth

    user, err = require_auth(session, storage)
    if user is None:
        return err
    try:
        call_id, _access_hash = _parse_input_phone_call(r)
    except ValueError:
        return _err(400, 'CALL_PEER_INVALID')
    if r.remaining() >= 4 and r.u32() == DATA_JSON:
        r.string()
    log.info('call debug id=%s user=%s', call_id, user['id'])
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_phone_save_call_log(session, req_msg_id, r, storage, push_queue,
                               registry):
    from handlers.rpc import require_auth

    user, err = require_auth(session, storage)
    if user is None:
        return err
    try:
        call_id, _access_hash = _parse_input_phone_call(r)
    except ValueError:
        return _err(400, 'CALL_PEER_INVALID')
    if r.remaining() >= 4:
        file_cid = r.u32()
        if file_cid in (0xf52ff27f, 0xfa4f0bb5):  # inputFile/inputFileBig
            r.i64()
            r.i32()
            r.string()
    log.info('call log id=%s user=%s', call_id, user['id'])
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


CALL_HANDLERS = {
    MESSAGES_GET_DH_CONFIG: handle_messages_get_dh_config,
    0x55451fa9: handle_phone_get_call_config,       # phone.getCallConfig
    0x42ff96ed: handle_phone_request_call,          # phone.requestCall
    0x17d54f61: handle_phone_received_call,         # phone.receivedCall
    0x3bd2b4a0: handle_phone_accept_call,           # phone.acceptCall
    0x2efe1722: handle_phone_confirm_call,          # phone.confirmCall
    0xb2cbc1c0: handle_phone_discard_call,          # phone.discardCall
    0xff7a9383: handle_phone_send_signaling_data,   # phone.sendSignalingData
    0x59ead627: handle_phone_set_call_rating,       # phone.setCallRating
    0x277add7e: handle_phone_save_call_debug,       # phone.saveCallDebug
    0x41248786: handle_phone_save_call_log,         # phone.saveCallLog
}
