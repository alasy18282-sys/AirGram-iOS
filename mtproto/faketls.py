"""MTProto-proxy fakeTLS transport (secret prefix ``dd``).

The client opens the connection with a genuine-looking TLS 1.3 ClientHello
whose SNI carries the camouflage domain.  The first 32 "random" bytes carry
an HMAC-SHA256 tag of the whole hello (with the random zeroed) keyed with the
proxy secret; the low 4 bytes of the XOR-result hold a unix timestamp.
After the server answers with ServerHello + ChangeCipherSpec + one
ApplicationData noise record, both sides wrap every further byte in TLS
ApplicationData records (``17 03 03 <len> <payload>``).  Inside that record
stream flows the ordinary obfuscated MTProto transport (init frame first),
with CTR keys derived exactly like the plain ``_obf`` proxy mode but keyed
with the 16 secret bytes from the ``dd`` secret.

Reference: 9seconds/mtg (mtglib/internal/tls).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import struct
import time
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

RECORD_HANDSHAKE = 0x16
RECORD_APPDATA = 0x17
RECORD_HEADER = 5           # type(1) + version(2) + length(2)
MAX_RECORD_PAYLOAD = 16384 - RECORD_HEADER

# Offset of the 32-byte random inside the ClientHello handshake message:
# record header (5) + handshake header type(1)+len(3) + client_version(2)
RANDOM_OFFSET = RECORD_HEADER + 4 + 2
RANDOM_LEN = 32

GREASE_MASK = 0x0F0F
GREASE_VALUE = 0x0A0A

SERVER_HELLO_SUFFIX = bytes([
    0x00,        # no compression
    0x00, 0x2e,  # 46 bytes of extensions
    0x00, 0x2b,  # supported_versions
    0x00, 0x02,
    0x03, 0x04,  # TLS 1.3
    0x00, 0x33,  # key_share
    0x00, 0x24,  # 36 bytes
    0x00, 0x1d,  # x25519
    0x00, 0x20,  # 32 bytes of key
])

# Handshake timestamp tolerance: MTProto clients stamp the hello with local
# time, and phones with skewed clocks must still connect.
TIMESTAMP_TOLERANCE = 6 * 3600


class FakeTlsError(RuntimeError):
    pass


def secret_domains(raw_secret: bytes) -> list[str]:
    """Camouflage domains encoded in a ``dd`` secret.

    The user-facing secret is the hex of ``0xdd + 16 bytes`` with the plain
    ASCII domain appended, e.g. ``dd<a1b2…><www.example.com>``; raw bytes
    therefore carry the domain verbatim after byte 17.
    """
    if len(raw_secret) > 17 and raw_secret[0] == 0xDD:
        tail = raw_secret[17:]
        if tail:
            try:
                text = tail.decode('ascii', 'ignore')
            except Exception:
                return []
            return [part for part in text.replace(';', ',').split(',')
                    if part]
    return []


def looks_like_tls(head: bytes) -> bool:
    # ClientHello records carry the legacy 0x0301 (TLS 1.0) version on the
    # wire even when negotiating TLS 1.3 — accept any 0x03 0x??
    return len(head) >= 3 and head[0] == RECORD_HANDSHAKE and head[1] == 0x03


def _parse_client_hello(data: bytes) -> Tuple[bytes, bytes, int, bytes]:
    """Parse a full-record ClientHello.

    Returns (hello_frame, session_id, cipher_suite, sni) where hello_frame is
    the complete record (digest input).  Raises FakeTlsError on malformation.
    """
    if len(data) < RECORD_HEADER + 4:
        raise FakeTlsError('truncated client hello')
    rec_type = data[0]
    rec_len = struct.unpack_from('>H', data, 3)[0]
    if rec_type != RECORD_HANDSHAKE or len(data) < RECORD_HEADER + rec_len:
        raise FakeTlsError('bad client hello record')
    frame = data[:RECORD_HEADER + rec_len]

    pos = RECORD_HEADER
    hs_type = frame[pos]
    if hs_type != 0x01:
        raise FakeTlsError(f'not a client hello (type {hs_type:#x})')
    pos += 4                       # type + uint24 length
    pos += 2                       # client_version
    if len(frame) < pos + RANDOM_LEN:
        raise FakeTlsError('truncated random')
    pos += RANDOM_LEN

    sess_len = frame[pos]
    pos += 1
    if len(frame) < pos + sess_len:
        raise FakeTlsError('truncated session id')
    session_id = frame[pos:pos + sess_len]
    pos += sess_len

    if len(frame) < pos + 2:
        raise FakeTlsError('truncated cipher length')
    suites_len = struct.unpack_from('>H', frame, pos)[0]
    pos += 2
    cipher_suite = 0
    suites_end = pos + suites_len
    while pos + 2 <= suites_end:
        cs = struct.unpack_from('>H', frame, pos)[0]
        pos += 2
        if cs & GREASE_MASK != GREASE_VALUE and cipher_suite == 0:
            cipher_suite = cs
    if cipher_suite == 0:
        raise FakeTlsError('no usable cipher suite')
    pos = suites_end

    comp_len = frame[pos]
    pos += 1 + comp_len

    sni: bytes = b''
    if pos + 2 <= len(frame):
        ext_end = pos + struct.unpack_from('>H', frame, pos)[0]
        pos += 2
        while pos + 4 <= min(ext_end, len(frame)):
            ext_type = struct.unpack_from('>H', frame, pos)[0]
            ext_len = struct.unpack_from('>H', frame, pos + 2)[0]
            ext_data = frame[pos + 4:pos + 4 + ext_len]
            pos += 4 + ext_len
            if ext_type != 0x0000 or len(ext_data) < 5:
                continue
            list_len = struct.unpack_from('>H', ext_data, 0)[0]
            entry = ext_data[2:2 + list_len]
            if len(entry) >= 3 and entry[0] == 0:
                name_len = struct.unpack_from('>H', entry, 1)[0]
                sni = entry[3:3 + name_len]
    return frame, session_id, cipher_suite, sni


def verify_client_hello(frame: bytes, secret: bytes) -> bytes:
    """Validate the HMAC tag; returns the raw 32-byte random."""
    if len(frame) < RANDOM_OFFSET + RANDOM_LEN:
        raise FakeTlsError('frame too short for digest check')
    client_random = frame[RANDOM_OFFSET:RANDOM_OFFSET + RANDOM_LEN]
    digest = hmac.new(secret, digestmod=hashlib.sha256)
    digest.update(frame[:RANDOM_OFFSET])
    digest.update(b'\x00' * RANDOM_LEN)
    digest.update(frame[RANDOM_OFFSET + RANDOM_LEN:])
    computed = digest.digest()
    for i in range(RANDOM_LEN):
        computed_list = bytearray(computed)
        computed_list[i] ^= client_random[i]
        computed = bytes(computed_list)
    if any(computed[:RANDOM_LEN - 4]):
        raise FakeTlsError('digest mismatch')
    (ts,) = struct.unpack('<I', computed[RANDOM_LEN - 4:])
    skew = abs(time.time() - ts)
    if skew > TIMESTAMP_TOLERANCE:
        raise FakeTlsError(f'timestamp skew {skew / 3600:.1f}h')
    return client_random


def _x25519_public() -> bytes:
    # A valid-looking x25519 public value; the client never validates it.
    return hashlib.sha256(b'projectre-faketls-keyshare').digest()


def build_server_hello(session_id: bytes, cipher_suite: int,
                       client_random: bytes, secret: bytes,
                       noise_size: int = 3328) -> bytes:
    """ServerHello + ChangeCipherSpec + one ApplicationData noise record."""
    hs_payload = bytearray()
    hs_payload += b'\x03\x03'                       # TLS 1.2 legacy version
    hs_payload += b'\x00' * RANDOM_LEN              # random placeholder
    hs_payload += bytes([len(session_id)]) + session_id
    hs_payload += struct.pack('>H', cipher_suite)
    hs_payload += SERVER_HELLO_SUFFIX
    hs_payload += _x25519_public()

    hs_msg = bytearray()
    hs_msg += b'\x02' + struct.pack('>I', len(hs_payload))[1:]
    hs_msg += hs_payload

    record = bytearray()
    record += bytes([RECORD_HANDSHAKE]) + b'\x03\x03'
    record += struct.pack('>H', len(hs_msg))
    record += hs_msg

    record += bytes([0x14]) + b'\x03\x03' + struct.pack('>H', 1) + b'\x01'

    noise = hashlib.sha256(f'{noise_size}:{time.time()}'.encode()).digest() * (
        noise_size // 32 + 1)
    record += bytes([RECORD_APPDATA]) + b'\x03\x03'
    record += struct.pack('>H', noise_size)
    record += noise[:noise_size]

    tag = hmac.new(secret, digestmod=hashlib.sha256)
    tag.update(client_random)
    tag.update(bytes(record))
    record[RANDOM_OFFSET:RANDOM_OFFSET + RANDOM_LEN] = tag.digest()
    return bytes(record)


class FakeTlsStream:
    """Record framing on top of a raw socket stream once hello is done."""

    def __init__(self):
        self._buf = bytearray()
        self._raw: Optional[asyncio.StreamReader] = None

    def bind(self, reader: asyncio.StreamReader) -> None:
        self._raw = reader

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)

    def next_payload(self) -> Optional[bytes]:
        """Next ApplicationData payload or None if more data is needed."""
        while True:
            if len(self._buf) < RECORD_HEADER:
                return None
            rec_len = struct.unpack_from('>H', self._buf, 3)[0]
            if len(self._buf) < RECORD_HEADER + rec_len:
                return None
            rec_type = self._buf[0]
            payload = bytes(self._buf[RECORD_HEADER:RECORD_HEADER + rec_len])
            del self._buf[:RECORD_HEADER + rec_len]
            if rec_type == RECORD_APPDATA:
                return payload
            # handshake/close records after the hello are ignored

    async def read_payload(self) -> bytes:
        if self._raw is None:
            raise FakeTlsError('stream not bound')
        while True:
            payload = self.next_payload()
            if payload is not None:
                return payload
            chunk = await self._raw.read(16384)
            if not chunk:
                raise asyncio.IncompleteReadError(b'', 1)
            self.feed(chunk)

    @staticmethod
    def wrap(data: bytes) -> bytes:
        out = bytearray()
        for off in range(0, len(data), MAX_RECORD_PAYLOAD):
            chunk = data[off:off + MAX_RECORD_PAYLOAD]
            out += bytes([RECORD_APPDATA]) + b'\x03\x03'
            out += struct.pack('>H', len(chunk))
            out += chunk
        return bytes(out)
