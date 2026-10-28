"""Generate MTProto proxy secret for local server + client setup."""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_FILE = ROOT / 'proxy_secret.txt'
HOST = '192.168.100.14'
PORT = 20443


def main() -> None:
    secret = secrets.token_bytes(16)
    hex_secret = secret.hex()
    SECRET_FILE.write_text(
        f'{hex_secret}  # auto-generated, restart server after change\n',
        encoding='utf-8',
    )
    print('Secret saved to proxy_secret.txt')
    print(f'Hex (32 chars, paste into Telegram proxy password): {hex_secret}')
    print()
    print('Telegram proxy settings:')
    print(f'  Type: MTProto')
    print(f'  Server: {HOST}')
    print(f'  Port: {PORT}')
    print(f'  Password: {hex_secret}')
    print()
    print(f'tg://proxy?server={HOST}&port={PORT}&secret={hex_secret}')
    print()
    print('Restart run_server.bat after this, then restart Telegram.')


if __name__ == '__main__':
    main()
