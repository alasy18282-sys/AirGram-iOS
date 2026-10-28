"""
Raw MTProto probe client: full handshake + encrypted RPC against our server.

Used to verify what the running server actually replies to the first
encrypted RPC (help.getConfig / auth.sendCode), byte-for-byte.

Usage:
  python3 probe_client.py [host] [port] [mode] [--layer N] [--api-id N] [--phone X]

Modes:
  config   (default) invokeWithLayer + initConnection + help.getConfig
  plain    bare help.getConfig (no wrappers)
  sendcode auth.sendCode for --phone (requires --phone)
"""
import argparse
import gzip
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

from mtproto import tl, crypto
from mtproto.transport import PacketBuffer
from config import DH_PRIME, DH_PRIME_BYTES, DH_G, PRIVATE_KEY_PATH

CID_NAMES = {
    0x9ec20908: 'newSessionCreated',
    0xf35c6d01: 'rpc_result',
    0x2144ca19: 'rpc_error',
    0x73f1f8dc: 'msg_container',
    0x62d6b459: 'msgs_ack',
    0xa7eff811: 'bad_msg_notification',
    0xedab447b: 'bad_server_salt',
    0x347773c5: 'pong',
    0xae5008d5: 'msgs_state_info',
    0x4afe8ad2: 'future_salts',
    0xda9b74e0: 'config',
}

BRIEF = {
    0x05162463: 'resPQ',
    0xd0e8075c: 'server_DH_params_ok',
    0x3bcbf734: 'dh_gen_ok',
}


def cid_name(cid: int) -> str:
    return CID_NAMES.get(cid) or BRIEF.get(cid) or f'cid_{cid:#010x}'


# ── transport helpers ──────────────────────────────────────────────

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


class Transport:
    def __init__(self, sock):
        self.sock = sock
        self.send_ctr = None
        self.recv_ctr = None
        self.buf = PacketBuffer()
        self._msg_seq = 0

    def start(self):
        init = build_init_frame()
        self.sock.sendall(init)
        self.send_ctr, self.recv_ctr = _client_ctrs(init)

    def _next_msg_id(self) -> int:
        self._msg_seq += 4
        return (int(time.time()) << 32) | (self._msg_seq & 0xFFFFFFFF)

    # unencrypted
    def exchange_unencrypted(self, payload: bytes) -> bytes:
        packet = tl.pack_unencrypted(self._next_msg_id(), payload)
        frame = bytes([len(packet) // 4]) + packet
        self.sock.sendall(self.send_ctr.process(frame))
        resp = self._read_payload()
        _, _, data = tl.unpack_unencrypted(resp)
        return data

    # encrypted
    def send_encrypted(self, body: bytes, seq_no: int) -> int:
        msg_id = self._next_msg_id()
        if not hasattr(self, 'session_id'):
            self.session_id = struct.unpack('<q', os.urandom(8))[0]
        inner = tl.Writer()
        inner.i64(int(getattr(self, 'salt', 0) or 0))  # current server salt
        inner.i64(self.session_id)
        inner.i64(msg_id)
        inner.i32(seq_no)
        inner.i32(len(body))
        inner.raw(body)
        plaintext = inner.bytes_out()
        pad_len = 12 + (16 - (len(plaintext) + 12) % 16) % 16
        plaintext += os.urandom(pad_len)
        msg_key = crypto.compute_msg_key(self.auth_key, plaintext, from_client=True)
        aes_key, aes_iv = crypto.derive_message_key(self.auth_key, msg_key, from_client=True)
        cipher = crypto.aes_ige_encrypt(plaintext, aes_key, aes_iv)
        packet = struct.pack('<q', self.auth_key_id) + msg_key + cipher
        self.sock.sendall(self.send_ctr.process(self.buf.pack_packet(packet)))
        return msg_id

    def _read_payload(self, timeout: float = 8.0) -> bytes:
        self.sock.settimeout(timeout)
        deadline = time.time() + timeout
        while True:
            pkt = self.buf.next_packet()
            if pkt is not None:
                return pkt
            if time.time() > deadline:
                raise TimeoutError('no packet from server')
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError('server closed connection')
            self.buf.feed(self.recv_ctr.process(chunk))

    def read_encrypted(self, timeout: float = 8.0) -> dict:
        """Read until an encrypted packet, decrypt, return parsed dict."""
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError('timeout waiting for encrypted reply')
            payload = self._read_payload(timeout=remaining)
            if len(payload) == 4:
                raise ConnectionError(f'server error packet {struct.unpack("<i", payload)[0]}')
            if len(payload) < 24:
                raise ConnectionError(f'short payload {len(payload)}b: {payload.hex()}')
            auth_key_id = struct.unpack_from('<q', payload, 0)[0]
            if auth_key_id != self.auth_key_id:
                raise ConnectionError(f'unexpected auth_key_id {auth_key_id:#x}')
            msg_key = payload[8:24]
            cipher = payload[24:]
            aes_key, aes_iv = crypto.derive_message_key(
                self.auth_key, msg_key, from_client=False)
            plain = crypto.aes_ige_decrypt(cipher, aes_key, aes_iv)
            salt, session_id, msg_id, seq_no, msg_len = struct.unpack_from('<qqqii', plain, 0)
            body = plain[32:32 + msg_len]
            expected = crypto.compute_msg_key(self.auth_key, plain, from_client=False)
            return {
                'msg_key_ok': expected == msg_key,
                'msg_id': msg_id,
                'seq': seq_no,
                'salt': salt,
                'body': body,
            }


# ── handshake ──────────────────────────────────────────────────────

def handshake(t: Transport) -> bytes:
    nonce = os.urandom(16)
    w = tl.Writer()
    w.u32(tl.REQ_PQ_MULTI)
    w.i128(nonce)
    data = t.exchange_unencrypted(w.bytes_out())

    r = tl.Reader(data)
    assert r.u32() == tl.RES_PQ, 'expected resPQ'
    assert r.i128() == nonce
    server_nonce = r.i128()
    pq = tl.bytes_to_int(r.tl_bytes())
    assert r.u32() == tl.VECTOR
    n_keys = r.i32()
    fp = r.i64()
    print(f'[hs] resPQ: pq={pq} fp={fp:#018x} ({n_keys} key)')

    import sympy
    factors = sympy.factorint(pq)
    primes = sorted(int(p_) for p_ in factors)
    assert len(primes) == 2, f'pq not semiprime: {factors}'
    p_val, q_val = primes[0], primes[1]
    print(f'[hs] factored pq = {p_val} * {q_val}')

    new_nonce = os.urandom(32)

    # p_q_inner_data wrapped for MTProto 2.0 (v2 RSA pad), payload == 192 B
    inner = tl.Writer()
    inner.u32(tl.P_Q_INNER_DATA)
    inner.tl_bytes(tl.int_to_bytes(pq))
    inner.tl_bytes(tl.int_to_bytes(p_val))
    inner.tl_bytes(tl.int_to_bytes(q_val))
    inner.i128(nonce)
    inner.i128(server_nonce)
    inner.i256(new_nonce)
    payload = inner.bytes_out()
    assert len(payload) <= 192, len(payload)
    payload = payload + os.urandom(192 - len(payload))

    pub = serialization.load_pem_private_key(
        open(PRIVATE_KEY_PATH, 'rb').read(), password=None,
        backend=default_backend()).public_key().public_numbers()
    encrypted = None
    while encrypted is None:   # raw RSA needs m < n — retry with fresh aes key
        temp_key = os.urandom(32)
        data_with_hash = bytes(reversed(payload)) + crypto.sha256(temp_key + payload)
        aes_encrypted = crypto.aes_ige_encrypt(data_with_hash, temp_key, bytes(32))
        temp_key_xor = crypto.xor_bytes(temp_key, crypto.sha256(aes_encrypted))
        block = temp_key_xor + aes_encrypted
        assert len(block) == 256
        m = int.from_bytes(block, 'big')
        if m < pub.n:
            encrypted = pow(m, pub.e, pub.n).to_bytes(256, 'big')

    w = tl.Writer()
    w.u32(tl.REQ_DH_PARAMS)
    w.i128(nonce)
    w.i128(server_nonce)
    w.tl_bytes(tl.int_to_bytes(p_val))
    w.tl_bytes(tl.int_to_bytes(q_val))
    w.i64(fp)
    w.tl_bytes(encrypted)
    data = t.exchange_unencrypted(w.bytes_out())

    r = tl.Reader(data)
    cid = r.u32()
    assert cid == tl.SERVER_DH_PARAMS_OK, f'got {cid:#010x} (dh params fail?)'
    assert r.i128() == nonce and r.i128() == server_nonce
    encrypted_answer = r.tl_bytes()

    h1 = crypto.sha1(new_nonce + server_nonce)
    h2 = crypto.sha1(server_nonce + new_nonce)
    h3 = crypto.sha1(new_nonce + new_nonce)
    key = h1 + h2[:12]
    iv = h2[12:] + h3 + new_nonce[:4]
    plain = crypto.aes_ige_decrypt(encrypted_answer, key, iv)
    r = tl.Reader(plain[20:])
    assert r.u32() == tl.SERVER_DH_INNER_DATA
    assert r.i128() == nonce and r.i128() == server_nonce
    g = r.i32()
    dh_prime = tl.bytes_to_int(r.tl_bytes())
    g_a = tl.bytes_to_int(r.tl_bytes())
    server_time = r.i32()

    assert dh_prime == DH_PRIME, 'dh_prime mismatch'
    assert g == DH_G, f'g mismatch {g}'
    assert crypto.is_good_ga_or_gb(g_a, dh_prime), 'bad g_a'
    print(f'[hs] server_DH_inner_data ok, g_a bits={g_a.bit_length()}, time drift={server_time - int(time.time())}s')

    b = crypto.dh_gen_private()
    g_b = pow(g, b, dh_prime)
    assert crypto.is_good_ga_or_gb(g_b, dh_prime), 'bad g_b'

    inner = tl.Writer()
    inner.u32(tl.CLIENT_DH_INNER_DATA)
    inner.i128(nonce)
    inner.i128(server_nonce)
    inner.i64(0)
    inner.tl_bytes(tl.int_to_bytes(g_b))
    inner_raw = inner.bytes_out()
    data_with_hash = crypto.sha1(inner_raw) + inner_raw
    pad = (16 - len(data_with_hash) % 16) % 16
    data_with_hash += os.urandom(pad)
    encrypted_dh = crypto.aes_ige_encrypt(data_with_hash, key, iv)

    w = tl.Writer()
    w.u32(tl.SET_CLIENT_DH_PARAMS)
    w.i128(nonce)
    w.i128(server_nonce)
    w.tl_bytes(encrypted_dh)
    data = t.exchange_unencrypted(w.bytes_out())

    r = tl.Reader(data)
    cid = r.u32()
    assert r.i128() == nonce and r.i128() == server_nonce
    if cid == tl.DH_GEN_RETRY:
        raise RuntimeError('dh_gen_retry')
    if cid == tl.DH_GEN_FAIL:
        raise RuntimeError('dh_gen_fail')
    assert cid == tl.DH_GEN_OK, f'expected dh_gen_ok, got {cid:#010x}'
    nn_hash = r.i128()

    auth_key = pow(g_a, b, dh_prime).to_bytes(256, 'big')
    expected = crypto.sha1(new_nonce + b'\x01' + crypto.sha1(auth_key)[:8])[-16:]
    assert nn_hash == expected, 'new_nonce_hash1 mismatch'
    print(f'[hs] dh_gen_ok, auth_key_id={crypto.auth_key_id(auth_key):#018x}')
    t.auth_key = auth_key
    t.auth_key_id = crypto.auth_key_id(auth_key)
    return auth_key


# ── RPC bodies ─────────────────────────────────────────────────────

def build_get_config(layer: int, api_id: int, mode: str) -> bytes:
    if mode == 'plain':
        w = tl.Writer()
        w.u32(tl.HELP_GET_CONFIG)
        return w.bytes_out()

    w = tl.Writer()
    w.u32(tl.INIT_CONNECTION)
    w.i32(0)          # flags
    w.i32(api_id)
    w.string('SM-A725F')       # device_model (Android-ish)
    w.string('13')             # system_version
    w.string('12.7.0 (67402)') # app_version
    w.string('en')             # system_lang_code
    w.string('')               # lang_pack
    w.string('en')             # lang_code
    w.u32(tl.HELP_GET_CONFIG)

    inner = w.bytes_out()
    w = tl.Writer()
    w.u32(tl.INVOKE_WITH_LAYER)
    w.i32(layer)
    w.raw(inner)
    return w.bytes_out()


def build_string(s: str) -> bytes:
    return tl.Writer().string(s).bytes_out()


# ── result parsing ─────────────────────────────────────────────────

def parse_result(body: bytes) -> bytes:
    if body[:2] == b'\x1f\x8b':
        body = gzip.decompress(body)
        print('[rpc] response was gzipped')
    r = tl.Reader(body)
    cid = r.u32()
    if cid == tl.RPC_RESULT:
        req_msg_id = r.i64()
        result = body[r.pos:]
        print(f'[rpc] rpc_result for req_msg_id={req_msg_id:#018x} ({len(result)}B)')
        return result
    if cid == 0x2144ca19:  # rpc_error inline
        code = r.i32()
        msg = r.string()
        raise RuntimeError(f'rpc_error {code}: {msg}')
    print(f'[rpc] unexpected body cid={cid:#010x} hex={body[:64].hex()}')
    return body


def dump(label: str, result: bytes) -> None:
    print(f'--- {label}: {len(result)} bytes ---')
    print(result[:96].hex())
    if b'apv.stel.com' in result:
        print('!! FOUND "apv.stel.com" IN REPLY')
    # look for our ip / official ips
    for needle in (b'84.21.173.237', b'149.154.167.50', b'2001:0678:'):
        if needle in result:
            print(f'!! reply contains {needle!r}')
    r = tl.Reader(result)
    cid = r.u32()
    print(f'constructor={cid:#010x}')
    if cid == 0xda9b74e0:  # config
        flags = r.i32()
        print(f'config flags={flags:#x}')
        api_id = r.i32()
        print(f'api_id={api_id}')
        if flags & 4:
            r.i32()   # test_mode
        r.i32()       # this_port
        r.string()    # this_dc_address...
        n_dc = r.i32()
        print(f'dc_options count={n_dc}')
        try:
            for i in range(n_dc):
                df = r.i32()
                dc_id = r.i32()
                ip = r.string()
                port = r.i32()
                print(f'  dc{dc_id}: {ip}:{port} flags={df:#x}')
                if df & 0x4000:
                    print(f'    has secret={r.tl_bytes().hex()[:16]}...')
                if df & 0x80000000:
                    print(f'    has static flags')
                if df & 0x40000000:
                    print(f'    has ip-version flag? bits...')
                if df & 0x1000:
                    print(f'    obfuscated')
        except Exception as exc:
            print(f'  !! parse stopped: {exc} (consumed {r.pos}/{len(result)})')
            print(f'  remaining hex: {result[r.pos:r.pos+64].hex()}')
    print(f'full hex:\n{result.hex()}')


# ── main ───────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('host', nargs='?', default='127.0.0.1')
    ap.add_argument('port', nargs='?', type=int, default=20443)
    ap.add_argument('mode', nargs='?', default='config',
                    choices=['config', 'plain'])
    ap.add_argument('--layer', type=int, default=225)
    ap.add_argument('--api-id', type=int, default=4)
    ap.add_argument('--timeout', type=float, default=8.0)
    args = ap.parse_args()

    print(f'== probe {args.host}:{args.port} mode={args.mode} '
          f'layer={args.layer} api_id={args.api_id} ==')
    sock = socket.create_connection((args.host, args.port), timeout=5)
    t = Transport(sock)
    t.start()
    handshake(t)

    body = build_get_config(args.layer, args.api_id, args.mode)
    seq = 1
    msg_id = t.send_encrypted(body, seq)
    print(f'[rpc] sent {args.mode} getConfig layer={args.layer} '
          f'api_id={args.api_id} msg_id={msg_id:#018x}')

    deadline = time.time() + args.timeout
    got_result = False
    while time.time() < deadline:
        try:
            pkt = t.read_encrypted(timeout=deadline - time.time())
        except TimeoutError:
            break
        r = tl.Reader(pkt['body'])
        cid = r.u32()
        print(f'[recv] {cid_name(cid)} msg_id={pkt["msg_id"]:#018x} '
          f'seq={pkt["seq"]} key_ok={pkt["msg_key_ok"]} len={len(pkt["body"])}')
        if cid == tl.MSG_CONTAINER:
            count = r.i32()
            for _ in range(count):
                i_id = r.i64()
                i_seq = r.i32()
                i_len = r.i32()
                i_body = r.read(i_len)
                ir = tl.Reader(i_body)
                i_cid = ir.u32()
                print(f'  [in-container] {cid_name(i_cid)} msg_id={i_id:#018x} seq={i_seq}')
                if i_cid == tl.RPC_RESULT:
                    dump(args.mode, parse_result(i_body))
                    got_result = True
        elif cid == tl.RPC_RESULT:
            dump(args.mode, parse_result(pkt['body']))
            got_result = True
        if got_result:
            break

    if not got_result:
        print('!! NO RPC_RESULT within timeout')
        return 2
    print('PROBE OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
