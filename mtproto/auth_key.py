"""
Auth-key generation state machine (MTProto 2.0).

Flow:
  Client → req_pq_multi
  Server → resPQ
  Client → req_DH_params   (RSA-encrypted p_q_inner_data_dc)
  Server → server_DH_params_ok  (AES-encrypted server_DH_inner_data)
  Client → set_client_DH_params (AES-encrypted client_DH_inner_data)
  Server → dh_gen_ok
"""
import os
import struct
import time
import logging
from typing import Optional

from . import tl, crypto
from config import DH_PRIME, DH_PRIME_BYTES, DH_G

log = logging.getLogger(__name__)


class HandshakeError(Exception):
    """Client sent invalid or out-of-order handshake data."""


def _make_server_nonce() -> bytes:
    return os.urandom(16)


def _aes_key_iv_from_nonces(server_nonce: bytes, new_nonce: bytes):
    """Derive temp AES key and IV from nonces for server_DH_params exchange."""
    h1 = crypto.sha1(new_nonce + server_nonce)
    h2 = crypto.sha1(server_nonce + new_nonce)
    h3 = crypto.sha1(new_nonce + new_nonce)
    key = h1 + h2[:12]
    iv  = h2[12:] + h3 + new_nonce[:4]
    return key, iv


def _new_nonce_hash_fail(new_nonce: bytes) -> bytes:
    """new_nonce_hash for server_DH_params_fail (bytes 4..20 of SHA1)."""
    return crypto.sha1(new_nonce)[4:20]


def _new_nonce_hash3(new_nonce: bytes, auth_key_aux: bytes) -> bytes:
    return crypto.sha1(new_nonce + b'\x03' + auth_key_aux)[-16:]


# iOS opens parallel TCP connections and continues handshake with nonces from
# another connection's resPQ. Cache DH state keyed by (nonce, server_nonce).
_HANDSHAKE_CACHE: dict[tuple[bytes, bytes], dict] = {}
_HANDSHAKE_CACHE_MAX = 256


def _handshake_cache_put(nonce: bytes, server_nonce: bytes, **data) -> None:
    key = (nonce, server_nonce)
    _HANDSHAKE_CACHE[key] = data
    while len(_HANDSHAKE_CACHE) > _HANDSHAKE_CACHE_MAX:
        _HANDSHAKE_CACHE.pop(next(iter(_HANDSHAKE_CACHE)))


def _handshake_cache_get(nonce: bytes, server_nonce: bytes) -> Optional[dict]:
    return _HANDSHAKE_CACHE.get((nonce, server_nonce))


def _handshake_cache_pop(nonce: bytes, server_nonce: bytes) -> None:
    _HANDSHAKE_CACHE.pop((nonce, server_nonce), None)


class AuthKeyGenerator:
    """Per-connection state machine for MTProto auth-key generation."""

    def __init__(self, private_key, fingerprint: int):
        self._priv = private_key
        self._fingerprint = fingerprint

        # State filled as handshake progresses
        self.nonce: bytes = b''
        self.server_nonce: bytes = b''
        self.new_nonce: bytes = b''
        self._pq: int = 0
        self._p: int = 0
        self._q: int = 0
        self._server_dh_priv: int = 0
        self.auth_key: bytes = b''
        self._stage: str = 'idle'

    def _reset(self) -> None:
        self.nonce = b''
        self.server_nonce = b''
        self.new_nonce = b''
        self._pq = 0
        self._p = 0
        self._q = 0
        self._server_dh_priv = 0
        self.auth_key = b''
        self._stage = 'idle'

    def _make_server_dh_params_fail(self, nonce: bytes, server_nonce: bytes,
                                    new_nonce: bytes) -> bytes:
        out = tl.Writer()
        out.u32(tl.SERVER_DH_PARAMS_FAIL)
        out.i128(nonce)
        out.i128(server_nonce)
        out.i128(_new_nonce_hash_fail(new_nonce))
        return out.bytes_out()

    # ──────────────────────────────────────────
    # Step 1: req_pq_multi → resPQ
    # ──────────────────────────────────────────

    def handle_req_pq(self, payload: bytes) -> bytes:
        r = tl.Reader(payload)
        cid = r.u32()
        if cid not in (tl.REQ_PQ, tl.REQ_PQ_MULTI):
            raise HandshakeError(f'unexpected handshake cid {cid:#010x}')

        if self._stage != 'idle':
            log.info('handshake restart on same connection (was %s)', self._stage)
            self._reset()

        self.nonce = r.i128()
        self.server_nonce = _make_server_nonce()
        pq, p, q = crypto.gen_pq()
        self._pq, self._p, self._q = pq, p, q
        self._stage = 'pq'

        log.debug('req_pq: nonce=%s, PQ=%d', self.nonce.hex(), pq)

        w = tl.Writer()
        w.u32(tl.RES_PQ)
        w.i128(self.nonce)
        w.i128(self.server_nonce)
        w.tl_bytes(tl.int_to_bytes(pq))
        w.u32(tl.VECTOR)
        w.i32(1)
        w.i64(self._fingerprint)
        return w.bytes_out()

    # ──────────────────────────────────────────
    # Step 2: req_DH_params → server_DH_params_ok
    # ──────────────────────────────────────────

    def handle_req_dh_params(self, payload: bytes) -> bytes:
        r = tl.Reader(payload)
        cid = r.u32()
        if cid != tl.REQ_DH_PARAMS:
            raise HandshakeError(f'expected req_DH_params, got {cid:#010x}')

        nonce        = r.i128()
        server_nonce = r.i128()
        _p_bytes     = r.tl_bytes()
        _q_bytes     = r.tl_bytes()
        fingerprint  = r.i64()
        encrypted    = r.tl_bytes()

        if self._stage != 'pq' or not self.nonce or (
                nonce != self.nonce or server_nonce != self.server_nonce):
            log.info(
                'req_DH_params adopting client nonces (stage=%s, parallel connection)',
                self._stage)
            self.nonce = nonce
            self.server_nonce = server_nonce
            self._stage = 'pq'

        if fingerprint != self._fingerprint:
            raise HandshakeError(
                f'fingerprint mismatch: got {fingerprint:#018x}, '
                f'want {self._fingerprint:#018x}')

        try:
            inner_data = crypto.decrypt_pq_inner_rsa(encrypted, self._priv)
        except Exception as exc:
            raise HandshakeError(f'RSA decrypt failed: {exc}') from exc

        r2 = tl.Reader(inner_data)
        cid2 = r2.u32()
        if cid2 not in (
            tl.P_Q_INNER_DATA,
            tl.P_Q_INNER_DATA_DC,
            tl.P_Q_INNER_DATA_TEMP,
            tl.P_Q_INNER_DATA_TEMP_DC,
        ):
            raise HandshakeError(f'bad pq inner constructor {cid2:#010x}')

        _pq_b       = r2.tl_bytes()
        _p_b        = r2.tl_bytes()
        _q_b        = r2.tl_bytes()
        inn_nonce   = r2.i128()
        inn_sn      = r2.i128()
        new_nonce   = r2.i256()
        if cid2 in (tl.P_Q_INNER_DATA_DC, tl.P_Q_INNER_DATA_TEMP_DC):
            r2.i32()
        if cid2 in (tl.P_Q_INNER_DATA_TEMP, tl.P_Q_INNER_DATA_TEMP_DC):
            r2.i32()

        if inn_nonce != self.nonce or inn_sn != self.server_nonce:
            log.warning('pq inner nonce mismatch, returning server_DH_params_fail')
            return self._make_server_dh_params_fail(nonce, server_nonce, new_nonce)

        self.new_nonce = new_nonce
        self._stage = 'dh'
        log.debug('req_DH_params: new_nonce=%s', self.new_nonce.hex())

        self._server_dh_priv, g_a = crypto.dh_gen_server_ga(DH_G, DH_PRIME)
        _handshake_cache_put(
            self.nonce, self.server_nonce,
            new_nonce=self.new_nonce,
            server_dh_priv=self._server_dh_priv,
        )
        g_a_bytes = tl.int_to_bytes(g_a)

        inner_w = tl.Writer()
        inner_w.u32(tl.SERVER_DH_INNER_DATA)
        inner_w.i128(self.nonce)
        inner_w.i128(self.server_nonce)
        inner_w.i32(DH_G)
        inner_w.tl_bytes(DH_PRIME_BYTES)
        inner_w.tl_bytes(g_a_bytes)
        inner_w.i32(int(time.time()))
        inner_raw2 = inner_w.bytes_out()

        key, iv = _aes_key_iv_from_nonces(self.server_nonce, self.new_nonce)
        data_with_hash = crypto.sha1(inner_raw2) + inner_raw2
        pad_len = (16 - len(data_with_hash) % 16) % 16
        data_with_hash += os.urandom(pad_len)
        encrypted_answer = crypto.aes_ige_encrypt(data_with_hash, key, iv)

        out = tl.Writer()
        out.u32(tl.SERVER_DH_PARAMS_OK)
        out.i128(self.nonce)
        out.i128(self.server_nonce)
        out.tl_bytes(encrypted_answer)
        return out.bytes_out()

    # ──────────────────────────────────────────
    # Step 3: set_client_DH_params → dh_gen_ok
    # ──────────────────────────────────────────

    def handle_set_client_dh(self, payload: bytes) -> bytes:
        r = tl.Reader(payload)
        cid = r.u32()
        if cid != tl.SET_CLIENT_DH_PARAMS:
            raise HandshakeError(f'expected set_client_DH_params, got {cid:#010x}')

        nonce        = r.i128()
        server_nonce = r.i128()
        encrypted    = r.tl_bytes()

        if self._stage != 'dh' or not self.new_nonce:
            cached = _handshake_cache_get(nonce, server_nonce)
            if cached:
                log.info(
                    'set_client_DH_params restoring cached DH state (parallel connection)')
                self.nonce = nonce
                self.server_nonce = server_nonce
                self.new_nonce = cached['new_nonce']
                self._server_dh_priv = cached['server_dh_priv']
                self._stage = 'dh'
            else:
                raise HandshakeError('set_client_DH_params before req_DH_params')

        if nonce != self.nonce or server_nonce != self.server_nonce:
            raise HandshakeError('set_client_DH_params nonce mismatch')

        key, iv = _aes_key_iv_from_nonces(self.server_nonce, self.new_nonce)
        try:
            decrypted = crypto.aes_ige_decrypt(encrypted, key, iv)
        except Exception as exc:
            raise HandshakeError(f'DH decrypt failed: {exc}') from exc

        inner = decrypted[20:]
        r2 = tl.Reader(inner)
        cid2 = r2.u32()
        if cid2 != tl.CLIENT_DH_INNER_DATA:
            auth_aux = crypto.sha1(b'')[0:8]
            out = tl.Writer()
            out.u32(tl.DH_GEN_FAIL)
            out.i128(self.nonce)
            out.i128(self.server_nonce)
            out.i128(_new_nonce_hash3(self.new_nonce, auth_aux))
            return out.bytes_out()

        inn_nonce  = r2.i128()
        inn_sn     = r2.i128()
        _retry     = r2.i64()
        g_b_bytes  = r2.tl_bytes()

        if inn_nonce != self.nonce or inn_sn != self.server_nonce:
            auth_aux = crypto.sha1(b'')[0:8]
            out = tl.Writer()
            out.u32(tl.DH_GEN_FAIL)
            out.i128(self.nonce)
            out.i128(self.server_nonce)
            out.i128(_new_nonce_hash3(self.new_nonce, auth_aux))
            return out.bytes_out()

        g_b = tl.bytes_to_int(g_b_bytes)
        if not crypto.is_good_ga_or_gb(g_b, DH_PRIME):
            auth_aux = crypto.sha1(b'')[0:8]
            out = tl.Writer()
            out.u32(tl.DH_GEN_RETRY)
            out.i128(self.nonce)
            out.i128(self.server_nonce)
            out.i128(_new_nonce_hash3(self.new_nonce, auth_aux))
            return out.bytes_out()

        shared = crypto.dh_compute_shared(g_b, self._server_dh_priv, DH_PRIME)
        self.auth_key = shared.to_bytes(256, 'big')
        self._stage = 'done'
        _handshake_cache_pop(self.nonce, self.server_nonce)

        log.debug('Auth key generated, id=%#018x', crypto.auth_key_id(self.auth_key))

        auth_key_aux = crypto.sha1(self.auth_key)[:8]
        nn_hash = crypto.sha1(self.new_nonce + b'\x01' + auth_key_aux)
        new_nonce_hash1 = nn_hash[-16:]

        out = tl.Writer()
        out.u32(tl.DH_GEN_OK)
        out.i128(self.nonce)
        out.i128(self.server_nonce)
        out.i128(new_nonce_hash1)
        return out.bytes_out()
