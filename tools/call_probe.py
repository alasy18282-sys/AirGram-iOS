"""End-to-end phone call signalling probe.

Connects two raw MTProto clients using existing auth keys from the DB
(caller 888906, callee 888913) and walks the full call handshake:
getDhConfig -> requestCall -> requested push -> acceptCall -> confirmCall ->
final push -> signaling relay -> discardCall.  Verifies what the server
actually pushes over the wire, independent of the Android UI.
"""
import gzip
import hashlib
import os
import socket
import sqlite3
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtproto import tl, crypto
from probe_client import Transport, parse_result

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'mytelegram.db')

PHONE_REQUEST_CALL = 0x42ff96ed
PHONE_RECEIVED_CALL = 0x17d54f61
PHONE_ACCEPT_CALL = 0x3bd2b4a0
PHONE_CONFIRM_CALL = 0x2efe1722
PHONE_SEND_SIGNALING = 0xff7a9383
PHONE_DISCARD_CALL = 0xb2cbc1c0
MESSAGES_GET_DH_CONFIG = 0x26cf8950

INPUT_USER = 0xf21158c6
INPUT_PHONE_CALL = 0x1e36fded
PHONE_PROTOCOL = 0xfc878fc8
DATA_JSON = 0x7d748d04

PC_WAITING = 0xc5226f17
PC_REQUESTED = 0x14b0ed0c
PC_ACCEPTED = 0x3660c311
PC_FINAL = 0x30535af5
PC_DISCARDED = 0x50ca4de1
CONN_WEBRTC = 0x635fe375
CONN_TCP = 0x9cc123c7
UPDATE_PHONE_CALL = 0xab0f6b1e
UPDATE_SIGNALING = 0x2661bf09
RPC_RESULT = 0xf35c6d01
RPC_ERROR = 0x2144ca19
BAD_SALT = 0xedab447b
MSG_CONTAINER = 0x73f1f8dc
NEW_SESSION = 0x9ec20908

DISCARD_HANGUP = 0x57adc690


def load_auth_key(user_id, avoid=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT s.auth_key_id AS kid, a.auth_key AS key '
        'FROM sessions s JOIN auth_keys a ON a.auth_key_id=s.auth_key_id '
        'WHERE s.user_id=? ORDER BY s.created_at DESC', (user_id,)).fetchall()
    conn.close()
    for row in rows:
        kid = int(row['kid'])
        if kid in avoid:
            continue
        key = bytes(row['key'])
        if crypto.auth_key_id(key) != kid:
            continue
        return kid, key
    raise RuntimeError(f'no usable auth key for {user_id}')


def user_access_hash(user_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute('SELECT access_hash FROM users WHERE id=?',
                       (user_id,)).fetchone()
    conn.close()
    if not row:
        raise RuntimeError(f'user {user_id} not found')
    return int(row[0])


def pack_protocol(w, protocol=None):
    proto = protocol or {}
    flags = int(proto.get('flags', 0x3)) or 0x3   # udp_p2p | udp_reflector
    min_layer = int(proto.get('min_layer') or 74)
    max_layer = int(proto.get('max_layer') or 92)
    libs = list(proto.get('libs') or ['11.0.0'])
    w.u32(PHONE_PROTOCOL)
    w.i32(flags)
    w.i32(min_layer)
    w.i32(max_layer)
    w.u32(0x1cb5c415)   # vector
    w.i32(len(libs))
    for lib in libs:
        w.string(str(lib))


def parse_protocol(r):
    ctor = r.u32()
    if ctor != PHONE_PROTOCOL:
        raise RuntimeError(f'protocol ctor {ctor:#010x}')
    flags = r.i32()
    min_layer = r.i32()
    max_layer = r.i32()
    if r.u32() != 0x1cb5c415:
        raise RuntimeError('protocol libs vector expected')
    count = r.i32()
    libs = [r.string() for _ in range(max(0, min(count, 50)))]
    return {'flags': flags, 'min_layer': min_layer,
            'max_layer': max_layer, 'libs': libs}


def parse_phone_call(data, off):
    r = tl.Reader(data)
    r.pos = off
    cid = r.u32()
    out = {'cid': cid}
    if cid in (PC_WAITING, PC_REQUESTED, PC_ACCEPTED):
        flags = r.i32()
        out['video'] = bool(flags & 0x40)
        out['id'] = r.i64()
        out['access_hash'] = r.i64()
        out['date'] = r.i32()
        out['admin_id'] = r.i64()
        out['participant_id'] = r.i64()
        if cid == PC_WAITING:
            out['protocol'] = parse_protocol(r)
            if flags & 1:
                out['receive_date'] = r.i32()
        elif cid == PC_REQUESTED:
            out['g_a_hash'] = r.tl_bytes()
            out['protocol'] = parse_protocol(r)
        else:
            out['g_b'] = r.tl_bytes()
            out['protocol'] = parse_protocol(r)
    elif cid == PC_FINAL:
        flags = r.i32()
        out['p2p_allowed'] = bool(flags & 0x20)
        out['video'] = bool(flags & 0x40)
        out['id'] = r.i64()
        out['access_hash'] = r.i64()
        out['date'] = r.i32()
        out['admin_id'] = r.i64()
        out['participant_id'] = r.i64()
        out['g_a_or_b'] = r.tl_bytes()
        out['key_fingerprint'] = r.i64()
        out['protocol'] = parse_protocol(r)
        if r.u32() != 0x1cb5c415:
            raise RuntimeError('connections vector expected')
        count = r.i32()
        conns = []
        for _ in range(max(0, min(count, 20))):
            ccid = r.u32()
            cflags = r.i32()
            cid_ = r.i64()
            ip = r.string()
            ipv6 = r.string()
            port = r.i32()
            if ccid == CONN_WEBRTC:
                conns.append({
                    'kind': 'webrtc', 'turn': bool(cflags & 1),
                    'stun': bool(cflags & 2), 'id': cid_, 'ip': ip,
                    'ipv6': ipv6, 'port': port,
                    'username': r.string(), 'password': r.string(),
                })
            elif ccid == CONN_TCP:
                conns.append({
                    'kind': 'tcp', 'tcp': bool(cflags & 1), 'id': cid_,
                    'ip': ip, 'ipv6': ipv6, 'port': port,
                    'peer_tag': r.tl_bytes(),
                })
            else:
                raise RuntimeError(f'unknown PhoneConnection {ccid:#010x}')
        out['connections'] = conns
        out['start_date'] = r.i32()
        if flags & 0x80:
            dc = r.u32()
            if dc == DATA_JSON:
                out['custom_parameters'] = r.string()
    elif cid == PC_DISCARDED:
        flags = r.i32()
        out['need_rating'] = bool(flags & 4)
        out['need_debug'] = bool(flags & 8)
        out['video'] = bool(flags & 0x40)
        out['id'] = r.i64()
        if flags & 1:
            out['reason'] = r.u32()
        if flags & 2:
            out['duration'] = r.i32()
    else:
        raise RuntimeError(f'unknown PhoneCall {cid:#010x}')
    return out


def find_ctor(data, ctor):
    pat = struct.pack('<I', ctor)
    off = data.find(pat)
    return off


class Probe:
    def __init__(self, name, user_id, avoid_keys, layer=229):
        self.name = name
        self.user_id = int(user_id)
        self.layer = layer
        kid, key = load_auth_key(self.user_id, avoid=avoid_keys)
        sock = socket.create_connection(('127.0.0.1', 443), timeout=10)
        t = Transport(sock)
        t.start()
        t.auth_key = key
        t.auth_key_id = kid
        t.session_id = struct.unpack('<q', os.urandom(8))[0]
        t.salt = 0
        self.t = t
        self.seq = 1
        self.wrapped = False
        self.bound = False
        self.calls = []
        self.signals = []
        print(f'[{name}] auth_key={kid} user={user_id}')

    # ── sending ────────────────────────────────────────────────
    def _wrap_init(self, body):
        w = tl.Writer()
        w.u32(0xc1cd5ea9)  # initConnection
        w.i32(0)
        w.i32(4)           # api_id
        w.string('call-probe')
        w.string('linux')
        w.string('12.10.1 (70389)')
        w.string('en')
        w.string('')
        w.string('en')
        w.raw(body)
        inner = w.bytes_out()
        w = tl.Writer()
        w.u32(tl.INVOKE_WITH_LAYER)  # invokeWithLayer
        w.i32(self.layer)
        w.raw(inner)
        return w.bytes_out()

    def send(self, body, wrap=True):
        if wrap and not self.wrapped:
            body = self._wrap_init(body)
            self.wrapped = True
        mid = self.t.send_encrypted(body, self.seq)
        self.seq += 2
        return mid

    def get_dh_config(self):
        w = tl.Writer()
        w.u32(MESSAGES_GET_DH_CONFIG)
        w.i32(0)
        w.i32(256)
        mid = self.send(w.bytes_out())
        body = self.wait_rpc(mid, 10)
        r = tl.Reader(body)
        cid = r.u32()
        if cid != 0x2c221edd:
            raise RuntimeError(f'dhConfig unexpected {cid:#010x}')
        g = r.i32()
        p = tl.bytes_to_int(r.tl_bytes())
        version = r.i32()
        rand = r.tl_bytes()
        print(f'[{self.name}] dhConfig g={g} bits={p.bit_length()} '
              f'version={version} random={len(rand)}B')
        return g, p

    def _read_bodies(self, timeout):
        pkt = self.t.read_encrypted(timeout=timeout)
        self.t.salt = int(pkt['salt'])
        body = pkt['body']
        r = tl.Reader(body)
        cid = r.u32()
        if cid == MSG_CONTAINER:
            n = r.i32()
            out = []
            for _ in range(max(0, min(n, 100))):
                _mid = r.i64()
                _seq = r.i32()
                ln = r.i32()
                out.append(r.read(ln))
            return out
        return [body]

    def poll(self, timeout):
        """Collect events from one or more reads until timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                bodies = self._read_bodies(max(0.05, deadline - time.time()))
            except TimeoutError:
                break
            for body in bodies:
                self._handle(body)
            if time.time() >= deadline:
                break

    def _handle(self, body):
        if len(body) >= 8 and struct.unpack_from('<I', body, 0)[0] == 0x3072cfa1:
            ln = struct.unpack_from('<i', body, 4)[0]
            return self._handle(gzip.decompress(body[8:8 + ln]))
        r = tl.Reader(body)
        cid = r.u32()
        if cid == RPC_ERROR:
            code = r.i32()
            msg = r.string()
            print(f'[{self.name}] rpc_error {code}: {msg}')
            self.last_error = (code, msg)
            return
        if cid == BAD_SALT:
            print(f'[{self.name}] bad_server_salt -> resend needed')
            return
        if cid == NEW_SESSION:
            return
        if cid == RPC_RESULT:
            req_msg_id = r.i64()
            result = body[r.pos:]
            if (len(result) >= 8
                    and struct.unpack_from('<I', result, 0)[0] == 0x3072cfa1):
                ln = struct.unpack_from('<i', result, 4)[0]
                result = gzip.decompress(result[8:8 + ln])
            rc = tl.Reader(result)
            rcid = rc.u32()
            if rcid == 0xec82e140:  # phone.phoneCall
                pc = parse_phone_call(result, 4)
                print(f'[{self.name}] rpc_result phone.phoneCall '
                      f'{pc["cid"]:#010x} id={pc.get("id")}')
                self.calls.append(pc)
            else:
                extra = ''
                if rcid == RPC_ERROR:
                    rr = tl.Reader(result)
                    rr.u32()
                    extra = f' {rr.i32()} {rr.string()}'
                print(f'[{self.name}] rpc_result cid={rcid:#010x} '
                      f'{len(result)}B req={req_msg_id:#018x}{extra}')
            self.last_result = result
            return
        off = find_ctor(body, UPDATE_PHONE_CALL)
        if off >= 0:
            try:
                pc = parse_phone_call(body, off + 4)
            except Exception as exc:
                print(f'[{self.name}] phone update scan skipped: {exc}')
                pc = None
            if pc is not None and pc.get('id') is not None:
                print(f'[{self.name}] updatePhoneCall {pc["cid"]:#010x} '
                      f'id={pc.get("id")}')
                self.calls.append(pc)
                return
        off = find_ctor(body, UPDATE_SIGNALING)
        if off >= 0:
            rr = tl.Reader(body)
            rr.pos = off + 4
            call_id = rr.i64()
            data = rr.tl_bytes()
            print(f'[{self.name}] updatePhoneCallSignalingData '
                  f'id={call_id} data={data!r}')
            self.signals.append((call_id, data))
            return
        # other account updates
        print(f'[{self.name}] update cid={cid:#010x} ({len(body)}B)')

    def wait_rpc(self, msg_id, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.poll(min(1.0, max(0.05, deadline - time.time())))
            res = getattr(self, 'last_result', None)
            if res is not None:
                self.last_result = None
                return res
        raise TimeoutError(f'[{self.name}] RPC timeout')

    def wait_call(self, ctor, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for pc in self.calls:
                if pc['cid'] == ctor:
                    self.calls.remove(pc)
                    return pc
            self.poll(min(1.0, max(0.05, deadline - time.time())))
        raise TimeoutError(f'[{self.name}] no {ctor:#010x} within {timeout}s')


def build_request_call(user_id, access_hash, random_id, g_a_hash, video=False):
    w = tl.Writer()
    w.u32(PHONE_REQUEST_CALL)
    w.i32(1 if video else 0)
    w.u32(INPUT_USER)
    w.i64(int(user_id))
    w.i64(int(access_hash))
    w.i32(int(random_id))
    w.tl_bytes(g_a_hash)
    pack_protocol(w)
    return w.bytes_out()


def build_received_call(call_id, access_hash):
    w = tl.Writer()
    w.u32(PHONE_RECEIVED_CALL)
    w.u32(INPUT_PHONE_CALL)
    w.i64(int(call_id))
    w.i64(int(access_hash))
    return w.bytes_out()


def build_accept_call(call_id, access_hash, g_b, protocol=None):
    w = tl.Writer()
    w.u32(PHONE_ACCEPT_CALL)
    w.u32(INPUT_PHONE_CALL)
    w.i64(int(call_id))
    w.i64(int(access_hash))
    w.tl_bytes(g_b)
    pack_protocol(w, protocol)
    return w.bytes_out()


def build_confirm_call(call_id, access_hash, g_a, fingerprint):
    w = tl.Writer()
    w.u32(PHONE_CONFIRM_CALL)
    w.u32(INPUT_PHONE_CALL)
    w.i64(int(call_id))
    w.i64(int(access_hash))
    w.tl_bytes(g_a)
    w.i64(int(fingerprint))
    pack_protocol(w)
    return w.bytes_out()


def build_signaling(call_id, access_hash, data):
    w = tl.Writer()
    w.u32(PHONE_SEND_SIGNALING)
    w.u32(INPUT_PHONE_CALL)
    w.i64(int(call_id))
    w.i64(int(access_hash))
    w.tl_bytes(data)
    return w.bytes_out()


def build_discard(call_id, access_hash, reason, duration):
    w = tl.Writer()
    w.u32(PHONE_DISCARD_CALL)
    w.i32(0)
    w.u32(INPUT_PHONE_CALL)
    w.i64(int(call_id))
    w.i64(int(access_hash))
    w.i32(int(duration))
    w.u32(int(reason))
    w.i64(0)
    return w.bytes_out()


def run_as_callee():
    """Answer a call placed by the real Android app and relay the full flow."""
    callee_key = -int('5fe37b6946d9a10', 16)
    probe = Probe('callee', 888913, avoid_keys={callee_key})
    call = {'id': None, 'ah': None, 'done': False}
    try:
        g, p = probe.get_dh_config()
        print('[callee] ready: place a call to +7 979 339-48-33 from the app '
              '(waiting up to 240s)')
        requested = probe.wait_call(PC_REQUESTED, 600)
        assert requested['admin_id'] == 888906, requested
        assert requested['participant_id'] == 888913, requested
        call_id = requested['id']
        call_ah = requested['access_hash']
        call.update(id=call_id, ah=call_ah)
        print(f'[callee] incoming call id={call_id} '
              f'g_a_hash={requested["g_a_hash"].hex()[:16]}... '
              f'caller_protocol={requested.get("protocol")}')

        mid = probe.send(build_received_call(call_id, call_ah))
        _ = probe.wait_rpc(mid, 10)
        print('[callee] receivedCall sent (caller will ring)')

        b = crypto.dh_gen_private()
        g_b = crypto.dh_compute_public(g, b, p)
        assert crypto.is_good_ga_or_gb(g_b, p), 'bad g_b'
        g_b_bytes = tl.int_to_bytes(g_b)
        # Echo the caller's own protocol back so the app always finds a
        # compatible layer / library version and proceeds to confirmCall.
        mid = probe.send(build_accept_call(call_id, call_ah, g_b_bytes,
                                           protocol=requested.get('protocol')))
        _ = probe.wait_rpc(mid, 10)
        print('[callee] acceptCall sent, waiting for the caller to confirm')

        final = probe.wait_call(PC_FINAL, 60)
        print(f'[callee] final g_a_or_b_len={len(final["g_a_or_b"])} '
              f'fp={final["key_fingerprint"]} start={final["start_date"]} '
              f'conns={[(c["kind"], c["ip"], c["port"]) for c in final["connections"]]}')
        webrtc = [c for c in final['connections'] if c['kind'] == 'webrtc']
        assert webrtc, 'no phoneConnectionWebrtc in final connections'
        assert any(c.get('turn') for c in webrtc), 'no TURN connection'

        print('[callee] collecting signaling from the app for 30s...')
        deadline = time.time() + 30
        while time.time() < deadline:
            probe.poll(1.0)
        print(f'[callee] got {len(probe.signals)} signaling packets '
              f'from the app')
        for cid_, data in probe.signals[:8]:
            print(f'   signal len={len(data)} head={data[:16].hex()}')
        assert probe.signals, 'app never started tgcalls (no signaling)'

        print('[callee] waiting for the app to hang up (up to 120s), '
              'or hanging up in 120s')
        try:
            discarded = probe.wait_call(PC_DISCARDED, 120)
            print(f'[callee] app discarded the call '
                  f'reason={discarded.get("reason", 0):#x}')
        except TimeoutError:
            mid = probe.send(build_discard(call_id, call_ah,
                                           DISCARD_HANGUP, 30))
            _ = probe.wait_rpc(mid, 10)
            print('[callee] probe sent discard')

        call['done'] = True
        print('CALL PROBE (as callee) OK')
        return 0
    finally:
        if call['id'] is not None and not call['done']:
            try:
                probe.send(build_discard(call['id'], call['ah'],
                                         DISCARD_HANGUP, 0), wrap=False)
                print('[callee] cleanup discard sent')
            except Exception as exc:
                print(f'[callee] cleanup discard failed: {exc}')
        try:
            probe.t.sock.close()
        except Exception:
            pass


def run_as_caller():
    """Call the real Android app (888906) and relay the flow once it answers."""
    probe = Probe('caller', 888913, avoid_keys=set())
    call = {'id': None, 'ah': None, 'done': False}
    try:
        g, p = probe.get_dh_config()
        a = crypto.dh_gen_private()
        g_a = crypto.dh_compute_public(g, a, p)
        assert crypto.is_good_ga_or_gb(g_a, p), 'bad g_a'
        g_a_bytes = tl.int_to_bytes(g_a)
        g_a_hash = hashlib.sha256(g_a_bytes).digest()

        access_hash = user_access_hash(888906)
        random_id = struct.unpack('<i', os.urandom(4))[0]
        mid = probe.send(build_request_call(888906, access_hash, random_id,
                                            g_a_hash))
        _ = probe.wait_rpc(mid, 10)
        waiting = probe.wait_call(PC_WAITING, 10)
        call_id = waiting['id']
        call_ah = waiting['access_hash']
        call.update(id=call_id, ah=call_ah)
        print(f'[caller] call {call_id} ringing — answer it on the device '
              f'(waiting up to 300s)')

        accepted = probe.wait_call(PC_ACCEPTED, 300)
        g_b_bytes = accepted['g_b']
        g_b = tl.bytes_to_int(g_b_bytes)
        print(f'[caller] app answered, g_b={len(g_b_bytes)}B')

        shared = crypto.dh_compute_shared(g_b, a, p)
        fingerprint = struct.unpack(
            '<q', hashlib.sha1(tl.int_to_bytes(shared)
                               ).digest()[:8])[0]
        mid = probe.send(build_confirm_call(call_id, call_ah, g_a_bytes,
                                            fingerprint))
        _ = probe.wait_rpc(mid, 10)
        final = probe.wait_call(PC_FINAL, 30)
        print(f'[caller] final g_a_or_b_len={len(final["g_a_or_b"])} '
              f'fp={final["key_fingerprint"]} start={final["start_date"]} '
              f'conns={[(c["kind"], c["ip"], c["port"]) for c in final["connections"]]}')
        webrtc = [c for c in final['connections'] if c['kind'] == 'webrtc']
        assert webrtc, 'no phoneConnectionWebrtc in final connections'
        assert any(c.get('turn') for c in webrtc), 'no TURN connection'

        print('[caller] collecting signaling from the app for 30s...')
        deadline = time.time() + 30
        while time.time() < deadline:
            probe.poll(1.0)
        print(f'[caller] got {len(probe.signals)} signaling packets '
              f'from the app')
        for cid_, data in probe.signals[:8]:
            print(f'   signal len={len(data)} head={data[:16].hex()}')
        assert probe.signals, 'app never started tgcalls (no signaling)'

        mid = probe.send(build_discard(call_id, call_ah, DISCARD_HANGUP, 30))
        _ = probe.wait_rpc(mid, 10)
        print('[caller] discard sent')
        call['done'] = True
        print('CALL PROBE (as caller) OK')
        return 0
    finally:
        if call['id'] is not None and not call['done']:
            try:
                probe.send(build_discard(call['id'], call['ah'],
                                         DISCARD_HANGUP, 0), wrap=False)
                print('[caller] cleanup discard sent')
            except Exception as exc:
                print(f'[caller] cleanup discard failed: {exc}')
        try:
            probe.t.sock.close()
        except Exception:
            pass


def main():
    if '--as-callee' in sys.argv:
        return run_as_callee()
    if '--call-app' in sys.argv:
        return run_as_caller()
    wait_accept = '--wait-accept' in sys.argv
    caller_key = -int('20d4a7e6be984c65', 16)
    callee_key = -int('5fe37b6946d9a10', 16)
    caller = Probe('caller', 888906, avoid_keys={caller_key})
    callee = Probe('callee', 888913, avoid_keys={callee_key})
    active = {'id': None, 'ah': None, 'done': False}
    try:
        g, p = caller.get_dh_config()
        g2, p2 = callee.get_dh_config()
        assert p == p2 and g == g2, 'dh params differ per client'
        assert crypto.dh_prime_ok(p) if hasattr(crypto, 'dh_prime_ok') else True

        a = crypto.dh_gen_private()
        g_a = crypto.dh_compute_public(g, a, p)
        assert crypto.is_good_ga_or_gb(g_a, p), 'bad g_a'
        g_a_bytes = tl.int_to_bytes(g_a)
        g_a_hash = hashlib.sha256(g_a_bytes).digest()

        access_hash = user_access_hash(888913)
        random_id = struct.unpack('<i', os.urandom(4))[0]
        print(f'[caller] requestCall g_a_hash={g_a_hash.hex()[:16]}...')
        mid = caller.send(build_request_call(888913, access_hash, random_id,
                                             g_a_hash))
        _ = caller.wait_rpc(mid, 10)
        waiting = caller.wait_call(PC_WAITING, 10)
        requested = callee.wait_call(PC_REQUESTED, 15)
        assert requested['g_a_hash'] == g_a_hash, 'g_a_hash relay mismatch'
        assert requested['admin_id'] == 888906, requested
        assert requested['participant_id'] == 888913, requested
        call_id = requested['id']
        call_ah = requested['access_hash']
        active.update(id=call_id, ah=call_ah)
        print(f'[call] id={call_id} access_hash={call_ah}')

        if wait_accept:
            print('[call] waiting for the Android app to accept '
                  '(answer it on the device)')
            accepted = caller.wait_call(PC_ACCEPTED, 180)
            g_b_bytes = accepted['g_b']
            g_b = tl.bytes_to_int(g_b_bytes)
            print(f'[call] accepted by app, g_b={len(g_b_bytes)}B')
        else:
            b = crypto.dh_gen_private()
            g_b = crypto.dh_compute_public(g, b, p)
            assert crypto.is_good_ga_or_gb(g_b, p), 'bad g_b'
            g_b_bytes = tl.int_to_bytes(g_b)
            mid = callee.send(build_accept_call(call_id, call_ah, g_b_bytes))
            _ = callee.wait_rpc(mid, 10)
            accepted = caller.wait_call(PC_ACCEPTED, 15)
            assert accepted['g_b'] == g_b_bytes, 'g_b relay mismatch'

        shared = crypto.dh_compute_shared(g_b, a, p)
        fingerprint = struct.unpack(
            '<q', hashlib.sha1(tl.int_to_bytes(shared)
                               ).digest()[:8])[0]
        mid = caller.send(build_confirm_call(call_id, call_ah, g_a_bytes,
                                             fingerprint))
        _ = caller.wait_rpc(mid, 10)
        caller_final = caller.wait_call(PC_FINAL, 15)
        callee_final = caller_final if wait_accept else callee.wait_call(PC_FINAL, 15)
        for who, final in (('caller', caller_final), ('callee', callee_final)):
            print(f'[{who}] final g_a_or_b_len={len(final["g_a_or_b"])} '
                  f'fp={final["key_fingerprint"]} start={final["start_date"]} '
                  f'conns={[(c["kind"], c["ip"], c["port"]) for c in final["connections"]]}')
        assert caller_final['g_a_or_b'] == g_b_bytes, 'caller final must carry g_b'
        if not wait_accept:
            assert callee_final['g_a_or_b'] == g_a_bytes, 'callee final must carry g_a'
        webrtc = [c for c in callee_final['connections']
                  if c['kind'] == 'webrtc']
        assert webrtc, 'no phoneConnectionWebrtc in final connections'
        assert any(c.get('turn') for c in webrtc), 'no TURN connection'
        print(f'[call] webrtc connections={webrtc}')

        if wait_accept:
            print('[call] collecting signaling from the app for 25s...')
            deadline = time.time() + 25
            while time.time() < deadline:
                caller.poll(1.0)
            print(f'[call] got {len(caller.signals)} signaling packets '
                  f'from the app')
            for cid_, data in caller.signals[:5]:
                print(f'   signal len={len(data)} head={data[:16].hex()}')
            time.sleep(2)
        else:
            payload = b'probe-signaling-' + os.urandom(4).hex().encode()
            mid = callee.send(build_signaling(call_id, call_ah, payload))
            _ = callee.wait_rpc(mid, 10)
            deadline = time.time() + 10
            got = None
            while time.time() < deadline and got is None:
                for cid_, data in list(caller.signals):
                    if data == payload:
                        got = data
                        caller.signals.remove((cid_, data))
                        break
                if got is None:
                    caller.poll(1.0)
            assert got == payload, 'signaling data not relayed to caller'
            print('[call] signaling relayed caller<->callee')

        mid = caller.send(build_discard(call_id, call_ah, DISCARD_HANGUP, 7))
        _ = caller.wait_rpc(mid, 10)
        if not wait_accept:
            discarded = callee.wait_call(PC_DISCARDED, 15)
            assert discarded.get('reason') == DISCARD_HANGUP, discarded
            print(f'[call] callee got discarded reason={discarded.get("reason"):#x}')

        active['done'] = True
        print('CALL PROBE OK')
        return 0
    finally:
        if active['id'] is not None and not active['done']:
            try:
                caller.send(build_discard(active['id'], active['ah'],
                                          DISCARD_HANGUP, 0), wrap=False)
                print('[call] cleanup discard sent')
            except Exception as exc:
                print(f'[call] cleanup discard failed: {exc}')
        for pr in (caller, callee):
            try:
                pr.t.sock.close()
            except Exception:
                pass


if __name__ == '__main__':
    raise SystemExit(main())
