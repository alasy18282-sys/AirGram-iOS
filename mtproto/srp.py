"""Telegram SRP-6a 2FA password helpers (server side)."""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Optional

SIZE_FOR_HASH = 256

# Standard 2048-bit safe prime used by Telegram (same as Telethon/TDLib).
GOOD_PRIME = bytes((
    0xC7, 0x1C, 0xAE, 0xB9, 0xC6, 0xB1, 0xC9, 0x04, 0x8E, 0x6C, 0x52, 0x2F, 0x70, 0xF1, 0x3F, 0x73,
    0x98, 0x0D, 0x40, 0x23, 0x8E, 0x3E, 0x21, 0xC1, 0x49, 0x34, 0xD0, 0x37, 0x56, 0x3D, 0x93, 0x0F,
    0x48, 0x19, 0x8A, 0x0A, 0xA7, 0xC1, 0x40, 0x58, 0x22, 0x94, 0x93, 0xD2, 0x25, 0x30, 0xF4, 0xDB,
    0xFA, 0x33, 0x6F, 0x6E, 0x0A, 0xC9, 0x25, 0x13, 0x95, 0x43, 0xAE, 0xD4, 0x4C, 0xCE, 0x7C, 0x37,
    0x20, 0xFD, 0x51, 0xF6, 0x94, 0x58, 0x70, 0x5A, 0xC6, 0x8C, 0xD4, 0xFE, 0x6B, 0x6B, 0x13, 0xAB,
    0xDC, 0x97, 0x46, 0x51, 0x29, 0x69, 0x32, 0x84, 0x54, 0xF1, 0x8F, 0xAF, 0x8C, 0x59, 0x5F, 0x64,
    0x24, 0x77, 0xFE, 0x96, 0xBB, 0x2A, 0x94, 0x1D, 0x5B, 0xCD, 0x1D, 0x4A, 0xC8, 0xCC, 0x49, 0x88,
    0x07, 0x08, 0xFA, 0x9B, 0x37, 0x8E, 0x3C, 0x4F, 0x3A, 0x90, 0x60, 0xBE, 0xE6, 0x7C, 0xF9, 0xA4,
    0xA4, 0xA6, 0x95, 0x81, 0x10, 0x51, 0x90, 0x7E, 0x16, 0x27, 0x53, 0xB5, 0x6B, 0x0F, 0x6B, 0x41,
    0x0D, 0xBA, 0x74, 0xD8, 0xA8, 0x4B, 0x2A, 0x14, 0xB3, 0x14, 0x4E, 0x0E, 0xF1, 0x28, 0x47, 0x54,
    0xFD, 0x17, 0xED, 0x95, 0x0D, 0x59, 0x65, 0xB4, 0xB9, 0xDD, 0x46, 0x58, 0x2D, 0xB1, 0x17, 0x8D,
    0x16, 0x9C, 0x6B, 0xC4, 0x65, 0xB0, 0xD6, 0xFF, 0x9C, 0xA3, 0x92, 0x8F, 0xEF, 0x5B, 0x9A, 0xE4,
    0xE4, 0x18, 0xFC, 0x15, 0xE8, 0x3E, 0xBE, 0xA0, 0xF8, 0x7F, 0xA9, 0xFF, 0x5E, 0xED, 0x70, 0x05,
    0x0D, 0xED, 0x28, 0x49, 0xF4, 0x7B, 0xF9, 0x59, 0xD9, 0x56, 0x85, 0x0C, 0xE9, 0x29, 0x85, 0x1F,
    0x0D, 0x81, 0x15, 0xF6, 0x35, 0xB1, 0x05, 0xEE, 0x2E, 0x4E, 0x15, 0xD0, 0x4B, 0x24, 0x54, 0xBF,
    0x6F, 0x4F, 0xAD, 0xF0, 0x34, 0xB1, 0x04, 0x03, 0x11, 0x9C, 0xD8, 0xE3, 0xB9, 0x2F, 0xCC, 0x5B,
))

DEFAULT_G = 3

PASSWORD_KDF_ALGO = 0x3a912d4a


def sha256(*parts: bytes) -> bytes:
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
    return h.digest()


def xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def num_bytes_for_hash(number: bytes) -> bytes:
    if len(number) >= SIZE_FOR_HASH:
        return number[-SIZE_FOR_HASH:]
    return bytes(SIZE_FOR_HASH - len(number)) + number


def big_num_for_hash(value: int) -> bytes:
    return value.to_bytes(SIZE_FOR_HASH, 'big')


def is_good_mod_exp_first(modexp: int, prime: int) -> bool:
    diff = prime - modexp
    min_diff_bits = 2048 - 64
    if diff < 0:
        return False
    if diff.bit_length() < min_diff_bits:
        return False
    if modexp.bit_length() < min_diff_bits:
        return False
    if (modexp.bit_length() + 7) // 8 > 256:
        return False
    return True


def make_default_algo() -> dict:
    return {
        'salt1': os.urandom(32),
        'salt2': os.urandom(32),
        'g': DEFAULT_G,
        'p': GOOD_PRIME,
    }


@dataclass
class SrpSession:
    srp_id: int
    srp_b: bytes
    b: int
    salt1: bytes
    salt2: bytes
    g: int
    p: bytes
    v: bytes


def create_srp_session(*, v: bytes, salt1: bytes, salt2: bytes,
                       g: int, p: bytes, srp_id: Optional[int] = None) -> SrpSession:
    p_int = int.from_bytes(p, 'big')
    v_int = int.from_bytes(v, 'big')
    p_for_hash = num_bytes_for_hash(p)
    g_for_hash = big_num_for_hash(g)
    k = int.from_bytes(sha256(p_for_hash, g_for_hash), 'big')
    k_v = (k * v_int) % p_int

    while True:
        b = int.from_bytes(os.urandom(256), 'big')
        g_b = (k_v + pow(g, b, p_int)) % p_int
        if is_good_mod_exp_first(g_b, p_int):
            return SrpSession(
                srp_id=srp_id or secrets.randbits(63),
                srp_b=g_b.to_bytes(SIZE_FOR_HASH, 'big'),
                b=b,
                salt1=salt1,
                salt2=salt2,
                g=g,
                p=p,
                v=v,
            )


def verify_check_password(*, a_bytes: bytes, m1: bytes, session: SrpSession) -> bool:
    p_int = int.from_bytes(session.p, 'big')
    g = session.g
    v = int.from_bytes(session.v, 'big')

    a_int = int.from_bytes(a_bytes, 'big')
    b_int = session.b
    b_bytes = session.srp_b

    p_for_hash = num_bytes_for_hash(session.p)
    g_for_hash = big_num_for_hash(g)
    a_for_hash = big_num_for_hash(a_int)
    b_for_hash = num_bytes_for_hash(b_bytes)

    u = int.from_bytes(sha256(a_for_hash, b_for_hash), 'big')
    if u == 0:
        return False

    s = pow(a_int * pow(v, u, p_int) % p_int, b_int, p_int)
    k = sha256(big_num_for_hash(s))
    m1_expected = sha256(
        xor_bytes(sha256(p_for_hash), sha256(g_for_hash)),
        sha256(session.salt1),
        sha256(session.salt2),
        a_for_hash,
        b_for_hash,
        k,
    )
    return secrets.compare_digest(m1, m1_expected)


def compute_client_check(*, password: str, salt1: bytes, salt2: bytes, g: int, p: bytes,
                         srp_b: bytes, srp_id: int) -> tuple[bytes, bytes, int]:
    """Client-side helper for tests: returns (A, M1, srp_id)."""
    pw = password.encode('utf-8')
    hash1 = sha256(salt1, pw, salt1)
    hash2 = sha256(salt2, hash1, salt2)
    hash3 = hashlib.pbkdf2_hmac('sha512', hash2, salt1, 100000)
    pw_hash = sha256(salt2, hash3, salt2)

    p_int = int.from_bytes(p, 'big')
    x = int.from_bytes(pw_hash, 'big')
    v = pow(g, x, p_int)

    p_for_hash = num_bytes_for_hash(p)
    g_for_hash = big_num_for_hash(g)
    b_for_hash = num_bytes_for_hash(srp_b)
    k = int.from_bytes(sha256(p_for_hash, g_for_hash), 'big')
    k_v = (k * v) % p_int
    b_server = int.from_bytes(srp_b, 'big')

    while True:
        a = int.from_bytes(os.urandom(256), 'big')
        a_pub = pow(g, a, p_int)
        if not is_good_mod_exp_first(a_pub, p_int):
            continue
        a_for_hash = big_num_for_hash(a_pub)
        u = int.from_bytes(sha256(a_for_hash, b_for_hash), 'big')
        if u == 0:
            continue
        g_b = (b_server - k_v) % p_int
        if not is_good_mod_exp_first(g_b, p_int):
            continue
        s = pow(g_b, a + u * x, p_int)
        k_sess = sha256(big_num_for_hash(s))
        m1 = sha256(
            xor_bytes(sha256(p_for_hash), sha256(g_for_hash)),
            sha256(salt1),
            sha256(salt2),
            a_for_hash,
            b_for_hash,
            k_sess,
        )
        return a_for_hash, m1, srp_id
