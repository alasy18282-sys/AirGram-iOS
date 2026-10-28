"""Export RSA public key PEM blocks for mtproto_dc_options.cpp."""
import struct
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

import config


def tl_bytes(data: bytes) -> bytes:
    n = len(data)
    if n < 254:
        raw = bytes([n]) + data
    else:
        raw = bytes([254]) + n.to_bytes(3, 'little') + data
    pad = (4 - len(raw) % 4) % 4
    return raw + b'\x00' * pad


def fingerprint(pub) -> int:
    nums = pub.public_numbers()
    n_bytes = nums.n.to_bytes((nums.n.bit_length() + 7) // 8, 'big')
    e_bytes = nums.e.to_bytes((nums.e.bit_length() + 7) // 8, 'big')
    digest = hashlib.sha1(tl_bytes(n_bytes) + tl_bytes(e_bytes)).digest()
    return struct.unpack_from('<q', digest, 12)[0]


def pem_to_cpp_block(label: str, pem: str) -> str:
    lines = [line for line in pem.strip().splitlines() if line]
    out = [f'const char* {label}[] = {{ "\\']
    for line in lines:
        out.append(line + '\\n\\')
    out.append('" };')
    return '\n'.join(out)


def main() -> None:
    with open(config.PRIVATE_KEY_PATH, 'rb') as f:
        priv = serialization.load_pem_private_key(
            f.read(), password=None, backend=default_backend())

    pub = priv.public_key()
    pkcs1 = pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.PKCS1,
    ).decode('ascii')
    spki = pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode('ascii')

    with open('keys/server_rsa_public_pkcs1.pem', 'w', encoding='ascii') as f:
        f.write(pkcs1)
    with open('keys/server_rsa_public_spki.pem', 'w', encoding='ascii') as f:
        f.write(spki)

    print(f'fingerprint: {fingerprint(pub):#018x}')
    print()
    print('Use PKCS#1 block for BEGIN RSA PUBLIC KEY (Telegram Desktop):')
    print(pem_to_cpp_block('kPublicRSAKeys', pkcs1))
    print()
    print('Use SPKI block for BEGIN PUBLIC KEY:')
    print(pem_to_cpp_block('kPublicRSAKeys', spki))


if __name__ == '__main__':
    main()
