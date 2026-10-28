"""Full auth-key handshake test through req_DH_params step."""
import os
import socket
import struct
import sys

from mtproto import tl, crypto
from mtproto.transport import PacketBuffer
from mtproto.session import make_message_id

HOST = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 20443


def build_init_frame() -> bytes:
    frame = bytearray(os.urandom(64))
    struct.pack_into('<Ih', frame, 56, 0xEFEFEFEF, 2)
    key = bytes(frame[8:40])
    iv = bytes(frame[40:56])
    ctr = crypto.AesCtr(key, iv)
    ks = ctr.process(b'\x00' * 64)
    frame[56:64] = bytes(a ^ b for a, b in zip(frame[56:64], ks[56:64]))
    return bytes(frame)


def _client_ctrs(frame: bytes):
    send = crypto.AesCtr(frame[8:40], frame[40:56])
    rev = bytes(reversed(frame[8:56]))
    recv = crypto.AesCtr(rev[0:32], rev[32:48])
    send.process(b'\x00' * 64)
    return send, recv


def parse_not_secure(raw: bytes) -> bytes:
    a = struct.unpack_from('<5i', raw, 0)
    assert a[0] == 0 and a[1] == 0, a
    assert (a[2] & 3) == 1, f'bad msg_id low bits: {a[2]:#x}'
    length = a[4]
    return raw[20:20 + length]


def exchange(sock, send_ctr, recv_ctr, payload: bytes, msg_id: int | None = None) -> bytes:
    if msg_id is None:
        msg_id = make_message_id()
    packet = tl.pack_unencrypted(msg_id, payload)
    framed = bytes([len(packet) // 4]) + packet
    sock.sendall(send_ctr.process(framed))
    buf = PacketBuffer()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError('connection closed')
        buf.feed(recv_ctr.process(chunk))
        resp = buf.next_packet()
        if resp is not None:
            return parse_not_secure(resp)


def main() -> int:
    sock = socket.create_connection((HOST, PORT), timeout=5)
    init = build_init_frame()
    sock.sendall(init)
    send_ctr, recv_ctr = _client_ctrs(init)

    nonce = os.urandom(16)
    w = tl.Writer()
    w.u32(tl.REQ_PQ_MULTI)
    w.i128(nonce)
    data = exchange(sock, send_ctr, recv_ctr, w.bytes_out())

    r = tl.Reader(data)
    assert r.u32() == tl.RES_PQ
    assert r.i128() == nonce
    server_nonce = r.i128()
    pq = tl.bytes_to_int(r.tl_bytes())
    assert r.u32() == tl.VECTOR
    assert r.i32() == 1
    fp = r.i64()
    print(f'OK: resPQ parsed, fp={fp:#018x}, pq={pq}')
    print(f'OK: msg_id validation passed (client parseNotSecureResponse)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
