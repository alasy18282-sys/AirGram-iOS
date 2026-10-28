"""Cloud password (2FA) RPC handlers — no email recovery."""
from __future__ import annotations

import logging
import os

from mtproto import tl
from mtproto.srp import (
    PASSWORD_KDF_ALGO,
    make_default_algo,
    verify_check_password,
)
from handlers import packers
from mtproto.client_info import format_client_log

log = logging.getLogger(__name__)

ACCOUNT_PASSWORD = 0x957b50fb
ACCOUNT_PASSWORD_SETTINGS = 0x9a5c33e5
ACCOUNT_SENT_EMAIL_CODE = 0x811f854f
ACCOUNT_EMAIL_VERIFIED = 0x2b96cd1b
SECURE_PASSWORD_KDF_ALGO_PBKDF2 = 0xbbf2dda0
SECURE_SECRET_SETTINGS = 0x1527bcac
INPUT_CHECK_PASSWORD_SRP = 0xd27ff082
INPUT_CHECK_PASSWORD_EMPTY = 0x9880f658
PASSWORD_INPUT_SETTINGS = 0xc23727c9

AUTH_CHECK_PASSWORD = 0xd18b4d16
ACCOUNT_UPDATE_PASSWORD_SETTINGS = 0xa59b102f
ACCOUNT_GET_PASSWORD_SETTINGS = 0x9cd4eaf9
ACCOUNT_SEND_VERIFY_EMAIL_CODE = 0x98e037bb
ACCOUNT_VERIFY_EMAIL = 0x032da4cf
ACCOUNT_CONFIRM_PASSWORD_EMAIL = 0x8fdf1920
ACCOUNT_CANCEL_PASSWORD_EMAIL = 0xc1cbd5b6
ACCOUNT_RESEND_PASSWORD_EMAIL = 0x7a7f2a15
AUTH_RESET_LOGIN_EMAIL = 0x7e960193


def _resolve_password_user_id(session, storage) -> int | None:
    user_id = storage.get_session_user(session.auth_key_id)
    if user_id:
        return user_id
    pending = storage.get_pending_sign_in(session.auth_key_id)
    if pending:
        return int(pending['user_id'])
    return None


def _pack_password_kdf_algo(algo: dict) -> bytes:
    w = tl.Writer()
    w.u32(PASSWORD_KDF_ALGO)
    w.tl_bytes(algo['salt1'])
    w.tl_bytes(algo['salt2'])
    w.i32(algo['g'])
    w.tl_bytes(algo['p'])
    return w.bytes_out()


def _pack_secure_kdf_algo() -> bytes:
    w = tl.Writer()
    w.u32(SECURE_PASSWORD_KDF_ALGO_PBKDF2)
    w.tl_bytes(os.urandom(32))
    return w.bytes_out()


def _normalize_password_hash(raw: bytes) -> bytes:
    if not raw:
        return b''
    if len(raw) >= 256:
        return raw[-256:]
    return raw.rjust(256, b'\x00')


def pack_account_password(user_id: int, storage, *, auth_key_id: int) -> bytes:
    pw = storage.get_user_password(user_id) if user_id else None
    default_algo = make_default_algo()
    if pw:
        default_algo = {
            'salt1': bytes(pw['salt1']),
            'salt2': bytes(pw['salt2']),
            'g': int(pw['g']),
            'p': bytes(pw['p']),
        }

    w = tl.Writer()
    w.u32(ACCOUNT_PASSWORD)
    flags = 0
    if pw:
        flags |= 1 << 2  # has_password
    if pw and pw.get('hint'):
        flags |= 1 << 3
    w.i32(flags)

    if pw:
        session = storage.create_pending_srp(
            user_id=user_id,
            auth_key_id=auth_key_id,
            salt1=bytes(pw['salt1']),
            salt2=bytes(pw['salt2']),
            g=int(pw['g']),
            p=bytes(pw['p']),
            v=bytes(pw['v']),
        )
        w.raw(_pack_password_kdf_algo(default_algo))
        w.tl_bytes(session.srp_b)
        w.i64(session.srp_id)
        if flags & (1 << 3):
            w.string(str(pw['hint']))

    w.raw(_pack_password_kdf_algo(default_algo))
    w.raw(_pack_secure_kdf_algo())
    w.tl_bytes(os.urandom(256))
    return w.bytes_out()


def _skip_secure_kdf_algo(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == SECURE_PASSWORD_KDF_ALGO_PBKDF2 or cid == 0x86471d92:
        r.tl_bytes()
    elif cid == 0x4a8537:  # securePasswordKdfAlgoUnknown
        pass


def _skip_secure_secret_settings(r: tl.Reader) -> None:
    cid = r.u32()
    if cid != SECURE_SECRET_SETTINGS:
        raise ValueError(f'unsupported SecureSecretSettings {cid:#010x}')
    _skip_secure_kdf_algo(r)
    r.tl_bytes()
    r.i64()


def _parse_input_check_password(r: tl.Reader) -> tuple[str, dict | None]:
    cid = r.u32()
    if cid == INPUT_CHECK_PASSWORD_EMPTY:
        return 'empty', None
    if cid == INPUT_CHECK_PASSWORD_SRP:
        srp_id = r.i64()
        a_bytes = r.tl_bytes()
        m1 = r.tl_bytes()
        return 'srp', {'srp_id': srp_id, 'a': a_bytes, 'm1': m1}
    raise ValueError(f'unknown InputCheckPasswordSRP {cid:#010x}')


def _parse_password_kdf_algo(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid != PASSWORD_KDF_ALGO:
        raise ValueError(f'unsupported PasswordKdfAlgo {cid:#010x}')
    salt1 = r.tl_bytes()
    salt2 = r.tl_bytes()
    g = r.i32()
    p = r.tl_bytes()
    return {'salt1': salt1, 'salt2': salt2, 'g': g, 'p': p}


def _parse_password_input_settings(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid != PASSWORD_INPUT_SETTINGS:
        raise ValueError(f'unsupported PasswordInputSettings {cid:#010x}')
    flags = r.i32()
    out: dict = {'flags': flags}
    if flags & 1:
        out['new_algo'] = _parse_password_kdf_algo(r)
        out['new_password_hash'] = r.tl_bytes()
        out['hint'] = r.string()
    if flags & 2:
        out['email'] = r.string()  # ignored — no email recovery
    if flags & 4:
        _skip_secure_secret_settings(r)
    return out


def _verify_password_srp(storage, user_id: int, check: dict) -> bool:
    session = storage.get_pending_srp(int(check['srp_id']))
    if not session:
        return False
    pw = storage.get_user_password(user_id)
    if not pw:
        return False
    ok = verify_check_password(
        a_bytes=check['a'], m1=check['m1'], session=session)
    if ok:
        storage.delete_pending_srp(int(check['srp_id']))
    return ok


def _bool_true() -> bytes:
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def _mask_email(email: str) -> str:
    email = (email or '').strip()
    if '@' not in email:
        return '***@***'
    local, domain = email.split('@', 1)
    if len(local) <= 1:
        masked = '*'
    else:
        masked = local[0] + '***'
    return f'{masked}@{domain}'


def handle_account_get_password(session, req_msg_id, r, storage, push_queue, registry):
    user_id = _resolve_password_user_id(session, storage)
    if not user_id:
        return pack_account_password(0, storage, auth_key_id=session.auth_key_id)
    return pack_account_password(user_id, storage, auth_key_id=session.auth_key_id)


def handle_auth_check_password(session, req_msg_id, r, storage, push_queue, registry):
    from handlers.rpc import _bind_user_session, rpc_error
    try:
        kind, check = _parse_input_check_password(r)
    except ValueError:
        return rpc_error(400, 'PASSWORD_HASH_INVALID')

    if kind != 'srp' or not check:
        return rpc_error(400, 'PASSWORD_HASH_INVALID')

    pending = storage.get_pending_sign_in(session.auth_key_id)
    if not pending:
        return rpc_error(400, 'SRP_ID_INVALID')

    user_id = int(pending['user_id'])
    if not _verify_password_srp(storage, user_id, check):
        return rpc_error(400, 'PASSWORD_HASH_INVALID')

    phone = pending['phone']
    code_hash = pending['code_hash']
    if not storage.consume_phone_code(phone, code_hash):
        return rpc_error(400, 'PHONE_CODE_EXPIRED')

    storage.clear_pending_sign_in(session.auth_key_id)
    user = storage.get_user_by_id(user_id)
    if not user:
        return rpc_error(400, 'USER_ID_INVALID')

    storage.deliver_pending_login_message(phone, user_id)
    _bind_user_session(session, storage, user_id, registry, push_queue)
    log.info('User signed in with 2FA: id=%d phone=+%s %s',
             user_id, phone, format_client_log(session))
    return packers.pack_auth_authorization(user, storage=storage)


def handle_account_update_password_settings(session, req_msg_id, r, storage,
                                            push_queue, registry):
    from handlers.rpc import require_auth, rpc_error
    user_id = _resolve_password_user_id(session, storage)
    if not user_id:
        user, err = require_auth(session, storage)
        if user is None:
            return err
        user_id = user['id']

    try:
        kind, check = _parse_input_check_password(r)
        settings = _parse_password_input_settings(r)
    except ValueError as exc:
        log.warning('updatePasswordSettings parse error for user %d: %s',
                    user_id, exc)
        return rpc_error(400, 'NEW_SETTINGS_INVALID')

    has_password = storage.user_has_password(user_id)
    if has_password:
        if kind != 'srp' or not check:
            return rpc_error(400, 'PASSWORD_HASH_INVALID')
        if not _verify_password_srp(storage, user_id, check):
            return rpc_error(400, 'PASSWORD_HASH_INVALID')
    elif kind != 'empty':
        return rpc_error(400, 'PASSWORD_HASH_INVALID')

    flags = settings.get('flags', 0)
    if not (flags & 1):
        return rpc_error(400, 'NEW_SETTINGS_EMPTY')

    new_hash = _normalize_password_hash(settings.get('new_password_hash', b''))
    if not new_hash:
        storage.delete_user_password(user_id)
        log.info('Cloud password removed for user %d', user_id)
        return _bool_true()

    algo = settings['new_algo']
    storage.set_user_password(
        user_id,
        salt1=bytes(algo['salt1']),
        salt2=bytes(algo['salt2']),
        g=int(algo['g']),
        p=bytes(algo['p']),
        v=new_hash,
        hint=str(settings.get('hint') or ''),
    )
    log.info('Cloud password set for user %d', user_id)
    return _bool_true()


def handle_account_get_password_settings(session, req_msg_id, r, storage,
                                         push_queue, registry):
    from handlers.rpc import require_auth, rpc_error
    user, err = require_auth(session, storage)
    if user is None:
        return err

    try:
        kind, check = _parse_input_check_password(r)
    except ValueError:
        return rpc_error(400, 'PASSWORD_HASH_INVALID')

    if kind != 'srp' or not check:
        return rpc_error(400, 'PASSWORD_HASH_INVALID')
    if not _verify_password_srp(storage, user['id'], check):
        return rpc_error(400, 'PASSWORD_HASH_INVALID')

    w = tl.Writer()
    w.u32(ACCOUNT_PASSWORD_SETTINGS)
    w.i32(0)  # no email, no secure settings
    return w.bytes_out()


def _skip_email_verify_purpose(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0x4345be73:  # emailVerifyPurposeLoginSetup
        r.string()
        r.string()
    elif cid == 0x527d22eb:  # emailVerifyPurposeLoginChange
        pass
    elif cid == 0xbb2740a2:  # emailVerifyPurposePassport
        r.string()


def handle_account_send_verify_email_code(session, req_msg_id, r, storage,
                                          push_queue, registry):
    _skip_email_verify_purpose(r)
    email = r.string()
    w = tl.Writer()
    w.u32(ACCOUNT_SENT_EMAIL_CODE)
    w.string(_mask_email(email))
    w.i32(6)
    return w.bytes_out()


def handle_account_verify_email(session, req_msg_id, r, storage, push_queue, registry):
    _skip_email_verify_purpose(r)
    cid = r.u32()
    if cid == 0x922e55a9:
        r.string()
    elif cid in (0xdb909ec2, 0x96d074fd):
        r.string()
    w = tl.Writer()
    w.u32(ACCOUNT_EMAIL_VERIFIED)
    w.string('')
    return w.bytes_out()


def handle_account_confirm_password_email(session, req_msg_id, r, storage,
                                          push_queue, registry):
    r.string()
    return _bool_true()


def handle_account_cancel_password_email(session, req_msg_id, r, storage,
                                         push_queue, registry):
    return _bool_true()


def handle_account_resend_password_email(session, req_msg_id, r, storage,
                                         push_queue, registry):
    return _bool_true()


def handle_auth_reset_login_email(session, req_msg_id, r, storage, push_queue, registry):
    r.string()
    r.string()
    w = tl.Writer()
    w.u32(tl.AUTH_SENT_CODE)
    w.i32((1 << 2) | (1 << 1))
    w.u32(tl.AUTH_SENT_CODE_TYPE_APP)
    w.i32(5)
    w.string('')
    w.u32(tl.AUTH_CODE_TYPE_SMS)
    w.i32(300)
    return w.bytes_out()


PASSWORD_HANDLERS = {
    0x548a30f5: handle_account_get_password,
    AUTH_CHECK_PASSWORD: handle_auth_check_password,
    ACCOUNT_UPDATE_PASSWORD_SETTINGS: handle_account_update_password_settings,
    ACCOUNT_GET_PASSWORD_SETTINGS: handle_account_get_password_settings,
    ACCOUNT_SEND_VERIFY_EMAIL_CODE: handle_account_send_verify_email_code,
    ACCOUNT_VERIFY_EMAIL: handle_account_verify_email,
    ACCOUNT_CONFIRM_PASSWORD_EMAIL: handle_account_confirm_password_email,
    ACCOUNT_CANCEL_PASSWORD_EMAIL: handle_account_cancel_password_email,
    ACCOUNT_RESEND_PASSWORD_EMAIL: handle_account_resend_password_email,
    AUTH_RESET_LOGIN_EMAIL: handle_auth_reset_login_email,
}
