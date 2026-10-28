"""
MTProto cryptographic primitives:
  - AES-256-IGE  (encrypted messages)
  - AES-256-CTR  (obfuscated transport)
  - RSA          (auth-key handshake)
  - DH           (auth-key handshake)
  - SHA1 / SHA256 helpers
"""
import os
import struct
import hashlib
import logging
from typing import Tuple

from Crypto.Cipher import AES as _AES
from Crypto.Util import Counter as _Counter
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Basic hash helpers
# ──────────────────────────────────────────────

def sha1(data: bytes) -> bytes:
    return hashlib.sha1(data).digest()

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))

# ──────────────────────────────────────────────
# AES-256-IGE  (MTProto encrypted messages)
# ──────────────────────────────────────────────

def aes_ige_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-256-IGE encryption.  C_i = E_k(P_i XOR C_{i-1}) XOR P_{i-1}"""
    assert len(key) == 32
    assert len(iv) == 32
    assert len(plaintext) % 16 == 0
    aes = _AES.new(key, _AES.MODE_ECB)
    prev_c = iv[:16]
    prev_p = iv[16:]
    out = bytearray()
    for i in range(0, len(plaintext), 16):
        p = plaintext[i:i + 16]
        c = xor_bytes(aes.encrypt(xor_bytes(p, prev_c)), prev_p)
        out.extend(c)
        prev_c = c
        prev_p = p
    return bytes(out)

def aes_ige_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-256-IGE decryption.  P_i = D_k(C_i XOR P_{i-1}) XOR C_{i-1}"""
    assert len(key) == 32
    assert len(iv) == 32
    assert len(ciphertext) % 16 == 0
    aes = _AES.new(key, _AES.MODE_ECB)
    prev_c = iv[:16]
    prev_p = iv[16:]
    out = bytearray()
    for i in range(0, len(ciphertext), 16):
        c = ciphertext[i:i + 16]
        p = xor_bytes(aes.decrypt(xor_bytes(c, prev_p)), prev_c)
        out.extend(p)
        prev_c = c
        prev_p = p
    return bytes(out)

# ──────────────────────────────────────────────
# AES-256-CTR  (obfuscated transport stream)
# ──────────────────────────────────────────────

class AesCtr:
    """Stateful AES-256-CTR stream cipher (big-endian counter)."""

    def __init__(self, key: bytes, iv: bytes):
        assert len(key) == 32
        assert len(iv) == 16
        ctr = _Counter.new(128, initial_value=int.from_bytes(iv, 'big'))
        self._cipher = _AES.new(key, _AES.MODE_CTR, counter=ctr)

    def process(self, data: bytes) -> bytes:
        return self._cipher.encrypt(data)

# ──────────────────────────────────────────────
# RSA helpers (for auth-key handshake)
# ──────────────────────────────────────────────

def load_private_key(pem_path: str):
    with open(pem_path, 'rb') as f:
        data = f.read()
    return serialization.load_pem_private_key(data, password=None,
                                               backend=default_backend())

def rsa_decrypt_raw(encrypted: bytes, private_key) -> bytes:
    """Raw RSA decryption: m = c^d mod n → 256-byte big-endian."""
    nums = private_key.private_numbers()
    n = nums.public_numbers.n
    d = nums.d
    if len(encrypted) < 256:
        encrypted = b'\x00' * (256 - len(encrypted)) + encrypted
    elif len(encrypted) > 256:
        encrypted = encrypted[-256:]
    c = int.from_bytes(encrypted, 'big')
    m = pow(c, d, n)
    return m.to_bytes(256, 'big')


def decrypt_pq_inner_rsa(encrypted: bytes, private_key) -> bytes:
    """
    Decrypt p_q_inner_data from req_DH_params.

    Telegram Desktop uses MTProto 2.0 wrapping (AES-IGE + RSA). Older clients
    used SHA1(inner) + RSA directly — we try the new format first.
    Returns serialized TL object bytes (constructor + fields, no padding).
    """
    block = rsa_decrypt_raw(encrypted, private_key)
    if len(block) == 256:
        try:
            return _decrypt_pq_inner_v2(block)
        except ValueError as exc:
            log.debug('PQ inner v2 decrypt failed (%s), trying v1', exc)
        # Telethon pads to 255 B before RSA; to_bytes(256) adds a leading 0x00.
        if block[0] == 0:
            block = block[1:]
    return _decrypt_pq_inner_v1(block)


def _decrypt_pq_inner_v2(key_aes_encrypted: bytes) -> bytes:
    if len(key_aes_encrypted) != 256:
        raise ValueError(f'expected 256 bytes, got {len(key_aes_encrypted)}')

    temp_key_xor = key_aes_encrypted[:32]
    aes_encrypted = key_aes_encrypted[32:]
    temp_key = xor_bytes(temp_key_xor, sha256(aes_encrypted))
    data_with_hash = aes_ige_decrypt(aes_encrypted, temp_key, bytes(32))

    data_pad_reversed = data_with_hash[:-32]
    hash_val = data_with_hash[-32:]
    data_with_padding = bytes(reversed(data_pad_reversed))
    if sha256(temp_key + data_with_padding) != hash_val:
        raise ValueError('PQ inner SHA256 mismatch')

    return data_with_padding[:_pq_inner_tl_size(data_with_padding)]


def _decrypt_pq_inner_v1(block: bytes) -> bytes:
    if len(block) < 21:
        raise ValueError('PQ inner v1 block too short')
    sha1_check = block[:20]
    inner = block[20:]
    end = _pq_inner_tl_size(inner)
    inner_raw = inner[:end]
    if sha1(inner_raw) != sha1_check:
        raise ValueError('PQ inner v1 SHA1 mismatch')
    return inner_raw


def _pq_inner_tl_size(data: bytes) -> int:
    """Return byte length of a serialized p_q_inner_data object."""
    from . import tl

    pq_inner_cids = (
        tl.P_Q_INNER_DATA,
        tl.P_Q_INNER_DATA_DC,
        tl.P_Q_INNER_DATA_TEMP,
        tl.P_Q_INNER_DATA_TEMP_DC,
    )
    r = tl.Reader(data)
    cid = r.u32()
    if cid not in pq_inner_cids:
        raise ValueError(f'bad pq inner constructor: {cid:#010x}')
    r.tl_bytes()
    r.tl_bytes()
    r.tl_bytes()
    r.i128()
    r.i128()
    r.i256()
    if cid in (tl.P_Q_INNER_DATA_DC, tl.P_Q_INNER_DATA_TEMP_DC):
        r.i32()
    if cid in (tl.P_Q_INNER_DATA_TEMP, tl.P_Q_INNER_DATA_TEMP_DC):
        r.i32()
    return r.pos

def rsa_public_fingerprint(private_key) -> int:
    """
    MTProto fingerprint = last 8 bytes of SHA1(tl_bytes(n) + tl_bytes(e))
    interpreted as int64 little-endian.
    """
    pub = private_key.public_key().public_numbers()
    n_bytes = pub.n.to_bytes((pub.n.bit_length() + 7) // 8, 'big')
    e_bytes = pub.e.to_bytes((pub.e.bit_length() + 7) // 8, 'big')
    data = _tl_bytes(n_bytes) + _tl_bytes(e_bytes)
    digest = sha1(data)
    return struct.unpack_from('<q', digest, 12)[0]   # bytes 12-19, little-endian int64

def _tl_bytes(data: bytes) -> bytes:
    """Encode byte-string in TL 'bytes' format (length-prefixed, 4-byte aligned)."""
    n = len(data)
    if n < 254:
        raw = bytes([n]) + data
    else:
        raw = bytes([254]) + n.to_bytes(3, 'little') + data
    pad = (4 - len(raw) % 4) % 4
    return raw + b'\x00' * pad

# ──────────────────────────────────────────────
# Diffie-Hellman helpers
# ──────────────────────────────────────────────

def dh_gen_private() -> int:
    """Generate a random 256-byte DH private exponent."""
    return int.from_bytes(os.urandom(256), 'big')


def is_good_ga_or_gb(value: int, prime: int) -> bool:
    """Telegram requires g_a/g_b in (2^(2048-64), p - 2^(2048-64))."""
    bound = 1 << (2048 - 64)
    return bound < value < prime - bound


def dh_gen_server_ga(g: int, prime: int) -> tuple[int, int]:
    """Generate server-side g^a with Telegram's ga/gb constraints."""
    while True:
        priv = dh_gen_private()
        ga = dh_compute_public(g, priv, prime)
        if is_good_ga_or_gb(ga, prime):
            return priv, ga

def dh_compute_public(g: int, priv: int, p: int) -> int:
    return pow(g, priv, p)

def dh_compute_shared(peer_pub: int, priv: int, p: int) -> int:
    return pow(peer_pub, priv, p)

# ──────────────────────────────────────────────
# MTProto 2.0  message key derivation
# ──────────────────────────────────────────────

def derive_message_key(auth_key: bytes, msg_key: bytes, from_client: bool) -> Tuple[bytes, bytes]:
    """
    MTProto 2.0 key derivation.
    from_client=True  → x=0  (client→server)
    from_client=False → x=8  (server→client)
    Returns (aes_key 32B, aes_iv 32B).
    """
    x = 0 if from_client else 8
    sha256_a = sha256(msg_key + auth_key[x:x + 36])
    sha256_b = sha256(auth_key[x + 40:x + 76] + msg_key)
    aes_key = sha256_a[:8] + sha256_b[8:24] + sha256_a[24:32]
    aes_iv  = sha256_b[:8] + sha256_a[8:24] + sha256_b[24:32]
    return aes_key, aes_iv

def compute_msg_key(auth_key: bytes, plaintext: bytes, from_client: bool) -> bytes:
    """Middle 16 bytes of SHA256(auth_key[x+88 : x+88+32] + plaintext)."""
    x = 0 if from_client else 8
    return sha256(auth_key[88 + x:88 + x + 32] + plaintext)[8:24]

def auth_key_id(auth_key: bytes) -> int:
    return struct.unpack_from('<q', sha1(auth_key), 12)[0]

# ──────────────────────────────────────────────
# PQ factorization (Pollard's rho)
# ──────────────────────────────────────────────

def gen_pq() -> Tuple[int, int, int]:
    """Generate PQ = P*Q where P < Q are random primes.

    Telethon reads pq bytes as a signed big-endian integer; if the top bit
    of the minimal encoding is set, factorization fails. Regenerate until the
    encoded value is positive as signed.
    """
    import sympy
    while True:
        p = int(sympy.nextprime(int.from_bytes(os.urandom(4), 'big') | 0x80000000))
        q = int(sympy.nextprime(p + 1))
        pq = p * q
        pq_bytes = pq.to_bytes((pq.bit_length() + 7) // 8, 'big')
        if int.from_bytes(pq_bytes, 'big', signed=True) > 0:
            return pq, p, q
