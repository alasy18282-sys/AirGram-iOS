"""Reproduce the "voice note breaks delivery" report at the MTProto level.

Two independent clients on one account each:
  * recipient (default 888907) signs in, then only listens — every push is
    decoded and its pts/seq/update constructors printed;
  * sender (default 888908) signs in, uploads a voice document and sends it
    with messages.sendMedia, then continues with plain text and a round
    video note.

If delivery is healthy the recipient sees updateNewMessage with pts = state+1
on the same open connection, without any RPC of its own.  A pts jump, a
malformed media or a missing push is the bug.

Usage: python3 probe_voice_stall.py [host] [port]
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mtproto import tl
import probe_layer228 as p

HOST_DEFAULT = '127.0.0.1'
PORT_DEFAULT = 443
LAYER = 229
API_ID = 2040
API_HASH = 'b18441a1ff607e10a989891a5462e627'

RECIPIENT_PHONE = '15555550123'   # 888907
SENDER_PHONE = '15555550124'      # 888908

DB_PATH = os.environ.get(
    'MYTELEGRAM_DB',
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 'mytelegram.db'))

UPLOAD_SAVE_FILE_PART = 0xb304a621
MESSAGES_SEND_MEDIA = 0x0330e77f
INPUT_MEDIA_UPLOADED_DOCUMENT = 0x37c9330
INPUT_FILE = 0xf52ff27f
DOCUMENT_ATTR_AUDIO = 0x9852f9c6
DOCUMENT_ATTR_VIDEO = 0x43c57c48
DOCUMENT_ATTR_FILENAME = 0x15590068
CODE_SETTINGS = 0xad253d78
UPDATES_GET_STATE = 0xedd4882a
UPDATES_STATE = 0xa56c2a3e
UPDATES = 0x74ae4240
UPDATE_NEW_MESSAGE = 0x1f2b0afd
UPDATE_MESSAGE_ID = 0x4e90bfd6
MSG_CONTAINER = 0x73f1f8dc
RPC_RESULT = 0xf35c6d01
NEW_SESSION = 0x9ec20908
MSGS_ACK = 0x62d6b459
MESSAGE_CTOR = 0x7600b9d3
MEDIA_DOCUMENT = 0x52d8ccd9
DOCUMENT = 0x8fd4c4d8


def wrap(inner: bytes) -> bytes:
    w = tl.Writer()
    w.u32(tl.INVOKE_WITH_LAYER)
    w.i32(LAYER)
    w.raw(inner)
    return w.bytes_out()


def init_connection(inner: bytes) -> bytes:
    w = tl.Writer()
    w.u32(tl.INIT_CONNECTION)
    w.i32(0)
    w.i32(API_ID)
    w.string('DESKTOP-QA-VOICE')
    w.string('Windows 10')
    w.string('7.2.5 x64')
    w.string('en-US')
    w.string('tdesktop')
    w.string('en')
    w.raw(inner)
    return wrap(w.bytes_out())


def db_code(code_hash: str) -> str:
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    try:
        row = conn.execute(
            'SELECT code FROM phone_codes WHERE code_hash=? '
            'ORDER BY rowid DESC LIMIT 1', (code_hash,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise RuntimeError(f'no code stored for hash {code_hash}')
    return str(row[0])


def db_user(phone: str) -> tuple[int, int]:
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    try:
        row = conn.execute(
            'SELECT id, access_hash FROM users WHERE phone=?',
            (phone,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise RuntimeError(f'user {phone} not found')
    return int(row[0]), int(row[1])


def mk(r: tl.Reader, label: str) -> p.Checker:
    """Checker view over an existing Reader (shares position)."""
    c = p.Checker.__new__(p.Checker)
    c.r = r
    c.label = label
    c.end = len(r.data)
    return c


def skip_peer(r: tl.Reader, label: str) -> None:
    p.skip_peer(mk(r, label))


def parse_document(r: tl.Reader, label: str) -> dict:
    cid = r.u32()
    assert cid == DOCUMENT, f'{label}: doc ctor {cid:#010x}'
    flags = r.i32()
    doc_id = r.i64()
    r.i64()                    # access_hash
    r.tl_bytes()               # file_reference
    r.i32()                    # date
    mime = r.string()
    size = r.i64()
    if flags & 1:
        c = mk(r, label)
        for _ in range(c.vec()):
            p._skip_photo_size(c)
    if flags & 2:
        c = mk(r, label)
        for _ in range(c.vec()):
            p._skip_video_size(c)
    r.i32()                    # dc_id
    c = mk(r, label)
    attrs = []
    for _ in range(c.vec()):
        ac = r.u32()
        if ac == DOCUMENT_ATTR_AUDIO:
            af = r.i32()
            dur = r.i32()
            title = r.string() if af & 1 else None
            performer = r.string() if af & 2 else None
            waveform = r.tl_bytes() if af & 4 else b''
            attrs.append(('audio', af, dur, title, performer, len(waveform)))
        elif ac == DOCUMENT_ATTR_VIDEO:
            vf = r.i32()
            dur = r.f64()
            w_ = r.i32(); h_ = r.i32()
            if vf & 4:
                r.i32()
            if vf & 16:
                r.f64()
            if vf & 32:
                r.string()
            attrs.append(('video', vf, dur, w_, h_))
        elif ac == DOCUMENT_ATTR_FILENAME:
            attrs.append(('filename', r.string()))
        else:
            raise AssertionError(f'{label}: attr {ac:#010x}')
    return {'id': doc_id, 'mime': mime, 'size': size, 'attrs': attrs}


def parse_message_ext(r: tl.Reader, label: str) -> dict:
    cid = r.u32()
    assert cid == MESSAGE_CTOR, f'{label}: message ctor {cid:#010x}'
    flags = r.i32()
    flags2 = r.i32()
    msg = {'flags': flags, 'flags2': flags2}
    msg['id'] = r.i32()
    if flags & (1 << 8):
        msg['from'] = None
        skip_peer(r, label)
    if flags & (1 << 29):
        r.i32()
    if flags2 & (1 << 12):
        r.string()
    skip_peer(r, label)                    # peer_id
    if flags & (1 << 28):
        skip_peer(r, label)
    if flags & (1 << 2):
        raise AssertionError(f'{label}: fwd_from not expected')
    if flags & (1 << 11):
        r.i64()
    if flags2 & (1 << 0):
        r.i64()
    if flags2 & (1 << 19):
        skip_peer(r, label)
    if flags & (1 << 3):
        c = mk(r, label)
        rf = c.u32()
        assert rf == 0x1b97dd66, f'{label}: reply hdr {rf:#x}'
        f = r.i32()
        if f & (1 << 4):
            r.i32()
        if f & 1:
            skip_peer(r, label)
        if f & (1 << 5):
            raise AssertionError('reply_from')
        if f & (1 << 8):
            raise AssertionError('reply_media')
        if f & 2:
            r.i32()
        if f & (1 << 6):
            r.string()
        if f & (1 << 7):
            p.packers.skip_entities(r)
        if f & (1 << 10):
            r.i32()
        if f & (1 << 11):
            r.i32()
        if f & (1 << 12):
            r.tl_bytes()
    msg['date'] = r.i32()
    msg['text'] = r.string()
    if flags & (1 << 9):
        mc = r.u32()
        assert mc == MEDIA_DOCUMENT, f'{label}: media ctor {mc:#010x}'
        mflags = r.i32()
        msg['media_flags'] = mflags
        if mflags & 1:
            msg['document'] = parse_document(r, label)
    if flags & (1 << 6):
        raise AssertionError(f'{label}: reply_markup not expected')
    if flags & (1 << 7):
        p.packers.skip_entities(r)
    if flags & (1 << 10):
        r.i32(); r.i32()
    for bit, name in ((23, 'replies'), (15, 'edit_date'), (16, 'post_author'),
                      (17, 'grouped_id'), (20, 'reactions'),
                      (22, 'restriction_reason'), (25, 'ttl_period'),
                      (30, 'quick_reply_shortcut_id')):
        if flags & (1 << bit):
            raise AssertionError(f'{label}: flag {name} not expected')
    if flags2 & (1 << 2):
        r.i64()
    if flags2 & (1 << 3):
        raise AssertionError('factcheck')
    if flags2 & (1 << 5):
        r.i32()
    if flags2 & (1 << 6):
        r.i64()
    if flags2 & (1 << 7):
        raise AssertionError('suggested_post')
    if flags2 & (1 << 10):
        r.i32()
    if flags2 & (1 << 11):
        r.string()
    if flags2 & (1 << 13):
        raise AssertionError('rich_message')
    return msg


class Probe(p.Client):
    def __init__(self, host: str, port: int):
        super().__init__(host, port, layer=LAYER)
        self.ack_seqs: list[int] = []

    def call(self, body: bytes, label: str, timeout: float = 10.0):
        res = super().call(body, label, timeout)
        if res and int.from_bytes(res[:4], 'little') == 0x2144ca19:
            r = tl.Reader(res)
            r.u32()
            code = r.i32()
            msg = r.string()
            print(f'  [!!] {label}: rpc_error {code}: {msg}')
            return None
        return res

    def sign_in(self, phone: str, label: str) -> int:
        w = tl.Writer(); w.u32(tl.HELP_GET_CONFIG)
        self.call(init_connection(w.bytes_out()), f'{label}/help.getConfig')

        w = tl.Writer()
        w.u32(tl.AUTH_SEND_CODE)
        w.string('+' + phone)
        w.i32(API_ID)
        w.string(API_HASH)
        w.u32(CODE_SETTINGS)
        w.i32(0)
        res = self.call(wrap(w.bytes_out()), f'{label}/auth.sendCode')
        r = tl.Reader(res)
        assert r.u32() == 0x5e002502, 'unexpected auth.sentCode'
        r.i32()
        tcid = r.u32()
        if tcid in (0x3dbb5986, 0xc000bba2, 0x5353e5a7):
            r.i32()
        elif tcid == 0xab03c6d9:
            r.string()
        elif tcid == 0xd9565c39:
            r.string(); r.i32()
        else:
            raise AssertionError(f'unknown sentCodeType {tcid:#x}')
        code_hash = r.string()
        code = db_code(code_hash)

        w = tl.Writer()
        w.u32(tl.AUTH_SIGN_IN)
        w.i32(1)
        w.string('+' + phone)
        w.string(code_hash)
        w.string(code)
        res = self.call(wrap(w.bytes_out()), f'{label}/auth.signIn')
        r = tl.Reader(res)
        cid = r.u32()
        assert cid == tl.AUTH_AUTHORIZATION, f'{label}: signIn ctor {cid:#x}'
        flags = r.i32()
        if flags & 1:
            r.i32()
        if flags & 2:
            r.i32()
        if flags & 4:
            r.tl_bytes()
        uid = p.parse_user(mk(r, label))
        print(f'[ok] {label} signed in as {uid}')
        return uid


def upload_voice(client: Probe, mime: str, attrs: bytes, name: str) -> None:
    payload = os.urandom(1800)
    file_id = int.from_bytes(os.urandom(8), 'little', signed=True)
    w = tl.Writer()
    w.u32(UPLOAD_SAVE_FILE_PART)
    w.i64(file_id)
    w.i32(0)
    w.tl_bytes(payload)
    client.call(wrap(w.bytes_out()), 'upload.saveFilePart')
    client._file = (file_id, 1, name, mime, attrs)


def send_media(client: Probe, peer_id: int, peer_hash: int, *,
               kind: str) -> None:
    file_id, parts, name, mime, attrs = client._file
    w = tl.Writer()
    w.u32(MESSAGES_SEND_MEDIA)
    w.i32(0)
    w.u32(tl.INPUT_PEER_USER)
    w.i64(peer_id)
    w.i64(peer_hash)
    w.u32(INPUT_MEDIA_UPLOADED_DOCUMENT)
    w.i32(0)
    w.u32(INPUT_FILE)
    w.i64(file_id)
    w.i32(parts)
    w.string(name)
    w.string('')
    w.string(mime)
    w.raw(attrs)
    w.string('')
    w.i64(int.from_bytes(os.urandom(8), 'little', signed=True))
    res = client.call(wrap(w.bytes_out()), f'messages.sendMedia({kind})')
    if res is not None:
        seq = int.from_bytes(res[-4:], 'little', signed=True)
        client.ack_seqs.append(seq)
        print(f'[ok] {kind} sent, ack {len(res)}B seq={seq}')


def audio_attrs() -> bytes:
    w = tl.Writer()
    w.u32(tl.VECTOR); w.i32(2)
    w.u32(DOCUMENT_ATTR_AUDIO)
    w.i32(0x404)      # waveform + voice
    w.i32(3)
    w.tl_bytes(b'\x01\x02\x03\x04')
    w.u32(DOCUMENT_ATTR_FILENAME)
    w.string('voice.ogg')
    return w.bytes_out()


def video_attrs() -> bytes:
    w = tl.Writer()
    w.u32(tl.VECTOR); w.i32(2)
    w.u32(DOCUMENT_ATTR_VIDEO)
    w.i32(1)          # round_message
    w.f64(2.0)
    w.i32(240)
    w.i32(240)
    w.u32(DOCUMENT_ATTR_FILENAME)
    w.string('round.mp4')
    return w.bytes_out()


def send_text(client: Probe, peer_id: int, peer_hash: int, text: str) -> None:
    w = tl.Writer()
    w.u32(tl.MESSAGES_SEND_MESSAGE_V227)
    w.i32(0)
    w.u32(tl.INPUT_PEER_USER)
    w.i64(peer_id)
    w.i64(peer_hash)
    w.string(text)
    w.i64(int.from_bytes(os.urandom(8), 'little', signed=True))
    res = client.call(wrap(w.bytes_out()), 'messages.sendMessage')
    seq = int.from_bytes(res[-4:], 'little', signed=True)
    client.ack_seqs.append(seq)
    print(f'[ok] text sent, ack {len(res)}B seq={seq}')


class Listener(Probe):
    def get_state(self) -> tuple[int, int, int, int]:
        w = tl.Writer(); w.u32(UPDATES_GET_STATE)
        res = self.call(wrap(w.bytes_out()), 'updates.getState')
        r = tl.Reader(res)
        assert r.u32() == UPDATES_STATE, 'bad updates.state'
        return r.i32(), r.i32(), r.i32(), r.i32()  # pts qts date seq

    def drain(self, seconds: float, label: str) -> list[dict]:
        out: list[dict] = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            body = self._read_quiet(deadline - time.time())
            if body is None:
                continue
            out.extend(self._decode(body, label))
        return out

    def _read_quiet(self, timeout: float):
        try:
            pkt = self.t.read_encrypted(timeout=max(0.05, timeout))
        except (TimeoutError, OSError):
            return None
        self.salt = int(pkt['salt'])
        return pkt['body']

    def _decode(self, data: bytes, label: str) -> list[dict]:
        cid = int.from_bytes(data[:4], 'little')
        res: list[dict] = []
        if cid == NEW_SESSION:
            print(f'  [{label}] new_session_created')
            return res
        if cid in (MSGS_ACK, RPC_RESULT):
            return res
        if cid == MSG_CONTAINER:
            r = tl.Reader(data); r.u32()
            for _ in range(r.i32()):
                r.i64(); r.i32()
                inner = r.read(r.i32())
                res.extend(self._decode(inner, label))
            return res
        if cid != UPDATES:
            print(f'  [{label}] packet cid={cid:#010x} {len(data)}B')
            return res
        res.append(self._parse_updates(data, label))
        return res

    def _parse_updates(self, data: bytes, label: str) -> dict:
        r = tl.Reader(data)
        r.u32()
        c = mk(r, label)
        info = {'label': label, 'updates': []}
        for _ in range(c.vec()):
            uc = r.u32()
            if uc == UPDATE_NEW_MESSAGE:
                msg = parse_message_ext(r, label)
                info['updates'].append(
                    ('new_message', msg, r.i32(), r.i32()))
            elif uc == UPDATE_MESSAGE_ID:
                info['updates'].append(('message_id', r.i32(), r.i64()))
            else:
                raise AssertionError(f'{label}: update {uc:#010x}')
        c = mk(r, label)
        for _ in range(c.vec()):
            p.parse_user(c)
        c = mk(r, label)
        for _ in range(c.vec()):
            p.parse_chat(c)
        info['date'] = r.i32()
        info['seq'] = r.i32()
        print(f"  [{label}] updates seq={info['seq']} date={info['date']}")
        for u in info['updates']:
            if u[0] == 'new_message':
                _, m, pts, pc = u
                desc = f"msg#{m['id']} pts={pts} pc={pc} text={m['text']!r}"
                if 'media_flags' in m:
                    desc += (f" media_flags={m['media_flags']:#x}"
                             f" attrs={m.get('document', {}).get('attrs')}")
                    if m['media_flags'] & (1 << 8):
                        desc += ' VOICE'
                    if m['media_flags'] & (1 << 7):
                        desc += ' ROUND'
                print('    ' + desc)
            else:
                print(f'    message_id id={u[1]} random_id={u[2]}')
        return info


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else HOST_DEFAULT
    port = int(sys.argv[2]) if len(sys.argv) > 2 else PORT_DEFAULT
    recv_id, recv_hash = db_user(RECIPIENT_PHONE)
    send_id, _ = db_user(SENDER_PHONE)
    print(f'== voice stall probe {host}:{port} '
          f'sender={send_id} recipient={recv_id} ==')

    listener = Listener(host, port)
    listener.sign_in(RECIPIENT_PHONE, 'recipient')
    sender = Listener(host, port)
    sender.sign_in(SENDER_PHONE, 'sender')

    pts0, qts0, date0, seq0 = listener.get_state()
    print(f'[i] recipient state pts={pts0} qts={qts0} seq={seq0}')
    send_seq0 = sender.get_state()[3]
    print(f'[i] sender state seq={send_seq0}')

    leftover = listener.drain(1.5, 'pre')
    print(f'[i] pre-existing pushes: {len(leftover)}')

    upload_voice(sender, 'audio/ogg', audio_attrs(), 'voice.ogg')
    send_media(sender, recv_id, recv_hash, kind='voice')
    time.sleep(0.3)
    send_text(sender, recv_id, recv_hash, 'after-voice-text')
    time.sleep(0.3)
    upload_voice(sender, 'video/mp4', video_attrs(), 'round.mp4')
    send_media(sender, recv_id, recv_hash, kind='round')
    time.sleep(0.3)
    send_text(sender, recv_id, recv_hash, 'after-round-text')

    got = listener.drain(6.0, 'push')
    pts_seen = [u[2] for info in got for u in info['updates']
                if u[0] == 'new_message']
    print(f'[i] pushes received: {len(got)}, message pts={pts_seen}')
    expected = list(range(pts0 + 1, pts0 + 1 + len(pts_seen)))
    print(f'[i] expected contiguous pts: {expected}')
    if pts_seen != expected:
        print('[!!] PTS HOLE / REGRESSION after media send')
    else:
        print('[ok] pts are contiguous')

    pts1, qts1, date1, seq1 = listener.get_state()
    print(f'[i] recipient state after pts={pts1} qts={qts1} seq={seq1}')
    if pts1 != (pts_seen[-1] if pts_seen else pts0):
        print(f'[!!] state.pts={pts1} != last pushed pts')

    push_seqs = [info['seq'] for info in got]
    expected_seqs = list(range(seq0 + 1, seq0 + 1 + len(push_seqs)))
    print(f'[i] push seqs={push_seqs} expected={expected_seqs}')
    if push_seqs != expected_seqs:
        print('[!!] SEQ GAP/REGRESSION in pushes')
    else:
        print('[ok] push seqs are contiguous')

    ack_seqs = sender.ack_seqs
    expected_acks = list(range(send_seq0 + 1, send_seq0 + 1 + len(ack_seqs)))
    print(f'[i] sender ack seqs={ack_seqs} expected={expected_acks}')
    if ack_seqs != expected_acks:
        print('[!!] SEQ GAP/REGRESSION in sender acks')
    else:
        print('[ok] sender ack seqs are contiguous')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
