#!/usr/bin/env python3
"""Patch an official Telegram Desktop build (Windows exe) for the local server.

Replaces inside the exe:
  1. Every embedded 2048-bit production RSA public key (PKCS#1 PEM block,
     "-----BEGIN RSA PUBLIC KEY-----", 425 bytes) with our server key.
  2. Every hardcoded Telegram DC IPv4 literal (149.154.*.*, 91.108.*.*,
     95.161.76.*, 185.76.151.*) with OUR_IP, null-padded to the same slot.

The port stays 443 (official DC options) — run the server on port 443.

Usage:
  python3 tools/patch_official_tdesktop.py <Telegram.exe> [-o out.exe]
"""
from __future__ import annotations

import argparse
import bisect
import re
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OUR_IP = b'84.21.173.237'          # projectre.work

PEM_RE = re.compile(
    rb'-----BEGIN RSA PUBLIC KEY-----.*?-----END RSA PUBLIC KEY-----', re.S)
IP_RE = re.compile(
    rb'\b(?:149\.154|91\.108|95\.161\.76|185\.76\.151)\.\d{1,3}\.\d{1,3}\b')
# Official builds store IPv6 literals with zero padding
# (e.g. 2001:067c:04e8:f002:0000:0000:0000:000a), so accept leading zeros.
V6_RE = re.compile(
    rb'\b(?:2001:0?b28|2001:0?67c|2a0a:0?f280):[0-9a-f:]+\b', re.I)


def load_our_pem() -> bytes:
    pem = (ROOT / 'keys' / 'server_rsa_public_pkcs1.pem').read_bytes()
    pem = pem.replace(b'\r\n', b'\n').strip()
    return pem


# --- craft success crash fix ---------------------------------------------
# TDesktop 7.2.5 bundles a lib_ui whose VerticalLayout::clear() deletes the
# row widgets.  The gift-craft success block calls container->clear() while
# its canvas widget (and the CraftAnimationState owned by that widget's
# lifetime) is still in use, so a successful craft crashes the client.
# We redirect that single clear() call to a detach-only stub that moves the
# rows vector out and merely releases the QPointers, like the older lib_ui
# behaved.  Verified on a real successful craft under Wine.
CRAFT_CALL_MARKER = bytes.fromhex(
    '0F57C0F30F7F44243041B9810000004C8D442430488BD6488B4F18')
CRAFT_CALL_NEEDLE = bytes.fromhex('488B4F18E8')
CLEAR_PROLOGUE = bytes.fromhex(
    '48895C24104889742418574883EC40488BF1488D5160')
TAKE_PROLOGUE = bytes.fromhex('48895C241048896C241848897C24204156')
VEC_PROLOGUE = bytes.fromhex('48895C2410564883EC30488B19')
STUB_HEAD = bytes.fromhex('534883EC40488D5160488D4C')
STUB_LEN = 35


class _Section:
    def __init__(self, name, va, vsize, raw_off, raw_size):
        self.name = name
        self.va = va
        self.vsize = vsize
        self.raw_off = raw_off
        self.raw_size = raw_size


def _parse_pe(data: bytes):
    e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b'PE\x00\x00':
        raise SystemExit('not a PE file')
    coff = e_lfanew + 4
    nsec = struct.unpack_from('<H', data, coff + 2)[0]
    opt_size = struct.unpack_from('<H', data, coff + 16)[0]
    opt = coff + 20
    image_base = struct.unpack_from('<Q', data, opt + 24)[0]
    sec_off = opt + opt_size
    sections = []
    for i in range(nsec):
        o = sec_off + i * 40
        name = data[o:o + 8].rstrip(b'\x00').decode('latin1')
        vsize, va, raw_size, raw_off = struct.unpack_from('<IIII', data, o + 8)
        sections.append(_Section(name, va, vsize, raw_off, raw_size))
    return image_base, sections


def _va_to_off(sections, image_base, va):
    for s in sections:
        if image_base + s.va <= va < image_base + s.va + max(
                s.vsize, s.raw_size):
            return s.raw_off + (va - image_base - s.va)
    raise SystemExit(f'VA 0x{va:x} not mapped to file offset')


def _off_to_va(sections, image_base, off):
    for s in sections:
        if s.raw_off <= off < s.raw_off + s.raw_size:
            return image_base + s.va + (off - s.raw_off)
    raise SystemExit(f'file offset 0x{off:x} not mapped to VA')


def _pdata_ranges(data, sections, image_base):
    pd = next((s for s in sections if s.name == '.pdata'), None)
    if pd is None:
        return []
    ranges = []
    for i in range(pd.vsize // 12):
        begin, end, _ = struct.unpack_from('<III', data, pd.raw_off + i * 12)
        if begin and end:
            ranges.append((image_base + begin, image_base + end))
    ranges.sort()
    return ranges


def _is_covered(ranges, starts, va, size):
    i = bisect.bisect_right(starts, va + size - 1) - 1
    while i >= 0:
        begin, end = ranges[i]
        if begin < va + size and end > va:
            return True
        if end <= va:
            break
        i -= 1
    return False


def apply_craft_crash_fix(data: bytearray) -> None:
    image_base, sections = _parse_pe(bytes(data))
    hits = []
    start = 0
    while True:
        i = data.find(CRAFT_CALL_NEEDLE, start)
        if i < 0:
            break
        if data[i + 9:i + 9 + len(CRAFT_CALL_MARKER)] == CRAFT_CALL_MARKER:
            hits.append(i)
        start = i + 1
    if len(hits) != 1:
        raise SystemExit(
            f'craft clear() call site: expected 1 hit, found {len(hits)}')
    call_off = hits[0] + 4
    call_va = _off_to_va(sections, image_base, call_off)
    target_va = call_va + 5 + struct.unpack_from('<i', data, call_off + 1)[0]
    target_off = _va_to_off(sections, image_base, target_va)

    if bytes(data[target_off:target_off + len(STUB_HEAD)]) == STUB_HEAD:
        print('Craft success-crash fix: already applied')
        return

    clear_va = target_va
    clear_off = target_off
    if bytes(data[clear_off:clear_off + len(CLEAR_PROLOGUE)]) \
            != CLEAR_PROLOGUE:
        raise SystemExit(
            f'0x{clear_va:x} is not the expected VerticalLayout::clear()')
    pro = bytes(data[clear_off:clear_off + 0x80])
    lea_call = bytes.fromhex('488D4C2420E8')
    positions = [j for j in range(len(pro)) if pro.startswith(lea_call, j)]
    if len(positions) < 2:
        raise SystemExit('cannot locate take()/vector-dtor calls in clear()')
    call_targets = [
        clear_va + p + 10 + struct.unpack_from('<i', pro, p + 6)[0]
        for p in positions[:2]
    ]
    take_va, vec_va = call_targets

    take_off = _va_to_off(sections, image_base, take_va)
    if bytes(data[take_off:take_off + len(TAKE_PROLOGUE)]) != TAKE_PROLOGUE:
        raise SystemExit(f'0x{take_va:x} is not the expected take() helper')
    vec_off = _va_to_off(sections, image_base, vec_va)
    if bytes(data[vec_off:vec_off + len(VEC_PROLOGUE)]) != VEC_PROLOGUE:
        raise SystemExit(f'0x{vec_va:x} is not the expected vector dtor')

    ranges = _pdata_ranges(data, sections, image_base)
    starts = [r[0] for r in ranges]
    text = next(s for s in sections if s.name == '.text')
    blob = bytes(data[text.raw_off:text.raw_off + text.raw_size])
    cave_va = None
    for m in re.finditer(rb'\xcc{64,}', blob):
        run_va = image_base + text.va + m.start()
        run_len = m.end() - m.start()
        for k in range(0, run_len - 0x40 + 1, 8):
            candidate = run_va + k
            if not _is_covered(ranges, starts, candidate, 0x40):
                delta = candidate - (call_va + 5)
                if -(1 << 31) <= delta < (1 << 31):
                    cave_va = candidate
                    break
        if cave_va is not None:
            break
    if cave_va is None:
        raise SystemExit('no usable code cave found for the craft fix')

    cave_off = _va_to_off(sections, image_base, cave_va)
    take_rel = take_va - (cave_va + 19)
    vec_rel = vec_va - (cave_va + 29)
    stub = (
        bytes.fromhex('534883EC40488D5160488D4C2420E8')
        + struct.pack('<i', take_rel)
        + bytes.fromhex('488D4C2420E8')
        + struct.pack('<i', vec_rel)
        + bytes.fromhex('4883C4405BC3')
    )
    assert len(stub) == STUB_LEN
    data[cave_off:cave_off + STUB_LEN] = stub

    data[call_off:call_off + 5] = b'\xe8' + struct.pack(
        '<i', cave_va - (call_va + 5))

    # post-check
    new_rel = struct.unpack_from('<i', data, call_off + 1)[0]
    if call_va + 5 + new_rel != cave_va:
        raise SystemExit('craft fix call-site verification failed')
    print(f'Craft success-crash fix applied: clear() at 0x{clear_va:x} '
          f'-> detach stub at 0x{cave_va:x}')


def replace_padded(data: bytearray, start: int, end: int, repl: bytes,
                   label: str) -> None:
    slot = end - start
    if len(repl) > slot:
        raise SystemExit(
            f'{label}: replacement ({len(repl)}) does not fit slot ({slot})')
    data[start:end] = repl + b'\x00' * (slot - len(repl))


def patch_exe(exe: Path, out: Path) -> None:
    our_pem = load_our_pem()
    data = bytearray(exe.read_bytes())
    print(f'Loaded {exe} ({len(data)} bytes)')

    # --- RSA keys: replace every production-size (>= 300 bytes) PEM block ---
    pem_hits = 0
    for m in list(PEM_RE.finditer(bytes(data)))[::-1]:
        if m.end() - m.start() < 300:
            continue  # test-mode 1024-bit keys: not used in production
        replace_padded(data, m.start(), m.end(), our_pem, 'RSA PEM')
        pem_hits += 1
    print(f'RSA PEM blocks replaced: {pem_hits}')
    if pem_hits == 0:
        raise SystemExit('No production RSA PEM blocks found — wrong build?')

    # --- DC IPv4 literals ---
    ip_hits = 0
    for m in list(IP_RE.finditer(bytes(data)))[::-1]:
        replace_padded(data, m.start(), m.end(), OUR_IP, 'DC IPv4')
        ip_hits += 1
    print(f'DC IPv4 literals replaced: {ip_hits}')
    if ip_hits == 0:
        raise SystemExit('No Telegram DC IPv4 literals found — wrong build?')

    # --- DC IPv6 literals (invalid v6 → client falls back to IPv4) ---
    v6_hits = 0
    for m in list(V6_RE.finditer(bytes(data)))[::-1]:
        replace_padded(data, m.start(), m.end(), OUR_IP, 'DC IPv6')
        v6_hits += 1
    print(f'DC IPv6 literals replaced: {v6_hits}')

    # --- kill the internal update check ---
    # tdesktop fetches updates from td.telegram.org; on a private server the
    # real CDN always offers a newer build, so the client nags "update" and
    # users install versions the server schema can't serve.  Point the update
    # host at the reserved .invalid TLD — the check fails silently.  Same
    # byte length keeps every other offset intact.
    UPD_OLD = b'td.telegram.org'
    UPD_NEW = b'nx.invalid.test'
    assert len(UPD_OLD) == len(UPD_NEW)
    upd_hits = data.count(UPD_OLD)
    if upd_hits:
        data = data.replace(UPD_OLD, UPD_NEW)
    print(f'Update host neutralized: {upd_hits} hit(s)')

    # --- successful gift craft must not crash the client ---
    apply_craft_crash_fix(data)

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.resolve() != exe.resolve():
        shutil.copy2(exe, out)
    out.write_bytes(data)
    print(f'Patched copy written: {out}')

    # --- post-checks ---
    blob = out.read_bytes()
    assert blob.count(our_pem) >= pem_hits, 'our PEM missing after patch'
    assert not IP_RE.search(blob), 'Telegram IPv4 still present after patch'
    assert not V6_RE.search(blob), 'Telegram IPv6 still present after patch'
    print('Post-checks OK: no Telegram DC addresses remain, our key embedded.')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('exe', type=Path)
    ap.add_argument('-o', '--out', type=Path, default=None)
    args = ap.parse_args()
    out = args.out or args.exe
    patch_exe(args.exe, out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
