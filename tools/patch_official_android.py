#!/usr/bin/env python3
"""Patch an official Telegram for Android APK (Layer-225 builds, e.g. 12.7.x)
for the local server, then rezip/zipalign/sign.

Replaces inside every lib/*/libtmessages.*.so:
  1. The embedded production RSA public key (PKCS#1 PEM, fingerprint
     0xd09d1d85de64fd85) with our server key.
  2. Every built-in Telegram DC address literal (IPv4 + IPv6, prod and test)
     with OUR_IP, null-padded to the original slot length.

Then re-signs the APK with keys/projectre.jks (created on first run).

Usage:
  python3 tools/patch_official_android.py <Telegram.apk> [-o out.apk]
  # then: zipalign + apksigner (printed at the end)
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUR_IP = b'84.21.173.237'          # projectre.work

PEM_RE = re.compile(
    rb'-----BEGIN RSA PUBLIC KEY-----.*?-----END RSA PUBLIC KEY-----', re.S)
IPV4_RE = re.compile(
    rb'\b(?:149\.154|91\.108|95\.161\.76|185\.76\.151)\.\d{1,3}\.\d{1,3}\b')
IPV6_RE = re.compile(
    rb'\b(?:2001:b28|2001:67c|2a0a:f280):[0-9a-fA-F:]+\b')

# Telegram's built-in server key fingerprints (Handshake.cpp constants —
# unlike Telegram Desktop, Android does NOT derive them from the PEM).
OLD_PROD_FP = 0xd09d1d85de64fd85
OLD_TEST_FP = 0xb25898df208d2603
OUR_FP = 0x3505a1d5c62c90f6   # fingerprint(keys/server_rsa.key), see tools/export_rsa_key.py

import struct


def _patch_fp_u64(data: bytearray, old_fp: int, new_fp: int, label: str) -> int:
    """x86_64 movabs / literal pools: full little-endian 64-bit constant."""
    old, new = struct.pack('<Q', old_fp), struct.pack('<Q', new_fp)
    n = data.count(old)
    start = 0
    for _ in range(n):
        i = bytes(data).find(old, start)
        data[i:i + 8] = new
        start = i + 8
    print(f'    fp[{label}] u64 x{n}')
    return n


def _patch_fp_u32_halves(data: bytearray, old_fp: int, new_fp: int,
                         label: str) -> int:
    """x86/ARM32 immediate halves: little-endian 32-bit halves of the value."""
    total = 0
    for shift in (0, 32):
        old = struct.pack('<I', (old_fp >> shift) & 0xffffffff)
        new = struct.pack('<I', (new_fp >> shift) & 0xffffffff)
        n = data.count(old)
        if n > 4:
            raise SystemExit(
                f'fp[{label}] half {shift} found {n}x — refusing to patch')
        start = 0
        for _ in range(n):
            i = bytes(data).find(old, start)
            data[i:i + 4] = new
            start = i + 4
        total += n
    print(f'    fp[{label}] u32 halves x{total}')
    return total


def _patch_fp_arm64(data: bytearray, old_fp: int, new_fp: int,
                    label: str) -> int:
    """arm64 movz/movk sequence: rewrite the 16-bit immediates in place."""
    ops = {0xD2800000: 0, 0xF2A00000: 16, 0xF2C00000: 32, 0xF2E00000: 48}
    old_imms = {(old_fp >> s) & 0xffff: s for s in (0, 16, 32, 48)}
    hits = []  # (offset, old_shift, imm)
    for i in range(0, len(data) - 4, 4):
        w = struct.unpack_from('<I', data, i)[0]
        shift = ops.get(w & 0xFFE00000)
        if shift is None:
            continue
        imm = (w >> 5) & 0xffff
        if imm in old_imms:
            hits.append((i, shift, imm))
    if not hits:
        return 0
    # keep only the tight cluster(s): the real sequence is 4 words within 32 bytes
    patched = 0
    for off, old_shift, imm in hits:
        if not any(abs(off - o) <= 32 for o, s, _ in hits if s != old_shift):
            continue
        new_imm = (new_fp >> old_shift) & 0xffff
        w = struct.unpack_from('<I', data, off)[0]
        # imm16 spans bits 20:5 — clear all of them (0xFFE0FFFF leaves the
        # low 11 imm bits set, OR-ing the old chunk into the new one)
        w = (w & 0xFFE0001F) | (new_imm << 5)
        struct.pack_into('<I', data, off, w)
        patched += 1
    print(f'    fp[{label}] arm64 immediates x{patched}')
    return patched


def patch_fingerprints(data: bytearray) -> None:
    for label, old_fp in (('prod', OLD_PROD_FP), ('test', OLD_TEST_FP)):
        n = (_patch_fp_u64(data, old_fp, OUR_FP, label)
             + _patch_fp_u32_halves(data, old_fp, OUR_FP, label)
             + _patch_fp_arm64(data, old_fp, OUR_FP, label))
        if label == 'prod' and n == 0:
            raise SystemExit('Production fingerprint not found in .so!')


def load_our_pem() -> bytes:
    pem = (ROOT / 'keys' / 'server_rsa_public_pkcs1.pem').read_bytes()
    return pem.replace(b'\r\n', b'\n').strip()


def replace_padded(data: bytearray, start: int, end: int, repl: bytes,
                   label: str) -> None:
    slot = end - start
    if len(repl) > slot:
        raise SystemExit(
            f'{label}: replacement ({len(repl)}) does not fit slot ({slot})')
    data[start:end] = repl + b'\x00' * (slot - len(repl))


def patch_so(data: bytes, our_pem: bytes) -> tuple[bytes, int, int, int]:
    buf = bytearray(data)
    pem_hits = ip4 = ip6 = 0
    for m in list(PEM_RE.finditer(data))[::-1]:
        if m.end() - m.start() < 300:
            continue  # 1024-bit test keys are not used in production
        replace_padded(buf, m.start(), m.end(), our_pem, 'RSA PEM')
        pem_hits += 1
    for m in list(IPV4_RE.finditer(data))[::-1]:
        replace_padded(buf, m.start(), m.end(), OUR_IP, 'DC IPv4')
        ip4 += 1
    for m in list(IPV6_RE.finditer(data))[::-1]:
        replace_padded(buf, m.start(), m.end(), OUR_IP, 'DC IPv6')
        ip6 += 1
    patch_fingerprints(buf)
    return bytes(buf), pem_hits, ip4, ip6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('apk', type=Path)
    ap.add_argument('-o', '--out', type=Path, default=None)
    args = ap.parse_args()
    out = args.out or args.apk.with_name(
        args.apk.stem + '-projectre' + args.apk.suffix)

    our_pem = load_our_pem()
    src = zipfile.ZipFile(args.apk)
    names = src.namelist()
    so_names = [n for n in names
                if n.startswith('lib/') and 'libtmessages' in n]
    print(f'APK: {args.apk}')
    print(f'native libs: {len(so_names)}: {", ".join(so_names)}')
    if not so_names:
        raise SystemExit('No libtmessages.*.so found — wrong APK?')

    out.parent.mkdir(parents=True, exist_ok=True)
    total = [0, 0, 0]
    with zipfile.ZipFile(out, 'w') as dst:
        for info in src.infolist():
            blob = src.read(info.filename)
            if info.filename in so_names:
                blob, p, i4, i6 = patch_so(blob, our_pem)
                total[0] += p; total[1] += i4; total[2] += i6
                print(f'  {info.filename}: PEM={p} IPv4={i4} IPv6={i6}')
            # preserve the original per-entry compression (native libs must
            # stay STORED: extractNativeLibs=false in the manifest)
            dst.writestr(info, blob, compress_type=info.compress_type)
    print(f'TOTAL: PEM={total[0]} IPv4={total[1]} IPv6={total[2]}')
    if total[0] == 0:
        raise SystemExit('Production RSA key not found in any .so!')
    if total[1] == 0:
        raise SystemExit('No DC IPv4 literals found in any .so!')

    aligned = out.with_suffix('.aligned.apk')
    # -p page-aligns stored .so entries (required for extractNativeLibs=false)
    subprocess.run(['zipalign', '-f', '-p', '4', str(out), str(aligned)],
                   check=True)
    print(f'zipalign OK -> {aligned}')
    print(f'Next: apksigner sign --ks keys/projectre.jks {aligned}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
