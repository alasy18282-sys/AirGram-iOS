"""
RPC method handlers.
"""
import os
import time
import json
import logging
import re
import zlib
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple
from zoneinfo import ZoneInfo, available_timezones

import config
from mtproto import tl
from mtproto.client_info import format_client_log
from db.storage import Storage, normalize_phone, is_valid_username, SERVICE_USER_ID
from auth_bot_notify import try_notify_login_code
from handlers.test_phones import (
    create_test_phone_code,
    is_allowed_test_phone,
    test_phone_disabled,
    test_sign_in_rejected,
)
from handlers import packers
from handlers.push import PushQueue, ConnectionRegistry, suppress_user_pushes

log = logging.getLogger(__name__)
_warned_methods: set[int] = set()
_timezone_cache: Optional[tuple[list[dict], int]] = None


def rpc_error(code: int, message: str) -> bytes:
    w = tl.Writer()
    w.u32(tl.RPC_ERROR)
    w.i32(code)
    w.string(message)
    return w.bytes_out()


def handle_rpc_drop_answer(session, req_msg_id, r, storage, push_queue, registry):
    dropped = r.i64()
    log.info('RPC drop_answer target=%d', dropped)
    w = tl.Writer()
    w.u32(0x5e2ad36e)  # rpc_answer_unknown
    return w.bytes_out()


def handle_messages_set_typing(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if flags & 1:
        r.i32()  # top_msg_id
    action = r.u32() if r.remaining() >= 4 else 0
    log.debug('setTyping viewer=%s peer=%s action=%#010x', viewer['id'], peer_id, action)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def parse_code_settings(r: tl.Reader) -> int:
    if r.remaining() < 8:
        return 0
    cid = r.u32()
    if cid != tl.CODE_SETTINGS:
        r.pos -= 4
        return 0
    flags = r.i32()
    if flags & (1 << 6):
        assert r.u32() == tl.VECTOR
        count = r.i32()
        for _ in range(count):
            r.tl_bytes()
    if flags & (1 << 8):
        r.tl_bytes()
        r.bool()
    return int(flags)


def skip_code_settings(r: tl.Reader) -> None:
    parse_code_settings(r)


def _auth_use_firebase_sms(session, code_settings_flags: int) -> bool:
    # Play Integrity / Firebase attestation only works against official Telegram
    # DCs. On a private server it never completes and Android hangs forever.
    return False


def _pack_auth_sent_code(code: str, code_hash: str, *,
                         use_firebase: bool,
                         use_fragment: bool = False,
                         phone: str = '') -> bytes:
    w = tl.Writer()
    w.u32(tl.AUTH_SENT_CODE)
    w.i32((1 << 2) | (1 << 1))  # timeout + next_type (SMS fallback)
    if use_fragment:
        w.u32(tl.AUTH_SENT_CODE_TYPE_FRAGMENT_SMS)
        w.i32(1)  # url present
        w.string(f'https://fragment.com/number/+{phone}')
        w.i32(len(code))
    elif use_firebase:
        w.u32(tl.AUTH_SENT_CODE_TYPE_FIREBASE_SMS)
        w.i32(1 << 2)  # play_integrity_project_id + play_integrity_nonce
        w.i64(1)
        w.tl_bytes(os.urandom(32))
        w.i32(len(code))
    else:
        w.u32(tl.AUTH_SENT_CODE_TYPE_SMS)
        w.i32(len(code))
    w.string(code_hash)
    w.u32(tl.AUTH_CODE_TYPE_SMS)
    w.i32(60)
    return w.bytes_out()


def skip_input_peer(r: tl.Reader) -> None:
    packers.skip_input_peer(r)


def require_auth(session, storage: Storage) -> Tuple[Optional[dict], Optional[bytes]]:
    auth_key_id = int(session.auth_key_id)
    perm_id = getattr(session, 'perm_auth_key_id', None)
    if perm_id and int(perm_id) != auth_key_id:
        if storage.get_temp_auth_perm(auth_key_id) != int(perm_id):
            storage.save_temp_auth_binding(auth_key_id, int(perm_id), 0)
    user_id = storage.resolve_session_user(auth_key_id)
    if not user_id and perm_id:
        user_id = storage.resolve_session_user(int(perm_id))
    if not user_id:
        return None, rpc_error(401, 'AUTH_KEY_UNREGISTERED')
    storage.bind_related_auth_keys(auth_key_id, int(user_id))
    if perm_id:
        storage.bind_related_auth_keys(int(perm_id), int(user_id))
    user = storage.get_user_by_id(user_id)
    if not user:
        return None, rpc_error(401, 'AUTH_KEY_UNREGISTERED')
    return user, None
    # Note: second value True means ok; use pattern: user, err = require_auth; if err is not True


def _broadcast_presence(storage, user_id: int, push_queue: Optional[PushQueue],
                        registry: Optional[ConnectionRegistry],
                        exclude_auth_key_id: Optional[int] = None) -> None:
    if push_queue is None:
        return
    user = storage.get_user_by_id(user_id)
    if not user:
        return
    from handlers.privacy import (
        INPUT_PRIVACY_KEY_STATUS_TIMESTAMP,
        can_view_privacy,
    )
    for partner_id in storage.get_dialog_partner_ids(user_id):
        if partner_id == SERVICE_USER_ID:
            continue
        user_view = dict(user)
        if not can_view_privacy(
                storage, user_id, partner_id, INPUT_PRIVACY_KEY_STATUS_TIMESTAMP):
            user_view['_hide_status'] = True
        upd = packers.pack_updates(
            [packers.pack_update_user_status(user_id, user_view)], [],
            user_id=partner_id, storage=storage)
        push_queue.push_to_user(partner_id, upd, exclude_auth_key_id=exclude_auth_key_id)


def _bind_user_session(session, storage, user_id, registry: Optional[ConnectionRegistry],
                       push_queue: Optional[PushQueue] = None,
                       *, push_updates: bool = True):
    storage.bind_session(session.auth_key_id, user_id)
    perm_id = getattr(session, 'perm_auth_key_id', None)
    if perm_id and perm_id != session.auth_key_id:
        storage.save_temp_auth_binding(session.auth_key_id, perm_id, 0)
        storage.bind_session(perm_id, user_id)
    storage.bind_related_auth_keys(session.auth_key_id, user_id)
    if registry is not None:
        registry.bind_user(session.auth_key_id, user_id)
        if perm_id and perm_id != session.auth_key_id:
            registry.bind_user(perm_id, user_id)
        conn = registry.get_connection(session.auth_key_id)
        if conn is not None:
            conn._user_id = user_id
            registry.attach_user_connection(user_id, conn)
    storage.set_user_online(user_id)
    # Remember the client layer before any push for a fresh signup is queued.
    try:
        from mtproto.client_info import persist_user_layer, remember_user
        remember_user(session, user_id)
        layer = int(getattr(session, 'client_layer', 0) or 0)
        if layer:
            persist_user_layer(storage, user_id, layer)
    except Exception:
        log.debug('Failed to cache client layer for user %s', user_id)
    if push_updates and push_queue is not None and user_id:
        try:
            import config
            balance = storage.get_ton_balance(user_id)
            if balance > 0 or config.can_use_ton_payments(user_id):
                push_queue.push_to_user(
                    user_id,
                    packers.pack_user_ton_balance(storage, user_id),
                    exclude_auth_key_id=session.auth_key_id)
        except Exception:
            log.exception('Failed to push TON balance for user %s', user_id)
    # Do not block auth.signIn response on mass presence fan-out; Android can
    # appear to hang on "loading" when server is under heavy gift traffic.
    if push_updates:
        try:
            from handlers.collectible_usernames import maybe_push_username_state
            maybe_push_username_state(storage, user_id, push_queue)
        except Exception:
            log.exception('Failed to push username state for user %s', user_id)
        try:
            push_queue.push_to_user(
                user_id,
                packers.pack_emoji_catalog_refresh(storage, recipient_id=user_id),
                exclude_auth_key_id=session.auth_key_id,
            )
        except Exception:
            log.exception('Failed to push emoji catalog refresh for user %s', user_id)


def _push_login_code(storage, phone, code, session, push_queue: Optional[PushQueue]):
    msg = storage.send_login_code_message(phone, code)
    if not msg or push_queue is None:
        return
    user = storage.get_user_by_phone(phone)
    if not user:
        return
    service_user = storage.get_user_by_id(SERVICE_USER_ID)
    upd = packers.pack_update_short(msg, msg['pts'], [service_user])
    push_queue.push_to_user(user['id'], upd, exclude_auth_key_id=session.auth_key_id)


def _deliver_login_code(storage, phone, code, session,
                        push_queue: Optional[PushQueue], *,
                        push_in_app: bool = True) -> None:
    """Notify the user without ever aborting auth.sendCode.

    Delivery failures (bot API down, missing schema, push error) must not
    prevent the RPC response — otherwise the client hangs on "Connecting".
    """
    try:
        try_notify_login_code(storage, phone, code)
    except Exception:
        log.exception('Failed to send login code via bot for +%s', phone)
    if not push_in_app:
        return
    try:
        _push_login_code(storage, phone, code, session, push_queue)
    except Exception:
        log.exception('Failed to push login code message for +%s', phone)


# ── Handlers ─────────────────────────────────────────────────

def handle_help_get_nearest_dc(session, req_msg_id, r, storage, push_queue, registry):
    from config import DC_ID
    w = tl.Writer()
    w.u32(tl.NEAREST_DC)
    w.string('RU')
    w.i32(DC_ID)
    w.i32(DC_ID)
    return w.bytes_out()


def handle_help_get_config(session, req_msg_id, r, storage, push_queue, registry):
    public_ip = getattr(config, 'SERVER_PUBLIC_IP', None)
    if not public_ip:
        import os
        public_ip = (
            os.environ.get('MYTELEGRAM_PUBLIC_IP', '84.21.173.237').strip()
            or '84.21.173.237'
        )
        log.warning('config.SERVER_PUBLIC_IP missing; using %s', public_ip)
    return packers.pack_config(config.DC_ID, public_ip, config.SERVER_PORT)


def handle_auth_bind_temp_auth_key(session, req_msg_id, r, storage, push_queue, registry):
    perm_auth_key_id = r.i64()
    _nonce = r.i64()
    expires_at = r.i32()
    _encrypted = r.tl_bytes()
    temp_auth_key_id = session.auth_key_id
    session.perm_auth_key_id = perm_auth_key_id
    storage.save_auth_key(temp_auth_key_id, session.auth_key)
    storage.save_temp_auth_binding(
        temp_auth_key_id, perm_auth_key_id, expires_at)
    storage.migrate_session_auth_key(temp_auth_key_id, perm_auth_key_id)
    user_id = storage.resolve_session_user(perm_auth_key_id)
    if user_id:
        storage.bind_session(temp_auth_key_id, int(user_id))
        storage.bind_related_auth_keys(temp_auth_key_id, int(user_id))
    if registry is not None and user_id:
        registry.bind_user(temp_auth_key_id, int(user_id))
        registry.bind_user(perm_auth_key_id, int(user_id))
        conn = registry.get_connection(temp_auth_key_id)
        if conn is not None:
            conn._user_id = int(user_id)
            registry.attach_user_connection(int(user_id), conn)
    log.info('bindTempAuthKey temp=%#018x perm=%#018x user=%s expires=%d %s',
             temp_auth_key_id, perm_auth_key_id, user_id, expires_at,
             format_client_log(session))
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def _exported_auth_token(user_id: int) -> bytes:
    return b'mtg-auth-v1:' + str(int(user_id)).encode('ascii')


def _fresh_user_for_auth(storage, user_id: int) -> Optional[dict]:
    """Reload user after bind_session/set_user_online for auth.* responses."""
    storage.set_user_online(int(user_id))
    return storage.get_user_by_id(int(user_id))


def _pack_auth_authorization_safe(user: dict) -> bytes:
    """Registration-safe authorization payload (minimal self user)."""
    return packers.pack_auth_authorization(
        user, storage=None, registration_safe=True)


def _bootstrap_user_after_auth(storage, phone: str, user_id: int) -> Optional[dict]:
    """Create the initial Telegram service dialog for brand-new accounts.

    Real Telegram delivers the login-code chat (777000) on first auth. Without
    it, clients get empty getDialogs / pts=0 and iOS may wipe auth keys and
    re-handshake right after signUp.
    """
    try:
        return storage.ensure_service_dialog_bootstrap(int(user_id), phone=phone)
    except Exception:
        log.exception('Failed to bootstrap service dialog for user %s', user_id)
        return None


def _push_post_signup_updates(storage, user_id: int, msg: Optional[dict],
                              push_queue: Optional[PushQueue]) -> None:
    """Post-signup state is pulled via getDialogs/getState; skip push to avoid races."""
    return


def handle_auth_export_authorization(session, req_msg_id, r, storage,
                                     push_queue, registry):
    _dc_id = r.i32()
    user, err = require_auth(session, storage)
    if user is None:
        return err
    log.info('exportAuthorization user=%d dc=%d %s',
             int(user['id']), _dc_id, format_client_log(session))
    w = tl.Writer()
    w.u32(tl.AUTH_EXPORTED_AUTH)
    w.i64(int(user['id']))
    w.tl_bytes(_exported_auth_token(int(user['id'])))
    return w.bytes_out()


def handle_auth_import_authorization(session, req_msg_id, r, storage,
                                     push_queue, registry):
    user_id = int(r.i64())
    token = r.tl_bytes()
    if token != _exported_auth_token(user_id):
        return rpc_error(400, 'AUTH_BYTES_INVALID')
    user = storage.get_user_by_id(user_id)
    if not user:
        return rpc_error(400, 'USER_ID_INVALID')
    _bind_user_session(session, storage, user_id, registry, push_queue,
                       push_updates=False)
    log.info('User imported session: id=%d %s', user_id, format_client_log(session))
    user = _fresh_user_for_auth(storage, user_id) or user
    return _pack_auth_authorization_safe(user)


def handle_auth_send_code(session, req_msg_id, r, storage, push_queue, registry):
    phone_number = r.string()
    _api_id = r.i32()
    _api_hash = r.string()
    code_settings_flags = parse_code_settings(r)
    try:
        from mtproto.client_info import apply_api_id_layer_hint
        apply_api_id_layer_hint(session, _api_id, storage)
    except Exception:
        log.exception('Failed to apply api_id layer hint')

    phone = normalize_phone(phone_number)
    if test_phone_disabled(phone):
        return rpc_error(400, 'DC_PHONE_NOT_ENABLED')
    user = storage.get_user_by_phone(phone)
    if user and storage.is_user_banned(user['id']):
        if storage.is_ban_blocking_recreate(user['id']):
            return rpc_error(400, 'PHONE_NUMBER_BANNED')

    if is_allowed_test_phone(phone):
        code, code_hash = create_test_phone_code(storage, phone)
        use_firebase = False
        use_fragment = False
        log.info('TEST PHONE CODE for +%s: %s', phone, code)
        return _pack_auth_sent_code(code, code_hash, use_firebase=use_firebase,
                                    use_fragment=use_fragment, phone=phone)

    code, code_hash = storage.create_phone_code(phone)
    use_firebase = _auth_use_firebase_sms(session, code_settings_flags)
    # Fragment SMS delivery only exists on official Telegram DCs. On a private
    # server Android waits forever after sentCodeTypeFragmentSms.
    use_fragment = False

    via = 'SMS' if not use_firebase else 'Firebase+SMS'
    log.info('PHONE CODE for +%s: %s (via %s)', phone, code, via)
    _deliver_login_code(storage, phone, code, session, push_queue,
                        push_in_app=not use_firebase)

    return _pack_auth_sent_code(code, code_hash, use_firebase=use_firebase,
                                use_fragment=use_fragment, phone=phone)


def handle_auth_request_firebase_sms(session, req_msg_id, r, storage,
                                     push_queue, registry):
    flags = r.i32()
    phone_number = r.string()
    code_hash = r.string()
    if flags & 1:
        r.string()  # safety_net_token
    if flags & 2:
        r.string()  # play_integrity_token
    if flags & (1 << 1):
        r.string()  # ios_push_secret

    phone = normalize_phone(phone_number)
    if test_phone_disabled(phone):
        return rpc_error(400, 'DC_PHONE_NOT_ENABLED')
    row = storage._conn.execute(
        '''SELECT code FROM phone_codes
           WHERE phone=? AND code_hash=? AND used=0 AND expires_at>?''',
        (phone, code_hash, int(time.time()))
    ).fetchone()
    if not row:
        return rpc_error(400, 'PHONE_CODE_EXPIRED')

    code = str(row['code'])
    log.info('Firebase SMS approved for +%s: %s', phone, code)
    _deliver_login_code(storage, phone, code, session, push_queue)

    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_auth_init_passkey_login(session, req_msg_id, r, storage,
                                   push_queue, registry):
    import json
    _api_id = r.i32()
    _api_hash = r.string()
    w = tl.Writer()
    w.u32(tl.AUTH_PASSKEY_LOGIN_OPTIONS)
    w.raw(packers.pack_data_json(json.dumps({'clientData': ''})))
    return w.bytes_out()


def handle_auth_export_login_token(session, req_msg_id, r, storage,
                                   push_queue, registry):
    api_id = r.i32()
    _api_hash = r.string()
    if r.u32() != tl.VECTOR:
        return rpc_error(400, 'VECTOR_EXPECTED')
    count = r.i32()
    for _ in range(max(0, count)):
        r.i64()
    expires, token = storage.create_login_token(api_id)
    return packers.pack_auth_login_token(expires, token)


def handle_auth_accept_login_token(session, req_msg_id, r, storage,
                                   push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    token = r.tl_bytes()
    if not storage.accept_login_token(token, viewer['id']):
        return rpc_error(400, 'AUTH_TOKEN_INVALID')
    viewer = _fresh_user_for_auth(storage, viewer['id']) or viewer
    return _pack_auth_authorization_safe(viewer)


def handle_auth_import_login_token(session, req_msg_id, r, storage,
                                   push_queue, registry):
    token = r.tl_bytes()
    result = storage.resolve_login_token(token)
    if not result:
        return rpc_error(400, 'AUTH_TOKEN_INVALID')
    if result.get('expired'):
        return rpc_error(400, 'AUTH_TOKEN_EXPIRED')
    user_id = result.get('user_id')
    if user_id:
        user = storage.get_user_by_id(int(user_id))
        if not user:
            return rpc_error(400, 'AUTH_TOKEN_INVALID')
        _bind_user_session(session, storage, user['id'], registry, push_queue,
                           push_updates=False)
        user = _fresh_user_for_auth(storage, user['id']) or user
        return packers.pack_auth_login_token_success(
            user, storage=None, registration_safe=True)
    return packers.pack_auth_login_token(int(result['expires']), token)


def handle_auth_resend_code(session, req_msg_id, r, storage, push_queue, registry):
    flags = r.i32()
    phone_number = r.string()
    _code_hash = r.string()
    if flags & 1:
        r.string()  # reason
    phone = normalize_phone(phone_number)
    if test_phone_disabled(phone):
        return rpc_error(400, 'DC_PHONE_NOT_ENABLED')
    from handlers.phone_countries import is_collectible_phone
    _ = is_collectible_phone  # reserved for fragment.getCollectibleInfo UI
    use_fragment = False
    if is_allowed_test_phone(phone):
        code, code_hash = create_test_phone_code(storage, phone)
        via = 'TEST'
        log.info('TEST PHONE CODE RESEND for +%s: %s (via %s)', phone, code, via)
        return _pack_auth_sent_code(code, code_hash, use_firebase=False,
                                    use_fragment=use_fragment, phone=phone)
    code, code_hash = storage.create_phone_code(phone)
    via = 'SMS'
    log.info('PHONE CODE RESEND for +%s: %s (via %s)', phone, code, via)
    _deliver_login_code(storage, phone, code, session, push_queue)
    return _pack_auth_sent_code(code, code_hash, use_firebase=False,
                                use_fragment=use_fragment, phone=phone)


def handle_auth_cancel_code(session, req_msg_id, r, storage, push_queue, registry):
    phone_number = r.string()
    code_hash = r.string()
    phone = normalize_phone(phone_number)
    storage._conn.execute(
        'DELETE FROM phone_codes WHERE phone=? AND code_hash=?',
        (phone, code_hash))
    storage._conn.commit()
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_auth_sign_in(session, req_msg_id, r, storage, push_queue, registry):
    flags = r.i32()
    phone = normalize_phone(r.string())
    code_hash = r.string()
    code = r.string() if (flags & 1) else ''

    if not code:
        return rpc_error(400, 'PHONE_CODE_EMPTY')

    if test_sign_in_rejected(phone, code):
        return rpc_error(400, 'DC_PHONE_NOT_ENABLED')

    if not storage.check_phone_code(phone, code_hash, code):
        return rpc_error(400, 'PHONE_CODE_INVALID')

    user = storage.get_user_by_phone(phone)
    if not user:
        log.info('Sign up required for +%s', phone)
        return packers.pack_auth_sign_up_required()

    if storage.is_user_banned(user['id']):
        if storage.is_ban_blocking_recreate(user['id']):
            return rpc_error(401, 'USER_DEACTIVATED_BAN')
        log.info('Deleted account can re-register: +%s', phone)
        return packers.pack_auth_sign_up_required()

    if storage.user_has_password(user['id']):
        storage.set_pending_sign_in(
            session.auth_key_id, phone, code_hash, user['id'])
        log.info('2FA required for +%s (user %d)', phone, user['id'])
        return rpc_error(401, 'SESSION_PASSWORD_NEEDED')

    if not storage.consume_phone_code(phone, code_hash):
        return rpc_error(400, 'PHONE_CODE_EXPIRED')

    _bind_user_session(session, storage, user['id'], registry, push_queue,
                       push_updates=False)
    _bootstrap_user_after_auth(storage, phone, user['id'])
    suppress_user_pushes(user['id'], seconds=3)
    log.info('User signed in: id=%d phone=+%s %s',
             user['id'], phone, format_client_log(session))
    user = _fresh_user_for_auth(storage, user['id']) or user
    return _pack_auth_authorization_safe(user)


def handle_auth_log_out(session, req_msg_id, r, storage, push_queue, registry):
    # Do not wipe DB session — TD may call logOut during recovery; keep auth binding.
    w = tl.Writer()
    w.u32(tl.AUTH_LOGGED_OUT)
    w.i32(0)
    return w.bytes_out()


from handlers.peer_colors import is_valid_color_id, pack_help_peer_colors


def handle_help_get_peer_colors(session, req_msg_id, r, storage, push_queue, registry):
    return pack_help_peer_colors(r.i32(), profile=False)


def handle_help_get_peer_profile_colors(session, req_msg_id, r, storage,
                                        push_queue, registry):
    return pack_help_peer_colors(r.i32(), profile=True)


def _pack_emoji_list(req_hash: int, storage: Storage) -> bytes:
    cur_hash, doc_ids = storage.get_default_background_emoji_ids()
    if req_hash and int(req_hash) == int(cur_hash):
        w = tl.Writer()
        w.u32(tl.EMOJI_LIST_NOT_MODIFIED)
        return w.bytes_out()
    w = tl.Writer()
    w.u32(tl.EMOJI_LIST)
    w.i64(int(cur_hash))
    w.u32(tl.VECTOR)
    w.i32(len(doc_ids))
    for doc_id in doc_ids:
        w.i64(int(doc_id))
    return w.bytes_out()


def handle_account_get_default_background_emojis(
        session, req_msg_id, r, storage, push_queue, registry):
    return _pack_emoji_list(r.i64(), storage)


def _broadcast_user_color_update(storage, push_queue, user_id: int,
                                 session=None) -> None:
    if push_queue is None:
        return
    user = storage.get_user_by_id(int(user_id))
    if not user:
        return
    all_recipients = {int(user_id)}
    all_recipients.update(
        int(uid) for uid in storage.get_users_who_see_user(int(user_id)))
    for uid in all_recipients:
        if int(uid) >= config.CHANNEL_ID_MIN:
            continue
        exclude = None
        if session is not None and int(uid) == int(user_id):
            exclude = session.auth_key_id
        upd = packers.pack_updates(
            [packers.pack_update_user(int(user_id))],
            [user],
            user_id=int(uid),
            storage=storage,
        )
        push_queue.push_to_user(int(uid), upd, exclude_auth_key_id=exclude)


def handle_account_update_color(session, req_msg_id, r, storage, push_queue,
                                registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err
    flags = r.i32()
    for_profile = bool(flags & (1 << 1))
    if flags & (1 << 2):
        try:
            peer_color = packers.parse_peer_color(r)
        except ValueError:
            return rpc_error(400, 'PEER_COLOR_INVALID')
        color_id = int(peer_color.get('color_id', -1))
        bg_emoji = int(peer_color.get('background_emoji_id') or 0)
        if color_id < 0 and not bg_emoji:
            color_id = -1
        if bg_emoji and not storage.get_document_by_id(bg_emoji):
            return rpc_error(400, 'DOCUMENT_INVALID')
        if color_id >= 0 and not is_valid_color_id(color_id, for_profile=for_profile):
            return rpc_error(400, 'COLOR_INVALID')
    else:
        color_id = -1
        bg_emoji = 0
    storage.set_user_peer_color(
        user['id'],
        for_profile=for_profile,
        color_id=color_id,
        background_emoji_id=bg_emoji,
    )
    _broadcast_user_color_update(storage, push_queue, user['id'], session)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_help_get_invite_text(session, req_msg_id, r, storage, push_queue, registry):
    w = tl.Writer()
    w.u32(tl.HELP_INVITE_TEXT)
    w.string('Join me on KoalGram')
    return w.bytes_out()


def handle_account_get_default_profile_photo_emojis(
        session, req_msg_id, r, storage, push_queue, registry):
    _hash = r.i64()
    w = tl.Writer()
    w.u32(tl.EMOJI_LIST_NOT_MODIFIED)
    return w.bytes_out()


def _do_auth_sign_up(session, storage, push_queue, registry,
                     phone: str, code_hash: str,
                     first_name: str, last_name: str) -> bytes:
    if test_phone_disabled(phone):
        return rpc_error(400, 'DC_PHONE_NOT_ENABLED')
    if not first_name.strip():
        return rpc_error(400, 'FIRSTNAME_INVALID')

    row = storage._conn.execute(
        '''SELECT code FROM phone_codes
           WHERE phone=? AND code_hash=? AND used=0 AND expires_at>?''',
        (phone, code_hash, int(time.time()))
    ).fetchone()
    if not row:
        return rpc_error(400, 'PHONE_CODE_EXPIRED')

    if existing := storage.get_user_by_phone(phone):
        if storage.is_user_banned(existing['id']):
            if storage.is_ban_blocking_recreate(existing['id']):
                return rpc_error(400, 'PHONE_NUMBER_BANNED')
            if not storage.consume_phone_code(phone, code_hash):
                return rpc_error(400, 'PHONE_CODE_EXPIRED')
            user = storage.recreate_banned_user(
                phone, first_name.strip(), last_name.strip(),
            )
            _bind_user_session(session, storage, user['id'], registry, push_queue,
                               push_updates=False)
            _bootstrap_user_after_auth(storage, phone, user['id'])
            suppress_user_pushes(user['id'], seconds=3)
            log.info('Recreated deleted user: id=%d +%s %s',
                     user['id'], phone, format_client_log(session))
            user = _fresh_user_for_auth(storage, user['id']) or user
            return _pack_auth_authorization_safe(user)
        return rpc_error(400, 'PHONE_NUMBER_OCCUPIED')

    if not storage.consume_phone_code(phone, code_hash):
        return rpc_error(400, 'PHONE_CODE_EXPIRED')

    user = storage.create_user(phone, first_name.strip(), last_name.strip())
    if not user:
        return rpc_error(500, 'INTERNAL_SERVER_ERROR')
    _bind_user_session(session, storage, user['id'], registry, push_queue,
                       push_updates=False)
    _bootstrap_user_after_auth(storage, phone, user['id'])
    suppress_user_pushes(user['id'], seconds=3)
    log.info('New user registered: id=%d +%s "%s %s" %s',
             user['id'], phone, first_name, last_name, format_client_log(session))
    user = _fresh_user_for_auth(storage, user['id']) or user
    return _pack_auth_authorization_safe(user)


def handle_auth_sign_up(session, req_msg_id, r, storage, push_queue, registry):
    flags = r.i32()
    if flags & 1:
        r.bool()
    phone = normalize_phone(r.string())
    code_hash = r.string()
    first_name = r.string()
    last_name = r.string()
    return _do_auth_sign_up(
        session, storage, push_queue, registry,
        phone, code_hash, first_name, last_name)


def handle_auth_sign_up_layer(session, req_msg_id, r, storage,
                              push_queue, registry):
    phone = normalize_phone(r.string())
    code_hash = r.string()
    first_name = r.string()
    last_name = r.string()
    return _do_auth_sign_up(
        session, storage, push_queue, registry,
        phone, code_hash, first_name, last_name)


def handle_account_update_profile(session, req_msg_id, r, storage, push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    flags = r.i32()
    first_name = r.string() if (flags & 1) else None
    last_name = r.string() if (flags & 2) else None
    about = r.string() if (flags & 4) else None

    if first_name is not None and not first_name.strip():
        return rpc_error(400, 'FIRSTNAME_INVALID')

    user = storage.update_profile(
        user['id'],
        first_name=first_name.strip() if first_name is not None else None,
        last_name=last_name if last_name is not None else None,
        about=about if about is not None else None,
    )
    return packers.pack_user(user, is_self=True, storage=storage)


def handle_account_send_change_phone_code(session, req_msg_id, r, storage,
                                          push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    phone_number = r.string()
    skip_code_settings(r)

    phone = normalize_phone(phone_number)
    if phone == normalize_phone(user.get('phone', '')):
        return rpc_error(400, 'PHONE_NUMBER_INVALID')

    existing = storage.get_user_by_phone(phone)
    if existing and int(existing['id']) != int(user['id']):
        return rpc_error(400, 'PHONE_NUMBER_OCCUPIED')

    code, code_hash = storage.create_phone_code(phone)
    log.info('CHANGE PHONE CODE for +%s (user %d): %s', phone, user['id'], code)
    _deliver_login_code(storage, phone, code, session, push_queue)

    w = tl.Writer()
    w.u32(tl.AUTH_SENT_CODE)
    w.i32((1 << 2) | (1 << 1))
    w.u32(tl.AUTH_SENT_CODE_TYPE_APP)
    w.i32(len(code))
    w.string(code_hash)
    w.u32(tl.AUTH_CODE_TYPE_SMS)
    w.i32(300)
    return w.bytes_out()


def handle_account_change_phone(session, req_msg_id, r, storage,
                                push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    phone_number = r.string()
    code_hash = r.string()
    phone_code = r.string()

    phone = normalize_phone(phone_number)
    if not storage.verify_phone_code(phone, code_hash, phone_code):
        return rpc_error(400, 'PHONE_CODE_INVALID')

    existing = storage.get_user_by_phone(phone)
    if existing and int(existing['id']) != int(user['id']):
        return rpc_error(400, 'PHONE_NUMBER_OCCUPIED')

    try:
        user = storage.update_user_phone(user['id'], phone)
    except ValueError:
        return rpc_error(400, 'PHONE_NUMBER_OCCUPIED')

    log.info('User %d changed phone to +%s', user['id'], phone)
    return packers.pack_user(user, is_self=True, storage=storage)


def handle_account_check_username(session, req_msg_id, r, storage, push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    username = r.string().strip().lstrip('@').lower()
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE if storage.is_username_available(username, user['id']) else tl.BOOL_FALSE)
    return w.bytes_out()


def handle_account_update_username(session, req_msg_id, r, storage, push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    username = r.string().strip().lstrip('@').lower()
    try:
        user = storage.update_username(user['id'], username)
    except ValueError as exc:
        return rpc_error(400, str(exc))
    from handlers.collectible_usernames import notify_username_changed
    notify_username_changed(storage, user['id'], push_queue)
    return packers.pack_user(user, is_self=True, storage=storage)


def _broadcast_business_user_update(storage, push_queue, user_id: int, session=None):
    """Notify peers to refresh userFull after business profile changes."""
    if push_queue is None:
        return
    user = storage.get_user_by_id(int(user_id))
    if not user:
        return
    recipients = storage.get_users_who_see_user(int(user_id))
    for uid in recipients:
        if int(uid) >= config.CHANNEL_ID_MIN:
            continue
        exclude = None
        if session is not None and int(uid) == int(user_id):
            exclude = session.auth_key_id
        upd = packers.pack_updates(
            [packers.pack_update_user(int(user_id))],
            [user],
            user_id=int(uid),
            storage=storage,
        )
        push_queue.push_to_user(int(uid), upd, exclude_auth_key_id=exclude)


def handle_account_update_business_intro(session, req_msg_id, r, storage,
                                         push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    flags = r.i32()
    if flags & 1:
        cid = r.u32()
        if cid != tl.INPUT_BUSINESS_INTRO:
            return rpc_error(400, 'BUSINESS_INTRO_INVALID')
        intro_flags = r.i32()
        title = r.string()
        description = r.string()
        sticker_document_id = 0
        if intro_flags & 1:
            try:
                sticker_document_id = int(packers.parse_input_document(r) or 0)
            except ValueError:
                return rpc_error(400, 'STICKER_INVALID')
        try:
            storage.set_business_intro(
                user['id'],
                title=title,
                description=description,
                sticker_document_id=sticker_document_id,
            )
        except ValueError as exc:
            return rpc_error(400, str(exc))
    else:
        storage.clear_business_intro(user['id'])

    _broadcast_business_user_update(storage, push_queue, user['id'], session)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_account_update_business_greeting_message(
        session, req_msg_id, r, storage, push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    flags = r.i32()
    if flags & 1:
        shortcut_id = 0
        recipients = {'flags': 0, 'users': []}
        no_activity_days = 0
        try:
            cid = r.u32()
            if cid == tl.INPUT_BUSINESS_GREETING_MESSAGE:
                shortcut_id = r.i32()
                recipients = packers.parse_input_business_recipients(
                    r, storage, self_user_id=user['id'])
                no_activity_days = r.i32()
            elif cid == tl.VECTOR:
                # Forward compatibility: tolerate vector wrapping and use first.
                count = r.i32()
                if count <= 0:
                    storage.clear_business_greeting_message(user['id'])
                    w = tl.Writer()
                    w.u32(tl.BOOL_TRUE)
                    return w.bytes_out()
                entry_cid = r.u32()
                if entry_cid == tl.INPUT_BUSINESS_GREETING_MESSAGE:
                    shortcut_id = r.i32()
                    recipients = packers.parse_input_business_recipients(
                        r, storage, self_user_id=user['id'])
                    no_activity_days = r.i32()
                else:
                    # Some clients may send boxed vectors of bare entries.
                    shortcut_id = int(entry_cid)
                    recipients = packers.parse_input_business_recipients(
                        r, storage, self_user_id=user['id'])
                    no_activity_days = r.i32()
                for _ in range(max(0, count - 1)):
                    if r.remaining() < 4:
                        break
                    next_cid = r.u32()
                    try:
                        if next_cid == tl.INPUT_BUSINESS_GREETING_MESSAGE:
                            _ = r.i32()
                            packers.parse_input_business_recipients(
                                r, storage, self_user_id=user['id'])
                            _ = r.i32()
                        else:
                            _ = int(next_cid)
                            packers.parse_input_business_recipients(
                                r, storage, self_user_id=user['id'])
                            _ = r.i32()
                    except Exception:
                        break
            else:
                # Forward compatibility:
                # 1) unknown constructor with canonical payload layout
                # 2) bare payload where first word is shortcut_id
                if 0 <= int(cid) <= 1000000000:
                    shortcut_id = int(cid)
                else:
                    shortcut_id = r.i32()
                    log.warning(
                        'updateBusinessGreeting: fallback parser used cid=%#010x',
                        cid,
                    )
                recipients = packers.parse_input_business_recipients(
                    r, storage, self_user_id=user['id'])
                no_activity_days = r.i32()
        except Exception as exc:
            log.warning(
                'updateBusinessGreeting parse failed user=%s error=%s remaining=%s',
                user['id'],
                exc,
                r.remaining() if hasattr(r, 'remaining') else 'n/a',
            )
            return rpc_error(400, 'BUSINESS_GREETING_INVALID')
        try:
            storage.set_business_greeting_message(
                user['id'],
                shortcut_id=shortcut_id,
                no_activity_days=no_activity_days,
                recipients_flags=int(recipients.get('flags') or 0),
                recipients_users=list(recipients.get('users') or []),
            )
        except ValueError as exc:
            return rpc_error(400, str(exc))
    else:
        storage.clear_business_greeting_message(user['id'])

    _broadcast_business_user_update(storage, push_queue, user['id'], session)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_account_update_business_away_message(
        session, req_msg_id, r, storage, push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    flags = r.i32()
    if flags & 1:
        try:
            parsed = packers.parse_input_business_away_message(
                r, storage, self_user_id=user['id'])
        except Exception as exc:
            log.warning(
                'updateBusinessAway parse failed user=%s error=%s remaining=%s',
                user['id'],
                exc,
                r.remaining() if hasattr(r, 'remaining') else 'n/a',
            )
            return rpc_error(400, 'BUSINESS_AWAY_INVALID')
        try:
            shortcut_id = int(parsed.get('shortcut_id') or 0)
            if shortcut_id <= 0:
                existing = storage.get_business_away_message(user['id'])
                msgs = storage.get_quick_reply_messages(user['id'], -1)
                if existing and int(existing.get('shortcut_id') or 0) > 0:
                    shortcut_id = int(existing['shortcut_id'])
                elif shortcut_id == 0 and msgs:
                    shortcut_id = -1
            storage.set_business_away_message(
                user['id'],
                shortcut_id=shortcut_id,
                offline_only=bool(parsed.get('offline_only')),
                schedule_type=str(parsed.get('schedule_type') or 'always'),
                schedule_start=int(parsed.get('schedule_start') or 0),
                schedule_end=int(parsed.get('schedule_end') or 0),
                recipients_flags=int(parsed.get('recipients_flags') or 0),
                recipients_users=list(parsed.get('recipients_users') or []),
            )
        except ValueError as exc:
            return rpc_error(400, str(exc))
    else:
        storage.clear_business_away_message(user['id'])
        from handlers.quick_replies import push_delete_quick_reply_update
        push_delete_quick_reply_update(
            storage, push_queue, int(user['id']), -1, session=session)

    _broadcast_business_user_update(storage, push_queue, user['id'], session)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def _parse_business_work_hours(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid != tl.BUSINESS_WORK_HOURS:
        raise ValueError('BUSINESS_WORK_HOURS_PERIOD_INVALID')
    flags = r.i32()
    timezone_id = r.string().strip()
    marker = r.u32()
    if marker != tl.VECTOR:
        raise ValueError('BUSINESS_WORK_HOURS_PERIOD_INVALID')
    count = r.i32()
    if count < 0 or count > 128:
        raise ValueError('BUSINESS_WORK_HOURS_PERIOD_INVALID')
    weekly_open = []
    for _ in range(count):
        if r.remaining() < 12:
            raise ValueError('BUSINESS_WORK_HOURS_PERIOD_INVALID')
        interval_cid = r.u32()
        if interval_cid == tl.BUSINESS_WEEKLY_OPEN:
            start_minute = r.i32()
            end_minute = r.i32()
        else:
            # Forward compatibility: tolerate bare payload.
            start_minute = int(interval_cid)
            end_minute = r.i32()
        weekly_open.append({
            'start_minute': int(start_minute),
            'end_minute': int(end_minute),
        })
    return {
        'timezone_id': timezone_id,
        'weekly_open': weekly_open,
    }


def handle_account_update_business_work_hours(
        session, req_msg_id, r, storage, push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err
    flags = r.i32()
    if flags & 1:
        try:
            parsed = _parse_business_work_hours(r)
            storage.set_business_work_hours(
                user['id'],
                timezone_id=parsed['timezone_id'],
                weekly_open=parsed['weekly_open'],
            )
        except ValueError as exc:
            return rpc_error(400, str(exc))
        except Exception:
            return rpc_error(400, 'BUSINESS_WORK_HOURS_PERIOD_INVALID')
    else:
        storage.clear_business_work_hours(user['id'])

    _broadcast_business_user_update(storage, push_queue, user['id'], session)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_account_update_business_location(
        session, req_msg_id, r, storage, push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    flags = r.i32()
    if flags & 3:
        address = ''
        geo = None
        if flags & 1:
            address = r.string()
        if flags & 2:
            try:
                geo = packers.parse_input_geo_point(r)
            except ValueError:
                return rpc_error(400, 'GEO_POINT_INVALID')
        try:
            storage.set_business_location(
                user['id'],
                address=address,
                lat=float((geo or {}).get('lat') or 0),
                long=float((geo or {}).get('long') or 0),
                accuracy_radius=int((geo or {}).get('accuracy_radius') or 0),
                has_geo=bool(geo),
            )
        except ValueError as exc:
            return rpc_error(400, str(exc))
    else:
        storage.clear_business_location(user['id'])

    _broadcast_business_user_update(storage, push_queue, user['id'], session)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_account_toggle_username(session, req_msg_id, r, storage, push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    username = r.string().strip().lstrip('@').lower()
    active = r.bool()
    try:
        storage.toggle_collectible_username(user['id'], username, active)
    except ValueError as exc:
        return rpc_error(400, str(exc))
    from handlers.collectible_usernames import notify_username_changed
    notify_username_changed(
        storage, user['id'], push_queue, notify_contacts=True)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_account_reorder_usernames(session, req_msg_id, r, storage, push_queue, registry):
    user, err = require_auth(session, storage)
    if user is None:
        return err

    assert r.u32() == tl.VECTOR
    count = r.i32()
    order = [r.string().strip().lstrip('@').lower() for _ in range(count)]
    try:
        storage.reorder_usernames(user['id'], order)
    except ValueError as exc:
        return rpc_error(400, str(exc))
    from handlers.collectible_usernames import notify_username_changed
    notify_username_changed(
        storage, user['id'], push_queue, notify_contacts=True)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()



def _collectible_price_amounts(price: str) -> tuple[str, int, str, int]:
    raw = (price or '').strip().replace(',', '.')
    number = ''.join(ch for ch in raw if ch.isdigit() or ch == '.')
    try:
        value = float(number) if number else 0.0
    except ValueError:
        value = 0.0
    if '$' in raw or 'usd' in raw.lower():
        return 'USD', int(round(value * 100)), 'TON', 0
    return 'USD', 0, 'TON', int(round(value * 1_000_000_000))


def _collectible_phone_price(kind: str, phone: str) -> tuple[str, int, str, int]:
    _ = kind, phone
    return 'USD', 10000, 'TON', 10_000_000_000


def handle_fragment_get_collectible_info(session, req_msg_id, r, storage, push_queue, registry):
    cid = r.u32()
    if cid == tl.INPUT_COLLECTIBLE_USERNAME:
        username = r.string().strip().lstrip('@').lower()
        row = storage.get_collectible_username_owner(username)
        if not row:
            return rpc_error(400, 'COLLECTIBLE_NOT_FOUND')
        purchase_date = int(row.get('granted_at') or row.get('created_at') or 0)
        currency, amount, crypto_currency, crypto_amount = _collectible_price_amounts(
            row.get('price') or '')
        url = f'https://fragment.com/username/{username}'
    elif cid == tl.INPUT_COLLECTIBLE_PHONE:
        raw_phone = r.string()
        phone = normalize_phone(raw_phone)
        row = storage.get_collectible_phone_info(phone)
        if not row:
            log.warning('collectible phone not found: raw=%r normalized=%s',
                        raw_phone, phone)
            return rpc_error(400, 'COLLECTIBLE_NOT_FOUND')
        purchase_date = int(row.get('purchase_date') or 0)
        currency, amount, crypto_currency, crypto_amount = _collectible_phone_price(
            row.get('kind') or '', phone)
        url = f'https://fragment.com/number/+{phone}'
    else:
        return rpc_error(400, 'COLLECTIBLE_INVALID')

    w = tl.Writer()
    w.u32(tl.FRAGMENT_COLLECTIBLE_INFO)
    w.i32(purchase_date)
    w.string(currency)
    w.i64(amount)
    w.string(crypto_currency)
    w.i64(crypto_amount)
    w.string(url)
    return w.bytes_out()

def handle_users_get_full_user(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    target = packers.parse_input_user(
        r, storage, self_user_id=viewer['id'])
    if target is None:
        return rpc_error(400, 'USER_ID_INVALID')
    viewer_is_self = int(target['id']) == int(viewer['id'])
    return packers.pack_users_user_full(
        target, viewer_is_self=viewer_is_self,
        storage=storage, viewer_id=viewer['id'])


def _contacts_search_impl(session, storage, r, *, has_flags: bool) -> bytes:
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    if has_flags:
        # layer 228: flags:# broadcasts:flags.0?true bots:flags.1?true ...
        r.i32()
    q = r.string()
    limit = r.i32()
    users = storage.search_users(q, min(limit, 50))
    channels = storage.search_channels(q, min(limit, 50))
    return packers.pack_contacts_found(
        users, storage=storage, channels=channels, viewer_id=viewer['id'])


def handle_contacts_search(session, req_msg_id, r, storage, push_queue, registry):
    return _contacts_search_impl(session, storage, r, has_flags=False)


def handle_contacts_search_v228(session, req_msg_id, r, storage, push_queue,
                                registry):
    return _contacts_search_impl(session, storage, r, has_flags=True)


def handle_contacts_resolve_username(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    username = r.string()
    if flags & 1:
        r.string()  # referer

    user = storage.get_user_by_username(username)
    if user and user['id'] != SERVICE_USER_ID:
        log.info('resolveUsername %r user=%s', username, user['id'])
        return packers.pack_contacts_resolved_peer(
            user, storage=storage, viewer_id=viewer['id'])

    ch = storage.get_channel_by_username(username)
    if ch:
        log.info('resolveUsername %r channel=%s', username, ch['id'])
        return packers.pack_contacts_resolved_peer_channel(
            ch, viewer_id=viewer['id'], storage=storage)

    log.warning('resolveUsername %r NOT_OCCUPIED viewer=%s',
                username, viewer['id'])
    return rpc_error(400, 'USERNAME_NOT_OCCUPIED')


def handle_contacts_resolve_phone(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    phone = r.string().strip()
    user = storage.get_user_by_phone(phone)
    if not user:
        return rpc_error(400, 'PHONE_NOT_OCCUPIED')
    return packers.pack_contacts_resolved_peer(
        user, storage=storage, viewer_id=viewer['id'])


def handle_users_get_users(session, req_msg_id, r, storage, push_queue, registry):
    viewer_id = storage.get_session_user(session.auth_key_id)
    assert r.u32() == tl.VECTOR
    count = r.i32()
    users_out = []
    for _ in range(count):
        iu_cid = r.u32()
        if iu_cid == tl.INPUT_USER_SELF:
            if not viewer_id:
                return rpc_error(401, 'AUTH_KEY_UNREGISTERED')
            user_id = viewer_id
            u = storage.get_user_by_id(user_id)
            if u:
                packed = packers.pack_user(
                    u, is_self=True, storage=None)
                users_out.append(packed)
                collectibles = storage.get_collectible_usernames(
                    user_id, include_inactive=True)
                if collectibles:
                    log.debug(
                        'getUsers self user_id=%s collectibles=%d packed=%d bytes',
                        user_id, len(collectibles), len(packed))
        elif iu_cid == tl.INPUT_USER:
            uid = r.i64()
            _hash = r.i64()
            u = storage.get_user_by_id(uid)
            if u:
                users_out.append(packers.pack_user(
                    u,
                    is_self=viewer_id is not None and uid == viewer_id,
                    viewer_id=viewer_id,
                    storage=None if (viewer_id is not None and uid == viewer_id) else storage))

    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(users_out))
    for u_bytes in users_out:
        w.raw(u_bytes)
    return w.bytes_out()


def handle_updates_get_state(session, req_msg_id, r, storage, push_queue, registry):
    user_id = storage.resolve_session_user(session.auth_key_id)
    if not user_id:
        return rpc_error(401, 'AUTH_KEY_UNREGISTERED')
    st = storage.get_user_state(user_id)
    w = tl.Writer()
    w.u32(tl.UPDATES_STATE)
    w.i32(int(st['pts']))
    w.i32(int(st['qts']))
    w.i32(int(st['date']))
    w.i32(int(st['seq']))
    w.i32(0)
    return w.bytes_out()


_DIFFERENCE_MSG_LIMIT = 80


def _get_difference_from_pts(storage, viewer: dict, pts: int) -> bytes:
    st = storage.get_user_state(viewer['id'])
    current_pts = int(st['pts'])
    client_pts = max(0, int(pts))
    if client_pts > 0 and (current_pts - client_pts) > 400:
        w = tl.Writer()
        w.u32(tl.UPDATES_DIFFERENCE_TOO_LONG)
        w.i32(current_pts)
        return w.bytes_out()

    if client_pts <= 0:
        client_pts = max(0, current_pts - _DIFFERENCE_MSG_LIMIT)

    messages = storage.get_messages_since_pts(viewer['id'], client_pts)
    if len(messages) > _DIFFERENCE_MSG_LIMIT:
        w = tl.Writer()
        w.u32(tl.UPDATES_DIFFERENCE_TOO_LONG)
        w.i32(current_pts)
        return w.bytes_out()

    users_map = {SERVICE_USER_ID: storage.get_user_by_id(SERVICE_USER_ID)}
    for m in messages:
        for uid in (m['from_id'], m['peer_id']):
            if uid in users_map:
                continue
            u = storage.get_user_by_id(uid)
            if u:
                users_map[uid] = u

    if not messages:
        if pts < int(st['pts']):
            return packers.pack_updates_difference(
                [], [], st['pts'], storage, viewer_id=viewer['id'])
        w = tl.Writer()
        w.u32(tl.UPDATES_DIFFERENCE_EMPTY)
        w.i32(st['date'])
        w.i32(st['seq'])
        return w.bytes_out()

    return packers.pack_updates_difference(
        messages, list(users_map.values()), st['pts'], storage,
        viewer_id=viewer['id'])


def handle_updates_get_difference(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    pts = r.i32()
    if flags & 2:
        r.i32()  # pts_limit
    if flags & 1:
        r.i32()  # pts_total_limit
    _date = r.i32()
    _qts = r.i32()
    if flags & 4:
        r.i32()  # qts_limit

    return _get_difference_from_pts(storage, viewer, pts)


def handle_updates_get_difference_legacy(session, req_msg_id, r, storage,
                                         push_queue, registry):
    """Desktop clients still invoke updates.getDifference#25939651."""
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    pts = r.i32()
    if flags & 1:
        r.i32()  # pts_total_limit
    _date = r.i32()
    _qts = r.i32()

    return _get_difference_from_pts(storage, viewer, pts)


def handle_messages_get_dialogs(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    if flags & 1:
        pass  # exclude_pinned
    if flags & 2:
        r.i32()  # folder_id
    offset_date = r.i32()
    offset_id = r.i32()
    offset_peer = packers.parse_input_peer(r, self_user_id=viewer['id']) or 0
    limit = max(1, min(int(r.i32() or 100), 100))
    _hash = r.i64()

    total = storage.count_dialogs(viewer['id'])
    dialogs = []
    messages = []
    users_map = {viewer['id']: viewer}
    for d in storage.get_dialogs(
            viewer['id'], limit, offset_date, offset_id, offset_peer):
        peer_id = d['peer_id']
        dialog = storage.ensure_dialog(viewer['id'], peer_id)
        top_id = int(dialog.get('top_message_id') or 0)
        if top_id > 0 and storage.get_message(viewer['id'], top_id) is None:
            storage.repair_dialog(viewer['id'], peer_id)
            dialog = storage.ensure_dialog(viewer['id'], peer_id)
            top_id = int(dialog.get('top_message_id') or 0)
        if top_id <= 0:
            if packers.is_channel_id(peer_id):
                dialogs.append(dialog)
            continue
        msg = storage.get_message(viewer['id'], top_id)
        if not msg:
            continue
        dialogs.append(dialog)
        messages.append(msg)
        for uid in (peer_id, int(msg.get('from_id') or 0)):
            if uid and uid not in users_map and not packers.is_channel_id(uid):
                u = storage.get_user_by_id(uid)
                if u:
                    users_map[uid] = u

    log.debug('getDialogs viewer=%s dialogs=%s messages=%s total=%s',
              viewer['id'], len(dialogs), len(messages), total)
    chats = storage.collect_channels_for_dialogs(viewer['id'], dialogs)
    users = list(users_map.values())
    if total > limit:
        return packers.pack_messages_dialogs_slice(
            dialogs, messages, users, total, viewer['id'], storage,
            chats=chats)
    return packers.pack_messages_dialogs(
        dialogs, messages, users, viewer['id'], storage, chats=chats)


def handle_messages_get_recent_reactions(session, req_msg_id, r, storage,
                                         push_queue, registry):
    _limit = r.i32()
    r.i64()  # hash
    w = tl.Writer()
    w.u32(tl.MESSAGES_REACTIONS_NOT_MODIFIED)
    return w.bytes_out()


def handle_messages_get_top_reactions(session, req_msg_id, r, storage,
                                      push_queue, registry):
    _limit = r.i32()
    r.i64()  # hash
    w = tl.Writer()
    w.u32(tl.MESSAGES_REACTIONS_NOT_MODIFIED)
    return w.bytes_out()


def handle_messages_get_default_tag_reactions(session, req_msg_id, r, storage,
                                              push_queue, registry):
    r.i64()  # hash
    w = tl.Writer()
    w.u32(tl.MESSAGES_REACTIONS_NOT_MODIFIED)
    return w.bytes_out()


def handle_messages_get_archived_stickers(session, req_msg_id, r, storage,
                                          push_queue, registry):
    _flags = r.i32()
    _offset_id = r.i64()
    _limit = r.i32()
    w = tl.Writer()
    w.u32(tl.MESSAGES_ARCHIVED_STICKERS)
    w.i32(0)          # count
    w.u32(tl.VECTOR)  # sets
    w.i32(0)
    return w.bytes_out()


def handle_messages_get_faved_stickers(session, req_msg_id, r, storage,
                                       push_queue, registry):
    _hash = r.i64()
    w = tl.Writer()
    w.u32(tl.MESSAGES_FAVED_STICKERS_NOT_MODIFIED)
    return w.bytes_out()


def handle_messages_get_saved_gifs(session, req_msg_id, r, storage,
                                   push_queue, registry):
    _hash = r.i64()
    w = tl.Writer()
    w.u32(tl.MESSAGES_SAVED_GIFS_NOT_MODIFIED)
    return w.bytes_out()


def handle_account_get_saved_ringtones(session, req_msg_id, r, storage,
                                       push_queue, registry):
    _hash = r.i64()
    w = tl.Writer()
    w.u32(tl.ACCOUNT_SAVED_RINGTONES_NOT_MODIFIED)
    return w.bytes_out()


def handle_messages_get_messages(session, req_msg_id, r, storage,
                                 push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    try:
        from handlers.groups import _parse_input_message_ids
        ids = _parse_input_message_ids(r)
    except (AssertionError, ValueError):
        return rpc_error(400, 'MSG_ID_INVALID')
    if not ids:
        return rpc_error(400, 'MESSAGE_IDS_EMPTY')
    messages = []
    for mid in ids:
        msg = storage.get_message(viewer['id'], mid)
        if msg:
            messages.append(msg)
    users = storage.collect_users_for_messages(viewer['id'], messages)
    chats = []
    seen_chats = set()
    for msg in messages:
        for peer_key in ('peer_id', 'from_id'):
            pid = int(msg.get(peer_key) or 0)
            if not packers.is_channel_id(pid) or pid in seen_chats:
                continue
            ch = storage.get_channel(pid)
            if ch:
                chats.append(ch)
                seen_chats.add(pid)
    return packers.pack_messages_messages(
        messages, users, viewer['id'], storage, chats=chats)


def handle_messages_delete_history(session, req_msg_id, r, storage,
                                   push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    _peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    _max_id = r.i32()
    if flags & (1 << 2):
        r.i32()
    if flags & (1 << 3):
        r.i32()
    st = storage.get_user_state(viewer['id'])
    w = tl.Writer()
    w.u32(tl.MESSAGES_AFFECTED_HISTORY)
    w.i32(int(st['pts']))
    w.i32(0)
    w.i32(0)
    return w.bytes_out()


def handle_messages_get_pinned_dialogs(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    folder_id = r.i32()
    if folder_id not in (0,):
        return rpc_error(400, 'FOLDER_ID_INVALID')

    # No pin state is stored yet. Returning every real dialog here made the
    # startup result contradict messages.getDialogs and exposed peers before
    # Desktop had merged their User objects.
    st = storage.get_user_state(viewer['id'])
    return packers.pack_messages_peer_dialogs(
        [], [], [], st['pts'], storage, chats=[], viewer_id=viewer['id'])


def _log_history_request(session, viewer_id, peer_id, offset_id, add_offset,
                         limit, max_id, min_id, req_hash, count, total):
    """Throttled getHistory diagnostics (loops are visible in logs)."""
    import time as _time
    key = (int(viewer_id), int(peer_id))
    now = _time.time()
    last = _LAST_HISTORY_LOG.get(key, 0.0)
    if now - last < 3.0:
        return
    _LAST_HISTORY_LOG[key] = now
    log.info('getHistory viewer=%s peer=%s offset=%s add=%s limit=%s '
             'max=%s min=%s hash=%#x -> msgs=%s total=%s layer=%s',
             viewer_id, peer_id, offset_id, add_offset, limit, max_id,
             min_id, req_hash, count, total,
             getattr(session, 'client_layer', 0))


_LAST_HISTORY_LOG = {}


def handle_messages_get_history(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        # Temp auth keys may prefetch history during login; AUTH errors retry forever.
        return packers.pack_messages_messages([], [], None, storage)

    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id is None:
        return rpc_error(400, 'PEER_ID_INVALID')
    _offset_id = r.i32()
    _offset_date = r.i32()
    add_offset = r.i32()
    limit = r.i32()
    max_id = r.i32()
    min_id = r.i32()
    _hash = r.i64()

    storage.repair_dialog_top_message(viewer['id'], peer_id)
    messages, total = storage.get_messages_history(
        viewer['id'], peer_id, _offset_id, add_offset, limit, max_id, min_id)
    _log_history_request(session, viewer['id'], peer_id, _offset_id,
                         add_offset, limit, max_id, min_id, _hash,
                         len(messages), total)
    users = storage.collect_users_for_messages(viewer['id'], messages)
    chats = []
    if packers.is_channel_id(peer_id):
        ch = storage.get_channel(peer_id)
        if ch and (storage.is_channel_member(peer_id, viewer['id'])
                   or ch.get('username')):
            chats = [ch]
    return packers.pack_messages_slice(
        messages, users, total, viewer['id'], storage,
        offset_id=_offset_id, add_offset=add_offset, chats=chats)


def _pack_saved_dialogs_empty(count: int = 0) -> bytes:
    w = tl.Writer()
    w.u32(0xf83ae221)  # messages.savedDialogs
    for _ in range(4):
        w.u32(tl.VECTOR)
        w.i32(0)
    return w.bytes_out()


def _pack_mono_forum_dialog(d: dict) -> bytes:
    w = tl.Writer()
    w.u32(0x64407ea7)  # monoForumDialog
    w.i32(0)
    w.raw(packers.pack_peer(int(d['peer_id'])))
    w.i32(int(d.get('top_message_id') or 0))
    w.i32(int(d.get('read_inbox_max_id') or 0))
    w.i32(int(d.get('read_outbox_max_id') or 0))
    w.i32(int(d.get('unread_count') or 0))
    w.i32(0)  # unread_reactions_count
    return w.bytes_out()


def _pack_saved_dialogs(dialogs: list[dict], messages: list[dict],
                        chats: list[dict], users: list[dict],
                        viewer_id: int, storage) -> bytes:
    w = tl.Writer()
    w.u32(0xf83ae221)  # messages.savedDialogs
    w.u32(tl.VECTOR)
    w.i32(len(dialogs))
    for d in dialogs:
        w.raw(_pack_mono_forum_dialog(d))
    w.u32(tl.VECTOR)
    w.i32(len(messages))
    for msg in messages:
        w.raw(packers.pack_message(msg, storage))
    w.u32(tl.VECTOR)
    w.i32(len(chats))
    for ch in chats:
        w.raw(packers.pack_channel(ch, viewer_id, storage=storage))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(
            u,
            is_self=int(u['id']) == int(viewer_id),
            viewer_id=viewer_id,
            storage=storage))
    return w.bytes_out()


def _monoforum_saved_dialogs_response(storage, viewer: dict,
                                      monoforum_id: int,
                                      topic_ids: Optional[list[int]] = None,
                                      limit: int = 50) -> bytes:
    mono = storage.get_channel(monoforum_id)
    linked = storage.get_linked_channel_for_monoforum(monoforum_id)
    if not mono or not linked:
        return rpc_error(400, 'CHANNEL_INVALID')
    if not storage.is_channel_admin(int(linked['id']), int(viewer['id'])):
        return rpc_error(400, 'CHAT_ADMIN_REQUIRED')
    dialogs = storage.list_monoforum_dialogs(
        int(viewer['id']), monoforum_id, limit=limit)
    if topic_ids is not None:
        allowed = {int(uid) for uid in topic_ids}
        dialogs = [d for d in dialogs if int(d['peer_id']) in allowed]
    messages = []
    for d in dialogs:
        msg = storage.get_message(int(viewer['id']), int(d['top_message_id']))
        if msg:
            messages.append(msg)
    user_ids = {int(viewer['id'])}
    for d in dialogs:
        user_ids.add(int(d['peer_id']))
    for msg in messages:
        if not packers.is_channel_id(int(msg.get('from_id') or 0)):
            user_ids.add(int(msg['from_id']))
    users = []
    for uid in user_ids:
        u = storage.get_user_by_id(uid)
        if u:
            users.append(u)
    return _pack_saved_dialogs(
        dialogs, messages, [mono, linked], users, int(viewer['id']), storage)


def _saved_dialogs_by_id_body(storage, viewer: dict, r: tl.Reader,
                              flags: int) -> bytes:
    parent_peer = 0
    if flags & 2:
        parent_peer = (
            packers.parse_input_peer(r, self_user_id=viewer['id']) or 0)
    assert r.u32() == tl.VECTOR
    ids = [
        packers.parse_input_peer(r, self_user_id=viewer['id']) or 0
        for _ in range(r.i32())
    ]
    if parent_peer and storage.get_linked_channel_for_monoforum(parent_peer):
        return _monoforum_saved_dialogs_response(
            storage, viewer, parent_peer, topic_ids=ids,
            limit=max(len(ids), 1))
    return _pack_saved_dialogs_empty()


def handle_messages_get_saved_dialogs(session, req_msg_id, r, storage,
                                      push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    parent_peer = 0
    if flags & 2:
        parent_peer = packers.parse_input_peer(r, self_user_id=viewer['id']) or 0
    r.i32()  # offset_date
    r.i32()  # offset_id
    packers.skip_input_peer(r)  # offset_peer
    limit = r.i32()
    _hash = r.i64()
    if parent_peer and storage.get_linked_channel_for_monoforum(parent_peer):
        return _monoforum_saved_dialogs_response(
            storage, viewer, parent_peer, limit=limit)
    return _pack_saved_dialogs_empty()


def handle_messages_get_saved_dialogs_by_id(session, req_msg_id, r, storage,
                                            push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    return _saved_dialogs_by_id_body(storage, viewer, r, flags)


def handle_langpack_get_strings(session, req_msg_id, r, storage, push_queue,
                                registry):
    from handlers import langpack
    lang_pack, lang_code, keys = langpack.parse_get_strings_body(r)
    log.debug('langpack.getStrings pack=%s code=%s keys=%d',
             lang_pack or '-', lang_code, len(keys))
    return langpack.pack_get_strings_response(lang_pack, lang_code, keys)


def handle_langpack_get_languages(session, req_msg_id, r, storage, push_queue,
                                  registry):
    from handlers import langpack
    lang_pack = r.string()
    log.debug('langpack.getLanguages pack=%s resolved=%s',
             lang_pack or '-', langpack._resolve_lang_pack(lang_pack))
    return langpack.pack_get_languages_response(lang_pack)


def handle_langpack_get_language(session, req_msg_id, r, storage, push_queue,
                                 registry):
    from handlers import langpack
    lang_pack = r.string()
    lang_code = r.string()
    log.debug('langpack.getLanguage pack=%s code=%s', lang_pack, lang_code)
    return langpack.pack_get_language_response(lang_pack, lang_code)


def _session_lang_code(session, fallback: str = 'en') -> str:
    return (getattr(session, 'init_lang_code', None) or fallback).strip().lower()


def handle_langpack_get_difference(session, req_msg_id, r, storage, push_queue,
                                   registry):
    from handlers import langpack
    lang_pack, lang_code = langpack.parse_lang_pack_body(r)
    from_version = r.i32()
    resp = langpack.pack_get_difference_response(
        lang_pack, lang_code, from_version)
    log.debug('langpack.getDifference pack=%s code=%s from=%d bytes=%d',
             lang_pack or '-', lang_code, from_version, len(resp))
    return resp


def handle_langpack_get_difference_legacy(session, req_msg_id, r, storage,
                                          push_queue, registry):
    from handlers import langpack
    from_version = r.i32()
    lang_code = _session_lang_code(session, 'en')
    resp = langpack.pack_get_difference_response('', lang_code, from_version)
    log.debug('langpack.getDifference[legacy] code=%s from=%d bytes=%d',
             lang_code, from_version, len(resp))
    return resp


def handle_langpack_get_lang_pack(session, req_msg_id, r, storage, push_queue,
                                  registry):
    from handlers import langpack
    lang_pack, lang_code = langpack.parse_lang_pack_body(r)
    resp = langpack.pack_get_difference_response(
        lang_pack, lang_code, 0)
    log.debug('langpack.getLangPack pack=%s code=%s bytes=%d',
             lang_pack or '-', lang_code, len(resp))
    return resp


def handle_langpack_get_lang_pack_legacy(session, req_msg_id, r, storage,
                                         push_queue, registry):
    from handlers import langpack
    lang_code = r.string()
    resp = langpack.pack_get_difference_response('', lang_code, 0)
    log.debug('langpack.getLangPack[legacy] code=%s bytes=%d',
             lang_code, len(resp))
    return resp


def handle_messages_toggle_suggested_post_approval(session, req_msg_id, r,
                                                   storage, push_queue,
                                                   registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    reject = bool(flags & 2)
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    msg_id = int(r.i32())
    schedule_date = int(r.i32()) if flags & 1 else 0
    reject_comment = r.string() if flags & 4 else ''
    if not peer_id or peer_id < config.CHANNEL_ID_MIN:
        return rpc_error(400, 'PEER_ID_INVALID')

    ch = storage.get_channel(peer_id)
    monoforum_id = peer_id
    if ch and not int(ch.get('monoforum') or 0):
        mono = storage.get_channel_monoforum(peer_id)
        if not mono:
            return rpc_error(400, 'PEER_ID_INVALID')
        monoforum_id = int(mono['id'])

    try:
        result = storage.approve_suggested_post(
            monoforum_id, msg_id, viewer['id'],
            reject=reject,
            schedule_date=schedule_date,
            reject_comment=reject_comment)
    except ValueError as exc:
        return rpc_error(400, str(exc))

    mono = storage.get_channel(monoforum_id)
    linked = storage.get_channel(result['linked_channel_id'])
    chats = [ch for ch in (mono, linked) if ch]
    users_map = {viewer['id']: viewer}
    updated = result['updated_msg']
    from_id = int(updated.get('from_id') or 0)
    if from_id and from_id not in users_map:
        author = storage.get_user_by_id(from_id)
        if author:
            users_map[from_id] = author
    users = list(users_map.values())

    from handlers.groups import (
        pack_update_edit_channel_message,
        pack_update_new_channel_message,
    )
    mono_pts = int(result['pts_monoforum'])
    updates = [
        pack_update_edit_channel_message(updated, mono_pts, storage),
        pack_update_new_channel_message(
            result['service_result']['sender_msg'], mono_pts, storage),
    ]
    channel_result = result.get('channel_result')
    if channel_result and channel_result.get('sender_msg'):
        ch_pts = int(channel_result['pts_channel'])
        updates.append(pack_update_new_channel_message(
            channel_result['sender_msg'], ch_pts, storage))
        for uid in channel_result.get('member_ids') or []:
            uid = int(uid)
            if uid not in users_map:
                u = storage.get_user_by_id(uid)
                if u:
                    users_map[uid] = u
        users = list(users_map.values())

    return packers.pack_updates(
        updates, users, user_id=viewer['id'], storage=storage, chats=chats)


def _saved_history_peer(r: tl.Reader, viewer_id: int) -> tuple[int, int]:
    flags = r.i32()
    parent_peer = 0
    if flags & 1:
        parent_peer = packers.parse_input_peer(r, self_user_id=viewer_id) or 0
    peer = packers.parse_input_peer(r, self_user_id=viewer_id) or 0
    return parent_peer, peer


def handle_messages_get_saved_history(session, req_msg_id, r, storage,
                                      push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    parent_peer, peer = _saved_history_peer(r, viewer['id'])
    offset_id = r.i32()
    offset_date = r.i32()
    add_offset = r.i32()
    limit = r.i32()
    max_id = r.i32()
    min_id = r.i32()
    _hash = r.i64()

    if parent_peer and storage.get_linked_channel_for_monoforum(parent_peer):
        linked = storage.get_linked_channel_for_monoforum(parent_peer)
        if not linked or not storage.is_channel_admin(
                int(linked['id']), int(viewer['id'])):
            return rpc_error(400, 'CHAT_ADMIN_REQUIRED')
        messages, total = storage.get_monoforum_topic_history(
            int(viewer['id']), parent_peer, peer,
            offset_id, add_offset, limit, max_id, min_id)
        users = storage.collect_users_for_messages(viewer['id'], messages)
        topic_user = storage.get_user_by_id(peer)
        if topic_user and all(int(u['id']) != int(peer) for u in users):
            users.append(topic_user)
        chats = [
            ch for ch in (
                storage.get_channel(parent_peer),
                linked,
            ) if ch
        ]
        return packers.pack_messages_slice(
            messages, users, total, viewer['id'], storage,
            offset_id=offset_id, add_offset=add_offset, chats=chats)

    history_peer = parent_peer or peer or viewer['id']
    storage.repair_dialog_top_message(viewer['id'], history_peer)
    messages, total = storage.get_messages_history(
        viewer['id'], history_peer, offset_id, add_offset, limit, max_id, min_id)
    users = storage.collect_users_for_messages(viewer['id'], messages)
    chats = []
    if packers.is_channel_id(history_peer):
        ch = storage.get_channel(history_peer)
        if ch and (storage.is_channel_member(history_peer, viewer['id'])
                   or ch.get('username')):
            chats = [ch]
    return packers.pack_messages_slice(
        messages, users, total, viewer['id'], storage,
        offset_id=offset_id, add_offset=add_offset, chats=chats)


def handle_messages_read_saved_history(session, req_msg_id, r, storage,
                                       push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    parent_peer = packers.parse_input_peer(r, self_user_id=viewer['id'])
    peer = packers.parse_input_peer(r, self_user_id=viewer['id'])
    max_id = r.i32()
    history_peer = parent_peer or peer
    if parent_peer and storage.get_linked_channel_for_monoforum(parent_peer):
        linked = storage.get_linked_channel_for_monoforum(parent_peer)
        if not linked or not storage.is_channel_admin(
                int(linked['id']), int(viewer['id'])):
            return rpc_error(400, 'CHAT_ADMIN_REQUIRED')
        storage.read_monoforum_topic(
            int(viewer['id']), parent_peer, peer, max_id)
        w = tl.Writer()
        w.u32(tl.BOOL_TRUE)
        return w.bytes_out()
    if history_peer:
        if packers.is_channel_id(history_peer):
            try:
                storage.read_channel_history(history_peer, viewer['id'], max_id)
            except ValueError:
                pass
        else:
            storage.mark_dialog_read(viewer['id'], history_peer, max_id)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_messages_report_read_metrics(session, req_msg_id, r, storage,
                                        push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    try:
        packers.skip_input_peer(r)
        if r.u32() == tl.VECTOR:
            for _ in range(r.i32()):
                cid = r.u32()
                if cid != 0x402b4495:  # inputMessageReadMetric
                    break
                r.i32()  # msg_id
                r.i64()  # view_id
                r.i32()  # time_in_view_ms
                r.i32()  # active_time_in_view_ms
                r.i32()  # height_to_viewport_ratio_permille
                r.i32()  # seen_range_ratio_permille
    except Exception:
        pass
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_messages_get_messages_reactions(session, req_msg_id, r, storage,
                                           push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    packers.skip_input_peer(r)
    assert r.u32() == tl.VECTOR
    for _ in range(r.i32()):
        r.i32()
    return packers.pack_updates(
        [], [], user_id=viewer['id'], storage=storage, increment_seq=False)


def handle_messages_get_emoji_keywords_languages(session, req_msg_id, r,
                                                 storage, push_queue,
                                                 registry):
    _viewer, err = require_auth(session, storage)
    if err is not None:
        return err

    assert r.u32() == tl.VECTOR
    for _ in range(r.i32()):
        r.string()
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_messages_get_emoji_keywords(session, req_msg_id, r, storage,
                                        push_queue, registry):
    _viewer, err = require_auth(session, storage)
    if err is not None:
        return err

    lang_code = r.string()
    w = tl.Writer()
    w.u32(0x5cc761bd)  # emojiKeywordsDifference
    w.string(lang_code)
    w.i32(0)  # from_version
    w.i32(0)  # version
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_messages_get_available_effects(session, req_msg_id, r, storage,
                                          push_queue, registry):
    _viewer, err = require_auth(session, storage)
    if err is not None:
        return err

    _hash = r.i32()
    w = tl.Writer()
    w.u32(0xbddb616e)  # messages.availableEffects
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def _validate_document(storage: Storage, document_id: int) -> Optional[dict]:
    if not document_id:
        return None
    return storage.get_document_by_id(document_id)


def _message_allowed(text: str, entities: list, media_document_id: int,
                     media_photo_id: int = 0, media_poll: bool = False) -> bool:
    if media_document_id or media_photo_id or media_poll:
        return True
    if text.strip():
        return True
    return packers.message_has_custom_emoji(entities)


def _enrich_service_push(storage, msg: dict) -> dict:
    """Attach saved_row to service actions so gift/offer packing never fails on push."""
    if not msg or storage is None:
        return msg
    action_json = msg.get('action_json')
    if isinstance(action_json, str) and action_json:
        action = json.loads(action_json)
    elif isinstance(action_json, dict):
        action = dict(action_json)
    else:
        return msg
    if action.get('saved_row'):
        return msg

    saved = None
    atype = action.get('type')
    if action.get('saved_id') and atype in (
            'star_gift_purchase_offer',
            'star_gift_purchase_offer_declined',
    ):
        saved = storage.get_saved_star_gift_by_id(int(action['saved_id']))
    elif atype == 'star_gift_unique':
        uid = int(action.get('unique_id') or 0)
        if uid:
            saved = storage.get_saved_star_gift_by_unique_id(
                int(msg['owner_id']), uid)
            if not saved:
                saved = storage.get_saved_star_gift_by_unique_id_global(uid)
        if not saved and action.get('saved_id'):
            saved = storage.get_saved_star_gift_by_msg(
                int(msg['owner_id']), int(action['saved_id']))
            if not saved:
                saved = storage.get_saved_star_gift_by_msg_id(
                    int(action['saved_id']))
    elif atype == 'star_gift' and action.get('saved'):
        sid = int(action.get('saved_id') or msg.get('id') or 0)
        if sid:
            saved = storage.get_saved_star_gift_by_msg(
                int(msg['owner_id']), sid)

    if saved:
        action['saved_row'] = saved
        return dict(msg, action_json=action)
    return msg


def _push_incoming(storage, push_queue, recipient_id, recipient_msg, users,
                   pts, session, *, saved_row=None):
    if push_queue is None or not recipient_msg:
        return
    push_msg = recipient_msg
    if saved_row:
        action_json = push_msg.get('action_json')
        if isinstance(action_json, str) and action_json:
            action = json.loads(action_json)
        elif isinstance(action_json, dict):
            action = dict(action_json)
        else:
            action = {}
        if action.get('saved_row') is None:
            action['saved_row'] = saved_row
            push_msg = dict(push_msg, action_json=action)
    push_msg = _enrich_service_push(storage, push_msg)
    # Build the update lazily per recipient connection: a message from an
    # Android (layer 224) sender must not reach Desktop 7.2.5 (layer 228)
    # with a layer-224 message/user constructor id.
    upd = packers.push_factory(
        packers.pack_inbox_push_updates, push_msg, users, pts, storage)
    push_queue.push_to_user(
        recipient_id, upd, exclude_auth_key_id=session.auth_key_id)


def _deliver_private_message(storage, push_queue, session, sender, peer_id,
                             text, entities, reply_to_msg_id, random_id,
                             media_document_id: int = 0,
                             media_photo_id: int = 0,
                             paid_message_stars: int = 0,
                             media_poll_json: str = '',
                             quick_reply_shortcut: Optional[dict] = None):
    log.debug(
        'send private start sender=%s peer=%s random_id=%s text_len=%s media_doc=%s media_photo=%s',
        sender['id'], peer_id, random_id, len(text or ''),
        media_document_id, media_photo_id)
    if peer_id == sender['id'] and quick_reply_shortcut:
        from handlers import quick_replies
        return quick_replies.deliver_quick_reply_message(
            storage, sender, text, entities,
            media_document_id=media_document_id,
            media_photo_id=media_photo_id,
            media_poll_json=media_poll_json,
            random_id=random_id,
            shortcut_info=quick_reply_shortcut)
    if peer_id == sender['id']:
        result = storage.send_saved_message(
            sender['id'], text, entities, reply_to_msg_id, media_document_id,
            media_photo_id, media_poll_json=media_poll_json)
        sender_msg = result['sender_msg']
        users = [sender]
        log.debug(
            'send saved ok sender=%s msg_id=%s pts=%s random_id=%s',
            sender['id'], sender_msg['id'], result['pts_sender'], random_id)
        return packers.pack_send_message_updates(
            sender_msg, random_id, users, result['pts_sender'], storage=storage)

    recipient = storage.get_user_by_id(peer_id)
    if not recipient:
        return rpc_error(400, 'PEER_ID_INVALID')
    dialog_before = storage._conn.execute(
        'SELECT top_message_id, last_message_date FROM dialogs '
        'WHERE owner_id=? AND peer_id=?',
        (int(peer_id), int(sender['id'])),
    ).fetchone()
    had_existing_chat = bool(
        dialog_before and int(dialog_before['top_message_id'] or 0) > 0)
    last_message_date = int(
        dialog_before['last_message_date'] or 0) if dialog_before else 0

    deliver = not storage.is_private_message_undelivered(sender['id'], peer_id)
    result = storage.send_private_message(
        sender['id'], peer_id, text, entities, reply_to_msg_id,
        media_document_id, media_photo_id,
        paid_message_stars=paid_message_stars,
        media_poll_json=media_poll_json,
        deliver_to_recipient=deliver)
    sender_msg = result['sender_msg']
    recipient_msg = result['recipient_msg']
    log.debug(
        'send private stored sender=%s peer=%s msg_id=%s pts_sender=%s deliver=%s random_id=%s',
        sender['id'], peer_id, sender_msg['id'], result['pts_sender'],
        deliver, random_id)
    users = [sender, recipient]

    if deliver and recipient_msg is not None:
        _push_incoming(storage, push_queue, peer_id, recipient_msg, users,
                       result['pts_recipient'], session)

        extra = []
        bot_extra = _maybe_bot_reply(
            storage, sender, recipient, peer_id, sender_msg,
            push_queue, session)
        if bot_extra:
            bot_msg = bot_extra['recipient_msg']
            bot_pts = bot_extra['pts_recipient']
            extra.append((bot_msg, bot_pts))
            bot_users = list(users)
            if bot_extra.get('users'):
                for u in bot_extra['users']:
                    if u['id'] not in {x['id'] for x in bot_users}:
                        bot_users.append(u)
            _push_incoming(storage, push_queue, sender['id'], bot_msg,
                           bot_users, bot_pts, session)
        greeting_extra = _maybe_business_greeting(
            storage, sender, recipient,
            had_existing_chat=had_existing_chat,
            last_message_date=last_message_date)
        if greeting_extra:
            g_msg = greeting_extra['recipient_msg']
            g_pts = greeting_extra['pts_recipient']
            extra.append((g_msg, g_pts))
            g_users = list(users)
            if greeting_extra.get('users'):
                for u in greeting_extra['users']:
                    if u['id'] not in {x['id'] for x in g_users}:
                        g_users.append(u)
            _push_incoming(storage, push_queue, sender['id'], g_msg,
                           g_users, g_pts, session)
        away_extra = _maybe_business_away(
            storage, sender, recipient,
            had_existing_chat=had_existing_chat)
        if away_extra:
            away_results = away_extra.get('all_results') or [away_extra]
            a_users = list(users)
            if away_extra.get('users'):
                for u in away_extra['users']:
                    if u['id'] not in {x['id'] for x in a_users}:
                        a_users.append(u)
            for item in away_results:
                a_msg = item['recipient_msg']
                a_pts = item['pts_recipient']
                extra.append((a_msg, a_pts))
                _push_incoming(storage, push_queue, sender['id'], a_msg,
                               a_users, a_pts, session)
    else:
        extra = []

    extra_upd = []
    if paid_message_stars > 0:
        extra_upd.append(packers.pack_user_stars_balance(
            storage, sender['id']))

    return packers.pack_send_message_updates(
        sender_msg, random_id, users, result['pts_sender'],
        extra_messages=extra, storage=storage, extra_updates=extra_upd)


def _check_outgoing_send(storage, sender_id: int, peer_id: int):
    if storage.is_user_frozen(sender_id):
        raise ValueError('FROZEN_METHOD_INVALID')
    if peer_id != sender_id and peer_id < config.CHANNEL_ID_MIN:
        storage.check_private_messaging_allowed(sender_id, peer_id)


def _suggested_post_json(data: dict) -> str:
    if not data:
        return ''
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def _resolve_outgoing_paid_stars(storage, sender_id: int, peer_id: int,
                                 allow_paid_stars: int) -> Tuple[int, Optional[bytes]]:
    if peer_id == sender_id:
        return 0, None
    if peer_id < config.CHANNEL_ID_MIN:
        from handlers.paid_messages import check_private_send_paid
        return check_private_send_paid(
            storage, sender_id, peer_id, allow_paid_stars)
    ch = storage.get_channel(peer_id)
    if not ch:
        return 0, None
    linked_id = peer_id
    if int(ch.get('monoforum') or 0):
        linked = storage.get_linked_channel_for_monoforum(peer_id)
        linked_id = int(linked['id']) if linked else peer_id
    is_admin = storage.is_channel_admin(linked_id, sender_id)
    if int(ch.get('monoforum') or 0) or storage.can_send_direct_to_channel(
            linked_id, sender_id):
        from handlers.paid_messages import check_channel_direct_paid
        return check_channel_direct_paid(
            storage, sender_id, peer_id, allow_paid_stars, is_admin=is_admin)
    return 0, None


def handle_messages_send_message(session, req_msg_id, r, storage, push_queue, registry):
    sender, err = require_auth(session, storage)
    if sender is None:
        return err

    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=sender['id'])
    reply_to_msg_id = 0
    monoforum_peer_id = 0
    if flags & 1:
        reply_info = packers.parse_reply_to(r)
        if reply_info:
            reply_to_msg_id = int(reply_info.get('reply_to_msg_id') or 0)
            monoforum_peer_id = int(reply_info.get('monoforum_peer_id') or 0)
    text = r.string()
    random_id = r.i64()
    if flags & (1 << 2):
        packers.skip_reply_markup(r)
    entities = []
    if flags & (1 << 3):
        entities = packers.parse_entities(r)
    allow_paid_stars, rich_text, rich_entities, suggested_post, quick_reply_shortcut = (
        packers.unpack_send_message_tail(r, flags))
    if not (text or '').strip() and rich_text:
        text = rich_text
    if rich_entities:
        entities = packers.merge_entities(entities, rich_entities)

    entities = packers.merge_entities(
        entities, packers.detect_text_entities(text))

    if peer_id is None:
        return rpc_error(400, 'PEER_ID_INVALID')

    if not _message_allowed(text, entities, 0):
        return rpc_error(400, 'MESSAGE_EMPTY')

    paid_stars, pay_err = _resolve_outgoing_paid_stars(
        storage, sender['id'], peer_id, allow_paid_stars)
    if pay_err:
        return pay_err
    suggested_post_json = _suggested_post_json(suggested_post)

    try:
        _check_outgoing_send(storage, sender['id'], peer_id)
        if peer_id >= config.CHANNEL_ID_MIN:
            from handlers.groups import deliver_to_peer
            return deliver_to_peer(
                storage, push_queue, session, sender, peer_id, text, entities,
                reply_to_msg_id, random_id,
                monoforum_peer_id=monoforum_peer_id,
                paid_message_stars=paid_stars,
                suggested_post_json=suggested_post_json)
        return _deliver_private_message(
            storage, push_queue, session, sender, peer_id, text, entities,
            reply_to_msg_id, random_id, paid_message_stars=paid_stars,
            quick_reply_shortcut=quick_reply_shortcut)
    except ValueError as exc:
        code = 420 if str(exc) == 'FROZEN_METHOD_INVALID' else 400
        return rpc_error(code, str(exc))


def handle_messages_send_media(session, req_msg_id, r, storage, push_queue, registry):
    from handlers import media as media_handlers
    sender, err = require_auth(session, storage)
    if sender is None:
        return err

    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=sender['id'])
    reply_to_msg_id = 0
    monoforum_peer_id = 0
    if flags & 1:
        reply_info = packers.parse_reply_to(r)
        if reply_info:
            reply_to_msg_id = int(reply_info.get('reply_to_msg_id') or 0)
            monoforum_peer_id = int(reply_info.get('monoforum_peer_id') or 0)
    try:
        media = media_handlers.parse_input_media_uploaded(
            r, storage, sender['id'])
    except (ValueError, AssertionError) as exc:
        log.warning(
            'sendMedia MEDIA_INVALID pos=%s remaining=%s error=%s',
            getattr(r, 'pos', '?'), getattr(r, 'remaining', lambda: '?')(), exc)
        return rpc_error(400, 'MEDIA_INVALID')
    if not media_handlers.validate_media_ref(storage, media):
        return rpc_error(400, 'MEDIA_INVALID')
    if (media['type'] == 'document' and peer_id is not None
            and peer_id != sender['id'] and peer_id < config.CHANNEL_ID_MIN):
        doc = storage.get_document_by_id(int(media['id']))
        if doc and storage.document_is_voice(doc):
            from handlers.privacy import check_can_send_voice
            try:
                check_can_send_voice(storage, sender['id'], peer_id)
            except ValueError as exc:
                return rpc_error(403, str(exc))
    text = r.string()
    random_id = r.i64()
    if flags & (1 << 2):
        packers.skip_reply_markup(r)
    entities = []
    if flags & (1 << 3):
        entities = packers.parse_entities(r)
    allow_paid_stars, rich_text, rich_entities, suggested_post, quick_reply_shortcut = (
        packers.unpack_send_message_tail(r, flags))
    if not (text or '').strip() and rich_text:
        text = rich_text
    if rich_entities:
        entities = packers.merge_entities(entities, rich_entities)

    entities = packers.merge_entities(
        entities, packers.detect_text_entities(text))

    if peer_id is None:
        return rpc_error(400, 'PEER_ID_INVALID')

    media_document_id = int(media['id']) if media['type'] == 'document' else 0
    media_photo_id = int(media['id']) if media['type'] == 'photo' else 0
    media_poll_json = ''
    if media['type'] == 'poll':
        from handlers.polls import build_poll_json
        media_poll_json = build_poll_json(media)
    elif media['type'] == 'dice':
        media_poll_json = media_handlers.build_dice_json(media)
    if not _message_allowed(
            text, entities, media_document_id, media_photo_id,
            media_poll=bool(media_poll_json)):
        return rpc_error(400, 'MESSAGE_EMPTY')

    paid_stars, pay_err = _resolve_outgoing_paid_stars(
        storage, sender['id'], peer_id, allow_paid_stars)
    if pay_err:
        return pay_err
    suggested_post_json = _suggested_post_json(suggested_post)

    try:
        _check_outgoing_send(storage, sender['id'], peer_id)
        if peer_id >= config.CHANNEL_ID_MIN:
            from handlers.groups import deliver_to_peer
            return deliver_to_peer(
                storage, push_queue, session, sender, peer_id, text, entities,
                reply_to_msg_id, random_id,
                media_document_id=media_document_id,
                media_photo_id=media_photo_id,
                media_poll_json=media_poll_json,
                monoforum_peer_id=monoforum_peer_id,
                paid_message_stars=paid_stars,
                suggested_post_json=suggested_post_json)
        return _deliver_private_message(
            storage, push_queue, session, sender, peer_id, text, entities,
            reply_to_msg_id, random_id,
            media_document_id=media_document_id,
            media_photo_id=media_photo_id,
            paid_message_stars=paid_stars,
            media_poll_json=media_poll_json,
            quick_reply_shortcut=quick_reply_shortcut)
    except ValueError as exc:
        code = 420 if str(exc) == 'FROZEN_METHOD_INVALID' else 400
        return rpc_error(code, str(exc))


def _media_payload(media: dict) -> tuple[int, int, str]:
    media_document_id = int(media['id']) if media['type'] == 'document' else 0
    media_photo_id = int(media['id']) if media['type'] == 'photo' else 0
    media_poll_json = ''
    if media['type'] == 'poll':
        from handlers.polls import build_poll_json
        media_poll_json = build_poll_json(media)
    elif media['type'] == 'dice':
        from handlers.media import build_dice_json
        media_poll_json = build_dice_json(media)
    return media_document_id, media_photo_id, media_poll_json


def handle_messages_send_multi_media(session, req_msg_id, r, storage,
                                     push_queue, registry):
    from handlers import media as media_handlers
    sender, err = require_auth(session, storage)
    if sender is None:
        return err

    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=sender['id'])
    reply_to_msg_id = 0
    monoforum_peer_id = 0
    if flags & 1:
        reply_info = packers.parse_reply_to(r)
        if reply_info:
            reply_to_msg_id = int(reply_info.get('reply_to_msg_id') or 0)
            monoforum_peer_id = int(reply_info.get('monoforum_peer_id') or 0)

    try:
        assert r.u32() == tl.VECTOR
        count = r.i32()
        items = []
        for _ in range(count):
            assert r.u32() == 0x1cc6e91f  # inputSingleMedia
            item_flags = r.i32()
            media = media_handlers.parse_input_media_uploaded(
                r, storage, sender['id'])
            random_id = r.i64()
            text = r.string()
            entities = (
                packers.parse_entities(r)
                if item_flags & 1 else [])
            items.append((media, random_id, text, entities))
    except (ValueError, AssertionError) as exc:
        log.warning(
            'sendMultiMedia MEDIA_INVALID pos=%s remaining=%s error=%s',
            getattr(r, 'pos', '?'), getattr(r, 'remaining', lambda: '?')(), exc)
        return rpc_error(400, 'MEDIA_INVALID')

    allow_paid_stars, rich_text, rich_entities, suggested_post, quick_reply_shortcut = (
        packers.unpack_send_message_tail(r, flags))

    if peer_id is None:
        return rpc_error(400, 'PEER_ID_INVALID')
    if len(items) != 1:
        return rpc_error(400, 'MEDIA_INVALID')

    media, random_id, text, entities = items[0]
    if not (text or '').strip() and rich_text:
        text = rich_text
    if rich_entities:
        entities = packers.merge_entities(entities, rich_entities)
    if not media_handlers.validate_media_ref(storage, media):
        return rpc_error(400, 'MEDIA_INVALID')
    entities = packers.merge_entities(
        entities, packers.detect_text_entities(text))

    media_document_id, media_photo_id, media_poll_json = _media_payload(media)
    if not _message_allowed(
            text, entities, media_document_id, media_photo_id,
            media_poll=bool(media_poll_json)):
        return rpc_error(400, 'MESSAGE_EMPTY')

    paid_stars, pay_err = _resolve_outgoing_paid_stars(
        storage, sender['id'], peer_id, allow_paid_stars)
    if pay_err:
        return pay_err
    suggested_post_json = _suggested_post_json(suggested_post)

    try:
        _check_outgoing_send(storage, sender['id'], peer_id)
        if peer_id >= config.CHANNEL_ID_MIN:
            from handlers.groups import deliver_to_peer
            return deliver_to_peer(
                storage, push_queue, session, sender, peer_id, text, entities,
                reply_to_msg_id, random_id,
                media_document_id=media_document_id,
                media_photo_id=media_photo_id,
                media_poll_json=media_poll_json,
                monoforum_peer_id=monoforum_peer_id,
                paid_message_stars=paid_stars,
                suggested_post_json=suggested_post_json)
        return _deliver_private_message(
            storage, push_queue, session, sender, peer_id, text, entities,
            reply_to_msg_id, random_id,
            media_document_id=media_document_id,
            media_photo_id=media_photo_id,
            paid_message_stars=paid_stars,
            media_poll_json=media_poll_json,
            quick_reply_shortcut=quick_reply_shortcut)
    except ValueError as exc:
        code = 420 if str(exc) == 'FROZEN_METHOD_INVALID' else 400
        return rpc_error(code, str(exc))


MESSAGES_FORWARD_MESSAGES = 0x13704a7c


def handle_messages_forward_messages(session, req_msg_id, r, storage,
                                     push_queue, registry):
    sender, err = require_auth(session, storage)
    if sender is None:
        return err

    flags = r.i32()
    drop_author = bool(flags & (1 << 11))
    drop_media_captions = bool(flags & (1 << 12))
    from_peer_id = packers.parse_input_peer(r, self_user_id=sender['id'])
    assert r.u32() == tl.VECTOR
    msg_count = r.i32()
    msg_ids = [int(r.i32()) for _ in range(msg_count)]
    assert r.u32() == tl.VECTOR
    rand_count = r.i32()
    random_ids = [int(r.i64()) for _ in range(rand_count)]
    to_peer_id = packers.parse_input_peer(r, self_user_id=sender['id'])
    if flags & (1 << 9):
        r.i32()
    if flags & (1 << 22):
        packers.parse_reply_to(r)
    if flags & (1 << 10):
        r.i32()
    if flags & (1 << 24):
        r.i32()
    if flags & (1 << 13):
        packers.skip_input_peer(r)
    if flags & (1 << 17):
        packers.skip_input_quick_reply_shortcut(r)
    if flags & (1 << 18):
        r.i64()
    if flags & (1 << 20):
        r.i32()
    allow_paid_stars = int(r.i64()) if flags & (1 << 21) else 0
    if flags & (1 << 23):
        packers.skip_suggested_post(r)

    if from_peer_id is None or to_peer_id is None:
        return rpc_error(400, 'PEER_ID_INVALID')
    if not msg_ids:
        return rpc_error(400, 'MESSAGE_ID_INVALID')
    if len(random_ids) < len(msg_ids):
        random_ids.extend([0] * (len(msg_ids) - len(random_ids)))

    paid_stars, pay_err = _resolve_outgoing_paid_stars(
        storage, sender['id'], to_peer_id, allow_paid_stars)
    if pay_err:
        return pay_err

    try:
        _check_outgoing_send(storage, sender['id'], to_peer_id)
        if to_peer_id >= config.CHANNEL_ID_MIN:
            from handlers.groups import deliver_to_peer
            last_upd = None
            for i, mid in enumerate(msg_ids):
                src = storage.get_message(sender['id'], int(mid))
                if not src:
                    if from_peer_id >= config.CHANNEL_ID_MIN:
                        storage.ensure_channel_messages_for_member(
                            from_peer_id, sender['id'])
                        src = storage.get_message(sender['id'], int(mid))
                if not src:
                    return rpc_error(400, 'MESSAGE_ID_INVALID')
                text = '' if drop_media_captions else (src.get('text') or '')
                entities = src.get('entities') or []
                if isinstance(entities, str):
                    entities = json.loads(entities)
                last_upd = deliver_to_peer(
                    storage, push_queue, session, sender, to_peer_id,
                    text, entities, 0, random_ids[i],
                    media_document_id=int(src.get('media_document_id') or 0),
                    media_photo_id=int(src.get('media_photo_id') or 0),
                    media_poll_json=src.get('media_poll_json') or '',
                    paid_message_stars=paid_stars)
            return last_upd or rpc_error(400, 'MESSAGE_ID_INVALID')

        forwarded = storage.forward_messages(
            sender['id'], from_peer_id, to_peer_id, msg_ids,
            drop_author=drop_author,
            drop_media_captions=drop_media_captions)
    except ValueError as exc:
        code = 420 if str(exc) == 'FROZEN_METHOD_INVALID' else 400
        return rpc_error(code, str(exc))

    users_map = {sender['id']: sender}
    updates_list: list[bytes] = []

    for i, result in enumerate(forwarded):
        msg = result['sender_msg']
        w_mid = tl.Writer()
        w_mid.u32(tl.UPDATE_MESSAGE_ID)
        w_mid.i32(int(msg['id']))
        w_mid.i64(int(random_ids[i]))
        updates_list.append(w_mid.bytes_out())
        updates_list.extend(packers.pack_message_delivery_update_bytes(
            msg, int(result['pts_sender']), storage,
            dialog_peer_id=int(msg.get('peer_id') or 0)))
        peer = int(msg.get('peer_id') or 0)
        if peer and peer not in users_map:
            u = storage.get_user_by_id(peer)
            if u:
                users_map[peer] = u
        if push_queue and result.get('recipient_msg'):
            _push_incoming(
                storage, push_queue, peer, result['recipient_msg'],
                list(users_map.values()), int(result.get('pts_recipient') or 0),
                session)

    return packers.pack_updates(
        updates_list, list(users_map.values()),
        user_id=sender['id'], storage=storage, increment_seq=True)


def _maybe_bot_reply(storage, sender, recipient, peer_id, incoming_msg,
                     push_queue=None, session=None):
    if not recipient.get('is_bot'):
        return None
    push_user_update = None
    if int(recipient['id']) == int(config.STICKERS_BOT_ID):
        from handlers.stickers_bot import handle_stickers_bot_message
        reply = handle_stickers_bot_message(storage, sender, incoming_msg) or {}
    elif int(recipient['id']) == int(config.REPORT_BOT_ID):
        from handlers.report_bot import handle_report_bot_message
        reply = handle_report_bot_message(storage, sender, incoming_msg) or {}
    elif int(recipient['id']) == int(config.PREMIUM_BOT_ID):
        from handlers.premium_bot import handle_premium_bot_message
        reply = handle_premium_bot_message(storage, sender, incoming_msg) or {}
    else:
        reply = {}
    bot_text = reply.get('text') or 'Принято! Я @mybot — демо-бот MyTelegram.'
    bot_entities = reply.get('entities') or []
    reply_markup_json = reply.get('reply_markup_json') or ''
    media_document_id = int(reply.get('media_document_id') or 0)
    media_photo_id = int(reply.get('media_photo_id') or 0)
    push_user_update = reply.get('push_user_update')
    bot_result = storage.send_bot_message(
        peer_id, sender['id'], bot_text, bot_entities,
        media_document_id=media_document_id,
        media_photo_id=media_photo_id,
        reply_markup_json=reply_markup_json)
    bot_user = storage.get_user_by_id(peer_id)
    if push_user_update and push_queue is not None:
        uid = int(push_user_update)
        user_row = storage.get_user_by_id(uid)
        if user_row:
            exclude = session.auth_key_id if session else None
            upd = packers.pack_updates(
                [packers.pack_update_user(uid)],
                [user_row],
                user_id=uid,
                storage=storage,
            )
            push_queue.push_to_user(uid, upd, exclude_auth_key_id=exclude)
    return {
        'recipient_msg': bot_result['recipient_msg'],
        'pts_recipient': bot_result['pts_recipient'],
        'users': [bot_user],
    }


def _business_recipients_match(config_row: dict, sender_id: int,
                               had_existing_chat: bool) -> bool:
    flags = int(config_row.get('recipients_flags') or 0)
    selected = {int(uid) for uid in (config_row.get('recipients_users') or [])}
    exclude_selected = bool(flags & (1 << 5))

    if selected:
        if exclude_selected and int(sender_id) in selected:
            return False
        if not exclude_selected and int(sender_id) not in selected:
            return False

    has_chat_kind_filter = bool((flags & (1 << 0)) or (flags & (1 << 1)))
    if has_chat_kind_filter:
        if had_existing_chat and not (flags & (1 << 0)):
            return False
        if not had_existing_chat and not (flags & (1 << 1)):
            return False

    # contacts/non_contacts are accepted and persisted, but contact graph
    # is not modeled in this server.
    return True


def _maybe_business_greeting(storage, sender, recipient, *,
                             had_existing_chat: bool,
                             last_message_date: int):
    if recipient.get('is_bot'):
        return None
    config_row = storage.get_business_greeting_message(int(recipient['id']))
    if not config_row:
        return None
    if not _business_recipients_match(
            config_row, int(sender['id']), had_existing_chat):
        return None

    inactivity_days = int(config_row.get('no_activity_days') or 0)
    if had_existing_chat:
        if inactivity_days <= 0:
            return None
        if (int(time.time()) - int(last_message_date or 0)) < inactivity_days * 86400:
            return None

    shortcut_id = int(config_row.get('shortcut_id') or 0)
    from handlers import quick_replies
    result = quick_replies.send_shortcut_to_peer(
        storage, recipient, int(sender['id']), shortcut_id, offline=True)
    if not result:
        text = f'👋 Business greeting #{shortcut_id}'
        result = storage.send_private_message(
            int(recipient['id']), int(sender['id']), text, [],
            deliver_to_recipient=True, offline=True)
        return {
            'recipient_msg': result['recipient_msg'],
            'pts_recipient': result['pts_recipient'],
            'users': [recipient],
        }
    return {
        'recipient_msg': result['recipient_msg'],
        'pts_recipient': result['pts_recipient'],
        'users': [recipient],
    }


def _business_owner_recently_online(owner: dict, *, within_seconds: int = 600) -> bool:
    now = int(time.time())
    online_until = int(owner.get('online_until') or 0)
    if online_until > now:
        return True
    last_seen = int(owner.get('last_seen') or 0)
    return (now - last_seen) < within_seconds


def _maybe_business_away(storage, sender, recipient, *,
                         had_existing_chat: bool):
    if recipient.get('is_bot'):
        return None
    owner_id = int(recipient['id'])
    sender_id = int(sender['id'])
    config_row = storage.get_business_away_message(owner_id)
    if not config_row:
        return None
    if not _business_recipients_match(
            config_row, sender_id, had_existing_chat):
        return None
    if not packers.business_away_schedule_active(config_row, storage, owner_id):
        return None
    if config_row.get('offline_only') and _business_owner_recently_online(recipient):
        return None
    if storage.business_away_message_was_sent(owner_id, sender_id):
        return None

    shortcut_id = int(config_row.get('shortcut_id') or 0)
    from handlers import quick_replies
    result = quick_replies.send_shortcut_to_peer(
        storage, recipient, sender_id, shortcut_id, offline=True)
    if not result:
        return None
    storage.mark_business_away_message_sent(owner_id, sender_id)
    all_results = result.get('all_results') or [result]
    return {
        'recipient_msg': result['recipient_msg'],
        'pts_recipient': result['pts_recipient'],
        'users': [recipient],
        'all_results': all_results,
    }


def handle_messages_get_peer_dialogs(session, req_msg_id, r, storage,
                                   push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    assert r.u32() == tl.VECTOR
    count = r.i32()
    peer_ids = []
    for _ in range(count):
        cid = r.u32()
        if cid == 0xfcaafeb7:  # inputDialogPeer
            pid = packers.parse_input_peer(r, self_user_id=viewer['id'])
            if pid:
                peer_ids.append(pid)
        elif cid == 0x64600527:  # inputDialogPeerFolder
            r.i32()

    dialogs = []
    messages = []
    users_map = {}
    channel_chats = []
    for pid in peer_ids:
        if packers.is_channel_id(pid):
            ch = storage.get_channel(pid)
            if ch:
                channel_chats.append(ch)
                if not storage.is_channel_member(pid, viewer['id']):
                    continue
            storage.repair_dialog(viewer['id'], pid)
            d = storage.ensure_dialog(viewer['id'], pid)
            top_id = int(d.get('top_message_id') or 0)
            if top_id > 0:
                msg = storage.get_message(viewer['id'], top_id)
                if msg:
                    dialogs.append(d)
                    messages.append(msg)
        else:
            storage.repair_dialog(viewer['id'], pid)
            d = storage.ensure_dialog(viewer['id'], pid)
            top_id = int(d.get('top_message_id') or 0)
            if top_id > 0:
                msg = storage.get_message(viewer['id'], top_id)
                if msg:
                    dialogs.append(d)
                    messages.append(msg)
            u = storage.get_user_by_id(pid)
            if u:
                users_map[pid] = u

    st = storage.get_user_state(viewer['id'])
    chats = storage.collect_channels_for_dialogs(viewer['id'], dialogs)
    seen_chats = {int(ch['id']) for ch in chats}
    for ch in channel_chats:
        if int(ch['id']) not in seen_chats:
            chats.append(ch)
            seen_chats.add(int(ch['id']))
    return packers.pack_messages_peer_dialogs(
        dialogs, messages, list(users_map.values()), st['pts'], storage,
        chats=chats,
        viewer_id=viewer['id'])


def handle_messages_read_history(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    max_id = r.i32()
    if peer_id is None:
        return rpc_error(400, 'PEER_ID_INVALID')

    pts = storage.mark_dialog_read(viewer['id'], peer_id, max_id)
    return packers.pack_affected_messages(pts, 1)


def handle_messages_get_peer_settings(session, req_msg_id, r, storage,
                                      push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id is None:
        return rpc_error(400, 'PEER_ID_INVALID')
    if packers.is_channel_id(peer_id):
        ch = storage.get_channel(peer_id)
        if not ch or (
            not storage.is_channel_member(peer_id, viewer['id'])
            and not ch.get('username')
        ):
            return rpc_error(400, 'PEER_ID_INVALID')
        return packers.pack_peer_settings_response_channel(
            ch, viewer_id=viewer['id'], storage=storage)
    peer = storage.get_user_by_id(peer_id)
    if not peer:
        return rpc_error(400, 'PEER_ID_INVALID')
    return packers.pack_peer_settings_response(
        peer, storage=storage, viewer_id=viewer['id'])


def handle_bots_can_send_message(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    _ = packers.parse_input_user(r, storage)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_messages_start_bot(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    bot = packers.parse_input_user(r, storage)
    if not bot:
        return rpc_error(400, 'USER_ID_INVALID')
    _peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    _random_id = r.i64()
    _start_param = r.string()

    return packers.pack_empty_updates()


def handle_account_update_status(session, req_msg_id, r, storage, push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    offline = r.bool()
    if offline:
        storage.set_user_offline(viewer['id'])
    else:
        storage.set_user_online(viewer['id'])
    # Presence fan-out to every dialog partner blocks the MTProto container
    # (updateStatus often precedes getDialogs) and leaves chats on Loading.
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_messages_get_scheduled_history(session, req_msg_id, r, storage,
                                          push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    _peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    _hash = r.i64()
    return packers.pack_messages_messages([], [], viewer['id'])


def handle_contacts_get_contacts(session, req_msg_id, r, storage,
                                 push_queue, registry):
    req_hash = r.i64()
    if req_hash and int(req_hash) == 0:
        w = tl.Writer()
        w.u32(tl.CONTACTS_NOT_MODIFIED)
        return w.bytes_out()
    w = tl.Writer()
    w.u32(0xeae87e42)  # contacts.contacts
    w.u32(tl.VECTOR)
    w.i32(0)
    w.i32(0)  # saved_count
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_contacts_import_contacts(session, req_msg_id, r, storage,
                                    push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    assert r.u32() == tl.VECTOR
    _count = r.i32()
    for _ in range(_count):
        _cid = r.u32()  # inputPhoneContact
        if _cid != 0xf392b7f4:
            return rpc_error(400, 'INPUT_CONSTRUCTOR_INVALID')
        r.i64()     # client_id
        r.string()  # phone
        r.string()  # first_name
        r.string()  # last_name
    w = tl.Writer()
    w.u32(tl.CONTACTS_IMPORTED_CONTACTS)
    for _ in range(3):
        w.u32(tl.VECTOR)
        w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_messages_hide_peer_settings_bar(session, req_msg_id, r, storage,
                                           push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    packers.skip_input_peer(r)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_messages_get_onlines(session, req_msg_id, r, storage,
                                push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    packers.skip_input_peer(r)
    w = tl.Writer()
    w.u32(tl.CHAT_ONLINES)
    w.i32(0)
    return w.bytes_out()


def handle_messages_get_outbox_read_date(session, req_msg_id, r, storage,
                                         push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    packers.skip_input_peer(r)
    r.i32()  # msg_id
    w = tl.Writer()
    w.u32(tl.OUTBOX_READ_DATE)
    w.i32(int(time.time()))
    return w.bytes_out()


def handle_messages_get_search_results_positions(session, req_msg_id, r, storage,
                                                 push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    packers.skip_input_peer(r)
    if flags & 4:
        packers.skip_input_peer(r)
    _filter_cid = r.u32()
    r.i32()  # offset_id
    r.i32()  # limit
    w = tl.Writer()
    w.u32(tl.MESSAGES_SEARCH_RESULTS_POSITIONS)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_premium_get_boosts_status(session, req_msg_id, r, storage,
                                     push_queue, registry):
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    packers.skip_input_peer(r)
    w = tl.Writer()
    w.u32(tl.PREMIUM_BOOSTS_STATUS)
    w.i32(0)  # flags
    w.i32(0)  # level
    w.i32(0)  # current_level_boosts
    w.i32(0)  # boosts
    w.string('')
    return w.bytes_out()


def handle_contacts_get_birthdays(session, req_msg_id, r, storage,
                                  push_queue, registry):
    from handlers.privacy import (
        INPUT_PRIVACY_KEY_BIRTHDAY,
        can_view_privacy,
        decode_user_birthday,
        pack_birthday,
    )
    CONTACT_BIRTHDAY = 0x1d998733
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    rows = storage._conn.execute(
        '''SELECT peer_id FROM dialogs
           WHERE owner_id=? AND peer_id>0 AND peer_id<? AND top_message_id>0''',
        (viewer['id'], config.CHANNEL_ID_MIN)).fetchall()
    contacts = []
    users = []
    for row in rows:
        uid = int(row['peer_id'])
        if uid == viewer['id']:
            continue
        if not can_view_privacy(
                storage, uid, viewer['id'], INPUT_PRIVACY_KEY_BIRTHDAY):
            continue
        u = storage.get_user_by_id(uid)
        if not u:
            continue
        decoded = decode_user_birthday(u.get('user_birthday'))
        if not decoded:
            continue
        day, month, year = decoded
        contacts.append({
            'contact_id': uid,
            'birthday': (day, month, year),
        })
        users.append(u)

    w = tl.Writer()
    w.u32(tl.CONTACTS_CONTACT_BIRTHDAYS)
    w.u32(tl.VECTOR)
    w.i32(len(contacts))
    for item in contacts:
        w.u32(CONTACT_BIRTHDAY)
        w.i64(int(item['contact_id']))
        day, month, year = item['birthday']
        w.raw(pack_birthday(day, month, year))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, viewer_id=viewer['id'], storage=storage))
    return w.bytes_out()


def handle_contacts_get_sponsored_peers(session, req_msg_id, r, storage,
                                        push_queue, registry):
    _q = r.string()
    w = tl.Writer()
    w.u32(tl.CONTACTS_SPONSORED_PEERS_EMPTY)
    return w.bytes_out()


def handle_contacts_get_top_peers(session, req_msg_id, r, storage,
                                 push_queue, registry):
    flags = r.i32()
    if flags & 1:
        pass  # correspondents
    if flags & 2:
        pass  # bots_pm
    if flags & 4:
        pass  # bots_inline
    if flags & 8:
        pass  # phone_calls
    if flags & 16:
        pass  # forward_users
    if flags & 32:
        pass  # forward_chats
    if flags & 1024:
        pass  # groups
    if flags & 32768:
        pass  # channels
    if flags & 65536:
        pass  # bots_app
    _offset = r.i32()
    _limit = r.i32()
    _hash = r.i64()
    w = tl.Writer()
    w.u32(tl.CONTACTS_TOP_PEERS_NOT_MODIFIED)
    return w.bytes_out()


def handle_messages_check_quick_reply_shortcut(
        session, req_msg_id, r, storage, push_queue, registry):
    _ = r.string()
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_messages_get_quick_replies(session, req_msg_id, r, storage,
                                      push_queue, registry):
    from handlers import quick_replies
    return quick_replies.handle_get_quick_replies(
        session, req_msg_id, r, storage, push_queue, registry)


def handle_messages_get_quick_reply_messages(session, req_msg_id, r, storage,
                                             push_queue, registry):
    from handlers import quick_replies
    return quick_replies.handle_get_quick_reply_messages(
        session, req_msg_id, r, storage, push_queue, registry)


def handle_messages_delete_quick_reply_shortcut(session, req_msg_id, r, storage,
                                                push_queue, registry):
    from handlers import quick_replies
    return quick_replies.handle_delete_quick_reply_shortcut(
        session, req_msg_id, r, storage, push_queue, registry)


def handle_messages_get_dialog_filters(session, req_msg_id, r, storage, push_queue, registry):
    w = tl.Writer()
    w.u32(tl.MESSAGES_DIALOG_FILTERS)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_account_register_device(session, req_msg_id, r, storage, push_queue, registry):
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_help_get_app_config(session, req_msg_id, r, storage, push_queue, registry):
    req_hash = r.i32()
    user = None
    user_id = storage.get_session_user(session.auth_key_id)
    if user_id:
        user = storage.get_user_by_id(user_id)
    entries = packers.build_app_config_entries(user=user, storage=storage)
    cfg_hash = packers.app_config_hash(entries)
    if req_hash and int(req_hash) == int(cfg_hash):
        w = tl.Writer()
        w.u32(tl.HELP_APP_CONFIG_NOT_MODIFIED)
        return w.bytes_out()
    w = tl.Writer()
    w.u32(tl.HELP_APP_CONFIG)
    w.i32(int(cfg_hash))
    w.raw(packers.pack_json_object(entries))
    return w.bytes_out()


def handle_help_get_app_update(session, req_msg_id, r, storage, push_queue,
                               registry):
    r.string()  # source
    w = tl.Writer()
    w.u32(tl.HELP_NO_APP_UPDATE)
    return w.bytes_out()


def handle_account_get_global_privacy_settings(session, req_msg_id, r, storage,
                                               push_queue, registry):
    from handlers.paid_messages import handle_account_get_global_privacy_settings as _h
    return _h(session, req_msg_id, r, storage, push_queue, registry)


def handle_account_get_notify_settings(session, req_msg_id, r, storage,
                                       push_queue, registry):
    packers.skip_input_notify_peer(r)
    return packers.pack_peer_notify_settings()


def handle_help_get_countries_list(session, req_msg_id, r, storage, push_queue, registry):
    _lang = r.string()
    req_hash = r.i32()
    from handlers.phone_countries import pack_countries_list
    return pack_countries_list(int(req_hash))


def _build_timezones_payload() -> tuple[list[dict], int]:
    zones = set(available_timezones() or [])
    if not zones:
        zones = {'UTC'}
    payload = []
    now_utc = datetime.utcnow()
    for tz_id in sorted(zones):
        try:
            tz = ZoneInfo(tz_id)
            offset = int((now_utc.replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).utcoffset() or 0).total_seconds())
        except Exception:
            continue
        payload.append({
            'id': tz_id,
            'name': tz_id.replace('_', ' '),
            'utc_offset': offset,
        })
    if not payload:
        payload = [{'id': 'UTC', 'name': 'UTC', 'utc_offset': 0}]
    digest = zlib.crc32(
        '\n'.join(
            f"{row['id']}|{row['name']}|{int(row['utc_offset'])}"
            for row in payload
        ).encode('utf-8')
    ) & 0x7FFFFFFF
    return payload, int(digest)


def handle_help_get_timezones_list(session, req_msg_id, r, storage, push_queue, registry):
    global _timezone_cache
    req_hash = r.i32()
    if _timezone_cache is None:
        _timezone_cache = _build_timezones_payload()
    payload, payload_hash = _timezone_cache
    if int(req_hash) == int(payload_hash):
        w = tl.Writer()
        w.u32(tl.HELP_TIMEZONES_LIST_NOT_MODIFIED)
        return w.bytes_out()

    w = tl.Writer()
    w.u32(tl.HELP_TIMEZONES_LIST)
    w.u32(tl.VECTOR)
    w.i32(len(payload))
    for row in payload:
        w.u32(tl.TIMEZONE)
        w.string(row['id'])
        w.string(row['name'])
        w.i32(int(row['utc_offset']))
    w.i32(int(payload_hash))
    return w.bytes_out()


def handle_lang_pack_get_languages(session, req_msg_id, r, storage, push_queue, registry):
    from handlers import langpack
    log.debug('langPack.getLanguages (android pack)')
    return langpack.pack_get_languages_response('')


def handle_messages_get_default_history_ttl(session, req_msg_id, r, storage,
                                            push_queue, registry):
    w = tl.Writer()
    w.u32(0x43b46b20)  # defaultHistoryTTL
    w.i32(0)
    return w.bytes_out()


def handle_account_get_account_ttl(session, req_msg_id, r, storage,
                                   push_queue, registry):
    w = tl.Writer()
    w.u32(0xb8d0afdf)  # accountDaysTTL
    w.i32(365)
    return w.bytes_out()


HANDLERS: Dict[int, Callable] = {
    0x58e4a740: handle_rpc_drop_answer,
    0x58943ee2: handle_messages_set_typing,
    tl.HELP_GET_NEAREST_DC: handle_help_get_nearest_dc,
    tl.HELP_GET_CONFIG: handle_help_get_config,
    tl.HELP_GET_APP_CONFIG: handle_help_get_app_config,
    tl.HELP_GET_APP_UPDATE: handle_help_get_app_update,
    tl.HELP_GET_COUNTRIES_LIST: handle_help_get_countries_list,
    tl.HELP_GET_TIMEZONES_LIST: handle_help_get_timezones_list,
    tl.HELP_GET_INVITE_TEXT: handle_help_get_invite_text,
    tl.AUTH_BIND_TEMP_AUTH_KEY: handle_auth_bind_temp_auth_key,
    tl.AUTH_EXPORT_AUTH: handle_auth_export_authorization,
    tl.AUTH_IMPORT_AUTH: handle_auth_import_authorization,
    tl.AUTH_SEND_CODE: handle_auth_send_code,
    tl.AUTH_REQUEST_FIREBASE_SMS: handle_auth_request_firebase_sms,
    tl.AUTH_INIT_PASSKEY_LOGIN: handle_auth_init_passkey_login,
    tl.AUTH_EXPORT_LOGIN_TOKEN: handle_auth_export_login_token,
    tl.AUTH_ACCEPT_LOGIN_TOKEN: handle_auth_accept_login_token,
    tl.AUTH_IMPORT_LOGIN_TOKEN: handle_auth_import_login_token,
    tl.AUTH_RESEND_CODE: handle_auth_resend_code,
    tl.AUTH_CANCEL_CODE: handle_auth_cancel_code,
    tl.AUTH_SIGN_IN: handle_auth_sign_in,
    tl.AUTH_SIGN_UP: handle_auth_sign_up,
    tl.AUTH_SIGN_UP_LAYER: handle_auth_sign_up_layer,
    tl.AUTH_LOG_OUT: handle_auth_log_out,
    tl.HELP_GET_PEER_COLORS: handle_help_get_peer_colors,
    tl.HELP_GET_PEER_PROFILE_COLORS: handle_help_get_peer_profile_colors,
    tl.ACCOUNT_UPDATE_COLOR: handle_account_update_color,
    tl.ACCOUNT_GET_DEFAULT_BACKGROUND_EMOJIS: (
        handle_account_get_default_background_emojis),
    tl.ACCOUNT_GET_GLOBAL_PRIVACY_SETTINGS: (
        handle_account_get_global_privacy_settings),
    tl.ACCOUNT_GET_DEFAULT_PROFILE_PHOTO_EMOJIS: (
        handle_account_get_default_profile_photo_emojis),
    tl.ACCOUNT_GET_SAVED_RINGTONES: handle_account_get_saved_ringtones,
    0x12b3ad31: handle_account_get_notify_settings,
    tl.ACCOUNT_UPDATE_PROFILE: handle_account_update_profile,
    tl.ACCOUNT_CHECK_USERNAME: handle_account_check_username,
    tl.ACCOUNT_UPDATE_USERNAME: handle_account_update_username,
    tl.ACCOUNT_UPDATE_BUSINESS_INTRO: handle_account_update_business_intro,
    tl.ACCOUNT_UPDATE_BUSINESS_GREETING_MESSAGE: (
        handle_account_update_business_greeting_message),
    tl.ACCOUNT_UPDATE_BUSINESS_WORK_HOURS: (
        handle_account_update_business_work_hours),
    tl.ACCOUNT_UPDATE_BUSINESS_AWAY_MESSAGE: (
        handle_account_update_business_away_message),
    tl.ACCOUNT_UPDATE_BUSINESS_LOCATION: (
        handle_account_update_business_location),
    tl.ACCOUNT_REORDER_USERNAMES: handle_account_reorder_usernames,
    tl.ACCOUNT_TOGGLE_USERNAME: handle_account_toggle_username,
    tl.FRAGMENT_GET_COLLECTIBLE_INFO: handle_fragment_get_collectible_info,
    tl.USERS_GET_FULL_USER: handle_users_get_full_user,
    tl.USERS_GET_USERS: handle_users_get_users,
    tl.CONTACTS_SEARCH: handle_contacts_search,
    tl.CONTACTS_SEARCH_V228: handle_contacts_search_v228,
    tl.CONTACTS_GET_BIRTHDAYS: handle_contacts_get_birthdays,
    tl.CONTACTS_GET_SPONSORED_PEERS: handle_contacts_get_sponsored_peers,
    tl.CONTACTS_GET_CONTACTS: handle_contacts_get_contacts,
    tl.CONTACTS_IMPORT_CONTACTS: handle_contacts_import_contacts,
    tl.CONTACTS_GET_TOP_PEERS: handle_contacts_get_top_peers,
    tl.CONTACTS_RESOLVE_USERNAME: handle_contacts_resolve_username,
    tl.CONTACTS_RESOLVE_PHONE: handle_contacts_resolve_phone,
    tl.UPDATES_GET_STATE: handle_updates_get_state,
    tl.UPDATES_GET_DIFFERENCE: handle_updates_get_difference,
    tl.UPDATES_GET_DIFFERENCE_LEGACY: handle_updates_get_difference_legacy,
    tl.MESSAGES_GET_DIALOGS: handle_messages_get_dialogs,
    tl.MESSAGES_GET_RECENT_REACTIONS: handle_messages_get_recent_reactions,
    tl.MESSAGES_GET_TOP_REACTIONS: handle_messages_get_top_reactions,
    0xbdf93428: handle_messages_get_default_tag_reactions,
    tl.MESSAGES_GET_ARCHIVED_STICKERS: handle_messages_get_archived_stickers,
    tl.MESSAGES_GET_SAVED_GIFS: handle_messages_get_saved_gifs,
    tl.MESSAGES_GET_FAVED_STICKERS: handle_messages_get_faved_stickers,
    tl.MESSAGES_GET_MESSAGES: handle_messages_get_messages,
    tl.MESSAGES_DELETE_HISTORY: handle_messages_delete_history,
    tl.MESSAGES_GET_PINNED_DIALOGS: handle_messages_get_pinned_dialogs,
    tl.MESSAGES_GET_HISTORY: handle_messages_get_history,
    tl.MESSAGES_GET_SAVED_DIALOGS: handle_messages_get_saved_dialogs,
    tl.LANGPACK_GET_STRINGS: handle_langpack_get_strings,
    tl.LANGPACK_GET_STRINGS_V2: handle_langpack_get_strings,
    tl.LANGPACK_GET_LANGUAGES: handle_langpack_get_languages,
    tl.LANGPACK_GET_LANGUAGE: handle_langpack_get_language,
    tl.LANGPACK_GET_DIFFERENCE: handle_langpack_get_difference,
    tl.LANGPACK_GET_LANG_PACK: handle_langpack_get_lang_pack,
    tl.LANGPACK_GET_LANG_PACK_LEGACY: handle_langpack_get_lang_pack_legacy,
    tl.LANGPACK_GET_DIFFERENCE_LEGACY: handle_langpack_get_difference_legacy,
    0x8107455c: handle_messages_toggle_suggested_post_approval,
    0x6f6f9c96: handle_messages_get_saved_dialogs_by_id,
    0x998ab009: handle_messages_get_saved_history,
    0xba4a3b5b: handle_messages_read_saved_history,
    0x4067c5e6: handle_messages_report_read_metrics,
    0x8bba90e6: handle_messages_get_messages_reactions,
    0x35a0e062: handle_messages_get_emoji_keywords,
    0x4e9963b2: handle_messages_get_emoji_keywords_languages,
    0xdea20a39: handle_messages_get_available_effects,
    tl.MESSAGES_GET_SCHEDULED_HISTORY: handle_messages_get_scheduled_history,
    tl.MESSAGES_GET_QUICK_REPLIES: handle_messages_get_quick_replies,
    tl.MESSAGES_GET_QUICK_REPLY_MESSAGES: handle_messages_get_quick_reply_messages,
    tl.MESSAGES_CHECK_QUICK_REPLY_SHORTCUT: handle_messages_check_quick_reply_shortcut,
    tl.MESSAGES_DELETE_QUICK_REPLY_SHORTCUT: handle_messages_delete_quick_reply_shortcut,
    tl.MESSAGES_SEND_MESSAGE: handle_messages_send_message,
    tl.MESSAGES_SEND_MESSAGE_214: handle_messages_send_message,
    tl.MESSAGES_SEND_MESSAGE_V227: handle_messages_send_message,
    tl.MESSAGES_SEND_MEDIA: handle_messages_send_media,
    tl.MESSAGES_SEND_MULTI_MEDIA: handle_messages_send_multi_media,
    MESSAGES_FORWARD_MESSAGES: handle_messages_forward_messages,
    tl.MESSAGES_START_BOT: handle_messages_start_bot,
    tl.MESSAGES_GET_PEER_DIALOGS: handle_messages_get_peer_dialogs,
    tl.MESSAGES_READ_HISTORY: handle_messages_read_history,
    tl.MESSAGES_GET_PEER_SETTINGS: handle_messages_get_peer_settings,
    tl.MESSAGES_HIDE_PEER_SETTINGS_BAR: handle_messages_hide_peer_settings_bar,
    tl.MESSAGES_GET_ONLINES: handle_messages_get_onlines,
    tl.MESSAGES_GET_OUTBOX_READ_DATE: handle_messages_get_outbox_read_date,
    tl.MESSAGES_GET_SEARCH_RESULTS_POSITIONS: (
        handle_messages_get_search_results_positions),
    tl.PREMIUM_GET_BOOSTS_STATUS: handle_premium_get_boosts_status,
    tl.BOTS_CAN_SEND_MESSAGE: handle_bots_can_send_message,
    tl.ACCOUNT_UPDATE_STATUS: handle_account_update_status,
    tl.MESSAGES_GET_DIALOG_FILTERS: handle_messages_get_dialog_filters,
    0x658b7188: handle_messages_get_default_history_ttl,
    0x08fc711d: handle_account_get_account_ttl,
    tl.ACCOUNT_REGISTER_DEVICE: handle_account_register_device,
    tl.LANG_PACK_GET_LANGUAGES: handle_lang_pack_get_languages,
    tl.LANG_PACK_GET_STRINGS: handle_langpack_get_strings,
}

from handlers.stickers import STICKER_HANDLERS  # noqa: E402
from handlers.media import MEDIA_HANDLERS  # noqa: E402
from handlers.star_gifts import STAR_GIFT_HANDLERS  # noqa: E402
from handlers.groups import GROUP_HANDLERS  # noqa: E402
from handlers.stubs import STUB_HANDLERS  # noqa: E402
from handlers.stories import STORY_HANDLERS  # noqa: E402
from handlers.password import PASSWORD_HANDLERS  # noqa: E402
from handlers.profile_music import PROFILE_MUSIC_HANDLERS  # noqa: E402
from handlers.personal_channel import PERSONAL_CHANNEL_HANDLERS  # noqa: E402
from handlers.paid_messages import PAID_MESSAGE_HANDLERS  # noqa: E402
from handlers.polls import POLL_HANDLERS  # noqa: E402
from handlers.privacy import PRIVACY_HANDLERS  # noqa: E402
from handlers.contacts import CONTACTS_HANDLERS  # noqa: E402
from handlers.calls import CALL_HANDLERS  # noqa: E402
HANDLERS.update(STICKER_HANDLERS)
HANDLERS.update(MEDIA_HANDLERS)
HANDLERS.update(STAR_GIFT_HANDLERS)
HANDLERS.update(GROUP_HANDLERS)
HANDLERS.update(STUB_HANDLERS)
HANDLERS.update(STORY_HANDLERS)
HANDLERS.update(PASSWORD_HANDLERS)
HANDLERS.update(PROFILE_MUSIC_HANDLERS)
HANDLERS.update(PERSONAL_CHANNEL_HANDLERS)
HANDLERS.update(PAID_MESSAGE_HANDLERS)
HANDLERS.update(POLL_HANDLERS)
HANDLERS.update(PRIVACY_HANDLERS)
HANDLERS.update(CONTACTS_HANDLERS)
HANDLERS.update(CALL_HANDLERS)

# Langpack handlers must win over generic stubs (layer 224 uses legacy getLangPack).
HANDLERS.update({
    tl.LANGPACK_GET_STRINGS: handle_langpack_get_strings,
    tl.LANGPACK_GET_STRINGS_V2: handle_langpack_get_strings,
    tl.LANGPACK_GET_LANGUAGES: handle_langpack_get_languages,
    tl.LANGPACK_GET_LANGUAGE: handle_langpack_get_language,
    tl.LANGPACK_GET_DIFFERENCE: handle_langpack_get_difference,
    tl.LANGPACK_GET_LANG_PACK: handle_langpack_get_lang_pack,
    tl.LANGPACK_GET_LANG_PACK_LEGACY: handle_langpack_get_lang_pack_legacy,
    tl.LANGPACK_GET_DIFFERENCE_LEGACY: handle_langpack_get_difference_legacy,
    tl.LANG_PACK_GET_LANGUAGES: handle_lang_pack_get_languages,
    tl.LANG_PACK_GET_STRINGS: handle_langpack_get_strings,
    tl.ACCOUNT_SEND_CHANGE_PHONE_CODE: handle_account_send_change_phone_code,
    tl.ACCOUNT_CHANGE_PHONE: handle_account_change_phone,
})


def dispatch(session, req_msg_id: int, body: bytes, storage: Storage,
             push_queue: Optional[PushQueue] = None,
             registry: Optional[ConnectionRegistry] = None) -> Optional[bytes]:
    if registry is not None and hasattr(session, 'auth_key_id'):
        user_id = storage.get_session_user(session.auth_key_id)
        if user_id:
            registry.bind_user(session.auth_key_id, user_id)

    body = tl.maybe_ungzip(body)
    r = tl.Reader(body)
    cid = tl.unwrap_rpc_query(r, session)

    log.info('RPC %#010x auth=%#018x req_msg_id=%s %s',
             cid, getattr(session, 'auth_key_id', 0), req_msg_id,
             format_client_log(session))

    handler = HANDLERS.get(cid)
    if handler is None:
        if cid not in _warned_methods:
            _warned_methods.add(cid)
            remaining = body[r.pos:r.pos + 32].hex()
            log.warning(
                'Unhandled RPC %#010x auth=%#018x remaining=%s next=%s %s',
                cid, getattr(session, 'auth_key_id', 0), r.remaining(), remaining,
                format_client_log(session))
        return rpc_error(400, 'METHOD_NOT_IMPLEMENTED')

    try:
        return handler(session, req_msg_id, r, storage, push_queue, registry)
    except Exception as exc:
        log.exception('RPC handler %#010x failed auth=%#018x %s: %s',
                      cid, getattr(session, 'auth_key_id', 0),
                      format_client_log(session), exc)
        return rpc_error(500, 'INTERNAL_SERVER_ERROR')
    finally:
        # A handler that wrote and then raised (or returned early) can leave an
        # implicit SQLite transaction open. That holds the WAL write lock for
        # the lifetime of the process, so external tools (gift imports, bot
        # verification grants) get "database is locked" forever.
        try:
            if storage._conn.in_transaction:
                log.warning(
                    'RPC %#010x left an open DB transaction; rolling back', cid)
                storage._conn.rollback()
        except Exception:
            log.exception('Failed to roll back DB transaction for %#010x', cid)
