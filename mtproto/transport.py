"""
Obfuscated abridged MTProto TCP transport.

Supports:
  - Direct (Telegram Desktop built-in DC, empty secret)
  - MTProto proxy obfuscation (_obf, 16-byte secret, SHA256 key derivation)
"""
import logging
from typing import Optional

import config
from .crypto import AesCtr, sha256

log = logging.getLogger(__name__)

PROTOCOL_ABRIDGED = 0xEFEFEFEF
PROTOCOL_INTER    = 0xEEEEEEEE
PROTOCOL_PADDED   = 0xDDDDDDDD
_VALID_PROTOCOLS = {PROTOCOL_ABRIDGED, PROTOCOL_INTER, PROTOCOL_PADDED}


class FramingError(RuntimeError):
    """Raised when transport framing is invalid for current protocol."""
    pass


def _derive_ctr_key(source: bytes, secret: bytes) -> bytes:
    if not secret:
        return source
    return sha256(source + secret)


def _read_tail(frame: bytes, recv_key: bytes, recv_iv: bytes):
    recv_ctr_tmp = AesCtr(recv_key, recv_iv)
    keystream64 = recv_ctr_tmp.process(b'\x00' * 64)
    tail_plain = bytes(a ^ b for a, b in zip(frame[56:64], keystream64[56:64]))
    protocol_id = int.from_bytes(tail_plain[0:4], 'little')
    dc_id = int.from_bytes(tail_plain[4:6], 'little', signed=True)
    return protocol_id, dc_id


class ObfuscatedTransport:
    """Stateful helper that manages the CTR stream for one connection."""

    def __init__(self):
        self._recv_ctr: Optional[AesCtr] = None
        self._send_ctr: Optional[AesCtr] = None
        self._initialized = False
        self._protocol_id: int = 0
        self._dc_id: int = 0
        self._secret: bytes = b''

    def is_ready(self) -> bool:
        return self._initialized

    def init_with_secret(self, frame: bytes, secret: bytes) -> bool:
        """Init from a known init frame + secret (fakeTLS inner stream).

        The ``dd`` secret's first byte (0xdd) is ignored per the MTProto
        proxy spec, so callers pass the 16-byte core.
        """
        if len(frame) < 64:
            return False
        recv_key = _derive_ctr_key(frame[8:40], secret)
        recv_iv = frame[40:56]
        protocol_id, dc_id = _read_tail(frame, recv_key, recv_iv)
        if protocol_id not in _VALID_PROTOCOLS:
            return False
        rev = bytes(reversed(frame[8:56]))
        self._protocol_id = protocol_id
        self._dc_id = dc_id
        self._secret = secret
        self._recv_ctr = AesCtr(recv_key, recv_iv)
        self._recv_ctr.process(b'\x00' * 64)
        self._send_ctr = AesCtr(
            _derive_ctr_key(rev[0:32], secret), rev[32:48])
        self._initialized = True
        log.debug('Transport init (fakeTLS inner): protocol=%#010x dc=%d',
                  protocol_id, dc_id)
        return True

    @property
    def protocol_id(self) -> int:
        return self._protocol_id

    def init_from_frame(self, frame: bytes) -> bool:
        if len(frame) < 64:
            return False

        for secret in config.MTPROXY_SECRETS:
            recv_key = _derive_ctr_key(frame[8:40], secret)
            recv_iv = frame[40:56]
            protocol_id, dc_id = _read_tail(frame, recv_key, recv_iv)
            if protocol_id not in _VALID_PROTOCOLS:
                continue

            rev = bytes(reversed(frame[8:56]))
            send_key = _derive_ctr_key(rev[0:32], secret)
            send_iv = rev[32:48]

            self._protocol_id = protocol_id
            self._dc_id = dc_id
            self._secret = secret

            self._recv_ctr = AesCtr(recv_key, recv_iv)
            self._recv_ctr.process(b'\x00' * 64)
            self._send_ctr = AesCtr(send_key, send_iv)
            self._initialized = True

            log.debug(
                'Transport init: protocol=%#010x, dc=%d, mode=%s',
                protocol_id,
                dc_id,
                'direct' if not secret else f'proxy secret={secret.hex()}',
            )
            return True

        log.warning('Transport init failed: no matching secret / bad init frame')
        return False

    def decrypt(self, data: bytes) -> bytes:
        return self._recv_ctr.process(data)

    def encrypt(self, data: bytes) -> bytes:
        return self._send_ctr.process(data)


class PacketBuffer:
    """Accumulates raw decrypted bytes and yields complete transport packets."""

    def __init__(self, protocol_id: int = PROTOCOL_ABRIDGED):
        self._buf = bytearray()
        self._protocol_id = protocol_id

    def set_protocol(self, protocol_id: int) -> None:
        self._protocol_id = protocol_id

    def feed(self, data: bytes):
        self._buf.extend(data)

    def next_packet(self):
        while True:
            if len(self._buf) == 0:
                return None
            if self._protocol_id in (PROTOCOL_INTER, PROTOCOL_PADDED):
                if len(self._buf) < 4:
                    return None
                n_raw = int.from_bytes(self._buf[0:4], 'little')
                # Intermediate/padded use byte length in the low 31 bits.
                # High bit may carry quick-ack request/response marker.
                n_bytes = n_raw & 0x7FFFFFFF
                if n_bytes <= 0 or n_bytes > 4 * 1024 * 1024:
                    first = self._buf[0]
                    raise FramingError(
                        f'invalid inter/padded length first={first:#04x} n={n_bytes}')
                total = 4 + n_bytes
                if len(self._buf) < total:
                    return None
                payload = bytes(self._buf[4:total])
                del self._buf[:total]
                # Ignore transport quick-ack packets.
                if len(payload) <= 16 and payload[:4] == b'\xff\xff\xff\xff':
                    continue
                return payload

            first = self._buf[0]
            # Abridged may carry quick-ack request bit in the highest bit
            # of the first length byte; clear it before decoding length.
            first_len = first & 0x7F
            if first_len == 0x7F:
                if len(self._buf) < 4:
                    return None
                n_ints = int.from_bytes(self._buf[1:4], 'little')
                header_len = 4
            elif 0 < first_len < 0x7F:
                n_ints = first_len
                header_len = 1
            else:
                if len(self._buf) < 4:
                    return None
                token = int.from_bytes(self._buf[0:4], 'little')
                # Abridged quick-ack is a standalone 4-byte token with high bit set.
                if token & 0x80000000:
                    del self._buf[:4]
                    continue
                raise FramingError(
                    f'invalid abridged header first={first:#04x}')

            payload_len = n_ints * 4
            total = header_len + payload_len
            if len(self._buf) < total:
                return None
            payload = bytes(self._buf[header_len:total])
            del self._buf[:total]
            return payload

    def pack_packet(self, data: bytes) -> bytes:
        if self._protocol_id == PROTOCOL_INTER:
            if len(data) % 4 != 0:
                raise ValueError('intermediate payload must be 4-byte aligned')
            return len(data).to_bytes(4, 'little') + data
        if self._protocol_id == PROTOCOL_PADDED:
            return len(data).to_bytes(4, 'little') + data
        n_ints = len(data) // 4
        assert len(data) % 4 == 0, 'payload must be 4-byte aligned'
        if n_ints < 0x7F:
            return bytes([n_ints]) + data
        return bytes([0x7F]) + n_ints.to_bytes(3, 'little') + data
