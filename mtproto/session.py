"""
Encrypted MTProto session.
Handles message encryption/decryption, message IDs, sequence numbers.
"""
import os
import struct
import time
import logging
from pathlib import Path
from typing import Optional, Tuple

from . import tl, crypto

log = logging.getLogger(__name__)

_SALT_FILE = Path(__file__).resolve().parent.parent / '.server_salt'


def _load_server_salt() -> int:
    try:
        if _SALT_FILE.exists():
            data = _SALT_FILE.read_bytes()
            if len(data) >= 8:
                return struct.unpack('<q', data[:8])[0]
    except Exception as exc:
        log.warning('Failed to read server salt file: %s', exc)
    salt = struct.unpack('<q', os.urandom(8))[0]
    try:
        _SALT_FILE.write_bytes(struct.pack('<q', salt))
    except Exception as exc:
        log.warning('Failed to persist server salt: %s', exc)
    return salt


# Persist across restarts so clients are not stuck in bad_server_salt loops.
_SERVER_SALT = _load_server_salt()


_msg_id_seq = 0


def make_message_id() -> int:
    """Server→client unencrypted msg_id (client requires low32 & 3 == 1)."""
    global _msg_id_seq
    t = time.time()
    sec = int(t)
    _msg_id_seq = (_msg_id_seq + 4) & 0xFFFFFFFF
    lo = (_msg_id_seq + 1) & 0xFFFFFFFF
    return (sec << 32) | lo


class Session:
    def __init__(self, auth_key: bytes, session_id: int):
        self.auth_key    = auth_key
        self.auth_key_id = crypto.auth_key_id(auth_key)
        self.session_id  = session_id
        self.salt        = _SERVER_SALT
        self._seq        = 0  # server outgoing seq_no (always even for server)
        self._client_seq: Optional[int] = None
        self.pending_new_session = True
        # Permanent auth key linked via auth.bindTempAuthKey (TD uses perm for RPC).
        self.perm_auth_key_id: Optional[int] = None
        # initConnection metadata (filled from first wrapped RPC).
        self.init_api_id: int = 0
        self.init_device_model: str = ''
        self.init_system_version: str = ''
        self.init_app_version: str = ''
        self.init_system_lang_code: str = ''
        self.init_lang_pack: str = ''
        self.init_lang_code: str = ''
        self.init_connection_flags: int = 0
        self.connection_peer: str = ''
        self.user_id: int = 0
        # Reconnected sessions never repeat initConnection; restore the
        # client's TL layer from the per-auth-key cache.
        try:
            from .client_info import inherit_client_info
            inherit_client_info(self)
        except Exception:
            pass

    def _next_seq(self, content_related: bool) -> int:
        if content_related:
            s = self._seq * 2 + 1
            self._seq += 1
        else:
            s = self._seq * 2
        return s

    # ──────────────────────────────────────────
    # Decrypt incoming message
    # ──────────────────────────────────────────

    def decrypt(self, data: bytes) -> Tuple[int, int, int, bytes]:
        """
        Decrypt an incoming encrypted MTProto message.
        Returns (msg_id, seq_no, body_data).
        Input `data` starts AFTER the 8-byte auth_key_id.
        """
        msg_key   = data[:16]
        encrypted = data[16:]

        aes_key, aes_iv = crypto.derive_message_key(self.auth_key, msg_key, from_client=True)
        plaintext = crypto.aes_ige_decrypt(encrypted, aes_key, aes_iv)

        r = tl.Reader(plaintext)
        salt       = r.i64()
        session_id = r.i64()
        msg_id     = r.i64()
        seq_no     = r.i32()
        msg_len    = r.i32()
        body       = r.read(msg_len)

        # Verify msg_key
        expected_key = crypto.compute_msg_key(self.auth_key, plaintext, from_client=True)
        if expected_key != msg_key:
            log.warning('msg_key mismatch (ignoring for development)')

        return msg_id, seq_no, body

    # ──────────────────────────────────────────
    # Encrypt outgoing message
    # ──────────────────────────────────────────

    def encrypt(self, body: bytes, content_related: bool = True,
                salt: Optional[int] = None) -> bytes:
        """
        Encrypt a single MTProto message body and return the full packet
        (including auth_key_id + msg_key + ciphertext).
        """
        msg_id  = make_message_id()
        seq_no  = self._next_seq(content_related)
        use_salt = self.salt if salt is None else int(salt)

        try:
            out_cid = struct.unpack_from('<I', body, 0)[0]
            out_req = None
            if out_cid == tl.RPC_RESULT and len(body) >= 12:
                out_req = struct.unpack_from('<q', body, 4)[0]
        except struct.error:
            out_cid, out_req = -1, None
        log.info('SEND auth=%#018x msg_id=%#018x seq=%d salt=%#x '
                 'session=%#x len=%d cid=%#010x req_msg_id=%s content=%s',
                 self.auth_key_id, msg_id, seq_no, use_salt & 0xFFFFFFFFFFFFFFFF,
                 self.session_id & 0xFFFFFFFFFFFFFFFF, len(body),
                 out_cid & 0xFFFFFFFF, out_req, content_related)

        inner = tl.Writer()
        inner.i64(use_salt)
        inner.i64(self.session_id)
        inner.i64(msg_id)
        inner.i32(seq_no)
        inner.i32(len(body))
        inner.raw(body)

        # Padding: at least 12 bytes, total must be divisible by 16
        plaintext = inner.bytes_out()
        pad_len = 12 + (16 - (len(plaintext) + 12) % 16) % 16
        plaintext += os.urandom(pad_len)

        msg_key  = crypto.compute_msg_key(self.auth_key, plaintext, from_client=False)
        aes_key, aes_iv = crypto.derive_message_key(self.auth_key, msg_key, from_client=False)
        ciphertext = crypto.aes_ige_encrypt(plaintext, aes_key, aes_iv)

        out = tl.Writer()
        out.i64(self.auth_key_id)
        out.i128(msg_key)
        out.raw(ciphertext)
        return out.bytes_out()

    def encrypt_rpc_result(self, req_msg_id: int, result_body: bytes, *,
                           salt: Optional[int] = None) -> bytes:
        """Wrap result_body in rpc_result and encrypt."""
        result_body = tl.maybe_gzip(result_body)
        w = tl.Writer()
        w.u32(tl.RPC_RESULT)
        w.i64(req_msg_id)
        w.raw(result_body)
        return self.encrypt(w.bytes_out(), salt=salt)

    def encrypt_bad_server_salt(self, bad_msg_id: int, bad_seq_no: int,
                                new_salt: int, *,
                                reply_salt: Optional[int] = None) -> bytes:
        w = tl.Writer()
        w.u32(tl.BAD_SERVER_SALT)
        w.i64(bad_msg_id)
        w.i32(bad_seq_no)
        w.i32(48)  # BAD_SERVER_SALT error code
        w.i64(new_salt)
        return self.encrypt(w.bytes_out(), content_related=False,
                            salt=reply_salt)

    def encrypt_pong(self, msg_id: int, ping_id: int, *,
                     salt: Optional[int] = None) -> bytes:
        w = tl.Writer()
        w.u32(tl.PONG)
        w.i64(msg_id)
        w.i64(ping_id)
        return self.encrypt(w.bytes_out(), content_related=False, salt=salt)

    def encrypt_new_session(self, first_msg_id: int, *,
                            reply_salt: Optional[int] = None) -> bytes:
        w = tl.Writer()
        w.u32(tl.NEW_SESSION_CREATED)
        w.i64(first_msg_id)
        w.i64(0)           # unique_id
        w.i64(self.salt)
        return self.encrypt(w.bytes_out(), content_related=False,
                            salt=reply_salt)

    def encrypt_future_salts(self, req_msg_id: int, *,
                             salt: Optional[int] = None) -> bytes:
        now = int(time.time())
        w = tl.Writer()
        w.u32(tl.FUTURE_SALTS)
        w.i64(req_msg_id)
        w.i32(now)
        # 1 salt
        w.i32(1)   # vector length without VECTOR header (bare)
        w.i32(now)
        w.i32(now + 3600)
        w.i64(self.salt)
        return self.encrypt(w.bytes_out(), content_related=False, salt=salt)

    def encrypt_rpc_error(self, req_msg_id: int, code: int, message: str) -> bytes:
        w = tl.Writer()
        w.u32(tl.RPC_RESULT)
        w.i64(req_msg_id)
        # rpc_error
        w.u32(tl.RPC_ERROR)
        w.i32(code)
        w.string(message)
        return self.encrypt(w.bytes_out())

    def encrypt_msgs_ack(self, msg_ids: list) -> bytes:
        w = tl.Writer()
        w.u32(tl.MSGS_ACK)
        w.vector_long(msg_ids)
        return self.encrypt(w.bytes_out(), content_related=False)
