"""Login as QA sender (888907) and message recipient 888906.

Used to verify instant server-side delivery/push between two accounts.
Usage: python3 probe_delivery.py [host] [port]
"""
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mtproto import tl
from probe_client import Transport, handshake
import probe_layer228 as p

PHONE = '15555550123'
CODE = '71977'
CODE_HASH = '31b94d7f776d301c0fac61acd13fee31'
PEER_ID = 888906
PEER_HASH = 1383722229971788417


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 443
    c = p.Client(host, port)

    w = tl.Writer(); w.u32(tl.HELP_GET_CONFIG)
    c.call(p.init_connection(w.bytes_out()), 'help.getConfig')
    print('[ok] config')

    w = tl.Writer()
    w.u32(tl.AUTH_SIGN_IN)
    w.i32(1)
    w.string('+' + PHONE)
    w.string(CODE_HASH)
    w.string(CODE)
    uid = p.parse_authorization(
        c.call(p.wrap_layer(w.bytes_out()), 'auth.signIn'), 'auth.signIn')
    if not uid:
        print('sign-in failed')
        return 1
    print('[ok] signed in as', uid)

    w = tl.Writer()
    w.u32(tl.MESSAGES_SEND_MESSAGE_V227)
    w.i32(0)
    w.u32(tl.INPUT_PEER_USER)
    w.i64(PEER_ID)
    w.i64(PEER_HASH)
    w.string('instant delivery probe')
    w.i64(int.from_bytes(os.urandom(8), 'little', signed=True))
    res = c.call(p.wrap_layer(w.bytes_out()), 'messages.sendMessage')
    print('[ok] sent, response bytes', len(res))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
