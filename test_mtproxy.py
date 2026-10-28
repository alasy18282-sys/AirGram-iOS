"""Test MTProto through MTProxy obfuscation (Version1 / _obf)."""
import os
import socket
import struct
import sys

from mtproto import tl, crypto
from mtproto.crypto import sha256, AesCtr
from mtproto.transport import PacketBuffer

HOST = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 20443
SECRET = bytes.fromhex(sys.argv[3]) if len(sys.argv) > 3 else b''


def _derive_key(source: bytes) -> bytes:
    if not SECRET:
        return source
    return sha256(source + SECRET)


def build_init_frame() -> bytes:
    frame = bytearray(os.urandom(64))
    struct.pack_into('<Ih', frame, 56, 0xEFEFEFEF, 2)
    send_key = _derive_key(bytes(frame[8:40]))
    send_iv = bytes(frame[40:56])
    ctr = AesCtr(send_key, send_iv)
    ks = ctr.process(b'\x00' * 64)
    frame[56:64] = bytes(a ^ b for a, b in zip(frame[56:64], ks[56:64]))
    return bytes(frame)


def _streams(frame: bytes):
    send_key = _derive_key(frame[8:40])
    send_iv = frame[40:56]
    rev = bytes(reversed(frame[8:56]))
    recv_key = _derive_key(rev[0:32])
    recv_iv = rev[32:48]
    send_ctr = AesCtr(send_key, send_iv)
    recv_ctr = AesCtr(recv_key, recv_iv)
    send_ctr.process(b'\x00' * 64)
    return send_ctr, recv_ctr


def main() -> int:
    sock = socket.create_connection((HOST, PORT), timeout=5)
    init = build_init_frame()
    sock.sendall(init)
    send_ctr, recv_ctr = _streams(init)

    nonce = os.urandom(16)
    payload = tl.Writer()
    payload.u32(tl.REQ_PQ)
    payload.i128(nonce)
    packet = tl.pack_unencrypted(0x1234567890ABCDEF, payload.bytes_out())
    framed = bytes([len(packet) // 4]) + packet
    sock.sendall(send_ctr.process(framed))

    buf = PacketBuffer()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            print('No response')
            return 1
        buf.feed(recv_ctr.process(chunk))
        resp = buf.next_packet()
        if resp is not None:
            break

    _, _, data = tl.unpack_unencrypted(resp)
    r = tl.Reader(data)
    if r.u32() != tl.RES_PQ or r.i128() != nonce:
        print('Bad resPQ')
        return 1
    mode = 'direct' if not SECRET else f'proxy secret={SECRET.hex()}'
    print(f'OK: resPQ via {mode} from {HOST}:{PORT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
