#!/usr/bin/env python3
"""Patch the AirGram iOS IPA (Telegram-iOS build) for the local server.

Inside Payload/*.app/<CFBundleExecutable>:
  1. The embedded MTProto RSA public key (PKCS#1 PEM, 425 bytes) is replaced
     with our server key (keys/server_rsa_public_pkcs1.pem).
  2. Every DC address literal (old private-server IPs, Telegram DC ranges)
     with the SAME byte length as OUR_IP is replaced with OUR_IP.

Telegram-iOS derives key fingerprints from the PEM at runtime, so unlike
Android there are no fingerprint constants to patch.

The patched IPA still needs device-side signing (Sideloadly / AltStore /
esign / fastlane match).  If `ldid` is on PATH the binary is additionally
fake-signed so the archive stays self-consistent (TrollStore etc.).

Usage:
  python3 tools/patch_official_ios.py <AirGram.ipa> [-o out.ipa]
"""
from __future__ import annotations

import argparse
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUR_IP = b'84.21.173.237'          # projectre.work

# Endpoint literals replaced only when byte-length matches OUR_IP (13).
OLD_IPS = [
    b'169.58.61.186',              # previous private server (AirGram build)
    b'149.154.167.51', b'149.154.175.50', b'149.154.175.51',
    b'91.108.56.10', b'91.108.4.20',
]

PEM_RE = re.compile(
    rb'-----BEGIN RSA PUBLIC KEY-----\n.*?\n-----END RSA PUBLIC KEY-----', re.S)


def load_our_pem() -> bytes:
    pem = (ROOT / 'keys' / 'server_rsa_public_pkcs1.pem').read_bytes()
    return pem.replace(b'\r\n', b'\n').strip()


def find_executable(zf: zipfile.ZipFile) -> str:
    app_dirs = {n.split('/')[1] for n in zf.namelist()
                if n.startswith('Payload/') and n.count('/') >= 1}
    app = sorted(n for n in zf.namelist()
                 if re.match(r'Payload/[^/]+\.app/Info\.plist$', n))
    if not app:
        raise SystemExit('Payload/*.app/Info.plist not found — wrong IPA?')
    info = plistlib.loads(zf.read(app[0]))
    exe = info.get('CFBundleExecutable')
    if not exe:
        raise SystemExit('CFBundleExecutable missing from Info.plist')
    binary_path = app[0].replace('Info.plist', exe)
    names = zf.namelist()
    if binary_path not in names:
        raise SystemExit(f'executable not found in IPA: {binary_path}')
    return binary_path


def patch_binary(data: bytes, our_pem: bytes) -> bytes:
    # --- RSA key ---
    pem_hits = 0
    out = data
    for m in list(PEM_RE.finditer(data))[::-1]:
        if m.end() - m.start() != len(our_pem):
            continue  # not the production-size MTProto key slot
        out = out[:m.start()] + our_pem + out[m.end():]
        pem_hits += 1
    print(f'RSA PEM blocks replaced: {pem_hits}')
    if pem_hits == 0:
        raise SystemExit('No 425-byte RSA PEM found — already patched?')

    # --- DC addresses ---
    ip_hits = 0
    for old in OLD_IPS:
        n = out.count(old)
        if n == 0:
            continue
        if len(old) != len(OUR_IP):
            print(f'  skip {old.decode()} (length differs from OUR_IP)')
            continue
        out = out.replace(old, OUR_IP)
        ip_hits += n
        print(f'  {old.decode()} -> {OUR_IP.decode()}: {n}')
    print(f'DC address literals replaced: {ip_hits}')
    if ip_hits == 0:
        print('WARNING: no DC address literals found — endpoints may be '
              'DNS-based already')

    # --- post-checks ---
    for m in PEM_RE.finditer(out):
        block = m.group(0)
        if len(block) == len(our_pem) and block != our_pem:
            raise SystemExit('a production PEM slot survived the patch!')
    for old in OLD_IPS:
        if len(old) == len(OUR_IP) and old in out:
            raise SystemExit(f'{old.decode()} still present after patch!')
    print('Post-checks OK')
    return out


def fake_sign(binary: Path) -> bool:
    if shutil.which('ldid') is None:
        print('ldid not found — skipping fake-sign (re-sign on device)')
        return False
    subprocess.run(['ldid', '-S', str(binary)], check=False)
    print(f'ldid fake-sign done: {binary}')
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('ipa', type=Path)
    ap.add_argument('-o', '--out', type=Path, default=None)
    args = ap.parse_args()
    out = args.out or args.ipa.with_name(args.ipa.stem + '-patched.ipa')
    if out.resolve() == args.ipa.resolve():
        raise SystemExit('-o must differ from the input IPA')

    our_pem = load_our_pem()
    src = zipfile.ZipFile(args.ipa)
    exe_name = find_executable(src)
    print(f'IPA: {args.ipa}')
    print(f'executable: {exe_name}')

    # Read every entry into memory first — the output must never be written
    # while the source archive is still open.
    entries = [(info, src.read(info.filename)) for info in src.infolist()]
    src.close()

    exe_data = patch_binary(
        next(blob for info, blob in entries if info.filename == exe_name),
        our_pem)

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp_exe = Path(td) / 'binary'
        tmp_exe.write_bytes(exe_data)
        fake_sign(tmp_exe)
        exe_data = tmp_exe.read_bytes()

        with zipfile.ZipFile(out, 'w') as dst:
            for info, blob in entries:
                if info.filename == exe_name:
                    blob = exe_data
                dst.writestr(info, blob, compress_type=info.compress_type)
    print(f'Patched IPA written: {out}')
    print('Next: sign on device (Sideloadly / AltStore / esign / fastlane)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
