"""Minimal MTProto TCP fake-req_pq test (matches Telegram Desktop on connect)."""
import os
import socket
import struct
import sys

from mtproto import tl, crypto
from mtproto.transport import PacketBuffer

HOST = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 20443


def build_init_frame() -> bytes:
    frame = bytearray(os.urandom(64))
    tail = struct.pack('<Ih', 0xEFEFEFEF, 2)
    key = bytes(frame[8:40])
    iv = bytes(frame[40:56])
    ctr = crypto.AesCtr(key, iv)
    ks = ctr.process(b'\x00' * 64)
    enc_tail = bytes(a ^ b for a, b in zip(tail + os.urandom(2), ks[56:64]))
    frame[56:64] = enc_tail
    return bytes(frame)


def client_send(sock, frame: bytes, packet: bytes):
    key = frame[8:40]
    iv = frame[40:56]
    ctr = crypto.AesCtr(key, iv)
    ctr.process(b'\x00' * 64)
    framed = bytes([len(packet) // 4]) + packet
    sock.sendall(ctr.process(framed))


def client_recv(sock, frame: bytes, buf: PacketBuffer) -> bytes | None:
    rev = bytes(reversed(frame[8:56]))
    ctr = crypto.AesCtr(rev[0:32], rev[32:48])
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buf.feed(ctr.process(chunk))
        resp = buf.next_packet()
        if resp is not None:
            return resp


def main() -> int:
    sock = socket.create_connection((HOST, PORT), timeout=5)
    init = build_init_frame()
    sock.sendall(init)

    nonce = os.urandom(16)
    payload = tl.Writer()
    payload.u32(tl.REQ_PQ)
    payload.i128(nonce)
    packet = tl.pack_unencrypted(0x1234567890ABCDEF, payload.bytes_out())
    client_send(sock, init, packet)

    resp = client_recv(sock, init, PacketBuffer())
    if resp is None:
        print('No response from server')
        return 1

    _, _, data = tl.unpack_unencrypted(resp)
    r = tl.Reader(data)
    cid = r.u32()
    if cid != tl.RES_PQ:
        print(f'Unexpected response: {cid:#010x}')
        return 1
    if r.i128() != nonce:
        print('Nonce mismatch in resPQ')
        return 1
    print(f'OK: resPQ received from {HOST}:{PORT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
