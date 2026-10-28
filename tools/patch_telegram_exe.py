"""
Patch built Telegram.exe in-place (no rebuild):
  1. Fix RSA public key PEM (SPKI -> PKCS#1, LF line endings)
  2. Fix DC IP 192.168.100.1 -> 192.168.100.14

Usage:
  python tools/patch_telegram_exe.py
  python tools/patch_telegram_exe.py path/to/Telegram.exe
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT.parent / 'mytelegram' / 'out' / 'Debug' / 'Telegram.exe'

OLD_RSA = b"""-----BEGIN RSA PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu+3tvscWDAlEvVylTeMr
5FpU2AjgqzoQHPjzp69r0YAtq0a8rX0MUe78F/FRAqBaEbZW6WBzF3AjOlNYpOtv
vwGhl9rGCgziunbd9nwcKJBMDWS9O7Mz/8xjz/swIB4V56XcjOhrjUHJ/GniFKou
m00xeEcYnr5xnLesvpVMq97Ga6b+xt3HRftHY/Zy1dG5zs8upuiAOlEiKilhu1It
hfMjFG3NF6TiGrO9YU3YixFbJy67jtHkv5FarscM2fC5iWQ2eP1y6jXR64sGU3Qj
ncvozYOePrH9jGcnmzUmj42x/H28IjJQ9EjEc22sPOuauK0IF2QiCGh+TfsKCK18
9wIDAQAB
-----END RSA PUBLIC KEY-----"""

NEW_RSA = (
    Path(ROOT / 'keys' / 'server_rsa_public_pkcs1.pem')
    .read_bytes()
    .replace(b'\r\n', b'\n')
    .strip()
)

OLD_IP_PAIR = b'192.168.100.1\x00\x00\x00192.168.100.1\x00\x00\x00'
NEW_IP_PAIR = b'192.168.100.14\x00\x00192.168.100.14\x00\x00'

OLD_IP_SLOT = b'192.168.100.1\x00\x00\x00'
NEW_IP_SLOT = b'192.168.100.14\x00\x00'


def _replace_all(data: bytearray, old: bytes, new: bytes, label: str) -> int:
    if len(old) != len(new):
        raise ValueError(f'{label}: length mismatch {len(old)} != {len(new)}')
    count = 0
    start = 0
    while True:
        idx = data.find(old, start)
        if idx < 0:
            break
        data[idx:idx + len(old)] = new
        count += 1
        start = idx + len(old)
    return count


def _fix_crlf_rsa_blocks(data: bytearray) -> int:
    """Repair a previous bad patch that wrote CRLF into RSA PEM blocks."""
    bad = NEW_RSA.replace(b'\n', b'\r\n')
    good = NEW_RSA + b'\x00' * (len(OLD_RSA) - len(NEW_RSA))
    count = 0
    start = 0
    while True:
        idx = data.find(bad, start)
        if idx < 0:
            break
        if len(bad) > len(OLD_RSA):
            raise ValueError('CRLF RSA block longer than slot')
        padded = bad + b'\x00' * (len(OLD_RSA) - len(bad))
        data[idx:idx + len(OLD_RSA)] = good
        count += 1
        start = idx + len(OLD_RSA)
    return count


def patch_exe(exe_path: Path) -> None:
    if not exe_path.is_file():
        raise SystemExit(f'Not found: {exe_path}')

    if len(NEW_RSA) > len(OLD_RSA):
        raise SystemExit('New RSA block is longer than old block; update patch script.')

    new_rsa_padded = NEW_RSA + b'\x00' * (len(OLD_RSA) - len(NEW_RSA))

    backup = exe_path.with_suffix(exe_path.suffix + '.bak')
    if not backup.exists():
        shutil.copy2(exe_path, backup)
        print(f'Backup: {backup}')

    data = bytearray(exe_path.read_bytes())

    crlf_fixes = _fix_crlf_rsa_blocks(data)
    rsa_hits = _replace_all(data, OLD_RSA, new_rsa_padded, 'RSA key')
    ip_pair_hits = _replace_all(data, OLD_IP_PAIR, NEW_IP_PAIR, 'DC IP pair')
    ip_slot_hits = _replace_all(data, OLD_IP_SLOT, NEW_IP_SLOT, 'DC IP slot')

    if rsa_hits == 0 and crlf_fixes == 0 and data.count(new_rsa_padded[:80]) < 2:
        raise SystemExit('RSA key block not found in exe (already patched or different build?)')

    if ip_pair_hits == 0 and ip_slot_hits == 0:
        if data.count(b'192.168.100.14') >= 8 and data.count(b'192.168.100.1') == 0:
            print('DC IP already patched.')
        else:
            print(f'Warning: partial IP patch (.14={data.count(b"192.168.100.14")}, .1={data.count(b"192.168.100.1")})')

    exe_path.write_bytes(data)
    print(f'Patched: {exe_path}')
    print(f'  RSA SPKI->PKCS1: {rsa_hits}')
    print(f'  RSA CRLF fix:    {crlf_fixes}')
    print(f'  DC IP pairs:     {ip_pair_hits}')
    print(f'  DC IP slots:     {ip_slot_hits}')
    print(f'  Remaining .1:    {data.count(b"192.168.100.1")}')
    print('Close Telegram completely, then start it again.')


def main() -> None:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXE
    patch_exe(exe)


if __name__ == '__main__':
    main()
