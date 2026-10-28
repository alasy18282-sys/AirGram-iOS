"""Paid messages for private chats — corefork.telegram.org/api/paid-messages

Scope (private users only, not supergroups/channels):
  - globalPrivacySettings.noncontact_peers_paid_stars
  - inputPrivacyKeyNoPaidMessages exceptions
  - user.send_paid_messages_stars / userFull.send_paid_messages_stars
  - peerSettings.charge_paid_message_stars
  - allow_paid_stars on messages.sendMessage / sendMedia
  - users.getRequirementsToContact
  - account.toggleNoPaidMessagesException
  - account.getPaidMessagesRevenue
"""
import logging
from typing import List, Optional, Tuple

import config
from mtproto import tl
from handlers import packers

log = logging.getLogger(__name__)

# ── TL constructors (layer 227) ─────────────────────────────────────────────

GLOBAL_PRIVACY_SETTINGS = 0xfe41b34f
DISALLOWED_GIFTS_SETTINGS = 0x71f276c4

INPUT_PRIVACY_KEY_NO_PAID_MESSAGES = 0xbdc597b4
PRIVACY_VALUE_ALLOW_USERS = 0xb8905fb2
PRIVACY_VALUE_DISALLOW_ALL = 0x8b73e763

ACCOUNT_SET_GLOBAL_PRIVACY_SETTINGS = 0x1edaaac2
ACCOUNT_TOGGLE_NO_PAID_MESSAGES_EXCEPTION = 0xfe2eda76
ACCOUNT_GET_PAID_MESSAGES_REVENUE = 0x19ba4a67
ACCOUNT_PAID_MESSAGES_REVENUE = 0x1e109708

USERS_GET_REQUIREMENTS_TO_CONTACT = 0xd89a83a3
REQUIREMENT_TO_CONTACT_PAID_MESSAGES = 0xb4f67e93
REQUIREMENT_TO_CONTACT_EMPTY = 0x050a9839

MESSAGE_ACTION_PAID_MESSAGES_PRICE = 0x84b88578
MESSAGE_ACTION_PAID_MESSAGES_REFUNDED = 0xac1f1fcd
MESSAGE_SERVICE = 0x7a800e0a

INPUT_PRIVACY_VALUE_ALLOW_USERS = 0x131cc67f
INPUT_PRIVACY_VALUE_DISALLOW_ALL = 0xd66b66c9
INPUT_PRIVACY_VALUE_ALLOW_ALL = 0x184b35ce
INPUT_PRIVACY_VALUE_DISALLOW_USERS = 0x90110467
INPUT_PRIVACY_VALUE_ALLOW_CHAT_PARTICIPANTS = 0x840649cf
INPUT_PRIVACY_VALUE_DISALLOW_CHAT_PARTICIPANTS = 0xe94f0f86


def _rpc_error(code: int, message: str) -> bytes:
    w = tl.Writer()
    w.u32(tl.RPC_ERROR)
    w.i32(code)
    w.string(message)
    return w.bytes_out()


# ── globalPrivacySettings ───────────────────────────────────────────────────

def _skip_disallowed_gifts_settings(r: tl.Reader) -> None:
    r.u32()
    r.i32()


def parse_global_privacy_settings(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid != GLOBAL_PRIVACY_SETTINGS:
        raise ValueError(f'unexpected GlobalPrivacySettings {cid:#010x}')
    flags = r.i32()
    settings: dict = {
        'archive_and_mute_new_noncontact_peers': bool(flags & (1 << 0)),
        'keep_archived_unmuted': bool(flags & (1 << 1)),
        'keep_archived_folders': bool(flags & (1 << 2)),
        'hide_read_marks': bool(flags & (1 << 3)),
        'new_noncontact_peers_require_premium': bool(flags & (1 << 4)),
        'noncontact_peers_paid_stars': int(r.i64()) if flags & (1 << 5) else 0,
        'display_gifts_button': bool(flags & (1 << 7)),
        'disallowed_gifts': {},
    }
    if flags & (1 << 6):
        dg_cid = r.u32()
        if dg_cid == DISALLOWED_GIFTS_SETTINGS:
            dg_flags = r.i32()
            settings['disallowed_gifts'] = {
                'disallow_unlimited_stargifts': bool(dg_flags & 1),
                'disallow_limited_stargifts': bool(dg_flags & 2),
                'disallow_unique_stargifts': bool(dg_flags & 4),
                'disallow_premium_gifts': bool(dg_flags & 8),
                'disallow_stargifts_from_channels': bool(dg_flags & 16),
            }
    return settings


def pack_global_privacy_settings(user: Optional[dict] = None,
                                 storage=None) -> bytes:
    gp = {}
    if user and storage is not None:
        gp = storage.get_global_privacy(user['id'])
    elif user:
        gp = {
            'noncontact_peers_paid_stars': int(
                user.get('noncontact_peers_paid_stars') or 0),
            'display_gifts_button': True,
        }
    flags = 0
    if gp.get('archive_and_mute_new_noncontact_peers'):
        flags |= 1 << 0
    if gp.get('keep_archived_unmuted'):
        flags |= 1 << 1
    if gp.get('keep_archived_folders'):
        flags |= 1 << 2
    if gp.get('hide_read_marks'):
        flags |= 1 << 3
    if gp.get('new_noncontact_peers_require_premium'):
        flags |= 1 << 4
    stars = int(gp.get('noncontact_peers_paid_stars') or 0)
    if stars > 0:
        flags |= 1 << 5
    disallowed = gp.get('disallowed_gifts') or {}
    dg_flags = 0
    if disallowed.get('disallow_unlimited_stargifts'):
        dg_flags |= 1
    if disallowed.get('disallow_limited_stargifts'):
        dg_flags |= 2
    if disallowed.get('disallow_unique_stargifts'):
        dg_flags |= 4
    if disallowed.get('disallow_premium_gifts'):
        dg_flags |= 8
    if disallowed.get('disallow_stargifts_from_channels'):
        dg_flags |= 16
    if dg_flags:
        flags |= 1 << 6
    if gp.get('display_gifts_button', True) and user and user.get('premium'):
        flags |= 1 << 7
    w = tl.Writer()
    w.u32(GLOBAL_PRIVACY_SETTINGS)
    w.i32(flags)
    if flags & (1 << 5):
        w.i64(stars)
    if flags & (1 << 6):
        w.u32(DISALLOWED_GIFTS_SETTINGS)
        w.i32(dg_flags)
    return w.bytes_out()


def pack_paid_messages_revenue(stars: int) -> bytes:
    w = tl.Writer()
    w.u32(ACCOUNT_PAID_MESSAGES_REVENUE)
    w.i64(int(stars))
    return w.bytes_out()


# ── Serialization helpers (used by packers.py) ──────────────────────────────

def user_send_paid_messages_stars(u: dict) -> int:
    """user.send_paid_messages_stars — price to message this user."""
    if u.get('is_bot'):
        return 0
    return max(0, int(u.get('noncontact_peers_paid_stars') or 0))


def user_full_paid_stars_for_viewer(storage, target: dict,
                                    viewer_id: Optional[int],
                                    *, viewer_is_self: bool) -> Optional[int]:
    """userFull.send_paid_messages_stars — viewer-specific (0 = exempt)."""
    if viewer_is_self or viewer_id is None:
        return None
    price = user_send_paid_messages_stars(target)
    if price <= 0:
        return None
    if storage.is_paid_message_exempt(target['id'], viewer_id):
        return 0
    return price


def peer_charge_stars(storage, peer_id: int, viewer_id: int) -> Optional[int]:
    """peerSettings.charge_paid_message_stars — only for the paid-messages owner.

    Per corefork: users who must pay *us* have this flag set in their peerSettings
    when we (the owner) view them. Payers learn the price via userFull.send_paid_messages_stars.
    """
    if peer_id == viewer_id:
        return None
    if user_send_paid_messages_stars(
            storage.get_user_by_id(viewer_id) or {}) <= 0:
        return None
    required = storage.get_required_paid_message_stars(viewer_id, peer_id)
    return required if required > 0 else None


def can_view_paid_messages_revenue(u: dict, *, viewer_is_self: bool) -> bool:
    return viewer_is_self and user_send_paid_messages_stars(u) > 0


# ── inputPrivacyKeyNoPaidMessages ───────────────────────────────────────────

def _pack_privacy_rules(rules: List[bytes], users: Optional[List[dict]] = None,
                        storage=None) -> bytes:
    users = users or []
    w = tl.Writer()
    w.u32(0x50a04e45)
    w.u32(tl.VECTOR)
    w.i32(len(rules))
    for rule in rules:
        w.raw(rule)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, storage=storage))
    return w.bytes_out()


def _pack_allow_users_rule(user_ids: List[int]) -> bytes:
    w = tl.Writer()
    w.u32(PRIVACY_VALUE_ALLOW_USERS)
    w.u32(tl.VECTOR)
    w.i32(len(user_ids))
    for uid in user_ids:
        w.i64(int(uid))
    return w.bytes_out()


def pack_no_paid_messages_privacy(storage, owner_id: int) -> bytes:
    exempt = storage.list_paid_message_exceptions(owner_id)
    if exempt:
        users = []
        for uid in exempt:
            u = storage.get_user_by_id(uid)
            if u:
                users.append(u)
        return _pack_privacy_rules(
            [_pack_allow_users_rule(exempt)], users, storage=storage)
    w = tl.Writer()
    w.u32(PRIVACY_VALUE_DISALLOW_ALL)
    return _pack_privacy_rules([w.bytes_out()])


def parse_input_privacy_rules(r: tl.Reader, storage) -> List[int]:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    allow_users: List[int] = []
    for _ in range(count):
        cid = r.u32()
        if cid == INPUT_PRIVACY_VALUE_ALLOW_USERS:
            assert r.u32() == tl.VECTOR
            n = r.i32()
            for _ in range(n):
                u = packers.parse_input_user(r, storage)
                if u:
                    allow_users.append(int(u['id']))
        elif cid == INPUT_PRIVACY_VALUE_DISALLOW_ALL:
            allow_users = []
        elif cid == INPUT_PRIVACY_VALUE_ALLOW_ALL:
            pass
        elif cid in (INPUT_PRIVACY_VALUE_DISALLOW_USERS,
                     INPUT_PRIVACY_VALUE_ALLOW_CHAT_PARTICIPANTS,
                     INPUT_PRIVACY_VALUE_DISALLOW_CHAT_PARTICIPANTS):
            if cid == INPUT_PRIVACY_VALUE_DISALLOW_USERS:
                assert r.u32() == tl.VECTOR
                n = r.i32()
                for _ in range(n):
                    packers.parse_input_user(r, storage)
            else:
                assert r.u32() == tl.VECTOR
                n = r.i32()
                for _ in range(n):
                    r.i64()
        else:
            log.debug('skip unknown inputPrivacyValue %s', hex(cid))
    return allow_users


# ── Sending paid private messages ───────────────────────────────────────────

def check_channel_direct_paid(storage, sender_id: int, peer_id: int,
                            allow_paid_stars: int,
                            *, is_admin: bool = False) -> Tuple[int, Optional[bytes]]:
    """Validate star payment for channel direct / monoforum messages."""
    if is_admin or peer_id == sender_id:
        return 0, None
    ch = storage.get_channel(peer_id)
    if not ch:
        return 0, None
    if int(ch.get('monoforum') or 0):
        linked = storage.get_linked_channel_for_monoforum(peer_id)
        required = storage.get_channel_direct_message_stars(
            int(linked['id']) if linked else peer_id)
    else:
        required = storage.get_channel_direct_message_stars(peer_id)
        if required <= 0 and storage.can_send_direct_to_channel(peer_id, sender_id):
            mono = storage.get_channel_monoforum(peer_id)
            if mono:
                required = storage.get_channel_direct_message_stars(int(mono['id']))
    if required <= 0:
        return 0, None
    paid = int(allow_paid_stars or 0)
    if paid < required:
        return 0, _rpc_error(403, f'ALLOW_PAYMENT_REQUIRED_{required}')
    if not config.INFINITE_STARS:
        if storage.get_stars_balance(sender_id) < required:
            return 0, _rpc_error(403, f'ALLOW_PAYMENT_REQUIRED_{required}')
    return required, None


def check_private_send_paid(storage, sender_id: int, peer_id: int,
                            allow_paid_stars: int) -> Tuple[int, Optional[bytes]]:
    """Validate payment before a private DM send. Returns (stars, error)."""
    if peer_id == sender_id or peer_id >= config.CHANNEL_ID_MIN:
        return 0, None
    required = storage.get_required_paid_message_stars(peer_id, sender_id)
    if required <= 0:
        return 0, None
    paid = int(allow_paid_stars or 0)
    if paid < required:
        return 0, _rpc_error(403, f'ALLOW_PAYMENT_REQUIRED_{required}')
    if not config.INFINITE_STARS:
        if storage.get_stars_balance(sender_id) < required:
            return 0, _rpc_error(403, f'ALLOW_PAYMENT_REQUIRED_{required}')
    return required, None


# ── Service messages ──────────────────────────────────────────────────────────

def pack_message_action_paid_messages_price(stars: int) -> bytes:
    w = tl.Writer()
    w.u32(MESSAGE_ACTION_PAID_MESSAGES_PRICE)
    w.i32(0)
    w.i64(int(stars))
    return w.bytes_out()


def pack_message_action_paid_messages_refunded(count: int, stars: int) -> bytes:
    w = tl.Writer()
    w.u32(MESSAGE_ACTION_PAID_MESSAGES_REFUNDED)
    w.i32(int(count))
    w.i64(int(stars))
    return w.bytes_out()


def _pack_service_paid_action(msg: dict, action: dict) -> bytes:
    """messageService for paid-messages price/refund actions."""
    from_id = int(msg['from_id'])
    peer_id = int(msg['peer_id'])
    is_out = packers._message_is_out(msg)
    flags = 0
    if is_out:
        flags |= 1 << 1
    else:
        flags |= 1 << 8
    chat_peer = peer_id if is_out else from_id
    w = tl.Writer()
    w.u32(MESSAGE_SERVICE)
    w.i32(flags)
    w.i32(int(msg['id']))
    if flags & (1 << 8):
        w.raw(packers.pack_peer_user(from_id))
    w.raw(packers.pack_peer_user(chat_peer))
    w.i32(int(msg['date']))
    if action.get('type') == 'paid_messages_refunded':
        w.raw(pack_message_action_paid_messages_refunded(
            int(action.get('count') or 0), int(action.get('stars') or 0)))
    else:
        w.raw(pack_message_action_paid_messages_price(
            int(action.get('stars') or 0)))
    return w.bytes_out()


def pack_service_paid_messages_price(msg: dict, action: dict) -> bytes:
    return _pack_service_paid_action(msg, action)


def pack_service_paid_messages_refunded(msg: dict, action: dict) -> bytes:
    return _pack_service_paid_action(msg, action)


def pack_requirement_paid_messages(stars: int) -> bytes:
    w = tl.Writer()
    w.u32(REQUIREMENT_TO_CONTACT_PAID_MESSAGES)
    w.i64(int(stars))
    return w.bytes_out()


def _push_price_change(storage, push_queue, session, owner: dict,
                       notices: Optional[dict]) -> None:
    if push_queue is None:
        return
    owner_id = int(owner['id'])
    owner_updates = [packers.pack_update_user(owner_id)]
    if notices and notices.get('saved'):
        saved = notices['saved']
        owner_updates.append(packers.pack_update_new_message(
            saved['msg'], saved['pts'], storage=storage))
    for dm in (notices or {}).get('dms') or []:
        owner_updates.append(packers.pack_update_new_message(
            dm['owner_msg'], dm['pts_owner'], storage=storage))
    owner_upd = packers.pack_updates(
        owner_updates, [owner], user_id=owner_id, storage=storage)
    push_queue.push_to_user(
        owner_id, owner_upd, exclude_auth_key_id=session.auth_key_id)

    for dm in (notices or {}).get('dms') or []:
        peer_id = int(dm['peer_id'])
        peer = storage.get_user_by_id(peer_id)
        users = [owner]
        if peer:
            users.append(peer)
        peer_updates = [
            packers.pack_update_user(owner_id),
            packers.pack_update_new_message(
                dm['peer_msg'], dm['pts_peer'], storage=storage),
        ]
        peer_upd = packers.pack_updates(
            peer_updates, users, user_id=peer_id, storage=storage)
        push_queue.push_to_user(peer_id, peer_upd)

    for uid in storage.get_users_who_know(owner_id):
        if uid == owner_id:
            continue
        if int(uid) >= config.CHANNEL_ID_MIN:
            continue
        if any(int(dm['peer_id']) == uid
               for dm in (notices or {}).get('dms') or []):
            continue
        peer_upd = packers.pack_updates(
            [packers.pack_update_user(owner_id)], [owner],
            user_id=uid, storage=storage)
        push_queue.push_to_user(uid, peer_upd)


def _push_refund_notice(storage, push_queue, session, owner: dict,
                        peer_id: int, notice: Optional[dict]) -> None:
    if push_queue is None or notice is None:
        return
    owner_id = int(owner['id'])
    peer = storage.get_user_by_id(peer_id)
    users = [owner]
    if peer:
        users.append(peer)

    owner_upd = packers.pack_updates(
        [packers.pack_update_new_message(
            notice['owner_msg'], notice['pts_owner'], storage=storage)],
        users, user_id=owner_id, storage=storage)
    push_queue.push_to_user(
        owner_id, owner_upd, exclude_auth_key_id=session.auth_key_id)

    peer_upd = packers.pack_updates(
        [packers.pack_update_new_message(
            notice['peer_msg'], notice['pts_peer'], storage=storage)],
        users, user_id=peer_id, storage=storage)
    push_queue.push_to_user(peer_id, peer_upd)


# ── RPC handlers ────────────────────────────────────────────────────────────

def handle_account_get_global_privacy_settings(session, req_msg_id, r, storage,
                                                 push_queue, registry):
    from handlers.rpc import require_auth
    user, err = require_auth(session, storage)
    if user is None:
        return err
    user = storage.get_user_by_id(user['id']) or user
    return pack_global_privacy_settings(user, storage=storage)


def handle_account_set_global_privacy_settings(session, req_msg_id, r, storage,
                                               push_queue, registry):
    from handlers.rpc import require_auth
    user, err = require_auth(session, storage)
    if user is None:
        return err

    settings = parse_global_privacy_settings(r)
    stars = int(settings['noncontact_peers_paid_stars'])
    if stars > 0:
        if not config.STARS_PAID_MESSAGES_AVAILABLE:
            return _rpc_error(400, 'PAID_MESSAGES_NOT_AVAILABLE')
        if not user.get('premium'):
            return _rpc_error(403, 'PREMIUM_ACCOUNT_REQUIRED')
        if stars > config.STARS_PAID_MESSAGE_AMOUNT_MAX:
            return _rpc_error(400, 'STARS_AMOUNT_INVALID')

    old_stars = storage.get_user_paid_message_price(user['id'])
    new_stars = storage.set_user_paid_message_price(user['id'], stars)
    storage.set_global_privacy(user['id'], settings)
    owner = storage.get_user_by_id(user['id']) or user

    if new_stars != old_stars:
        _push_price_change(storage, push_queue, session, owner, None)

    return pack_global_privacy_settings(owner, storage=storage)


def handle_account_toggle_no_paid_messages_exception(session, req_msg_id, r,
                                                     storage, push_queue,
                                                     registry):
    from handlers.rpc import require_auth
    user, err = require_auth(session, storage)
    if user is None:
        return err

    flags = r.i32()
    refund_charged = bool(flags & 1)
    if flags & 2:
        packers.parse_input_peer(r, self_user_id=user['id'])
        return _rpc_error(400, 'CHANNEL_INVALID')
    require_payment = bool(flags & 4)
    if require_payment:
        return _rpc_error(400, 'PAID_MESSAGE_REMOVAL_NOT_ALLOWED')

    target = packers.parse_input_user(r, storage, self_user_id=user['id'])
    if target is None:
        return _rpc_error(400, 'USER_ID_INVALID')
    if target['id'] == user['id']:
        return _rpc_error(400, 'USER_ID_INVALID')

    storage.add_paid_message_exception(user['id'], target['id'])

    if refund_charged:
        count, total = storage.refund_paid_messages_from_user(
            user['id'], target['id'])
        if count > 0:
            owner = storage.get_user_by_id(user['id']) or user
            notice = storage.insert_paid_messages_refund_notice(
                user['id'], target['id'], count, total)
            _push_refund_notice(
                storage, push_queue, session, owner, target['id'], notice)
            if push_queue is not None:
                sender_upd = packers.pack_updates(
                    [packers.pack_user_stars_balance(storage, target['id'])],
                    [target], user_id=target['id'], storage=storage)
                push_queue.push_to_user(target['id'], sender_upd)

    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_account_get_paid_messages_revenue(session, req_msg_id, r, storage,
                                             push_queue, registry):
    from handlers.rpc import require_auth
    user, err = require_auth(session, storage)
    if user is None:
        return err

    flags = r.i32()
    if flags & 1:
        packers.parse_input_peer(r, self_user_id=user['id'])
        return _rpc_error(400, 'CHANNEL_INVALID')

    target = packers.parse_input_user(r, storage, self_user_id=user['id'])
    if target is None:
        return _rpc_error(400, 'USER_ID_INVALID')

    stars = storage.get_paid_messages_revenue(user['id'], target['id'])
    return pack_paid_messages_revenue(stars)


def handle_users_get_requirements_to_contact(session, req_msg_id, r, storage,
                                             push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    assert r.u32() == tl.VECTOR
    count = r.i32()
    results = []
    for _ in range(count):
        target = packers.parse_input_user(r, storage, self_user_id=viewer['id'])
        if target is None:
            results.append(b'')
            continue
        required = storage.get_required_paid_message_stars(
            target['id'], viewer['id'])
        if required > 0:
            results.append(pack_requirement_paid_messages(required))
        else:
            w = tl.Writer()
            w.u32(REQUIREMENT_TO_CONTACT_EMPTY)
            results.append(w.bytes_out())

    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(results))
    for item in results:
        w.raw(item)
    return w.bytes_out()


PAID_MESSAGE_HANDLERS = {
    ACCOUNT_SET_GLOBAL_PRIVACY_SETTINGS: handle_account_set_global_privacy_settings,
    ACCOUNT_TOGGLE_NO_PAID_MESSAGES_EXCEPTION: (
        handle_account_toggle_no_paid_messages_exception),
    ACCOUNT_GET_PAID_MESSAGES_REVENUE: handle_account_get_paid_messages_revenue,
    USERS_GET_REQUIREMENTS_TO_CONTACT: handle_users_get_requirements_to_contact,
}
