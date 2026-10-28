"""Verify the server's fakeTLS (dd-secret) transport end to end.

Speaks the MTProto-proxy fakeTLS camouflage on the wire: TLS 1.3 ClientHello
with the HMAC tag and SNI, ServerHello validation, then the ordinary
obfuscated MTProto handshake (req_pq -> resPQ) inside TLS application-data
records.  Success = resPQ received and digest-verified.

Usage: python3 probe_faketls.py [host] [port] [secret_line]
"""
import hashlib
import hmac
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mtproto import tl, crypto
from mtproto.transport import PacketBuffer
from probe_client import Transport, build_init_frame
from config import PRIVATE_KEY_PATH
from cryptography.hazmat.primitives import serialization

SNI = 'projectre.work'


def load_secret(line: str) -> bytes:
    line = line.strip()
    assert line[:2] == 'dd' and len(line) >= 34, 'bad secret line'
    core = bytes.fromhex(line[2:34])
    return core


def read_record(sock) -> tuple[int, bytes]:
    header = b''
    while len(header) < 5:
        chunk = sock.recv(5 - len(header))
        if not chunk:
            raise ConnectionError('closed while reading record header')
        header += chunk
    rec_type = header[0]
    rec_len = struct.unpack('>H', header[3:5])[0]
    payload = b''
    while len(payload) < rec_len:
        chunk = sock.recv(rec_len - len(payload))
        if not chunk:
            raise ConnectionError('closed while reading record body')
        payload += chunk
    return rec_type, payload


def wrap_record(payload: bytes) -> bytes:
    out = bytearray()
    for off in range(0, len(payload), 16379):
        chunk = payload[off:off + 16379]
        out += b'\x17\x03\x03' + struct.pack('>H', len(chunk)) + chunk
    return bytes(out)


def build_client_hello(core: bytes, sni: str) -> bytes:
    body = bytearray()
    body += b'\x03\x03'                       # client_version TLS 1.2
    random_field = bytearray(32)
    body += random_field                      # placeholder for the tag
    session_id = os.urandom(32)
    body += bytes([len(session_id)]) + session_id
    cipher_suites = b'\x13\x01\x13\x02\x13\x03\xc0\x2f\xc0\x2b'
    body += struct.pack('>H', len(cipher_suites)) + cipher_suites
    body += b'\x01\x00'                       # compression: null

    sni_bytes = sni.encode()
    ext_data = struct.pack('>H', len(sni_bytes) + 3) + b'\x00' + \
        struct.pack('>H', len(sni_bytes)) + sni_bytes
    extensions = struct.pack('>H', 0x0000) + struct.pack('>H', len(ext_data)) + \
        ext_data
    extensions += struct.pack('>H', 0x000b) + struct.pack('>H', 4) + \
        b'\x00\x01\x00\x02'                   # ec_point_formats
    body += struct.pack('>H', len(extensions)) + extensions

    hs = bytearray(b'\x01') + struct.pack('>I', len(body))[1:] + body
    record = bytearray(b'\x16\x03\x01') + struct.pack('>H', len(hs)) + hs

    digest = hmac.new(core, digestmod=hashlib.sha256)
    digest.update(bytes(record[:11]))
    digest.update(b'\x00' * 32)
    digest.update(bytes(record[43:]))
    tag = digest.digest()
    ts_bytes = struct.pack('<I', int(time.time()))
    client_random = tag[:28] + bytes(
        a ^ b for a, b in zip(tag[28:32], ts_bytes))
    record[11:43] = client_random
    return bytes(record)


def verify_server_hello(records: bytes, core: bytes, client_random: bytes):
    assert records[0] == 0x16, 'first record must be ServerHello'
    # ServerHello random is the tag itself: HMAC is computed over the
    # records with that field zeroed.
    tag = hmac.new(core, digestmod=hashlib.sha256)
    tag.update(client_random)
    tag.update(records[:11] + b'\x00' * 32 + records[43:])
    got = records[11:43]
    assert got == tag.digest(), 'ServerHello HMAC mismatch'


class TlsSock:
    """Socket adapter: TLS record framing over a raw socket."""

    def __init__(self, sock):
        self.sock = sock
        self._buf = b''

    def sendall(self, data):
        self.sock.sendall(wrap_record(data))

    def recv(self, n):
        while not self._buf:
            _, payload = read_record(self.sock)
            if payload:
                self._buf = payload
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def settimeout(self, t):
        self.sock.settimeout(t)


def build_init_frame_secret(core: bytes) -> bytes:
    """Init frame whose tail is obfuscated with the secret-keyed CTR."""
    frame = bytearray(os.urandom(64))
    struct.pack_into('<Ih', frame, 56, 0xEFEFEFEF, 2)
    key = crypto.sha256(bytes(frame[8:40]) + core)
    iv = bytes(frame[40:56])
    ctr = crypto.AesCtr(key, iv)
    ks = ctr.process(b'\x00' * 64)
    frame[56:64] = bytes(a ^ b for a, b in zip(frame[56:64], ks[56:64]))
    return bytes(frame), ctr


def secret_ctrs(init_frame: bytes, core: bytes):
    def k(src):
        return crypto.sha256(src + core)
    send = crypto.AesCtr(k(init_frame[8:40]), init_frame[40:56])
    rev = bytes(reversed(init_frame[8:56]))
    recv = crypto.AesCtr(k(rev[0:32]), rev[32:48])
    send.process(b'\x00' * 64)
    return send, recv


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2] if len(sys.argv) > 2 else 443)
    secret_line = sys.argv[3] if len(sys.argv) > 3 else \
        open('proxy_secret.txt').read().splitlines()[0]
    core = load_secret(secret_line)

    sock = socket.create_connection((host, port), timeout=10)
    hello = build_client_hello(core, SNI)
    sock.sendall(hello)
    client_random = hello[11:43]

    records = b''
    while True:
        rec_type, payload = read_record(sock)
        records += bytes([rec_type]) + b'\x03\x03' + \
            struct.pack('>H', len(payload)) + payload
        if rec_type == 0x17:   # noise record = handshake complete
            break
    verify_server_hello(records, core, client_random)
    print('[ok] fakeTLS handshake verified (ServerHello HMAC)')

    init_frame, send_ctr = build_init_frame_secret(core)
    adapter = TlsSock(sock)
    adapter.sendall(init_frame)

    t = Transport(adapter)
    t.send_ctr = send_ctr
    _, t.recv_ctr = secret_ctrs(init_frame, core)
    t.salt = 0

    nonce = os.urandom(16)
    w = tl.Writer()
    w.u32(0x60469778)  # req_pq
    w.raw(nonce)
    resp = t.exchange_unencrypted(w.bytes_out())
    r = tl.Reader(resp)
    cid = r.u32()
    assert cid == 0x05162463, f'resPQ expected, got {cid:#010x}'
    print('[ok] resPQ received over fakeTLS: transport works')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
